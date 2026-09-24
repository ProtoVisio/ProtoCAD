"""Описания граней детали для сопряжений: плоскости и цилиндры.

Сопряжение ссылается на грань ОПИСАНИЕМ, а не номером (§26 движка):
номер меняется при первой правке выше по дереву. Описание снимается в
СОБСТВЕННЫХ координатах детали, поэтому одна деталь, поставленная дважды,
даёт две разные грани по одной ссылке — положение вхождения переводит
описание в координаты сборки.

Метки — строки:

* плоскость — ``"nx,ny,nz,d"``: нормаль наружу и удаление от нуля. Тот же
  вид, что у ссылок эскиза и опор трёхмерного эскиза отверстий;
* цилиндр — ``"c:ax,ay,az,px,py,pz,r"``: направление оси, ближайшая к нулю
  точка оси и радиус. Соосность держится за ось, а не за грань: половинки
  цилиндра, на которые его режут экспортёры, — это одна и та же ось.

Откуда берутся описания: у детали с формой ядра (STEP, BREP, старое дерево)
— из формы; у детали на движке — из описания подэлементов, которое движок
присылает вместе с сеткой. Второго ядра в окне при этом не заводится (§16.3).
"""

from __future__ import annotations

import math

#: Допуски опознания грани по метке: направление, место, радиус.
SAME_DIRECTION = 1e-3
SAME_PLACE = 1e-3
SAME_RADIUS = 1e-3


def item_faces(item) -> list:
    """Описания граней изделия в его собственных координатах.

    Порядок — тот же, что у номеров граней в сетке показа: по этим номерам
    выбранная мышью грань и находит своё описание.
    """
    document = getattr(item, "document", None)
    if document is not None and getattr(document, "result", None) is not None:
        return _engine_faces(document.result)
    shape = getattr(item, "shape", None)
    if shape is None:
        return []
    return shape_faces(shape)


def shape_faces(shape) -> list:
    """Описания граней формы ядра в порядке `kernel.iter_faces`."""
    from .. import kernel
    from ..prep.select import describe

    found = []
    for index, face in enumerate(kernel.iter_faces(shape)):
        data = describe(face)
        found.append(_normalised(index, data))
    return found


def _normalised(index: int, data: dict) -> dict:
    """Описание в общем виде — одном для формы ядра и для движка."""
    kind = data.get("type") or data.get("surface") or "other"
    face = {"index": index, "surface": kind,
            "center": [float(v) for v in data.get("center") or (0.0, 0.0, 0.0)],
            "area": float(data.get("area", 0.0))}
    if kind == "plane" and data.get("normal"):
        face["normal"] = list(_unit(data["normal"]))
    if kind in ("cylinder", "cone") and data.get("axis"):
        face["axis"] = list(_unit(data["axis"]))
        origin = data.get("origin") or data.get("axis_origin") or face["center"]
        face["origin"] = [float(v) for v in origin]
        face["radius"] = float(data.get("radius", 0.0))
    return face


def _engine_faces(result) -> list:
    found = []
    for entity in getattr(result, "entities", ()) or ():
        if getattr(entity, "kind", "") != "face":
            continue
        data = dict(entity.data or {})
        found.append(_normalised(int(entity.index), data))
    found.sort(key=lambda face: face["index"])
    return found


# --- метки ---------------------------------------------------------------


def mark_of(face: dict) -> str:
    """Метка грани. Пустая строка — к такой грани сопрягать нечем."""
    if face.get("surface") == "plane" and face.get("normal"):
        normal = face["normal"]
        offset = _dot(normal, face["center"])
        return ",".join(f"{value:.9g}" for value in (*normal, offset))
    if face.get("surface") in ("cylinder", "cone") and face.get("axis"):
        axis = face["axis"]
        point = _closest_to_origin(face["origin"], axis)
        values = (*axis, *point, face.get("radius", 0.0))
        return "c:" + ",".join(f"{value:.9g}" for value in values)
    return ""


def kind_of(mark: str) -> str:
    """Вид поверхности по метке: ``plane``, ``cylinder`` или пусто."""
    if not mark:
        return ""
    return "cylinder" if str(mark).startswith("c:") else "plane"


def find(faces, mark: str):
    """Грань по метке. ``None`` — не нашлась: сопряжение объявляется
    потерянным, похожая грань не подставляется (§38)."""
    if kind_of(mark) == "cylinder":
        return _find_cylinder(faces, mark)
    return _find_plane(faces, mark)


def _values(text: str) -> list:
    try:
        return [float(piece) for piece in str(text).split(",")]
    except ValueError:
        return []


def _find_plane(faces, mark: str):
    values = _values(mark)
    if len(values) < 4:
        return None
    wanted, offset = _unit(values[:3]), values[3]
    best, best_gap = None, None
    for face in faces:
        normal = face.get("normal")
        if not normal:
            continue
        if _dot(normal, wanted) < 1.0 - SAME_DIRECTION:
            continue
        # По направлению, затем по ближайшему удалению: грань, которая
        # переехала при правке детали, остаётся той же гранью.
        gap = abs(_dot(normal, face["center"]) - offset)
        if best_gap is None or gap < best_gap:
            best, best_gap = face, gap
    return best


def _find_cylinder(faces, mark: str):
    values = _values(str(mark)[2:])
    if len(values) < 7:
        return None
    axis, point, radius = _unit(values[:3]), values[3:6], values[6]
    best, best_gap = None, None
    for face in faces:
        if not face.get("axis"):
            continue
        if abs(abs(_dot(face["axis"], axis)) - 1.0) > SAME_DIRECTION:
            continue
        if abs(face.get("radius", 0.0) - radius) > SAME_RADIUS:
            continue
        gap = _line_gap(face["origin"], axis, point)
        if best_gap is None or gap < best_gap:
            best, best_gap = face, gap
    return best


# --- векторы -------------------------------------------------------------


def _dot(a, b) -> float:
    return sum(float(a[i]) * float(b[i]) for i in range(3))


def _unit(vector) -> tuple:
    length = math.sqrt(sum(float(v) * float(v) for v in vector))
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return tuple(float(v) / length for v in vector)


def _closest_to_origin(point, axis) -> tuple:
    along = _dot(point, axis)
    return tuple(float(point[i]) - axis[i] * along for i in range(3))


def _line_gap(point, axis, other) -> float:
    """Расстояние от точки ``point`` до прямой через ``other`` вдоль ``axis``."""
    offset = [float(point[i]) - float(other[i]) for i in range(3)]
    along = _dot(offset, axis)
    across = [offset[i] - axis[i] * along for i in range(3)]
    return math.sqrt(_dot(across, across))
