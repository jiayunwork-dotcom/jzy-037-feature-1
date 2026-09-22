"""物性定义登记项的 HTTP 路由。

物性定义是可复用的登记项：登记后多个作业可按 id 引用；
提交作业时也可以临时内联给出（见 jobs 路由），内联定义不进入登记列表。
"""
from __future__ import annotations

from fastapi import APIRouter, Request

from ..deps import get_store
from ..errors import PropertyDefinitionNotFound
from ..schemas import PropertyDefinition, PropertyDefinitionCreate
from ..service import validate_property_definition_payload

router = APIRouter(prefix="/property-definitions", tags=["property-definitions"])


@router.post("", status_code=201, response_model=PropertyDefinition)
def register_property_definition(
    payload: PropertyDefinitionCreate, request: Request
) -> dict:
    """登记一份二元体系物性定义（Antoine 系数或直接给定的饱和蒸汽压）。"""
    validate_property_definition_payload(payload)
    return get_store(request).create_property_definition(payload.model_dump())


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
