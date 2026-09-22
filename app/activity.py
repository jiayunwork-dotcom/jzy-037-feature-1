"""液相非理想性：二元双参数 van Laar 活度系数模型。

平衡常数的液相修正（理想汽相，Raoult 定律 + 活度）：

    K_i(T, P, x) = γ_i(x) · P_i^sat(T) / P

模型（van Laar，二元、双参数、对称端点）：

    ln γ_1 = A12 / (1 + (A12·x_1)/(A21·x_2))²
    ln γ_2 = A21 / (1 + (A21·x_2)/(A12·x_1))²

实现采用与之严格等价、在纯组分端点（x1=0/1）不产生 0/0 的数值稳定形式：

    令 u = A12·x_1，v = A21·x_2，s = u + v，则
        ln γ_1 = A12 · (v/s)²
        ln γ_2 = A21 · (u/s)²

性质：
- A12 = ln γ_1^∞（组分 1 在组分 2 中的无限稀释活度系数对数），
  A21 = ln γ_2^∞；γ_i 在端部分别回到 1（γ_1(0)=1，γ_2(1)=1）。
- 两个参数必须为正的有限数：van Laar 的 A12/A21 即 ln γ^∞，
  γ^∞ > 0 是其物理取值；取零时退化（应直接登记为理想体系），
  取负或非有限数无物理意义并使上面的分式失去定义，一律拒绝。
- 适用域限定为完全互溶的二元体系，组成 x_i ∈ [0,1]、Σx_i = 1。
  落在闭单纯形之外的组成以 ACTIVITY_MODEL_INPUT_INVALID 拒绝。
- 求值结果（γ 非有限或非正）以 ACTIVITY_COEFFICIENT_INVALID 拒绝——
  这是「模型参数导致不可接受活度系数」的兜底防线，合法正值参数配合
  合法组成本不应触发，触发即说明参数或求解状态不可接受，绝不把
  NaN/负 K 值送进后续求根。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .errors import (
    ActivityCoefficientInvalid,
    ActivityModelInputInvalid,
    ActivityModelParameterInvalid,
)

LIQUID_MODEL_IDEAL = "ideal"
LIQUID_MODEL_VAN_LAAR = "van_laar"


@dataclass(frozen=True)
class VanLaarModel:
    """双参数 van Laar 活度系数模型（不可变、纯函数、可安全并发共享）。"""

    A12: float
    A21: float

    def __post_init__(self) -> None:
        for name, value in (("A12", self.A12), ("A21", self.A21)):
            if not math.isfinite(value):
                raise ActivityModelParameterInvalid(
                    f"van Laar 参数 {name} 必须是有限数，实际 {value!r}",
                    {"parameter": name, "value": value},
                )
            if value <= 0.0:
                raise ActivityModelParameterInvalid(
                    f"van Laar 参数 {name} 必须为正数（等于 ln γ^∞，取 0 即理想体系，"
                    "请直接登记/保持为理想物性定义；负值无物理意义）",
                    {"parameter": name, "value": value},
                )

    @staticmethod
    def _check_composition(x: list[float]) -> None:
        if len(x) != 2:
            raise ActivityModelInputInvalid(
                "van Laar 模型仅支持二元体系，组成须恰好含 2 个组分",
                {"composition_length": len(x)},
            )
        for index, xi in enumerate(x):
            if not math.isfinite(xi):
                raise ActivityModelInputInvalid(
                    f"液相组成 x[{index}] 必须是有限数，实际 {xi!r}",
                    {"component_index": index, "value": xi},
                )
            if xi < 0.0 or xi > 1.0:
                raise ActivityModelInputInvalid(
                    f"液相组成 x[{index}] = {xi!r} 超出模型适用域 [0, 1]",
                    {"component_index": index, "value": xi},
                )

    def activity_coefficients(self, x: list[float]) -> list[float]:
        """按 (γ_1, γ_2) 返回活度系数；组分顺序与物性定义 components 一致。"""
        self._check_composition(x)
        x1, x2 = x
        if not math.isclose(x1 + x2, 1.0, abs_tol=1e-9):
            raise ActivityModelInputInvalid(
                f"液相组成之和 {x1 + x2!r} 不等于 1（超出容差 1e-9），"
                "超出模型适用的二元闭单纯形",
                {"x": list(x), "sum": x1 + x2},
            )
        u = self.A12 * x1
        v = self.A21 * x2
        s = u + v
        if s <= 0.0:
            # 合法正值参数 + x 在闭单纯形上时唯一发生在 x1=x2=0（Σx=0），
            # 这不是合法液相组成，明确拒绝而不是返回 inf/NaN。
            raise ActivityModelInputInvalid(
                "液相组成全为零，无法求值活度系数（要求 Σx_i = 1）",
                {"x": list(x)},
            )
        ratio_v = v / s
        ratio_u = u / s
        log_gamma = (self.A12 * ratio_v * ratio_v, self.A21 * ratio_u * ratio_u)
        gammas: list[float] = []
        for index, lg in enumerate(log_gamma):
            try:
                gamma = math.exp(lg)
            except OverflowError:
                gamma = math.inf
            if not math.isfinite(gamma) or gamma <= 0.0:
                raise ActivityCoefficientInvalid(
                    f"van Laar 求得 γ[{index}] 非正有限数（{gamma!r}），"
                    "拒绝该模型参数/组成组合",
                    {
                        "component_index": index,
                        "x": list(x),
                        "A12": self.A12,
                        "A21": self.A21,
                        "ln_gamma": lg,
                    },
                )
            gammas.append(gamma)
        return gammas
