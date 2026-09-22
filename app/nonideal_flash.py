"""非理想液相的二元等温闪蒸内核（van Laar + 理想汽相）。

与理想路径（app/flash.py）共用同一套组分下标约定，但求解前提发生了根本
变化：K_i = γ_i(x)·P_i^sat(T)/P **依赖液相组成 x**，因此

- 旧的单相判据 ΣzK≤1 / Σz/K≤1 不能直接套用：那里的 K 是与 x 无关的
  常数，「进料在 x=z 处的 K」与「相平衡液相处的 K」是同一个；现在两者
  不同，必须重新推导。
- Rachford-Rice 对每一组固定 K 的单调性（见 app/flash.py）仍然成立，
  但 V 与 x 通过物料衡算耦合，f(V;x)=0 不再是单变量方程，旧的二分流程
  不能整体照搬。

本模块的处理（二元体系专用，完全互溶、无 LLE）：

1. 泡点压力（闭式，无需求根）
   P_b(z) = Σ_i z_i·γ_i(z)·P_i^sat。若 P ≥ P_b → 单相液体（V=0，x=z）。

2. 露点压力（一元二分求根）
   第一滴液相组成 x* 满足平衡 y=z，即 x_i·γ_i(x*)·P_i^sat = z_i·P_d。
   两个组分须给出同一个 P_d，配平成一元方程
       H(x_1) = x_1·γ_1(x)·P_1^sat/z_1 − x_2·γ_2(x)·P_2^sat/z_2 = 0,
       P_d = x_1·γ_1·P_1^sat/z_1（= 另一组分的对应值），x_1+x_2=1。
   H(0+) = −e^A21·P_2^sat/z_2 < 0，H(1−) = P_1^sat/z_1 > 0，端点天然
   异号。**强正偏差/共沸体系下 H 可有三个根**（露点曲线折曲，两侧是
   不稳定根，其 P_d 反高于泡点压力）：物理稳定根是从低压加压时最先
   接触两相区的那个，即各根中 P_d **最小**者（也必满足 P_d ≤ P_b；
   等价于取与进料同在共沸侧的根），绝不能按最大 P_d 选。

3. 两相区：液相组成直接参数化
   每个液相组成 x 都唯一决定一个与之平衡的泡点压力
       B(x_1) = x_1·γ_1(x)·P_1^sat + x_2·γ_2(x)·P_2^sat，
   两相平衡即 B(x_1) = P，平衡汽相 y_i = x_i·γ_i·P_i^sat/P，
   再由物料衡算（杠杆规则）
       V = (z_1 − x_1)/(y_1 − x_1)
   反求汽化率。在 [0,1] 上稠密符号扫描 B(x_1)−P 的全部变号区间并逐一
   **二分**（共沸体系 B 有内部极大值时可有两个根），唯一接受同时满足
   x_1∈(0,1) 且 V∈[0,1] 的根；不存在则抛 SolverNonConvergence，
   绝不返回近似解。扫描密度 1024 区间，van Laar 二元 B 函数光滑且至多
   一个内部极值，该密度足以发现任何符号区间；随后二分保证收敛精度。

求解仍然全部使用**二分法等无需求导的一维方法**（与理想路径同一
方法学），不引入牛顿迭代的初值/回退问题。所有函数无状态、无共享
中间量，可安全并发调用。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .activity import VanLaarModel
from .errors import SolverNonConvergence
from .flash import (
    PHASE_LIQUID,
    PHASE_TWO_PHASE,
    PHASE_VAPOR,
    FlashResult,
)


@dataclass(frozen=True)
class NonidealSettings:
    x_tol: float = 1e-12
    f_tol: float = 1e-10
    max_iter: int = 200
    scan_intervals: int = 1024


def _bisect_signs(
    func,
    lo: float,
    hi: float,
    sign_lo: int,
    sign_hi: int,
    *,
    x_tol: float,
    f_tol: float,
    max_iter: int,
) -> tuple[float, float, int]:
    """给定端点符号的二分求根，返回 (根, 残差, 迭代次数)。

    func 只需在内部点可求值；端点本身可能不可求值（露点配平函数
    在 x1=0/1 处为有限值，但仍按符号入参处理，统一逻辑）。
    """
    if sign_lo * sign_hi >= 0:
        raise SolverNonConvergence(
            "二分端点不变号", {"lo": lo, "hi": hi, "sign_lo": sign_lo, "sign_hi": sign_hi}
        )
    for iteration in range(1, max_iter + 1):
        mid = 0.5 * (lo + hi)
        f_mid = func(mid)
        if abs(f_mid) <= f_tol:
            return mid, f_mid, iteration
        sign_mid = 1 if f_mid > 0.0 else -1
        if sign_mid == sign_lo:
            lo, sign_lo = mid, sign_mid
        else:
            hi, sign_hi = mid, sign_mid
        if hi - lo <= x_tol:
            mid = 0.5 * (lo + hi)
            return mid, func(mid), iteration
    raise SolverNonConvergence(
        f"非理想闪蒸二分法 {max_iter} 次迭代未收敛", {"x_tol": x_tol, "f_tol": f_tol}
    )


def _bubble_pressure(model: VanLaarModel, x: list[float], psat: list[float]) -> float:
    """B(x) = Σ x_i·γ_i(x)·P_i^sat。"""
    gamma = model.activity_coefficients(x)
    return sum(xi * gi * pi for xi, gi, pi in zip(x, gamma, psat))


def _find_dew(
    model: VanLaarModel,
    z: list[float],
    psat: list[float],
    settings: NonidealSettings,
) -> tuple[float, list[float], list[float], list[float], int]:
    """求露点：返回 (P_d, x*, γ(x*), K(x*) at P_d, 二分迭代次数)。

    配平函数（y=z，两组分给出同一压力）：
        H(x1) = x1·γ1·P1sat/z1 − x2·γ2·P2sat/z2，
        H(0+) < 0，H(1−) > 0。在稠密网格上找全部变号区间逐一二分，
        共沸体系可有多个根：取 P_d 最小者（稳定根，见模块文档）。
    """

    def balance(x1: float) -> float:
        gamma = model.activity_coefficients([x1, 1.0 - x1])
        return (
            x1 * gamma[0] * psat[0] / z[0]
            - (1.0 - x1) * gamma[1] * psat[1] / z[1]
        )

    n = settings.scan_intervals
    grid = [i / n for i in range(n + 1)]
    values = [balance(x1) for x1 in grid]
    # H(0)、H(1) 用闭式极限值，避免在纯组分端点求值的舍入问题
    h0 = -model.activity_coefficients([0.0, 1.0])[1] * psat[1] / z[1]
    h1 = psat[0] / z[0]
    values[0], values[n] = h0, h1

    best: tuple[float, list[float], list[float], list[float]] | None = None
    best_pd = math.inf
    total_iterations = 0
    for i in range(n):
        f_lo, f_hi = values[i], values[i + 1]
        if f_lo == 0.0:
            x_root, iters = grid[i], 0
        elif f_lo * f_hi < 0.0:
            x_root, _, iters = _bisect_signs(
                balance, grid[i], grid[i + 1],
                1 if f_lo > 0 else -1, 1 if f_hi > 0 else -1,
                x_tol=settings.x_tol, f_tol=settings.f_tol,
                max_iter=settings.max_iter,
            )
        else:
            continue
        total_iterations += iters
        if not (0.0 < x_root < 1.0):
            continue
        x = [x_root, 1.0 - x_root]
        gamma = model.activity_coefficients(x)
        p_d = x[0] * gamma[0] * psat[0] / z[0]
        p_d_check = x[1] * gamma[1] * psat[1] / z[1]
        # 配平一致性：两组分给出的压力必须相同。容差 1e-6（相对）远严于
        # 闪蒸所需精度，仅用于排除真正错误的根；注意 H 的绝对尺度可达
        # 10^4（P_sat 跨度大且组成极稀时），不能取比 f_tol 可达到的
        # 相对精度更严的阈值。
        if abs(p_d - p_d_check) > 1e-6 * max(p_d, 1.0):
            continue
        if p_d < best_pd:
            best_pd = p_d
            k = [gamma[j] * psat[j] / p_d for j in range(2)]
            best = (p_d, x, gamma, k)

    if best is None:
        raise SolverNonConvergence(
            "露点配平方程 H(x1)=0 在 (0,1) 内未找到可用根",
            {"z": list(z), "A12": model.A12, "A21": model.A21},
        )
    return (*best, total_iterations)


def isothermal_flash_nonideal(
    z: list[float],
    psat: list[float],
    pressure: float,
    model: VanLaarModel,
    *,
    x_tol: float = 1e-12,
    f_tol: float = 1e-10,
    max_iter: int = 200,
    scan_intervals: int = 1024,
) -> FlashResult:
    """给定进料 z、饱和蒸汽压 P^sat(T)、总压 P 与 van Laar 模型，返回闪蒸结果。"""
    settings = NonidealSettings(
        x_tol=x_tol, f_tol=f_tol, max_iter=max_iter, scan_intervals=scan_intervals
    )

    # 泡点（闭式）与露点（配平分）：单相判定的两条重新推导后的边界
    gamma_z = model.activity_coefficients(list(z))
    p_bubble = sum(z[i] * gamma_z[i] * psat[i] for i in range(2))
    p_dew, _, _, k_dew, dew_iterations = _find_dew(model, z, psat, settings)
    if p_dew > p_bubble * (1.0 + 1e-9):
        # 物理上露点压力不高于泡点压力（纯组分/共沸点处相等）；
        # 出现反转说明参数或求根状态不可接受，不静默吞掉。
        raise SolverNonConvergence(
            "露点压力高于泡点压力，模型结果不自洽",
            {"P_dew": p_dew, "P_bubble": p_bubble, "A12": model.A12, "A21": model.A21},
        )

    bubble_sum = p_bubble / pressure  # ≤1 ⟺ P ≥ P_b（单相液体）
    dew_sum = pressure / p_dew  # ≤1 ⟺ P ≤ P_d（单相蒸汽）
    k_bubble = [gamma_z[i] * psat[i] / pressure for i in range(2)]

    # 1) P ≥ P_b(z) → 单相液体
    if pressure >= p_bubble:
        reason = (
            f"泡点检验（非理想）：P = {pressure:.6g} ≥ P_b(z) = {p_bubble:.6g}"
            "（P_b = Σ z_i·γ_i(z)·P_i^sat），工况落在泡点以下的单相液相区"
            "（汽化率取 0，非求根结果）"
        )
        return FlashResult(
            phase=PHASE_LIQUID,
            vapor_fraction=0.0,
            liquid_composition=list(z),
            vapor_composition=None,
            k_values=k_bubble,
            bubble_sum=bubble_sum,
            dew_sum=dew_sum,
            rr_residual=None,
            rr_iterations=None,
            reason=reason,
            activity_coefficients=gamma_z,
            nonideal_residual=None,
            nonideal_iterations=None,
        )

    # 2) P ≤ P_d(z) → 单相蒸汽
    if pressure <= p_dew:
        reason = (
            f"露点检验（非理想）：P = {pressure:.6g} ≤ P_d(z) = {p_dew:.6g}"
            "（P_d 由配平条件 x_i·γ_i·P_i^sat/z_i 两组分相等二分求得），"
            "工况落在露点以上的单相汽相区（汽化率取 1，非求根结果）"
        )
        return FlashResult(
            phase=PHASE_VAPOR,
            vapor_fraction=1.0,
            liquid_composition=None,
            vapor_composition=list(z),
            k_values=k_dew,
            bubble_sum=bubble_sum,
            dew_sum=dew_sum,
            rr_residual=None,
            rr_iterations=None,
            reason=reason,
            activity_coefficients=None,
            nonideal_residual=None,
            nonideal_iterations=dew_iterations,
        )

    # 3) 两相区：扫描 B(x1) − P 的变号区间并二分，按杠杆规则筛选平衡根
    n = settings.scan_intervals

    def bubble_minus_p(x1: float) -> float:
        return _bubble_pressure(model, [x1, 1.0 - x1], psat) - pressure

    grid = [i / n for i in range(n + 1)]
    values = [bubble_minus_p(x1) for x1 in grid]

    candidates: list[tuple[float, list[float], list[float], list[float], int]] = []
    total_iterations = dew_iterations
    for i in range(n):
        f_lo, f_hi = values[i], values[i + 1]
        if f_lo == 0.0:
            x1_root, iters = grid[i], 0
        elif f_lo * f_hi < 0.0:
            x1_root, _, iters = _bisect_signs(
                bubble_minus_p, grid[i], grid[i + 1],
                1 if f_lo > 0 else -1, 1 if f_hi > 0 else -1,
                x_tol=settings.x_tol, f_tol=settings.f_tol,
                max_iter=settings.max_iter,
            )
        else:
            continue
        total_iterations += iters
        if not (0.0 < x1_root < 1.0):
            continue  # 边界根对应纯组分，应已被泡/露点判定吸收
        x = [x1_root, 1.0 - x1_root]
        gamma = model.activity_coefficients(x)
        y = [x[j] * gamma[j] * psat[j] / pressure for j in range(2)]
        if abs(y[0] - x[0]) > 1e-14:
            v_frac = (z[0] - x[0]) / (y[0] - x[0])
        else:
            v_frac = (z[1] - x[1]) / (y[1] - x[1])
        if -1e-10 <= v_frac <= 1.0 + 1e-10:
            candidates.append((min(max(v_frac, 0.0), 1.0), x, y, gamma, iters))

    if not candidates:
        raise SolverNonConvergence(
            "两相区内未找到满足杠杆规则（V∈[0,1]）的平衡液相组成",
            {"pressure": pressure, "P_bubble": p_bubble, "P_dew": p_dew,
             "z": list(z), "A12": model.A12, "A21": model.A21},
        )
    # 共沸体系可能有两个交点：只有一个能让给定进料落在它的结线上；
    # 若数值上同时通过（理论上不会），取物料衡算残差最小者。
    v_frac, x, y, gamma, _ = min(
        candidates,
        key=lambda c: max(
            abs(z[i] - ((1.0 - c[0]) * c[1][i] + c[0] * c[2][i])) for i in range(2)
        ),
    )
    k = [y[j] / x[j] for j in range(2)]
    equilibrium_residual = max(
        abs(y[j] - x[j] * gamma[j] * psat[j] / pressure) for j in range(2)
    )
    return FlashResult(
        phase=PHASE_TWO_PHASE,
        vapor_fraction=v_frac,
        liquid_composition=x,
        vapor_composition=y,
        k_values=k,
        bubble_sum=bubble_sum,
        dew_sum=dew_sum,
        rr_residual=None,
        rr_iterations=None,
        reason=None,
        activity_coefficients=gamma,
        nonideal_residual=equilibrium_residual,
        nonideal_iterations=total_iterations,
    )
