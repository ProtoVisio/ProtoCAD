"""Проверка эскиза на то, из-за чего он не даёт контура.

По спецификации, `050_repair_sketch.yaml`.

Зачем это нужно, видно на самом частом случае. Человек нарисовал
прямоугольник, но два конца разошлись на пять сотых миллиметра. На экране
контур сомкнут — разрыв меньше пикселя. Разбор областей его не сваривает:
допуск сварки — миллионные доли, и такой зазор для него настоящий. Область
не получается, выдавливание отвечает «профиль пуст», и понять, почему,
нельзя: смотреть не на что.

Эта проверка и отвечает на такой вопрос. Она НЕ чинит молча: сначала
говорит, что нашла и где, а чинит только то, что чинится однозначно.

Разделение на «нашла» и «починила» здесь принципиальное. Совпавшие
объекты, ветвление и самопересечение однозначного исправления не имеют:
какой из двух одинаковых отрезков лишний — знает только человек. Чинить их
наугад значит испортить эскиз молча, а это хуже, чем не чинить.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import geom2d
from . import tools as T

#: Что проверяется. Порядок значим: сначала то, что чинится однозначно.
CHECKS = ("gap", "short", "duplicate", "overlap", "branch", "self_intersection")

#: Насколько разошедшиеся концы считаются РАЗРЫВОМ, мм.
#:
#: Меньше этого — беда, больше — замысел. Ноль здесь не годится: сварка
#: точек в разборе областей идёт по миллионным долям, и зазор в сотые для
#: неё настоящий, а для человека его нет. Десятая миллиметра — граница, за
#: которой зазор виден на чертеже и, значит, нарисован нарочно.
TOLERANCE = 0.1

#: Объект короче этого считается случайным, мм. Такие получаются от
#: двойного щелчка и от неудачного отсечения; в контуре они не видны, а
#: разбор областей об них спотыкается.
SHORT = 1e-3

TITLES = {
    "gap": "Разрыв контура",
    "short": "Слишком короткий объект",
    "duplicate": "Совпавшие объекты",
    "overlap": "Наложение объектов",
    "branch": "Ветвление контура",
    "self_intersection": "Самопересечение контура",
}


@dataclass
class Problem:
    """Одна найденная беда: что, где и чинится ли однозначно."""

    kind: str
    segments: list = field(default_factory=list)
    points: list = field(default_factory=list)
    #: Где смотреть — координаты в плоскости эскиза.
    at: tuple = (0.0, 0.0)
    #: Величина беды: длина разрыва, длина объекта. Для сравнения и показа.
    value: float = 0.0
    fixable: bool = False

    @property
    def title(self) -> str:
        return TITLES.get(self.kind, self.kind)

    @property
    def text(self) -> str:
        where = f"({self.at[0]:.2f}; {self.at[1]:.2f})"
        if self.kind == "gap":
            return f"{self.title} {self.value:.4f} мм в {where}"
        if self.kind == "short":
            return f"{self.title}: длина {self.value:.5f} мм в {where}"
        if self.kind == "branch":
            return f"{self.title}: сходятся {int(self.value)} направления в {where}"
        return f"{self.title} в {where}"


@dataclass
class RepairResult:
    """Что починено и что осталось человеку."""

    fixed: list = field(default_factory=list)
    left: list = field(default_factory=list)

    @property
    def note(self) -> str:
        if not self.fixed and not self.left:
            return "эскиз в порядке"
        parts = []
        if self.fixed:
            parts.append(f"исправлено: {len(self.fixed)}")
        if self.left:
            parts.append(f"осталось разобрать: {len(self.left)}")
        return "; ".join(parts)


def scan(sketch, checks=CHECKS, tolerance: float = TOLERANCE) -> list[Problem]:
    """Найти всё, из-за чего эскиз может не дать контура.

    Ничего не меняет. Порядок находок устойчив — от него зависит, что
    покажут первым, и прыгать он не должен.
    """
    segments = [item for item in sketch.segments if not item.construction]
    found: list[Problem] = []
    for kind in checks:
        found.extend(_FINDERS[kind](sketch, segments, tolerance))
    return found


def repair(sketch, problems) -> RepairResult:
    """Исправить то, что исправляется однозначно.

    Остальное возвращается нетронутым: у совпавших объектов, ветвления и
    самопересечения однозначного исправления нет, и выбирать за человека
    здесь значит портить молча.
    """
    result = RepairResult()
    for problem in problems:
        if not problem.fixable:
            result.left.append(problem)
            continue
        if _fix(sketch, problem):
            result.fixed.append(problem)
        else:
            result.left.append(problem)
    if result.fixed:
        sketch.solve()
    return result


def _fix(sketch, problem: Problem) -> bool:
    if problem.kind == "gap":
        # Разрыв закрывается СВЯЗЬЮ, а не сдвигом точки. Связь останется в
        # эскизе и не даст ему разойтись снова при первой же правке
        # размера; сдвиг держался бы ровно до неё.
        try:
            sketch.coincident(*problem.points[:2])
        except Exception:  # noqa: BLE001
            return False
        return sketch.solve().ok
    if problem.kind == "short":
        return bool(sketch.delete(problem.segments))
    return False


# --- сами проверки ---------------------------------------------------------


def _gaps(sketch, segments, tolerance: float) -> list[Problem]:
    """Концы, разошедшиеся меньше чем на допуск, но не сведённые вместе."""
    ends = [(segment, point) for segment in segments
            for point in segment.ends]
    joined = {
        tuple(sorted((first.id, second.id)))
        for item in sketch.constraints if item.kind == "coincident"
        for first, second in [item.targets[:2]]
    }
    found = []
    for index, (owner, first) in enumerate(ends):
        for other, second in ends[index + 1:]:
            if first is second or owner is other:
                continue
            if tuple(sorted((first.id, second.id))) in joined:
                continue
            gap = geom2d.distance(sketch.coordinates(first),
                                  sketch.coordinates(second))
            if gap <= 1e-9 or gap > tolerance:
                continue
            here = sketch.coordinates(first)
            found.append(Problem("gap", [owner, other], [first, second],
                                 here, gap, fixable=True))
    return found


def _short(sketch, segments, tolerance: float) -> list[Problem]:
    found = []
    for segment in segments:
        shape = T.describe(sketch, segment)
        if shape.kind == "line":
            length = geom2d.distance(shape.start, shape.end)
        elif shape.kind == "arc":
            length = shape.radius * geom2d.arc_sweep(shape.start_angle,
                                                     shape.end_angle)
        elif shape.kind == "circle":
            length = geom2d.TAU * shape.radius
        else:
            # Периметр эллипса точной формулы не имеет; для «слишком
            # короткий» довольно нижней оценки — четырёх полуосей.
            length = 2.0 * (shape.radius + shape.minor)
        if length <= SHORT:
            found.append(Problem("short", [segment], list(segment.points),
                                 shape.start, length, fixable=True))
    return found


def _duplicates(sketch, segments, tolerance: float) -> list[Problem]:
    """Объекты, совпадающие геометрически. Чинить за человека нельзя."""
    found, seen = [], []
    for segment in segments:
        shape = T.describe(sketch, segment)
        for other, other_shape in seen:
            if _same(shape, other_shape, tolerance):
                found.append(Problem("duplicate", [other, segment], [],
                                     shape.start if shape.kind == "line"
                                     else shape.center, 0.0))
                break
        seen.append((segment, shape))
    return found


def _same(a, b, tolerance: float) -> bool:
    if a.kind != b.kind:
        return False
    if a.kind == "line":
        return ((geom2d.distance(a.start, b.start) <= tolerance
                 and geom2d.distance(a.end, b.end) <= tolerance)
                or (geom2d.distance(a.start, b.end) <= tolerance
                    and geom2d.distance(a.end, b.start) <= tolerance))
    if geom2d.distance(a.center, b.center) > tolerance:
        return False
    if a.kind == "circle":
        return abs(a.radius - b.radius) <= tolerance
    if a.kind == "ellipse":
        return (abs(a.radius - b.radius) <= tolerance
                and abs(a.minor - b.minor) <= tolerance)
    return (abs(a.radius - b.radius) <= tolerance
            and geom2d.distance(a.start, b.start) <= tolerance
            and geom2d.distance(a.end, b.end) <= tolerance)


def _overlaps(sketch, segments, tolerance: float) -> list[Problem]:
    """Отрезки, лежащие на одной прямой и перекрывающиеся.

    Совпавшие целиком сюда не попадают — о них говорит своя проверка.
    """
    lines = [(item, T.describe(sketch, item)) for item in segments
             if item.kind == "line"]
    found = []
    for index, (first, a) in enumerate(lines):
        for second, b in lines[index + 1:]:
            if _same(a, b, tolerance):
                continue
            if not _collinear(a, b, tolerance):
                continue
            overlap = _shared_span(a, b)
            if overlap > tolerance:
                middle = ((a.start[0] + a.end[0]) / 2.0,
                          (a.start[1] + a.end[1]) / 2.0)
                found.append(Problem("overlap", [first, second], [],
                                     middle, overlap))
    return found


def _collinear(a, b, tolerance: float) -> bool:
    if geom2d.distance(a.start, a.end) <= tolerance:
        return False
    return (geom2d.distance_to_line(b.start, a.start, a.end) <= tolerance
            and geom2d.distance_to_line(b.end, a.start, a.end) <= tolerance)


def _shared_span(a, b) -> float:
    """Длина общего участка двух отрезков на одной прямой."""
    along = (a.end[0] - a.start[0], a.end[1] - a.start[1])
    length = math.hypot(*along)
    if length <= 1e-12:
        return 0.0

    def position(point) -> float:
        return ((point[0] - a.start[0]) * along[0]
                + (point[1] - a.start[1]) * along[1]) / length

    first = sorted((0.0, length))
    second = sorted((position(b.start), position(b.end)))
    return max(0.0, min(first[1], second[1]) - max(first[0], second[0]))


def _branches(sketch, segments, tolerance: float) -> list[Problem]:
    """Точки, где контур ветвится: сходятся три и больше направлений.

    Обход контура в такой точке неоднозначен: куда идти дальше, геометрия
    не говорит. Разбор областей выбирает сам, и выбор этот человеку не
    виден — поэтому о ветвлении надо предупредить, а не чинить его.

    Считаются НАПРАВЛЕНИЯ, а не объекты. Объект, кончающийся в точке, даёт
    одно направление; объект, ПРОХОДЯЩИЙ через неё, — два. Иначе тройник
    (отрезок упёрся в середину другого) не находился бы вовсе: объектов
    там два, а направлений три. Именно так он и терялся.

    Чистое пересечение крест-накрест ветвлением не считается: там ни один
    объект не кончается, и об этом говорит своя проверка.
    """
    places: dict = {}
    for segment in segments:
        for point in segment.ends:
            key = tuple(round(value / max(tolerance, 1e-9))
                        for value in sketch.coordinates(point))
            places.setdefault(key, sketch.coordinates(point))

    found = []
    for key in sorted(places):
        here = places[key]
        ways, owners = 0, []
        for segment in segments:
            ends = [point for point in segment.ends
                    if geom2d.distance(sketch.coordinates(point), here)
                    <= tolerance]
            if ends:
                ways += len(ends)
                owners.append(segment)
            elif T.distance_to(sketch, segment, here) <= tolerance:
                ways += 2
                owners.append(segment)
        if ways >= 3:
            found.append(Problem("branch", owners, [], here, float(ways)))
    return found


def _self_intersections(sketch, segments, tolerance: float) -> list[Problem]:
    """Пересечения объектов НЕ в общих концах.

    Замкнутый контур, пересекающий сам себя, области не даёт: разбор
    честно разложит его на несколько кусков, и человек получит не то, что
    рисовал.
    """
    found = []
    for index, first in enumerate(segments):
        for second in segments[index + 1:]:
            if set(first.ends) & set(second.ends):
                continue
            try:
                crossings = T.intersections(T.describe(sketch, first),
                                            T.describe(sketch, second))
            except T.ToolError:
                continue    # эллипс — пересечения по нему пока не считаем
            for point in crossings:
                if any(geom2d.distance(point, sketch.coordinates(end))
                       <= tolerance
                       for end in list(first.ends) + list(second.ends)):
                    continue
                found.append(Problem("self_intersection", [first, second],
                                     [], point, 0.0))
    return found


_FINDERS = {
    "gap": _gaps,
    "short": _short,
    "duplicate": _duplicates,
    "overlap": _overlaps,
    "branch": _branches,
    "self_intersection": _self_intersections,
}
