"""Rachford-Rice 等温闪蒸求解内核。

模型：理想溶液 + 理想汽相。K_i 由调用方给定（见 app/antoine.py，
与本内核共用同一套组分下标：z[i] 与 K[i] 是同一组分）。

求解方法（全服务唯一的一套，任何入口都不另起炉灶）：
    **二分法（bisection）**，不用牛顿迭代。Rachford-Rice 函数

        f(V) = Σ_i z_i (K_i - 1) / (1 + V (K_i - 1))

    在 V ∈ (0, 1) 上严格单调递减
    （f'(V) = -Σ_i z_i (K_i-1)² / (1+V(K_i-1))² < 0），
    且经两相判定后必有 f(0) > 0 > f(1)，故二分法必然收敛，
    不需要牛顿法的初值选取与回退策略。

收敛判据（满足其一即停）：
    1. |f(V)| ≤ rr_function_tolerance（默认 1e-12）；
    2. 当前 bracket 宽度 ≤ rr_bracket_tolerance（默认 1e-14）；
    迭代上限 rr_max_iterations（默认 200；二分法约 50 次即可到 1e-14，
    上限只是保险，触发即抛 SolverNonConvergence，绝不返回近似解）。

单相判定（落在两相区之外时绝不硬解一个假的汽化率）：
    Σ z_i·K_i ≤ 1  → 泡点检验不通过 → 单相液体（V 取 0，x = z）；
    Σ z_i/K_i ≤ 1  → 露点检验不通过 → 单相蒸汽（V 取 1，y = z）；
    两者同时 > 1 才进入两相区求根。
    「所有 K_i 同时 > 1」⇒ Σ z_i/K_i < 1（蒸汽），「所有 K_i 同时 < 1」
    ⇒ Σ z_i·K_i < 1（液体），即需求中的全大于/全小于情形是上述判定的子集；
    返回结果的 reason 字段会显式说明落在哪一侧。
"""
from __future__ import annotations

from dataclasses import dataclass

from .errors import SolverInputError, SolverNonConvergence

PHASE_TWO_PHASE = "two_phase"
PHASE_LIQUID = "liquid"
PHASE_VAPOR = "vapor"


@dataclass
class FlashResult:
    phase: str  # "two_phase" | "liquid" | "vapor"
    vapor_fraction: float  # 两相时 ∈ (0,1)；液体 = 0.0；蒸汽 = 1.0
    liquid_composition: list[float] | None
    vapor_composition: list[float] | None
    k_values: list[float]
    bubble_sum: float
    dew_sum: float
    rr_residual: float | None
    rr_iterations: int | None
    reason: str | None


def rachford_rice_residual(vapor_fraction: float, z: list[float], k: list[float]) -> float:
    """f(V) = Σ z_i (K_i - 1) / (1 + V (K_i - 1))。"""
    v = vapor_fraction
    return sum(zi * (ki - 1.0) / (1.0 + v * (ki - 1.0)) for zi, ki in zip(z, k))


def solve_vapor_fraction(
    z: list[float],
    k: list[float],
    *,
    f_tol: float,
    x_tol: float,
    max_iter: int,
) -> tuple[float, float, int]:
    """二分法求 f(V) = 0 在开区间 (0, 1) 内的根，返回 (V, 残差, 迭代次数)。"""
    f_lo = rachford_rice_residual(0.0, z, k)
    f_hi = rachford_rice_residual(1.0, z, k)
    if not (f_lo > 0.0 and f_hi < 0.0):
        raise SolverNonConvergence(
            "Rachford-Rice 在 (0,1) 端点不变号，无法二分求根（调用前应先做单相判定）",
            {"f_at_0": f_lo, "f_at_1": f_hi},
        )
    lo, hi = 0.0, 1.0
    for iteration in range(1, max_iter + 1):
        mid = 0.5 * (lo + hi)
        f_mid = rachford_rice_residual(mid, z, k)
        if abs(f_mid) <= f_tol:
            return mid, f_mid, iteration
        if f_mid > 0.0:
            lo = mid
        else:
            hi = mid
        if hi - lo <= x_tol:
            mid = 0.5 * (lo + hi)
            return mid, rachford_rice_residual(mid, z, k), iteration
    raise SolverNonConvergence(
        f"Rachford-Rice 二分法 {max_iter} 次迭代未收敛",
        {"f_tol": f_tol, "x_tol": x_tol},
    )


def isothermal_flash(
    z: list[float],
    k: list[float],
    *,
    f_tol: float = 1e-12,
    x_tol: float = 1e-14,
    max_iter: int = 200,
) -> FlashResult:
    """给定进料组成 z 与平衡常数 K（同一组分顺序），返回闪蒸结果。"""
    if len(z) != len(k) or len(z) < 2:
        raise SolverInputError(
            "z 与 K 长度必须一致且至少两个组分",
            {"len_z": len(z), "len_k": len(k)},
        )

    bubble_sum = sum(zi * ki for zi, ki in zip(z, k))  # Σ z_i·K_i
    dew_sum = sum(zi / ki for zi, ki in zip(z, k))  # Σ z_i/K_i

    if bubble_sum <= 1.0:
        reason = (
            f"泡点检验：Σ z_i·K_i = {bubble_sum:.6g} ≤ 1，"
            "工况落在泡点以下的单相液相区（汽化率取 0，非求根结果）"
        )
        if all(ki < 1.0 for ki in k):
            reason += "；所有 K_i < 1，各组分均倾向液相"
        return FlashResult(
            phase=PHASE_LIQUID,
            vapor_fraction=0.0,
            liquid_composition=list(z),
            vapor_composition=None,
            k_values=list(k),
            bubble_sum=bubble_sum,
            dew_sum=dew_sum,
            rr_residual=None,
            rr_iterations=None,
            reason=reason,
        )

    if dew_sum <= 1.0:
        reason = (
            f"露点检验：Σ z_i/K_i = {dew_sum:.6g} ≤ 1，"
            "工况落在露点以上的单相汽相区（汽化率取 1，非求根结果）"
        )
        if all(ki > 1.0 for ki in k):
            reason += "；所有 K_i > 1，各组分均倾向汽相"
        return FlashResult(
            phase=PHASE_VAPOR,
            vapor_fraction=1.0,
            liquid_composition=None,
            vapor_composition=list(z),
            k_values=list(k),
            bubble_sum=bubble_sum,
            dew_sum=dew_sum,
            rr_residual=None,
            rr_iterations=None,
            reason=reason,
        )

    v, residual, iterations = solve_vapor_fraction(
        z, k, f_tol=f_tol, x_tol=x_tol, max_iter=max_iter
    )
    x = [zi / (1.0 + v * (ki - 1.0)) for zi, ki in zip(z, k)]
    y = [ki * xi for ki, xi in zip(k, x)]
    return FlashResult(
        phase=PHASE_TWO_PHASE,
        vapor_fraction=v,
        liquid_composition=x,
        vapor_composition=y,
        k_values=list(k),
        bubble_sum=bubble_sum,
        dew_sum=dew_sum,
        rr_residual=residual,
        rr_iterations=iterations,
        reason=None,
    )
