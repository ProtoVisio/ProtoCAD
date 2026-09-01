"""Интерфейс решателя ограничений — точка смены реализации.

Решатель выбирается не по вкусу, а по лицензии:

* **py-slvs** (SolveSpace) — GPL-3. Связывает GPL-3 всё приложение.
* **planegcs** (PlaneGCS из FreeCAD) — LGPL-2.1+. Лицензия мягче, решатель
  тот же, что в Sketcher FreeCAD. Требует Python ≥ 3.12.

Чтобы этот выбор не пришлось делать сейчас и навсегда, геометрия эскиза
хранится в структурах ProtoCAD, а решателю передаются только параметры и
связи. Смена реализации — это замена одного класса, а не переписывание
эскизов и модели.

Дуги и окружности возвращаются обёрнутыми в :class:`CurveHandle`. Причина
техническая, но существенная: в обеих библиотеках радиус — это отдельный
параметр, а не свойство кривой, и размер накладывается именно на параметр.
Держать эту связь в самом дескрипторе надёжнее, чем в постороннем словаре,
где она рассыпается при удалении объектов.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SolverInfo:
    """Чем решаем и на каких условиях. Лицензия — часть характеристики."""

    name: str
    version: str
    license: str
    copyleft_scope: str  # что лицензия распространяет на приложение
    available: bool
    reason: str = ""


@dataclass
class Solution:
    ok: bool
    dof: int
    message: str
    failed: list = field(default_factory=list)

    @property
    def fully_constrained(self) -> bool:
        return self.ok and self.dof == 0


@dataclass
class CurveHandle:
    """Дескриптор окружности или дуги вместе с её параметрами.

    ``kind`` — ``"circle"`` или ``"arc"``. Дескрипторы в обеих библиотеках
    целочисленные, и окружность №3 неотличима от дуги №3 — вид приходится
    хранить рядом.
    """

    kind: str
    id: Any
    radius: Any = None
    start_angle: Any = None
    end_angle: Any = None
    # Что реализации нужно помнить о кривой сверх общего набора: у SolveSpace
    # радиус дуги не параметр, а расстояние до начальной точки, и без самих
    # точек геометрию обратно не прочитать.
    extra: dict = field(default_factory=dict)


@dataclass
class ArcGeometry:
    center: tuple[float, float]
    radius: float
    start_angle: float
    end_angle: float


@dataclass
class HyperbolaGeometry:
    """Гипербола так, как её держит решатель: центр, ФОКУС и малая полуось.

    Устроена так же, как эллипс, и не случайно: у обоих фокус — обычная
    точка, поэтому поворот и большая полуось получаются перетаскиванием.
    Большая выводится: ``a = √(c² − b²)``, где ``c`` — расстояние до фокуса.

    Параметр — ГИПЕРБОЛИЧЕСКИЙ: точка при ``t`` это ``(a·ch t, b·sh t)`` в
    системе центра. Установлено опытом и на решателе, и на ядре: у обоих он
    одинаков, поэтому через границу параметр идёт как есть, без перевода.
    """

    center: tuple[float, float]
    focus: tuple[float, float]
    minor: float
    start_angle: float = 0.0
    end_angle: float = 0.0

    @property
    def distance(self) -> float:
        """Расстояние от центра до фокуса."""
        return ((self.focus[0] - self.center[0]) ** 2
                + (self.focus[1] - self.center[1]) ** 2) ** 0.5

    @property
    def major(self) -> float:
        """Действительная полуось: ``√(c² − b²)``."""
        gap = self.distance ** 2 - self.minor ** 2
        return gap ** 0.5 if gap > 0.0 else 0.0

    @property
    def rotation(self) -> float:
        import math

        return math.atan2(self.focus[1] - self.center[1],
                          self.focus[0] - self.center[0])

    def point_at(self, t: float) -> tuple:
        import math

        along = self.major * math.cosh(t)
        across = self.minor * math.sinh(t)
        turn = self.rotation
        cos, sin = math.cos(turn), math.sin(turn)
        return (self.center[0] + along * cos - across * sin,
                self.center[1] + along * sin + across * cos)

    def tangent_at(self, t: float) -> tuple:
        import math

        along = self.major * math.sinh(t)
        across = self.minor * math.cosh(t)
        turn = self.rotation
        cos, sin = math.cos(turn), math.sin(turn)
        return (along * cos - across * sin, along * sin + across * cos)


@dataclass
class ParabolaGeometry:
    """Парабола так, как её держит решатель: ВЕРШИНА и ФОКУС.

    Больше ничего и не нужно: расстояние от вершины до фокуса задаёт
    раскрытие, а направление на фокус — ось. Оба — обычные точки эскиза,
    поэтому и раскрытие, и поворот получаются перетаскиванием.

    Параметр кривой — МЕСТНАЯ КООРДИНАТА Y: точка при ``t`` это
    ``(t² / 4p, t)`` в системе вершины. Установлено опытом на самом
    решателе; выводить это по памяти нельзя — у параболы в ходу несколько
    разных параметризаций.
    """

    vertex: tuple[float, float]
    focus: tuple[float, float]
    start_angle: float = 0.0
    end_angle: float = 0.0

    @property
    def focal(self) -> float:
        """Расстояние от вершины до фокуса. Оно же — раскрытие."""
        return ((self.focus[0] - self.vertex[0]) ** 2
                + (self.focus[1] - self.vertex[1]) ** 2) ** 0.5

    @property
    def rotation(self) -> float:
        """Направление оси: от вершины к фокусу."""
        import math

        return math.atan2(self.focus[1] - self.vertex[1],
                          self.focus[0] - self.vertex[0])

    def point_at(self, t: float) -> tuple:
        """Точка кривой по параметру."""
        import math

        focal = self.focal or 1e-9
        along, across = t * t / (4.0 * focal), t
        turn = self.rotation
        cos, sin = math.cos(turn), math.sin(turn)
        return (self.vertex[0] + along * cos - across * sin,
                self.vertex[1] + along * sin + across * cos)

    def tangent_at(self, t: float) -> tuple:
        """Производная по параметру."""
        import math

        focal = self.focal or 1e-9
        along, across = t / (2.0 * focal), 1.0
        turn = self.rotation
        cos, sin = math.cos(turn), math.sin(turn)
        return (along * cos - across * sin, along * sin + across * cos)


@dataclass
class EllipseGeometry:
    """Эллипс так, как его держит решатель: центр, ФОКУС и малая полуось.

    Именно так он задан и в PlaneGCS, и в Sketcher FreeCAD, и это не
    прихоть: фокус — обычная точка, поэтому поворот и большая полуось
    получаются перетаскиванием и связями, без отдельных величин.

    Большая полуось выводится: ``a = √(b² + c²)``, где ``c`` — расстояние
    от центра до фокуса. Угол наклона — направление на фокус.
    """

    center: tuple[float, float]
    focus: tuple[float, float]
    minor: float
    #: Кусок эллипса: параметрические углы начала и конца. У целого
    #: эллипса конец равен началу плюс полный оборот.
    start_angle: float = 0.0
    end_angle: float = 2.0 * math.pi

    @property
    def whole(self) -> bool:
        return abs((self.end_angle - self.start_angle) % (2.0 * math.pi)) < 1e-12

    @property
    def distance(self) -> float:
        return math.hypot(self.focus[0] - self.center[0],
                          self.focus[1] - self.center[1])

    @property
    def major(self) -> float:
        return math.hypot(self.minor, self.distance)

    @property
    def rotation(self) -> float:
        """Наклон большой оси, радианы. У круглого эллипса — ноль."""
        if self.distance < 1e-12:
            return 0.0
        return math.atan2(self.focus[1] - self.center[1],
                          self.focus[0] - self.center[0])


@dataclass
class SplineGeometry:
    """Сплайн так, как его держат и решатель, и ядро.

    Полюсы — точки эскиза, поэтому двигаются мышью и держатся связями.
    Узлы, кратности и степень — числа: у них нет собственного положения,
    и делать из них объекты значило бы завести то, что нельзя показать.
    """

    poles: tuple
    knots: tuple
    mults: tuple
    degree: int


class SketchSolver(ABC):
    """Что обязан уметь решатель, чтобы ProtoCAD на нём работал.

    Намеренно узкий набор: всё, что можно выразить через эти операции,
    выражается в слое эскиза, а не в реализации решателя.
    """

    @classmethod
    @abstractmethod
    def info(cls) -> SolverInfo:
        """Сведения о реализации, включая лицензию и доступность."""

    # --- построение ---

    @abstractmethod
    def add_point(self, x: float, y: float) -> Any:
        """Точка на плоскости эскиза."""

    @abstractmethod
    def add_line(self, start: Any, end: Any) -> Any:
        """Отрезок между двумя точками."""

    @abstractmethod
    def add_circle(self, center: Any, radius: float) -> CurveHandle:
        """Окружность. Радиус — свободный параметр: без размера у окружности
        обязана оставаться степень свободы."""

    @abstractmethod
    def add_arc(self, center: Any, start: Any, end: Any) -> CurveHandle:
        """Дуга по центру и двум точкам, обход против часовой стрелки.

        Точки — общие с примыкающими отрезками, поэтому стыковка контура не
        требует отдельных совпадений.
        """

    # --- ограничения ---

    @abstractmethod
    def constrain_coincident(self, point_a: Any, point_b: Any) -> Any: ...

    @abstractmethod
    def constrain_horizontal(self, line: Any) -> Any: ...

    @abstractmethod
    def constrain_vertical(self, line: Any) -> Any: ...

    @abstractmethod
    def constrain_parallel(self, line_a: Any, line_b: Any) -> Any: ...

    @abstractmethod
    def constrain_perpendicular(self, line_a: Any, line_b: Any) -> Any: ...

    @abstractmethod
    def constrain_equal(self, line_a: Any, line_b: Any) -> Any: ...

    @abstractmethod
    def constrain_anchor(self, point: Any) -> Any:
        """Закрепить точку: без этого эскиз плавает целиком."""

    @abstractmethod
    def constrain_point_on_line(self, point: Any, line: Any) -> Any: ...

    @abstractmethod
    def constrain_point_on_curve(self, point: Any, curve: CurveHandle) -> Any: ...

    @abstractmethod
    def constrain_midpoint(self, point: Any, line: Any) -> Any: ...

    @abstractmethod
    def constrain_tangent(self, a: Any, b: Any, at_end: bool = False) -> Any:
        """Касание. Допустимые пары: отрезок–кривая и кривая–кривая.

        ``at_end`` — к какому концу дуги примыкает отрезок. PlaneGCS
        определяет это сам, SolveSpace требует указания явно; слой эскиза
        знает ответ и передаёт его, а не гадает.
        """

    @abstractmethod
    def constrain_symmetric(self, point_a: Any, point_b: Any, line: Any) -> Any: ...

    @abstractmethod
    def constrain_equal_radius(self, a: CurveHandle, b: CurveHandle) -> Any: ...

    # --- размеры ---

    @abstractmethod
    def add_dimension(self, point_a: Any, point_b: Any, value: float) -> Any:
        """Управляющий размер между точками."""

    @abstractmethod
    def add_radius_dimension(self, curve: CurveHandle, value: float) -> Any: ...

    @abstractmethod
    def add_diameter_dimension(self, curve: CurveHandle, value: float) -> Any: ...

    @abstractmethod
    def add_angle_dimension(self, line_a: Any, line_b: Any, value_deg: float) -> Any:
        """Угол между отрезками, в градусах на входе и на выходе."""

    @abstractmethod
    def set_dimension(self, handle: Any, value: float) -> None:
        """Изменить управляющий размер — суть параметрической модели."""

    # --- решение и чтение ---

    @abstractmethod
    def solve(self) -> Solution: ...

    @abstractmethod
    def point_coordinates(self, point: Any) -> tuple[float, float]: ...

    @abstractmethod
    def circle_geometry(self, curve: CurveHandle) -> tuple[tuple[float, float], float]:
        """Центр и радиус — то, что нужно для отрисовки и для выдачи в ядро."""

    @abstractmethod
    def arc_geometry(self, curve: CurveHandle) -> ArcGeometry: ...

    @abstractmethod
    def move_point(self, point: Any, x: float, y: float) -> Solution:
        """Перетащить точку и пересчитать остальное."""

    def add_spline(self, poles, knots, mults, degree: int) -> CurveHandle:
        """Сплайн по полюсам, узлам и кратностям.

        Не ``@abstractmethod`` по той же причине, что и эллипс: решатель,
        который сплайна не знает, обязан остаться рабочим и честно
        отказать.
        """
        raise NotImplementedError(f"{type(self).__name__} не умеет сплайн")

    def spline_geometry(self, curve: CurveHandle) -> "SplineGeometry":
        raise NotImplementedError(f"{type(self).__name__} не умеет сплайн")

    def add_ellipse(self, center: Any, focus: Any, minor: float) -> CurveHandle:
        """Эллипс по центру, фокусу и малой полуоси.

        Не ``@abstractmethod`` намеренно: решатель, который эллипса не
        знает, обязан остаться рабочим и честно отказать, а не перестать
        собираться. Проверочный решатель под GPL — как раз такой.
        """
        raise NotImplementedError(
            f"{type(self).__name__} не умеет эллипс")

    def ellipse_geometry(self, curve: CurveHandle) -> "EllipseGeometry":
        raise NotImplementedError(
            f"{type(self).__name__} не умеет эллипс")

    def add_ellipse_arc(self, center: Any, focus: Any, minor: float,
                        start_angle: float, end_angle: float,
                        start: Any, end: Any) -> CurveHandle:
        """Дуга эллипса. Концы — ТОЧКИ: их держат связи, как у дуги круга."""
        raise NotImplementedError(
            f"{type(self).__name__} не умеет дугу эллипса")

    def constrain_point_on_ellipse(self, point: Any, curve: CurveHandle):
        raise NotImplementedError(
            f"{type(self).__name__} не умеет эллипс")

    def place_points(self, pairs) -> Solution:
        """Передвинуть СРАЗУ несколько точек и решить один раз.

        ``pairs`` — последовательность ``(точка, x, y)``.

        Почему не циклом по ``move_point``: каждый его вызов решает эскиз
        целиком, и промежуточные решения тянут ещё не сдвинутые точки
        обратно. При переносе прямоугольника первый же угол уезжает один,
        связи возвращают его на место, и до второго угла дело доходит уже
        на испорченной расстановке.

        Реализация по умолчанию — именно тот самый цикл: она годится для
        решателя, у которого нет способа поставить точку без решения, и
        существует, чтобы такой решатель не пришлось переписывать. Основной
        (PlaneGCS) переопределяет её.
        """
        answer = None
        for point, x, y in pairs:
            answer = self.move_point(point, x, y)
        return answer if answer is not None else self.solve()


class SolverUnavailable(RuntimeError):
    """Реализация решателя не установлена или не подходит окружению."""


#: Реализации решателя, доступные ПРОДУКТУ. Здесь только PlaneGCS: он
#: LGPL-2.1+, производный от FreeCAD, и его допускает политика проекта.
#: py-slvs под GPL-3 сюда не входит и не является запасным вариантом —
#: одна такая зависимость связала бы GPL всё приложение. Проверки
#: подключают его сами через ``register_backend`` (см. tests/gpl_only).
_BACKENDS: dict = {}


def _production_backends() -> dict:
    if "planegcs" not in _BACKENDS:
        from . import planegcs_backend

        _BACKENDS["planegcs"] = planegcs_backend.PlaneGcsSolver
    return _BACKENDS


def register_backend(name: str, backend) -> None:
    """Добавить реализацию решателя.

    Нужен проверкам: второй решатель — единственный способ убедиться, что
    слой эскиза не сросся с первым. В поставке вызывать это некому и
    нечем: GPL-реализация в пакет не входит.
    """
    _production_backends()[name] = backend


def available_solvers() -> list[SolverInfo]:
    """Все зарегистрированные реализации с их состоянием."""
    return [backend.info() for backend in _production_backends().values()]


def make_solver(prefer: str = "") -> SketchSolver:
    """Создать решатель. ``prefer`` — имя реализации, иначе основной.

    Запасного варианта НЕТ намеренно. Раньше при недоступности PlaneGCS
    молча брался py-slvs, и приложение оказывалось связано GPL-3, ничем
    этого не показав. Теперь отсутствие решателя — отказ с объяснением.
    """
    backends = _production_backends()
    if prefer:
        backend = backends.get(prefer)
        if backend is None:
            raise SolverUnavailable(
                f"неизвестный решатель: {prefer!r}. "
                f"Известны: {', '.join(sorted(backends))}"
            )
        info = backend.info()
        if not info.available:
            raise SolverUnavailable(f"{prefer}: {info.reason}")
        return backend()

    backend = backends["planegcs"]
    info = backend.info()
    if info.available:
        return backend()
    raise SolverUnavailable(f"решатель эскизов недоступен: {info.reason}")
