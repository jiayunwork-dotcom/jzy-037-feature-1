"""非理想液相闪蒸（Margules）求解正确性。

覆盖：
- Margules 公式手算锚点（含无限稀释极限、理想退化）；
- 参数/组成非法被带类型错误拒绝；
- 闪蒸闭合：Σx=1、Σy=1、物料衡算、K = γ·P^sat/P、V∈(0,1)；
- 单相两侧判定；a12=a21=0 时与理想内核结果严格一致；
- 乙醇/水示范点相对理想假设的偏离方向（正偏差 → 更易汽化）；
- 外层迭代不收敛时拒绝返回近似解。
"""
from __future__ import annotations

import math

import pytest

from app import activity
from app.errors import (
    ActivityCoefficientInvalid,
    ActivityCompositionOutOfDomain,
    ActivityModelParameterInvalid,
    ActivityModelParameterMissing,
    NonIdealFlashNonConvergence,
)
from app.flash import isothermal_flash
from app.nonideal_flash import isothermal_flash_nonideal
from app.schemas import ActivityModelSpec

# 乙醇/水示范参数（343.15 K），与种子定义一致
A12, A21 = 1.6798, 0.9227
PSAT = [72.28762329963882, 31.25571268637961]  # kPa，乙醇、水 @343.15 K

KW = dict(k_tol=1e-10, ln_k_step=1.0, max_iter=200, f_tol=1e-12, x_tol=1e-14, rr_max_iter=200)


def _model(a12=A12, a21=A21) -> ActivityModelSpec:
    return ActivityModelSpec(model="margules", a12=a12, a21=a21)


def _flash(z, pressure, model: ActivityModelSpec | None = None, **overrides):
    params = dict(KW)
    params.update(overrides)
    return isothermal_flash_nonideal(z, PSAT, pressure, model or _model(), **params)


class TestMargulesFormula:
    def test_hand_computed_values_at_x1_03(self):
        # x1=0.3, x2=0.7（手算见 README「非理想手算示例」）
        # ln γ1 = 0.49·[1.6798 + 2·(0.9227−1.6798)·0.3]
        #       = 0.49·(1.6798 − 0.45426) = 0.60050...
        # ln γ2 = 0.09·[0.9227 + 2·(1.6798−0.9227)·0.7]
        #       = 0.09·(0.9227 + 1.05994) = 0.17844...
        ln_g1, ln_g2 = activity.margules_ln_activity_coefficients(0.3, A12, A21)
        assert ln_g1 == pytest.approx(0.6005146, abs=1e-7)
        assert ln_g2 == pytest.approx(0.1784376, abs=1e-7)
        g1, g2 = activity.margules_activity_coefficients(0.3, A12, A21)
        assert g1 == pytest.approx(1.82306, rel=1e-4)
        assert g2 == pytest.approx(1.19535, rel=1e-4)

    def test_infinite_dilution_limits(self):
        # x1→0：ln γ1 → a12；x2→0：ln γ2 → a21；另一端活度系数 → 1
        ln_g1_0, ln_g2_0 = activity.margules_ln_activity_coefficients(0.0, A12, A21)
        assert ln_g1_0 == pytest.approx(A12)
        assert ln_g2_0 == pytest.approx(0.0)
        ln_g1_1, ln_g2_1 = activity.margules_ln_activity_coefficients(1.0, A12, A21)
        assert ln_g1_1 == pytest.approx(0.0)
        assert ln_g2_1 == pytest.approx(A21)
        g1, _ = activity.margules_activity_coefficients(0.0, A12, A21)
        assert g1 == pytest.approx(math.exp(A12))
        assert g1 == pytest.approx(5.3645, rel=1e-3)  # 乙醇无限稀释活度系数量级

    def test_symmetric_parameters(self):
        # a12=a21=A 时退化为两后缀 Margules：ln γ1 = A·x2²，ln γ2 = A·x1²
        ln_g1, ln_g2 = activity.margules_ln_activity_coefficients(0.4, 0.8, 0.8)
        assert ln_g1 == pytest.approx(0.8 * 0.6**2)
        assert ln_g2 == pytest.approx(0.8 * 0.4**2)

    def test_zero_parameters_give_unit_gamma(self):
        g1, g2 = activity.margules_activity_coefficients(0.37, 0.0, 0.0)
        assert g1 == pytest.approx(1.0) and g2 == pytest.approx(1.0)


class TestParameterValidation:
    def test_missing_parameter_rejected(self):
        with pytest.raises(ActivityModelParameterMissing):
            activity.validate_activity_model_spec(ActivityModelSpec(a12=1.0, a21=None))
        with pytest.raises(ActivityModelParameterMissing):
            _flash([0.5, 0.5], 50.0, ActivityModelSpec(a12=1.0))

    def test_nonfinite_parameter_rejected(self):
        with pytest.raises(ActivityModelParameterInvalid):
            activity.validate_activity_model_spec(
                ActivityModelSpec(a12=float("nan"), a21=0.5)
            )
        with pytest.raises(ActivityModelParameterInvalid):
            activity.validate_activity_model_spec(
                ActivityModelSpec(a12=1.0, a21=float("inf"))
            )

    def test_parameters_yielding_nonpositive_gamma_rejected_upfront(self):
        # a=800：x1=0 处 γ1 = e^800 溢出 → 登记阶段全域扫描即拒绝
        with pytest.raises(ActivityModelParameterInvalid) as exc_info:
            activity.validate_activity_model_spec(
                ActivityModelSpec(a12=800.0, a21=800.0)
            )
        assert exc_info.value.details["x1"] == 0.0

    def test_composition_out_of_domain(self):
        with pytest.raises(ActivityCompositionOutOfDomain):
            activity.margules_activity_coefficients(-0.01, A12, A21)
        with pytest.raises(ActivityCompositionOutOfDomain):
            activity.margules_activity_coefficients(1.01, A12, A21)
        with pytest.raises(ActivityCompositionOutOfDomain):
            activity.margules_activity_coefficients(float("nan"), A12, A21)

    def test_none_spec_is_ideal_noop(self):
        assert activity.validate_activity_model_spec(None) is None


class TestTwoPhaseClosure:
    @pytest.mark.parametrize(
        "z,pressure",
        [
            ([0.1, 0.9], 40.0),
            ([0.2, 0.8], 50.0),
            ([0.3, 0.7], 50.0),
            ([0.4, 0.6], 60.0),
            ([0.5, 0.5], 65.0),
        ],
    )
    def test_hard_closure_relations(self, z, pressure):
        res = _flash(z, pressure)
        assert res.phase == "two_phase"
        v = res.vapor_fraction
        assert 0.0 < v < 1.0
        x, y = res.liquid_composition, res.vapor_composition
        # 组成加和
        assert math.fsum(x) == pytest.approx(1.0, abs=1e-9)
        assert math.fsum(y) == pytest.approx(1.0, abs=1e-9)
        # 物料衡算按汽化率加权还原进料
        for i in range(2):
            assert (1 - v) * x[i] + v * y[i] == pytest.approx(z[i], abs=1e-9)
        # 平衡常数与 γ(x)·P^sat/P 一致（组成依赖 K 的核心关系）
        for i in range(2):
            assert res.k_values[i] == pytest.approx(
                res.activity_coefficients[i] * PSAT[i] / pressure, rel=1e-8
            )
        # 汽液组成与 K 互相一致
        for i in range(2):
            assert y[i] == pytest.approx(res.k_values[i] * x[i], rel=1e-8)
        assert res.nonideal_k_residual <= 1e-10
        assert res.rr_residual is not None and abs(res.rr_residual) <= 1e-12

    def test_demo_point_hand_anchor(self):
        """README 示范点：343.15 K、50 kPa、z_乙醇=0.3 → V≈0.663，液相富水。"""
        res = _flash([0.3, 0.7], 50.0)
        assert res.phase == "two_phase"
        assert res.vapor_fraction == pytest.approx(0.6634, abs=2e-3)
        assert res.liquid_composition[0] < 0.15  # 液相以水为主
        assert res.vapor_composition[0] > 0.35  # 汽相明显富集乙醇
        assert res.activity_coefficients[0] == pytest.approx(3.82, rel=2e-2)

    def test_positive_deviation_promotes_vaporization(self):
        """同一工况下非理想（正偏差）汽化率必须不小于理想假设。"""
        z, pressure = [0.3, 0.7], 50.0
        ideal = isothermal_flash(z, [p / pressure for p in PSAT])
        nonideal = _flash(z, pressure)
        assert ideal.phase == "liquid" and ideal.vapor_fraction == 0.0
        assert nonideal.phase == "two_phase" and nonideal.vapor_fraction > 0.5

    def test_zero_parameters_reproduce_ideal_kernel(self):
        """a12=a21=0 ⇒ γ≡1，非理想路径必须与理想内核算出同一个汽化率。"""
        z, pressure = [0.3, 0.7], 40.0
        ideal = isothermal_flash(z, [p / pressure for p in PSAT])
        nonideal = _flash(z, pressure, _model(0.0, 0.0), k_tol=1e-12)
        assert nonideal.phase == ideal.phase == "two_phase"
        assert nonideal.vapor_fraction == pytest.approx(ideal.vapor_fraction, abs=1e-12)
        assert nonideal.liquid_composition[0] == pytest.approx(
            ideal.liquid_composition[0], abs=1e-10
        )
        assert nonideal.activity_coefficients == pytest.approx([1.0, 1.0])


class TestSinglePhase:
    def test_high_pressure_liquid(self):
        res = _flash([0.3, 0.7], 90.0)
        assert res.phase == "liquid"
        assert res.vapor_fraction == 0.0
        assert res.liquid_composition == pytest.approx([0.3, 0.7])
        assert res.vapor_composition is None
        assert res.bubble_sum <= 1.0
        assert "泡点" in res.reason

    def test_low_pressure_vapor(self):
        res = _flash([0.3, 0.7], 15.0)
        assert res.phase == "vapor"
        assert res.vapor_fraction == 1.0
        assert res.vapor_composition == pytest.approx([0.3, 0.7])
        assert res.liquid_composition is None
        assert res.dew_sum <= 1.0
        assert "露点" in res.reason

    def test_pressure_sweep_monotone(self):
        pressures = [15.0 + 0.6 * i for i in range(141)]
        fractions = [_flash([0.3, 0.7], p).vapor_fraction for p in pressures]
        assert fractions[0] == 1.0 and fractions[-1] == 0.0
        for before, after in zip(fractions, fractions[1:]):
            assert after <= before + 1e-12


class TestNonConvergence:
    def test_zero_iteration_budget_rejected(self):
        # 迭代预算为 0：绝不返回未收敛的近似解
        with pytest.raises(NonIdealFlashNonConvergence) as exc_info:
            _flash([0.3, 0.7], 50.0, max_iter=0)
        assert exc_info.value.details["max_iterations"] == 0

    def test_tight_budget_rejected_then_full_budget_converges(self):
        # 该点普通逐次代入在相界附近周期弹跳，需要约 50 轮：
        # 预算不足必须拒绝；给足预算（阻尼 Wegstein）必须收敛且闭合
        with pytest.raises(NonIdealFlashNonConvergence):
            _flash([0.4, 0.6], 65.0, max_iter=10)
        res = _flash([0.4, 0.6], 65.0, max_iter=200)
        assert res.phase == "two_phase"
        v = res.vapor_fraction
        for i in range(2):
            assert (1 - v) * res.liquid_composition[i] + v * res.vapor_composition[
                i
            ] == pytest.approx([0.4, 0.6][i], abs=1e-9)

    def test_feed_pressure_grid_all_converge(self):
        # 乙醇/水示范参数下的稠密网格：每个点都要收敛（不收敛即报错失败）
        for z10 in range(1, 10):
            z1 = z10 / 10
            for pressure_kpa in range(20, 75, 5):
                res = _flash([z1, 1 - z1], float(pressure_kpa))
                if res.phase == "two_phase":
                    assert 0.0 < res.vapor_fraction < 1.0
