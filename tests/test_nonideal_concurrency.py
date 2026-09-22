"""非理想路径并发：不同作业的非理想求解互不覆盖、互不串号。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

NONIDEAL_DEFINITION = {
    "name": "eth-water-conc",
    "source": "antoine",
    "liquid_model": "van_laar",
    "activity_model": {"model": "van_laar", "A12": 1.75, "A21": 0.91},
    "components": [
        {"name": "ethanol", "antoine": {"A": 16.50917323, "B": 3578.90801, "C": -50.5}},
        {"name": "water", "antoine": {"A": 16.5698924, "B": 3984.92284, "C": -39.724}},
    ],
}


def _submit_nonideal_job(app, worker: int, points_per_job: int):
    z1 = round(0.12 + 0.08 * worker, 10)
    points = [
        {
            "label": f"w{worker}-p{i}",
            "temperature": 351.30,
            "pressure": 78.0 + 2.0 * i,  # 横跨汽/两/液区
            "feed": [z1, round(1.0 - z1, 10)],
        }
        for i in range(points_per_job)
    ]
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/jobs",
            json={"property_definition": NONIDEAL_DEFINITION, "points": points},
        )
    assert response.status_code == 201
    return worker, z1, response.json()


def test_concurrent_nonideal_jobs_are_isolated(app):
    workers, points_per_job = 8, 6
    with ThreadPoolExecutor(max_workers=workers) as pool:
        submitted = list(
            pool.map(lambda w: _submit_nonideal_job(app, w, points_per_job), range(workers))
        )
    assert len({job["id"] for _, _, job in submitted}) == workers

    with TestClient(app) as client:
        listing = client.get("/api/v1/jobs").json()
        assert len(listing) == workers
        for worker, z1, job_summary in submitted:
            job = client.get(f"/api/v1/jobs/{job_summary['id']}").json()
            assert job["point_count"] == points_per_job
            for index, point in enumerate(job["points"]):
                assert point["label"] == f"w{worker}-p{index}"
                assert point["liquid_model"] == "van_laar"
                assert point["activity_model"]["A12"] == 1.75
                assert point["input"]["feed"][0] == pytest.approx(z1)
                assert point["input"]["pressure"] == pytest.approx(78.0 + 2.0 * index)
                # 每个点自身衡算闭合（非理想求解中间量没有串到别的作业）
                if point["phase"] == "two_phase":
                    x, y, v = (
                        point["liquid_composition"],
                        point["vapor_composition"],
                        point["vapor_fraction"],
                    )
                    assert sum(x) == pytest.approx(1.0, abs=1e-9)
                    assert sum(y) == pytest.approx(1.0, abs=1e-9)
                    for i in range(2):
                        assert (1 - v) * x[i] + v * y[i] == pytest.approx(
                            point["input"]["feed"][i], abs=1e-8
                        )


def test_concurrent_upgrades_and_jobs_do_not_corrupt(app):
    """升级登记项与引用该登记项的并发作业同时进行：旧作业结果按提交时刻快照。"""
    with TestClient(app) as client:
        pid = client.post(
            "/api/v1/property-definitions",
            json={
                "name": "upgrade-race",
                "source": "antoine",
                "components": [
                    {"name": "ethanol",
                     "antoine": {"A": 16.50917323, "B": 3578.90801, "C": -50.5}},
                    {"name": "water",
                     "antoine": {"A": 16.5698924, "B": 3984.92284, "C": -39.724}},
                ],
            },
        ).json()["id"]

    def submit_ideal_job(i: int):
        with TestClient(app) as client:
            r = client.post(
                "/api/v1/jobs",
                json={
                    "property_definition_id": pid,
                    "points": [{"temperature": 351.30, "pressure": 85.0,
                                "feed": [0.5, 0.5]}],
                },
            )
            return r.status_code, r.json()

    def upgrade():
        with TestClient(app) as client:
            return client.patch(
                f"/api/v1/property-definitions/{pid}",
                json={"liquid_model": "van_laar",
                      "activity_model": {"A12": 1.75, "A21": 0.91}},
            ).status_code

    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs_future = [pool.submit(submit_ideal_job, i) for i in range(6)]
        upgrade_future = pool.submit(upgrade)
        job_results = [f.result() for f in jobs_future]
        assert upgrade_future.result() == 200

    # 作业要么是升级前快照（ideal），要么升级后（van_laar），二者都必须自洽
    with TestClient(app) as client:
        for status_code, job in job_results:
            assert status_code == 201
            detail = client.get(f"/api/v1/jobs/{job['id']}").json()
            model_used = detail["property_definition"]["liquid_model"]
            assert model_used in ("ideal", "van_laar")
            assert detail["points"][0]["liquid_model"] == model_used
