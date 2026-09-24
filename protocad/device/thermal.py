"""Тепловые сопротивления контактов и материалы деталей.

Контакт двух деталей в тепловом расчёте — это тонкий слой между ними:
паста, прокладка, клей, припой, воздух. У слоя есть теплопроводность λ и
толщина t, у контакта — площадь S. Отсюда три числа, и каждой расчётной
системе нужно своё:

* r = t / λ, м²·К/Вт — удельное сопротивление. Его вводят в SOLIDWORKS
  Flow Simulation как контактное сопротивление (Contact Resistance);
* h = λ / t, Вт/(м²·К) — проводимость контакта. Это Thermal Conductance
  контактной области в Ansys Mechanical;
* R = r / S, К/Вт — полное сопротивление пары. Его подставляют в тепловую
  схему замещения и по нему видно, какой контакт узкое место.

Сухой контакт металла с металлом тоже слой: неровности поверхностей
оставляют между ними воздух, и по порядку величины это слой воздуха
толщиной в шероховатость (для обработанных поверхностей — около 10 мкм,
h ≈ 2600 Вт/(м²·К)). Это оценка, а не справочное значение для конкретной
пары при конкретном усилии затяжки, — и так она и названа.

Числа в библиотеках — типовые, из открытых справочников; для ответственного
расчёта их заменяют данными на конкретный материал.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from .contacts import CLASH, GAP, TOUCH, Report, pair_key
from .model import COMPONENT, EXCLUDED, FASTENER, HOUSING, OTHER, SUBSTRATE, Device


@dataclass(frozen=True)
class Material:
    name: str
    conductivity: float          # Вт/(м·К)
    note: str = ""


@dataclass(frozen=True)
class Interface:
    """Слой в контакте. ``thickness`` — по умолчанию, мм; ``None`` — толщина
    берётся из геометрии (зазор)."""

    name: str
    conductivity: float          # Вт/(м·К)
    thickness: float | None
    note: str = ""


MATERIALS = {item.name: item for item in (
    Material("АМг6", 122.0, "деформируемый алюминиевый сплав"),
    Material("Д16Т", 130.0, "деформируемый алюминиевый сплав"),
    Material("АД31Т1", 190.0, "профильный алюминиевый сплав"),
    Material("АК12", 155.0, "литейный алюминиевый сплав"),
    Material("Медь М1", 390.0),
    Material("Латунь Л63", 110.0),
    Material("Сталь 20", 50.0),
    Material("Сталь 30ХГСА", 38.0),
    Material("Сталь 12Х18Н10Т", 15.0, "нержавеющая"),
    Material("Стеклотекстолит FR-4", 0.3, "без меди; плата со слоями меди в плоскости "
                                         "проводит в десятки раз лучше — задайте "
                                         "эквивалент в расчётной системе"),
    Material("Корпус ЭРИ (компаунд)", 0.8, "эпоксидный компаунд пластмассовых корпусов"),
    Material("Керамика ВК94", 20.0, "керамические корпуса и подложки"),
    Material("Полиамид ПА6", 0.25),
    Material("Фторопласт-4", 0.25),
)}

INTERFACES = {item.name: item for item in (
    Interface("Сухой контакт", 0.026, 0.01,
              "металл по металлу: воздух в неровностях ≈ 10 мкм — оценка"),
    Interface("Паста КПТ-8", 0.7, 0.05),
    Interface("Паста теплопроводящая λ=3", 3.0, 0.05),
    Interface("Прокладка теплопроводящая λ=1,5", 1.5, 0.5),
    Interface("Прокладка теплопроводящая λ=3", 3.0, 1.0),
    Interface("Клей теплопроводящий", 1.0, 0.1),
    Interface("Клей эпоксидный", 0.2, 0.1),
    Interface("Припой ПОС-61", 50.0, 0.1, "паяные выводы"),
    Interface("Лак", 0.2, 0.05),
    Interface("Воздух", 0.026, None, "толщина — зазор по геометрии"),
)}

#: Материал детали по умолчанию — по роли.
DEFAULT_MATERIAL = {SUBSTRATE: "Стеклотекстолит FR-4", COMPONENT: "Корпус ЭРИ (компаунд)",
                    HOUSING: "АМг6", FASTENER: "Сталь 30ХГСА", OTHER: "Латунь Л63"}


def default_interface(device: Device, contact) -> str:
    """Слой по умолчанию: зазор — воздух; компонент на плате — припой;
    остальное — сухой контакт."""
    if contact.kind == GAP:
        return "Воздух"
    roles = {device.units[contact.first].role, device.units[contact.second].role}
    if roles == {COMPONENT, SUBSTRATE}:
        return "Припой ПОС-61"
    return "Сухой контакт"


def material_of(unit) -> Material:
    name = unit.material or DEFAULT_MATERIAL.get(unit.role, "")
    return MATERIALS.get(name) or Material(name or "не задан", 0.0)


# --- таблица -------------------------------------------------------------------------


@dataclass
class Row:
    """Строка таблицы тепловых сопротивлений."""

    number: int
    key: str
    first: str
    second: str
    kind: str
    area: float                  # мм²
    interface: str
    thickness: float             # мм
    conductivity: float          # Вт/(м·К)
    centre: tuple
    note: str = ""

    @property
    def r(self) -> float:
        """м²·К/Вт."""
        return (self.thickness / 1000.0) / self.conductivity if self.conductivity else 0.0

    @property
    def h(self) -> float:
        """Вт/(м²·К)."""
        return 1.0 / self.r if self.r else float("inf")

    @property
    def resistance(self) -> float:
        """К/Вт."""
        return self.r / (self.area / 1e6) if self.area else float("inf")


def table(device: Device, report: Report) -> list:
    """Строки для каждого контакта и зазора прибора. Пересечения в таблицу
    не идут: это ошибка модели, её исправляют, а не считают."""
    rows = []
    for contact in report.contacts:
        if contact.kind == CLASH:
            continue
        first, second = device.units[contact.first], device.units[contact.second]
        if EXCLUDED in (first.role, second.role):
            continue
        chosen = device.interfaces.get(contact.key, {}) if hasattr(device, "interfaces") \
            else {}
        name = chosen.get("interface") or default_interface(device, contact)
        layer = INTERFACES.get(name) or INTERFACES["Сухой контакт"]
        thickness = chosen.get("thickness")
        if thickness is None:
            thickness = contact.distance if layer.thickness is None else layer.thickness
        if contact.kind == GAP and name == "Воздух":
            thickness = contact.distance
        note = layer.note
        if contact.kind == GAP and name != "Воздух":
            note = (f"по геометрии зазор {contact.distance:.3g} мм, задан слой "
                    f"{thickness:.3g} мм").strip()
        rows.append(Row(number=len(rows) + 1, key=contact.key, first=first.name,
                        second=second.name, kind=contact.kind, area=contact.area,
                        interface=name, thickness=float(thickness),
                        conductivity=layer.conductivity, centre=contact.centre, note=note))
    return rows


def set_interface(device: Device, keys, name: str, thickness: float | None = None) -> None:
    """Задать слой для контактов (по ключам пар)."""
    if name not in INTERFACES:
        raise ValueError(f"нет такого слоя: {name}")
    for key in keys:
        device.interfaces[key] = {"interface": name, "thickness": thickness}


def groups(rows: list) -> list:
    """Одинаковые сопротивления вместе: в Flow Simulation одно условие
    «Contact Resistance» ставится сразу на все грани с одним r."""
    found: dict = {}
    for row in rows:
        key = (row.interface, round(row.thickness, 6), round(row.conductivity, 6))
        found.setdefault(key, []).append(row)
    return [{"interface": name, "thickness": thickness, "conductivity": conductivity,
             "r": items[0].r, "h": items[0].h, "rows": items}
            for (name, thickness, conductivity), items in found.items()]


# --- выгрузка ----------------------------------------------------------------------------


def _number(value: float) -> str:
    """Число для русского Excel: запятая, без лишних нулей."""
    if value in (float("inf"), float("-inf")):
        return "∞"
    return f"{value:.6g}".replace(".", ",")


COLUMNS = ("№", "Деталь 1", "Деталь 2", "Вид", "Площадь, мм²", "Слой", "Толщина, мм",
           "λ, Вт/(м·К)", "r, м²·К/Вт (Flow Simulation)", "h, Вт/(м²·К) (Ansys)",
           "R, К/Вт", "X, мм", "Y, мм", "Z, мм", "Примечание")


def write_csv(rows: list, path) -> Path:
    """Таблица для Excel: точка с запятой, UTF-8 с меткой, запятая в числах."""
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(COLUMNS)
        for row in rows:
            writer.writerow([row.number, row.first, row.second, row.kind, _number(row.area),
                             row.interface, _number(row.thickness),
                             _number(row.conductivity), _number(row.r), _number(row.h),
                             _number(row.resistance), *(_number(v) for v in row.centre),
                             row.note])
    return path


def write_json(device: Device, report: Report, rows: list, path) -> Path:
    """Всё для сценариев расчётной системы: единицы, контакты, случаи."""
    path = Path(path)
    data = {
        "device": device.name,
        "units_of_measure": {"area": "mm2", "thickness": "mm", "conductivity": "W/(m*K)",
                             "r": "m2*K/W", "h": "W/(m2*K)", "R": "K/W", "length": "mm"},
        "settings": report.settings,
        "units": [{"key": unit.key, "name": unit.name, "role": unit.role, "kind": unit.kind,
                   "board": unit.board, "cases": sorted(unit.cases),
                   "material": material_of(unit).name,
                   "conductivity": material_of(unit).conductivity}
                  for unit in device.units.values() if unit.role != EXCLUDED],
        "cases": [{"name": case.name, "note": case.note} for case in device.cases],
        "contacts": [{"number": row.number, "first": row.first, "second": row.second,
                      "kind": row.kind, "area": row.area, "interface": row.interface,
                      "thickness": row.thickness, "conductivity": row.conductivity,
                      "r": row.r, "h": row.h, "R": row.resistance,
                      "centre": list(row.centre), "note": row.note} for row in rows],
        "clashes": [{"first": device.units[item.first].name,
                     "second": device.units[item.second].name, "volume": item.volume,
                     "centre": list(item.centre), "note": item.note}
                    for item in report.of_kind(CLASH)],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def write_cases_csv(device: Device, path) -> Path:
    """Состав расчётных случаев: какой компонент в каком случае."""
    path = Path(path)
    names = device.case_names()
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(["Плата", "Обозначение", "Вид", "Изделие", "Материал", *names])
        units = sorted((unit for unit in device.units.values() if unit.role == COMPONENT),
                       key=lambda unit: (unit.board, unit.designator.sort_key
                                         if unit.designator else ("", unit.label, 0, "")))
        for unit in units:
            board = device.boards.get(unit.board)
            writer.writerow([board.name if board else "", unit.name, device.kind_title(unit),
                             unit.item.name, material_of(unit).name,
                             *("да" if name in unit.cases else "" for name in names)])
    return path


def contact_key(first: str, second: str) -> str:
    return pair_key(first, second)


__all__ = ["CLASH", "GAP", "TOUCH", "INTERFACES", "MATERIALS", "Row", "table", "groups",
           "write_csv", "write_json", "write_cases_csv", "set_interface", "material_of"]
