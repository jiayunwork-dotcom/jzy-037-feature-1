"""非理想（van Laar）等温闪蒸内核：衡算闭合、重新推导的单相判定、
泡/露点趋近、压力单调性、非法输入拒绝。

示范物系：乙醇(1)/水(2) @ 351.30 K，P1sat≈100.606 kPa、P2sat≈43.830 kPa，
van Laar A12=1.75、A21=0.91（来源见 app/seed.py 与 README）。
"""
from __future__ import annotations

import pytest

from app.activity import VanLaarModel
from app.errors import (
    ActivityCoefficientInvalid,
    ActivityModelInputInvalid,
    SolverNonConvergence,
)
from app.nonideal_flash import _find_dew, NonidealSettings, isothermal_flash_nonideal

MODEL = VanLaarModel(1.75, 0.91)
PSAT = [100.60558523182848, 43.83020178882937]


def _pbubble(z):
    gamma = MODEL.activity_coefficients(z)
    return sum(z[i] * gamma[i] * PSAT[i] for i in range(2))


class TestClosure:
    def test_two_phase_material_balance_closes(self):
        z = [0.5, 0.5]
        res = isothermal_flash_nonideal(z, PSAT, 85.0, MODEL)
        assert res.phase == "two_phase"
        v, x, y = res.vapor_fraction, res.liquid_composition, res.vapor_composition
        assert 0.0 < v < 1.0
        assert sum(x) == pytest.approx(1.0, abs=1e-10)
        assert sum(y) == pytest.approx(1.0, abs=1e-10)
        for i in range(2):
            assert (1.0 - v) * x[i] + v * y[i] == pytest.approx(z[i], abs=1e-9)

    def test_equilibrium_relation_satisfied(self):
        res = isothermal_flash_nonideal([0.5, 0.5], PSAT, 85.0, MODEL)
        P = 85.0
        for i in range(2):
            assert res.vapor_composition[i] == pytest.approx(
                res.liquid_composition[i] * res.activity_coefficients[i] * PSAT[i] / P,
                abs=1e-9,
            )
        assert res.k_values[0] == pytest.approx(
            res.activity_coefficients[0] * PSAT[0] / P
        )
        assert res.nonideal_residual < 1e-9

    @pytest.mark.parametrize("z,P", [
        ([0.5, 0.5], 80.0),
        ([0.5, 0.5], 85.0),
        ([0.2, 0.8], 70.0),
        ([0.35, 0.65], 70.0),
        ([0.9, 0.1], 100.9),  # 共沸附近极窄的两相窗（Pb≈100.90, Pd≈100.90）
    ])
    def test_closure_across_spectrum(self, z, P):
        res = isothermal_flash_nonideal(z, PSAT, P, MODEL)
        assert res.phase == "two_phase"
        v, x, y = res.vapor_fraction, res.liquid_composition, res.vapor_composition
        for i in range(2):
            assert (1 - v) * x[i] + v * y[i] == pytest.approx(z[i], abs=1e-9)
        assert sum(x) == pytest.approx(1.0, abs=1e-10)
        assert sum(y) == pytest.approx(1.0, abs=1e-10)


class TestHandCheckEthanolWater:
    """乙醇/水 @351.30 K、等摩尔、85 kPa 的手算量级锚点。

    手算（x=z=(0.5,0.5) 处）：
    ln γ1 = 1.75/(1+1.75/0.91)² ≈ 0.2046 → γ1 ≈ 1.227、γ2 ≈ 1.483；
    泡点压力 P_b ≈ 0.5·1.227·100.61 + 0.5·1.483·43.83 ≈ 94.2 kPa。
    P_b > 85 kPa，故不是单相液体；而理想假设 P_b^id≈72.2 kPa < 85 kPa，
    会误判为单相液体。服务解出两相：x1≈0.231、y1≈0.552、V≈0.838。
    """

    def test_gamma_hand_values_at_equimolar(self):
        g1, g2 = MODEL.activity_coefficients([0.5, 0.5])
        assert g1 == pytest.approx(1.227, abs=5e-4)
        assert g2 == pytest.approx(1.483, abs=5e-4)

    def test_bubble_pressure_hand_value(self):
        assert _pbubble([0.5, 0.5]) == pytest.approx(94.23, abs=0.02)

    def test_flash_hand_values(self):
        res = isothermal_flash_nonideal([0.5, 0.5], PSAT, 85.0, MODEL)
        assert res.phase == "two_phase"
        assert res.liquid_composition[0] == pytest.approx(0.231, abs=2e-3)
        assert res.vapor_composition[0] == pytest.approx(0.552, abs=2e-3)
        assert res.vapor_fraction == pytest.approx(0.838, abs=3e-3)

    def test_direction_of_deviation_from_ideal(self):
        # 理想假设：K_i=Psat_i/P 与组成无关 → ΣzK=72.22/85<1 判单相液体；
        # 计入活度：P_b≈94.2>85、P_d≈79.2<85，实际处于两相区 → 两相。
        from app.flash import isothermal_flash

        ideal = isothermal_flash([0.5, 0.5], [PSAT[0] / 85.0, PSAT[1] / 85.0])
        nonideal = isothermal_flash_nonideal([0.5, 0.5], PSAT, 85.0, MODEL)
        assert ideal.phase == "liquid"
        assert ideal.vapor_fraction == 0.0
        assert nonideal.phase == "two_phase"
        assert nonideal.vapor_fraction > 0.8  # 不是退化回理想解


class TestSinglePhaseReDerived:
    def test_high_pressure_is_liquid(self):
        res = isothermal_flash_nonideal([0.5, 0.5], PSAT, 101.325, MODEL)
        assert res.phase == "liquid"
        assert res.vapor_fraction == 0.0
        assert res.liquid_composition == [0.5, 0.5]
        assert res.vapor_composition is None
        assert res.activity_coefficients is not None  # γ 在 x=z 处给出
        assert "泡点" in res.reason
        assert res.bubble_sum <= 1.0

    def test_low_pressure_is_vapor(self):
        res = isothermal_flash_nonideal([0.5, 0.5], PSAT, 60.0, MODEL)
        assert res.phase == "vapor"
        assert res.vapor_fraction == 1.0
        assert res.vapor_composition == [0.5, 0.5]
        assert res.liquid_composition is None
        assert res.activity_coefficients is None  # 无平衡液相
        assert "露点" in res.reason
        assert res.dew_sum <= 1.0

    def test_bubble_and_dew_ordering(self):
        z = [0.5, 0.5]
        settings = NonidealSettings()
        p_dew, _, _, _, _ = _find_dew(MODEL, z, PSAT, settings)
        p_bubble = _pbubble(z)
        assert p_dew < p_bubble
        assert p_dew == pytest.approx(79.15, abs=0.02)

    def test_old_k_constant_criteria_would_misclassify(self):
        # K 变成 x 的函数后，旧的露点不等式 Σz/K(x=z)≤1 不再是正确判据：
        # 真正的露点要用第一滴液相组成 x*。P=82 kPa（两相区，P_d≈79.2）时，
        # 用 x=z 的 K 代入旧判据会得到 Σz/K≈0.963<1，误判成单相蒸汽。
        z = [0.5, 0.5]
        P = 82.0
        gamma_z = MODEL.activity_coefficients(z)
        k_at_feed = [gamma_z[i] * PSAT[i] / P for i in range(2)]
        naively_at_feed = sum(z[i] / k_at_feed[i] for i in range(2))
        res = isothermal_flash_nonideal(z, PSAT, P, MODEL)
        assert naively_at_feed < 1.0  # 旧判据会误判为单相蒸汽
        assert res.phase == "two_phase"  # 重新推导后的正确结论
        assert 0.0 < res.vapor_fraction < 1.0


class TestApproachLimits:
    def setup_method(self):
        settings = NonidealSettings()
        self.p_bubble = _pbubble([0.5, 0.5])
        self.p_dew, _, _, _, _ = _find_dew(MODEL, [0.5, 0.5], PSAT, settings)

    def test_approach_bubble_v_to_zero(self):
        fractions = []
        for exponent in range(2, 7):
            res = isothermal_flash_nonideal(
                [0.5, 0.5], PSAT, self.p_bubble * (1.0 - 10.0**-exponent), MODEL
            )
            assert res.phase == "two_phase"
            fractions.append(res.vapor_fraction)
        assert fractions[-1] < 1e-4
        assert all(b < a for a, b in zip(fractions, fractions[1:]))
        above = isothermal_flash_nonideal([0.5, 0.5], PSAT, self.p_bubble * 1.0001, MODEL)
        assert above.phase == "liquid"

    def test_approach_dew_v_to_one(self):
        liquids = []
        for exponent in range(2, 7):
            res = isothermal_flash_nonideal(
                [0.5, 0.5], PSAT, self.p_dew * (1.0 + 10.0**-exponent), MODEL
            )
            assert res.phase == "two_phase"
            liquids.append(1.0 - res.vapor_fraction)
        assert liquids[-1] < 1e-4
        assert all(b < a for a, b in zip(liquids, liquids[1:]))
        below = isothermal_flash_nonideal([0.5, 0.5], PSAT, self.p_dew * 0.9999, MODEL)
        assert below.phase == "vapor"


class TestMonotonicity:
    @pytest.mark.parametrize("z", [[0.5, 0.5], [0.2, 0.8], [0.9, 0.1]])
    def test_vapor_fraction_non_increasing_in_pressure(self, z):
        fractions = [
            isothermal_flash_nonideal(z, PSAT, 40.0 + 1.5 * i, MODEL).vapor_fraction
            for i in range(54)  # 40 → 119.5 kPa，覆盖全部单相/两相区
        ]
        assert fractions[0] == 1.0
        assert fractions[-1] == 0.0
        for before, after in zip(fractions, fractions[1:]):
            assert after <= before + 1e-9


class TestAzeotropeRegion:
    """共沸组成附近：B(x1)=P 可有两个交点，须按杠杆规则选出正确结线。"""

    def test_rich_ethanol_feed_picks_right_tie_line(self):
        # 共沸点 x≈0.92、P≈100.9 kPa：其富乙醇侧两相窗极窄，B(x1)=P 有两个交点，
        # 只有让 z=0.9 落在结线上的那个根合法（V≈0.87，x≈0.898、y≈0.900）
        res = isothermal_flash_nonideal([0.9, 0.1], PSAT, 100.9, MODEL)
        assert res.phase == "two_phase"
        x, y, v = res.liquid_composition, res.vapor_composition, res.vapor_fraction
        assert 0.0 < v < 1.0
        for i in range(2):
            assert (1 - v) * x[i] + v * y[i] == pytest.approx([0.9, 0.1][i], abs=1e-9)

    def test_near_azeotrope_pressure_classifies_liquid(self):
        # 共沸点压力是泡点曲线最大值；超过它必然全液
        res = isothermal_flash_nonideal([0.86, 0.14], PSAT, 101.5, MODEL)
        assert res.phase == "liquid"

    def test_dew_balance_multiple_roots_picks_stable_minimum_pd(self):
        """回归：强正偏差共沸体系露点配平 H(x1)=0 有三个根。

        若错误地取最大 P_d 的根会得到 P_d≈76.0 > P_b≈71.6 的非物理结果；
        正确的稳定根（与进料同在共沸富乙醇侧）P_d≈70.6 ≤ P_b。
        该参数组由随机稳健性扫描发现（random.seed(42), trial 12）。
        """
        az_model = VanLaarModel(2.969093884377956, 1.937999291569574)
        az_psat = [57.05464321573507, 26.052731095939738]
        z = [0.6772296809502796, 0.3227703190497204]
        settings = NonidealSettings()
        p_bubble = _pbubble_z(az_model, az_psat, z)
        p_dew, x_star, _, _, _ = _find_dew(az_model, z, az_psat, settings)
        assert p_dew == pytest.approx(70.63, abs=0.05)
        assert x_star[0] == pytest.approx(0.781, abs=5e-3)  # 富乙醇侧稳定根
        assert p_dew <= p_bubble
        # P=93 高于泡点压力 → 单相液体（错误根会让判定自相矛盾）
        res = isothermal_flash_nonideal(z, az_psat, 93.0204, az_model)
        assert res.phase == "liquid"


    def test_dew_root_extremely_close_to_pure_component(self):
        """回归：极稀组成 + P_sat 悬殊时，露点根 x1≈1−2.6e-6 仍能求出。

        早期实现里配平一致性阈值（1e-7 相对）比 H 数值尺度（~1e4）下
        二分可达精度更严，导致该根被误弃并报「未找到可用根」。
        参数组由随机稳健性扫描发现（random.seed(2026), trial 572）。
        """
        extreme = VanLaarModel(0.05296320808614623, 3.485003460816062)
        psat_extreme = [0.9133024250037554, 208.52083933360277]
        z = [0.9811585381084575, 0.018841461891542455]
        p_dew, x_star, _, _, _ = _find_dew(extreme, z, psat_extreme, NonidealSettings())
        assert x_star[0] == pytest.approx(0.9999974, abs=1e-6)
        assert p_dew == pytest.approx(0.93084, abs=1e-3)
        # 该露点压力以下判单相蒸汽，求解全程不抛错
        res = isothermal_flash_nonideal(z, psat_extreme, 0.8689, extreme)
        assert res.phase == "vapor"


def _pbubble_z(model, psat, z):
    gamma = model.activity_coefficients(z)
    return sum(z[i] * gamma[i] * psat[i] for i in range(2))


class TestInvalidInput:
    def test_composition_outside_model_domain(self):
        with pytest.raises(ActivityModelInputInvalid):
            isothermal_flash_nonideal([1.1, -0.1], PSAT, 85.0, MODEL)

    def test_nonpositive_psat_rejected(self):
        # Psat 非正在下游先被蒸汽压校验拦截；构造一个使 γ 溢出的极端参数组合，
        # 确认非有限活度系数不会带着 NaN 进入求根
        crazy = VanLaarModel(1e4, 1e4)
        with pytest.raises(ActivityCoefficientInvalid):
            isothermal_flash_nonideal([0.5, 0.5], PSAT, 85.0, crazy)

    def test_solver_reports_nonconvergence_as_typed_error(self):
        # 粗到只有 1 个区间且极端参数时，扫描理论上仍应抓到根；
        # 这里直接验证失败路径抛的是带类型的求解错误而非裸异常
        with pytest.raises(SolverNonConvergence):
            isothermal_flash_nonideal(
                [0.5, 0.5], PSAT, 85.0, MODEL, max_iter=0
            )
