"""液相活度系数模型：双参数三后缀 Margules（二元体系）。

适用范围（刻意收窄，不做 NRTL/UNIQUAC 那类通用关联）：
    - 二元混合物；
    - 一个液相（模型不判别液液分相）；
    - 模型只依赖液相组成 x = (x1, x2) 与一对无量纲可调常数 a12、a21。

三后缀 Margules 方程（x2 = 1 − x1）：

    ln γ1 = x2² [ a12 + 2 (a21 − a12) x1 ]
    ln γ2 = x1² [ a21 + 2 (a12 − a21) x2 ]

端点行为可用于手工核对：
    x1→0：ln γ1 → a12（γ1 的无限稀释值 = e^a12），ln γ2 → 0；
    x1→1：ln γ2 → a21（γ2 的无限稀释值 = e^a21），ln γ1 → 0；
    a12 = a21 = 0 时 γ1 = γ2 ≡ 1，严格退化为理想溶液。

热力学一致性：公式天然满足二元 Gibbs-Duhem 方程，因此内部统一以
单自由度 x1 入参（x2 由 1 − x1 推出），不接受两份各自归一化的组成。

非法输入一律以带类型领域错误拒绝（见 app/errors.py）：
    - 缺 a12/a21 → ACTIVITY_MODEL_PARAMETER_MISSING；
    - 参数非有限 → ACTIVITY_MODEL_PARAMETER_INVALID；
    - 参数组在任意液相组成下算不出正有限活度系数（如 exp 溢出/下溢）
      → ACTIVITY_MODEL_PARAMETER_INVALID（登记/升级时即拒绝，不把坏参数
      留到求解时才爆）；
    - 求解中 x 越出 [0,1] → ACTIVITY_COMPOSITION_OUT_OF_DOMAIN；
    - 求解中在合法 x 处仍算出非正有限 γ → ACTIVITY_COEFFICIENT_INVALID。
"""
from __future__ import annotations

import math

from .errors import (
    ActivityCoefficientInvalid,
    ActivityCompositionOutOfDomain,
    ActivityModelParameterInvalid,
    ActivityModelParameterMissing,
)
from .schemas import ActivityModelSpec

MARGULES = "margules"

# 登记参数时扫描全组成域的步长（含 0 与 1 两个端点，共 101 个组成）
_DOMAIN_SCAN_STEPS = 100


def margules_ln_activity_coefficients(
    x1: float, a12: float, a21: float
) -> tuple[float, float]:
    """返回 (ln γ1, ln γ2)。x1 必须是 [0,1] 内的有限数。"""
    if not math.isfinite(x1) or not (0.0 <= x1 <= 1.0):
        raise ActivityCompositionOutOfDomain(
            f"液相组成 x1={x1!r} 超出 Margules 模型适用域 [0, 1]",
            {"x1": x1},
        )
    x2 = 1.0 - x1
    ln_g1 = x2 * x2 * (a12 + 2.0 * (a21 - a12) * x1)
    ln_g2 = x1 * x1 * (a21 + 2.0 * (a12 - a21) * x2)
    return ln_g1, ln_g2


def margules_activity_coefficients(
    x1: float, a12: float, a21: float
) -> tuple[float, float]:
    """返回 (γ1, γ2)，两者都必须为正有限数，否则拒绝。"""
    ln_g1, ln_g2 = margules_ln_activity_coefficients(x1, a12, a21)
    gammas: list[float] = []
    for component_index, ln_g in enumerate((ln_g1, ln_g2)):
        try:
            gamma = math.exp(ln_g)
        except OverflowError as exc:
            raise ActivityCoefficientInvalid(
                f"组分[{component_index}] 活度系数求值溢出"
                f"（ln γ={ln_g!r}，x1={x1!r}，a12={a12!r}，a21={a21!r}）",
                {
                    "component_index": component_index,
                    "x1": x1,
                    "ln_gamma": ln_g,
                    "a12": a12,
                    "a21": a21,
                },
            ) from exc
        if not math.isfinite(gamma) or gamma <= 0.0:
            raise ActivityCoefficientInvalid(
                f"组分[{component_index}] 活度系数不是正有限数"
                f"（γ={gamma!r}，x1={x1!r}）",
                {
                    "component_index": component_index,
                    "x1": x1,
                    "gamma": gamma,
                    "ln_gamma": ln_g,
                },
            )
        gammas.append(gamma)
    return gammas[0], gammas[1]


def activity_coefficients(model: ActivityModelSpec, x1: float) -> tuple[float, float]:
    """按模型分派；调用前 model 应已通过 validate_activity_model_spec。"""
    if model.model != MARGULES:
        raise ActivityModelParameterInvalid(
            f"不支持的活度系数模型 '{model.model}'（当前仅支持 'margules'）",
            {"model": model.model},
        )
    return margules_activity_coefficients(x1, model.a12, model.a21)


def validate_activity_model_spec(model: ActivityModelSpec | None) -> None:
    """登记/升级/求解共用的参数校验：缺参、非有限、全组成域 γ 正有限。

    None 表示理想溶液（默认），不报错——理想路径不归本模块管。
    """
    if model is None:
        return
    if model.model != MARGULES:
        raise ActivityModelParameterInvalid(
            f"不支持的活度系数模型 '{model.model}'（当前仅支持 'margules'）",
            {"model": model.model},
        )
    for field_name in ("a12", "a21"):
        value = getattr(model, field_name)
        if value is None:
            raise ActivityModelParameterMissing(
                f"Margules 活度系数模型缺少参数 {field_name}"
                "（非理想物性必须同时给出 a12 与 a21；理想物性请留空 activity_model）",
                {"parameter": field_name},
            )
        if not math.isfinite(value):
            raise ActivityModelParameterInvalid(
                f"Margules 参数 {field_name} 必须是有限数（收到 {value!r}）",
                {"parameter": field_name, "value": value},
            )

    # 全组成域预扫：合法参数组必须在整个 [0,1] 上给出正有限活度系数。
    # 在入口（而不是等求解到某个组成）就把坏参数挡掉。
    a12, a21 = model.a12, model.a21
    for step in range(_DOMAIN_SCAN_STEPS + 1):
        x1 = step / _DOMAIN_SCAN_STEPS
        try:
            margules_activity_coefficients(x1, a12, a21)
        except ActivityCoefficientInvalid as exc:
            raise ActivityModelParameterInvalid(
                f"Margules 参数 a12={a12!r}、a21={a21!r} 在液相组成 "
                f"x1={exc.details.get('x1')!r} 处算不出正有限活度系数，"
                "该参数组不可用于求解",
                {
                    "a12": a12,
                    "a21": a21,
                    "x1": exc.details.get("x1"),
                    "component_index": exc.details.get("component_index"),
                },
            ) from exc
