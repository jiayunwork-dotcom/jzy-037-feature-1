"""端到端 API：登记、提交、取回、手算锚点、作业间隔离。"""
from __future__ import annotations

import pytest

from app.seed import DEMO_PROPERTY_DEFINITION_ID

DEMO = DEMO_PROPERTY_DEFINITION_ID


def _job_payload(points: list[dict], **kwargs) -> dict:
    payload = {"points": points}
    payload.update(kwargs)
    return payload


class TestPropertyDefinitions:
    def test_demo_definition_seeded(self, client):
        r = client.get(f"/api/v1/property-definitions/{DEMO}")
        assert r.status_code == 200
        body = r.json()
        assert body["source"] == "antoine"
        assert [c["name"] for c in body["components"]] == ["n-pentane", "n-hexane"]
        assert body["temperature_unit"] == "K"
        assert body["pressure_unit"] == "kPa"

    def test_register_and_get(self, client):
        payload = {
            "name": "test-direct",
            "source": "direct_psat",
            "components": [{"name": "a", "psat": 80.0}, {"name": "b", "psat": 20.0}],
        }
        r = client.post("/api/v1/property-definitions", json=payload)
        assert r.status_code == 201
        created = r.json()
        assert created["id"].startswith("pd_")
        assert created["created_at"]

        again = client.get(f"/api/v1/property-definitions/{created['id']}")
        assert again.status_code == 200
        assert again.json()["components"][0]["psat"] == 80.0

        listing = client.get("/api/v1/property-definitions")
        assert created["id"] in {d["id"] for d in listing.json()}
        assert DEMO in {d["id"] for d in listing.json()}

    def test_missing_definition_404(self, client):
        r = client.get("/api/v1/property-definitions/pd_nope")
        assert r.status_code == 404
        assert r.json()["error"]["type"] == "PROPERTY_DEFINITION_NOT_FOUND"


class TestJobFlow:
    def test_submit_with_registered_definition(self, client):
        payload = _job_payload(
            [
                {"label": "two-phase", "temperature": 298.15, "pressure": 43.0, "feed": [0.5, 0.5]},
                {"label": "liquid", "temperature": 298.15, "pressure": 50.0, "feed": [0.5, 0.5]},
                {"label": "vapor", "temperature": 298.15, "pressure": 30.0, "feed": [0.5, 0.5]},
            ],
            property_definition_id=DEMO,
        )
        r = client.post("/api/v1/jobs", json=payload)
        assert r.status_code == 201
        job = r.json()
        assert job["status"] == "completed"
        assert job["point_count"] == 3
        assert job["property_definition_id"] == DEMO
        assert job["property_definition"]["id"] == DEMO  # 快照随作业保存

        phases = {p["label"]: p["phase"] for p in job["points"]}
        assert phases == {"two-phase": "two_phase", "liquid": "liquid", "vapor": "vapor"}

        # 单相点：汽化率取边界值，存在相组成等于进料，并给出所处一侧的理由
        liquid = job["points"][1]
        assert liquid["vapor_fraction"] == 0.0
        assert liquid["liquid_composition"] == [0.5, 0.5]
        assert liquid["vapor_composition"] is None
        assert "泡点" in liquid["reason"]
        vapor = job["points"][2]
        assert vapor["vapor_fraction"] == 1.0
        assert vapor["vapor_composition"] == [0.5, 0.5]
        assert vapor["liquid_composition"] is None
        assert "露点" in vapor["reason"]

        # 按作业号取回全部结果
        refetched = client.get(f"/api/v1/jobs/{job['id']}")
        assert refetched.status_code == 200
        assert refetched.json() == job

        # 只取某一工况点
        point = client.get(f"/api/v1/jobs/{job['id']}/points/0")
        assert point.status_code == 200
        assert point.json()["label"] == "two-phase"
        assert point.json()["phase"] == "two_phase"

        # 作业整体可查
        listing = client.get("/api/v1/jobs")
        assert job["id"] in {j["id"] for j in listing.json()}

    def test_submit_with_inline_direct_psat_definition(self, client):
        payload = _job_payload(
            [{"temperature": 300.0, "pressure": 40.0, "feed": [0.5, 0.5]}],
            property_definition={
                "name": "inline-80-20",
                "source": "direct_psat",
                "components": [{"name": "a", "psat": 80.0}, {"name": "b", "psat": 20.0}],
            },
        )
        r = client.post("/api/v1/jobs", json=payload)
        assert r.status_code == 201
        job = r.json()
        # 临时物性定义不进入登记列表
        assert job["property_definition_id"] is None
        assert job["property_definition"]["id"] is None

        point = job["points"][0]
        # K = [2.0, 0.5] → V = 0.5，x = [1/3, 2/3]，y = [2/3, 1/3]（手算可核）
        assert point["k_values"] == pytest.approx([2.0, 0.5])
        assert point["phase"] == "two_phase"
        assert point["vapor_fraction"] == pytest.approx(0.5, abs=1e-12)
        assert point["liquid_composition"] == pytest.approx([1 / 3, 2 / 3], abs=1e-9)
        assert point["vapor_composition"] == pytest.approx([2 / 3, 1 / 3], abs=1e-9)

    def test_hand_check_near_bubble_point(self, client):
        """README 手算示例：298.15 K、43 kPa、等摩尔 → V ≈ 0.0945。"""
        payload = _job_payload(
            [{"temperature": 298.15, "pressure": 43.0, "feed": [0.5, 0.5]}],
            property_definition_id=DEMO,
        )
        point = client.post("/api/v1/jobs", json=payload).json()["points"][0]
        assert point["phase"] == "two_phase"
        # 符号与量级与手算一致
        assert 0.0 < point["vapor_fraction"] < 0.2
        assert point["vapor_fraction"] == pytest.approx(0.0945, abs=2e-3)
        # 用返回的 K 做二元闭式解独立核对二分法结果
        k1, k2 = point["k_values"]
        v_closed = -(0.5 * (k1 - 1) + 0.5 * (k2 - 1)) / ((k1 - 1) * (k2 - 1))
        assert point["vapor_fraction"] == pytest.approx(v_closed, abs=1e-9)
        # 衡算闭合
        assert sum(point["liquid_composition"]) == pytest.approx(1.0, abs=1e-9)
        assert sum(point["vapor_composition"]) == pytest.approx(1.0, abs=1e-9)
        assert point["material_balance_residual"] < 1e-9

    def test_pressure_sweep_monotone_within_one_job(self, client):
        # 同一作业内：T、z 不变仅升压，汽化率不得上升
        points = [
            {"temperature": 298.15, "pressure": 30.0 + i, "feed": [0.5, 0.5]}
            for i in range(16)  # 30 → 45 kPa，跨越露点（≈31.2）与泡点（≈44.3）
        ]
        job = client.post("/api/v1/jobs", json=_job_payload(points, property_definition_id=DEMO)).json()
        fractions = [p["vapor_fraction"] for p in job["points"]]
        assert fractions[0] == 1.0 and fractions[-1] == 0.0
        for before, after in zip(fractions, fractions[1:]):
            assert after <= before + 1e-12

    def test_multiple_jobs_share_one_definition(self, client):
        ids = set()
        for _ in range(3):
            job = client.post(
                "/api/v1/jobs",
                json=_job_payload(
                    [{"temperature": 298.15, "pressure": 43.0, "feed": [0.5, 0.5]}],
                    property_definition_id=DEMO,
                ),
            ).json()
            ids.add(job["id"])
        assert len(ids) == 3  # 同一物性定义可复用，作业号互不冲突
        listing = client.get("/api/v1/jobs").json()
        assert len(listing) == 3

    def test_job_not_found(self, client):
        r = client.get("/api/v1/jobs/job_nope")
        assert r.status_code == 404
        assert r.json()["error"]["type"] == "JOB_NOT_FOUND"

    def test_point_not_found(self, client):
        job = client.post(
            "/api/v1/jobs",
            json=_job_payload(
                [{"temperature": 298.15, "pressure": 43.0, "feed": [0.5, 0.5]}],
                property_definition_id=DEMO,
            ),
        ).json()
        r = client.get(f"/api/v1/jobs/{job['id']}/points/7")
        assert r.status_code == 404
        assert r.json()["error"]["type"] == "POINT_NOT_FOUND"
