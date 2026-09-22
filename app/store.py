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
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    description       TEXT,
    source            TEXT NOT NULL,
    liquid_model      TEXT NOT NULL DEFAULT 'ideal',
    activity_model_json TEXT,
    temperature_unit  TEXT NOT NULL,
    pressure_unit     TEXT NOT NULL,
    components_json   TEXT NOT NULL,
    created_at        TEXT NOT NULL
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

# 存量库（旧版本没有液相模型列）的幂等迁移：加列带默认值，
# 老登记项一律视为理想定义；历史作业快照原样不动。
_MIGRATIONS = (
    (
        "property_definitions",
        "liquid_model",
        "ALTER TABLE property_definitions ADD COLUMN liquid_model TEXT NOT NULL DEFAULT 'ideal'",
    ),
    (
        "property_definitions",
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
            existing = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(property_definitions)")
            }
            for table, column, ddl in _MIGRATIONS:
                if table == "property_definitions" and column not in existing:
                    conn.execute(ddl)

    # ---------- 物性定义登记项 ----------

    def create_property_definition(
        self, payload: dict[str, Any], *, record_id: str | None = None
    ) -> dict[str, Any]:
        record = {
            "id": record_id or new_property_definition_id(),
            "name": payload["name"],
            "description": payload.get("description"),
            "source": payload["source"],
            "liquid_model": payload.get("liquid_model", "ideal"),
            "activity_model": payload.get("activity_model"),
            "temperature_unit": payload["temperature_unit"],
            "pressure_unit": payload["pressure_unit"],
            "components": payload["components"],
            "created_at": _utcnow(),
        }
        with self._session() as conn:
            conn.execute(
                """
                INSERT INTO property_definitions
                    (id, name, description, source, liquid_model, activity_model_json,
                     temperature_unit, pressure_unit, components_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["id"],
                    record["name"],
                    record["description"],
                    record["source"],
                    record["liquid_model"],
                    json.dumps(record["activity_model"], ensure_ascii=False),
                    record["temperature_unit"],
                    record["pressure_unit"],
                    json.dumps(record["components"], ensure_ascii=False),
                    record["created_at"],
                ),
            )
        return record

    @staticmethod
    def _row_to_property_definition(row: sqlite3.Row) -> dict[str, Any]:
        columns = set(row.keys())
        record = {
            "id": row["id"],
            "name": row["name"],
            "description": row["description"],
            "source": row["source"],
            "temperature_unit": row["temperature_unit"],
            "pressure_unit": row["pressure_unit"],
            "components": json.loads(row["components_json"]),
            "created_at": row["created_at"],
        }
        if "liquid_model" in columns:
            record["liquid_model"] = row["liquid_model"]
            raw_activity = row["activity_model_json"]
            record["activity_model"] = (
                json.loads(raw_activity) if raw_activity is not None else None
            )
        else:
            # 极端情况下读到迁移前的行：按理想定义呈现
            record["liquid_model"] = "ideal"
            record["activity_model"] = None
        return record

    def get_property_definition(self, record_id: str) -> dict[str, Any] | None:
        with self._session() as conn:
            row = conn.execute(
                "SELECT * FROM property_definitions WHERE id = ?", (record_id,)
            ).fetchone()
        return self._row_to_property_definition(row) if row is not None else None

    def list_property_definitions(self) -> list[dict[str, Any]]:
        with self._session() as conn:
            rows = conn.execute(
                "SELECT * FROM property_definitions ORDER BY created_at, rowid"
            ).fetchall()
        return [self._row_to_property_definition(row) for row in rows]

    def upgrade_property_definition(
        self, record_id: str, liquid_model: str, activity_model: dict[str, Any]
    ) -> dict[str, Any] | None:
        """给已登记定义追加非理想参数（只改登记项，绝不触碰历史作业快照）。

        不存在返回 None；是否允许升级（当前须为理想定义）由编排层裁决。
        """
        with self._session() as conn:
            row = conn.execute(
                "SELECT * FROM property_definitions WHERE id = ?", (record_id,)
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE property_definitions
                   SET liquid_model = ?, activity_model_json = ?
                 WHERE id = ?
                """,
                (
                    liquid_model,
                    json.dumps(activity_model, ensure_ascii=False),
                    record_id,
                ),
            )
            row = conn.execute(
                "SELECT * FROM property_definitions WHERE id = ?", (record_id,)
            ).fetchone()
        return self._row_to_property_definition(row)

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
