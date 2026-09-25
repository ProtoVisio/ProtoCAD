"""Сборка: вхождения деталей и СОПРЯЖЕНИЯ между ними.

Сборка, собранная из готовых положений, — это не сборка, а картинка.
Подвинули деталь — соседние остались где были, и узнаётся это по чертежу.
Поэтому положение вхождения здесь не задаётся, а ВЫЧИСЛЯЕТСЯ из
сопряжений; заданным остаётся только закреплённое.

Что уже было в проекте: `protocad.model.Assembly` — состав с готовыми
преобразованиями и спецификацией. Он остаётся: спецификация и дерево
изделия к сопряжениям отношения не имеют. Здесь добавляется то, чего не
было, — правила взаимного положения.

Ссылка на грань или ось детали хранится ОПИСАНИЕМ, а не номером: номер
меняется при первой же правке выше по дереву (§26 движка). Описание —
нормаль и удаление от нуля, тот же вид, что у ссылок эскиза и у опор
трёхмерного эскиза отверстий.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

#: Виды сопряжений.
MATES = ("fixed", "coincident", "concentric", "distance",
         "angle", "parallel", "perpendicular", "tangent")

#: Названия для человека.
TITLES = {"fixed": "Закрепление", "coincident": "Совпадение",
          "concentric": "Соосность", "distance": "Расстояние",
          "angle": "Угол", "parallel": "Параллельность",
          "perpendicular": "Перпендикулярность", "tangent": "Касание"}


def new_id() -> str:
    return str(uuid.uuid4())


class AssemblyError(Exception):
    def __init__(self, code: str, message: str, mate: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.mate = mate


@dataclass
class Diagnostic:
    code: str
    message: str
    severity: str = "error"
    mate: str = ""

    @property
    def blocking(self) -> bool:
        return self.severity == "error"

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message,
                "severity": self.severity, "mate": self.mate}


@dataclass
class Reference:
    """На что ссылается сопряжение: грань конкретного вхождения.

    ``mark`` — описание грани в СОБСТВЕННЫХ координатах детали: нормаль и
    удаление от нуля. В координаты сборки его переводит положение
    вхождения, и потому одна и та же деталь, поставленная дважды, даёт две
    разные грани по одной ссылке.
    """

    instance: str = ""
    mark: str = ""

    def to_dict(self) -> dict:
        return {"instance": self.instance, "mark": self.mark}

    @classmethod
    def from_dict(cls, data: dict) -> "Reference":
        return cls(instance=str(data.get("instance") or ""),
                   mark=str(data.get("mark") or ""))


@dataclass
class Mate:
    """Одно правило взаимного положения."""

    kind: str = "coincident"
    first: Reference = field(default_factory=Reference)
    second: Reference = field(default_factory=Reference)
    #: Для «Расстояния» — сколько; для остальных не используется.
    value_mm: float = 0.0
    #: Для «Угла» — сколько градусов между гранями (осями).
    angle_deg: float = 0.0
    #: Развернуть: у совпадения граней есть две стороны, и выбирает их
    #: человек. Без этого поля деталь садилась бы то так, то наоборот в
    #: зависимости от того, куда смотрит нормаль, — а это не его дело.
    flip: bool = False
    id: str = field(default_factory=new_id)
    #: Итог последнего решения.
    ok: bool = True
    message: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind,
                "first": self.first.to_dict(), "second": self.second.to_dict(),
                "value_mm": self.value_mm, "angle_deg": self.angle_deg,
                "flip": self.flip}

    @classmethod
    def from_dict(cls, data: dict) -> "Mate":
        return cls(id=str(data.get("id") or new_id()),
                   kind=str(data.get("kind") or "coincident"),
                   first=Reference.from_dict(data.get("first") or {}),
                   second=Reference.from_dict(data.get("second") or {}),
                   value_mm=float(data.get("value_mm", 0.0)),
                   angle_deg=float(data.get("angle_deg", 0.0)),
                   flip=bool(data.get("flip")))


@dataclass
class Instance:
    """Одно вхождение детали в сборку.

    ``placement`` — ИТОГ решения, а не задание. Задаёт положение только
    закрепление: у всех прочих оно выводится из сопряжений, и записанное
    вручную было бы затёрто первым же решением.
    """

    name: str = "Деталь"
    source: str = ""              # путь либо идентификатор детали
    id: str = field(default_factory=new_id)
    #: (сдвиг, поворот) — поворот матрицей 3×3 по строкам.
    placement: tuple = ((0.0, 0.0, 0.0),
                        ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))
    fixed: bool = False
    #: Описания граней детали в её собственных координатах. Заполняет тот,
    #: кто загрузил деталь; сборка их только использует.
    faces: list = field(default_factory=list)

    def to_dict(self) -> dict:
        shift, basis = self.placement
        return {"id": self.id, "name": self.name, "source": self.source,
                "fixed": self.fixed,
                "placement": {"shift": list(shift),
                              "basis": [list(row) for row in basis]}}

    @classmethod
    def from_dict(cls, data: dict) -> "Instance":
        place = data.get("placement") or {}
        shift = tuple(float(v) for v in (place.get("shift") or (0, 0, 0)))
        rows = place.get("basis") or ((1, 0, 0), (0, 1, 0), (0, 0, 1))
        basis = tuple(tuple(float(v) for v in row) for row in rows)
        return cls(id=str(data.get("id") or new_id()),
                   name=str(data.get("name") or "Деталь"),
                   source=str(data.get("source") or ""),
                   fixed=bool(data.get("fixed")),
                   placement=(shift, basis))


@dataclass
class Assembly:
    """Сборка: вхождения и сопряжения."""

    name: str = "Сборка"
    instances: list = field(default_factory=list)
    mates: list = field(default_factory=list)

    def add(self, instance: Instance) -> Instance:
        self.instances.append(instance)
        return instance

    def mate(self, mate: Mate) -> Mate:
        self.mates.append(mate)
        return mate

    def by_id(self, instance_id: str):
        for item in self.instances:
            if item.id == instance_id:
                return item
        return None

    def to_dict(self) -> dict:
        return {"schema_version": 1, "type": "Assembly", "name": self.name,
                "instances": [item.to_dict() for item in self.instances],
                "mates": [item.to_dict() for item in self.mates]}

    @classmethod
    def from_dict(cls, data: dict) -> "Assembly":
        return cls(name=str(data.get("name") or "Сборка"),
                   instances=[Instance.from_dict(item)
                              for item in (data.get("instances") or ())],
                   mates=[Mate.from_dict(item)
                          for item in (data.get("mates") or ())])
