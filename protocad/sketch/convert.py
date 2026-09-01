"""Перенос рёбер детали в эскиз.

Рисовать заново контур, который уже есть на детали, — работа, которой в
CAD быть не должно: обвести грань от руки значит получить размеры «почти
такие же», а потом искать, откуда взялась щель в десятую миллиметра.

Ребро переносится **проекцией на плоскость эскиза**, а не копированием
координат. Для ребра, лежащего в этой плоскости, разницы нет; для
стороннего — проекция и есть то, что нужно: на чертеже так же.

Что переносится точно:

* отрезок — отрезком;
* окружность и дуга, чья плоскость параллельна плоскости эскиза, — своей
  окружностью или дугой;
* эллипс и его дуга на такой же плоскости — своим эллипсом: наклонный
  разрез цилиндра приходит именно им, и по ломаной из тридцати двух
  звеньев диаметр отверстия не поставить;
* всё остальное — ломаной по разбиению. Сюда же попадает НАКЛОННАЯ
  окружность или эллипс: в проекции на плоскость эскиза это другой эллипс,
  и строить его как исходный значило бы соврать о размере.

Ломаная честнее отказа: контур получается, а то, что он приближённый,
сказано вызывающему в отчёте, а не спрятано.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import geom2d


@dataclass
class ConvertResult:
    created: list = field(default_factory=list)
    approximated: int = 0     # рёбер, перенесённых ломаной
    skipped: int = 0          # рёбер, из которых ничего не вышло

    @property
    def note(self) -> str:
        parts = []
        if self.approximated:
            parts.append(f"ломаной перенесено рёбер: {self.approximated}")
        if self.skipped:
            parts.append(f"пропущено: {self.skipped}")
        return "; ".join(parts)


PARALLEL = 1e-6           # допуск на параллельность плоскостей
DISCRETIZE = 0.05         # шаг разбиения для ломаной, мм


#: точность, с которой концы соседних рёбер считаются одной точкой
JOIN = 6


class _Points:
    """Реестр точек переноса.

    Соседние рёбра детали сходятся в общей вершине. Если каждое ребро
    получит СВОИ концы, контур в эскизе не замкнётся: сборка профиля ищет
    циклы по тождеству точек, а не по близости координат, и обведённая
    грань даёт вместо пластины одно отверстие в ней. Замечается это только
    по площади — на экране контур выглядит сомкнутым.
    """

    def __init__(self, sketch, construction: bool):
        self.sketch = sketch
        self.construction = construction
        self._known: dict[tuple, object] = {}

    def get(self, position, construction=None):
        key = (round(position[0], JOIN), round(position[1], JOIN))
        found = self._known.get(key)
        if found is not None:
            return found
        is_construction = (
            self.construction if construction is None else construction
        )
        point = self.sketch.point(*position, construction=is_construction)
        self._known[key] = point
        return point


def from_edges(sketch, edges, construction: bool = False) -> ConvertResult:
    """Перенести рёбра детали в эскиз проекцией на его плоскость."""
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line

    result = ConvertResult()
    plane = sketch.plane
    points = _Points(sketch, construction)
    for edge in edges:
        curve = BRepAdaptor_Curve(edge)
        kind = curve.GetType()
        try:
            if kind == GeomAbs_Line:
                result.created.append(_line(sketch, curve, points, construction))
            elif kind == GeomAbs_Circle and _coplanar(plane, curve):
                result.created.append(_circular(sketch, curve, points, construction))
            else:
                result.created.extend(_polyline(sketch, edge, points, construction))
                result.approximated += 1
        except Exception:  # noqa: BLE001 — ребро могло выродиться в точку
            result.skipped += 1
    return result


def from_entities(sketch, entities, construction: bool = False) -> ConvertResult:
    """То же, но по ОПИСАНИЮ рёбер, пришедшему от движка.

    Окно не разбирает формы ядра (`docs/08_ENGINE_BACKEND.md`, §10.2), а
    описание содержит всё нужное: вид кривой, концы, для окружности —
    центр, ось и радиус, для эллипса — ещё полуоси и направление большой,
    для остальных — разбиение. Строится по нему то же самое, что и по
    форме: расхождение здесь означало бы, что «Преобразовать ребро» даёт
    разный контур в зависимости от того, кто считал деталь.
    """
    result = ConvertResult()
    plane = sketch.plane
    points = _Points(sketch, construction)
    for entity in entities:
        data = getattr(entity, "data", entity) or {}
        kind = data.get("curve")
        try:
            if kind == "line":
                result.created.append(_line_of(sketch, data, points, construction))
            elif kind == "circle" and _coplanar_normal(plane, data.get("normal")):
                result.created.append(
                    _circular_of(sketch, data, points, construction))
            elif (kind == "ellipse" and data.get("major_radius")
                    and _coplanar_normal(plane, data.get("normal"))):
                result.created.append(
                    _ellipse_of(sketch, data, points, construction))
            else:
                result.created.extend(
                    _polyline_of(sketch, data, points, construction))
                result.approximated += 1
        except Exception:  # noqa: BLE001 — ребро могло выродиться в точку
            result.skipped += 1
    return result


def _line_of(sketch, data, points: "_Points", construction: bool):
    plane = sketch.plane
    a = plane.project(tuple(data["start"]))
    b = plane.project(tuple(data["end"]))
    if geom2d.distance(a, b) < 1e-9:
        raise ValueError("ребро выродилось в точку")
    return sketch.line(points.get(a), points.get(b), construction=construction)


def _circular_of(sketch, data, points: "_Points", construction: bool):
    plane = sketch.plane
    center = plane.project(tuple(data["center"]))
    radius = float(data["radius"])
    first = plane.project(tuple(data["start"]))
    last = plane.project(tuple(data["end"]))
    if geom2d.distance(first, last) < 1e-9:
        return sketch.circle(
            points.get(center, construction=True), radius,
            construction=construction)
    # Дуга: сторону обхода задаёт точка на её середине. Порядок концов
    # проекция переворачивает, а середина остаётся серединой.
    sampled = data.get("points") or []
    if len(sampled) < 3:
        raise ValueError("дуга пришла без разбиения")
    middle = plane.project(tuple(sampled[len(sampled) // 2]))
    if not geom2d.arc_contains(
        geom2d.angle_at(center, first), geom2d.angle_at(center, last),
        geom2d.angle_at(center, middle),
    ):
        first, last = last, first
    return sketch.arc(
        points.get(center, construction=True),
        points.get(first), points.get(last),
        construction=construction,
    )


def _ellipse_of(sketch, data, points: "_Points", construction: bool):
    """Эллипс целиком или его дуга — по центру, полуосям и большой оси.

    Наклонный разрез цилиндрического отверстия — ровно этот случай, и
    приходит он эллипсом, а не окружностью. Ломаная вместо него выглядит
    так же, но размер по ней не поставить: у звена нет ни центра, ни
    полуоси, а значит нечего и подписывать.
    """
    plane = sketch.plane
    center = plane.project(tuple(data["center"]))
    major = float(data["major_radius"])
    minor = float(data["minor_radius"])
    # Направление большой оси переводится в плоскость БЕЗ начала: `project`
    # вычитает origin, и для вектора это дало бы точку, а не направление.
    axis = tuple(float(value) for value in data["major_axis"])
    angle = math.atan2(
        sum(axis[i] * plane.y_direction[i] for i in range(3)),
        sum(axis[i] * plane.x_direction[i] for i in range(3)))
    if major < minor:
        major, minor = minor, major
        angle += math.pi / 2.0
    if minor <= 0.0:
        raise ValueError("эллипс пришёл с нулевой полуосью")
    cos, sin = math.cos(angle), math.sin(angle)
    gap = math.sqrt(max(major * major - minor * minor, 0.0))

    first = plane.project(tuple(data["start"]))
    last = plane.project(tuple(data["end"]))
    if gap < 1e-9:
        # Полуоси сошлись — это окружность, как её ни назови. Эллипс с
        # фокусом в центре решатель принять не может, а терять ребро из-за
        # вырождения нельзя: окружность здесь не приближение, а тот же
        # самый ответ.
        return _circular_of(
            sketch, dict(data, radius=major), points, construction)

    # Отказ — ДО того, как заведены точки: точка, созданная перед отказом,
    # остаётся в эскизе ни к чему не привязанной, а ребро уходит в
    # пропущенные. Так же устроен и `_circular_of`.
    closed = geom2d.distance(first, last) < 1e-9
    sampled = data.get("points") or []
    if not closed and len(sampled) < 3:
        raise ValueError("дуга эллипса пришла без разбиения")

    # Фокус заводится СВОЕЙ точкой, а не из реестра: реестр сводит
    # совпавшие координаты в одну точку, и фокус, попавший на вершину
    # контура, связал бы эллипс с чужим углом — тянешь угол, едет эллипс.
    # Центр наоборот берётся из реестра: совпасть ему положено с центром
    # соосного отверстия, и это как раз то, чего от переноса ждут.
    centre_point = points.get(center, construction=True)
    focus_point = sketch.point(center[0] + gap * cos, center[1] + gap * sin,
                               construction=True)
    if closed:
        return sketch.ellipse(centre_point, focus_point, minor,
                              construction=construction)
    # Дуга: сторону обхода задаёт точка на её середине — так же, как у дуги
    # окружности. Углы СОБСТВЕННЫЕ, параметрические: у эллипса точка «под
    # 45°» лежит не там, где параметр 45°.
    middle = plane.project(tuple(sampled[len(sampled) // 2]))
    start_angle = _ellipse_angle(center, cos, sin, major, minor, first)
    end_angle = _ellipse_angle(center, cos, sin, major, minor, last)
    if not geom2d.arc_contains(
        start_angle, end_angle,
        _ellipse_angle(center, cos, sin, major, minor, middle),
    ):
        first, last = last, first
        start_angle, end_angle = end_angle, start_angle
    return sketch.ellipse_arc(
        centre_point, focus_point, minor, start_angle, end_angle,
        points.get(first), points.get(last), construction=construction,
    )


def _ellipse_angle(center, cos: float, sin: float, major: float,
                   minor: float, point) -> float:
    """Собственный параметр эллипса в точке.

    Точка при ``t`` — это ``center + a·cos t·u + b·sin t·v``, где ``u``
    вдоль большой оси, ``v`` повёрнут от неё на 90° против часовой. Обратно
    параметр берётся тем же разложением, поделённым на полуоси.
    """
    dx = point[0] - center[0]
    dy = point[1] - center[1]
    return math.atan2((-dx * sin + dy * cos) / minor,
                      (dx * cos + dy * sin) / major)


def _polyline_of(sketch, data, points: "_Points", construction: bool) -> list:
    sampled = data.get("points") or [data.get("start"), data.get("end")]
    return _stitch(sketch, sampled, points, construction)


def _coplanar_normal(plane, normal) -> bool:
    """Параллельна ли плоскость окружности плоскости эскиза."""
    if not normal:
        return False
    dot = abs(sum(normal[i] * plane.normal[i] for i in range(3)))
    return abs(dot - 1.0) < PARALLEL


def _coplanar(plane, curve) -> bool:
    """Лежит ли окружность в плоскости, параллельной плоскости эскиза."""
    axis = curve.Circle().Axis().Direction()
    dot = abs(
        axis.X() * plane.normal[0]
        + axis.Y() * plane.normal[1]
        + axis.Z() * plane.normal[2]
    )
    return abs(dot - 1.0) < PARALLEL


def _line(sketch, curve, points: "_Points", construction: bool):
    plane = sketch.plane
    start = curve.Value(curve.FirstParameter())
    end = curve.Value(curve.LastParameter())
    a = plane.project((start.X(), start.Y(), start.Z()))
    b = plane.project((end.X(), end.Y(), end.Z()))
    if geom2d.distance(a, b) < 1e-9:
        raise ValueError("ребро выродилось в точку")
    return sketch.line(points.get(a), points.get(b), construction=construction)


def _circular(sketch, curve, points: "_Points", construction: bool):
    """Окружность целиком или дуга — по диапазону параметра."""
    plane = sketch.plane
    circle = curve.Circle()
    location = circle.Location()
    center = plane.project((location.X(), location.Y(), location.Z()))
    radius = circle.Radius()
    span = curve.LastParameter() - curve.FirstParameter()
    if abs(span - 2.0 * math.pi) < 1e-6:
        return sketch.circle(
            points.get(center, construction=True), radius, construction=construction
        )
    start = curve.Value(curve.FirstParameter())
    end = curve.Value(curve.LastParameter())
    first = plane.project((start.X(), start.Y(), start.Z()))
    last = plane.project((end.X(), end.Y(), end.Z()))
    # Направление обхода дуги в эскизе — всегда против часовой. Проекция
    # может его перевернуть, поэтому концы выбираются по средней точке, а
    # не по порядку параметра.
    middle_point = curve.Value(
        (curve.FirstParameter() + curve.LastParameter()) / 2.0
    )
    middle = plane.project(
        (middle_point.X(), middle_point.Y(), middle_point.Z())
    )
    if not geom2d.arc_contains(
        geom2d.angle_at(center, first), geom2d.angle_at(center, last),
        geom2d.angle_at(center, middle),
    ):
        first, last = last, first
    return sketch.arc(
        points.get(center, construction=True),
        points.get(first), points.get(last),
        construction=construction,
    )


def _polyline(sketch, edge, points: "_Points", construction: bool) -> list:
    from .. import kernel

    return _stitch(sketch, kernel.discretize(edge, DISCRETIZE), points,
                   construction)


def _stitch(sketch, sampled, points: "_Points", construction: bool) -> list:
    """Ломаная по разбиению — общая часть обоих путей переноса."""
    plane = sketch.plane
    flat = [plane.project(tuple(point)) for point in sampled if point]
    if not flat:
        raise ValueError("ребро пришло без разбиения")
    # Подряд идущие совпавшие точки убираются: вырожденный отрезок ядро не
    # примет, а решателю он добавит степень свободы ни за что.
    cleaned = [flat[0]]
    for point in flat[1:]:
        if geom2d.distance(cleaned[-1], point) > 1e-7:
            cleaned.append(point)
    if len(cleaned) < 2:
        raise ValueError("ребро выродилось в точку")
    nodes = [points.get(position) for position in cleaned]
    return [
        sketch.line(nodes[index], nodes[index + 1], construction=construction)
        for index in range(len(nodes) - 1)
    ]
