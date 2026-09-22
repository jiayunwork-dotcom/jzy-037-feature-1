"""登记项升级（理想 → van Laar）与历史作业不可变。

要求：允许给已登记的理想定义追加非理想参数；升级只改登记项，
已完成作业保存的是当次物性快照，逐字节不受影响；作业可追溯其
当时使用的液相模型与参数。
"""
from __future__ import annotations

import copy

import pytest

from app.seed import DEMO_PROPERTY_DEFINITION_ID

DEMO = DEMO_PROPERTY_DEFINITION_ID
ETHANOL_WATER_ANTOINE = {
    "name": "eth-water",
    "source": "antoine",
    "components": [
        {"name": "ethanol", "antoine": {"A": 16.50917323, "B": 3578.90801, "C": -50.5}},
        {"name": "water", "antoine": {"A": 16.5698924, "B": 3984.92284, "C": -39.724}},
    ],
}
POINTS = [
    {"label": "p0", "temperature": 351.30, "pressure": 85.0, "feed": [0.5, 0.5]},
    {"label": "p1", "temperature": 351.30, "pressure": 101.325, "feed": [0.5, 0.5]},
]


def _register_ideal(client) -> str:
    r = client.post("/api/v1/property-definitions", json=ETHANOL_WATER_ANTOINE)
    assert r.status_code == 201
    return r.json()["id"]


class TestUpgrade:
    def test_upgrade_changes_registry_only(self, client):
        pid = _register_ideal(client)
        before = client.get(f"/api/v1/property-definitions/{pid}").json()
        assert before["liquid_model"] == "ideal"
        assert before["activity_model"] is None

        r = client.patch(
            f"/api/v1/property-definitions/{pid}",
            json={"liquid_model": "van_laar",
                  "activity_model": {"model": "van_laar", "A12": 1.75, "A21": 0.91}},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["liquid_model"] == "van_laar"
        assert body["activity_model"]["A12"] == 1.75
        assert body["activity_model"]["A21"] == 0.91
        # 其它字段原样保留
        assert body["name"] == before["name"]
        assert body["source"] == before["source"]
        assert body["components"] == before["components"]

    def test_upgrade_then_new_jobs_use_nonideal(self, client):
        pid = _register_ideal(client)
        client.patch(
            f"/api/v1/property-definitions/{pid}",
            json={"liquid_model": "van_laar",
                  "activity_model": {"A12": 1.75, "A21": 0.91}},
        )
        job = client.post(
            "/api/v1/jobs",
            json={"property_definition_id": pid, "points": POINTS},
        ).json()
        for point in job["points"]:
            assert point["liquid_model"] == "van_laar"
            assert point["activity_model"] == {
                "model": "van_laar", "A12": 1.75, "A21": 0.91
            }
        # 该点理想假设判液体，非理想实际两相（与内核测试同一锚点）
        assert job["points"][0]["phase"] == "two_phase"
        assert job["property_definition"]["liquid_model"] == "van_laar"

    def test_double_upgrade_conflicts(self, client):
        pid = _register_ideal(client)
        payload = {"liquid_model": "van_laar",
                   "activity_model": {"A12": 1.75, "A21": 0.91}}
        assert client.patch(f"/api/v1/property-definitions/{pid}", json=payload).status_code == 200
        again = client.patch(
            f"/api/v1/property-definitions/{pid}",
            json={"liquid_model": "van_laar",
                  "activity_model": {"A12": 2.0, "A21": 1.0}},
        )
        assert again.status_code == 409
        assert again.json()["error"]["type"] == "PROPERTY_DEFINITION_UPGRADE_CONFLICT"
        # 原参数未被第二次请求覆盖
        body = client.get(f"/api/v1/property-definitions/{pid}").json()
        assert body["activity_model"]["A12"] == 1.75

    def test_upgrade_unknown_definition_404(self, client):
        r = client.patch(
            "/api/v1/property-definitions/pd_ghost",
            json={"liquid_model": "van_laar",
                  "activity_model": {"A12": 1.75, "A21": 0.91}},
        )
        assert r.status_code == 404
        assert r.json()["error"]["type"] == "PROPERTY_DEFINITION_NOT_FOUND"

    @pytest.mark.parametrize("activity", [
        {"A12": 0.0, "A21": 0.91},
        {"A12": 1.75, "A21": -0.1},
        {"A12": "x", "A21": 0.91},
    ])
    def test_upgrade_with_invalid_parameters_rejected(self, client, activity):
        pid = _register_ideal(client)
        r = client.patch(
            f"/api/v1/property-definitions/{pid}",
            json={"liquid_model": "van_laar", "activity_model": activity},
        )
        assert r.status_code == 422
        # 登记项未被半写坏
        body = client.get(f"/api/v1/property-definitions/{pid}").json()
        assert body["liquid_model"] == "ideal"
        assert body["activity_model"] is None


class TestHistoryImmutable:
    def test_historical_job_unchanged_after_upgrade(self, client):
        pid = _register_ideal(client)
        # 升级前完成一批作业（多工况点，含两相/单相）
        job_before = client.post(
            "/api/v1/jobs",
            json={"property_definition_id": pid, "points": POINTS},
        ).json()
        snapshot_before = copy.deepcopy(job_before)

        client.patch(
            f"/api/v1/property-definitions/{pid}",
            json={"liquid_model": "van_laar",
                  "activity_model": {"A12": 1.75, "A21": 0.91}},
        )
        # 再提交一个新作业，确认升级确实生效（不是没升上去）
        job_after_upgrade = client.post(
            "/api/v1/jobs",
            json={"property_definition_id": pid, "points": POINTS[:1]},
        ).json()
        assert job_after_upgrade["points"][0]["liquid_model"] == "van_laar"

        # 旧作业整包（含快照与每个点的结果）逐字节不变
        job_now = client.get(f"/api/v1/jobs/{job_before['id']}").json()
        assert job_now == snapshot_before
        assert job_now["property_definition"]["liquid_model"] == "ideal"
        assert job_now["property_definition"]["activity_model"] is None
        for point in job_now["points"]:
            assert point["liquid_model"] == "ideal"
            assert point["activity_model"] is None
            assert point["activity_coefficients"] is None

    def test_point_endpoint_also_returns_historical_snapshot(self, client):
        pid = _register_ideal(client)
        job = client.post(
            "/api/v1/jobs",
            json={"property_definition_id": pid, "points": POINTS},
        ).json()
        client.patch(
            f"/api/v1/property-definitions/{pid}",
            json={"liquid_model": "van_laar",
                  "activity_model": {"A12": 1.75, "A21": 0.91}},
        )
        point = client.get(f"/api/v1/jobs/{job['id']}/points/0").json()
        assert point["liquid_model"] == "ideal"

    def test_tracability_fields_on_all_paths(self, client):
        # 非理想示范定义：作业级别快照 + 点级别字段都可追溯模型与参数
        r = client.post(
            "/api/v1/jobs",
            json={
                "property_definition_id": "pd-demo-ethanol-water-vanlaar",
                "points": [
                    {"temperature": 351.30, "pressure": 85.0, "feed": [0.5, 0.5]},
                    {"temperature": 351.30, "pressure": 101.325, "feed": [0.5, 0.5]},
                    {"temperature": 351.30, "pressure": 60.0, "feed": [0.5, 0.5]},
                ],
            },
        )
        assert r.status_code == 201
        job = r.json()
        snap = job["property_definition"]
        assert snap["liquid_model"] == "van_laar"
        assert snap["activity_model"]["A12"] == 1.75
        for point in job["points"]:
            assert point["liquid_model"] == "van_laar"
            assert point["activity_model"]["A12"] == 1.75
        two_phase, liquid, vapor = job["points"]
        assert two_phase["activity_coefficients"] is not None
        assert liquid["activity_coefficients"] is not None  # γ(x=z)
        assert vapor["activity_coefficients"] is None  # 无平衡液相
