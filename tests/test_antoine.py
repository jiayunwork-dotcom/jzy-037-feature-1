"""Antoine 蒸汽压与 K 值：数值锚点、组分下标一致性、非法输入。"""
from __future__ import annotations

import pytest

from app.antoine import k_values, saturation_pressure_antoine, saturation_pressures
from app.errors import (
    AntoineCoefficientMissing,
    AntoineDenominatorZero,
    InvalidSaturationPressure,
)
from app.schemas import AntoineCoefficients, ComponentSpec, PropertyDefinitionCreate

PENTANE = AntoineCoefficients(A=13.8183, B=2477.07, C=-39.94)
HEXANE = AntoineCoefficients(A=13.8216, B=2697.55, C=-48.78)


def _prop(source: str, components: list[ComponentSpec]) -> PropertyDefinitionCreate:
    return PropertyDefinitionCreate(name="t", source=source, components=components)


def test_pentane_psat_25c():
    # 文献值：正戊烷 25 °C 饱和蒸汽压 ≈ 68.3 kPa
    assert saturation_pressure_antoine(PENTANE, 298.15) == pytest.approx(68.3, rel=0.01)


def test_hexane_psat_25c():
    # 文献值：正己烷 25 °C 饱和蒸汽压 ≈ 20.2 kPa
    assert saturation_pressure_antoine(HEXANE, 298.15) == pytest.approx(20.2, rel=0.01)


def test_pentane_normal_boiling_point():
    # 正戊烷正常沸点 36.07 °C，该处 P_sat ≈ 101.325 kPa
    assert saturation_pressure_antoine(PENTANE, 309.22) == pytest.approx(101.325, rel=0.01)


def test_antoine_denominator_zero():
    with pytest.raises(AntoineDenominatorZero):
        saturation_pressure_antoine(PENTANE, 39.94)  # T + C = 0


def test_k_values_follow_component_order():
    prop = _prop(
        "direct_psat",
        [ComponentSpec(name="light", psat=80.0), ComponentSpec(name="heavy", psat=20.0)],
    )
    assert k_values(prop, temperature=300.0, pressure=40.0) == [2.0, 0.5]
    # 调换组分顺序，K 顺序必须跟着变：不允许存在第二份独立维护的组分顺序
    swapped = _prop(
        "direct_psat",
        [ComponentSpec(name="heavy", psat=20.0), ComponentSpec(name="light", psat=80.0)],
    )
    assert k_values(swapped, temperature=300.0, pressure=40.0) == [0.5, 2.0]


def test_direct_psat_must_be_positive():
    prop = _prop(
        "direct_psat",
        [ComponentSpec(name="a", psat=0.0), ComponentSpec(name="b", psat=20.0)],
    )
    with pytest.raises(InvalidSaturationPressure):
        k_values(prop, 300.0, 40.0)


def test_missing_antoine_coefficients():
    prop = _prop(
        "antoine",
        [ComponentSpec(name="a"), ComponentSpec(name="b", antoine=HEXANE)],
    )
    with pytest.raises(AntoineCoefficientMissing):
        k_values(prop, 300.0, 40.0)


def test_saturation_pressures_and_k_values_share_source():
    prop = _prop(
        "antoine",
        [ComponentSpec(name="p", antoine=PENTANE), ComponentSpec(name="h", antoine=HEXANE)],
    )
    temperature, pressure = 298.15, 50.0
    psat = saturation_pressures(prop, temperature)
    ks = k_values(prop, temperature, pressure)
    assert ks == [psat[0] / pressure, psat[1] / pressure]
