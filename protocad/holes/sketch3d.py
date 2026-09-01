"""Трёхмерный эскиз позиций отверстий.

По `docs/09_HOLES.md`, §26. Нужен там, где двумерный не годится: точки на
РАЗНЫХ гранях, на кривых поверхностях, с разными местными нормалями. Одна
операция, одно описание отверстия — и четыре оси, если точки лежат на
четырёх гранях (§30).

§26.2 прямо говорит: полный универсальный трёхмерный эскиз сразу строить
не нужно. Здесь специализированный объект — параметрический набор точек с
опорами. Позже интерфейс источника позиций позволит подменить его обычным
трёхмерным эскизом, не меняя `HoleFeature`.

Точка помнит ТРИ вещи (§27): опору, положение на ней и запасные
координаты. Опора — главное: она правило, а координаты снимок. Пока
опора находится, точка едет за деталью; когда пропала — точка объявляется
потерянной, и подставлять «похожую» грань запрещено (§38).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .model import new_id

#: Откуда берётся ось отверстия в этой точке (§26.2).
DIRECTIONS = ("surface_normal", "shared_direction", "explicit_vector")


@dataclass
class Point3D:
    """Одна позиция трёхмерного эскиза."""

    id: str = field(default_factory=new_id)
    #: Запасные координаты. Годятся, только пока опора не найдена.
    xyz: tuple = (0.0, 0.0, 0.0)
    #: Описание опорной грани: нормаль и удаление от нуля, как у ссылок
    #: эскиза. Пусто — точка висит на своих координатах.
    support_ref: str = ""
    #: Где на опоре стоит точка: смещение от её центра в плоскости грани.
    #: Хранится ОТ ЦЕНТРА, а не абсолютно: грань переехала — точка едет с
    #: ней, и правило остаётся тем же.
    offset: tuple = (0.0, 0.0)
    direction_mode: str = "surface_normal"
    direction: tuple | None = None
    #: Итог последнего пересчёта.
    resolved: tuple | None = None
    axis: tuple | None = None
    lost: bool = False
    message: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "xyz": list(self.xyz),
                "support_ref": self.support_ref, "offset": list(self.offset),
                "direction_mode": self.direction_mode,
                "direction": (None if self.direction is None
                              else list(self.direction))}

    @classmethod
    def from_dict(cls, data: dict) -> "Point3D":
        return cls(
            id=str(data.get("id") or new_id()),
            xyz=tuple(float(v) for v in (data.get("xyz") or (0, 0, 0))),
            support_ref=str(data.get("support_ref") or ""),
            offset=tuple(float(v) for v in (data.get("offset") or (0.0, 0.0))),
            direction_mode=str(data.get("direction_mode") or "surface_normal"),
            direction=(None if data.get("direction") is None
                       else tuple(float(v) for v in data["direction"])))


@dataclass
class HolePositionSketch3D:
    """Набор трёхмерных позиций. Дочерний объект операции отверстия."""

    name: str = "Позиции 3D"
    points: list = field(default_factory=list)
    shared_direction: tuple | None = None

    def add(self, xyz, support_ref: str = "", offset=(0.0, 0.0),
            direction_mode: str = "surface_normal") -> Point3D:
        point = Point3D(xyz=tuple(float(v) for v in xyz),
                        support_ref=support_ref,
                        offset=tuple(float(v) for v in offset),
                        direction_mode=direction_mode)
        self.points.append(point)
        return point

    @property
    def lost(self) -> list:
        return [item for item in self.points if item.lost]

    def resolve(self, faces) -> list:
        """Поставить точки на их опоры. Возвращает потерянные.

        ``faces`` — описания граней детали: у каждой ``normal``, ``center``
        и, если есть, ``area``. Опора ищется по НАПРАВЛЕНИЮ и ближайшему
        удалению — точное совпадение описания не годится, потому что в него
        входит место, а опора нужна как раз тогда, когда грань переехала.

        Похожая грань НЕ подставляется (§38). Потерянная точка помечается,
        и операция на ней останавливается: тихо просверлить не там хуже,
        чем не просверлить вовсе.
        """
        lost = []
        for point in self.points:
            point.lost, point.message = False, ""
            if not point.support_ref:
                point.resolved = point.xyz
                point.axis = point.direction or self.shared_direction
                continue
            found = _face_like(faces, point.support_ref)
            if found is None:
                point.lost = True
                point.message = "HOLE_LOST_REFERENCE"
                point.resolved, point.axis = None, None
                lost.append(point)
                continue
            centre, normal = found
            across, up = _frame(normal)
            point.resolved = tuple(
                centre[index]
                + across[index] * point.offset[0]
                + up[index] * point.offset[1] for index in range(3))
            if point.direction_mode == "explicit_vector" and point.direction:
                point.axis = point.direction
            elif point.direction_mode == "shared_direction" \
                    and self.shared_direction:
                point.axis = self.shared_direction
            else:
                # Ось смотрит В МАТЕРИАЛ, нормаль грани — наружу.
                point.axis = tuple(-value for value in normal)
        return lost

    def to_dict(self) -> dict:
        return {"name": self.name,
                "shared_direction": (None if self.shared_direction is None
                                     else list(self.shared_direction)),
                "points": [item.to_dict() for item in self.points]}

    @classmethod
    def from_dict(cls, data: dict) -> "HolePositionSketch3D":
        return cls(
            name=str(data.get("name") or "Позиции 3D"),
            shared_direction=(None if data.get("shared_direction") is None
                              else tuple(float(v)
                                         for v in data["shared_direction"])),
            points=[Point3D.from_dict(item)
                    for item in (data.get("points") or ())])


def describe(normal, centre, precision: int = 3) -> str:
    """Описание опорной грани строкой: нормаль и удаление от нуля."""
    values = [round(float(value), precision) for value in normal]
    offset = sum(float(normal[i]) * float(centre[i]) for i in range(3))
    values.append(round(offset, precision))
    return ",".join(str(value) for value in values)


def offset_on(normal, centre, spot) -> tuple:
    """Где точка стоит на грани: смещение от её центра, в осях грани."""
    across, up = _frame(normal)
    delta = tuple(float(spot[i]) - float(centre[i]) for i in range(3))
    return (sum(delta[i] * across[i] for i in range(3)),
            sum(delta[i] * up[i] for i in range(3)))


#: Насколько нормали считаются одинаковыми при поиске опоры.
SAME_NORMAL = 1e-3


def _face_like(faces, mark: str):
    """(центр, нормаль) грани по описанию. ``None`` — такой нет."""
    try:
        values = [float(piece) for piece in mark.split(",")]
    except ValueError:
        return None
    if len(values) < 4:
        return None
    wanted, offset = tuple(values[:3]), values[3]
    best, best_gap = None, None
    for face in faces:
        normal = face.get("normal")
        centre = face.get("center")
        if not normal or not centre:
            continue
        along = sum(float(normal[i]) * wanted[i] for i in range(3))
        if along < 1.0 - SAME_NORMAL:
            continue
        gap = abs(sum(float(normal[i]) * float(centre[i])
                      for i in range(3)) - offset)
        if best_gap is None or gap < best_gap:
            best = (tuple(float(v) for v in centre),
                    tuple(float(v) for v in normal))
            best_gap = gap
    return best


def _frame(normal) -> tuple:
    """Пара направлений В плоскости с такой нормалью.

    Берётся ось, наименее совпадающая с нормалью: иначе на нормали
    (0, 0, 1) выбор оси Z дал бы вырожденную пару.
    """
    axes = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    normal = _unit(normal)
    helper = min(axes, key=lambda item: abs(_dot(item, normal)))
    across = _unit(_cross(normal, helper))
    return across, _unit(_cross(normal, across))


def _dot(a, b) -> float:
    return sum(a[i] * b[i] for i in range(3))


def _cross(a, b) -> tuple:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _unit(vector) -> tuple:
    length = sum(value * value for value in vector) ** 0.5
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return tuple(float(value) / length for value in vector)
