"""Поле эскиза: рисование, привязки, связи, размеры.

Здесь живёт всё, что человек делает руками. Три решения определяют
поведение и стоят объяснения.

**Привязка возвращает не координату, а намерение.** Щелчок рядом с концом
чужого отрезка означает «эта же точка», а не «точка примерно там». Поэтому
:class:`Snap` несёт не только положение, но и объект, к которому
привязались, и построение переиспользует СУЩЕСТВУЮЩУЮ точку эскиза вместо
создания новой поверх. Совпадение, полученное общей точкой, нельзя
случайно разорвать — в отличие от совпадения, выраженного связью.

**Связи наводятся при рисовании, а не после.** Отрезок, проведённый почти
горизонтально, получает горизонтальность сразу. Без этого эскиз из десяти
отрезков имеет двадцать степеней свободы, и человек проставляет два
десятка связей руками — ровно та работа, ради избавления от которой
параметрический эскиз и придуман. Порог намеренно узкий: наведённая связь,
которой не просили, хуже отсутствующей.

**Цвет показывает определённость.** Недоопределённый эскиз рисуется синим,
полностью определённый — чёрным. Цвет назначается всему эскизу целиком, а
не каждому объекту: степени свободы решатели считают для системы, и
раскрашивать объекты по отдельности значило бы выдумывать данные, которых
нет.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6 import QtCore, QtGui, QtWidgets

from protocad.sketch import Segment, Sketch, SketchError, geom2d
from protocad.sketch import regions as regions_module
from protocad.sketch import tools as T

# --- оформление ---

COLOR_BACKGROUND = QtGui.QColor("#fbfbfd")
COLOR_GRID = QtGui.QColor("#e6e9ef")
COLOR_GRID_MAJOR = QtGui.QColor("#d3d8e2")
COLOR_AXIS = QtGui.QColor("#aab2c0")
# Начало координат эскиза. Красный — общепринятая метка нуля на чертеже, и
# ни с чем в самом эскизе он не путается.
COLOR_ORIGIN = QtGui.QColor("#c0392b")
# Определённость видна ЦВЕТОМ, и различать надо с одного взгляда: чёрное
# доделано, синее ещё нет. Тёмно-серый вместо чёрного эту пару смазывает —
# на светлом фоне он читается как «просто линия», и разница замечается
# только рядом с синей.
COLOR_FREE = QtGui.QColor("#1f6fb2")      # недоопределённая геометрия
COLOR_FIXED = QtGui.QColor("#000000")     # полностью определённая
COLOR_CONFLICT = QtGui.QColor("#c0392b")  # противоречие или переопределённость
# Вспомогательная геометрия отличается ШТРИХОМ, а не только бледностью:
# на неё смотрят, чтобы понять построение, и цветом одним её от обычной
# линии не отделить — особенно распечатанную.
COLOR_CONSTRUCTION = QtGui.QColor("#8a94a6")
COLOR_SELECTED = QtGui.QColor("#e07b00")
COLOR_HOVER = QtGui.QColor("#2f9e44")
COLOR_DIMENSION = QtGui.QColor("#8e2b1f")
COLOR_PREVIEW = QtGui.QColor("#7b8794")
COLOR_SNAP = QtGui.QColor("#2f9e44")
# Взятое с детали и потерявшее свой объект детали. Ссылка не должна
# выглядеть как обычная вспомогательная линия: правится она не здесь.
COLOR_EXTERNAL = QtGui.QColor("#7048c4")
# Закрашенные области эскиза. Выбранная — заметно плотнее наведённой:
# «на что я навёл» и «что я выбрал» путать нельзя, от второго зависит
# результат операции.
COLOR_REGION = QtGui.QColor(120, 150, 190, 40)
COLOR_REGION_HOVER = QtGui.QColor(90, 140, 200, 70)
COLOR_REGION_PICKED = QtGui.QColor(230, 150, 40, 105)
COLOR_DANGLING = QtGui.QColor("#8a5a12")

PICK_RADIUS = 8.0          # экранных пикселей
SNAP_RADIUS = 12.0
AXIS_TOLERANCE = 2.5       # градусов: порог наведения горизонтали и вертикали


class PlaneProjector:
    """Перевод «плоскость эскиза ↔ экран» через камеру трёхмерного вида.

    С ним поле эскиза перестаёт быть отдельным окном: те же инструменты,
    те же привязки, но координаты берутся не из собственного масштаба, а из
    камеры. Эскиз оказывается НА детали, и его видно вместе с ней под любым
    углом.

    Обратный перевод — пересечение луча взгляда с плоскостью. Когда
    плоскость видна с ребра, луч ей почти параллелен и точка уезжает в
    бесконечность; такие положения отбрасываются, а не выдают координату,
    которой человек не хотел.
    """

    # Совсем вырожденный взгляд: плоскость видна с ребра, луч ей
    # параллелен, точка уходит в бесконечность.
    EDGE_ON = 0.05          # около 87° от нормали

    # Ограничителя «не дальше стольких-то радиусов сцены» здесь НЕТ
    # намеренно. Он был и оказался вреден: у новой детали сцена пуста, её
    # радиус равен единице, и рисовать становилось нельзя вообще. Отказ
    # должен опираться на геометрию, а не на догадку о масштабе: плоскость
    # видна с ребра — отказ; точка позади камеры — отказ; всё остальное
    # законно, даже если далеко.

    def __init__(self, viewport, plane):
        self.viewport = viewport
        self.plane = plane

    def to_screen(self, u: float, v: float) -> QtCore.QPointF:
        # По ОДНОЙ точке: показ эскиза зовёт это тысячами раз за перерисовку,
        # и массив из одного элемента стоил вдесятеро дороже арифметики.
        point = self.plane.point_at(u, v)
        x, y, _ = self.viewport.project_point(point[0], point[1], point[2])
        return QtCore.QPointF(x, y)

    def to_world(self, position) -> tuple[float, float] | None:
        origin, direction = self.viewport.ray(position)
        normal = self.plane.normal
        denominator = sum(direction[i] * normal[i] for i in range(3))
        if abs(denominator) < self.EDGE_ON:
            return None
        offset = [self.plane.origin[i] - origin[i] for i in range(3)]
        t = sum(offset[i] * normal[i] for i in range(3)) / denominator
        if t <= 0.0:
            # Точка ПОЗАДИ камеры. Так выглядит щелчок выше линии
            # горизонта: луч уходит вверх и пересекает плоскость с обратной
            # стороны. Формула даёт координату, и она зеркальная и далёкая —
            # отсюда и брался огромный скошенный контур вместо
            # нарисованного прямоугольника.
            return None
        hit = tuple(origin[i] + direction[i] * t for i in range(3))
        return self.plane.project(hit)

    def edge_on(self) -> bool:
        """Видна ли плоскость слишком косо, чтобы на ней рисовать."""
        origin, direction = self.viewport.ray(
            QtCore.QPointF(self.viewport.width() / 2.0, self.viewport.height() / 2.0)
        )
        normal = self.plane.normal
        return abs(sum(direction[i] * normal[i] for i in range(3))) < self.EDGE_ON

    def pixels_per_mm(self) -> float:
        """Сколько пикселей в миллиметре здесь и сейчас.

        От этого зависят все допуски выбора и привязки. Считается по
        реальной проекции, а не по коэффициенту приближения: при перспективе
        дальний край плоскости мельче ближнего.
        """
        origin = self.to_screen(0.0, 0.0)
        unit = self.to_screen(1.0, 0.0)
        distance = math.hypot(unit.x() - origin.x(), unit.y() - origin.y())
        return distance if distance > 1e-6 else 1.0


@dataclass
class Snap:
    """Куда попадёт следующая точка и почему именно туда."""

    position: tuple[float, float]
    kind: str = "free"
    point: object = None       # существующая точка эскиза
    segment: object = None     # объект, на котором лежит привязка

    TITLES = {
        "free": "",
        "point": "точка",
        "end": "конец",
        "middle": "середина",
        "center": "центр",
        "on": "на объекте",
        "cross": "пересечение",
        "grid": "сетка",
        "axis": "по оси",
        "origin": "начало координат",
    }

    @property
    def title(self) -> str:
        return self.TITLES.get(self.kind, self.kind)


# Сколько точек собирает каждый инструмент и что из них строит.
DRAW_TOOLS = {
    "draw.line": 2,
    "draw.polyline": 0,          # 0 — сколько угодно, до завершения
    "draw.spline": 0,
    "draw.rectangle": 2,
    "draw.center_rectangle": 2,
    "draw.circle": 2,
    "draw.ellipse": 3,
    # Дуга эллипса: сам эллипс тремя щелчками, потом начало и конец.
    "draw.ellipse_arc": 5,
    # Парабола: вершина, фокус, затем два конца дуги.
    "draw.parabola": 4,
    # Гипербола: центр, вершина, мнимая полуось, затем концы дуги.
    "draw.hyperbola": 5,
    "draw.arc": 3,
    "draw.arc_3p": 3,
    "draw.slot": 3,
    "draw.polygon": 2,
    "draw.point": 1,
    "draw.tangent_arc": 2,
    "draw.circle_3p": 3,
    "draw.rectangle_3p": 3,
    "draw.parallelogram": 3,
    "draw.center_slot": 3,
}

CLICK_TOOLS = {"edit.trim", "edit.extend", "edit.fillet", "edit.chamfer"}
DIMENSION_TOOLS = {"dim.smart", "dim.radius", "dim.diameter", "dim.angle",
                   "dim.horizontal", "dim.vertical"}

#: Насколько прямые считаются параллельными, градусы. Нарисованные от руки
#: параллельными не бывают: между ними всегда доли градуса.
ANGLE_PARALLEL = 0.5


def _hyperbolic(centre, angle_deg: float, minor: float, point) -> float:
    """Гиперболический параметр щелчка: ``asinh(Y / b)`` в системе центра.

    По ПОПЕРЕЧНОЙ координате: ``ch`` чётен и ветвей не различает, а ``sh``
    монотонен — по нему параметр восстанавливается однозначно.
    """
    angle = math.radians(angle_deg)
    dx, dy = point[0] - centre[0], point[1] - centre[1]
    across = -dx * math.sin(angle) + dy * math.cos(angle)
    return math.asinh(across / (minor or 1e-9))


def _across_axis(vertex, angle_deg: float, point) -> float:
    """Местная координата точки ПОПЕРЁК оси — она же параметр параболы.

    Расстояние до вершины для этого не годится: у параболы параметр не
    длина и не угол, а именно поперечная координата.
    """
    angle = math.radians(angle_deg)
    dx, dy = point[0] - vertex[0], point[1] - vertex[1]
    return -dx * math.sin(angle) + dy * math.cos(angle)


def _own_angle(centre, major: float, minor: float, angle_deg: float,
               point) -> float:
    """Собственный (параметрический) угол точки на эллипсе, в градусах.

    У эллипса параметрический угол и полярный — РАЗНЫЕ: точка с параметром
    45° лежит не под 45° от центра. Щелчок переводится в параметр так же,
    как эллипс и задан: раскладывается по его осям и делится на полуоси.
    """
    angle = math.radians(angle_deg)
    dx = point[0] - centre[0]
    dy = point[1] - centre[1]
    along = dx * math.cos(angle) + dy * math.sin(angle)
    across = -dx * math.sin(angle) + dy * math.cos(angle)
    return math.degrees(math.atan2(across / max(minor, 1e-9),
                                   along / max(major, 1e-9)))


class SketchCanvas(QtWidgets.QWidget):
    """Поле эскиза."""

    changed = QtCore.Signal()
    status = QtCore.Signal(str)
    hint = QtCore.Signal(str)
    tool_finished = QtCore.Signal()
    #: инструмент сменился — лента обязана это показать
    tool_changed = QtCore.Signal(str)
    #: Щелчок пришёлся мимо эскиза — может быть, целились в деталь.
    model_pick = QtCore.Signal(QtCore.QPointF)

    def __init__(self, sketch: Sketch, parent=None):
        super().__init__(parent)
        self.sketch = sketch
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMinimumSize(480, 360)

        self._scale = 3.0
        self.origin = QtCore.QPointF(0.0, 0.0)   # мировая точка в центре окна
        # Проекция через камеру трёхмерного вида. None — своё плоское поле
        # со своим приближением; иначе эскиз живёт поверх детали.
        self.projector: PlaneProjector | None = None
        #: (точка экрана) -> отрезок-след грани детали либо ``None``.
        #: Ставится окном: поле эскиза о детали ничего не знает и знать не
        #: должно — оно лишь спрашивает, что там, и получает обычный
        #: отрезок, с которым дальше работает как со своим.
        self.face_probe = None
        self.tool = "sketch.select"
        self.construction_mode = False
        self.show_relations = True
        self.show_grid = False
        self.show_dimensions = True
        self.show_points = True
        # Закрашенные области эскиза. Без них не видно, ЧТО именно
        # выдавится: прямоугольник с кругом внутри выглядит одинаково и
        # когда круг — отверстие, и когда он самостоятельная область.
        self.show_regions = True
        self._regions: list = []
        self._regions_stamp = None
        self._region_hover = None
        self.selected_regions: list = []
        #: Выбранная связь — её значок подсвечен, а объекты связи выделены.
        self.selected_relation = None
        self._relation_hover = None
        self.grid_step = 5.0
        self.polygon_sides = 6

        self.selection: list = []
        self._pending: list[Snap] = []
        self._cursor: tuple[float, float] = (0.0, 0.0)
        self._snap: Snap | None = None
        self._hover = None
        self._dragging = None
        self._panning: QtCore.QPoint | None = None
        self._band: tuple | None = None
        self._fillet_radius = 5.0
        self._chamfer_distance = 5.0
        self._note = ""
        # Отмена хранит состояния эскиза целиком. Это возможно только
        # потому, что эскиз — это журнал построения, а не граф объектов в
        # памяти: снимок стоит словаря, а возврат — его проигрывания.
        self._history: list[dict] = []
        self._future: list[dict] = []
        #: Ctrl подавляет наведение связей — так их отключают на один раз
        self._suppress_relations = False

        self._fit()

    UNDO_DEPTH = 60

    def reset_history(self) -> None:
        """Забыть шаги отмены. Нужно при отказе от правок эскиза целиком:
        отменять «назад» после отказа было бы возвратом к тому, от чего
        только что отказались."""
        self._history.clear()

    def _begin_change(self) -> None:
        """Запомнить состояние ПЕРЕД правкой."""
        self._record(self.sketch.to_dict())

    def _record(self, snapshot: dict) -> None:
        self._history.append(snapshot)
        if len(self._history) > self.UNDO_DEPTH:
            self._history.pop(0)
        self._future.clear()

    def undo(self) -> None:
        if not self._history:
            self._note = "отменять нечего"
            self._emit_status()
            return
        self._future.append(self.sketch.to_dict())
        self.sketch.restore(self._history.pop())
        # Выбор указывает на объекты, которых после отката может не быть.
        self.selection.clear()
        self._pending.clear()
        self._note = "отменено"
        self._after_change()

    def redo(self) -> None:
        if not self._future:
            self._note = "повторять нечего"
            self._emit_status()
            return
        self._history.append(self.sketch.to_dict())
        self.sketch.restore(self._future.pop())
        self.selection.clear()
        self._pending.clear()
        self._note = "повторено"
        self._after_change()

    # --- преобразование координат ---

    @property
    def scale(self) -> float:
        """Пикселей в миллиметре. С проекцией берётся у камеры, иначе своё."""
        if self.projector is not None:
            return self.projector.pixels_per_mm()
        return self._scale

    @scale.setter
    def scale(self, value: float) -> None:
        self._scale = value

    def _to_screen(self, point) -> QtCore.QPointF:
        if self.projector is not None:
            return self.projector.to_screen(point[0], point[1])
        return QtCore.QPointF(
            self.width() / 2.0 + (point[0] - self.origin.x()) * self._scale,
            self.height() / 2.0 - (point[1] - self.origin.y()) * self._scale,
        )

    def _to_world(self, position) -> tuple[float, float]:
        if self.projector is not None:
            # Плоскость, видимая слишком косо, координаты не даёт. Вернуть
            # приблизительное значение нельзя: именно так получался
            # огромный скошенный контур вместо нарисованного.
            hit = self.projector.to_world(position)
            return hit if hit is not None else self._cursor
        return (
            self.origin.x() + (position.x() - self.width() / 2.0) / self._scale,
            self.origin.y() - (position.y() - self.height() / 2.0) / self._scale,
        )

    def _fit(self) -> None:
        if self.projector is not None:
            self.projector.viewport.fit_view()
            return
        points = [self.sketch.coordinates(p) for p in self.sketch.points]
        for segment in self.sketch.segments:
            if segment.kind in ("circle", "arc"):
                center, radius = self.sketch.circle_geometry(segment)
                points += [
                    (center[0] - radius, center[1] - radius),
                    (center[0] + radius, center[1] + radius),
                ]
        if not points:
            self.origin = QtCore.QPointF(0.0, 0.0)
            self.scale = 3.0
            return
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        width = max(max(xs) - min(xs), 1.0)
        height = max(max(ys) - min(ys), 1.0)
        self.origin = QtCore.QPointF((min(xs) + max(xs)) / 2.0, (min(ys) + max(ys)) / 2.0)
        self.scale = 0.8 * min(
            max(self.width(), 200) / width, max(self.height(), 200) / height
        )

    def fit_view(self) -> None:
        self._fit()
        self.update()

    # --- инструменты ---

    def set_tool(self, key: str) -> None:
        previous = self.tool
        self.tool = key or "sketch.select"
        self._pending.clear()
        self._note = ""
        self.setCursor(
            QtCore.Qt.ArrowCursor if self.tool == "sketch.select"
            else QtCore.Qt.CrossCursor
        )
        self.hint.emit(self._hint_text())
        if self.tool != previous:
            # Иначе кнопка ленты остаётся нажатой, а поле уже в другом
            # режиме: на экране одно, под рукой другое.
            self.tool_changed.emit(self.tool)
        self.update()

    def _can_draw(self, position=None) -> bool:
        """Можно ли сейчас ставить точки на плоскости эскиза."""
        if self.projector is None:
            return True
        if self.projector.edge_on():
            return False
        return position is None or self.projector.to_world(position) is not None

    def _hint_text(self) -> str:
        texts = {
            "sketch.select": "Выбор: щелчок — объект, перетаскивание — точка, "
                             "рамка — несколько. Средняя кнопка — панорама.",
            "draw.line": "Отрезок: щёлкните начало, затем конец.",
            "draw.polyline": "Ломаная: щёлкайте вершины, двойной щелчок или Esc — конец.",
            "draw.spline": ("Сплайн: щёлкайте точки, через которые пройдёт "
                            "кривая; двойной щелчок или Esc — конец."),
            "draw.rectangle": "Прямоугольник: два противоположных угла.",
            "draw.center_rectangle": "Прямоугольник от центра: центр, затем угол.",
            "draw.circle": "Окружность: центр, затем точка на окружности.",
            "draw.ellipse": ("Эллипс: центр, конец большой оси, затем "
                             "малая полуось."),
            "draw.arc": "Дуга: центр, начало, конец. Обход против часовой стрелки.",
            "draw.hyperbola": ("Гипербола: центр, вершина, мнимая полуось, "
                               "затем начало и конец дуги."),
            "draw.parabola": ("Парабола: вершина, фокус, затем начало и "
                              "конец дуги."),
            "draw.ellipse_arc": ("Дуга эллипса: центр, конец большой оси, "
                                 "малая полуось, затем начало и конец дуги"),
            "draw.arc_3p": "Дуга по трём точкам: начало, промежуточная, конец.",
            "draw.slot": "Паз: начало оси, конец оси, затем ширина.",
            "draw.polygon": f"Многоугольник ({self.polygon_sides} сторон): центр, затем вершина.",
            "draw.point": "Точка: щёлкните место.",
            "edit.trim": "Отсечение: щёлкните участок, который надо убрать.",
            "edit.extend": "Продление: щёлкните у того конца, который надо продлить.",
            "edit.fillet": f"Скругление R{self._fillet_radius:g}: щёлкните два отрезка.",
            "edit.chamfer": f"Фаска {self._chamfer_distance:g}: щёлкните два отрезка.",
            "dim.smart": "Размер: выберите две точки, отрезок, дугу или два отрезка.",
            "dim.radius": "Радиус: щёлкните дугу или окружность.",
            "dim.diameter": "Диаметр: щёлкните окружность.",
            "dim.angle": "Угол: щёлкните два отрезка.",
            "dim.horizontal": "Размер по X: щёлкните две точки или отрезок.",
            "dim.vertical": "Размер по Y: щёлкните две точки или отрезок.",
        }
        return texts.get(self.tool, "")

    # --- привязки ---

    def _find_snap(self, world) -> Snap:
        """Ближайшая осмысленная точка. Порядок проверок = порядок важности."""
        tolerance = SNAP_RADIUS / self.scale
        best: Snap | None = None
        best_distance = tolerance

        # 1. Существующие точки: самая сильная привязка, даёт общую точку.
        for point in self.sketch.points:
            position = self.sketch.coordinates(point)
            distance = geom2d.distance(world, position)
            if distance < best_distance:
                kind = "center" if self._is_center(point) else "point"
                # Объект, которому принадлежит точка, запоминается вместе с
                # ней: касательной дуге нужно знать не только «где», но и
                # «к чему касаться».
                owner = next(
                    (segment for segment in self.sketch.segments
                     if any(end is point for end in segment.ends)),
                    None,
                )
                best = Snap(position, kind, point=point, segment=owner)
                best_distance = distance

        # 2. Начало координат — его в эскизе нет как объекта, но привязка к
        #    нему нужна: с неё начинается закрепление контура.
        if geom2d.distance(world, (0.0, 0.0)) < best_distance:
            best = Snap((0.0, 0.0), "origin")
            best_distance = geom2d.distance(world, (0.0, 0.0))

        if best is not None and best.kind in ("point", "center", "origin"):
            return best

        # 3. Середины отрезков и точки на объектах.
        for segment in self.sketch.segments:
            shape = T.describe(self.sketch, segment)
            middle = self._middle_of(shape)
            if middle is not None:
                distance = geom2d.distance(world, middle)
                if distance < best_distance:
                    best = Snap(middle, "middle", segment=segment)
                    best_distance = distance
            distance = T.distance_to(self.sketch, segment, world)
            if distance < best_distance:
                best = Snap(self._project(segment, world), "on", segment=segment)
                best_distance = distance

        if best is not None:
            return best

        # 4. Сетка — только когда она показана: невидимая привязка сбивает.
        #    Порог здесь свой, долей шага, а не общий пиксельный: при
        #    отдалении шаг сетки на экране становится меньше радиуса
        #    привязки, и с общим порогом к узлам притягивается ВСЁ подряд —
        #    поставить точку между узлами становится невозможно.
        if self.show_grid:
            step = self._grid_step()
            snapped = (
                round(world[0] / step) * step,
                round(world[1] / step) * step,
            )
            if geom2d.distance(world, snapped) < min(tolerance, step * 0.35):
                return Snap(snapped, "grid")

        # 5. Выравнивание по осям от предыдущей точки — подсказка при
        #    рисовании, а не привязка к объекту.
        if self._pending:
            previous = self._pending[-1].position
            if abs(world[1] - previous[1]) * self.scale < SNAP_RADIUS:
                return Snap((world[0], previous[1]), "axis")
            if abs(world[0] - previous[0]) * self.scale < SNAP_RADIUS:
                return Snap((previous[0], world[1]), "axis")

        return Snap(world)

    @staticmethod
    def _middle_of(shape):
        """Середина объекта. У окружности её нет — есть центр."""
        if shape.kind == "line":
            return geom2d.midpoint(shape.start, shape.end)
        if shape.kind == "arc":
            sweep = geom2d.arc_sweep(shape.start_angle, shape.end_angle)
            return geom2d.point_at_angle(
                shape.center, shape.radius, shape.start_angle + sweep / 2.0
            )
        return None

    def _is_center(self, point) -> bool:
        return any(
            segment.kind in ("circle", "arc") and segment.points[0] is point
            for segment in self.sketch.segments
        )

    def _project(self, segment, world) -> tuple[float, float]:
        shape = T.describe(self.sketch, segment)
        if shape.kind == "line":
            return geom2d.project_on_line(world, shape.start, shape.end)[0]
        return geom2d.point_at_angle(
            shape.center, shape.radius, geom2d.angle_at(shape.center, world)
        )

    def _pick(self, world):
        """Объект под курсором. Точки имеют приоритет над линиями: попасть
        в точку труднее, а нужна она чаще."""
        tolerance = PICK_RADIUS / self.scale
        for point in self.sketch.points:
            if geom2d.distance(world, self.sketch.coordinates(point)) < tolerance:
                return point
        best, best_distance = None, tolerance
        for segment in self.sketch.segments:
            distance = T.distance_to(self.sketch, segment, world)
            if distance < best_distance:
                best, best_distance = segment, distance
        if best is not None:
            return best
        for dimension in self.sketch.dimensions:
            anchor = self._dimension_anchor(dimension)
            if anchor and geom2d.distance(world, anchor) < tolerance * 2.0:
                return dimension
        return None

    # --- события мыши ---

    def mousePressEvent(self, event) -> None:
        world = self._to_world(event.position())
        if event.button() == QtCore.Qt.MiddleButton:
            if self.projector is not None:
                # Средняя кнопка принадлежит виду: в режиме эскиза деталь
                # надо уметь повернуть, не выходя из эскиза.
                self._panning = "viewport"
                self.projector.viewport.apply_press(event)
                return
            self._panning = event.position().toPoint()
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            return
        if event.button() == QtCore.Qt.RightButton:
            self._finish_tool()
            return
        if event.button() != QtCore.Qt.LeftButton:
            return
        # Ctrl отключает наведение связей на это построение. Иногда линия
        # почти горизонтальна намеренно, и навязанная горизонтальность —
        # это лишняя правка, которую потом ищут.
        self._suppress_relations = bool(event.modifiers() & QtCore.Qt.ControlModifier)

        if self.tool != "sketch.select" and not self._can_draw(position=event.position()):
            self._note = (
                "Плоскость видна слишком косо — поверните вид к ней "
                "(«Нормально к плоскости»)"
            )
            self._emit_status()
            self.update()
            return
        if self.tool == "sketch.select":
            self._press_select(world, event.modifiers())
        elif self.tool in DRAW_TOOLS:
            self._pending.append(self._find_snap(world))
            self._maybe_build()
        elif self.tool in CLICK_TOOLS:
            self._press_click_tool(world)
        elif self.tool in DIMENSION_TOOLS:
            self._press_dimension(world, event.position())
        self.update()

    def regions(self) -> list:
        """Области эскиза. Пересчитываются, когда эскиз изменился.

        Отпечаток снимается с положения всех точек и радиусов: правка
        размера двигает геометрию, не меняя её состава, а области при этом
        становятся другими.
        """
        stamp = self._region_stamp()
        if stamp != self._regions_stamp:
            try:
                self._regions = regions_module.build(self.sketch)
            except Exception:  # noqa: BLE001 — незамкнутый эскиз это не отказ
                self._regions = []
            self._regions_stamp = stamp
            self._drop_lost_regions()
        return self._regions

    def _region_stamp(self):
        try:
            points = tuple(
                (round(v, 9) for v in self.sketch.coordinates(point))
                for point in self.sketch.points
            )
            curves = tuple(
                round(self.sketch.circle_geometry(segment)[1], 9)
                for segment in self.sketch.segments
                if segment.kind in ("circle", "arc")
            )
        except Exception:  # noqa: BLE001
            return None
        kinds = tuple((s.id, s.kind, s.construction) for s in self.sketch.segments)
        return (tuple(tuple(p) for p in points), curves, kinds)

    def _drop_lost_regions(self) -> None:
        """Оставить в выборе только те области, что ещё существуют.

        Потеря ГОВОРИТСЯ вслух. Молчание здесь обходилось дорого: правка
        эскиза убирала выбор, операция получала пустой список, строила
        «весь эскиз» — и в кармане оставался остров, которого никто не
        заказывал. Заметить это можно было только по форме детали.
        """
        kept = []
        for reference in self.selected_regions:
            if regions_module.find(self._regions, reference) is not None:
                kept.append(reference)
        lost = len(self.selected_regions) - len(kept)
        self.selected_regions = kept
        if lost:
            self.status.emit(
                f"Выбор области потерян: правка убрала её контур "
                f"(областей снято с выбора: {lost}). Выберите заново — иначе "
                f"операция возьмёт весь эскиз")

    def region_at(self, world):
        """Область под точкой. Из вложенных выбирается самая мелкая."""
        found = [r for r in self.regions() if r.contains(world)]
        return min(found, key=lambda r: r.area) if found else None

    def toggle_region(self, region, add: bool = False) -> None:
        reference = region.reference()
        same = [r for r in self.selected_regions
                if regions_module.find([region], r) is not None]
        if same:
            for item in same:
                self.selected_regions.remove(item)
        else:
            if not add:
                self.selected_regions = []
            self.selected_regions.append(reference)
        self._emit_status()
        self.update()

    def region_is_picked(self, region) -> bool:
        return any(regions_module.find([region], reference) is not None
                   for reference in self.selected_regions)

    def _press_select(self, world, modifiers) -> None:
        # Значок связи имеет приоритет над геометрией: он мелкий и лежит
        # поверх, и промахнуться по нему в пользу отрезка легче лёгкого.
        relation = self.pick_relation(self._to_screen(world))
        if relation is not None:
            self.selected_relation = relation
            self.selection = [
                target for target in relation.targets
                if _is_point(target) or _is_segment(target)
            ]
            self._emit_status()
            self.update()
            return
        self.selected_relation = None
        picked = self._pick(world)
        if picked is None:
            region = self.region_at(world) if self.show_regions else None
            if region is not None:
                self.toggle_region(
                    region, add=bool(modifiers & QtCore.Qt.ControlModifier))
                return
            if not (modifiers & QtCore.Qt.ControlModifier):
                self.selection.clear()
                self.selected_regions = []
            # Щелчок по пустому месту — просьба указать на ДЕТАЛЬ: там
            # может быть вершина, ребро или грань, к которой привязывают
            # эскиз. Отдельный режим для этого не нужен.
            self.model_pick.emit(self._to_screen(world))
            self._band = (world, world)
            return
        if modifiers & QtCore.Qt.ControlModifier:
            if picked in self.selection:
                self.selection.remove(picked)
            else:
                self.selection.append(picked)
        else:
            if picked not in self.selection:
                self.selection = [picked]
        if _is_point(picked):
            self._begin_change()
            self._dragging = picked
        elif hasattr(picked, "value"):
            # Размер тянется за подпись. Положение выноски к решению
            # отношения не имеет, но на плотном чертеже подписи ложатся друг
            # на друга, и разложить их — не украшение, а условие читаемости.
            self._dragging = picked
        self._emit_status()

    def _drag_dimension(self, dimension, world) -> None:
        try:
            if dimension.kind in ("radius", "diameter"):
                center, radius = self.sketch.circle_geometry(dimension.targets[0])
                dimension.offset = (
                    geom2d.angle_at(center, world),
                    max(geom2d.distance(center, world) - radius, 1.0),
                )
                return
            if dimension.kind == "angle":
                first = T.describe(self.sketch, dimension.targets[0])
                second = T.describe(self.sketch, dimension.targets[1])
                crossing = geom2d.line_line(first.start, first.end,
                                            second.start, second.end)
                if crossing is not None:
                    dimension.offset = (0.0, max(geom2d.distance(crossing[0], world), 1.0))
                return
            first = self.sketch.coordinates(dimension.targets[0])
            second = self.sketch.coordinates(dimension.targets[1])
            axis = geom2d.direction(first, second)
            if axis == (0.0, 0.0):
                return
            relative = (world[0] - first[0], world[1] - first[1])
            normal = geom2d.normal(axis)
            middle = geom2d.midpoint(first, second)
            along = ((world[0] - middle[0]) * axis[0] + (world[1] - middle[1]) * axis[1])
            dimension.offset = (
                relative[0] * normal[0] + relative[1] * normal[1],
                along,
            )
        except Exception:  # noqa: BLE001 — цель размера могла исчезнуть
            pass

    def _press_click_tool(self, world) -> None:
        segment = self._pick(world)
        if segment is None or not hasattr(segment, "kind"):
            return
        self._begin_change()
        try:
            if self.tool == "edit.trim":
                result = T.trim(self.sketch, segment, world)
                self._note = "отсечено" + (f"; {result.note}" if result.note else "")
            elif self.tool == "edit.extend":
                result = T.extend(self.sketch, segment, world)
                self._note = "продлено" + (f"; {result.note}" if result.note else "")
            else:
                self._pending.append(Snap(world, segment=segment))
                if len(self._pending) < 2:
                    return
                first = self._pending[0].segment
                second = self._pending[1].segment
                self._pending.clear()
                if self.tool == "edit.fillet":
                    result = T.fillet(self.sketch, first, second, self._fillet_radius)
                    self._note = "скруглено"
                else:
                    result = T.chamfer(self.sketch, first, second, self._chamfer_distance)
                    self._note = "фаска снята"
                if result.note:
                    self._note += f"; {result.note}"
        except T.ToolError as error:
            self._note = str(error)
            self._pending.clear()
        except Exception as error:  # noqa: BLE001 — отказ показываем, а не глотаем
            self._note = f"{type(error).__name__}: {error}"
            self._pending.clear()
        self.selection.clear()
        self._after_change()

    def mouseMoveEvent(self, event) -> None:
        if self._panning == "viewport":
            self.projector.viewport.apply_move(event)
            self.update()
            return
        if self._panning is not None:
            delta = event.position().toPoint() - self._panning
            self._panning = event.position().toPoint()
            self.origin -= QtCore.QPointF(
                delta.x() / self._scale, -delta.y() / self._scale
            )
            self.update()
            return

        world = self._to_world(event.position())
        self._cursor = world
        self._snap = self._find_snap(world) if self.tool != "sketch.select" else None
        self._hover = self._pick(world) if self.tool == "sketch.select" else None
        spot = self._to_screen(world)
        was = self._relation_hover
        self._relation_hover = (self.pick_relation(spot)
                                if self.tool == "sketch.select" else None)
        if was is not self._relation_hover:
            self.update()
        previous = self._region_hover
        self._region_hover = (
            self.region_at(world)
            if self.tool == "sketch.select" and self._hover is None
               and self.show_regions
            else None
        )
        if previous is not self._region_hover:
            self.update()

        if self._dragging is not None:
            if hasattr(self._dragging, "value"):
                self._drag_dimension(self._dragging, world)
            else:
                # Перетаскивание решает эскиз на каждом шаге: связи держат
                # точку сразу, а не после отпускания кнопки.
                self.sketch.move(self._dragging, world[0], world[1])
                self.changed.emit()
                self._emit_status()
        elif self._band is not None:
            self._band = (self._band[0], world)
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == QtCore.Qt.MiddleButton:
            if self._panning == "viewport":
                self.projector.viewport.apply_release(event)
            self._panning = None
            self.setCursor(
                QtCore.Qt.ArrowCursor if self.tool == "sketch.select"
                else QtCore.Qt.CrossCursor
            )
            return
        if self._dragging is not None:
            self._dragging = None
            self._after_change()
        if self._band is not None:
            self._select_in_band(*self._band)
            self._band = None
            self.update()

    def mouseDoubleClickEvent(self, event) -> None:
        world = self._to_world(event.position())
        if self.tool in ("draw.polyline", "draw.spline"):
            self._finish_tool()
            return
        picked = self._pick(world)
        if picked is not None and hasattr(picked, "value"):
            self._edit_dimension(picked)

    def wheelEvent(self, event) -> None:
        if self.projector is not None:
            # Приближением в этом режиме управляет вид: у эскиза своего
            # масштаба нет, он живёт в координатах детали.
            self.projector.viewport.apply_wheel(event)
            self.update()
            return
        before = self._to_world(event.position())
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self._scale = max(0.05, min(400.0, self._scale * factor))
        after = self._to_world(event.position())
        # Точка под курсором обязана остаться под курсором — иначе при
        # приближении чертёж уползает и его приходится ловить.
        self.origin += QtCore.QPointF(before[0] - after[0], before[1] - after[1])
        self.update()

    def keyPressEvent(self, event) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            if self._pending:
                self._finish_tool()
            else:
                self.selection.clear()
                self.tool_finished.emit()
            self.update()
        elif event.key() in (QtCore.Qt.Key_Delete, QtCore.Qt.Key_Backspace):
            self.delete_selected()
        elif event.matches(QtGui.QKeySequence.Undo):
            self.undo()
        elif event.matches(QtGui.QKeySequence.Redo):
            self.redo()
        else:
            super().keyPressEvent(event)

    def _select_in_band(self, corner_a, corner_b) -> None:
        x0, x1 = sorted((corner_a[0], corner_b[0]))
        y0, y1 = sorted((corner_a[1], corner_b[1]))
        if (x1 - x0) * self.scale < 3 and (y1 - y0) * self.scale < 3:
            return
        for segment in self.sketch.segments:
            shape = T.describe(self.sketch, segment)
            probes = [shape.start, shape.end] if shape.kind != "circle" else [shape.center]
            if all(x0 <= p[0] <= x1 and y0 <= p[1] <= y1 for p in probes):
                if segment not in self.selection:
                    self.selection.append(segment)
        self._emit_status()

    # --- построение ---

    def _maybe_build(self) -> None:
        needed = DRAW_TOOLS.get(self.tool, 0)
        if needed and len(self._pending) >= needed:
            self._build()

    def _finish_tool(self) -> None:
        if self.tool in ("draw.polyline", "draw.spline") \
                and len(self._pending) >= 2:
            self._build()
        self._pending.clear()
        self.update()

    def _point_for(self, snap: Snap, construction: bool | None = None):
        """Точка эскиза под привязку.

        Если привязались к существующей точке — возвращается ОНА. Общая
        точка и есть совпадение: его нельзя разорвать по недосмотру, и оно
        не стоит уравнения решателю.
        """
        if snap.point is not None:
            return snap.point
        is_construction = self.construction_mode if construction is None else construction
        point = self.sketch.point(*snap.position, construction=is_construction)
        if snap.kind == "origin":
            # Привязка к началу координат означает закрепление: иначе контур
            # «привязан» лишь на вид и уезжает при первом перетаскивании.
            self.sketch.anchor(point)
        elif snap.kind == "on" and snap.segment is not None:
            if snap.segment.kind == "line":
                self.sketch.point_on_line(point, snap.segment)
            else:
                self.sketch.point_on_curve(point, snap.segment)
        elif snap.kind == "middle" and snap.segment is not None:
            self.sketch.midpoint(point, snap.segment)
        return point

    def _auto_axis(self, segment) -> None:
        """Навести связи на только что построенный отрезок.

        Порядок проверок — от самой сильной к слабой: горизонталь и
        вертикаль важнее параллельности соседу, параллельность важнее
        касания. Накладывается ОДНА связь: две наведённые сразу почти всегда
        переопределяют эскиз, и человек получает красный контур вместо
        помощи.
        """
        if self._suppress_relations:
            return
        shape = T.describe(self.sketch, segment)
        dx, dy = shape.end[0] - shape.start[0], shape.end[1] - shape.start[1]
        if abs(dx) < 1e-9 and abs(dy) < 1e-9:
            return
        angle = abs(math.degrees(math.atan2(dy, dx))) % 180.0
        try:
            if angle < AXIS_TOLERANCE or angle > 180.0 - AXIS_TOLERANCE:
                self.sketch.horizontal(segment)
                return
            if abs(angle - 90.0) < AXIS_TOLERANCE:
                self.sketch.vertical(segment)
                return
        except Exception:  # noqa: BLE001 — связь могла оказаться избыточной
            return
        self._auto_neighbour(segment, shape, angle)

    def _auto_neighbour(self, segment, shape, angle: float) -> None:
        """Параллельность, перпендикулярность или касание к соседу по контуру.

        Сосед — объект, с которым новый отрезок делит точку. Смотреть на
        весь эскиз было бы неверно: совпадение направления с далёким
        отрезком случайно, а с примыкающим — почти всегда намеренно.
        """
        shared = {id(point) for point in segment.ends}
        for other in self.sketch.segments:
            if other is segment or not (shared & {id(p) for p in other.ends}):
                continue
            if other.kind == "arc":
                self._try(self.sketch.tangent, segment, other)
                return
            if other.kind != "line":
                continue
            neighbour = T.describe(self.sketch, other)
            their = abs(math.degrees(math.atan2(
                neighbour.end[1] - neighbour.start[1],
                neighbour.end[0] - neighbour.start[0],
            ))) % 180.0
            difference = abs(angle - their)
            if difference < AXIS_TOLERANCE or difference > 180.0 - AXIS_TOLERANCE:
                self._try(self.sketch.parallel, segment, other)
                return
            if abs(difference - 90.0) < AXIS_TOLERANCE:
                self._try(self.sketch.perpendicular, segment, other)
                return

    def _try(self, apply, *arguments) -> None:
        """Наложить связь, не роняя построение, если она избыточна."""
        try:
            apply(*arguments)
        except Exception:  # noqa: BLE001
            pass

    def _build(self) -> None:
        snaps = list(self._pending)
        self._pending.clear()
        self._begin_change()
        sketch = self.sketch
        construction = self.construction_mode
        try:
            if self.tool == "draw.line":
                segment = sketch.line(self._point_for(snaps[0]), self._point_for(snaps[1]),
                                      construction=construction)
                self._auto_axis(segment)
            elif self.tool == "draw.spline":
                # Через точки, а не по полюсам: щёлкают там, где кривая
                # должна ПРОЙТИ. Полюсы считаются по ним и становятся
                # точками эскиза — править потом можно и за них.
                sketch.spline_through([snap.position for snap in snaps],
                                      construction=construction)
            elif self.tool == "draw.polyline":
                points = [self._point_for(snap) for snap in snaps]
                for index in range(len(points) - 1):
                    self._auto_axis(
                        sketch.line(points[index], points[index + 1],
                                    construction=construction)
                    )
            elif self.tool == "draw.rectangle":
                (x1, y1), (x2, y2) = snaps[0].position, snaps[1].position
                sketch.rectangle(x1, y1, x2, y2, construction=construction)
            elif self.tool == "draw.center_rectangle":
                (cx, cy), (x, y) = snaps[0].position, snaps[1].position
                sketch.center_rectangle(cx, cy, x, y, construction=construction)
            elif self.tool == "draw.circle":
                center = self._point_for(snaps[0], construction=True)
                radius = geom2d.distance(snaps[0].position, snaps[1].position)
                sketch.circle(center, max(radius, 1e-3), construction=construction)
            elif self.tool == "draw.ellipse":
                centre = snaps[0].position
                major = geom2d.distance(centre, snaps[1].position)
                # Малая полуось — РАССТОЯНИЕ от третьей точки до большой
                # оси, а не до центра: так третий щелчок не обязан лежать
                # на малой оси, и эллипс не прыгает от неточного попадания.
                minor = geom2d.distance_to_line(
                    snaps[2].position, centre, snaps[1].position)
                angle = math.degrees(geom2d.angle_at(centre,
                                                     snaps[1].position))
                sketch.ellipse_at(*centre, max(major, 1e-3),
                                  max(minor, 1e-3), angle,
                                  construction=construction)
            elif self.tool == "draw.hyperbola":
                centre = snaps[0].position
                tip = snaps[1].position
                major = max(geom2d.distance(centre, tip), 1e-3)
                angle = math.degrees(geom2d.angle_at(centre, tip))
                minor = max(geom2d.distance_to_line(
                    snaps[2].position, centre, tip), 1e-3)
                start = _hyperbolic(centre, angle, minor, snaps[3].position)
                end = _hyperbolic(centre, angle, minor, snaps[4].position)
                if abs(end - start) < 1e-6:
                    end = start + 1.0
                sketch.hyperbola_at(*centre, major, minor, angle, start, end,
                                    construction=construction)
            elif self.tool == "draw.parabola":
                vertex = snaps[0].position
                focus = snaps[1].position
                focal = max(geom2d.distance(vertex, focus), 1e-3)
                angle = math.degrees(geom2d.angle_at(vertex, focus))
                start = _across_axis(vertex, angle, snaps[2].position)
                end = _across_axis(vertex, angle, snaps[3].position)
                if abs(end - start) < 1e-6:
                    end = start + 1.0
                sketch.parabola_at(*vertex, focal, angle, start, end,
                                   construction=construction)
            elif self.tool == "draw.ellipse_arc":
                centre = snaps[0].position
                major = max(geom2d.distance(centre, snaps[1].position), 1e-3)
                minor = max(geom2d.distance_to_line(
                    snaps[2].position, centre, snaps[1].position), 1e-3)
                angle = math.degrees(geom2d.angle_at(centre,
                                                     snaps[1].position))
                # Углы дуги СОБСТВЕННЫЕ, а не полярные: у эллипса это
                # разные вещи, и щелчок «под 45°» соответствует другому
                # параметру. Считается он по проекции щелчка на оси
                # эллипса — тем же способом, каким точка на эллипсе и
                # задаётся.
                start = _own_angle(centre, major, minor, angle,
                                   snaps[3].position)
                end = _own_angle(centre, major, minor, angle,
                                 snaps[4].position)
                sketch.ellipse_arc_at(*centre, major, minor, angle,
                                      start, end,
                                      construction=construction)
            elif self.tool == "draw.arc":
                sketch.arc(
                    self._point_for(snaps[0], construction=True),
                    self._point_for(snaps[1]),
                    self._point_for(snaps[2]),
                    construction=construction,
                )
            elif self.tool == "draw.arc_3p":
                sketch.arc_through(*snaps[0].position, *snaps[1].position,
                                   *snaps[2].position, construction=construction)
            elif self.tool == "draw.slot":
                axis_start, axis_end = snaps[0].position, snaps[1].position
                width = 2.0 * geom2d.distance_to_segment(
                    snaps[2].position, axis_start, axis_end
                )
                sketch.slot(*axis_start, *axis_end, max(width, 1e-3),
                            construction=construction)
            elif self.tool == "draw.polygon":
                center = snaps[0].position
                radius = geom2d.distance(center, snaps[1].position)
                sketch.polygon(*center, max(radius, 1e-3), self.polygon_sides,
                               construction=construction)
            elif self.tool == "draw.circle_3p":
                sketch.circle_through(*snaps[0].position, *snaps[1].position,
                                      *snaps[2].position, construction=construction)
            elif self.tool == "draw.rectangle_3p":
                sketch.rectangle_3p(*snaps[0].position, *snaps[1].position,
                                    *snaps[2].position, construction=construction)
            elif self.tool == "draw.parallelogram":
                sketch.parallelogram(*snaps[0].position, *snaps[1].position,
                                     *snaps[2].position, construction=construction)
            elif self.tool == "draw.center_slot":
                axis_end = snaps[1].position
                width = 2.0 * geom2d.distance_to_segment(
                    snaps[2].position, snaps[0].position, axis_end
                )
                sketch.center_slot(*snaps[0].position, *axis_end, max(width, 1e-3),
                                   construction=construction)
            elif self.tool == "draw.tangent_arc":
                anchor = snaps[0]
                if anchor.point is None or anchor.segment is None:
                    # Касательная дуга продолжает СУЩЕСТВУЮЩИЙ объект: без
                    # него касаться нечего, и молча строить обычную дугу
                    # значило бы сделать не то, что просили.
                    raise SketchError("начните касательную дугу с конца объекта")
                sketch.tangent_arc(anchor.segment, anchor.point,
                                   *snaps[1].position, construction=construction)
            elif self.tool == "draw.point":
                self._point_for(snaps[0])
        except Exception as error:  # noqa: BLE001
            self._note = f"{type(error).__name__}: {error}"
        self._after_change()

    # --- связи ---

    def apply_relation(self, kind: str) -> None:
        """Наложить связь на выбранное. Разбор «что выбрано» — здесь: одна
        и та же кнопка означает разное для точек и для отрезков."""
        points = [e for e in self.selection if _is_point(e)]
        segments = [e for e in self.selection if _is_segment(e)]
        sketch = self.sketch
        snapshot = sketch.to_dict()
        try:
            if kind == "coincident" and len(points) >= 2:
                for other in points[1:]:
                    sketch.coincident(points[0], other)
            elif kind in ("horizontal", "vertical") and segments:
                for segment in segments:
                    getattr(sketch, kind)(segment)
            elif kind in ("parallel", "perpendicular", "equal") and len(segments) >= 2:
                for other in segments[1:]:
                    getattr(sketch, kind)(segments[0], other)
            elif kind == "tangent" and len(segments) >= 2:
                sketch.tangent(segments[0], segments[1])
            elif kind == "concentric" and len(segments) >= 2:
                sketch.concentric(segments[0], segments[1])
            elif kind == "equal_radius" and len(segments) >= 2:
                sketch.equal_radius(segments[0], segments[1])
            elif kind == "midpoint" and points and segments:
                sketch.midpoint(points[0], segments[0])
            elif kind == "symmetric" and len(points) >= 2 and segments:
                sketch.symmetric(points[0], points[1], segments[0])
            elif kind == "point_on" and points and segments:
                if segments[0].kind == "line":
                    sketch.point_on_line(points[0], segments[0])
                else:
                    sketch.point_on_curve(points[0], segments[0])
            elif kind == "anchor" and points:
                for point in points:
                    sketch.anchor(point)
            else:
                self._note = _relation_requirement(kind)
                self.update()
                self.status.emit(self._status_text())
                return
        except Exception as error:  # noqa: BLE001
            self._note = f"связь не наложена: {error}"
            self._after_change()
            return
        # Снимок кладётся в историю ТОЛЬКО когда связь действительно
        # наложена: иначе отмена тратилась бы на неудавшиеся попытки.
        self._record(snapshot)
        self._note = ""
        self._after_change()

    def delete_selected(self) -> None:
        # Выбранная связь удаляется вместе со своим значком, а не с
        # объектами, к которым она приложена: щёлкнув по значку, человек
        # целился в связь, и удаление отрезка было бы неожиданностью.
        if self.selected_relation is not None:
            self._begin_change()
            self.sketch.delete_constraint(self.selected_relation)
            self.selected_relation = None
            self.selection.clear()
            self._note = ""
            self._after_change()
            return
        if not self.selection:
            return
        self._begin_change()
        geometry = [e for e in self.selection if _is_point(e) or _is_segment(e)]
        relations = [e for e in self.selection if not (_is_point(e) or _is_segment(e))]
        for relation in relations:
            self.sketch.delete_constraint(relation)
        if geometry:
            self.sketch.delete(geometry)
        self.selection.clear()
        self._note = ""
        self._after_change()

    def toggle_construction(self) -> None:
        """Перевести выбранное во вспомогательную геометрию и обратно.

        Без выбора переключается режим рисования — следующие объекты будут
        вспомогательными.
        """
        segments = [e for e in self.selection if _is_segment(e)]
        if not segments:
            self.construction_mode = not self.construction_mode
            self._note = ("рисование вспомогательной геометрии"
                          if self.construction_mode else "обычное рисование")
            self.update()
            self.status.emit(self._status_text())
            return
        self._begin_change()
        for segment in segments:
            segment.construction = not segment.construction
            for entry in self.sketch._log.entries:
                if entry.get("id") == segment.id:
                    entry["construction"] = segment.construction
        self._after_change()

    # --- размеры ---

    @staticmethod
    def _axis_ends(chosen):
        """Две точки для осевого размера: концы отрезка или два щелчка."""
        if chosen and _is_segment(chosen[0]) and chosen[0].kind == "line":
            return chosen[0].ends
        if len(chosen) >= 2 and all(_is_point(e) for e in chosen[:2]):
            return chosen[0], chosen[1]
        return None

    def _press_dimension(self, world, position=None) -> None:
        picked = self._pick(world)
        if picked is None and position is not None and self.face_probe is not None:
            # Своей геометрии под курсором нет — спрашиваем ДЕТАЛЬ. Так
            # размер до её грани ставится тем же движением, что и любой
            # другой: отдельной командой «взять ссылку» и лишней прямой,
            # которую потом надо искать в списке, платить не приходится.
            picked = self.face_probe(position)
        if picked is None:
            # Щелчок в пустоту ЗАВЕРШАЕТ набор: прямая, выбранная одна,
            # мерится по длине. Без этого шага длину поставить было
            # некуда — прямая бралась в размер сразу, и второй объект
            # (другая прямая, точка, перенесённое ребро детали) набрать
            # было уже нельзя.
            if self.tool == "dim.smart" and len(self._pending) == 1:
                single = self._pending[0].segment
                if single is not None and single.kind == "line":
                    snapshot = self.sketch.to_dict()
                    self._pending.clear()
                    start, end = single.ends
                    self.sketch.dimension(
                        start, end,
                        round(geom2d.distance(self.sketch.coordinates(start),
                                              self.sketch.coordinates(end)), 3),
                    )
                    self._record(snapshot)
                    self._after_change()
                    return
            self._pending.clear()
            return
        snapshot = self.sketch.to_dict()
        before = len(self.sketch.dimensions)
        self._pending.append(Snap(world, point=picked if _is_point(picked) else None,
                                  segment=picked if _is_segment(picked) else None))
        chosen = [s.point or s.segment for s in self._pending]

        try:
            if self.tool == "dim.radius" and _is_segment(chosen[0]):
                self._pending.clear()
                _, radius = self.sketch.circle_geometry(chosen[0])
                self.sketch.radius(chosen[0], round(radius, 3))
            elif self.tool == "dim.diameter" and _is_segment(chosen[0]):
                self._pending.clear()
                _, radius = self.sketch.circle_geometry(chosen[0])
                self.sketch.diameter(chosen[0], round(2.0 * radius, 3))
            elif self.tool == "dim.angle":
                if len(chosen) < 2:
                    return
                self._pending.clear()
                self._add_angle(chosen[0], chosen[1])
            elif self.tool in ("dim.horizontal", "dim.vertical"):
                axis = "x" if self.tool == "dim.horizontal" else "y"
                ends = self._axis_ends(chosen)
                if ends is None:
                    return
                self._pending.clear()
                start, finish = ends
                first = self.sketch.coordinates(start)
                second = self.sketch.coordinates(finish)
                span = (second[0] - first[0]) if axis == "x" else (second[1] - first[1])
                if abs(span) < 1e-6:
                    self._note = ("размер по этой оси нулевой — "
                                  "выберите точки, разнесённые вдоль неё")
                    self._after_change()
                    return
                self.sketch.dimension_axis(start, finish, round(abs(span), 3), axis)
            else:  # умный размер
                if _is_segment(chosen[0]) and chosen[0].kind in ("circle", "arc"):
                    self._pending.clear()
                    _, radius = self.sketch.circle_geometry(chosen[0])
                    if chosen[0].kind == "circle":
                        self.sketch.diameter(chosen[0], round(2.0 * radius, 3))
                    else:
                        self.sketch.radius(chosen[0], round(radius, 3))
                elif _is_segment(chosen[0]) and len(chosen) == 1:
                    # Прямая, выбранная первой, ЖДЁТ второго объекта: с ним
                    # это расстояние или угол, без него — своя длина. Что
                    # именно, решает следующий щелчок, в том числе щелчок в
                    # пустоту.
                    self._note = ("укажите второй объект — или щёлкните "
                                  "в стороне, чтобы поставить длину")
                    self._after_change()
                    return
                elif len(chosen) >= 2 and all(_is_point(e) for e in chosen[:2]):
                    self._pending.clear()
                    self.sketch.dimension(
                        chosen[0], chosen[1],
                        round(geom2d.distance(self.sketch.coordinates(chosen[0]),
                                              self.sketch.coordinates(chosen[1])), 3),
                    )
                elif len(chosen) >= 2 and all(_is_segment(e) for e in chosen[:2]):
                    self._pending.clear()
                    self._add_distance_or_angle(chosen[0], chosen[1])
                elif len(chosen) >= 2 and _is_point(chosen[0]) \
                        and _is_segment(chosen[1]):
                    self._pending.clear()
                    self._add_to_line(chosen[0], chosen[1])
                elif len(chosen) >= 2 and _is_segment(chosen[0]) \
                        and _is_point(chosen[1]):
                    self._pending.clear()
                    self._add_to_line(chosen[1], chosen[0])
                elif len(chosen) >= 2:
                    # Пара набрана, а размера для неё нет. Молчать нельзя:
                    # два щелчка выглядят принятыми, ничего не появляется,
                    # и человек щёлкает третий раз — теперь уже по другому
                    # объекту, и набор окончательно путается.
                    self._pending.clear()
                    self._note = ("для этой пары размера нет: выберите две "
                                  "точки, точку и прямую, две прямые, "
                                  "отрезок, дугу или окружность")
                    self._after_change()
                    return
                else:
                    return
        except Exception as error:  # noqa: BLE001
            self._note = f"размер не поставлен: {error}"
            self._pending.clear()
        if len(self.sketch.dimensions) != before:
            self._record(snapshot)
            # Подсказка «укажите второй объект» относилась к НЕЗАКОНЧЕННОМУ
            # набору. Оставленная после успеха, она просит того, что уже
            # сделано.
            self._note = ""
        self._after_change()

    def _add_to_line(self, point, segment) -> None:
        """Расстояние от точки до прямой — по перпендикуляру."""
        first, second = (self.sketch.coordinates(end) for end in segment.ends)
        away = geom2d.distance_to_line(self.sketch.coordinates(point),
                                       first, second)
        if away < 1e-6:
            self._note = ("точка лежит на этой прямой — расстояние нулевое; "
                          "поставьте связь «точка на прямой»")
            self._after_change()
            return
        self.sketch.dimension_to_line(point, segment, round(away, 3))

    def _add_distance_or_angle(self, first, second) -> None:
        """Две прямые: параллельные мерятся расстоянием, прочие — углом.

        Угол между параллельными равен нулю и ничего не задаёт, а
        расстояние между ними — обычный чертёжный размер. Раньше сюда
        уходил угол в обоих случаях, и размер между параллельными
        поставить было нечем.
        """
        if _is_segment(first) and _is_segment(second) \
                and first.kind == "line" and second.kind == "line":
            a = T.describe(self.sketch, first)
            b = T.describe(self.sketch, second)
            first_angle = math.atan2(a.end[1] - a.start[1], a.end[0] - a.start[0])
            second_angle = math.atan2(b.end[1] - b.start[1], b.end[0] - b.start[0])
            between = abs(math.degrees(second_angle - first_angle)) % 180.0
            if between < ANGLE_PARALLEL or abs(between - 180.0) < ANGLE_PARALLEL:
                away = geom2d.distance_to_line(
                    self.sketch.coordinates(second.ends[0]), a.start, a.end)
                if away < 1e-6:
                    self._note = ("прямые лежат на одной линии — "
                                  "расстояние между ними нулевое")
                    self._after_change()
                    return
                self.sketch.dimension_to_line(
                    second.ends[0], first, round(away, 3))
                return
        self._add_angle(first, second)

    def _add_angle(self, first, second) -> None:
        # Угол ставится между ПРЯМЫМИ. У решателя ограничение угла принимает
        # только их, и на окружности он отвечал ошибкой C++ с перечислением
        # типов аргументов — она уходила прямо в строку состояния.
        for item in (first, second):
            if not _is_segment(item) or item.kind != "line":
                self._note = ("угол ставится между двумя прямыми — "
                              "для дуги и окружности есть радиус и диаметр")
                self._after_change()
                return
        a = T.describe(self.sketch, first)
        b = T.describe(self.sketch, second)
        angle_a = math.atan2(a.end[1] - a.start[1], a.end[0] - a.start[0])
        angle_b = math.atan2(b.end[1] - b.start[1], b.end[0] - b.start[0])
        value = abs(math.degrees(angle_b - angle_a)) % 180.0
        self.sketch.angle(first, second, round(value, 3))

    def _edit_dimension(self, dimension) -> None:
        value, ok = QtWidgets.QInputDialog.getDouble(
            self, "Размер", f"{dimension.name}:", dimension.value,
            -1e6, 1e6, 3,
        )
        if not ok:
            return
        self._begin_change()
        try:
            self.sketch.set_dimension(dimension, value)
        except Exception as error:  # noqa: BLE001
            self._note = f"размер не изменён: {error}"
        self._after_change()

    # Отступ размерной линии от геометрии, если человек её не перетаскивал.
    DIMENSION_OFFSET = 12.0

    def _dimension_layout(self, dimension):
        """Полная раскладка размера: где выносные, где размерная, где подпись.

        Размер на чертеже — не подпись с двумя поводками к концам. Это
        выносные линии от измеряемых точек, отведённая от геометрии
        размерная линия со стрелками и надпись на ней. Разница не
        косметическая: поводки, сходящиеся к подписи, пересекают чертёж
        наискось и читаются как ещё одна линия контура.
        """
        try:
            kind = dimension.kind
            if kind in ("radius", "diameter"):
                center, radius = self.sketch.circle_geometry(dimension.targets[0])
                angle = dimension.offset[0] if dimension.offset[0] else math.pi / 4.0
                reach = radius + (dimension.offset[1] or self.DIMENSION_OFFSET)
                touch = geom2d.point_at_angle(center, radius, angle)
                label = geom2d.point_at_angle(center, reach, angle)
                start = center if kind == "diameter" else touch
                if kind == "diameter":
                    start = geom2d.point_at_angle(center, radius, angle + math.pi)
                return {"kind": kind, "line": (start, label), "arrows": [(label, touch)],
                        "label": label, "extensions": []}

            if kind == "angle":
                first = T.describe(self.sketch, dimension.targets[0])
                second = T.describe(self.sketch, dimension.targets[1])
                crossing = geom2d.line_line(first.start, first.end,
                                            second.start, second.end)
                corner = crossing[0] if crossing else geom2d.midpoint(
                    first.start, second.start
                )
                reach = dimension.offset[1] or self.DIMENSION_OFFSET * 2.0
                angle_a = geom2d.angle_at(corner, _far_end(corner, first))
                angle_b = geom2d.angle_at(corner, _far_end(corner, second))
                middle = angle_a + geom2d.arc_sweep(angle_a, angle_b) / 2.0
                return {
                    "kind": kind, "line": None, "arrows": [],
                    "arc": (corner, reach, angle_a, angle_b),
                    "label": geom2d.point_at_angle(corner, reach * 1.15, middle),
                    "extensions": [],
                }

            first = self.sketch.coordinates(dimension.targets[0])
            second = self.sketch.coordinates(dimension.targets[1])
            axis = geom2d.direction(first, second)
            if axis == (0.0, 0.0):
                return None
            perpendicular, along = dimension.offset
            if perpendicular == 0.0 and along == 0.0:
                perpendicular = self.DIMENSION_OFFSET
            shift = geom2d.scale(geom2d.normal(axis), perpendicular)
            start = geom2d.add(first, shift)
            end = geom2d.add(second, shift)
            label = geom2d.add(geom2d.midpoint(start, end), geom2d.scale(axis, along))
            # Выносная линия чуть перекрывает размерную — так на чертеже
            # видно, что она именно доведена, а не оборвана не дойдя.
            overshoot = geom2d.scale(geom2d.normal(axis),
                                     perpendicular + math.copysign(2.0, perpendicular))
            return {
                "kind": "distance",
                "line": (start, end),
                "arrows": [(label, start), (label, end)],
                "label": label,
                "extensions": [(first, geom2d.add(first, overshoot)),
                               (second, geom2d.add(second, overshoot))],
            }
        except Exception:  # noqa: BLE001 — размер мог остаться без цели
            return None

    def _dimension_anchor(self, dimension):
        layout = self._dimension_layout(dimension)
        return layout["label"] if layout else None

    # --- изменение состояния ---

    def _after_change(self) -> None:
        self.changed.emit()
        self._emit_status()
        self.update()

    def _emit_status(self) -> None:
        self.status.emit(self._status_text())

    def _status_text(self) -> str:
        state = self.sketch.status()
        parts = [state.text]
        if self.selection:
            parts.append(f"выбрано: {len(self.selection)}")
        parts.append(self._profile_note())
        if self.construction_mode:
            parts.append("вспомогательная геометрия")
        # Потерянная ссылка говорит о себе сразу, а не в момент, когда
        # эскиз необъяснимо перестал соответствовать детали.
        lost = getattr(self.sketch, "dangling", [])
        if lost:
            parts.append(
                f"ссылок на деталь потеряно: {len(lost)} "
                f"({', '.join(entity.label for entity in lost[:3])}) — "
                f"их объекты в детали исчезли"
            )
        if self._note:
            parts.append(self._note)
        return "    |    ".join(part for part in parts if part)

    def _profile_note(self) -> str:
        """Что именно станет профилем операции.

        Пишется всегда, когда областей больше одной. Умолчание — «вложенный
        контур это отверстие» — совпадает с отраслевой практикой, но
        догадаться о том, что его МОЖНО изменить, неоткуда: заливка
        показывает области, а не то, что они выбираются.
        """
        found = self.regions() if self.show_regions else []
        if len(found) < 2:
            return ""
        picked = [region for region in found if self.region_is_picked(region)]
        if picked:
            area = sum(region.area for region in picked)
            return (f"профиль: выбрано областей {len(picked)} из {len(found)}, "
                    f"{area:.1f} мм²")
        holes = sum(len(region.inner) for region in found)
        tail = (f", вложенных контуров {holes} — они станут отверстиями"
                if holes else "")
        return (f"профиль: весь эскиз, областей {len(found)}{tail}. "
                f"Щёлкните область, чтобы выбрать её одну")

    def relations_of_selection(self) -> list:
        result = []
        for entity in self.selection:
            result.extend(self.sketch.relations_on(entity))
        return result

    # --- отрисовка ---

    def paintEvent(self, _event) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self.paint(painter)

    def paint(self, painter: QtGui.QPainter) -> None:
        """Нарисовать эскиз. Отделено от события, чтобы то же рисование
        годилось и для наложения поверх трёхмерного вида."""
        if self.projector is None:
            painter.fillRect(self.rect(), COLOR_BACKGROUND)

        if self.show_grid:
            self._draw_grid(painter)
        self._draw_axes(painter)
        if self.show_regions:
            self._draw_regions(painter)

        state = self.sketch.status()
        if not state.ok:
            # Красный — не украшение: противоречивый эскиз выглядит как
            # обычный, и без цвета о нём узнают на отказе операции.
            base = COLOR_CONFLICT
        elif state.dof == 0:
            base = COLOR_FIXED
        else:
            base = COLOR_FREE

        for segment in self.sketch.segments:
            self._draw_segment(painter, segment, base)
        if self.show_points:
            for point in self.sketch.points:
                self._draw_point(painter, point, base)
        if self.show_dimensions:
            for dimension in self.sketch.dimensions:
                self._draw_dimension(painter, dimension)
        if self.show_relations:
            self._draw_relations(painter)

        self._draw_marks(painter)
        self._draw_preview(painter)
        self._draw_snap(painter)
        self._draw_readout(painter)
        self._draw_band(painter)

    def _grid_step(self) -> float:
        """Шаг сетки с учётом масштаба.

        Один и тот же шаг используется и для рисования, и для привязки:
        притягиваться к линиям, которых на экране нет, — вернейший способ
        получить координаты, взявшиеся ниоткуда.
        """
        step = self.grid_step
        while step * self.scale < 8:
            step *= 5
        return step

    CURVE_FACETS = 72

    def _curve_points(self, center, radius, start_angle, sweep) -> list:
        """Кривая точками. При наклонной камере окружность на экране —
        эллипс, и рисовать её `drawEllipse` по описанному прямоугольнику
        нельзя: получится окружность там, где должен быть эллипс."""
        steps = max(8, int(self.CURVE_FACETS * abs(sweep) / geom2d.TAU))
        return [
            self._to_screen(
                geom2d.point_at_angle(center, radius, start_angle + sweep * i / steps)
            )
            for i in range(steps + 1)
        ]

    def _stroke_curve(self, painter, center, radius, start_angle, sweep) -> None:
        points = self._curve_points(center, radius, start_angle, sweep)
        path = QtGui.QPainterPath(points[0])
        for point in points[1:]:
            path.lineTo(point)
        painter.drawPath(path)

    def _plane_extent(self) -> float:
        """Насколько далеко от нуля тянуть сетку и оси плоскости."""
        reach = 40.0
        for point in self.sketch.points:
            try:
                x, y = self.sketch.coordinates(point)
            except Exception:  # noqa: BLE001
                continue
            reach = max(reach, abs(x), abs(y))
        return reach * 1.4

    def _draw_grid(self, painter) -> None:
        if self.projector is not None:
            self._draw_plane_grid(painter)
            return
        step = self._grid_step()
        left, top = self._to_world(QtCore.QPointF(0, 0))
        right, bottom = self._to_world(
            QtCore.QPointF(self.width(), self.height())
        )
        painter.setPen(QtGui.QPen(COLOR_GRID, 1))
        start = math.floor(left / step) * step
        index = 0
        x = start
        while x < right:
            major = abs(round(x / (step * 5)) * step * 5 - x) < step / 10
            painter.setPen(QtGui.QPen(COLOR_GRID_MAJOR if major else COLOR_GRID, 1))
            screen = self._to_screen((x, 0)).x()
            painter.drawLine(QtCore.QPointF(screen, 0), QtCore.QPointF(screen, self.height()))
            x += step
            index += 1
            if index > 400:
                break
        y = math.floor(bottom / step) * step
        index = 0
        while y < top:
            major = abs(round(y / (step * 5)) * step * 5 - y) < step / 10
            painter.setPen(QtGui.QPen(COLOR_GRID_MAJOR if major else COLOR_GRID, 1))
            screen = self._to_screen((0, y)).y()
            painter.drawLine(QtCore.QPointF(0, screen), QtCore.QPointF(self.width(), screen))
            y += step
            index += 1
            if index > 400:
                break

    def _draw_plane_grid(self, painter) -> None:
        """Сетка на плоскости эскиза, а не на экране.

        В трёхмерном виде сетка обязана лежать в той же плоскости, что и
        эскиз: экранная сетка при повороте детали осталась бы висеть перед
        глазами и только сбивала бы с толку насчёт того, где эта плоскость.
        """
        reach = self._plane_extent()
        step = self.grid_step
        while step * self.scale < 8:
            step *= 5
        count = int(reach / step) + 1
        if count > 200:
            return
        # Поверх трёхмерного вида сетка полупрозрачна. Те же плотные линии,
        # что уместны на белом поле, на тёмной сцене перебивают сам эскиз —
        # ради которого на неё и смотрят.
        minor = QtGui.QColor(COLOR_GRID)
        minor.setAlpha(70)
        major_color = QtGui.QColor(COLOR_GRID_MAJOR)
        major_color.setAlpha(120)
        for index in range(-count, count + 1):
            offset = index * step
            major = index % 5 == 0
            painter.setPen(QtGui.QPen(major_color if major else minor, 1))
            painter.drawLine(self._to_screen((offset, -reach)),
                             self._to_screen((offset, reach)))
            painter.drawLine(self._to_screen((-reach, offset)),
                             self._to_screen((reach, offset)))

    ORIGIN_PIXELS = 16.0

    def _draw_axes(self, painter) -> None:
        """Начало координат эскиза — короткий знак, а не бесконечные оси.

        Оси во всю плоскость перечёркивали деталь двумя светлыми линиями,
        и их принимали за геометрию. На чертеже начало отмечают знаком; его
        размер задан в пикселях, поэтому при приближении он не разрастается
        на весь экран.
        """
        zero = self._to_screen((0.0, 0.0))
        reach = self.ORIGIN_PIXELS
        painter.setPen(QtGui.QPen(COLOR_ORIGIN, 1.6))
        if self.projector is not None:
            # Направления берутся у самой плоскости: на повёрнутой камере
            # знак обязан лежать в ней, а не стоять по экрану.
            along = self._to_screen((1.0, 0.0)) - zero
            up = self._to_screen((0.0, 1.0)) - zero
            for direction in (along, up):
                length = math.hypot(direction.x(), direction.y())
                if length < 1e-6:
                    continue
                step = QtCore.QPointF(
                    direction.x() / length * reach, direction.y() / length * reach
                )
                painter.drawLine(zero - step, zero + step)
        else:
            painter.drawLine(QtCore.QPointF(zero.x() - reach, zero.y()),
                             QtCore.QPointF(zero.x() + reach, zero.y()))
            painter.drawLine(QtCore.QPointF(zero.x(), zero.y() - reach),
                             QtCore.QPointF(zero.x(), zero.y() + reach))
        painter.setBrush(QtGui.QBrush(COLOR_ORIGIN))
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawEllipse(zero, 2.6, 2.6)

    def _entity_pen(self, entity, base) -> QtGui.QPen:
        if entity in self.selection:
            pen = QtGui.QPen(COLOR_SELECTED, 2.4)
        elif entity is self._hover:
            pen = QtGui.QPen(COLOR_HOVER, 2.2)
        elif getattr(entity, "dangling", False):
            pen = QtGui.QPen(COLOR_DANGLING, 2.0)
            pen.setDashPattern([3.0, 3.0])
        elif getattr(entity, "external", ""):
            pen = QtGui.QPen(COLOR_EXTERNAL, 1.6)
            pen.setDashPattern([8.0, 3.0, 2.0, 3.0])
        elif getattr(entity, "construction", False):
            pen = QtGui.QPen(COLOR_CONSTRUCTION, 1.2)
            pen.setDashPattern([6.0, 4.0])
        else:
            pen = QtGui.QPen(base, 1.8)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        return pen

    def _draw_segment(self, painter, segment, base) -> None:
        painter.setPen(self._entity_pen(segment, base))
        painter.setBrush(QtCore.Qt.NoBrush)
        self._draw_shape(painter, T.describe(self.sketch, segment))

    def _draw_shape(self, painter, shape) -> None:
        if shape.kind == "line":
            painter.drawLine(self._to_screen(shape.start), self._to_screen(shape.end))
        elif shape.kind == "circle":
            self._draw_circle(painter, shape.center, shape.radius)
        elif shape.kind in ("ellipse", "spline", "ellipse_arc"):
            self._draw_curve(painter, shape)
        else:
            self._draw_arc(painter, shape)

    def _draw_curve(self, painter, shape) -> None:
        """Кривая без короткой формулы — ломаной по её параметру.

        Через `drawEllipse` с поворотом холста было бы короче, но эскиз
        показывается и наложенным на деталь — в проекции, — а там поворот
        холста уже ничего не значит. Одна ломаная годится в обоих случаях.
        """
        outline = T.curve_outline_of(shape)
        path = QtGui.QPainterPath(self._to_screen(outline[0]))
        for point in outline[1:]:
            path.lineTo(self._to_screen(point))
        painter.drawPath(path)

    def _draw_circle(self, painter, center, radius) -> None:
        if self.projector is not None:
            self._stroke_curve(painter, center, radius, 0.0, geom2d.TAU)
            return
        painter.drawEllipse(
            self._to_screen(center), radius * self.scale, radius * self.scale
        )

    def _draw_arc(self, painter, shape) -> None:
        sweep = geom2d.arc_sweep(shape.start_angle, shape.end_angle)
        if self.projector is not None:
            self._stroke_curve(painter, shape.center, shape.radius,
                               shape.start_angle, sweep)
            return
        center = self._to_screen(shape.center)
        radius = shape.radius * self.scale
        rect = QtCore.QRectF(
            center.x() - radius, center.y() - radius, 2 * radius, 2 * radius
        )
        # Qt отсчитывает углы против часовой в 1/16 градуса, но ось Y на
        # экране смотрит вниз — поэтому знак раствора обратный.
        painter.drawArc(
            rect,
            int(math.degrees(shape.start_angle) * 16),
            int(math.degrees(sweep) * 16),
        )

    def _draw_point(self, painter, point, base) -> None:
        position = self._to_screen(self.sketch.coordinates(point))
        if point in self.selection:
            color, size = COLOR_SELECTED, 4.0
        elif point is self._hover:
            color, size = COLOR_HOVER, 4.0
        elif point.dangling:
            color, size = COLOR_DANGLING, 4.0
        elif point.external:
            color, size = COLOR_EXTERNAL, 3.6
        elif point.construction:
            color, size = COLOR_CONSTRUCTION, 2.4
        else:
            color, size = base, 3.0
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QBrush(color))
        painter.drawEllipse(position, size, size)

    ARROW_PIXELS = 7.0

    def _draw_dimension(self, painter, dimension) -> None:
        layout = self._dimension_layout(dimension)
        if layout is None:
            return
        color = COLOR_SELECTED if dimension in self.selection else COLOR_DIMENSION
        painter.setPen(QtGui.QPen(color, 1.0))
        painter.setBrush(QtCore.Qt.NoBrush)

        for start, end in layout["extensions"]:
            painter.drawLine(self._to_screen(start), self._to_screen(end))
        if layout["line"] is not None:
            painter.drawLine(self._to_screen(layout["line"][0]),
                             self._to_screen(layout["line"][1]))
        if "arc" in layout:
            center, radius, angle_a, angle_b = layout["arc"]
            self._draw_arc(painter, T.Shape2D(
                "arc", center=center, radius=radius,
                start_angle=angle_a, end_angle=angle_b,
            ))
        for tail, head in layout["arrows"]:
            self._draw_arrow(painter, tail, head)

        position = self._to_screen(layout["label"])
        text = dimension.text
        metrics = painter.fontMetrics()
        rect = metrics.boundingRect(text).adjusted(-4, -2, 4, 2)
        rect.moveCenter(position.toPoint())
        # Подложка под надписью: размерная линия под текстом читается хуже,
        # чем разрыв в ней, и на чертежах её именно разрывают.
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255, 235)))
        painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(QtGui.QPen(color, 1.0))
        painter.drawText(rect, QtCore.Qt.AlignCenter, text)

    def _draw_arrow(self, painter, tail, head) -> None:
        """Стрелка постоянного размера на экране: при отдалении она не должна
        схлопываться в точку, а при приближении — закрывать чертёж."""
        start = self._to_screen(tail)
        end = self._to_screen(head)
        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        painter.drawLine(start, end)
        for turn in (2.7, -2.7):
            painter.drawLine(end, QtCore.QPointF(
                end.x() + self.ARROW_PIXELS * math.cos(angle + turn),
                end.y() + self.ARROW_PIXELS * math.sin(angle + turn),
            ))

    RELATION_MARKS = {
        "horizontal": "—", "vertical": "|", "parallel": "∥", "perpendicular": "⊥",
        "equal": "=", "coincident": "•", "tangent": "◠", "concentric": "◎",
        "midpoint": "◐", "symmetric": "⟷", "anchor": "▣",
        "point_on_line": "→", "point_on_curve": "→", "equal_radius": "R=",
    }

    GLYPH_RADIUS = 9.0

    def relation_glyphs(self) -> list:
        """Значки связей: сама связь, место на экране и обозначение.

        Один список на отрисовку и на попадание мышью. Разойдись они —
        значок рисовался бы в одном месте, а нажимался в другом, и найти
        такое можно было бы только руками.
        """
        found = []
        placed: dict[tuple[int, int], int] = {}
        for constraint in self.sketch.constraints:
            mark = self.RELATION_MARKS.get(constraint.kind)
            if not mark or not constraint.targets:
                continue
            anchor = self._relation_anchor(constraint)
            if anchor is None:
                continue
            position = self._to_screen(anchor)
            # Значки одного объекта раскладываются в столбик, иначе они
            # ложатся друг на друга и не читается ни один.
            key = (int(position.x()) // 12, int(position.y()) // 12)
            level = placed.get(key, 0)
            placed[key] = level + 1
            found.append((
                constraint,
                QtCore.QPointF(position.x() + 7, position.y() - 7 - level * 11),
                mark,
            ))
        return found

    def _relation_anchor(self, constraint):
        target = constraint.targets[0]
        try:
            if _is_point(target):
                return self.sketch.coordinates(target)
            shape = T.describe(self.sketch, target)
            return (geom2d.midpoint(shape.start, shape.end)
                    if shape.kind == "line" else shape.center)
        except Exception:  # noqa: BLE001
            return None

    def pick_relation(self, position):
        """Связь под указателем. Значки мельче геометрии, поэтому у них
        свой радиус попадания и приоритет над объектами."""
        best, best_distance = None, self.GLYPH_RADIUS
        for constraint, spot, _ in self.relation_glyphs():
            # spot — начало строки на базовой линии; середина значка выше и
            # правее её. Мерить от начала строки означало бы промахиваться
            # мимо того, что видно.
            distance = math.hypot(spot.x() + 4.0 - position.x(),
                                  spot.y() - 4.0 - position.y())
            if distance < best_distance:
                best, best_distance = constraint, distance
        return best

    def _visible_relations(self) -> set:
        """Какие связи показывать.

        Общий показ выключен — значит, показываются связи ВЫБРАННОГО
        объекта. Так поведение совпадает с отраслевым: разбираясь с одним
        отрезком, не надо включать все значки эскиза разом.
        """
        if self.show_relations:
            return {id(constraint) for constraint in self.sketch.constraints}
        shown = set()
        for entity in self.selection:
            for constraint in self.sketch.relations_on(entity):
                shown.add(id(constraint))
        if self.selected_relation is not None:
            shown.add(id(self.selected_relation))
        return shown

    def _draw_relations(self, painter) -> None:
        visible = self._visible_relations()
        font = painter.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.0))
        painter.setFont(font)
        for constraint, spot, mark in self.relation_glyphs():
            if id(constraint) not in visible:
                continue
            if constraint is self.selected_relation:
                color, weight = COLOR_SELECTED, 2.0
            elif constraint is self._relation_hover:
                color, weight = COLOR_HOVER, 1.8
            else:
                color, weight = COLOR_HOVER.darker(120), 1.0
            if constraint is self.selected_relation or constraint is self._relation_hover:
                painter.setPen(QtCore.Qt.NoPen)
                painter.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255, 220)))
                painter.drawRoundedRect(
                    QtCore.QRectF(spot.x() - 3, spot.y() - 11, 14, 14), 3, 3)
            painter.setPen(QtGui.QPen(color, weight))
            painter.drawText(spot, mark)

    def _draw_preview(self, painter) -> None:
        if not self._pending:
            return
        pen = QtGui.QPen(COLOR_PREVIEW, 1.3)
        pen.setDashPattern([4.0, 3.0])
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        points = [snap.position for snap in self._pending]
        cursor = self._snap.position if self._snap else self._cursor

        if self.tool == "draw.spline":
            # Пока точек мало, показывается ломаная: кривой ещё нет, а
            # рисовать несуществующее — обманывать.
            guide = points + [cursor]
            if len(guide) >= 3:
                from protocad.sketch import bspline as bspline_module

                try:
                    poles, knots, mults = bspline_module.poles_through(guide)
                    guide = bspline_module.outline(poles, knots, mults,
                                                   mults[0] - 1)
                except Exception:  # noqa: BLE001 — точки могли совпасть
                    pass
            path = QtGui.QPainterPath(self._to_screen(guide[0]))
            for item in guide[1:]:
                path.lineTo(self._to_screen(item))
            painter.drawPath(path)
        elif self.tool in ("draw.line", "draw.polyline"):
            for index in range(len(points) - 1):
                painter.drawLine(self._to_screen(points[index]),
                                 self._to_screen(points[index + 1]))
            painter.drawLine(self._to_screen(points[-1]), self._to_screen(cursor))
        elif self.tool in ("draw.rectangle", "draw.center_rectangle"):
            corner_a, corner_b = points[0], cursor
            if self.tool == "draw.center_rectangle":
                dx, dy = abs(corner_b[0] - corner_a[0]), abs(corner_b[1] - corner_a[1])
                corner_a = (points[0][0] - dx, points[0][1] - dy)
                corner_b = (points[0][0] + dx, points[0][1] + dy)
            # Прямоугольник рисуется по четырём углам В ПЛОСКОСТИ, а не
            # экранным `drawRect`: на наклонной камере это параллелограмм.
            corners = [
                (corner_a[0], corner_a[1]), (corner_b[0], corner_a[1]),
                (corner_b[0], corner_b[1]), (corner_a[0], corner_b[1]),
            ]
            path = QtGui.QPainterPath(self._to_screen(corners[0]))
            for corner in corners[1:]:
                path.lineTo(self._to_screen(corner))
            path.closeSubpath()
            painter.drawPath(path)
        elif self.tool == "draw.circle":
            self._draw_circle(painter, points[0], geom2d.distance(points[0], cursor))
        elif self.tool == "draw.ellipse":
            centre = points[0]
            if len(points) == 1:
                major = geom2d.distance(centre, cursor)
                minor, angle = major, geom2d.angle_at(centre, cursor)
            else:
                major = geom2d.distance(centre, points[1])
                angle = geom2d.angle_at(centre, points[1])
                minor = max(geom2d.distance_to_line(cursor, centre, points[1]),
                            1e-3)
            self._draw_shape(painter, T.Shape2D(
                "ellipse", center=centre, radius=max(major, 1e-3),
                minor=minor, rotation=angle,
                start_angle=0.0, end_angle=geom2d.TAU))
        elif self.tool == "draw.hyperbola":
            centre = points[0]
            tip = points[1] if len(points) > 1 else cursor
            major = max(geom2d.distance(centre, tip), 1e-3)
            angle = geom2d.angle_at(centre, tip)
            degrees = math.degrees(angle)
            if len(points) > 2:
                minor = max(geom2d.distance_to_line(points[2], centre, tip),
                            1e-3)
            else:
                minor = max(geom2d.distance_to_line(cursor, centre, tip), 1e-3)
            if len(points) < 4:
                low, high = -1.5, 1.5
            else:
                low = _hyperbolic(centre, degrees, minor, points[3])
                high = _hyperbolic(centre, degrees, minor, cursor)
                if abs(high - low) < 1e-6:
                    high = low + 1e-3
            self._draw_shape(painter, T.Shape2D(
                "hyperbola_arc", center=centre, radius=major, minor=minor,
                rotation=angle, start_angle=low, end_angle=high))
        elif self.tool == "draw.parabola":
            vertex = points[0]
            focus = points[1] if len(points) > 1 else cursor
            focal = max(geom2d.distance(vertex, focus), 1e-3)
            angle = geom2d.angle_at(vertex, focus)
            degrees = math.degrees(angle)
            if len(points) < 3:
                # Пока концы не указаны, показываем ветвь вокруг вершины:
                # обещать дугу, которой ещё нет, нельзя.
                reach = max(focal * 4.0, 1.0)
                low, high = -reach, reach
            else:
                low = _across_axis(vertex, degrees, points[2])
                high = _across_axis(vertex, degrees, cursor)
                if abs(high - low) < 1e-6:
                    high = low + 1e-3
            self._draw_shape(painter, T.Shape2D(
                "parabola_arc", center=vertex, radius=focal, rotation=angle,
                start_angle=low, end_angle=high))
        elif self.tool == "draw.ellipse_arc":
            centre = points[0]
            if len(points) == 1:
                major = geom2d.distance(centre, cursor)
                minor, angle = major, geom2d.angle_at(centre, cursor)
                start, sweep = 0.0, geom2d.TAU
            else:
                major = max(geom2d.distance(centre, points[1]), 1e-3)
                angle = geom2d.angle_at(centre, points[1])
                if len(points) == 2:
                    minor = max(geom2d.distance_to_line(cursor, centre,
                                                        points[1]), 1e-3)
                    start, sweep = 0.0, geom2d.TAU
                else:
                    minor = max(geom2d.distance_to_line(points[2], centre,
                                                        points[1]), 1e-3)
                    degrees = math.degrees(angle)
                    first = math.radians(_own_angle(centre, major, minor,
                                                    degrees, points[3]))                         if len(points) > 3 else                         math.radians(_own_angle(centre, major, minor,
                                                degrees, cursor))
                    if len(points) > 3:
                        last = math.radians(_own_angle(centre, major, minor,
                                                       degrees, cursor))
                        start, sweep = first, geom2d.arc_sweep(first, last)
                    else:
                        # Пока указано только начало, показываем весь
                        # эллипс: обещать дугу, которой ещё нет, нельзя.
                        start, sweep = 0.0, geom2d.TAU
            self._draw_shape(painter, T.Shape2D(
                "ellipse_arc" if sweep < geom2d.TAU - 1e-9 else "ellipse",
                center=centre, radius=major, minor=minor, rotation=angle,
                start_angle=start, end_angle=start + sweep))
        elif self.tool == "draw.polygon":
            radius = geom2d.distance(points[0], cursor)
            vertices = geom2d.polygon_points(points[0], radius, self.polygon_sides)
            path = QtGui.QPainterPath(self._to_screen(vertices[0]))
            for vertex in vertices[1:]:
                path.lineTo(self._to_screen(vertex))
            path.closeSubpath()
            painter.drawPath(path)
        elif self.tool == "draw.arc" and len(points) >= 2:
            radius = geom2d.distance(points[0], points[1])
            start = geom2d.angle_at(points[0], points[1])
            end = geom2d.angle_at(points[0], cursor)
            self._draw_arc(painter, T.Shape2D(
                "arc", center=points[0], radius=radius,
                start_angle=start, end_angle=end,
            ))
        elif self.tool == "draw.slot" and len(points) >= 2:
            width = 2.0 * geom2d.distance_to_segment(cursor, points[0], points[1])
            axis = geom2d.direction(points[0], points[1])
            shift = geom2d.scale(geom2d.normal(axis), width / 2.0)
            for sign in (1.0, -1.0):
                offset = geom2d.scale(shift, sign)
                painter.drawLine(self._to_screen(geom2d.add(points[0], offset)),
                                 self._to_screen(geom2d.add(points[1], offset)))
        elif self.tool in ("edit.fillet", "edit.chamfer"):
            for snap in self._pending:
                if snap.segment is not None:
                    painter.setPen(QtGui.QPen(COLOR_SELECTED, 2.2))
                    self._draw_segment_outline(painter, snap.segment)

    def _draw_segment_outline(self, painter, segment) -> None:
        self._draw_shape(painter, T.describe(self.sketch, segment))

    def cursor_readout(self) -> str:
        """Числа, которые нужны прямо сейчас: длина, угол, радиус.

        Без них рисование превращается в «нарисуй как получится, потом
        проставь размеры». Показывается то, что определяет текущий шаг
        инструмента, а не всё подряд.
        """
        cursor = self._snap.position if self._snap else self._cursor
        if not self._pending:
            if self.tool == "sketch.select":
                return ""
            return f"{cursor[0]:.2f}; {cursor[1]:.2f}"
        first = self._pending[0].position
        last = self._pending[-1].position
        if self.tool in ("draw.circle",):
            radius = geom2d.distance(first, cursor)
            return f"R {radius:.2f}   ⌀ {2 * radius:.2f}"
        if self.tool == "draw.ellipse":
            if len(self._pending) == 1:
                return f"большая полуось {geom2d.distance(first, cursor):.2f}"
            major = geom2d.distance(first, last)
            minor = geom2d.distance_to_line(cursor, first, last)
            return f"полуоси {major:.2f} × {minor:.2f}"
        if self.tool == "draw.polygon":
            return (f"R {geom2d.distance(first, cursor):.2f}   "
                    f"сторон {self.polygon_sides}")
        if self.tool in ("draw.rectangle", "draw.center_rectangle"):
            width = abs(cursor[0] - first[0])
            height = abs(cursor[1] - first[1])
            if self.tool == "draw.center_rectangle":
                width, height = width * 2.0, height * 2.0
            return f"{width:.2f} × {height:.2f}"
        if self.tool == "draw.arc" and len(self._pending) >= 2:
            radius = geom2d.distance(first, self._pending[1].position)
            sweep = geom2d.arc_sweep(
                geom2d.angle_at(first, self._pending[1].position),
                geom2d.angle_at(first, cursor),
            )
            return f"R {radius:.2f}   {math.degrees(sweep):.1f}°"
        if self.tool == "draw.slot" and len(self._pending) >= 2:
            width = 2.0 * geom2d.distance_to_segment(cursor, first, self._pending[1].position)
            return (f"длина {geom2d.distance(first, self._pending[1].position):.2f}   "
                    f"ширина {width:.2f}")
        length = geom2d.distance(last, cursor)
        angle = math.degrees(math.atan2(cursor[1] - last[1], cursor[0] - last[0]))
        return f"{length:.2f}   {angle % 360.0:.1f}°"

    def _draw_regions(self, painter) -> None:
        """Залить области эскиза. Заливка идёт ПОД линиями и размерами.

        Наведённая и выбранная различаются плотностью, а не только цветом:
        на закрашенном эскизе одного оттенка выбор не читается.
        """
        painter.setPen(QtCore.Qt.NoPen)
        for region in self.regions():
            path = QtGui.QPainterPath()
            path.addPolygon(QtGui.QPolygonF(
                [self._to_screen(point) for point in region.outer.outline]))
            for hole in region.inner:
                inner = QtGui.QPainterPath()
                inner.addPolygon(QtGui.QPolygonF(
                    [self._to_screen(point) for point in hole.outline]))
                path = path.subtracted(inner)
            if self.region_is_picked(region):
                color = COLOR_REGION_PICKED
            elif region is self._region_hover:
                color = COLOR_REGION_HOVER
            else:
                color = COLOR_REGION
            painter.fillPath(path, QtGui.QBrush(color))

    CENTER_MARK = 4.0

    def _draw_marks(self, painter) -> None:
        """Центры кривых и середина объекта под курсором.

        Центр окружности нужен постоянно: к нему привязывают, от него
        меряют. Середину показываем только у объекта под указателем — иначе
        на контуре из двадцати отрезков рябит от точек, и ни одну не
        выцелить.
        """
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.setPen(QtGui.QPen(COLOR_CONSTRUCTION, 1.2))
        for segment in self.sketch.segments:
            if segment.kind not in ("circle", "arc"):
                continue
            center = self._to_screen(self.sketch.circle_geometry(segment)[0])
            mark = self.CENTER_MARK
            painter.drawLine(QtCore.QPointF(center.x() - mark, center.y()),
                             QtCore.QPointF(center.x() + mark, center.y()))
            painter.drawLine(QtCore.QPointF(center.x(), center.y() - mark),
                             QtCore.QPointF(center.x(), center.y() + mark))

        hovered = self._hover
        if hovered is None and self._snap is not None:
            hovered = self._snap.segment
        if not isinstance(hovered, Segment):
            return
        middle = self._middle_of(T.describe(self.sketch, hovered))
        if middle is None:
            return
        position = self._to_screen(middle)
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QBrush(COLOR_HOVER))
        painter.drawEllipse(position, 3.2, 3.2)

    def _draw_readout(self, painter) -> None:
        text = self.cursor_readout()
        if not text:
            return
        position = self._to_screen(self._snap.position if self._snap else self._cursor)
        metrics = painter.fontMetrics()
        rect = metrics.boundingRect(text).adjusted(-5, -3, 5, 3)
        rect.moveTopLeft(QtCore.QPoint(int(position.x()) + 16, int(position.y()) + 12))
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255, 235)))
        painter.drawRoundedRect(rect, 3, 3)
        painter.setPen(QtGui.QPen(COLOR_FIXED, 1.0))
        painter.drawText(rect, QtCore.Qt.AlignCenter, text)

    def _draw_snap(self, painter) -> None:
        if self._snap is None or self._snap.kind == "free":
            return
        position = self._to_screen(self._snap.position)
        painter.setPen(QtGui.QPen(COLOR_SNAP, 1.4))
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawRect(QtCore.QRectF(position.x() - 5, position.y() - 5, 10, 10))
        if self._snap.title:
            painter.drawText(QtCore.QPointF(position.x() + 9, position.y() - 9),
                             self._snap.title)

    def _draw_band(self, painter) -> None:
        if self._band is None:
            return
        pen = QtGui.QPen(COLOR_SELECTED, 1.0)
        pen.setDashPattern([4.0, 3.0])
        painter.setPen(pen)
        painter.setBrush(QtGui.QBrush(QtGui.QColor(224, 123, 0, 26)))
        painter.drawRect(
            QtCore.QRectF(self._to_screen(self._band[0]),
                          self._to_screen(self._band[1])).normalized()
        )


def _far_end(corner, shape: T.Shape2D):
    """Дальний от угла конец отрезка — им задаётся направление стороны угла."""
    if geom2d.distance(corner, shape.start) > geom2d.distance(corner, shape.end):
        return shape.start
    return shape.end


def _is_point(entity) -> bool:
    return hasattr(entity, "handle") and not hasattr(entity, "kind")


def _is_segment(entity) -> bool:
    return hasattr(entity, "kind") and hasattr(entity, "points")


def _relation_requirement(kind: str) -> str:
    return {
        "coincident": "нужно выбрать две точки",
        "horizontal": "нужно выбрать отрезок",
        "vertical": "нужно выбрать отрезок",
        "parallel": "нужно выбрать два отрезка",
        "perpendicular": "нужно выбрать два отрезка",
        "equal": "нужно выбрать два отрезка",
        "tangent": "нужно выбрать отрезок и дугу",
        "concentric": "нужно выбрать две окружности или дуги",
        "equal_radius": "нужно выбрать две окружности или дуги",
        "midpoint": "нужно выбрать точку и отрезок",
        "symmetric": "нужно выбрать две точки и осевую линию",
        "point_on": "нужно выбрать точку и объект",
        "anchor": "нужно выбрать точку",
    }.get(kind, "выбрано не то, что нужно для этой связи")
