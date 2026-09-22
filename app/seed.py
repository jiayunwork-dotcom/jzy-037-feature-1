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
