"""Расчётные случаи: полукомплекты по нумерации, диапазоны, сохранение."""

import pytest

from device_helpers import by_label, demo_device
from protocad.device import COMPONENT, HOUSING, Device


def test_half_sets_split_by_numbering(tmp_path_factory):
    device = demo_device(tmp_path_factory)
    board = next(iter(device.boards))
    notes = device.split_halves(board)
    first = {unit.name for unit in device.units.values() if "Полукомплект 1" in unit.cases}
    second = {unit.name for unit in device.units.values() if "Полукомплект 2" in unit.cases}
    assert {"R1", "R12", "C8", "DD2", "VT1"} <= first
    assert {"R13", "R24", "C9", "DD4", "VT2"} <= second
    # Общее — то, что осталось после двух одинаковых половин: C17 после
    # C1–C16, одиночные XS1 и ZQ1. Оно входит в оба случая.
    assert first & second == {"C17", "XS1", "ZQ1"}
    assert notes == ["Общие для обоих полукомплектов: C17, XS1, ZQ1"]


def test_ranges_report_missing(tmp_path_factory):
    device = demo_device(tmp_path_factory)
    found, missing = device.by_ranges("R1–R3, DD1, VD9")
    assert sorted(unit.name for unit in found) == ["DD1", "R1", "R2", "R3"]
    assert missing == ["VD9"]
    device.add_case("Режим А")
    assert device.assign(found, "Режим А") == 4
    with pytest.raises(ValueError):
        device.add_case("Режим А")


def test_save_and_open_keep_roles_and_cases(tmp_path_factory, tmp_path):
    device = demo_device(tmp_path_factory)
    device.split_halves(next(iter(device.boards)))
    device.set_role([by_label(device, "Шильдик")], HOUSING)
    device.set_material([by_label(device, "Крышка")], "АМг6")
    path = device.save(tmp_path / "прибор.prcadAsm")
    again = Device.open(path)
    assert len(again.units) == len(device.units)
    assert by_label(again, "Шильдик").role == HOUSING
    assert by_label(again, "Крышка").material == "АМг6"
    assert "Полукомплект 1" in by_label(again, "R5").cases
    assert len(again.of_role(COMPONENT)) == 57
    assert again.case_names() == ["Полукомплект 1", "Полукомплект 2"]
