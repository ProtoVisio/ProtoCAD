"""Проверки прибора: площади контактов, зазоры, пересечения, одиночки."""

import math

import numpy as np
import pytest

from device_helpers import by_label, demo_device
from protocad import kernel
from protocad.device import Device, HOUSING, OTHER
from protocad.device.contacts import (CLASH, ERROR, GAP, INFO, TOUCH, check, findings,
                                      pair_key)
from protocad.model import KIND_DETAIL, Assembly, Item


def _device(*parts):
    """Прибор из деталей: (имя, форма, сдвиг)."""
    root = Assembly("", "Проба")
    for name, shape, shift in parts:
        matrix = np.eye(4)
        matrix[:3, 3] = shift
        root.place(Item("", name, kind=KIND_DETAIL, shape=shape), name, matrix)
    device = Device(root)
    device.build_units()
    for unit in device.units.values():
        unit.role = HOUSING
    return device


def _only(report, kind):
    found = report.of_kind(kind)
    assert len(found) == 1, [(item.first, item.second, item.kind) for item in report.contacts]
    return found[0]


def test_partial_face_contact_area_is_exact():
    device = _device(("Низ", kernel.box(10, 10, 2), (0, 0, 0)),
                     ("Верх", kernel.box(4, 6, 3), (8, 7, 2)))
    contact = _only(check(device), TOUCH)
    assert contact.area == pytest.approx(2 * 3)          # 8…10 × 7…10
    assert contact.distance == 0.0


def test_hole_under_contact_is_subtracted():
    plate = kernel.cut(kernel.box(20, 20, 2), kernel.cylinder(2, 2, origin=(10, 10, 0)))
    device = _device(("Плита", plate, (0, 0, 0)), ("Брусок", kernel.box(8, 8, 3), (6, 6, 2)))
    contact = _only(check(device), TOUCH)
    assert contact.area == pytest.approx(64 - math.pi * 4, rel=2e-3)


def test_lifted_part_is_a_gap_and_isolated():
    device = _device(("Низ", kernel.box(10, 10, 2), (0, 0, 0)),
                     ("Верх", kernel.box(4, 4, 1), (3, 3, 2.3)))
    report = check(device)
    gap = _only(report, GAP)
    assert gap.distance == pytest.approx(0.3) and gap.area == pytest.approx(16)
    assert set(report.isolated) == {unit.key for unit in device.units.values()}
    notes = findings(device, report)
    assert notes[0].severity == ERROR and "зазор 0.3 мм" in notes[0].text


def test_interference_volume():
    device = _device(("А", kernel.box(10, 10, 10), (0, 0, 0)),
                     ("Б", kernel.box(10, 10, 10), (7, 8, 9)))
    clash = _only(check(device), CLASH)
    assert clash.volume == pytest.approx(3 * 2 * 1)


def test_shaft_in_hole_of_same_radius():
    plate = kernel.cut(kernel.box(20, 20, 5), kernel.cylinder(3, 5, origin=(10, 10, 0)))
    pin = kernel.cylinder(3, 12, origin=(10, 10, -3))
    report = check(_device(("Плита", plate, (0, 0, 0)), ("Штифт", pin, (0, 0, 0))))
    contact = _only(report, TOUCH)
    assert contact.area == pytest.approx(2 * math.pi * 3 * 5, rel=1e-2)
    assert not report.of_kind(CLASH)


def test_part_inside_another_and_part_in_a_cavity():
    tub = kernel.cut(kernel.box(50, 50, 30), kernel.box(40, 40, 25, origin=(5, 5, 5)))
    device = _device(("Ванна", tub, (0, 0, 0)),
                     ("В полости", kernel.box(4, 4, 4), (20, 20, 12)),
                     ("В стенке", kernel.box(2, 2, 2), (1, 20, 12)))
    report = check(device)
    clash = _only(report, CLASH)
    names = {device.units[clash.first].label, device.units[clash.second].label}
    assert names == {"Ванна", "В стенке"} and clash.note == "деталь целиком внутри другой"
    assert clash.volume == pytest.approx(8)


def test_demo_device_checks(tmp_path_factory):
    device = demo_device(tmp_path_factory, problems=True)
    report = check(device)
    assert {device.units[key].name for key in report.isolated} == {"VT2", "Шильдик"}
    assert len(report.groups) == 1
    board = by_label(device, "Плата")
    area = {device.units[item.second if item.first == board.key else item.first].name:
            item.area for item in report.of_unit(board.key) if item.kind == TOUCH}
    assert area["R1"] == pytest.approx(1.6 * 0.8)
    assert area["DD1"] == pytest.approx(8 * 0.4 * 0.9)
    clashes = report.of_kind(CLASH)
    assert len(clashes) == 8 and all("резьб" in item.note for item in clashes)
    notes = findings(device, report)
    errors = [note for note in notes if note.severity == ERROR]
    assert len(errors) == 2 and all("не касается" in note.text for note in errors)
    assert sum(1 for note in notes if note.severity == INFO and "Пересечение" in note.text) == 8
    cover = by_label(device, "Крышка")
    base = by_label(device, "Основание")
    lid = [item for item in report.contacts if item.key == pair_key(cover.key, base.key)]
    assert lid and lid[0].kind == TOUCH and lid[0].area > 1000
