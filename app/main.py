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
from .seed import seed_all
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
二元体系等温闪蒸核算服务（理想汽相；液相可选理想溶液或双参数活度系数模型）。

- 平衡常数：
  · 理想液相（默认）：K_i = P_i^sat(T) / P；
  · 非理想液相（Margules）：K_i = γ_i(x)·P_i^sat(T) / P，
    γ 由三后缀 Margules 双参数模型按液相组成算出，K 在单相判定与
    两相区迭代中都随组成更新。
  P_i^sat 由 Antoine（ln P^sat = A - B/(T+C)）算出或直接给定，共用同一套组分下标。
- 汽化率：Rachford-Rice 方程二分法求根（K 冻结时严格单调，全服务唯一内层求根器）；
  非理想路径在其外做 K 逐次代入迭代（max|Δln K| ≤ 1e-10）。
- 单相判定（K 在正确组成处取值）：Σ z_i·K_i(z) ≤ 1 → 泡点以下单相液体；
  露点隐式组成处 Σ z_i/K_i(x) ≤ 1 → 露点以上单相蒸汽。
- 以「核算作业」为核心：提交作业（物性定义引用/临时 + 工况点组）→ 逐点求解落库
  → 按作业号取回全部结果或单个工况点；作业存完整物性快照，可追溯所用模型与参数。
"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = Store(settings.db_path)
    if settings.demo_seed_enabled:
        seed_all(store)

    app = FastAPI(
        title="二元等温闪蒸核算服务",
        version="0.2.0",
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
                "ideal": "理想液相：K_i = P_i^sat(T) / P（activity_model 缺省）",
                "margules": (
                    "非理想液相：K_i = γ_i(x)·P_i^sat(T) / P；"
                    "三后缀 Margules 双参数 a12、a21"
                ),
            },
            "solver": {
                "equation": "Rachford-Rice: Σ z_i (K_i - 1) / (1 + V (K_i - 1)) = 0, V ∈ (0, 1)",
                "inner_method": "bisection（二分法，K 冻结时严格单调，不使用牛顿迭代）",
                "outer_method": "非理想路径：K 逐次代入，ln K 有界步长，判据 max|Δln K|",
                "function_tolerance": settings.rr_function_tolerance,
                "bracket_tolerance": settings.rr_bracket_tolerance,
                "max_iterations": settings.rr_max_iterations,
                "nonideal_k_tolerance": settings.nonideal_k_tolerance,
                "nonideal_max_iterations": settings.nonideal_max_iterations,
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
