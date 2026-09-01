"""Эскиз → контур OCCT.

Отдельный модуль, потому что задача не сводится к перечислению рёбер.
Профиль детали — это, как правило, наружный контур и отверстия внутри, и
перепутать их местами значит получить вместо пластины с дырками дырку в
форме пластины. Здесь решается три вопроса:

1. какие объекты идут в контур (вспомогательная геометрия — не идёт);
2. как разрозненные рёбра собираются в замкнутые циклы;
3. какой цикл наружный, а какие — внутренние.

Топология берётся из самого эскиза: отрезки и дуги ссылаются на общие
точки, и цикл ищется обходом по этим ссылкам, а не подгонкой координат с
допуском. Совпадение концов «на глаз» — источник контуров, которые то
замыкаются, то нет, в зависимости от того, как решатель округлил.
"""

from __future__ import annotations

import math

from . import geom2d

CIRCLE_FACETS = 64  # только для оценки площади, в геометрию не идёт


class ProfileError(RuntimeError):
    """Из эскиза не получается замкнутый профиль."""


def _loops(segments, endpoints) -> list[list]:
    """Разложить отрезки и дуги на замкнутые циклы по общим точкам.

    ``endpoints(segment)`` возвращает пару идентификаторов концов.
    Незамкнутые цепочки отбрасываются: профиль из них не построить, а
    молчаливое «замыкание» отрезком по прямой даёт деталь, которую никто
    не рисовал.
    """
    by_point: dict[int, list] = {}
    for segment in segments:
        for point_id in endpoints(segment):
            by_point.setdefault(point_id, []).append(segment)

    unused = list(segments)
    loops = []
    while unused:
        start = unused.pop(0)
        first, current_point = endpoints(start)
        chain = [start]
        while current_point != first:
            following = None
            for candidate in by_point.get(current_point, ()):
                if candidate in chain or candidate not in unused:
                    continue
                following = candidate
                break
            if following is None:
                break  # цепочка оборвалась — не цикл
            a, b = endpoints(following)
            current_point = b if a == current_point else a
            chain.append(following)
            unused.remove(following)
        if current_point == first and len(chain) >= 2:
            loops.append(chain)
    return loops


def _sample_loop(chain, sampler) -> list[tuple[float, float]]:
    """Ломаная по циклу — для оценки площади и вложенности."""
    points: list[tuple[float, float]] = []
    for segment in chain:
        points.extend(sampler(segment))
    return points


def build_face(sketch):
    """Профиль эскиза: одна грань или несколько, с отверстиями.

    Вложенность выясняется проверкой «лежит внутри», а не сравнением
    площадей. Разница видна на самом обычном эскизе: четыре круга под
    отверстия в плите не вложены друг в друга — это ЧЕТЫРЕ грани. По
    правилу «самый большой снаружи, остальные — дыры в нём» такой эскиз
    давал грань отрицательной площади, и вырез по ней отказывал, ссылаясь
    на плоскость эскиза, которая была ни при чём.

    Считается и глубина вложенности: контур внутри отверстия — снова
    материал (остров), а не дыра в дыре.
    """
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt
    from OCP.GC import GC_MakeArcOfCircle

    def edge_of(segment):
        if segment.kind == "line":
            start, end = (sketch.coordinates(p) for p in segment.points)
            if geom2d.distance(start, end) < 1e-9:
                return None  # вырожденный отрезок ядро не примет
            return BRepBuilderAPI_MakeEdge(
                gp_Pnt(start[0], start[1], 0.0), gp_Pnt(end[0], end[1], 0.0)
            ).Edge()
        if segment.kind == "circle":
            center, radius = sketch.solver.circle_geometry(segment.handle)
            circle = gp_Circ(gp_Ax2(gp_Pnt(center[0], center[1], 0.0), gp_Dir(0, 0, 1)), radius)
            return BRepBuilderAPI_MakeEdge(circle).Edge()
        arc = sketch.solver.arc_geometry(segment.handle)
        circle = gp_Circ(
            gp_Ax2(gp_Pnt(arc.center[0], arc.center[1], 0.0), gp_Dir(0, 0, 1)), arc.radius
        )
        # sense=True — обход против часовой стрелки, то же соглашение, что и
        # в решателе. Иначе дуга уходит «длинной стороной».
        return BRepBuilderAPI_MakeEdge(
            GC_MakeArcOfCircle(circle, arc.start_angle, arc.end_angle, True).Value()
        ).Edge()

    def sample(segment):
        if segment.kind == "line":
            return [sketch.coordinates(point) for point in segment.points]
        arc = sketch.solver.arc_geometry(segment.handle)
        sweep = geom2d.arc_sweep(arc.start_angle, arc.end_angle)
        steps = max(2, int(CIRCLE_FACETS * sweep / geom2d.TAU))
        return [
            geom2d.point_at_angle(arc.center, arc.radius, arc.start_angle + sweep * i / steps)
            for i in range(steps + 1)
        ]

    open_segments = [s for s in sketch.segments if not s.construction and s.kind != "circle"]
    circles = [s for s in sketch.segments if not s.construction and s.kind == "circle"]
    if not open_segments and not circles:
        raise ProfileError(f"{sketch.name}: нет геометрии для контура")

    chains = _loops(open_segments, lambda s: (s.points[-2].id, s.points[-1].id))

    contours = []  # (площадь, ломаная, набор рёбер)
    for chain in chains:
        outline = _sample_loop(chain, sample)
        contours.append((abs(geom2d.signed_area(outline)), outline, [edge_of(s) for s in chain]))
    for circle in circles:
        center, radius = sketch.solver.circle_geometry(circle.handle)
        outline = [
            geom2d.point_at_angle(center, radius, geom2d.TAU * i / CIRCLE_FACETS)
            for i in range(CIRCLE_FACETS)
        ]
        contours.append((math.pi * radius * radius, outline, [edge_of(circle)]))

    if not contours:
        raise ProfileError(
            f"{sketch.name}: контур не замкнут — проверьте стыковку концов"
        )

    contours.sort(key=lambda item: item[0], reverse=True)
    parents = _nesting(contours)

    def wire_of(edges):
        builder = BRepBuilderAPI_MakeWire()
        for edge in edges:
            if edge is not None:
                builder.Add(edge)
        if not builder.IsDone():
            raise ProfileError(f"{sketch.name}: контур не собрался в ядре")
        return builder.Wire()

    from OCP.TopoDS import TopoDS

    faces = []
    for index, (_, _, edges) in enumerate(contours):
        if _depth(parents, index) % 2:
            continue  # нечётная глубина — это отверстие, а не грань
        face_builder = BRepBuilderAPI_MakeFace(wire_of(edges))
        for hole in range(len(contours)):
            if parents[hole] != index:
                continue
            # Внутренний контур добавляется развёрнутым — так ядро понимает,
            # что это отверстие, а не вторая грань поверх первой. Reversed()
            # отдаёт TopoDS_Shape, и без обратного приведения к Wire грань
            # его не принимает.
            face_builder.Add(TopoDS.Wire_s(wire_of(contours[hole][2]).Reversed()))
        if not face_builder.IsDone():
            raise ProfileError(f"{sketch.name}: грань не построилась")
        faces.append(face_builder.Face())

    if not faces:
        raise ProfileError(f"{sketch.name}: наружного контура нет")
    # Профиль строится в плоских координатах и лишь затем переносится на
    # свою плоскость. Строить сразу в пространстве было бы можно, но тогда
    # каждое пересечение и каждая площадь в `tools.py` считались бы в
    # трёхмерии ради результата, который заведомо плоский.
    if len(faces) == 1:
        return sketch.plane.place(faces[0])
    return sketch.plane.place(_compound(faces))


def _compound(shapes):
    """Несколько граней одним объектом ядра."""
    from OCP.BRep import BRep_Builder
    from OCP.TopoDS import TopoDS_Compound

    compound = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(compound)
    for shape in shapes:
        builder.Add(compound, shape)
    return compound


def _nesting(contours) -> list[int]:
    """Для каждого контура — номер ближайшего охватывающего, или -1.

    Контуры уже упорядочены по убыванию площади, поэтому охватывающий
    всегда стоит раньше охватываемого, и достаточно одного прохода назад.
    """
    parents = []
    for index, (_, outline, _) in enumerate(contours):
        probe = outline[0]
        found = -1
        # Ближайший — значит последний из подходящих: он самый мелкий, а
        # значит внутренний по отношению к остальным охватывающим.
        for candidate in range(index):
            if _inside(probe, contours[candidate][1]):
                found = candidate
        parents.append(found)
    return parents


def _depth(parents: list[int], index: int) -> int:
    depth = 0
    while parents[index] >= 0:
        index = parents[index]
        depth += 1
    return depth


def _inside(point, outline) -> bool:
    """Лежит ли точка внутри замкнутой ломаной. Луч вправо, чётность."""
    x, y = point
    inside = False
    count = len(outline)
    for index in range(count):
        x1, y1 = outline[index]
        x2, y2 = outline[(index + 1) % count]
        if (y1 > y) != (y2 > y):
            crossing = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if crossing > x:
                inside = not inside
    return inside


def build_wire(sketch):
    """Наружный контур одной проволокой — для операций по траектории."""
    from OCP.BRepTools import BRepTools_WireExplorer  # noqa: F401  (проверка доступности)
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_WIRE
    from OCP.TopoDS import TopoDS

    explorer = TopExp_Explorer(build_face(sketch), TopAbs_WIRE)
    if not explorer.More():
        raise ProfileError(f"{sketch.name}: контур не найден")
    return TopoDS.Wire_s(explorer.Current())


# --- профиль из выбранных областей -----------------------------------------


def build_regions(sketch, regions):
    """Грань (или несколько) по выбранным областям эскиза.

    Область хранит КУСКИ исходных кривых, поэтому грань строится точной:
    дуга остаётся дугой, а не ломаной из девяноста шести звеньев, по
    которой её опознавали при разбиении.

    Выбранные области объединяются в плоскости (§5.3 спецификации):
    кольцо плюс вложенный в него круг дают сплошной прямоугольник, а не
    прямоугольник с отверстием и отдельный цилиндр внутри него.
    """
    if not regions:
        raise ProfileError(f"{sketch.name}: не выбрано ни одной области")
    faces = [_region_face(sketch, region) for region in regions]
    joined = _fuse(faces) if len(faces) > 1 else faces[0]
    return sketch.plane.place(joined)


def _region_face(sketch, region):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
    from OCP.TopoDS import TopoDS

    builder = BRepBuilderAPI_MakeFace(_loop_wire(sketch, region.outer))
    for hole in region.inner:
        builder.Add(TopoDS.Wire_s(_loop_wire(sketch, hole).Reversed()))
    if not builder.IsDone():
        raise ProfileError(f"{sketch.name}: область не собралась в грань")
    return builder.Face()


def _loop_wire(sketch, loop):
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeWire

    builder = BRepBuilderAPI_MakeWire()
    for piece in loop.pieces:
        edge = _piece_edge(sketch, piece)
        if edge is not None:
            builder.Add(edge)
    if not builder.IsDone():
        raise ProfileError(f"{sketch.name}: граница области не замкнулась")
    return builder.Wire()


def _piece_edge(sketch, piece):
    """Кусок кривой → точное ребро ядра.

    Дуга строится ПО ТРЁМ ТОЧКАМ, а не по паре углов. Углы пришлось бы
    отсчитывать от собственной оси окружности ядра, а она задаётся не
    нами; при построении по трём точкам направление обхода определено
    самой средней точкой, и ошибиться ветвью невозможно. Точки берутся с
    истинной окружности по долям параметра, а не с ломаной разбиения, —
    ломаная лежит на хордах и дала бы дугу чуть меньшего радиуса.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeEdge
    from OCP.GC import GC_MakeArcOfCircle
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt

    segment = piece.segment
    if segment.kind == "line":
        if geom2d.distance(piece.start, piece.end) < 1e-9:
            return None
        return BRepBuilderAPI_MakeEdge(
            gp_Pnt(piece.start[0], piece.start[1], 0.0),
            gp_Pnt(piece.end[0], piece.end[1], 0.0),
        ).Edge()

    if segment.kind == "circle":
        center, radius = sketch.circle_geometry(segment)
        base, span = 0.0, geom2d.TAU
    else:
        arc = sketch.arc_geometry(segment)
        center, radius = arc.center, arc.radius
        base = arc.start_angle
        span = geom2d.arc_sweep(arc.start_angle, arc.end_angle)

    whole = piece.t_start <= 1e-12 and piece.t_end >= 1.0 - 1e-12
    if whole and segment.kind == "circle":
        circle = gp_Circ(
            gp_Ax2(gp_Pnt(center[0], center[1], 0.0), gp_Dir(0, 0, 1)), radius)
        return BRepBuilderAPI_MakeEdge(circle).Edge()

    if piece.t_end - piece.t_start < 1e-12:
        return None
    angles = [base + span * value for value in (
        piece.t_start, (piece.t_start + piece.t_end) / 2.0, piece.t_end)]
    first, middle, last = (
        geom2d.point_at_angle(center, radius, angle) for angle in angles)
    return BRepBuilderAPI_MakeEdge(
        GC_MakeArcOfCircle(
            gp_Pnt(first[0], first[1], 0.0),
            gp_Pnt(middle[0], middle[1], 0.0),
            gp_Pnt(last[0], last[1], 0.0),
        ).Value()
    ).Edge()


def _fuse(faces):
    """Объединить грани в плоскости.

    Ядро объединяет их как плоские тела: соседние области сливаются в одну
    грань, а разнесённые остаются отдельными. Именно это и нужно — выбор
    кольца и круга внутри него обязан дать сплошной прямоугольник.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Fuse
    from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
    from OCP.TopTools import TopTools_ListOfShape

    arguments = TopTools_ListOfShape()
    arguments.Append(faces[0])
    tools = TopTools_ListOfShape()
    for face in faces[1:]:
        tools.Append(face)
    algorithm = BRepAlgoAPI_Fuse()
    algorithm.SetArguments(arguments)
    algorithm.SetTools(tools)
    algorithm.Build()
    if not algorithm.IsDone():
        raise ProfileError("области не объединились")
    result = algorithm.Shape()
    # Без этого на месте бывшей границы остаётся лишнее ребро, и грань,
    # которая должна быть одной, состоит из двух половин.
    unify = ShapeUpgrade_UnifySameDomain(result, True, True, False)
    unify.Build()
    return unify.Shape()
