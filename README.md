# 二元等温闪蒸核算服务

二元体系等温闪蒸（VLE）核算：给定温度、总压与进料组成，返回汽化率 V、
平衡液/汽相组成 x、y 与平衡常数 K。液相支持两种模型：

- **理想溶液（默认，历史行为）**：K_i = P_i^sat(T) / P，Rachford-Rice
  方程在 (0,1) 上二分求根；
- **van Laar 非理想溶液（新增）**：K_i = γ_i(x)·P_i^sat(T) / P，
  γ 由二元双参数 van Laar 模型按当前液相组成求值。

汽相统一按理想气体处理。范围限定二元体系；不做三组分、不引入
NRTL/UNIQUAC 完整形式与立方型状态方程。

## 一键启动

```bash
docker compose up --build
# http://localhost:8000/docs  （OpenAPI / Swagger）
# http://localhost:8000/healthz
```

数据落在命名卷 `flash-data`（SQLite，WAL 模式）。本地开发：

```bash
pip install -r requirements-dev.txt
pytest                       # 全量自动化测试
uvicorn app.main:app --reload
```

预置两个示范登记项（幂等种子）：

| id | 物系 | 液相模型 |
| --- | --- | --- |
| `pd-demo-pentane-hexane` | 正戊烷/正己烷 | ideal |
| `pd-demo-ethanol-water-vanlaar` | 乙醇/水 | van Laar（A12=1.75，A21=0.91） |

## van Laar 活度系数模型

组分顺序与 `components` 列表一致（组分 1 = 乙醇、组分 2 = 水）：

```
ln γ1 = A12 / (1 + A12·x1 / (A21·x2))²
ln γ2 = A21 / (1 + A21·x2 / (A12·x1))²
```

实现采用严格等价、在纯组分端点（x=0/1）不产生 0/0 的稳定形式（令
u=A12·x1、v=A21·x2、s=u+v：ln γ1 = A12·(v/s)²、ln γ2 = A21·(u/s)²）。

- `A12 = ln γ1∞`、`A21 = ln γ2∞`；γ 在端点回到 1。
- 两个参数必须是**正的有限数**。取零即理想体系——这种情况直接登记为
  理想定义即可，不允许用 van Laar 伪装；负值/非有限数拒绝
  （`ACTIVITY_MODEL_PARAMETER_INVALID`，不做静默缺省补全）。
- 求值时若组成超出闭单纯形（x_i∉[0,1] 或 Σx≠1）拒绝
  （`ACTIVITY_MODEL_INPUT_INVALID`）；若算出的 γ 非有限/非正拒绝
  （`ACTIVITY_COEFFICIENT_INVALID`），绝不把 NaN/负 K 送进求根。
- 适用域：完全互溶、无液液分相的二元体系。

## 非理想路径的求解（推导要点）

K 依赖 x 后，旧流程的前提「K 只依赖 T、P」不再成立，因此单相判据与
求根流程都重新推导（**不是把旧不等式直接套过来**）：

1. **泡点（闭式）**：P_b(z) = Σ z_i·γ_i(z)·P_i^sat。P ≥ P_b → 单相液体。
2. **露点（一元二分）**：第一滴液相 x* 满足 y=z，即两组分给出同一压力
   `x1·γ1·P1sat/z1 = x2·γ2·P2sat/z2`，配平函数
   H(x1)=x1γ1P1sat/z1 − x2γ2P2sat/z2，H(0+)<0、H(1−)>0，端点天然异号，
   二分求全部根。强正偏差/共沸体系下 H 可有三个根（露点曲线折曲，
   两侧是不稳定根，P_d 反高于泡点压力），物理稳定根是从低压加压时最先
   接触两相区的那个——各根中 **P_d 最小**者（与进料同在共沸侧，且必
   满足 P_d ≤ P_b）。P ≤ P_d → 单相蒸汽。
3. **两相区**：以液相组成直接参数化——每个 x 唯一对应泡点压力
   B(x1)=Σx_iγ_iP_i^sat，两相平衡即 B(x1)=P（在 [0,1] 上稠密符号扫描 +
   **二分**，共沸体系可有两个交点），平衡汽相 y_i=x_iγ_iP_i^sat/P，
   再由杠杆规则 V=(z1−x1)/(y1−x1) 反求汽化率；只接受 x1∈(0,1) 且
   V∈[0,1] 的根，找不到就抛 `SOLVER_NON_CONVERGENCE`，不返回近似解。

所有求解均为无状态纯函数（二分/黄金分割类一维保收敛方法，不用牛顿迭代），
不同作业的中间量互不影响。容差与扫描密度可用环境变量调整
（`FLASH_NONIDEAL_X_TOLERANCE`、`FLASH_NONIDEAL_F_TOLERANCE`、
`FLASH_NONIDEAL_SCAN_INTERVALS`）。

## 乙醇/水手核示例（非理想示范）

T = 351.30 K（78.15 °C），P = 85 kPa，等摩尔进料 z=(0.5, 0.5)。
Antoine（ln P_sat/kPa = A − B/(T+C)，由经典 log10/mmHg/°C 常数
8.04494/1554.3/222.65（乙醇）、8.07131/1730.63/233.426（水）换算）：

- 乙醇 A=16.5092, B=3578.91, C=−50.500 → P1sat ≈ 100.6 kPa
- 水   A=16.5699, B=3984.92, C=−39.724 → P2sat ≈ 43.8 kPa

两式在各自正常沸点（乙醇 351.52 K、水 373.15 K）均给 P_sat ≈ 101.3 kPa。

van Laar 参数 A12=1.75、A21=0.91（**来源**：Smith, Van Ness & Abbott,
*Introduction to Chemical Engineering Thermodynamics*, 7th ed., Table 12.4
同量级取值；Gmehling et al., DECHEMA Chemistry Data Series Vol. I/1a
ethanol/water 的 1 atm VLE 回归值 A12≈1.75、A21≈0.91。该组参数预示
常压最低沸点共沸物 x_乙醇≈0.92、P≈100.9 kPa，实验值 0.894、78.2 °C，
量级一致）。

手算（x=z=(0.5,0.5) 处）：

```
ln γ1 = 1.75/(1+1.75/0.91)² ≈ 0.2046 → γ1 ≈ 1.227
ln γ2 = 0.91/(1+0.91/1.75)² ≈ 0.3941 → γ2 ≈ 1.483
P_b   = 0.5·1.227·100.6 + 0.5·1.483·43.8 ≈ 94.2 kPa
```

P_b ≈ 94.2 kPa > 85 kPa，故**不是**单相液体；而同样工况下理想假设
P_b^id = 0.5·100.6+0.5·43.8 ≈ 72.2 kPa < 85 kPa，会误判为单相液体。
服务求解结果（85 kPa，两相区，露点压力 P_d≈79.2 kPa）：

```
V  ≈ 0.8384
x  ≈ (0.2306, 0.7694)，γ ≈ (2.023, 1.129)
y  ≈ (0.5519, 0.4481)
衡算：(1−V)x + V·y ≈ (0.5, 0.5) ✓；Σx=Σy=1 ✓
```

即：计入非理想性后，活度把乙醇（尤其在贫乙醇液相处 γ1≈2.0）显著
「推进」汽相，汽化率远大于理想假设的 0，偏离方向合理且可手核量级。

## API 摘要

- `POST /api/v1/property-definitions`：登记物性定义。省略
  `liquid_model` 即历史行为（ideal）；van Laar 须同时给
  `activity_model: {"model":"van_laar","A12":..,"A21":..}`，
  两种 P_sat 来源（Antoine / direct_psat）都支持。
- `PATCH /api/v1/property-definitions/{id}`：把**已登记的理想定义**
  追加参数升级为 van Laar（只允许一次；再改参数报 409
  `PROPERTY_DEFINITION_UPGRADE_CONFLICT`，需要另一组参数请登记新定义）。
- `POST /api/v1/jobs`：提交作业（引用登记项或内联临时定义 + 工况点组），
  逐点求解、全部成功后同一事务落库；任一点非法整体拒绝、不留半截作业。
- `GET /api/v1/jobs/{id}` / `.../points/{i}`：取回整包或单点。

每个工况点结果带有可追溯字段：

- `liquid_model`：本次实际使用的液相模型（`ideal`/`van_laar`）；
- `activity_model`：非理想点的参数快照（`{model,A12,A21}`），理想点为 null；
- `activity_coefficients`：平衡液相组成处的 γ（单相蒸汽无平衡液相 → null）；
- `nonideal_residual` / `nonideal_iterations`：非理想路径的相平衡残差与
  二分迭代次数（理想点为 null；理想点继续用 `rr_residual/rr_iterations`）。

**历史可追溯性与不可变性**：作业落库时保存当次求解的完整物性快照；
登记项的升级只改登记项本身，不触碰任何历史作业——升级前完成的作业
取回时 `liquid_model` 仍是 `ideal`、结果逐项不变（有专门测试
`tests/test_upgrade.py` 与 `tests/test_migration.py` 盯这件事）。

## 测试

```bash
pytest -q
```

覆盖三块交付要求：

1. **与老代码兼容**（`tests/test_compatibility.py`）：同一份理想物性
   （Antoine 与 direct_psat）× 跨两相/单相的工况矩阵，API 结果与历史
   纯函数内核逐字段比对；另钉死正戊烷/正己烷黄金值防数值漂移。
2. **非理想求解正确性**（`tests/test_activity.py`、
   `tests/test_nonideal_flash.py`、`tests/test_nonideal_api.py`）：
   闭式活度锚点、衡算闭合、Σx=Σy=1、重新推导的单相判定、泡/露点趋近、
   压力单调性、共沸区结线选择、非法参数/组成拒绝、乙醇/水手核锚点。
3. **升级不影响历史结果**（`tests/test_upgrade.py`、
   `tests/test_migration.py`）：升级前后作业快照逐字节比对、双重升级
   冲突、旧库启动迁移。另有并发隔离测试
   `tests/test_nonideal_concurrency.py`。
