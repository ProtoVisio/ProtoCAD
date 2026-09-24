"""Упрощение модели: убрать мелкие отверстия, скругления и мелкие грани.

Для расчёта деталь почти всегда подробнее, чем нужно: крепёжное отверстие
вдали от зоны интереса, скругление 0,5 мм на ребре корпуса, фаска под
сварку. Каждая такая мелочь стоит тысяч элементов и ничего не меняет в
ответе — или ломает построение сетки вовсе.

Находит мелочи этот модуль (правилами по виду и размеру граней), а
убирает — алгоритм ядра `BRepAlgoAPI_Defeaturing`: он удаляет грани и
продлевает соседние до смыкания. Это не заплатка поверх дыры, а та же
деталь, какой она была бы без этого элемента.

Что убрать не удалось — называется поимённо. Молча оставить отверстие,
которое просили убрать, значит отдать в расчёт не ту модель.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from OCP.BRep import BRep_Tool
from OCP.BRepAlgoAPI import BRepAlgoAPI_Defeaturing
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.BRepLib import BRepLib
from OCP.GeomAbs import GeomAbs_C0
from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE
from OCP.TopExp import TopExp
from OCP.TopoDS import TopoDS
from OCP.TopTools import (
    TopTools_IndexedDataMapOfShapeListOfShape,
    TopTools_ListOfShape,
)

from .. import kernel
from .model import ERROR, INFO, WARNING, Body, Report, combined, from_history, logged
from .select import describe

#: Насколько оси считаются одной осью: угол (радианы) и сдвиг (доля
#: диагонали детали).
AXIS_ANGLE = 1e-6
AXIS_SHIFT = 1e-6


@dataclass
class Feature:
    """Найденная мелочь: что это, в каком теле, какими гранями."""

    kind: str               # "hole" | "fillet" | "small_face" | "faces"
    body: str
    faces: list = field(default_factory=list)
    size: float = 0.0       # диаметр отверстия, радиус скругления, площадь
    where: tuple = (0.0, 0.0, 0.0)

    @property
    def title(self) -> str:
        x, y, z = (round(value, 3) for value in self.where)
        if self.kind == "hole":
            return f"отверстие Ø{self.size:.4g} у ({x}, {y}, {z})"
        if self.kind == "fillet":
            return f"скругление R{self.size:.4g} у ({x}, {y}, {z})"
        if self.kind == "small_face":
            return f"мелкая грань {self.size:.4g} мм² у ({x}, {y}, {z})"
        return f"грани {self.faces} тела «{self.body}»"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "body": self.body, "faces": list(self.faces),
                "size": self.size, "where": list(self.where),
                "title": self.title}


# --- поиск -----------------------------------------------------------------


class _Topology:
    """Соседство граней одного тела и их описания — считаются один раз."""

    def __init__(self, study, body):
        self.study = study
        self.body = body
        self.faces = study.faces_of(body.name)
        self.data = {index: describe(study.face(index)) for index in self.faces}
        self.edges = TopTools_IndexedDataMapOfShapeListOfShape()
        TopExp.MapShapesAndAncestors_s(body.shape, TopAbs_EDGE, TopAbs_FACE,
                                       self.edges)
        self.neighbours = {index: set() for index in self.faces}
        self.links = []          # (грань, грань, ребро)
        for number in range(1, self.edges.Extent() + 1):
            touching = sorted({study.face_index(face)
                               for face in self.edges.FindFromIndex(number)}
                              - {-1})
            edge = TopoDS.Edge_s(self.edges.FindKey(number))
            for first in touching:
                for second in touching:
                    if first < second:
                        self.neighbours[first].add(second)
                        self.neighbours[second].add(first)
                        self.links.append((first, second, edge))
        self.shift = max(study.diagonal() * AXIS_SHIFT, 1e-9)

    def coaxial(self, first: dict, second: dict) -> bool:
        if "axis" not in first or "axis" not in second:
            return False
        a = np.asarray(first["axis"], float)
        b = np.asarray(second["axis"], float)
        if abs(abs(float(a @ b)) - 1.0) > AXIS_ANGLE:
            return False
        offset = np.asarray(second["origin"], float) - np.asarray(first["origin"], float)
        across = offset - a * float(offset @ a)
        return float(np.linalg.norm(across)) <= self.shift

    def circles_on_axis(self, index: int, axis_data: dict, limit: float) -> bool:
        """Все рёбра плоской грани — окружности на оси отверстия не больше
        предела: это дно глухого отверстия или ступень, а не грань детали."""
        from OCP.BRepAdaptor import BRepAdaptor_Curve
        from OCP.GeomAbs import GeomAbs_Circle

        face = self.study.face(index)
        edges = []
        mapping = TopTools_IndexedDataMapOfShapeListOfShape()
        TopExp.MapShapesAndAncestors_s(face, TopAbs_EDGE, TopAbs_FACE, mapping)
        for number in range(1, mapping.Extent() + 1):
            edges.append(TopoDS.Edge_s(mapping.FindKey(number)))
        if not edges:
            return False
        axis = np.asarray(axis_data["axis"], float)
        origin = np.asarray(axis_data["origin"], float)
        for edge in edges:
            if BRep_Tool.Degenerated_s(edge):
                continue
            curve = BRepAdaptor_Curve(edge)
            if curve.GetType() != GeomAbs_Circle:
                return False
            circle = curve.Circle()
            if circle.Radius() > limit + self.shift:
                return False
            centre = circle.Location()
            offset = np.array([centre.X(), centre.Y(), centre.Z()]) - origin
            across = offset - axis * float(offset @ axis)
            if float(np.linalg.norm(across)) > self.shift:
                return False
        return True


def find_holes(study, max_diameter: float, bodies=None) -> list:
    """Отверстия диаметром не больше заданного.

    Отверстие — связная группа ВОГНУТЫХ соосных граней: цилиндров
    (экспортёры режут их на половинки по шву), конусов дна или зенковки,
    плоских кольцевых ступеней и дна. Цилиндр обязан замыкаться вокруг оси
    целиком: вогнутая четверть цилиндра — это внутреннее скругление, а не
    отверстие, и путать их нельзя.
    """
    limit = max_diameter / 2.0
    found = []
    for body in _bodies(study, bodies):
        topology = _Topology(study, body)
        taken = set()
        for start in topology.faces:
            data = topology.data[start]
            if start in taken or data["type"] != "cylinder" or not data.get("concave"):
                continue
            if data["radius"] > limit + topology.shift:
                continue
            members, queue = {start}, [start]
            while queue:
                current = queue.pop()
                for other in topology.neighbours[current]:
                    if other in members:
                        continue
                    if _hole_part(topology, other, data, limit):
                        members.add(other)
                        queue.append(other)
            cover = sum(topology.data[index].get("span", 0.0)
                        for index in members
                        if topology.data[index]["type"] == "cylinder"
                        and abs(topology.data[index]["radius"] - data["radius"])
                        <= topology.shift)
            taken.update(members)
            if cover < 359.0:
                continue
            radius = max(topology.data[index].get("radius", 0.0)
                         for index in members
                         if topology.data[index]["type"] in ("cylinder", "cone"))
            found.append(Feature("hole", body.name, sorted(members),
                                 2.0 * radius, _middle(topology, members)))
    return found


def _hole_part(topology, index: int, axis_data: dict, limit: float) -> bool:
    data = topology.data[index]
    kind = data["type"]
    if kind == "cylinder":
        return (data.get("concave", False) and data["radius"] <= limit + topology.shift
                and topology.coaxial(axis_data, data))
    if kind == "cone":
        return (data.get("concave", False) and data["radius"] <= limit + topology.shift
                and topology.coaxial(axis_data, data))
    if kind == "sphere":
        centre = np.asarray(data["origin"], float)
        axis = np.asarray(axis_data["axis"], float)
        offset = centre - np.asarray(axis_data["origin"], float)
        across = offset - axis * float(offset @ axis)
        return (data.get("concave", False) and data["radius"] <= limit + topology.shift
                and float(np.linalg.norm(across)) <= topology.shift)
    if kind == "plane":
        normal = np.asarray(data["normal"], float)
        axis = np.asarray(axis_data["axis"], float)
        if abs(abs(float(normal @ axis)) - 1.0) > 1e-6:
            return False
        return topology.circles_on_axis(index, axis_data, limit)
    return False


def find_fillets(study, max_radius: float, bodies=None) -> list:
    """Скругления радиусом не больше заданного.

    Скругление — цилиндрическая, торовая или сферическая грань малого
    радиуса, ГЛАДКО переходящая в соседей хотя бы по двум рёбрам. Гладкость
    спрашивается у ядра (`BRepLib::EncodeRegularity`), а не угадывается по
    картинке. Цилиндр, замкнутый вокруг оси, — это отверстие или вал, не
    скругление.
    """
    found = []
    for body in _bodies(study, bodies):
        BRepLib.EncodeRegularity_s(body.shape, 1e-4)
        topology = _Topology(study, body)
        smooth = {index: 0 for index in topology.faces}
        smooth_links = {index: set() for index in topology.faces}
        for first, second, edge in topology.links:
            continuity = BRep_Tool.Continuity_s(edge, study.face(first),
                                                study.face(second))
            if continuity != GeomAbs_C0:
                smooth[first] += 1
                smooth[second] += 1
                smooth_links[first].add(second)
                smooth_links[second].add(first)
        candidates = set()
        closed = _closed_cylinders(topology)
        for index in topology.faces:
            data = topology.data[index]
            if data["type"] not in ("cylinder", "torus", "sphere"):
                continue
            if data["radius"] > max_radius + topology.shift:
                continue
            if index in closed or smooth[index] < 2:
                continue
            candidates.add(index)
        taken = set()
        for start in sorted(candidates):
            if start in taken:
                continue
            chain, queue = {start}, [start]
            while queue:
                current = queue.pop()
                for other in smooth_links[current]:
                    if other in candidates and other not in chain:
                        chain.add(other)
                        queue.append(other)
            taken.update(chain)
            radius = max(topology.data[index]["radius"] for index in chain)
            found.append(Feature("fillet", body.name, sorted(chain), radius,
                                 _middle(topology, chain)))
    return found


def _closed_cylinders(topology) -> set:
    """Цилиндры, вместе с соосными соседями замыкающиеся вокруг оси."""
    closed = set()
    for index in topology.faces:
        data = topology.data[index]
        if data["type"] != "cylinder" or index in closed:
            continue
        group = {index}
        queue = [index]
        while queue:
            current = queue.pop()
            for other in topology.neighbours[current]:
                other_data = topology.data[other]
                if (other not in group and other_data["type"] == "cylinder"
                        and abs(other_data["radius"] - data["radius"]) <= topology.shift
                        and topology.coaxial(data, other_data)):
                    group.add(other)
                    queue.append(other)
        if sum(topology.data[item].get("span", 0.0) for item in group) >= 359.0:
            closed.update(group)
    return closed


def find_small_faces(study, max_area: float, bodies=None) -> list:
    found = []
    for body in _bodies(study, bodies):
        for index in study.faces_of(body.name):
            data = describe(study.face(index))
            if data["area"] <= max_area:
                found.append(Feature("small_face", body.name, [index],
                                     data["area"], data["center"]))
    return found


def _bodies(study, names):
    if not names:
        return [body for body in study.bodies if body.is_solid]
    return [study.body(name) for name in names
            if study.body(name) is not None and study.body(name).is_solid]


def _middle(topology, members) -> tuple:
    points = np.array([topology.data[index]["center"] for index in members])
    return tuple(float(value) for value in points.mean(axis=0))


# --- удаление --------------------------------------------------------------


@logged
def defeature(study, holes: float = 0.0, fillets: float = 0.0,
              small_faces: float = 0.0, faces=(), bodies=None) -> Report:
    """Убрать мелочи. Нули — этот вид не трогать.

    ``holes`` — наибольший диаметр отверстия, ``fillets`` — наибольший
    радиус скругления, ``small_faces`` — наибольшая площадь грани, мм².
    ``faces`` — номера граней, выбранные руками (удаляются как есть).
    """
    report = Report("defeature", params={"holes": holes, "fillets": fillets,
                                         "small_faces": small_faces,
                                         "faces": [int(v) for v in faces],
                                         "bodies": list(bodies or ())})
    report.before = study.summary()
    features = []
    if holes > 0.0:
        features += find_holes(study, holes, bodies)
    if fillets > 0.0:
        hole_faces = {index for item in features for index in item.faces}
        features += [item for item in find_fillets(study, fillets, bodies)
                     if not set(item.faces) & hole_faces]
    if small_faces > 0.0:
        busy = {index for item in features for index in item.faces}
        features += [item for item in find_small_faces(study, small_faces, bodies)
                     if not set(item.faces) & busy]
    if faces:
        by_body = {}
        for index in faces:
            owners = study.owners(int(index))
            if owners:
                by_body.setdefault(owners[0], []).append(int(index))
        features += [Feature("faces", name, sorted(members))
                     for name, members in by_body.items()]
    if not features:
        report.message = "убирать нечего: под заданные размеры ничего не попало"
        report.after = report.before
        return report

    removed_total = 0
    bodies_now, images = [], []
    for body in study.bodies:
        mine = [item for item in features if item.body == body.name]
        if not mine:
            bodies_now.append(body)
            continue
        shape, image, done, failed = _remove(study, body, mine)
        for item in failed:
            report.note("NOT_REMOVED", f"«{body.name}»: {item.title} не "
                        f"убирается — ядро не смогло сомкнуть соседние грани",
                        WARNING, faces=item.faces, bodies=[body.name])
        if shape is None:
            bodies_now.append(body)
            continue
        removed_total += len(done)
        bodies_now.append(Body(body.name, shape))
        images.append(image)
    study.replace(bodies_now, combined(*images), report)
    kinds = {}
    for item in features:
        kinds[item.kind] = kinds.get(item.kind, 0) + 1
    report.after = study.summary()
    report.used["found"] = kinds
    report.message = (f"убрано {removed_total} из {len(features)}; граней "
                      f"{report.before['faces']} → {report.after['faces']}")
    if removed_total == 0:
        report.ok = False
        report.note("NOTHING_REMOVED", "ни одного элемента убрать не удалось",
                    ERROR)
    for item in features:
        report.note("FOUND", item.title, INFO, faces=item.faces,
                    bodies=[item.body])
    return report


def _remove(study, body, features):
    """Убрать элементы из одного тела. (форма, история, убранные, не убранные).

    Сначала все разом — так быстрее и соседние элементы смыкаются вместе.
    Не вышло — по одному: один упрямый элемент не должен держать остальные.
    """
    shape, image = _attempt(study, body.shape, features)
    if shape is not None:
        failed = [item for item in features if _survived(study, image, item)]
        done = [item for item in features if item not in failed]
        return shape, image, done, failed
    current, steps, done, failed = body.shape, [], [], []
    for item in features:
        faces = [study.face(index) for index in item.faces]
        attempt, history = _attempt_faces(current, faces)
        if attempt is None:
            failed.append(item)
            continue
        current = attempt
        steps.append(history)
        done.append(item)
    if not done:
        return None, None, done, failed

    def image(face):
        # История по шагам: каждая следующая знает только свою форму.
        answer = [face]
        touched = False
        for step in steps:
            following = []
            for item in answer:
                result = step(item)
                if result is None:
                    following.append(item)
                else:
                    touched = True
                    following.extend(result)
            answer = following
        return answer if touched else None

    return current, image, done, failed


def _attempt(study, shape, features):
    faces = [study.face(index) for item in features for index in item.faces]
    result, history = _attempt_faces(shape, faces)
    return result, history


def _attempt_faces(shape, faces):
    algorithm = BRepAlgoAPI_Defeaturing()
    algorithm.SetShape(shape)
    removing = TopTools_ListOfShape()
    for face in faces:
        removing.Append(face)
    algorithm.AddFacesToRemove(removing)
    algorithm.SetRunParallel(True)
    algorithm.SetToFillHistory(True)
    try:
        algorithm.Build()
    except Exception:  # noqa: BLE001 — ядро бросает на безнадёжных случаях
        return None, None
    if not algorithm.IsDone():
        return None, None
    result = algorithm.Shape()
    if result is None or result.IsNull() or kernel.is_empty(result):
        return None, None
    if not BRepCheck_Analyzer(result, True).IsValid():
        return None, None
    return result, from_history(algorithm.History())


def _survived(study, image, feature) -> bool:
    """Остался ли элемент: хоть одна его грань не удалена.

    Удалённой грани история отвечает пустым списком. Нетронутой — ``None``,
    изменённой — её новыми частями; и то и другое значит, что грань на месте.
    """
    for index in feature.faces:
        answer = image(study.face(index)) if image is not None else None
        if answer is None or answer:
            return True
    return False
