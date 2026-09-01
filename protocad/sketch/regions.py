"""Области эскиза: планарное разбиение и атомарные области.

Операция получает не «весь эскиз» и не список линий, а выбранные
двумерные ОБЛАСТИ. Разница видна на самом простом эскизе: прямоугольник и
окружность внутри него дают две области — круг и рамку между ними. Что из
этого выдавливать, решает человек, а не порядок разбора контуров.

Разбиение строится так (по §4 спецификации):

    кривые эскиза без вспомогательных
        → разрезать во всех пересечениях
        → склеить близкие концы
        → граф полурёбер
        → циклы обходом «минимальный поворот налево»
        → отбросить внешний цикл
        → дерево вложенности
        → атомарные области

Здесь только двумерная геометрия и топология. Ядра нет намеренно: область
должна вычисляться и показываться до того, как построена хоть одна грань,
и не зависеть от того, удастся ли её построить.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import geom2d
from . import tools as T

#: Насколько близкими считаются точки, чтобы стать одной вершиной графа.
#: Крупнее, чем допуск решателя: концы, сведённые связью «совпадение»,
#: расходятся на последние знаки, и без склейки контур не замкнётся.
WELD = 1e-6

#: На сколько частей разбивается окружность и дуга при поиске областей.
#: Разбиение нужно только для определения ВЛОЖЕННОСТИ и площади; сама
#: область хранит исходные кривые, поэтому грань строится точной.
FACETS = 96

#: Площадь меньше этой считается вырожденной областью и отбрасывается.
MIN_AREA = 1e-9


@dataclass
class Piece:
    """Кусок кривой эскиза между двумя пересечениями."""

    segment: object              # исходный объект эскиза
    start: tuple[float, float]
    end: tuple[float, float]
    points: list                 # ломаная, включая концы
    t_start: float = 0.0         # доля вдоль исходной кривой
    t_end: float = 1.0

    @property
    def reversed_points(self) -> list:
        return list(reversed(self.points))


@dataclass
class Loop:
    """Замкнутый цикл: ломаная и куски, из которых он собран."""

    outline: list
    pieces: list
    area: float                  # со знаком: положительная — против часовой

    @property
    def absolute_area(self) -> float:
        return abs(self.area)

    @property
    def segment_ids(self) -> list:
        """Объекты эскиза, образующие эту петлю, по одному разу."""
        seen, found = set(), []
        for piece in self.pieces:
            key = piece.segment.id
            if key in seen:
                continue
            seen.add(key)
            found.append(key)
        return sorted(found)


@dataclass
class Region:
    """Атомарная область: внешняя петля минус непосредственно вложенные."""

    index: int
    outer: Loop
    inner: list = field(default_factory=list)
    depth: int = 0

    @property
    def area(self) -> float:
        return self.outer.absolute_area - sum(
            loop.absolute_area for loop in self.inner)

    @property
    def centroid(self) -> tuple[float, float]:
        return _centroid(self.outer.outline)

    @property
    def sample(self) -> tuple[float, float]:
        """Точка ВНУТРИ области — та, по которой её опознают после правки.

        Центр тяжести внешней петли не годится: у подковообразной области
        он лежит снаружи, а у кольца попадает в отверстие.
        """
        return _inner_point(self.outer.outline, self.inner)

    @property
    def segments(self) -> list:
        """Объекты эскиза, образующие границу области."""
        seen, result = set(), []
        for loop in [self.outer] + list(self.inner):
            for piece in loop.pieces:
                if id(piece.segment) in seen:
                    continue
                seen.add(id(piece.segment))
                result.append(piece.segment)
        return result

    def contains(self, point) -> bool:
        if not _point_in(point, self.outer.outline):
            return False
        return not any(_point_in(point, loop.outline) for loop in self.inner)

    def reference(self) -> dict:
        """Устойчивая ссылка на область: чем она была, а не какой по счёту.

        Номер области нестабилен — добавленная линия сдвигает все
        последующие. Опознаётся область по НАРУЖНОМУ контуру; вложенные
        петли, точка внутри и площадь — только различители.

        Почему по наружному, а не по всем объектам границы. Потому что
        нарисованная ВНУТРИ выбранной области окружность меняет набор всех
        объектов — и выбор молча пропадал. На экране это выглядело так:
        человек выбрал область, дорисовал окружность, нажал «Вырез» — и
        получил в кармане остров, которого не заказывал. Наружный контур
        при этом не менялся; область осталась той же, у неё просто
        появилось отверстие.

        Строгость при этом сохраняется: исчез наружный контур — ссылка
        честно теряется, а не находит соседнюю область (§33, Strict).
        """
        return {
            "outer": self.outer.segment_ids,
            "holes": sorted(key for loop in self.inner
                            for key in loop.segment_ids),
            # Полный набор — для записей, сделанных прежней схемой.
            "segments": sorted(segment.id for segment in self.segments),
            "sample": list(self.sample),
            "area": round(self.area, 6),
        }


def build(sketch) -> list[Region]:
    """Атомарные области эскиза, от большей к меньшей по площади."""
    pieces = _split_all(_working(sketch), sketch)
    if not pieces:
        return []
    loops = _loops(pieces)
    return _regions(loops)


def chains(sketch) -> list[list[Piece]]:
    """Незамкнутая геометрия эскиза, сшитая в цепочки.

    Область из разомкнутого контура не получается по определению, и
    обычное выдавливание его не видит. Но тонкая стенка строится как раз
    по нему: это единственный способ выдавить разомкнутый контур вообще.

    Разомкнутым считается кусок, у которого ОБА полуребра попали в один и
    тот же цикл обхода. У куска, лежащего на настоящем контуре, слева и
    справа разные грани разбиения, и полурёбра расходятся по разным
    циклам; у отростка и у перемычки между двумя контурами обход
    возвращается по нему же. Считать по числу соседей в вершине нельзя:
    перемычка между двумя замкнутыми контурами упирается обоими концами в
    вершины со многими соседями и сошла бы за замкнутую.
    """
    pieces = _split_all(_working(sketch), sketch)
    if not pieces:
        return []
    return _stitch_chains(_open_pieces(pieces))


def find(regions: list[Region], reference: dict) -> Region | None:
    """Найти область по устойчивой ссылке после пересчёта эскиза.

    Отбор СТРОГИЙ (§33 спецификации, режим Strict), но строгость — по
    НАРУЖНОМУ контуру. Совпасть обязан он; вложенные петли, точка внутри и
    площадь различают области с одинаковым наружным контуром.

    Мягкий отбор недопустим. Стоило разрешить поиск по одной лишь точке
    внутри — и удаление внутренней окружности переставало быть ошибкой:
    ссылка на круг находила прямоугольник, внутри которого эта точка тоже
    лежит, и вместо отказа операция молча выдавливала сплошную пластину.
    Материал, которого не заказывали, хуже отказа.

    Но и обратная крайность — сверять ВСЕ объекты границы — оказалась
    неверной: дорисованная внутри выбранной области окружность меняла
    набор, и выбор молча пропадал. Наружный контур при этом тот же, и
    область та же — у неё появилось отверстие.
    """
    if not regions or not reference:
        return None
    wanted = set(reference.get("outer") or ())
    if wanted:
        candidates = [region for region in regions
                      if set(region.outer.segment_ids) == wanted]
    else:
        # Ссылка из записи прежней схемы: наружного контура в ней нет,
        # есть только полный набор. Такие читаются по-старому — иначе
        # сохранённые детали перестали бы открываться.
        whole = set(reference.get("segments") or ())
        if not whole:
            return None
        candidates = [region for region in regions
                      if {segment.id for segment in region.segments} == whole]
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]

    holes = set(reference.get("holes") or ())
    same_holes = [region for region in candidates
                  if {key for loop in region.inner
                      for key in loop.segment_ids} == holes]
    if len(same_holes) == 1:
        return same_holes[0]
    if same_holes:
        candidates = same_holes

    sample = tuple(reference.get("sample") or ())
    if len(sample) == 2:
        inside = [region for region in candidates if region.contains(sample)]
        if len(inside) == 1:
            return inside[0]
        if inside:
            candidates = inside
    area = reference.get("area")
    if area is None:
        return None
    return min(candidates, key=lambda region: abs(region.area - area))


# --- разбиение -------------------------------------------------------------


def _working(sketch) -> list:
    """Рабочая геометрия: всё, кроме вспомогательной (§24 спецификации).

    Вспомогательные линии областей НЕ режут. Иначе осевая линия,
    проведённая через деталь для симметрии, разваливала бы её профиль на
    две половины.
    """
    return [segment for segment in sketch.segments if not segment.construction]


def _sample(sketch, segment) -> list:
    """Объект эскиза ломаной. Окружность и эллипс замыкаются сами на себя."""
    shape = T.describe(sketch, segment)
    if shape.kind == "line":
        return [shape.start, shape.end]
    if shape.kind in ("ellipse", "spline", "ellipse_arc", "parabola_arc",
                       "hyperbola_arc"):
        # Густота та же, что у окружности: разбиение нужно только для
        # ВЛОЖЕННОСТИ и площади, сама область хранит исходные кривые.
        return T.curve_outline_of(shape, FACETS)
    if shape.kind == "circle":
        return [
            geom2d.point_at_angle(shape.center, shape.radius,
                                  geom2d.TAU * i / FACETS)
            for i in range(FACETS + 1)
        ]
    sweep = geom2d.arc_sweep(shape.start_angle, shape.end_angle)
    steps = max(2, int(FACETS * sweep / geom2d.TAU))
    return [
        geom2d.point_at_angle(shape.center, shape.radius,
                              shape.start_angle + sweep * i / steps)
        for i in range(steps + 1)
    ]


def _split_all(segments, sketch) -> list[Piece]:
    """Разрезать каждую кривую во всех пересечениях с остальными (§4.4)."""
    polylines = {id(s): _sample(sketch, s) for s in segments}
    cuts: dict[int, set] = {id(s): set() for s in segments}

    for first_index, first in enumerate(segments):
        for second in segments[first_index + 1:]:
            for point in _exact_crossings(sketch, first, second):
                cuts[id(first)].add(_fraction(sketch, first, point))
                cuts[id(second)].add(_fraction(sketch, second, point))

    pieces: list[Piece] = []
    for segment in segments:
        outline = polylines[id(segment)]
        marks = sorted({0.0, 1.0} | {
            value for value in cuts[id(segment)] if 1e-9 < value < 1.0 - 1e-9
        })
        for low, high in zip(marks, marks[1:]):
            part = _slice(outline, low, high)
            # Концы куска ставятся на ИСТИННУЮ кривую, а не на ломаную:
            # ломаная вписана в окружность и отстоит от неё на сотые доли
            # миллиметра. Куски двух окружностей, разрезанные в одной
            # точке, из-за этого не склеивались в графе, и пересекающиеся
            # окружности снова становились двумя отдельными областями.
            part[0] = _point_on(sketch, segment, low)
            part[-1] = _point_on(sketch, segment, high)
            if len(part) < 2 or geom2d.distance(part[0], part[-1]) < WELD:
                # Кусок нулевой длины — это не кусок. Замкнутая кривая,
                # которую ничто не пересекло, идёт целиком.
                if not (low == 0.0 and high == 1.0):
                    continue
            pieces.append(Piece(segment, part[0], part[-1], part, low, high))
    return pieces


def _exact_crossings(sketch, first, second) -> list:
    """Пересечения двух объектов эскиза — аналитически, а не по ломаным.

    Пересечение ломаных даёт точку с ошибкой порядка тысячной миллиметра.
    Для показа областей этого хватает, а для построения грани — нет: два
    куска дуги, разрезанные в «почти одной» точке, ядро отказывается
    сшить в замкнутый контур. Кривые здесь только прямые и окружности,
    поэтому точное решение короткое.
    """
    a = T.describe(sketch, first)
    b = T.describe(sketch, second)
    if {a.kind, b.kind} & set(T.HARD):
        # У эллипса точной формулы пересечения нет: с окружностью и с
        # другим эллипсом она приводит к уравнению четвёртой степени.
        # Поэтому положение находится по ломаным, а потом УТОЧНЯЕТСЯ
        # Ньютоном по самим кривым — до последних знаков, а не до
        # тысячных, на которых разваливается сшивка контура.
        found = _refined(a, b)
    elif a.kind == "line" and b.kind == "line":
        found = _line_line(a, b)
    elif a.kind == "line":
        found = _line_circle(a, b)
    elif b.kind == "line":
        found = _line_circle(b, a)
    else:
        found = _circle_circle(a, b)
    return [point for point in found
            if _on(a, point) and _on(b, point)]


#: Сколько шагов Ньютона отводится на уточнение одного пересечения. Метод
#: сходится квадратично, и трёх-четырёх шагов хватает; восемь — с запасом
#: на неудачное начальное приближение.
REFINE_STEPS = 8


def _parametric(shape):
    """Точка кривой и производная по её параметру. Для уточнения."""
    if shape.kind == "line":
        along = (shape.end[0] - shape.start[0], shape.end[1] - shape.start[1])
        return (lambda t: (shape.start[0] + along[0] * t,
                           shape.start[1] + along[1] * t),
                lambda t: along)
    if shape.kind == "spline":
        from . import bspline as bspline_module

        return (lambda t: bspline_module.value(shape.poles, shape.knots,
                                               shape.mults, shape.degree, t),
                lambda t: bspline_module.tangent(shape.poles, shape.knots,
                                                 shape.mults, shape.degree, t))
    if shape.kind == "ellipse":
        cos_turn = math.cos(shape.rotation)
        sin_turn = math.sin(shape.rotation)

        def point(t):
            return T.ellipse_point(shape, t)

        def slope(t):
            along = -shape.radius * math.sin(t)
            across = shape.minor * math.cos(t)
            return (along * cos_turn - across * sin_turn,
                    along * sin_turn + across * cos_turn)

        return point, slope
    return (lambda t: geom2d.point_at_angle(shape.center, shape.radius, t),
            lambda t: (-shape.radius * math.sin(t),
                       shape.radius * math.cos(t)))


def _at_parameter(shape, point) -> float:
    """Параметр кривой, отвечающий точке. Начальное приближение Ньютона."""
    if shape.kind == "line":
        along = (shape.end[0] - shape.start[0], shape.end[1] - shape.start[1])
        length = along[0] ** 2 + along[1] ** 2
        if length < 1e-18:
            return 0.0
        return ((point[0] - shape.start[0]) * along[0]
                + (point[1] - shape.start[1]) * along[1]) / length
    if shape.kind == "spline":
        # У сплайна обратной формулы нет: параметр берётся по ближайшей
        # точке ломаной, а дальше его уточняет сам Ньютон.
        outline = T.curve_outline(shape, FACETS)
        best = min(range(len(outline)),
                   key=lambda index: geom2d.distance(outline[index], point))
        return best / (len(outline) - 1)
    if shape.kind == "hyperbola_arc":
        return T.hyperbola_parameter(shape, point)
    if shape.kind == "parabola_arc":
        return T.parabola_parameter(shape, point)
    if shape.kind in ("ellipse", "ellipse_arc"):
        return T.ellipse_angle(shape, point)
    return geom2d.angle_at(shape.center, point)


def _refined(a, b) -> list:
    """Пересечения, найденные по ломаным и уточнённые по кривым."""
    first = _outline_of(a)
    second = _outline_of(b)
    found = []
    for guess in _crossings(first, second):
        point = _newton(a, b, guess)
        if point is None:
            continue
        if all(geom2d.distance(point, other) > WELD for other in found):
            found.append(point)
    return found


def _outline_of(shape) -> list:
    if shape.kind == "line":
        return [shape.start, shape.end]
    if shape.kind in ("ellipse", "spline"):
        return T.curve_outline(shape, FACETS)
    steps = FACETS if shape.kind == "circle" else max(
        2, int(FACETS * geom2d.arc_sweep(shape.start_angle, shape.end_angle)
               / geom2d.TAU))
    if shape.kind == "circle":
        return [geom2d.point_at_angle(shape.center, shape.radius,
                                      geom2d.TAU * i / steps)
                for i in range(steps + 1)]
    sweep = geom2d.arc_sweep(shape.start_angle, shape.end_angle)
    return [geom2d.point_at_angle(shape.center, shape.radius,
                                  shape.start_angle + sweep * i / steps)
            for i in range(steps + 1)]


def _newton(a, b, guess):
    """Уточнить пересечение двух кривых из приближения ``guess``.

    Решается система «точка первой кривой равна точке второй» по двум
    параметрам. Якобиан — производные обеих кривых; вырожденный означает
    касание, и тогда уточнять нечего: приближение и есть ответ с той
    точностью, какую даёт ломаная.
    """
    point_a, slope_a = _parametric(a)
    point_b, slope_b = _parametric(b)
    u, v = _at_parameter(a, guess), _at_parameter(b, guess)
    for _ in range(REFINE_STEPS):
        pa, pb = point_a(u), point_b(v)
        fx, fy = pa[0] - pb[0], pa[1] - pb[1]
        if math.hypot(fx, fy) <= 1e-12:
            return pa
        da, db = slope_a(u), slope_b(v)
        determinant = da[0] * (-db[1]) - (-db[0]) * da[1]
        if abs(determinant) < 1e-15:
            return guess
        u -= (fx * (-db[1]) - (-db[0]) * fy) / determinant
        v -= (da[0] * fy - fx * da[1]) / determinant
    pa = point_a(u)
    return pa if geom2d.distance(pa, point_b(v)) <= 1e-9 else None


def _line_line(a, b) -> list:
    point = _segment_crossing(a.start, a.end, b.start, b.end)
    return [] if point is None else [point]


def _line_circle(line, circle) -> list:
    """Точки пересечения прямой с окружностью."""
    ax, ay = line.start
    dx, dy = line.end[0] - ax, line.end[1] - ay
    cx, cy = circle.center
    a = dx * dx + dy * dy
    if a < 1e-18:
        return []
    b = 2.0 * (dx * (ax - cx) + dy * (ay - cy))
    c = (ax - cx) ** 2 + (ay - cy) ** 2 - circle.radius ** 2
    discriminant = b * b - 4.0 * a * c
    if discriminant < 0.0:
        return []
    root = math.sqrt(discriminant)
    found = []
    for t in ((-b - root) / (2.0 * a), (-b + root) / (2.0 * a)):
        if -1e-9 <= t <= 1.0 + 1e-9:
            found.append((ax + dx * t, ay + dy * t))
    return found


def _circle_circle(first, second) -> list:
    """Точки пересечения двух окружностей."""
    x1, y1 = first.center
    x2, y2 = second.center
    r1, r2 = first.radius, second.radius
    span = math.hypot(x2 - x1, y2 - y1)
    if span < 1e-12 or span > r1 + r2 + 1e-12 or span < abs(r1 - r2) - 1e-12:
        return []
    a = (r1 * r1 - r2 * r2 + span * span) / (2.0 * span)
    height = r1 * r1 - a * a
    height = math.sqrt(height) if height > 0.0 else 0.0
    mx = x1 + a * (x2 - x1) / span
    my = y1 + a * (y2 - y1) / span
    if height < 1e-12:
        return [(mx, my)]
    ox = height * (y2 - y1) / span
    oy = height * (x2 - x1) / span
    return [(mx + ox, my - oy), (mx - ox, my + oy)]


def _on(shape, point) -> bool:
    """Лежит ли точка на объекте с учётом его пределов."""
    if shape.kind == "line":
        return True          # предел уже учтён при решении
    if shape.kind == "circle":
        return True
    angle = geom2d.angle_at(shape.center, point)
    return geom2d.arc_contains(shape.start_angle, shape.end_angle, angle)


def _fraction(sketch, segment, point) -> float:
    """Доля вдоль объекта, отсчитанная по его собственному параметру."""
    shape = T.describe(sketch, segment)
    if shape.kind == "line":
        along = (shape.end[0] - shape.start[0], shape.end[1] - shape.start[1])
        length = along[0] ** 2 + along[1] ** 2
        if length < 1e-18:
            return 0.0
        return max(0.0, min(1.0, (
            (point[0] - shape.start[0]) * along[0]
            + (point[1] - shape.start[1]) * along[1]) / length))
    if shape.kind == "spline":
        return _at_parameter(shape, point)
    if shape.kind == "hyperbola_arc":
        low, high = shape.start_angle, shape.end_angle
        span = high - low
        if abs(span) < 1e-15:
            return 0.0
        return max(0.0, min(1.0,
                            (T.hyperbola_parameter(shape, point) - low) / span))
    if shape.kind == "parabola_arc":
        # Доля вдоль параболы считается по ОТРЕЗКУ параметра: он идёт по
        # прямой, и развёртки по кругу здесь нет.
        low, high = shape.start_angle, shape.end_angle
        span = high - low
        if abs(span) < 1e-15:
            return 0.0
        return max(0.0, min(1.0,
                            (T.parabola_parameter(shape, point) - low) / span))
    if shape.kind == "ellipse_arc":
        sweep = geom2d.arc_sweep(shape.start_angle, shape.end_angle)
        if sweep < 1e-15:
            return 0.0
        offset = (T.ellipse_angle(shape, point) - shape.start_angle) % geom2d.TAU
        return max(0.0, min(1.0, offset / sweep))
    if shape.kind == "ellipse":
        # У эллипса собственный параметр — не полярный угол: точка равна
        # C + a·cos t·u + b·sin t·v. По полярному куски ложились бы
        # неравномерно, и середина куска уезжала бы с кривой.
        return (_at_parameter(shape, point) % geom2d.TAU) / geom2d.TAU
    angle = geom2d.angle_at(shape.center, point)
    if shape.kind == "circle":
        return (angle % geom2d.TAU) / geom2d.TAU
    sweep = geom2d.arc_sweep(shape.start_angle, shape.end_angle)
    if sweep < 1e-15:
        return 0.0
    offset = (angle - shape.start_angle) % geom2d.TAU
    return max(0.0, min(1.0, offset / sweep))


def _crossings(first: list, second: list) -> list:
    """Точки пересечения двух ломаных, без повторов."""
    found = []
    for a1, a2 in zip(first, first[1:]):
        for b1, b2 in zip(second, second[1:]):
            point = _segment_crossing(a1, a2, b1, b2)
            if point is None:
                continue
            if all(geom2d.distance(point, seen) > WELD for seen in found):
                found.append(point)
    return found


def _segment_crossing(a1, a2, b1, b2):
    """Пересечение двух отрезков, включая касание концами."""
    r = (a2[0] - a1[0], a2[1] - a1[1])
    s = (b2[0] - b1[0], b2[1] - b1[1])
    denominator = r[0] * s[1] - r[1] * s[0]
    if abs(denominator) < 1e-15:
        return None
    delta = (b1[0] - a1[0], b1[1] - a1[1])
    t = (delta[0] * s[1] - delta[1] * s[0]) / denominator
    u = (delta[0] * r[1] - delta[1] * r[0]) / denominator
    if not (-1e-9 <= t <= 1.0 + 1e-9 and -1e-9 <= u <= 1.0 + 1e-9):
        return None
    return (a1[0] + r[0] * t, a1[1] + r[1] * t)


def _lengths(outline: list) -> list:
    total, marks = 0.0, [0.0]
    for a, b in zip(outline, outline[1:]):
        total += geom2d.distance(a, b)
        marks.append(total)
    if total < 1e-15:
        return [0.0] * len(marks)
    return [value / total for value in marks]


def _parameter(outline: list, point) -> float:
    """Доля вдоль ломаной, ближайшая к точке."""
    marks = _lengths(outline)
    best, best_distance = 0.0, float("inf")
    for index, (a, b) in enumerate(zip(outline, outline[1:])):
        along = (b[0] - a[0], b[1] - a[1])
        length = along[0] ** 2 + along[1] ** 2
        if length < 1e-18:
            continue
        t = ((point[0] - a[0]) * along[0] + (point[1] - a[1]) * along[1]) / length
        t = max(0.0, min(1.0, t))
        near = (a[0] + along[0] * t, a[1] + along[1] * t)
        distance = geom2d.distance(near, point)
        if distance < best_distance:
            best_distance = distance
            best = marks[index] + (marks[index + 1] - marks[index]) * t
    return best


def _slice(outline: list, low: float, high: float) -> list:
    """Кусок ломаной между двумя долями длины."""
    marks = _lengths(outline)
    result = [_at(outline, marks, low)]
    for point, mark in zip(outline, marks):
        if low + 1e-12 < mark < high - 1e-12:
            result.append(point)
    result.append(_at(outline, marks, high))
    return result


def _point_on(sketch, segment, fraction: float):
    """Точка на объекте эскиза по доле его собственного параметра."""
    shape = T.describe(sketch, segment)
    if shape.kind == "line":
        return (shape.start[0] + (shape.end[0] - shape.start[0]) * fraction,
                shape.start[1] + (shape.end[1] - shape.start[1]) * fraction)
    if shape.kind == "spline":
        from . import bspline as bspline_module

        return bspline_module.value(shape.poles, shape.knots, shape.mults,
                                    shape.degree, fraction)
    if shape.kind == "hyperbola_arc":
        low, high = shape.start_angle, shape.end_angle
        return T.hyperbola_point(shape, low + (high - low) * fraction)
    if shape.kind == "parabola_arc":
        # Параметр параболы идёт по ПРЯМОЙ: доля откладывается на отрезке
        # от начала до конца, без развёртки по кругу. Через дугу эллипса
        # это считалось иначе, и концы кусков оказывались не на кривой —
        # граф не склеивался, и области не находились вовсе.
        low, high = shape.start_angle, shape.end_angle
        return T.parabola_point(shape, low + (high - low) * fraction)
    if shape.kind == "ellipse_arc":
        sweep = geom2d.arc_sweep(shape.start_angle, shape.end_angle)
        return T.ellipse_point(shape, shape.start_angle + sweep * fraction)
    if shape.kind == "ellipse":
        return T.ellipse_point(shape, geom2d.TAU * fraction)
    if shape.kind == "circle":
        return geom2d.point_at_angle(
            shape.center, shape.radius, geom2d.TAU * fraction)
    sweep = geom2d.arc_sweep(shape.start_angle, shape.end_angle)
    return geom2d.point_at_angle(
        shape.center, shape.radius, shape.start_angle + sweep * fraction)


def _at(outline: list, marks: list, value: float):
    if value <= 0.0:
        return outline[0]
    if value >= 1.0:
        return outline[-1]
    for index in range(len(marks) - 1):
        if marks[index] <= value <= marks[index + 1]:
            span = marks[index + 1] - marks[index]
            t = 0.0 if span < 1e-15 else (value - marks[index]) / span
            a, b = outline[index], outline[index + 1]
            return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)
    return outline[-1]


# --- граф и циклы ----------------------------------------------------------


def _key(point) -> tuple:
    return (round(point[0] / WELD), round(point[1] / WELD))


def _traverse(pieces: list[Piece]) -> tuple:
    """Обход всех граней разбиения. ``(полурёбра, циклы)``.

    Каждый кусок даёт два полуребра. В вершине следующим берётся то, что
    поворачивает максимально налево от пришедшего, — так обход обводит
    ровно одну грань планарного разбиения и ни одной лишней.
    """
    half_edges = []
    for piece in pieces:
        half_edges.append((piece, False, piece.points))
        half_edges.append((piece, True, piece.reversed_points))

    outgoing: dict[tuple, list] = {}
    for index, (_, _, points) in enumerate(half_edges):
        outgoing.setdefault(_key(points[0]), []).append(index)

    visited = [False] * len(half_edges)
    cycles: list[list] = []
    for start in range(len(half_edges)):
        if visited[start]:
            continue
        cycle, current = [], start
        while not visited[current]:
            visited[current] = True
            cycle.append(current)
            _, _, points = half_edges[current]
            arrival = _direction(points[-2], points[-1])
            candidates = outgoing.get(_key(points[-1]), [])
            if not candidates:
                cycle = []
                break
            current = _leftmost(half_edges, candidates, arrival, current)
            if current is None:
                cycle = []
                break
        if not cycle or current != start:
            continue
        cycles.append(cycle)
    return half_edges, cycles


def _loops(pieces: list[Piece]) -> list[Loop]:
    """Замкнутые циклы разбиения — те, что ограничивают площадь."""
    half_edges, cycles = _traverse(pieces)
    loops: list[Loop] = []
    for cycle in cycles:
        outline, parts = [], []
        for index in cycle:
            piece, _, points = half_edges[index]
            outline.extend(points[:-1])
            parts.append(piece)
        if len(outline) < 3:
            continue
        area = geom2d.signed_area(outline)
        if abs(area) < MIN_AREA:
            continue
        loops.append(Loop(outline, parts, area))
    return loops


def _open_pieces(pieces: list[Piece]) -> list[Piece]:
    """Куски, не лежащие ни на одном замкнутом контуре.

    Признак — оба полуребра куска в ОДНОМ цикле обхода: обход прошёл по
    нему туда и обратно, значит слева и справа от него одна и та же грань
    разбиения, и никакой контур он не ограничивает.
    """
    half_edges, cycles = _traverse(pieces)
    number = [-1] * len(half_edges)
    for index, cycle in enumerate(cycles):
        for half in cycle:
            number[half] = index
    found = []
    for position, piece in enumerate(pieces):
        forward, backward = number[2 * position], number[2 * position + 1]
        # −1 означает, что обход оборвался; на замкнутом контуре он не
        # обрывается, поэтому такой кусок тоже разомкнутый.
        if forward == backward or forward < 0 or backward < 0:
            found.append(piece)
    return found


def _stitch_chains(pieces: list[Piece]) -> list[list[Piece]]:
    """Связные группы кусков. Каждая группа — один разомкнутый контур.

    Порядок внутри группы не выстраивается: петлю из кривых собирает
    движок, и сортировка рёбер у него своя. Здесь важно только не
    смешать в одном контуре куски, которые нигде не соприкасаются.
    """
    owner: dict[tuple, int] = {}
    groups: dict[int, list] = {}
    for number, piece in enumerate(pieces):
        ends = {_key(piece.start), _key(piece.end)}
        meets = sorted({owner[end] for end in ends if end in owner})
        if not meets:
            groups[number] = [piece]
            for end in ends:
                owner[end] = number
            continue
        target = meets[0]
        groups[target].append(piece)
        for other in meets[1:]:
            groups[target].extend(groups.pop(other))
            for end, value in owner.items():
                if value == other:
                    owner[end] = target
        for end in ends:
            owner[end] = target
    return [groups[key] for key in sorted(groups)]


def _direction(a, b) -> float:
    return math.atan2(b[1] - a[1], b[0] - a[0])


def _leftmost(half_edges, candidates, arrival: float, current: int):
    """Следующее полуребро при обходе ВНУТРЕННЕЙ грани.

    В вершине из всех исходящих берётся то, что отстоит от направления
    «назад» на максимальный угол против часовой стрелки. Минимальный угол
    обводил бы грань снаружи: на квадрате, пройденном на восток, минимум
    указывает на юг, а внутренняя грань требует севера — из-за этого две
    пересекающиеся окружности давали одну область вместо трёх.

    Обратное полуребро того же куска берётся только если другого нет —
    иначе обход разворачивается на месте и цикл вырождается в линию.
    """
    incoming = arrival + math.pi
    best, best_turn = None, None
    twin = current ^ 1
    for index in candidates:
        if index == twin:
            continue
        _, _, points = half_edges[index]
        turn = (_direction(points[0], points[1]) - incoming) % geom2d.TAU
        if best_turn is None or turn > best_turn:
            best, best_turn = index, turn
    return best if best is not None else twin


def _regions(loops: list[Loop]) -> list[Region]:
    """Циклы → атомарные области с деревом вложенности (§4.8, §4.9).

    Внешняя граница разбиения обходится по часовой стрелке и областью не
    является: это «всё остальное поле», бесконечное снаружи.
    """
    filled = [loop for loop in loops if loop.area > 0.0]
    filled.sort(key=lambda loop: loop.absolute_area, reverse=True)

    regions: list[Region] = []
    for index, loop in enumerate(filled):
        inner = []
        for other in filled:
            if other is loop:
                continue
            if other.absolute_area >= loop.absolute_area:
                continue
            if not _point_in(_inner_point(other.outline, []), loop.outline):
                continue
            # Только НЕПОСРЕДСТВЕННО вложенные: петля внутри петли внутри
            # нашей — отверстие не в нас, а в промежуточной.
            if any(o is not other and o is not loop
                   and o.absolute_area < loop.absolute_area
                   and o.absolute_area > other.absolute_area
                   and _point_in(_inner_point(other.outline, []), o.outline)
                   and _point_in(_inner_point(o.outline, []), loop.outline)
                   for o in filled):
                continue
            inner.append(other)
        regions.append(Region(index, loop, inner))

    for region in regions:
        region.depth = sum(
            1 for other in regions
            if other is not region and other.outer.absolute_area > region.outer.absolute_area
            and _point_in(region.sample, other.outer.outline)
        )
    return [region for region in regions if region.area > MIN_AREA]


# --- вспомогательная геометрия ---------------------------------------------


def _point_in(point, outline: list) -> bool:
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


def _centroid(outline: list) -> tuple[float, float]:
    area = geom2d.signed_area(outline)
    if abs(area) < MIN_AREA:
        count = max(1, len(outline))
        return (sum(p[0] for p in outline) / count,
                sum(p[1] for p in outline) / count)
    cx = cy = 0.0
    count = len(outline)
    for index in range(count):
        x1, y1 = outline[index]
        x2, y2 = outline[(index + 1) % count]
        cross = x1 * y2 - x2 * y1
        cx += (x1 + x2) * cross
        cy += (y1 + y2) * cross
    return (cx / (6.0 * area), cy / (6.0 * area))


def _inner_point(outline: list, holes: list) -> tuple[float, float]:
    """Точка заведомо внутри области — и подальше от её границ.

    Луч пересекается НЕ ТОЛЬКО с наружным контуром, но и с отверстиями, а
    из просветов берётся самый широкий. Пока отверстия в счёт не шли,
    середина полного пролёта могла лечь ровно на грань отверстия: у
    прямоугольника 10…110 с вложенным до x = 60 точкой внутри объявлялась
    (60, 40) — на самой границе. Опознание области по такой точке
    оказывалось делом случая: строго внутри она не лежит ни в области, ни
    в отверстии.
    """
    # Запас от границ берётся от размера области: у детали в сотню
    # миллиметров и у детали в сотую доли миллиметра «близко к краю»
    # значит разное.
    span = max(max(point[0] for point in outline) - min(point[0] for point in outline),
               max(point[1] for point in outline) - min(point[1] for point in outline))
    margin = max(span * 1e-4, 1e-7)
    guess = _centroid(outline)
    if _outside_all(guess, outline, holes) \
            and _clearance(guess, outline, holes) > margin:
        return guess
    low = min(point[1] for point in outline)
    high = max(point[1] for point in outline)
    best = None
    for step in range(1, 12):
        y = low + (high - low) * step / 12.0
        marks = sorted(_row_crossings(outline, y)
                       + [value for hole in holes
                          for value in _row_crossings(hole.outline, y)])
        for first, second in zip(marks, marks[1:]):
            width = second - first
            if width <= 1e-9:
                continue
            middle = ((first + second) / 2.0, y)
            if not _outside_all(middle, outline, holes):
                continue
            if best is None or width > best[0]:
                best = (width, middle)
    return best[1] if best is not None else guess


def _outside_all(point, outline: list, holes: list) -> bool:
    """Точка внутри наружного контура и ни в одном из отверстий."""
    return _point_in(point, outline) and not any(
        _point_in(point, hole.outline) for hole in holes)


def _clearance(point, outline: list, holes: list) -> float:
    """Насколько точка удалена от ближайшей границы области.

    Точка НА границе формально не лежит ни внутри, ни снаружи: обход луча
    засчитывает её как придётся. Опознавать по такой точке область — дело
    случая, поэтому она не годится, даже если проверка на вхождение её
    пропустила.
    """
    best = None
    for loop in [outline] + [hole.outline for hole in holes]:
        for index in range(len(loop)):
            first, second = loop[index], loop[(index + 1) % len(loop)]
            distance = _distance_to_segment(point, first, second)
            if best is None or distance < best:
                best = distance
    return best if best is not None else 0.0


def _distance_to_segment(point, first, second) -> float:
    dx, dy = second[0] - first[0], second[1] - first[1]
    length = dx * dx + dy * dy
    if length <= 1e-18:
        return math.hypot(point[0] - first[0], point[1] - first[1])
    along = ((point[0] - first[0]) * dx + (point[1] - first[1]) * dy) / length
    along = max(0.0, min(1.0, along))
    return math.hypot(point[0] - (first[0] + along * dx),
                      point[1] - (first[1] + along * dy))


def _row_crossings(outline: list, y: float) -> list:
    values = []
    count = len(outline)
    for index in range(count):
        x1, y1 = outline[index]
        x2, y2 = outline[(index + 1) % count]
        if (y1 > y) != (y2 > y):
            values.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
    return values
