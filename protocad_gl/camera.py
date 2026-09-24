"""Поворот камеры — матрицей, без углов.

Прежняя камера хранила азимут и наклон. У такой записи есть особые точки —
полюса: при взгляде строго сверху азимут не определён, и вид сверху
(кнопкой или кубиком) вёл себя странно — вращение мышью по горизонтали не
делало ничего, а первое же движение по вертикали разворачивало деталь
рывком на 90°. Никакая правка углов этого не лечит: особая точка — свойство
самой записи.

Здесь поворот хранится тремя осями экрана в координатах модели: вправо,
вверх, к зрителю. Мышь поворачивает их вокруг осей ЭКРАНА — по горизонтали
вокруг «вверх», по вертикали вокруг «вправо», как в SolidWorks. Каждый шаг —
маленький поворот той же тройки, поэтому вращение непрерывно в любую
сторону и сколько угодно оборотов: ни шва на 360°, ни полюса.

Углы (азимут и наклон) остались как ПОКАЗ положения — для кубика видов и
старого кода, который ими ставит вид. Задать вид углами можно, но храним
мы всё равно оси.
"""

from __future__ import annotations

import math

import numpy as np

#: Вид по умолчанию: азимут 45°, наклон 28° — изометрия, к которой привыкли.
DEFAULT_YAW = 45.0
DEFAULT_PITCH = 28.0


def _unit(vector) -> np.ndarray:
    vector = np.asarray(vector, np.float64)
    length = float(np.linalg.norm(vector))
    return vector / length if length > 1e-12 else vector


def _turned(vector, axis, degrees: float) -> np.ndarray:
    """Поворот вектора вокруг оси (формула Родрига)."""
    axis = _unit(axis)
    angle = math.radians(degrees)
    vector = np.asarray(vector, np.float64)
    return (vector * math.cos(angle) + np.cross(axis, vector) * math.sin(angle)
            + axis * float(axis @ vector) * (1.0 - math.cos(angle)))


def toward_of(yaw: float, pitch: float) -> np.ndarray:
    """Откуда смотрим (от цели к глазу) по азимуту и наклону."""
    y, p = math.radians(yaw), math.radians(pitch)
    return np.array([math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), math.sin(p)])


class Orbit:
    """Оси экрана в координатах модели: ``right``, ``up``, ``toward``.

    ``toward`` — от цели к глазу (так же, как «сторона, с которой смотрим» у
    кубика видов). Тройка всегда правая и ортонормированная.
    """

    def __init__(self):
        self.right = np.array([1.0, 0.0, 0.0])
        self.up = np.array([0.0, 0.0, 1.0])
        self.toward = np.array([0.0, -1.0, 0.0])
        self.set_angles(DEFAULT_YAW, DEFAULT_PITCH)

    # --- задать ----------------------------------------------------------------

    def set_view(self, toward, up=None) -> None:
        """Смотреть со стороны ``toward``, верх экрана — по ``up``.

        ``up`` проецируется на плоскость экрана. Если он почти совпадает с
        направлением взгляда (сверху при верхе по Z), берётся ось Y — так
        вид сверху показывает деталь, как на чертеже.
        """
        toward = _unit(toward)
        if float(np.linalg.norm(toward)) < 1e-9:
            return
        up = _unit(up if up is not None else (0.0, 0.0, 1.0))
        if abs(float(up @ toward)) > 1.0 - 1e-6:
            up = np.array([0.0, 1.0, 0.0]) if abs(toward[2]) > 0.5 else np.array(
                [0.0, 0.0, 1.0])
        right = _unit(np.cross(up, toward))
        self.right, self.up, self.toward = right, _unit(np.cross(toward, right)), toward

    def set_angles(self, yaw: float, pitch: float, up=(0.0, 0.0, 1.0)) -> None:
        self.set_view(toward_of(yaw, pitch), up)

    # --- повернуть --------------------------------------------------------------

    def rotate(self, yaw: float, pitch: float) -> None:
        """Повернуть вид: ``yaw`` — вокруг оси «вверх» экрана, ``pitch`` —
        вокруг оси «вправо» (плюс поднимает глаз, как прежний наклон)."""
        if yaw:
            self.toward = _turned(self.toward, self.up, yaw)
            self.right = _turned(self.right, self.up, yaw)
        if pitch:
            self.toward = _turned(self.toward, self.right, -pitch)
            self.up = _turned(self.up, self.right, -pitch)
        self._straighten()

    def roll(self, degrees: float) -> None:
        """Повернуть вокруг направления взгляда."""
        self.right = _turned(self.right, self.toward, degrees)
        self.up = _turned(self.up, self.toward, degrees)
        self._straighten()

    def _straighten(self) -> None:
        """Вернуть тройке прямые углы: ошибки округления за тысячи шагов
        иначе скапливаются, и картинка начинает перекашиваться."""
        toward = _unit(self.toward)
        right = _unit(self.right - toward * float(self.right @ toward))
        self.toward, self.right = toward, right
        self.up = _unit(np.cross(toward, right))

    # --- прочитать -------------------------------------------------------------

    @property
    def yaw(self) -> float:
        """Азимут направления «к зрителю», градусы (для показа)."""
        x, y, z = self.toward
        if math.hypot(x, y) < 1e-9:
            # Строго сверху или снизу азимут не определён. Берём предел со
            # стороны текущего верха экрана: глаз, чуть отведённый от
            # полюса, уходит от верха (сверху) или к нему (снизу).
            if z > 0:
                return math.degrees(math.atan2(-self.up[1], -self.up[0]))
            return math.degrees(math.atan2(self.up[1], self.up[0]))
        return math.degrees(math.atan2(y, x))

    @property
    def pitch(self) -> float:
        return math.degrees(math.asin(max(-1.0, min(1.0, float(self.toward[2])))))

    def basis(self) -> np.ndarray:
        """Строки: вправо, вверх, к зрителю — это и есть поворот вида."""
        return np.array([self.right, self.up, self.toward])

    def view_matrix(self, target, distance: float) -> np.ndarray:
        """Матрица вида 4×4 (как у gluLookAt)."""
        target = np.asarray(target, np.float64)
        eye = target + self.toward * distance
        matrix = np.eye(4)
        matrix[:3, :3] = self.basis()
        matrix[:3, 3] = -matrix[:3, :3] @ eye
        return matrix.astype(np.float32)

    def copy(self) -> "Orbit":
        other = Orbit.__new__(Orbit)
        other.right, other.up, other.toward = (self.right.copy(), self.up.copy(),
                                               self.toward.copy())
        return other
