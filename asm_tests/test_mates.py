"""Сопряжения: построение, доводка, отказы."""

import numpy as np

from asm_helpers import bolt, face, part, place_of
from protocad import kernel
from protocad.assembly import AssemblyDocument


def _two_bricks():
    document = AssemblyDocument("Бруски")
    brick = part("Брусок", kernel.box(20, 20, 20))
    host = document.add(brick)
    guest = document.add(brick, transform=np.eye(4))
    return document, host, guest


def test_brick_on_brick_and_gap():
    document, host, guest = _two_bricks()
    top = face(document, host, "plane", normal=(0, 0, 1))
    bottom = face(document, guest, "plane", normal=(0, 0, -1))
    mate = document.mate("coincident", top, bottom)
    assert document.solve() == []
    assert np.allclose(place_of(guest), (0, 0, 20))
    mate.kind, mate.value_mm = "distance", 5.0
    assert document.solve() == []
    assert np.allclose(place_of(guest), (0, 0, 25))


def test_flip_lays_guest_upside_down():
    document, host, guest = _two_bricks()
    top = face(document, host, "plane", normal=(0, 0, 1))
    guest_top = face(document, guest, "plane", normal=(0, 0, 1))
    document.mate("coincident", top, guest_top)
    assert document.solve() == []
    # Верх гостя прижат к верху хозяина: гость перевёрнут и лёг сверху.
    assert np.allclose(guest.transform[:3, :3] @ (0, 0, 1), (0, 0, -1))
    assert np.isclose(place_of(guest)[2] + 0.0, 40.0)


def test_chain_follows_edited_part():
    document = AssemblyDocument("Цепочка")
    brick = part("Брусок", kernel.box(20, 20, 20))
    first = document.add(brick)
    second = document.add(brick)
    third = document.add(brick)
    for lower, upper in ((first, second), (second, third)):
        document.mate("coincident", face(document, lower, "plane", normal=(0, 0, 1)),
                      face(document, upper, "plane", normal=(0, 0, -1)))
    assert document.solve() == []
    assert np.isclose(place_of(second)[2], 20) and np.isclose(place_of(third)[2], 40)
    brick.shape = kernel.box(20, 20, 10)       # деталь стала вдвое ниже
    document.forget_faces()
    assert document.solve() == []
    assert np.isclose(place_of(second)[2], 10) and np.isclose(place_of(third)[2], 20)


def test_bolt_goes_into_hole_with_two_mates():
    """Соосность и прилегание головки — обычная пара, раньше это был отказ."""
    document = AssemblyDocument("Болт в плите")
    plate = document.add(part("Плита", kernel.cut(kernel.box(60, 40, 10),
                                                  kernel.cylinder(5, 10, origin=(20, 15, 0)))))
    screw = document.add(part("Болт", bolt()),
                         transform=_turned((1, 0, 0), 0.7, (80, 60, -30)))
    hole = face(document, plate, "cylinder", radius=5.0)
    shaft = face(document, screw, "cylinder", radius=5.0)
    document.mate("concentric", hole, shaft)
    top = face(document, plate, "plane", normal=(0, 0, 1))
    under_head = face(document, screw, "plane", normal=(0, 0, -1), near=(6.5, 0, 30))
    document.mate("coincident", top, under_head)
    assert document.solve() == []
    axis = screw.transform[:3, :3] @ (0, 0, 1)
    tip = screw.transform[:3, :3] @ (0, 0, 0) + place_of(screw)
    assert np.allclose(np.abs(axis), (0, 0, 1), atol=1e-7)
    assert np.allclose(tip[:2], (20, 15), atol=1e-6)
    # Головка лежит на плите: низ головки на высоте верха плиты.
    head = screw.transform[:3, :3] @ (0, 0, 30) + place_of(screw)
    assert np.isclose(head[2], 10.0, atol=1e-6) and tip[2] < head[2]


def test_contradicting_mates_are_reported():
    document, host, guest = _two_bricks()
    top = face(document, host, "plane", normal=(0, 0, 1))
    bottom = face(document, guest, "plane", normal=(0, 0, -1))
    document.mate("coincident", top, bottom)
    distance = document.mate("distance", top, bottom, value_mm=7.0)
    notes = document.solve()
    assert [note.code for note in notes] == ["ASSEMBLY_OVERCONSTRAINED"]
    assert not distance.ok and document.mates[0].ok
    assert np.allclose(place_of(guest), (0, 0, 20))   # первое осталось точным


def test_group_not_tied_to_fixed_is_refused():
    document = AssemblyDocument("Висит")
    brick = part("Брусок", kernel.box(10, 10, 10))
    document.add(brick)                              # закреплено
    loose = document.add(brick, transform=_turned((0, 0, 1), 0.0, (50, 0, 0)))
    other = document.add(brick, transform=_turned((0, 0, 1), 0.0, (80, 0, 0)))
    document.mate("coincident", face(document, loose, "plane", normal=(0, 0, 1)),
                  face(document, other, "plane", normal=(0, 0, -1)))
    notes = document.solve()
    assert [note.code for note in notes] == ["ASSEMBLY_LOOP"]
    assert np.allclose(place_of(loose), (50, 0, 0))   # не двигали наугад


def test_lost_and_wrong_faces_are_refused():
    document, host, guest = _two_bricks()
    top = face(document, host, "plane", normal=(0, 0, 1))
    bottom = face(document, guest, "plane", normal=(0, 0, -1))
    mate = document.mate("concentric", top, bottom)
    assert [note.code for note in document.solve()] == ["ASSEMBLY_BAD_REFERENCE"]
    mate.kind = "coincident"
    mate.first.mark = "0,0,1,20"                      # верха нет — есть только «не та»
    mate.first.mark = "c:0,0,1,0,0,0,3"
    assert [note.code for note in document.solve()] == ["ASSEMBLY_BAD_REFERENCE"]


def _turned(axis, angle, shift):
    from protocad.assembly.solve import _spin

    matrix = np.eye(4)
    axis = np.asarray(axis, float) / np.linalg.norm(axis)
    matrix[:3, :3] = _spin(axis, angle)
    matrix[:3, 3] = shift
    return matrix
