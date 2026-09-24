"""Склейка тел общей топологией — для согласованной сетки сборки.

Две детали, касающиеся гранью, для построителя сетки — два несвязанных
тела: узлы по обе стороны стыка не совпадут, и нагрузка через стык не
пойдёт. Склейка делает грань стыка ОДНОЙ на двоих (алгоритм General Fuse
ядра), и сетка по обе её стороны получается общей.

Наложение деталей склейкой не лечится: общий объём принадлежал бы двоим,
и материал в нём был бы неопределён. Такие пары называются, а склейка
отказывает — решать, кому отдать объём, должен человек.
"""

from __future__ import annotations

from OCP.BRepAlgoAPI import BRepAlgoAPI_BuilderAlgo, BRepAlgoAPI_Common
from OCP.TopTools import TopTools_ListOfShape

from .. import kernel
from .model import ERROR, INFO, Body, Report, from_history, logged, solids_of


def overlaps(study, tolerance: float = 0.0) -> list:
    """Пары тел, накладывающихся объёмом: [(имя, имя, объём), …]."""
    diagonal = study.diagonal()
    tolerance = tolerance or 1e-6 * diagonal ** 3
    gap = max(diagonal * 1e-6, 1e-7)
    solid_bodies = [body for body in study.bodies if body.is_solid]
    boxes = [kernel.bounds(body.shape) for body in solid_bodies]
    found = []
    for first in range(len(solid_bodies)):
        for second in range(first + 1, len(solid_bodies)):
            a, b = boxes[first], boxes[second]
            if (a.xmax < b.xmin - gap or b.xmax < a.xmin - gap or
                    a.ymax < b.ymin - gap or b.ymax < a.ymin - gap or
                    a.zmax < b.zmin - gap or b.zmax < a.zmin - gap):
                continue
            common = BRepAlgoAPI_Common(solid_bodies[first].shape,
                                        solid_bodies[second].shape)
            volume = kernel.volume(common.Shape()) if common.IsDone() else 0.0
            if volume > tolerance:
                found.append((solid_bodies[first].name, solid_bodies[second].name,
                              volume))
    return found


@logged
def glue(study, fuzzy: float = 0.0, interfaces: bool = True) -> Report:
    """Склеить все тела-объёмы. ``fuzzy`` — зазор, который ещё считается
    касанием, мм (ноль — только точное касание).

    ``interfaces`` — сложить общие грани в группы по парам тел: «стык A | B».
    По ним задают контакт или связь, если склейка — не то, что нужно.
    """
    report = Report("glue", params={"fuzzy": fuzzy, "interfaces": interfaces})
    report.before = study.summary()
    solid_bodies = [body for body in study.bodies if body.is_solid]
    if len(solid_bodies) < 2:
        report.message = "склеивать нечего: тело одно"
        report.after = report.before
        return report
    crossing = overlaps(study)
    if crossing:
        for first, second, volume in crossing:
            report.note("OVERLAP", f"«{first}» и «{second}» накладываются на "
                        f"{volume:.6g} мм³", ERROR, bodies=[first, second])
        report.ok = False
        report.message = ("тела накладываются — склейка отдала бы общий объём "
                          "двоим. Уберите наложение в исходной модели")
        return report

    arguments = TopTools_ListOfShape()
    for body in solid_bodies:
        arguments.Append(body.shape)
    builder = BRepAlgoAPI_BuilderAlgo()
    builder.SetArguments(arguments)
    if fuzzy > 0.0:
        builder.SetFuzzyValue(fuzzy)
    builder.SetNonDestructive(True)
    builder.SetRunParallel(True)
    builder.Build()
    if not builder.IsDone():
        return report.fail("GLUE_FAILED", "ядро не смогло склеить тела")
    image = from_history(builder.History())

    fresh = []
    for body in study.bodies:
        if not body.is_solid:
            fresh.append(body)
            continue
        pieces = []
        for solid in solids_of(body.shape):
            answer = image(solid)
            pieces.extend([solid] if answer is None else answer)
        fresh.append(Body(body.name, pieces[0] if len(pieces) == 1
                          else kernel.compound(pieces)))
    study.replace(fresh, image, report)
    study.glued = True

    shared = {}
    for index in range(study.face_count):
        owners = study.owners(index)
        if len(owners) > 1:
            shared.setdefault(tuple(sorted(owners)[:2]), []).append(index)
    total = sum(len(faces) for faces in shared.values())
    if interfaces:
        for (first, second), faces in shared.items():
            study.add_group(f"стык {first} | {second}", faces=faces)
    for (first, second), faces in shared.items():
        report.note("INTERFACE", f"«{first}» и «{second}» склеены по "
                    f"{len(faces)} граням", INFO, faces=faces,
                    bodies=[first, second])
    report.after = study.summary()
    report.message = (f"общих граней {total}, пар тел со стыком {len(shared)}"
                      if total else "тела не касаются — общих граней нет")
    return report
