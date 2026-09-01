"""Кривая Безье-сплайн: вычисление точки, касательной и построение по точкам.

Своя математика здесь нужна по той же причине, по которой у эскиза своя
геометрия вообще: сплайн надо рисовать, разбирать на области и мерить ДО
того, как построено хоть одно тело, и не зависеть от того, удастся ли его
построить. Ядро при этом получает те же полюсы и узлы и строит точную
кривую само.

Хранится сплайн так же, как в PlaneGCS и в ядре: **полюсы, узлы с
кратностями и степень**. Полюсы — обычные точки эскиза, поэтому они
двигаются мышью и держатся связями; всё остальное — числа.

Через точки кривая проводится отдельным построением: узлы выбираются по
длинам хорд, а полюсы находятся решением трёхдиагональной системы. Это
классический интерполяционный кубический сплайн, у него непрерывна вторая
производная — то есть кривизна не прыгает на стыках. Проверяется это
числом: кривая проходит через заданные точки с невязкой порядка 1e-15.
"""

from __future__ import annotations

import math

#: Степень по умолчанию. Третья: ниже кривизна рвётся на стыках, выше —
#: кривая начинает «гулять» между точками, и править её мышью мучительно.
DEGREE = 3


def clamped_knots(count: int, degree: int = DEGREE) -> tuple:
    """Узлы и кратности для незамкнутого сплайна по числу полюсов.

    Концевые узлы повторяются ``degree + 1`` раз — тогда кривая начинается
    в первом полюсе и кончается в последнем. Без этого она стартует где-то
    внутри оболочки, и «начало» контура оказывается не там, где нарисовано.
    """
    inner = count - degree - 1
    if inner < 0:
        raise ValueError(
            f"полюсов {count}: для степени {degree} нужно не меньше "
            f"{degree + 1}")
    knots = [0.0] + [(index + 1) / (inner + 1) for index in range(inner)] + [1.0]
    mults = [degree + 1] + [1] * inner + [degree + 1]
    return tuple(knots), tuple(mults)


def _flat(knots, mults) -> list:
    values = []
    for knot, times in zip(knots, mults):
        values.extend([float(knot)] * int(times))
    return values


def value(poles, knots, mults, degree: int, t: float) -> tuple:
    """Точка кривой при параметре ``t`` от 0 до 1 (алгоритм де Бура)."""
    point, _ = _de_boor(poles, knots, mults, degree, t)
    return point


def tangent(poles, knots, mults, degree: int, t: float) -> tuple:
    """Производная по параметру. Нужна для уточнения пересечений."""
    _, slope = _de_boor(poles, knots, mults, degree, t)
    return slope


def _de_boor(poles, knots, mults, degree: int, t: float) -> tuple:
    """Точка и производная. Возвращает пару, чтобы не считать дважды.

    Производная берётся не численно: разностная производная у сплайна с
    кратными узлами теряет знаки на стыках, и уточнение пересечений
    Ньютоном на ней разваливается.
    """
    full = _flat(knots, mults)
    count = len(poles)
    low, high = full[degree], full[count]
    t = min(max(float(t), 0.0), 1.0)
    u = low + (high - low) * t

    # Пролёт, в котором лежит параметр. Последний берётся включительно:
    # иначе конец кривой попадает в пустой пролёт и точка не считается.
    span = degree
    while span < count - 1 and u >= full[span + 1]:
        span += 1

    points = [tuple(poles[span - degree + index]) for index in range(degree + 1)]
    # Производная кривой степени p — это кривая степени p−1 по разностям
    # полюсов; её и ведём рядом тем же алгоритмом.
    slopes = []
    for index in range(degree):
        first = poles[span - degree + index]
        second = poles[span - degree + index + 1]
        gap = full[span + index + 1] - full[span - degree + index + 1]
        scale = degree / gap if abs(gap) > 1e-15 else 0.0
        slopes.append(((second[0] - first[0]) * scale,
                       (second[1] - first[1]) * scale))

    for level in range(1, degree + 1):
        for index in range(degree, level - 1, -1):
            left = full[span - degree + index]
            right = full[span + index - level + 1]
            gap = right - left
            share = 0.0 if abs(gap) < 1e-15 else (u - left) / gap
            before, after = points[index - 1], points[index]
            points[index] = (before[0] + (after[0] - before[0]) * share,
                             before[1] + (after[1] - before[1]) * share)
        if level <= degree - 1:
            for index in range(degree - 1, level - 1, -1):
                left = full[span - degree + index + 1]
                right = full[span + index - level + 1]
                gap = right - left
                share = 0.0 if abs(gap) < 1e-15 else (u - left) / gap
                before, after = slopes[index - 1], slopes[index]
                slopes[index] = (before[0] + (after[0] - before[0]) * share,
                                 before[1] + (after[1] - before[1]) * share)

    slope = slopes[degree - 1] if degree else (0.0, 0.0)
    # Параметр наружу идёт от 0 до 1, поэтому производная пересчитывается
    # в ту же шкалу.
    stretch = high - low
    return points[degree], (slope[0] * stretch, slope[1] * stretch)


def outline(poles, knots, mults, degree: int, facets: int = 96) -> list:
    """Ломаная по кривой — для показа, замера и разбора областей."""
    return [value(poles, knots, mults, degree, index / facets)
            for index in range(facets + 1)]


def poles_through(points, degree: int = DEGREE) -> tuple:
    """Полюсы кривой, ПРОХОДЯЩЕЙ через заданные точки.

    Узлы расставляются по длинам хорд: на длинном участке параметр идёт
    дольше. При равномерной расстановке кривая на неравных промежутках
    выгибается наружу, и это видно глазом.

    Возвращает ``(полюсы, узлы, кратности)``. Степень ниже третьей
    обрабатывается ломаной: у неё полюсы и есть точки.
    """
    points = [tuple(map(float, item)) for item in points]
    count = len(points)
    if count < 2:
        raise ValueError("для кривой нужно не меньше двух точек")
    # Степень не может быть выше, чем позволяет число точек: для кубики
    # нужно хотя бы четыре. Молча взять меньше точек нельзя, а отказать —
    # значит запретить провести кривую через три; поэтому понижается
    # степень, и об этом говорит возвращаемая кратность.
    degree = max(1, min(degree, count - 1))

    times = _chord_times(points)
    knots, mults = _averaged_knots(times, degree, count)
    full = _flat(knots, mults)
    # Полюсов ровно столько же, сколько точек: система квадратная, и её
    # первая и последняя строки сразу дают концы кривой — при зажатых
    # узлах базис на концах равен единице ровно у крайнего полюса.
    rows = [_basis_row(full, count, degree, times[index])
            for index in range(count)]
    return tuple(_solve(rows, points)), knots, mults


def _averaged_knots(times, degree: int, count: int) -> tuple:
    """Узлы по правилу усреднения де Бура.

    Внутренний узел — среднее ``degree`` подряд идущих параметров точек.
    Правило не украшение: при нём система интерполяции невырождена, а при
    равномерных узлах на неравных промежутках она вырождается, и кривую
    провести нельзя вовсе.
    """
    inner = []
    for index in range(1, count - degree):
        inner.append(sum(times[index:index + degree]) / degree)
    knots = [0.0, *inner, 1.0]
    mults = [degree + 1, *[1] * len(inner), degree + 1]
    return tuple(knots), tuple(mults)


def _chord_times(points) -> list:
    """Параметры точек по длинам хорд, от 0 до 1."""
    lengths = [0.0]
    for first, second in zip(points, points[1:]):
        lengths.append(lengths[-1] + math.dist(first, second))
    total = lengths[-1]
    if total <= 1e-12:
        return [index / (len(points) - 1) for index in range(len(points))]
    return [length / total for length in lengths]


def _basis_row(full, count: int, degree: int, u: float) -> list:
    """Значения всех базисных функций в точке. Строка системы."""
    low, high = full[degree], full[count]
    value_at = low + (high - low) * min(max(u, 0.0), 1.0)
    row = [0.0] * count
    span = degree
    while span < count - 1 and value_at >= full[span + 1]:
        span += 1
    weights = [0.0] * (degree + 1)
    weights[0] = 1.0
    left = [0.0] * (degree + 1)
    right = [0.0] * (degree + 1)
    for level in range(1, degree + 1):
        left[level] = value_at - full[span + 1 - level]
        right[level] = full[span + level] - value_at
        saved = 0.0
        for index in range(level):
            gap = right[index + 1] + left[level - index]
            share = 0.0 if abs(gap) < 1e-15 else weights[index] / gap
            weights[index] = saved + right[index + 1] * share
            saved = left[level - index] * share
        weights[level] = saved
    for index in range(degree + 1):
        row[span - degree + index] = weights[index]
    return row


def _solve(rows, right) -> list:
    """Решить систему методом Гаусса. Размер здесь — единицы неизвестных.

    Строк столько, сколько внутренних точек, и это редко больше десятка:
    заводить ради них разреженное решение значило бы усложнить то, что
    считается за микросекунды.
    """
    size = len(rows)
    matrix = [list(row) + [right[index][0], right[index][1]]
              for index, row in enumerate(rows)]
    for column in range(size):
        pivot = max(range(column, size), key=lambda r: abs(matrix[r][column]))
        if abs(matrix[pivot][column]) < 1e-12:
            raise ValueError("точки не годятся для кривой: система вырождена")
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        head = matrix[column]
        for row in range(size):
            if row == column:
                continue
            factor = matrix[row][column] / head[column]
            if factor == 0.0:
                continue
            for position in range(column, size + 2):
                matrix[row][position] -= factor * head[position]
    return [(matrix[index][size] / matrix[index][index],
             matrix[index][size + 1] / matrix[index][index])
            for index in range(size)]


# --- разрез кривой ----------------------------------------------------------
#
# Отсечение сплайна — это не «нарисовать покороче». Кусок кривой обязан
# СОВПАДАТЬ с исходной на всём своём протяжении, иначе отсечение молча
# меняет форму контура, а вместе с ней площадь и объём детали. Поэтому
# режется он вставкой узлов (алгоритм Бёма): при кратности узла, равной
# степени, кривая проходит через полюс, и в этом месте её можно разнять на
# две, ничего не изменив.


def _collapse(values, degree: int) -> tuple:
    """Плоский узловой вектор → узлы с кратностями."""
    knots, mults = [], []
    for value_ in values:
        if knots and abs(value_ - knots[-1]) < 1e-12:
            mults[-1] += 1
        else:
            knots.append(float(value_))
            mults.append(1)
    return tuple(knots), tuple(mults)


def _multiplicity(flat, t: float) -> int:
    return sum(1 for value_ in flat if abs(value_ - t) < 1e-12)


def _span(flat, degree: int, t: float) -> int:
    """Номер промежутка, в который попал параметр."""
    count = len(flat) - degree - 1
    if t >= flat[count]:
        return count - 1
    if t <= flat[degree]:
        return degree
    low, high = degree, count
    while high - low > 1:
        middle = (low + high) // 2
        if t < flat[middle]:
            high = middle
        else:
            low = middle
    return low


def insert(poles, knots, mults, degree: int, t: float, times: int = 1):
    """Вставить узел ``t`` заданное число раз, НЕ меняя кривой.

    Возвращает ``(полюсы, узлы, кратности)``. Форма кривой при этом та же
    до последнего знака — меняется только её запись. На этом и держится
    разрез: вставили узел степень раз, и кривая стала проходить ровно
    через один из полюсов.
    """
    flat = _flat(knots, mults)
    points = [tuple(float(value_) for value_ in point) for point in poles]
    for _ in range(int(times)):
        span = _span(flat, degree, t)
        fresh = list(points[:span - degree + 1])
        for index in range(span - degree + 1, span + 1):
            low, high = flat[index], flat[index + degree]
            weight = 0.0 if high - low < 1e-15 else (t - low) / (high - low)
            before = points[index - 1]
            after = points[index]
            fresh.append(tuple((1.0 - weight) * before[k] + weight * after[k]
                               for k in range(len(after))))
        fresh.extend(points[span:])
        points = fresh
        flat = flat[:span + 1] + [float(t)] + flat[span + 1:]
    knots_out, mults_out = _collapse(flat, degree)
    return points, knots_out, mults_out


def piece(poles, knots, mults, degree: int, start: float, end: float):
    """Кусок кривой между двумя параметрами. ``(полюсы, узлы, кратности)``.

    Узлы куска переводятся обратно в 0…1: у эскиза сплайн всегда задан на
    этом промежутке, и кусок обязан выглядеть так же, как любой другой
    сплайн, — иначе его нельзя ни решать, ни строить, ни записать.
    """
    if end - start < 1e-12:
        raise ValueError("пустой кусок кривой")
    points, knots_out, mults_out = poles, knots, mults
    for value_ in (start, end):
        if value_ <= 1e-12 or value_ >= 1.0 - 1e-12:
            continue
        flat = _flat(knots_out, mults_out)
        already = _multiplicity(flat, value_)
        if already < degree:
            points, knots_out, mults_out = insert(
                points, knots_out, mults_out, degree, value_,
                degree - already)

    flat = _flat(knots_out, mults_out)
    # Полюсы куска берутся не по номерам «на глаз», а по НОСИТЕЛЮ: полюс
    # `i` влияет на кривую на промежутке ``U[i] … U[i+p+1]``, и в кусок
    # входят те, чей носитель этот кусок задевает. Считать номера иначе —
    # значит каждый раз выводить их заново и каждый раз ошибаться.
    span_start = start if start > 1e-12 else flat[0]
    span_end = end if end < 1.0 - 1e-12 else flat[-1]
    first = next(index for index in range(len(points))
                 if flat[index + degree + 1] > span_start + 1e-12)
    last = max(index for index in range(len(points))
               if flat[index] < span_end - 1e-12)
    taken = points[first:last + 1]

    inner = [value_ for value_ in flat
             if span_start + 1e-12 < value_ < span_end - 1e-12]
    span = span_end - span_start
    scaled = [min(1.0, max(0.0, (value_ - span_start) / span))
              for value_ in inner]
    whole = [0.0] * (degree + 1) + scaled + [1.0] * (degree + 1)
    if len(whole) != len(taken) + degree + 1:
        # Число узлов и полюсов связано жёстко: разошлись — значит кусок
        # выделен неверно, и молча отдать такую кривую нельзя.
        raise ValueError(
            f"кусок сплайна не собрался: полюсов {len(taken)}, "
            f"узлов {len(whole)}, нужно {len(taken) + degree + 1}")
    knots_piece, mults_piece = _collapse(whole, degree)
    return taken, knots_piece, mults_piece
