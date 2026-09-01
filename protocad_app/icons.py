"""Значки ProtoCAD — рисуются кодом, а не берутся готовыми.

Причина не в экономии файлов. Значки, подписи команд и оформление
конкретного продукта — предмет авторского права, и копировать их нельзя,
даже если порядок операций совпадает с отраслевой практикой (это условие
записано в ``docs/00_CONTEXT.md``). Нарисованный по месту глиф решает и
вторую задачу: он строится из той же геометрии, которую обозначает, и
поэтому одинаково читается в любом размере и на любом фоне.

Все глифы вписаны в квадрат 0…1 и масштабируются под запрошенный размер,
поэтому набор одинаково пригоден и для ленты, и для меню, и для дерева.
"""

from __future__ import annotations

import math

from PySide6 import QtCore, QtGui

# Цвет обводки берётся у темы: значок обязан читаться и на светлом фоне, и
# на тёмном. Второй цвет — для вспомогательных линий внутри глифа.
STROKE = QtGui.QColor("#1f2933")
ACCENT = QtGui.QColor("#c0392b")
# Зелёный и красный только там, где значок обозначает согласие или отказ:
# в остальных цвет не несёт смысла и мешает читать форму.
GREEN = QtGui.QColor("#2f7d32")
RED = QtGui.QColor("#c0392b")
GHOST = QtGui.QColor("#7b8794")

_CACHE: dict[tuple, QtGui.QIcon] = {}


def _pen(color: QtGui.QColor, width: float, dashed: bool = False) -> QtGui.QPen:
    pen = QtGui.QPen(color, width)
    pen.setCapStyle(QtCore.Qt.RoundCap)
    pen.setJoinStyle(QtCore.Qt.RoundJoin)
    if dashed:
        # Штрих задаётся в толщинах пера, поэтому не рассыпается при
        # изменении размера значка.
        pen.setDashPattern([3.0, 2.5])
    return pen


class _Canvas:
    """Тонкая обёртка над QPainter в координатах 0…1."""

    def __init__(self, painter: QtGui.QPainter, size: int):
        self.painter = painter
        self.size = size
        self.unit = size

    def _p(self, x: float, y: float) -> QtCore.QPointF:
        # Ось Y вниз, как на экране; глифы описываются в тех же координатах,
        # что и всё остальное в отрисовке.
        return QtCore.QPointF(x * self.unit, y * self.unit)

    def stroke(self, color=STROKE, width: float = 1.6, dashed: bool = False):
        self.painter.setPen(_pen(color, width * self.unit / 24.0, dashed))
        self.painter.setBrush(QtCore.Qt.NoBrush)

    def fill(self, color=STROKE):
        self.painter.setPen(QtCore.Qt.NoPen)
        self.painter.setBrush(QtGui.QBrush(color))

    def line(self, x1, y1, x2, y2):
        self.painter.drawLine(self._p(x1, y1), self._p(x2, y2))

    def polyline(self, points, closed: bool = False):
        path = QtGui.QPainterPath(self._p(*points[0]))
        for x, y in points[1:]:
            path.lineTo(self._p(x, y))
        if closed:
            path.closeSubpath()
        self.painter.drawPath(path)

    def circle(self, cx, cy, r):
        self.painter.drawEllipse(self._p(cx, cy), r * self.unit, r * self.unit)

    def arc(self, cx, cy, r, start_deg, sweep_deg):
        rect = QtCore.QRectF(
            self._p(cx - r, cy - r), QtCore.QSizeF(2 * r * self.unit, 2 * r * self.unit)
        )
        self.painter.drawArc(rect, int(start_deg * 16), int(sweep_deg * 16))

    def dot(self, cx, cy, r=0.055, color=STROKE):
        self.fill(color)
        self.painter.drawEllipse(self._p(cx, cy), r * self.unit, r * self.unit)

    def arrow(self, x1, y1, x2, y2, head: float = 0.09):
        """Стрелка — общий элемент значков размеров и смещений."""
        self.line(x1, y1, x2, y2)
        angle = math.atan2(y2 - y1, x2 - x1)
        for turn in (2.6, -2.6):
            self.line(
                x2, y2,
                x2 + head * math.cos(angle + turn),
                y2 + head * math.sin(angle + turn),
            )


# --- сами глифы -------------------------------------------------------------
#
# Каждый рисует то, что обозначает: значок отрезка — отрезок с концевыми
# точками, значок отсечения — то же с отрезанным куском. Читаемость важнее
# изобретательности, поэтому все построены из одних и тех же примитивов.


def _select(c: _Canvas):
    c.stroke()
    c.polyline([(0.3, 0.14), (0.3, 0.78), (0.45, 0.63), (0.56, 0.86),
                (0.67, 0.81), (0.56, 0.58), (0.74, 0.55)], closed=True)


def _line(c: _Canvas):
    c.stroke()
    c.line(0.18, 0.78, 0.82, 0.24)
    c.dot(0.18, 0.78)
    c.dot(0.82, 0.24)


def _polyline(c: _Canvas):
    c.stroke()
    c.polyline([(0.14, 0.76), (0.38, 0.32), (0.62, 0.62), (0.86, 0.22)])
    for x, y in ((0.14, 0.76), (0.38, 0.32), (0.62, 0.62), (0.86, 0.22)):
        c.dot(x, y, 0.045)


def _rectangle(c: _Canvas):
    c.stroke()
    c.polyline([(0.16, 0.24), (0.84, 0.24), (0.84, 0.76), (0.16, 0.76)], closed=True)
    c.dot(0.16, 0.24, 0.045)
    c.dot(0.84, 0.76, 0.045)


def _center_rectangle(c: _Canvas):
    c.stroke()
    c.polyline([(0.16, 0.26), (0.84, 0.26), (0.84, 0.74), (0.16, 0.74)], closed=True)
    c.stroke(GHOST, 1.1, dashed=True)
    c.line(0.5, 0.16, 0.5, 0.84)
    c.line(0.1, 0.5, 0.9, 0.5)
    c.dot(0.5, 0.5, 0.05, ACCENT)


def _circle(c: _Canvas):
    c.stroke()
    c.circle(0.5, 0.5, 0.33)
    c.dot(0.5, 0.5, 0.05)


def _spline(c: _Canvas):
    """Сплайн: волна через отмеченные точки."""
    c.stroke()
    path = QtGui.QPainterPath(c._p(0.12, 0.7))
    path.cubicTo(c._p(0.3, 0.1), c._p(0.55, 0.95), c._p(0.88, 0.32))
    c.painter.drawPath(path)
    c.dot(0.12, 0.7, 0.05)
    c.dot(0.88, 0.32, 0.05)


def _ellipse(c: _Canvas):
    c.stroke()
    c.painter.save()
    c.painter.translate(c._p(0.5, 0.5))
    c.painter.rotate(-20.0)
    c.painter.drawEllipse(QtCore.QPointF(0.0, 0.0),
                          0.38 * c.unit, 0.22 * c.unit)
    c.painter.restore()
    c.dot(0.5, 0.5, 0.05)


def _hyperbola(c: _Canvas):
    """Две ветви с асимптотами."""
    c.stroke(GHOST, 1.0, dashed=True)
    c.line(0.10, 0.10, 0.90, 0.90)
    c.line(0.10, 0.90, 0.90, 0.10)
    c.stroke()
    left = QtGui.QPainterPath(c._p(0.12, 0.10))
    left.quadTo(c._p(0.46, 0.50), c._p(0.12, 0.90))
    c.painter.drawPath(left)
    right = QtGui.QPainterPath(c._p(0.88, 0.10))
    right.quadTo(c._p(0.54, 0.50), c._p(0.88, 0.90))
    c.painter.drawPath(right)
    c.dot(0.50, 0.50, 0.045, GHOST)


def _parabola(c: _Canvas):
    """Ветвь параболы с отмеченными вершиной и фокусом."""
    c.stroke()
    path = QtGui.QPainterPath(c._p(0.86, 0.12))
    path.quadTo(c._p(0.06, 0.50), c._p(0.86, 0.88))
    c.painter.drawPath(path)
    c.stroke(GHOST, 1.1, dashed=True)
    c.line(0.24, 0.50, 0.88, 0.50)
    c.dot(0.30, 0.50, 0.05)
    c.dot(0.48, 0.50, 0.05, ACCENT)


def _ellipse_arc(c: _Canvas):
    """Часть эллипса: сам он намечен пунктиром, дуга — сплошной."""
    c.painter.save()
    c.painter.translate(c._p(0.5, 0.5))
    c.painter.rotate(-20.0)
    c.stroke(GHOST, 1.1, dashed=True)
    c.painter.drawEllipse(QtCore.QPointF(0.0, 0.0),
                          0.38 * c.unit, 0.22 * c.unit)
    c.stroke(STROKE, 1.9)
    rect = QtCore.QRectF(-0.38 * c.unit, -0.22 * c.unit,
                         0.76 * c.unit, 0.44 * c.unit)
    c.painter.drawArc(rect, int(20 * 16), int(160 * 16))
    c.painter.restore()
    c.dot(0.5, 0.5, 0.045, GHOST)


def _arc(c: _Canvas):
    c.stroke()
    c.arc(0.5, 0.62, 0.36, 20, 140)
    c.stroke(GHOST, 1.1, dashed=True)
    c.line(0.5, 0.62, 0.16, 0.62)
    c.dot(0.5, 0.62, 0.05, GHOST)
    c.dot(0.16, 0.62, 0.045)
    c.dot(0.84, 0.62, 0.045)


def _arc_3p(c: _Canvas):
    c.stroke()
    c.arc(0.5, 0.68, 0.4, 15, 150)
    c.dot(0.12, 0.66, 0.045)
    c.dot(0.5, 0.28, 0.045)
    c.dot(0.88, 0.66, 0.045)


def _slot(c: _Canvas):
    c.stroke()
    c.line(0.32, 0.34, 0.68, 0.34)
    c.line(0.32, 0.66, 0.68, 0.66)
    c.arc(0.32, 0.5, 0.16, 90, 180)
    c.arc(0.68, 0.5, 0.16, 270, 180)
    c.stroke(GHOST, 1.1, dashed=True)
    c.line(0.32, 0.5, 0.68, 0.5)


def _polygon(c: _Canvas):
    c.stroke()
    points = [
        (0.5 + 0.34 * math.cos(math.pi / 2 + i * math.pi / 3),
         0.5 - 0.34 * math.sin(math.pi / 2 + i * math.pi / 3))
        for i in range(6)
    ]
    c.polyline(points, closed=True)
    c.dot(0.5, 0.5, 0.045)


def _point(c: _Canvas):
    c.stroke(GHOST, 1.1)
    c.line(0.5, 0.2, 0.5, 0.8)
    c.line(0.2, 0.5, 0.8, 0.5)
    c.dot(0.5, 0.5, 0.11)


def _construction(c: _Canvas):
    c.stroke(GHOST, 1.5, dashed=True)
    c.line(0.14, 0.78, 0.86, 0.22)
    c.dot(0.14, 0.78, 0.045, GHOST)
    c.dot(0.86, 0.22, 0.045, GHOST)


def _trim(c: _Canvas):
    c.stroke(GHOST, 1.4, dashed=True)
    c.line(0.5, 0.72, 0.9, 0.72)
    c.stroke()
    c.line(0.1, 0.72, 0.5, 0.72)
    c.line(0.5, 0.16, 0.5, 0.88)
    c.stroke(ACCENT, 1.6)
    c.line(0.62, 0.34, 0.82, 0.54)
    c.line(0.82, 0.34, 0.62, 0.54)


def _extend(c: _Canvas):
    c.stroke()
    c.line(0.1, 0.7, 0.46, 0.7)
    c.line(0.86, 0.2, 0.86, 0.86)
    c.stroke(ACCENT, 1.5)
    c.arrow(0.5, 0.7, 0.82, 0.7, 0.1)


def _offset(c: _Canvas):
    c.stroke()
    c.polyline([(0.2, 0.8), (0.2, 0.34), (0.62, 0.34)])
    c.stroke(GHOST, 1.4, dashed=True)
    c.polyline([(0.36, 0.8), (0.36, 0.5), (0.62, 0.5)])
    c.stroke(ACCENT, 1.3)
    c.arrow(0.2, 0.66, 0.36, 0.66, 0.07)


def _mirror(c: _Canvas):
    c.stroke(ACCENT, 1.3, dashed=True)
    c.line(0.5, 0.12, 0.5, 0.88)
    c.stroke()
    c.polyline([(0.16, 0.74), (0.4, 0.74), (0.4, 0.3)])
    c.polyline([(0.84, 0.74), (0.6, 0.74), (0.6, 0.3)])


def _pattern_linear(c: _Canvas):
    c.stroke()
    for row in (0.32, 0.68):
        for column in (0.22, 0.5, 0.78):
            c.circle(column, row, 0.085)
    c.stroke(GHOST, 1.1, dashed=True)
    c.line(0.22, 0.86, 0.78, 0.86)


def _pattern_circular(c: _Canvas):
    c.stroke(GHOST, 1.1, dashed=True)
    c.circle(0.5, 0.5, 0.31)
    c.stroke()
    for index in range(6):
        angle = math.pi / 2 + index * math.pi / 3
        c.circle(0.5 + 0.31 * math.cos(angle), 0.5 - 0.31 * math.sin(angle), 0.075)
    c.dot(0.5, 0.5, 0.04, GHOST)


def _fillet(c: _Canvas):
    c.stroke(GHOST, 1.2, dashed=True)
    c.polyline([(0.18, 0.22), (0.78, 0.22), (0.78, 0.82)])
    c.stroke()
    c.polyline([(0.18, 0.22), (0.5, 0.22)])
    c.arc(0.5, 0.5, 0.28, 0, 90)
    c.polyline([(0.78, 0.5), (0.78, 0.82)])


def _chamfer(c: _Canvas):
    c.stroke(GHOST, 1.2, dashed=True)
    c.polyline([(0.18, 0.22), (0.78, 0.22), (0.78, 0.82)])
    c.stroke()
    c.polyline([(0.18, 0.22), (0.48, 0.22), (0.78, 0.52), (0.78, 0.82)])


def _dimension(c: _Canvas):
    c.stroke(GHOST, 1.1)
    c.line(0.18, 0.32, 0.18, 0.78)
    c.line(0.82, 0.32, 0.82, 0.78)
    c.stroke(ACCENT, 1.4)
    c.arrow(0.5, 0.56, 0.18, 0.56, 0.08)
    c.arrow(0.5, 0.56, 0.82, 0.56, 0.08)


def _dimension_radius(c: _Canvas):
    c.stroke()
    c.arc(0.5, 0.62, 0.38, 10, 160)
    c.stroke(ACCENT, 1.4)
    c.arrow(0.5, 0.62, 0.78, 0.36, 0.08)
    c.dot(0.5, 0.62, 0.04, GHOST)


def _dimension_angle(c: _Canvas):
    c.stroke()
    c.line(0.16, 0.8, 0.86, 0.8)
    c.line(0.16, 0.8, 0.74, 0.24)
    c.stroke(ACCENT, 1.4)
    c.arc(0.16, 0.8, 0.42, 0, 45)


def _relation_horizontal(c: _Canvas):
    c.stroke()
    c.line(0.14, 0.5, 0.86, 0.5)
    c.dot(0.14, 0.5, 0.05)
    c.dot(0.86, 0.5, 0.05)


def _relation_vertical(c: _Canvas):
    c.stroke()
    c.line(0.5, 0.14, 0.5, 0.86)
    c.dot(0.5, 0.14, 0.05)
    c.dot(0.5, 0.86, 0.05)


def _relation_parallel(c: _Canvas):
    c.stroke()
    c.line(0.24, 0.82, 0.5, 0.18)
    c.line(0.52, 0.82, 0.78, 0.18)


def _relation_perpendicular(c: _Canvas):
    c.stroke()
    c.line(0.22, 0.78, 0.86, 0.78)
    c.line(0.42, 0.16, 0.42, 0.78)
    c.stroke(GHOST, 1.1)
    c.polyline([(0.42, 0.64), (0.56, 0.64), (0.56, 0.78)])


def _relation_equal(c: _Canvas):
    c.stroke()
    c.line(0.2, 0.38, 0.8, 0.38)
    c.line(0.2, 0.62, 0.8, 0.62)


def _relation_coincident(c: _Canvas):
    c.stroke()
    c.circle(0.5, 0.5, 0.24)
    c.dot(0.5, 0.5, 0.08, ACCENT)


def _relation_tangent(c: _Canvas):
    c.stroke()
    c.circle(0.42, 0.56, 0.26)
    c.line(0.1, 0.24, 0.9, 0.24)
    c.dot(0.42, 0.3, 0.05, ACCENT)


def _relation_concentric(c: _Canvas):
    c.stroke()
    c.circle(0.5, 0.5, 0.34)
    c.circle(0.5, 0.5, 0.16)
    c.dot(0.5, 0.5, 0.04, ACCENT)


def _relation_midpoint(c: _Canvas):
    c.stroke()
    c.line(0.14, 0.5, 0.86, 0.5)
    c.dot(0.14, 0.5, 0.045)
    c.dot(0.86, 0.5, 0.045)
    c.dot(0.5, 0.5, 0.08, ACCENT)


def _relation_symmetric(c: _Canvas):
    c.stroke(ACCENT, 1.3, dashed=True)
    c.line(0.5, 0.12, 0.5, 0.88)
    c.stroke()
    c.dot(0.22, 0.5, 0.075)
    c.dot(0.78, 0.5, 0.075)


def _relation_point_on(c: _Canvas):
    c.stroke()
    c.line(0.12, 0.72, 0.88, 0.34)
    c.dot(0.5, 0.53, 0.085, ACCENT)


def _relation_fix(c: _Canvas):
    c.stroke()
    c.line(0.22, 0.7, 0.78, 0.7)
    for x in (0.3, 0.46, 0.62):
        c.line(x, 0.7, x - 0.1, 0.84)
    c.dot(0.5, 0.7, 0.075, ACCENT)


def _relations_show(c: _Canvas):
    c.stroke()
    c.circle(0.5, 0.5, 0.34)
    c.stroke(ACCENT, 1.4)
    c.line(0.5, 0.5, 0.5, 0.5)
    c.dot(0.5, 0.5, 0.1, ACCENT)


def _sketch(c: _Canvas):
    c.stroke(GHOST, 1.1, dashed=True)
    c.line(0.5, 0.12, 0.5, 0.88)
    c.line(0.12, 0.5, 0.88, 0.5)
    c.stroke()
    c.polyline([(0.22, 0.74), (0.7, 0.74), (0.7, 0.3)])


def _pad(c: _Canvas):
    c.stroke(GHOST, 1.2, dashed=True)
    c.polyline([(0.16, 0.72), (0.56, 0.72), (0.56, 0.42), (0.16, 0.42)], closed=True)
    c.stroke()
    c.polyline([(0.16, 0.42), (0.34, 0.26), (0.74, 0.26), (0.74, 0.56),
                (0.56, 0.72)])
    c.line(0.56, 0.42, 0.74, 0.26)
    c.line(0.56, 0.42, 0.56, 0.72)
    c.stroke(ACCENT, 1.3)
    c.arrow(0.4, 0.66, 0.58, 0.48, 0.08)


def _pocket(c: _Canvas):
    c.stroke()
    c.polyline([(0.14, 0.7), (0.14, 0.34), (0.86, 0.34), (0.86, 0.7)], closed=True)
    c.stroke(ACCENT, 1.4)
    c.polyline([(0.36, 0.34), (0.36, 0.56), (0.64, 0.56), (0.64, 0.34)])


def _revolve(c: _Canvas):
    c.stroke(ACCENT, 1.3, dashed=True)
    c.line(0.5, 0.1, 0.5, 0.9)
    c.stroke()
    c.circle(0.5, 0.5, 0.3)
    c.line(0.2, 0.5, 0.8, 0.5)
    c.stroke(GHOST, 1.1)
    c.arc(0.5, 0.5, 0.3, 200, 140)


def _hole(c: _Canvas):
    c.stroke()
    c.polyline([(0.14, 0.7), (0.14, 0.3), (0.86, 0.3), (0.86, 0.7)], closed=True)
    c.stroke(ACCENT, 1.4)
    c.circle(0.5, 0.5, 0.14)


def _shell(c: _Canvas):
    c.stroke()
    c.polyline([(0.16, 0.76), (0.16, 0.28), (0.84, 0.28), (0.84, 0.76)], closed=True)
    c.stroke(GHOST, 1.2, dashed=True)
    c.polyline([(0.28, 0.76), (0.28, 0.4), (0.72, 0.4), (0.72, 0.76)])


def _draft(c: _Canvas):
    """Уклон: призма с наклонёнными боками и нейтральная плоскость сверху."""
    c.stroke(GHOST, 1.2, dashed=True)
    c.line(0.08, 0.3, 0.92, 0.3)
    c.stroke()
    c.polyline([(0.16, 0.78), (0.3, 0.3), (0.7, 0.3), (0.84, 0.78)], closed=True)


def _transform(c: _Canvas):
    """Преобразование: объект и его сдвинутая копия со стрелкой."""
    c.stroke(GHOST, 1.2, dashed=True)
    c.polyline([(0.1, 0.62), (0.1, 0.3), (0.42, 0.3), (0.42, 0.62)], closed=True)
    c.stroke()
    c.polyline([(0.5, 0.82), (0.5, 0.5), (0.82, 0.5), (0.82, 0.82)], closed=True)
    c.stroke(ACCENT, 1.3)
    c.line(0.3, 0.5, 0.58, 0.68)
    c.polyline([(0.5, 0.6), (0.6, 0.7), (0.48, 0.72)], closed=True)


def _check_sketch(c: _Canvas):
    """Проверка: контур с разрывом и лупа над ним."""
    c.stroke()
    c.polyline([(0.14, 0.76), (0.14, 0.28), (0.5, 0.28)])
    c.polyline([(0.62, 0.28), (0.86, 0.28), (0.86, 0.5)])
    c.stroke(ACCENT, 1.5)
    c.circle(0.56, 0.28, 0.055)


def _repair_sketch(c: _Canvas):
    """Починка: тот же контур, но сомкнутый, и отметка о готовности."""
    c.stroke()
    c.polyline([(0.14, 0.76), (0.14, 0.28), (0.86, 0.28), (0.86, 0.56)])
    c.stroke(GREEN, 1.7)
    c.polyline([(0.5, 0.66), (0.63, 0.79), (0.88, 0.5)])


def _define(c: _Canvas):
    """Полное определение: контур и размерные стрелки по двум осям."""
    c.stroke()
    c.polyline([(0.3, 0.72), (0.3, 0.34), (0.82, 0.34)])
    c.stroke(ACCENT, 1.2)
    c.arrow(0.14, 0.72, 0.14, 0.34, 0.07)
    c.arrow(0.3, 0.86, 0.82, 0.86, 0.07)


def _mirror_body(c: _Canvas):
    c.stroke(ACCENT, 1.3, dashed=True)
    c.line(0.5, 0.1, 0.5, 0.9)
    c.stroke()
    c.polyline([(0.12, 0.72), (0.12, 0.36), (0.42, 0.28), (0.42, 0.64)], closed=True)
    c.stroke(GHOST, 1.3)
    c.polyline([(0.88, 0.72), (0.88, 0.36), (0.58, 0.28), (0.58, 0.64)], closed=True)


def _measure(c: _Canvas):
    c.stroke()
    c.polyline([(0.14, 0.66), (0.66, 0.14), (0.86, 0.34), (0.34, 0.86)], closed=True)
    c.stroke(GHOST, 1.1)
    for shift in (0.13, 0.26, 0.39):
        c.line(0.14 + shift, 0.66 + shift, 0.24 + shift, 0.56 + shift)


def _sketch_accept(c: _Canvas):
    """Выйти из эскиза, сохранив правки: лист и галка."""
    c.stroke(STROKE, 1.4)
    c.polyline([(0.14, 0.10), (0.14, 0.90), (0.62, 0.90), (0.62, 0.10)],
               closed=True)
    c.stroke(GREEN, 3.4)
    c.polyline([(0.30, 0.52), (0.48, 0.74), (0.92, 0.16)])


def _sketch_reject(c: _Canvas):
    """Выйти из эскиза, отменив правки: лист и косой крест."""
    c.stroke(STROKE, 1.4)
    c.polyline([(0.14, 0.10), (0.14, 0.90), (0.62, 0.90), (0.62, 0.10)],
               closed=True)
    c.stroke(RED, 3.4)
    c.line(0.42, 0.20, 0.92, 0.74)
    c.line(0.92, 0.20, 0.42, 0.74)


def _view_orientation(c: _Canvas):
    """Куб с помеченной гранью — выбор направления взгляда."""
    c.stroke()
    c.polyline([(0.16, 0.34), (0.50, 0.16), (0.84, 0.34),
                (0.84, 0.70), (0.50, 0.88), (0.16, 0.70)], closed=True)
    c.polyline([(0.16, 0.34), (0.50, 0.52), (0.84, 0.34)])
    c.line(0.50, 0.52, 0.50, 0.88)


def _view_fit(c: _Canvas):
    """Вписать: рамка со стрелками наружу."""
    c.stroke()
    c.polyline([(0.22, 0.22), (0.78, 0.22), (0.78, 0.78), (0.22, 0.78)], closed=True)
    for x1, y1, x2, y2 in ((0.46, 0.46, 0.28, 0.28), (0.54, 0.46, 0.72, 0.28),
                           (0.46, 0.54, 0.28, 0.72), (0.54, 0.54, 0.72, 0.72)):
        c.arrow(x1, y1, x2, y2, head=0.08)


def _view_style(c: _Canvas):
    """Способ отображения: половина закрашена, половина в рёбрах."""
    c.stroke()
    c.circle(0.5, 0.5, 0.33)
    path = QtGui.QPainterPath(c._p(0.5, 0.17))
    path.arcTo(QtCore.QRectF(c._p(0.17, 0.17),
                             QtCore.QSizeF(0.66 * c.unit, 0.66 * c.unit)),
               90.0, -180.0)
    path.closeSubpath()
    c.painter.setPen(QtCore.Qt.NoPen)
    c.painter.setBrush(QtGui.QBrush(STROKE))
    c.painter.drawPath(path)


def _visibility(c: _Canvas):
    """Условия видимости: глаз."""
    c.stroke()
    c.polyline([(0.14, 0.50), (0.34, 0.28), (0.66, 0.28), (0.86, 0.50)])
    c.polyline([(0.14, 0.50), (0.34, 0.72), (0.66, 0.72), (0.86, 0.50)])
    c.circle(0.50, 0.50, 0.13)


def _perspective(c: _Canvas):
    """Перспектива: сходящиеся к точке прямые."""
    c.stroke()
    c.polyline([(0.18, 0.24), (0.82, 0.40), (0.82, 0.66), (0.18, 0.82)], closed=True)
    c.line(0.18, 0.24, 0.18, 0.82)


def _dimension_ordinate(c: _Canvas):
    """Ординатный размер: общая база и выноски от неё."""
    c.stroke()
    c.line(0.16, 0.84, 0.16, 0.16)
    for y in (0.30, 0.52, 0.74):
        c.line(0.16, y, 0.74, y)
        c.dot(0.74, y, 0.045)


def _dimension_chain(c: _Canvas):
    """Цепочка размеров: подряд, встык."""
    c.stroke()
    for x in (0.14, 0.42, 0.66, 0.88):
        c.line(x, 0.30, x, 0.74)
    c.arrow(0.14, 0.52, 0.42, 0.52, head=0.07)
    c.arrow(0.42, 0.52, 0.66, 0.52, head=0.07)
    c.arrow(0.66, 0.52, 0.88, 0.52, head=0.07)


def _dimension_baseline(c: _Canvas):
    """Размеры от базовой линии: все от одного края."""
    c.stroke()
    c.line(0.14, 0.16, 0.14, 0.88)
    for x, y in ((0.44, 0.32), (0.66, 0.54), (0.88, 0.76)):
        c.line(x, 0.16, x, y)
        c.arrow(0.14, y, x, y, head=0.07)


def _face(c: _Canvas):
    """Грань детали: закрашенный четырёхугольник с ребром."""
    c.stroke()
    c.polyline([(0.16, 0.40), (0.52, 0.20), (0.86, 0.40),
                (0.50, 0.62)], closed=True)
    c.stroke(STROKE, 1.2, dashed=True)
    c.polyline([(0.16, 0.40), (0.16, 0.66), (0.50, 0.86), (0.50, 0.62)])


def _rectangle_3p(c: _Canvas):
    """Наклонный прямоугольник по трём точкам."""
    c.stroke()
    c.polyline([(0.14, 0.60), (0.56, 0.20), (0.86, 0.46), (0.44, 0.86)], closed=True)
    for x, y in ((0.14, 0.60), (0.56, 0.20), (0.86, 0.46)):
        c.dot(x, y)


def _parallelogram(c: _Canvas):
    c.stroke()
    c.polyline([(0.10, 0.78), (0.40, 0.22), (0.90, 0.22), (0.60, 0.78)], closed=True)


def _circle_3p(c: _Canvas):
    """Окружность через три отмеченные точки."""
    c.stroke()
    c.circle(0.5, 0.5, 0.33)
    for angle in (0.4, 2.5, 4.6):
        import math as _m
        c.dot(0.5 + 0.33 * _m.cos(angle), 0.5 + 0.33 * _m.sin(angle))


def _tangent_arc(c: _Canvas):
    """Дуга, продолжающая отрезок по касательной."""
    c.stroke()
    c.line(0.10, 0.74, 0.48, 0.74)
    c.arc(0.48, 0.40, 0.34, -90.0, 90.0)
    c.dot(0.48, 0.74)


def _center_slot(c: _Canvas):
    """Паз, отложенный от центра."""
    c.stroke()
    c.line(0.30, 0.34, 0.70, 0.34)
    c.line(0.30, 0.66, 0.70, 0.66)
    c.arc(0.14, 0.34, 0.16, 90.0, 180.0)
    c.arc(0.54, 0.34, 0.16, -90.0, 180.0)
    c.dot(0.50, 0.50, 0.05)
    c.stroke(STROKE, 1.0, dashed=True)
    c.line(0.50, 0.24, 0.50, 0.76)


def _delete(c: _Canvas):
    """Удалить: объект и косой крест."""
    c.stroke()
    c.polyline([(0.12, 0.30), (0.52, 0.30), (0.52, 0.86), (0.12, 0.86)], closed=True)
    c.stroke(RED, 2.4)
    c.line(0.58, 0.16, 0.92, 0.52)
    c.line(0.92, 0.16, 0.58, 0.52)


def _convert(c: _Canvas):
    """Перенос ребра детали в эскиз: грань и снятая с неё линия."""
    c.stroke(STROKE, 1.0, dashed=True)
    c.polyline([(0.12, 0.30), (0.48, 0.14), (0.88, 0.32), (0.52, 0.50)], closed=True)
    c.stroke(STROKE, 2.2)
    c.line(0.12, 0.74, 0.88, 0.74)
    c.dot(0.12, 0.74)
    c.dot(0.88, 0.74)
    c.stroke(STROKE, 1.0)
    c.arrow(0.50, 0.56, 0.50, 0.70, head=0.07)


def _intersect(c: _Canvas):
    """Кривая пересечения: тело, секущая плоскость и след на ней."""
    c.stroke(GHOST, 1.1, dashed=True)
    # Тело — призма, у которой видно верх и переднюю стенку.
    c.polyline([(0.20, 0.20), (0.62, 0.20), (0.62, 0.78), (0.20, 0.78)],
               closed=True)
    c.polyline([(0.20, 0.20), (0.40, 0.10), (0.82, 0.10), (0.62, 0.20)],
               closed=True)
    c.line(0.82, 0.10, 0.82, 0.68)
    c.line(0.62, 0.78, 0.82, 0.68)
    c.stroke(STROKE, 1.0, dashed=True)
    # Секущая плоскость — наклонный четырёхугольник поперёк тела.
    c.polyline([(0.10, 0.56), (0.52, 0.66), (0.94, 0.50), (0.52, 0.40)],
               closed=True)
    # След: то, ради чего команда и нужна.
    c.stroke(ACCENT, 2.2)
    c.polyline([(0.20, 0.53), (0.62, 0.62), (0.82, 0.53), (0.40, 0.44)],
               closed=True)


def _hole_shape(c, near_wide=0.0, near_deep=0.0, sink=0.0, taper=0.0,
                thread=False):
    """Разрез отверстия: материал штриховкой, канал белым.

    Общая рисовалка на все виды: разница между простым, цековкой и
    зенковкой — это числа, а не разные картинки. Так значки заведомо
    остаются одного семейства, а не расходятся стилем.
    """
    c.stroke(GHOST, 1.0)
    for step in range(5):
        x = 0.10 + step * 0.05
        c.line(x, 0.16, x - 0.06, 0.30)
        c.line(1.0 - x, 0.16, 1.0 - x + 0.06, 0.30)
    c.stroke(STROKE, 1.6)
    c.line(0.06, 0.16, 0.94, 0.16)
    core = 0.13
    left, right = 0.5 - core, 0.5 + core
    top = 0.16
    if near_wide:
        wide = 0.5 - near_wide
        c.polyline([(wide, top), (wide, top + near_deep),
                    (left, top + near_deep), (left, 0.88)])
        c.polyline([(1.0 - wide, top), (1.0 - wide, top + near_deep),
                    (right, top + near_deep), (right, 0.88)])
    elif sink:
        c.polyline([(0.5 - sink, top), (left, top + sink * 0.9), (left, 0.88)])
        c.polyline([(0.5 + sink, top), (right, top + sink * 0.9), (right, 0.88)])
    elif taper:
        c.polyline([(left - taper, top), (left + taper, 0.88)])
        c.polyline([(right + taper, top), (right - taper, 0.88)])
    else:
        c.line(left, top, left, 0.88)
        c.line(right, top, right, 0.88)
    if thread:
        c.stroke(ACCENT, 1.2)
        for step in range(5):
            y = 0.34 + step * 0.12
            c.line(left, y, right, y - 0.05)


def _hole_simple(c):
    _hole_shape(c)


def _hole_counterbore(c):
    _hole_shape(c, near_wide=0.28, near_deep=0.22)


def _hole_countersink(c):
    _hole_shape(c, sink=0.30)


def _hole_both(c):
    _hole_shape(c, near_wide=0.30, near_deep=0.16)
    c.stroke(STROKE, 1.4)
    c.polyline([(0.30, 0.32), (0.37, 0.46)])
    c.polyline([(0.70, 0.32), (0.63, 0.46)])


def _hole_taper(c):
    _hole_shape(c, taper=0.07)


def _hole_threaded(c):
    _hole_shape(c, thread=True)


def _grid(c: _Canvas):
    c.stroke(STROKE, 1.0)
    for value in (0.22, 0.44, 0.66, 0.88):
        c.line(value, 0.12, value, 0.88)
        c.line(0.12, value, 0.88, value)


def _hole_pattern(c: _Canvas):
    c.stroke()
    for x in (0.28, 0.72):
        for y in (0.28, 0.72):
            c.circle(x, y, 0.13)


def _square(c: _Canvas, cx: float, cy: float, half: float) -> None:
    c.polyline([(cx - half, cy - half), (cx + half, cy - half),
                (cx + half, cy + half), (cx - half, cy + half)], closed=True)


def _datum_plane(c: _Canvas):
    """Наклонный четырёхугольник и грань под ним со стрелкой расстояния."""
    c.stroke(GHOST, 1.1, dashed=True)
    c.polyline([(0.10, 0.72), (0.62, 0.72), (0.90, 0.60), (0.38, 0.60)],
               closed=True)
    c.stroke()
    c.polyline([(0.10, 0.42), (0.62, 0.42), (0.90, 0.30), (0.38, 0.30)],
               closed=True)
    c.stroke(ACCENT, 1.4)
    c.arrow(0.50, 0.66, 0.50, 0.38, 0.07)


def _linear_pattern(c: _Canvas):
    """Сетка квадратиков вдоль двух осей."""
    c.stroke(GHOST, 1.1, dashed=True)
    c.line(0.13, 0.87, 0.90, 0.87)
    c.line(0.13, 0.87, 0.13, 0.12)
    c.stroke()
    for y in (0.30, 0.64):
        for x in (0.28, 0.56, 0.84):
            if y > 0.5 and x > 0.7:
                continue
            _square(c, x, y, 0.095)


def _circular_pattern(c: _Canvas):
    """Квадратики по окружности вокруг центра."""
    c.stroke(GHOST, 1.1, dashed=True)
    c.circle(0.5, 0.5, 0.32)
    c.stroke()
    for step in range(4):
        angle = math.pi / 2.0 * step + math.pi / 4.0
        _square(c, 0.5 + 0.32 * math.cos(angle),
                0.5 + 0.32 * math.sin(angle), 0.095)
    c.dot(0.5, 0.5, 0.05)


def _point_on(c: _Canvas):
    """Точка на объекте."""
    c.stroke()
    c.line(0.12, 0.70, 0.88, 0.34)
    c.dot(0.50, 0.52, 0.085)


def _reference_point(c: _Canvas):
    """Ссылка на деталь: грань и снятая с неё точка."""
    c.stroke(STROKE, 1.0, dashed=True)
    c.polyline([(0.10, 0.44), (0.46, 0.20), (0.90, 0.42), (0.54, 0.66)], closed=True)
    c.dot(0.46, 0.20, 0.075)
    c.stroke(ACCENT, 1.6)
    c.line(0.46, 0.20, 0.46, 0.86)


def _edit_feature(c: _Canvas):
    """Править операцию: карандаш над телом."""
    c.stroke(STROKE, 1.0, dashed=True)
    c.polyline([(0.10, 0.62), (0.42, 0.46), (0.78, 0.64), (0.46, 0.82)], closed=True)
    c.stroke(STROKE, 1.8)
    c.polyline([(0.34, 0.56), (0.78, 0.14), (0.90, 0.26), (0.46, 0.68)], closed=True)
    c.line(0.34, 0.56, 0.30, 0.72)
    c.line(0.30, 0.72, 0.46, 0.68)


def _rebuild(c: _Canvas):
    """Пересчитать: замкнутая стрелка."""
    c.stroke(STROKE, 1.8)
    c.arc(0.5, 0.5, 0.32, 40.0, 260.0)
    import math as _m
    angle = _m.radians(40.0)
    x, y = 0.5 + 0.32 * _m.cos(angle), 0.5 - 0.32 * _m.sin(angle)
    c.arrow(x - 0.10, y + 0.14, x, y, head=0.10)


def _region(c: _Canvas):
    """Закрашенная область: контур с заливкой и вырезом."""
    c.painter.setPen(QtCore.Qt.NoPen)
    c.painter.setBrush(QtGui.QBrush(QtGui.QColor(120, 150, 190, 110)))
    path = QtGui.QPainterPath()
    path.addRect(QtCore.QRectF(c._p(0.14, 0.24),
                               QtCore.QSizeF(0.72 * c.unit, 0.52 * c.unit)))
    hole = QtGui.QPainterPath()
    hole.addEllipse(c._p(0.50, 0.50), 0.14 * c.unit, 0.14 * c.unit)
    c.painter.drawPath(path.subtracted(hole))
    c.stroke()
    c.polyline([(0.14, 0.24), (0.86, 0.24), (0.86, 0.76), (0.14, 0.76)], closed=True)
    c.circle(0.50, 0.50, 0.14)


GLYPHS = {
    "region": _region,
    "edit_feature": _edit_feature,
    "rebuild": _rebuild,
    "rectangle_3p": _rectangle_3p,
    "parallelogram": _parallelogram,
    "circle_3p": _circle_3p,
    "tangent_arc": _tangent_arc,
    "center_slot": _center_slot,
    "delete": _delete,
    "convert": _convert,
    "intersect": _intersect,
    "hole_simple": _hole_simple,
    "hole_counterbore": _hole_counterbore,
    "hole_countersink": _hole_countersink,
    "hole_both": _hole_both,
    "hole_taper": _hole_taper,
    "hole_threaded": _hole_threaded,
    "grid": _grid,
    "hole_pattern": _hole_pattern,
    "datum_plane": _datum_plane,
    "linear_pattern": _linear_pattern,
    "circular_pattern": _circular_pattern,
    "point_on": _point_on,
    "reference_point": _reference_point,
    "sketch_accept": _sketch_accept,
    "sketch_reject": _sketch_reject,
    "view_orientation": _view_orientation,
    "view_fit": _view_fit,
    "view_style": _view_style,
    "visibility": _visibility,
    "perspective": _perspective,
    "dimension_ordinate": _dimension_ordinate,
    "dimension_chain": _dimension_chain,
    "dimension_baseline": _dimension_baseline,
    "face": _face,
    "select": _select,
    "line": _line,
    "polyline": _polyline,
    "rectangle": _rectangle,
    "center_rectangle": _center_rectangle,
    "circle": _circle,
    "ellipse": _ellipse,
    "hyperbola": _hyperbola,
    "parabola": _parabola,
    "ellipse_arc": _ellipse_arc,
    "spline": _spline,
    "arc": _arc,
    "arc_3p": _arc_3p,
    "slot": _slot,
    "polygon": _polygon,
    "point": _point,
    "construction": _construction,
    "trim": _trim,
    "extend": _extend,
    "offset": _offset,
    "mirror": _mirror,
    "pattern_linear": _pattern_linear,
    "pattern_circular": _pattern_circular,
    "fillet": _fillet,
    "chamfer": _chamfer,
    "dimension": _dimension,
    "dimension_radius": _dimension_radius,
    "dimension_angle": _dimension_angle,
    "horizontal": _relation_horizontal,
    "vertical": _relation_vertical,
    "parallel": _relation_parallel,
    "perpendicular": _relation_perpendicular,
    "equal": _relation_equal,
    "coincident": _relation_coincident,
    "tangent": _relation_tangent,
    "concentric": _relation_concentric,
    "midpoint": _relation_midpoint,
    "symmetric": _relation_symmetric,
    "point_on_line": _relation_point_on,
    "point_on_curve": _relation_point_on,
    "equal_radius": _relation_concentric,
    "anchor": _relation_fix,
    "relations": _relations_show,
    "sketch": _sketch,
    "pad": _pad,
    "pocket": _pocket,
    "revolve": _revolve,
    "hole": _hole,
    "shell": _shell,
    "draft": _draft,
    "transform": _transform,
    "define": _define,
    "check_sketch": _check_sketch,
    "repair_sketch": _repair_sketch,
    "mirror_body": _mirror_body,
    "measure": _measure,
}


def icon(name: str, size: int = 32, color: QtGui.QColor | None = None) -> QtGui.QIcon:
    """Значок по имени. Неизвестное имя даёт пустой значок, а не отказ —
    из-за отсутствующей картинки команда исчезать не должна."""
    key = (name, size, color.name() if color else "")
    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    ratio = 2  # рисуем крупнее и отдаём с коэффициентом — иначе мылится
    pixmap = QtGui.QPixmap(size * ratio, size * ratio)
    pixmap.fill(QtCore.Qt.transparent)
    glyph = GLYPHS.get(name)
    if glyph is not None:
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        canvas = _Canvas(painter, size * ratio)
        global STROKE
        previous = STROKE
        if color is not None:
            STROKE = color
        try:
            glyph(canvas)
        finally:
            STROKE = previous
        painter.end()
    # Коэффициент ставится ПОСЛЕ рисования, и это не мелочь. QPainter по
    # картинке с коэффициентом сам делит на него координаты: холст в 40
    # единиц превращался в поле 20, глиф рисовался вдвое крупнее рамки, и
    # у него отрезало правый нижний угол. На экране это выглядело как
    # «значок обрезан», а на самом деле обрезаны были ВСЕ значки — просто
    # у тех, что нарисованы ближе к левому верхнему углу, потеря была
    # меньше заметна.
    pixmap.setDevicePixelRatio(ratio)
    result = QtGui.QIcon(pixmap)
    _CACHE[key] = result
    return result
