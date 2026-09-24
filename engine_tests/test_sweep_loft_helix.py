"""«По траектории», «По сечениям», «Спираль» и их вырезы.

Геометрию строит FreeCAD (`PartDesign::Pipe`, `Loft`, `Helix`); здесь —
что до него доходит то, что нарисовано, и что объём сходится с посчитанным
вручную. Проверки учёта эскизов и записи в файл движка не требуют.
"""

import math

import pytest

from protocad.engine.freecad_backend import find_freecad

needs_freecad = pytest.mark.skipif(
    find_freecad() is None, reason="FreeCAD не найден (python -m protocad.doctor)")


def _plane(name, offset=0.0):
    from protocad.sketch import plane as planes

    base = planes.STANDARD[name]
    return base.offset(offset) if offset else base


def _rect(name, x, y, width, height, plane):
    from protocad.sketch import Sketch

    sketch = Sketch(name, plane=plane)
    a, b, c, d = sketch.polyline([(x, y), (x + width, y), (x + width, y + height),
                                  (x, y + height)])
    sketch.horizontal(a)
    sketch.vertical(b)
    sketch.horizontal(c)
    sketch.vertical(d)
    sketch.anchor(a.points[0])
    sketch.solve()
    return sketch


def _circle(name, x, y, radius, plane):
    from protocad.sketch import Sketch

    sketch = Sketch(name, plane=plane)
    sketch.circle(sketch.point(x, y), radius)
    sketch.anchor(sketch.points[0])
    sketch.solve()
    return sketch


def _bend(name="Траектория"):
    """Отрезок 30, четверть окружности R10 и отрезок 30 — в плоскости XZ.

    Длина по оси — 60 + 5π; круг R3, протянутый по ней, даёт π·9·(60 + 5π).
    """
    from protocad.sketch import Sketch

    sketch = Sketch(name, plane=_plane("XZ"))
    p0 = sketch.point(0.0, 0.0)
    p1 = sketch.point(0.0, 30.0)
    p2 = sketch.point(10.0, 40.0)
    p3 = sketch.point(40.0, 40.0)
    centre = sketch.point(10.0, 30.0)
    sketch.line(p0, p1)
    # Дуга идёт против часовой от начала к концу: от (10, 40) к (0, 30).
    sketch.arc(centre, p2, p1)
    sketch.line(p2, p3)
    for point in (p0, p1, p2, p3, centre):
        sketch.anchor(point)
    sketch.solve()
    return sketch


def _block(document):
    from protocad.document import Operation

    document.add(Operation("pad", "Брусок", sketch=_rect("Основа", 0, 0, 60, 40,
                                                          _plane("XY")),
                           length=20.0))


def _sweep(d):
    from protocad.document import Operation

    d.add(Operation("sweep", "Труба", sketch=_circle("Сечение", 0, 0, 3, _plane("XY")),
                    path=_bend()))


def _sweep_cut(d):
    from protocad.document import Operation

    _block(d)
    # Канавка по верху бруска: круг R2 с центром на верхней грани, протянутый
    # вдоль X с запасом по обе стороны. Снимается половина круга на 60 мм.
    from protocad.sketch import Sketch

    path = Sketch("Путь", plane=_plane("XY", 20.0))
    path.line(path.point(-5.0, 20.0), path.point(65.0, 20.0))
    path.anchor(path.points[0])
    path.anchor(path.points[1])
    path.solve()
    d.add(Operation("sweep_cut", "Канавка",
                    sketch=_circle("Круг", 20, 20, 2, _plane("YZ", -5.0)), path=path))


def _loft(d):
    from protocad.document import Operation

    d.add(Operation("loft", "Пирамида",
                    sketch=_rect("Низ", -10, -10, 20, 20, _plane("XY")),
                    sections=[_rect("Верх", -5, -5, 10, 10, _plane("XY", 30.0))]))


def _loft_ruled(d):
    from protocad.document import Operation

    d.add(Operation("loft", "Пирамида",
                    sketch=_rect("Низ", -10, -10, 20, 20, _plane("XY")),
                    sections=[_rect("Середина", -5, -5, 10, 10, _plane("XY", 30.0)),
                              _rect("Верх", -5, -5, 10, 10, _plane("XY", 40.0))],
                    ruled=True))


def _loft_cut(d):
    from protocad.document import Operation

    _block(d)
    d.add(Operation("loft_cut", "Воронка",
                    sketch=_circle("Устье", 30, 20, 8, _plane("XY", 20.0)),
                    sections=[_circle("Дно", 30, 20, 4, _plane("XY", 10.0))]))


def _helix(d):
    from protocad.document import Operation

    d.add(Operation("helix", "Пружина", sketch=_circle("Виток", 5, 0, 1, _plane("XZ")),
                    axis=(0.0, 0.0, 1.0), pitch=5.0, height=20.0))


def _helix_cut(d):
    from protocad.document import Operation
    from protocad.sketch import Sketch

    d.add(Operation("pad", "Вал", sketch=_circle("Вал", 0, 0, 5, _plane("XY")),
                    length=20.0))
    thread = Sketch("Резьба", plane=_plane("XZ"))
    thread.polyline([(5.5, 2.0), (4.2, 3.0), (5.5, 4.0)])
    for point in thread.points:
        thread.anchor(point)
    thread.solve()
    d.add(Operation("helix_cut", "Нарезка", sketch=thread, axis=(0.0, 0.0, 1.0),
                    helix_mode="pitch-height", pitch=3.0, height=15.0))


def _thread_removed() -> float:
    """Снятое резьбой: часть треугольника внутри вала (r < 5), провёрнутая
    на пять витков. Сдвиг вдоль оси объёма не меняет, поэтому считается как
    тело вращения: 5 · ∫ 2πr·h(r) dr, где h(r) = 2(r − 4.2)/1.3."""
    def primitive(r):
        return r ** 3 / 3.0 - 2.1 * r ** 2
    return 5 * (4 * math.pi / 1.3) * (primitive(5.0) - primitive(4.2))


CASES = [
    ("по траектории", _sweep, math.pi * 9 * (60 + 5 * math.pi)),
    ("вырез по траектории", _sweep_cut, 48000.0 - math.pi * 4 / 2 * 60),
    ("по сечениям", _loft, 30 / 3 * (400 + 100 + 200)),
    ("по сечениям, три, прямые", _loft_ruled, 7000.0 + 1000.0),
    ("вырез по сечениям", _loft_cut, 48000.0 - math.pi * 10 / 3 * (64 + 32 + 16)),
    ("спираль", _helix, math.pi * 1 * 2 * math.pi * 5 * 4),
    ("вырез по спирали", _helix_cut, math.pi * 25 * 20 - _thread_removed()),
]


@pytest.fixture(scope="module")
def backend():
    from protocad import engine

    made = engine.make_backend()
    yield made
    made.shutdown()


@needs_freecad
@pytest.mark.parametrize("title, build, volume", CASES, ids=[case[0] for case in CASES])
def test_operation(backend, title, build, volume):
    from protocad.document import Document

    document = Document("Проба", designation="ПРОБА", backend=backend)
    try:
        build(document)
        report = document.rebuild()
        failed = [f"{item.name}: {item.message}" for item in document.operations
                  if not item.ok]
        assert report.ok and not failed, failed
        assert document.volume == pytest.approx(volume, rel=1e-4)
    finally:
        document.forget()


@needs_freecad
def test_edit_in_place_and_reopen(backend):
    """Правка шага спирали и траектории идёт НА МЕСТЕ, а записанное в файл
    строится после открытия в тот же объём."""
    from protocad.document import Document

    document = Document("Правка", backend=backend)
    try:
        _helix(document)
        _sweep(document)
        assert document.rebuild().ok
        spring = document.operations[0]
        first = spring.feature_id
        spring.pitch, spring.height = 5.0, 10.0          # два витка вместо четырёх
        assert document.rebuild().ok
        assert spring.feature_id == first
        tube = math.pi * 9 * (60 + 5 * math.pi)
        assert document.volume == pytest.approx(math.pi * 2 * math.pi * 5 * 2 + tube,
                                                rel=1e-4)

        again = Document.from_dict(document.to_dict(), backend=backend)
        try:
            assert again.rebuild().ok
            assert again.volume == pytest.approx(document.volume, rel=1e-6)
            assert again.operations[1].path is not None
            assert again.operations[1].path.name == "Траектория"
        finally:
            again.forget()
    finally:
        document.forget()


def _two_lines(d):
    """Две несвязанные линии в эскизе траектории — не протяжка по одной из них."""
    from protocad.document import Operation
    from protocad.sketch import Sketch

    path = Sketch("Две линии", plane=_plane("XZ"))
    path.line(path.point(0.0, 0.0), path.point(0.0, 30.0))
    path.line(path.point(10.0, 0.0), path.point(10.0, 30.0))
    for point in path.points:
        path.anchor(point)
    path.solve()
    d.add(Operation("sweep", "Труба", sketch=_circle("Круг", 0, 0, 2, _plane("XY")),
                    path=path))


def _crowded(d):
    """Шаг 1 мм при профиле ⌀4: FreeCAD строит это молча, с неверным объёмом."""
    from protocad.document import Operation

    d.add(Operation("helix", "Пружина", sketch=_circle("Виток", 5, 0, 2, _plane("XZ")),
                    axis=(0.0, 0.0, 1.0), pitch=1.0, height=10.0))


def _on_axis(d):
    from protocad.document import Operation

    d.add(Operation("helix", "Пружина", sketch=_circle("Виток", 0, 0, 1, _plane("XZ")),
                    axis=(0.0, 0.0, 1.0), pitch=5.0, height=20.0))


def _unequal_sections(d):
    from protocad.document import Operation
    from protocad.sketch import Sketch

    two = Sketch("Два круга", plane=_plane("XY", 20.0))
    two.circle(two.point(-5.0, 0.0), 2.0)
    two.circle(two.point(5.0, 0.0), 2.0)
    for point in two.points:
        two.anchor(point)
    two.solve()
    d.add(Operation("loft", "Переход", sketch=_circle("Один", 0, 0, 5, _plane("XY")),
                    sections=[two]))


def _cut_nothing(d):
    from protocad.document import Operation

    d.add(Operation("helix_cut", "Резьба", sketch=_circle("Виток", 5, 0, 1, _plane("XZ")),
                    axis=(0.0, 0.0, 1.0)))


REFUSALS = [
    ("две линии в траектории", _two_lines, "нужна одна"),
    ("витки налезают", _crowded, "налезли бы"),
    ("профиль на оси", _on_axis, "пересекает сам себя"),
    ("разное число контуров", _unequal_sections, "столько же контуров"),
    ("вырез без тела", _cut_nothing, "нечего резать"),
]


@needs_freecad
@pytest.mark.parametrize("title, build, words", REFUSALS,
                         ids=[case[0] for case in REFUSALS])
def test_refusal_names_the_reason(backend, title, build, words):
    """Отказ — с причиной по-русски, а не «не пересчиталась: Invalid»."""
    from protocad.document import Document

    document = Document("Отказ", backend=backend)
    try:
        build(document)
        report = document.rebuild()
        assert not report.ok
        assert words in report.message, report.message
    finally:
        document.forget()


# --- без движка -------------------------------------------------------------


def _local_document():
    from protocad import engine
    from protocad.document import Document

    return Document("Учёт", backend=engine.make_backend("local"))


def test_path_and_sections_are_not_free():
    """Траектория и сечения израсходованы операцией: свободными они не
    числятся, иначе следующая команда взяла бы траекторию профилем."""
    from protocad.document import Operation

    document = _local_document()
    profile = _circle("Профиль", 0, 0, 2, _plane("XY"))
    path = _bend()
    spare = _circle("Лишний", 0, 0, 1, _plane("XY", 50.0))
    top = _rect("Верх", -5, -5, 10, 10, _plane("XY", 30.0))
    document.add_sketch(spare)
    document.add(Operation("sweep", "Труба", sketch=profile, path=path))
    document.add(Operation("loft", "Переход",
                           sketch=_rect("Низ", -10, -10, 20, 20, _plane("XY")),
                           sections=[top]))
    assert path in document.sketches and top in document.sketches
    assert document.free_sketches() == [spare]

    document.remove_sketch(path)
    assert [item.name for item in document.operations] == ["Переход"]
    document.remove_sketch(top)
    assert document.operations == []


def test_round_trip_keeps_path_sections_and_parameters():
    from protocad.document import Document, Operation

    document = _local_document()
    document.add(Operation("sweep_cut", "Канал",
                           sketch=_circle("Профиль", 0, 0, 2, _plane("XY")),
                           path=_bend(), sweep_mode="frenet", transition="round"))
    document.add(Operation("loft", "Переход",
                           sketch=_rect("Низ", -10, -10, 20, 20, _plane("XY")),
                           sections=[_rect("Середина", -5, -5, 10, 10, _plane("XY", 30.0)),
                                     _rect("Верх", -2, -2, 4, 4, _plane("XY", 40.0))],
                           ruled=True, closed=False))
    document.add(Operation("helix_cut", "Резьба",
                           sketch=_circle("Виток", 5, 0, 1, _plane("XZ")),
                           axis=(0.0, 0.0, 1.0), axis_origin=(1.0, 2.0, 0.0),
                           helix_mode="pitch-turns", pitch=2.5, turns=7.0,
                           taper=3.0, left_handed=True, reversed=True))

    again = Document.from_dict(document.to_dict(), backend=document.backend)
    sweep, loft, helix = again.operations
    assert sweep.kind == "sweep_cut" and sweep.subtract
    assert sweep.path.name == "Траектория"
    assert (sweep.sweep_mode, sweep.transition) == ("frenet", "round")
    assert [item.name for item in loft.sections] == ["Середина", "Верх"]
    assert loft.ruled and not loft.closed
    assert helix.kind == "helix_cut" and helix.subtract
    assert (helix.helix_mode, helix.pitch, helix.turns) == ("pitch-turns", 2.5, 7.0)
    assert helix.taper == 3.0 and helix.left_handed and helix.reversed
    assert tuple(helix.axis_origin) == (1.0, 2.0, 0.0)
    # Эскизы не размножились: у каждого одна запись.
    assert len(again.sketches) == len(document.sketches) == 6
    assert again.free_sketches() == []


def test_prototype_refuses_loudly():
    """Замороженный прототип не строит протяжку молча чем-то похожим."""
    from protocad.document import Operation

    document = _local_document()
    document.add(Operation("sweep", "Труба",
                           sketch=_circle("Профиль", 0, 0, 2, _plane("XY")),
                           path=_bend()))
    report = document.rebuild()
    assert not report.ok
    assert "FreeCAD" in report.message
