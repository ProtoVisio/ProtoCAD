"""Исследование: тела, группы и журнал шагов подготовки.

Здесь нет ни одной операции над геометрией — только то, над чем они
работают и что после себя оставляют.

**Грани называются номерами в карте исследования**, а не объектами. Два
Python-объекта одной и той же грани OCCT не равны друг другу (`==`
сравнивает обёртки), и множества граней на обычных наборах Python молча
не находят то, что в них лежит. Карта `TopTools_IndexedMapOfShape` сравнивает
по `IsSame` — так, как это понимает ядро. Номер действителен до следующей
смены геометрии; при смене группы пересчитываются по истории операции
(`Study.replace`), а не угадываются по положению.

Группы двух видов: грани (граничные условия — «закрепление», «давление»,
«вход») и тела (материалы). Тела называются именами: имя переживает любую
операцию, пока тело существует, и именно им тело помечено в исходном файле.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from OCP.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL, TopAbs_SOLID
from OCP.TopExp import TopExp, TopExp_Explorer
from OCP.TopTools import TopTools_IndexedMapOfShape

from .. import kernel

FACE_GROUP = "faces"
BODY_GROUP = "bodies"

ERROR = "error"
WARNING = "warning"
INFO = "info"


@dataclass
class Body:
    """Тело исследования. ``shape`` — обычно SOLID, но после импорта бывает
    и оболочкой: такое тело не сеткуется объёмом, и проверка об этом скажет."""

    name: str
    shape: object

    @property
    def is_solid(self) -> bool:
        return kernel.count(self.shape, TopAbs_SOLID) > 0

    @property
    def volume(self) -> float:
        return kernel.volume(self.shape) if self.is_solid else 0.0


@dataclass
class Group:
    """Именованный набор граней или тел.

    ``rule`` — чем группа задана: правило отбора (`select.py`) либо точки
    на выбранных гранях. По нему группа находится заново, когда рецепт
    прогоняют на новой версии геометрии; номера граней для этого не годятся.
    """

    name: str
    kind: str = FACE_GROUP
    faces: set = field(default_factory=set)
    bodies: set = field(default_factory=set)
    rule: dict = field(default_factory=dict)

    @property
    def size(self) -> int:
        return len(self.faces) if self.kind == FACE_GROUP else len(self.bodies)

    @property
    def empty(self) -> bool:
        return self.size == 0

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind,
                "faces": sorted(self.faces), "bodies": sorted(self.bodies),
                "rule": dict(self.rule)}


@dataclass
class Finding:
    """Замечание: код для программы, текст для человека, где это — номерами."""

    code: str
    message: str
    severity: str = WARNING
    faces: list = field(default_factory=list)
    edges: list = field(default_factory=list)
    bodies: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message,
                "severity": self.severity, "faces": list(self.faces),
                "edges": list(self.edges), "bodies": list(self.bodies)}


@dataclass
class Report:
    """Итог шага. Операции возвращают его, а не бросают исключения.

    Отказ шага геометрию НЕ меняет: исследование остаётся таким, каким было
    до него. Полуизменённая модель хуже отказа — её не отличить от готовой.
    """

    op: str
    ok: bool = True
    message: str = ""
    #: Параметры, КАК ИХ ЗАДАЛИ: ноль значит «подобрать самому». По ним шаг
    #: повторяется в рецепте, и на другой геометрии подбор даст своё.
    params: dict = field(default_factory=dict)
    #: То, что шаг подобрал сам: допуск, размер элемента.
    used: dict = field(default_factory=dict)
    findings: list = field(default_factory=list)
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)
    #: Крупные данные для показа (треугольники сетки). В журнал и в JSON не
    #: идут: это картинка, а не итог.
    preview: object = field(default=None, repr=False, compare=False)

    def note(self, code: str, message: str, severity: str = WARNING,
             faces=(), edges=(), bodies=()) -> Finding:
        finding = Finding(code, message, severity, list(faces), list(edges),
                          list(bodies))
        self.findings.append(finding)
        return finding

    def fail(self, code: str, message: str, **where) -> "Report":
        self.ok = False
        self.message = message
        self.note(code, message, ERROR, **where)
        return self

    @property
    def errors(self) -> list:
        return [item for item in self.findings if item.severity == ERROR]

    @property
    def warnings(self) -> list:
        return [item for item in self.findings if item.severity == WARNING]

    def to_dict(self) -> dict:
        return {"op": self.op, "ok": self.ok, "message": self.message,
                "params": dict(self.params), "used": dict(self.used),
                "findings": [item.to_dict() for item in self.findings],
                "before": dict(self.before), "after": dict(self.after)}

    def text(self) -> str:
        """Итог одной строкой-абзацем — для консоли и строки состояния."""
        head = f"{self.op}: {'готово' if self.ok else 'ОТКАЗ'}"
        if self.message:
            head += f" — {self.message}"
        lines = [head]
        marks = {ERROR: "!!", WARNING: " !", INFO: "  "}
        for item in self.findings:
            lines.append(f"  {marks.get(item.severity, '  ')} {item.message}")
        return "\n".join(lines)


class Study:
    """Геометрия под расчёт: тела, группы и журнал того, что с ней делали."""

    def __init__(self, bodies=(), name: str = "Исследование",
                 source: str = "", units: str = "mm"):
        self.name = name
        self.source = source
        #: Единицы геометрии. Ядро и импорт STEP работают в миллиметрах;
        #: пересчёт в единицы решателя делается при записи сетки.
        self.units = units
        self.bodies: list = []
        self.groups: dict = {}
        self.log: list = []
        #: Склеены ли тела общей топологией. Любая операция по отдельным
        #: телам склейку разрушает — об этом говорят, а не молчат.
        self.glued = False
        self._maps = None
        for body in bodies:
            self.add_body(body.shape, body.name)

    # --- тела -----------------------------------------------------------

    def add_body(self, shape, name: str = "") -> Body:
        body = Body(self._unique(name or f"Тело {len(self.bodies) + 1}"), shape)
        self.bodies.append(body)
        self._maps = None
        return body

    def _unique(self, name: str, taken=None) -> str:
        taken = set(taken if taken is not None
                    else (body.name for body in self.bodies))
        if name not in taken:
            return name
        number = 2
        while f"{name} [{number}]" in taken:
            number += 1
        return f"{name} [{number}]"

    def body(self, name: str):
        for body in self.bodies:
            if body.name == name:
                return body
        return None

    def compound(self):
        return kernel.compound([body.shape for body in self.bodies])

    # --- карта граней ---------------------------------------------------

    def _build_maps(self):
        """Карты граней и рёбер всего исследования и принадлежность граней.

        Одна карта на всё, а не по телу: у склеенных тел общая грань — одна и
        та же, и в карте она должна встречаться один раз, иначе группа на
        стыке получила бы два номера для одной поверхности.
        """
        whole = self.compound()
        faces = TopTools_IndexedMapOfShape()
        edges = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(whole, TopAbs_FACE, faces)
        TopExp.MapShapes_s(whole, TopAbs_EDGE, edges)
        owners = [[] for _ in range(faces.Extent())]
        for body in self.bodies:
            local = TopTools_IndexedMapOfShape()
            TopExp.MapShapes_s(body.shape, TopAbs_FACE, local)
            for index in range(1, local.Extent() + 1):
                place = faces.FindIndex(local.FindKey(index))
                if place > 0 and body.name not in owners[place - 1]:
                    owners[place - 1].append(body.name)
        self._maps = (faces, edges, owners)
        return self._maps

    def _maps_now(self):
        return self._maps if self._maps is not None else self._build_maps()

    def face_map(self):
        return self._maps_now()[0]

    def edge_map(self):
        return self._maps_now()[1]

    @property
    def face_count(self) -> int:
        return self.face_map().Extent()

    @property
    def edge_count(self) -> int:
        return self.edge_map().Extent()

    def face(self, index: int):
        """Грань по номеру (с нуля)."""
        from OCP.TopoDS import TopoDS

        return TopoDS.Face_s(self.face_map().FindKey(int(index) + 1))

    def edge(self, index: int):
        from OCP.TopoDS import TopoDS

        return TopoDS.Edge_s(self.edge_map().FindKey(int(index) + 1))

    def face_index(self, face) -> int:
        """Номер грани (с нуля); −1 — такой грани в исследовании нет."""
        return self.face_map().FindIndex(face) - 1

    def edge_index(self, edge) -> int:
        return self.edge_map().FindIndex(edge) - 1

    def owners(self, index: int) -> list:
        """Тела, которым принадлежит грань. Два — грань на стыке склеенных."""
        return list(self._maps_now()[2][int(index)])

    def faces_of(self, name: str) -> list:
        """Номера граней тела."""
        body = self.body(name)
        if body is None:
            return []
        local = TopTools_IndexedMapOfShape()
        TopExp.MapShapes_s(body.shape, TopAbs_FACE, local)
        found = []
        whole = self.face_map()
        for index in range(1, local.Extent() + 1):
            place = whole.FindIndex(local.FindKey(index))
            if place > 0:
                found.append(place - 1)
        return found

    # --- замеры ---------------------------------------------------------

    def bounds(self):
        return kernel.bounds(self.compound())

    def diagonal(self) -> float:
        box = self.bounds()
        return math.sqrt(sum(value * value for value in box.size))

    def summary(self) -> dict:
        solids = sum(kernel.count(body.shape, TopAbs_SOLID) for body in self.bodies)
        shells = 0
        for body in self.bodies:
            if not body.is_solid:
                shells += kernel.count(body.shape, TopAbs_SHELL)
        box = self.bounds()
        return {
            "bodies": len(self.bodies),
            "solids": solids,
            "open_shells": shells,
            "faces": self.face_count,
            "edges": self.edge_count,
            "volume": round(sum(body.volume for body in self.bodies), 6),
            "bounds": [round(value, 6) for value in
                       (box.xmin, box.ymin, box.zmin, box.xmax, box.ymax, box.zmax)],
            "groups": {name: group.size for name, group in self.groups.items()},
            "glued": self.glued,
        }

    # --- группы ---------------------------------------------------------

    def add_group(self, name: str, kind: str = FACE_GROUP, faces=(),
                  bodies=(), rule=None) -> Group:
        """Завести группу. Одноимённая заменяется: имя — это адрес группы в
        файле решателя, и двух групп с одним адресом быть не может."""
        if not name:
            raise ValueError("у группы должно быть имя")
        group = Group(name=name, kind=kind,
                      faces={int(value) for value in faces},
                      bodies=set(bodies), rule=dict(rule or {}))
        self.groups[name] = group
        return group

    def remove_group(self, name: str) -> bool:
        return self.groups.pop(name, None) is not None

    def groups_of_face(self, index: int) -> list:
        return [name for name, group in self.groups.items()
                if group.kind == FACE_GROUP and int(index) in group.faces]

    # --- смена геометрии -------------------------------------------------

    def replace(self, bodies, image=None, report: Report | None = None) -> None:
        """Поставить новые тела и перенести группы по истории операции.

        ``bodies`` — список `Body` (или пар имя/форма). ``image(грань)``
        отвечает, во что грань превратилась: список новых граней, пустой
        список — грань удалена, ``None`` — операция её не трогала. Без
        ``image`` грань ищется в новой геометрии как есть.

        Потерянные грани не прощаются молча: группа, у которой пропала
        часть, — это граничное условие, приложенное не туда, куда хотели.
        """
        old = self.face_map()
        kept_groups = {}
        for name, group in self.groups.items():
            if group.kind == FACE_GROUP:
                kept_groups[name] = [(index, old.FindKey(index + 1))
                                     for index in sorted(group.faces)
                                     if 0 <= index < old.Extent()]
        self.bodies = []
        for body in bodies:
            if isinstance(body, Body):
                self.bodies.append(body)
            else:
                name, shape = body
                self.bodies.append(Body(name, shape))
        self._maps = None
        fresh = self.face_map()
        alive = {body.name for body in self.bodies}

        for name, members in kept_groups.items():
            group = self.groups[name]
            found, lost = set(), []
            for index, face in members:
                images = image(face) if image is not None else None
                if images is None:
                    images = [face]
                hit = False
                for item in images:
                    place = fresh.FindIndex(item)
                    if place > 0:
                        found.add(place - 1)
                        hit = True
                if not hit:
                    lost.append(index)
            group.faces = found
            if report is None:
                continue
            if lost and found:
                report.note("GROUP_SHRANK",
                            f"группа «{name}»: {len(lost)} из {len(members)} "
                            f"граней исчезли вместе с геометрией",
                            faces=sorted(found))
            elif lost:
                report.note("GROUP_LOST",
                            f"группа «{name}» опустела: все её грани удалены "
                            f"операцией", ERROR if members else WARNING)
        for name, group in self.groups.items():
            if group.kind != BODY_GROUP:
                continue
            gone = sorted(group.bodies - alive)
            if gone:
                group.bodies -= set(gone)
                if report is not None:
                    report.note("GROUP_BODIES_LOST",
                                f"группа «{name}»: тел {gone} больше нет",
                                bodies=gone)

    # --- снимки для отмены ----------------------------------------------

    def snapshot(self) -> dict:
        """Состояние целиком. Формы ядра неизменяемы, поэтому снимок дешёв:
        копируются списки и группы, а не геометрия."""
        return {
            "bodies": [Body(body.name, body.shape) for body in self.bodies],
            "groups": {name: Group(group.name, group.kind, set(group.faces),
                                   set(group.bodies), dict(group.rule))
                       for name, group in self.groups.items()},
            "log": list(self.log),
            "glued": self.glued,
        }

    def restore(self, state: dict) -> None:
        self.bodies = [Body(body.name, body.shape) for body in state["bodies"]]
        self.groups = {name: Group(group.name, group.kind, set(group.faces),
                                   set(group.bodies), dict(group.rule))
                       for name, group in state["groups"].items()}
        self.log = list(state["log"])
        self.glued = state["glued"]
        self._maps = None


def logged(function):
    """Записать итог шага в журнал исследования.

    Журнал — это и история для показа, и черновик рецепта: по нему ту же
    подготовку повторяют на новой версии геометрии. Поэтому пишет каждый
    шаг сам, а не тот, кто его вызвал: вызвать могут из окна, из рецепта,
    из консоли — и забыть записать.
    """
    import functools

    @functools.wraps(function)
    def wrapper(study, *args, **kwargs):
        report = function(study, *args, **kwargs)
        study.log.append(report)
        return report

    return wrapper


# --- история операций ---------------------------------------------------


def from_history(history):
    """Ответ «во что превратилась грань» по истории OCCT (`BRepTools_History`
    или любой алгоритм с `Modified`/`IsDeleted`).

    ``None`` — история о грани ничего не знает, то есть операция её не
    трогала. Это НЕ то же, что удаление: удалённой грани отвечают пустым
    списком.
    """
    removed = getattr(history, "IsRemoved", None) or getattr(history, "IsDeleted")

    def image(shape):
        if removed(shape):
            return []
        modified = history.Modified(shape)
        if modified.Size() > 0:
            return list(modified)
        return None

    return image


def combined(*images):
    """Несколько историй разом — по телам, обработанным порознь.

    Грань принадлежит одному телу, и знает о ней одна история; первая, что
    ответила не ``None``, и права.
    """
    parts = [item for item in images if item is not None]

    def image(shape):
        for item in parts:
            answer = item(shape)
            if answer is not None:
                return answer
        return None

    return image


def solids_of(shape) -> list:
    """Тела внутри формы — каждое по одному разу."""
    from OCP.TopoDS import TopoDS

    found = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_SOLID, found)
    return [TopoDS.Solid_s(found.FindKey(index))
            for index in range(1, found.Extent() + 1)]


def faces_of_shape(shape) -> list:
    from OCP.TopoDS import TopoDS

    found = TopTools_IndexedMapOfShape()
    TopExp.MapShapes_s(shape, TopAbs_FACE, found)
    return [TopoDS.Face_s(found.FindKey(index))
            for index in range(1, found.Extent() + 1)]


def loose_parts(shape) -> list:
    """То, что в форме НЕ входит ни в одно тело: оболочки и одинокие грани.

    Импорт поверхностной модели (IGES, «кожа» из STEP) приходит именно так.
    Выбросить их нельзя — из них тело и сшивают; выдать за тело тоже
    нельзя — объёма у них нет.
    """
    from OCP.TopoDS import TopoDS

    inside = TopTools_IndexedMapOfShape()
    for solid in solids_of(shape):
        TopExp.MapShapes_s(solid, TopAbs_FACE, inside)
    shells, faces = [], []
    seen = TopTools_IndexedMapOfShape()
    explorer = TopExp_Explorer(shape, TopAbs_SHELL)
    while explorer.More():
        shell = explorer.Current()
        explorer.Next()
        if seen.FindIndex(shell) > 0:
            continue
        seen.Add(shell)
        members = faces_of_shape(shell)
        if members and inside.FindIndex(members[0]) == 0:
            shells.append(TopoDS.Shell_s(shell))
            for face in members:
                inside.Add(face)
    for face in faces_of_shape(shape):
        if inside.FindIndex(face) == 0:
            faces.append(face)
    return shells + faces
