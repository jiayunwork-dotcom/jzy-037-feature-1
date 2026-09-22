"""van Laar 活度系数模型：闭式锚点、端点行为、非法参数/组成拒绝。"""
from __future__ import annotations

import math

import pytest

from app.activity import VanLaarModel
from app.errors import (
    ActivityCoefficientInvalid,
    ActivityModelInputInvalid,
    ActivityModelParameterInvalid,
)


class TestVanLaarFormula:
    def test_ln_gamma_equals_parameters_at_infinite_dilution(self):
        # A12 = ln γ1∞（x1→0），A21 = ln γ2∞（x2→0）
        model = VanLaarModel(1.75, 0.91)
        gamma_at_x1_zero = model.activity_coefficients([0.0, 1.0])
        gamma_at_x2_zero = model.activity_coefficients([1.0, 0.0])
        assert gamma_at_x1_zero[0] == pytest.approx(math.exp(1.75))
        assert gamma_at_x1_zero[1] == pytest.approx(1.0)
        assert gamma_at_x2_zero[1] == pytest.approx(math.exp(0.91))
        assert gamma_at_x2_zero[0] == pytest.approx(1.0)

    def test_closed_form_at_equimolar(self):
        # x1=x2=0.5：ln γ1 = A12/(1+A12/A21)²
        A12, A21 = 1.75, 0.91
        model = VanLaarModel(A12, A21)
        g1, g2 = model.activity_coefficients([0.5, 0.5])
        assert math.log(g1) == pytest.approx(A12 / (1.0 + A12 / A21) ** 2)
        assert math.log(g2) == pytest.approx(A21 / (1.0 + A21 / A12) ** 2)

    def test_symmetric_parameters(self):
        # A12=A21 时，对称组成 x=(0.5,0.5) 上 γ1=γ2（且均非 1）
        model = VanLaarModel(0.5, 0.5)
        g1, g2 = model.activity_coefficients([0.5, 0.5])
        assert g1 != pytest.approx(1.0)  # 确实非理想，不是悄悄退化
        assert g1 == pytest.approx(g2)

    def test_pure_components_have_unit_activity(self):
        model = VanLaarModel(1.75, 0.91)
        assert model.activity_coefficients([0.0, 1.0])[1] == 1.0
        assert model.activity_coefficients([1.0, 0.0])[0] == 1.0

    def test_positive_deviation_gamma_above_one(self):
        # A>0 ⇒ γ ≥ 1（正偏差体系）
        model = VanLaarModel(1.75, 0.91)
        for x1 in [0.01, 0.1, 0.5, 0.9, 0.99]:
            g1, g2 = model.activity_coefficients([x1, 1.0 - x1])
            assert g1 >= 1.0 and g2 >= 1.0

    def test_results_are_composition_dependent(self):
        # 关键性质：γ 随 x 变化（不能偷懒假定组成恒定）
        model = VanLaarModel(1.75, 0.91)
        g_dilute = model.activity_coefficients([0.01, 0.99])
        g_rich = model.activity_coefficients([0.99, 0.01])
        assert g_dilute[0] != pytest.approx(g_rich[0], rel=0.05)


class TestParameterValidation:
    @pytest.mark.parametrize("A12,A21", [
        (0.0, 0.91),
        (1.75, 0.0),
        (-1.0, 0.91),
        (1.75, -0.2),
        (float("nan"), 0.91),
        (1.75, float("inf")),
        (float("-inf"), 0.91),
    ])
    def test_invalid_parameters_rejected(self, A12, A21):
        with pytest.raises(ActivityModelParameterInvalid):
            VanLaarModel(A12, A21)

    def test_overflow_activity_coefficient_rejected(self):
        # 参数大到 exp 溢出：拒绝非有限 γ，而不是把 inf/NaN 传下去
        with pytest.raises(ActivityCoefficientInvalid):
            VanLaarModel(1e4, 1e4).activity_coefficients([0.5, 0.5])


class TestCompositionDomain:
    def setup_method(self):
        self.model = VanLaarModel(1.75, 0.91)

    @pytest.mark.parametrize("x", [
        [1.2, -0.2],
        [-0.1, 1.1],
        [0.5, 0.6],
        [0.0, 0.0],
        [float("nan"), 0.5],
    ])
    def test_out_of_domain_composition_rejected(self, x):
        with pytest.raises(ActivityModelInputInvalid):
            self.model.activity_coefficients(x)

    def test_wrong_component_count_rejected(self):
        with pytest.raises(ActivityModelInputInvalid):
            self.model.activity_coefficients([0.5, 0.3, 0.2])

    def test_model_is_immutable(self):
        model = VanLaarModel(1.75, 0.91)
        with pytest.raises(Exception):
            model.A12 = 2.0  # type: ignore[misc]
