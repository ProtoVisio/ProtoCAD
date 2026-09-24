"""Разрез плоскостью: половина по симметрии и деление тела на части.

**Симметрия.** Половина модели — вдвое меньше элементов при том же ответе,
если нагрузка и закрепление симметричны тоже. Грани разреза сразу
собираются в группу: на них ставят условие симметрии, и искать их потом
руками среди сотни граней незачем.

**Деление.** Тело режется на части, которые остаются СКЛЕЕННЫМИ по
разрезу: общая грань одна на двоих, и сетка по обе стороны совпадает.
Нужно, чтобы задать разные размеры сетки частям тела или приложить
нагрузку к пятну, которого на исходной грани нет.
"""

from __future__ import annotations

import numpy as np

from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Splitter
from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Ax2, gp_Dir, gp_Pln, gp_Pnt
from OCP.TopTools import TopTools_ListOfShape

from .. import kernel
from .model import ERROR, WARNING, Body, Report, from_history, logged, solids_of


def _unit(vector) -> np.ndarray:
    value = np.asarray(vector, float)
    length = float(np.linalg.norm(value))
    if length == 0.0:
        raise ValueError("нормаль плоскости нулевая")
    return value / length


def _frame(origin, normal):
    """Система координат на плоскости: Z — нормаль, X — любая поперёк."""
    n = _unit(normal)
    helper = np.eye(3)[int(np.argmin(np.abs(n)))]
    x = helper - n * float(helper @ n)
    x = x / float(np.linalg.norm(x))
    return n, x, np.cross(n, x)


def half_space(origin, normal, size: float):
    """Коробка по одну сторону плоскости (туда, куда смотрит нормаль) и её
    грань, лежащая на плоскости.

    Коробка, а не `BRepPrimAPI_MakeHalfSpace`: булевы операции с настоящим
    полупространством ядро выполняет заметно капризнее, а коробка втрое
    больше детали даёт тот же результат.
    """
    n, x, y = _frame(origin, normal)
    corner = np.asarray(origin, float) - x * size - y * size
    maker = BRepPrimAPI_MakeBox(
        gp_Ax2(gp_Pnt(*corner), gp_Dir(*n), gp_Dir(*x)), 2 * size, 2 * size, size)
    return maker.Shape(), maker.BottomFace()


def plane_face(origin, normal, size: float):
    n, x, _y = _frame(origin, normal)
    plane = gp_Pln(gp_Pnt(*[float(v) for v in origin]), gp_Dir(*n))
    return BRepBuilderAPI_MakeFace(plane, -size, size, -size, size).Face()


def _arguments(study):
    solid_bodies = [body for body in study.bodies if body.is_solid]
    listing = TopTools_ListOfShape()
    for body in solid_bodies:
        listing.Append(body.shape)
    return solid_bodies, listing


def _images_of(history_image, shape) -> list:
    answer = history_image(shape)
    return [shape] if answer is None else answer


@logged
def cut_by_plane(study, origin, normal, keep: str = "positive",
                 group: str = "symmetry") -> Report:
    """Оставить часть модели по одну сторону плоскости.

    ``keep`` — ``"positive"``: там, куда смотрит нормаль; ``"negative"`` —
    по другую сторону. ``group`` — куда сложить грани разреза; пусто — не
    складывать.
    """
    report = Report("cut", params={"origin": [float(v) for v in origin],
                                   "normal": [float(v) for v in normal],
                                   "keep": keep, "group": group})
    report.before = study.summary()
    if keep not in ("positive", "negative"):
        return report.fail("BAD_SIDE", f"сторона {keep!r}: ждали positive или negative")
    direction = _unit(normal) * (1.0 if keep == "positive" else -1.0)
    size = max(study.diagonal(), 1.0) * 3.0 + float(np.linalg.norm(
        np.asarray(origin, float) - np.asarray(study.bounds().center)))
    tool, on_plane = half_space(origin, direction, size)
    solid_bodies, listing = _arguments(study)
    if not solid_bodies:
        return report.fail("NO_SOLIDS", "резать нечего: в исследовании нет тел-объёмов")
    tools = TopTools_ListOfShape()
    tools.Append(tool)
    operation = BRepAlgoAPI_Common()
    operation.SetArguments(listing)
    operation.SetTools(tools)
    operation.SetRunParallel(True)
    operation.Build()
    if not operation.IsDone():
        return report.fail("CUT_FAILED", "ядро не смогло разрезать модель этой плоскостью")
    image = from_history(operation.History())
    result = operation.Shape()
    kept = [solid for solid in solids_of(result)]
    if not kept:
        return report.fail("NOTHING_LEFT", "по эту сторону плоскости модели нет — "
                           "проверьте сторону и положение плоскости")

    bodies = []
    for body in study.bodies:
        if not body.is_solid:
            report.note("SKIPPED", f"«{body.name}»: не тело — оставлено как было",
                        WARNING, bodies=[body.name])
            bodies.append(body)
            continue
        pieces = []
        for solid in solids_of(body.shape):
            pieces.extend(_images_of(image, solid))
        pieces = [piece for piece in pieces if _inside(piece, kept)]
        if not pieces:
            report.note("BODY_REMOVED", f"«{body.name}» целиком по другую сторону "
                        f"плоскости — убрано", WARNING, bodies=[body.name])
            continue
        shape = pieces[0] if len(pieces) == 1 else kernel.compound(pieces)
        bodies.append(Body(body.name, shape))
    study.replace(bodies, image, report)

    if group:
        cut_faces = []
        for piece in _images_of(image, on_plane):
            index = study.face_index(piece)
            if index >= 0:
                cut_faces.append(index)
        if cut_faces:
            existing = study.groups.get(group)
            members = set(cut_faces) | (existing.faces if existing else set())
            # Правила у группы нет: при повторном прогоне рецепта её заново
            # соберёт сам разрез, а не отбор граней.
            study.add_group(group, faces=sorted(members))
            report.note("GROUP_MADE", f"грани разреза собраны в группу «{group}» "
                        f"({len(cut_faces)})", "info", faces=cut_faces)
        else:
            report.note("NO_CUT_FACES", "плоскость модель не пересекла — граней "
                        "разреза нет", WARNING)
    report.after = study.summary()
    report.message = (f"объём {report.before['volume']:.6g} → "
                      f"{report.after['volume']:.6g} мм³")
    return report


def _inside(piece, kept) -> bool:
    return any(piece.IsSame(item) for item in kept)


@logged
def split_by_plane(study, origin, normal, group: str = "",
                   bodies=None) -> Report:
    """Разделить тела плоскостью на части, склеенные по разрезу.

    Части тела получают имена «Тело/1», «Тело/2» — по порядку вдоль нормали.
    ``bodies`` — какие тела делить; пусто — все, которые плоскость задевает.
    """
    report = Report("split", params={"origin": [float(v) for v in origin],
                                     "normal": [float(v) for v in normal],
                                     "group": group,
                                     "bodies": list(bodies or ())})
    report.before = study.summary()
    size = max(study.diagonal(), 1.0) * 3.0 + float(np.linalg.norm(
        np.asarray(origin, float) - np.asarray(study.bounds().center)))
    face = plane_face(origin, normal, size)
    chosen = [body for body in study.bodies
              if body.is_solid and (not bodies or body.name in bodies)]
    if not chosen:
        return report.fail("NO_SOLIDS", "делить нечего: подходящих тел-объёмов нет")
    arguments = TopTools_ListOfShape()
    for body in chosen:
        arguments.Append(body.shape)
    tools = TopTools_ListOfShape()
    tools.Append(face)
    splitter = BRepAlgoAPI_Splitter()
    splitter.SetArguments(arguments)
    splitter.SetTools(tools)
    splitter.SetRunParallel(True)
    splitter.Build()
    if not splitter.IsDone():
        return report.fail("SPLIT_FAILED", "ядро не смогло разделить тела этой плоскостью")
    image = from_history(splitter.History())
    direction = _unit(normal)
    names = {body.name for body in chosen}
    fresh = []
    renamed = {}
    for body in study.bodies:
        if body.name not in names:
            fresh.append(body)
            continue
        pieces = []
        for solid in solids_of(body.shape):
            pieces.extend(_images_of(image, solid))
        if len(pieces) <= 1:
            fresh.append(Body(body.name, pieces[0] if pieces else body.shape))
            continue
        pieces.sort(key=lambda piece: float(np.asarray(kernel.bounds(piece).center)
                                            @ direction))
        renamed[body.name] = []
        for number, piece in enumerate(pieces, start=1):
            fresh.append(Body(f"{body.name}/{number}", piece))
            renamed[body.name].append(f"{body.name}/{number}")
        report.note("DIVIDED", f"«{body.name}» разделено на {len(pieces)}",
                    "info", bodies=[body.name])
    if not renamed:
        return report.fail("NOT_CROSSED", "плоскость не пересекает ни одно из тел")
    # Материал у половинок тот же, что был у целого: группа тел получает
    # все части ДО замены, иначе замена сочла бы тело пропавшим.
    for item in study.groups.values():
        if item.kind != "bodies":
            continue
        for old, parts in renamed.items():
            if old in item.bodies:
                item.bodies.discard(old)
                item.bodies.update(parts)
    study.replace(fresh, image, report)
    if group:
        faces = [index for index in (study.face_index(piece)
                                     for piece in _images_of(image, face))
                 if index >= 0]
        if faces:
            study.add_group(group, faces=faces)
    report.after = study.summary()
    report.message = f"разделено тел: {len(renamed)}, тел стало {len(study.bodies)}"
    return report
