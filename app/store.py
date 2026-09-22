"""作业与物性定义的持久化（SQLite）。

设计要点：

- 每次操作独立开连接，WAL 模式 + busy_timeout，多线程/多请求并发安全；
- 作业与其全部工况点在同一事务内写入，要么完整落库要么不落，杜绝半截作业；
- 主键为随机 UUID 字符串（``pd_`` / ``job_`` 前缀），工况点以
  (job_id, point_index) 为复合主键——并发提交的作业之间不可能串号或互相覆盖；
- 作业落库时保存物性定义快照，登记项事后的任何变化都不影响已完成的核算；
- 求解内核是无状态纯函数，不同工况点、不同作业的中间量各自独立。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_SCHEMA = """
CREATE TABLE IF NOT EXISTS property_definitions (
    id                   TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    description          TEXT,
    source               TEXT NOT NULL,
    temperature_unit     TEXT NOT NULL,
    pressure_unit        TEXT NOT NULL,
    components_json      TEXT NOT NULL,
    activity_model_json  TEXT,
    created_at           TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id                       TEXT PRIMARY KEY,
    property_definition_id   TEXT,
    property_snapshot_json   TEXT NOT NULL,
    status                   TEXT NOT NULL,
    point_count              INTEGER NOT NULL,
    created_at               TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_points (
    job_id       TEXT NOT NULL REFERENCES jobs (id),
    point_index  INTEGER NOT NULL,
    point_json   TEXT NOT NULL,
    PRIMARY KEY (job_id, point_index)
);
"""

# 旧版库（无 activity_model_json 列）的在线迁移：
# 只加列、不动任何历史登记项与作业快照——作业结果随快照永存，
# 登记项事后升级为非理想也不会回写历史作业。
_MIGRATIONS = (
    (
        "activity_model_json",
        "ALTER TABLE property_definitions ADD COLUMN activity_model_json TEXT",
    ),
)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_property_definition_id() -> str:
    return f"pd_{uuid.uuid4().hex}"


def new_job_id() -> str:
    return f"job_{uuid.uuid4().hex}"


class Store:
    """SQLite 存储。所有方法各自开连接，实例可安全地跨线程共享。"""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _session(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            with conn:  # 正常结束 commit，异常 rollback
                yield conn
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._session() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(_SCHEMA)
            existing_columns = {
                row[1]
                for row in conn.execute("PRAGMA table_info(property_definitions)").fetchall()
            }
            for column_name, migration_sql in _MIGRATIONS:
                if column_name not in existing_columns:
                    conn.execute(migration_sql)

    # ---------- 物性定义登记项 ----------

    def create_property_definition(
        self, payload: dict[str, Any], *, record_id: str | None = None
    ) -> dict[str, Any]:
        record = {
            "id": record_id or new_property_definition_id(),
            "name": payload["name"],
            "description": payload.get("description"),
            "source": payload["source"],
            "temperature_unit": payload["temperature_unit"],
            "pressure_unit": payload["pressure_unit"],
            "components": payload["components"],
            "activity_model": payload.get("activity_model"),
            "created_at": _utcnow(),
        }
        with self._session() as conn:
            conn.execute(
                """
                INSERT INTO property_definitions
                    (id, name, description, source, temperature_unit, pressure_unit,
                     components_json, activity_model_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["name"],
                    record["description"],
                    record["source"],
                    record["temperature_unit"],
                    record["pressure_unit"],
                    json.dumps(record["components"], ensure_ascii=False),
                    json.dumps(record["activity_model"], ensure_ascii=False)
                    if record["activity_model"] is not None
                    else None,
                    record["created_at"],
                ),
            )
        return record

    @staticmethod
    def _row_to_property_definition(row: sqlite3.Row) -> dict[str, Any]:
        activity_raw = row["activity_model_json"]
        return {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "source": row["source"],
            "temperature_unit": row["temperature_unit"],
            "pressure_unit": row["pressure_unit"],
            "components": json.loads(row["components_json"]),
            "activity_model": json.loads(activity_raw) if activity_raw is not None else None,
            "created_at": row["created_at"],
        }

    def get_property_definition(self, record_id: str) -> dict[str, Any] | None:
        with self._session() as conn:
            row = conn.execute(
                "SELECT * FROM property_definitions WHERE id = ?", (record_id,)
            ).fetchone()
        return self._row_to_property_definition(row) if row is not None else None

    def set_property_activity_model(
        self, record_id: str, activity_model: dict[str, Any]
    ) -> dict[str, Any] | None:
        """把已有登记项升级为非理想：只写登记项本身，不回写任何作业快照。

        作业落库时保存的是当次求解用的完整物性快照（jobs.property_snapshot_json），
        本方法不触碰 jobs / job_points 两张表。
        """
        with self._session() as conn:
            cursor = conn.execute(
                "UPDATE property_definitions SET activity_model_json = ? WHERE id = ?",
                (json.dumps(activity_model, ensure_ascii=False), record_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_property_definition(record_id)

    def list_property_definitions(self) -> list[dict[str, Any]]:
        with self._session() as conn:
            rows = conn.execute(
                "SELECT * FROM property_definitions ORDER BY created_at, rowid"
            ).fetchall()
        return [self._row_to_property_definition(row) for row in rows]

    # ---------- 核算作业 ----------

    def create_job(
        self,
        *,
        job_id: str,
        property_definition_id: str | None,
        property_snapshot: dict[str, Any],
        status: str,
        points: list[dict[str, Any]],
    ) -> None:
        """作业行 + 全部工况点在同一事务写入。"""
        with self._session() as conn:
            conn.execute(
                """
                INSERT INTO jobs
                    (id, property_definition_id, property_snapshot_json,
                     status, point_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    property_definition_id,
                    json.dumps(property_snapshot, ensure_ascii=False),
                    status,
                    len(points),
                    _utcnow(),
                ),
            )
            conn.executemany(
                "INSERT INTO job_points (job_id, point_index, point_json) VALUES (?, ?, ?)",
                [
                    (job_id, point["point_index"], json.dumps(point, ensure_ascii=False))
                    for point in points
                ],
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._session() as conn:
            job_row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if job_row is None:
                return None
            point_rows = conn.execute(
                "SELECT point_json FROM job_points WHERE job_id = ? ORDER BY point_index",
                (job_id,),
            ).fetchall()
        return {
            "id": job_row["id"],
            "property_definition_id": job_row["property_definition_id"],
            "property_snapshot": json.loads(job_row["property_snapshot_json"]),
            "status": job_row["status"],
            "point_count": job_row["point_count"],
            "created_at": job_row["created_at"],
            "points": [json.loads(row["point_json"]) for row in point_rows],
        }

    def list_jobs(self, *, limit: int, offset: int) -> list[dict[str, Any]]:
        with self._session() as conn:
            rows = conn.execute(
                """
                SELECT id, property_definition_id, status, point_count, created_at
                FROM jobs
                ORDER BY created_at DESC, rowid DESC
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def job_exists(self, job_id: str) -> bool:
        with self._session() as conn:
            return (
                conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
                is not None
            )

    def get_job_point(self, job_id: str, point_index: int) -> dict[str, Any] | None:
        with self._session() as conn:
            row = conn.execute(
                "SELECT point_json FROM job_points WHERE job_id = ? AND point_index = ?",
                (job_id, point_index),
            ).fetchone()
        return json.loads(row["point_json"]) if row is not None else None
