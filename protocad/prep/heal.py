"""Лечение геометрии: сшить, починить, собрать тело, слить лишние грани.

Порядок не случаен:

1. **сшивка** — только для того, что пришло поверхностями: закрывает зазоры
   между гранями в пределах допуска и даёт оболочку;
2. **тело из замкнутой оболочки** — иначе объёмной сетки не будет;
3. **починка** (`ShapeFix_Shape`) — ориентация, провалы в контурах,
   рёбра с неверными допусками;
4. **слияние граней** (`ShapeUpgrade_UnifySameDomain`) — грань, разрезанная
   экспортёром на полоски по одной поверхности, становится одной. Для сетки
   это главное: каждая лишняя полоска — это линия, вдоль которой построитель
   обязан положить узлы.

Грани, входящие в группы, при слиянии НЕ растворяются в соседних: граница
области с граничным условием — это то, что человек выбрал, и стирать её
ради красоты сетки нельзя.
"""

from __future__ import annotations

from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeSolid, BRepBuilderAPI_Sewing
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.ShapeFix import ShapeFix_Shape, ShapeFix_Solid
from OCP.ShapeUpgrade import ShapeUpgrade_UnifySameDomain
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL
from OCP.TopExp import TopExp
from OCP.TopoDS import TopoDS
from OCP.TopTools import TopTools_IndexedMapOfShape, TopTools_MapOfShape

from .. import kernel
from .check import _free_edges
from .model import (ERROR, FACE_GROUP, INFO, WARNING, Body, Report, combined,
                    from_history, logged)


@logged
def heal(study, tolerance: float = 0.0, sew: bool = True, fix: bool = True,
         unify: bool = True) -> Report:
    """Вылечить все тела. Тело, которое вылечить не удалось, остаётся как было.

    ``tolerance`` — зазор, который ещё можно закрыть сшивкой, мм. По
    умолчанию десятитысячная доля диагонали: это заведомо шум экспорта, а
    не конструктивный зазор.
    """
    report = Report("heal", params={"tolerance": tolerance, "sew": sew,
                                    "fix": fix, "unify": unify})
    tolerance = tolerance or max(study.diagonal() * 1e-4, 1e-6)
    report.used["tolerance"] = tolerance
    report.before = study.summary()
    keep = _group_edges(study)

    stages = []
    if sew:
        stages.append(("сшивка", _sew))
    if fix:
        stages.append(("починка", _fix))
    if unify:
        stages.append(("слияние граней", _unify))

    for title, stage in stages:
        bodies, images = [], []
        for body in study.bodies:
            try:
                shape, image, note = stage(body, tolerance, keep)
            except Exception as failure:  # noqa: BLE001 — ядро отказывает по-разному
                report.note("HEAL_STAGE_FAILED",
                            f"«{body.name}»: {title} не удалась ({failure}); "
                            f"тело оставлено как было", WARNING,
                            bodies=[body.name])
                bodies.append(body)
                continue
            if note:
                report.note(note[0], f"«{body.name}»: {note[1]}", note[2],
                            bodies=[body.name])
            if shape is None:
                bodies.append(body)
                continue
            if not _acceptable(body.shape, shape):
                report.note("HEAL_REJECTED",
                            f"«{body.name}»: {title} дала негодную форму — "
                            f"отброшена", WARNING, bodies=[body.name])
                bodies.append(body)
                continue
            bodies.append(Body(body.name, shape))
            images.append(image)
        study.replace(bodies, combined(*images), report)
    report.after = study.summary()
    before, after = report.before, report.after
    report.message = (f"граней {before['faces']} → {after['faces']}, "
                      f"тел-объёмов {before['solids']} → {after['solids']}")
    return report


def _acceptable(before, after) -> bool:
    """Лечение не должно делать хуже: негодную форму из годной не берём."""
    if after is None or after.IsNull():
        return False
    if BRepCheck_Analyzer(before, True).IsValid() and \
            not BRepCheck_Analyzer(after, True).IsValid():
        return False
    return True


def _group_edges(study):
    """Рёбра на ГРАНИЦАХ групп: слияние граней их не стирает.

    Граница — ребро, по разные стороны которого грани с разным составом
    групп. Ребро между двумя гранями одной и той же группы границей не
    является, и две половинки «верха» должны слиться в один верх.
    """
    from OCP.TopTools import TopTools_IndexedDataMapOfShapeListOfShape

    keep = TopTools_MapOfShape()
    marks = {}
    for name, group in study.groups.items():
        if group.kind != FACE_GROUP:
            continue
        for index in group.faces:
            marks.setdefault(index, set()).add(name)
    if not marks:
        return keep
    mapping = TopTools_IndexedDataMapOfShapeListOfShape()
    TopExp.MapShapesAndAncestors_s(study.compound(), TopAbs_EDGE, TopAbs_FACE,
                                   mapping)
    for number in range(1, mapping.Extent() + 1):
        kinds = {frozenset(marks.get(study.face_index(face), ()))
                 for face in mapping.FindFromIndex(number)}
        if len(kinds) > 1:
            keep.Add(mapping.FindKey(number))
    return keep


def _sew(body, tolerance: float, _keep):
    """Сшивка и сборка тела — только для того, что телом не является."""
    if body.is_solid:
        return None, None, None
    sewing = BRepBuilderAPI_Sewing(tolerance)
    sewing.Add(body.shape)
    sewing.Perform()
    sewed = sewing.SewedShape()

    def image(shape):
        if sewing.IsModified(shape):
            return [sewing.Modified(shape)]
        return None

    shells = []
    found = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(sewed, TopAbs_SHELL, found)
    for index in range(1, found.Extent() + 1):
        shells.append(TopoDS.Shell_s(found.FindKey(index)))
    solids, open_shells = [], []
    for shell in shells:
        if _free_edges(shell):
            open_shells.append(shell)
            continue
        maker = BRepBuilderAPI_MakeSolid(shell)
        if not maker.IsDone():
            open_shells.append(shell)
            continue
        fixer = ShapeFix_Solid(maker.Solid())
        fixer.Perform()
        solids.append(fixer.Solid())
    if solids and not open_shells:
        shape = solids[0] if len(solids) == 1 else kernel.compound(solids)
        return shape, image, ("SEWN", f"сшито в тело ({len(solids)})", INFO)
    if solids:
        return (kernel.compound(solids + open_shells), image,
                ("PARTLY_SEWN", f"в тело собрано {len(solids)}, открытых "
                 f"оболочек осталось {len(open_shells)} — зазоры больше "
                 f"допуска сшивки", WARNING))
    free = sum(len(_free_edges(shell)) for shell in open_shells)
    return (sewed, image,
            ("STILL_OPEN", f"сшито, но оболочка открыта: {free} свободных "
             f"рёбер. Увеличьте допуск сшивки или закройте дыру в исходной "
             f"модели", ERROR))


def _fix(body, tolerance: float, _keep):
    fixer = ShapeFix_Shape(body.shape)
    fixer.SetPrecision(tolerance)
    fixer.SetMaxTolerance(tolerance * 10.0)
    fixer.Perform()
    shape = fixer.Shape()
    history = fixer.Context().History()
    return shape, from_history(history), None


def _unify(body, _tolerance: float, keep):
    unifier = ShapeUpgrade_UnifySameDomain(body.shape, True, True, False)
    unifier.SetSafeInputMode(True)
    if keep.Size():
        unifier.KeepShapes(keep)
    unifier.Build()
    shape = unifier.Shape()
    return shape, from_history(unifier.History()), None
