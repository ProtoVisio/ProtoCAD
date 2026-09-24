"""Геометрический слой ProtoCAD на OCCT напрямую, без FreeCAD.

Spike 4 проверяет независимость: то, что мы брали у FreeCAD, — это почти
исключительно OCCT. `Part` — обёртка над `BRepAlgoAPI`/`BRepPrimAPI`,
`TechDraw.project` — над `HLRBRep`, `tessellate()` — над `BRepMesh`.

Здесь те же операции вызываются напрямую. Уровень абстракции свой, а не
подражание FreeCAD: копировать чужой API значит унаследовать и его решения.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from OCP.BRep import BRep_Tool
from OCP.BRepAlgoAPI import BRepAlgoAPI_Common, BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_Transform,
)
from OCP.BRepFilletAPI import BRepFilletAPI_MakeChamfer, BRepFilletAPI_MakeFillet
from OCP.BRepGProp import BRepGProp
from OCP.BRepMesh import BRepMesh_IncrementalMesh
from OCP.BRepPrimAPI import (
    BRepPrimAPI_MakeBox,
    BRepPrimAPI_MakeCylinder,
    BRepPrimAPI_MakePrism,
)
from OCP.BRepTools import BRepTools
from OCP.Bnd import Bnd_Box
from OCP.BRepAdaptor import BRepAdaptor_Curve
from OCP.GCPnts import GCPnts_QuasiUniformDeflection
from OCP.GProp import GProp_GProps
from OCP.gp import gp_Ax1, gp_Ax2, gp_Dir, gp_Pnt, gp_Trsf, gp_Vec
from OCP.HLRAlgo import HLRAlgo_Projector
from OCP.HLRBRep import HLRBRep_Algo, HLRBRep_HLRToShape
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.TopoDS import TopoDS, TopoDS_Compound, TopoDS_Shape
from OCP.BRep import BRep_Builder


@dataclass(frozen=True)
class Box:
    """Габарит тела."""

    xmin: float
    ymin: float
    zmin: float
    xmax: float
    ymax: float
    zmax: float

    @property
    def size(self) -> tuple[float, float, float]:
        return self.xmax - self.xmin, self.ymax - self.ymin, self.zmax - self.zmin

    @property
    def center(self) -> tuple[float, float, float]:
        return (
            (self.xmin + self.xmax) / 2,
            (self.ymin + self.ymax) / 2,
            (self.zmin + self.zmax) / 2,
        )


# --- примитивы ---


def box(length: float, width: float, height: float, origin=(0.0, 0.0, 0.0)):
    return BRepPrimAPI_MakeBox(gp_Pnt(*origin), length, width, height).Shape()


def cylinder(radius: float, height: float, origin=(0.0, 0.0, 0.0), axis=(0.0, 0.0, 1.0)):
    frame = gp_Ax2(gp_Pnt(*origin), gp_Dir(*axis))
    return BRepPrimAPI_MakeCylinder(frame, radius, height).Shape()


def extrude_polygon(points, height: float, z: float = 0.0):
    """Замкнутый профиль → грань → выдавливание. Путь реальной детали из эскиза."""
    polygon = BRepBuilderAPI_MakePolygon()
    for x, y in points:
        polygon.Add(gp_Pnt(x, y, z))
    polygon.Close()
    face = BRepBuilderAPI_MakeFace(polygon.Wire()).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, height)).Shape()


# --- операции ---


def cut(base, tool):
    return BRepAlgoAPI_Cut(base, tool).Shape()


def fuse(base, tool):
    return BRepAlgoAPI_Fuse(base, tool).Shape()


def common(base, tool):
    return BRepAlgoAPI_Common(base, tool).Shape()


def chamfer(shape, size: float, edges=None):
    """Фаска. ``edges`` — отобранные рёбра либо None для всех."""
    maker = BRepFilletAPI_MakeChamfer(shape)
    for edge in edges if edges is not None else iter_edges(shape):
        maker.Add(size, edge)
    return maker.Shape()


def fillet(shape, radius: float, edges):
    maker = BRepFilletAPI_MakeFillet(shape)
    for edge in edges:
        maker.Add(radius, edge)
    return maker.Shape()


def revolve(profile, angle_deg: float = 360.0, origin=(0.0, 0.0, 0.0), axis=(0.0, 1.0, 0.0)):
    """Вращение профиля вокруг оси.

    Профиль должен лежать по одну сторону от оси, иначе OCCT выдаёт
    самопересекающееся тело — отказ здесь честнее молчаливого мусора.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeRevol

    face = _as_face(profile)
    return BRepPrimAPI_MakeRevol(
        face, gp_Ax1(gp_Pnt(*origin), gp_Dir(*axis)), math.radians(angle_deg)
    ).Shape()


def prism(profile, vector):
    """Выдавливание профиля вектором."""
    return BRepPrimAPI_MakePrism(_as_face(profile), gp_Vec(*vector)).Shape()


def _as_face(shape):
    """Контур → грань; грань и тело возвращаются как есть."""
    from OCP.TopAbs import TopAbs_WIRE

    if shape.ShapeType() == TopAbs_WIRE:
        from OCP.TopoDS import TopoDS

        return BRepBuilderAPI_MakeFace(TopoDS.Wire_s(shape)).Face()
    return shape


def mirrored(shape, origin=(0.0, 0.0, 0.0), normal=(1.0, 0.0, 0.0)):
    """Зеркальное отражение относительно плоскости."""
    from OCP.gp import gp_Ax2 as _Ax2

    transform = gp_Trsf()
    transform.SetMirror(_Ax2(gp_Pnt(*origin), gp_Dir(*normal)))
    return BRepBuilderAPI_Transform(shape, transform, True).Shape()


def compound(shapes):
    """Составная форма. Пустые формы отбрасываются: передача null в
    ``BRep_Builder.Add`` роняет процесс access violation, а не исключением."""
    builder = BRep_Builder()
    result = TopoDS_Compound()
    builder.MakeCompound(result)
    for shape in shapes:
        if shape is None or shape.IsNull():
            continue
        builder.Add(result, shape)
    return result


# --- трансформации ---


def translated(shape, vector):
    transform = gp_Trsf()
    transform.SetTranslation(gp_Vec(*vector))
    return BRepBuilderAPI_Transform(shape, transform, True).Shape()


def rotated(shape, angle_deg: float, axis=(0.0, 0.0, 1.0), origin=(0.0, 0.0, 0.0)):
    transform = gp_Trsf()
    transform.SetRotation(
        gp_Ax1(gp_Pnt(*origin), gp_Dir(*axis)), math.radians(angle_deg)
    )
    return BRepBuilderAPI_Transform(shape, transform, True).Shape()


def transformed(shape, matrix: np.ndarray):
    """Применить матрицу 4×4 — так размещаются экземпляры."""
    transform = gp_Trsf()
    transform.SetValues(
        float(matrix[0, 0]), float(matrix[0, 1]), float(matrix[0, 2]), float(matrix[0, 3]),
        float(matrix[1, 0]), float(matrix[1, 1]), float(matrix[1, 2]), float(matrix[1, 3]),
        float(matrix[2, 0]), float(matrix[2, 1]), float(matrix[2, 2]), float(matrix[2, 3]),
    )
    return BRepBuilderAPI_Transform(shape, transform, True).Shape()


# --- разбор ---


def iter_edges(shape):
    """Рёбра тела, каждое ОДИН раз.

    Обход `TopExp_Explorer` идёт по граням, и общее ребро выдаётся от
    каждой из них — у бруска получалось 24 ребра вместо 12. Пока рёбра
    только рисовались, удвоение было незаметно: линия поверх такой же
    линии выглядит одной. Как только по рёбрам стали выбирать и считать —
    «вертикальных рёбер 8» у бруска и вдвое больше отрезков в буфере.

    `MapShapes_s` собирает рёбра без повторов, сохраняя порядок обхода:
    номер ребра остаётся тем же, что и раньше, для первого вхождения.
    """
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedMapOfShape

    mapping = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, mapping)
    for index in range(1, mapping.Extent() + 1):
        yield TopoDS.Edge_s(mapping.FindKey(index))


def iter_faces(shape):
    explorer = TopExp_Explorer(shape, TopAbs_FACE)
    while explorer.More():
        yield TopoDS.Face_s(explorer.Current())
        explorer.Next()


def count(shape, kind=TopAbs_SOLID) -> int:
    explorer = TopExp_Explorer(shape, kind)
    total = 0
    while explorer.More():
        total += 1
        explorer.Next()
    return total


def bounds(shape) -> Box:
    """Габарит по геометрии, БЕЗ прибавки допусков ядра.

    ``Add_s`` строит коробку по триангуляции и раздувает её на допуск формы.
    На простом теле прибавка ничтожна, но после булевых операций допуски
    растут: пластина 100 × 60 × 10 после выреза и оболочки показывала
    100,1 × 60,1 × 10,1. Габарит детали — то, что уходит в чертёж и в
    спецификацию, и завышать его на неизвестную величину нельзя.
    """
    bnd = Bnd_Box()
    if shape is not None and not shape.IsNull():
        BRepBndLib.AddOptimal_s(shape, bnd, True, False)
    if bnd.IsVoid():
        # Пустая форма — это не повод падать. `Bnd_Box.Get()` на пустой
        # коробке бросает исключение, и оно всплывало из отрисовки окна:
        # человек видел трассировку вместо сообщения о том, что операция
        # ничего не оставила.
        return Box(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    # Углы, а не `Get()`: в OCP 8 `Get()` отдаёт структуру вместо шести
    # чисел, и габарит падал на любой детали. Углы одинаковы в обеих.
    low, high = bnd.CornerMin(), bnd.CornerMax()
    return Box(low.X(), low.Y(), low.Z(), high.X(), high.Y(), high.Z())


def is_empty(shape) -> bool:
    """Есть ли в форме хоть что-нибудь.

    Пустой результат булевой операции — это компаунд без вложенных форм.
    Внешне он ведёт себя как обычная форма: не None, не Null, объём ноль.
    Отличить его можно только заглянув внутрь.
    """
    if shape is None or shape.IsNull():
        return True
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer

    return not TopExp_Explorer(shape, TopAbs_FACE).More()


def volume(shape) -> float:
    props = GProp_GProps()
    if shape is None or shape.IsNull():
        # Отказавшая операция оставляет форму пустой. Замер такой формы —
        # обычное дело при показе отказа, и ронять его трассировкой из
        # ядра нельзя: то же уже исправлено в bounds.
        return 0.0
    BRepGProp.VolumeProperties_s(shape, props)
    return props.Mass()


def area(shape) -> float:
    """Площадь поверхности. Для плоской грани — площадь профиля, чем и
    проверяется, что эскиз собрался в тот контур, который рисовали."""
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(shape, props)
    return props.Mass()


def edge_endpoints(edge) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    curve = BRepAdaptor_Curve(edge)
    first = curve.Value(curve.FirstParameter())
    last = curve.Value(curve.LastParameter())
    return (first.X(), first.Y(), first.Z()), (last.X(), last.Y(), last.Z())


def face_normal(face) -> tuple[float, float, float]:
    """Нормаль плоской грани с учётом её ориентации в теле.

    Ориентация обязательна: у грани, вошедшей в тело развёрнутой, геометрия
    та же, а наружу смотрит противоположная сторона. Выдавливание по нормали
    без этой поправки на половине граней уходит внутрь материала.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane
    from OCP.TopAbs import TopAbs_REVERSED

    surface = BRepAdaptor_Surface(face)
    if surface.GetType() != GeomAbs_Plane:
        raise ValueError("нормаль берётся только у плоской грани")
    direction = surface.Plane().Axis().Direction()
    sign = -1.0 if face.Orientation() == TopAbs_REVERSED else 1.0
    return (direction.X() * sign, direction.Y() * sign, direction.Z() * sign)


def face_center(face) -> tuple[float, float, float]:
    """Центр масс грани — им грани и различают при отборе."""
    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    point = props.CentreOfMass()
    return point.X(), point.Y(), point.Z()


def top_face(shape):
    """Самая верхняя плоская грань. По ней вскрывают оболочку.

    Отбор идёт по центру масс, а не по номеру грани: порядок граней в теле
    меняется при любой правке дерева, и «грань №4» после пересчёта
    оказывается совсем другой поверхностью.
    """
    best, best_z = None, None
    for face in iter_faces(shape):
        if not _is_planar(face):
            continue
        z = face_center(face)[2]
        if best_z is None or z > best_z:
            best, best_z = face, z
    return best


def shell(shape, thickness: float, open_face=None):
    """Выбрать материал изнутри, оставив стенку заданной толщины.

    ``open_face`` — грань, которую надо вскрыть; без неё берётся верхняя.
    Толщина отрицательна внутрь: снаружи размеры детали меняться не должны.
    """
    from OCP.BRepOffsetAPI import BRepOffsetAPI_MakeThickSolid
    from OCP.TopTools import TopTools_ListOfShape

    face = open_face if open_face is not None else top_face(shape)
    if face is None:
        raise ValueError("оболочка: не найдено плоской грани для вскрытия")
    opening = TopTools_ListOfShape()
    opening.Append(face)
    maker = BRepOffsetAPI_MakeThickSolid()
    maker.MakeThickSolidByJoin(shape, opening, -abs(thickness), 1.0e-3)
    if not maker.IsDone():
        raise ValueError("оболочка: построение не удалось")
    return maker.Shape()


def _face_map(shape):
    """Соответствие ребро → примыкающие грани."""
    from OCP.TopExp import TopExp
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

    mapping = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, mapping)
    return mapping


def _is_planar(face) -> bool:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane

    return BRepAdaptor_Surface(face).GetType() == GeomAbs_Plane


def vertical_edges(shape, tolerance: float = 1e-7, planar_only: bool = True):
    """Рёбра, параллельные Z, — по ним снимается фаска или скругление корпуса.

    ``planar_only`` отбрасывает рёбра, у которых хоть одна примыкающая грань
    не плоская. Без этого в отбор попадают ШВЫ ЦИЛИНДРОВ: у отверстия шов —
    такой же отрезок, параллельный Z. Скруглить шов радиусом порядка радиуса
    самого отверстия нельзя, и операция падала с `StdFail_NotDone` на любой
    детали с отверстиями, то есть почти на любой.
    """
    mapping = _face_map(shape) if planar_only else None
    result = []
    for edge in iter_edges(shape):
        (x1, y1, _z1), (x2, y2, _z2) = edge_endpoints(edge)
        if abs(x1 - x2) > tolerance or abs(y1 - y2) > tolerance:
            continue
        if mapping is not None:
            try:
                faces = mapping.FindFromKey(edge)
            except Exception:  # noqa: BLE001 — ребро без граней пропускаем
                continue
            if not all(_is_planar(TopoDS.Face_s(face)) for face in faces):
                continue
        result.append(edge)
    return result


def discretize(edge, deflection: float = 0.05) -> list[tuple[float, float, float]]:
    """Ребро в ломаную — для отрисовки и для чертежа."""
    curve = BRepAdaptor_Curve(edge)
    sampler = GCPnts_QuasiUniformDeflection(curve, deflection)
    if not sampler.IsDone():
        return []
    points = []
    for index in range(1, sampler.NbPoints() + 1):
        point = sampler.Value(index)
        points.append((point.X(), point.Y(), point.Z()))
    return points


# --- тесселяция ---


def tessellate(shape, deflection: float = 0.1, angular: float = 0.5):
    """Треугольники и нормали плоского затенения, готовые для GPU."""
    positions, normals, _ = tessellate_faces(shape, deflection, angular)
    return positions, normals


def tessellate_faces(shape, deflection: float = 0.1, angular: float = 0.5):
    """То же, но с номером грани у каждой вершины.

    Номер нужен для выбора граней в виде. Считать его отдельным проходом
    нельзя: разбиение на треугольники должно быть тем же самым, иначе номера
    разъедутся с геометрией — и щелчок по грани выделит соседнюю.

    Номер — это порядковый номер грани в обходе тела. Он действителен ровно
    до следующего пересчёта дерева и наружу, в файл, не идёт: после правки
    операции обход даёт другие грани.
    """
    BRepMesh_IncrementalMesh(shape, deflection, False, angular, True)
    chunks = []
    face_chunks = []
    for face_index, face in enumerate(iter_faces(shape)):
        location = TopLoc_Location()
        triangulation = BRep_Tool.Triangulation_s(face, location)
        if triangulation is None:
            continue
        transform = location.Transformation()
        nodes = np.empty((triangulation.NbNodes(), 3), dtype=np.float64)
        for index in range(1, triangulation.NbNodes() + 1):
            point = triangulation.Node(index).Transformed(transform)
            nodes[index - 1] = (point.X(), point.Y(), point.Z())
        indices = np.empty((triangulation.NbTriangles(), 3), dtype=np.int64)
        reversed_face = face.Orientation() == 1  # TopAbs_REVERSED
        for index in range(1, triangulation.NbTriangles() + 1):
            a, b, c = triangulation.Triangle(index).Get()
            indices[index - 1] = (a - 1, c - 1, b - 1) if reversed_face else (a - 1, b - 1, c - 1)
        vertices = nodes[indices.reshape(-1)]
        chunks.append(vertices)
        face_chunks.append(np.full(len(vertices), face_index, dtype=np.uint32))
    if not chunks:
        return (
            np.zeros((0, 3), np.float32),
            np.zeros((0, 3), np.float32),
            np.zeros(0, np.uint32),
        )

    positions = np.concatenate(chunks).astype(np.float32)
    a = positions[0::3]
    b = positions[1::3]
    c = positions[2::3]
    face_normals = np.cross(b - a, c - a)
    lengths = np.linalg.norm(face_normals, axis=1, keepdims=True)
    lengths[lengths == 0] = 1.0
    normals = np.repeat((face_normals / lengths).astype(np.float32), 3, axis=0)
    return positions, normals, np.concatenate(face_chunks)


def face_at(shape, index: int):
    """Грань по номеру из ``tessellate_faces``. None, если номер устарел."""
    for current, face in enumerate(iter_faces(shape)):
        if current == index:
            return face
    return None


def vertices(shape) -> np.ndarray:
    """Точки тела — вершины его рёбер, без повторов.

    Нужны для выбора точек в виде: привязка эскиза к вершине детали должна
    попадать в саму вершину, а не в ближайшую точку разбиения ребра.
    """
    from OCP.BRep import BRep_Tool
    from OCP.TopAbs import TopAbs_VERTEX
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS

    found = []
    explorer = TopExp_Explorer(shape, TopAbs_VERTEX)
    while explorer.More():
        point = BRep_Tool.Pnt_s(TopoDS.Vertex_s(explorer.Current()))
        found.append((point.X(), point.Y(), point.Z()))
        explorer.Next()
    if not found:
        return np.zeros((0, 3), np.float32)
    unique = np.unique(np.round(np.asarray(found, np.float64), 6), axis=0)
    return unique.astype(np.float32)


def edge_segments(shape, deflection: float = 0.05, with_index: bool = False):
    """Рёбра тела отрезками для GL_LINES.

    ``with_index`` добавляет номер ребра у каждой вершины: без него выбрать
    ребро в виде нельзя — по точке на экране видно, что это ребро, но не
    какое именно.
    """
    segments = []
    indices = []
    for index, edge in enumerate(iter_edges(shape)):
        points = discretize(edge, deflection)
        if len(points) < 2:
            continue
        coords = np.asarray(points, dtype=np.float32)
        starts, ends = coords[:-1], coords[1:]
        merged = np.empty((len(starts) * 2, 3), dtype=np.float32)
        merged[0::2] = starts
        merged[1::2] = ends
        segments.append(merged)
        indices.append(np.full(len(merged), index, dtype=np.uint32))
    if not segments:
        empty = np.zeros((0, 3), np.float32)
        return (empty, np.zeros(0, np.uint32)) if with_index else empty
    joined = np.concatenate(segments)
    return (joined, np.concatenate(indices)) if with_index else joined


def validate(shape) -> list[str]:
    """Что не так с телом. Пустой список — всё в порядке.

    Булева операция умеет вернуть форму, которая строится и даже
    отрисовывается, но телом не является: незамкнутая оболочка,
    самопересечение, вывернутая грань. Такая форма ведёт себя необъяснимо —
    выглядит тёмным пятном, теряет грани при выборе, роняет следующую
    операцию где-то далеко от места, где возникла. Ловить это надо там, где
    оно появилось.

    Проверяет ядро (``BRepCheck_Analyzer``), а не мы: у него для этого есть
    полный разбор топологии, а у нас — только догадки по картинке.
    """
    if shape is None or shape.IsNull():
        return ["формы нет"]
    from OCP.BRepCheck import BRepCheck_Analyzer

    problems = []
    analyzer = BRepCheck_Analyzer(shape, True)
    if not analyzer.IsValid():
        problems.append("ядро считает форму неправильной")

    from OCP.TopAbs import TopAbs_SHELL, TopAbs_SOLID
    from OCP.TopExp import TopExp_Explorer

    solids = count(shape, TopAbs_SOLID)
    if solids == 0:
        shells = count(shape, TopAbs_SHELL)
        problems.append(
            "тела нет: получилась оболочка" if shells else "тела нет: получилась поверхность"
        )
    return problems


def solid_count(shape) -> int:
    """Сколько отдельных тел. Больше одного — не отказ, но об этом говорят."""
    from OCP.TopAbs import TopAbs_SOLID

    return 0 if shape is None or shape.IsNull() else count(shape, TopAbs_SOLID)


def straight_ends(edge, tolerance: float = 1e-6):
    """Концы ребра, если оно прямое; иначе ``None``.

    Опорой для параллельности годится только прямое ребро. Отличить его от
    дуги по числу точек разбиения нельзя — отрезок тоже может разбиться на
    две точки, а пологая дуга на две точки не разобьётся; смотрим на
    отклонение точек разбиения от хорды.
    """
    points = discretize(edge, 0.05)
    if len(points) < 2:
        return None
    first = np.asarray(points[0], dtype=float)
    last = np.asarray(points[-1], dtype=float)
    along = last - first
    length = float(np.linalg.norm(along))
    if length < tolerance:
        return None
    along = along / length
    for point in points[1:-1]:
        offset = np.asarray(point, dtype=float) - first
        if float(np.linalg.norm(offset - along * float(offset @ along))) > tolerance:
            return None
    return tuple(first), tuple(last)


def edge_at(shape, index: int):
    """Ребро по номеру обхода. None, если номер устарел."""
    for current, edge in enumerate(iter_edges(shape)):
        if current == index:
            return edge
    return None


def edge_signature(edge, precision: int = 3) -> tuple:
    """Опознавательная подпись ребра: середина и длина.

    Номер ребра для этого не годится — обход меняется при любой правке
    дерева, и «ребро №7» после пересчёта оказывается другим. Подпись по
    геометрии переживает пересчёт, пока само ребро не сдвинулось.

    Это НЕ топологическое имя. Изменение размера выше по дереву сдвигает
    рёбра, подпись перестаёт совпадать, и операция обязана об этом
    сказать, а не молча скруглить не то.
    """
    props = GProp_GProps()
    BRepGProp.LinearProperties_s(edge, props)
    middle = props.CentreOfMass()
    return (
        round(middle.X(), precision),
        round(middle.Y(), precision),
        round(middle.Z(), precision),
        round(props.Mass(), precision),
    )


def edges_matching(shape, signatures, tolerance: float = 1e-2):
    """Рёбра тела по подписям. Возвращает найденные и потерянные подписи."""
    wanted = [tuple(signature) for signature in signatures]
    available = [(edge_signature(edge), edge) for edge in iter_edges(shape)]
    found, missing = [], []
    for signature in wanted:
        best, best_gap = None, tolerance
        for candidate, edge in available:
            gap = max(abs(a - b) for a, b in zip(candidate, signature))
            if gap <= best_gap:
                best, best_gap = edge, gap
        if best is None:
            missing.append(signature)
        else:
            found.append(best)
    return found, missing


# --- проекция для чертежа ---


def project(
    shape,
    direction=(0.0, 0.0, 1.0),
    x_direction=(1.0, 0.0, 0.0),
    with_hidden: bool = False,
):
    """Проекция с удалением невидимых линий.

    ``direction`` — куда смотрим (ось Z системы вида), ``x_direction`` — что
    считается «вправо» на листе. Направление «вверх» получается их векторным
    произведением, отдельно не задаётся: так устроен ``gp_Ax2``.

    Задавать систему координат вида ЯВНО обязательно. Полагаться на то, в
    какой плоскости вернутся кривые, нельзя — на этом вид спереди приходил
    повёрнутым на 90°, и деталь 120×18 рисовалась узкой и высокой.

    Возвращает (видимые, скрытые); координаты — в плоскости проекции.
    """
    frame = gp_Ax2(gp_Pnt(0, 0, 0), gp_Dir(*direction), gp_Dir(*x_direction))
    projector = HLRAlgo_Projector(frame)
    algo = HLRBRep_Algo()
    algo.Add(shape)
    algo.Projector(projector)
    algo.Update()
    algo.Hide()
    to_shape = HLRBRep_HLRToShape(algo)

    def _usable(shape):
        return shape if shape is not None and not shape.IsNull() else None

    # Видимые рёбра и контур силуэта — разные наборы: у цилиндра образующие
    # приходят только в OutLine. Берём оба.
    parts = [p for p in (_usable(to_shape.VCompound()),
                         _usable(to_shape.OutLineVCompound())) if p is not None]
    visible = compound(parts) if parts else None
    hidden = _usable(to_shape.HCompound()) if with_hidden else None
    return visible, hidden


# --- обмен ---


def export_step(shape, path: str) -> str:
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer

    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    writer.Write(str(path))
    return str(path)


def import_step(path: str):
    from OCP.STEPControl import STEPControl_Reader

    reader = STEPControl_Reader()
    reader.ReadFile(str(path))
    reader.TransferRoots()
    return reader.OneShape()


def export_brep(shape, path: str) -> str:
    BRepTools.Write_s(shape, str(path))
    return str(path)


def import_brep(path: str):
    shape = TopoDS_Shape()
    builder = BRep_Builder()
    BRepTools.Read_s(shape, str(path), builder)
    return shape
