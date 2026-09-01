"""Данные отверстия для чертежа.

По `docs/09_HOLES.md`, §42. Операция обязана отдавать структуру, а не
готовую строку: строку собирает формировщик ЕСКД, и держать её здесь
значило бы иметь два источника истины (§21). Сменится оформление — при
готовой строке пришлось бы переписывать модель.

Главное правило §32 и §37: чертёж НЕ восстанавливает тип отверстия из
формы. Цековка Ø9 × 4 и просто ступенчатое отверстие в B-Rep выглядят
одинаково, и различить их по граням нельзя. Поэтому что это было, знает
операция, а не геометрия.

Обозначение резьбы собирается из ЛЕКСЕМ каталога (§21): номинал, шаг,
поле допуска, направление. UI строку не склеивает.
"""

from __future__ import annotations

from .model import Counterbore, Countersink, StraightBore, TaperBore


def of(feature, catalog=None) -> dict:
    """Данные выноски для операции. Одна структура на всю операцию.

    Отверстия одной операции одинаковы по построению (§2.1), поэтому
    количество — число, а не список: «8 отв. Ø5» и есть выноска.
    """
    definition = feature.definition
    definition.resolved()
    data = {
        "count": feature.count,
        "core": _core_of(definition),
        "near": _side_of(definition.near_stack),
        "far": _side_of(definition.far_stack),
    }
    threads = [_thread_of(item, catalog) for item in definition.threads]
    if threads:
        data["thread"] = threads[0]
        if len(threads) > 1:
            data["threads"] = threads
    return data


def _core_of(definition) -> dict:
    core = definition.core
    end = definition.end_condition
    found = {"end_condition": end.type}
    if isinstance(core, TaperBore):
        found.update({"kind": "taper",
                      "diameter": core.diameter_mm,
                      "end_diameter": core.end_diameter_mm,
                      "included_angle": core.included_angle_deg})
    else:
        found.update({"kind": "straight", "diameter": core.diameter_mm})
    if end.type == "blind":
        # На чертёж идут ОБЕ глубины и то, от чего они считаны: при
        # коническом дне одно число без указания «от чего» означает две
        # разные детали (§9.2).
        found["depth"] = end.depth_mm
        found["depth_reference"] = end.depth_reference
        found["bottom"] = end.bottom_type
        if end.bottom_type != "flat":
            found["tip_angle"] = end.tip_angle_deg
            found["cylinder_depth"] = end.cylinder_depth(
                definition.core_diameter_mm)
            found["total_depth"] = end.total_depth(
                definition.core_diameter_mm)
    return found


def _side_of(stack) -> dict:
    """Одна сторона: что на ней сделано. Пусто — ничего."""
    found = {}
    for element in stack:
        if isinstance(element, Counterbore):
            found["counterbore"] = {"diameter": element.diameter_mm,
                                    "depth": element.depth_mm}
        elif isinstance(element, Countersink):
            found["countersink"] = {"diameter": element.diameter_mm,
                                    "depth": element.depth_mm,
                                    "angle": element.included_angle_deg}
        elif isinstance(element, StraightBore):
            found["bore"] = {"diameter": element.diameter_mm,
                             "depth": element.depth_mm}
    return found


def _thread_of(thread, catalog) -> dict:
    """Лексемы обозначения резьбы. Строку из них собирает ЕСКД-формировщик."""
    tokens = {
        "nominal": thread.size_id,
        "pitch": thread.pitch_mm,
        "tolerance": thread.tolerance_class,
        "handedness": thread.handedness,
        "starts": thread.starts,
    }
    found = {"catalog_id": thread.catalog_id,
             "representation": thread.representation,
             "designation_tokens": tokens}
    if thread.depth_mode == "specified" and thread.depth_mm is not None:
        found["depth"] = thread.depth_mm
    if thread.overrides:
        # Правки человека едут на чертёж ОТДЕЛЬНО от каталожных значений:
        # иначе не отличить норму от чужого решения (§18).
        found["overrides"] = dict(thread.overrides)
    if catalog is not None and thread.catalog_id:
        try:
            values = catalog.resolve(thread)
        except Exception:  # noqa: BLE001 — каталога может не быть (§H-034)
            found["catalog_missing"] = True
        else:
            found["standard"] = values.get("standard", {})
            found["minor_diameter"] = values.get("minor_diameter_mm")
    return found


def roles_of(profile, feature_id: str, position_id: str) -> list:
    """Семантические имена граней отверстия (§37).

    Имя строится из операции, позиции и роли участка — не из номера грани.
    Номер меняется при любой правке выше по дереву, и выноска, привязанная
    к `Face12`, однажды укажет на другое место.
    """
    found = []
    for role in profile.roles:
        if not role:
            continue
        found.append(f"hole/{feature_id}/{position_id}/{role}")
    return found
