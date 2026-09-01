"""Построение режущего инструмента отверстия.

По `docs/09_HOLES.md`, §36. Инструмент собирается ЦЕЛИКОМ и вычитается
один раз. Отдельная булева операция на каждую цековку — это лишние
топологические преобразования на ровном месте, а на решете из тридцати
отверстий они складываются в секунды.

Инструмент описывается ОСЕВЫМ ПРОФИЛЕМ: ломаной «радиус — глубина» от
поверхности входа внутрь материала. Тело вращения по такому профилю и
есть то, что вынимается. Форма получается одна, а не набор пересекающихся
цилиндров, и объём её считается точно — по формуле усечённого конуса, а
не приближением.

Стороны независимы (§5.6). Стек выхода строится от ВЫЧИСЛЕННОЙ дальней
поверхности, а не от ближней плюс толщина: при сквозном отверстии в
ступенчатой детали это разные места.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .model import (
    Counterbore,
    Countersink,
    HoleError,
    StraightBore,
    TaperBore,
)


@dataclass
class ToolProfile:
    """Осевой профиль инструмента: точки ``(радиус, глубина)``.

    Глубина растёт ОТ ПОВЕРХНОСТИ ВХОДА внутрь. Точки идут по порядку, и
    между соседними инструмент — усечённый конус (у равных радиусов это
    цилиндр).
    """

    points: list = field(default_factory=list)
    #: Роли участков, по одной на промежуток между точками (§37).
    roles: list = field(default_factory=list)

    @property
    def depth_mm(self) -> float:
        return self.points[-1][1] if self.points else 0.0

    def volume_mm3(self) -> float:
        """Объём тела вращения. Точный, а не по сетке.

        Усечённый конус: ``π·h·(R₁² + R₁R₂ + R₂²)/3``. Цилиндр — его
        частный случай, и отдельной ветки не требует.
        """
        total = 0.0
        for (first_r, first_z), (second_r, second_z) in zip(self.points,
                                                            self.points[1:]):
            height = second_z - first_z
            if height <= 0.0:
                continue
            total += (math.pi * height
                      * (first_r * first_r + first_r * second_r
                         + second_r * second_r) / 3.0)
        return total

    def contour(self) -> list:
        """ЗАМКНУТЫЙ осевой контур для тела вращения: ``(радиус, глубина)``.

        К профилю добавляется возврат по оси: от последней точки к оси, по
        оси назад и к началу. Тело вращения по такому контуру и есть
        инструмент.

        Вырожденные звенья убираются здесь. Ядро на отрезке нулевой длины
        отвечает `Both points are equal` и роняет построение целиком, а
        появляются такие звенья сами: ступенька нулевой высоты — обычное
        дело, когда зенковка кончается ровно на диаметре канала.
        """
        if not self.points:
            return []
        found = list(self.points)
        if found[-1][0] > 1e-12:
            found.append((0.0, found[-1][1]))
        if found[0][0] > 1e-12:
            found.append((0.0, found[0][1]))
        kept = [found[0]]
        for radius, depth in found[1:]:
            last = kept[-1]
            if abs(radius - last[0]) > 1e-9 or abs(depth - last[1]) > 1e-9:
                kept.append((radius, depth))
        # Замыкание неявное: последняя точка соединяется с первой. Если они
        # совпали, лишнюю надо снять — иначе то же нулевое звено.
        if len(kept) > 2 and abs(kept[0][0] - kept[-1][0]) < 1e-9                 and abs(kept[0][1] - kept[-1][1]) < 1e-9:
            kept.pop()
        return kept

    def radius_at(self, depth_mm: float) -> float:
        """Радиус инструмента на этой глубине. Вне профиля — ноль."""
        if not self.points or depth_mm < 0.0 or depth_mm > self.depth_mm:
            return 0.0
        for (first_r, first_z), (second_r, second_z) in zip(self.points,
                                                            self.points[1:]):
            if first_z <= depth_mm <= second_z:
                if second_z - first_z < 1e-12:
                    return max(first_r, second_r)
                part = (depth_mm - first_z) / (second_z - first_z)
                return first_r + (second_r - first_r) * part
        return 0.0


def build(definition, thickness_mm: float) -> ToolProfile:
    """Профиль инструмента для одного отверстия.

    ``thickness_mm`` — сколько материала вдоль оси в этой позиции. Оно
    приходит СНАРУЖИ, из пересечения оси с телом (§32): сама модель
    отверстия про деталь ничего не знает и знать не должна.

    Строится УЧАСТКАМИ, а не точками. Точка сама по себе не знает, чем
    она соединена со следующей, и роли участков разъезжались с ними при
    первой же перестановке. Здесь участок несёт оба радиуса и своё имя
    сразу, а точки выводятся из него.
    """
    definition.resolved()
    core = definition.core
    diameter = definition.core_diameter_mm
    if diameter <= 0.0:
        raise HoleError("HOLE_NO_POSITION",
                        "диаметр отверстия должен быть больше нуля")
    if thickness_mm <= 0.0:
        raise HoleError("HOLE_NO_MATERIAL_INTERSECTION",
                        "ось отверстия не пересекает материал")

    reach = _core_reach(definition, thickness_mm)
    near = _stack_runs(definition.near_stack, diameter, "near")
    far = _stack_runs(definition.far_stack, diameter, "far")
    near_depth = sum(run[2] for run in near)
    far_depth = sum(run[2] for run in far)
    if near_depth + far_depth >= reach - 1e-9:
        raise HoleError(
            "HOLE_STACK_OVERLAP",
            f"обработка с двух сторон сходится: вход занимает "
            f"{near_depth:g} мм, выход {far_depth:g} мм, а канала "
            f"{reach:g} мм")

    runs = list(near)
    body = reach - near_depth - far_depth
    if isinstance(core, TaperBore):
        runs.append((diameter / 2.0, core.end_diameter_mm / 2.0, body,
                     "core_taper"))
    else:
        runs.append((diameter / 2.0, diameter / 2.0, body, "core_wall"))
    # Стек выхода разворачивается: хранится он «от дальней поверхности
    # внутрь», а по оси идёт навстречу. Переворот делает построение, и
    # человеку мысленно инвертировать порядок не приходится (§4.2).
    for first, second, height, role in reversed(far):
        runs.append((second, first, height, role))
    if not far and definition.end_condition.type == "blind":
        tip = definition.end_condition.tip_length(diameter)
        if tip > 0.0:
            runs.append((diameter / 2.0, 0.0, tip, "drill_tip"))

    points, roles, depth = [], [], 0.0
    for first, second, height, role in runs:
        if height <= 1e-12:
            continue
        if not points:
            points.append((first, depth))
        elif abs(points[-1][0] - first) > 1e-12:
            # СТУПЕНЬКА: радиус меняется на месте, без наклона. Без неё
            # между цековкой и каналом вырастал конус, которого в металле
            # нет, — и снимаемый объём выходил больше настоящего.
            #
            # Имя у неё не служебное: ступенька после цековки — это её
            # ПОЛ, и чертёж с интерфейсом обращаются именно к нему (§37).
            points.append((first, depth))
            roles.append(_floor_role(roles))
        depth += height
        points.append((second, depth))
        roles.append(role)
    if not points:
        points = [(diameter / 2.0, 0.0)]
    return ToolProfile(points=points, roles=roles)


def _core_reach(definition, thickness_mm: float) -> float:
    """Докуда идёт инструмент от поверхности входа."""
    end = definition.end_condition
    if end.type == "blind":
        if end.depth_mm is None:
            raise HoleError("HOLE_NO_POSITION", "глубина не задана")
        return end.cylinder_depth(definition.core_diameter_mm)
    if end.type == "offset_from_face":
        return thickness_mm + float(end.offset_mm)
    # Сквозное и «до границы» — на всю толщину. Запас не добавляется:
    # профиль описывает СНИМАЕМОЕ, а лишний хвост в воздухе только
    # искажал бы объём, по которому потом сверяют.
    return thickness_mm


def _stack_runs(stack, core_diameter_mm: float, side: str) -> list:
    """Стек одной стороны → участки ``(радиус, радиус, длина, роль)``."""
    runs = []
    for element in stack:
        if isinstance(element, Counterbore):
            radius = element.diameter_mm / 2.0
            runs.append((radius, radius, element.depth_mm,
                         f"{side}_counterbore_wall"))
        elif isinstance(element, Countersink):
            runs.append((element.diameter_mm / 2.0, core_diameter_mm / 2.0,
                         element.depth_mm, f"{side}_countersink"))
        elif isinstance(element, StraightBore):
            radius = element.diameter_mm / 2.0
            runs.append((radius, radius, element.depth_mm, f"{side}_bore"))
        else:
            raise HoleError("HOLE_STACK_OVERLAP",
                            f"элемент {type(element).__name__} в стеке "
                            f"стороны пока не строится")
    return runs


def _floor_role(roles) -> str:
    """Имя ступеньки по тому, что ей предшествовало."""
    previous = roles[-1] if roles else ""
    if previous.endswith("_wall"):
        return previous[:-len("_wall")] + "_floor"
    return f"{previous}_floor" if previous else "step"
