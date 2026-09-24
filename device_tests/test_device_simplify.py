"""Упрощение прибора: отверстия, компоненты габаритом, исключение, отмена."""

import numpy as np
import pytest

from device_helpers import by_label, demo_device
from protocad.device import COMPONENT, EXCLUDED, FASTENER
from protocad.device import simplify
from protocad.device.contacts import CLASH, TOUCH, check
from protocad.prep import Study
from protocad.prep.defeature import find_holes


def _holes(unit, limit=10.0):
    study = Study()
    study.add_body(unit.item.shape, "деталь")
    return sorted(round(item.size, 3) for item in find_holes(study, limit))


def test_board_holes_up_to_diameter(tmp_path_factory):
    device = demo_device(tmp_path_factory)
    board = by_label(device, "Плата")
    assert _holes(board).count(3.2) == 4 and len(_holes(board)) == 48
    result = simplify.remove_holes(device, 1.2, roles=("substrate",))
    assert result.definitions == 1
    board = by_label(device, "Плата")
    assert _holes(board) == [3.2, 3.2, 3.2, 3.2]


def test_components_become_boxes_standing_on_the_board(tmp_path_factory):
    device = demo_device(tmp_path_factory, problems=True)
    before = simplify.snapshot(device)
    result = simplify.components_to_boxes(device)
    assert result.units == 57 and len(device.of_role(COMPONENT)) == 57
    c17 = by_label(device, "C17")
    assert c17.item.name == "Конденсатор К50-35 (габарит)"
    low, high = device.world_box(c17)
    assert low[2] == pytest.approx(12.6) and high[2] == pytest.approx(12.6 + 10.5)
    report = check(device)
    board = by_label(device, "Плата").key
    assert not [item for item in report.of_kind(CLASH)
                if board in (item.first, item.second)]
    areas = {device.units[item.second if item.first == board else item.first].name: item.area
             for item in report.of_unit(board) if item.kind == TOUCH}
    assert areas["R1"] == pytest.approx(1.28)
    assert areas["DD1"] == pytest.approx(4.9 * 5.9)
    # Висящий VT2 остался висеть: ошибку модели упрощение не прячет.
    assert "VT2" in {device.units[key].name for key in report.isolated}
    simplify.restore(device, before)
    assert len(by_label(device, "DD1").item.placements) == 17


def test_exclude_small_and_fasteners(tmp_path_factory):
    device = demo_device(tmp_path_factory)
    result = simplify.exclude_small(device, 1.7)
    assert result.units == 24
    assert by_label(device, "R1").role == EXCLUDED and by_label(device, "C1").role == COMPONENT
    simplify.exclude_role(device, FASTENER)
    assert not device.of_role(FASTENER)
    report = check(device)
    names = {device.units[key].name for item in report.contacts
             for key in (item.first, item.second)}
    assert "R1" not in names and "Винт крышки:1" not in names
