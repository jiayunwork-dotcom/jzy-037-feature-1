"""预置示范物性定义：正戊烷/正己烷二元体系（可手工核对，见 README「手算核对示例」）。

Antoine 自然对数形：ln(P_sat/kPa) = A - B / (T/K + C)
  n-pentane: A=13.8183, B=2477.07, C=-39.94
  n-hexane:  A=13.8216, B=2697.55, C=-48.78

锚点：298.15 K 时 P_sat(戊烷) ≈ 68.4 kPa、P_sat(己烷) ≈ 20.2 kPa；
等摩尔进料泡点压力 ≈ 44.3 kPa、露点压力 ≈ 31.2 kPa。
"""
from __future__ import annotations

from typing import Any

from .store import Store

DEMO_PROPERTY_DEFINITION_ID = "pd-demo-pentane-hexane"


def demo_property_definition_payload() -> dict[str, Any]:
    return {
        "name": "正戊烷/正己烷（理想体系示范）",
        "description": (
            "Antoine 自然对数形：ln(P_sat/kPa) = A - B / (T/K + C)。"
            "298.15 K 时 P_sat(戊烷)≈68.4 kPa、P_sat(己烷)≈20.2 kPa；"
            "等摩尔进料泡点压力≈44.3 kPa、露点压力≈31.2 kPa，便于手算核对。"
        ),
        "temperature_unit": "K",
        "pressure_unit": "kPa",
        "source": "antoine",
        "components": [
            {
                "name": "n-pentane",
                "antoine": {"A": 13.8183, "B": 2477.07, "C": -39.94, "form": "ln"},
                "psat": None,
            },
            {
                "name": "n-hexane",
                "antoine": {"A": 13.8216, "B": 2697.55, "C": -48.78, "form": "ln"},
                "psat": None,
            },
        ],
    }


def seed_demo_property_definition(store: Store) -> None:
    """幂等：已存在则不重复写入。"""
    if store.get_property_definition(DEMO_PROPERTY_DEFINITION_ID) is None:
        store.create_property_definition(
            demo_property_definition_payload(), record_id=DEMO_PROPERTY_DEFINITION_ID
        )


# ---------- 非理想示范：乙醇(1)/水(2)，van Laar ----------

DEMO_NONIDEAL_PROPERTY_DEFINITION_ID = "pd-demo-ethanol-water-vanlaar"


def demo_nonideal_property_definition_payload() -> dict[str, Any]:
    """乙醇/水：液相强非理想（氢键、正偏差、常压下有最低沸点共沸物）。

    Antoine（自然对数形 ln(P_sat/kPa) = A − B/(T/K + C)，由经典常数
    换算：乙醇 8.04494/1554.3/222.65、水 8.07131/1730.63/233.426，
    原形式为 log10(P_sat/mmHg) = a − b/(t/°C + c)）：
      ethanol: A=16.5092, B=3578.91, C=−50.500
      water:   A=16.5699, B=3984.92, C=−39.724
    校验：两式在各自正常沸点（乙醇 351.52 K、水 373.15 K）均给
    P_sat ≈ 101.3 kPa。
    van Laar 参数 A12=1.75、A21=0.91（组分顺序：乙醇→水），取自
    Smith, Van Ness & Abbott《Introduction to Chemical Engineering
    Thermodynamics》（7th ed., Table 12.4 同量级取值；Gmehling et al.,
    DECHEMA Chemistry Data Series Vol. I/1a, ethanol/water 1973 年
    VLE 回归值 A12≈1.75、A21≈0.91）。

    手核锚点（78.15 °C = 351.30 K，常压附近）：
      P_sat(乙醇) ≈ 100.6 kPa，P_sat(水) ≈ 43.8 kPa；
      该组参数预示共沸组成 x_乙醇 ≈ 0.92、共沸压力 ≈ 100.9 kPa
      （实验共沸 x_乙醇 ≈ 0.894，T≈78.2 °C，量级一致）。
    等摩尔进料在 85 kPa 下：理想假设 P_b^id≈72.2 kPa<85 判单相液体，
    计入活度后泡点压力 ≈ 94.2 kPa，实际处于两相区（V≈0.838，
    x_乙醇≈0.231、y_乙醇≈0.552）——方向与量级都可手算核对（见 README）。
    """
    return {
        "name": "乙醇/水（非理想体系示范，van Laar）",
        "description": (
            "液相 van Laar 活度系数模型：A12=1.75、A21=0.91（乙醇→水），"
            "参数取自 Smith/Van Ness/Abbott 与 DECHEMA 数据表的乙醇/水 VLE "
            "回归值；预示常压最低沸点共沸物 x_乙醇≈0.92（实验 0.894）。"
            "Antoine 为自然对数形，P_sat 单位 kPa、T 单位 K。"
        ),
        "temperature_unit": "K",
        "pressure_unit": "kPa",
        "source": "antoine",
        "liquid_model": "van_laar",
        "activity_model": {"model": "van_laar", "A12": 1.75, "A21": 0.91},
        "components": [
            {
                "name": "ethanol",
                "antoine": {"A": 16.50917323, "B": 3578.90801, "C": -50.5, "form": "ln"},
                "psat": None,
            },
            {
                "name": "water",
                "antoine": {"A": 16.5698924, "B": 3984.92284, "C": -39.724, "form": "ln"},
                "psat": None,
            },
        ],
    }


def seed_demo_nonideal_property_definition(store: Store) -> None:
    """幂等：已存在则不重复写入。"""
    if store.get_property_definition(DEMO_NONIDEAL_PROPERTY_DEFINITION_ID) is None:
        store.create_property_definition(
            demo_nonideal_property_definition_payload(),
            record_id=DEMO_NONIDEAL_PROPERTY_DEFINITION_ID,
        )
