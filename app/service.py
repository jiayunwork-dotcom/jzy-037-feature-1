"""编排层：校验 → 求解 → 落库。

HTTP 路由只做协议适配；所有领域规则（正数、加和容差、Antoine 分母、
物性来源二选一等）都集中在这里，以带类型的领域错误拒绝非法输入。
求解内核是无状态纯函数，不同工况点、不同作业的中间量互不影响；
作业与其全部工况点在求解全部完成后才于同一事务落库（见 app/store.py），
因此非法输入不会留下半截作业。
"""
from __future__ import annotations

import math
from typing import Any

from . import activity, antoine
from .config import Settings
from .errors import (
    ActivityModelAlreadyPresent,
    AntoineCoefficientInvalid,
    AntoineCoefficientMissing,
    ComponentCountMismatch,
    DomainError,
    FeedSumOutOfTolerance,
    InvalidFeedComposition,
    InvalidPressure,
    InvalidSaturationPressure,
    InvalidTemperature,
    JobNotFound,
    PointNotFound,
    PropertyDefinitionNotFound,
    PropertySourceAmbiguous,
    PropertySourceMissing,
    TooManyPoints,
)
from .flash import PHASE_TWO_PHASE, isothermal_flash
from .nonideal_flash import isothermal_flash_nonideal
from .schemas import (
    ActivityModelSpec,
    JobCreate,
    OperatingPointIn,
    PropertyDefinitionCreate,
)
from .store import Store, new_job_id

N_COMPONENTS = 2  # 本服务范围限定二元体系

JOB_STATUS_COMPLETED = "completed"


# ---------- 物性定义校验 ----------


def validate_property_definition_payload(payload: PropertyDefinitionCreate) -> None:
    if len(payload.components) != N_COMPONENTS:
        raise ComponentCountMismatch(
            f"本服务仅支持二元体系，物性定义须恰好 {N_COMPONENTS} 个组分，"
            f"实际 {len(payload.components)} 个",
            {"component_count": len(payload.components)},
        )
    for index, component in enumerate(payload.components):
        if payload.source == "antoine":
            if component.antoine is None:
                raise AntoineCoefficientMissing(
                    f"组分[{index}] '{component.name}' 缺少 Antoine 系数"
                    "（source=antoine 时每个组分都必须给出 A、B、C）",
                    {"component_index": index, "component": component.name},
                )
            for field_name in ("A", "B", "C"):
                value = getattr(component.antoine, field_name)
                if not math.isfinite(value):
                    raise AntoineCoefficientInvalid(
                        f"组分[{index}] '{component.name}' 的 Antoine 系数 "
                        f"{field_name} 必须是有限数",
                        {
                            "component_index": index,
                            "component": component.name,
                            "coefficient": field_name,
                            "value": value,
                        },
                    )
        else:  # direct_psat
            psat = component.psat
            if psat is None or not math.isfinite(psat) or psat <= 0.0:
                raise InvalidSaturationPressure(
                    f"组分[{index}] '{component.name}' 直接给定的饱和蒸汽压"
                    "必须为正有限数",
                    {
                        "component_index": index,
                        "component": component.name,
                        "psat": psat,
                    },
                )
    # activity_model 为 None（缺省）= 理想定义，逐字保留旧行为；
    # 非缺省时按活度系数模型自身的规则校验（缺参/非有限/全域 γ 正有限）
    activity.validate_activity_model_spec(payload.activity_model)


# ---------- 工况点校验 ----------


def validate_operating_point(
    point: OperatingPointIn, point_index: int, feed_sum_tolerance: float
) -> None:
    if not math.isfinite(point.temperature) or point.temperature <= 0.0:
        raise InvalidTemperature(
            "温度必须为正的有限数",
            {"point_index": point_index, "temperature": point.temperature},
        )
    if not math.isfinite(point.pressure) or point.pressure <= 0.0:
        raise InvalidPressure(
            "压力必须为正的有限数",
            {"point_index": point_index, "pressure": point.pressure},
        )
    if len(point.feed) != N_COMPONENTS:
        raise InvalidFeedComposition(
            f"进料组成须恰好含 {N_COMPONENTS} 个组分（与物性定义的组分顺序一致）",
            {"point_index": point_index, "feed_length": len(point.feed)},
        )
    for component_index, zi in enumerate(point.feed):
        if not math.isfinite(zi) or zi <= 0.0:
            raise InvalidFeedComposition(
                f"进料组成 z[{component_index}] 必须为正的有限数",
                {
                    "point_index": point_index,
                    "component_index": component_index,
                    "value": zi,
                },
            )
    feed_sum = math.fsum(point.feed)
    if abs(feed_sum - 1.0) > feed_sum_tolerance:
        raise FeedSumOutOfTolerance(
            f"进料组成之和 {feed_sum!r} 与 1 的偏差超出容差 {feed_sum_tolerance}",
            {
                "point_index": point_index,
                "feed_sum": feed_sum,
                "tolerance": feed_sum_tolerance,
            },
        )


# ---------- 单点求解 ----------


def solve_point(
    prop: PropertyDefinitionCreate,
    point: OperatingPointIn,
    point_index: int,
    settings: Settings,
) -> dict[str, Any]:
    validate_operating_point(point, point_index, settings.feed_sum_tolerance)
    try:
        # K 值与饱和蒸汽压同源同序（app/antoine.py 是唯一入口）
        ideal_k_vals = antoine.k_values(prop, point.temperature, point.pressure)
        psat = antoine.saturation_pressures(prop, point.temperature)
    except DomainError as exc:
        exc.details.setdefault("point_index", point_index)
        raise

    if prop.activity_model is None:
        # 理想路径：一行不动地沿用旧求解内核，保证历史结果逐项一致
        result = isothermal_flash(
            list(point.feed),
            ideal_k_vals,
            f_tol=settings.rr_function_tolerance,
            x_tol=settings.rr_bracket_tolerance,
            max_iter=settings.rr_max_iterations,
        )
        liquid_model = "ideal"
        activity_coefficients = None
        k_residual = None
        k_iterations = None
    else:
        try:
            result = isothermal_flash_nonideal(
                list(point.feed),
                psat,
                point.pressure,
                prop.activity_model,
                k_tol=settings.nonideal_k_tolerance,
                ln_k_step=settings.nonideal_ln_k_step,
                max_iter=settings.nonideal_max_iterations,
                f_tol=settings.rr_function_tolerance,
                x_tol=settings.rr_bracket_tolerance,
                rr_max_iter=settings.rr_max_iterations,
            )
        except DomainError as exc:
            exc.details.setdefault("point_index", point_index)
            raise
        liquid_model = "margules"
        activity_coefficients = list(result.activity_coefficients)
        k_residual = result.nonideal_k_residual
        k_iterations = result.nonideal_iterations

    v = result.vapor_fraction
    if result.phase == PHASE_TWO_PHASE:
        assert result.liquid_composition is not None
        assert result.vapor_composition is not None
        material_balance_residual = max(
            abs(
                point.feed[i]
                - ((1.0 - v) * result.liquid_composition[i] + v * result.vapor_composition[i])
            )
            for i in range(N_COMPONENTS)
        )
    else:
        # 单相点：存在相的组成直接取进料组成，物料衡算残差恒为零
        material_balance_residual = 0.0
    return {
        "point_index": point_index,
        "label": point.label,
        "input": {
            "temperature": point.temperature,
            "pressure": point.pressure,
            "feed": list(point.feed),
        },
        "phase": result.phase,
        "vapor_fraction": v,
        "liquid_composition": result.liquid_composition,
        "vapor_composition": result.vapor_composition,
        "k_values": result.k_values,
        "saturation_pressures": psat,
        "liquid_model": liquid_model,
        "activity_coefficients": activity_coefficients,
        "bubble_sum": result.bubble_sum,
        "dew_sum": result.dew_sum,
        "nonideal_k_residual": k_residual,
        "nonideal_iterations": k_iterations,
        "rr_residual": result.rr_residual,
        "rr_iterations": result.rr_iterations,
        "material_balance_residual": material_balance_residual,
        "reason": result.reason,
    }


# ---------- 作业 ----------


def submit_job(store: Store, payload: JobCreate, settings: Settings) -> dict[str, Any]:
    has_reference = payload.property_definition_id is not None
    has_inline = payload.property_definition is not None
    if has_reference and has_inline:
        raise PropertySourceAmbiguous(
            "property_definition_id 与 property_definition 只能二选一："
            "要么引用已登记的物性定义，要么临时给出一份"
        )
    if not has_reference and not has_inline:
        raise PropertySourceMissing(
            "必须提供 property_definition_id（引用已登记物性定义）"
            "或 property_definition（临时物性定义）之一"
        )
    if len(payload.points) > settings.max_points_per_job:
        raise TooManyPoints(
            f"单个作业最多 {settings.max_points_per_job} 个工况点，"
            f"实际 {len(payload.points)} 个",
            {"point_count": len(payload.points), "max": settings.max_points_per_job},
        )

    if has_reference:
        assert payload.property_definition_id is not None
        snapshot: dict[str, Any] | None = store.get_property_definition(
            payload.property_definition_id
        )
        if snapshot is None:
            raise PropertyDefinitionNotFound(
                f"物性定义 '{payload.property_definition_id}' 不存在",
                {"property_definition_id": payload.property_definition_id},
            )
        property_definition_id: str | None = payload.property_definition_id
    else:
        assert payload.property_definition is not None
        validate_property_definition_payload(payload.property_definition)
        snapshot = payload.property_definition.model_dump()
        snapshot["id"] = None
        snapshot["created_at"] = None
        property_definition_id = None

    prop_model = PropertyDefinitionCreate.model_validate(snapshot)
    # 逐点求解；任何一点非法都会在此抛出带类型错误，作业整体不落库
    results = [
        solve_point(prop_model, point, index, settings)
        for index, point in enumerate(payload.points)
    ]

    job_id = new_job_id()
    store.create_job(
        job_id=job_id,
        property_definition_id=property_definition_id,
        property_snapshot=snapshot,
        status=JOB_STATUS_COMPLETED,
        points=results,
    )
    return get_job_detail(store, job_id)


def upgrade_property_definition(
    store: Store, definition_id: str, activity_model: ActivityModelSpec
) -> dict[str, Any]:
    """把已登记的理想物性升级为非理想（追加 Margules 参数）。

    - 登记项不存在 → 404；
    - 已经带活度系数模型 → 409（不允许覆盖/降级，避免静默改变后续作业语义）；
    - 参数本身非法 → 422（校验与新建登记项同一入口）。
    只更新登记项；历史作业的物性快照在 jobs 表里，本函数碰不到。
    """
    existing = store.get_property_definition(definition_id)
    if existing is None:
        raise PropertyDefinitionNotFound(
            f"物性定义 '{definition_id}' 不存在，无法升级",
            {"property_definition_id": definition_id},
        )
    if existing.get("activity_model") is not None:
        raise ActivityModelAlreadyPresent(
            f"物性定义 '{definition_id}' 已是带活度系数模型的非理想登记项，"
            "不允许再次追加或覆盖参数",
            {
                "property_definition_id": definition_id,
                "activity_model": existing["activity_model"],
            },
        )
    activity.validate_activity_model_spec(activity_model)
    updated = store.set_property_activity_model(
        definition_id, activity_model.model_dump()
    )
    assert updated is not None  # 上面刚取过，并发删除的窗口极小；为 None 走 500
    return updated


def get_job_detail(store: Store, job_id: str) -> dict[str, Any]:
    job = store.get_job(job_id)
    if job is None:
        raise JobNotFound(f"作业 '{job_id}' 不存在", {"job_id": job_id})
    return {
        "id": job["id"],
        "status": job["status"],
        "point_count": job["point_count"],
        "property_definition_id": job["property_definition_id"],
        "created_at": job["created_at"],
        "property_definition": job["property_snapshot"],
        "points": job["points"],
    }


def get_job_point(store: Store, job_id: str, point_index: int) -> dict[str, Any]:
    if not store.job_exists(job_id):
        raise JobNotFound(f"作业 '{job_id}' 不存在", {"job_id": job_id})
    point = store.get_job_point(job_id, point_index)
    if point is None:
        raise PointNotFound(
            f"作业 '{job_id}' 不存在工况点 {point_index}",
            {"job_id": job_id, "point_index": point_index},
        )
    return point
