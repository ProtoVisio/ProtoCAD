"""Движок на OCP/OCCT — исследовательский прототип и эталон сверки.

По `docs/08_ENGINE_BACKEND.md`, §15 и §16.1 это НЕ основной путь для
стандартных операций детали. Он остаётся, чтобы у приёмочных проверок был
второй ответ: пока движок на FreeCAD не даёт те же числа на тех же
входах, переключение не считается состоявшимся.

Расширять его новыми возможностями нельзя. Новое — только в движке на
FreeCAD.
"""

from __future__ import annotations

from .. import kernel
from .. import operations
from .protocol import (
    Capabilities,
    Diagnostic,
    EndCondition,
    EntityRef,
    FeatureResult,
    Mesh,
    PadRequest,
    Status,
    error,
)

#: Как концевые условия протокола называются в собственном слое операций.
_ENDS = {
    EndCondition.BLIND: operations.BLIND,
    EndCondition.THROUGH_ALL: operations.THROUGH,
    EndCondition.UP_TO_FIRST: operations.TO_FIRST,
    EndCondition.UP_TO_LAST: operations.TO_LAST,
    EndCondition.UP_TO_FACE: operations.TO_FACE,
    EndCondition.MID_PLANE: operations.MIDPLANE,
}


class LocalBackend:
    """Прототип. Работает с формами напрямую, без документа и без IPC."""

    name = "local"

    def __init__(self):
        #: Документы прототипа — просто словарь форм. Ни истории, ни
        #: сохранения: то и другое есть у целевого движка, и повторять их
        #: здесь значило бы делать вторую документную модель.
        self.shapes: dict = {}
        self.profiles: dict = {}

    def capabilities(self) -> Capabilities:
        return Capabilities(
            backend="protocad-local",
            version="прототип",
            features=["partdesign.pad", "partdesign.pocket"],
            end_conditions={
                "partdesign.pad": [value.value for value in _ENDS],
                "partdesign.pocket": [value.value for value in _ENDS],
            },
            notes="исследовательский прототип на OCP; не основной путь",
        )

    def available(self) -> tuple:
        try:
            kernel.box(1.0, 1.0, 1.0)
        except Exception as failure:  # noqa: BLE001
            return False, f"OCP недоступен: {failure}"
        return True, ""

    # --- подготовка входа ---------------------------------------------

    def put_body(self, body_id: str, shape) -> None:
        """Положить готовое тело. У прототипа нет документа, поэтому вход
        задаётся прямо — этим он и отличается от целевого движка."""
        self.shapes[body_id] = shape

    # --- операции ------------------------------------------------------

    def pad(self, request: PadRequest) -> FeatureResult:
        if request.thin:
            # Отказ, а не выдавливание сплошным: сплошное вместо стенки —
            # это молча построить не то, о чём просили.
            return self._frozen("тонкостенное выдавливание")
        if request.profile.empty:
            return error("NO_PROFILE", "профиль пуст: областей не передано",
                         ["profile"], self.name)
        try:
            profile = _face_of(request.profile.regions)
        except Exception as failure:  # noqa: BLE001
            return error("BAD_PROFILE", f"профиль не собрался: {failure}",
                         ["profile"], self.name)

        shape = self.shapes.get(request.body_id)
        if request.subtract and shape is None:
            return error("NO_BASE", "нечего резать: тело ещё не построено",
                         ["body_id"], self.name)
        mode = (operations.CUT if request.subtract
                else (operations.CREATE if shape is None else operations.ADD))
        wanted = operations.Extrusion(
            length=request.length,
            end=_ENDS.get(request.end_condition, operations.BLIND),
            reverse=request.reversed,
        )
        title = request.body_id or "Операция"
        try:
            # Разворот и проверка «ничего не произошло» сюда НЕ входят: они
            # одинаковы для всех движков и живут в ``policy.Policy``. Иначе
            # прототип умел бы то, чего не умеет целевой движок, и сравнение
            # их ответов ничего бы не значило.
            tool = operations.build_tool(profile, wanted, shape)
            result, note = operations.apply(shape, tool, mode, title,
                                            require_change=False)
        except operations.OperationError as failure:
            return error(_code_of(str(failure)), str(failure),
                         ["length", "end_condition"], self.name)

        # Предпросмотр не меняет тело: он только показывает (§12.3).
        if not request.preview:
            self.shapes[request.body_id] = result
        answer = FeatureResult(
            status=Status.VALID,
            feature_id="" if request.preview else request.body_id,
            revision=request.revision,
            session_id=request.session_id,
            volume=kernel.volume(result),
            bounds=list(_bounds(result)),
            mesh=_mesh(result),
            entities=_entities(result, request.body_id),
        )
        if note:
            answer.diagnostics.append(
                Diagnostic("MULTI_SOLID", note, "warning", [], self.name))
        return answer

    def dress_up(self, request) -> FeatureResult:
        """Скругление и фаска на OCP. Отвечает тем же, что и целевой движок.

        Номера рёбер — по тому же обходу, что и в описании подэлементов:
        расхождение здесь означало бы, что выбор мышью указывает у двух
        движков на разные рёбра.
        """
        if request.faces or request.mode not in ("", "equal"):
            # Выбор гранью и типы фаски — новое, а прототип заморожен
            # (§15.1). Сделать вид, что тип учтён, значило бы построить
            # другую фаску и отчитаться успехом.
            return self._frozen("выбор рёбер гранью и типы фаски")
        shape = self.shapes.get(request.body_id)
        if shape is None:
            return error("NO_BASE", "нечего обрабатывать: тела ещё нет",
                         ["body_id"], self.name)
        edges = list(kernel.iter_edges(shape))
        beyond = [value for value in request.edges
                  if not 0 <= int(value) < len(edges)]
        if beyond:
            return error(
                "ELEMENT_LOST",
                f"рёбер с номерами {beyond} в детали нет: их всего "
                f"{len(edges)}. Похоже, форма изменилась выше по дереву",
                ["edges"], self.name)
        chosen = [edges[int(value)] for value in request.edges]
        try:
            build = kernel.fillet if request.kind == "fillet" else kernel.chamfer
            result = build(shape, float(request.size), chosen)
        except Exception as failure:  # noqa: BLE001 — ядро отказывает по-разному
            return error(
                "DRESSUP_FAILED",
                f"{failure}. Скругление или фаска такого размера на этих "
                f"рёбрах не строится — уменьшите размер либо выберите другие",
                ["size", "edges"], self.name)

        if not request.preview:
            self.shapes[request.body_id] = result
        return FeatureResult(
            status=Status.VALID,
            feature_id="" if request.preview else request.body_id,
            revision=request.revision, session_id=request.session_id,
            volume=kernel.volume(result), bounds=list(_bounds(result)),
            mesh=_mesh(result), entities=_entities(result, request.body_id))

    def revolve(self, request) -> FeatureResult:
        return self._frozen("вращение")

    def hole(self, request) -> FeatureResult:
        return self._frozen("отверстие")

    def draft(self, request) -> FeatureResult:
        return self._frozen("уклон")

    def shell(self, request) -> FeatureResult:
        return self._frozen("оболочку")

    def pattern(self, request) -> FeatureResult:
        return self._frozen("массив")

    def section(self, request):
        """След детали на плоскости — тоже новое, и тоже мимо прототипа.

        Отказ, а не пустой список рёбер: пустой означает «плоскость детали
        не задела», и подмена одного другим выглядела бы как деталь,
        которую плоскость почему-то не пересекает.
        """
        from .protocol import SectionResult

        refusal = self._frozen("сечение")
        return SectionResult(status=refusal.status,
                             diagnostics=refusal.diagnostics)

    def _frozen(self, what: str) -> FeatureResult:
        """Отказ вместо второй реализации.

        Прототип заморожен (§15.1): новое — только в движке на FreeCAD.
        Повторить здесь вращение или массив значило бы завести второй
        источник истины на операцию ради того, чтобы было с чем сверять, —
        а сверять было бы не с чем: обе стороны писал бы один и тот же я.
        """
        return error(
            "NOT_SUPPORTED",
            f"прототип не умеет {what}: он заморожен как эталон сверки "
            f"выдавливания и обработки рёбер, новое — только в движке FreeCAD",
            [], self.name)

    def save(self, document_id: str, path) -> FeatureResult:
        """Прототип не сохраняет: документной модели у него нет.

        Отказ вместо заглушки намеренный. Заглушка, отвечающая «сохранено»,
        отличалась бы от настоящего сохранения только тем, что файла нет, —
        и приёмочный набор проходил бы, ничего не проверив.
        """
        return error("NOT_SUPPORTED",
                     "прототип не умеет сохранять: документной модели у него нет",
                     ["path"], self.name)

    def open(self, document_id: str, path) -> FeatureResult:
        return error("NOT_SUPPORTED",
                     "прототип не умеет открывать документы", ["path"], self.name)

    def export(self, document_id: str, path, body_id: str = "") -> FeatureResult:
        """Выгрузить тела в STEP (с именами) или BREP.

        Выгрузка — не операция детали, и заморозка прототипа (§15.1) её не
        касается: новой геометрии здесь не строится, отдаётся построенная.
        Без неё путь «деталь → подготовка к расчёту» нельзя было бы
        проверить там, где FreeCAD не установлен.
        """
        from pathlib import Path

        from ..prep.io import write_brep, write_step
        from ..prep.model import Study

        names = [body_id] if body_id else list(self.shapes)
        present = [(name, self.shapes[name]) for name in names if name in self.shapes]
        if not present:
            return error("EMPTY_RESULT", "тело пустое", [], self.name)
        study = Study(name=document_id)
        for name, shape in present:
            study.add_body(shape, name)
        if Path(path).suffix.lower() in (".step", ".stp"):
            write_step(study, path)
        else:
            write_brep(study, path)
        return FeatureResult(status=Status.VALID,
                             feature_id=";".join(name for name, _ in present))

    def scene(self, document_id: str, body_id: str = "") -> FeatureResult:
        shape = self.shapes.get(body_id)
        if shape is None:
            return error("EMPTY_RESULT", "тело пустое", [], self.name)
        return FeatureResult(
            status=Status.VALID, feature_id=body_id,
            volume=kernel.volume(shape), bounds=list(_bounds(shape)),
            mesh=_mesh(shape), entities=_entities(shape, body_id))

    def drop_feature(self, document_id: str, body_id: str,
                     feature_id: str) -> None:
        """У прототипа операций нет — есть только текущая форма тела.

        Убирать нечего, и делать вид, что убрал, тоже нельзя: он честно
        ничего не делает, а его собственный откат — это `put_body`.
        """

    def clear(self, document_id: str) -> None:
        """У прототипа документов нет, и разделить тела по ним нельзя.

        Он забывает всё — и это честнее, чем сделать вид, что забыл только
        нужное: тела у него лежат по имени тела, без документа.
        """
        self.reset()

    def reset(self) -> None:
        self.shapes.clear()
        self.profiles.clear()


def _face_of(regions):
    """Общий вид профиля → грань ядра.

    Внутренняя петля должна идти В ОБРАТНУЮ сторону от наружной — иначе
    ядро складывает её площадь с наружной вместо того, чтобы вычесть.
    Разворачивать вслепую нельзя: из разбора эскиза петли выходят в разные
    стороны, и «развернуть каждую» помогает одной и портит другую. Сторона
    сверяется по знаку площади.
    """
    from OCP.BRepBuilderAPI import (
        BRepBuilderAPI_MakeEdge,
        BRepBuilderAPI_MakeFace,
        BRepBuilderAPI_MakeWire,
    )
    from OCP.GC import GC_MakeArcOfCircle
    from OCP.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt
    from OCP.TopoDS import TopoDS

    def edge(curve):
        if curve.get("kind") in ("ellipse", "spline"):
            # Прототип заморожен (§15.1). Построить эллипс дугами значило бы
            # выдать приближение за точную кривую.
            raise ValueError(f"прототип не строит {curve.get('kind')}")
        kind = curve.get("kind")
        if kind == "line":
            return BRepBuilderAPI_MakeEdge(gp_Pnt(*curve["a"]),
                                           gp_Pnt(*curve["b"])).Edge()
        if kind == "arc":
            return BRepBuilderAPI_MakeEdge(
                GC_MakeArcOfCircle(gp_Pnt(*curve["a"]), gp_Pnt(*curve["m"]),
                                   gp_Pnt(*curve["b"])).Value()).Edge()
        if kind == "circle":
            circle = gp_Circ(gp_Ax2(gp_Pnt(*curve["c"]), gp_Dir(*curve["n"])),
                             float(curve["r"]))
            return BRepBuilderAPI_MakeEdge(circle).Edge()
        raise ValueError(f"неизвестная кривая: {kind!r}")

    def wire(curves):
        builder = BRepBuilderAPI_MakeWire()
        for curve in curves:
            builder.Add(edge(curve))
        if not builder.IsDone():
            raise ValueError("контур не замкнулся")
        return builder.Wire()

    faces = []
    for region in regions:
        outer = region.get("outer") or ()
        builder = BRepBuilderAPI_MakeFace(wire(outer))
        outward = _winding(outer)
        for loop in region.get("inner") or ():
            hole = wire(loop)
            if _winding(loop) * outward > 0.0:
                hole = TopoDS.Wire_s(hole.Reversed())
            builder.Add(hole)
        if not builder.IsDone():
            raise ValueError("грань не построилась")
        faces.append(builder.Face())
    if len(faces) == 1:
        return faces[0]
    joined = faces[0]
    for face in faces[1:]:
        joined = kernel.fuse(joined, face)
    return joined


def _winding(curves) -> float:
    """Знак обхода петли: положительный — против часовой в её плоскости.

    Считается по концам кривых — этого хватает, чтобы отличить одну
    сторону от другой, а точная площадь здесь не нужна.
    """
    points = []
    for curve in curves:
        kind = curve.get("kind")
        if kind == "circle":
            centre, radius = curve["c"], float(curve["r"])
            points.extend([(centre[0] + radius, centre[1]),
                           (centre[0], centre[1] + radius),
                           (centre[0] - radius, centre[1]),
                           (centre[0], centre[1] - radius)])
            continue
        points.append((curve["a"][0], curve["a"][1]))
        if kind == "arc":
            points.append((curve["m"][0], curve["m"][1]))
    if len(points) < 3:
        return 1.0
    total = 0.0
    for first, second in zip(points, points[1:] + points[:1]):
        total += first[0] * second[1] - second[0] * first[1]
    return total


# --- перевод формы в данные ------------------------------------------


def _code_of(message: str) -> str:
    """Код отказа по его тексту. Нужен, чтобы интерфейс мог отличать
    случаи, не разбирая русский текст."""
    if "ничего не добавлено" in message:
        return "NO_MATERIAL_ADDED"
    if "ничего не снято" in message:
        return "NO_MATERIAL_REMOVED"
    if "ничего не осталось" in message:
        return "EVERYTHING_REMOVED"
    if "не является телом" in message:
        return "INVALID_SOLID"
    if "нет профиля" in message:
        return "NO_PROFILE"
    return "OPERATION_FAILED"


def _bounds(shape) -> tuple:
    box = kernel.bounds(shape)
    return (box.xmin, box.ymin, box.zmin, box.xmax, box.ymax, box.zmax)


def _mesh(shape) -> Mesh:
    positions, normals, faces = kernel.tessellate_faces(shape, 0.1)
    edges, edge_ids = kernel.edge_segments(shape, 0.05, with_index=True)
    return Mesh(
        positions=[float(value) for value in positions.reshape(-1)],
        normals=[float(value) for value in normals.reshape(-1)],
        face_ids=[int(value) for value in faces],
        edge_positions=[float(value) for value in edges.reshape(-1)],
        edge_ids=[int(value) for value in edge_ids],
    )


def _entities(shape, feature: str, body: str = "") -> list:
    """Грани, рёбра и вершины с описанием — тот же состав, что у движка.

    Отвечать иначе, чем целевой движок, прототипу нельзя: весь смысл его
    существования в том, что приёмочный набор идёт по обоим и числа
    сходятся. Ответ другой формы означал бы, что набор проверяет у одного
    одно, у другого другое.

    Устойчивых имён у прототипа НЕТ: карту элементов ведёт FreeCAD, а
    заводить свою здесь значило бы делать второе прослеживание топологии
    ради движка, который заморожен (§15.1). Поле имени остаётся пустым, и
    ссылки на его подэлементы держатся на номерах — с той оговоркой, что
    он и не рабочий путь.
    """
    found = []
    for index, face in enumerate(kernel.iter_faces(shape)):
        try:
            centre = kernel.face_center(face)
        except Exception:  # noqa: BLE001
            continue
        signature = ",".join(f"{value:.3f}" for value in centre)
        found.append(EntityRef(
            id=f"{feature}.face@{signature}", kind="face", feature=feature,
            origin="по месту, не по происхождению", index=index,
            data=_face_data(face, centre)))
    for index, edge in enumerate(kernel.iter_edges(shape)):
        found.append(EntityRef(
            id=f"{feature}.edge{index}", kind="edge", feature=feature,
            origin="по месту, не по происхождению", index=index,
            data=_edge_data(edge)))
    for index, point in enumerate(kernel.vertices(shape)):
        found.append(EntityRef(
            id=f"{feature}.vertex{index}", kind="vertex", feature=feature,
            origin="по месту, не по происхождению", index=index,
            data={"point": [float(value) for value in point]}))
    return found


def _face_data(face, centre) -> dict:
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Cylinder, GeomAbs_Plane

    surface = BRepAdaptor_Surface(face)
    kind = surface.GetType()
    data = {"area": float(kernel.area(face)),
            "center": [float(value) for value in centre]}
    if kind == GeomAbs_Plane:
        normal = kernel.face_normal(face)
        along = sum(normal[i] * centre[i] for i in range(3))
        data.update({
            "surface": "plane",
            "origin": [normal[i] * along for i in range(3)],
            "normal": [float(value) for value in normal],
            "x_direction": _x_direction_of(normal),
        })
    elif kind == GeomAbs_Cylinder:
        cylinder = surface.Cylinder()
        axis = cylinder.Axis().Direction()
        location = cylinder.Location()
        data.update({"surface": "cylinder",
                     "axis": [axis.X(), axis.Y(), axis.Z()],
                     "radius": float(cylinder.Radius()),
                     "axis_origin": [location.X(), location.Y(), location.Z()]})
    else:
        data["surface"] = "other"
    return data


def _x_direction_of(normal) -> list:
    """Та же ось X, что выбирает целевой движок: самая далёкая от нормали
    мировая ось, спрямлённая в плоскость. Совпадение обязательно — иначе
    один и тот же эскиз ложится на грань повёрнутым по-разному."""
    import numpy as np

    normal = np.asarray(normal, float)
    axes = np.eye(3)
    best = axes[int(np.argmin(np.abs(axes @ normal)))]
    along = best - normal * float(best @ normal)
    length = float(np.linalg.norm(along))
    if length < 1e-9:
        along, length = np.array([1.0, 0.0, 0.0]), 1.0
    return [float(value) for value in along / length]


def _edge_data(edge) -> dict:
    from OCP.BRepAdaptor import BRepAdaptor_Curve
    from OCP.GeomAbs import GeomAbs_Circle, GeomAbs_Line

    points = kernel.discretize(edge, 0.05)
    curve = BRepAdaptor_Curve(edge)
    kind = curve.GetType()
    # Середина — центр масс, а не полусумма концов: у дуги это разные
    # точки, а подпись ребра считается именно по центру масс.
    signature = kernel.edge_signature(edge)
    data = {
        "curve": {GeomAbs_Line: "line", GeomAbs_Circle: "circle"}.get(kind, "other"),
        "length": float(signature[3]),
        "middle": [float(value) for value in signature[:3]],
        "start": [float(value) for value in points[0]],
        "end": [float(value) for value in points[-1]],
    }
    if kind == GeomAbs_Circle:
        circle = curve.Circle()
        axis = circle.Axis().Direction()
        location = circle.Location()
        data.update({"center": [location.X(), location.Y(), location.Z()],
                     "normal": [axis.X(), axis.Y(), axis.Z()],
                     "radius": float(circle.Radius())})
    if kind != GeomAbs_Line:
        # Разбиение — для переноса ребра в эскиз. Прямому оно не нужно, а
        # у остальных без него окну пришлось бы восстанавливать кривую по
        # её виду и параметрам, то есть заводить второе ядро.
        data["points"] = [[float(value) for value in point] for point in points]
    return data
