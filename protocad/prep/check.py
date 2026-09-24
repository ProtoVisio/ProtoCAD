"""Проверка геометрии перед расчётом.

Построитель сетки на плохой геометрии либо падает, либо — хуже — строит
сетку, по которой считать нельзя: щель между деталями становится
разрывом в модели, наложение — удвоенной жёсткостью, крошечное ребро —
элементом с углом в доли градуса. Узнают об этом по странным напряжениям.

Поэтому проверка говорит заранее и адресно: что не так, с каким телом и
какими гранями — номерами, чтобы окно могло их подсветить.
"""

from __future__ import annotations

from OCP.BRep import BRep_Tool
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_VERTEX
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopoDS import TopoDS
from OCP.TopTools import (
    TopTools_IndexedDataMapOfShapeListOfShape,
    TopTools_IndexedMapOfShape,
)

from .. import kernel
from .model import ERROR, INFO, WARNING, Report, logged

#: Сколько номеров перечислять в тексте замечания. Остальное — в списке
#: номеров для подсветки: тысяча чисел в строке никому не поможет.
LISTED = 8

#: Сколько тел проверять попарно без явной просьбы. Пары растут квадратом:
#: на сотне касающихся деталей это тысячи булевых операций и минуты.
PAIR_LIMIT = 60


@logged
def check(study, small: float = 0.0, deep: bool = False,
          pairs=None) -> Report:
    """Проверить исследование. Геометрию не меняет.

    ``small`` — что считать мелким ребром, мм. По умолчанию тысячная доля
    диагонали габарита: на детали в метр это миллиметр, на плате в 50 мм —
    пять сотых.

    ``deep`` — ещё и самопересечения (`BRepAlgoAPI_Check`). Дорого: на
    большой сборке это минуты, поэтому по требованию.

    ``pairs`` — наложения и касания между телами. Попарно, с отсевом по
    габаритам. ``None`` — если тел не больше ``PAIR_LIMIT``; ``True`` —
    всегда, ``False`` — никогда.
    """
    diagonal = study.diagonal()
    report = Report("check", params={"small": small, "deep": deep, "pairs": pairs})
    small = small or max(diagonal * 1e-3, 1e-6)
    report.used["small"] = small
    report.before = study.summary()
    if not study.bodies:
        return report.fail("EMPTY", "в исследовании нет ни одного тела")

    for body in study.bodies:
        _check_body(study, body, small, deep, report)
    many = len(study.bodies) > PAIR_LIMIT
    if pairs is None and many:
        report.note("PAIRS_SKIPPED",
                    f"наложения и касания не проверялись: тел {len(study.bodies)}, "
                    f"попарная проверка займёт долго. Запросите её явно "
                    f"(pairs=True)", INFO)
    elif (pairs or pairs is None) and len(study.bodies) > 1:
        _check_pairs(study, report, diagonal)

    blocking = [item for item in report.findings if item.severity == ERROR]
    report.ok = not blocking
    report.message = ("замечаний нет" if not report.findings else
                      f"ошибок {len(blocking)}, предупреждений "
                      f"{len(report.warnings)}")
    return report


def _global_faces(study, faces) -> list:
    return [index for index in (study.face_index(face) for face in faces)
            if index >= 0]


def _global_edges(study, edges) -> list:
    return [index for index in (study.edge_index(edge) for edge in edges)
            if index >= 0]


def _listed(values) -> str:
    values = list(values)
    shown = ", ".join(str(value) for value in values[:LISTED])
    return shown + (f" и ещё {len(values) - LISTED}" if len(values) > LISTED else "")


def _check_body(study, body, small: float, deep: bool, report: Report) -> None:
    name = body.name
    shape = body.shape

    if not body.is_solid:
        free = _free_edges(shape)
        detail = (f"свободных рёбер {len(free)} — в оболочке дыры" if free
                  else "грани замкнуты, но тело из них не собрано")
        report.note("NOT_SOLID",
                    f"«{name}»: не тело, а поверхность ({detail}) — объёмную "
                    f"сетку не построить. «Исправить» сшивает грани в "
                    f"пределах допуска и собирает тело",
                    ERROR, edges=_global_edges(study, free), bodies=[name])
        return

    volume = kernel.volume(shape)
    if volume < 0.0:
        report.note("INVERTED",
                    f"«{name}»: тело вывернуто (объём {volume:.6g} мм³ меньше "
                    f"нуля) — «Исправить» развернёт его",
                    ERROR, bodies=[name])

    analyzer = BRepCheck_Analyzer(shape, True)
    if not analyzer.IsValid():
        bad = [face for face in _faces(shape)
               if not BRepCheck_Analyzer(face, True).IsValid()]
        report.note("INVALID",
                    f"«{name}»: ядро считает форму неправильной"
                    + (f", граней с ошибками {len(bad)}" if bad else "")
                    + " — «Исправить» чинит большую часть таких случаев",
                    ERROR, faces=_global_faces(study, bad), bodies=[name])

    free = _free_edges(shape)
    if free:
        report.note("FREE_EDGES",
                    f"«{name}»: {len(free)} свободных рёбер — в оболочке "
                    f"тела дыра", ERROR, edges=_global_edges(study, free),
                    bodies=[name])
    crowded = _nonmanifold_edges(shape)
    if crowded:
        report.note("NONMANIFOLD",
                    f"«{name}»: {len(crowded)} рёбер, где сходятся больше "
                    f"двух граней", WARNING,
                    edges=_global_edges(study, crowded), bodies=[name])

    tiny = []
    for edge in _edges(shape):
        if BRep_Tool.Degenerated_s(edge):
            continue
        if _length(edge) < small:
            tiny.append(edge)
    if tiny:
        indexes = _global_edges(study, tiny)
        report.note("SMALL_EDGES",
                    f"«{name}»: {len(tiny)} рёбер короче {small:.4g} мм — "
                    f"сетка у них сгустится или выродится (рёбра "
                    f"{_listed(indexes)})", WARNING, edges=indexes,
                    bodies=[name])

    slivers = []
    for face in _faces(shape):
        width = _width(face)
        if width is not None and width < small:
            slivers.append(face)
    if slivers:
        indexes = _global_faces(study, slivers)
        report.note("SLIVER_FACES",
                    f"«{name}»: {len(slivers)} узких граней уже {small:.4g} мм "
                    f"(грани {_listed(indexes)}) — кандидаты на удаление",
                    WARNING, faces=indexes, bodies=[name])

    worst = _worst_tolerance(shape)
    if worst > max(small, 1e-3):
        report.note("TOLERANCE",
                    f"«{name}»: допуск ядра дорос до {worst:.4g} мм — "
                    f"геометрия «рыхлая», построитель сетки может на ней "
                    f"споткнуться", WARNING, bodies=[name])

    lumps = kernel.solid_count(shape)
    if lumps > 1:
        report.note("SEVERAL_SOLIDS",
                    f"«{name}»: {lumps} отдельных тел под одним именем",
                    INFO, bodies=[name])

    if deep:
        from OCP.BRepAlgoAPI import BRepAlgoAPI_Check

        probe = BRepAlgoAPI_Check(shape, True, True)
        if not probe.IsValid():
            report.note("SELF_INTERSECTION",
                        f"«{name}»: тело пересекает само себя или негодно для "
                        f"булевых операций", ERROR, bodies=[name])


def _check_pairs(study, report: Report, diagonal: float) -> None:
    """Наложения и касания между телами.

    Наложение — ошибка: объём, принадлежащий двум телам, считается дважды.
    Касание без общей топологии — предупреждение: сетки на стыке будут
    несогласованными, и нагрузка через стык не пойдёт, пока их не склеить
    или не задать контакт.
    """
    from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
    from OCP.BRepExtrema import BRepExtrema_DistShapeShape

    gap = max(diagonal * 1e-6, 1e-7)
    boxes = [kernel.bounds(body.shape) for body in study.bodies]
    shared = _shared_pairs(study)
    for first in range(len(study.bodies)):
        for second in range(first + 1, len(study.bodies)):
            a, b = boxes[first], boxes[second]
            if (a.xmax < b.xmin - gap or b.xmax < a.xmin - gap or
                    a.ymax < b.ymin - gap or b.ymax < a.ymin - gap or
                    a.zmax < b.zmin - gap or b.zmax < a.zmin - gap):
                continue
            one, two = study.bodies[first], study.bodies[second]
            if not (one.is_solid and two.is_solid):
                continue
            common = BRepAlgoAPI_Common(one.shape, two.shape)
            overlap = kernel.volume(common.Shape()) if common.IsDone() else 0.0
            if overlap > gap * diagonal * diagonal:
                report.note("OVERLAP",
                            f"«{one.name}» и «{two.name}» накладываются на "
                            f"{overlap:.6g} мм³ — объём будет посчитан дважды",
                            ERROR, bodies=[one.name, two.name])
                continue
            if (one.name, two.name) in shared:
                continue
            distance = BRepExtrema_DistShapeShape(one.shape, two.shape)
            if distance.IsDone() and distance.Value() <= gap:
                report.note("CONTACT",
                            f"«{one.name}» и «{two.name}» касаются, но не "
                            f"склеены — сетки на стыке не совпадут. «Склеить» "
                            f"сделает стык общим", WARNING,
                            bodies=[one.name, two.name])


def _shared_pairs(study) -> set:
    found = set()
    for index in range(study.face_count):
        owners = study.owners(index)
        if len(owners) > 1:
            for first in owners:
                for second in owners:
                    if first != second:
                        found.add((first, second))
    return found


# --- мелочи ---------------------------------------------------------------


def _faces(shape) -> list:
    found = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, found)
    return [TopoDS.Face_s(found.FindKey(index))
            for index in range(1, found.Extent() + 1)]


def _edges(shape) -> list:
    found = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_EDGE, found)
    return [TopoDS.Edge_s(found.FindKey(index))
            for index in range(1, found.Extent() + 1)]


def _edge_faces(shape):
    mapping = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(shape, TopAbs_EDGE, TopAbs_FACE, mapping)
    return mapping


def _distinct_faces(mapping, index: int) -> int:
    seen = TopTools_IndexedMapOfShape()
    for face in mapping.FindFromIndex(index):
        seen.Add(face)
    return seen.Extent()


def _free_edges(shape) -> list:
    """Рёбра, у которых одна грань. Шов цилиндра и вырожденное ребро
    полюса сферы — не дыры, хотя грань у них тоже одна."""
    mapping = _edge_faces(shape)
    found = []
    for index in range(1, mapping.Extent() + 1):
        edge = TopoDS.Edge_s(mapping.FindKey(index))
        if BRep_Tool.Degenerated_s(edge):
            continue
        if _distinct_faces(mapping, index) != 1:
            continue
        face = TopoDS.Face_s(mapping.FindFromIndex(index).First())
        if BRep_Tool.IsClosed_s(edge, face):
            continue
        found.append(edge)
    return found


def _nonmanifold_edges(shape) -> list:
    mapping = _edge_faces(shape)
    return [TopoDS.Edge_s(mapping.FindKey(index))
            for index in range(1, mapping.Extent() + 1)
            if _distinct_faces(mapping, index) > 2]


def _length(edge) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.LinearProperties_s(edge, props)
    return props.Mass()


def _width(face):
    """Ширина грани по площади и периметру: 2·S/P.

    Для полосы длиной L и шириной w это почти ровно w, для круга — радиус.
    Точной «минимальной ширины» ядро не даёт, а эта оценка ловит именно то,
    что ломает сетку: длинные узкие полоски.
    """
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    props = GProp_GProps()
    BRepGProp.SurfaceProperties_s(face, props)
    area = props.Mass()
    perimeter = 0.0
    explorer = TopExp_Explorer(face, TopAbs_EDGE)
    while explorer.More():
        edge = TopoDS.Edge_s(explorer.Current())
        if not BRep_Tool.Degenerated_s(edge):
            perimeter += _length(edge)
        explorer.Next()
    if perimeter <= 0.0:
        return None
    return 2.0 * area / perimeter


def _worst_tolerance(shape) -> float:
    worst = 0.0
    for kind, tool in ((TopAbs_VERTEX, lambda s: BRep_Tool.Tolerance_s(TopoDS.Vertex_s(s))),
                       (TopAbs_EDGE, lambda s: BRep_Tool.Tolerance_s(TopoDS.Edge_s(s))),
                       (TopAbs_FACE, lambda s: BRep_Tool.Tolerance_s(TopoDS.Face_s(s)))):
        explorer = TopExp_Explorer(shape, kind)
        while explorer.More():
            worst = max(worst, tool(explorer.Current()))
            explorer.Next()
    return worst
