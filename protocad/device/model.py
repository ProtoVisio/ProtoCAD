"""Прибор для теплового расчёта: расчётные единицы, платы, роли, случаи.

STEP прибора — это дерево сборки, и его устройство зависит от того, кто
его выгрузил. Для расчёта нужно другое дерево — по смыслу: платы с их
компонентами, корпус, крепёж, прочее. Здесь оно и строится.

**Расчётная единица** — то, что в расчёте одно тело или одна группа тел:

* компонент платы — ЦЕЛИКОМ, с выводами: у модели SOIC-8 девять тел, но
  это один компонент;
* основание платы;
* деталь корпуса, винт, втулка — каждая деталь отдельно.

Единица — это вхождение в дереве STEP, адресуется путём вхождений от
корня. Роль, вид, плата, расчётные случаи и материал — её свойства;
группы в дереве окна выводятся из них, а не хранятся отдельно, поэтому
перестановка единицы из группы в группу не может разойтись с данными.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..model import Assembly, Item
from . import designators as designators_module
from .designators import GOST
from .shapes import Facts, facts_of

SUBSTRATE = "substrate"
COMPONENT = "component"
HOUSING = "housing"
FASTENER = "fastener"
OTHER = "other"
EXCLUDED = "excluded"

ROLES = (SUBSTRATE, COMPONENT, HOUSING, FASTENER, OTHER, EXCLUDED)
ROLE_TITLES = {SUBSTRATE: "Основание платы", COMPONENT: "Компонент (ЭРИ)",
               HOUSING: "Корпус", FASTENER: "Крепёж", OTHER: "Прочее",
               EXCLUDED: "Исключено из расчёта"}
#: Роли во множественном числе — для заголовков групп дерева.
ROLE_GROUPS = {SUBSTRATE: "Основания плат", COMPONENT: "Компоненты",
               HOUSING: "Корпус", FASTENER: "Крепёж", OTHER: "Прочее",
               EXCLUDED: "Исключено из расчёта"}

#: Имя записи прибора в контейнере.
EXTRA = "device"


def key_of(path) -> str:
    return "/".join(path)


@dataclass
class Unit:
    """Расчётная единица прибора."""

    key: str
    path: tuple
    label: str
    item: Item
    #: Положение в координатах прибора (произведение положений по пути).
    matrix: np.ndarray
    role: str = OTHER
    #: Вид: у компонента — буквы обозначения (R, VD, DD), у крепежа —
    #: «винт», «гайка», «шайба», у прочего — «втулка», «разъём»…
    kind: str = ""
    #: Ключ платы — у основания и компонентов.
    board: str = ""
    designator: object = None
    cases: set = field(default_factory=set)
    material: str = ""
    #: Почему так решено — человеку, который будет проверять разбор.
    reason: str = ""

    @property
    def name(self) -> str:
        return self.designator.text if self.designator is not None else self.label


@dataclass
class Board:
    """Плата: узел дерева, в котором лежат основание и компоненты."""

    key: str
    name: str
    convention: str = GOST


@dataclass
class Case:
    """Расчётный случай: какие компоненты в нём работают."""

    name: str
    note: str = ""


class Device:
    """Прибор: дерево STEP плюс то, что нужно тепловому расчёту."""

    def __init__(self, root: Item, name: str = ""):
        self.root = root
        self.name = name or root.label or "Прибор"
        self.units: dict = {}
        self.boards: dict = {}
        self.cases: list = []
        #: Узлы, которые человек велел считать одной единицей / разобрать.
        self.whole: set = set()
        self.split: set = set()
        self.source = ""
        self.path: Path | None = None
        self._facts: dict = {}
        self._shapes: dict = {}
        self._boxes: dict = {}
        #: Замечания первого разбора — показываются человеку.
        self.notes: list = []

    # --- сведения о форме -------------------------------------------------------

    def shape_of(self, item: Item):
        """Форма единицы. У сборки (компонент с выводами) набор тел
        собирается один раз и пересобирается, только если тела поменялись."""
        if not isinstance(item, Assembly):
            return item.shape
        signature = _leaf_signature(item)
        held = self._shapes.get(item.stable_id)
        if held is None or held[0] != signature:
            held = (signature, unit_shape(item))
            self._shapes[item.stable_id] = held
        return held[1]

    def facts(self, item: Item) -> Facts:
        """Сведения о форме определения — один разбор на определение."""
        shape = self.shape_of(item)
        key = (item.stable_id, id(shape))
        if key not in self._facts:
            self._facts[key] = (facts_of(shape), shape)
        return self._facts[key][0]

    def item_box(self, item: Item) -> tuple:
        """Габарит изделия в его координатах: (низ, верх) или (None, None).

        У сборки — по габаритам частей, без сборки формы: форма всей платы
        в тысячу компонентов — это десятки тысяч граней, а габарит нужен
        ради одного сравнения.
        """
        held = self._boxes.get(item.stable_id)
        if held is not None:
            return held
        if isinstance(item, Assembly):
            lows, highs = [], []
            for occurrence in item.placements:
                low, high = self.item_box(occurrence.item)
                if low is None:
                    continue
                corners = _box_corners(low, high) @ occurrence.transform[:3, :3].T \
                    + occurrence.transform[:3, 3]
                lows.append(corners.min(axis=0))
                highs.append(corners.max(axis=0))
            box = (np.min(lows, axis=0), np.max(highs, axis=0)) if lows else (None, None)
        elif item.shape is None:
            box = (None, None)
        else:
            corners = _obb_corners(self.facts(item))
            box = (corners.min(axis=0), corners.max(axis=0))
        self._boxes[item.stable_id] = box
        return box

    def forget_shapes(self) -> None:
        """Забыть всё посчитанное по формам — после упрощения деталей."""
        self._facts, self._shapes, self._boxes = {}, {}, {}

    def world_box(self, unit: Unit) -> tuple:
        """Габарит единицы в координатах прибора: (низ, верх)."""
        if isinstance(unit.item, Assembly):
            corners = _box_corners(*self.item_box(unit.item))
        else:
            corners = _obb_corners(self.facts(unit.item))
        moved = corners @ unit.matrix[:3, :3].T + unit.matrix[:3, 3]
        return moved.min(axis=0), moved.max(axis=0)

    # --- единицы ------------------------------------------------------------------

    def build_units(self) -> None:
        """Разложить дерево на расчётные единицы. Свойства уже известных
        единиц сохраняются: пересборка не должна терять работу человека."""
        before = self.units
        self.units = {}
        board_keys = set(self.boards)
        self._walk(self.root, (), np.eye(4), "" in board_keys, board_keys)
        for key, unit in self.units.items():
            old = before.get(key)
            if old is not None:
                for name in ("role", "kind", "board", "cases", "material", "reason"):
                    setattr(unit, name, getattr(old, name))

    def _walk(self, item, path, matrix, in_board: bool, board_keys) -> None:
        for occurrence in getattr(item, "placements", ()):
            here = path + (occurrence.stable_id,)
            key = key_of(here)
            placed = matrix @ occurrence.transform
            child = occurrence.item
            if isinstance(child, Assembly):
                if not self._is_unit(occurrence, key, in_board):
                    self._walk(child, here, placed, key in board_keys, board_keys)
                    continue
            elif child.shape is None:
                continue
            self.units[key] = Unit(
                key=key, path=here, label=occurrence.label, item=child,
                matrix=placed,
                designator=designators_module.parse(occurrence.reference)
                or designators_module.parse(child.name))

    def _is_unit(self, occurrence, key: str, in_board: bool) -> bool:
        """Узел-сборка — одна единица или разбирается на детали.

        Компонент платы — одна единица, даже если он сборка корпуса с
        выводами. Узел с позиционным обозначением — тоже: так в STEP
        приходят ЭРИ. Всё прочее разбирается до деталей.
        """
        if key in self.whole:
            return True
        if key in self.split or key in self.boards:
            return False
        if designators_module.parse(occurrence.reference) is not None:
            return not _has_boards(occurrence.item, self.boards, key)
        return in_board and not _looks_like_housing(occurrence.item)

    # --- выборки -------------------------------------------------------------------

    def of_role(self, role: str) -> list:
        return [unit for unit in self.units.values() if unit.role == role]

    def on_board(self, board_key: str, role: str = COMPONENT) -> list:
        return [unit for unit in self.units.values()
                if unit.board == board_key and unit.role == role]

    def unit(self, key: str):
        return self.units.get(key)

    def convention(self, unit: Unit) -> str:
        board = self.boards.get(unit.board)
        return board.convention if board is not None else GOST

    def kind_title(self, unit: Unit) -> str:
        """Вид единицы словами — для группы в дереве."""
        if unit.role == COMPONENT:
            if unit.designator is None:
                return unit.item.name or "Без обозначения"
            name = designators_module.kind_name(unit.kind, self.convention(unit))
            return f"{unit.kind} — {name}"
        return unit.kind or ROLE_TITLES.get(unit.role, "")

    # --- правка ----------------------------------------------------------------------

    def set_role(self, units, role: str, board: str = "") -> None:
        """Назначить роль. Компонент и основание обязаны принадлежать
        плате; у остальных принадлежность к плате снимается."""
        if role not in ROLES:
            raise ValueError(f"неизвестная роль: {role}")
        for unit in units:
            unit.role = role
            if role in (SUBSTRATE, COMPONENT):
                unit.board = board or unit.board or next(iter(self.boards), "")
                if role == COMPONENT and unit.designator is not None:
                    unit.kind = unit.designator.prefix
            else:
                unit.board = ""
            unit.reason = "назначено вручную"

    def set_kind(self, units, kind: str) -> None:
        for unit in units:
            unit.kind = kind

    def set_material(self, units, material: str) -> None:
        for unit in units:
            unit.material = material

    # --- расчётные случаи ---------------------------------------------------------

    def case_names(self) -> list:
        return [case.name for case in self.cases]

    def add_case(self, name: str, note: str = "") -> Case:
        name = name.strip()
        if not name:
            raise ValueError("у расчётного случая должно быть имя")
        if name in self.case_names():
            raise ValueError(f"случай «{name}» уже есть")
        case = Case(name, note)
        self.cases.append(case)
        return case

    def remove_case(self, name: str) -> None:
        self.cases = [case for case in self.cases if case.name != name]
        for unit in self.units.values():
            unit.cases.discard(name)

    def rename_case(self, old: str, new: str) -> None:
        new = new.strip()
        if not new or new in self.case_names():
            raise ValueError(f"имя «{new}» пустое или уже занято")
        for case in self.cases:
            if case.name == old:
                case.name = new
        for unit in self.units.values():
            if old in unit.cases:
                unit.cases.discard(old)
                unit.cases.add(new)

    def assign(self, units, case: str, on: bool = True) -> int:
        """Включить единицы в случай (или исключить). Возвращает, скольких
        это коснулось."""
        if case not in self.case_names():
            raise ValueError(f"нет случая «{case}»")
        touched = 0
        for unit in units:
            if on and case not in unit.cases:
                unit.cases.add(case)
                touched += 1
            elif not on and case in unit.cases:
                unit.cases.discard(case)
                touched += 1
        return touched

    def by_ranges(self, text: str, board: str = "") -> tuple:
        """Компоненты по диапазонам обозначений: (найденные, чего нет).

        «Чего нет» — обозначения из диапазона, которых на плате не нашлось:
        опечатку в диапазоне надо показать, а не проглотить.
        """
        ranges = designators_module.parse_ranges(text)
        found = [unit for unit in self.units.values()
                 if unit.role == COMPONENT and (not board or unit.board == board)
                 and designators_module.in_ranges(unit.designator, ranges)]
        present = {(unit.designator.prefix, unit.designator.number) for unit in found}
        missing = [f"{prefix}{number}" for prefix, low, high in ranges
                   for number in range(low, high + 1)
                   if (prefix, number) not in present]
        return found, missing

    def split_halves(self, board: str, names=("Полукомплект 1", "Полукомплект 2")):
        """Разделить компоненты платы на два полукомплекта по нумерации.

        Для каждого вида компоненты идут по номеру. Граница ищется так,
        чтобы у половин совпал состав — те же определения в том же
        количестве: R1–R12 и R13–R24 одинаковы, значит, это полукомплекты.
        Что осталось после двух одинаковых половин (C17 после C1–C16,
        одиночный XS1), — ОБЩЕЕ: входит в оба случая.

        Возвращает замечания — их надо показать человеку: граница по
        нумерации — это догадка, и у вида, где половины не совпали по
        составу, её надо проверить.
        """
        from collections import Counter

        notes = []
        for name in names:
            if name not in self.case_names():
                self.add_case(name)
        by_kind: dict = {}
        for unit in self.on_board(board):
            if unit.designator is not None:
                by_kind.setdefault(unit.designator.prefix, []).append(unit)
        common = []
        for prefix, units in sorted(by_kind.items()):
            units.sort(key=lambda unit: unit.designator.sort_key)
            kinds = [unit.item.stable_id for unit in units]
            half = next((size for size in range(len(units) // 2, 0, -1)
                         if Counter(kinds[:size]) == Counter(kinds[size:2 * size])), 0)
            if half == 0 and len(units) > 1:
                half = len(units) // 2
                notes.append(f"{prefix}: половины не совпадают по составу — "
                             f"разделено пополам по номерам, проверьте границу")
                self.assign(units[:half], names[0])
                self.assign(units[half:], names[1])
                continue
            self.assign(units[:half], names[0])
            self.assign(units[half:2 * half], names[1])
            rest = units[2 * half:]
            for name in names:
                self.assign(rest, name)
            common.extend(rest)
        if common:
            notes.append("Общие для обоих полукомплектов: " + designators_module.compact(
                unit.designator for unit in common))
        return notes

    # --- файл --------------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "schema": 1, "name": self.name, "source": self.source,
            "boards": [{"key": board.key, "name": board.name,
                        "convention": board.convention}
                       for board in self.boards.values()],
            "cases": [{"name": case.name, "note": case.note} for case in self.cases],
            "whole": sorted(self.whole), "split": sorted(self.split),
            "units": {key: {"role": unit.role, "kind": unit.kind, "board": unit.board,
                            "cases": sorted(unit.cases), "material": unit.material,
                            "reason": unit.reason}
                      for key, unit in self.units.items()},
        }

    def apply(self, data: dict) -> None:
        """Восстановить свойства из записи. Единицы строятся заново по
        дереву; записи о единицах, которых больше нет, пропускаются."""
        self.name = data.get("name") or self.name
        self.source = data.get("source") or ""
        self.boards = {item["key"]: Board(item["key"], item.get("name") or "Плата",
                                          item.get("convention") or GOST)
                       for item in data.get("boards") or ()}
        self.cases = [Case(item["name"], item.get("note") or "")
                      for item in data.get("cases") or ()]
        self.whole = set(data.get("whole") or ())
        self.split = set(data.get("split") or ())
        self.build_units()
        for key, saved in (data.get("units") or {}).items():
            unit = self.units.get(key)
            if unit is None:
                continue
            unit.role = saved.get("role") or unit.role
            unit.kind = saved.get("kind") or ""
            unit.board = saved.get("board") or ""
            unit.cases = set(saved.get("cases") or ())
            unit.material = saved.get("material") or ""
            unit.reason = saved.get("reason") or ""

    def save(self, path) -> Path:
        from .. import format as fmt
        from ..preview import build

        path = fmt.write(self.root, path, preview=build(self.root),
                         extras={EXTRA: self.to_dict()})
        self.path = Path(path)
        return self.path

    @classmethod
    def open(cls, path) -> "Device":
        from .. import format as fmt

        root, _manifest = fmt.read(path)
        data = fmt.read_extra(path, EXTRA)
        if data is None:
            raise ValueError(f"{Path(path).name}: в файле нет разбора прибора")
        device = cls(root)
        device.apply(data)
        device.path = Path(path)
        return device


# --- помощники ----------------------------------------------------------------------


def unit_shape(item: Item):
    """Форма единицы в её собственных координатах. У сборки (компонент с
    выводами) — все её тела одним набором."""
    if not isinstance(item, Assembly):
        return item.shape
    from .. import kernel

    pieces = []

    def gather(node, matrix):
        for occurrence in node.placements:
            placed = matrix @ occurrence.transform
            if isinstance(occurrence.item, Assembly):
                gather(occurrence.item, placed)
            elif occurrence.item.shape is not None:
                shape = occurrence.item.shape
                if np.abs(placed - _IDENTITY).max() > 1e-12:
                    shape = kernel.transformed(shape, placed)
                pieces.append(shape)

    gather(item, np.eye(4))
    if not pieces:
        return None
    return pieces[0] if len(pieces) == 1 else kernel.compound(pieces)


_IDENTITY = np.eye(4)


def _leaf_signature(item) -> tuple:
    found = []
    stack = [item]
    while stack:
        node = stack.pop()
        for occurrence in node.placements:
            if isinstance(occurrence.item, Assembly):
                stack.append(occurrence.item)
            else:
                found.append(id(occurrence.item.shape))
    return tuple(found)


_SIGNS = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], float)


def _box_corners(low, high) -> np.ndarray:
    low, high = np.asarray(low, float), np.asarray(high, float)
    return (low + high) / 2.0 + _SIGNS * (high - low) / 2.0


def _obb_corners(facts: Facts) -> np.ndarray:
    signs = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)],
                     float)
    return facts.centre + (signs * facts.half) @ facts.axes


def _has_boards(item, boards: dict, key: str) -> bool:
    return any(board_key.startswith(key + "/") or board_key == key
               for board_key in boards)


def _looks_like_housing(item) -> bool:
    from .classify import housing_name

    return housing_name(item.name) is not None
