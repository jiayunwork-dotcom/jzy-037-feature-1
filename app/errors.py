"""带类型的领域错误。

所有非法输入与求解失败都以结构化 JSON 返回（见 app/main.py 的异常处理器）：

    {"error": {"type": <错误类型>, "message": <说明>, "details": {...}}}

而不是崩溃或返回近似解。
"""
from __future__ import annotations

from typing import Any


class DomainError(Exception):
    """领域错误基类：携带 HTTP 状态码、稳定错误类型与结构化细节。"""

    status_code: int = 422
    error_type: str = "DOMAIN_ERROR"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = dict(details or {})


# ---------- 工况点输入 ----------


class InvalidTemperature(DomainError):
    error_type = "INVALID_TEMPERATURE"


class InvalidPressure(DomainError):
    error_type = "INVALID_PRESSURE"


class InvalidFeedComposition(DomainError):
    error_type = "INVALID_FEED_COMPOSITION"


class FeedSumOutOfTolerance(DomainError):
    error_type = "FEED_SUM_OUT_OF_TOLERANCE"


# ---------- 物性定义 ----------


class ComponentCountMismatch(DomainError):
    error_type = "COMPONENT_COUNT_MISMATCH"


class AntoineCoefficientMissing(DomainError):
    error_type = "ANTOINE_COEFFICIENT_MISSING"


class AntoineCoefficientInvalid(DomainError):
    error_type = "ANTOINE_COEFFICIENT_INVALID"


class AntoineDenominatorZero(DomainError):
    error_type = "ANTOINE_DENOMINATOR_ZERO"


class InvalidSaturationPressure(DomainError):
    error_type = "INVALID_PSAT"


class PsatEvaluationFailed(DomainError):
    error_type = "PSAT_EVALUATION_FAILED"


# ---------- 作业提交 ----------


class PropertySourceMissing(DomainError):
    error_type = "PROPERTY_SOURCE_MISSING"


class PropertySourceAmbiguous(DomainError):
    error_type = "PROPERTY_SOURCE_AMBIGUOUS"


class TooManyPoints(DomainError):
    error_type = "TOO_MANY_POINTS"


# ---------- 资源不存在 ----------


class PropertyDefinitionNotFound(DomainError):
    status_code = 404
    error_type = "PROPERTY_DEFINITION_NOT_FOUND"


class JobNotFound(DomainError):
    status_code = 404
    error_type = "JOB_NOT_FOUND"


class PointNotFound(DomainError):
    status_code = 404
    error_type = "POINT_NOT_FOUND"


# ---------- 求解内核（理论上不应触发，触发即 500） ----------


class SolverInputError(DomainError):
    status_code = 500
    error_type = "SOLVER_INPUT_ERROR"


class SolverNonConvergence(DomainError):
    status_code = 500
    error_type = "SOLVER_NON_CONVERGENCE"
