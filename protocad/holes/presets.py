"""Пресеты отверстий: готовые описания под частые случаи.

По `docs/09_HOLES.md`, §41. Пресет хранит ОПИСАНИЕ отверстия и ничего
больше: ни опор, ни позиций, ни области действия. Поэтому «М4 крепёж
платы, сквозное, цековка Ø8×2» применяется в любой новой детали, а не
только там, где его завели.

Числа здесь — не нормативные таблицы. Цековка под винт с цилиндрической
головкой берётся из каталога опорных поверхностей (P03), и когда он
ляжет в `catalogs/`, пресеты станут ссылаться на записи вместо
собственных чисел. Пока они помечены как ЧЕРНОВЫЕ и это видно в самом
пресете: `provisional: True`.

Диаметра подготовительного отверстия под резьбу здесь нет и быть не
может (§19): технологический каталог ещё не поставлен, а выдумывать
диаметр сверления запрещено. Резьбовой пресет поэтому задаёт диаметр
канала ЯВНО и говорит об этом.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import (
    Counterbore,
    Countersink,
    EndCondition,
    HoleDefinition,
    StraightBore,
)


@dataclass
class Preset:
    """Готовое описание отверстия под имя."""

    id: str
    title: str
    note: str = ""
    #: Черновой: числа взяты не из поставленного каталога.
    provisional: bool = False
    make: object = None

    def definition(self) -> HoleDefinition:
        return self.make()


def _simple() -> HoleDefinition:
    return HoleDefinition(
        core=StraightBore(diameter_mm=5.0),
        end_condition=EndCondition(type="through_all"))


def _blind_drill() -> HoleDefinition:
    return HoleDefinition(
        core=StraightBore(diameter_mm=5.0),
        end_condition=EndCondition(type="blind", depth_mm=10.0,
                                   bottom_type="drill_tip",
                                   depth_reference="shoulder",
                                   tip_angle_deg=118.0))


def _board_m4() -> HoleDefinition:
    """Сквозное под винт М4 с цилиндрической головкой, с цековкой.

    Диаметр канала 4,5 — ЗАЗОР под винт М4, а не резьба. Числа черновые:
    придут из каталога сквозных отверстий (P02) и опорных поверхностей
    (P03).
    """
    return HoleDefinition(
        core=StraightBore(diameter_mm=4.5),
        near_stack=[Counterbore(diameter_mm=8.0, depth_mm=2.0)],
        end_condition=EndCondition(type="through_all"))


def _countersunk_m4() -> HoleDefinition:
    """Сквозное под винт М4 с потайной головкой, зенковка 90°."""
    return HoleDefinition(
        core=StraightBore(diameter_mm=4.5),
        near_stack=[Countersink(definition_mode="diameter_angle",
                                diameter_mm=9.0, included_angle_deg=90.0)],
        end_condition=EndCondition(type="through_all"))


PRESETS = (
    Preset("simple.through", "Простое сквозное",
           "Канал Ø5 насквозь", False, _simple),
    Preset("simple.blind", "Глухое со следом сверла",
           "Канал Ø5 на 10 мм, дно 118°", False, _blind_drill),
    Preset("fastener.m4.counterbore", "М4 с цековкой (черновой)",
           "Зазор Ø4,5 насквозь, цековка Ø8 × 2. Числа станут каталожными "
           "после поставки P02/P03", True, _board_m4),
    Preset("fastener.m4.countersink", "М4 потайной (черновой)",
           "Зазор Ø4,5 насквозь, зенковка Ø9 под 90°. Числа станут "
           "каталожными после поставки P03", True, _countersunk_m4),
)

BY_ID = {item.id: item for item in PRESETS}


def titles() -> list:
    return [item.title for item in PRESETS]


def by_title(title: str) -> Preset | None:
    for item in PRESETS:
        if item.title == title:
            return item
    return None
