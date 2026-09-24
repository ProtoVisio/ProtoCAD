"""Треугольники исследования — для окна и для STL.

Номер грани у треугольника — номер в КАРТЕ исследования (`Study.face_map`),
а не в обходе `TopExp_Explorer`. Обход выдаёт общую грань склеенных тел
дважды, и номера разъехались бы с группами: щелчок по грани на стыке
добавлял бы в группу не ту грань.
"""

from __future__ import annotations

import numpy as np

from OCP.BRep import BRep_Tool
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.TopAbs import TopAbs_REVERSED
from OCP.TopLoc import TopLoc_Location


def face_triangles(face, deflection: float, angular: float = 0.5):
    """Треугольники одной грани массивом (n, 3, 3), наружу по ориентации.

    ``None`` — грань не разбилась (вырожденная). Пустой массив — разбилась,
    но треугольников нет.
    """
    triangulation = BRep_Tool.Triangulation_s(face, TopLoc_Location())
    if triangulation is None:
        BRepMesh_IncrementalMesh(face, deflection, False, angular, True)
    location = TopLoc_Location()
    triangulation = BRep_Tool.Triangulation_s(face, location)
    if triangulation is None:
        return None
    transform = location.Transformation()
    nodes = np.empty((triangulation.NbNodes(), 3), dtype=np.float64)
    for index in range(1, triangulation.NbNodes() + 1):
        point = triangulation.Node(index).Transformed(transform)
        nodes[index - 1] = (point.X(), point.Y(), point.Z())
    count = triangulation.NbTriangles()
    indices = np.empty((count, 3), dtype=np.int64)
    for index in range(1, count + 1):
        indices[index - 1] = triangulation.Triangle(index).Get()
    indices -= 1
    if face.Orientation() == TopAbs_REVERSED:
        indices = indices[:, [0, 2, 1]]
    return nodes[indices]


def scene(study, deflection: float = 0.0, angular: float = 0.5) -> dict:
    """Всё исследование буферами для вьюпорта.

    Возвращает словарь numpy-массивов: ``positions``, ``normals``,
    ``face_ids`` (номер грани + 1: ноль во вьюпорте — «пусто»), ``body_ids``
    (номер тела + 1), ``edge_positions``, ``edge_ids`` (номер ребра).
    """
    deflection = deflection or max(study.diagonal() * 1e-3, 1e-3)
    whole = study.compound()
    BRepMesh_IncrementalMesh(whole, deflection, False, angular, True)
    body_number = {body.name: index + 1 for index, body in enumerate(study.bodies)}
    chunks, faces, bodies = [], [], []
    for index in range(study.face_count):
        triangles = face_triangles(study.face(index), deflection, angular)
        if triangles is None or not len(triangles):
            continue
        flat = triangles.reshape(-1, 3)
        chunks.append(flat)
        faces.append(np.full(len(flat), index + 1, np.uint32))
        owner = (study.owners(index) or [""])[0]
        bodies.append(np.full(len(flat), body_number.get(owner, 1), np.uint32))
    if chunks:
        positions = np.concatenate(chunks).astype(np.float32)
        a, b, c = positions[0::3], positions[1::3], positions[2::3]
        normals = np.cross(b - a, c - a)
        lengths = np.linalg.norm(normals, axis=1, keepdims=True)
        lengths[lengths == 0.0] = 1.0
        normals = np.repeat((normals / lengths).astype(np.float32), 3, axis=0)
        face_ids = np.concatenate(faces)
        body_ids = np.concatenate(bodies)
    else:
        positions = np.zeros((0, 3), np.float32)
        normals = np.zeros((0, 3), np.float32)
        face_ids = np.zeros(0, np.uint32)
        body_ids = np.zeros(0, np.uint32)

    from .. import kernel

    segments, edge_ids = [], []
    for index in range(study.edge_count):
        edge = study.edge(index)
        if BRep_Tool.Degenerated_s(edge):
            continue
        points = kernel.discretize(edge, deflection)
        if len(points) < 2:
            continue
        coords = np.asarray(points, np.float32)
        pairs = np.empty(((len(coords) - 1) * 2, 3), np.float32)
        pairs[0::2] = coords[:-1]
        pairs[1::2] = coords[1:]
        segments.append(pairs)
        edge_ids.append(np.full(len(pairs), index, np.uint32))
    return {
        "positions": positions,
        "normals": normals,
        "face_ids": face_ids,
        "body_ids": body_ids,
        "edge_positions": (np.concatenate(segments) if segments
                           else np.zeros((0, 3), np.float32)),
        "edge_ids": (np.concatenate(edge_ids) if edge_ids
                     else np.zeros(0, np.uint32)),
    }
