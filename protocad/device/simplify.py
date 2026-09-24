"""Упрощение прибора для расчёта — по определениям, для всех вхождений сразу.

Правится ОПРЕДЕЛЕНИЕ, а не вхождение: у платы с тремястами резисторами
0603 одна форма резистора, и заменить её габаритом — это одна операция, а
не триста. Поэтому упрощение быстрое и не разъезжается между одинаковыми
деталями.

Что делается:

* **отверстия** — снимаются алгоритмом ядра (тот же путь, что в окне
  подготовки к расчёту, `protocad.prep.defeature`): переходные в основании
  платы, крепёжные в корпусе. Что убрать не удалось, называется;
* **компоненты → габарит** — корпус с выводами заменяется одним бруском по
  габариту в осях компонента. Брусок стоит на плате: выводы, уходящие под
  плату (выводной монтаж), отрезаются по её поверхности — иначе брусок
  пересёк бы основание;
* **исключить** мелкие компоненты или весь крепёж — единица остаётся в
  приборе, но в расчёт и в STEP для расчёта не идёт.

Любое упрощение отменяется снимком (`Device.snapshot`): формы ядра
неизменяемы, снимок хранит ссылки, а не копии геометрии.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .. import kernel
from ..model import KIND_DETAIL, Assembly, Item
from .model import COMPONENT, EXCLUDED, FASTENER, HOUSING, OTHER, SUBSTRATE, Device


@dataclass
class Result:
    """Итог упрощения словами и числами."""

    message: str
    definitions: int = 0
    units: int = 0
    notes: list = field(default_factory=list)


# --- снимки ---------------------------------------------------------------------------


def snapshot(device: Device) -> dict:
    """Всё, что меняют разбор и упрощение: свойства единиц, случаи, слои,
    формы определений и то, на какие определения ссылаются вхождения."""
    shapes, links = {}, []
    stack = [device.root]
    seen = set()
    while stack:
        item = stack.pop()
        if item.stable_id in seen:
            continue
        seen.add(item.stable_id)
        if isinstance(item, Assembly):
            for occurrence in item.placements:
                links.append((occurrence, occurrence.item))
                stack.append(occurrence.item)
        else:
            shapes[item.stable_id] = (item, item.shape)
    return {"shapes": shapes, "links": links, "state": device.to_dict()}


def restore(device: Device, state: dict) -> None:
    for occurrence, item in state["links"]:
        occurrence.item = item
    for item, shape in state["shapes"].values():
        item.shape = shape
    device.forget_shapes()
    device.apply(state["state"])


def _changed(device: Device) -> None:
    """После правки форм: забыть посчитанное и пересобрать единицы."""
    device.forget_shapes()
    device.__dict__.pop("_contact_faces", None)
    device.build_units()


# --- отверстия ---------------------------------------------------------------------------


def remove_holes(device: Device, max_diameter: float,
                 roles=(SUBSTRATE, HOUSING, OTHER), units=None,
                 fillets: float = 0.0) -> Result:
    """Снять отверстия до ``max_diameter`` (и скругления до ``fillets``) у
    определений выбранных единиц."""
    from ..prep import Study
    from ..prep.defeature import defeature

    chosen = list(units) if units is not None else [
        unit for unit in device.units.values() if unit.role in roles]
    definitions: dict = {}
    skipped = 0
    for unit in chosen:
        if isinstance(unit.item, Assembly) or unit.item.shape is None:
            skipped += 1
            continue
        definitions.setdefault(unit.item.stable_id, (unit.item, []))[1].append(unit)
    result = Result("")
    faces_before = faces_after = 0
    for item, owners in definitions.values():
        study = Study(name=item.name)
        study.add_body(item.shape, item.name or "деталь")
        report = defeature(study, holes=max_diameter, fillets=fillets)
        for note in report.findings:
            if note.code == "NOT_REMOVED":
                result.notes.append(f"{owners[0].name}: {note.message}")
        if not report.ok or not report.used.get("found"):
            continue
        faces_before += report.before["faces"]
        faces_after += report.after["faces"]
        item.shape = study.bodies[0].shape if len(study.bodies) == 1 else kernel.compound(
            [body.shape for body in study.bodies])
        result.definitions += 1
        result.units += len(owners)
    if skipped:
        result.notes.append(f"пропущено единиц-сборок (компоненты с выводами): {skipped} — "
                            f"их упрощает замена габаритом")
    if result.definitions:
        _changed(device)
        result.message = (f"отверстия до Ø{max_diameter:g} мм сняты у {result.definitions} "
                          f"определений ({result.units} единиц); граней "
                          f"{faces_before} → {faces_after}")
    else:
        result.message = f"отверстий до Ø{max_diameter:g} мм у выбранных деталей нет"
    return result


# --- компоненты габаритом -----------------------------------------------------------------


def components_to_boxes(device: Device, units=None) -> Result:
    """Заменить компоненты брусками по габариту в их собственных осях.

    Брусок ставится на плату: низ — не ниже поверхности основания в осях
    компонента. Выводы под платой отрезаются; иначе брусок пересёк бы
    основание, а в расчёте это ошибка сетки.
    """
    chosen = list(units) if units is not None else device.of_role(COMPONENT)
    by_definition: dict = {}
    for unit in chosen:
        if unit.role != COMPONENT:
            continue
        by_definition.setdefault(unit.item.stable_id, (unit.item, []))[1].append(unit)
    result = Result("")
    replaced: dict = {}
    for item, owners in by_definition.values():
        if item.stable_id in replaced:
            continue
        low, high = device.item_box(item)
        if low is None:
            continue
        floor = _board_floor(device, owners)
        if floor is not None and floor < high[2]:
            low = low.copy()
            low[2] = max(low[2], floor)
        size = np.maximum(high - low, 1e-3)
        box = kernel.box(*size, origin=tuple(low))
        replaced[item.stable_id] = Item(item.designation, f"{item.name} (габарит)",
                                        kind=KIND_DETAIL, shape=box)
        result.definitions += 1
        result.units += len(owners)
        if floor is None:
            result.notes.append(f"{owners[0].name}: плата под компонентом не найдена — "
                                f"брусок по полному габариту")
    if not replaced:
        return Result("компонентов для замены нет")
    _swap(device.root, replaced)
    _changed(device)
    result.message = (f"компонентов заменено габаритом: {result.units} "
                      f"(определений {result.definitions})")
    return result


def _board_floor(device: Device, owners: list):
    """Высота поверхности платы в осях компонента — одна для всех его
    вхождений, иначе ``None`` (тогда брусок по полному габариту)."""
    heights = []
    for unit in owners:
        substrate = next((other for other in device.units.values()
                          if other.role == SUBSTRATE and other.board == unit.board), None)
        if substrate is None:
            return None
        facts = device.facts(substrate.item)
        if facts.plate is None:
            return None
        normal = substrate.matrix[:3, :3] @ np.asarray(facts.plate["normal"], float)
        centre = substrate.matrix[:3, :3] @ facts.centre + substrate.matrix[:3, 3]
        half = facts.plate["thickness"] / 2.0
        inverse = np.linalg.inv(unit.matrix)
        local_normal = inverse[:3, :3] @ normal
        if abs(abs(local_normal[2]) - 1.0) > 1e-6:
            return None
        # Та поверхность основания, что обращена к компоненту.
        side = 1.0 if float((unit.matrix[:3, 3] - centre) @ normal) >= 0 else -1.0
        surface = centre + normal * half * side
        heights.append(float((inverse[:3, :3] @ surface + inverse[:3, 3])[2]))
    if not heights or max(heights) - min(heights) > 1e-6:
        return None
    return heights[0]


def _swap(root, replaced: dict) -> None:
    """Все вхождения заменённых определений — на новые определения."""
    stack, seen = [root], set()
    while stack:
        item = stack.pop()
        if item.stable_id in seen or not isinstance(item, Assembly):
            continue
        seen.add(item.stable_id)
        for occurrence in item.placements:
            fresh = replaced.get(occurrence.item.stable_id)
            if fresh is not None:
                occurrence.item = fresh
            else:
                stack.append(occurrence.item)


# --- исключение -------------------------------------------------------------------------------


def exclude_small(device: Device, max_size: float, roles=(COMPONENT,)) -> Result:
    """Исключить из расчёта единицы, у которых наибольший размер меньше
    ``max_size`` мм: чип-резисторы 0402 и мельче в тепловом расчёте прибора
    обычно ничего не решают, а сетку утяжеляют."""
    found = []
    for unit in device.units.values():
        if unit.role not in roles:
            continue
        low, high = device.world_box(unit)
        if float(np.max(high - low)) < max_size:
            found.append(unit)
    for unit in found:
        unit.role = EXCLUDED
        unit.reason = f"исключено: меньше {max_size:g} мм"
    return Result(f"исключено единиц: {len(found)} (наибольший размер меньше "
                  f"{max_size:g} мм)", units=len(found))


def exclude_role(device: Device, role: str = FASTENER) -> Result:
    found = [unit for unit in device.units.values() if unit.role == role]
    for unit in found:
        unit.role = EXCLUDED
        unit.reason = "исключено вместе со своей группой"
    return Result(f"исключено единиц: {len(found)}", units=len(found))
