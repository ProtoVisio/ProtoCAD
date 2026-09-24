"""Отбор граней ПРАВИЛОМ, а не номером.

Группа, заданная номерами граней, живёт до первой новой версии геометрии:
конструктор поменял толщину, выгрузил STEP заново — и «грань 17» стала
другой гранью. Правило переживает это: «плоские грани с нормалью вниз,
самые нижние» — это опора и в первой версии, и в десятой.

Правило — словарь. Все условия складываются через И:

* ``type`` — вид поверхности (``plane``, ``cylinder``, ``cone``, ``sphere``,
  ``torus``, ``bspline``, …) или их список;
* ``normal`` + ``angle`` — у плоских граней нормаль НАРУЖУ от материала
  в пределах угла, градусы (по умолчанию 1°);
* ``axis`` + ``angle`` — ось цилиндра или конуса параллельна (в любую сторону);
* ``radius`` — [от, до] у цилиндров, конусов (по большему), сфер, торов
  (по малому радиусу);
* ``concave`` — вогнутая (отверстие, внутреннее скругление) или нет;
* ``area`` — [от, до];
* ``box`` — центр грани внутри коробки [x0, y0, z0, x1, y1, z1];
* ``near`` + ``tol`` — грани, на которых лежат указанные точки: так
  записывается выбор мышью;
* ``body`` / ``bodies`` — только грани этих тел;
* ``faces`` — номера граней как есть (годны только до смены геометрии);
* ``at`` = ``"max"``/``"min"`` + ``along`` — из прошедших оставить крайние
  вдоль направления (по умолчанию — вдоль ``normal``): «самая нижняя»,
  «самая дальняя по потоку».
"""

from __future__ import annotations

import math

import numpy as np

from OCP.BRepAdaptor import BRepAdaptor_Surface
from OCP.BRepGProp import BRepGProp
from OCP.BRepTools import BRepTools
from OCP.GeomAbs import (
    GeomAbs_BezierSurface,
    GeomAbs_BSplineSurface,
    GeomAbs_Cone,
    GeomAbs_Cylinder,
    GeomAbs_OffsetSurface,
    GeomAbs_Plane,
    GeomAbs_Sphere,
    GeomAbs_SurfaceOfExtrusion,
    GeomAbs_SurfaceOfRevolution,
    GeomAbs_Torus,
)
from OCP.GProp import GProp_GProps
from OCP.TopAbs import TopAbs_REVERSED

TYPES = {
    GeomAbs_Plane: "plane",
    GeomAbs_Cylinder: "cylinder",
    GeomAbs_Cone: "cone",
    GeomAbs_Sphere: "sphere",
    GeomAbs_Torus: "torus",
    GeomAbs_BezierSurface: "bezier",
    GeomAbs_BSplineSurface: "bspline",
    GeomAbs_SurfaceOfRevolution: "revolution",
    GeomAbs_SurfaceOfExtrusion: "extrusion",
    GeomAbs_OffsetSurface: "offset",
}


class RuleError(ValueError):
    """Правило задано так, что его нельзя выполнить."""


def describe(face) -> dict:
    """Описание грани числами: вид, площадь, центр, габарит, ось, радиус.

    Нормаль и вогнутость — с учётом ориентации грани в теле: у грани,
    вошедшей развёрнутой, наружу смотрит обратная сторона поверхности.
    """
    from OCP.Bnd import Bnd_Box
    from OCP.BRepBndLib import BRepBndLib

    surface = BRepAdaptor_Surface(face)
    kind = TYPES.get(surface.GetType(), "other")
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    centre = props.CentreOfMass()
    sign = -1.0 if face.Orientation() == TopAbs_REVERSED else 1.0
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(face, box, False, False)
    low, high = box.CornerMin(), box.CornerMax()
    data = {
        "type": kind,
        "area": props.Mass(),
        "center": (centre.X(), centre.Y(), centre.Z()),
        "box": (low.X(), low.Y(), low.Z(), high.X(), high.Y(), high.Z()),
    }
    if kind == "plane":
        direction = surface.Plane().Axis().Direction()
        data["normal"] = (direction.X() * sign, direction.Y() * sign,
                          direction.Z() * sign)
        return data
    element = {"cylinder": surface.Cylinder, "cone": surface.Cone,
               "sphere": surface.Sphere, "torus": surface.Torus}.get(kind)
    if element is None:
        return data
    geometry = element()
    if kind == "sphere":
        location = geometry.Location()
        data["origin"] = (location.X(), location.Y(), location.Z())
        data["radius"] = geometry.Radius()
    else:
        axis = geometry.Axis()
        direction, location = axis.Direction(), axis.Location()
        data["axis"] = (direction.X(), direction.Y(), direction.Z())
        data["origin"] = (location.X(), location.Y(), location.Z())
        if kind == "cylinder":
            data["radius"] = geometry.Radius()
        elif kind == "torus":
            data["radius"] = geometry.MinorRadius()
            data["major_radius"] = geometry.MajorRadius()
        else:
            data["radius"] = _cone_radius(face, geometry)
            data["half_angle"] = math.degrees(abs(geometry.SemiAngle()))
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    data["span"] = math.degrees(u1 - u0)
    data["concave"] = _concave(surface, face, kind, data, sign,
                               (u0 + u1) / 2.0, (v0 + v1) / 2.0)
    return data


def _cone_radius(face, cone) -> float:
    """Больший радиус конуса В ПРЕДЕЛАХ грани — по её габариту вдоль оси."""
    u0, u1, v0, v1 = BRepTools.UVBounds_s(face)
    reference = cone.RefRadius()
    slope = math.sin(cone.SemiAngle())
    return max(abs(reference + v0 * slope), abs(reference + v1 * slope))


def _concave(surface, face, kind: str, data: dict, sign: float,
             u: float, v: float) -> bool:
    """Смотрит ли нормаль НАРУЖУ к оси (сфере — к центру).

    Вогнутая грань — отверстие или внутреннее скругление: материал снаружи
    поверхности, воздух — со стороны оси.
    """
    from OCP.BRepLProp import BRepLProp_SLProps

    probe = BRepLProp_SLProps(surface, u, v, 1, 1e-9)
    if not probe.IsNormalDefined():
        return False
    point, normal = probe.Value(), probe.Normal()
    p = np.array([point.X(), point.Y(), point.Z()])
    n = np.array([normal.X(), normal.Y(), normal.Z()]) * sign
    origin = np.array(data["origin"])
    if kind == "sphere":
        radial = p - origin
    else:
        axis = np.array(data["axis"])
        offset = p - origin
        radial = offset - axis * float(offset @ axis)
    return float(n @ radial) < 0.0


def _angle_ok(first, second, angle_deg: float, either_way: bool = False) -> bool:
    a = np.asarray(first, float)
    b = np.asarray(second, float)
    norm = float(np.linalg.norm(a) * np.linalg.norm(b))
    if norm == 0.0:
        return False
    cosine = float(a @ b) / norm
    if either_way:
        cosine = abs(cosine)
    return cosine >= math.cos(math.radians(angle_deg))


def _inside(value: float, bounds) -> bool:
    low, high = float(bounds[0]), float(bounds[1])
    return low <= value <= high


def select(study, rule: dict) -> list:
    """Номера граней исследования, прошедших правило. Порядок — по номеру."""
    if not isinstance(rule, dict) or not rule:
        raise RuleError("правило отбора пустое")
    known = {"type", "normal", "axis", "angle", "radius", "concave", "area",
             "box", "near", "tol", "body", "bodies", "faces", "at", "along"}
    strange = sorted(set(rule) - known)
    if strange:
        raise RuleError(f"в правиле непонятные условия: {strange}")

    if "faces" in rule:
        candidates = [int(value) for value in rule["faces"]
                      if 0 <= int(value) < study.face_count]
    else:
        candidates = list(range(study.face_count))
    wanted_bodies = []
    if rule.get("body"):
        wanted_bodies.append(rule["body"])
    wanted_bodies.extend(rule.get("bodies") or ())
    if wanted_bodies:
        missing = [name for name in wanted_bodies if study.body(name) is None]
        if missing:
            raise RuleError(f"тел {missing} в исследовании нет")
        allowed = set()
        for name in wanted_bodies:
            allowed.update(study.faces_of(name))
        candidates = [index for index in candidates if index in allowed]

    angle = float(rule.get("angle", 1.0))
    types = rule.get("type")
    if isinstance(types, str):
        types = [types]
    described = {}
    passed = []
    for index in candidates:
        data = describe(study.face(index))
        described[index] = data
        if types and data["type"] not in types:
            continue
        if "normal" in rule:
            if "normal" not in data or not _angle_ok(data["normal"], rule["normal"], angle):
                continue
        if "axis" in rule:
            if "axis" not in data or not _angle_ok(data["axis"], rule["axis"], angle,
                                                   either_way=True):
                continue
        if "radius" in rule:
            if "radius" not in data or not _inside(data["radius"], rule["radius"]):
                continue
        if "concave" in rule and bool(data.get("concave", False)) != bool(rule["concave"]):
            continue
        if "area" in rule and not _inside(data["area"], rule["area"]):
            continue
        if "box" in rule:
            x0, y0, z0, x1, y1, z1 = (float(value) for value in rule["box"])
            x, y, z = data["center"]
            if not (x0 <= x <= x1 and y0 <= y <= y1 and z0 <= z <= z1):
                continue
        passed.append(index)

    if "near" in rule:
        passed = _near(study, passed, described, rule["near"],
                       float(rule.get("tol") or study.diagonal() * 1e-4))

    if rule.get("at"):
        along = rule.get("along") or rule.get("normal")
        if along is None:
            raise RuleError("«at» требует направления: задайте along или normal")
        direction = np.asarray(along, float)
        direction = direction / (np.linalg.norm(direction) or 1.0)
        if passed:
            depth = {index: float(np.asarray(described[index]["center"]) @ direction)
                     for index in passed}
            extreme = (max if rule["at"] == "max" else min)(depth.values())
            tolerance = max(study.diagonal() * 1e-6, 1e-7)
            passed = [index for index in passed
                      if abs(depth[index] - extreme) <= tolerance]
    return sorted(passed)


def _near(study, candidates, described, points, tolerance: float) -> list:
    """Для каждой точки — ОДНА ближайшая грань в пределах допуска.

    Одна, а не все в допуске: точка у самого ребра лежит в допуске от обеих
    граней, а выбирали одну.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt

    chosen = set()
    for point in points:
        x, y, z = (float(value) for value in point)
        vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(x, y, z)).Vertex()
        best, best_gap = None, None
        for index in candidates:
            x0, y0, z0, x1, y1, z1 = described[index]["box"]
            if (x < x0 - tolerance or x > x1 + tolerance or y < y0 - tolerance
                    or y > y1 + tolerance or z < z0 - tolerance or z > z1 + tolerance):
                continue
            measure = BRepExtrema_DistShapeShape(vertex, study.face(index))
            if not measure.IsDone():
                continue
            gap = measure.Value()
            if gap <= tolerance and (best_gap is None or gap < best_gap):
                best, best_gap = index, gap
        if best is not None:
            chosen.add(best)
    return [index for index in candidates if index in chosen]


def inner_point(face, deflection: float) -> tuple:
    """Точка, лежащая НА грани, — для записи выбора мышью.

    Центр масс не годится: у кольца он в дыре, у изогнутой грани — в
    воздухе. Берётся середина самого большого треугольника разбиения и
    опускается на саму грань.
    """
    from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeVertex
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape
    from OCP.gp import gp_Pnt

    from .display import face_triangles

    triangles = face_triangles(face, deflection)
    if triangles is None or not len(triangles):
        data = describe(face)
        return data["center"]
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    areas = np.linalg.norm(np.cross(b - a, c - a), axis=1)
    middle = triangles[int(np.argmax(areas))].mean(axis=0)
    vertex = BRepBuilderAPI_MakeVertex(gp_Pnt(*[float(v) for v in middle])).Vertex()
    measure = BRepExtrema_DistShapeShape(vertex, face)
    if measure.IsDone() and measure.NbSolution() > 0:
        point = measure.PointOnShape2(1)
        return (point.X(), point.Y(), point.Z())
    return tuple(float(value) for value in middle)


def picked_rule(study, faces) -> dict:
    """Правило, которым записывается выбор мышью: по точке на каждой грани."""
    deflection = max(study.diagonal() * 1e-3, 1e-3)
    points = [list(inner_point(study.face(index), deflection)) for index in faces]
    return {"near": points, "tol": max(study.diagonal() * 1e-4, 1e-4)}


# --- группы как шаги подготовки ------------------------------------------------


def _log(study, report):
    study.log.append(report)
    return report


def make_group(study, name: str, rule=None, kind: str = "faces", bodies=(),
               add: bool = False, allow_empty: bool = False):
    """Шаг «группа»: собрать грани по правилу или тела по именам.

    Правило, не нашедшее ни одной грани, — отказ, а не пустая группа.
    На новой версии геометрии это ровно тот случай, когда закрепление
    молча пропало бы из расчёта.

    Имена тел можно задавать образцами: ``"Болт*"`` — все болты сборки.
    """
    import fnmatch

    from .model import BODY_GROUP, FACE_GROUP, Report

    report = Report("group", params={"name": name, "rule": dict(rule or {}),
                                     "kind": kind, "bodies": list(bodies),
                                     "add": add, "allow_empty": allow_empty})
    if not name:
        return _log(study, report.fail("NO_NAME", "у группы должно быть имя"))
    existing = study.groups.get(name)
    if kind == BODY_GROUP:
        alive = [body.name for body in study.bodies]
        chosen, missing = [], []
        for pattern in bodies:
            hits = [item for item in alive if fnmatch.fnmatchcase(item, pattern)]
            if hits:
                chosen.extend(item for item in hits if item not in chosen)
            else:
                missing.append(pattern)
        if missing and not allow_empty:
            return _log(study, report.fail(
                "NO_BODIES", f"группа «{name}»: тел {missing} в исследовании нет"))
        members = set(chosen)
        if add and existing is not None:
            members |= existing.bodies
        study.add_group(name, BODY_GROUP, bodies=members)
        report.message = f"тел в группе: {len(members)}"
        return _log(study, report)
    try:
        faces = select(study, rule or {})
    except RuleError as failure:
        return _log(study, report.fail("BAD_RULE", f"группа «{name}»: {failure}"))
    if not faces and not allow_empty:
        return _log(study, report.fail(
            "RULE_EMPTY", f"группа «{name}»: правило не нашло ни одной грани — "
            f"геометрия изменилась или правило задано не так"))
    members = set(faces)
    if add and existing is not None:
        members |= existing.faces
    group = study.add_group(name, FACE_GROUP, faces=members, rule=rule or {})
    report.message = f"граней в группе: {group.size}"
    report.note("GROUP_FACES", f"«{name}»: {group.size}", "info",
                faces=sorted(members))
    return _log(study, report)


def drop_group(study, name: str):
    from .model import Report

    report = Report("ungroup", params={"name": name})
    if not study.remove_group(name):
        report.fail("NO_GROUP", f"группы «{name}» нет")
    return _log(study, report)
