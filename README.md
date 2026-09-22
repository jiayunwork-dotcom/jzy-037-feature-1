# 二元等温闪蒸核算服务

二元体系（两个组分）的等温闪蒸（T、P、进料组成 z 已知，求汽化率 V 与液/汽相组成 x、y）核算服务。
FastAPI + 内嵌 SQLite，容器一键拉起；以「核算作业」为核心，作业落库时保存**当次求解用的完整物性快照**。

液相支持两类物性定义，共用同一套求解流程：

- **理想溶液**（默认，行为与 0.1.x 完全一致）：`K_i = P_i^sat(T) / P`；
- **非理想液相**（0.2.0 新增，双参数三后缀 Margules 活度系数模型）：`K_i = γ_i(x)·P_i^sat(T) / P`。

汽相均按理想气体处理。范围锁定二元体系；不做三组分及以上，不引入立方型状态方程与 NRTL/UNIQUAC 完整关联式。

---

## 1. 一键启动

```bash
docker compose up --build
# API： http://localhost:8000
# 文档：http://localhost:8000/docs
# 健康检查：http://localhost:8000/healthz
```

数据落在命名卷 `flash-data`（容器内 `/data/flash.db`，WAL 模式）。服务启动时幂等预置两个示范登记项：

| id | 体系 | 液相模型 |
|---|---|---|
| `pd-demo-pentane-hexane` | 正戊烷/正己烷（Antoine） | 理想 |
| `pd-demo-ethanol-water-margules` | 乙醇/水（Antoine + Margules） | 非理想 |

本地直接运行：`pip install -r requirements.txt` 后 `uvicorn app.main:app`。

---

## 2. 非理想液相模型

### 2.1 三后缀 Margules（双参数）

组分顺序与物性定义 `components` 列表一致（组分 1、组分 2），记 `x2 = 1 − x1`：

```
ln γ1 = x2² [ a12 + 2 (a21 − a12) x1 ]
ln γ2 = x1² [ a21 + 2 (a12 − a21) x2 ]
```

- `a12`、`a21` 为一对无量纲可调常数，只依赖组成，不依赖 T/P（在登记温度附近拟合）；
- 端点极限可手工核对：`x1→0 ⇒ ln γ1 → a12`（γ1 的无限稀释值 = e^a12）、`ln γ2 → 0`；`x1→1 ⇒ ln γ2 → a21`；
- `a12 = a21 = 0 ⇒ γ ≡ 1`，严格退化为理想溶液（自动化测试断言该情况下两条路径算出的汽化率逐位相同）；
- 公式天然满足二元 Gibbs-Duhem 一致性，内部以单自由度 x1 入参（x2 由 1−x1 推出）。

### 2.2 参数在物性定义中的表达与缺省处理

物性定义新增可选字段 `activity_model`：

- 缺省（`null`/不带该字段）→ **理想定义**，`K = P^sat/P`，旧调用方一行都不用改；
- 给值 → **非理想定义**：`{"activity_model": {"model": "margules", "a12": 1.6798, "a21": 0.9227}}`；
- `source`（Antoine 系数 / 直接给定 P_sat）与液相模型正交，四种组合都支持。

### 2.3 非法参数/输入的拒绝规则（全部 422 + 带类型错误，不返回近似解）

| 情况 | 错误类型 |
|---|---|
| 非理想定义缺 `a12` 或 `a21` | `ACTIVITY_MODEL_PARAMETER_MISSING` |
| 参数非有限（NaN/Inf） | `ACTIVITY_MODEL_PARAMETER_INVALID` |
| 参数组在 [0,1] 任意组成下算不出正有限 γ（如 e^800 溢出/下溢到 0） | `ACTIVITY_MODEL_PARAMETER_INVALID`（登记/升级时全组成域预扫即拒绝） |
| 求解中液相组成越出模型适用域 | `ACTIVITY_COMPOSITION_OUT_OF_DOMAIN` |
| 求解中合法组成处 γ 非正/非有限（防御性，正常参数到不了） | `ACTIVITY_COEFFICIENT_INVALID` |
| K 逐次代入达到迭代上限仍未收敛 | `NONIDEAL_FLASH_NON_CONVERGENCE` |

---

## 3. K 依赖组成后，求解流程做了什么改动

原流程全部建立在「K 只依赖 T、P」的前提上。引入 γ(x) 后三处前提重新推导/加固（实现见 `app/nonideal_flash.py`）：

**单相判定（重新推导，不是照搬）。** 联立 `y_i = K_i(x) x_i` 与物料衡算 `z_i = (1−V)x_i + V y_i`，得 `x_i = z_i/(1+V(K_i−1))`：

- 泡点侧 V→0：液相组成就是 z，故 K 必须取 **x = z** 处的活度系数：`Σ z_i γ_i(z) P_i^sat/P ≤ 1 ⇒ 单相液体`；
- 露点侧 V→1：汽相组成就是 z，与之平衡的微量液相隐式满足 `x_i = z_i/K_i(x)`（x ≠ z），在该露点组成处 `Σ z_i/K_i(x) ≤ 1 ⇒ 单相蒸汽`。

判据的不等式形状与理想情形相同，但每步的 K 都在**正确的组成**处取值。

**求根单调性。** 冻结任意一轮的 K 后，Rachford-Rice 函数对 V 仍严格单调递减（导数只含 z 与当前 K），所以**内层不另起求根器**，逐字复用 `app/flash.py` 那套二分法（`app/nonideal_flash.py` 直接调用 `isothermal_flash`）；非单调性只可能来自 K 随组成的更新。

**外层求解策略。** K 逐次代入（successive substitution）+ 阻尼 Wegstein 加速：

1. 以理想 K⁰ = P^sat/P 起步；
2. 冻结 K → 重新推导过的单相判定或 RR 二分求 V → 得当前液相组成 x；
3. 由 x 算 γ(x)，组装 K* = γ(x)·P^sat/P；
4. 在 ln K 空间更新：前两轮普通代入（单步步长有界 `|Δln K| ≤ 1`，抑制初轮大步长振荡）；
   第三轮起对每个组分独立做阻尼 Wegstein 割线外推（权重 q 截断在 [−1,1]，消除近相界的
   周期-2 弹跳——这是普通逐次代入在露点边界附近的典型失速模式），步长同样有界、
   割线奇点保护回退普通代入；
5. `max_i |ln(K*_i/K_i)| ≤ 1e-10` 收敛；200 轮不收敛即抛错。

随机稳健性扫荡（1 万组随机 P_sat 比/进料/压力/Margules 参数）全部在 200 轮内收敛，
返回前独立自认证：`0<V<1`、`|Σx−1|`、`|Σy−1|`、`K_i = γ_i P_i^sat/P`、物料衡算，
任一不过按不收敛拒绝。求解器是无状态纯函数，并发作业互不影响。

---

## 4. 非理想手算示例（乙醇/水，343.15 K，50 kPa）

种子定义 `pd-demo-ethanol-water-margules`，组分 1 = 乙醇：

- Antoine（ln 形，kPa/K）：乙醇 A=16.68631, B=3681.081, C=−46.424；水 A=16.3872, B=3885.70, C=−42.98；
- 343.15 K：`P_sat(乙醇) = 72.288 kPa`、`P_sat(水) = 31.256 kPa`（水在正常沸点 373.15 K 复算 ≈ 101.3 kPa，可校验）；
- Margules：`a12 = 1.6798, a21 = 0.9227`（参数来源见第 7 节）。

进料 `z = (0.3, 0.7)`，P = 50 kPa：

**理想假设**：`Σ z_i P_i^sat/P = 0.3×72.288/50 + 0.7×31.256/50 = 0.8713 ≤ 1` → 判为单相液体，V = 0。

**非理想模型**：泡点检验的 K 取进料组成 x = z = (0.3, 0.7)：

```
ln γ1 = 0.7² × [1.6798 + 2×(0.9227−1.6798)×0.3] = 0.60051 → γ1 = 1.8231
ln γ2 = 0.3² × [0.9227 + 2×(1.6798−0.9227)×0.7] = 0.17844 → γ2 = 1.1953
Σ z_i γ_i P_i^sat/P = 0.3×1.8231×72.288/50 + 0.7×1.1953×31.256/50 = 1.3138 > 1
```

→ 进入两相区。服务求得（26 轮外层迭代、内层 RR 二分 38 次）：

```
V = 0.6634
x = (0.0750, 0.9250)   （液相以水为主）
y = (0.4142, 0.5858)   （汽相乙醇富集）
γ(x) = (3.8194, 1.0132)，K = (5.5219, 0.6333)
```

可逐项手核：`K1 = 3.8194×72.288/50 = 5.522`；`y1 = K1 x1 = 0.4142`；杠杆规则
`V = (z1−x1)/(y1−x1) = (0.3−0.0750)/(0.4142−0.0750) = 0.6634`；`Σx = Σy = 1`；
`(1−V)x + V·y = z` 闭合。

**偏离方向**：乙醇/水是强正偏差体系（无限稀释 γ₁^∞ = e^1.6798 ≈ 5.36），活度系数推高了易挥发组分的逃逸倾向——同一工况理想假设判全液，真实体系却有 66% 汽化，方向与物理直觉一致，没有退化回理想解。

复现：

```bash
curl -s http://localhost:8000/api/v1/jobs -H 'content-type: application/json' -d '{
  "property_definition_id": "pd-demo-ethanol-water-margules",
  "points": [{"temperature": 343.15, "pressure": 50.0, "feed": [0.3, 0.7]}]}'
```

---

## 5. HTTP 接口

基础前缀 `/api/v1`。

### 5.1 物性定义

```bash
# 登记理想定义（activity_model 缺省）
POST /property-definitions
{"name": "eth-water-ideal", "source": "antoine",
 "components": [
   {"name": "ethanol", "antoine": {"A": 16.68631, "B": 3681.081, "C": -46.424}},
   {"name": "water",   "antoine": {"A": 16.3872,  "B": 3885.70,  "C": -42.98}}]}

# 登记时直接给非理想参数
POST /property-definitions
{..., "activity_model": {"model": "margules", "a12": 1.6798, "a21": 0.9227}}

# 把已登记的理想定义升级为非理想（只动登记项，不碰历史作业）
POST /property-definitions/{id}/activity-model
{"activity_model": {"model": "margules", "a12": 1.6798, "a21": 0.9227}}
# 404 不存在；409 已经是非理想定义（不允许覆盖/降级）；422 参数非法

GET  /property-definitions            # 列出全部
GET  /property-definitions/{id}       # 取回（含 activity_model，区分理想/非理想）
```

`source = "direct_psat"` 时每个组分直接给 `psat`（与定义的 pressure_unit 同单位），温度不参与 K 计算但仍校验。

### 5.2 核算作业

```bash
POST /jobs
{"property_definition_id": "pd-demo-ethanol-water-margules",
 "points": [{"temperature": 343.15, "pressure": 50.0, "feed": [0.3, 0.7]}]}
# 也可二选一给内联临时定义 "property_definition": {...}（不进入登记列表）

GET /jobs?limit=100&offset=0
GET /jobs/{id}                 # 完整结果 + 当次物性快照（可追溯模型与参数）
GET /jobs/{id}/points/{index}  # 单个工况点（index 从 0 起）
```

工况点结果新增字段（理想点取安全默认值，不影响旧字段）：

| 字段 | 含义 |
|---|---|
| `liquid_model` | `"ideal"` / `"margules"`，该点快照所用液相模型 |
| `activity_coefficients` | 最终液相组成处的 γ（单相汽相点取隐式露点液相组成处）；理想为 `null` |
| `nonideal_k_residual` / `nonideal_iterations` | 外层 K 迭代残差与轮数；理想为 `null` |

历史可追溯性：`GET /jobs/{id}` 返回的 `property_definition.activity_model` 即该作业当时用的模型；非理想时完整携带 `a12/a21`；每个点的 `liquid_model` 直接标明路径。

### 5.3 升级与历史结果的关系

作业落库存的是**当次完整物性快照**（`jobs.property_snapshot_json`），升级登记项只更新登记项本身：

- 升级**前**的作业：快照 `activity_model = null`、各点 `liquid_model = "ideal"`、数值永不变（有专项测试逐字节比对）；
- 升级**后**的新作业：快照携带 Margules 参数、走非理想路径；
- 非理想定义不允许再次升级（409），需要别的参数请新建登记项。

---

## 6. 自动化测试

```bash
pip install -r requirements-dev.txt
pytest -q
```

96 个测试，按需求分三块：

1. **与老代码的兼容性**（`test_ideal_compat.py`）：`tests/fixtures/ideal_golden.json`
   是用**改动前代码**生成的黄金基准（21 步工作流：Antoine/direct_psat × 登记/引用/内联 ×
   两相/单相/错误响应）；新代码原样回放，基准中的每个字段逐项**严格相等**（浮点不用 approx）。
   另有旧版 SQLite 结构（无 `activity_model_json` 列）的迁移测试（`test_legacy_migration.py`）：
   自动加列、旧数据可读且数值不变、旧登记项可升级。
2. **非理想求解正确性**（`test_nonideal_flash.py`、`test_nonideal_api.py`）：
   Margules 手算锚点与无限稀释极限、衡算闭合、组成加和、`K = γ P^sat/P`、
   单相两侧判定、压力单调性、参数/组成非法拒绝、零参数退化与理想路径逐位一致、
   乙醇/水示范点方向合理、不收敛拒绝；以及并发 8 个理想/非理想混合作业互不覆盖。
3. **登记项升级不影响历史结果**（`test_nonideal_api.py::TestUpgradePreservesHistory`）：
   升级前后同一作业逐字节不变、新作业走非理想路径、404/409/422 各分支。

> 重新生成黄金基准（仅在你确认要变更理想路径行为时）：
> `PYTHONPATH=. python3 tests/generate_ideal_golden.py`（须在变更前的代码上运行；
> 交付的 fixture 已由非理想特性合入前的代码生成，日常不需要重跑）。

---

## 7. 参数来源与取值依据

- **Antoine（乙醇）**：NIST Chemistry WebBook 常用温段（约 292.8–366.6 K）的常用系数
  `log10(P/bar) = 5.24677 − 1598.673/(T/K − 46.424)`，换算为本服务的 ln/kPa 形式：
  `A = 5.24677·ln10 + ln100 = 16.68631`，`B = 1598.673·ln10 = 3681.081`，`C = −46.424`。
  校验：351.30 K（正常沸点 78.15 ℃）复算 P_sat ≈ 100.7 kPa。
- **Antoine（水）**：Yaws, *Handbook of Antoine Equations for Vapor Pressure of Pure
  Components*（ln 形，kPa/K，273–643 K）：A=16.3872, B=3885.70, C=−42.98。
  校验：373.15 K → 101.3 kPa。
- **Margules a12/a21**：乙醇(1)/水(2) 在 70 ℃（343.15 K）的等压 VLE 数据回归值
  （Smith, Van Ness & Abbott, *Introduction to Chemical Engineering Thermodynamics*,
  7th ed., Table 10.2 乙醇/水条目 Margules 列：A12 = 1.6798, A21 = 0.9227）。
  无限稀释校验：e^1.6798 = 5.36、e^0.9227 = 2.52，与同表 van Laar 列（1.7550/0.9171）
  及该体系文献无限稀释活度系数量级一致。仅适用于单一液相、拟合温度附近的核算。

## 8. 配置项（环境变量前缀 `FLASH_`）

| 变量 | 默认 | 说明 |
|---|---|---|
| `FLASH_DB_PATH` | `./data/flash.db` | SQLite 文件 |
| `FLASH_FEED_SUM_TOLERANCE` | 1e-6 | 进料加和容差 |
| `FLASH_RR_FUNCTION_TOLERANCE` | 1e-12 | RR 二分 \|f(V)\| 容差 |
| `FLASH_RR_BRACKET_TOLERANCE` | 1e-14 | RR 二分 bracket 宽度容差 |
| `FLASH_RR_MAX_ITERATIONS` | 200 | RR 二分上限 |
| `FLASH_NONIDEAL_K_TOLERANCE` | 1e-10 | 非理想外层 max\|Δln K\| 容差 |
| `FLASH_NONIDEAL_LN_K_STEP` | 1.0 | ln K 单步步长上限 |
| `FLASH_NONIDEAL_MAX_ITERATIONS` | 200 | 非理想外层迭代上限 |
