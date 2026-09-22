"""FastAPI 应用装配：路由、异常处理、启动期种子数据。"""
from __future__ import annotations

import logging
import math
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import Settings, get_settings
from .errors import DomainError
from .routes import jobs, properties
from .seed import (
    seed_demo_nonideal_property_definition,
    seed_demo_property_definition,
)
from .store import Store

logger = logging.getLogger("flash_service")


def _json_safe(value: Any) -> Any:
    """把 details 里的非有限浮点（nan/inf）转成字符串，保证响应可 JSON 序列化。"""
    if isinstance(value, float) and not math.isfinite(value):
        return repr(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value

_API_DESCRIPTION = """\
二元体系等温闪蒸核算服务（理想汽相；液相支持理想溶液与 van Laar 非理想模型）。

- 理想液相（默认，历史行为不变）：K_i = P_i^sat(T) / P。
- 非理想液相（liquid_model=van_laar）：K_i = γ_i(x)·P_i^sat(T) / P，
  lnγ 由双参数 van Laar 模型按当前液相组成求值；单相判定改为泡点压力
  （闭式 P_b=ΣzγPsat）与露点压力（一维极大化 P_d）检验，两相区以
  泡点函数 B(x1)=P 二分 + 杠杆规则求汽化率。
- P_i^sat 由 Antoine（自然对数形 ln P^sat = A - B/(T+C)）算出或直接给定。
- 以「核算作业」为核心：提交作业（物性定义引用/临时 + 工况点组）→ 逐点求解落库
  → 按作业号取回全部结果或单个工况点；作业保存当次物性快照，登记项升级
  不影响历史作业。
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = Store(settings.db_path)
    if settings.demo_seed_enabled:
        seed_demo_property_definition(store)
        seed_demo_nonideal_property_definition(store)

    app = FastAPI(
        title="二元等温闪蒸核算服务",
        version="0.1.0",
        description=_API_DESCRIPTION,
    )
    app.state.settings = settings
    app.state.store = store

    app.include_router(properties.router, prefix="/api/v1")
    app.include_router(jobs.router, prefix="/api/v1")

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "type": exc.error_type,
                    "message": exc.message,
                    "details": _json_safe(exc.details),
                }
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "type": "SCHEMA_VALIDATION_ERROR",
                    "message": "请求未通过模式校验（字段缺失或类型错误）",
                    "details": _json_safe(jsonable_encoder(exc.errors())),
                }
            },
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理异常: %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "type": "INTERNAL_ERROR",
                    "message": "服务内部错误",
                    "details": {},
                }
            },
        )

    @app.get("/", tags=["meta"])
    def root() -> dict:
        return {
            "service": "binary-isothermal-flash",
            "version": "0.2.0",
            "models": {
                "ideal": "K_i = P_i^sat(T) / P（默认，历史行为）",
                "van_laar": "K_i = gamma_i(x)·P_i^sat(T) / P，二元双参数 A12、A21",
            },
            "solver": {
                "ideal": "Rachford-Rice: Σ z_i (K_i - 1) / (1 + V (K_i - 1)) = 0, V ∈ (0, 1)，二分法",
                "van_laar": (
                    "泡点/露点压力单相判定；两相区 B(x1)=P 二分 + 杠杆规则求 V"
                ),
            },
            "api": {
                "docs": "/docs",
                "jobs": "/api/v1/jobs",
                "property_definitions": "/api/v1/property-definitions",
            },
        }

    @app.get("/healthz", tags=["meta"])
    def healthz() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
