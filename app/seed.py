"""预置示范物性定义。

1) 正戊烷/正己烷（理想体系，手算锚点）：
   Antoine 自然对数形：ln(P_sat/kPa) = A - B / (T/K + C)
     n-pentane: A=13.8183, B=2477.07, C=-39.94
     n-hexane:  A=13.8216, B=2697.55, C=-48.78
   锚点：298.15 K 时 P_sat(戊烷) ≈ 68.4 kPa、P_sat(己烷) ≈ 20.2 kPa；
   等摩尔进料泡点压力 ≈ 44.3 kPa、露点压力 ≈ 31.2 kPa。

2) 乙醇/水（非理想体系，三后缀 Margules）：
   Antoine（ln 形，kPa/K）：
     ethanol: A=16.68631, B=3681.081, C=-46.424
       （由 NIST WebBook 常用段 log10(P/bar)=5.24677−1598.673/(T/K−46.424)
         换算为 ln/kPa：A×ln10+ln100、B×ln10；适用约 292.8–366.6 K）
     water:   A=16.3872,  B=3885.70,  C=-42.98
       （Yaws《Handbook of Antoine Equations》ln 形直接给出；273–643 K）
   Margules 参数（70 ℃ / 343.15 K 拟合值，来源见 README「参数来源」）：
     a12 = 1.6798, a21 = 0.9227（组分 1 = 乙醇）
   无限稀释活度系数：γ_乙醇^∞ = e^1.6798 ≈ 5.36，γ_水^∞ = e^0.9227 ≈ 2.52，
   量级与乙醇/水体系的文献关联式（van Laar 1.7550/0.9171、无限稀释实测）一致。
"""
from __future__ import annotations

from typing import Any

from .store import Store

DEMO_PROPERTY_DEFINITION_ID = "pd-demo-pentane-hexane"
DEMO_ETHANOL_WATER_ID = "pd-demo-ethanol-water-margules"


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


def ethanol_water_property_definition_payload() -> dict[str, Any]:
    return {
        "name": "乙醇/水（非理想体系示范，Margules @343.15 K）",
        "description": (
            "三后缀 Margules：ln γ1 = x2²[a12 + 2(a21−a12)x1]，"
            "a12=1.6798、a21=0.9227（组分1=乙醇，343.15 K 拟合值）。"
            "示范工况 343.15 K、50 kPa、z_乙醇=0.3：理想假设判为单相液相，"
            "非理想模型判为两相（正偏差促进汽化），见 README「非理想手算示例」。"
        ),
        "temperature_unit": "K",
        "pressure_unit": "kPa",
        "source": "antoine",
        "components": [
            {
                "name": "ethanol",
                "antoine": {"A": 16.68631, "B": 3681.081, "C": -46.424, "form": "ln"},
                "psat": None,
            },
            {
                "name": "water",
                "antoine": {"A": 16.3872, "B": 3885.70, "C": -42.98, "form": "ln"},
                "psat": None,
            },
        ],
        "activity_model": {"model": "margules", "a12": 1.6798, "a21": 0.9227},
    }


def seed_ethanol_water_property_definition(store: Store) -> None:
    """幂等：已存在则不重复写入。"""
    if store.get_property_definition(DEMO_ETHANOL_WATER_ID) is None:
        store.create_property_definition(
            ethanol_water_property_definition_payload(),
            record_id=DEMO_ETHANOL_WATER_ID,
        )


def seed_all(store: Store) -> None:
    seed_demo_property_definition(store)
    seed_ethanol_water_property_definition(store)
