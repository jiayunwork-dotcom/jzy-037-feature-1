"""兼容性守卫：非理想支持引入后，理想路径的行为与结果必须逐项不变。

- 同一份理想物性（Antoine 与 direct_psat 两种来源）、同一组工况点，
  经 HTTP API 算出的结果与直接调用历史内核（isothermal_flash +
  antoine.k_values）逐字段一致；
- 一组钉死的黄金值（正戊烷/正己烷手算锚点）防止数值悄然漂移；
- 旧请求体（不含 liquid_model/activity_model）继续被接受，且
  新增字段取稳定的缺省值。
"""
from __future__ import annotations

import pytest

from app import antoine
from app.flash import isothermal_flash
from app.seed import DEMO_PROPERTY_DEFINITION_ID
from app.schemas import PropertyDefinitionCreate

DEMO = DEMO_PROPERTY_DEFINITION_ID

# 跨液相/汽相/两相的代表性工况（正戊烷/正己烷 @298.15 K）
PRESSURE_GRID = [22.0, 26.0, 30.0, 31.2, 35.0, 40.0, 43.0, 44.3, 50.0, 68.0]
FEEDS = [[0.5, 0.5], [0.3, 0.7], [0.8, 0.2]]


def _legacy_expected(prop: PropertyDefinitionCreate, temperature: float,
                     pressure: float, feed: list[float]) -> dict:
    """改动前的历史计算路径：理想 K + Rachford-Rice 二分。"""
    k_vals = antoine.k_values(prop, temperature, pressure)
    psat = antoine.saturation_pressures(prop, temperature)
    result = isothermal_flash(list(feed), k_vals)
    return {
        "phase": result.phase,
        "vapor_fraction": result.vapor_fraction,
        "liquid_composition": result.liquid_composition,
        "vapor_composition": result.vapor_composition,
        "k_values": result.k_values,
        "saturation_pressures": psat,
        "bubble_sum": result.bubble_sum,
        "dew_sum": result.dew_sum,
        "rr_residual": result.rr_residual,
        "rr_iterations": result.rr_iterations,
        "reason": result.reason,
    }


def _assert_point_matches_legacy(point: dict, expected: dict) -> None:
    for field in (
        "phase", "liquid_composition", "vapor_composition",
        "bubble_sum", "dew_sum", "rr_residual", "rr_iterations", "reason",
    ):
        assert point[field] == expected[field], field
    assert point["vapor_fraction"] == pytest.approx(expected["vapor_fraction"], abs=0.0)
    assert point["k_values"] == pytest.approx(expected["k_values"], abs=0.0)
    assert point["saturation_pressures"] == pytest.approx(
        expected["saturation_pressures"], rel=1e-14, abs=1e-12
    )


class TestIdealAntoineMatchesLegacyKernel:
    @pytest.mark.parametrize("feed", FEEDS)
    @pytest.mark.parametrize("pressure", PRESSURE_GRID)
    def test_identical_to_legacy_kernel(self, client, feed, pressure):
        store = client.app.state.store
        prop_model = PropertyDefinitionCreate.model_validate(
            store.get_property_definition(DEMO)
        )
        r = client.post(
            "/api/v1/jobs",
            json={
                "property_definition_id": DEMO,
                "points": [{"temperature": 298.15, "pressure": pressure, "feed": feed}],
            },
        )
        assert r.status_code == 201
        point = r.json()["points"][0]
        expected = _legacy_expected(prop_model, 298.15, pressure, feed)
        _assert_point_matches_legacy(point, expected)


class TestIdealDirectPsatMatchesLegacyKernel:
    @pytest.mark.parametrize("pressure", [15.0, 30.0, 40.0, 50.0, 70.0, 90.0])
    def test_inline_direct_psat_identical(self, client, pressure):
        prop = PropertyDefinitionCreate.model_validate({
            "name": "inline",
            "source": "direct_psat",
            "components": [{"name": "a", "psat": 80.0}, {"name": "b", "psat": 20.0}],
        })
        feed = [0.5, 0.5]
        r = client.post(
            "/api/v1/jobs",
            json={
                "property_definition": {
                    "name": "inline",
                    "source": "direct_psat",
                    "components": [
                        {"name": "a", "psat": 80.0},
                        {"name": "b", "psat": 20.0},
                    ],
                },
                "points": [{"temperature": 300.0, "pressure": pressure, "feed": feed}],
            },
        )
        assert r.status_code == 201
        point = r.json()["points"][0]
        expected = _legacy_expected(prop, 300.0, pressure, feed)
        _assert_point_matches_legacy(point, expected)


class TestGoldenValues:
    """钉死的手算黄金值：任何使理想结果漂移的改动都应在此暴露。"""

    def test_hand_check_43kpa_equimolar(self, client):
        point = client.post(
            "/api/v1/jobs",
            json={
                "property_definition_id": DEMO,
                "points": [{"temperature": 298.15, "pressure": 43.0, "feed": [0.5, 0.5]}],
            },
        ).json()["points"][0]
        assert point["phase"] == "two_phase"
        assert point["vapor_fraction"] == pytest.approx(0.094531, abs=2e-6)
        assert point["k_values"] == pytest.approx([1.590203, 0.469043], abs=2e-6)
        assert point["rr_iterations"] == 35
        assert point["bubble_sum"] == pytest.approx(1.029623, abs=2e-6)
        assert point["dew_sum"] == pytest.approx(1.380425, abs=2e-6)

    def test_inline_80_20_at_40_closed_form(self, client):
        point = client.post(
            "/api/v1/jobs",
            json={
                "property_definition": {
                    "name": "inline",
                    "source": "direct_psat",
                    "components": [{"name": "a", "psat": 80.0}, {"name": "b", "psat": 20.0}],
                },
                "points": [{"temperature": 300.0, "pressure": 40.0, "feed": [0.5, 0.5]}],
            },
        ).json()["points"][0]
        assert point["phase"] == "two_phase"
        assert point["vapor_fraction"] == pytest.approx(0.5, abs=1e-12)
        assert point["liquid_composition"] == pytest.approx([1 / 3, 2 / 3], abs=1e-12)
        assert point["vapor_composition"] == pytest.approx([2 / 3, 1 / 3], abs=1e-12)


class TestLegacyRequestShapeAndDefaults:
    def test_legacy_request_body_accepted_with_stable_defaults(self, client):
        # 请求体中完全没有液相模型字段：必须按理想体系处理
        r = client.post(
            "/api/v1/jobs",
            json={
                "property_definition_id": DEMO,
                "points": [{"temperature": 298.15, "pressure": 43.0, "feed": [0.5, 0.5]}],
            },
        )
        assert r.status_code == 201
        point = r.json()["points"][0]
        assert point["liquid_model"] == "ideal"
        assert point["activity_model"] is None
        assert point["activity_coefficients"] is None
        assert point["nonideal_residual"] is None
        assert point["nonideal_iterations"] is None
        snap = r.json()["property_definition"]
        assert snap["liquid_model"] == "ideal"
        assert snap["activity_model"] is None

    def test_register_legacy_shape_defaults_to_ideal(self, client):
        r = client.post(
            "/api/v1/property-definitions",
            json={
                "name": "legacy-shape",
                "source": "direct_psat",
                "components": [{"name": "a", "psat": 80.0}, {"name": "b", "psat": 20.0}],
            },
        )
        assert r.status_code == 201
        body = r.json()
        assert body["liquid_model"] == "ideal"
        assert body["activity_model"] is None
