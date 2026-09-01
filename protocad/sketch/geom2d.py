"""Плоская геометрия эскиза: пересечения, углы, смещения.

Модуль намеренно чистый: ни решателя, ни OCCT, ни состояния. Только числа
на входе и числа на выходе. Отсечение, смещение и скругление в эскизе —
операции, где легче всего ошибиться на знаке или на выборе ветви, поэтому
их математика вынесена туда, где её можно проверить отдельно от всего
остального.

Соглашения:

* точка — кортеж ``(x, y)``;
* дуга задаётся центром, радиусом и углами начала и конца, **против часовой
  стрелки** от начала к концу; это же соглашение действует в OCCT и в
  решателях, и расхождение здесь стоило бы дуг, вывернутых наизнанку;
* параметр ``t`` вдоль отрезка: 0 в начале, 1 в конце.
"""

from __future__ import annotations

import math

TOLERANCE = 1e-9
TAU = 2.0 * math.pi

Point = tuple[float, float]


# --- элементарное ---


def distance(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def direction(a: Point, b: Point) -> Point:
    """Единичный вектор из ``a`` в ``b``. Для совпавших точек — (0, 0)."""
    length = distance(a, b)
    if length < TOLERANCE:
        return (0.0, 0.0)
    return ((b[0] - a[0]) / length, (b[1] - a[1]) / length)


def normal(vector: Point) -> Point:
    """Левая нормаль. Знак задаёт сторону смещения — менять его нельзя."""
    return (-vector[1], vector[0])


def add(a: Point, b: Point) -> Point:
    return (a[0] + b[0], a[1] + b[1])


def scale(a: Point, factor: float) -> Point:
    return (a[0] * factor, a[1] * factor)


def midpoint(a: Point, b: Point) -> Point:
    return ((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)


def normalize_angle(angle: float) -> float:
    """Угол в [0, 2π). Единая нормализация: сравнение углов из разных мест
    без неё даёт разные ответы для одного и того же направления."""
    return angle % TAU


def angle_at(center: Point, point: Point) -> float:
    return normalize_angle(math.atan2(point[1] - center[1], point[0] - center[0]))


def point_at_angle(center: Point, radius: float, angle: float) -> Point:
    return (center[0] + radius * math.cos(angle), center[1] + radius * math.sin(angle))


def arc_sweep(start_angle: float, end_angle: float) -> float:
    """Угловая протяжённость дуги против часовой стрелки. Полный круг для
    совпавших углов: дуга нулевой длины смысла не имеет."""
    sweep = normalize_angle(end_angle - start_angle)
    return TAU if sweep < TOLERANCE else sweep


def arc_contains(start_angle: float, end_angle: float, angle: float) -> bool:
    """Лежит ли угол на дуге. Обход против часовой от начала к концу."""
    return normalize_angle(angle - start_angle) <= arc_sweep(start_angle, end_angle) + 1e-9


# --- пересечения ---


def line_line(a1: Point, a2: Point, b1: Point, b2: Point) -> tuple[Point, float, float] | None:
    """Пересечение ПРЯМЫХ, несущих отрезки, с параметрами вдоль каждой.

    Возвращает точку и параметры ``ta``, ``tb``. Отрезки могут и не
    доставать друг до друга — проверять принадлежность должен вызывающий:
    у продления (``extend``) и у отсечения (``trim``) требования разные.
    """
    ax, ay = a2[0] - a1[0], a2[1] - a1[1]
    bx, by = b2[0] - b1[0], b2[1] - b1[1]
    denominator = ax * by - ay * bx
    if abs(denominator) < TOLERANCE:
        return None  # параллельны или вырождены
    dx, dy = b1[0] - a1[0], b1[1] - a1[1]
    ta = (dx * by - dy * bx) / denominator
    tb = (dx * ay - dy * ax) / denominator
    return ((a1[0] + ax * ta, a1[1] + ay * ta), ta, tb)


def line_circle(a1: Point, a2: Point, center: Point, radius: float) -> list[tuple[Point, float]]:
    """Пересечения прямой с окружностью: список точек с параметром вдоль прямой."""
    dx, dy = a2[0] - a1[0], a2[1] - a1[1]
    fx, fy = a1[0] - center[0], a1[1] - center[1]
    a = dx * dx + dy * dy
    if a < TOLERANCE:
        return []
    b = 2.0 * (fx * dx + fy * dy)
    c = fx * fx + fy * fy - radius * radius
    discriminant = b * b - 4.0 * a * c
    if discriminant < -TOLERANCE:
        return []
    discriminant = max(discriminant, 0.0)
    root = math.sqrt(discriminant)
    result = []
    for t in ((-b - root) / (2.0 * a), (-b + root) / (2.0 * a)):
        result.append(((a1[0] + dx * t, a1[1] + dy * t), t))
    if root < TOLERANCE:  # касание — одна точка, а не две совпавших
        return result[:1]
    return result


def circle_circle(c1: Point, r1: float, c2: Point, r2: float) -> list[Point]:
    """Пересечения двух окружностей."""
    span = distance(c1, c2)
    if span < TOLERANCE or span > r1 + r2 + TOLERANCE or span < abs(r1 - r2) - TOLERANCE:
        return []
    a = (r1 * r1 - r2 * r2 + span * span) / (2.0 * span)
    height_squared = r1 * r1 - a * a
    height = math.sqrt(max(height_squared, 0.0))
    base = (c1[0] + a * (c2[0] - c1[0]) / span, c1[1] + a * (c2[1] - c1[1]) / span)
    if height < TOLERANCE:
        return [base]
    offset = (-height * (c2[1] - c1[1]) / span, height * (c2[0] - c1[0]) / span)
    return [add(base, offset), add(base, scale(offset, -1.0))]


# --- проекции и попадания ---


def project_on_line(point: Point, a1: Point, a2: Point) -> tuple[Point, float]:
    """Проекция точки на прямую и параметр вдоль неё."""
    dx, dy = a2[0] - a1[0], a2[1] - a1[1]
    length_squared = dx * dx + dy * dy
    if length_squared < TOLERANCE:
        return a1, 0.0
    t = ((point[0] - a1[0]) * dx + (point[1] - a1[1]) * dy) / length_squared
    return ((a1[0] + dx * t, a1[1] + dy * t), t)


def distance_to_line(point: Point, a1: Point, a2: Point) -> float:
    """Расстояние до БЕСКОНЕЧНОЙ прямой через две точки.

    Отличается от расстояния до отрезка тем, что за концами не
    «притягивается» к ним. Нужно там, где прямая задаёт направление, а не
    протяжённость: малая полуось эллипса меряется от его большой оси, и
    третий щелчок не обязан попадать между её концами.
    """
    projection, _ = project_on_line(point, a1, a2)
    return distance(point, projection)


def distance_to_segment(point: Point, a1: Point, a2: Point) -> float:
    projection, t = project_on_line(point, a1, a2)
    if t <= 0.0:
        return distance(point, a1)
    if t >= 1.0:
        return distance(point, a2)
    return distance(point, projection)


def distance_to_arc(point: Point, center: Point, radius: float,
                    start_angle: float, end_angle: float) -> float:
    """Расстояние до дуги: до самой дуги, если угол на неё попадает, иначе до
    ближайшего конца. Без второй ветви щелчок мимо дуги «притягивался» бы к
    её продолжению, которого на экране нет."""
    angle = angle_at(center, point)
    if arc_contains(start_angle, end_angle, angle):
        return abs(distance(center, point) - radius)
    ends = (
        point_at_angle(center, radius, start_angle),
        point_at_angle(center, radius, end_angle),
    )
    return min(distance(point, end) for end in ends)


def distance_to_circle(point: Point, center: Point, radius: float) -> float:
    return abs(distance(center, point) - radius)


# --- построения ---


def offset_line(a1: Point, a2: Point, amount: float) -> tuple[Point, Point]:
    """Отрезок, смещённый по нормали. Знак задаёт сторону."""
    shift = scale(normal(direction(a1, a2)), amount)
    return add(a1, shift), add(a2, shift)


def fillet_lines(a1: Point, a2: Point, b1: Point, b2: Point, radius: float):
    """Скругление угла между отрезками.

    Возвращает ``(центр, точка касания на первом, точка касания на втором,
    угол начала, угол конца)`` или ``None``, если отрезки параллельны либо
    радиус не помещается.

    Направление обхода дуги выбирается так, чтобы она шла ОТ первого отрезка
    К второму по короткой стороне: иначе на месте скругления получается дуга
    в 300°, обходящая угол снаружи.
    """
    crossing = line_line(a1, a2, b1, b2)
    if crossing is None:
        return None
    corner = crossing[0]
    # Направления от угла вдоль каждого отрезка — в сторону дальнего конца.
    first = direction(corner, a1 if distance(corner, a1) > distance(corner, a2) else a2)
    second = direction(corner, b1 if distance(corner, b1) > distance(corner, b2) else b2)
    cosine = max(-1.0, min(1.0, first[0] * second[0] + first[1] * second[1]))
    angle = math.acos(cosine)
    if angle < TOLERANCE or abs(angle - math.pi) < TOLERANCE:
        return None  # отрезки на одной прямой — скруглять нечего
    setback = radius / math.tan(angle / 2.0)
    if setback > distance(corner, a1) + distance(corner, a2):
        return None  # не помещается
    tangent_a = add(corner, scale(first, setback))
    tangent_b = add(corner, scale(second, setback))
    bisector = direction((0.0, 0.0), add(first, second))
    center = add(corner, scale(bisector, radius / math.sin(angle / 2.0)))
    angle_a = angle_at(center, tangent_a)
    angle_b = angle_at(center, tangent_b)
    # Короткая сторона: если обход против часовой длиннее полуокружности,
    # значит концы надо поменять местами.
    if arc_sweep(angle_a, angle_b) > math.pi:
        angle_a, angle_b = angle_b, angle_a
        tangent_a, tangent_b = tangent_b, tangent_a
    return center, tangent_a, tangent_b, angle_a, angle_b


def polygon_points(center: Point, radius: float, sides: int,
                   start_angle: float = math.pi / 2.0,
                   inscribed: bool = True) -> list[Point]:
    """Вершины правильного многоугольника.

    ``inscribed`` — вписанный в окружность радиуса ``radius`` (вершины на
    окружности). Иначе описанный: радиус считается расстоянием до середины
    стороны, и вершины отодвигаются на 1/cos(π/n).
    """
    if sides < 3:
        raise ValueError("многоугольник — не меньше трёх сторон")
    effective = radius if inscribed else radius / math.cos(math.pi / sides)
    return [
        point_at_angle(center, effective, start_angle + index * TAU / sides)
        for index in range(sides)
    ]


def tangent_arc_center(anchor: Point, direction: Point, end: Point):
    """Центр дуги, касательной к направлению в точке ``anchor`` и проходящей
    через ``end``.

    Центр лежит на перпендикуляре к касательной в точке касания — это и
    есть условие касания. Расстояние по перпендикуляру находится из
    равенства радиусов: |C−anchor| = |C−end|.

    ``None``, если точки совпали или конец лежит на самой касательной: дуги
    через такую пару не существует, и подставлять «почти дугу» нельзя.
    """
    normal_direction = normal(direction)
    offset = (end[0] - anchor[0], end[1] - anchor[1])
    projection = offset[0] * normal_direction[0] + offset[1] * normal_direction[1]
    if abs(projection) < TOLERANCE:
        return None
    distance = (offset[0] ** 2 + offset[1] ** 2) / (2.0 * projection)
    return add(anchor, scale(normal_direction, distance))


def circle_through(a: Point, b: Point, c: Point):
    """Центр и радиус окружности через три точки. ``None`` для точек на прямой."""
    d = 2.0 * (a[0] * (b[1] - c[1]) + b[0] * (c[1] - a[1]) + c[0] * (a[1] - b[1]))
    if abs(d) < 1e-12:
        return None
    a2, b2, c2 = a[0] ** 2 + a[1] ** 2, b[0] ** 2 + b[1] ** 2, c[0] ** 2 + c[1] ** 2
    center = (
        (a2 * (b[1] - c[1]) + b2 * (c[1] - a[1]) + c2 * (a[1] - b[1])) / d,
        (a2 * (c[0] - b[0]) + b2 * (a[0] - c[0]) + c2 * (b[0] - a[0])) / d,
    )
    return center, distance(center, a)


def rotate_about(point: Point, center: Point, angle: float) -> Point:
    cosine, sine = math.cos(angle), math.sin(angle)
    dx, dy = point[0] - center[0], point[1] - center[1]
    return (center[0] + dx * cosine - dy * sine, center[1] + dx * sine + dy * cosine)


def mirror_about_line(point: Point, a1: Point, a2: Point) -> Point:
    projection, _ = project_on_line(point, a1, a2)
    return (2.0 * projection[0] - point[0], 2.0 * projection[1] - point[1])


def signed_area(points: list[Point]) -> float:
    """Площадь со знаком: положительная — обход против часовой стрелки.

    Нужна, чтобы отличить наружный контур от внутреннего и не сделать
    отверстие телом.
    """
    total = 0.0
    for index in range(len(points)):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % len(points)]
        total += x1 * y2 - x2 * y1
    return total / 2.0
