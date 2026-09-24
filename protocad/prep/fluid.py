"""Область течения вокруг модели — для расчёта обтекания (CFD).

Считают не деталь, а воздух или воду вокруг неё. Область — коробка с
запасом от модели, из которой вычтены тела. Грани сразу раскладываются по
группам, без которых задачу не поставить:

* ``inlet`` / ``outlet`` — стенки коробки навстречу и по потоку;
* ``farfield`` — остальные стенки коробки;
* ``wall`` — поверхность самих тел.

Группы, уже заданные на телах, переезжают на соответствующие стенки
области: «нагреватель», выбранный на детали, остаётся нагревателем и в
области течения.

С ``keep_solids`` тела остаются в модели и склеиваются с областью по
общим граням — это постановка сопряжённого теплообмена.
"""

from __future__ import annotations

import numpy as np

from OCP.BRepAlgoAPI import BRepAlgoAPI_BuilderAlgo, BRepAlgoAPI_Cut
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.gp import gp_Pnt
from OCP.TopTools import TopTools_ListOfShape

from .. import kernel
from .model import Body, Report, faces_of_shape, from_history, logged, solids_of
from .select import describe

DEFAULT_NAMES = {"inlet": "inlet", "outlet": "outlet", "farfield": "farfield",
                 "wall": "wall", "fluid": "fluid"}


def _padding(value, size) -> list:
    """Запас: число — со всех сторон; шесть чисел — (−x, +x, −y, +y, −z, +z).
    По умолчанию — наибольший размер модели с каждой стороны."""
    if value is None:
        return [float(max(size))] * 6
    if isinstance(value, (int, float)):
        return [float(value)] * 6
    values = [float(item) for item in value]
    if len(values) != 6:
        raise ValueError("запас задаётся одним числом или шестью: −x, +x, −y, +y, −z, +z")
    return values


@logged
def enclosure(study, padding=None, flow=(1.0, 0.0, 0.0), keep_solids: bool = False,
              names=None) -> Report:
    """Построить область течения вокруг всех тел исследования.

    ``flow`` — направление потока; ``None`` — выделенного потока нет, и все
    стенки коробки идут в ``farfield``. Поток должен идти вдоль одной из осей:
    коробка стоит по осям, и «вход» у неё — одна грань.
    """
    names = {**DEFAULT_NAMES, **(names or {})}
    report = Report("enclosure", params={
        "padding": padding, "flow": list(flow) if flow is not None else None,
        "keep_solids": keep_solids, "names": dict(names)})
    report.before = study.summary()
    solid_bodies = [body for body in study.bodies if body.is_solid]
    if not solid_bodies:
        return report.fail("NO_SOLIDS", "обтекать нечего: в исследовании нет тел-объёмов")
    box = study.bounds()
    try:
        pad = _padding(padding, box.size)
    except ValueError as failure:
        return report.fail("BAD_PADDING", str(failure))
    low = (box.xmin - pad[0], box.ymin - pad[2], box.zmin - pad[4])
    high = (box.xmax + pad[1], box.ymax + pad[3], box.zmax + pad[5])
    domain = BRepPrimAPI_MakeBox(gp_Pnt(*low), gp_Pnt(*high)).Shape()
    domain_faces = faces_of_shape(domain)

    axis = None
    if flow is not None:
        vector = np.asarray(flow, float)
        axis = int(np.argmax(np.abs(vector)))
        if np.count_nonzero(np.abs(vector) > 1e-12) != 1:
            return report.fail("BAD_FLOW", "поток должен идти вдоль одной из осей "
                               "X, Y или Z")
        sign = 1.0 if vector[axis] > 0 else -1.0

    arguments = TopTools_ListOfShape()
    arguments.Append(domain)
    tools = TopTools_ListOfShape()
    for body in solid_bodies:
        tools.Append(body.shape)
    if keep_solids:
        operation = BRepAlgoAPI_BuilderAlgo()
        everything = TopTools_ListOfShape()
        everything.Append(domain)
        for body in solid_bodies:
            everything.Append(body.shape)
        operation.SetArguments(everything)
    else:
        operation = BRepAlgoAPI_Cut()
        operation.SetArguments(arguments)
        operation.SetTools(tools)
    operation.SetRunParallel(True)
    operation.Build()
    if not operation.IsDone():
        return report.fail("DOMAIN_FAILED", "ядро не смогло вычесть тела из области")
    image = from_history(operation.History())

    def images(shape) -> list:
        answer = image(shape)
        return [shape] if answer is None else answer

    bodies_solids = []
    new_bodies = []
    if keep_solids:
        for body in solid_bodies:
            pieces = []
            for solid in solids_of(body.shape):
                pieces.extend(images(solid))
            bodies_solids.extend(pieces)
            new_bodies.append(Body(body.name, pieces[0] if len(pieces) == 1
                                   else kernel.compound(pieces)))
    fluid = [piece for piece in images(domain)
             if not any(piece.IsSame(item) for item in bodies_solids)]
    if not fluid:
        return report.fail("NO_FLUID", "от области ничего не осталось")
    fluid_shape = fluid[0] if len(fluid) == 1 else kernel.compound(fluid)
    new_bodies.insert(0, Body(names["fluid"], fluid_shape))
    if len(fluid) > 1:
        report.note("FLUID_SPLIT", f"область течения распалась на {len(fluid)} "
                    f"несвязанных частей — тела перегородили её", "warning")
    others = [body for body in study.bodies if not body.is_solid]
    study.replace(new_bodies + others, image, report)
    study.glued = keep_solids

    buckets = {names["inlet"]: [], names["outlet"]: [], names["farfield"]: []}
    for face in domain_faces:
        data = describe(face)
        normal = np.asarray(data["normal"], float)
        if axis is not None and abs(abs(normal[axis]) - 1.0) < 1e-9:
            key = names["inlet"] if normal[axis] * sign < 0 else names["outlet"]
        else:
            key = names["farfield"]
        for piece in images(face):
            index = study.face_index(piece)
            if index >= 0:
                buckets[key].append(index)
    fluid_faces = set(study.faces_of(names["fluid"]))
    walls = []
    for body in solid_bodies:
        for face in faces_of_shape(body.shape):
            for piece in images(face):
                index = study.face_index(piece)
                if index >= 0 and index in fluid_faces:
                    walls.append(index)
    buckets[names["wall"]] = walls
    for name, faces in buckets.items():
        if faces:
            study.add_group(name, faces=sorted(set(faces)))
    report.after = study.summary()
    report.message = (f"область {high[0] - low[0]:.4g} × {high[1] - low[1]:.4g} × "
                      f"{high[2] - low[2]:.4g} мм, объём течения "
                      f"{kernel.volume(fluid_shape):.6g} мм³")
    return report
