"""非理想路径的 API 层验证：登记、非法输入、作业求解、可追溯性、
登记项升级不影响历史快照、以及理想/非理想作业并发互不干扰。
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.seed import DEMO_ETHANOL_WATER_ID

DEMO_NONIDEAL = DEMO_ETHANOL_WATER_ID
DEMO_POINT = {"temperature": 343.15, "pressure": 50.0, "feed": [0.3, 0.7]}

ETHANOL_ANTOINE = {"A": 16.68631, "B": 3681.081, "C": -46.424}
WATER_ANTOINE = {"A": 16.3872, "B": 3885.70, "C": -42.98}
MARGULES_PARAMS = {"model": "margules", "a12": 1.6798, "a21": 0.9227}


def _ideal_ethanol_water_payload(name: str = "eth-water-ideal") -> dict:
    return {
        "name": name,
        "source": "antoine",
        "components": [
            {"name": "ethanol", "antoine": ETHANOL_ANTOINE},
            {"name": "water", "antoine": WATER_ANTOINE},
        ],
    }


def _submit(client, definition_id, point=DEMO_POINT):
    return client.post(
        "/api/v1/jobs",
        json={"property_definition_id": definition_id, "points": [point]},
    )


class TestNonidealRegistration:
    def test_register_nonideal_definition(self, client):
        payload = _ideal_ethanol_water_payload("nonideal-inline-reg")
        payload["activity_model"] = MARGULES_PARAMS
        r = client.post("/api/v1/property-definitions", json=payload)
        assert r.status_code == 201
        assert r.json()["activity_model"] == MARGULES_PARAMS
        got = client.get(f"/api/v1/property-definitions/{r.json()['id']}")
        assert got.json()["activity_model"] == MARGULES_PARAMS

    @pytest.mark.parametrize(
        "activity_model,expected_type",
        [
            ({"model": "margules", "a21": 0.5}, "ACTIVITY_MODEL_PARAMETER_MISSING"),
            ({"model": "margules", "a12": 1.0}, "ACTIVITY_MODEL_PARAMETER_MISSING"),
            ({"model": "margules", "a12": 800.0, "a21": 800.0}, "ACTIVITY_MODEL_PARAMETER_INVALID"),
        ],
    )
    def test_register_with_bad_activity_model_rejected(
        self, client, activity_model, expected_type
    ):
        payload = _ideal_ethanol_water_payload("bad-model")
        payload["activity_model"] = activity_model
        r = client.post("/api/v1/property-definitions", json=payload)
        assert r.status_code == 422
        assert r.json()["error"]["type"] == expected_type

    def test_register_with_nan_parameter_rejected(self, client):
        # httpx 不允许序列化 NaN，直接发原始 JSON 体（Python json.loads 接受 NaN 字面量）
        r = client.post(
            "/api/v1/property-definitions",
            content=(
                '{"name": "nan-model", "source": "direct_psat", '
                '"components": [{"name": "a", "psat": 80.0}, '
                '{"name": "b", "psat": 20.0}], '
                '"activity_model": {"model": "margules", "a12": NaN, "a21": 0.5}}'
            ),
            headers={"content-type": "application/json"},
        )
        assert r.status_code == 422
        assert r.json()["error"]["type"] == "ACTIVITY_MODEL_PARAMETER_INVALID"

    def test_inline_nonideal_job_with_bad_model_rejected_and_not_persisted(self, client):
        r = client.post(
            "/api/v1/jobs",
            json={
                "property_definition": {
                    "name": "inline-bad",
                    "source": "direct_psat",
                    "components": [
                        {"name": "a", "psat": 80.0},
                        {"name": "b", "psat": 20.0},
                    ],
                    "activity_model": {"a12": 800.0, "a21": 800.0},
                },
                "points": [{"temperature": 300.0, "pressure": 40.0, "feed": [0.5, 0.5]}],
            },
        )
        assert r.status_code == 422
        assert r.json()["error"]["type"] == "ACTIVITY_MODEL_PARAMETER_INVALID"
        assert client.get("/api/v1/jobs").json() == []


class TestNonidealJobs:
    def test_seeded_nonideal_demo_job(self, client):
        r = _submit(client, DEMO_NONIDEAL)
        assert r.status_code == 201
        point = r.json()["points"][0]
        assert point["phase"] == "two_phase"
        assert point["vapor_fraction"] == pytest.approx(0.6634, abs=2e-3)
        assert point["liquid_model"] == "margules"
        assert len(point["activity_coefficients"]) == 2
        assert all(g > 0 for g in point["activity_coefficients"])
        # K 与 γ·P^sat/P 一致；衡算闭合；组成加和
        for i in range(2):
            assert point["k_values"][i] == pytest.approx(
                point["activity_coefficients"][i]
                * point["saturation_pressures"][i]
                / DEMO_POINT["pressure"],
                rel=1e-8,
            )
        assert sum(point["liquid_composition"]) == pytest.approx(1.0, abs=1e-9)
        assert sum(point["vapor_composition"]) == pytest.approx(1.0, abs=1e-9)
        assert point["material_balance_residual"] < 1e-9
        assert point["nonideal_k_residual"] <= 1e-10
        assert point["nonideal_iterations"] >= 1

    def test_inline_nonideal_definition_job(self, client):
        payload = _ideal_ethanol_water_payload("inline-nonideal")
        payload["activity_model"] = MARGULES_PARAMS
        r = client.post(
            "/api/v1/jobs",
            json={"property_definition": payload, "points": [DEMO_POINT]},
        )
        assert r.status_code == 201
        point = r.json()["points"][0]
        assert point["liquid_model"] == "margules"
        assert point["phase"] == "two_phase"

    def test_ideal_comparison_point_is_liquid(self, client):
        """同一工况点，理想假设判单相液相——非理想示范偏离方向的对照。"""
        r = client.post(
            "/api/v1/jobs",
            json={
                "property_definition": _ideal_ethanol_water_payload(),
                "points": [DEMO_POINT],
            },
        )
        point = r.json()["points"][0]
        assert point["liquid_model"] == "ideal"
        assert point["activity_coefficients"] is None
        assert point["nonideal_k_residual"] is None
        assert point["phase"] == "liquid"
        assert point["vapor_fraction"] == 0.0

    def test_single_phase_nonideal_points_have_reasons(self, client):
        for pressure, phase in ((90.0, "liquid"), (15.0, "vapor")):
            r = _submit(
                client,
                DEMO_NONIDEAL,
                {"temperature": 343.15, "pressure": pressure, "feed": [0.3, 0.7]},
            )
            point = r.json()["points"][0]
            assert point["phase"] == phase
            assert point["reason"]
            assert point["liquid_model"] == "margules"

    def test_traceability_snapshot_carries_model_and_parameters(self, client):
        """历史作业必须可查：当时用的理想/非理想，以及非理想的具体参数。"""
        job = _submit(client, DEMO_NONIDEAL).json()
        refetched = client.get(f"/api/v1/jobs/{job['id']}").json()
        snapshot = refetched["property_definition"]
        assert snapshot["activity_model"] == MARGULES_PARAMS
        assert refetched["points"][0]["liquid_model"] == "margules"


class TestUpgradePreservesHistory:
    def test_upgrade_then_history_unchanged_and_new_job_nonideal(self, client):
        # 1) 登记一份理想物性，跑一个作业并留存完整结果
        pd_id = client.post(
            "/api/v1/property-definitions", json=_ideal_ethanol_water_payload()
        ).json()["id"]
        job_before = _submit(client, pd_id).json()
        assert job_before["points"][0]["liquid_model"] == "ideal"
        assert job_before["property_definition"]["activity_model"] is None

        # 2) 追加 Margules 参数升级为非理想
        upgrade = client.post(
            f"/api/v1/property-definitions/{pd_id}/activity-model",
            json={"activity_model": MARGULES_PARAMS},
        )
        assert upgrade.status_code == 200
        assert upgrade.json()["activity_model"] == MARGULES_PARAMS

        # 3) 历史作业逐字节不变（快照、各点结果都不回写）
        job_after = client.get(f"/api/v1/jobs/{job_before['id']}").json()
        assert job_after == job_before
        point_url = f"/api/v1/jobs/{job_before['id']}/points/0"
        point_before = client.get(point_url).json()
        assert point_before["liquid_model"] == "ideal"

        # 4) 升级后的新作业走非理想路径，结果与旧作业不同且快照带参数
        job_new = _submit(client, pd_id).json()
        point_new = job_new["points"][0]
        assert point_new["liquid_model"] == "margules"
        assert point_new["phase"] == "two_phase"
        assert point_new["vapor_fraction"] != job_before["points"][0]["vapor_fraction"]
        assert job_new["property_definition"]["activity_model"] == MARGULES_PARAMS

    def test_upgrade_nonexistent_404(self, client):
        r = client.post(
            "/api/v1/property-definitions/pd_ghost/activity-model",
            json={"activity_model": MARGULES_PARAMS},
        )
        assert r.status_code == 404
        assert r.json()["error"]["type"] == "PROPERTY_DEFINITION_NOT_FOUND"

    def test_upgrade_already_nonideal_conflicts(self, client):
        payload = _ideal_ethanol_water_payload("nonideal-once")
        payload["activity_model"] = MARGULES_PARAMS
        pd_id = client.post(
            "/api/v1/property-definitions", json=payload
        ).json()["id"]
        r = client.post(
            f"/api/v1/property-definitions/{pd_id}/activity-model",
            json={"activity_model": MARGULES_PARAMS},
        )
        assert r.status_code == 409
        assert r.json()["error"]["type"] == "ACTIVITY_MODEL_ALREADY_PRESENT"

    def test_upgrade_with_invalid_parameters_rejected_and_keeps_ideal(self, client):
        pd_id = client.post(
            "/api/v1/property-definitions", json=_ideal_ethanol_water_payload()
        ).json()["id"]
        r = client.post(
            f"/api/v1/property-definitions/{pd_id}/activity-model",
            json={"activity_model": {"a12": 800.0, "a21": 800.0}},
        )
        assert r.status_code == 422
        assert r.json()["error"]["type"] == "ACTIVITY_MODEL_PARAMETER_INVALID"
        # 升级失败后登记项仍是理想定义，继续按理想工作
        record = client.get(f"/api/v1/property-definitions/{pd_id}").json()
        assert record["activity_model"] is None
        job = _submit(client, pd_id).json()
        assert job["points"][0]["liquid_model"] == "ideal"


class TestNonidealConcurrency:
    def test_mixed_ideal_and_nonideal_jobs_are_isolated(self, app):
        workers = 8

        def run_job(worker: int):
            z1 = round(0.1 + 0.05 * worker, 10)
            definition = (
                DEMO_NONIDEAL if worker % 2 == 0 else "pd-demo-pentane-hexane"
            )
            point = (
                {"temperature": 343.15, "pressure": 50.0, "feed": [z1, round(1 - z1, 10)]}
                if worker % 2 == 0
                else {"temperature": 298.15, "pressure": 40.0, "feed": [z1, round(1 - z1, 10)]}
            )
            with TestClient(app) as client:
                r = client.post(
                    "/api/v1/jobs",
                    json={"property_definition_id": definition, "points": [point]},
                )
            assert r.status_code == 201
            return worker, definition, r.json()

        with ThreadPoolExecutor(max_workers=workers) as pool:
            submitted = list(pool.map(run_job, range(workers)))

        with TestClient(app) as client:
            for worker, definition, job in submitted:
                stored = client.get(f"/api/v1/jobs/{job['id']}").json()
                point = stored["points"][0]
                expected_model = "margules" if definition == DEMO_NONIDEAL else "ideal"
                assert point["liquid_model"] == expected_model
                composition = point["liquid_composition"] or point["vapor_composition"]
                assert sum(composition) == pytest.approx(1.0, abs=1e-9)
                if point["phase"] == "two_phase":
                    assert point["material_balance_residual"] < 1e-9
                    z = point["input"]["feed"]
                    v = point["vapor_fraction"]
                    for i in range(2):
                        reconstructed = (
                            (1 - v) * point["liquid_composition"][i]
                            + v * point["vapor_composition"][i]
                        )
                        assert reconstructed == pytest.approx(z[i], abs=1e-9)
