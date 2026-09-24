"""Площадь пересечения треугольников на плоскости — пачкой, без цикла по парам.

Площадь контакта двух плоских граней — это площадь пересечения их
разбиений на треугольники. Пар треугольников на плате в тысячу
компонентов — сотни тысяч, и отсечение по одной паре в Python заняло бы
секунды. Здесь отсечение (Сазерленд — Ходжмен: треугольник отсекается
тремя сторонами другого) идёт сразу по всем парам: цикл — только по
вершинам многоугольника, их не больше шести.

Разбиение плоской грани с прямыми краями точное, поэтому и площадь
точная; у дуг (края отверстий) ошибка — порядка прогиба разбиения.
"""

from __future__ import annotations

import numpy as np

#: Треугольник, отсечённый тремя полуплоскостями, — не больше шести вершин.
_MAX = 7


def triangle_overlap(first: np.ndarray, second: np.ndarray) -> np.ndarray:
    """Площади пересечения пар треугольников: ``first``, ``second`` — (K, 3, 2)."""
    first = np.asarray(first, np.float64)
    second = np.asarray(second, np.float64)
    count = len(first)
    if count == 0:
        return np.zeros(0)
    # Отсекающий треугольник — против часовой: тогда «внутри» — слева от
    # каждой стороны. Вырожденный (нулевой площади) даёт ноль сам.
    flip = _signed(second) < 0
    second = np.where(flip[:, None, None], second[:, ::-1], second)
    rows = np.arange(count)
    polygon = np.zeros((count, _MAX, 2))
    polygon[:, :3] = first
    size = np.full(count, 3)
    for side in range(3):
        start = second[:, side]
        edge = second[:, (side + 1) % 3] - start
        result = np.zeros_like(polygon)
        result_size = np.zeros(count, int)
        for index in range(_MAX - 1):
            live = index < size
            if not live.any():
                break
            point = polygon[:, index]
            following = polygon[rows, np.where(index + 1 < size, index + 1, 0)]
            here = _side(edge, point - start)
            there = _side(edge, following - start)
            inside = here >= 0.0
            keep = live & inside
            chosen = rows[keep]
            result[chosen, result_size[chosen]] = point[keep]
            result_size[chosen] += 1
            crossing = live & (inside != (there >= 0.0))
            chosen = rows[crossing]
            fraction = here[crossing] / (here[crossing] - there[crossing])
            result[chosen, result_size[chosen]] = point[crossing] + fraction[:, None] * (
                following[crossing] - point[crossing])
            result_size[chosen] += 1
        polygon, size = result, result_size
    area = np.zeros(count)
    for index in range(_MAX):
        live = index < size
        point = polygon[:, index]
        following = polygon[rows, np.where(index + 1 < size, index + 1, 0)]
        area += np.where(live, point[:, 0] * following[:, 1] - following[:, 0] * point[:, 1],
                         0.0)
    return np.abs(area) / 2.0


def _signed(triangles: np.ndarray) -> np.ndarray:
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    return ((b[:, 0] - a[:, 0]) * (c[:, 1] - a[:, 1])
            - (b[:, 1] - a[:, 1]) * (c[:, 0] - a[:, 0])) / 2.0


def _side(edge: np.ndarray, offset: np.ndarray) -> np.ndarray:
    return edge[:, 0] * offset[:, 1] - edge[:, 1] * offset[:, 0]


def candidate_pairs(low_a: np.ndarray, high_a: np.ndarray, low_b: np.ndarray,
                    high_b: np.ndarray, margin: float = 0.0,
                    limit: int = 4_000_000) -> tuple:
    """Пары треугольников, чьи габариты на плоскости пересекаются.

    Принимает габариты треугольников (их считают один раз на грань) и
    возвращает два массива номеров. Матрица сравнений строится кусками,
    чтобы большая грань против большой не съела память.
    """
    step = max(1, limit // max(1, len(low_b)))
    found_a, found_b = [], []
    for begin in range(0, len(low_a), step):
        end = begin + step
        overlap = np.all((low_a[begin:end, None, :] <= high_b[None, :, :] + margin)
                         & (low_b[None, :, :] <= high_a[begin:end, None, :] + margin),
                         axis=2)
        rows, columns = np.nonzero(overlap)
        found_a.append(rows + begin)
        found_b.append(columns)
    if not found_a:
        return np.zeros(0, int), np.zeros(0, int)
    return np.concatenate(found_a), np.concatenate(found_b)


class Grid:
    """Указатель треугольников большой грани по клеткам плоскости.

    Верхняя грань платы — тысячи треугольников, а спрашивают её о крошечных
    прямоугольниках под каждым компонентом. Перебирать все треугольники на
    каждый вопрос — это и было узким местом; клетка отдаёт только соседей.
    """

    def __init__(self, low: np.ndarray, high: np.ndarray, cells: int = 0):
        self.low, self.high = low, high
        count = len(low)
        cells = cells or max(1, int(np.sqrt(count / 4.0)))
        self.origin = low.min(axis=0)
        span = np.maximum(high.max(axis=0) - self.origin, 1e-9)
        self.size = span / cells
        self.cells = cells
        first = self._cell(low)
        last = self._cell(high)
        owners, keys = [], []
        for index in range(count):
            for column in range(first[index, 0], last[index, 0] + 1):
                for row in range(first[index, 1], last[index, 1] + 1):
                    owners.append(index)
                    keys.append(column * cells + row)
        keys = np.asarray(keys, np.int64)
        owners = np.asarray(owners, np.int64)
        order = np.argsort(keys, kind="stable")
        self.keys, self.owners = keys[order], owners[order]
        self.starts = np.searchsorted(self.keys, np.arange(cells * cells + 1))

    def _cell(self, points: np.ndarray) -> np.ndarray:
        cell = np.floor((points - self.origin) / self.size).astype(np.int64)
        return np.clip(cell, 0, self.cells - 1)

    def query(self, low: np.ndarray, high: np.ndarray, margin: float = 0.0) -> np.ndarray:
        """Номера треугольников, чьи габариты пересекают прямоугольник."""
        first = self._cell(low - margin)
        last = self._cell(high + margin)
        found = []
        for column in range(first[0], last[0] + 1):
            begin = column * self.cells
            found.append(self.owners[self.starts[begin + first[1]]:
                                     self.starts[begin + last[1] + 1]])
        if not found:
            return np.zeros(0, np.int64)
        found = np.unique(np.concatenate(found))
        keep = np.all((self.low[found] <= high + margin)
                      & (low - margin <= self.high[found]), axis=1)
        return found[keep]


#: С какого числа треугольников грани заводится указатель.
GRID_FROM = 256


def pairs_with_grid(low_a, high_a, grid: Grid, margin: float = 0.0) -> tuple:
    """То же, что `candidate_pairs`, когда у второй грани есть указатель."""
    found_a, found_b = [], []
    for index in range(len(low_a)):
        near = grid.query(low_a[index], high_a[index], margin)
        if len(near):
            found_a.append(np.full(len(near), index))
            found_b.append(near)
    if not found_a:
        return np.zeros(0, int), np.zeros(0, int)
    return np.concatenate(found_a), np.concatenate(found_b)
