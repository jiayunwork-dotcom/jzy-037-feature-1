"""非法输入：全部以带类型错误拒绝，不落库、不崩溃、不返回近似解。"""
from __future__ import annotations

import pytest

from app.seed import DEMO_PROPERTY_DEFINITION_ID

DEMO = DEMO_PROPERTY_DEFINITION_ID


def _post_job(client, points: list[dict], **kwargs):
    payload = {"points": points}
    payload.update(kwargs)
    return client.post("/api/v1/jobs", json=payload)


@pytest.mark.parametrize(
    "point, expected_type",
    [
        ({"temperature": 0.0, "pressure": 50.0, "feed": [0.5, 0.5]}, "INVALID_TEMPERATURE"),
        ({"temperature": -273.15, "pressure": 50.0, "feed": [0.5, 0.5]}, "INVALID_TEMPERATURE"),
        ({"temperature": 300.0, "pressure": 0.0, "feed": [0.5, 0.5]}, "INVALID_PRESSURE"),
        ({"temperature": 300.0, "pressure": -1.0, "feed": [0.5, 0.5]}, "INVALID_PRESSURE"),
        ({"temperature": 300.0, "pressure": 50.0, "feed": [0.0, 1.0]}, "INVALID_FEED_COMPOSITION"),
        ({"temperature": 300.0, "pressure": 50.0, "feed": [-0.2, 1.2]}, "INVALID_FEED_COMPOSITION"),
        ({"temperature": 300.0, "pressure": 50.0, "feed": [1.0]}, "INVALID_FEED_COMPOSITION"),
        ({"temperature": 300.0, "pressure": 50.0, "feed": [0.3, 0.3, 0.4]}, "INVALID_FEED_COMPOSITION"),
        ({"temperature": 300.0, "pressure": 50.0, "feed": [0.6, 0.6]}, "FEED_SUM_OUT_OF_TOLERANCE"),
        # 示范物性中戊烷 C = -39.94：T = 39.94 K 使 Antoine 分母 T + C = 0
        ({"temperature": 39.94, "pressure": 50.0, "feed": [0.5, 0.5]}, "ANTOINE_DENOMINATOR_ZERO"),
    ],
)
def test_invalid_points_rejected(client, point, expected_type):
    r = _post_job(client, [point], property_definition_id=DEMO)
    assert r.status_code == 422
    body = r.json()
    assert body["error"]["type"] == expected_type
    assert body["error"]["details"]["point_index"] == 0
    # 非法作业不落库
    assert client.get("/api/v1/jobs").json() == []


def test_nan_temperature_rejected(client):
    # httpx 不允许序列化 NaN，直接发原始 JSON 体（Python json.loads 接受 NaN 字面量）
    r = client.post(
        "/api/v1/jobs",
        content=(
            '{"property_definition_id": "%s", "points": '
            '[{"temperature": NaN, "pressure": 50.0, "feed": [0.5, 0.5]}]}' % DEMO
        ),
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["type"] == "INVALID_TEMPERATURE"


def test_error_envelope_shape(client):
    r = _post_job(
        client,
        [{"temperature": -1.0, "pressure": 50.0, "feed": [0.5, 0.5]}],
        property_definition_id=DEMO,
    )
    body = r.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"type", "message", "details"}


@pytest.mark.parametrize(
    "components, expected_type",
    [
        ([{"name": "a", "psat": 10.0}], "COMPONENT_COUNT_MISMATCH"),
        (
            [{"name": "a", "psat": 10.0}, {"name": "b", "psat": 8.0}, {"name": "c", "psat": 5.0}],
            "COMPONENT_COUNT_MISMATCH",
        ),
    ],
)
def test_component_count(client, components, expected_type):
    payload = {"name": "bad", "source": "direct_psat", "components": components}
    r = client.post("/api/v1/property-definitions", json=payload)
    assert r.status_code == 422
    assert r.json()["error"]["type"] == expected_type


def test_antoine_coefficient_missing(client):
    payload = {
        "name": "bad",
        "source": "antoine",
        "components": [
            {"name": "a", "antoine": {"A": 1.0, "B": 2.0, "C": 3.0}},
            {"name": "b"},  # 缺 Antoine 系数
        ],
    }
    r = client.post("/api/v1/property-definitions", json=payload)
    assert r.status_code == 422
    assert r.json()["error"]["type"] == "ANTOINE_COEFFICIENT_MISSING"


@pytest.mark.parametrize("psat", [0.0, -5.0, None])
def test_direct_psat_invalid(client, psat):
    component = {"name": "a"}
    if psat is not None:
        component["psat"] = psat
    payload = {
        "name": "bad",
        "source": "direct_psat",
        "components": [component, {"name": "b", "psat": 10.0}],
    }
    r = client.post("/api/v1/property-definitions", json=payload)
    assert r.status_code == 422
    assert r.json()["error"]["type"] == "INVALID_PSAT"


def test_job_property_source_ambiguous(client):
    r = _post_job(
        client,
        [{"temperature": 300.0, "pressure": 50.0, "feed": [0.5, 0.5]}],
        property_definition_id=DEMO,
        property_definition={
            "name": "x",
            "source": "direct_psat",
            "components": [{"name": "a", "psat": 10.0}, {"name": "b", "psat": 5.0}],
        },
    )
    assert r.status_code == 422
    assert r.json()["error"]["type"] == "PROPERTY_SOURCE_AMBIGUOUS"


def test_job_property_source_missing(client):
    r = _post_job(client, [{"temperature": 300.0, "pressure": 50.0, "feed": [0.5, 0.5]}])
    assert r.status_code == 422
    assert r.json()["error"]["type"] == "PROPERTY_SOURCE_MISSING"


def test_unknown_property_definition_reference(client):
    r = _post_job(
        client,
        [{"temperature": 300.0, "pressure": 50.0, "feed": [0.5, 0.5]}],
        property_definition_id="pd_ghost",
    )
    assert r.status_code == 404
    assert r.json()["error"]["type"] == "PROPERTY_DEFINITION_NOT_FOUND"


def test_schema_validation_error(client):
    r = client.post("/api/v1/jobs", json={"property_definition_id": DEMO})  # 缺 points
    assert r.status_code == 422
    assert r.json()["error"]["type"] == "SCHEMA_VALIDATION_ERROR"


def test_invalid_job_leaves_no_trace(client):
    before = client.get("/api/v1/jobs").json()
    _post_job(
        client,
        [{"temperature": -1.0, "pressure": 50.0, "feed": [0.5, 0.5]}],
        property_definition_id=DEMO,
    )
    assert client.get("/api/v1/jobs").json() == before
