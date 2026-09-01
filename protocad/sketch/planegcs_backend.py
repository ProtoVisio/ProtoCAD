"""Решатель на planegcs (PlaneGCS из FreeCAD). Лицензия LGPL-2.1+.

Предпочтительная реализация: тот же решатель, что работает в Sketcher
FreeCAD, но его лицензия не связывает приложение целиком — в отличие от
GPL-3 у SolveSpace.

Требует **Python ≥ 3.12**: колёса planegcs собраны под CPython 3.12 и 3.13.

Отличия API от SolveSpace, учтённые здесь:

* размер задаётся отдельным параметром (``add_param`` → ``p2p_distance``),
  и менять его надо через ``set_param``, а не пересозданием ограничения:
  ``set_p2p_distance`` добавляет НОВОЕ ограничение, а не правит существующее;
* ``fix_point`` принимает координаты, поэтому закрепление читает текущее
  положение точки;
* у дуги радиус и оба угла — самостоятельные параметры, а концевые точки
  привязываются к ним внутренними уравнениями; свободная дуга имеет пять
  степеней свободы, и это правильно;
* угол задаётся в радианах, наружу выдаём градусы;
* есть ``diagnose()`` с разбором на конфликтующие и избыточные ограничения —
  богаче, чем список отказов у SolveSpace.
"""

from __future__ import annotations

import math
import sys
from dataclasses import dataclass

from .solver import (
    ArcGeometry,
    CurveHandle,
    EllipseGeometry,
    HyperbolaGeometry,
    ParabolaGeometry,
    SketchSolver,
    SplineGeometry,
    Solution,
    SolverInfo,
    SolverUnavailable,
)

MIN_PYTHON = (3, 12)

STATUS_TEXT = {
    0: "решено",
    1: "сошлось",
    2: "решение не найдено",
    3: "решение найдено, но недопустимо",
}


@dataclass
class _Dimension:
    """Управляющий размер: ограничение плюс параметр, который им управляет."""

    tag: object
    param: object
    scale: float = 1.0  # множитель значения: диаметр и угол хранятся иначе


class PlaneGcsSolver(SketchSolver):
    """Обёртка PlaneGCS под интерфейс ProtoCAD."""

    @classmethod
    def info(cls) -> SolverInfo:
        version, available, reason = "", False, ""
        if sys.version_info < MIN_PYTHON:
            reason = (
                f"нужен Python ≥ {MIN_PYTHON[0]}.{MIN_PYTHON[1]}, "
                f"текущий {sys.version_info.major}.{sys.version_info.minor}"
            )
        else:
            try:
                import importlib.metadata as metadata

                import planegcs  # noqa: F401

                version = metadata.version("planegcs")
                available = True
            except Exception as error:  # noqa: BLE001
                reason = f"не установлен ({type(error).__name__})"
        return SolverInfo(
            name="planegcs",
            version=version,
            license="LGPL-2.1-or-later",
            copyleft_scope="только сама библиотека; приложение свободно",
            available=available,
            reason=reason,
        )

    def __init__(self):
        info = self.info()
        if not info.available:
            raise SolverUnavailable(info.reason)
        import planegcs

        self._planegcs = planegcs
        self.sketch = planegcs.Sketch()

    # --- построение ---

    def add_point(self, x: float, y: float):
        return self.sketch.add_point(x, y)

    def add_line(self, start, end):
        return self.sketch.add_line(start, end)

    def add_circle(self, center, radius: float) -> CurveHandle:
        # Параметр СВОБОДНЫЙ: окружность без размера обязана иметь степень
        # свободы, иначе эскиз считался бы определённым до простановки
        # радиуса. Закрепляет его отдельный размер.
        parameter = self.sketch.add_param(radius)
        return CurveHandle("circle", self.sketch.add_circle(center, parameter), parameter)

    def add_spline(self, poles, knots, mults, degree: int) -> CurveHandle:
        """Сплайн: полюсы точками, узлы и веса — ЗАКРЕПЛЁННЫМИ числами.

        Веса единичные: рациональные сплайны эскизу не нужны, а свободный
        вес добавил бы степень свободы, которую нечем задать.

        Узлы тоже закреплены. Свободный узел решатель двигал бы вместе с
        полюсами, и кривая меняла бы форму от правки соседнего размера —
        сама, без причины, видимой человеку.
        """
        weights = [self.sketch.add_param(1.0, fixed=True) for _ in poles]
        knot_ids = [self.sketch.add_param(float(value), fixed=True)
                    for value in knots]
        handle = self.sketch.add_bspline(
            poles[0], poles[-1], list(poles), weights, knot_ids,
            [int(value) for value in mults], int(degree), False)
        return CurveHandle("spline", handle,
                           extra={"poles": list(poles),
                                  "knots": tuple(float(v) for v in knots),
                                  "mults": tuple(int(v) for v in mults),
                                  "degree": int(degree)})

    def spline_geometry(self, curve: CurveHandle) -> SplineGeometry:
        info = self.sketch.get_bspline(curve.id)
        return SplineGeometry(
            poles=tuple(tuple(item) for item in info.poles),
            knots=tuple(float(value) for value in info.knots),
            mults=tuple(int(value) for value in info.multiplicities),
            degree=int(info.degree))

    def add_ellipse(self, center, focus, minor: float) -> CurveHandle:
        """Эллипс по центру, фокусу и малой полуоси.

        Малая полуось у PlaneGCS передаётся ЗНАЧЕНИЕМ, а параметр под неё
        решатель заводит сам, и его номер наружу не отдаёт. Поэтому
        отдельного размера на неё пока нет (ELLIPSE-GAP-001), а масштаб
        пересобирает эллипс — как и окружность.
        """
        return CurveHandle(
            "ellipse", self.sketch.add_ellipse(center, focus, float(minor)),
            extra={"center": center, "focus": focus})

    def ellipse_geometry(self, curve: CurveHandle) -> EllipseGeometry:
        info = self.sketch.get_ellipse(curve.id)
        return EllipseGeometry(center=tuple(info.center),
                               focus=tuple(info.focus1),
                               minor=float(info.radmin))

    def add_ellipse_arc(self, center, focus, minor: float,
                        start_angle: float, end_angle: float,
                        start, end) -> CurveHandle:
        """Дуга эллипса: концы — точки, всё остальное числа.

        Концы точками не для удобства: контур сшивается по общим точкам, и
        дуга, у которой концы не точки эскиза, ни к чему не пристыкуется.
        """
        return CurveHandle(
            "ellipse_arc",
            self.sketch.add_arc_of_ellipse(
                center, focus, float(minor), float(start_angle),
                float(end_angle), start, end))

    def add_hyperbola_arc(self, center, focus, minor: float,
                          start_angle: float, end_angle: float,
                          start, end) -> CurveHandle:
        """Дуга гиперболы: центр, фокус, малая полуось и два конца."""
        return CurveHandle(
            "hyperbola_arc",
            self.sketch.add_arc_of_hyperbola(
                center, focus, float(minor), float(start_angle),
                float(end_angle), start, end))

    def hyperbola_arc_geometry(self, curve: CurveHandle):
        info = self.sketch.get_arc_of_hyperbola(curve.id)
        return HyperbolaGeometry(
            center=tuple(info.center), focus=tuple(info.focus1),
            minor=float(info.radmin),
            start_angle=float(info.start_angle),
            end_angle=float(info.end_angle))

    def add_parabola_arc(self, vertex, focus, start_angle: float,
                         end_angle: float, start, end) -> CurveHandle:
        """Дуга параболы: вершина, фокус и два конца — точки эскиза.

        Параметр у неё — МЕСТНАЯ КООРДИНАТА Y: точка при `t` это
        `(t² / 4p, t)` в системе вершины, где `p` — расстояние до фокуса.
        Проверено опытом на самом решателе, а не выведено по памяти.
        """
        return CurveHandle(
            "parabola_arc",
            self.sketch.add_arc_of_parabola(
                vertex, focus, float(start_angle), float(end_angle),
                start, end))

    def parabola_arc_geometry(self, curve: CurveHandle):
        info = self.sketch.get_arc_of_parabola(curve.id)
        return ParabolaGeometry(
            vertex=tuple(info.vertex), focus=tuple(info.focus1),
            start_angle=float(info.start_angle),
            end_angle=float(info.end_angle))

    def ellipse_arc_geometry(self, curve: CurveHandle) -> EllipseGeometry:
        info = self.sketch.get_arc_of_ellipse(curve.id)
        return EllipseGeometry(center=tuple(info.center),
                               focus=tuple(info.focus1),
                               minor=float(info.radmin),
                               start_angle=float(info.start_angle),
                               end_angle=float(info.end_angle))

    def constrain_point_on_ellipse(self, point, curve: CurveHandle):
        return self.sketch.point_on_ellipse(point, curve.id)

    def add_arc(self, center, start, end) -> CurveHandle:
        cx, cy = self.point_coordinates(center)
        sx, sy = self.point_coordinates(start)
        ex, ey = self.point_coordinates(end)
        radius = math.hypot(sx - cx, sy - cy)
        start_angle = math.atan2(sy - cy, sx - cx)
        end_angle = math.atan2(ey - cy, ex - cx)
        radius_param = self.sketch.add_param(radius)
        start_param = self.sketch.add_param(start_angle)
        end_param = self.sketch.add_param(end_angle)
        arc = self.sketch.add_arc(
            center, start, end, radius_param, start_param, end_param
        )
        return CurveHandle("arc", arc, radius_param, start_param, end_param)

    # --- ограничения ---

    def constrain_coincident(self, point_a, point_b):
        return self.sketch.coincident(point_a, point_b)

    def constrain_horizontal(self, line):
        return self.sketch.horizontal(line)

    def constrain_vertical(self, line):
        return self.sketch.vertical(line)

    def constrain_parallel(self, line_a, line_b):
        return self.sketch.parallel(line_a, line_b)

    def constrain_perpendicular(self, line_a, line_b):
        return self.sketch.perpendicular(line_a, line_b)

    def constrain_equal(self, line_a, line_b):
        return self.sketch.equal_length(line_a, line_b)

    def constrain_anchor(self, point):
        # fix_point требует координаты — берём текущие, иначе точка «прыгнет».
        x, y = self.point_coordinates(point)
        return self.sketch.fix_point(point, x, y)

    def constrain_point_on_line(self, point, line):
        return self.sketch.point_on_line(point, line)

    def constrain_point_on_curve(self, point, curve: CurveHandle):
        if curve.kind == "circle":
            return self.sketch.point_on_circle(point, curve.id)
        return self.sketch.point_on_arc(point, curve.id)

    def constrain_midpoint(self, point, line):
        # В PlaneGCS середина выражается через вырожденный отрезок: точка
        # задаётся отрезком нулевой длины, лежащим серединой на целевом.
        return self.sketch.midpoint_on_line(self.sketch.add_line(point, point), line)

    def constrain_tangent(self, a, b, at_end: bool = False):
        # at_end не нужен: PlaneGCS сам разбирается, каким концом дуга
        # примыкает к отрезку.
        a_curve, b_curve = isinstance(a, CurveHandle), isinstance(b, CurveHandle)
        if a_curve and b_curve:
            return self.sketch.tangent_arc_arc(_as_arc(a), _as_arc(b))
        if a_curve:
            a, b, a_curve, b_curve = b, a, b_curve, a_curve
        if not b_curve:
            raise ValueError("касание требует хотя бы одной дуги или окружности")
        if b.kind == "circle":
            return self.sketch.tangent_line_circle(a, b.id)
        return self.sketch.tangent_line_arc(a, b.id)

    def constrain_symmetric(self, point_a, point_b, line):
        return self.sketch.symmetric_line(point_a, point_b, line)

    def constrain_equal_radius(self, a: CurveHandle, b: CurveHandle):
        if a.kind == "circle" and b.kind == "circle":
            return self.sketch.equal_radius_cc(a.id, b.id)
        if a.kind == "arc" and b.kind == "arc":
            return self.sketch.equal_radius_aa(a.id, b.id)
        circle, arc = (a, b) if a.kind == "circle" else (b, a)
        return self.sketch.equal_radius_ca(circle.id, arc.id)

    # --- размеры ---

    def add_dimension(self, point_a, point_b, value: float):
        # Параметр размера ЗАКРЕПЛЯЕТСЯ. Свободный превращает размер в ещё
        # одну неизвестную: эскиз остаётся недоопределённым, а решатель
        # «удовлетворяет» ограничение, меняя сам размер вместо геометрии.
        parameter = self.sketch.add_param(value, fixed=True)
        return _Dimension(self.sketch.p2p_distance(point_a, point_b, parameter), parameter)

    def add_radius_dimension(self, curve: CurveHandle, value: float):
        parameter = self.sketch.add_param(value, fixed=True)
        if curve.kind == "circle":
            tag = self.sketch.circle_radius(curve.id, parameter)
        else:
            tag = self.sketch.arc_radius(curve.id, parameter)
        return _Dimension(tag, parameter)

    def add_diameter_dimension(self, curve: CurveHandle, value: float):
        parameter = self.sketch.add_param(value, fixed=True)
        if curve.kind == "circle":
            tag = self.sketch.circle_diameter(curve.id, parameter)
        else:
            tag = self.sketch.arc_diameter(curve.id, parameter)
        return _Dimension(tag, parameter)

    def add_angle_dimension(self, line_a, line_b, value_deg: float):
        # Решатель считает в радианах; scale переводит обратно при правке.
        parameter = self.sketch.add_param(math.radians(value_deg), fixed=True)
        tag = self.sketch.l2l_angle(line_a, line_b, parameter)
        return _Dimension(tag, parameter, scale=math.pi / 180.0)

    def set_dimension(self, handle: _Dimension, value: float) -> None:
        # Именно set_param: set_p2p_distance добавил бы второе ограничение
        # и сделал эскиз противоречивым.
        self.sketch.set_param(handle.param, value * handle.scale)

    # --- решение и чтение ---

    def solve(self) -> Solution:
        status = self.sketch.solve()
        code = int(getattr(status, "value", status))
        ok = code in (0, 1)
        failed: list = []
        message = STATUS_TEXT.get(code, f"код {code}")
        try:
            diagnosis = self.sketch.diagnose()
            failed = list(getattr(diagnosis, "conflicting", []) or [])
            redundant = list(getattr(diagnosis, "redundant", []) or [])
            if failed:
                ok = False
                message = "ограничения противоречивы"
            elif redundant:
                message += f"; избыточных ограничений {len(redundant)}"
        except Exception:  # noqa: BLE001 — разбор есть не во всех версиях
            pass
        return Solution(ok=ok, dof=self.sketch.dof(), message=message, failed=failed)

    def point_coordinates(self, point) -> tuple[float, float]:
        px, py = self.sketch.get_point_param_ids(point)
        return self.sketch.get_param(px), self.sketch.get_param(py)

    def circle_geometry(self, curve: CurveHandle):
        if curve.kind == "circle":
            info = self.sketch.get_circle(curve.id)
            return tuple(info.center), info.radius
        info = self.sketch.get_arc(curve.id)
        return tuple(info.center), info.radius

    def arc_geometry(self, curve: CurveHandle) -> ArcGeometry:
        info = self.sketch.get_arc(curve.id)
        return ArcGeometry(
            center=tuple(info.center),
            radius=info.radius,
            start_angle=info.start_angle,
            end_angle=info.end_angle,
        )

    def move_point(self, point, x: float, y: float) -> Solution:
        px, py = self.sketch.get_point_param_ids(point)
        self.sketch.set_param(px, x)
        self.sketch.set_param(py, y)
        return self.solve()

    def place_points(self, pairs) -> Solution:
        """Поставить сразу несколько точек и решить ОДИН раз.

        Промежуточных решений нет намеренно: они тянули бы ещё не
        сдвинутые точки обратно к связям, и перенос группы получался бы по
        частям, каждая часть — с испорченной расстановки.
        """
        for point, x, y in pairs:
            px, py = self.sketch.get_point_param_ids(point)
            self.sketch.set_param(px, x)
            self.sketch.set_param(py, y)
        return self.solve()


def _as_arc(curve: CurveHandle):
    """PlaneGCS различает дуги и окружности в касании: `tangent_arc_arc`
    окружность не принимает. Ловим это здесь, а не в недрах решателя."""
    if curve.kind != "arc":
        raise ValueError("касание двух окружностей задаётся через отрезок-посредник")
    return curve.id
