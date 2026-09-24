"""Прибор → STEP для расчётной системы.

Два вида структуры:

* ``"source"`` — как пришло, только без исключённых из расчёта единиц;
* ``"roles"`` — по смыслу: «Корпус», платы (основание и компоненты по
  видам: «R — Резисторы», «C — Конденсаторы»…), «Крепёж», «Прочее». В
  расчётной системе группа выбирается целиком — материал и источники тепла
  назначаются всем резисторам разом, а не по одному.

Определения не копируются: 300 одинаковых резисторов уходят одной формой и
300 положениями (`protocad.assembly.export`).
"""

from __future__ import annotations

from pathlib import Path

from ..assembly.export import write_step_tree
from ..model import KIND_ASSEMBLY, Assembly
from .model import COMPONENT, EXCLUDED, FASTENER, HOUSING, OTHER, ROLE_GROUPS, SUBSTRATE, Device


def export_step(device: Device, path, structure: str = "roles") -> Path:
    if structure == "source":
        root = _filtered(device)
    elif structure == "roles":
        root = by_roles(device)
    else:
        raise ValueError(f"неизвестная структура: {structure}")
    return write_step_tree(root, path)


def by_roles(device: Device) -> Assembly:
    """Состав прибора по видам — новый, из тех же определений."""
    root = Assembly(device.root.designation, device.root.name or device.name,
                    kind=KIND_ASSEMBLY)
    placed = {HOUSING: [], FASTENER: [], OTHER: []}
    boards: dict = {key: {"substrate": [], "kinds": {}} for key in device.boards}
    for unit in device.units.values():
        if unit.role == EXCLUDED:
            continue
        if unit.role in (SUBSTRATE, COMPONENT) and unit.board in boards:
            entry = boards[unit.board]
            if unit.role == SUBSTRATE:
                entry["substrate"].append(unit)
            else:
                entry["kinds"].setdefault(device.kind_title(unit), []).append(unit)
            continue
        placed.setdefault(unit.role if unit.role in placed else OTHER, []).append(unit)
    for role in (HOUSING,):
        _group(root, ROLE_GROUPS[role], placed[role])
    for key, entry in boards.items():
        board = Assembly("", device.boards[key].name, kind=KIND_ASSEMBLY)
        for unit in entry["substrate"]:
            board.place(unit.item, unit.name, unit.matrix)
        for title in sorted(entry["kinds"]):
            units = sorted(entry["kinds"][title], key=_order)
            _group(board, title, units)
        if board.placements:
            root.place(board, device.boards[key].name)
    for role in (FASTENER, OTHER):
        _group(root, ROLE_GROUPS[role], placed[role])
    return root


def _order(unit):
    return unit.designator.sort_key if unit.designator is not None else ("", unit.label, 0, "")


def _group(parent: Assembly, title: str, units: list) -> None:
    if not units:
        return
    group = Assembly("", title, kind=KIND_ASSEMBLY)
    for unit in units:
        group.place(unit.item, unit.name, unit.matrix)
    parent.place(group, title)


def _filtered(device: Device):
    """Исходный состав без исключённых единиц. Узлы, в которых исключать
    нечего, берутся как есть — так сохраняются общие определения."""
    excluded = [unit.key for unit in device.units.values() if unit.role == EXCLUDED]
    if not excluded:
        return device.root

    def touched(key: str) -> bool:
        return any(item == key or item.startswith(key + "/") or not key
                   for item in excluded)

    def rebuild(item, path):
        copy = Assembly(item.designation, item.name, kind=item.kind)
        for occurrence in item.placements:
            here = path + (occurrence.stable_id,)
            key = "/".join(here)
            if key in excluded:
                continue
            child = occurrence.item
            if isinstance(child, Assembly) and touched(key):
                child = rebuild(child, here)
            copy.place(child, occurrence.reference, occurrence.transform)
        return copy

    return rebuild(device.root, ())
