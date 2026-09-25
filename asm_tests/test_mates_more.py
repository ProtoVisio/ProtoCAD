"""Угол, параллельность, перпендикулярность, касание — и перетаскивание мышью.

Положение считается от того места, где деталь стоит: поворот наименьший,
сдвиг — только вдоль того, что сопряжение задаёт. Поэтому деталь,
утащенная вдоль свободного направления, там и остаётся.
"""

import math

import numpy as np

from asm_helpers import face, part, place_of
from protocad import kernel
from protocad.assembly import AssemblyDocument
from protocad.assembly.solve import _spin


def _pose(axis, degrees, shift):
    matrix = np.eye(4)
    axis = np.asarray(axis, float) / np.linalg.norm(axis)
    matrix[:3, :3] = _spin(axis, math.radians(degrees))
    matrix[:3, 3] = shift
    return matrix


def _normal(occurrence, local):
    return occurrence.transform[:3, :3] @ np.asarray(local, float)


def _point(occurrence, local):
    return occurrence.transform[:3, :3] @ np.asarray(local, float) + place_of(occurrence)


def _angle(a, b) -> float:
    return math.degrees(math.acos(max(-1.0, min(1.0, float(np.dot(a, b))))))


def _bricks(guest_pose):
    document = AssemblyDocument("Бруски")
    brick = part("Брусок", kernel.box(20, 20, 20))
    host = document.add(brick)
    guest = document.add(brick, transform=guest_pose)
    return document, host, guest


def test_parallel_turns_least_and_keeps_the_face_in_place():
    document, host, guest = _bricks(_pose((1, 1, 0), 25.0, (60, 10, 5)))
    top_centre_before = _point(guest, (10, 10, 20))
    mate = document.mate("parallel", face(document, host, "plane", normal=(0, 0, 1)),
                         face(document, guest, "plane", near=(10, 10, 20)))
    assert document.solve() == []
    assert np.allclose(_normal(guest, (0, 0, 1)), (0, 0, 1), atol=1e-7)
    # Поворот шёл вокруг самой грани: её середина осталась где была.
    assert np.allclose(_point(guest, (10, 10, 20)), top_centre_before, atol=1e-6)
    mate.flip = True
    assert document.solve() == []
    assert np.allclose(_normal(guest, (0, 0, 1)), (0, 0, -1), atol=1e-7)


def test_perpendicular_and_angle_between_faces():
    document, host, guest = _bricks(_pose((1, 0, 0), 10.0, (60, 0, 0)))
    top = face(document, host, "plane", normal=(0, 0, 1))
    guest_top = face(document, guest, "plane", near=(10, 10, 20))
    mate = document.mate("perpendicular", top, guest_top)
    assert document.solve() == []
    assert abs(float(np.dot(_normal(guest, (0, 0, 1)), (0, 0, 1)))) < 1e-7
    mate.kind, mate.angle_deg = "angle", 30.0
    assert document.solve() == []
    assert math.isclose(_angle(_normal(guest, (0, 0, 1)), (0, 0, 1)), 30.0, abs_tol=1e-5)
    mate.flip = True
    assert document.solve() == []
    assert math.isclose(_angle(_normal(guest, (0, 0, 1)), (0, 0, 1)), 150.0, abs_tol=1e-5)


def test_axis_parallel_to_plane_and_rod_lying_on_plate():
    """Смешанная пара: ось ПАРАЛЛЕЛЬНА плоскости, когда лежит вдоль неё; касание
    кладёт стержень на плиту — ось на радиус выше верха."""
    document = AssemblyDocument("Стержень на плите")
    plate = document.add(part("Плита", kernel.box(100, 60, 10)))
    rod = document.add(part("Стержень", kernel.cylinder(4, 50)),
                       transform=_pose((1, 0, 0), 30.0, (20, 20, 40)))
    top = face(document, plate, "plane", normal=(0, 0, 1))
    side = face(document, rod, "cylinder", radius=4.0)
    mate = document.mate("parallel", top, side)
    assert document.solve() == []
    assert abs(float(np.dot(_normal(rod, (0, 0, 1)), (0, 0, 1)))) < 1e-7

    mate.kind = "tangent"
    assert document.solve() == []
    axis_point = _point(rod, (0, 0, 0))
    assert abs(float(np.dot(_normal(rod, (0, 0, 1)), (0, 0, 1)))) < 1e-7
    assert math.isclose(axis_point[2], 10.0 + 4.0, abs_tol=1e-6)
    mate.flip = True                  # по другую сторону плоскости
    assert document.solve() == []
    assert math.isclose(_point(rod, (0, 0, 0))[2], 10.0 - 4.0, abs_tol=1e-6)


def test_two_rods_touch_outside_and_inside():
    document = AssemblyDocument("Два стержня")
    big = document.add(part("Большой", kernel.cylinder(10, 40)))
    small = document.add(part("Малый", kernel.cylinder(3, 40)),
                         transform=_pose((0, 1, 0), 20.0, (40, 5, 0)))
    mate = document.mate("tangent", face(document, big, "cylinder", radius=10.0),
                         face(document, small, "cylinder", radius=3.0))
    assert document.solve() == []

    def axis_gap():
        axis = _normal(small, (0, 0, 1))
        assert np.linalg.norm(np.cross(axis, (0, 0, 1))) < 1e-7
        point = _point(small, (0, 0, 0))
        return float(np.hypot(point[0], point[1]))

    assert math.isclose(axis_gap(), 13.0, abs_tol=1e-6)
    mate.flip = True
    assert document.solve() == []
    assert math.isclose(axis_gap(), 7.0, abs_tol=1e-6)


def test_tangent_of_two_planes_is_refused():
    document, host, guest = _bricks(_pose((0, 0, 1), 0.0, (40, 0, 0)))
    document.mate("tangent", face(document, host, "plane", normal=(0, 0, 1)),
                  face(document, guest, "plane", normal=(0, 0, -1)))
    notes = document.solve()
    assert [note.code for note in notes] == ["ASSEMBLY_BAD_REFERENCE"]
    assert "совпадением" in notes[0].message


def test_parallel_and_perpendicular_together_are_overconstrained():
    document, host, guest = _bricks(_pose((1, 0, 0), 10.0, (60, 0, 0)))
    top = face(document, host, "plane", normal=(0, 0, 1))
    guest_top = face(document, guest, "plane", near=(10, 10, 20))
    document.mate("parallel", top, guest_top)
    extra = document.mate("perpendicular", top, guest_top)
    notes = document.solve()
    assert [note.code for note in notes] == ["ASSEMBLY_OVERCONSTRAINED"]
    assert not extra.ok and "90" in notes[0].message


def test_angle_survives_file(tmp_path):
    document, host, guest = _bricks(_pose((1, 0, 0), 10.0, (60, 0, 0)))
    document.mate("angle", face(document, host, "plane", normal=(0, 0, 1)),
                  face(document, guest, "plane", near=(10, 10, 20)), angle_deg=40.0)
    assert document.solve() == []
    again = AssemblyDocument.open(document.save(tmp_path / "угол.prcadAsm"))
    assert again.mates[0].kind == "angle" and again.mates[0].angle_deg == 40.0
    assert "40°" in again.describe(again.mates[0])
    assert again.solve() == []


# --- перетаскивание ------------------------------------------------------------


def test_drag_slides_on_plane_and_cannot_lift():
    document, host, guest = _bricks(np.eye(4))
    document.mate("coincident", face(document, host, "plane", normal=(0, 0, 1)),
                  face(document, guest, "plane", normal=(0, 0, -1)))
    assert document.solve() == []
    grab = (10.0, 10.0, 20.0)                   # середина верха гостя
    start = _point(guest, grab)
    assert document.solve(drag=(guest, grab, start + (15.0, -5.0, 30.0))) == []
    # По плоскости ушла за мышью, вверх — нет: её держит совпадение.
    assert np.allclose(place_of(guest), (15.0, -5.0, 20.0), atol=1e-6)
    # Обычный пересчёт после перетаскивания её не возвращает.
    assert document.solve() == []
    assert np.allclose(place_of(guest), (15.0, -5.0, 20.0), atol=1e-6)


def test_drag_along_and_around_axis():
    document = AssemblyDocument("Ось")
    plate = document.add(part("Плита", kernel.cut(kernel.box(60, 40, 10),
                                                  kernel.cylinder(5, 10, origin=(20, 15, 0)))))
    # Палец с рычагом наверху: за конец рычага его и тянут.
    pin = document.add(part("Палец", kernel.fuse(
        kernel.cylinder(5, 40), kernel.box(20, 2, 2, origin=(0, -1, 38)))))
    document.mate("concentric", face(document, plate, "cylinder", radius=5.0),
                  face(document, pin, "cylinder", radius=5.0))
    assert document.solve() == []
    base = place_of(pin).copy()
    # Тянем за конец рычага вверх и вбок: вверх — вдоль оси, вбок —
    # поворот вокруг оси; сойти с оси палец не может.
    grab = (20.0, 0.0, 39.0)
    here = _point(pin, grab)
    target = here + (0.0, 20.0, 12.0)
    assert document.solve(drag=(pin, grab, target)) == []
    axis_point = _point(pin, (0, 0, 0))
    assert np.allclose(axis_point[:2], (20, 15), atol=1e-6)
    assert math.isclose(axis_point[2], base[2] + 12.0, abs_tol=1e-3)
    lever = _point(pin, grab) - _point(pin, (0, 0, 39.0))
    assert _angle(lever / np.linalg.norm(lever),
                  (target - _point(pin, (0, 0, 39.0))) / np.linalg.norm(
                      target - _point(pin, (0, 0, 39.0)))) < 1.0


def test_dragged_part_carries_what_is_mated_to_it():
    document = AssemblyDocument("Стопка")
    brick = part("Брусок", kernel.box(20, 20, 20))
    first = document.add(brick)
    second = document.add(brick)
    third = document.add(brick)
    for lower, upper in ((first, second), (second, third)):
        document.mate("coincident", face(document, lower, "plane", normal=(0, 0, 1)),
                      face(document, upper, "plane", normal=(0, 0, -1)))
    # Третий держится ещё и за бок второго: по X он теперь привязан к нему.
    document.mate("coincident", face(document, second, "plane", normal=(1, 0, 0)),
                  face(document, third, "plane", normal=(-1, 0, 0)))
    assert document.solve() == []
    before = place_of(third).copy()
    assert document.solve(drag=(second, (10, 10, 20), _point(second, (10, 10, 20))
                                + (0.0, 7.0, 0.0))) == []
    assert np.allclose(place_of(second), (0, 7, 20), atol=1e-6)
    # Третий прижат к боку второго: по Y он свободен и остаётся, по X и Z —
    # едет за вторым.
    assert np.allclose(place_of(third)[[0, 2]], before[[0, 2]], atol=1e-6)


def test_free_part_drags_by_translation_and_fixed_does_not_move():
    document, host, guest = _bricks(_pose((0, 0, 1), 0.0, (50, 0, 0)))
    grab = (0.0, 0.0, 0.0)
    document.solve(drag=(guest, grab, (70.0, 5.0, 3.0)))
    assert np.allclose(place_of(guest), (70, 5, 3), atol=1e-9)
    assert np.allclose(guest.transform[:3, :3], np.eye(3))    # не повернулась
    document.solve(drag=(host, grab, (5.0, 5.0, 5.0)))
    assert np.allclose(place_of(host), (0, 0, 0))
