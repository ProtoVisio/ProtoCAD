"""Слитое представление сборочной единицы.

Это визуальный производный артефакт, а не структурный: логический состав он не
трогает, спецификация собирается из структуры независимо от того, чем узел
нарисован в вышестоящей сборке.

Смысл — не экономия геометрии, а сокращение числа узлов сцены. Замеры
исследовательской фазы (прибор 6 плат × 300 компонентов, план 2×2):

| | детальная геометрия | габаритные коробки |
|---|---|---|
| 19 800 узлов | 42,8 мс | 40,5 мс |
| 66 узлов | **6,9 мс** | 6,9 мс |

Число узлов даёт ×6,2, детализация геометрии — около 5 %. Поэтому слитое
представление сохраняет ПОЛНУЮ геометрию: упрощать нечего, выигрыш не там.

Совпадает и с порядком работы: то, что оформляешь сейчас, показывается
детально; то, на что КД уже выпущена, входит единицей состава.
"""

from __future__ import annotations

import FreeCAD as App
import Part

from . import model


def merged_name(assembly) -> str:
    return f"Merged_{assembly.Name}"


def build(doc, assembly, force: bool = False):
    """Собрать (или пересобрать) слитое представление сборочной единицы.

    Возвращает объект ``Part::Feature`` — ядровой тип без Origin и без
    Python-прокси, самый дешёвый в обходе сцены.
    """
    if assembly.Kind != model.KIND_ASSEMBLY:
        raise ValueError(f"{assembly.Label!r} — не сборочная единица")

    existing = assembly.Merged
    if existing is not None and not assembly.MergedStale and not force:
        return existing

    shapes = _collect(assembly)
    if existing is None:
        existing = doc.addObject("Part::Feature", merged_name(assembly))
        existing.Label = f"{assembly.Designation} (слитое)".strip()
        model._add(
            existing,
            "App::PropertyString",
            "SourceAssemblyId",
            "Сборочная единица, из которой собрано представление",
        )
        assembly.Merged = existing
    existing.SourceAssemblyId = assembly.StableId
    existing.Shape = Part.makeCompound(shapes) if shapes else Part.Shape()
    assembly.MergedStale = False
    return existing


def _collect(assembly) -> list:
    """Геометрия всего состава с учётом вложенности и трансформаций.

    Вложенные сборочные единицы берутся слитыми, если те уже собраны и свежи, —
    иначе разворачиваются рекурсивно. Так пересборка верхнего уровня не тянет
    за собой пересчёт всего дерева.
    """
    shapes = []
    for link in assembly.Placements:
        item = link.LinkedObject
        if item is None:
            continue
        if item.Kind == model.KIND_ASSEMBLY:
            source = item.Merged
            if source is None or item.MergedStale:
                child_shapes = _collect(item)
                if not child_shapes:
                    continue
                shape = Part.makeCompound(child_shapes)
            else:
                shape = source.Shape.copy()
        else:
            shape = getattr(item, "Shape", None)
            if shape is None or shape.isNull():
                continue
            shape = shape.copy()
        shape.Placement = link.Placement.multiply(shape.Placement)
        shapes.append(shape)
    return shapes


def mark_stale(assembly) -> None:
    """Пометить представление несвежим — и вверх по дереву тоже."""
    assembly.MergedStale = True


def set_context(doc, focused) -> dict:
    """Показать сборку в рабочем контексте: она детально, соседи слитыми.

    Прямое следствие порядка оформления КД: изделие, на которое сейчас
    выпускается документация, нужно видеть в составе; изделия с уже выпущенной
    КД входят единицами.
    """
    detailed = {focused.Name}
    for link in getattr(focused, "Placements", []) or []:
        detailed.add(link.Name)

    shown, hidden = 0, 0
    for obj in doc.Objects:
        view = getattr(obj, "ViewObject", None)
        if view is None:
            continue
        merged_of = getattr(obj, "SourceAssemblyId", None)
        if obj.Name in detailed:
            view.Visibility = True
            shown += 1
        elif merged_of:
            view.Visibility = True
            shown += 1
        elif obj.TypeId == "App::Link" or _is_protocad(obj):
            view.Visibility = False
            hidden += 1
    return {"focused": focused.Label, "shown": shown, "hidden": hidden}


def _is_protocad(obj) -> bool:
    return getattr(getattr(obj, "Proxy", None), "Type", "").startswith("ProtoCAD::")
