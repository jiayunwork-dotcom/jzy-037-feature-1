"""API 契约（Pydantic 模型）。

注意分层：这里只做「形状」校验（类型、必填、最小约束）；
领域规则（正数、组成加和容差、Antoine 分母非零等）在 app/service.py
以带类型的领域错误拒绝，见 app/errors.py。
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AntoineCoefficients(BaseModel):
    """自然对数形 Antoine 方程：ln(P_sat) = A - B / (T + C)。

    P_sat 的单位 = 所属物性定义的 pressure_unit；
    T 的单位 = 所属物性定义的 temperature_unit。服务不做单位换算。
    """

    A: float
    B: float
    C: float
    form: Literal["ln"] = "ln"


class ActivityModelSpec(BaseModel):
    """双参数活度系数模型（三后缀 Margules，二元体系）。

    仅依赖液相组成 x 与一对可调常数（无量纲，沿同一条相互作用边成对出现）：

        ln γ1 = x2² [ a12 + 2 (a21 − a12) x1 ]
        ln γ2 = x1² [ a21 + 2 (a12 − a21) x2 ]

    a12 对应无限稀释极限 x1→0 时的 ln γ1，a21 对应 x2→0 时的 ln γ2；
    a12 = a21 = 0（或二者都缺省）即退化为理想溶液（γ ≡ 1）。
    平衡常数相应改为 K_i = γ_i(x) · P_i^sat(T) / P。
    """

    model: Literal["margules"] = "margules"
    a12: float | None = None
    a21: float | None = None


class ActivityModelUpgrade(BaseModel):
    """把已有理想登记项升级为非理想登记项时的请求体。"""

    activity_model: ActivityModelSpec


class ComponentSpec(BaseModel):
    """单个组分的物性。source=antoine 时须给 antoine；source=direct_psat 时须给 psat。"""

    name: str = Field(min_length=1, max_length=128)
    antoine: AntoineCoefficients | None = None
    psat: float | None = None  # 直接给定的饱和蒸汽压，单位 = pressure_unit


class PropertyDefinitionCreate(BaseModel):
    """可复用的二元体系物性定义（登记项）。

    components 的列表顺序即全服务唯一的组分下标顺序：
    工况点的 feed、结果中的 x/y/K 都按此顺序对齐。
    """

    name: str = Field(min_length=1, max_length=256)
    description: str | None = None
    temperature_unit: str = Field(default="K", min_length=1, max_length=32)
    pressure_unit: str = Field(default="kPa", min_length=1, max_length=32)
    source: Literal["antoine", "direct_psat"]
    components: list[ComponentSpec]
    # null（默认）= 理想溶液，K_i = P_i^sat/P，行为与旧版完全一致；
    # 给值 = 非理想液相，K_i = γ_i(x)·P_i^sat/P。
    activity_model: ActivityModelSpec | None = None


class PropertyDefinition(PropertyDefinitionCreate):
    id: str | None = None
    created_at: str | None = None


class PointInput(BaseModel):
    temperature: float
    pressure: float
    feed: list[float]


class OperatingPointIn(PointInput):
    label: str | None = Field(default=None, max_length=256)


class JobCreate(BaseModel):
    """核算作业：一份物性定义（引用或临时）+ 一组工况点。二者有且仅有一个。"""

    property_definition_id: str | None = None
    property_definition: PropertyDefinitionCreate | None = None
    points: list[OperatingPointIn] = Field(min_length=1)


class PointResult(BaseModel):
    point_index: int
    label: str | None
    input: PointInput
    phase: Literal["two_phase", "liquid", "vapor"]
    vapor_fraction: float
    liquid_composition: list[float] | None  # 单相蒸汽时不存在液相 → null
    vapor_composition: list[float] | None  # 单相液体时不存在汽相 → null
    k_values: list[float]
    saturation_pressures: list[float]
    # 以下四字段是后加的；旧作业落库的点 JSON 没有它们，
    # 默认值保证历史数据经响应模型读出时自动呈现为「理想、无非理想迭代量」
    liquid_model: str = "ideal"  # "ideal" | "margules"：该点落库快照所用的液相模型
    # 液相活度系数：两相/液相点取最终液相组成处的 γ；
    # 单相汽相点取隐式露点液相组成处的 γ；理想模型为 null
    activity_coefficients: list[float] | None = None
    nonideal_k_residual: float | None = None  # 非理想外层 max|Δln K|，理想/单相点为 null
    nonideal_iterations: int | None = None  # 非理想外层迭代次数，理想/单相点为 null
    bubble_sum: float  # Σ z_i·K_i，≤1 即泡点以下（单相液体）
    dew_sum: float  # Σ z_i/K_i，≤1 即露点以上（单相蒸汽）
    rr_residual: float | None  # 二分法收敛时 |f(V)|，单相点为 null
    rr_iterations: int | None
    material_balance_residual: float  # max_i |z_i - ((1-V)x_i + V·y_i)|
    reason: str | None  # 单相判定的理由；两相点为 null


class JobSummary(BaseModel):
    id: str
    status: str
    point_count: int
    property_definition_id: str | None
    created_at: str


class JobDetail(JobSummary):
    property_definition: PropertyDefinition  # 提交时的物性快照
    points: list[PointResult]
