"""物性定义登记项的 HTTP 路由。

物性定义是可复用的登记项：登记后多个作业可按 id 引用；
提交作业时也可以临时内联给出（见 jobs 路由），内联定义不进入登记列表。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..deps import get_store
from ..errors import PropertyDefinitionNotFound
from ..schemas import (
    ActivityModelUpgrade,
    PropertyDefinition,
    PropertyDefinitionCreate,
)
from .. import service

router = APIRouter(prefix="/property-definitions", tags=["property-definitions"])


@router.post("", status_code=201, response_model=PropertyDefinition)
def register_property_definition(
    payload: PropertyDefinitionCreate, request: Request
) -> dict:
    """登记一份二元体系物性定义（Antoine 系数或直接给定的饱和蒸汽压）。

    activity_model 缺省 = 理想溶液（K=P^sat/P）；给 Margules 参数 =
    非理想液相（K=γ(x)·P^sat/P）。
    """
    service.validate_property_definition_payload(payload)
    return get_store(request).create_property_definition(payload.model_dump())


@router.post(
    "/{definition_id}/activity-model",
    response_model=PropertyDefinition,
)
def upgrade_property_definition(
    definition_id: str, payload: ActivityModelUpgrade, request: Request
) -> dict:
    """给已登记的理想物性追加活度系数模型，升级为非理想登记项。

    历史作业落库时保存的是各自的物性快照，本次升级不会改动任何历史结果；
    已是非理想的登记项返回 409，不允许覆盖。
    """
    return service.upgrade_property_definition(
        get_store(request), definition_id, payload.activity_model
    )


@router.get("", response_model=list[PropertyDefinition])
def list_property_definitions(request: Request) -> list[dict]:
    """列出全部已登记的物性定义（含预置的示范定义）。"""
    return get_store(request).list_property_definitions()


@router.get("/{definition_id}", response_model=PropertyDefinition)
def get_property_definition(definition_id: str, request: Request) -> dict:
    """按 id 取回一份物性定义。"""
    record = get_store(request).get_property_definition(definition_id)
    if record is None:
        raise PropertyDefinitionNotFound(
            f"物性定义 '{definition_id}' 不存在",
            {"property_definition_id": definition_id},
        )
    return record
