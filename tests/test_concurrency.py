"""并发提交：多个作业同时进行，结果互不覆盖、互不串号。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app.seed import DEMO_PROPERTY_DEFINITION_ID


def _submit_job(app, worker: int, points_per_job: int):
    """每个线程用独立的 TestClient 提交一个作业，共享同一个 app 与数据库。"""
    z1 = round(0.1 + 0.1 * worker, 10)
    points = [
        {
            "label": f"w{worker}-p{i}",
            "temperature": 298.15,
            "pressure": 38.0 + i,
            "feed": [z1, round(1.0 - z1, 10)],
        }
        for i in range(points_per_job)
    ]
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/jobs",
            json={"property_definition_id": DEMO_PROPERTY_DEFINITION_ID, "points": points},
        )
    assert response.status_code == 201
    return worker, z1, response.json()["id"]


def test_concurrent_jobs_are_isolated(app):
    workers, points_per_job = 8, 5
    with ThreadPoolExecutor(max_workers=workers) as pool:
        submitted = list(pool.map(lambda w: _submit_job(app, w, points_per_job), range(workers)))

    job_ids = [job_id for _, _, job_id in submitted]
    assert len(set(job_ids)) == workers  # 作业号不冲突

    with TestClient(app) as client:
        listing = client.get("/api/v1/jobs").json()
        assert len(listing) == workers  # 全部落库，无人被覆盖
        for worker, z1, job_id in submitted:
            job = client.get(f"/api/v1/jobs/{job_id}").json()
            assert job["point_count"] == points_per_job
            assert len(job["points"]) == points_per_job
            for index, point in enumerate(job["points"]):
                # 每个点仍是提交时自己的输入，没有串到别的作业
                assert point["point_index"] == index
                assert point["label"] == f"w{worker}-p{index}"
                assert point["input"]["feed"][0] == pytest.approx(z1)
                assert point["input"]["pressure"] == pytest.approx(38.0 + index)
                # 每个点的结果自身衡算闭合
                composition = point["liquid_composition"] or point["vapor_composition"]
                assert sum(composition) == pytest.approx(1.0, abs=1e-9)


def test_concurrent_property_registration(app):
    def register(i: int) -> str:
        payload = {
            "name": f"def-{i}",
            "source": "direct_psat",
            "components": [{"name": "a", "psat": 50.0 + i}, {"name": "b", "psat": 10.0}],
        }
        with TestClient(app) as client:
            return client.post("/api/v1/property-definitions", json=payload).json()["id"]

    with ThreadPoolExecutor(max_workers=6) as pool:
        ids = list(pool.map(register, range(6)))
    assert len(set(ids)) == 6

    with TestClient(app) as client:
        for i, definition_id in enumerate(ids):
            body = client.get(f"/api/v1/property-definitions/{definition_id}").json()
            assert body["name"] == f"def-{i}"
            assert body["components"][0]["psat"] == 50.0 + i
