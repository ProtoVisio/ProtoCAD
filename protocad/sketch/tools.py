"""Инструменты правки эскиза: отсечение, продление, смещение, массивы.

Все они меняют состав эскиза, а не только координаты, и потому устроены
одинаково: сначала считается новая геометрия по числам, затем эскиз
перестраивается, затем восстанавливаются связи, которые ещё имеют смысл.

Про потерю связей — честно. Отсечь отрезок значит заменить его другим
отрезком: у него другая длина и другие концы. Односторонние связи
(горизонталь, вертикаль) переносятся на новый объект автоматически, парные
(параллельность, равенство, касание) — тоже, если второй участник уцелел.
Размеры, привязанные к исчезнувшему концу, восстановить нельзя, и они
снимаются. Это сообщается вызывающему в :class:`EditResult`, чтобы
интерфейс мог сказать об этом человеку, а не делать вид, что ничего не
произошло.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import Point, geom2d
from .solver import ArcGeometry

TOLERANCE = 1e-7


class ToolError(RuntimeError):
    """Операция неприменима к тому, что выбрано."""


@dataclass
class EditResult:
    """Что получилось и чем за это заплатили."""

    created: list = field(default_factory=list)
    removed: list = field(default_factory=list)
    lost_relations: list[str] = field(default_factory=list)
    #: Объекты, которые в расчёт НЕ взяты: их пересечения считать пока
    #: нечем. Молчать нельзя — отсечение прошло бы мимо такого объекта, и
    #: выглядело бы это как «инструмент его не заметил».
    skipped: list[str] = field(default_factory=list)
    #: На сколько миллиметров результат разошёлся с запрошенным. Больше
    #: нуля означает, что связи не дали выполнить преобразование целиком:
    #: повернуть прямоугольник, стороны которого объявлены горизонтальными,
    #: нельзя — решатель оставит их горизонтальными и вместо поворота
    #: растянет. Молчать об этом нельзя: команда отчиталась бы успехом,
    #: сделав не то, о чём просили.
    deviation: float = 0.0

    @property
    def note(self) -> str:
        parts = []
        if self.lost_relations:
            parts.append("сняты связи: "
                         + ", ".join(sorted(set(self.lost_relations))))
        if self.deviation > 1e-6:
            parts.append(
                f"связи не дали выполнить целиком: расхождение "
                f"{self.deviation:.3f} мм")
        if self.skipped:
            names = ", ".join(sorted({KIND_NAMES.get(kind, kind)
                                      for kind in self.skipped}))
            parts.append(f"не учтено — пересечения пока не считаются: {names}")
        return "; ".join(parts)


# --- описание геометрии в числах ---


@dataclass
class Shape2D:
    """Геометрия объекта в координатах — то, с чем работает математика."""

    kind: str
    start: tuple = (0.0, 0.0)
    end: tuple = (0.0, 0.0)
    center: tuple = (0.0, 0.0)
    radius: float = 0.0
    start_angle: float = 0.0
    end_angle: float = 0.0
    #: Эллипс: ``radius`` — БОЛЬШАЯ полуось, ``minor`` — малая,
    #: ``rotation`` — наклон большой оси в радианах. У остальных видов не
    #: используется, поэтому и лежит отдельными полями, а не в подтипе:
    #: круг — это эллипс с равными полуосями, и разводить их значило бы
    #: держать две математики там, где хватает одной.
    minor: float = 0.0
    rotation: float = 0.0
    #: Сплайн: полюсы, узлы, кратности и степень — то же, чем его держат и
    #: решатель, и ядро. Своей математики у него больше, чем у остальных,
    #: и живёт она в ``bspline.py``.
    poles: tuple = ()
    knots: tuple = ()
    mults: tuple = ()
    degree: int = 0


def describe(sketch, segment) -> Shape2D:
    if segment.kind == "line":
        start, end = (sketch.coordinates(point) for point in segment.points)
        return Shape2D("line", start=start, end=end)
    if segment.kind == "circle":
        center, radius = sketch.circle_geometry(segment)
        return Shape2D("circle", center=center, radius=radius,
                       start_angle=0.0, end_angle=geom2d.TAU)
    if segment.kind == "spline":
        geometry = sketch.spline_geometry(segment)
        return Shape2D("spline", poles=geometry.poles, knots=geometry.knots,
                       mults=geometry.mults, degree=geometry.degree,
                       start=geometry.poles[0], end=geometry.poles[-1])
    if segment.kind == "hyperbola_arc":
        geometry = sketch.hyperbola_geometry(segment)
        shape = Shape2D("hyperbola_arc", center=geometry.center,
                        radius=geometry.major, minor=geometry.minor,
                        rotation=geometry.rotation,
                        start_angle=geometry.start_angle,
                        end_angle=geometry.end_angle)
        shape.start = hyperbola_point(shape, shape.start_angle)
        shape.end = hyperbola_point(shape, shape.end_angle)
        return shape
    if segment.kind == "parabola_arc":
        geometry = sketch.parabola_geometry(segment)
        shape = Shape2D("parabola_arc", center=geometry.vertex,
                        radius=geometry.focal, rotation=geometry.rotation,
                        start_angle=geometry.start_angle,
                        end_angle=geometry.end_angle)
        # У параболы «center» — это ВЕРШИНА, а «radius» — расстояние до
        # фокуса. Заводить под них отдельные поля незачем: смысл в
        # описании, а не в названии, и лишние поля пришлось бы протаскивать
        # через каждый инструмент.
        shape.start = parabola_point(shape, shape.start_angle)
        shape.end = parabola_point(shape, shape.end_angle)
        return shape
    if segment.kind in ("ellipse", "ellipse_arc"):
        geometry = sketch.ellipse_geometry(segment)
        shape = Shape2D(segment.kind, center=geometry.center,
                        radius=geometry.major, minor=geometry.minor,
                        rotation=geometry.rotation,
                        start_angle=geometry.start_angle,
                        end_angle=geometry.end_angle)
        if segment.kind == "ellipse_arc":
            shape.start = ellipse_point(shape, shape.start_angle)
            shape.end = ellipse_point(shape, shape.end_angle)
        return shape
    arc: ArcGeometry = sketch.arc_geometry(segment)
    return Shape2D(
        "arc",
        start=geom2d.point_at_angle(arc.center, arc.radius, arc.start_angle),
        end=geom2d.point_at_angle(arc.center, arc.radius, arc.end_angle),
        center=arc.center,
        radius=arc.radius,
        start_angle=arc.start_angle,
        end_angle=arc.end_angle,
    )


#: Кривые, у которых нет короткой формулы пересечения: с окружностью и
#: друг с другом они дают уравнение четвёртой степени и выше. Их
#: пересечения находятся по ломаным и уточняются Ньютоном.
HARD = ("ellipse", "ellipse_arc", "spline", "parabola_arc",
        "hyperbola_arc")

#: Названия для отказов — там, где инструмент кривую ещё не берёт.
#: Два падежа, потому что и мест два: «отсечение ПО сплайну» и «не учтён
#: сплайн». Склонять на ходу — верный способ получить косноязычие в том
#: единственном сообщении, ради которого его и читают.
KIND_TITLES = {"ellipse": "эллипсу", "ellipse_arc": "дуге эллипса",
               "parabola_arc": "параболе",
               "hyperbola_arc": "гиперболе",
               "spline": "сплайну"}
KIND_NAMES = {"ellipse": "эллипс", "ellipse_arc": "дуга эллипса",
              "parabola_arc": "парабола",
              "hyperbola_arc": "гипербола",
              "spline": "сплайн"}


def _no_ellipse(what: str, *shapes) -> None:
    """Отказ вместо попытки — там, где обработать кривую ещё нечем."""
    for shape in shapes:
        if shape.kind in HARD:
            title = KIND_TITLES.get(shape.kind, shape.kind)
            raise ToolError(f"{what} по {title} пока не делается")


def intersections(a: Shape2D, b: Shape2D) -> list[tuple]:
    """Точки пересечения двух объектов, уже отфильтрованные по их протяжённости.

    Бесконечные прямые и полные окружности здесь ни при чём: отсечение
    работает по тому, что нарисовано, а не по тому, что могло бы быть.
    """
    if a.kind in HARD or b.kind in HARD:
        # Сплайн теперь режется вставкой узлов, поэтому пересечения по нему
        # отдаются наравне с остальными: нашлись — есть чем отсечь.
        return [point for point in refined_crossings(a, b)
                if _on_curve(a, point) and _on_curve(b, point)]
    result = []
    if a.kind == "line" and b.kind == "line":
        crossing = geom2d.line_line(a.start, a.end, b.start, b.end)
        if crossing and -TOLERANCE <= crossing[1] <= 1 + TOLERANCE \
                and -TOLERANCE <= crossing[2] <= 1 + TOLERANCE:
            result.append(crossing[0])
    elif a.kind == "line":
        for point, t in geom2d.line_circle(a.start, a.end, b.center, b.radius):
            if -TOLERANCE <= t <= 1 + TOLERANCE and _on_curve(b, point):
                result.append(point)
    elif b.kind == "line":
        return intersections(b, a)
    else:
        for point in geom2d.circle_circle(a.center, a.radius, b.center, b.radius):
            if _on_curve(a, point) and _on_curve(b, point):
                result.append(point)
    return result


def ellipse_point(shape: Shape2D, angle: float) -> tuple:
    """Точка эллипса по ПАРАМЕТРИЧЕСКОМУ углу.

    Угол здесь не полярный: точка равна ``C + a·cos t·u + b·sin t·v``, где
    ``u`` и ``v`` — оси эллипса. Полярный угол с этим совпадает только у
    круга, и путать их нельзя: по полярному эллипс рисуется неравномерно и
    в узких местах разваливается на ломаной.
    """
    cos_turn, sin_turn = math.cos(shape.rotation), math.sin(shape.rotation)
    along = shape.radius * math.cos(angle)
    across = shape.minor * math.sin(angle)
    return (shape.center[0] + along * cos_turn - across * sin_turn,
            shape.center[1] + along * sin_turn + across * cos_turn)


def ellipse_angle(shape: Shape2D, point) -> float:
    """Собственный угол эллипса, отвечающий точке.

    Обратное к :func:`ellipse_point`. С полярным углом совпадает только на
    осях: точка под полярными 45° лежит на эллипсе при другом собственном
    угле, и путать их — значит резать не там, куда показали.
    """
    dx = point[0] - shape.center[0]
    dy = point[1] - shape.center[1]
    cos_turn, sin_turn = math.cos(shape.rotation), math.sin(shape.rotation)
    along = dx * cos_turn + dy * sin_turn
    across = -dx * sin_turn + dy * cos_turn
    return math.atan2(across / max(shape.minor, 1e-12),
                      along / max(shape.radius, 1e-12))


def hyperbola_point(shape: Shape2D, parameter: float) -> tuple:
    """Точка гиперболы по её параметру: ``(a·ch t, b·sh t)``.

    Параметр ГИПЕРБОЛИЧЕСКИЙ — тот же, что у решателя и у ядра. Проверено
    на обоих: через границу он идёт как есть, переводить нечего.
    """
    along = shape.radius * math.cosh(parameter)
    across = shape.minor * math.sinh(parameter)
    cos, sin = math.cos(shape.rotation), math.sin(shape.rotation)
    return (shape.center[0] + along * cos - across * sin,
            shape.center[1] + along * sin + across * cos)


def hyperbola_parameter(shape: Shape2D, point) -> float:
    """Параметр точки на гиперболе: ``asinh(Y / b)`` в системе центра.

    Считается по ПОПЕРЕЧНОЙ координате, а не по продольной: ``ch`` чётен и
    двух ветвей не различает, а ``sh`` монотонен — по нему параметр
    восстанавливается однозначно.
    """
    cos, sin = math.cos(shape.rotation), math.sin(shape.rotation)
    dx = point[0] - shape.center[0]
    dy = point[1] - shape.center[1]
    across = -dx * sin + dy * cos
    return math.asinh(across / (shape.minor or 1e-9))


def parabola_point(shape: Shape2D, parameter: float) -> tuple:
    """Точка параболы по её параметру.

    Параметр — МЕСТНАЯ КООРДИНАТА поперёк оси: точка при ``t`` это
    ``(t² / 4p, t)`` в системе вершины. Установлено опытом на самом
    решателе: у параболы в ходу несколько разных параметризаций, и
    выбирать «привычную» по памяти — верный способ получить кривую, не
    совпадающую с той, что решает решатель.
    """
    focal = shape.radius or 1e-9
    along, across = parameter * parameter / (4.0 * focal), parameter
    cos, sin = math.cos(shape.rotation), math.sin(shape.rotation)
    return (shape.center[0] + along * cos - across * sin,
            shape.center[1] + along * sin + across * cos)


def parabola_parameter(shape: Shape2D, point) -> float:
    """Параметр точки на параболе: её местная координата поперёк оси."""
    cos, sin = math.cos(shape.rotation), math.sin(shape.rotation)
    dx = point[0] - shape.center[0]
    dy = point[1] - shape.center[1]
    return -dx * sin + dy * cos


def curve_tangent(shape: Shape2D, parameter: float) -> tuple:
    """Производная кривой по её собственному параметру."""
    if shape.kind == "line":
        return (shape.end[0] - shape.start[0], shape.end[1] - shape.start[1])
    if shape.kind == "spline":
        from . import bspline as bspline_module

        return bspline_module.tangent(shape.poles, shape.knots, shape.mults,
                                      shape.degree, parameter)
    if shape.kind == "hyperbola_arc":
        along = shape.radius * math.sinh(parameter)
        across = shape.minor * math.cosh(parameter)
        cos_turn, sin_turn = math.cos(shape.rotation), math.sin(shape.rotation)
        return (along * cos_turn - across * sin_turn,
                along * sin_turn + across * cos_turn)
    if shape.kind == "parabola_arc":
        focal = shape.radius or 1e-9
        along, across = parameter / (2.0 * focal), 1.0
        cos_turn, sin_turn = math.cos(shape.rotation), math.sin(shape.rotation)
        return (along * cos_turn - across * sin_turn,
                along * sin_turn + across * cos_turn)
    if shape.kind in ("ellipse", "ellipse_arc"):
        cos_turn, sin_turn = math.cos(shape.rotation), math.sin(shape.rotation)
        along = -shape.radius * math.sin(parameter)
        across = shape.minor * math.cos(parameter)
        return (along * cos_turn - across * sin_turn,
                along * sin_turn + across * cos_turn)
    return (-shape.radius * math.sin(parameter),
            shape.radius * math.cos(parameter))


def curve_value(shape: Shape2D, parameter: float) -> tuple:
    """Точка кривой по её собственному параметру."""
    if shape.kind == "line":
        return (shape.start[0] + (shape.end[0] - shape.start[0]) * parameter,
                shape.start[1] + (shape.end[1] - shape.start[1]) * parameter)
    if shape.kind == "spline":
        from . import bspline as bspline_module

        return bspline_module.value(shape.poles, shape.knots, shape.mults,
                                    shape.degree, parameter)
    if shape.kind == "hyperbola_arc":
        return hyperbola_point(shape, parameter)
    if shape.kind == "parabola_arc":
        return parabola_point(shape, parameter)
    if shape.kind in ("ellipse", "ellipse_arc"):
        return ellipse_point(shape, parameter)
    return geom2d.point_at_angle(shape.center, shape.radius, parameter)


def curve_parameter(shape: Shape2D, point) -> float:
    """Собственный параметр кривой в точке. Приближённо у сплайна."""
    if shape.kind == "line":
        return geom2d.project_on_line(point, shape.start, shape.end)[1]
    if shape.kind == "spline":
        outline = curve_outline(shape)
        best = min(range(len(outline)),
                   key=lambda index: geom2d.distance(outline[index], point))
        return best / (len(outline) - 1)
    if shape.kind == "hyperbola_arc":
        return hyperbola_parameter(shape, point)
    if shape.kind == "parabola_arc":
        return parabola_parameter(shape, point)
    if shape.kind in ("ellipse", "ellipse_arc"):
        return ellipse_angle(shape, point)
    return geom2d.angle_at(shape.center, point)


#: Сколько шагов Ньютона отводится на уточнение одного пересечения.
REFINE_STEPS = 8


def refined_crossings(a: Shape2D, b: Shape2D) -> list:
    """Пересечения кривых: по ломаным, затем уточнение по самим кривым.

    Точной формулы у эллипса и сплайна нет — с окружностью и друг с другом
    они дают уравнение четвёртой степени и выше. Положение находится по
    ломаным, а потом решается система «точка первой кривой равна точке
    второй» по двум параметрам. Тысячные доли, которые даёт ломаная, для
    сшивки контура не годятся.
    """
    found = []
    for guess in _polyline_crossings(curve_outline_of(a), curve_outline_of(b)):
        point = _newton_crossing(a, b, guess)
        if point is None:
            continue
        if all(geom2d.distance(point, other) > 1e-6 for other in found):
            found.append(point)
    return found


def curve_outline_of(shape: Shape2D, facets: int = 128) -> list:
    """Ломаная по любому объекту — общий вход для поиска пересечений."""
    if shape.kind == "line":
        return [shape.start, shape.end]
    if shape.kind in ("spline",):
        return curve_outline(shape, facets)
    if shape.kind == "ellipse":
        return ellipse_outline(shape, facets)
    if shape.kind == "hyperbola_arc":
        low, high = shape.start_angle, shape.end_angle
        return [hyperbola_point(shape, low + (high - low) * index / facets)
                for index in range(facets + 1)]
    if shape.kind == "parabola_arc":
        # Параметр у параболы идёт по прямой, а не по кругу: и дробить его
        # надо равномерно, без всяких развёрток угла.
        low, high = shape.start_angle, shape.end_angle
        return [parabola_point(shape, low + (high - low) * index / facets)
                for index in range(facets + 1)]
    if shape.kind == "ellipse_arc":
        sweep = geom2d.arc_sweep(shape.start_angle, shape.end_angle)
        steps = max(2, int(facets * sweep / geom2d.TAU))
        return [ellipse_point(shape, shape.start_angle + sweep * i / steps)
                for i in range(steps + 1)]
    if shape.kind == "circle":
        return [geom2d.point_at_angle(shape.center, shape.radius,
                                      geom2d.TAU * i / facets)
                for i in range(facets + 1)]
    sweep = geom2d.arc_sweep(shape.start_angle, shape.end_angle)
    steps = max(2, int(facets * sweep / geom2d.TAU))
    return [geom2d.point_at_angle(shape.center, shape.radius,
                                  shape.start_angle + sweep * i / steps)
            for i in range(steps + 1)]


def _polyline_crossings(first: list, second: list) -> list:
    found = []
    for a1, a2 in zip(first, first[1:]):
        for b1, b2 in zip(second, second[1:]):
            point = geom2d.line_line(a1, a2, b1, b2)
            if not point:
                continue
            crossing, t, u = point
            if -1e-9 <= t <= 1 + 1e-9 and -1e-9 <= u <= 1 + 1e-9:
                found.append(crossing)
    return found


def _newton_crossing(a: Shape2D, b: Shape2D, guess):
    """Уточнить пересечение двух кривых из приближения."""
    u, v = curve_parameter(a, guess), curve_parameter(b, guess)
    for _ in range(REFINE_STEPS):
        pa, pb = curve_value(a, u), curve_value(b, v)
        fx, fy = pa[0] - pb[0], pa[1] - pb[1]
        if math.hypot(fx, fy) <= 1e-12:
            return pa
        da, db = curve_tangent(a, u), curve_tangent(b, v)
        determinant = da[0] * (-db[1]) - (-db[0]) * da[1]
        if abs(determinant) < 1e-15:
            return guess
        u -= (fx * (-db[1]) - (-db[0]) * fy) / determinant
        v -= (da[0] * fy - fx * da[1]) / determinant
    pa = curve_value(a, u)
    return pa if geom2d.distance(pa, curve_value(b, v)) <= 1e-9 else None


def _on_curve(shape: Shape2D, point) -> bool:
    if shape.kind in ("circle", "ellipse", "spline", "line"):
        return True
    if shape.kind == "hyperbola_arc":
        low, high = sorted((shape.start_angle, shape.end_angle))
        value = hyperbola_parameter(shape, point)
        return low - TOLERANCE <= value <= high + TOLERANCE
    if shape.kind == "parabola_arc":
        # Пределы у параболы — обычный ОТРЕЗОК параметра, и проверка
        # такая же простая: развёртки по кругу здесь нет.
        low, high = sorted((shape.start_angle, shape.end_angle))
        value = parabola_parameter(shape, point)
        return low - TOLERANCE <= value <= high + TOLERANCE
    if shape.kind == "ellipse_arc":
        # Угол СОБСТВЕННЫЙ, не полярный: у эллипса они совпадают только на
        # осях, и по полярному кусок дуги считался бы не тем.
        return geom2d.arc_contains(shape.start_angle, shape.end_angle,
                                   ellipse_angle(shape, point))
    return geom2d.arc_contains(
        shape.start_angle, shape.end_angle, geom2d.angle_at(shape.center, point)
    )


def parameter_of(shape: Shape2D, point) -> float:
    """Положение точки вдоль объекта: 0…1 у отрезка, угол от начала у кривой.

    Единая шкала позволяет сортировать точки реза одинаково для отрезка,
    дуги и окружности и не писать три версии отсечения.
    """
    if shape.kind == "line":
        return geom2d.project_on_line(point, shape.start, shape.end)[1]
    if shape.kind == "spline":
        # У сплайна своя шкала 0…1 — та же, по которой он и задан.
        # Приводить её к углу не к чему: центра у него нет.
        return curve_parameter(shape, point)
    if shape.kind in ("ellipse", "ellipse_arc"):
        return geom2d.normalize_angle(ellipse_angle(shape, point)
                                      - shape.start_angle)
    return geom2d.normalize_angle(geom2d.angle_at(shape.center, point) - shape.start_angle)


def point_at(shape: Shape2D, parameter: float):
    if shape.kind == "line":
        return (
            shape.start[0] + (shape.end[0] - shape.start[0]) * parameter,
            shape.start[1] + (shape.end[1] - shape.start[1]) * parameter,
        )
    if shape.kind in ("ellipse", "ellipse_arc"):
        return ellipse_point(shape, shape.start_angle + parameter)
    return geom2d.point_at_angle(shape.center, shape.radius, shape.start_angle + parameter)


#: На сколько частей разбивается эллипс, когда его надо померить или
#: нарисовать. Точной формулы расстояния до эллипса нет — она приводит к
#: уравнению четвёртой степени, — а для выбора щелчком и для разбора
#: областей ломаной достаточно, если её густота согласована с той, что
#: используется при разборе.
ELLIPSE_FACETS = 128


def ellipse_outline(shape: Shape2D, facets: int = ELLIPSE_FACETS) -> list:
    """Замкнутая ломаная по эллипсу, первая точка повторена в конце."""
    return [ellipse_point(shape, geom2d.TAU * index / facets)
            for index in range(facets + 1)]


def curve_outline(shape: Shape2D, facets: int = ELLIPSE_FACETS) -> list:
    """Ломаная по кривой, у которой нет короткой формулы: эллипс, сплайн."""
    if shape.kind == "spline":
        from . import bspline as bspline_module

        return bspline_module.outline(shape.poles, shape.knots, shape.mults,
                                      shape.degree, facets)
    return ellipse_outline(shape, facets)


def distance_to(sketch, segment, point) -> float:
    """Расстояние от точки до объекта — то, чем выбирают объект щелчком."""
    shape = describe(sketch, segment)
    if shape.kind == "line":
        return geom2d.distance_to_segment(point, shape.start, shape.end)
    if shape.kind == "circle":
        return geom2d.distance_to_circle(point, shape.center, shape.radius)
    if shape.kind in ("ellipse", "spline"):
        outline = curve_outline(shape)
        return min(geom2d.distance_to_segment(point, first, second)
                   for first, second in zip(outline, outline[1:]))
    return geom2d.distance_to_arc(
        point, shape.center, shape.radius, shape.start_angle, shape.end_angle
    )


# --- перестройка объектов с сохранением связей ---


TRANSFERABLE = {
    "horizontal", "vertical", "parallel", "perpendicular", "equal",
    "tangent", "equal_radius", "concentric", "point_on_line", "point_on_curve",
}


def _rebuild_segments(sketch, replacements: dict, result: EditResult,
                      extras: list | None = None) -> dict:
    """Заменить объекты новыми, перенеся на них переносимые связи.

    ``replacements`` — соответствие «старый объект → описание нового» в виде
    ``(kind, координаты…)``. ``extras`` — дополнительные куски, возникшие из
    того же объекта: список пар «описание, исходный объект». Возвращает
    соответствие «старый → новый».

    Порядок важен вдвойне. Связи снимаются вместе со старыми объектами,
    поэтому их запоминают ДО удаления, а накладывают ПОСЛЕ создания. А все
    новые куски создаются тоже ДО удаления: удаление объекта уносит его
    концы, если на них больше никто не опирается, и кусок, созданный
    следом, остался бы без своей точки. Так отсечение середины отрезка
    давало один кусок вместо двух.
    """
    remembered = []
    for old in replacements:
        for relation in sketch.relations_on(old):
            remembered.append((relation.kind, relation.targets))

    created = {}
    for old, recipe in replacements.items():
        created[old] = _make(sketch, recipe, old)
    for recipe, source in (extras or ()):
        result.created.append(_make(sketch, recipe, source))
    sketch.delete(list(replacements))
    result.removed.extend(replacements)
    result.created.extend(created.values())

    for kind, targets in remembered:
        if kind not in TRANSFERABLE:
            if kind in ("anchor", "coincident", "midpoint", "symmetric"):
                result.lost_relations.append(kind)
            continue
        moved = [created.get(target, target) for target in targets]
        if any(_is_gone(sketch, target) for target in moved):
            result.lost_relations.append(kind)
            continue
        try:
            getattr(sketch, kind)(*moved)
        except Exception:  # noqa: BLE001 — связь могла потерять смысл
            result.lost_relations.append(kind)
    return created


def _is_gone(sketch, entity) -> bool:
    return sketch.entity(getattr(entity, "id", -1)) is not entity


def _as_point(sketch, value, construction: bool) -> Point:
    """Координаты превратить в новую точку, готовую точку взять как есть.

    Возможность передать существующую точку — не удобство, а условие
    целостности контура: если перестроенный отрезок получит НОВУЮ точку на
    месте общего угла, соседний отрезок останется висеть на старой, и контур
    развалится ровно там, где на экране всё выглядит сомкнутым.
    """
    if isinstance(value, Point):
        return value
    return sketch.point(*value, construction=construction)


def _make(sketch, recipe, template):
    """Создать объект по рецепту, унаследовав имя и вспомогательность."""
    name, construction = template.name, template.construction
    kind = recipe[0]
    if kind == "line":
        _, start, end = recipe
        return sketch.line(
            _as_point(sketch, start, construction),
            _as_point(sketch, end, construction),
            name, construction,
        )
    if kind == "arc":
        _, center, start, end = recipe
        return sketch.arc(
            _as_point(sketch, center, True),
            _as_point(sketch, start, construction),
            _as_point(sketch, end, construction),
            name, construction,
        )
    if kind == "spline":
        _, poles, knots, mults, degree = recipe
        return sketch.spline(
            [_as_point(sketch, item, True) for item in poles],
            knots, mults, degree, name, construction)
    if kind == "ellipse_arc":
        _, center, major, minor, rotation, first, last, start, end = recipe
        gap = math.sqrt(max(major * major - minor * minor, 0.0))
        focus = (center[0] + gap * math.cos(rotation),
                 center[1] + gap * math.sin(rotation))
        return sketch.ellipse_arc(
            _as_point(sketch, center, True), _as_point(sketch, focus, True),
            minor, first, last, _as_point(sketch, start, construction),
            _as_point(sketch, end, construction), name, construction)
    if kind == "ellipse":
        _, center, major, minor, rotation = recipe
        gap = math.sqrt(max(major * major - minor * minor, 0.0))
        focus = (center[0] + gap * math.cos(rotation),
                 center[1] + gap * math.sin(rotation))
        return sketch.ellipse(
            _as_point(sketch, center, True), _as_point(sketch, focus, True),
            minor, name, construction)
    _, center, radius = recipe
    return sketch.circle(_as_point(sketch, center, True), radius, name, construction)


def _recipe_of(shape: Shape2D):
    if shape.kind == "line":
        return ("line", shape.start, shape.end)
    if shape.kind == "circle":
        return ("circle", shape.center, shape.radius)
    if shape.kind == "spline":
        return ("spline", shape.poles, shape.knots, shape.mults, shape.degree)
    if shape.kind == "ellipse":
        return ("ellipse", shape.center, shape.radius, shape.minor,
                shape.rotation)
    if shape.kind == "ellipse_arc":
        return ("ellipse_arc", shape.center, shape.radius, shape.minor,
                shape.rotation, shape.start_angle, shape.end_angle,
                shape.start, shape.end)
    return ("arc", shape.center, shape.start, shape.end)


def _keep_ends(segment, shape: Shape2D, recipe):
    """Подставить в рецепт СУЩЕСТВУЮЩИЕ точки там, где конец не сдвинулся.

    Сравнение по координатам здесь уместно: речь о том, осталась ли точка на
    прежнем месте, а не о том, какая она из двух.
    """
    closed = ("circle", "ellipse", "spline")
    if segment.kind in closed or recipe[0] in closed:
        return recipe
    old_start, old_end = segment.points[-2], segment.points[-1]
    head = list(recipe)
    if recipe[0] == "line":
        positions = (1, 2)
    elif recipe[0] == "ellipse_arc":
        positions = (7, 8)
    else:
        positions = (2, 3)
    for slot, original, coordinate in zip(
        positions, (old_start, old_end), (shape.start, shape.end)
    ):
        value = head[slot]
        if isinstance(value, Point):
            continue
        if geom2d.distance(value, coordinate) < TOLERANCE:
            head[slot] = original
    return tuple(head)


def _sub_shape(shape: Shape2D, from_parameter: float, to_parameter: float) -> Shape2D:
    """Кусок объекта между двумя положениями вдоль него."""
    if shape.kind == "spline":
        from . import bspline as bspline_module

        # Кусок сплайна — не «сплайн покороче», а ТА ЖЕ кривая на части
        # своего протяжения: полюсы пересчитываются вставкой узлов, и
        # форма сохраняется до последнего знака. Обрезать иначе значило бы
        # молча поменять контур, а с ним площадь и объём детали.
        poles, knots, mults = bspline_module.piece(
            shape.poles, shape.knots, shape.mults, shape.degree,
            max(0.0, from_parameter), min(1.0, to_parameter))
        piece = Shape2D("spline", poles=poles, knots=knots, mults=mults,
                        degree=shape.degree)
        piece.start = poles[0]
        piece.end = poles[-1]
        return piece
    if shape.kind == "line":
        return Shape2D("line", start=point_at(shape, from_parameter),
                       end=point_at(shape, to_parameter))
    if shape.kind in ("ellipse", "ellipse_arc"):
        start_angle = geom2d.normalize_angle(shape.start_angle + from_parameter)
        end_angle = geom2d.normalize_angle(shape.start_angle + to_parameter)
        piece = Shape2D("ellipse_arc", center=shape.center,
                        radius=shape.radius, minor=shape.minor,
                        rotation=shape.rotation,
                        start_angle=start_angle, end_angle=end_angle)
        piece.start = ellipse_point(piece, start_angle)
        piece.end = ellipse_point(piece, end_angle)
        return piece
    start_angle = geom2d.normalize_angle(shape.start_angle + from_parameter)
    end_angle = geom2d.normalize_angle(shape.start_angle + to_parameter)
    return Shape2D(
        "arc",
        start=geom2d.point_at_angle(shape.center, shape.radius, start_angle),
        end=geom2d.point_at_angle(shape.center, shape.radius, end_angle),
        center=shape.center,
        radius=shape.radius,
        start_angle=start_angle,
        end_angle=end_angle,
    )


# --- отсечение и продление ---


def trim(sketch, segment, at_point) -> EditResult:
    """Убрать участок объекта до ближайших пересечений вокруг указанной точки.

    Без пересечений объект удаляется целиком — это же и есть «отсечь всё».
    Окружность, отсечённая один раз, остаётся окружностью: одна точка реза
    не делит замкнутую кривую, и превращать её в дугу было бы выдумкой.
    """
    result = EditResult()
    shape = describe(sketch, segment)
    cuts = []
    for other in sketch.segments:
        if other is segment:
            continue
        target = describe(sketch, other)
        try:
            crossings = intersections(shape, target)
        except ToolError:
            # Одна необсчитываемая кривая не должна отменять работу со
            # всеми остальными. Она пропускается — и об этом говорится:
            # молчаливый пропуск выглядел бы как «инструмент её не увидел».
            result.skipped.append(target.kind)
            continue
        for point in crossings:
            cuts.append(parameter_of(shape, point))

    # Замкнутая кривая — это окружность И ЭЛЛИПС. Пока эллипс считался
    # разомкнутым, отсечение резало его как дугу: одна точка реза делила
    # его надвое, и вместо целого эллипса оставалось два куска. На простом
    # эллипсе, разрезанном ровно по оси, это не проявлялось — куски
    # выходили одинаковыми, — а на повёрнутом сразу.
    closed = shape.kind in ("circle", "ellipse")
    limit = geom2d.TAU if closed else (1.0 if shape.kind == "line" else
                                       geom2d.arc_sweep(shape.start_angle, shape.end_angle))
    cuts = sorted({round(value, 9) for value in cuts if -TOLERANCE <= value <= limit + TOLERANCE})
    if not cuts or (closed and len(cuts) < 2):
        sketch.delete([segment])
        result.removed.append(segment)
        return result

    click = parameter_of(shape, at_point)
    bounds = cuts if closed else [0.0] + cuts + [limit]
    if closed:
        # На замкнутой кривой участки идут по кругу: последний смыкается с
        # первым через ноль.
        spans = [(bounds[i], bounds[(i + 1) % len(bounds)]) for i in range(len(bounds))]
    else:
        spans = list(zip(bounds, bounds[1:]))

    victim = None
    for low, high in spans:
        span = (high - low) % limit if closed else high - low
        offset = (click - low) % limit if closed else click - low
        if -TOLERANCE <= offset <= span + TOLERANCE:
            victim = (low, high)
            break
    if victim is None:
        raise ToolError("точка не попадает на объект")

    survivors = []
    for low, high in spans:
        if (low, high) == victim:
            continue
        span = (high - low) % limit if closed else high - low
        if span > TOLERANCE:
            survivors.append(_sub_shape(shape, low, low + span))

    if not survivors:
        sketch.delete([segment])
        result.removed.append(segment)
        return result

    # Первый уцелевший кусок заменяет исходный объект и наследует его связи,
    # остальные добавляются рядом. Концы, оставшиеся на месте, переиспользуют
    # прежние точки — там держится стыковка с соседями по контуру.
    extras = [
        (_keep_ends(segment, shape, _recipe_of(piece)), segment)
        for piece in survivors[1:]
    ]
    _rebuild_segments(
        sketch,
        {segment: _keep_ends(segment, shape, _recipe_of(survivors[0]))},
        result,
        extras=extras,
    )
    return result


def extend(sketch, segment, at_point) -> EditResult:
    """Продлить ближний к указанной точке конец до первого встречного объекта."""
    result = EditResult()
    shape = describe(sketch, segment)
    if shape.kind == "circle":
        raise ToolError("окружность продлевать некуда")

    at_start = parameter_of(shape, at_point) < (
        0.5 if shape.kind == "line"
        else geom2d.arc_sweep(shape.start_angle, shape.end_angle) / 2.0
    )

    best = None
    for other in sketch.segments:
        if other is segment:
            continue
        target = describe(sketch, other)
        try:
            hits = _extension_hits(shape, target, at_start)
        except ToolError:
            result.skipped.append(target.kind)
            continue
        for point in hits:
            reach = _reach(shape, point, at_start)
            if reach > TOLERANCE and (best is None or reach < best[0]):
                best = (reach, point)
    if best is None:
        raise ToolError("впереди нет объекта, до которого можно продлить")

    point = best[1]
    if shape.kind == "line":
        recipe = ("line", point, shape.end) if at_start else ("line", shape.start, point)
    else:
        recipe = (
            ("arc", segment.points[0], point, shape.end) if at_start
            else ("arc", segment.points[0], shape.start, point)
        )
    _rebuild_segments(sketch, {segment: _keep_ends(segment, shape, recipe)}, result)
    return result


def _extension_hits(shape: Shape2D, target: Shape2D, at_start: bool) -> list:
    """Точки, до которых объект может дотянуться при продлении.

    Считаются по НЕограниченной несущей кривой продлеваемого объекта, но по
    реальной протяжённости встречного: продлевают до того, что нарисовано.
    """
    # Несущая кривая: у дуги круга это окружность, у дуги эллипса — сам
    # эллипс. Подменять эллипс окружностью нельзя даже «на время поиска»:
    # точка встречи нашлась бы не там, и продление ушло бы мимо.
    unbounded = Shape2D(
        "ellipse" if shape.kind == "ellipse_arc" else shape.kind,
        start=shape.start,
        end=shape.end,
        center=shape.center,
        radius=shape.radius,
        start_angle=0.0,
        end_angle=geom2d.TAU,
        minor=shape.minor,
        rotation=shape.rotation,
        poles=shape.poles, knots=shape.knots, mults=shape.mults,
        degree=shape.degree,
    )
    if shape.kind == "line":
        if target.kind == "line":
            crossing = geom2d.line_line(shape.start, shape.end, target.start, target.end)
            if crossing and -TOLERANCE <= crossing[2] <= 1 + TOLERANCE:
                return [crossing[0]]
            return []
        if target.kind in HARD:
            # Продлевать до эллипса — законно, и считать это надо по
            # эллипсу, а не по окружности его большой полуоси.
            return [point for point in refined_crossings(unbounded, target)
                    if _on_curve(target, point)]
        return [
            point
            for point, _ in geom2d.line_circle(shape.start, shape.end,
                                               target.center, target.radius)
            if _on_curve(target, point)
        ]
    if unbounded.kind != "ellipse":
        unbounded.kind = "circle"
    return intersections(unbounded, target)


def _reach(shape: Shape2D, point, at_start: bool) -> float:
    """Насколько далеко за конец лежит точка. Отрицательное — назад, внутрь."""
    if shape.kind == "line":
        t = geom2d.project_on_line(point, shape.start, shape.end)[1]
        return (-t) if at_start else (t - 1.0)
    if shape.kind in ("ellipse", "ellipse_arc"):
        angle = ellipse_angle(shape, point)
    else:
        angle = geom2d.angle_at(shape.center, point)
    if at_start:
        return geom2d.normalize_angle(shape.start_angle - angle)
    return geom2d.normalize_angle(angle - shape.end_angle)


# --- смещение ---


#: Насколько эквидистанта кривой вправе отойти от точной. Микрон: ниже
#: любого производственного допуска, но достижим при разумном числе
#: полюсов. Десятая доля микрона, стоявшая здесь сначала, была не строгостью
#: — при ней даже верная эквидистанта эллипса не проходила, потому что сама
#: ПРОВЕРКА точнее не мерила.
OFFSET_TOLERANCE = 1e-3

#: Дальше этого дробить выборку незачем: кривая, не сошедшаяся к такому
#: числу точек, скорее выродилась, чем сложна.
OFFSET_LIMIT = 256


def _curve_span(shape: Shape2D) -> tuple:
    """Промежуток собственного параметра кривой и замкнута ли она."""
    if shape.kind == "spline":
        return 0.0, 1.0, False
    if shape.kind in ("circle", "ellipse"):
        return 0.0, geom2d.TAU, True
    return (shape.start_angle,
            shape.start_angle + geom2d.arc_sweep(shape.start_angle,
                                                 shape.end_angle), False)


def _offset_point(shape: Shape2D, parameter: float, amount: float):
    """Точка эквидистанты: отход ВЛЕВО от направления обхода.

    Сторона считается так же, как у отрезка и окружности: положительное
    смещение уходит влево. Иначе у контура из отрезков, дуг и эллипсов
    один и тот же знак означал бы разные стороны, и смещение рвало бы его.
    """
    point = curve_value(shape, parameter)
    slope = curve_tangent(shape, parameter)
    length = (slope[0] ** 2 + slope[1] ** 2) ** 0.5
    if length < 1e-12:
        raise ToolError("кривая вырождается — эквидистанта не определена")
    left = (-slope[1] / length, slope[0] / length)
    return (point[0] + amount * left[0], point[1] + amount * left[1])


def _away_from_polyline(point, polyline) -> float:
    """Расстояние от точки до ломаной — до ОТРЕЗКОВ, а не до вершин."""
    best = float("inf")
    for first, second in zip(polyline, polyline[1:]):
        along = (second[0] - first[0], second[1] - first[1])
        length = along[0] ** 2 + along[1] ** 2
        if length < 1e-18:
            near = first
        else:
            part = ((point[0] - first[0]) * along[0]
                    + (point[1] - first[1]) * along[1]) / length
            part = min(1.0, max(0.0, part))
            near = (first[0] + along[0] * part, first[1] + along[1] * part)
        best = min(best, geom2d.distance(point, near))
    return best


def _offset_curve(shape: Shape2D, amount: float) -> tuple:
    """Эквидистанта кривой сплайном. ``(форма, отклонение)``.

    Эквидистанта эллипса — НЕ эллипс: это кривая более высокого порядка, и
    точной формулы у неё нет. Поэтому она приближается сплайном, а величина
    приближения не скрывается, а возвращается наружу: молча выданная
    «почти та» кривая — это молча изменённый контур.

    Число точек подбирается по достигнутой точности, а не берётся наугад:
    у сплюснутого эллипса кривизна на концах во много раз больше, чем на
    боках, и одна и та же выборка даёт то избыток, то нехватку.
    """
    from . import bspline as bspline_module

    low, high, closed = _curve_span(shape)
    span = high - low
    count = 16
    best = None
    while count <= OFFSET_LIMIT:
        times = [low + span * index / count for index in range(count + 1)]
        points = [_offset_point(shape, t, amount) for t in times]
        if closed:
            # У замкнутой кривой последняя точка совпадает с первой — иначе
            # эквидистанта окажется разомкнутой, и область по ней не
            # соберётся.
            points[-1] = points[0]
        poles, knots, mults = bspline_module.poles_through(points)
        degree = len(knots) and (sum(mults) - len(poles) - 1)
        piece = Shape2D("spline", poles=poles, knots=knots, mults=mults,
                        degree=degree)
        piece.start, piece.end = points[0], points[-1]
        # Отклонение — величина ГЕОМЕТРИЧЕСКАЯ: насколько сплайн отошёл
        # от настоящей эквидистанты. Сравнивать «половину между точками»
        # у сплайна и у кривой нельзя: параметр у сплайна расставлен по
        # длинам хорд, и одна и та же доля означает у них разные места.
        # Поэтому берётся густая ломаная настоящей эквидистанты, и
        # меряется расстояние до неё — в окне вокруг ожидаемого места,
        # чтобы не перебирать её целиком.
        # Ломаная берётся вчетверо гуще выборки: её собственная погрешность
        # — это ПОЛ измерения, и меряя ею, легко принять её за ошибку
        # кривой. Восьмикратная гуще на порядок точнее нужного порога.
        fine = 8
        dense = [_offset_point(shape, low + span * index / (count * fine),
                               amount)
                 for index in range(count * fine + 1)]
        worst = 0.0
        for index in range(count):
            middle = (index + 0.5) / count
            got = curve_value(piece, middle)
            # Расстояние до ЛОМАНОЙ, а не до её вершин. Разница не
            # тонкая: до вершин оно не бывает меньше половины шага
            # выборки, и эта половина выдавала себя за ошибку кривой —
            # сходимость выходила первого порядка там, где у кубического
            # сплайна она четвёртого.
            worst = max(worst, _away_from_polyline(got, dense))
        best = (piece, worst)
        if worst <= OFFSET_TOLERANCE:
            break
        count *= 2
    if best is None or best[1] > OFFSET_TOLERANCE:
        # Две разные беды, и путать их нельзя. Эквидистанта ВЫРОЖДАЕТСЯ,
        # когда смещение больше радиуса кривизны: кривая складывается сама
        # на себя, и это видно по развороту хода. Всё остальное — просто
        # не сошлось, и говорить про радиус кривизны тут значило бы
        # выдумывать причину.
        if _folded(shape, amount):
            raise ToolError(
                "эквидистанта вырождается: смещение больше радиуса кривизны "
                "кривой — она сложилась бы сама на себя")
        raise ToolError(
            f"эквидистанту не удалось приблизить точнее "
            f"{best[1]:.4f} мм при {OFFSET_LIMIT} точках")
    return best


def _folded(shape: Shape2D, amount: float) -> bool:
    """Складывается ли эквидистанта сама на себя.

    Признак прямой: на сложившемся участке она идёт НАВСТРЕЧУ исходной
    кривой. Считать радиус кривизны отдельной формулой для каждого вида
    кривой не нужно — разворот хода виден одинаково у всех.
    """
    low, high, _ = _curve_span(shape)
    span = high - low
    steps = 240
    for index in range(steps):
        first = low + span * index / steps
        second = low + span * (index + 1) / steps
        source = curve_value(shape, second), curve_value(shape, first)
        moved = _offset_point(shape, second, amount), _offset_point(shape, first, amount)
        a = (source[0][0] - source[1][0], source[0][1] - source[1][1])
        b = (moved[0][0] - moved[1][0], moved[0][1] - moved[1][1])
        if a[0] * b[0] + a[1] * b[1] < 0.0:
            return True
    return False


def offset(sketch, segments, distance: float, through=None,
           construction: bool = False) -> EditResult:
    """Копии объектов, смещённые на расстояние.

    Сторона задаётся знаком **единообразно для всех видов**: положительное
    смещение уходит влево от направления обхода. У кривой, идущей против
    часовой стрелки, влево — это к центру, поэтому положительное смещение
    окружность уменьшает. Соблазн сделать «наружу» положительным для
    окружностей велик, но тогда у замкнутого контура из отрезков и дуг один
    и тот же знак означал бы разные стороны, и смещение рвало бы контур.

    ``through`` снимает вопрос знака вовсе: точка указывает нужную сторону, и
    знак подбирается так, чтобы результат лёг к ней ближе. Именно так
    смещение и вызывается с экрана — курсором, а не числом со знаком.

    Соседние отрезки, имевшие общий конец, стыкуются в точке пересечения
    смещённых прямых, иначе на выпуклых углах остаются разрывы, а на
    вогнутых — самопересечения.
    """
    result = EditResult()
    shapes = {segment: describe(sketch, segment) for segment in segments}

    def displaced(amount: float) -> dict:
        moved: dict = {}
        for segment, shape in shapes.items():
            if shape.kind == "line":
                start, end = geom2d.offset_line(shape.start, shape.end, amount)
                moved[segment] = Shape2D("line", start=start, end=end)
            elif shape.kind in ("ellipse", "ellipse_arc", "spline"):
                # У эллипса и сплайна эквидистанта — кривая другого рода, и
                # подставлять вместо неё «такой же эллипс поменьше» нельзя:
                # это разные кривые, и расходятся они тем сильнее, чем
                # сплюснутее исходная.
                piece, deviation = _offset_curve(shape, amount)
                moved[segment] = piece
                result.deviation = max(result.deviation, deviation)
            else:
                radius = shape.radius - amount
                if radius <= TOLERANCE:
                    raise ToolError("смещение больше радиуса — кривая вырождается")
                moved[segment] = Shape2D(
                    shape.kind,
                    start=geom2d.point_at_angle(shape.center, radius, shape.start_angle),
                    end=geom2d.point_at_angle(shape.center, radius, shape.end_angle),
                    center=shape.center,
                    radius=radius,
                    start_angle=shape.start_angle,
                    end_angle=shape.end_angle,
                )
        return moved

    amount = abs(distance) if through is not None else distance
    moved = displaced(amount)
    if through is not None and _farther(shapes, moved, through):
        moved = displaced(-amount)

    _stitch(shapes, moved)

    for segment, shape in moved.items():
        created = _make(sketch, _recipe_of(shape), segment)
        created.construction = construction or segment.construction
        result.created.append(created)
    return result


def _farther(originals: dict, moved: dict, target) -> bool:
    """Оказалась ли смещённая геометрия дальше от указанной точки, чем исходная."""

    def gap(shape: Shape2D) -> float:
        if shape.kind == "line":
            return geom2d.distance_to_segment(target, shape.start, shape.end)
        if shape.kind == "circle":
            return geom2d.distance_to_circle(target, shape.center, shape.radius)
        return geom2d.distance_to_arc(
            target, shape.center, shape.radius, shape.start_angle, shape.end_angle
        )

    return min(gap(shape) for shape in moved.values()) > min(
        gap(shape) for shape in originals.values()
    )


def _stitch(originals: dict, moved: dict) -> None:
    """Свести смещённые отрезки в общих углах.

    Какой конец подтягивать, определяется по ТОЖДЕСТВУ точки, а не по
    близости координат: у короткого отрезка оба конца рядом с углом, и
    сравнение расстояний выбирало бы из них наугад.
    """
    items = list(originals)
    for index, segment_a in enumerate(items):
        for segment_b in items[index + 1:]:
            shared = _shared_end(segment_a, segment_b)
            if shared is None:
                continue
            if moved[segment_a].kind != "line" or moved[segment_b].kind != "line":
                continue
            crossing = geom2d.line_line(
                moved[segment_a].start, moved[segment_a].end,
                moved[segment_b].start, moved[segment_b].end,
            )
            if crossing is None:
                continue
            for segment in (segment_a, segment_b):
                shape = moved[segment]
                if segment.points[-2] is shared:
                    shape.start = crossing[0]
                else:
                    shape.end = crossing[0]


def _shared_end(a, b):
    for point in a.ends:
        for other in b.ends:
            if point is other:
                return point
    return None


# --- зеркало и массивы ---


def mirror(sketch, segments, axis, with_constraints: bool = True) -> EditResult:
    """Отражённые копии относительно отрезка-оси.

    ``with_constraints`` связывает копии с оригиналами симметрией: тогда
    правка одной половины ведёт вторую. Без связей получаются просто копии,
    и это законный выбор — симметрия на сложном контуре нередко делает
    систему переопределённой.
    """
    result = EditResult()
    a1, a2 = (sketch.coordinates(point) for point in axis.points[-2:])

    def reflect(point):
        return geom2d.mirror_about_line(point, a1, a2)

    pairs = []
    for segment in segments:
        if segment is axis:
            continue
        shape = describe(sketch, segment)
        if shape.kind == "line":
            created = _make(sketch, ("line", reflect(shape.start), reflect(shape.end)), segment)
            pairs.append((segment, created))
        elif shape.kind == "circle":
            created = _make(sketch, ("circle", reflect(shape.center), shape.radius), segment)
            pairs.append((segment, created))
        elif shape.kind == "ellipse":
            # Отражение меняет знак наклона относительно оси: направление
            # большой оси отражается вместе с самим эллипсом.
            tip = geom2d.point_at_angle(shape.center, shape.radius,
                                        shape.rotation)
            moved, turned = reflect(shape.center), reflect(tip)
            angle = math.atan2(turned[1] - moved[1], turned[0] - moved[0])
            created = _make(sketch, ("ellipse", moved, shape.radius,
                                     shape.minor, angle), segment)
            pairs.append((segment, created))
        else:
            # Отражение меняет направление обхода, поэтому концы дуги
            # переставляются местами: иначе зеркальная дуга уходит длинной
            # стороной вместо короткой.
            created = _make(
                sketch,
                ("arc", reflect(shape.center), reflect(shape.end), reflect(shape.start)),
                segment,
            )
            pairs.append((segment, created))
        result.created.append(created)

    if with_constraints:
        for original, created in pairs:
            try:
                if original.kind == "line":
                    sketch.symmetric(original.points[0], created.points[0], axis)
                    sketch.symmetric(original.points[1], created.points[1], axis)
                else:
                    sketch.symmetric(original.points[0], created.points[0], axis)
                    sketch.equal_radius(original, created)
            except Exception:  # noqa: BLE001 — симметрия могла переопределить эскиз
                result.lost_relations.append("symmetric")
    return result


def linear_pattern(sketch, segments, step_x: float, step_y: float, count: int,
                   step2_x: float = 0.0, step2_y: float = 0.0, count2: int = 1) -> EditResult:
    """Массив копий по одному или двум направлениям. Исходные объекты — экземпляр
    номер один, поэтому их копий на своём месте не появляется."""
    if count < 1 or count2 < 1:
        raise ToolError("число экземпляров не меньше одного")
    result = EditResult()
    shapes = {segment: describe(sketch, segment) for segment in segments}
    for index2 in range(count2):
        for index in range(count):
            if index == 0 and index2 == 0:
                continue
            shift = (
                step_x * index + step2_x * index2,
                step_y * index + step2_y * index2,
            )
            for segment, shape in shapes.items():
                result.created.append(
                    _make(sketch, _recipe_of(_translate(shape, shift)), segment)
                )
    return result


def circular_pattern(sketch, segments, center, count: int,
                     total_angle: float = 360.0, equal_spacing: bool = True) -> EditResult:
    """Массив копий по окружности вокруг точки.

    При полном обороте шаг считается по ``count`` экземплярам, а не по
    ``count - 1``: иначе последняя копия ложится поверх исходной.
    """
    if count < 2:
        raise ToolError("в круговом массиве не меньше двух экземпляров")
    result = EditResult()
    full = abs(total_angle % 360.0) < 1e-9
    divisor = count if (full and equal_spacing) else max(count - 1, 1)
    step = math.radians(total_angle) / divisor
    shapes = {segment: describe(sketch, segment) for segment in segments}
    for index in range(1, count):
        angle = step * index
        for segment, shape in shapes.items():
            result.created.append(
                _make(sketch, _recipe_of(_rotate(shape, center, angle)), segment)
            )
    return result


# --- перенос, поворот, масштаб ---
#
# По спецификации, 038_transform_entities.yaml. Три вида преобразования из
# пяти: «растянуть» отдельным пробелом (TRANSFORM-GAP-001).


def move(sketch, segments, shift, copy: bool = False,
         keep_relations: bool = True) -> EditResult:
    """Перенести выбранное на вектор ``shift``."""
    return _transform(
        sketch, segments, copy, keep_relations,
        lambda point: geom2d.add(point, shift),
        lambda shape: _translate(shape, shift))


def rotate(sketch, segments, center, angle_deg: float, copy: bool = False,
           keep_relations: bool = True) -> EditResult:
    """Повернуть выбранное вокруг точки на угол в градусах."""
    angle = math.radians(angle_deg)
    return _transform(
        sketch, segments, copy, keep_relations,
        lambda point: geom2d.rotate_about(point, center, angle),
        lambda shape: _rotate(shape, center, angle))


def scale(sketch, segments, center, factor: float, copy: bool = False,
          keep_relations: bool = True) -> EditResult:
    """Изменить размер выбранного относительно точки.

    Коэффициент строго положительный. Отрицательный дал бы зеркало, и
    выдавать его за масштаб значило бы прятать другое действие под чужим
    именем: для отражения есть ``mirror``.
    """
    if factor <= 0.0:
        raise ToolError("коэффициент масштаба должен быть больше нуля")
    return _transform(
        sketch, segments, copy, keep_relations,
        lambda point: _from(center, point, factor),
        lambda shape: _scale(shape, center, factor), factor)


def _transform(sketch, segments, copy: bool, keep_relations: bool,
               place, reshape, factor: float = 1.0) -> EditResult:
    """Общее для переноса, поворота и масштаба.

    Копия — это НОВЫЕ объекты по преобразованным очертаниям, как у
    массивов. Правка на месте — перенос точек: они общие у соседних
    объектов, поэтому контур остаётся сомкнутым, связи и размеры остаются
    при своих объектах, а решатель честно возвращает то, что держат
    размеры. Пересобирать объекты заново здесь нельзя: пересборка не
    переносит совпадения и привязки, и перенесённый прямоугольник
    развалился бы на четыре отрезка.
    """
    if not segments:
        raise ToolError("не выбрано ни одного объекта")
    result = EditResult()
    shapes = {segment: describe(sketch, segment) for segment in segments}
    if copy:
        for segment, shape in shapes.items():
            result.created.append(
                _make(sketch, _recipe_of(reshape(shape)), segment))
        return result

    if not keep_relations:
        _free(sketch, segments, result)

    points, order = {}, []
    for segment in segments:
        for point in segment.points:
            if point.id not in points:
                points[point.id] = point
                order.append(point)
    targets = [(point, place(sketch.coordinates(point))) for point in order]
    result.deviation = _settle(sketch, targets)

    # У окружности радиус — собственная величина, а не следствие
    # положения точек: центр переехал, размер остался прежним. Поэтому
    # при масштабе окружности пересобираются, и только они.
    if abs(factor - 1.0) > TOLERANCE:
        closed = {segment: describe(sketch, segment) for segment in segments
                  if segment.kind in ("circle", "ellipse")}
        if closed:
            _rebuild_segments(
                sketch,
                {segment: _resized(shape, factor)
                 for segment, shape in closed.items()},
                result)
    return result


def _resized(shape: Shape2D, factor: float):
    """Рецепт замкнутой кривой после того, как её точки уже переехали.

    У окружности радиус точками не задан вовсе, и его надо умножить. У
    эллипса переезд ФОКУСА уже растянул большую полуось — осталась малая.
    Умножить обе значило бы применить масштаб дважды: на полуосях 20 и 12
    при коэффициенте 2 выходило 68.35 вместо 40, и заметно это только по
    числу.
    """
    if shape.kind == "circle":
        return ("circle", shape.center, shape.radius * factor)
    minor = shape.minor * factor
    gap = math.sqrt(max(shape.radius ** 2 - shape.minor ** 2, 0.0))
    return ("ellipse", shape.center, math.hypot(minor, gap), minor,
            shape.rotation)


#: Связи, привязывающие объект к системе координат эскиза, а не к другому
#: объекту. Они держат выбранное независимо от того, что ещё выбрано, и
#: при «не сохранять связи» снимаются всегда.
FRAME_BOUND = ("anchor", "horizontal", "vertical")

#: Сколько раз повторять постановку точек. Решатель ведёт себя как при
#: перетаскивании: за один вызов он делает ШАГ к запрошенному положению, а
#: не прыгает в него. Дуге, увеличенной втрое, нужно три прохода — её
#: собственный радиус тоже участвует в решении и подтягивается вместе с
#: точками. Больше восьми не нужно: если за восемь не сошлось, держат
#: связи, а не счёт проходов.
PLACE_PASSES = 8


def _settle(sketch, targets) -> float:
    """Поставить точки в нужные места. Возвращает остаточное расхождение.

    Повтор нужен потому, что один вызов решателя — это шаг к цели, а не
    попадание. Останавливаемся, когда попали или когда очередной проход
    перестал улучшать: дальше держат связи, и повторять бессмысленно.
    """
    previous = None
    for _ in range(PLACE_PASSES):
        sketch.place([(point, x, y) for point, (x, y) in targets])
        away = max((geom2d.distance(sketch.coordinates(point), wanted)
                    for point, wanted in targets), default=0.0)
        if away <= TOLERANCE:
            return 0.0
        if previous is not None and away >= previous - TOLERANCE:
            return away
        previous = away
    return previous or 0.0


def _free(sketch, segments, result: EditResult) -> None:
    """Снять связи, которые держат выбранное на месте.

    Снимаются две породы: привязанные к системе координат эскиза
    (закрепление, горизонтальность, вертикальность) и уходящие ЗА пределы
    выбранного — они держат группу за то, что не двигается.

    Связи целиком ВНУТРИ выбранного остаются: они и делают группу
    группой. Совпадение углов, равенство сторон, касание — всё это
    переезжает вместе с ней, и снимать их значило бы развалить контур
    ради того, чтобы его подвинуть.

    Размеры не трогаются вовсе: размер задаёт величину, а не положение.
    """
    chosen_points = {point.id for segment in segments
                     for point in segment.points}
    chosen_segments = {segment.id for segment in segments}

    def inside(target) -> bool:
        value = getattr(target, "id", None)
        return value in chosen_points or value in chosen_segments

    doomed, seen = [], set()
    for segment in segments:
        holders = [segment, *segment.points]
        for holder in holders:
            for relation in sketch.relations_on(holder):
                if relation.id in seen:
                    continue
                seen.add(relation.id)
                doomed.append(relation)

    for relation in doomed:
        if relation not in sketch.constraints:
            continue    # это размер, а не связь
        if (relation.kind not in FRAME_BOUND
                and all(inside(target) for target in relation.targets)):
            continue
        sketch.delete_constraint(relation)
        result.lost_relations.append(relation.kind)


def _from(center, point, factor: float):
    return (center[0] + (point[0] - center[0]) * factor,
            center[1] + (point[1] - center[1]) * factor)


def _scale(shape: Shape2D, center, factor: float) -> Shape2D:
    return Shape2D(
        shape.kind,
        start=_from(center, shape.start, factor),
        end=_from(center, shape.end, factor),
        center=_from(center, shape.center, factor),
        radius=shape.radius * factor,
        start_angle=shape.start_angle,
        end_angle=shape.end_angle,
        minor=shape.minor * factor,
        rotation=shape.rotation,
        poles=tuple(_from(center, item, factor) for item in shape.poles),
        knots=shape.knots, mults=shape.mults, degree=shape.degree,
    )


def _translate(shape: Shape2D, shift) -> Shape2D:
    return Shape2D(
        shape.kind,
        start=geom2d.add(shape.start, shift),
        end=geom2d.add(shape.end, shift),
        center=geom2d.add(shape.center, shift),
        radius=shape.radius,
        start_angle=shape.start_angle,
        end_angle=shape.end_angle,
        minor=shape.minor,
        rotation=shape.rotation,
        poles=tuple(geom2d.add(item, shift) for item in shape.poles),
        knots=shape.knots, mults=shape.mults, degree=shape.degree,
    )


def _rotate(shape: Shape2D, center, angle: float) -> Shape2D:
    return Shape2D(
        shape.kind,
        start=geom2d.rotate_about(shape.start, center, angle),
        end=geom2d.rotate_about(shape.end, center, angle),
        center=geom2d.rotate_about(shape.center, center, angle),
        radius=shape.radius,
        start_angle=geom2d.normalize_angle(shape.start_angle + angle),
        end_angle=geom2d.normalize_angle(shape.end_angle + angle),
        minor=shape.minor,
        rotation=shape.rotation + angle,
        poles=tuple(geom2d.rotate_about(item, center, angle)
                    for item in shape.poles),
        knots=shape.knots, mults=shape.mults, degree=shape.degree,
    )


# --- скругление и фаска в эскизе ---


def fillet(sketch, segment_a, segment_b, radius: float,
           keep_constraints: bool = True) -> EditResult:
    """Скруглить угол между двумя отрезками дугой заданного радиуса."""
    result = EditResult()
    shape_a, shape_b = describe(sketch, segment_a), describe(sketch, segment_b)
    if shape_a.kind != "line" or shape_b.kind != "line":
        raise ToolError("скругление в эскизе задаётся между двумя отрезками")
    corner = geom2d.fillet_lines(
        shape_a.start, shape_a.end, shape_b.start, shape_b.end, radius
    )
    if corner is None:
        raise ToolError("радиус не помещается в угол")
    center, tangent_a, tangent_b, _, _ = corner

    replacements = {
        segment_a: _keep_ends(segment_a, shape_a, ("line", *_shorten(shape_a, tangent_a))),
        segment_b: _keep_ends(segment_b, shape_b, ("line", *_shorten(shape_b, tangent_b))),
    }
    created = _rebuild_segments(sketch, replacements, result)

    new_a, new_b = created[segment_a], created[segment_b]
    point_a = _nearest_point(sketch, new_a, tangent_a)
    point_b = _nearest_point(sketch, new_b, tangent_b)
    # Порядок концов дуги выбирается так, чтобы обход против часовой шёл по
    # короткой стороне — той, что и заменяет угол.
    first, last = (point_a, point_b)
    if geom2d.arc_sweep(geom2d.angle_at(center, tangent_a),
                        geom2d.angle_at(center, tangent_b)) > math.pi:
        first, last = last, first
    arc = sketch.arc(sketch.point(*center, construction=True), first, last)
    result.created.append(arc)

    if keep_constraints:
        try:
            sketch.tangent(new_a, arc)
            sketch.tangent(new_b, arc)
            sketch.radius(arc, radius)
        except Exception:  # noqa: BLE001
            result.lost_relations.append("tangent")
    return result


def chamfer(sketch, segment_a, segment_b, distance: float) -> EditResult:
    """Срезать угол между отрезками на равные расстояния."""
    result = EditResult()
    shape_a, shape_b = describe(sketch, segment_a), describe(sketch, segment_b)
    if shape_a.kind != "line" or shape_b.kind != "line":
        raise ToolError("фаска в эскизе задаётся между двумя отрезками")
    crossing = geom2d.line_line(shape_a.start, shape_a.end, shape_b.start, shape_b.end)
    if crossing is None:
        raise ToolError("отрезки параллельны — угла нет")
    corner = crossing[0]
    cut_a = _along(shape_a, corner, distance)
    cut_b = _along(shape_b, corner, distance)

    replacements = {
        segment_a: _keep_ends(segment_a, shape_a, ("line", *_shorten(shape_a, cut_a))),
        segment_b: _keep_ends(segment_b, shape_b, ("line", *_shorten(shape_b, cut_b))),
    }
    created = _rebuild_segments(sketch, replacements, result)
    result.created.append(
        sketch.line(
            _nearest_point(sketch, created[segment_a], cut_a),
            _nearest_point(sketch, created[segment_b], cut_b),
        )
    )
    return result


def _shorten(shape: Shape2D, new_end):
    """Заменить тот конец отрезка, что ближе к новой точке."""
    if geom2d.distance(shape.start, new_end) < geom2d.distance(shape.end, new_end):
        return new_end, shape.end
    return shape.start, new_end


def _along(shape: Shape2D, corner, distance: float):
    """Точка на отрезке в стороне от угла — туда, где отрезок продолжается."""
    far = shape.start if geom2d.distance(corner, shape.start) > geom2d.distance(corner, shape.end) \
        else shape.end
    return geom2d.add(corner, geom2d.scale(geom2d.direction(corner, far), distance))


def _nearest_point(sketch, segment, target):
    return min(
        segment.points,
        key=lambda point: geom2d.distance(sketch.coordinates(point), target),
    )

# --- полное определение эскиза ---------------------------------------------
#
# По спецификации, 051_fully_define_sketch.yaml. Задача: довести эскиз до
# нуля степеней свободы, добавляя то, что человек и так имел в виду.

#: Связи, которые команда пробует накладывать. Порядок значим: сначала то,
#: что убирает больше свободы и реже противоречит.
SAFE_RELATIONS = ("horizontal", "vertical", "coincident", "concentric",
                  "perpendicular", "parallel", "equal")

#: Насколько «почти горизонтальным» должен быть отрезок, чтобы его сделали
#: горизонтальным, радианы. Полградуса: больше — и команда начинает
#: выпрямлять то, что нарисовано наклонным нарочно.
ANGLE_TOLERANCE = math.radians(0.5)

#: Насколько близкими должны быть точки, чтобы их свели вместе, мм.
POINT_TOLERANCE = 0.05

#: Насколько близкими должны быть длины, чтобы их приравняли, — доля.
LENGTH_TOLERANCE = 0.01


@dataclass
class DefineResult:
    """Что добавлено и сколько свободы осталось."""

    relations: list = field(default_factory=list)
    dimensions: list = field(default_factory=list)
    dof: int = 0
    #: На сколько миллиметров сдвинулась геометрия. Связи выпрямляют то,
    #: что было нарисовано почти прямым, и молчать об этом нельзя.
    deviation: float = 0.0

    @property
    def note(self) -> str:
        parts = []
        if self.relations:
            parts.append(f"связей добавлено: {len(self.relations)}")
        if self.dimensions:
            parts.append(f"размеров добавлено: {len(self.dimensions)}")
        if self.deviation > 1e-6:
            parts.append(f"геометрия сдвинулась на {self.deviation:.3f} мм")
        if self.dof > 0:
            parts.append(f"осталось степеней свободы: {self.dof}")
        elif self.dof == 0:
            parts.append("эскиз полностью определён")
        return "; ".join(parts) or "добавлять было нечего"


def fully_define(sketch, segments=None, relations=SAFE_RELATIONS,
                 scheme: str = "baseline", origin=None) -> DefineResult:
    """Довести эскиз до нуля степеней свободы.

    Сначала накладываются СВЯЗИ — то, что на эскизе и так почти выполнено:
    почти горизонтальный отрезок объявляется горизонтальным, почти
    сомкнутые концы сводятся вместе. Затем добавляются РАЗМЕРЫ, пока
    свобода не кончится.

    Каждое добавление проверяется: если эскиз перестал решаться или число
    степеней свободы не уменьшилось, добавленное снимается обратно. Это и
    отличает команду от «навалить связей»: переопределённый эскиз хуже
    недоопределённого, потому что его нельзя править.

    ``scheme`` — откуда мерить: ``"baseline"`` от одной точки отсчёта,
    ``"chain"`` цепочкой от предыдущей. Обе дают одинаковую геометрию и
    разный вид на чертеже.
    """
    if scheme not in ("baseline", "chain"):
        raise ToolError(f"неизвестная схема размеров: {scheme!r}")
    working = list(segments if segments is not None
                   else [item for item in sketch.segments
                         if not item.construction])
    result = DefineResult()
    if not working:
        result.dof = sketch.status().dof
        return result

    before = {point.id: sketch.coordinates(point) for point in sketch.points}
    for kind in relations:
        for targets in _candidates(sketch, working, kind):
            if _dof(sketch) == 0:
                break
            if _try(sketch, kind, targets):
                result.relations.append(kind)

    _dimension(sketch, working, scheme, origin, result)

    result.dof = _dof(sketch)
    result.deviation = max(
        (geom2d.distance(sketch.coordinates(point), before[point.id])
         for point in sketch.points if point.id in before), default=0.0)
    return result


def _dof(sketch) -> int:
    """Степеней свободы сейчас. Отрицательное означает избыток связей."""
    return sketch.status().dof


def _try(sketch, kind: str, targets) -> bool:
    """Наложить связь и оставить её, только если она помогла.

    «Помогла» значит: эскиз по-прежнему решается И свободы стало меньше.
    Связь, не изменившая число степеней свободы, ничего не добавила к
    замыслу, зато мешает править — снимается.
    """
    was = _dof(sketch)
    try:
        constraint = getattr(sketch, kind)(*targets)
    except Exception:  # noqa: BLE001 — связь могла оказаться невозможной
        return False
    now = _dof(sketch)
    if now < 0 or now >= was or not sketch.solve().ok:
        sketch.delete_constraint(constraint)
        return False
    return True


def _candidates(sketch, segments, kind: str) -> list:
    """Что стоит попробовать для этой породы связи."""
    lines = [item for item in segments if item.kind == "line"]
    curves = [item for item in segments if item.kind in ("circle", "arc",
                                                         "ellipse")]
    if kind in ("horizontal", "vertical"):
        wanted = 0.0 if kind == "horizontal" else math.pi / 2.0
        found = []
        for line in lines:
            shape = describe(sketch, line)
            angle = math.atan2(shape.end[1] - shape.start[1],
                               shape.end[0] - shape.start[0]) % math.pi
            if min(abs(angle - wanted), math.pi - abs(angle - wanted)) \
                    <= ANGLE_TOLERANCE:
                found.append((line,))
        return found
    if kind == "coincident":
        found, ends = [], []
        for segment in segments:
            ends.extend(segment.ends)
        for index, first in enumerate(ends):
            for second in ends[index + 1:]:
                if first is second:
                    continue
                if geom2d.distance(sketch.coordinates(first),
                                   sketch.coordinates(second)) \
                        <= POINT_TOLERANCE:
                    found.append((first, second))
        return found
    if kind == "concentric":
        return [(a, b) for index, a in enumerate(curves)
                for b in curves[index + 1:]
                if geom2d.distance(describe(sketch, a).center,
                                   describe(sketch, b).center)
                <= POINT_TOLERANCE]
    if kind in ("parallel", "perpendicular"):
        turn = 0.0 if kind == "parallel" else math.pi / 2.0
        found = []
        for index, a in enumerate(lines):
            for b in lines[index + 1:]:
                first, second = describe(sketch, a), describe(sketch, b)
                angle = abs(_slope(first) - _slope(second)) % math.pi
                if min(abs(angle - turn), math.pi - abs(angle - turn)) \
                        <= ANGLE_TOLERANCE:
                    found.append((a, b))
        return found
    if kind == "equal":
        found = []
        for index, a in enumerate(lines):
            for b in lines[index + 1:]:
                first = _length(describe(sketch, a))
                second = _length(describe(sketch, b))
                if first > TOLERANCE and abs(first - second) / first \
                        <= LENGTH_TOLERANCE:
                    found.append((a, b))
        return found
    return []


def _slope(shape: Shape2D) -> float:
    return math.atan2(shape.end[1] - shape.start[1],
                      shape.end[0] - shape.start[0]) % math.pi


def _length(shape: Shape2D) -> float:
    return geom2d.distance(shape.start, shape.end)


def _dimension(sketch, segments, scheme: str, origin, result: DefineResult) -> None:
    """Добавлять размеры, пока свобода не кончится.

    Порядок разумный, а не произвольный: сначала размеры кривых — они
    убирают свободу, не привязывая ничего к месту, — затем закрепление
    точки отсчёта, затем положения остальных точек.
    """
    for segment in segments:
        if _dof(sketch) == 0:
            return
        if segment.kind == "circle":
            _, radius = sketch.circle_geometry(segment)
            _try_dimension(sketch, result, sketch.radius, segment, radius)
        elif segment.kind == "arc":
            _try_dimension(sketch, result, sketch.radius, segment,
                           sketch.arc_geometry(segment).radius)

    if _dof(sketch) == 0:
        return
    anchor = origin or _closest_to_zero(sketch, segments)
    if anchor is not None and not any(
            item.kind == "anchor" for item in sketch.constraints):
        if _try(sketch, "anchor", (anchor,)):
            result.relations.append("anchor")

    previous = anchor
    for point in _free_points(sketch, segments):
        if _dof(sketch) == 0:
            return
        base = anchor if scheme == "baseline" else (previous or anchor)
        if base is None or point is base:
            continue
        first, second = sketch.coordinates(base), sketch.coordinates(point)
        for axis, value in (("x", second[0] - first[0]),
                            ("y", second[1] - first[1])):
            if _dof(sketch) == 0:
                break
            _try_dimension(sketch, result, sketch.dimension_axis,
                           base, point, abs(value), axis)
        previous = point


def _try_dimension(sketch, result: DefineResult, maker, *arguments) -> bool:
    """Поставить размер и оставить его, только если он убрал свободу."""
    was = _dof(sketch)
    if was <= 0:
        return False
    try:
        dimension = maker(*arguments)
    except Exception:  # noqa: BLE001 — размер мог оказаться невозможным
        return False
    now = _dof(sketch)
    if now < 0 or now >= was or not sketch.solve().ok:
        sketch.delete_constraint(dimension)
        return False
    result.dimensions.append(dimension.name)
    return True


def _closest_to_zero(sketch, segments):
    """Точка отсчёта: ближайшая к началу координат эскиза.

    Собственной точки начала координат у эскиза нет — ноль это свойство
    плоскости, а не объект. Поэтому за отсчёт берётся ближайшая к нулю
    точка контура: она же обычно и оказывается тем углом, от которого
    человек размеряет деталь.
    """
    points = [point for segment in segments for point in segment.points]
    if not points:
        return None
    return min(points,
               key=lambda point: geom2d.distance(sketch.coordinates(point),
                                                 (0.0, 0.0)))


def _free_points(sketch, segments) -> list:
    """Точки выбранных объектов, по одной, в порядке появления."""
    seen, found = set(), []
    for segment in segments:
        for point in segment.points:
            if point.id in seen:
                continue
            seen.add(point.id)
            found.append(point)
    return found
