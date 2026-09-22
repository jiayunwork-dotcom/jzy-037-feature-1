"""非理想液相的二元等温闪蒸：K_i = γ_i(x)·P_i^sat(T) / P。

为什么不能直接套用理想内核
--------------------------
``app/flash.py`` 的整套流程建立在「K 只依赖 T、P，与组成无关」之上。
引入 γ(x) 后 K 依赖液相组成，有三处前提必须重新处理（不是照搬）：

1. 单相判定。平衡关系 y_i = K_i(x)·x_i 与物料衡算
   z_i = (1−V)x_i + V·y_i 联立消去 y 得 x_i = z_i/(1+V(K_i−1))。
   - 泡点侧（V→0）：液相组成就是 z，逸出的微量汽相 y_i = K_i(z)·z_i，
     全液自洽 ⟺ bubble_sum = Σ z_i·K_i(z) ≤ 1。
     ——K 必须取 x = z 处的值，不能取别的组成。
   - 露点侧（V→1）：汽相组成就是 z，与之平衡的微量液相 x 隐式满足
     x_i = z_i/K_i(x)；全汽自洽 ⟺ 该组成处 dew_sum = Σ z_i/K_i(x) ≤ 1。
     ——x 不再等于 z，需求解隐式露点组成。
   判据的不等式形状与理想情形相同，但每一步的 K 都在正确的组成处取值。

2. 求根单调性。冻结某一轮的 K 后，f(V) = Σ z_i(K_i−1)/(1+V(K_i−1))
   对 V 仍严格单调递减（导数只含 z_i 与当前 K_i，逐字沿用旧证明），
   所以内层不另写求根器，直接调用 ``app/flash.isothermal_flash`` 的
   单相判定与 ``solve_vapor_fraction`` 二分法；非单调性只可能来自外层
   K 随组成的更新，由下面的外层迭代负责。

3. 求解策略。采用 **K 逐次代入（successive substitution）+ Wegstein 加速**：
   每轮冻结 K → 做（重新推导过的）单相判定或二分求 V → 由当前液相
   组成 x 算 γ(x) → 组装新的 K* = γ(x)·P^sat/P。更新在 ln K 空间进行：
   前两轮走普通代入（单步步长有界 |Δln K| ≤ ln_k_step，默认 1，即单轮
   变化不超过 e 倍，抑制强非理想体系初轮大步长振荡）；从第三轮起对
   每个组分独立做**阻尼 Wegstein 割线外推**（固定点迭代的标准加速，
   近相界的周期-2 弹跳正是它的靶子）：外推权重 q 截断在 [−1, 1] 内
   防过冲、步长统一受 ln_k_step 约束、s≈1 的割线奇点保护回退普通
   代入。收敛判据
   max_i |ln(K*_i/K_i)| ≤ k_tolerance（默认 1e-10）；迭代上限
   max_iter（默认 200）触发即抛 NonIdealFlashNonConvergence，
   绝不返回未收敛的近似解。

自认证
------
收敛返回前再独立核验一遍硬性关系（任一不过即按不收敛拒绝）：
    0 < V < 1、|Σx−1|、|Σy−1| 容差内为零、K_i 与 γ(x)·P^sat/P 一致。
衡算关系 z = (1−V)x + V·y 是 x、V 构造方式的恒等式（见 service 层残差）。

无状态
------
本模块全是纯函数：模型参数与组成全部从参数传入，不持有任何跨调用
状态，并发求解各自独立。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from . import activity
from .errors import NonIdealFlashNonConvergence
from .flash import (
    PHASE_LIQUID,
    PHASE_TWO_PHASE,
    PHASE_VAPOR,
    isothermal_flash,
)
from .schemas import ActivityModelSpec

# 收敛后硬性关系的认证容差（比 k_tol 略宽，吸收浮点噪声）
_CERTIFY_TOLERANCE = 1e-8


@dataclass
class NonIdealFlashResult:
    phase: str
    vapor_fraction: float
    liquid_composition: list[float] | None
    vapor_composition: list[float] | None
    k_values: list[float]
    activity_coefficients: list[float]  # 最终液相组成处的 γ
    bubble_sum: float
    dew_sum: float
    nonideal_k_residual: float
    nonideal_iterations: int
    rr_residual: float | None
    rr_iterations: int | None
    reason: str | None


def _frozen_k_state(
    z: list[float],
    k: list[float],
    *,
    f_tol: float,
    x_tol: float,
    rr_max_iter: int,
):
    """冻结 K 下的一次完整闪蒸：直接复用理想内核的判定与二分。

    返回 (phase, V, x, y, bubble_sum, dew_sum, rr_residual, rr_iterations)。
    蒸汽侧的 x 由露点关系 x_i = z_i/K_i 现算（理想内核在该侧不返回液相）。
    """
    frozen = isothermal_flash(
        z, k, f_tol=f_tol, x_tol=x_tol, max_iter=rr_max_iter
    )
    if frozen.phase == PHASE_LIQUID:
        x = list(z)
        y = [ki * xi for ki, xi in zip(k, x)]
        return (
            PHASE_LIQUID,
            0.0,
            x,
            y,
            frozen.bubble_sum,
            frozen.dew_sum,
            None,
            None,
        )
    if frozen.phase == PHASE_VAPOR:
        y = list(z)
        x = [zi / ki for zi, ki in zip(z, k)]  # 隐式露点液相组成
        return (
            PHASE_VAPOR,
            1.0,
            x,
            y,
            frozen.bubble_sum,
            frozen.dew_sum,
            None,
            None,
        )
    assert frozen.liquid_composition is not None
    assert frozen.vapor_composition is not None
    return (
        PHASE_TWO_PHASE,
        frozen.vapor_fraction,
        list(frozen.liquid_composition),
        list(frozen.vapor_composition),
        frozen.bubble_sum,
        frozen.dew_sum,
        frozen.rr_residual,
        frozen.rr_iterations,
    )


def _next_ln_k(
    ln_k: list[float],
    ln_k_star: list[float],
    prev_ln_k: list[float] | None,
    prev_ln_k_star: list[float] | None,
    step_max: float,
) -> list[float]:
    """下一轮 ln K：前两轮普通代入（有界），之后组分独立阻尼 Wegstein 外推。

    记不动点迭代映射 g：ln K^{n+1} = g(ln K^n)，割线斜率
        s = (g^n − g^{n−1}) / (ln K^n − ln K^{n−1})，
    Wegstein 外推 ln K^{n+1} = q·ln K^n + (1−q)·g^n，q = s/(s−1)
    截断到 [−1, 1]；s≈1（割线近平行）时退化为普通代入。
    步长统一截断到 ±step_max。
    """
    if prev_ln_k is None or prev_ln_k_star is None:
        return [
            cur + max(-step_max, min(step_max, star - cur))
            for cur, star in zip(ln_k, ln_k_star)
        ]
    next_values: list[float] = []
    for cur, star, prev_cur, prev_star in zip(
        ln_k, ln_k_star, prev_ln_k, prev_ln_k_star
    ):
        denominator = cur - prev_cur
        if abs(denominator) < 1e-12:
            extrapolated = star  # 本轮没动：直接采用映射值
        else:
            s = (star - prev_star) / denominator
            if abs(1.0 - s) < 1e-6:
                extrapolated = star  # 割线奇点保护：普通代入
            else:
                q = s / (s - 1.0)
                # 阻尼：q 截断到 [−1, 1]，避免相界附近外推过冲
                q = max(-1.0, min(1.0, q))
                extrapolated = q * cur + (1.0 - q) * star
        next_values.append(cur + max(-step_max, min(step_max, extrapolated - cur)))
    return next_values


def isothermal_flash_nonideal(
    z: list[float],
    psat: list[float],
    pressure: float,
    model: ActivityModelSpec,
    *,
    k_tol: float,
    ln_k_step: float,
    max_iter: int,
    f_tol: float,
    x_tol: float,
    rr_max_iter: int,
) -> NonIdealFlashResult:
    """组成依赖 K 的二元等温闪蒸。z/psat 同序，调用方负责正数与加和校验。"""
    activity.validate_activity_model_spec(model)
    assert model.a12 is not None and model.a21 is not None
    a12, a21 = model.a12, model.a21

    # 内部统一用归一化组成，保证 x1 恒在模型适用域内、Σx = 1 到机器精度
    feed_total = math.fsum(z)
    zn = [zi / feed_total for zi in z]

    k = [p / pressure for p in psat]  # 理想猜测起步（γ=1）
    last: dict | None = None
    prev_ln_k: list[float] | None = None
    prev_ln_k_star: list[float] | None = None

    for iteration in range(1, max_iter + 1):
        phase, v, x, y, bubble_sum, dew_sum, rr_res, rr_iter = _frozen_k_state(
            zn,
            k,
            f_tol=f_tol,
            x_tol=x_tol,
            rr_max_iter=rr_max_iter,
        )
        g1, g2 = activity.activity_coefficients(model, x[0])
        k_star = [g1 * psat[0] / pressure, g2 * psat[1] / pressure]
        ln_k = [math.log(ki) for ki in k]
        ln_k_star = [math.log(ki) for ki in k_star]
        ln_steps = [star - cur for star, cur in zip(ln_k_star, ln_k)]
        k_residual = max(abs(step) for step in ln_steps)
        last = {
            "phase": phase,
            "v": v,
            "x": x,
            "y": y,
            "k": list(k),
            "gamma": [g1, g2],
            "bubble": bubble_sum,
            "dew": dew_sum,
            "rr_residual": rr_res,
            "rr_iterations": rr_iter,
            "k_residual": k_residual,
        }
        if k_residual <= k_tol:
            break
        ln_k_next = _next_ln_k(
            ln_k, ln_k_star, prev_ln_k, prev_ln_k_star, ln_k_step
        )
        prev_ln_k, prev_ln_k_star = ln_k, ln_k_star
        k = [math.exp(value) for value in ln_k_next]
    else:
        raise NonIdealFlashNonConvergence(
            f"非理想闪蒸 K 逐次代入 {max_iter} 次迭代后仍未满足收敛容差"
            + (
                f"，max|Δln K| = {last['k_residual']:.3g} > {k_tol:.3g}"
                if last is not None
                else "（迭代预算为 0，一步都没跑）"
            ),
            {
                "max_iterations": max_iter,
                "k_tolerance": k_tol,
                "last_k_residual": last["k_residual"] if last is not None else None,
                "last_phase": last["phase"] if last is not None else None,
                "last_k": last["k"] if last is not None else None,
            },
        )

    iterations = iteration  # type: ignore[possibly-undefined]
    assert last is not None  # max_iter >= 1 且未抛非收敛：至少完成一轮
    phase = last["phase"]
    v = last["v"]
    x = last["x"]
    y = last["y"]
    k_final = last["k"]
    gammas = last["gamma"]

    if phase == PHASE_TWO_PHASE:
        _certify_two_phase(zn, x, y, v, k_final, gammas, psat, pressure)
        reason = None
        liquid_composition = x
        vapor_composition = y
    elif phase == PHASE_LIQUID:
        reason = (
            f"泡点检验：Σ z_i·γ_i(z)·P_i^sat/P = {last['bubble']:.6g} ≤ 1，"
            "工况落在泡点以下的单相液相区（汽化率取 0，非求根结果；"
            "K 取进料组成 x = z 处的活度系数）"
        )
        liquid_composition = list(zn)
        vapor_composition = None
    else:
        reason = (
            f"露点检验：Σ z_i/K_i = {last['dew']:.6g} ≤ 1，"
            "工况落在露点以上的单相汽相区（汽化率取 1，非求根结果；"
            "K 取隐式露点液相组成处的活度系数）"
        )
        liquid_composition = None
        vapor_composition = list(zn)

    return NonIdealFlashResult(
        phase=phase,
        vapor_fraction=v,
        liquid_composition=liquid_composition,
        vapor_composition=vapor_composition,
        k_values=k_final,
        activity_coefficients=gammas,
        bubble_sum=last["bubble"],
        dew_sum=last["dew"],
        nonideal_k_residual=last["k_residual"],
        nonideal_iterations=iterations,
        rr_residual=last["rr_residual"],
        rr_iterations=last["rr_iterations"],
        reason=reason,
    )


def _certify_two_phase(
    z: list[float],
    x: list[float],
    y: list[float],
    v: float,
    k: list[float],
    gammas: list[float],
    psat: list[float],
    pressure: float,
) -> None:
    """返回前独立认证两相解的硬性关系；不过即按不收敛拒绝。"""
    failures: dict[str, float] = {}
    if not (0.0 < v < 1.0):
        failures["vapor_fraction"] = v
    if abs(math.fsum(x) - 1.0) > _CERTIFY_TOLERANCE:
        failures["liquid_sum_error"] = abs(math.fsum(x) - 1.0)
    if abs(math.fsum(y) - 1.0) > _CERTIFY_TOLERANCE:
        failures["vapor_sum_error"] = abs(math.fsum(y) - 1.0)
    for i in range(2):
        equilibrium_error = abs(k[i] - gammas[i] * psat[i] / pressure) / k[i]
        if equilibrium_error > _CERTIFY_TOLERANCE:
            failures[f"k_equilibrium_error_{i}"] = equilibrium_error
        balance = (1.0 - v) * x[i] + v * y[i]
        if abs(balance - z[i]) > _CERTIFY_TOLERANCE:
            failures[f"material_balance_error_{i}"] = abs(balance - z[i])
    if failures:
        raise NonIdealFlashNonConvergence(
            "非理想闪蒸结果未通过收敛后一致性认证",
            failures,
        )
