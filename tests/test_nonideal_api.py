"""非理想路径的 HTTP 层校验：参数缺失/非法、内联非理想、非法作业不落库。"""
from __future__ import annotations

import pytest

NONIDEAL_COMPONENTS = [
    {"name": "ethanol", "antoine": {"A": 16.50917323, "B": 3578.90801, "C": -50.5}},
    {"name": "water", "antoine": {"A": 16.5698924, "B": 3984.92284, "C": -39.724}},
]


def _inline_definition(**overrides):
    payload = {
        "name": "eth-water-inline",
        "source": "antoine",
        "liquid_model": "van_laar",
        "activity_model": {"model": "van_laar", "A12": 1.75, "A21": 0.91},
        "components": NONIDEAL_COMPONENTS,
    }
    payload.update(overrides)
    return payload


class TestNonidealRegistration:
    @pytest.mark.parametrize("payload,expected", [
        (_inline_definition(activity_model=None), "ACTIVITY_MODEL_MISSING"),
        (_inline_definition(activity_model={"A12": 0.0, "A21": 0.91}),
         "ACTIVITY_MODEL_PARAMETER_INVALID"),
        (_inline_definition(activity_model={"A12": 1.75, "A21": -0.2}),
         "ACTIVITY_MODEL_PARAMETER_INVALID"),
        (
            {
                "name": "ideal-with-stray-params",
                "source": "direct_psat",
                "liquid_model": "ideal",
                "activity_model": {"A12": 1.75, "A21": 0.91},
                "components": [{"name": "a", "psat": 1.0}, {"name": "b", "psat": 2.0}],
            },
            "ACTIVITY_MODEL_PARAMETER_INVALID",
        ),
    ])
    def test_invalid_definitions_rejected(self, client, payload, expected):
        r = client.post("/api/v1/property-definitions", json=payload)
        assert r.status_code == 422
        assert r.json()["error"]["type"] == expected

    def test_nonideal_definition_roundtrip(self, client):
        r = client.post(
            "/api/v1/property-definitions", json=_inline_definition()
        )
        assert r.status_code == 201
        pid = r.json()["id"]
        body = client.get(f"/api/v1/property-definitions/{pid}").json()
        assert body["liquid_model"] == "van_laar"
        assert body["activity_model"] == {
            "model": "van_laar", "A12": 1.75, "A21": 0.91
        }


class TestNonidealJobs:
    def test_inline_nonideal_job(self, client):
        r = client.post(
            "/api/v1/jobs",
            json={
                "property_definition": _inline_definition(),
                "points": [{"temperature": 351.30, "pressure": 85.0, "feed": [0.5, 0.5]}],
            },
        )
        assert r.status_code == 201
        point = r.json()["points"][0]
        assert point["phase"] == "two_phase"
        assert point["liquid_model"] == "van_laar"
        assert point["activity_coefficients"] == pytest.approx([2.0226, 1.1293], abs=2e-3)
        assert point["material_balance_residual"] < 1e-9
        assert point["nonideal_residual"] < 1e-9

    def test_inline_nonideal_missing_activity_model_rejected(self, client):
        r = client.post(
            "/api/v1/jobs",
            json={
                "property_definition": _inline_definition(activity_model=None),
                "points": [{"temperature": 351.30, "pressure": 85.0, "feed": [0.5, 0.5]}],
            },
        )
        assert r.status_code == 422
        assert r.json()["error"]["type"] == "ACTIVITY_MODEL_MISSING"
        assert client.get("/api/v1/jobs").json() == []

    def test_seeded_nonideal_definition_end_to_end(self, client):
        r = client.post(
            "/api/v1/jobs",
            json={
                "property_definition_id": "pd-demo-ethanol-water-vanlaar",
                "points": [
                    {"label": "2p", "temperature": 351.30, "pressure": 85.0,
                     "feed": [0.5, 0.5]},
                    {"label": "liq", "temperature": 351.30, "pressure": 101.325,
                     "feed": [0.5, 0.5]},
                    {"label": "vap", "temperature": 351.30, "pressure": 60.0,
                     "feed": [0.5, 0.5]},
                ],
            },
        )
        assert r.status_code == 201
        phases = {p["label"]: p["phase"] for p in r.json()["points"]}
        assert phases == {"2p": "two_phase", "liq": "liquid", "vap": "vapor"}

    def test_nonideal_point_composition_normal_validation_still_applies(self, client):
        r = client.post(
            "/api/v1/jobs",
            json={
                "property_definition": _inline_definition(),
                "points": [{"temperature": 351.30, "pressure": 85.0, "feed": [0.6, 0.6]}],
            },
        )
        assert r.status_code == 422
        assert r.json()["error"]["type"] == "FEED_SUM_OUT_OF_TOLERANCE"

    def test_nonideal_direct_psat_source_also_supported(self, client):
        # 非理想不只服务于 Antoine：direct_psat 同样可用
        r = client.post(
            "/api/v1/jobs",
            json={
                "property_definition": {
                    "name": "nl-direct",
                    "source": "direct_psat",
                    "liquid_model": "van_laar",
                    "activity_model": {"A12": 1.75, "A21": 0.91},
                    "components": [
                        {"name": "a", "psat": 94.38},
                        {"name": "b", "psat": 44.01},
                    ],
                },
                "points": [{"temperature": 300.0, "pressure": 85.0, "feed": [0.5, 0.5]}],
            },
        )
        assert r.status_code == 201
        point = r.json()["points"][0]
        assert point["phase"] == "two_phase"
        assert point["saturation_pressures"] == [94.38, 44.01]
