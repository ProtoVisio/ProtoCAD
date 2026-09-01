"""Редактор эскиза: рисование, ограничения, перетаскивание с пересчётом.

Эскиз задаётся СВЯЗЯМИ, а не координатами: инженер рисует приблизительно,
накладывает ограничения и размеры, а решатель приводит геометрию к ним. Из
замеров Spike 4 известно, что шаг перетаскивания укладывается в 0,1 мс при
кадре 16 мс, поэтому пересчёт идёт на каждое движение мыши.

Рисование через ``QPainter``: для плоского эскиза это точнее и проще, чем GL,
а нагрузка ничтожна на фоне 3D-вида.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6 import QtCore, QtGui, QtWidgets

from protocad.sketch import Point, Segment, Sketch

GRID_STEP_MM = 10.0
POINT_RADIUS_PX = 4
PICK_TOLERANCE_PX = 8


@dataclass
class Selection:
    """Что выбрано сейчас. Ограничение накладывается на выбранное."""

    points: list = None
    segments: list = None

    def __post_init__(self):
        self.points = self.points or []
        self.segments = self.segments or []

    def clear(self) -> None:
        self.points.clear()
        self.segments.clear()

    @property
    def empty(self) -> bool:
        return not self.points and not self.segments


class SketchCanvas(QtWidgets.QWidget):
    """Полотно эскиза."""

    changed = QtCore.Signal()
    status = QtCore.Signal(str)

    def __init__(self, sketch: Sketch, parent=None):
        super().__init__(parent)
        self.sketch = sketch
        self.scale = 4.0  # пикселей на миллиметр
        self.origin = QtCore.QPointF(0.0, 0.0)  # мм, центр вида
        self.tool = "select"
        self.selection = Selection()
        self._pending: list[Point] = []
        self._dragging: Point | None = None
        self._last_mouse = None
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMinimumSize(560, 420)
        self.sketch.solve()

    # --- преобразования ---

    def to_screen(self, x: float, y: float) -> QtCore.QPointF:
        return QtCore.QPointF(
            self.width() / 2 + (x - self.origin.x()) * self.scale,
            self.height() / 2 - (y - self.origin.y()) * self.scale,
        )

    def to_world(self, position) -> tuple[float, float]:
        return (
            (position.x() - self.width() / 2) / self.scale + self.origin.x(),
            (self.height() / 2 - position.y()) / self.scale + self.origin.y(),
        )

    def fit(self) -> None:
        if not self.sketch.points:
            return
        coordinates = [self.sketch.coordinates(p) for p in self.sketch.points]
        xs = [c[0] for c in coordinates]
        ys = [c[1] for c in coordinates]
        width = max(max(xs) - min(xs), 1.0)
        height = max(max(ys) - min(ys), 1.0)
        self.origin = QtCore.QPointF((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)
        self.scale = min(self.width() / (width * 1.4), self.height() / (height * 1.4))
        self.update()

    # --- отрисовка ---

    def paintEvent(self, _event) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing)
        painter.fillRect(self.rect(), QtGui.QColor("#1f2124"))
        self._draw_grid(painter)
        self._draw_segments(painter)
        self._draw_points(painter)
        self._draw_dimensions(painter)
        painter.end()

    def _draw_grid(self, painter: QtGui.QPainter) -> None:
        step = GRID_STEP_MM
        while step * self.scale < 12:
            step *= 5
        left, bottom = self.to_world(QtCore.QPointF(0, self.height()))
        right, top = self.to_world(QtCore.QPointF(self.width(), 0))

        painter.setPen(QtGui.QPen(QtGui.QColor("#2b2e33"), 1))
        x = step * int(left / step)
        while x < right:
            screen = self.to_screen(x, 0)
            painter.drawLine(QtCore.QPointF(screen.x(), 0),
                             QtCore.QPointF(screen.x(), self.height()))
            x += step
        y = step * int(bottom / step)
        while y < top:
            screen = self.to_screen(0, y)
            painter.drawLine(QtCore.QPointF(0, screen.y()),
                             QtCore.QPointF(self.width(), screen.y()))
            y += step

        # Оси эскиза заметнее сетки: без них теряется начало координат.
        painter.setPen(QtGui.QPen(QtGui.QColor("#4a5058"), 1.5))
        zero = self.to_screen(0, 0)
        painter.drawLine(QtCore.QPointF(0, zero.y()), QtCore.QPointF(self.width(), zero.y()))
        painter.drawLine(QtCore.QPointF(zero.x(), 0), QtCore.QPointF(zero.x(), self.height()))

    def _draw_segments(self, painter: QtGui.QPainter) -> None:
        for segment in self.sketch.segments:
            if segment.kind != "line":
                continue
            start, end = segment.points
            x1, y1 = self.sketch.coordinates(start)
            x2, y2 = self.sketch.coordinates(end)
            selected = segment in self.selection.segments
            painter.setPen(
                QtGui.QPen(
                    QtGui.QColor("#f0a020" if selected else "#d8dde3"),
                    2.5 if selected else 1.8,
                )
            )
            painter.drawLine(self.to_screen(x1, y1), self.to_screen(x2, y2))

    def _draw_points(self, painter: QtGui.QPainter) -> None:
        for point in self.sketch.points:
            x, y = self.sketch.coordinates(point)
            screen = self.to_screen(x, y)
            selected = point in self.selection.points
            painter.setBrush(QtGui.QBrush(QtGui.QColor("#f0a020" if selected else "#7fb3ff")))
            painter.setPen(QtGui.QPen(QtGui.QColor("#12141a"), 1))
            radius = POINT_RADIUS_PX + (2 if selected else 0)
            painter.drawRect(
                QtCore.QRectF(screen.x() - radius, screen.y() - radius,
                              radius * 2, radius * 2)
            )

    def _draw_dimensions(self, painter: QtGui.QPainter) -> None:
        """Управляющие размеры подписями у своих отрезков."""
        painter.setPen(QtGui.QPen(QtGui.QColor("#8fd18f"), 1))
        font = painter.font()
        font.setPointSize(9)
        painter.setFont(font)
        for operation, arguments in self.sketch._log:
            if operation != "dimension":
                continue
            a = self.sketch.points[arguments["a"]]
            b = self.sketch.points[arguments["b"]]
            x1, y1 = self.sketch.coordinates(a)
            x2, y2 = self.sketch.coordinates(b)
            middle = self.to_screen((x1 + x2) / 2, (y1 + y2) / 2)
            painter.drawText(
                QtCore.QPointF(middle.x() + 8, middle.y() - 8),
                f"{arguments['name']} = {arguments['value']:g}",
            )

    # --- выбор и инструменты ---

    def _point_at(self, position) -> Point | None:
        for point in self.sketch.points:
            x, y = self.sketch.coordinates(point)
            screen = self.to_screen(x, y)
            if (screen - position).manhattanLength() <= PICK_TOLERANCE_PX * 2:
                return point
        return None

    def _segment_at(self, position) -> Segment | None:
        for segment in self.sketch.segments:
            if segment.kind != "line":
                continue
            start, end = segment.points
            a = self.to_screen(*self.sketch.coordinates(start))
            b = self.to_screen(*self.sketch.coordinates(end))
            if _distance_to_segment(position, a, b) <= PICK_TOLERANCE_PX:
                return segment
        return None

    def mousePressEvent(self, event) -> None:
        self._last_mouse = event.position()
        if event.button() == QtCore.Qt.MiddleButton:
            return
        if self.tool == "line":
            self._place_line_point(event.position())
            return

        point = self._point_at(event.position())
        segment = None if point else self._segment_at(event.position())
        additive = bool(event.modifiers() & QtCore.Qt.ControlModifier)
        if not additive:
            self.selection.clear()
        if point is not None:
            self.selection.points.append(point)
            self._dragging = point
        elif segment is not None:
            self.selection.segments.append(segment)
        self._report()
        self.update()

    def _place_line_point(self, position) -> None:
        """Ломаная строится по щелчкам; повтор по первой точке замыкает контур."""
        x, y = self.to_world(position)
        existing = self._point_at(position)
        point = existing if existing is not None else self.sketch.point(x, y)
        if self._pending:
            previous = self._pending[-1]
            if previous is not point:
                self.sketch.line(previous, point)
        self._pending.append(point)
        if len(self._pending) > 2 and point is self._pending[0]:
            self.finish_line()
        self.sketch.solve()
        self.changed.emit()
        self._report()
        self.update()

    def finish_line(self) -> None:
        self._pending.clear()
        self.tool = "select"
        self._report()

    def mouseMoveEvent(self, event) -> None:
        if self._last_mouse is None:
            self.update()
            return
        delta = event.position() - self._last_mouse
        if event.buttons() & QtCore.Qt.MiddleButton:
            self.origin -= QtCore.QPointF(
                delta.x() / self.scale, -delta.y() / self.scale
            )
            self._last_mouse = event.position()
            self.update()
            return
        if self._dragging is not None and (event.buttons() & QtCore.Qt.LeftButton):
            x, y = self.to_world(event.position())
            # Пересчёт на каждое движение: связи должны держать вживую,
            # иначе перетаскивание не даёт понимания, что чем управляет.
            self.sketch.move(self._dragging, x, y)
            self.changed.emit()
            self._report()
            self.update()
        self._last_mouse = event.position()

    def mouseReleaseEvent(self, _event) -> None:
        self._dragging = None
        self._last_mouse = None

    def wheelEvent(self, event) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
        self.scale = max(0.2, min(200.0, self.scale * factor))
        self.update()

    def keyPressEvent(self, event) -> None:
        if event.key() == QtCore.Qt.Key_Escape:
            self.finish_line()
            self.selection.clear()
            self.update()
        elif event.key() == QtCore.Qt.Key_F:
            self.fit()

    # --- ограничения ---

    def apply_constraint(self, kind: str) -> None:
        selection = self.selection
        try:
            if kind == "horizontal" and len(selection.segments) == 1:
                self.sketch.horizontal(selection.segments[0])
            elif kind == "vertical" and len(selection.segments) == 1:
                self.sketch.vertical(selection.segments[0])
            elif kind == "parallel" and len(selection.segments) == 2:
                self.sketch.parallel(*selection.segments)
            elif kind == "perpendicular" and len(selection.segments) == 2:
                self.sketch.perpendicular(*selection.segments)
            elif kind == "equal" and len(selection.segments) == 2:
                self.sketch.equal(*selection.segments)
            elif kind == "coincident" and len(selection.points) == 2:
                self.sketch.coincident(*selection.points)
            elif kind == "anchor" and len(selection.points) == 1:
                self.sketch.anchor(selection.points[0])
            else:
                self.status.emit(
                    f"Для «{kind}» выбрано не то: нужны другие элементы"
                )
                return
        except Exception as error:  # noqa: BLE001 — отказ показываем, а не глотаем
            self.status.emit(f"{type(error).__name__}: {error}")
            return
        self.sketch.solve()
        self.selection.clear()
        self.changed.emit()
        self._report()
        self.update()

    def add_dimension(self) -> None:
        if len(self.selection.points) != 2:
            self.status.emit("Размер: выберите две точки")
            return
        a, b = self.selection.points
        x1, y1 = self.sketch.coordinates(a)
        x2, y2 = self.sketch.coordinates(b)
        current = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        value, ok = QtWidgets.QInputDialog.getDouble(
            self, "Размер", "Значение, мм:", current, 0.001, 100000.0, 3
        )
        if not ok:
            return
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Размер", "Имя размера:", text=f"размер{len(self.sketch.dimensions) + 1}"
        )
        if not ok or not name:
            return
        self.sketch.dimension(a, b, value, name)
        self.sketch.solve()
        self.selection.clear()
        self.changed.emit()
        self._report()
        self.update()

    def _report(self) -> None:
        solution = self.sketch.solve()
        state = (
            "определён полностью"
            if solution.fully_constrained
            else f"степеней свободы: {solution.dof}"
        )
        if not solution.ok:
            state = f"ОТКАЗ: {solution.message}"
        chosen = []
        if self.selection.points:
            chosen.append(f"точек: {len(self.selection.points)}")
        if self.selection.segments:
            chosen.append(f"отрезков: {len(self.selection.segments)}")
        self.status.emit(
            f"{self.sketch.name}  |  {state}"
            + (f"  |  выбрано {', '.join(chosen)}" if chosen else "")
        )


def _distance_to_segment(point, a, b) -> float:
    ax, ay, bx, by = a.x(), a.y(), b.x(), b.y()
    px, py = point.x(), point.y()
    dx, dy = bx - ax, by - ay
    length = dx * dx + dy * dy
    if length < 1e-9:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length))
    nearest_x, nearest_y = ax + t * dx, ay + t * dy
    return ((px - nearest_x) ** 2 + (py - nearest_y) ** 2) ** 0.5


class SketchEditor(QtWidgets.QDialog):
    """Окно редактора: полотно, инструменты, ограничения."""

    def __init__(self, sketch: Sketch, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Эскиз — {sketch.name}")
        self.resize(1000, 720)
        self.canvas = SketchCanvas(sketch, self)

        toolbar = QtWidgets.QToolBar()
        toolbar.addAction("Отрезки", lambda: self._set_tool("line"))
        toolbar.addAction("Выбор", lambda: self._set_tool("select"))
        toolbar.addSeparator()
        for label, kind in (
            ("Гориз.", "horizontal"),
            ("Верт.", "vertical"),
            ("Паралл.", "parallel"),
            ("Перпенд.", "perpendicular"),
            ("Равные", "equal"),
            ("Совпад.", "coincident"),
            ("Закрепить", "anchor"),
        ):
            toolbar.addAction(label, lambda k=kind: self.canvas.apply_constraint(k))
        toolbar.addSeparator()
        toolbar.addAction("Размер…", self.canvas.add_dimension)
        toolbar.addSeparator()
        toolbar.addAction("Вписать", self.canvas.fit)

        self.status = QtWidgets.QLabel()
        self.status.setContentsMargins(8, 4, 8, 4)
        self.canvas.status.connect(self.status.setText)

        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        buttons.rejected.connect(self.accept)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(toolbar)
        layout.addWidget(self.canvas, 1)
        layout.addWidget(self.status)
        layout.addWidget(buttons)
        QtCore.QTimer.singleShot(0, self.canvas.fit)
        self.canvas._report()

    def _set_tool(self, tool: str) -> None:
        self.canvas.finish_line()
        self.canvas.tool = tool
        self.canvas.setCursor(
            QtCore.Qt.CrossCursor if tool == "line" else QtCore.Qt.ArrowCursor
        )
