"""Antoine 饱和蒸汽压与平衡常数 K_i。

约定（全服务唯一的一套，闪蒸内核与蒸汽压计算共用同一组分下标）：

- 组分顺序 = 物性定义 ``components`` 列表的顺序，索引 0/1 即组分 1/2。
  工况点的进料组成、结果中的液/汽相组成、K 值全部按这个顺序对齐，
  不存在第二份独立维护的组分顺序。
- K_i = P_i^sat / P（理想溶液 + 理想汽相）。
- P_i^sat 要么由 Antoine 方程（自然对数形）ln(P^sat) = A - B / (T + C)
  按温度算出，要么由物性定义直接给定（source = "direct_psat"）；
  两种来源都收敛到本模块的 ``k_values`` 这一个入口。
- 温度、压力单位以物性定义声明的 temperature_unit / pressure_unit 为准，
  服务不做单位换算；调用方须保证 P 与 P^sat 同单位、T 与 Antoine 系数同单位。
- source = "direct_psat" 时温度不参与 K 值计算（仍会校验并随结果记录）。
"""
from __future__ import annotations

import math

from .errors import (
    AntoineCoefficientMissing,
    AntoineDenominatorZero,
    InvalidSaturationPressure,
    PsatEvaluationFailed,
)
from .schemas import AntoineCoefficients, ComponentSpec, PropertyDefinitionCreate


def saturation_pressure_antoine(coeffs: AntoineCoefficients, temperature: float) -> float:
    """自然对数形 Antoine：ln(P^sat) = A - B / (T + C)。"""
    denominator = temperature + coeffs.C
    if denominator == 0.0:
        raise AntoineDenominatorZero(
            f"Antoine 分母 T + C 为零（T={temperature!r}, C={coeffs.C!r}）",
            {"temperature": temperature, "C": coeffs.C},
        )
    exponent = coeffs.A - coeffs.B / denominator
    try:
        value = math.exp(exponent)
    except OverflowError as exc:
        raise PsatEvaluationFailed(
            f"Antoine 求值溢出（指数 {exponent!r}），请检查系数与温度单位",
            {"temperature": temperature, "exponent": exponent},
        ) from exc
    if not math.isfinite(value) or value <= 0.0:
        raise PsatEvaluationFailed(
            f"Antoine 求得的饱和蒸汽压非正有限数（{value!r}）",
            {"temperature": temperature, "value": value},
        )
    return value


def _component_saturation_pressure(
    component: ComponentSpec, source: str, temperature: float
) -> float:
    if source == "antoine":
        if component.antoine is None:
            raise AntoineCoefficientMissing(
                f"组分 '{component.name}' 缺少 Antoine 系数（source=antoine 时必填）",
                {"component": component.name},
            )
        return saturation_pressure_antoine(component.antoine, temperature)
    psat = component.psat
    if psat is None or not math.isfinite(psat) or psat <= 0.0:
        raise InvalidSaturationPressure(
            f"组分 '{component.name}' 直接给定的饱和蒸汽压必须为正有限数",
            {"component": component.name, "psat": psat},
        )
    return psat


def saturation_pressures(prop: PropertyDefinitionCreate, temperature: float) -> list[float]:
    """按物性定义的组分顺序返回 [P_1^sat, P_2^sat]。"""
    return [
        _component_saturation_pressure(component, prop.source, temperature)
        for component in prop.components
    ]


def k_values(prop: PropertyDefinitionCreate, temperature: float, pressure: float) -> list[float]:
    """K_i = P_i^sat(T) / P，组分顺序与物性定义 components 完全一致。"""
    return [psat / pressure for psat in saturation_pressures(prop, temperature)]
