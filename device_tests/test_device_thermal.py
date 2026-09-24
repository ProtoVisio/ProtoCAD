"""Тепловые сопротивления контактов и выгрузка для расчётных систем."""

import csv
import json

import pytest

from device_helpers import by_label, demo_device
from protocad import kernel
from protocad.assembly import read_step_tree
from protocad.device import EXCLUDED, Device
from protocad.device import thermal
from protocad.device.contacts import GAP, TOUCH, check
from protocad.device.export import export_step
from test_device_contacts import _device


def test_resistance_from_layer_area_and_thickness():
    device = _device(("Низ", kernel.box(10, 10, 2), (0, 0, 0)),
                     ("Верх", kernel.box(4, 6, 3), (8, 7, 2)))
    report = check(device)
    contact = report.of_kind(TOUCH)[0]
    thermal.set_interface(device, [contact.key], "Паста КПТ-8")
    row = thermal.table(device, report)[0]
    assert row.area == pytest.approx(6.0)
    assert row.r == pytest.approx(0.05e-3 / 0.7)              # м²·К/Вт
    assert row.h == pytest.approx(0.7 / 0.05e-3)              # Вт/(м²·К)
    assert row.resistance == pytest.approx((0.05e-3 / 0.7) / 6e-6)   # К/Вт ≈ 11,9
    thermal.set_interface(device, [contact.key], "Прокладка теплопроводящая λ=1,5", 0.25)
    row = thermal.table(device, report)[0]
    assert row.thickness == 0.25 and row.r == pytest.approx(0.25e-3 / 1.5)


def test_demo_table_defaults_and_exports(tmp_path_factory, tmp_path):
    device = demo_device(tmp_path_factory, problems=True)
    report = check(device)
    rows = thermal.table(device, report)
    by_pair = {(row.first, row.second): row for row in rows}
    solder = by_pair[("Плата", "R1")]
    assert solder.interface == "Припой ПОС-61" and solder.area == pytest.approx(1.28)
    air = next(row for row in rows if "VT2" in (row.first, row.second) and row.kind == GAP)
    assert air.interface == "Воздух" and air.thickness == pytest.approx(0.386, abs=1e-3)
    lid = next(row for row in rows if {row.first, row.second} == {"Основание", "Крышка"})
    assert lid.interface == "Сухой контакт" and lid.h == pytest.approx(2600)
    groups = thermal.groups(rows)
    assert sum(len(group["rows"]) for group in groups) == len(rows)

    path = thermal.write_csv(rows, tmp_path / "контакты.csv")
    with path.open(encoding="utf-8-sig") as handle:
        table = list(csv.reader(handle, delimiter=";"))
    assert table[0][0] == "№" and len(table) == len(rows) + 1
    assert table[1][4].count(",") <= 1 and "." not in table[1][4]
    data = json.loads(thermal.write_json(device, report, rows, tmp_path / "прибор.json")
                      .read_text(encoding="utf-8"))
    assert len(data["contacts"]) == len(rows) and len(data["clashes"]) == 8
    device.split_halves(next(iter(device.boards)))
    cases = thermal.write_cases_csv(device, tmp_path / "случаи.csv")
    lines = cases.read_text(encoding="utf-8-sig").splitlines()
    assert lines[0].endswith("Полукомплект 1;Полукомплект 2") and len(lines) == 58


def test_interfaces_survive_save(tmp_path_factory, tmp_path):
    device = demo_device(tmp_path_factory)
    report = check(device)
    key = next(item.key for item in report.contacts if item.kind == TOUCH)
    thermal.set_interface(device, [key], "Паста КПТ-8", 0.1)
    again = Device.open(device.save(tmp_path / "прибор.prcadAsm"))
    assert again.interfaces[key] == {"interface": "Паста КПТ-8", "thickness": 0.1}


def test_step_by_roles_and_without_excluded(tmp_path_factory, tmp_path):
    device = demo_device(tmp_path_factory)
    device.set_role([by_label(device, "Шильдик")], EXCLUDED)
    tree = read_step_tree(export_step(device, tmp_path / "по видам.step"))
    top = [item.reference for item in tree.placements]
    assert top == ["Корпус", "A1", "Крепёж", "Прочее"]
    board = tree.placements[1].item
    kinds = [item.reference for item in board.placements]
    assert kinds[0] == "Плата" and "R — Резисторы" in kinds
    resistors = next(item.item for item in board.placements if item.reference == "R — Резисторы")
    assert [item.reference for item in resistors.placements][:3] == ["R1", "R2", "R3"]
    assert resistors.placements[0].item is resistors.placements[5].item
    other = tree.placements[3].item
    assert "Шильдик" not in [item.reference for item in other.placements]
    source = read_step_tree(export_step(device, tmp_path / "как было.step", "source"))
    assert "Шильдик" not in [item.reference for item in source.placements]
    assert "A1" in [item.reference for item in source.placements]
