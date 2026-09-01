"""Отверстие как ПАРАМЕТРИЧЕСКАЯ ОСЕВАЯ КОНСТРУКЦИЯ.

По `docs/09_HOLES.md`, §2.1. Отверстие здесь — не цилиндр с диаметром, а
стек элементов вдоль оси: со стороны входа, основной канал, со стороны
выхода. Разница не в словах. Пока отверстие «это диаметр», цековка с
зенковкой с двух сторон выражается только набором отдельных операций, и
чертёж потом восстанавливает их обратно из формы — то есть гадает.

Две стороны НЕЗАВИСИМЫ (§5.6) и обе хранятся в порядке «от внешней
поверхности внутрь материала» (§4.2). Человеку не приходится мысленно
переворачивать список для выхода; переворачивает построение.

Стороны называются по ОСИ, а не по модели (§3): near — откуда входит
инструмент, far — куда выходит. На кривой поверхности у каждой позиции
своя ось, и «верх детали» тут ничего не значит.

Резьба — отдельная семантика поверх цилиндрического участка (§14), а не
диаметр. По умолчанию она не меняет форму вовсе: хранится описание,
чертёж получает обозначение. Нельзя брать номинальный диаметр резьбы как
диаметр выреза (§19) — под М6 сверлят 5, а не 6.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field

#: Виды элементов осевого стека (§4.1). Первая очередь — эти пять.
ELEMENTS = ("straight", "counterbore", "countersink", "taper", "thread")

#: Условия окончания основного канала (§9.1).
END_CONDITIONS = ("blind", "through_all", "up_to_next", "up_to_face",
                  "up_to_body", "offset_from_face")

#: От чего считается глубина глухого отверстия (§9.2). Это не мелочь: при
#: коническом дне одно и то же число означает две разные детали.
DEPTH_REFERENCES = ("shoulder", "tip")

#: Дно отверстия (§10).
BOTTOM_TYPES = ("flat", "drill_tip", "custom_cone")

#: Способы задать зенковку (§12). Любые два числа дают третье.
COUNTERSINK_MODES = ("diameter_angle", "depth_angle", "diameter_depth")

#: Представление резьбы (§23).
REPRESENTATIONS = ("cosmetic", "modeled")


def new_id() -> str:
    """Устойчивый идентификатор. Порядок в списке им НЕ является (§29)."""
    return str(uuid.uuid4())


class HoleError(Exception):
    """Отказ модели отверстия. Несёт код диагностики (§49)."""

    def __init__(self, code: str, message: str, position: str = ""):
        super().__init__(message)
        self.code = code
        self.message = message
        self.position = position


@dataclass
class Diagnostic:
    """Одно замечание проверки. ``position`` — если беда местная (§49)."""

    code: str
    message: str
    severity: str = "error"
    position: str = ""

    @property
    def blocking(self) -> bool:
        return self.severity == "error"

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message,
                "severity": self.severity, "position": self.position}


# --- элементы осевого стека ------------------------------------------------


@dataclass
class StraightBore:
    """Цилиндрический участок. Основной канал по умолчанию."""

    diameter_mm: float = 5.0
    depth_mm: float = 0.0          # 0 — «сколько потребуется»
    id: str = field(default_factory=new_id)
    type: str = "straight"

    def to_dict(self) -> dict:
        return {"id": self.id, "type": "straight",
                "diameter_mm": self.diameter_mm, "depth_mm": self.depth_mm}


@dataclass
class Counterbore:
    """Цилиндрическая цековка (§11)."""

    diameter_mm: float = 9.0
    depth_mm: float = 4.0
    bottom_type: str = "sharp"     # sharp | drill_transition
    id: str = field(default_factory=new_id)
    type: str = "counterbore"

    def to_dict(self) -> dict:
        return {"id": self.id, "type": "counterbore",
                "diameter_mm": self.diameter_mm, "depth_mm": self.depth_mm,
                "bottom_type": self.bottom_type}


@dataclass
class Countersink:
    """Коническая зенковка (§12).

    Три равноправных способа задания, и любые два числа дают третье.
    Пересчёт делается ЯВНО, методом `resolve`, а не втихую при чтении
    поля: человек видит, какое значение вычислено, а какое задал он.

    ``included_angle_deg`` — ПОЛНЫЙ угол конуса, не половина.
    """

    definition_mode: str = "diameter_angle"
    diameter_mm: float | None = 10.0
    depth_mm: float | None = None
    included_angle_deg: float | None = 90.0
    id: str = field(default_factory=new_id)
    type: str = "countersink"

    def resolve(self, core_diameter_mm: float) -> "Countersink":
        """Досчитать третье число по двум заданным.

        Считается от ОСНОВНОГО канала: конус зенковки идёт от её диаметра
        до диаметра отверстия, а не до нуля. Иначе глубина выходит больше
        настоящей — на конусе, которого в металле нет.
        """
        half = None
        if self.definition_mode == "diameter_angle":
            if self.diameter_mm is None or self.included_angle_deg is None:
                raise HoleError("HOLE_COUNTERSINK_TOO_SMALL",
                                "зенковке нужны диаметр и угол")
            half = math.radians(self.included_angle_deg) / 2.0
            self.depth_mm = ((self.diameter_mm - core_diameter_mm) / 2.0
                             / math.tan(half))
        elif self.definition_mode == "depth_angle":
            if self.depth_mm is None or self.included_angle_deg is None:
                raise HoleError("HOLE_COUNTERSINK_TOO_SMALL",
                                "зенковке нужны глубина и угол")
            half = math.radians(self.included_angle_deg) / 2.0
            self.diameter_mm = (core_diameter_mm
                                + 2.0 * self.depth_mm * math.tan(half))
        elif self.definition_mode == "diameter_depth":
            if self.diameter_mm is None or self.depth_mm is None:
                raise HoleError("HOLE_COUNTERSINK_TOO_SMALL",
                                "зенковке нужны диаметр и глубина")
            if self.depth_mm <= 0.0:
                raise HoleError("HOLE_COUNTERSINK_TOO_SMALL",
                                "глубина зенковки должна быть больше нуля")
            half = math.atan((self.diameter_mm - core_diameter_mm) / 2.0
                             / self.depth_mm)
            self.included_angle_deg = math.degrees(2.0 * half)
        else:
            raise HoleError("HOLE_COUNTERSINK_TOO_SMALL",
                            f"неизвестный способ задания: "
                            f"{self.definition_mode!r}")
        return self

    def to_dict(self) -> dict:
        return {"id": self.id, "type": "countersink",
                "definition_mode": self.definition_mode,
                "diameter_mm": self.diameter_mm, "depth_mm": self.depth_mm,
                "included_angle_deg": self.included_angle_deg}


@dataclass
class TaperBore:
    """Коническое отверстие (§5.5): два диаметра либо диаметр и угол."""

    diameter_mm: float = 5.0            # у входа
    end_diameter_mm: float | None = None
    included_angle_deg: float | None = None
    depth_mm: float = 10.0
    id: str = field(default_factory=new_id)
    type: str = "taper"

    def resolve(self) -> "TaperBore":
        if self.end_diameter_mm is None and self.included_angle_deg is None:
            raise HoleError("HOLE_STACK_OVERLAP",
                            "конусу нужен второй диаметр либо угол")
        half = None
        if self.end_diameter_mm is None:
            half = math.radians(self.included_angle_deg) / 2.0
            self.end_diameter_mm = (self.diameter_mm
                                    - 2.0 * self.depth_mm * math.tan(half))
        elif self.included_angle_deg is None:
            half = math.atan((self.diameter_mm - self.end_diameter_mm) / 2.0
                             / max(self.depth_mm, 1e-9))
            self.included_angle_deg = math.degrees(2.0 * half)
        return self

    def to_dict(self) -> dict:
        return {"id": self.id, "type": "taper",
                "diameter_mm": self.diameter_mm,
                "end_diameter_mm": self.end_diameter_mm,
                "included_angle_deg": self.included_angle_deg,
                "depth_mm": self.depth_mm}


#: Как элемент восстанавливается из записи. Ключ — поле `type`.
_ELEMENT_TYPES = {"straight": StraightBore, "counterbore": Counterbore,
                  "countersink": Countersink, "taper": TaperBore}


def element_from_dict(data: dict):
    kind = str(data.get("type") or "")
    maker = _ELEMENT_TYPES.get(kind)
    if maker is None:
        raise HoleError("HOLE_STACK_OVERLAP",
                        f"неизвестный элемент стека: {kind!r}")
    known = {key: value for key, value in data.items() if key != "type"}
    return maker(**known)


# --- резьба ----------------------------------------------------------------


@dataclass
class ThreadDefinition:
    """Резьба на цилиндрическом участке (§14).

    Хранит ССЫЛКУ НА КАТАЛОГ и выбранные значения, а не готовую строку
    обозначения: строку собирает форматировщик каталога (§21), и держать
    её здесь значило бы иметь два источника истины.

    ``overrides`` — то, что человек задал вручную поверх каталога (§18).
    Они хранятся ОТДЕЛЬНО от каталожных значений: иначе не отличить «так в
    стандарте» от «так решил человек», и при смене стандарта чужое
    значение молча переехало бы в новый (§H-023).
    """

    catalog_id: str = ""
    size_id: str = ""
    pitch_mm: float | None = None
    tolerance_class: str = ""
    handedness: str = "right"           # right | left
    starts: int = 1                     # §22: поле есть сразу
    span: str = "core"                  # core | идентификатор элемента
    side: str = "near"
    depth_mode: str = "specified"       # specified | full | up_to
    depth_mm: float | None = None
    representation: str = "cosmetic"    # §23: modeled только по просьбе
    overrides: dict = field(default_factory=dict)
    id: str = field(default_factory=new_id)

    def to_dict(self) -> dict:
        return {"id": self.id, "span": self.span, "side": self.side,
                "catalog_id": self.catalog_id, "size_id": self.size_id,
                "pitch_mm": self.pitch_mm,
                "tolerance_class": self.tolerance_class,
                "handedness": self.handedness, "starts": self.starts,
                "depth": {"mode": self.depth_mode, "value_mm": self.depth_mm},
                "representation": self.representation,
                "overrides": dict(self.overrides)}

    @classmethod
    def from_dict(cls, data: dict) -> "ThreadDefinition":
        depth = data.get("depth") or {}
        return cls(
            id=str(data.get("id") or new_id()),
            span=str(data.get("span") or "core"),
            side=str(data.get("side") or "near"),
            catalog_id=str(data.get("catalog_id") or ""),
            size_id=str(data.get("size_id") or ""),
            pitch_mm=(None if data.get("pitch_mm") is None
                      else float(data["pitch_mm"])),
            tolerance_class=str(data.get("tolerance_class") or ""),
            handedness=str(data.get("handedness") or "right"),
            starts=int(data.get("starts", 1) or 1),
            depth_mode=str(depth.get("mode") or "specified"),
            depth_mm=(None if depth.get("value_mm") is None
                      else float(depth["value_mm"])),
            representation=str(data.get("representation") or "cosmetic"),
            overrides=dict(data.get("overrides") or {}),
        )


# --- условие окончания -----------------------------------------------------


@dataclass
class EndCondition:
    """Докуда идёт основной канал (§9.1) и от чего мерится глубина (§9.2)."""

    type: str = "blind"
    depth_mm: float | None = 10.0
    depth_reference: str = "shoulder"
    bottom_type: str = "flat"
    tip_angle_deg: float = 118.0        # §10: умолчание интерфейса, не норма
    target_ref: str = ""
    offset_mm: float = 0.0

    def cylinder_depth(self, core_diameter_mm: float) -> float:
        """Длина ЦИЛИНДРИЧЕСКОЙ части глухого отверстия.

        Разница между `shoulder` и `tip` — не оформление. При сверловочном
        дне одно и то же число глубины даёт две разные детали: у «до
        цилиндрического дна» цилиндр ровно такой, как заказан, и конус
        уходит глубже; у «до вершины» в заданное укладывается всё вместе,
        и цилиндр выходит короче.
        """
        if self.depth_mm is None:
            raise HoleError("HOLE_NO_POSITION", "глубина не задана")
        if self.bottom_type == "flat" or self.depth_reference == "shoulder":
            return float(self.depth_mm)
        return float(self.depth_mm) - self.tip_length(core_diameter_mm)

    def tip_length(self, core_diameter_mm: float) -> float:
        """Высота конуса дна. Ноль — дно плоское."""
        if self.bottom_type == "flat":
            return 0.0
        half = math.radians(self.tip_angle_deg) / 2.0
        return (core_diameter_mm / 2.0) / math.tan(half)

    def total_depth(self, core_diameter_mm: float) -> float:
        """Полная глубина — от поверхности до самой дальней точки."""
        return (self.cylinder_depth(core_diameter_mm)
                + self.tip_length(core_diameter_mm))

    def to_dict(self) -> dict:
        return {"type": self.type, "value_mm": self.depth_mm,
                "depth_reference": self.depth_reference,
                "bottom_type": self.bottom_type,
                "tip_angle_deg": self.tip_angle_deg,
                "target_ref": self.target_ref, "offset_mm": self.offset_mm}

    @classmethod
    def from_dict(cls, data: dict) -> "EndCondition":
        return cls(
            type=str(data.get("type") or "blind"),
            depth_mm=(None if data.get("value_mm") is None
                      else float(data["value_mm"])),
            depth_reference=str(data.get("depth_reference") or "shoulder"),
            bottom_type=str(data.get("bottom_type") or "flat"),
            tip_angle_deg=float(data.get("tip_angle_deg", 118.0)),
            target_ref=str(data.get("target_ref") or ""),
            offset_mm=float(data.get("offset_mm", 0.0)),
        )


# --- описание отверстия ----------------------------------------------------


@dataclass
class HoleDefinition:
    """Одно отверстие как конструкция. БЕЗ позиций (§41).

    Пресет хранит именно это: описание применимо в любой детали, а опоры,
    позиции и область действия к нему не относятся.
    """

    core: object = field(default_factory=StraightBore)
    near_stack: list = field(default_factory=list)
    far_stack: list = field(default_factory=list)
    end_condition: EndCondition = field(default_factory=EndCondition)
    threads: list = field(default_factory=list)
    preset_id: str = ""

    @property
    def core_diameter_mm(self) -> float:
        return float(getattr(self.core, "diameter_mm", 0.0))

    def resolved(self) -> "HoleDefinition":
        """Досчитать зависимые числа стека. Возвращает себя же."""
        for element in list(self.near_stack) + list(self.far_stack):
            if isinstance(element, Countersink):
                element.resolve(self.core_diameter_mm)
        if isinstance(self.core, TaperBore):
            self.core.resolve()
        return self

    def near_reach_mm(self) -> float:
        """Насколько стек входа съедает материал от поверхности внутрь."""
        return sum(_reach_of(item) for item in self.near_stack)

    def far_reach_mm(self) -> float:
        return sum(_reach_of(item) for item in self.far_stack)

    def to_dict(self) -> dict:
        return {
            "preset_id": self.preset_id,
            "core": self.core.to_dict(),
            "near_stack": [item.to_dict() for item in self.near_stack],
            "far_stack": [item.to_dict() for item in self.far_stack],
            "end_condition": self.end_condition.to_dict(),
            "threads": [item.to_dict() for item in self.threads],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "HoleDefinition":
        return cls(
            preset_id=str(data.get("preset_id") or ""),
            core=element_from_dict(data.get("core") or {"type": "straight"}),
            near_stack=[element_from_dict(item)
                        for item in (data.get("near_stack") or ())],
            far_stack=[element_from_dict(item)
                       for item in (data.get("far_stack") or ())],
            end_condition=EndCondition.from_dict(
                data.get("end_condition") or {}),
            threads=[ThreadDefinition.from_dict(item)
                     for item in (data.get("threads") or ())],
        )


def _reach_of(element) -> float:
    """Насколько элемент уходит вглубь от своей внешней поверхности."""
    depth = getattr(element, "depth_mm", None)
    return float(depth or 0.0)


# --- позиции ---------------------------------------------------------------


@dataclass
class HolePosition:
    """Одна позиция отверстия. У неё СВОЙ устойчивый номер (§29)."""

    id: str = field(default_factory=new_id)
    point_ref: str = ""
    support_ref: str = ""
    xyz: tuple = (0.0, 0.0, 0.0)
    direction: tuple | None = None
    state: str = "valid"                # valid | warning | invalid
    message: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "point_ref": self.point_ref,
                "support_ref": self.support_ref, "xyz": list(self.xyz),
                "direction": (None if self.direction is None
                              else list(self.direction))}

    @classmethod
    def from_dict(cls, data: dict) -> "HolePosition":
        return cls(
            id=str(data.get("id") or new_id()),
            point_ref=str(data.get("point_ref") or ""),
            support_ref=str(data.get("support_ref") or ""),
            xyz=tuple(float(v) for v in (data.get("xyz") or (0.0, 0.0, 0.0))),
            direction=(None if data.get("direction") is None
                       else tuple(float(v) for v in data["direction"])),
        )


@dataclass
class HolePlacementSet:
    """Откуда берутся позиции и куда смотрит ось (§24)."""

    source_type: str = "position_sketch_2d"
    source_id: str = ""
    direction_mode: str = "surface_normal"
    shared_direction_ref: str = ""
    positions: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"source_type": self.source_type, "source_id": self.source_id,
                "direction_mode": self.direction_mode,
                "shared_direction_ref": self.shared_direction_ref,
                "positions": [item.to_dict() for item in self.positions]}

    @classmethod
    def from_dict(cls, data: dict) -> "HolePlacementSet":
        return cls(
            source_type=str(data.get("source_type") or "position_sketch_2d"),
            source_id=str(data.get("source_id") or ""),
            direction_mode=str(data.get("direction_mode") or "surface_normal"),
            shared_direction_ref=str(data.get("shared_direction_ref") or ""),
            positions=[HolePosition.from_dict(item)
                       for item in (data.get("positions") or ())],
        )


@dataclass
class FeatureScope:
    """Что резать (§34). «Авто» не значит «молча все»."""

    mode: str = "auto"                  # auto | selected_bodies | all_intersected
    body_ids: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"mode": self.mode, "body_ids": list(self.body_ids)}

    @classmethod
    def from_dict(cls, data: dict) -> "FeatureScope":
        return cls(mode=str(data.get("mode") or "auto"),
                   body_ids=list(data.get("body_ids") or ()))


@dataclass
class HoleFeature:
    """Операция «Отверстие»: ОДНО описание и N позиций (§2.1).

    Одна операция, а не N независимых: правка диаметра обязана менять все
    отверстия сразу, и в дереве это одна строка.
    """

    id: str = field(default_factory=new_id)
    name: str = "Отверстие"
    definition: HoleDefinition = field(default_factory=HoleDefinition)
    placement: HolePlacementSet = field(default_factory=HolePlacementSet)
    scope: FeatureScope = field(default_factory=FeatureScope)

    @property
    def count(self) -> int:
        return len(self.placement.positions)

    def to_dict(self) -> dict:
        return {"schema_version": 1, "type": "HoleFeature", "id": self.id,
                "name": self.name,
                "definition": self.definition.to_dict(),
                "placement": self.placement.to_dict(),
                "scope": self.scope.to_dict()}

    @classmethod
    def from_dict(cls, data: dict) -> "HoleFeature":
        return cls(
            id=str(data.get("id") or new_id()),
            name=str(data.get("name") or "Отверстие"),
            definition=HoleDefinition.from_dict(data.get("definition") or {}),
            placement=HolePlacementSet.from_dict(data.get("placement") or {}),
            scope=FeatureScope.from_dict(data.get("scope") or {}),
        )
