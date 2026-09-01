"""Опорная геометрия поверх вида: плоскости, начало координат, оси.

Всё это не деталь, а разметка: она не участвует в выборе, не отбрасывает
тени и не должна попадать в снимок как часть тела. Поэтому рисуется не в
GL, а прозрачным виджетом поверх — тем же приёмом, что и поле эскиза.

Отдельный слой нужен ещё и потому, что «условия видимости» переключают
именно эти вещи по одной. Будь они частью сцены, каждое переключение
означало бы пересборку буферов детали.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

COLOR_PLANE = QtGui.QColor("#7a8ba8")
COLOR_PLANE_FILL = QtGui.QColor(122, 139, 168, 26)
COLOR_ORIGIN = QtGui.QColor("#c0392b")
# Эскиз, уже израсходованный операцией. Бледнее правящегося: он показан
# для справки, трогать его здесь нельзя.
COLOR_SKETCH = QtGui.QColor("#5b8db8")
AXIS_COLORS = (QtGui.QColor("#c0392b"), QtGui.QColor("#2f7d32"),
               QtGui.QColor("#1f5fa8"))
AXIS_TITLES = ("X", "Y", "Z")


class SceneDecor(QtWidgets.QWidget):
    """Разметка вида. Мышь сквозь неё проходит к детали.

    Размер плоскостей берётся от габарита детали, а не задан в
    миллиметрах: плоскость размером с деталь читается одинаково и у
    шайбы, и у корпуса, а фиксированный квадрат в одном из этих случаев
    выглядит точкой, в другом — застилает всё.
    """

    def __init__(self, viewport, parent=None):
        super().__init__(parent or viewport)
        self.viewport = viewport
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.show_planes = False
        self.show_origin = True
        self.show_axes = False
        self.show_sketches = False
        self.planes: list = []
        #: Справочные плоскости детали. Показываются ВСЕГДА, а не по
        #: флажку «плоскости»: тот управляет тремя стандартными, а эти
        #: человек завёл сам — прятать их значило бы потерять работу.
        self.reference_planes: list = []
        #: Плоскость незавершённой команды: показывается, пока её задают.
        self.pending_plane = None
        #: Экземпляры массива: [(номер, точка, пропущен ли)]. Нужны, чтобы
        #: по ним можно было ЩЁЛКНУТЬ: пропуск экземпляра указывают мышью,
        #: а не номером, и указывать надо во что-то видимое.
        self.instances: list = []
        self.sketches: list = []
        #: Стрелка направления незавершённой операции:
        #: (начало, направление, длина, подпись) в координатах детали.
        #: Пусто — операции нет.
        self.arrow = None

    def visible_anything(self) -> bool:
        return bool(
            (self.show_planes and self.planes) or self.show_origin
            or self.show_axes or (self.show_sketches and self.sketches)
            or self.arrow or self.reference_planes or self.pending_plane
            or self.instances
        )

    def _reach(self) -> float:
        radius = float(getattr(self.viewport, "radius", 0.0) or 0.0)
        return max(radius, 1.0) * 0.75

    def _to_screen(self, point) -> QtCore.QPointF | None:
        screen = self.viewport.project([point])[0]
        if screen[2] <= 0.0:
            return None
        return QtCore.QPointF(float(screen[0]), float(screen[1]))

    def paintEvent(self, _event) -> None:
        if not self.visible_anything():
            return
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        if self.show_planes:
            for plane in self.planes:
                self._draw_plane(painter, plane)
        for plane in self.reference_planes:
            self._draw_plane(painter, plane)
        if self.instances:
            self._draw_instances(painter)
        if self.pending_plane is not None:
            # Задаваемая плоскость рисуется ярче построенных: пока команда
            # открыта, смотрят именно на неё.
            self._draw_plane(painter, self.pending_plane, pending=True)
        if self.show_sketches:
            for sketch in self.sketches:
                self._draw_sketch(painter, sketch)
        if self.show_axes:
            self._draw_axes(painter)
        if self.arrow:
            self._draw_arrow(painter)
        if self.show_origin:
            self._draw_origin(painter)

    #: Насколько близко к метке надо попасть, чтобы её задеть.
    INSTANCE_PIXELS = 16.0

    def _draw_instances(self, painter) -> None:
        """Метки экземпляров массива: кружок на каждом.

        Пропущенный перечёркнут, а не спрятан: спрятанный экземпляр нельзя
        вернуть щелчком, и человек остаётся без обратного хода.
        """
        for number, point, skipped in self.instances:
            place = self._to_screen(tuple(point))
            if place is None:
                continue
            colour = QtGui.QColor("#c0392b") if skipped else QtGui.QColor("#1f6fb2")
            painter.setPen(QtGui.QPen(colour, 1.6))
            painter.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255, 200)))
            painter.drawEllipse(place, 7.0, 7.0)
            if skipped:
                painter.setPen(QtGui.QPen(colour, 2.0))
                painter.drawLine(place + QtCore.QPointF(-4.5, -4.5),
                                 place + QtCore.QPointF(4.5, 4.5))
                painter.drawLine(place + QtCore.QPointF(4.5, -4.5),
                                 place + QtCore.QPointF(-4.5, 4.5))
            else:
                painter.setPen(QtGui.QPen(colour, 1.4))
                painter.drawText(place + QtCore.QPointF(-3.0, 4.0),
                                 str(number + 1))

    def instance_at(self, position):
        """Номер экземпляра под точкой экрана. ``None`` — мимо."""
        best, distance = None, self.INSTANCE_PIXELS ** 2
        for number, point, _ in self.instances:
            place = self._to_screen(tuple(point))
            if place is None:
                continue
            away = ((position.x() - place.x()) ** 2
                    + (position.y() - place.y()) ** 2)
            if away <= distance:
                best, distance = number, away
        return best

    def _draw_plane(self, painter, plane, pending: bool = False) -> None:
        reach = self._reach()
        corners = []
        for u, v in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
            corners.append(self._to_screen(plane.point_at(u * reach, v * reach)))
        if any(corner is None for corner in corners):
            return
        polygon = QtGui.QPolygonF(corners)
        painter.setBrush(QtGui.QBrush(COLOR_PLANE_FILL))
        pen = QtGui.QPen(COLOR_PLANE.darker(150) if pending else COLOR_PLANE,
                         2.0 if pending else 1.0)
        pen.setDashPattern([5.0, 4.0])
        painter.setPen(pen)
        painter.drawPolygon(polygon)
        label = self._to_screen(plane.point_at(-reach, reach))
        if label is not None and plane.name:
            painter.setPen(QtGui.QPen(COLOR_PLANE.darker(130)))
            painter.drawText(label + QtCore.QPointF(4.0, -4.0), plane.name)

    ARC_STEPS = 48

    def _draw_sketch(self, painter, sketch) -> None:
        """Контур эскиза на своей плоскости, без размеров и связей.

        Показывается только основная геометрия: вспомогательные линии и
        размеры принадлежат правке эскиза, а здесь эскиз лишь напоминает,
        откуда взялась операция.
        """
        pen = QtGui.QPen(COLOR_SKETCH, 1.4)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        for segment in sketch.segments:
            if segment.construction:
                continue
            flat = self._flatten(sketch, segment)
            if len(flat) < 2:
                continue
            points = [self._to_screen(sketch.plane.point_at(u, v)) for u, v in flat]
            if any(point is None for point in points):
                continue
            painter.drawPolyline(QtGui.QPolygonF(points))

    def _flatten(self, sketch, segment) -> list[tuple[float, float]]:
        """Объект эскиза ломаной в координатах его плоскости."""
        import math

        if segment.kind == "line":
            return [sketch.coordinates(point) for point in segment.ends]
        if segment.kind == "circle":
            (cx, cy), radius = sketch.circle_geometry(segment)
            return [
                (cx + radius * math.cos(2.0 * math.pi * i / self.ARC_STEPS),
                 cy + radius * math.sin(2.0 * math.pi * i / self.ARC_STEPS))
                for i in range(self.ARC_STEPS + 1)
            ]
        if segment.kind in ("ellipse", "spline", "ellipse_arc"):
            from protocad.sketch import tools as tools_module

            shape = tools_module.describe(sketch, segment)
            return tools_module.curve_outline_of(shape, self.ARC_STEPS)
        if segment.kind == "arc":
            arc = sketch.arc_geometry(segment)
            sweep = arc.end_angle - arc.start_angle
            while sweep <= 0.0:
                sweep += 2.0 * math.pi
            steps = max(4, int(self.ARC_STEPS * sweep / (2.0 * math.pi)))
            return [
                (arc.center[0] + arc.radius * math.cos(arc.start_angle + sweep * i / steps),
                 arc.center[1] + arc.radius * math.sin(arc.start_angle + sweep * i / steps))
                for i in range(steps + 1)
            ]
        return []

    def _draw_axes(self, painter) -> None:
        reach = self._reach() * 1.2
        zero = self._to_screen((0.0, 0.0, 0.0))
        if zero is None:
            return
        for index in range(3):
            far = [0.0, 0.0, 0.0]
            far[index] = reach
            end = self._to_screen(tuple(far))
            if end is None:
                continue
            pen = QtGui.QPen(AXIS_COLORS[index], 1.0)
            pen.setDashPattern([9.0, 5.0])
            painter.setPen(pen)
            painter.drawLine(zero, end)

    #: Цвет стрелки — тот же, что у предпросмотра: она о нём и говорит.
    ARROW_COLOR = QtGui.QColor(196, 150, 30)
    ARROW_HEAD = 13.0

    def arrow_line(self):
        """Стрелка в ЭКРАННЫХ точках: ``(начало, конец)`` или ``None``.

        Нужна не только рисованию: за стрелку тянут мышью, и решать, попал
        ли щелчок в её наконечник, надо там же, где она нарисована. Считать
        это второй раз в окне значило бы завести второе место, где
        известно, где она.
        """
        if not self.arrow:
            return None
        origin, direction, length, _ = self.arrow
        finish = tuple(origin[i] + direction[i] * length for i in range(3))
        start = self._to_screen(tuple(origin))
        end = self._to_screen(finish)
        if start is None or end is None:
            return None
        return start, end

    #: Насколько близко к наконечнику надо попасть, чтобы схватить его.
    GRIP_PIXELS = 14.0

    def near_arrow_head(self, point) -> bool:
        """Попал ли щелчок в наконечник стрелки."""
        line = self.arrow_line()
        if line is None:
            return False
        end = line[1]
        return ((point.x() - end.x()) ** 2
                + (point.y() - end.y()) ** 2) <= self.GRIP_PIXELS ** 2

    def _draw_arrow(self, painter) -> None:
        """Стрелка направления операции: откуда, куда и на сколько.

        Рисуется поверх вида в экранных координатах: длина наконечника в
        пикселях, а не в миллиметрах, иначе на мелкой детали он занимает
        её целиком, а на крупной исчезает.
        """
        origin, direction, length, label = self.arrow
        finish = tuple(origin[i] + direction[i] * length for i in range(3))
        start = self._to_screen(tuple(origin))
        end = self._to_screen(finish)
        if start is None or end is None:
            return
        line = QtCore.QLineF(start, end)
        if line.length() < 1.0:
            return
        pen = QtGui.QPen(self.ARROW_COLOR, 2.2)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        painter.setPen(pen)
        painter.setBrush(QtCore.Qt.NoBrush)
        painter.drawLine(line)

        # Наконечник — треугольник у конца, развёрнутый вдоль линии.
        import math

        angle = math.atan2(end.y() - start.y(), end.x() - start.x())
        spread = math.radians(22.0)
        wing = [
            QtCore.QPointF(
                end.x() - self.ARROW_HEAD * math.cos(angle + side),
                end.y() - self.ARROW_HEAD * math.sin(angle + side))
            for side in (spread, -spread)
        ]
        painter.setBrush(QtGui.QBrush(self.ARROW_COLOR))
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawPolygon(QtGui.QPolygonF([end] + wing))

        # Засечка в начале: видно, откуда операция считает свою длину.
        painter.setPen(QtGui.QPen(self.ARROW_COLOR, 2.0))
        across = QtCore.QLineF(start, end).normalVector()
        across.setLength(7.0)
        painter.drawLine(
            QtCore.QPointF(start.x() + (across.dx()), start.y() + (across.dy())),
            QtCore.QPointF(start.x() - (across.dx()), start.y() - (across.dy())))

        if not label:
            return
        painter.setPen(QtGui.QPen(self.ARROW_COLOR.darker(130), 1.0))
        font = painter.font()
        font.setPointSizeF(max(8.0, font.pointSizeF()))
        painter.setFont(font)
        middle = QtCore.QPointF((start.x() + end.x()) / 2.0 + 10.0,
                                (start.y() + end.y()) / 2.0 - 6.0)
        painter.drawText(middle, label)

    ORIGIN_PIXELS = 42.0

    def _draw_origin(self, painter) -> None:
        """Триэдр начала координат — постоянного размера на экране.

        Привязать его к габариту нельзя: на приближенной детали он
        разрастётся во весь кадр, на отдалённой исчезнет, а нужен он
        одинаково в обоих случаях.
        """
        zero = self._to_screen((0.0, 0.0, 0.0))
        if zero is None:
            return
        reach = self._reach() * 0.2
        for index in range(3):
            far = [0.0, 0.0, 0.0]
            far[index] = reach
            end = self._to_screen(tuple(far))
            if end is None:
                continue
            direction = end - zero
            length = (direction.x() ** 2 + direction.y() ** 2) ** 0.5
            if length < 1e-6:
                continue
            tip = zero + direction * (self.ORIGIN_PIXELS / length)
            painter.setPen(QtGui.QPen(AXIS_COLORS[index], 1.6))
            painter.drawLine(zero, tip)
            painter.drawText(tip + QtCore.QPointF(3.0, -3.0), AXIS_TITLES[index])
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QBrush(COLOR_ORIGIN))
        painter.drawEllipse(zero, 3.0, 3.0)
