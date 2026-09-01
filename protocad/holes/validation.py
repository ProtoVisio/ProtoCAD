"""Проверка отверстия перед построением.

Главное правило спецификации (§31): **отсутствие отверстия хуже явного
отказа**. Одна негодная позиция блокирует всю операцию — девять отверстий
из десяти молча не строятся. Узнать о пропавшем десятом можно только по
готовой детали, а к тому времени по ней уже заказали заготовку.

Второе правило (§52): ничего не додумывать. Не подбирать ближайший
диаметр, не заменять потерянную грань похожей, не превращать номинальный
диаметр резьбы в диаметр выреза. Если правила нет — `SPEC_GAP`.
"""

from __future__ import annotations

from .model import (
    Counterbore,
    Countersink,
    Diagnostic,
    HoleError,
    StraightBore,
    TaperBore,
)


def check(feature) -> list:
    """Все замечания к операции. Пустой список — можно строить."""
    found = []
    found.extend(_check_definition(feature.definition))
    found.extend(_check_positions(feature.placement))
    found.extend(_check_threads(feature.definition))
    found.extend(_check_thickness(feature.definition))
    return found


def blocking(found) -> list:
    return [item for item in found if item.blocking]


def _check_definition(definition) -> list:
    found = []
    core = definition.core
    diameter = definition.core_diameter_mm
    if diameter <= 0.0:
        found.append(Diagnostic(
            "HOLE_NO_POSITION", "диаметр отверстия должен быть больше нуля"))
        return found
    try:
        definition.resolved()
    except HoleError as failure:
        found.append(Diagnostic(failure.code, failure.message))
        return found

    for side, stack in (("входа", definition.near_stack),
                        ("выхода", definition.far_stack)):
        for element in stack:
            if isinstance(element, Counterbore):
                if element.diameter_mm <= diameter:
                    found.append(Diagnostic(
                        "HOLE_COUNTERBORE_TOO_SMALL",
                        f"цековка со стороны {side}: диаметр "
                        f"{element.diameter_mm:g} не больше отверстия "
                        f"{diameter:g}"))
                if element.depth_mm <= 0.0:
                    found.append(Diagnostic(
                        "HOLE_COUNTERBORE_TOO_SMALL",
                        f"цековка со стороны {side}: глубина должна быть "
                        f"больше нуля"))
            elif isinstance(element, Countersink):
                if (element.diameter_mm or 0.0) <= diameter:
                    found.append(Diagnostic(
                        "HOLE_COUNTERSINK_TOO_SMALL",
                        f"зенковка со стороны {side}: диаметр "
                        f"{element.diameter_mm:g} не больше отверстия "
                        f"{diameter:g}"))
                if (element.depth_mm or 0.0) <= 0.0:
                    found.append(Diagnostic(
                        "HOLE_COUNTERSINK_TOO_SMALL",
                        f"зенковка со стороны {side}: глубина вышла "
                        f"неположительной — проверьте угол"))
    if isinstance(core, TaperBore) and core.end_diameter_mm is not None \
            and core.end_diameter_mm <= 0.0:
        found.append(Diagnostic(
            "HOLE_STACK_OVERLAP",
            "конус сходится в точку раньше заданной глубины"))
    return found


def _check_thickness(definition) -> list:
    """Стеки не должны съедать друг друга (§11, H-011).

    Проверяется только там, где толщина ИЗВЕСТНА из самой операции — у
    глухого отверстия. Сквозное меряется по детали, и это дело построения:
    выдумывать здесь толщину значило бы проверять не то.
    """
    end = definition.end_condition
    if end.type != "blind" or end.depth_mm is None:
        return []
    try:
        reach = end.total_depth(definition.core_diameter_mm)
    except HoleError as failure:
        return [Diagnostic(failure.code, failure.message)]
    near = definition.near_reach_mm()
    if near >= reach:
        return [Diagnostic(
            "HOLE_STACK_OVERLAP",
            f"обработка со стороны входа уходит на {near:g} мм при глубине "
            f"отверстия {reach:g} мм — от канала ничего не остаётся")]
    return []


def _check_positions(placement) -> list:
    found = []
    if not placement.positions:
        found.append(Diagnostic("HOLE_NO_POSITION",
                                "не задано ни одной позиции отверстия"))
        return found
    seen = set()
    for position in placement.positions:
        if position.id in seen:
            found.append(Diagnostic(
                "HOLE_LOST_REFERENCE",
                f"позиция {position.id} встречается дважды",
                position=position.id))
        seen.add(position.id)
        if position.state == "invalid":
            # Одна негодная позиция блокирует ВСЮ операцию (§31).
            found.append(Diagnostic(
                position.message or "HOLE_NO_MATERIAL_INTERSECTION",
                f"позиция {position.id}: "
                f"{position.message or 'не пересекает материал'}",
                position=position.id))
        elif position.state == "warning":
            found.append(Diagnostic(
                "HOLE_NO_MATERIAL_INTERSECTION", position.message,
                severity="warning", position=position.id))
    return found


def _check_threads(definition) -> list:
    found = []
    for thread in definition.threads:
        if not thread.catalog_id and not thread.overrides:
            found.append(Diagnostic(
                "HOLE_THREAD_CATALOG_ENTRY_MISSING",
                "резьба включена, но стандарт не выбран"))
            continue
        if thread.starts < 1:
            found.append(Diagnostic(
                "HOLE_THREAD_NO_VALID_BORE",
                "число заходов резьбы должно быть не меньше одного"))
        if thread.depth_mode == "specified" and thread.depth_mm is not None:
            end = definition.end_condition
            if end.type == "blind" and end.depth_mm is not None:
                try:
                    available = end.cylinder_depth(
                        definition.core_diameter_mm)
                except HoleError:
                    continue
                # Резьба начинается под цековкой, если она есть (§20).
                start = sum(item.depth_mm for item in definition.near_stack
                            if isinstance(item, Counterbore))
                if thread.depth_mm > available - start + 1e-9:
                    found.append(Diagnostic(
                        "HOLE_THREAD_DEPTH_EXCEEDS_BORE",
                        f"резьба длиной {thread.depth_mm:g} мм не помещается: "
                        f"цилиндрического участка под неё "
                        f"{available - start:g} мм"))
    return found
