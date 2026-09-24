"""Что можно сказать о детали по её форме — один раз на определение.

Здесь нет угадывания по именам — только геометрия:

* **пластина** — тонкое плоское тело: так выглядит основание платы;
* **тело вращения** — грани соосны одной оси: винт, шайба, втулка, стойка;
* **шестигранник** — шесть граней вдоль оси через 60°: гайка, головка болта,
  шестигранная стойка.

Всё считается в собственных координатах определения и хранится у
вызывающего: 300 одинаковых резисторов разбираются один раз.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .. import kernel

#: Насколько направления считаются одинаковыми (синус угла).
SAME_DIRECTION = 1e-3


@dataclass
class Facts:
    """Сведения о форме определения."""

    #: Ориентированный габарит: центр, оси (строки), полуразмеры по осям.
    centre: np.ndarray
    axes: np.ndarray
    half: np.ndarray
    volume: float
    area: float
    solids: int
    faces: int
    #: Пластина: {"thickness", "normal", "length", "width", "fill"} или None.
    plate: dict | None = None
    #: Тело вращения: {"axis", "origin", "length", "diameter", "hole",
    #: "radii", "fraction"} или None.
    revolution: dict | None = None
    #: Шестигранник вдоль оси: размер под ключ, мм, или 0; его ось и есть
    #: ли вдоль неё отверстие насквозь.
    hex_flats: float = 0.0
    hex_axis: list | None = None
    hex_hole: bool = False
    notes: list = field(default_factory=list)

    @property
    def size(self) -> np.ndarray:
        """Размеры ориентированного габарита по убыванию, мм."""
        return np.sort(2.0 * self.half)[::-1]

    @property
    def box_volume(self) -> float:
        return float(np.prod(2.0 * self.half))


def facts_of(shape) -> Facts:
    """Разобрать форму. Пустая форма даёт нулевые сведения, а не отказ."""
    from OCP.BRepBndLib import BRepBndLib
    from OCP.Bnd import Bnd_OBB

    box = Bnd_OBB()
    if shape is not None and not kernel.is_empty(shape):
        BRepBndLib.AddOBB_s(shape, box, True, True, False)
    if box.IsVoid():
        return Facts(np.zeros(3), np.eye(3), np.zeros(3), 0.0, 0.0, 0, 0)
    centre = box.Center()
    axes = np.array([[direction.X(), direction.Y(), direction.Z()]
                     for direction in (box.XDirection(), box.YDirection(),
                                       box.ZDirection())])
    half = np.array([box.XHSize(), box.YHSize(), box.ZHSize()])
    faces = list(kernel.iter_faces(shape))
    found = Facts(centre=np.array([centre.X(), centre.Y(), centre.Z()]),
                  axes=axes, half=half, volume=abs(kernel.volume(shape)),
                  area=kernel.area(shape), solids=kernel.solid_count(shape),
                  faces=len(faces))
    found.plate = _plate(found)
    if len(faces) <= MAX_FACES_FOR_REVOLUTION:
        surfaces = [_surface(face) for face in faces]
        found.revolution = _revolution(surfaces, shape, found)
        flats, axis, origin = _hex(surfaces, found.revolution)
        if flats:
            found.hex_flats, found.hex_axis = flats, axis.tolist()
            found.hex_hole = _hole_along(surfaces, shape, axis, origin, found)
    return found


# --- пластина ------------------------------------------------------------------


#: Основание платы: толщина до 6 мм, длина и ширина — не меньше восьми
#: толщин, объём — не меньше трети габарита (вырезы и отверстия допустимы).
PLATE_THICKNESS = 6.0
PLATE_ASPECT = 8.0
PLATE_FILL = 0.33


def _plate(found: Facts):
    order = np.argsort(found.half)
    thickness = 2.0 * found.half[order[0]]
    width = 2.0 * found.half[order[1]]
    length = 2.0 * found.half[order[2]]
    if thickness <= 0 or thickness > PLATE_THICKNESS or width < PLATE_ASPECT * thickness:
        return None
    fill = found.volume / max(found.box_volume, 1e-12)
    if fill < PLATE_FILL:
        return None
    return {"thickness": float(thickness), "normal": found.axes[order[0]].tolist(),
            "length": float(length), "width": float(width), "fill": float(fill)}


# --- поверхности ----------------------------------------------------------------


def _surface(face) -> dict:
    """Вид поверхности грани с параметрами и площадью."""
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import (GeomAbs_Cone, GeomAbs_Cylinder, GeomAbs_Plane,
                             GeomAbs_Sphere, GeomAbs_Torus)

    adaptor = BRepAdaptor_Surface(face, True)
    kind = adaptor.GetType()
    data = {"area": kernel.area(face), "kind": "other"}
    if kind == GeomAbs_Plane:
        axis = adaptor.Plane().Axis()
        data.update(kind="plane", direction=_vector(axis.Direction()),
                    point=_point(axis.Location()), centre=_centre(face))
    elif kind in (GeomAbs_Cylinder, GeomAbs_Cone, GeomAbs_Torus):
        getter = {GeomAbs_Cylinder: adaptor.Cylinder, GeomAbs_Cone: adaptor.Cone,
                  GeomAbs_Torus: adaptor.Torus}[kind]
        surface = getter()
        axis = surface.Axis()
        data.update(kind={GeomAbs_Cylinder: "cylinder", GeomAbs_Cone: "cone",
                          GeomAbs_Torus: "torus"}[kind],
                    direction=_vector(axis.Direction()), point=_point(axis.Location()))
        if kind == GeomAbs_Cylinder:
            data["radius"] = surface.Radius()
            data["convex"] = _convex(face, adaptor, data)
    elif kind == GeomAbs_Sphere:
        data.update(kind="sphere", point=_point(adaptor.Sphere().Location()))
    return data


def _centre(face) -> np.ndarray:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    return _point(props.CentreOfMass())


def _vector(direction) -> np.ndarray:
    return np.array([direction.X(), direction.Y(), direction.Z()])


def _point(point) -> np.ndarray:
    return np.array([point.X(), point.Y(), point.Z()])


def _convex(face, adaptor, data) -> bool:
    """Выпуклый цилиндр (вал, стержень) или вогнутый (отверстие).

    Нормаль грани в средней точке смотрит от оси у вала и к оси у
    отверстия — с учётом ориентации грани в теле.
    """
    from OCP.BRepTools import BRepTools
    from OCP.gp import gp_Pnt, gp_Vec
    from OCP.TopAbs import TopAbs_REVERSED

    u_low, u_high, v_low, v_high = BRepTools.UVBounds_s(face)
    u, v = (u_low + u_high) / 2.0, (v_low + v_high) / 2.0
    point, along_u, along_v = gp_Pnt(), gp_Vec(), gp_Vec()
    adaptor.D1(u, v, point, along_u, along_v)
    normal = np.cross(_vector(along_u), _vector(along_v))
    if face.Orientation() == TopAbs_REVERSED:
        normal = -normal
    position = _point(point) - data["point"]
    radial = position - data["direction"] * float(position @ data["direction"])
    return float(normal @ radial) > 0.0


# --- тело вращения --------------------------------------------------------------


#: Крепёж и втулки — это десятки граней, а не тысячи. Разбирать на оси
#: основание платы с тысячей переходных отверстий незачем и дорого.
MAX_FACES_FOR_REVOLUTION = 400

#: Сколько площади должно приходиться на соосные грани и торцы, чтобы тело
#: считалось телом вращения. Шлиц и шестигранник под ключ в головке винта
#: занимают немного — порог их прощает.
REVOLUTION_FRACTION = 0.75


def _revolution(surfaces: list, shape, found: Facts):
    """Общая ось соосных граней — если на них приходится почти вся площадь."""
    round_faces = [item for item in surfaces
                   if item["kind"] in ("cylinder", "cone", "torus")]
    if not round_faces or found.area <= 0:
        return None
    best = None
    # Кандидаты в оси — крупнейшие круглые грани: ось крепежа несут стержень
    # и головка, а не фаска.
    candidates = sorted(round_faces, key=lambda item: -item["area"])[:12]
    for candidate in candidates:
        axis, origin = candidate["direction"], candidate["point"]
        coaxial = [item for item in round_faces if _on_axis(item, axis, origin, found)]
        # Торец тела вращения — поперёк оси И с центром на ней. Без второго
        # условия верх платы сходил за торец каждого переходного отверстия,
        # и плата оказывалась «телом вращения».
        ends = [item for item in surfaces if item["kind"] == "plane"
                and abs(abs(float(item["direction"] @ axis)) - 1.0) < SAME_DIRECTION
                and _line_gap(item["centre"], axis, origin) < 0.02 * float(
                    found.half.max()) + _tolerance(found)]
        spheres = [item for item in surfaces if item["kind"] == "sphere"
                   and _line_gap(item["point"], axis, origin) < _tolerance(found)]
        share = sum(item["area"] for item in coaxial + ends + spheres) / found.area
        if best is None or share > best[0]:
            best = (share, axis, origin, coaxial)
    share, axis, origin, coaxial = best
    if share < 0.5:
        return None
    points = kernel.vertices(shape).astype(float)
    along = (points - origin) @ axis if len(points) else np.zeros(1)
    radii = sorted({round(item["radius"], 4) for item in coaxial if "radius" in item
                    and item.get("convex")})
    holes = [item for item in coaxial if item["kind"] == "cylinder"
             and not item.get("convex")]
    length = float(along.max() - along.min()) if len(points) else 0.0
    return {"axis": axis.tolist(), "origin": origin.tolist(), "length": length,
            "diameter": 2.0 * max(radii) if radii else float(2.0 * found.half.max()),
            "radii": radii, "hole": _through(holes, along, axis, origin),
            "fraction": float(share)}


def _on_axis(item, axis, origin, found) -> bool:
    if abs(abs(float(item["direction"] @ axis)) - 1.0) > SAME_DIRECTION:
        return False
    return _line_gap(item["point"], axis, origin) < _tolerance(found)


def _tolerance(found: Facts) -> float:
    return max(1e-4, 1e-4 * float(found.half.max()))


def _line_gap(point, axis, origin) -> float:
    offset = np.asarray(point, float) - np.asarray(origin, float)
    across = offset - axis * float(offset @ axis)
    return float(np.linalg.norm(across))


def _through(holes, along, axis, origin) -> bool:
    """Есть ли соосное отверстие насквозь: суммарная длина вогнутых
    цилиндров покрывает всю длину тела (с запасом на фаски)."""
    if not holes or len(along) == 0:
        return False
    total = float(along.max() - along.min())
    covered = sum(item["area"] / (2.0 * math.pi * item["radius"])
                  for item in holes if item.get("radius", 0) > 0)
    return covered >= 0.6 * total


# --- шестигранник --------------------------------------------------------------


def _hole_along(surfaces, shape, axis, origin, found) -> bool:
    holes = [item for item in surfaces if item["kind"] == "cylinder"
             and not item.get("convex") and _on_axis(item, axis, origin, found)]
    points = kernel.vertices(shape).astype(float)
    along = (points - origin) @ axis if len(points) else np.zeros(0)
    return _through(holes, along, axis, origin)


def _hex(surfaces: list, revolution):
    """Размер под ключ, ось и точка на ней — если вдоль оси есть шесть
    граней через 60°. Иначе ``(0, None, None)``."""
    planes = [item for item in surfaces if item["kind"] == "plane"]
    if len(planes) < 6:
        return 0.0, None, None
    axes = []
    if revolution is not None:
        axes.append((np.asarray(revolution["axis"]), np.asarray(revolution["origin"])))
    for item in planes:
        if not any(abs(abs(float(item["direction"] @ axis)) - 1.0) < SAME_DIRECTION
                   for axis, _ in axes):
            axes.append((item["direction"], item["point"]))
    for axis, origin in axes[:4]:
        sides = [item for item in planes
                 if abs(float(item["direction"] @ axis)) < SAME_DIRECTION]
        if len(sides) < 6:
            continue
        gaps = sorted({round(abs(float((item["point"] - origin) @ item["direction"])), 3)
                       for item in sides})
        for gap in gaps:
            ring = [item for item in sides if abs(abs(float(
                (item["point"] - origin) @ item["direction"])) - gap) < 1e-3]
            angles = sorted({round(math.degrees(math.atan2(
                *_in_plane(item["direction"], axis))) % 60.0, 1) % 60.0 for item in ring})
            if len(ring) >= 6 and len(angles) == 1 and gap > 0:
                return 2.0 * gap, np.asarray(axis, float), _hex_centre(ring, axis)
    return 0.0, None, None


def _hex_centre(ring, axis) -> np.ndarray:
    """Точка на оси шестигранника: среднее центров его боковых граней."""
    centre = np.mean([item["centre"] for item in ring], axis=0)
    return centre - axis * float(centre @ axis)


def _in_plane(direction, axis) -> tuple:
    helper = np.array([1.0, 0.0, 0.0]) if abs(axis[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    first = np.cross(axis, helper)
    first /= np.linalg.norm(first)
    second = np.cross(axis, first)
    return float(direction @ second), float(direction @ first)
