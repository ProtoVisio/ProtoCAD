"""Проверки прибора: контакты с площадью, зазоры, пересечения, одиночки.

Для теплового расчёта важно не «касаются ли детали», а ГДЕ и НАСКОЛЬКО:
тепловое сопротивление контакта обратно пропорционально его площади. Поэтому
контакт ищется по граням:

* **плоский контакт** — две плоские грани навстречу друг другу в одной
  плоскости (с допуском ``touch``); площадь — пересечение их разбиений на
  треугольники (`overlap.py`);
* **соосный контакт** — вал в отверстии того же радиуса; площадь — длина
  перекрытия на дугу охвата;
* **зазор** — те же грани, но на расстоянии от ``touch`` до ``gap``: так
  выглядит компонент, висящий над платой на 0,3 мм, — в расчёте между ними
  окажется воздух, и об этом надо знать;
* **пересечение** — объём общей части по булевой операции ядра. Она
  дорогая, поэтому делается только там, где грани действительно могут
  пересекаться: грань, целиком лежащая по свою сторону плоской грани
  соседа, её не пересекает. Компонент на плате — это как раз такой
  случай, и тысяча компонентов не даёт тысячи булевых операций.

Итог — список контактов, одиночки (деталь не касается ничего: в расчёте
она будет греться без отвода тепла) и группы, не связанные с остальным
прибором (плата «висит» — нет стоек).

Что НЕ находится, сказано честно: контакт по конусам (потайная головка в
зенковке) и по криволинейным поверхностям не считается — такие пары
показываются как касание без площади, если нет плоского контакта рядом.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from .. import kernel
from .model import EXCLUDED, FASTENER, Device, Unit
from .overlap import GRID_FROM, Grid, candidate_pairs, pairs_with_grid, triangle_overlap

TOUCH = "контакт"
GAP = "зазор"
CLASH = "пересечение"

#: Параллельность нормалей и осей (1 − косинус).
PARALLEL = 1e-6


@dataclass
class Contact:
    """Контакт, зазор или пересечение двух единиц."""

    first: str
    second: str
    kind: str
    #: Площадь, мм²: контакта или зазора; у пересечения — площадь контакта
    #: рядом с ним, если есть.
    area: float = 0.0
    #: Зазор, мм (средний по площади); у контакта — ноль.
    distance: float = 0.0
    #: Объём общей части, мм³ (у пересечения).
    volume: float = 0.0
    #: Где: середина пятна контакта или общей части, мм.
    centre: tuple = (0.0, 0.0, 0.0)
    #: Нормаль пятна (от первой к второй) — у плоского контакта.
    normal: tuple = (0.0, 0.0, 0.0)
    #: Сколько граней участвует: [(грань первой, грань второй, площадь)].
    faces: list = field(default_factory=list)
    note: str = ""

    @property
    def key(self) -> str:
        return pair_key(self.first, self.second)


def pair_key(first: str, second: str) -> str:
    return "|".join(sorted((first, second)))


@dataclass
class Report:
    """Итог проверки прибора."""

    contacts: list = field(default_factory=list)
    #: Единицы без единого контакта.
    isolated: list = field(default_factory=list)
    #: Группы единиц, связанные контактами; первая — основная.
    groups: list = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    settings: dict = field(default_factory=dict)

    def of_kind(self, kind: str) -> list:
        return [item for item in self.contacts if item.kind == kind]

    def of_unit(self, key: str) -> list:
        return [item for item in self.contacts if key in (item.first, item.second)]


# --- сведения о гранях -----------------------------------------------------------


@dataclass
class _Faces:
    """Грани определения в его координатах."""

    kind: np.ndarray          # 1 — плоскость, 2 — цилиндр, 0 — прочее
    direction: np.ndarray     # нормаль наружу / ось
    point: np.ndarray         # точка плоскости / оси
    radius: np.ndarray
    convex: np.ndarray
    area: np.ndarray          # по разбиению
    triangles: np.ndarray     # (T, 3, 3)
    owner: np.ndarray         # номер грани у треугольника
    first: np.ndarray         # первый треугольник грани
    count: np.ndarray         # число треугольников грани
    low: np.ndarray           # габарит грани
    high: np.ndarray
    volume: float
    shape: object


def _faces_of(shape, deflection: float) -> _Faces:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane
    from OCP.TopAbs import TopAbs_REVERSED

    from .shapes import _convex

    positions, _normals, owners = kernel.tessellate_faces(shape, deflection, 0.3)
    triangles = np.asarray(positions, np.float64).reshape(-1, 3, 3)
    owner = np.asarray(owners[::3], np.int64)
    faces = list(kernel.iter_faces(shape))
    total = len(faces)
    kind = np.zeros(total, np.int8)
    direction = np.zeros((total, 3))
    point = np.zeros((total, 3))
    radius = np.zeros(total)
    convex = np.zeros(total, bool)
    for index, face in enumerate(faces):
        adaptor = BRepAdaptor_Surface(face, True)
        surface = adaptor.GetType()
        if surface == GeomAbs_Plane:
            axis = adaptor.Plane().Axis()
            normal = _vec(axis.Direction())
            if face.Orientation() == TopAbs_REVERSED:
                normal = -normal
            kind[index], direction[index], point[index] = 1, normal, _pnt(axis.Location())
        elif surface == GeomAbs_Cylinder:
            cylinder = adaptor.Cylinder()
            axis = cylinder.Axis()
            data = {"direction": _vec(axis.Direction()), "point": _pnt(axis.Location())}
            kind[index], direction[index], point[index] = 2, data["direction"], data["point"]
            radius[index] = cylinder.Radius()
            convex[index] = _convex(face, adaptor, data)
    sides = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    tri_area = np.linalg.norm(sides, axis=1) / 2.0
    area = np.bincount(owner, weights=tri_area, minlength=total)[:total]
    order = np.argsort(owner, kind="stable")
    triangles, owner = triangles[order], owner[order]
    count = np.bincount(owner, minlength=total)[:total]
    first = np.concatenate([[0], np.cumsum(count)[:-1]]) if total else np.zeros(0, int)
    low = np.full((total, 3), np.inf)
    high = np.full((total, 3), -np.inf)
    if len(triangles):
        np.minimum.at(low, owner, triangles.min(axis=1))
        np.maximum.at(high, owner, triangles.max(axis=1))
    return _Faces(kind, direction, point, radius, convex, area, triangles, owner,
                  first, count, low, high, abs(kernel.volume(shape)), shape)


def _vec(direction) -> np.ndarray:
    return np.array([direction.X(), direction.Y(), direction.Z()])


def _pnt(point) -> np.ndarray:
    return np.array([point.X(), point.Y(), point.Z()])


@dataclass
class _Placed:
    """Грани единицы в координатах прибора."""

    unit: Unit
    faces: _Faces
    rotation: np.ndarray
    shift: np.ndarray
    direction: np.ndarray
    point: np.ndarray
    offset: np.ndarray        # n·p у плоскостей
    low: np.ndarray           # габариты граней
    high: np.ndarray
    box: tuple                # габарит единицы
    planes: np.ndarray = None
    cylinders: np.ndarray = None
    #: Посчитанное по граням: треугольники в координатах прибора и на
    #: плоскости грани. Верхняя грань платы нужна тысяче компонентов —
    #: считать её тысячу раз нельзя.
    _world: dict = field(default_factory=dict)
    _flat: dict = field(default_factory=dict)
    _grids: dict = field(default_factory=dict)
    _shape: object = None

    def world_shape(self):
        if self._shape is None:
            from ..assembly.export import _location

            self._shape = self.faces.shape.Moved(_location(self.unit.matrix))
        return self._shape

    def grid(self, face: int):
        """Указатель треугольников большой грани; у маленькой — ``None``."""
        if self.faces.count[face] < GRID_FROM:
            return None
        found = self._grids.get(face)
        if found is None:
            _flat, low, high = self.flat(face)
            found = Grid(low, high)
            self._grids[face] = found
        return found

    def triangles(self, face: int) -> np.ndarray:
        found = self._world.get(face)
        if found is None:
            begin = self.faces.first[face]
            local = self.faces.triangles[begin:begin + self.faces.count[face]]
            found = local @ self.rotation.T + self.shift
            self._world[face] = found
        return found

    def flat(self, face: int) -> tuple:
        """Треугольники плоской грани на её плоскости и их габариты.

        Базис плоскости берётся по нормали, приведённой к одному знаку, —
        тогда у двух встречных граней он общий и обе проекции можно
        посчитать один раз.
        """
        found = self._flat.get(face)
        if found is None:
            u, v = _basis(_canonical(self.direction[face]))
            triangles = self.triangles(face)
            flat = np.stack([triangles @ u, triangles @ v], axis=-1)
            found = (flat, flat.min(axis=1), flat.max(axis=1))
            self._flat[face] = found
        return found


def _placed(unit: Unit, faces: _Faces) -> _Placed:
    rotation = unit.matrix[:3, :3]
    shift = unit.matrix[:3, 3]
    direction = faces.direction @ rotation.T
    point = faces.point @ rotation.T + shift
    offset = np.einsum("ij,ij->i", direction, point)
    corners = _corners(faces.low, faces.high)
    moved = corners @ rotation.T + shift
    low, high = moved.min(axis=1), moved.max(axis=1)
    finite = np.isfinite(faces.low).all(axis=1)
    low[~finite], high[~finite] = np.inf, -np.inf
    box = (low[finite].min(axis=0), high[finite].max(axis=0)) if finite.any() else (
        np.zeros(3), np.zeros(3))
    return _Placed(unit, faces, rotation, shift, direction, point, offset, low, high, box,
                   planes=np.nonzero(faces.kind == 1)[0],
                   cylinders=np.nonzero(faces.kind == 2)[0])


_SIGNS = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], float)


def _corners(low, high) -> np.ndarray:
    low = np.where(np.isfinite(low), low, 0.0)
    high = np.where(np.isfinite(high), high, 0.0)
    return low[:, None, :] + _SIGNS[None, :, :] * (high - low)[:, None, :]


# --- проверка -----------------------------------------------------------------------


def check(device: Device, touch: float = 0.01, gap: float = 0.5,
          min_area: float = 0.01, deflection: float = 0.05,
          clashes: bool = True, progress=None) -> Report:
    """Проверить прибор: контакты, зазоры, пересечения, одиночки."""
    started = time.perf_counter()
    report = Report(settings={"touch": touch, "gap": gap, "min_area": min_area,
                              "deflection": deflection})
    units = [unit for unit in device.units.values() if unit.role != EXCLUDED]
    cache = device.__dict__.setdefault("_contact_faces", {})
    placed = []
    for unit in units:
        shape = device.shape_of(unit.item)
        if shape is None:
            continue
        key = (unit.item.stable_id, id(shape), deflection)
        if key not in cache:
            cache[key] = _faces_of(shape, deflection)
        placed.append(_placed(unit, cache[key]))
    report.stats["faces_s"] = time.perf_counter() - started

    pairs = _broad_phase(placed, gap)
    report.stats["pairs"] = len(pairs)
    batch = _Batch()
    records = []
    deep = []
    for first, second in pairs:
        a, b = placed[first], placed[second]
        _planar(a, b, touch, gap, batch, records)
        _coaxial(a, b, touch, gap, records)
        if clashes:
            deep.append((a, b))
    areas = batch.areas()
    report.stats["triangle_pairs"] = batch.size
    contacts = _collect(records, areas, touch, min_area)
    report.stats["contacts_s"] = time.perf_counter() - started
    counters = {"booleans": 0, "classified": 0}
    if clashes:
        # Вопросы «не внутри ли целиком» копятся и решаются пачкой на
        # каждую объемлющую деталь: у основания корпуса их тысяча.
        inside_queries: dict = {}
        for index, (a, b) in enumerate(deep):
            clash = _clash(a, b, touch, counters, inside_queries)
            if clash is not None:
                contacts.setdefault(pair_key(a.unit.key, b.unit.key), []).append(clash)
            if progress is not None and index % 50 == 0:
                progress(index, len(deep))
        for clash in _resolve_inside(inside_queries, counters):
            contacts.setdefault(pair_key(clash.first, clash.second), []).append(clash)
    report.contacts = _merge(contacts)
    report.stats.update(counters)
    _connectivity(report, [item.unit for item in placed])
    report.stats["total_s"] = time.perf_counter() - started
    return report


def _broad_phase(placed: list, gap: float) -> list:
    """Пары единиц, чьи габариты сближены не больше чем на ``gap``."""
    if not placed:
        return []
    low = np.array([item.box[0] for item in placed]) - gap
    high = np.array([item.box[1] for item in placed]) + gap
    order = np.argsort(low[:, 0])
    pairs = []
    active: list = []
    for index in order:
        active = [other for other in active if high[other, 0] >= low[index, 0]]
        if active:
            others = np.array(active)
            touching = np.all((low[others] <= high[index]) & (low[index] <= high[others]),
                              axis=1)
            for other in others[touching]:
                pairs.append((min(index, other), max(index, other)))
        active.append(index)
    return pairs


class _Batch:
    """Пары треугольников всех граней — одним заходом в отсечение."""

    def __init__(self):
        self.first, self.second, self.record = [], [], []
        self.size = 0

    def add(self, first2d, second2d, record: int) -> None:
        if len(first2d) == 0:
            return
        self.first.append(first2d)
        self.second.append(second2d)
        self.record.append(np.full(len(first2d), record))
        self.size += len(first2d)

    def areas(self) -> np.ndarray:
        if not self.first:
            return np.zeros(0)
        first = np.concatenate(self.first)
        second = np.concatenate(self.second)
        record = np.concatenate(self.record)
        result = np.zeros(record.max() + 1)
        step = 200_000
        for begin in range(0, len(first), step):
            area = triangle_overlap(first[begin:begin + step], second[begin:begin + step])
            np.add.at(result, record[begin:begin + step], area)
        return result


def _canonical(normal) -> np.ndarray:
    """Нормаль с первой ненулевой составляющей больше нуля."""
    for value in normal:
        if abs(value) > 1e-9:
            return normal if value > 0 else -normal
    return normal


_BASES: dict = {}


def _basis(normal) -> tuple:
    """Базис плоскости по нормали. Векторное произведение — вручную:
    `np.cross` на трёх числах в сотни раз медленнее арифметики."""
    key = tuple(np.round(normal, 9))
    found = _BASES.get(key)
    if found is None:
        x, y, z = (float(value) for value in normal)
        hx, hy, hz = (1.0, 0.0, 0.0) if abs(x) < 0.9 else (0.0, 1.0, 0.0)
        first = np.array([y * hz - z * hy, z * hx - x * hz, x * hy - y * hx])
        first /= np.linalg.norm(first)
        a, b, c = first
        second = np.array([y * c - z * b, z * a - x * c, x * b - y * a])
        found = (first, second)
        if len(_BASES) < 10000:
            _BASES[key] = found
    return found


def _planar(a: _Placed, b: _Placed, touch, gap, batch: _Batch, records: list) -> None:
    """Плоские грани навстречу друг другу на расстоянии до ``gap``."""
    mine, theirs = a.planes, b.planes
    if not len(mine) or not len(theirs):
        return
    facing = (a.direction[mine] @ b.direction[theirs].T) < -1.0 + PARALLEL
    # Расстояние со знаком: плоскость соседа ВПЕРЕДИ своей грани — зазор,
    # позади — грани ушли друг в друга (это пересечение, а не зазор).
    separation = -(a.offset[mine][:, None] + b.offset[theirs][None, :])
    near = facing & (separation >= -touch) & (separation <= gap)
    distance = np.maximum(separation, 0.0)
    if not near.any():
        return
    rows, columns = np.nonzero(near)
    faces_a, faces_b = mine[rows], theirs[columns]
    apart = np.any((a.low[faces_a] > b.high[faces_b] + gap)
                   | (b.low[faces_b] > a.high[faces_a] + gap), axis=1)
    for row, column, i, j in zip(rows[~apart], columns[~apart], faces_a[~apart],
                                 faces_b[~apart]):
        mine2d, low_a, high_a = a.flat(i)
        theirs2d, low_b, high_b = b.flat(j)
        grid = b.grid(j)
        if grid is not None and len(low_a) < len(low_b):
            first, second = pairs_with_grid(low_a, high_a, grid)
        elif a.grid(i) is not None and len(low_b) < len(low_a):
            second, first = pairs_with_grid(low_b, high_b, a.grid(i))
        else:
            first, second = candidate_pairs(low_a, high_a, low_b, high_b)
        record = len(records)
        records.append({"a": a, "b": b, "i": int(i), "j": int(j),
                        "distance": float(distance[row, column]), "kind": "plane",
                        "normal": a.direction[i]})
        batch.add(mine2d[first], theirs2d[second], record)


def _coaxial(a: _Placed, b: _Placed, touch, gap, records: list) -> None:
    """Вал в отверстии: соосные цилиндры, один выпуклый, другой вогнутый."""
    mine, theirs = a.cylinders, b.cylinders
    if not len(mine) or not len(theirs):
        return
    for i in mine:
        for j in theirs:
            if a.faces.convex[i] == b.faces.convex[j]:
                continue
            axis = a.direction[i]
            if abs(abs(float(axis @ b.direction[j])) - 1.0) > PARALLEL:
                continue
            offset = b.point[j] - a.point[i]
            across = offset - axis * float(offset @ axis)
            # Зазор — отверстие шире вала. Вал шире отверстия — натяг (винт в
            # отверстии под резьбу): это пересечение, его находит булева
            # операция, а не зазор.
            shaft, hole = ((a.faces.radius[i], b.faces.radius[j]) if a.faces.convex[i]
                           else (b.faces.radius[j], a.faces.radius[i]))
            radial = hole - shaft
            if np.linalg.norm(across) > touch or radial < -touch or radial > gap:
                continue
            radial = max(radial, 0.0)
            ends_a = a.triangles(i).reshape(-1, 3) @ axis
            ends_b = b.triangles(j).reshape(-1, 3) @ axis
            length = min(ends_a.max(), ends_b.max()) - max(ends_a.min(), ends_b.min())
            if length <= 0:
                continue
            radius = (a.faces.radius[i] + b.faces.radius[j]) / 2.0
            cover = min(a.faces.area[i] / max(a.faces.radius[i] * np.ptp(ends_a), 1e-12),
                        b.faces.area[j] / max(b.faces.radius[j] * np.ptp(ends_b), 1e-12))
            records.append({"a": a, "b": b, "i": int(i), "j": int(j), "distance": radial,
                            "kind": "cylinder", "area": float(length * cover * radius),
                            "normal": np.zeros(3),
                            "centre": a.point[i] + axis * (
                                (max(ends_a.min(), ends_b.min())
                                 + min(ends_a.max(), ends_b.max())) / 2.0
                                - float(a.point[i] @ axis))})


def _collect(records, areas, touch, min_area) -> dict:
    """Записи граней → контакты и зазоры по парам единиц."""
    found: dict = {}
    for index, record in enumerate(records):
        area = record.get("area")
        if area is None:
            area = float(areas[index]) if index < len(areas) else 0.0
        if area < min_area:
            continue
        a, b = record["a"], record["b"]
        kind = TOUCH if record["distance"] <= touch else GAP
        key = (pair_key(a.unit.key, b.unit.key), kind)
        entry = found.setdefault(key, {"a": a, "b": b, "kind": kind, "area": 0.0,
                                       "weighted": 0.0, "faces": [], "centre": np.zeros(3),
                                       "normal": np.zeros(3)})
        entry["area"] += area
        entry["weighted"] += area * record["distance"]
        entry["faces"].append((record["i"], record["j"], area))
        centre = record.get("centre")
        if centre is None:
            centre = (a.low[record["i"]] + a.high[record["i"]]) / 2.0
        entry["centre"] += area * np.asarray(centre)
        entry["normal"] += area * np.asarray(record["normal"])
    contacts: dict = {}
    for (key, kind), entry in found.items():
        normal = entry["normal"]
        length = np.linalg.norm(normal)
        contact = Contact(
            first=entry["a"].unit.key, second=entry["b"].unit.key, kind=kind,
            area=entry["area"], distance=entry["weighted"] / entry["area"],
            centre=tuple(float(v) for v in entry["centre"] / entry["area"]),
            normal=tuple(float(v) for v in (normal / length if length > 0 else normal)),
            faces=entry["faces"])
        contacts.setdefault(key, []).append(contact)
    return contacts


# --- пересечения ------------------------------------------------------------------------


def _clash(a: _Placed, b: _Placed, touch: float, counters: dict, inside: dict):
    """Пересечение пары: объём общей части или ``None``."""
    if a.unit.item is b.unit.item and np.allclose(a.unit.matrix, b.unit.matrix, atol=1e-6):
        return Contact(a.unit.key, b.unit.key, CLASH, volume=a.faces.volume,
                       centre=tuple((a.box[0] + a.box[1]) / 2.0),
                       note="одна и та же деталь дважды на одном месте")
    overlap = np.minimum(a.box[1], b.box[1]) - np.maximum(a.box[0], b.box[0])
    if np.any(overlap <= touch):
        return None
    if not _may_cross(a, b, touch) and not _may_cross(b, a, touch):
        # Границы не пересекаются: либо порознь, либо одна целиком в другой.
        for inner, outer in ((a, b), (b, a)):
            if np.all(inner.box[0] >= outer.box[0]) and np.all(inner.box[1] <= outer.box[1]) \
                    and len(inner.faces.triangles):
                point = inner.faces.triangles[0].mean(axis=0) @ inner.rotation.T + inner.shift
                inside.setdefault(id(outer), (outer, []))[1].append((inner, point))
        return None
    counters["booleans"] += 1
    return _common(a, b, touch)


def _may_cross(a: _Placed, b: _Placed, touch: float) -> bool:
    """Может ли какая-то грань ``a`` пересечь грань ``b``.

    Пара граней отпадает, если габариты не встречаются или одна из граней
    плоская, а другая целиком лежит по её наружную сторону: тогда они
    самое большее касаются.
    """
    near = np.all((a.low[:, None, :] <= b.high[None, :, :] + touch)
                  & (b.low[None, :, :] <= a.high[:, None, :] + touch), axis=2)
    for i, j in zip(*np.nonzero(near)):
        if b.faces.kind[j] == 1 and _outside(a.triangles(i), b.direction[j], b.offset[j], touch):
            continue
        if a.faces.kind[i] == 1 and _outside(b.triangles(j), a.direction[i], a.offset[i], touch):
            continue
        return True
    return False


def _outside(triangles, normal, offset, touch) -> bool:
    return bool(np.all(triangles.reshape(-1, 3) @ normal >= offset - touch))


def _common(a: _Placed, b: _Placed, touch: float):
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.TopTools import TopTools_ListOfShape

    operation = BRepAlgoAPI_Common()
    arguments, tools = TopTools_ListOfShape(), TopTools_ListOfShape()
    arguments.Append(a.world_shape())
    tools.Append(b.world_shape())
    operation.SetArguments(arguments)
    operation.SetTools(tools)
    operation.SetRunParallel(True)
    operation.Build()
    if not operation.IsDone():
        return Contact(a.unit.key, b.unit.key, CLASH, note="булева операция не удалась — "
                       "проверьте пару вручную")
    common = operation.Shape()
    volume = abs(kernel.volume(common))
    smaller = min(a.faces.volume, b.faces.volume) or 1.0
    if volume <= max(1e-6, 1e-7 * smaller):
        return None
    centre = kernel.bounds(common).center
    note = ""
    if FASTENER in (a.unit.role, b.unit.role) and volume < 0.3 * smaller:
        note = "крепёж в резьбовом отверстии: пересечение по резьбе — обычно допустимо"
    return Contact(a.unit.key, b.unit.key, CLASH, volume=volume, centre=centre, note=note)


def _resolve_inside(queries: dict, counters: dict) -> list:
    """Решить копившиеся «целиком внутри»: лучом по треугольникам соседа.

    Луч из точки пересекает границу тела нечётное число раз, если точка
    внутри. Два разных направления — для надёжности: луч, попавший точно в
    ребро, считает его дважды. Если направления не согласны, решает
    классификатор ядра.
    """
    found = []
    for outer, items in queries.values():
        points = np.array([point for _inner, point in items])
        triangles = np.concatenate([outer.triangles(face) for face in range(len(outer.faces.kind))
                                    if outer.faces.count[face]]) \
            if len(outer.faces.kind) else np.zeros((0, 3, 3))
        first = _crossings(points, triangles, _RAYS[0]) % 2 == 1
        second = _crossings(points, triangles, _RAYS[1]) % 2 == 1
        counters["classified"] += len(points)
        for (inner, point), one, two in zip(items, first, second):
            verdict = one if one == two else _inside(outer.world_shape(), point)
            if verdict:
                found.append(Contact(inner.unit.key, outer.unit.key, CLASH,
                                     volume=inner.faces.volume,
                                     centre=tuple(float(v) for v in
                                                  (inner.box[0] + inner.box[1]) / 2.0),
                                     note="деталь целиком внутри другой"))
    return found


_RAYS = (np.array([0.5773502, 0.5773507, 0.5773497]),
         np.array([-0.2672612, 0.5345225, 0.8017837]))


def _crossings(points: np.ndarray, triangles: np.ndarray, direction: np.ndarray,
               chunk: int = 256) -> np.ndarray:
    """Сколько треугольников пересекает луч из каждой точки (Мёллер — Трумбор)."""
    counts = np.zeros(len(points), int)
    if not len(triangles) or not len(points):
        return counts
    origin = triangles[:, 0]
    edge1 = triangles[:, 1] - origin
    edge2 = triangles[:, 2] - origin
    helper = _cross(np.broadcast_to(direction, edge2.shape), edge2)
    determinant = np.einsum("ij,ij->i", edge1, helper)
    valid = np.abs(determinant) > 1e-12
    inverse = np.where(valid, 1.0 / np.where(valid, determinant, 1.0), 0.0)
    for begin in range(0, len(points), chunk):
        offset = points[begin:begin + chunk, None, :] - origin[None, :, :]
        u = inverse * np.einsum("ktj,tj->kt", offset, helper)
        q = _cross(offset, np.broadcast_to(edge1, offset.shape))
        v = inverse * (q @ direction)
        t = inverse * np.einsum("ktj,tj->kt", q, edge2)
        hit = valid & (u >= 0.0) & (v >= 0.0) & (u + v <= 1.0) & (t > 1e-9)
        counts[begin:begin + chunk] = hit.sum(axis=1)
    return counts


def _cross(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Векторное произведение по последней оси — без накладных `np.cross`."""
    return np.stack([a[..., 1] * b[..., 2] - a[..., 2] * b[..., 1],
                     a[..., 2] * b[..., 0] - a[..., 0] * b[..., 2],
                     a[..., 0] * b[..., 1] - a[..., 1] * b[..., 0]], axis=-1)


def _inside(shape, point) -> bool:
    from OCP.BRepClass3d import BRepClass3d_SolidClassifier
    from OCP.TopAbs import TopAbs_IN, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer
    from OCP.gp import gp_Pnt

    explorer = TopExp_Explorer(shape, TopAbs_SOLID)
    while explorer.More():
        classifier = BRepClass3d_SolidClassifier(explorer.Current(), gp_Pnt(*map(float, point)),
                                                 1e-6)
        if classifier.State() == TopAbs_IN:
            return True
        explorer.Next()
    return False


# --- итог --------------------------------------------------------------------------------


def _merge(contacts: dict) -> list:
    merged = []
    for key in sorted(contacts):
        items = contacts[key]
        clash = next((item for item in items if item.kind == CLASH), None)
        touch = next((item for item in items if item.kind == TOUCH), None)
        if clash is not None and touch is not None:
            clash.area = touch.area
        merged.extend(items)
    return merged


def _connectivity(report: Report, units: list) -> None:
    """Одиночки и группы, связанные контактами и пересечениями."""
    parent = {unit.key: unit.key for unit in units}

    def root(key):
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    linked = set()
    for contact in report.contacts:
        if contact.kind == GAP:
            continue
        linked.update((contact.first, contact.second))
        parent[root(contact.first)] = root(contact.second)
    groups: dict = {}
    for unit in units:
        groups.setdefault(root(unit.key), []).append(unit.key)
    report.isolated = [unit.key for unit in units if unit.key not in linked]
    ordered = sorted((group for group in groups.values() if len(group) > 1),
                     key=len, reverse=True)
    report.groups = ordered


# --- замечания для человека -----------------------------------------------------------

ERROR = "error"
WARNING = "warning"
INFO = "info"


@dataclass
class Finding:
    """Замечание проверки — словами, с единицами, к которым оно относится."""

    severity: str
    text: str
    units: list = field(default_factory=list)
    contact: Contact | None = None


def findings(device: Device, report: Report) -> list:
    """Итог проверки словами: сначала то, что испортит расчёт."""
    from .model import HOUSING

    found = []

    def name(key):
        unit = device.units.get(key)
        return unit.name if unit is not None else key

    def other(contact, key):
        return contact.second if contact.first == key else contact.first

    isolated = set(report.isolated)
    for key in report.isolated:
        gaps = [item for item in report.of_unit(key) if item.kind == GAP]
        if gaps:
            nearest = min(gaps, key=lambda item: item.distance)
            text = (f"«{name(key)}» не касается ни одной детали: зазор "
                    f"{nearest.distance:.3g} мм до «{name(other(nearest, key))}» на площади "
                    f"{nearest.area:.3g} мм²")
            found.append(Finding(ERROR, text, [key, other(nearest, key)], nearest))
        else:
            found.append(Finding(ERROR, f"«{name(key)}» не касается ни одной детали — в "
                                        f"расчёте она останется без отвода тепла", [key]))
    if len(report.groups) > 1:
        def weight(group):
            return (sum(1 for key in group if device.units[key].role == HOUSING), len(group))

        main = max(report.groups, key=weight)
        for group in report.groups:
            if group is main:
                continue
            shown = ", ".join(name(key) for key in group[:4]) + (" …" if len(group) > 4 else "")
            found.append(Finding(WARNING, f"Группа из {len(group)} деталей ({shown}) не связана "
                                          f"контактами с остальным прибором", list(group)))
    for contact in report.of_kind(CLASH):
        severity = INFO if "резьб" in contact.note else ERROR
        text = (f"Пересечение «{name(contact.first)}» и «{name(contact.second)}»: "
                f"{contact.volume:.3g} мм³")
        if contact.note:
            text += f" — {contact.note}"
        found.append(Finding(severity, text, [contact.first, contact.second], contact))
    touching = {item.key for item in report.of_kind(TOUCH)} | {
        item.key for item in report.of_kind(CLASH)}
    beside = []
    for contact in report.of_kind(GAP):
        # Зазор рядом с контактом (под корпусом компонента) и зазор у
        # крепежа (винт в сквозном отверстии) — так и задумано: в сводку.
        fastened = FASTENER in (device.units[contact.first].role,
                                device.units[contact.second].role)
        if contact.key in touching or fastened:
            beside.append(contact)
            continue
        if contact.first in isolated or contact.second in isolated:
            continue
        found.append(Finding(WARNING, f"«{name(contact.first)}» и «{name(contact.second)}» "
                                      f"не касаются: зазор {contact.distance:.3g} мм на площади "
                                      f"{contact.area:.3g} мм²",
                             [contact.first, contact.second], contact))
    if beside:
        low = min(item.distance for item in beside)
        high = max(item.distance for item in beside)
        found.append(Finding(INFO, f"Воздушный зазор рядом с контактом у {len(beside)} пар — "
                                   f"под корпусами компонентов, в отверстиях под крепёж: "
                                   f"{low:.3g}–{high:.3g} мм. В расчёте это воздух",
                             sorted({key for item in beside
                                     for key in (item.first, item.second)})))
    order = {ERROR: 0, WARNING: 1, INFO: 2}
    found.sort(key=lambda item: order[item.severity])
    return found
