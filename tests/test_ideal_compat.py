"""与老代码的兼容性：用改动前生成的黄金基准逐项回放比对。

fixture（tests/fixtures/ideal_golden.json）由改动前的代码生成：
固定的请求工作流（登记/引用/内联、Antoine/direct_psat、两相/单相、
错误响应）+ 归一化后的完整响应。本测试在当前代码上原样回放，
要求响应中**基准里存在的每个字段都逐项完全一致**（浮点严格相等，
不使用 approx）——新增字段（liquid_model、activity_coefficients 等）
是纯增量，允许出现在新响应里但不得改变任何旧字段的值。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.seed import DEMO_ETHANOL_WATER_ID

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "ideal_golden.json"

_ID_RE = re.compile(r"^(pd|job)_[0-9a-f]{32}$")
_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _normalize(value):
    if isinstance(value, str):
        if _ID_RE.match(value):
            return "<DYNAMIC_ID>"
        if _TS_RE.match(value):
            return "<TIMESTAMP>"
        return value
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_normalize(v) for v in value]
    return value


def _assert_subset(expected, actual, path: str = "$") -> None:
    """expected 里的每个键/元素都必须在 actual 中严格相等；actual 允许新增键。"""
    if isinstance(expected, dict):
        assert isinstance(actual, dict), f"{path}: 期望 dict，实际 {type(actual).__name__}"
        for key, exp_value in expected.items():
            assert key in actual, f"{path}.{key}: 旧字段在新响应中缺失"
            _assert_subset(exp_value, actual[key], f"{path}.{key}")
    elif isinstance(expected, list):
        assert isinstance(actual, list), f"{path}: 期望 list，实际 {type(actual).__name__}"
        assert len(actual) == len(expected), (
            f"{path}: 列表长度变化（旧 {len(expected)} → 新 {len(actual)}）"
        )
        for index, (exp_value, act_value) in enumerate(zip(expected, actual)):
            _assert_subset(exp_value, act_value, f"{path}[{index}]")
    else:
        # 标量严格相等：整数/浮点均走 ==，JSON 往返保浮点二进制值
        assert actual == expected and type(actual) is type(expected), (
            f"{path}: 值变化（旧 {expected!r} [{type(expected).__name__}] → "
            f"新 {actual!r} [{type(actual).__name__}]）"
        )


def _substitute(node, ctx: dict[str, str]) -> None:
    if isinstance(node, dict):
        for key in list(node):
            value = node[key]
            if isinstance(value, str):
                for var, captured in ctx.items():
                    value = value.replace("{{" + var + "}}", captured)
                node[key] = value
            else:
                _substitute(value, ctx)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            if isinstance(value, str):
                for var, captured in ctx.items():
                    value = value.replace("{{" + var + "}}", captured)
                node[index] = value
            else:
                _substitute(value, ctx)


@pytest.fixture(scope="module")
def golden_steps() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["steps"]


def test_golden_fixture_covers_all_ideal_paths(golden_steps) -> None:
    """fixture 本身必须确实覆盖：两种物性来源 × 三种相态 × 错误响应。"""
    names = {step["name"] for step in golden_steps}
    assert {
        "job-demo-multi-phase",  # antoine 引用，含 two_phase/liquid/vapor
        "job-inline-direct",  # direct_psat 内联
        "job-registered-direct",  # direct_psat 登记引用
        "job-registered-antoine",  # antoine 登记引用
        "err-feed-sum",
        "err-antoine-denominator",
    } <= names
    demo_job = next(s for s in golden_steps if s["name"] == "job-demo-multi-phase")
    phases = {p["phase"] for p in demo_job["response"]["points"]}
    assert phases == {"two_phase", "liquid", "vapor"}


def test_ideal_responses_identical_to_baseline(golden_steps, tmp_path) -> None:
    app = create_app(Settings(db_path=str(tmp_path / "compat.db")))
    ctx: dict[str, str] = {}
    with TestClient(app) as client:
        for step in golden_steps:
            req = json.loads(json.dumps(step["request"]))
            _substitute(req, ctx)
            response = client.request(req["method"], req["path"], json=req["json"])
            assert response.status_code == step["status"], (
                f"步骤 {step['name']}: 状态码变化（旧 {step['status']} → 新 {response.status_code}）"
            )
            actual = _normalize(response.json())
            # 列表类端点：剔除新版新增的乙醇/水非理想种子后再与旧基准逐项比对，
            # 旧版两个种子（含正戊烷/正己烷）必须原样都在、字段逐个相等
            if step["name"] == "list-definitions":
                actual = [d for d in actual if d["id"] != DEMO_ETHANOL_WATER_ID]
            _assert_subset(step["response"], actual, path=step["name"])
            for var, key in step.get("capture", {}).items():
                ctx[var] = response.json()[key]

    # 新版多出的乙醇/水非理想种子是纯增量，单独确认其存在
    with TestClient(app) as client:
        ids = {d["id"] for d in client.get("/api/v1/property-definitions").json()}
        assert DEMO_ETHANOL_WATER_ID in ids


def test_ideal_point_has_new_fields_with_safe_defaults(tmp_path) -> None:
    """旧调用方拿到的响应里新字段必须是安全默认（ideal/null），不改变旧字段语义。"""
    app = create_app(Settings(db_path=str(tmp_path / "compat-defaults.db")))
    with TestClient(app) as client:
        job = client.post(
            "/api/v1/jobs",
            json={
                "property_definition": {
                    "name": "inline-ideal",
                    "source": "direct_psat",
                    "components": [{"name": "a", "psat": 80.0}, {"name": "b", "psat": 20.0}],
                },
                "points": [
                    {"temperature": 300.0, "pressure": 40.0, "feed": [0.5, 0.5]},
                    {"temperature": 300.0, "pressure": 100.0, "feed": [0.5, 0.5]},
                ],
            },
        ).json()
    for point in job["points"]:
        assert point["liquid_model"] == "ideal"
        assert point["activity_coefficients"] is None
        assert point["nonideal_k_residual"] is None
        assert point["nonideal_iterations"] is None
    assert job["property_definition"]["activity_model"] is None
