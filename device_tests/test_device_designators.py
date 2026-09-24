"""Позиционные обозначения: разбор, виды, диапазоны."""

import pytest

from protocad.device import designators as d


def test_parse_accepts_designators_and_rejects_package_names():
    assert d.parse("R12").prefix == "R" and d.parse("R12").number == 12
    assert d.parse("С1").text == "C1"                  # русская «эс»
    assert d.parse("A1-DD12").group == "A1"
    assert d.parse(" vd 3 ").text == "VD3"
    for text in ("SOIC8", "LQFP64", "Болт:1", "плита:1", ""):
        assert d.parse(text) is None


def test_convention_decides_what_a_letter_means():
    assert d.guess_convention([d.parse(x) for x in ("R1", "DD1", "VD2")]) == d.GOST
    assert d.guess_convention([d.parse(x) for x in ("R1", "U1", "J2", "Q3")]) == d.WESTERN
    assert d.kind_name("D", d.GOST) == "Микросхемы"
    assert d.kind_name("D", d.WESTERN) == "Диоды"
    assert d.kind_name("VD") == "Диоды"


def test_ranges():
    assert d.parse_ranges("R1–R20, C1…C15; DD3  VD1 - 4") == [
        ("R", 1, 20), ("C", 1, 15), ("DD", 3, 3), ("VD", 1, 4)]
    assert d.in_ranges(d.parse("R7"), [("R", 1, 20)])
    assert not d.in_ranges(d.parse("R21"), [("R", 1, 20)])
    with pytest.raises(d.RangeError, match="разные буквы"):
        d.parse_ranges("R1-C5")
    with pytest.raises(d.RangeError, match="не понял"):
        d.parse_ranges("R1-R5, болт")


def test_compact():
    items = [d.parse(x) for x in "R1 R2 R3 R4 R7 C2 C1 DD1".split()]
    assert d.compact(items) == "C1, C2, DD1, R1–R4, R7"
