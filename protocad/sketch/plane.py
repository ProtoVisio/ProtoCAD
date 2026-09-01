"""Плоскость эскиза.

До сих пор эскиз молча лежал в XY. Это работало ровно до второй операции:
деталь сложнее пластины требует эскизов в разных плоскостях, и без них
дерево построения упирается в потолок.

Плоскость задаётся тройкой: начало, нормаль и направление, считающееся
«вправо». Третье задаётся ЯВНО и не выводится из нормали. Вывести его можно
— OCCT так и делает, — но результат зависит от внутреннего выбора
библиотеки, и эскиз, нарисованный сегодня, завтра оказался бы повёрнутым на
90°. Та же ошибка уже стоила времени в чертежах (`kernel.project`), второй
раз её повторять незачем.

Внутри эскиза координаты плоские: u вдоль ``x_direction``, v вдоль
``y_direction``. Наружу они выходят трёхмерными, и перевод — единственное,
что этот модуль делает.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

Vector = tuple[float, float, float]


def _normalize(vector: Vector) -> Vector:
    length = math.sqrt(sum(component * component for component in vector))
    if length < 1e-12:
        raise ValueError("нулевой вектор не задаёт направление")
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def _cross(a: Vector, b: Vector) -> Vector:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _dot(a: Vector, b: Vector) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


@dataclass(frozen=True)
class Plane:
    """Плоскость эскиза: начало, нормаль, направление «вправо»."""

    origin: Vector = (0.0, 0.0, 0.0)
    normal: Vector = (0.0, 0.0, 1.0)
    x_direction: Vector = (1.0, 0.0, 0.0)
    name: str = "XY"

    def __post_init__(self):
        normal = _normalize(self.normal)
        x_direction = _normalize(self.x_direction)
        # Направление «вправо» обязано лежать В плоскости. Если задали
        # наклонно — отнимаем составляющую вдоль нормали, а не отказываем:
        # грань тела редко даёт идеально ортогональную пару.
        along = _dot(x_direction, normal)
        if abs(along) > 1.0 - 1e-9:
            raise ValueError("направление «вправо» совпало с нормалью")
        if abs(along) > 1e-12:
            x_direction = _normalize(
                tuple(x_direction[i] - along * normal[i] for i in range(3))
            )
        object.__setattr__(self, "normal", normal)
        object.__setattr__(self, "x_direction", x_direction)

    @property
    def y_direction(self) -> Vector:
        return _cross(self.normal, self.x_direction)

    def point_at(self, u: float, v: float) -> Vector:
        """Плоская точка эскиза → точка в пространстве детали."""
        y = self.y_direction
        return tuple(
            self.origin[i] + u * self.x_direction[i] + v * y[i] for i in range(3)
        )

    def project(self, point: Vector) -> tuple[float, float]:
        """Точка в пространстве → плоские координаты. Составляющая вдоль
        нормали отбрасывается: точка считается лежащей в плоскости."""
        relative = tuple(point[i] - self.origin[i] for i in range(3))
        return _dot(relative, self.x_direction), _dot(relative, self.y_direction)

    def offset(self, distance: float, name: str = "") -> "Plane":
        """Параллельная плоскость на заданном расстоянии по нормали."""
        moved = tuple(self.origin[i] + distance * self.normal[i] for i in range(3))
        return Plane(
            moved, self.normal, self.x_direction,
            name or f"{self.name}{distance:+g}",
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "origin": list(self.origin),
            "normal": list(self.normal),
            "x_direction": list(self.x_direction),
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "Plane":
        if not data:
            return STANDARD["XY"]
        return cls(
            tuple(data.get("origin", (0.0, 0.0, 0.0))),
            tuple(data.get("normal", (0.0, 0.0, 1.0))),
            tuple(data.get("x_direction", (1.0, 0.0, 0.0))),
            data.get("name", "XY"),
        )

    # --- выдача в ядро ---

    def to_ax2(self):
        from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

        return gp_Ax2(gp_Pnt(*self.origin), gp_Dir(*self.normal),
                      gp_Dir(*self.x_direction))

    def placement(self):
        """Преобразование «плоские координаты → пространство детали».

        Матрица собирается вручную, столбцами из направлений плоскости.
        Готовые ``SetTransformation`` и ``SetDisplacement`` делают то же, но
        их направление (из системы или в систему) различается между версиями
        документации, а ошибка здесь даёт эскиз, зеркально отражённый или
        повёрнутый, — и обнаруживается на готовой детали.
        """
        from OCP.gp import gp_Trsf

        x, y, z = self.x_direction, self.y_direction, self.normal
        trsf = gp_Trsf()
        trsf.SetValues(
            x[0], y[0], z[0], self.origin[0],
            x[1], y[1], z[1], self.origin[1],
            x[2], y[2], z[2], self.origin[2],
        )
        return trsf

    def place(self, shape):
        """Перенести форму, построенную в плоских координатах, на место."""
        from OCP.BRepBuilderAPI import BRepBuilderAPI_Transform

        if self.is_default:
            return shape  # лишнее преобразование только копит погрешность
        return BRepBuilderAPI_Transform(shape, self.placement(), True).Shape()

    @property
    def is_default(self) -> bool:
        return (
            self.origin == (0.0, 0.0, 0.0)
            and self.normal == (0.0, 0.0, 1.0)
            and self.x_direction == (1.0, 0.0, 0.0)
        )


# Три основные плоскости. Направления «вправо» выбраны так, чтобы ось v
# смотрела вверх у вертикальных плоскостей: эскиз на XZ и YZ рисуется в тех
# же координатах, что и вид спереди и вид слева на чертеже.
STANDARD = {
    "XY": Plane((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0), "XY"),
    "XZ": Plane((0.0, 0.0, 0.0), (0.0, -1.0, 0.0), (1.0, 0.0, 0.0), "XZ"),
    "YZ": Plane((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), "YZ"),
}

TITLES = {
    "XY": "XY — вид сверху",
    "XZ": "XZ — вид спереди",
    "YZ": "YZ — вид слева",
}


def from_face(face) -> Plane:
    """Плоскость по плоской грани тела.

    Направление «вправо» берётся у самой поверхности, а не выдумывается:
    иначе эскиз на грани поворачивался бы от того, как ядро пересчитало
    грань после предыдущей операции.

    Начало координат ставится в **проекцию начала координат детали** на эту
    плоскость, а не туда, где его оставило ядро. Ядро кладёт начало в центр
    грани: у пластины 100 × 60 эскизная точка (10, 10) оказывалась в (60, 40)
    детали. Формально верно, практически непригодно — человек рисует на
    грани в тех же координатах, в которых нарисовал основание, и любое
    другое соглашение он обнаружит только по готовому телу не на месте.
    """
    from OCP.BRepAdaptor import BRepAdaptor_Surface
    from OCP.GeomAbs import GeomAbs_Plane

    surface = BRepAdaptor_Surface(face)
    if surface.GetType() != GeomAbs_Plane:
        raise ValueError("эскиз строится только на плоской грани")
    position = surface.Plane().Position()
    location = position.Location()
    normal = _normalize(
        (position.Direction().X(), position.Direction().Y(), position.Direction().Z())
    )
    x_direction = (
        position.XDirection().X(),
        position.XDirection().Y(),
        position.XDirection().Z(),
    )
    # Проекция нуля детали на плоскость грани.
    distance = _dot((location.X(), location.Y(), location.Z()), normal)
    origin = tuple(distance * component for component in normal)
    return Plane(origin, normal, x_direction, "грань")
