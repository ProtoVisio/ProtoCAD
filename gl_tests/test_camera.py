"""Камера: вращение без шва на 360° и без особой точки на виде сверху."""

import math

import numpy as np
import pytest

from protocad_gl.camera import Orbit


def _angle(first, second) -> float:
    """Угол между двумя положениями камеры, градусы."""
    turn = first.basis() @ second.basis().T
    return math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(turn) - 1.0) / 2.0))))


def _drag(orbit, yaw, pitch, steps):
    worst = 0.0
    for _step in range(steps):
        before = orbit.copy()
        orbit.rotate(yaw, pitch)
        worst = max(worst, _angle(before, orbit))
    return worst


@pytest.mark.parametrize("yaw, pitch", [(0.8, 0.0), (0.0, 0.8), (0.0, -0.8), (0.8, 0.8),
                                        (0.3, -0.8)])
def test_every_step_is_as_small_as_the_mouse_move(yaw, pitch):
    step = math.hypot(yaw, pitch)
    for start in ("изометрия", "сверху", "снизу"):
        orbit = Orbit()
        if start == "сверху":
            orbit.set_view((0, 0, 1), (0, 1, 0))
        elif start == "снизу":
            orbit.set_view((0, 0, -1), (0, 1, 0))
        # Двадцать оборотов в одну сторону — ни шва, ни полюса.
        worst = _drag(orbit, yaw, pitch, int(20 * 360 / step))
        # Шаг по диагонали — два поворота подряд: его угол чуть меньше
        # гипотенузы. Проверяется главное — скачков больше шага нет.
        assert step * 0.999 <= worst <= step * 1.001, start


def test_full_turn_comes_back():
    for yaw, pitch in ((0.8, 0.0), (0.0, 0.8)):
        orbit = Orbit()
        start = orbit.copy()
        for _step in range(450):          # 450 × 0,8° = 360°
            orbit.rotate(yaw, pitch)
        assert _angle(start, orbit) == pytest.approx(0.0, abs=1e-6)


def test_top_view_is_drawn_like_a_drawing():
    orbit = Orbit()
    orbit.set_view((0, 0, 1), (0, 0, 1))          # верх по Z совпал со взглядом
    assert np.allclose(orbit.right, (1, 0, 0)) and np.allclose(orbit.up, (0, 1, 0))
    assert orbit.pitch == pytest.approx(90.0)


def test_angles_round_trip_and_axes_stay_square():
    orbit = Orbit()
    orbit.set_angles(45.0, 28.0)
    assert (orbit.yaw, orbit.pitch) == (pytest.approx(45.0), pytest.approx(28.0))
    rng = np.random.default_rng(3)
    for yaw, pitch, roll in rng.uniform(-5, 5, (20000, 3)):
        orbit.rotate(yaw, pitch)
        orbit.roll(roll)
    basis = orbit.basis()
    assert np.allclose(basis @ basis.T, np.eye(3), atol=1e-9)
    assert np.linalg.det(basis) == pytest.approx(1.0)
