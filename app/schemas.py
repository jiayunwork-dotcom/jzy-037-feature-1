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


class ComponentSpec(BaseModel):
    """单个组分的物性。source=antoine 时须给 antoine；source=direct_psat 时须给 psat。"""

    name: str = Field(min_length=1, max_length=128)
    antoine: AntoineCoefficients | None = None
    psat: float | None = None  # 直接给定的饱和蒸汽压，单位 = pressure_unit


class VanLaarParameters(BaseModel):
    """二元 van Laar 活度系数模型参数（A12 = ln γ1∞，A21 = ln γ2∞）。

    下标顺序与 components 一致：组分 1 → 组分 2 的无限稀释方向用 A12。
    参数的领域校验（正有限数）在 app/service.py，这里只做形状校验。
    """

    model: Literal["van_laar"] = "van_laar"
    A12: float
    A21: float


class PropertyDefinitionCreate(BaseModel):
    """可复用的二元体系物性定义（登记项）。

    components 的列表顺序即全服务唯一的组分下标顺序：
    工况点的 feed、结果中的 x/y/K 都按此顺序对齐。

    液相模型：
    - liquid_model 缺省为 "ideal"：K_i = P_i^sat/P，行为与历史版本完全一致；
    - liquid_model = "van_laar"：K_i = γ_i(x)·P_i^sat/P，必须给 activity_model；
      旧登记项可在不影响其历史作业的前提下追加 activity_model 升级
      （见 PATCH /property-definitions/{id}）。
    """

    name: str = Field(min_length=1, max_length=256)
    description: str | None = None
    temperature_unit: str = Field(default="K", min_length=1, max_length=32)
    pressure_unit: str = Field(default="kPa", min_length=1, max_length=32)
    source: Literal["antoine", "direct_psat"]
    liquid_model: Literal["ideal", "van_laar"] = "ideal"
    activity_model: VanLaarParameters | None = None
    components: list[ComponentSpec]


class PropertyDefinitionUpgrade(BaseModel):
    """把已登记的理想物性定义升级为非理想（仅允许追加活度系数参数）。"""

    liquid_model: Literal["van_laar"]
    activity_model: VanLaarParameters


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
    liquid_model: Literal["ideal", "van_laar"]  # 本次求解实际使用的液相模型
    activity_model: dict | None  # 非理想点的模型参数快照（理想点为 null）
    activity_coefficients: list[float] | None  # 平衡液相组成处的 γ（无平衡液相时为 null）
    bubble_sum: float  # Σ z_i·K_i，≤1 即泡点以下（单相液体）
    dew_sum: float  # Σ z_i/K_i，≤1 即露点以上（单相蒸汽）
    rr_residual: float | None  # 理想点二分法收敛时 |f(V)|，单相点为 null
    rr_iterations: int | None
    nonideal_residual: float | None = None  # 非理想点相平衡残差 |γ_i·Psat_i·x_i - P·y_i| 最大项
    nonideal_iterations: int | None = None  # 非理想路径求根累计二分迭代次数
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
