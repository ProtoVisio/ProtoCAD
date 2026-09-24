"""Кубик видов: показывает, откуда смотрим, и позволяет повернуть щелчком.

Рисуется обычным ``QPainter`` в отдельном виджете поверх вида, а не внутри
``paintGL``. Причина не в удобстве: рисование ``QPainter`` внутри
``paintGL`` на здешней видеокарте портит состояние GL — выбор мышью
переставал работать, а снимок кадра ронял окно. Отдельный прозрачный
виджет обходится без этого совсем.

Углы кубик задаёт те же, что и кнопки видов: разойдись они — «Сверху» на
кнопке и «Сверху» на кубике показывали бы разное, и доверия не осталось бы
ни к тому, ни к другому.
"""

from __future__ import annotations

import math

from PySide6 import QtCore, QtGui, QtWidgets

#: Куда смотрит камера при именованном виде. Хранится СТОРОНОЙ, с которой
#: смотрят, а не углами: сторона очевидна и проверяема, углы — нет.
SIDES = {
    "front": ((0.0, -1.0, 0.0), "Спереди"),
    "back": ((0.0, 1.0, 0.0), "Сзади"),
    "left": ((-1.0, 0.0, 0.0), "Слева"),
    "right": ((1.0, 0.0, 0.0), "Справа"),
    "top": ((0.0, 0.0, 1.0), "Сверху"),
    "bottom": ((0.0, 0.0, -1.0), "Снизу"),
    "iso": ((1.0, -1.0, 1.0), "Изометрия"),
    "dimetric": ((0.7, -1.0, 0.5), "Диметрия"),
}

#: Подпись на грани кубика по её внешней нормали.
FACE_TITLES = {
    (0, -1, 0): "СПЕРЕДИ",
    (0, 1, 0): "СЗАДИ",
    (-1, 0, 0): "СЛЕВА",
    (1, 0, 0): "СПРАВА",
    (0, 0, 1): "СВЕРХУ",
    (0, 0, -1): "СНИЗУ",
}

#: Что пишется НА грани. Полное слово в неё не влезает: грань кубика при
#: взгляде под углом сжимается вдвое, и «СПЕРЕДИ» вылезает за её край на
#: соседнюю. Полное название остаётся в подсказке под курсором.
FACE_MARKS = {
    (0, -1, 0): "ПЕР",
    (0, 1, 0): "ЗАД",
    (-1, 0, 0): "ЛЕВ",
    (1, 0, 0): "ПРАВ",
    (0, 0, 1): "ВЕРХ",
    (0, 0, -1): "НИЗ",
}


def angles(direction) -> tuple[float, float]:
    """Сторона обзора → углы камеры вида.

    Обратно к ``eye = target + (cos p cos y, cos p sin y, sin p) * L``: вид
    задаётся именно этим смещением, и любая другая формула здесь означала
    бы, что кубик и камера расходятся.
    """
    x, y, z = (float(value) for value in direction)
    length = math.sqrt(x * x + y * y + z * z)
    if length < 1e-9:
        return 0.0, 0.0
    x, y, z = x / length, y / length, z / length
    pitch = math.degrees(math.asin(max(-1.0, min(1.0, z))))
    # Строго сверху и строго снизу азимут не определён: любое значение даёт
    # ту же точку. Берём то, при котором ось Y смотрит вверх экрана.
    yaw = -90.0 if abs(z) > 1.0 - 1e-9 else math.degrees(math.atan2(y, x))
    return yaw, pitch


def up_for(direction) -> tuple[float, float, float]:
    """Что считать «верхом» экрана при таком взгляде.

    Обычно это ось Z. Но при взгляде строго сверху или снизу Z совпадает с
    направлением взгляда, верх становится неопределённым, и деталь
    разворачивается на случайный угол — отсюда прежний «вид сверху», где
    плита 80 × 120 выглядела шире, чем выше.
    """
    z = float(direction[2])
    length = math.sqrt(sum(float(value) ** 2 for value in direction))
    if length > 1e-9 and abs(z / length) > 0.999:
        return (0.0, 1.0, 0.0)
    return (0.0, 0.0, 1.0)


def _basis(yaw: float, pitch: float, up):
    """Оси экрана при таком положении камеры: вправо, вверх, к зрителю."""
    y, p = math.radians(yaw), math.radians(pitch)
    toward = (math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), math.sin(p))
    forward = tuple(-value for value in toward)
    # За полюсом верх переворачивается — так же, как в самом виде: иначе
    # кубик показывает деталь стоящей, когда на экране она вверх ногами.
    if abs(((pitch + 180.0) % 360.0) - 180.0) > 90.0:
        up = tuple(-value for value in up)
    right = _cross(forward, up)
    if _norm(right) < 1e-6:
        right = _cross(forward, (0.0, 1.0, 0.0))
    right = _unit(right)
    upward = _unit(_cross(right, forward))
    return right, upward, toward


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _norm(v) -> float:
    return math.sqrt(sum(value * value for value in v))


def _unit(v):
    length = _norm(v)
    return tuple(value / length for value in v) if length > 1e-12 else v


def _dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


class ViewCube(QtWidgets.QWidget):
    """Кубик в углу вида: грань, ребро или угол — щелчком.

    Двадцать шесть направлений: шесть граней, двенадцать рёбер, восемь
    углов. Попадание ищется по БЛИЖАЙШЕМУ спроецированному направлению, а
    не по многоугольникам граней: у грани, повёрнутой почти ребром, площадь
    на экране вырождается, и попасть в неё мышью нельзя, хотя видно её
    прекрасно.
    """

    oriented = QtCore.Signal(float, float, object)

    SIZE = 148
    MARGIN = 14
    #: Доля половины ребра, отдаваемая угловым и рёберным зонам. Именно
    #: она делает грань «усечённой»: середина грани — сама грань, полосы по
    #: краям — рёбра (взгляд под 45°), квадраты в углах — углы (изометрия).
    CHAMFER = 0.32

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setMouseTracking(True)
        self.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.yaw = 45.0
        self.pitch = 28.0
        self.up = (0.0, 0.0, 1.0)
        #: Оси экрана: вправо, вверх, к зрителю. Кубик рисуется по ним, а не
        #: по двум углам: по углам не видно, перевёрнут вид или повёрнут
        #: вокруг взгляда, и кубик показывал бы не то, что на экране.
        self.axes = _basis(self.yaw, self.pitch, self.up)
        self._hover = None
        self._zones = self._build_zones()

    @staticmethod
    def _build_zones() -> list[tuple[int, int, int]]:
        return [
            (i, j, k)
            for i in (-1, 0, 1) for j in (-1, 0, 1) for k in (-1, 0, 1)
            if (i, j, k) != (0, 0, 0)
        ]

    def follow(self, yaw: float, pitch: float, up=None) -> None:
        """Показать положение камеры, заданное углами."""
        self.yaw, self.pitch = float(yaw), float(pitch)
        if up is not None:
            self.up = tuple(float(value) for value in up)
        self._set_axes(_basis(self.yaw, self.pitch, self.up))

    def follow_basis(self, basis) -> None:
        """Показать положение камеры по её осям (строки: вправо, вверх, к
        зрителю) — так, как их отдаёт вьюпорт."""
        rows = [tuple(float(value) for value in row) for row in basis]
        self._set_axes((rows[0], rows[1], rows[2]))

    def _set_axes(self, axes) -> None:
        rounded = tuple(tuple(round(value, 5) for value in axis) for axis in axes)
        if rounded != tuple(tuple(round(value, 5) for value in axis) for axis in self.axes):
            self.axes = axes
            self.update()

    # --- геометрия ---

    def _project(self, point) -> QtCore.QPointF:
        right, upward, _ = self.axes
        # Куб занимает виджет почти целиком. Множитель подобран так, что
        # самый дальний угол (полудиагональ 0.87 от полуребра) остаётся
        # внутри поля: при большем куб обрезается углами, при меньшем
        # висит крошечным пятном посреди пустоты — чем он и был.
        scale = self.width() * 0.52
        middle = self.width() / 2.0
        return QtCore.QPointF(
            middle + _dot(point, right) * scale,
            middle - _dot(point, upward) * scale,
        )

    def _visible(self, zone) -> bool:
        """Видна ли зона: её направление должно смотреть на зрителя."""
        _, _, toward = self.axes
        return _dot(_unit(zone), toward) > 1e-3

    def zone_at(self, position) -> tuple | None:
        """Зона под указателем — по нарисованным клеткам.

        Проверяется попадание В МНОГОУГОЛЬНИК клетки, а не близость к
        центру зоны: клетки разной величины, и «ближе к центру» отдавало бы
        крупной грани щелчки, попавшие в узкую полосу ребра рядом с ней.
        """
        _, _, toward = self.axes
        for axis in range(3):
            for sign in (-1, 1):
                normal = [0, 0, 0]
                normal[axis] = sign
                if _dot(tuple(normal), toward) <= 1e-3:
                    continue
                for zone, corners in self._cells(axis, sign):
                    polygon = QtGui.QPolygonF([self._project(c) for c in corners])
                    if polygon.containsPoint(position, QtCore.Qt.OddEvenFill):
                        return zone
        return None

    # --- отрисовка ---

    def _cells(self, axis: int, sign: int):
        """Грань кубика девятью клетками: грань, четыре ребра, четыре угла.

        Разбиение то же, по которому ищется попадание: клетка и зона —
        одно и то же, поэтому нарисованное и нажимаемое совпадают всегда.
        Без клеток по кубику нельзя выбрать взгляд под 45° — не видно, за
        что там браться.
        """
        edge = self.CHAMFER
        first, second = [index for index in range(3) if index != axis]
        spans = {-1: (-1.0, -edge), 0: (-edge, edge), 1: (edge, 1.0)}
        for m in (-1, 0, 1):
            for n in (-1, 0, 1):
                low_u, high_u = spans[m]
                low_v, high_v = spans[n]
                corners = []
                for u, v in ((low_u, low_v), (high_u, low_v),
                             (high_u, high_v), (low_u, high_v)):
                    point = [0.0, 0.0, 0.0]
                    point[axis] = float(sign)
                    point[first] = u
                    point[second] = v
                    corners.append(tuple(value * 0.5 for value in point))
                zone = [0, 0, 0]
                zone[axis] = sign
                zone[first] = m
                zone[second] = n
                yield tuple(zone), corners

    def paintEvent(self, _event) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        _, _, toward = self.axes

        font = painter.font()
        font.setPointSizeF(max(6.0, self.width() * 0.058))
        painter.setFont(font)

        for axis in range(3):
            for sign in (-1, 1):
                normal = [0, 0, 0]
                normal[axis] = sign
                if _dot(tuple(normal), toward) <= 1e-3:
                    continue
                for zone, corners in self._cells(axis, sign):
                    self._draw_cell(painter, zone, corners, tuple(normal))

    def _draw_cell(self, painter, zone, corners, normal) -> None:
        polygon = QtGui.QPolygonF([self._project(c) for c in corners])
        centre = sum(abs(value) for value in zone) == 1
        lit = self._hover is not None and tuple(self._hover) == tuple(zone)
        if lit:
            fill = QtGui.QColor("#a9cbec")
        elif centre:
            fill = QtGui.QColor("#eef1f6")
        else:
            # Рёбра и углы темнее грани: иначе кубик читается как ровный
            # квадрат и по нему не видно, что у него есть скосы.
            fill = QtGui.QColor("#dfe4ec")
        painter.setBrush(QtGui.QBrush(fill))
        painter.setPen(QtGui.QPen(QtGui.QColor("#8894a5"), 1.0))
        painter.drawPolygon(polygon)
        if not centre:
            return
        box = polygon.boundingRect()
        mark = FACE_MARKS.get(normal, "")
        if box.width() > painter.fontMetrics().horizontalAdvance(mark) + 3:
            painter.setPen(QtGui.QPen(QtGui.QColor("#33404f")))
            painter.drawText(box, QtCore.Qt.AlignCenter, mark)

    # --- мышь ---

    def mouseMoveEvent(self, event) -> None:
        zone = self.zone_at(event.position())
        if zone != self._hover:
            self._hover = zone
            self.setToolTip(self._title(zone) if zone else "")
            self.update()

    def leaveEvent(self, _event) -> None:
        if self._hover is not None:
            self._hover = None
            self.update()

    def mousePressEvent(self, event) -> None:
        zone = self.zone_at(event.position())
        if zone is None:
            return
        self.orient_to(zone)

    def orient_to(self, direction) -> None:
        """Попросить окно повернуть вид. Сам кубик при этом НЕ поворачивается.

        Кубик показывает положение камеры, а не своё собственное. Если он
        поворачивается сам, а окно поворот отклоняет — например, в эскизе,
        где вид закреплён на плоскости, — кубик начинает показывать то,
        чего на экране нет. Он сдвинется, когда сдвинется камера, и не
        раньше.
        """
        yaw, pitch = angles(direction)
        self.oriented.emit(yaw, pitch, up_for(direction))

    @staticmethod
    def _title(zone) -> str:
        named = FACE_TITLES.get(tuple(zone))
        if named:
            return named.capitalize()
        kind = "Ребро" if sum(abs(v) for v in zone) == 2 else "Угол"
        parts = [FACE_TITLES[tuple(axis)].lower()
                 for axis in _split(zone)]
        return f"{kind}: {' + '.join(parts)}"


def _split(zone):
    for index, value in enumerate(zone):
        if value:
            axis = [0, 0, 0]
            axis[index] = value
            yield axis
