"""Rachford-Rice 求解内核：衡算闭合、单相判定、压力单调性、泡点/露点逼近。"""
from __future__ import annotations

import pytest

from app.flash import isothermal_flash

# 298.15 K 下正戊烷/正己烷的饱和蒸汽压（kPa，由示范 Antoine 系数算得）
P_SAT_LIGHT = 68.379
P_SAT_HEAVY = 20.169
TOL = 1e-9


def _k(pressure: float) -> list[float]:
    return [P_SAT_LIGHT / pressure, P_SAT_HEAVY / pressure]


def _bubble_pressure(z: list[float]) -> float:
    return z[0] * P_SAT_LIGHT + z[1] * P_SAT_HEAVY


def _dew_pressure(z: list[float]) -> float:
    return 1.0 / (z[0] / P_SAT_LIGHT + z[1] / P_SAT_HEAVY)


class TestClosure:
    """收敛后的硬性衡算关系。"""

    def test_composition_sums_close_to_one(self):
        res = isothermal_flash([0.5, 0.5], _k(43.0))
        assert res.phase == "two_phase"
        assert 0.0 < res.vapor_fraction < 1.0
        assert sum(res.liquid_composition) == pytest.approx(1.0, abs=TOL)
        assert sum(res.vapor_composition) == pytest.approx(1.0, abs=TOL)

    def test_material_balance_residual(self):
        z = [0.35, 0.65]
        res = isothermal_flash(z, _k(32.0))  # 该组成的泡点 ≈37.0、露点 ≈26.8 kPa
        assert res.phase == "two_phase"
        v = res.vapor_fraction
        for i in range(2):
            lhs = (1.0 - v) * res.liquid_composition[i] + v * res.vapor_composition[i]
            assert lhs == pytest.approx(z[i], abs=1e-12)

    def test_binary_closed_form(self):
        # 二元 Rachford-Rice 有闭式解，用来独立核对二分法求根结果
        z = [0.5, 0.5]
        res = isothermal_flash(z, _k(43.0))
        k1, k2 = res.k_values
        v_closed = -(z[0] * (k1 - 1.0) + z[1] * (k2 - 1.0)) / ((k1 - 1.0) * (k2 - 1.0))
        assert res.vapor_fraction == pytest.approx(v_closed, abs=1e-9)


class TestSinglePhase:
    """两相区之外：返回单相判定与所处一侧的理由，不给假汽化率。"""

    def test_all_k_below_one_is_liquid(self):
        res = isothermal_flash([0.5, 0.5], _k(80.0))  # P 高于两个 P_sat
        assert all(ki < 1.0 for ki in res.k_values)
        assert res.phase == "liquid"
        assert res.vapor_fraction == 0.0
        assert res.liquid_composition == [0.5, 0.5]
        assert res.vapor_composition is None
        assert "泡点" in res.reason

    def test_all_k_above_one_is_vapor(self):
        res = isothermal_flash([0.5, 0.5], _k(15.0))  # P 低于两个 P_sat
        assert all(ki > 1.0 for ki in res.k_values)
        assert res.phase == "vapor"
        assert res.vapor_fraction == 1.0
        assert res.vapor_composition == [0.5, 0.5]
        assert res.liquid_composition is None
        assert "露点" in res.reason

    def test_mixed_k_can_still_be_liquid(self):
        # K1>1>K2 但 ΣzK ≤ 1（P=50 > 泡点压力 44.3）：
        # 严格的泡点/露点检验覆盖「全 K 同侧」朴素规则之外的情形
        res = isothermal_flash([0.5, 0.5], _k(50.0))
        assert res.k_values[0] > 1.0 > res.k_values[1]
        assert res.phase == "liquid"
        assert res.vapor_fraction == 0.0

    def test_mixed_k_can_still_be_vapor(self):
        # K1>1>K2 但 Σz/K ≤ 1（P=35 低于该组成的露点压力 39.8）
        res = isothermal_flash([0.7, 0.3], _k(35.0))
        assert res.k_values[0] > 1.0 > res.k_values[1]
        assert res.phase == "vapor"
        assert res.vapor_fraction == 1.0


class TestMonotonicity:
    def test_vapor_fraction_non_increasing_in_pressure(self):
        # 温度与进料组成不变，仅升高压力：汽化率不得上升
        z = [0.5, 0.5]
        pressures = [30.0 + 0.5 * i for i in range(33)]  # 30 → 46 kPa，跨越露点与泡点
        fractions = [isothermal_flash(z, _k(p)).vapor_fraction for p in pressures]
        assert fractions[0] == 1.0  # 露点以下：全汽
        assert fractions[-1] == 0.0  # 泡点以上：全液
        for before, after in zip(fractions, fractions[1:]):
            assert after <= before + 1e-12


class TestApproachLimits:
    """逼近泡点 V→0、逼近露点 V→1（用逼近序列检验，而非只断言有解）。"""

    @pytest.mark.parametrize("z", [[0.5, 0.5], [0.3, 0.7]])
    def test_approach_bubble_point(self, z):
        p_bubble = _bubble_pressure(z)
        fractions = []
        for exponent in range(1, 7):
            pressure = p_bubble * (1.0 - 10.0**-exponent)
            res = isothermal_flash(z, _k(pressure))
            assert res.phase == "two_phase"
            fractions.append(res.vapor_fraction)
        assert fractions[-1] < 1e-5  # 足够逼近泡点时 V 任意小
        assert all(b < a for a, b in zip(fractions, fractions[1:]))  # 且单调趋近
        # 泡点压力之上 → 单相液体，V 恒为 0
        above = isothermal_flash(z, _k(p_bubble * 1.0001))
        assert above.phase == "liquid"
        assert above.vapor_fraction == 0.0

    @pytest.mark.parametrize("z", [[0.5, 0.5], [0.3, 0.7]])
    def test_approach_dew_point(self, z):
        p_dew = _dew_pressure(z)
        liquid_fractions = []
        for exponent in range(1, 7):
            pressure = p_dew * (1.0 + 10.0**-exponent)
            res = isothermal_flash(z, _k(pressure))
            assert res.phase == "two_phase"
            liquid_fractions.append(1.0 - res.vapor_fraction)
        assert liquid_fractions[-1] < 1e-5  # 足够逼近露点时 1-V 任意小
        assert all(b < a for a, b in zip(liquid_fractions, liquid_fractions[1:]))
        # 露点压力之下 → 单相蒸汽，V 恒为 1
        below = isothermal_flash(z, _k(p_dew * 0.9999))
        assert below.phase == "vapor"
        assert below.vapor_fraction == 1.0
