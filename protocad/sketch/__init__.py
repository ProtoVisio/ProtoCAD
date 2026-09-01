"""Параметрический эскиз ProtoCAD.

Геометрия хранится в структурах ProtoCAD, решателю передаются только
параметры и связи. Поэтому смена решателя — замена одного класса, а не
переписывание эскизов. Выбор реализации и его лицензионные последствия — в
``solver.py``.

Два решения определяют всё устройство модуля.

**Журнал построения — источник истины.** Эскиз это не набор координат, а
последовательность операций: создать точку, провести отрезок, наложить
связь, проставить размер. Координаты вторичны — их вычисляет решатель.
Поэтому сохранение это запись журнала, а загрузка — его проигрывание.

**Объекты опознаются по идентификаторам, а не по местам в списке.** Раньше
связь ссылалась на «третий отрезок»; после удаления второго третий
становился вторым, и связь беззвучно переезжала на чужую геометрию.
Идентификатор выдаётся один раз и не переиспользуется, а удаление объекта
вычёркивает из журнала всё, что на него ссылалось.

Из второго решения следует и способ удаления: решатели не умеют убирать
уже добавленные объекты, поэтому при удалении система собирается заново по
исправленному журналу. Дескрипторы внутри объектов при этом подменяются, а
сами объекты остаются теми же — ссылки, которые держит интерфейс, не
протухают.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from . import geom2d
from .plane import STANDARD as STANDARD_PLANES
from .plane import Plane
from .solver import (
    ArcGeometry,
    CurveHandle,
    EllipseGeometry,
    SketchSolver,
    SplineGeometry,
    Solution,
    SolverInfo,
    SolverUnavailable,
    available_solvers,
    make_solver,
)

__all__ = [
    "Sketch",
    "SketchError",
    "Plane",
    "STANDARD_PLANES",
    "Point",
    "Segment",
    "Constraint",
    "Dimension",
    "Solution",
    "SolverInfo",
    "SolverUnavailable",
    "available_solvers",
    "make_solver",
    "geom2d",
]

FORMAT_VERSION = 2


class SketchError(RuntimeError):
    """Эскиз построен неверно или не выдаёт контур."""


@dataclass(eq=False)
class Point:
    id: int
    handle: object
    name: str = ""
    construction: bool = False
    external: str = ""     # подпись объекта детали, если точка снята с него
    dangling: bool = False  # объект детали пропал при пересчёте

    @property
    def label(self) -> str:
        if self.external:
            return self.name or f"Ссылка{self.id}"
        return self.name or f"Точка{self.id}"


@dataclass(eq=False)
class Segment:
    """Отрезок, дуга или окружность.

    ``points`` — концы в порядке, зависящем от вида: у отрезка это начало и
    конец, у дуги центр, начало и конец, у окружности только центр. Общий
    порядок выбран так, что последние две записи у отрезка и дуги — всегда
    концы: сборка контура опирается на это и не разбирает виды по отдельности.
    """

    id: int
    kind: str  # "line" | "arc" | "circle" | "ellipse"
    handle: object
    points: tuple
    name: str = ""
    construction: bool = False
    external: str = ""
    dangling: bool = False

    KIND_NAMES = {"line": "Отрезок", "arc": "Дуга", "circle": "Окружность",
                  "ellipse": "Эллипс", "spline": "Сплайн",
                  "ellipse_arc": "Дуга эллипса",
                  "parabola_arc": "Парабола",
                  "hyperbola_arc": "Гипербола"}

    #: Замкнутые сами на себя: концов у них нет, и спрашивать их бесполезно.
    CLOSED = ("circle", "ellipse")

    @property
    def label(self) -> str:
        return self.name or f"{self.KIND_NAMES.get(self.kind, self.kind)}{self.id}"

    @property
    def ends(self) -> tuple:
        """Концевые точки. У окружности и эллипса их нет.

        У эллипса в ``points`` лежат центр и ФОКУС, и выдать их за концы —
        готовая беда: сборка контура опирается на последние две записи и
        принялась бы стыковать эллипс по фокусу.
        """
        return () if self.kind in self.CLOSED else tuple(self.points[-2:])


@dataclass(eq=False)
class Constraint:
    """Наложенная связь. Хранится отдельно, чтобы её можно было показать и снять."""

    id: int
    kind: str
    targets: tuple
    handle: object = None

    TITLES = {
        "coincident": "Совпадение",
        "horizontal": "Горизонталь",
        "vertical": "Вертикаль",
        "parallel": "Параллельность",
        "perpendicular": "Перпендикулярность",
        "equal": "Равенство",
        "anchor": "Закрепление",
        "point_on_line": "Точка на отрезке",
        "point_on_curve": "Точка на кривой",
        "midpoint": "Середина",
        "tangent": "Касание",
        "symmetric": "Симметрия",
        "concentric": "Концентричность",
        "equal_radius": "Равные радиусы",
    }

    @property
    def title(self) -> str:
        return self.TITLES.get(self.kind, self.kind)


@dataclass(eq=False)
class Dimension:
    """Управляющий размер. ``value`` — то, что видит человек, в мм или градусах."""

    id: int
    kind: str  # "distance" | "radius" | "diameter" | "angle"
    name: str
    targets: tuple
    value: float
    handle: object = None
    # Куда интерфейс оттащил выноску. К решению отношения не имеет, но
    # теряться при сохранении не должно.
    offset: tuple[float, float] = (0.0, 0.0)

    UNITS = {"angle": "°"}

    @property
    def text(self) -> str:
        unit = self.UNITS.get(self.kind, "")
        if self.kind == "diameter":
            return f"⌀{self.value:g}"
        if self.kind == "radius":
            return f"R{self.value:g}"
        return f"{self.value:g}{unit}"


@dataclass
class SketchStatus:
    """Состояние эскиза одной строкой — то, что показывают внизу окна."""

    ok: bool
    dof: int
    message: str

    @property
    def text(self) -> str:
        if not self.ok:
            return f"Эскиз противоречив: {self.message}"
        if self.dof < 0:
            # Решатель посчитал геометрию, но отказался считать степени
            # свободы из-за избыточных связей. Выдать при этом «полностью
            # определён» значило бы соврать в единственной строке, ради
            # которой на неё и смотрят.
            return "Эскиз решён, но связи избыточны — число степеней свободы неизвестно"
        if self.dof == 0:
            return "Эскиз полностью определён"
        return f"Эскиз недоопределён: степеней свободы {self.dof}"


@dataclass
class _Log:
    """Журнал построения. Отдельный тип, чтобы правки шли в одном месте."""

    entries: list[dict] = field(default_factory=list)

    def add(self, operation: str, **arguments) -> dict:
        entry = {"op": operation, **arguments}
        self.entries.append(entry)
        return entry

    # Поля журнала, содержащие ССЫЛКИ на объекты. Список именно белый:
    # перебирать все целые подряд нельзя — `value=40` у размера и
    # `construction=True` (в Python это тоже целое) неотличимы от номеров и
    # уносили бы с собой посторонние записи.
    REFERENCE_KEYS = frozenset(
        {"id", "a", "b", "point", "segment", "start", "end", "center", "axis",
         "focus", "poles"}
    )

    def drop_referencing(self, ids: set[int]) -> None:
        """Убрать записи о самих объектах и обо всём, что на них ссылается."""
        def touches(value) -> bool:
            # Ссылка бывает и СПИСКОМ: у сплайна полюсов сколько угодно.
            # Пока список не разбирался, удаление полюса оставляло запись
            # о сплайне висеть на исчезнувшей точке.
            if isinstance(value, (list, tuple)):
                return any(item in ids for item in value)
            return value in ids

        self.entries = [
            entry
            for entry in self.entries
            if not any(
                touches(value)
                for key, value in entry.items()
                if key in self.REFERENCE_KEYS
            )
        ]


class Sketch:
    """Плоский эскиз: геометрия, связи, размеры, решение, выдача профиля."""

    def __init__(
        self,
        name: str = "Sketch",
        solver: SketchSolver | None = None,
        solver_name: str = "",
        plane: Plane | None = None,
    ):
        self.name = name
        # Плоскость — свойство эскиза, а не операции над ним. Иначе один и
        # тот же контур, использованный дважды, пришлось бы размещать в
        # каждой операции заново, и рассогласование было бы вопросом времени.
        self.plane = plane if plane is not None else STANDARD_PLANES["XY"]
        #: НА ЧЁМ эскиз лежит: грань детали, справочная плоскость или
        #: стандартная. Пусто — плоскость задана числами и ни за чем не
        #: следует.
        #:
        #: Без этого плоскость эскиза — снимок: подвинули грань, а эскиз
        #: остался, где был. Отказа не будет — деталь построится, просто
        #: не та. Само поле координат не заменяет: координаты говорят ГДЕ,
        #: а опора — ПОЧЕМУ там, и пересчитать можно только по второму.
        self.support: dict | None = None
        self._solver_name = solver_name
        self.solver = solver if solver is not None else make_solver(solver_name)
        self._points: dict[int, Point] = {}
        self._segments: dict[int, Segment] = {}
        self._constraints: dict[int, Constraint] = {}
        self._dimensions: dict[int, Dimension] = {}
        self._log = _Log()
        self._next_id = 1

    # --- доступ к содержимому ---

    @property
    def points(self) -> list[Point]:
        return list(self._points.values())

    @property
    def segments(self) -> list[Segment]:
        return list(self._segments.values())

    @property
    def constraints(self) -> list[Constraint]:
        return list(self._constraints.values())

    @property
    def dimensions(self) -> list[Dimension]:
        return list(self._dimensions.values())

    @property
    def solver_info(self) -> SolverInfo:
        return type(self.solver).info()

    def entity(self, entity_id: int):
        """Объект по идентификатору, каким бы он ни был."""
        for registry in (self._points, self._segments, self._constraints, self._dimensions):
            if entity_id in registry:
                return registry[entity_id]
        return None

    def relations_on(self, entity) -> list:
        """Связи и размеры, наложенные на объект. То, что показывают в списке
        отношений и чем объясняют, почему объект не двигается."""
        return [
            relation
            for registry in (self._constraints, self._dimensions)
            for relation in registry.values()
            if any(target is entity for target in relation.targets)
        ]

    def uses_point(self, point: Point) -> list[Segment]:
        return [
            segment
            for segment in self._segments.values()
            if any(other is point for other in segment.points)
        ]

    def dimension_by_name(self, name: str) -> Dimension | None:
        for dimension in self._dimensions.values():
            if dimension.name == name:
                return dimension
        return None

    def _claim_id(self) -> int:
        result = self._next_id
        self._next_id += 1
        return result

    # --- геометрия ---

    def point(self, x: float, y: float, name: str = "", construction: bool = False,
              _id: int | None = None, external: str = "") -> Point:
        point_id = _id if _id is not None else self._claim_id()
        result = Point(point_id, self.solver.add_point(x, y), name, construction, external)
        self._points[point_id] = result
        self._log.add("point", id=point_id, x=x, y=y, name=name,
                      construction=construction, external=external)
        return result

    def line(self, start: Point, end: Point, name: str = "",
             construction: bool = False, _id: int | None = None,
             external: str = "") -> Segment:
        segment_id = _id if _id is not None else self._claim_id()
        segment = Segment(
            segment_id,
            "line",
            self.solver.add_line(start.handle, end.handle),
            (start, end),
            name,
            construction,
            external,
        )
        self._segments[segment_id] = segment
        self._log.add(
            "line", id=segment_id, start=start.id, end=end.id,
            name=name, construction=construction, external=external,
        )
        return segment

    def arc(self, center: Point, start: Point, end: Point, name: str = "",
            construction: bool = False, _id: int | None = None) -> Segment:
        """Дуга по центру и двум концам, против часовой стрелки от начала."""
        segment_id = _id if _id is not None else self._claim_id()
        segment = Segment(
            segment_id,
            "arc",
            self.solver.add_arc(center.handle, start.handle, end.handle),
            (center, start, end),
            name,
            construction,
        )
        self._segments[segment_id] = segment
        self._log.add(
            "arc", id=segment_id, center=center.id, start=start.id, end=end.id,
            name=name, construction=construction,
        )
        return segment

    def circle(self, center: Point, radius: float, name: str = "",
               construction: bool = False, _id: int | None = None) -> Segment:
        segment_id = _id if _id is not None else self._claim_id()
        segment = Segment(
            segment_id,
            "circle",
            self.solver.add_circle(center.handle, radius),
            (center,),
            name,
            construction,
        )
        self._segments[segment_id] = segment
        self._log.add(
            "circle", id=segment_id, center=center.id, radius=radius,
            name=name, construction=construction,
        )
        return segment

    def ellipse(self, center: Point, focus: Point, minor: float,
                name: str = "", construction: bool = False,
                _id: int | None = None) -> Segment:
        """Эллипс по центру, ФОКУСУ и малой полуоси.

        Фокус — обычная точка эскиза, и это не причуда решателя, а удобство:
        поворот и большая полуось получаются перетаскиванием фокуса и
        обычными связями, без отдельных величин. Большая полуось выводится
        из расстояния до фокуса: ``a = √(b² + c²)``.
        """
        if minor <= 0.0:
            raise SketchError(f"{self.name}: малая полуось должна быть больше нуля")
        segment_id = _id if _id is not None else self._claim_id()
        segment = Segment(
            segment_id,
            "ellipse",
            self.solver.add_ellipse(center.handle, focus.handle, minor),
            (center, focus),
            name,
            construction,
        )
        self._segments[segment_id] = segment
        self._log.add(
            "ellipse", id=segment_id, center=center.id, focus=focus.id,
            minor=minor, name=name, construction=construction,
        )
        return segment

    def spline(self, poles: list, knots, mults, degree: int,
               name: str = "", construction: bool = False,
               _id: int | None = None) -> Segment:
        """Сплайн по ПОЛЮСАМ, узлам и кратностям.

        Полюсы — обычные точки эскиза: их двигают мышью и держат связями.
        Узлы и кратности числа: собственного положения у них нет.
        """
        if len(poles) < degree + 1:
            raise SketchError(
                f"{self.name}: для степени {degree} нужно не меньше "
                f"{degree + 1} полюсов")
        segment_id = _id if _id is not None else self._claim_id()
        segment = Segment(
            segment_id,
            "spline",
            self.solver.add_spline([point.handle for point in poles],
                                   knots, mults, degree),
            tuple(poles),
            name,
            construction,
        )
        self._segments[segment_id] = segment
        self._log.add(
            "spline", id=segment_id, poles=[point.id for point in poles],
            knots=[float(value) for value in knots],
            mults=[int(value) for value in mults], degree=int(degree),
            name=name, construction=construction,
        )
        return segment

    def spline_through(self, coordinates, degree: int = 3,
                       construction: bool = False) -> Segment:
        """Сплайн, ПРОХОДЯЩИЙ через заданные точки.

        Так его и рисуют: щёлкают по местам, через которые кривая должна
        пройти. Полюсы считаются по ним и становятся точками эскиза —
        править кривую потом можно и за них, и связями.
        """
        from . import bspline as bspline_module

        poles, knots, mults = bspline_module.poles_through(coordinates, degree)
        points = [self.point(x, y, construction=True) for x, y in poles]
        return self.spline(points, knots, mults, mults[0] - 1,
                           construction=construction)

    def ellipse_arc(self, center: Point, focus: Point, minor: float,
                    start_angle: float, end_angle: float,
                    start: Point, end: Point, name: str = "",
                    construction: bool = False,
                    _id: int | None = None) -> Segment:
        """Кусок эллипса между двумя параметрическими углами.

        Порядок точек тот же, что у дуги круга: последние две — концы.
        На этом держится сшивка контура, и менять его нельзя.
        """
        if minor <= 0.0:
            raise SketchError(f"{self.name}: малая полуось должна быть больше нуля")
        segment_id = _id if _id is not None else self._claim_id()
        segment = Segment(
            segment_id,
            "ellipse_arc",
            self.solver.add_ellipse_arc(center.handle, focus.handle, minor,
                                        start_angle, end_angle,
                                        start.handle, end.handle),
            (center, focus, start, end),
            name,
            construction,
        )
        self._segments[segment_id] = segment
        self._log.add(
            "ellipse_arc", id=segment_id, center=center.id, focus=focus.id,
            minor=minor, start_angle=start_angle, end_angle=end_angle,
            start=start.id, end=end.id, name=name, construction=construction,
        )
        return segment

    def hyperbola_arc(self, center: Point, focus: Point, minor: float,
                      start_angle: float, end_angle: float,
                      start: Point, end: Point, name: str = "",
                      construction: bool = False,
                      _id: int | None = None) -> Segment:
        """Дуга гиперболы: центр, фокус, малая полуось и два конца.

        Порядок точек тот же, что у дуги эллипса: последние две — концы.
        На этом держится сшивка контура, и менять его нельзя.
        """
        if minor <= 0.0:
            raise SketchError(
                f"{self.name}: малая полуось должна быть больше нуля")
        segment_id = _id if _id is not None else self._claim_id()
        segment = Segment(
            segment_id,
            "hyperbola_arc",
            self.solver.add_hyperbola_arc(center.handle, focus.handle, minor,
                                          start_angle, end_angle,
                                          start.handle, end.handle),
            (center, focus, start, end),
            name,
            construction,
        )
        self._segments[segment_id] = segment
        self._log.add(
            "hyperbola_arc", id=segment_id, center=center.id, focus=focus.id,
            minor=minor, start_angle=start_angle, end_angle=end_angle,
            start=start.id, end=end.id, name=name, construction=construction)
        return segment

    def hyperbola_at(self, cx: float, cy: float, major: float, minor: float,
                     angle_deg: float, start: float, end: float,
                     construction: bool = False) -> Segment:
        """Гипербола по числам: центр, полуоси, наклон и пределы.

        Пределы — ГИПЕРБОЛИЧЕСКИЙ параметр, тот же, что у решателя и у
        ядра: точка при ``t`` это ``(a·ch t, b·sh t)``.
        """
        if major <= 0.0 or minor <= 0.0:
            raise SketchError(
                f"{self.name}: полуоси должны быть больше нуля")
        angle = math.radians(angle_deg)
        cos, sin = math.cos(angle), math.sin(angle)
        gap = math.sqrt(major * major + minor * minor)

        def _at(value: float) -> tuple:
            along = major * math.cosh(value)
            across = minor * math.sinh(value)
            return (cx + along * cos - across * sin,
                    cy + along * sin + across * cos)

        centre = self.point(cx, cy, construction=True)
        focus = self.point(cx + gap * cos, cy + gap * sin, construction=True)
        return self.hyperbola_arc(centre, focus, minor, start, end,
                                  self.point(*_at(start)),
                                  self.point(*_at(end)),
                                  construction=construction)

    def hyperbola_geometry(self, segment: Segment):
        return self.solver.hyperbola_arc_geometry(segment.handle)

    def parabola_arc(self, vertex: Point, focus: Point,
                     start_angle: float, end_angle: float,
                     start: Point, end: Point, name: str = "",
                     construction: bool = False,
                     _id: int | None = None) -> Segment:
        """Дуга параболы: вершина, фокус и два конца.

        Порядок точек тот же, что у дуги эллипса: последние две — концы.
        На этом держится сшивка контура, и менять его нельзя.
        """
        segment_id = _id if _id is not None else self._claim_id()
        segment = Segment(
            segment_id,
            "parabola_arc",
            self.solver.add_parabola_arc(vertex.handle, focus.handle,
                                         start_angle, end_angle,
                                         start.handle, end.handle),
            (vertex, focus, start, end),
            name,
            construction,
        )
        self._segments[segment_id] = segment
        self._log.add(
            "parabola_arc", id=segment_id, vertex=vertex.id, focus=focus.id,
            start_angle=start_angle, end_angle=end_angle,
            start=start.id, end=end.id, name=name,
            construction=construction)
        return segment

    def parabola_at(self, vx: float, vy: float, focal: float,
                    angle_deg: float, start: float, end: float,
                    construction: bool = False) -> Segment:
        """Парабола по числам: вершина, раскрытие, наклон оси и пределы.

        ``focal`` — расстояние от вершины до фокуса; чем оно больше, тем
        кривая положе. Пределы задаются в том же параметре, что и у
        решателя: это местная координата поперёк оси.
        """
        if focal <= 0.0:
            raise SketchError(
                f"{self.name}: расстояние до фокуса должно быть больше нуля")
        angle = math.radians(angle_deg)
        cos, sin = math.cos(angle), math.sin(angle)

        def _at(value: float) -> tuple:
            along, across = value * value / (4.0 * focal), value
            return (vx + along * cos - across * sin,
                    vy + along * sin + across * cos)

        vertex = self.point(vx, vy, construction=True)
        focus = self.point(vx + focal * cos, vy + focal * sin,
                           construction=True)
        return self.parabola_arc(vertex, focus, start, end,
                                 self.point(*_at(start)),
                                 self.point(*_at(end)),
                                 construction=construction)

    def parabola_geometry(self, segment: Segment):
        return self.solver.parabola_arc_geometry(segment.handle)

    def ellipse_at(self, cx: float, cy: float, major: float, minor: float,
                   angle_deg: float = 0.0, construction: bool = False) -> Segment:
        """Эллипс по числам: центр, полуоси и наклон большой оси.

        Так его и задают, когда рисуют. Фокус вычисляется: он лежит на
        большой оси на расстоянии ``√(a² − b²)`` от центра.
        """
        if major < minor:
            major, minor = minor, major
            angle_deg += 90.0
        if minor <= 0.0:
            raise SketchError(f"{self.name}: полуось должна быть больше нуля")
        gap = math.sqrt(max(major * major - minor * minor, 0.0))
        angle = math.radians(angle_deg)
        centre = self.point(cx, cy, construction=True)
        focus = self.point(cx + gap * math.cos(angle),
                           cy + gap * math.sin(angle), construction=True)
        return self.ellipse(centre, focus, minor, construction=construction)

    def ellipse_arc_at(self, cx: float, cy: float, major: float,
                       minor: float, angle_deg: float,
                       start_deg: float, end_deg: float,
                       construction: bool = False) -> Segment:
        """Дуга эллипса по числам — тем же, какими её и рисуют.

        Углы СОБСТВЕННЫЕ, параметрические, а не полярные: у эллипса это
        разные вещи, и точка «под 45°» лежит не там, где параметр 45°.
        Инструмент на экране считает параметр по щелчку сам.
        """
        if major < minor:
            major, minor = minor, major
            angle_deg += 90.0
            start_deg -= 90.0
            end_deg -= 90.0
        if minor <= 0.0:
            raise SketchError(f"{self.name}: полуось должна быть больше нуля")
        gap = math.sqrt(max(major * major - minor * minor, 0.0))
        angle = math.radians(angle_deg)
        centre = self.point(cx, cy, construction=True)
        focus = self.point(cx + gap * math.cos(angle),
                           cy + gap * math.sin(angle), construction=True)
        start_angle = math.radians(start_deg)
        end_angle = math.radians(end_deg)

        def _at(parameter: float) -> tuple:
            along = major * math.cos(parameter)
            across = minor * math.sin(parameter)
            return (cx + along * math.cos(angle) - across * math.sin(angle),
                    cy + along * math.sin(angle) + across * math.cos(angle))

        start = self.point(*_at(start_angle))
        end = self.point(*_at(end_angle))
        return self.ellipse_arc(centre, focus, minor, start_angle, end_angle,
                                start, end, construction=construction)

    # --- составные построения ---
    #
    # Каждое собирается из примитивов, поэтому в журнал попадает не «слот», а
    # его отрезки и дуги со связями. Файл не зависит от того, каким
    # инструментом рисовали, и остаётся читаемым, даже если инструмент потом
    # переделают.

    def polyline(self, coordinates, closed: bool = True,
                 construction: bool = False) -> list[Segment]:
        """Цепочка отрезков по координатам — самый частый случай."""
        points = [self.point(x, y, construction=construction) for x, y in coordinates]
        segments = [
            self.line(points[index], points[index + 1], construction=construction)
            for index in range(len(points) - 1)
        ]
        if closed:
            segments.append(self.line(points[-1], points[0], construction=construction))
        return segments

    def rectangle(self, x1: float, y1: float, x2: float, y2: float,
                  construction: bool = False) -> list[Segment]:
        """Прямоугольник по двум углам, со связями горизонтальности.

        Связи ставятся сразу: прямоугольник без них — это четыре отрезка,
        которые расползаются при первом же перетаскивании.
        """
        segments = self.polyline(
            [(x1, y1), (x2, y1), (x2, y2), (x1, y2)], construction=construction
        )
        self.horizontal(segments[0])
        self.vertical(segments[1])
        self.horizontal(segments[2])
        self.vertical(segments[3])
        return segments

    def center_rectangle(self, cx: float, cy: float, x: float, y: float,
                         construction: bool = False) -> list[Segment]:
        """Прямоугольник от центра. Центр остаётся в эскизе вспомогательной
        точкой и связан симметрией — иначе «от центра» перестаёт быть правдой
        сразу после первой правки размера."""
        half_x, half_y = abs(x - cx), abs(y - cy)
        segments = self.rectangle(
            cx - half_x, cy - half_y, cx + half_x, cy + half_y, construction=construction
        )
        center = self.point(cx, cy, construction=True)
        diagonal = self.line(segments[0].points[0], segments[2].points[0], construction=True)
        self.midpoint(center, diagonal)
        self.symmetric(segments[0].points[0], segments[1].points[1], diagonal)
        return segments

    def polygon(self, cx: float, cy: float, radius: float, sides: int,
                inscribed: bool = True, construction: bool = False) -> list[Segment]:
        """Правильный многоугольник. Вспомогательная окружность и равенство
        сторон держат его правильным при правке размеров."""
        vertices = geom2d.polygon_points((cx, cy), radius, sides, inscribed=inscribed)
        segments = self.polyline(vertices, construction=construction)
        center = self.point(cx, cy, construction=True)
        guide = self.circle(center, radius if inscribed else radius / math.cos(math.pi / sides),
                            construction=True)
        for segment in segments:
            self.equal(segments[0], segment)
        for vertex in {segment.points[0] for segment in segments}:
            self.point_on_curve(vertex, guide)
        return segments

    def slot(self, x1: float, y1: float, x2: float, y2: float,
             width: float, construction: bool = False) -> list[Segment]:
        """Паз: два отрезка и две дуги. Точки стыка общие, поэтому контур
        замкнут по построению, а не по совпадению координат."""
        axis = geom2d.direction((x1, y1), (x2, y2))
        if axis == (0.0, 0.0):
            raise SketchError(f"{self.name}: паз нулевой длины")
        half = width / 2.0
        shift = geom2d.scale(geom2d.normal(axis), half)
        a1 = geom2d.add((x1, y1), shift)
        a2 = geom2d.add((x2, y2), shift)
        b2 = geom2d.add((x2, y2), geom2d.scale(shift, -1.0))
        b1 = geom2d.add((x1, y1), geom2d.scale(shift, -1.0))

        start_center = self.point(x1, y1, construction=True)
        end_center = self.point(x2, y2, construction=True)
        pa1 = self.point(*a1, construction=construction)
        pa2 = self.point(*a2, construction=construction)
        pb2 = self.point(*b2, construction=construction)
        pb1 = self.point(*b1, construction=construction)

        top = self.line(pa1, pa2, construction=construction)
        bottom = self.line(pb2, pb1, construction=construction)
        # Дуга всегда идёт ПРОТИВ ЧАСОВОЙ от начала к концу, поэтому концы
        # выбираются так, чтобы полуокружность выпирала наружу. Возьми
        # дальнюю крышку как pa2 → pb2 — и она обогнёт центр с внутренней
        # стороны, срезав паз вместо того, чтобы его замкнуть.
        cap_far = self.arc(end_center, pb2, pa2, construction=construction)
        cap_near = self.arc(start_center, pa1, pb1, construction=construction)

        axis_line = self.line(start_center, end_center, construction=True)
        # Набор связей намеренно скупой: две параллели оси и равные радиусы.
        # Касание кромок к дугам здесь уже НЕ ограничение, а следствие — оно
        # выполняется само, и добавленное явно делает систему избыточной.
        # PlaneGCS такое терпит и лишь отмечает, SolveSpace отказывается
        # считать степени свободы; проверено на обоих.
        self.parallel(top, axis_line)
        self.parallel(bottom, axis_line)
        self.equal_radius(cap_far, cap_near)
        return [top, cap_far, bottom, cap_near]

    def circle_through(self, x1: float, y1: float, x2: float, y2: float,
                       x3: float, y3: float, construction: bool = False) -> Segment:
        """Окружность по трём точкам на ней.

        Точки на окружности НЕ становятся её точками в эскизе: у окружности
        есть только центр. Чтобы построение осталось управляемым, три точки
        сохраняются и связываются условием «точка на кривой» — иначе
        окружность после первой же правки перестала бы через них проходить.
        """
        found = geom2d.circle_through((x1, y1), (x2, y2), (x3, y3))
        if found is None:
            raise SketchError(f"{self.name}: три точки окружности лежат на прямой")
        center, radius = found
        circle = self.circle(
            self.point(*center, construction=True), radius, construction=construction
        )
        for x, y in ((x1, y1), (x2, y2), (x3, y3)):
            self.point_on_curve(self.point(x, y, construction=True), circle)
        return circle

    def tangent_arc(self, segment: Segment, at_point: Point,
                    x: float, y: float, construction: bool = False) -> Segment:
        """Дуга, касательная к объекту в его конце и проходящая через точку.

        Самый частый способ продолжить контур: подошли отрезком, дальше
        плавно. Касание накладывается связью, а не только начальным
        положением, — иначе первая же правка размера его разорвёт.
        """
        if at_point not in segment.points:
            raise SketchError(f"{self.name}: точка не принадлежит объекту")
        shape_start, shape_end = (self.coordinates(p) for p in segment.ends)
        anchor = self.coordinates(at_point)
        # Касательная смотрит НАРУЖУ от объекта: дуга продолжает контур, а
        # не возвращается по нему.
        other = shape_end if geom2d.distance(anchor, shape_start) < 1e-9 else shape_start
        along = geom2d.direction(other, anchor)
        center = geom2d.tangent_arc_center(anchor, along, (x, y))
        if center is None:
            raise SketchError(f"{self.name}: касательной дуги через эту точку нет")

        finish = self.point(x, y, construction=construction)
        middle = self.point(*center, construction=True)
        # Обход против часовой: какой конец начальный, решает положение
        # точки касания относительно направления вращения.
        start_angle = geom2d.angle_at(center, anchor)
        end_angle = geom2d.angle_at(center, (x, y))
        cross = along[0] * (center[1] - anchor[1]) - along[1] * (center[0] - anchor[0])
        first, last = (at_point, finish) if cross > 0 else (finish, at_point)
        arc = self.arc(middle, first, last, construction=construction)
        try:
            self.tangent(segment, arc)
        except Exception:  # noqa: BLE001 — связь могла оказаться избыточной
            pass
        return arc

    def rectangle_3p(self, x1: float, y1: float, x2: float, y2: float,
                     x3: float, y3: float, construction: bool = False) -> list[Segment]:
        """Прямоугольник по трём точкам: угол, направление стороны, ширина.

        В отличие от прямоугольника по двум углам, этот может стоять под
        любым углом — связи горизонтальности здесь не годятся, держат его
        перпендикулярность и параллельность.
        """
        along = geom2d.direction((x1, y1), (x2, y2))
        if along == (0.0, 0.0):
            raise SketchError(f"{self.name}: первая сторона нулевой длины")
        across = geom2d.normal(along)
        width = ((x3 - x2) * across[0] + (y3 - y2) * across[1])
        shift = geom2d.scale(across, width)
        corners = [
            (x1, y1), (x2, y2),
            geom2d.add((x2, y2), shift), geom2d.add((x1, y1), shift),
        ]
        sides = self.polyline(corners, construction=construction)
        self.parallel(sides[0], sides[2])
        self.parallel(sides[1], sides[3])
        self.perpendicular(sides[0], sides[1])
        return sides

    def parallelogram(self, x1: float, y1: float, x2: float, y2: float,
                      x3: float, y3: float, construction: bool = False) -> list[Segment]:
        """Параллелограмм по углу и двум векторам."""
        first = (x2 - x1, y2 - y1)
        second = (x3 - x2, y3 - y2)
        corners = [
            (x1, y1), (x2, y2),
            geom2d.add((x2, y2), second), geom2d.add((x1, y1), second),
        ]
        sides = self.polyline(corners, construction=construction)
        self.parallel(sides[0], sides[2])
        self.parallel(sides[1], sides[3])
        self.equal(sides[0], sides[2])
        self.equal(sides[1], sides[3])
        return sides

    def center_slot(self, cx: float, cy: float, x: float, y: float,
                    width: float, construction: bool = False) -> list[Segment]:
        """Паз от центра: центр, половина длины и направление, ширина."""
        half = (x - cx, y - cy)
        return self.slot(
            cx - half[0], cy - half[1], cx + half[0], cy + half[1],
            width, construction=construction,
        )

    def arc_through(self, x1: float, y1: float, x2: float, y2: float,
                    x3: float, y3: float, construction: bool = False) -> Segment:
        """Дуга по трём точкам: начало, промежуточная, конец.

        Центр вычисляется здесь, а не решателем: промежуточная точка задаёт
        ветвь, и без неё дуга через два конца неоднозначна.
        """
        center = _circumcenter((x1, y1), (x2, y2), (x3, y3))
        if center is None:
            raise SketchError(f"{self.name}: три точки дуги лежат на одной прямой")
        start_angle = geom2d.angle_at(center, (x1, y1))
        middle_angle = geom2d.angle_at(center, (x2, y2))
        end_angle = geom2d.angle_at(center, (x3, y3))
        first, last = (x1, y1), (x3, y3)
        # Если промежуточная точка не попадает на дугу против часовой, значит
        # обход обратный — меняем концы местами.
        if not geom2d.arc_contains(start_angle, end_angle, middle_angle):
            first, last = last, first
        return self.arc(
            self.point(*center, construction=True),
            self.point(*first, construction=construction),
            self.point(*last, construction=construction),
            construction=construction,
        )

    # --- ссылки на деталь ---

    def reference_point(self, x: float, y: float, source: str = "",
                        name: str = "") -> Point:
        """Вершина детали, перенесённая в эскиз.

        Закрепляется: точка детали в эскизе не свободна, она там, где её
        поставила деталь. К ней после этого привязывают обычными связями —
        совпадением, «точкой на отрезке», размером, — и эскиз оказывается
        связан с телом, не зная о нём ничего.
        """
        point = self.point(x, y, name, construction=True,
                           external=source or "деталь")
        self.anchor(point)
        return point

    def reference_line(self, x1: float, y1: float, x2: float, y2: float,
                       source: str = "", name: str = "") -> Segment:
        """Ребро детали, перенесённое в эскиз вспомогательным отрезком.

        В отличие от «преобразовать объекты», эта прямая не входит в контур:
        она нужна, чтобы к ней приложить параллельность или перпендикуляр.
        """
        source = source or "деталь"
        # Концы получают разные подписи: иначе при пересадке обоим достанется
        # одно и то же место и прямая схлопнется в точку.
        start = self.reference_point(x1, y1, f"{source}#0")
        end = self.reference_point(x2, y2, f"{source}#1")
        return self.line(start, end, name, construction=True, external=source)

    @property
    def references(self) -> list:
        """Всё, что снято с детали, в порядке появления."""
        entities = list(self._points.values()) + list(self._segments.values())
        return [entity for entity in entities if entity.external]

    @property
    def dangling(self) -> list:
        """Ссылки, потерявшие свой объект детали."""
        return [entity for entity in self.references if entity.dangling]

    def rebind(self, resolver) -> list[str]:
        """Пересадить ссылки на деталь после её пересчёта.

        ``resolver`` получает подпись объекта детали и возвращает его новое
        положение в плоскости эскиза либо ``None``. Правится ЖУРНАЛ, а не
        решатель: закреплённую точку нельзя просто сдвинуть — закрепление
        держит её там, где она была создана. Эскиз собирается заново из
        исправленного журнала, тем же путём, что и при отмене.

        Возвращает подписи, которых в детали больше нет: потерянная ссылка
        не молчит, она помечается и попадает в состояние эскиза.
        """
        lost: list[str] = []
        moved: set[int] = set()
        for entry in self._log.entries:
            source = entry.get("external")
            if entry.get("op") != "point" or not source:
                continue
            found = resolver(source)
            if found is None:
                lost.append(source)
                continue
            if abs(entry["x"] - found[0]) > 1e-9 or abs(entry["y"] - found[1]) > 1e-9:
                entry["x"], entry["y"] = float(found[0]), float(found[1])
                moved.add(int(entry["id"]))
        if moved:
            data = self.to_dict()
            # Сохранённые координаты подставляются вместо журнальных, и
            # переехавшая ссылка вернулась бы на прежнее место.
            data["coordinates"] = {
                key: value for key, value in data["coordinates"].items()
                if int(key) not in moved
            }
            self.restore(data)
        missing = {source.split("#")[0] for source in lost}
        for entity in self.references:
            entity.dangling = entity.external.split("#")[0] in missing
        return lost

    # --- связи ---

    def _add_constraint(self, kind: str, handle, targets, _id: int | None = None) -> Constraint:
        constraint_id = _id if _id is not None else self._claim_id()
        constraint = Constraint(constraint_id, kind, tuple(targets), handle)
        self._constraints[constraint_id] = constraint
        return constraint

    def coincident(self, a: Point, b: Point) -> Constraint:
        constraint = self._add_constraint(
            "coincident", self.solver.constrain_coincident(a.handle, b.handle), (a, b)
        )
        self._log.add("coincident", id=constraint.id, a=a.id, b=b.id)
        return constraint

    def horizontal(self, segment: Segment) -> Constraint:
        constraint = self._add_constraint(
            "horizontal", self.solver.constrain_horizontal(segment.handle), (segment,)
        )
        self._log.add("horizontal", id=constraint.id, segment=segment.id)
        return constraint

    def vertical(self, segment: Segment) -> Constraint:
        constraint = self._add_constraint(
            "vertical", self.solver.constrain_vertical(segment.handle), (segment,)
        )
        self._log.add("vertical", id=constraint.id, segment=segment.id)
        return constraint

    def parallel(self, a: Segment, b: Segment) -> Constraint:
        constraint = self._add_constraint(
            "parallel", self.solver.constrain_parallel(a.handle, b.handle), (a, b)
        )
        self._log.add("parallel", id=constraint.id, a=a.id, b=b.id)
        return constraint

    def perpendicular(self, a: Segment, b: Segment) -> Constraint:
        constraint = self._add_constraint(
            "perpendicular", self.solver.constrain_perpendicular(a.handle, b.handle), (a, b)
        )
        self._log.add("perpendicular", id=constraint.id, a=a.id, b=b.id)
        return constraint

    def equal(self, a: Segment, b: Segment) -> Constraint:
        constraint = self._add_constraint(
            "equal", self.solver.constrain_equal(a.handle, b.handle), (a, b)
        )
        self._log.add("equal", id=constraint.id, a=a.id, b=b.id)
        return constraint

    def anchor(self, point: Point) -> Constraint:
        """Закрепить точку — иначе эскиз плавает целиком."""
        constraint = self._add_constraint(
            "anchor", self.solver.constrain_anchor(point.handle), (point,)
        )
        self._log.add("anchor", id=constraint.id, point=point.id)
        return constraint

    def point_on_line(self, point: Point, segment: Segment) -> Constraint:
        constraint = self._add_constraint(
            "point_on_line",
            self.solver.constrain_point_on_line(point.handle, segment.handle),
            (point, segment),
        )
        self._log.add("point_on_line", id=constraint.id, point=point.id, segment=segment.id)
        return constraint

    def point_on_curve(self, point: Point, segment: Segment) -> Constraint:
        constraint = self._add_constraint(
            "point_on_curve",
            self.solver.constrain_point_on_curve(point.handle, segment.handle),
            (point, segment),
        )
        self._log.add("point_on_curve", id=constraint.id, point=point.id, segment=segment.id)
        return constraint

    def midpoint(self, point: Point, segment: Segment) -> Constraint:
        constraint = self._add_constraint(
            "midpoint",
            self.solver.constrain_midpoint(point.handle, segment.handle),
            (point, segment),
        )
        self._log.add("midpoint", id=constraint.id, point=point.id, segment=segment.id)
        return constraint

    def tangent(self, a: Segment, b: Segment, at_end: bool | None = None) -> Constraint:
        """Касание. Каким концом дуга примыкает, выясняется по общим точкам.

        Указывать это вручную — верный способ ошибиться: конец дуги зависит
        от направления её обхода, а оно выбирается при построении и на
        экране никак не видно.
        """
        if at_end is None:
            at_end = self._touching_end(a, b)
        constraint = self._add_constraint(
            "tangent",
            self.solver.constrain_tangent(a.handle, b.handle, at_end),
            (a, b),
        )
        self._log.add("tangent", id=constraint.id, a=a.id, b=b.id, at_end=at_end)
        return constraint

    def _touching_end(self, a: Segment, b: Segment) -> bool:
        """Примыкают ли объекты концом дуги (а не её началом)."""
        curve = b if b.kind == "arc" else a
        other = a if curve is b else b
        if curve.kind != "arc":
            return False
        shared = {id(point) for point in other.ends}
        if curve.points[-1] is not None and id(curve.points[-1]) in shared:
            return True
        return False

    def symmetric(self, a: Point, b: Point, axis: Segment) -> Constraint:
        constraint = self._add_constraint(
            "symmetric",
            self.solver.constrain_symmetric(a.handle, b.handle, axis.handle),
            (a, b, axis),
        )
        self._log.add("symmetric", id=constraint.id, a=a.id, b=b.id, axis=axis.id)
        return constraint

    def concentric(self, a: Segment, b: Segment) -> Constraint:
        """Концентричность — это совпадение центров; отдельного ограничения в
        решателях нет, но в списке связей человек ищет именно её."""
        handle = self.solver.constrain_coincident(a.points[0].handle, b.points[0].handle)
        constraint = self._add_constraint("concentric", handle, (a, b))
        self._log.add("concentric", id=constraint.id, a=a.id, b=b.id)
        return constraint

    def equal_radius(self, a: Segment, b: Segment) -> Constraint:
        constraint = self._add_constraint(
            "equal_radius",
            self.solver.constrain_equal_radius(a.handle, b.handle),
            (a, b),
        )
        self._log.add("equal_radius", id=constraint.id, a=a.id, b=b.id)
        return constraint

    # --- размеры ---

    def _dimension_name(self, prefix: str) -> str:
        index = 1
        existing = {dimension.name for dimension in self._dimensions.values()}
        while f"{prefix}{index}" in existing:
            index += 1
        return f"{prefix}{index}"

    def _add_dimension(self, kind: str, name: str, handle, targets, value: float,
                       _id: int | None = None) -> Dimension:
        dimension_id = _id if _id is not None else self._claim_id()
        dimension = Dimension(dimension_id, kind, name, tuple(targets), value, handle)
        self._dimensions[dimension_id] = dimension
        return dimension

    def dimension(self, a: Point, b: Point, value: float, name: str = "") -> Dimension:
        name = name or self._dimension_name("Размер")
        dimension = self._add_dimension(
            "distance", name, self.solver.add_dimension(a.handle, b.handle, value), (a, b), value
        )
        self._log.add("dimension", id=dimension.id, a=a.id, b=b.id, value=value, name=name)
        return dimension

    def dimension_axis(self, a: Point, b: Point, value: float, axis: str,
                       name: str = "") -> Dimension:
        """Размер строго по оси: только X или только Y.

        Прямого ограничения «расстояние по оси» у решателя нет, поэтому
        строится вспомогательный прямой угол: точка-помощник лежит на
        одной высоте с первой и на одной вертикали со второй, а мерится
        катет. Так же это работает и во ВТОРОМ решателе, где отдельного
        осевого расстояния тоже нет, — размер остаётся переносимым.
        """
        if axis not in ("x", "y"):
            raise SketchError(f"{self.name}: ось размера — «x» или «y»")
        first, second = self.coordinates(a), self.coordinates(b)
        corner = ((second[0], first[1]) if axis == "x" else (first[0], second[1]))
        helper = self.point(*corner, construction=True)
        along = self.line(a, helper, construction=True)
        across = self.line(helper, b, construction=True)
        if axis == "x":
            self.horizontal(along)
            self.vertical(across)
        else:
            self.vertical(along)
            self.horizontal(across)
        title = name or self._dimension_name(
            "Размер по X" if axis == "x" else "Размер по Y")
        return self.dimension(a, helper, abs(value), title)

    def dimension_to_line(self, point: Point, segment: Segment, value: float,
                          name: str = "") -> Dimension:
        """Расстояние от точки до прямой — по перпендикуляру.

        Отдельного ограничения «точка — прямая» у решателя нет, и здесь
        делается то же, что у осевого размера: строится помощник НА
        прямой, отрезок к нему объявляется перпендикулярным прямой, и
        мерится он. Так размер остаётся переносимым — во втором решателе
        такого ограничения тоже нет.

        Помощник ставится в основание перпендикуляра, а не в конец прямой:
        решателю нужна не любая точка прямой, а та, от которой считают, —
        иначе он вправе увести её куда угодно вдоль прямой, и размер
        перестанет быть расстоянием.
        """
        if segment.kind != "line":
            raise SketchError(
                f"{self.name}: расстояние до кривой не ставится — "
                f"выберите прямой отрезок")
        first, second = (self.coordinates(end) for end in segment.ends)
        if geom2d.distance(first, second) < 1e-9:
            raise SketchError(f"{self.name}: отрезок выродился в точку")
        foot, _ = geom2d.project_on_line(self.coordinates(point), first, second)
        helper = self.point(*foot, construction=True)
        self.point_on_line(helper, segment)
        leg = self.line(helper, point, construction=True)
        self.perpendicular(leg, segment)
        title = name or self._dimension_name("Расстояние")
        return self.dimension(helper, point, abs(value), title)

    def radius(self, segment: Segment, value: float, name: str = "") -> Dimension:
        name = name or self._dimension_name("Радиус")
        dimension = self._add_dimension(
            "radius", name, self.solver.add_radius_dimension(segment.handle, value),
            (segment,), value,
        )
        self._log.add("radius", id=dimension.id, segment=segment.id, value=value, name=name)
        return dimension

    def diameter(self, segment: Segment, value: float, name: str = "") -> Dimension:
        name = name or self._dimension_name("Диаметр")
        dimension = self._add_dimension(
            "diameter", name, self.solver.add_diameter_dimension(segment.handle, value),
            (segment,), value,
        )
        self._log.add("diameter", id=dimension.id, segment=segment.id, value=value, name=name)
        return dimension

    def angle(self, a: Segment, b: Segment, value_deg: float, name: str = "") -> Dimension:
        name = name or self._dimension_name("Угол")
        dimension = self._add_dimension(
            "angle", name, self.solver.add_angle_dimension(a.handle, b.handle, value_deg),
            (a, b), value_deg,
        )
        self._log.add("angle", id=dimension.id, a=a.id, b=b.id, value=value_deg, name=name)
        return dimension

    def set_dimension(self, name_or_dimension, value: float) -> None:
        """Изменить управляющий размер — суть параметрической модели."""
        dimension = name_or_dimension
        if isinstance(name_or_dimension, str):
            dimension = self.dimension_by_name(name_or_dimension)
            if dimension is None:
                raise SketchError(f"{self.name}: нет размера {name_or_dimension!r}")
        self.solver.set_dimension(dimension.handle, value)
        dimension.value = value
        # Журнал хранит ТЕКУЩЕЕ значение: при загрузке эскиз должен
        # восстановиться таким, каким его сохранили, а не исходным.
        for entry in self._log.entries:
            if entry.get("id") == dimension.id:
                entry["value"] = value

    # --- удаление ---

    def delete(self, entities) -> int:
        """Удалить объекты вместе со всем, что на них опиралось.

        Возвращает число выброшенных записей журнала. Решатели не умеют
        убирать добавленное, поэтому система пересобирается заново — но
        объекты, которые остались, сохраняют свои идентификаторы и сами
        остаются теми же, так что ссылки из интерфейса не рвутся.
        """
        doomed = {entity.id for entity in entities}
        # Точка тянет за собой отрезки и дуги, которые на неё опираются:
        # без неё они не определены.
        changed = True
        while changed:
            changed = False
            for segment in self._segments.values():
                if segment.id in doomed:
                    continue
                if any(point.id in doomed for point in segment.points):
                    doomed.add(segment.id)
                    changed = True
        doomed |= self._orphan_points(doomed)
        before = len(self._log.entries)
        self._log.drop_referencing(doomed)
        removed = before - len(self._log.entries)
        for registry in (self._points, self._segments, self._constraints, self._dimensions):
            for entity_id in list(registry):
                if entity_id in doomed:
                    del registry[entity_id]
        self._rebuild()
        return removed

    def _orphan_points(self, doomed: set) -> set:
        """Концы удаляемых объектов, которые больше никому не нужны.

        Удалив отрезок, надо убрать и его концы — иначе на чертеже остаются
        точки, которые ничего не держат, цепляют привязку и мешают выбрать
        то, что рядом. Но убрать можно не все:

        * точка, общая с уцелевшим объектом (угол прямоугольника, из
          которого удалили одну сторону), остаётся — на ней держится сосед;
        * одиночная точка, поставленная инструментом «Точка», не является
          концом ничего и в этот набор не попадает вовсе. Именно это и
          отличает её от осиротевшего конца — отдельного признака не нужно.
        """
        dying = {
            point.id
            for segment_id in doomed
            for segment in (self._segments.get(segment_id),)
            if segment is not None
            for point in segment.points
        }
        surviving = {
            point.id
            for segment in self._segments.values()
            if segment.id not in doomed
            for point in segment.points
        }
        return {point_id for point_id in dying - surviving
                if point_id not in doomed}

    def delete_constraint(self, constraint) -> None:
        """Снять связь или размер, не трогая геометрию."""
        self._log.drop_referencing({constraint.id})
        self._constraints.pop(constraint.id, None)
        self._dimensions.pop(constraint.id, None)
        self._rebuild()

    # --- пересборка и сохранение ---

    def _snapshot(self) -> dict[int, tuple[float, float]]:
        result = {}
        for point in self._points.values():
            try:
                result[point.id] = self.coordinates(point)
            except Exception:  # noqa: BLE001 — точка могла остаться без дескриптора
                pass
        return result

    def _rebuild(self) -> Solution:
        """Собрать решатель заново по журналу, сохранив объекты и их положение."""
        coordinates = self._snapshot()
        self.solver = make_solver(self._solver_name)
        entries = list(self._log.entries)
        self._log.entries = []
        points, segments = dict(self._points), dict(self._segments)
        constraints, dimensions = dict(self._constraints), dict(self._dimensions)
        self._points, self._segments = {}, {}
        self._constraints, self._dimensions = {}, {}
        self._replay(entries, coordinates, points, segments, constraints, dimensions)
        return self.solve()

    def _replay(self, entries, coordinates, points=None, segments=None,
                constraints=None, dimensions=None) -> None:
        """Проиграть журнал. Если переданы прежние объекты, их дескрипторы
        подменяются на новые — тогда ссылки, которые держит интерфейс,
        продолжают указывать на те же объекты эскиза."""
        old_points = points or {}
        old_segments = segments or {}
        old_constraints = constraints or {}
        old_dimensions = dimensions or {}

        def adopt(registry, old, created):
            previous = old.get(created.id)
            if previous is None:
                return created
            previous.handle = created.handle
            if isinstance(previous, Segment):
                previous.points = created.points
            registry[created.id] = previous
            return previous

        for entry in entries:
            operation = entry["op"]
            arguments = {key: value for key, value in entry.items() if key != "op"}
            entity_id = arguments.pop("id", None)
            self._next_id = max(self._next_id, (entity_id or 0) + 1)
            handler = _REPLAY.get(operation)
            if handler is None:
                raise SketchError(f"{self.name}: неизвестная операция {operation!r}")
            if operation == "point" and entity_id in coordinates:
                # Точки создаются в сохранённом положении, а не в исходном:
                # иначе решатель может сойтись к другой ветви решения.
                arguments["x"], arguments["y"] = coordinates[entity_id]
            created = handler(self, arguments, entity_id)
            if isinstance(created, Point):
                adopt(self._points, old_points, created)
            elif isinstance(created, Segment):
                adopt(self._segments, old_segments, created)
            elif isinstance(created, Constraint):
                adopt(self._constraints, old_constraints, created)
            elif isinstance(created, Dimension):
                adopt(self._dimensions, old_dimensions, created)

    def to_dict(self) -> dict:
        """Эскиз как данные: журнал построения плюс текущие координаты.

        Координаты пишутся как подсказка решателю при загрузке: он всё равно
        пересчитает их по ограничениям, но с близкого старта сойдётся вернее.
        """
        return {
            "version": FORMAT_VERSION,
            "name": self.name,
            "plane": self.plane.to_dict(),
            "support": dict(self.support) if self.support else None,
            "log": [dict(entry) for entry in self._log.entries],
            "coordinates": {
                str(point_id): list(value)
                for point_id, value in self._snapshot().items()
            },
            # Положение выносок хранится отдельно от журнала построения: оно
            # ни на что не влияет, но разложенные вручную подписи — работа,
            # которую обидно терять при сохранении.
            "annotations": {
                str(dimension.id): list(dimension.offset)
                for dimension in self._dimensions.values()
                if tuple(dimension.offset) != (0.0, 0.0)
            },
        }

    def restore(self, data: dict) -> Solution:
        """Вернуть эскиз к сохранённому состоянию, НЕ создавая новый объект.

        Именно на этом держится отмена. Операция и дерево построения хранят
        ссылку на эскиз; подмена его новым объектом означала бы, что после
        первой же отмены операция считает по старому эскизу, а на экране
        показан новый — расхождение, которое обнаружится только при
        пересчёте детали.
        """
        self.name = data.get("name", self.name)
        self.plane = Plane.from_dict(data.get("plane"))
        support = data.get("support")
        self.support = dict(support) if support else None
        self.solver = make_solver(self._solver_name)
        self._points.clear()
        self._segments.clear()
        self._constraints.clear()
        self._dimensions.clear()
        self._log.entries = []
        self._next_id = 1
        entries = list(data.get("log", []))
        raw = data.get("coordinates") or {}
        self._replay(entries, {int(k): tuple(v) for k, v in raw.items()})
        for key, value in (data.get("annotations") or {}).items():
            dimension = self._dimensions.get(int(key))
            if dimension is not None:
                dimension.offset = tuple(value)
        return self.solve()

    @classmethod
    def from_dict(cls, data: dict, solver: SketchSolver | None = None) -> "Sketch":
        """Восстановить эскиз, проиграв журнал построения заново."""
        sketch = cls(data.get("name", "Sketch"), solver,
                     plane=Plane.from_dict(data.get("plane")))
        support = data.get("support")
        sketch.support = dict(support) if support else None
        entries = list(data.get("log", []))
        raw = data.get("coordinates") or {}
        if data.get("version", 1) < FORMAT_VERSION:
            entries, raw = _upgrade_v1(entries, data.get("coordinates") or [])
        coordinates = {int(key): tuple(value) for key, value in raw.items()}
        sketch._replay(entries, coordinates)
        for key, value in (data.get("annotations") or {}).items():
            dimension = sketch._dimensions.get(int(key))
            if dimension is not None:
                dimension.offset = tuple(value)
        return sketch

    # --- решение ---

    def solve(self) -> Solution:
        return self.solver.solve()

    def status(self) -> SketchStatus:
        solution = self.solve()
        return SketchStatus(solution.ok, solution.dof, solution.message)

    def coordinates(self, point: Point) -> tuple[float, float]:
        return self.solver.point_coordinates(point.handle)

    def circle_geometry(self, segment: Segment) -> tuple[tuple[float, float], float]:
        return self.solver.circle_geometry(segment.handle)

    def arc_geometry(self, segment: Segment) -> ArcGeometry:
        return self.solver.arc_geometry(segment.handle)

    def ellipse_geometry(self, segment: Segment) -> EllipseGeometry:
        if segment.kind == "ellipse_arc":
            return self.solver.ellipse_arc_geometry(segment.handle)
        return self.solver.ellipse_geometry(segment.handle)

    def spline_geometry(self, segment: Segment) -> SplineGeometry:
        return self.solver.spline_geometry(segment.handle)

    def move(self, point: Point, x: float, y: float) -> Solution:
        """Перетащить точку. На определённом эскизе она не сдвинется — её
        держат размеры, и это правильное поведение, а не отказ."""
        return self.solver.move_point(point.handle, x, y)

    def place(self, pairs) -> Solution:
        """Передвинуть сразу несколько точек. ``pairs`` — ``(точка, x, y)``.

        Нужно там, где двигается ГРУППА: перенос, поворот, масштаб. По
        одной точке через ``move`` не годится — каждый вызов решает эскиз
        целиком, и связи возвращают на место те точки, до которых очередь
        ещё не дошла.
        """
        return self.solver.place_points(
            [(point.handle, float(x), float(y)) for point, x, y in pairs])

    # --- выдача в ядро ---

    def regions(self) -> list:
        """Атомарные области эскиза. Пересчитываются при каждом обращении.

        Кэша нет намеренно: области зависят от решённого положения каждой
        точки, а оно меняется от любой правки размера. Устаревший кэш здесь
        опаснее лишнего пересчёта — по нему строится тело.
        """
        from . import regions as regions_module

        return regions_module.build(self)

    def region_face(self, references: list):
        """Грань по устойчивым ссылкам на области.

        Пропавшая область — отказ, а не молчаливое построение по
        оставшимся: «выдавить эту область» и «выдавить то, что от неё
        осталось» — разные вещи, и вторую человек не заказывал.
        """
        from . import regions as regions_module
        from .profile import build_regions

        found = regions_module.build(self)
        chosen = []
        for reference in references:
            region = regions_module.find(found, reference)
            if region is None:
                raise SketchError(
                    f"{self.name}: выбранная область больше не существует — "
                    f"эскиз изменился так, что она пропала или разделилась"
                )
            chosen.append(region)
        return build_regions(self, chosen)

    def to_occt_face(self):
        from .profile import build_face

        return build_face(self)

    def to_occt_wire(self):
        from .profile import build_wire

        return build_wire(self)

    def to_occt_solid(self, height: float):
        """Эскиз → тело. Удобство для проверок, не рабочий путь.

        Считает ОБЩИЙ слой операций, а не строит призму сам. Второй путь
        выдавливания рядом с основным — это два источника истины на одну
        операцию (`docs/08_ENGINE_BACKEND.md`, §16.2): расходятся допуски,
        направление и диагностика, а обнаруживается это на чужой детали.
        """
        from ..operations import BLIND, Extrusion, build_tool

        return build_tool(self.to_occt_face(), Extrusion(length=height, end=BLIND))


def _circumcenter(a, b, c):
    """Центр окружности через три точки. None, если они на одной прямой."""
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return None
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    return (
        (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d,
        (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d,
    )


def _upgrade_v1(entries, coordinates):
    """Журнал первой версии: ссылки по местам в списке, а не по номерам.

    Удалений в первой версии не было, поэтому порядковый номер и есть
    идентификатор — пересчёт однозначен.
    """
    point_ids: list[int] = []
    segment_ids: list[int] = []
    next_id = 1
    upgraded = []
    for entry in entries:
        entry = dict(entry)
        operation = entry.get("op")
        entry["id"] = next_id
        if operation == "point":
            point_ids.append(next_id)
        elif operation in ("line", "circle"):
            segment_ids.append(next_id)
        for key in ("a", "b", "point", "start", "end", "center"):
            if key not in entry:
                continue
            index = entry[key]
            if operation in ("line", "circle") or key in ("point",):
                entry[key] = point_ids[index]
            elif operation in ("coincident", "anchor", "dimension"):
                entry[key] = point_ids[index]
            else:
                entry[key] = segment_ids[index]
        if "segment" in entry:
            entry["segment"] = segment_ids[entry["segment"]]
        next_id += 1
        upgraded.append(entry)
    return upgraded, {
        str(point_ids[index]): value
        for index, value in enumerate(coordinates)
        if index < len(point_ids)
    }


def _point_of(sketch: Sketch, entity_id: int) -> Point:
    point = sketch._points.get(entity_id)
    if point is None:
        raise SketchError(f"{sketch.name}: нет точки {entity_id}")
    return point


def _segment_of(sketch: Sketch, entity_id: int) -> Segment:
    segment = sketch._segments.get(entity_id)
    if segment is None:
        raise SketchError(f"{sketch.name}: нет объекта {entity_id}")
    return segment


# Таблица проигрывания журнала. Держится рядом с записью в журнал: любая
# новая операция обязана появиться в обоих местах, иначе файл перестанет
# открываться — и это выяснится сразу, а не у пользователя.
_REPLAY = {
    "point": lambda s, a, i: s.point(a["x"], a["y"], a.get("name", ""),
                                     a.get("construction", False), _id=i,
                                     external=a.get("external", "")),
    "line": lambda s, a, i: s.line(_point_of(s, a["start"]), _point_of(s, a["end"]),
                                   a.get("name", ""), a.get("construction", False),
                                   _id=i, external=a.get("external", "")),
    "arc": lambda s, a, i: s.arc(_point_of(s, a["center"]), _point_of(s, a["start"]),
                                 _point_of(s, a["end"]), a.get("name", ""),
                                 a.get("construction", False), _id=i),
    "circle": lambda s, a, i: s.circle(_point_of(s, a["center"]), a["radius"],
                                       a.get("name", ""), a.get("construction", False), _id=i),
    "spline": lambda s, a, i: s.spline(
        [_point_of(s, value) for value in a["poles"]], a["knots"],
        a["mults"], a["degree"], a.get("name", ""),
        a.get("construction", False), _id=i),
    "hyperbola_arc": lambda s, a, i: s.hyperbola_arc(
        s._points[a["center"]], s._points[a["focus"]], a["minor"],
        a["start_angle"], a["end_angle"],
        s._points[a["start"]], s._points[a["end"]],
        a.get("name", ""), a.get("construction", False), _id=i),
    "parabola_arc": lambda s, a, i: s.parabola_arc(
        s._points[a["vertex"]], s._points[a["focus"]],
        a["start_angle"], a["end_angle"],
        s._points[a["start"]], s._points[a["end"]],
        a.get("name", ""), a.get("construction", False), _id=i),
    "ellipse_arc": lambda s, a, i: s.ellipse_arc(
        _point_of(s, a["center"]), _point_of(s, a["focus"]), a["minor"],
        a["start_angle"], a["end_angle"], _point_of(s, a["start"]),
        _point_of(s, a["end"]), a.get("name", ""),
        a.get("construction", False), _id=i),
    "ellipse": lambda s, a, i: s.ellipse(
        _point_of(s, a["center"]), _point_of(s, a["focus"]), a["minor"],
        a.get("name", ""), a.get("construction", False), _id=i),
    "coincident": lambda s, a, i: _replay_constraint(
        s, i, "coincident", s.coincident, _point_of(s, a["a"]), _point_of(s, a["b"])),
    "horizontal": lambda s, a, i: _replay_constraint(
        s, i, "horizontal", s.horizontal, _segment_of(s, a["segment"])),
    "vertical": lambda s, a, i: _replay_constraint(
        s, i, "vertical", s.vertical, _segment_of(s, a["segment"])),
    "parallel": lambda s, a, i: _replay_constraint(
        s, i, "parallel", s.parallel, _segment_of(s, a["a"]), _segment_of(s, a["b"])),
    "perpendicular": lambda s, a, i: _replay_constraint(
        s, i, "perpendicular", s.perpendicular, _segment_of(s, a["a"]), _segment_of(s, a["b"])),
    "equal": lambda s, a, i: _replay_constraint(
        s, i, "equal", s.equal, _segment_of(s, a["a"]), _segment_of(s, a["b"])),
    "anchor": lambda s, a, i: _replay_constraint(
        s, i, "anchor", s.anchor, _point_of(s, a["point"])),
    "point_on_line": lambda s, a, i: _replay_constraint(
        s, i, "point_on_line", s.point_on_line,
        _point_of(s, a["point"]), _segment_of(s, a["segment"])),
    "point_on_curve": lambda s, a, i: _replay_constraint(
        s, i, "point_on_curve", s.point_on_curve,
        _point_of(s, a["point"]), _segment_of(s, a["segment"])),
    "midpoint": lambda s, a, i: _replay_constraint(
        s, i, "midpoint", s.midpoint,
        _point_of(s, a["point"]), _segment_of(s, a["segment"])),
    "tangent": lambda s, a, i: _replay_constraint(
        s, i, "tangent", s.tangent, _segment_of(s, a["a"]), _segment_of(s, a["b"]),
        a.get("at_end", False)),
    "symmetric": lambda s, a, i: _replay_constraint(
        s, i, "symmetric", s.symmetric, _point_of(s, a["a"]), _point_of(s, a["b"]),
        _segment_of(s, a["axis"])),
    "concentric": lambda s, a, i: _replay_constraint(
        s, i, "concentric", s.concentric, _segment_of(s, a["a"]), _segment_of(s, a["b"])),
    "equal_radius": lambda s, a, i: _replay_constraint(
        s, i, "equal_radius", s.equal_radius, _segment_of(s, a["a"]), _segment_of(s, a["b"])),
    "dimension": lambda s, a, i: _replay_dimension(
        s, i, s.dimension, _point_of(s, a["a"]), _point_of(s, a["b"]), a["value"], a["name"]),
    "radius": lambda s, a, i: _replay_dimension(
        s, i, s.radius, _segment_of(s, a["segment"]), a["value"], a["name"]),
    "diameter": lambda s, a, i: _replay_dimension(
        s, i, s.diameter, _segment_of(s, a["segment"]), a["value"], a["name"]),
    "angle": lambda s, a, i: _replay_dimension(
        s, i, s.angle, _segment_of(s, a["a"]), _segment_of(s, a["b"]), a["value"], a["name"]),
}


def _replay_constraint(sketch: Sketch, entity_id, kind, method, *arguments):
    """Проиграть связь, сохранив её прежний идентификатор.

    Идентификаторы связей обязаны совпадать до и после пересборки: на них
    ссылается журнал, и разъехавшись однажды, они разъедутся навсегда.
    """
    result = method(*arguments)
    return _renumber(sketch, sketch._constraints, result, entity_id)


def _replay_dimension(sketch: Sketch, entity_id, method, *arguments):
    result = method(*arguments)
    return _renumber(sketch, sketch._dimensions, result, entity_id)


def _renumber(sketch: Sketch, registry, entity, entity_id):
    if entity_id is None or entity.id == entity_id:
        return entity
    registry.pop(entity.id, None)
    entity.id = entity_id
    registry[entity_id] = entity
    sketch._log.entries[-1]["id"] = entity_id
    return entity
