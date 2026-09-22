"""黄金基准生成器（仅维护用）。

⚠️ 必须在**理想路径行为发生任何变更之前**的代码上运行：

    PYTHONPATH=. python3 tests/generate_ideal_golden.py

它回放固定的 API 工作流，归一化动态值（随机 id / 时间戳），把请求与完整
响应写入 tests/fixtures/ideal_golden.json；test_ideal_compat.py 在当前
代码上原样回放并逐字段比对。交付的 fixture 已由非理想特性合入前的代码
生成，日常测试不需要重跑本脚本。
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ideal_golden.json"

_ID_RE = re.compile(r"^(pd|job)_[0-9a-f]{32}$")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def normalize(value):
    if isinstance(value, str):
        if _ID_RE.match(value):
            return "<DYNAMIC_ID>"
        if _TS_RE.match(value):
            return "<TIMESTAMP>"
        return value
    if isinstance(value, dict):
        return {k: normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v) for v in value]
    return value


def main() -> None:
    db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db.close()
    app = create_app(Settings(db_path=db.name))

    steps = []

    def record(name, method, path, json_body=None, capture=None):
        return {
            "name": name,
            "request": {"method": method, "path": path, "json": json_body},
            "capture": capture or {},
            "response": None,
            "status": None,
        }

    ctx: dict[str, str] = {}
    with TestClient(app) as client:
        planned = [
            record("get-demo-definition", "GET", "/api/v1/property-definitions/pd-demo-pentane-hexane"),
            record(
                "register-direct-psat",
                "POST",
                "/api/v1/property-definitions",
                {
                    "name": "golden-direct-80-20",
                    "description": "direct_psat 理想物性（兼容性基准）",
                    "source": "direct_psat",
                    "components": [
                        {"name": "light", "psat": 80.0},
                        {"name": "heavy", "psat": 20.0},
                    ],
                },
                capture={"direct_id": "id"},
            ),
            record("get-registered-direct", "GET", "/api/v1/property-definitions/{{direct_id}}"),
            record("list-definitions", "GET", "/api/v1/property-definitions"),
            record(
                "register-antoine",
                "POST",
                "/api/v1/property-definitions",
                {
                    "name": "golden-antoine",
                    "source": "antoine",
                    "temperature_unit": "K",
                    "pressure_unit": "kPa",
                    "components": [
                        {"name": "c1", "antoine": {"A": 13.8183, "B": 2477.07, "C": -39.94}},
                        {"name": "c2", "antoine": {"A": 13.8216, "B": 2697.55, "C": -48.78}},
                    ],
                },
                capture={"antoine_id": "id"},
            ),
            record(
                "job-demo-multi-phase",
                "POST",
                "/api/v1/jobs",
                {
                    "property_definition_id": "pd-demo-pentane-hexane",
                    "points": [
                        {"label": "two-phase", "temperature": 298.15, "pressure": 43.0, "feed": [0.5, 0.5]},
                        {"label": "liquid", "temperature": 298.15, "pressure": 50.0, "feed": [0.5, 0.5]},
                        {"label": "vapor", "temperature": 298.15, "pressure": 30.0, "feed": [0.5, 0.5]},
                        {"label": "lean-light", "temperature": 310.0, "pressure": 55.0, "feed": [0.3, 0.7]},
                        {"label": "rich-light", "temperature": 283.15, "pressure": 25.0, "feed": [0.7, 0.3]},
                        {"label": "near-bubble", "temperature": 298.15, "pressure": 44.2, "feed": [0.5, 0.5]},
                        {"label": "near-dew", "temperature": 298.15, "pressure": 31.3, "feed": [0.5, 0.5]},
                        {"label": "skewed", "temperature": 305.0, "pressure": 47.5, "feed": [0.15, 0.85]},
                    ],
                },
                capture={"job_demo": "id"},
            ),
            record(
                "job-inline-direct",
                "POST",
                "/api/v1/jobs",
                {
                    "property_definition": {
                        "name": "golden-inline",
                        "source": "direct_psat",
                        "components": [
                            {"name": "a", "psat": 80.0},
                            {"name": "b", "psat": 20.0},
                        ],
                    },
                    "points": [
                        {"temperature": 300.0, "pressure": 40.0, "feed": [0.5, 0.5]},
                        {"temperature": 300.0, "pressure": 100.0, "feed": [0.5, 0.5]},
                        {"temperature": 300.0, "pressure": 15.0, "feed": [0.5, 0.5]},
                        {"temperature": 350.0, "pressure": 33.0, "feed": [0.25, 0.75]},
                    ],
                },
                capture={"job_inline": "id"},
            ),
            record(
                "job-registered-direct",
                "POST",
                "/api/v1/jobs",
                {
                    "property_definition_id": "{{direct_id}}",
                    "points": [
                        {"temperature": 300.0, "pressure": 40.0, "feed": [0.5, 0.5]},
                        {"temperature": 310.0, "pressure": 45.0, "feed": [0.6, 0.4]},
                    ],
                },
                capture={"job_direct": "id"},
            ),
            record(
                "job-registered-antoine",
                "POST",
                "/api/v1/jobs",
                {
                    "property_definition_id": "{{antoine_id}}",
                    "points": [
                        {"temperature": 298.15, "pressure": 43.0, "feed": [0.5, 0.5]},
                        {"temperature": 320.0, "pressure": 70.0, "feed": [0.4, 0.6]},
                    ],
                },
            ),
            record("get-job-demo", "GET", "/api/v1/jobs/{{job_demo}}"),
            record("get-job-demo-point-0", "GET", "/api/v1/jobs/{{job_demo}}/points/0"),
            record("get-job-demo-point-4", "GET", "/api/v1/jobs/{{job_demo}}/points/4"),
            record("list-jobs", "GET", "/api/v1/jobs"),
            # ---- error behaviour must also stay identical ----
            record(
                "err-feed-sum",
                "POST",
                "/api/v1/jobs",
                {
                    "property_definition_id": "pd-demo-pentane-hexane",
                    "points": [{"temperature": 300.0, "pressure": 50.0, "feed": [0.6, 0.6]}],
                },
            ),
            record(
                "err-antoine-denominator",
                "POST",
                "/api/v1/jobs",
                {
                    "property_definition_id": "pd-demo-pentane-hexane",
                    "points": [{"temperature": 39.94, "pressure": 50.0, "feed": [0.5, 0.5]}],
                },
            ),
            record(
                "err-unknown-definition",
                "POST",
                "/api/v1/jobs",
                {
                    "property_definition_id": "pd_ghost",
                    "points": [{"temperature": 300.0, "pressure": 50.0, "feed": [0.5, 0.5]}],
                },
            ),
            record(
                "err-ambiguous-source",
                "POST",
                "/api/v1/jobs",
                {
                    "property_definition_id": "pd-demo-pentane-hexane",
                    "property_definition": {
                        "name": "x",
                        "source": "direct_psat",
                        "components": [{"name": "a", "psat": 10.0}, {"name": "b", "psat": 5.0}],
                    },
                    "points": [{"temperature": 300.0, "pressure": 50.0, "feed": [0.5, 0.5]}],
                },
            ),
            record(
                "err-invalid-temperature",
                "POST",
                "/api/v1/jobs",
                {
                    "property_definition_id": "pd-demo-pentane-hexane",
                    "points": [{"temperature": -5.0, "pressure": 50.0, "feed": [0.5, 0.5]}],
                },
            ),
            record("err-definition-404", "GET", "/api/v1/property-definitions/pd_nope"),
            record("err-job-404", "GET", "/api/v1/jobs/job_nope"),
            record(
                "err-bad-definition",
                "POST",
                "/api/v1/property-definitions",
                {
                    "name": "bad",
                    "source": "direct_psat",
                    "components": [{"name": "a", "psat": -1.0}, {"name": "b", "psat": 5.0}],
                },
            ),
        ]

        for step in planned:
            req = step["request"]
            path = req["path"]
            body = req["json"]
            if body is not None:
                body = json.loads(json.dumps(body))
                _substitute(body, ctx)
            path = _substitute_str(path, ctx)
            response = client.request(req["method"], path, json=body)
            step["status"] = response.status_code
            step["response"] = normalize(response.json())
            for var, key in step["capture"].items():
                ctx[var] = response.json()[key]
            # requests 里的模板替换为占位符，回放时再动态填充
            step["request"] = {"method": req["method"], "path": req["path"], "json": req["json"]}

    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(
        json.dumps({"steps": planned}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {FIXTURE_PATH} with {len(planned)} steps")


def _substitute_str(text: str, ctx: dict[str, str]) -> str:
    for var, value in ctx.items():
        text = text.replace("{{" + var + "}}", value)
    return text


def _substitute(node, ctx: dict[str, str]) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str):
                node[key] = _substitute_str(value, ctx)
            else:
                _substitute(value, ctx)
    elif isinstance(node, list):
        for i, value in enumerate(node):
            if isinstance(value, str):
                node[i] = _substitute_str(value, ctx)
            else:
                _substitute(value, ctx)


if __name__ == "__main__":
    main()
