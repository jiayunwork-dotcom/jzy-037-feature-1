"""存量数据平顺过渡：旧版结构的数据库（无 activity_model_json 列）
在新代码打开时自动加列；旧登记项/旧作业完整可读、结果不变，
并可以对旧登记项追加非理想参数。
"""
from __future__ import annotations

import json
import sqlite3

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


_OLD_SCHEMA = """
CREATE TABLE property_definitions (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    description       TEXT,
    source            TEXT NOT NULL,
    temperature_unit  TEXT NOT NULL,
    pressure_unit     TEXT NOT NULL,
    components_json   TEXT NOT NULL,
    created_at        TEXT NOT NULL
);
CREATE TABLE jobs (
    id                       TEXT PRIMARY KEY,
    property_definition_id   TEXT,
    property_snapshot_json   TEXT NOT NULL,
    status                   TEXT NOT NULL,
    point_count              INTEGER NOT NULL,
    created_at               TEXT NOT NULL
);
CREATE TABLE job_points (
    job_id       TEXT NOT NULL REFERENCES jobs (id),
    point_index  INTEGER NOT NULL,
    point_json   TEXT NOT NULL,
    PRIMARY KEY (job_id, point_index)
);
"""


def _create_legacy_database(db_path: str) -> None:
    """用旧版 DDL 手工造一个带登记项和作业的库（模拟升级前的线上数据）。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(_OLD_SCHEMA)
        components = [
            {"name": "light", "antoine": None, "psat": 80.0},
            {"name": "heavy", "antoine": None, "psat": 20.0},
        ]
        conn.execute(
            """INSERT INTO property_definitions
                   (id, name, description, source, temperature_unit, pressure_unit,
                    components_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "pd-legacy-direct",
                "legacy-direct",
                None,
                "direct_psat",
                "K",
                "kPa",
                json.dumps(components),
                "2026-01-01T00:00:00+00:00",
            ),
        )
        snapshot = {
            "name": "legacy-direct",
            "description": None,
            "source": "direct_psat",
            "temperature_unit": "K",
            "pressure_unit": "kPa",
            "components": components,
            "id": None,
            "created_at": None,
        }
        point = {
            "point_index": 0,
            "label": None,
            "input": {"temperature": 300.0, "pressure": 40.0, "feed": [0.5, 0.5]},
            "phase": "two_phase",
            "vapor_fraction": 0.5,
            "liquid_composition": [1 / 3, 2 / 3],
            "vapor_composition": [2 / 3, 1 / 3],
            "k_values": [2.0, 0.5],
            "saturation_pressures": [80.0, 20.0],
            "bubble_sum": 1.25,
            "dew_sum": 1.25,
            "rr_residual": 0.0,
            "rr_iterations": 1,
            "material_balance_residual": 0.0,
            "reason": None,
        }
        conn.execute(
            """INSERT INTO jobs
                   (id, property_definition_id, property_snapshot_json,
                    status, point_count, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "job-legacy-1",
                "pd-legacy-direct",
                json.dumps(snapshot),
                "completed",
                1,
                "2026-01-01T00:05:00+00:00",
            ),
        )
        conn.execute(
            "INSERT INTO job_points (job_id, point_index, point_json) VALUES (?, ?, ?)",
            ("job-legacy-1", 0, json.dumps(point)),
        )
        conn.commit()
    finally:
        conn.close()


def test_legacy_database_migrates_and_history_preserved(tmp_path):
    db_path = str(tmp_path / "legacy.db")
    _create_legacy_database(db_path)

    app = create_app(Settings(db_path=db_path, demo_seed_enabled=False))
    with TestClient(app) as client:
        # 旧登记项可读，迁移后呈现为理想定义（activity_model=None）
        definition = client.get("/api/v1/property-definitions/pd-legacy-direct")
        assert definition.status_code == 200
        body = definition.json()
        assert body["activity_model"] is None
        assert body["components"][0]["psat"] == 80.0

        # 旧作业与旧工况点完整可读，数值原样保留
        job = client.get("/api/v1/jobs/job-legacy-1")
        assert job.status_code == 200
        job_body = job.json()
        assert job_body["property_definition"]["source"] == "direct_psat"
        point = job_body["points"][0]
        assert point["vapor_fraction"] == 0.5
        assert point["k_values"] == [2.0, 0.5]
        # 旧快照不带 activity_model，响应模型补安全默认
        assert point["liquid_model"] == "ideal"
        assert point["activity_coefficients"] is None

        # 旧登记项仍按理想行为工作（新作业结果与旧作业一致）
        new_job = client.post(
            "/api/v1/jobs",
            json={
                "property_definition_id": "pd-legacy-direct",
                "points": [{"temperature": 300.0, "pressure": 40.0, "feed": [0.5, 0.5]}],
            },
        ).json()
        assert new_job["points"][0]["vapor_fraction"] == 0.5
        assert new_job["points"][0]["liquid_model"] == "ideal"

        # 可以对旧登记项追加非理想参数，升级后旧作业仍不动
        upgrade = client.post(
            "/api/v1/property-definitions/pd-legacy-direct/activity-model",
            json={"activity_model": {"model": "margules", "a12": 0.5, "a21": 0.3}},
        )
        assert upgrade.status_code == 200
        assert upgrade.json()["activity_model"]["a12"] == 0.5
        history = client.get("/api/v1/jobs/job-legacy-1").json()
        assert history["points"][0]["vapor_fraction"] == 0.5
        assert history["property_definition"].get("activity_model") is None


def test_fresh_database_has_activity_model_column(tmp_path):
    """全新建库也带新列，登记/升级一轮可用。"""
    db_path = str(tmp_path / "fresh.db")
    app = create_app(Settings(db_path=db_path, demo_seed_enabled=False))
    with TestClient(app) as client:
        pd_id = client.post(
            "/api/v1/property-definitions",
            json={
                "name": "fresh",
                "source": "direct_psat",
                "components": [
                    {"name": "a", "psat": 80.0},
                    {"name": "b", "psat": 20.0},
                ],
            },
        ).json()["id"]
        r = client.post(
            f"/api/v1/property-definitions/{pd_id}/activity-model",
            json={"activity_model": {"model": "margules", "a12": 0.5, "a21": 0.3}},
        )
        assert r.status_code == 200
