"""存量库迁移：旧版本（无 liquid_model 列）数据库在新版启动时幂等升级，
旧登记项按理想定义呈现，旧作业快照逐字节保持原样。"""
from __future__ import annotations

import json
import sqlite3

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.store import Store

_OLD_SCHEMA = """
CREATE TABLE property_definitions (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT, source TEXT NOT NULL,
  temperature_unit TEXT NOT NULL, pressure_unit TEXT NOT NULL,
  components_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE jobs (id TEXT PRIMARY KEY, property_definition_id TEXT,
  property_snapshot_json TEXT NOT NULL, status TEXT NOT NULL,
  point_count INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE job_points (job_id TEXT NOT NULL, point_index INTEGER NOT NULL,
  point_json TEXT NOT NULL, PRIMARY KEY (job_id, point_index));
"""

_OLD_SNAPSHOT = {
    "name": "legacy",
    "description": None,
    "temperature_unit": "K",
    "pressure_unit": "kPa",
    "source": "direct_psat",
    "components": [
        {"name": "a", "psat": 80.0, "antoine": None},
        {"name": "b", "psat": 20.0, "antoine": None},
    ],
}


def test_legacy_database_migrates_and_stays_ideal(tmp_path):
    db_path = str(tmp_path / "old.db")
    conn = sqlite3.connect(db_path)
    conn.executescript(_OLD_SCHEMA)
    conn.execute(
        "INSERT INTO property_definitions VALUES (?,?,?,?,?,?,?,?)",
        ("pd_old", "legacy", None, "direct_psat", "K", "kPa",
         json.dumps([{"name": "a", "psat": 80.0}, {"name": "b", "psat": 20.0}]),
         "t0"),
    )
    conn.execute(
        "INSERT INTO jobs VALUES (?,?,?,?,?,?)",
        ("job_old", None, json.dumps(_OLD_SNAPSHOT), "completed", 1, "t0"),
    )
    conn.execute(
        "INSERT INTO job_points VALUES (?,?,?)",
        ("job_old", 0, json.dumps({"point_index": 0, "phase": "two_phase"})),
    )
    conn.commit()
    conn.close()

    store = Store(db_path)  # 构造即迁移
    record = store.get_property_definition("pd_old")
    assert record["liquid_model"] == "ideal"
    assert record["activity_model"] is None

    # 历史作业快照不被迁移补写字段
    job = store.get_job("job_old")
    assert "liquid_model" not in job["property_snapshot"]
    assert job["points"][0]["phase"] == "two_phase"

    # 迁移是幂等的：再开一次不报错
    Store(db_path)

    # 旧登记项经新服务仍按理想体系求解
    client = TestClient(create_app(Settings(db_path=db_path, demo_seed_enabled=False)))
    r = client.post(
        "/api/v1/jobs",
        json={
            "property_definition_id": "pd_old",
            "points": [{"temperature": 300.0, "pressure": 40.0, "feed": [0.5, 0.5]}],
        },
    )
    assert r.status_code == 201
    point = r.json()["points"][0]
    assert point["phase"] == "two_phase"
    assert point["vapor_fraction"] == 0.5
    assert point["liquid_model"] == "ideal"
