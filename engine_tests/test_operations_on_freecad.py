"""Все виды операций детали — на настоящем FreeCAD, с проверкой объёма.

Пропускается, если FreeCAD не найден. Объёмы посчитаны вручную: брусок
60 × 40 × 20 = 48 000 мм³ и т. д.
"""

import math

import pytest

from protocad.engine.freecad_backend import find_freecad

pytestmark = pytest.mark.skipif(find_freecad() is None,
                                reason="FreeCAD не найден (python -m protocad.doctor)")


def _rect(name, width, height, x=0.0, y=0.0, plane=None):
    from protocad.sketch import Sketch

    sketch = Sketch(name, plane=plane) if plane is not None else Sketch(name)
    a, b, c, d = sketch.polyline([(x, y), (x + width, y), (x + width, y + height),
                                  (x, y + height)])
    sketch.horizontal(a)
    sketch.vertical(b)
    sketch.horizontal(c)
    sketch.vertical(d)
    sketch.anchor(a.points[0])
    sketch.solve()
    return sketch


def _circles(name, places, radius, plane=None):
    from protocad.sketch import Sketch

    sketch = Sketch(name, plane=plane) if plane is not None else Sketch(name)
    for x, y in places:
        sketch.circle(sketch.point(x, y), radius)
    sketch.anchor(sketch.points[0])
    sketch.solve()
    return sketch


@pytest.fixture(scope="module")
def backend():
    from protocad import engine

    made = engine.make_backend()
    yield made
    made.shutdown()


def _block(document):
    from model_view import ModelView
    from protocad.document import Operation

    document.add(Operation("pad", "Брусок", sketch=_rect("Основа", 60, 40), length=20.0))
    document.rebuild()
    return ModelView(document.result)


def _top(model):
    from commands import top_face

    return model.plane_of(top_face(model))


def _pad(d):
    _block(d)


def _thin(d):
    from protocad.document import Operation

    d.add(Operation("pad", "Тонко", sketch=_rect("Основа", 60, 40), length=10, thin="one",
                    thickness=2.0))


def _through(d):
    from protocad.document import EndCondition, Operation

    plane = _top(_block(d))
    d.add(Operation("pocket", "Вырез", sketch=_circles("Круг", [(30, 20)], 8, plane),
                    length=1.0, end=EndCondition.THROUGH_ALL))


def _revolve(d):
    from protocad.document import Operation
    from protocad.sketch import plane as planes

    d.add(Operation("revolve", "Вращение", sketch=_rect("Профиль", 10, 30, 5, 0,
                                                        planes.STANDARD["XZ"]),
                    angle=360.0, axis=(0.0, 0.0, 1.0)))


def _holes(d):
    from protocad.document import Operation

    plane = _top(_block(d))
    d.add(Operation("hole", "Отверстия", sketch=_circles("Места", [(10, 10), (50, 30)], 3,
                                                         plane), diameter=6.0, through=True))


def _shell(d):
    from commands import top_face
    from protocad.document import Operation

    model = _block(d)
    d.add(Operation("shell", "Оболочка", faces=[top_face(model)], thickness=2.0))


def _linear(d):
    from protocad.document import Operation

    plane = _top(_block(d))
    d.add(Operation("pocket", "Лунка", sketch=_circles("Лунка", [(8, 8)], 3, plane), length=4.0))
    d.add(Operation("linear", "Ряд", sources=["Лунка"], count=4, spacing=12.0,
                    direction=(1.0, 0.0, 0.0), count2=2, spacing2=20.0,
                    pattern_direction2=(0.0, 1.0, 0.0)))


def _polar(d):
    from model_view import ModelView
    from protocad.document import EndCondition, Operation

    d.add(Operation("pad", "Диск", sketch=_circles("Диск", [(0, 0)], 30), length=5))
    d.rebuild()
    plane = _top(ModelView(d.result))
    d.add(Operation("pocket", "Лунка", sketch=_circles("Лунка", [(20, 0)], 3, plane),
                    length=5.0, end=EndCondition.THROUGH_ALL))
    d.add(Operation("polar", "Круг", sources=["Лунка"], count=6, angle=360.0,
                    axis=(0.0, 0.0, 1.0)))


def _mirror(d):
    from protocad.document import Operation

    plane = _top(_block(d))
    d.add(Operation("pocket", "Лунка", sketch=_circles("Лунка", [(10, 20)], 4, plane),
                    length=5.0))
    d.add(Operation("mirror", "Зеркало", sources=["Лунка"], plane_normal=(1.0, 0.0, 0.0),
                    plane_origin=(30.0, 0.0, 0.0)))


def _dress(kind, selector, **values):
    def build(d):
        from commands import pick_edges
        from protocad.document import Operation

        model = _block(d)
        d.add(Operation(kind, kind, edges=pick_edges(model, selector), **values))
    return build


CASES = [
    ("выдавливание", _pad, 48000.0),
    ("тонкостенное", _thin, (60 * 40 - 56 * 36) * 10.0),
    ("вырез насквозь", _through, 48000.0 - math.pi * 64 * 20),
    ("вращение", _revolve, math.pi * (15 ** 2 - 5 ** 2) * 30),
    ("отверстия", _holes, 48000.0 - 2 * math.pi * 9 * 20),
    ("оболочка", _shell, 48000.0 - 56 * 36 * 18),
    ("линейный массив", _linear, 48000.0 - 8 * math.pi * 9 * 4),
    ("круговой массив", _polar, math.pi * 900 * 5 - 6 * math.pi * 9 * 5),
    ("зеркало", _mirror, 48000.0 - 2 * math.pi * 16 * 5),
    ("скругление", _dress("fillet", "vertical", size=3.0), 48000.0 - 4 * (9 - math.pi * 9 / 4) * 20),
    ("фаска", _dress("chamfer", "vertical", size=2.0), 48000.0 - 4 * 2.0 * 20),
    ("фаска с углом", _dress("chamfer", "vertical", size=2.0, chamfer_type="angle", angle=45.0),
     48000.0 - 4 * 2.0 * 20),
]


@pytest.mark.parametrize("title, build, volume", CASES, ids=[case[0] for case in CASES])
def test_operation(backend, title, build, volume):
    from protocad.document import Document

    document = Document("Проба", designation="ПРОБА", backend=backend)
    try:
        build(document)
        report = document.rebuild()
        failed = [f"{item.name}: {item.message}" for item in document.operations if not item.ok]
        assert report.ok and not failed, failed
        assert document.volume == pytest.approx(volume, rel=1e-4)
    finally:
        document.forget()
