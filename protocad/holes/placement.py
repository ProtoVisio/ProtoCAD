"""Позиции отверстий: от точек эскиза до готовых инструментов.

По `docs/09_HOLES.md`, §25: если все отверстия лежат на одной плоской
опоре, источник позиций — обычный эскиз ProtoCAD, а позициями служат его
ТОЧКИ. Ничего нового для этого заводить не нужно: у эскиза уже есть связи,
размеры и решатель, и отверстия ездят за ними сами (H-012).

Толщина под каждой позицией берётся у движка, а не из габарита (§32).
Разница видна на ступенчатой детали: габарит скажет 25 там, где под этой
точкой десять, и зенковка выхода повиснет в воздухе.

Негодная позиция не пропускается (§31). Девять отверстий из десяти молча
не строятся: отсутствие отверстия хуже явного отказа.
"""

from __future__ import annotations

from . import geometry
from .model import Diagnostic, HoleError, HolePosition


def points_of(sketch) -> list:
    """Точки эскиза, годные в позиции отверстий.

    Вспомогательные и ссылочные не берутся: первые держат построение,
    вторые сняты с детали. Отверстие в опорной точке — почти всегда не то,
    чего хотели, а узнать об этом можно только по готовой детали.
    """
    found = []
    for point in sketch.points:
        if getattr(point, "construction", False):
            continue
        if getattr(point, "external", ""):
            continue
        if sketch.uses_point(point):
            # Точка, на которой держится отрезок или дуга, — это УГОЛ
            # контура, а не место отверстия.
            continue
        found.append(point)
    return found


def positions_from(sketch, existing=()) -> list:
    """Точки эскиза → позиции. Номера уже известных сохраняются.

    Устойчивость номеров — требование §29 и H-019: сдвиг точки не должен
    выглядеть как «старой позиции не стало, появилась новая», иначе всё,
    что на неё ссылается, теряет опору на ровном месте.
    """
    known = {item.point_ref: item for item in existing}
    found = []
    for point in points_of(sketch):
        reference = f"{sketch.name}#{point.id}"
        place = known.get(reference)
        x, y = sketch.coordinates(point)
        spot = sketch.plane.point_at(x, y)
        if place is None:
            place = HolePosition(point_ref=reference)
        place.xyz = tuple(float(value) for value in spot)
        place.state, place.message = "valid", ""
        found.append(place)
    return found


def instances(feature, spans, thickness_fallback: float = 0.0) -> tuple:
    """Позиции и интервалы материала → инструменты для движка.

    ``spans`` — по списку интервалов на каждую позицию, как их вернул
    движок. Возвращает ``(инструменты, замечания)``.

    Интервал выбирается ПЕРВЫЙ по ходу оси: инструмент входит там, где
    впервые встретил материал. Если интервалов несколько — деталь с
    полостью, — сквозное проходит их все, и длина считается до последнего
    выхода (§32). Иначе отверстие остановилось бы в пустоте внутри детали.
    """
    definition = feature.definition
    through = definition.end_condition.type != "blind"
    axis = (0.0, 0.0, 1.0)
    if feature.placement.direction_mode == "shared_direction" \
            and feature.placement.positions:
        first = feature.placement.positions[0].direction
        if first:
            axis = first

    made, notes = [], []
    for place, intervals in zip(feature.placement.positions, spans):
        forward = [pair for pair in intervals if pair[1] > 1e-9]
        if not forward:
            # §31: молча пропустить нельзя. Позиция помечается негодной, и
            # проверка операции на этом остановится.
            place.state = "invalid"
            place.message = "HOLE_NO_MATERIAL_INTERSECTION"
            notes.append(Diagnostic(
                "HOLE_NO_MATERIAL_INTERSECTION",
                f"позиция {place.id}: ось не пересекает материал",
                position=place.id))
            continue
        entry = max(0.0, forward[0][0])
        exit_at = forward[-1][1] if through else forward[0][1]
        thickness = exit_at - entry
        if thickness <= 0.0:
            place.state = "invalid"
            place.message = "HOLE_NO_MATERIAL_INTERSECTION"
            notes.append(Diagnostic(
                "HOLE_NO_MATERIAL_INTERSECTION",
                f"позиция {place.id}: материала под ней нет",
                position=place.id))
            continue
        try:
            profile = geometry.build(definition, thickness)
        except HoleError as failure:
            place.state = "invalid"
            place.message = failure.code
            notes.append(Diagnostic(failure.code,
                                    f"позиция {place.id}: {failure.message}",
                                    position=place.id))
            continue
        # Вход считается по СВОЕЙ оси позиции: у точек на разных гранях
        # оси разные (§30), и общая сдвинула бы начало инструмента не туда.
        along = place.direction or axis
        origin = tuple(place.xyz[index] + along[index] * entry
                       for index in range(3))
        made.append({
            "contour": [[radius, depth] for radius, depth in profile.contour()],
            "origin": list(origin),
            "axis": list(along),
        })
    return made, notes


def positions_from_3d(sketch, existing=()) -> tuple:
    """Точки трёхмерного эскиза → позиции и замечания.

    У каждой позиции СВОЯ ось: точки на разных гранях дают разные оси при
    одном описании отверстия (§30). Поэтому ось едет в самой позиции, а не
    одна на всю операцию.

    Потерянная опора — не повод подставить похожую грань (§38). Позиция
    объявляется негодной, операция на ней останавливается.
    """
    known = {item.point_ref: item for item in existing}
    found, notes = [], []
    for point in sketch.points:
        place = known.get(point.id) or HolePosition(point_ref=point.id)
        place.support_ref = point.support_ref
        place.state, place.message = "valid", ""
        if point.lost or point.resolved is None:
            place.state = "invalid"
            place.message = "HOLE_LOST_REFERENCE"
            place.xyz = tuple(point.xyz)
            place.direction = None
            notes.append(Diagnostic(
                "HOLE_LOST_REFERENCE",
                f"позиция {place.id}: опора потеряна, укажите её заново",
                position=place.id))
        else:
            place.xyz = tuple(point.resolved)
            place.direction = (tuple(point.axis) if point.axis else None)
        found.append(place)
    return found, notes
