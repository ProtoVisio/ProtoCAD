"""Панели «По траектории», «По сечениям», «Спираль» — без окна и без движка.

Проверяется то, что делает сеанс команды: куда попадают эскизы, как
снимается повторным щелчком лишнее сечение, какие поля видны и что
получается операцией — и что правка открывает ту же панель заполненной.
"""

import pytest

from test_sweep_loft_helix import _bend, _circle, _plane, _rect


def _session(descriptor, preselection=()):
    from command_session import CommandSession
    from descriptors import BuildContext

    from protocad import engine
    from protocad.document import Document

    document = Document("Панель", backend=engine.make_backend("local"))
    return CommandSession(descriptor, BuildContext(document=document),
                          preselection=list(preselection))


def test_sweep_takes_profile_then_path():
    import descriptors as D

    profile = _circle("Круг", 0, 0, 3, _plane("XY"))
    path = _bend()
    session = _session(D.SWEEP, [D.sketch_pick(profile)])
    # Профиль взят из предвыбора, и поле сразу переходит к траектории.
    assert session.active_box_id == "path"
    assert not session.ready
    session.click(D.sketch_pick(path))
    assert session.ready
    operation = session.commit()
    assert operation.kind == "sweep" and not operation.subtract
    assert operation.sketch is profile and operation.path is path
    assert (operation.sweep_mode, operation.transition) == ("standard", "transformed")


def test_sweep_refuses_same_sketch_twice():
    import descriptors as D

    profile = _circle("Круг", 0, 0, 3, _plane("XY"))
    session = _session(D.SWEEP_CUT, [D.sketch_pick(profile)])
    session.click(D.sketch_pick(profile))
    check = session.validate()
    assert check.blocking and "один и тот же" in check.message


def test_loft_sections_keep_order_and_toggle():
    import descriptors as D

    low = _rect("Низ", -10, -10, 20, 20, _plane("XY"))
    mid = _rect("Середина", -5, -5, 10, 10, _plane("XY", 30.0))
    top = _circle("Верх", 0, 0, 3, _plane("XY", 40.0))
    session = _session(D.LOFT)
    # Сечения указывают по порядку, начиная с первого: предвыбора нет —
    # последний нарисованный эскиз обычно верхний, и начинать с него значило
    # бы перекрутить тело.
    assert session.active_box_id == "profile"
    for sketch in (low, mid, top):
        session.click(D.sketch_pick(sketch))
    assert [pick.label for pick in session.box("sections").items] == \
        ["Середина", "Верх"]
    session.click(D.sketch_pick(mid))           # повторный щелчок снимает
    assert [pick.label for pick in session.box("sections").items] == ["Верх"]
    session.click(D.sketch_pick(mid))
    operation = session.commit()
    assert operation.sketch is low
    assert operation.sections == [top, mid]


def test_loft_refuses_first_section_again():
    import descriptors as D

    low = _rect("Низ", -10, -10, 20, 20, _plane("XY"))
    session = _session(D.LOFT)
    session.click(D.sketch_pick(low))
    session.click(D.sketch_pick(low))
    check = session.validate()
    assert check.blocking and "ещё раз" in check.message


@pytest.mark.parametrize("mode, shown, hidden", [
    ("Шаг и высота", {"pitch", "height"}, {"turns"}),
    ("Шаг и витки", {"pitch", "turns"}, {"height"}),
    ("Высота и витки", {"height", "turns"}, {"pitch"}),
])
def test_helix_shows_the_two_given_values(mode, shown, hidden):
    import descriptors as D

    session = _session(D.HELIX)
    session.set_value("helix_mode", mode)
    keys = {item.key for item in session.visible_parameters()}
    assert shown <= keys and not (hidden & keys)


def test_helix_builds_and_edit_reopens_filled():
    import descriptors as D

    coil = _circle("Виток", 5, 0, 1, _plane("XZ"))
    session = _session(D.HELIX_CUT, [D.sketch_pick(coil)])
    for key, value in (("axis", "X"), ("helix_mode", "Высота и витки"),
                       ("height", 12.0), ("turns", 3.0), ("taper", 5.0),
                       ("left_handed", True), ("reverse", True)):
        session.set_value(key, value)
    operation = session.commit()
    assert operation.kind == "helix_cut" and operation.subtract
    assert operation.axis == (1.0, 0.0, 0.0)
    assert (operation.helix_mode, operation.height, operation.turns) == \
        ("height-turns", 12.0, 3.0)
    assert operation.taper == 5.0 and operation.left_handed and operation.reversed

    again = _session(D.HELIX_CUT)
    D.HELIX_CUT.load(again, operation, again.context)
    rebuilt = again.commit()
    for field in ("sketch", "axis", "helix_mode", "height", "turns", "taper",
                  "left_handed", "reversed"):
        assert getattr(rebuilt, field) == getattr(operation, field), field


def test_edit_reopens_sweep_and_loft_filled():
    import descriptors as D

    profile = _circle("Круг", 0, 0, 3, _plane("XY"))
    path = _bend()
    session = _session(D.SWEEP, [D.sketch_pick(profile), D.sketch_pick(path)])
    session.set_value("orientation", "По винтовой (Френе)")
    session.set_value("corner", "Скругление")
    sweep = session.commit()
    again = _session(D.SWEEP)
    D.SWEEP.load(again, sweep, again.context)
    rebuilt = again.commit()
    assert rebuilt.path is path and rebuilt.sketch is profile
    assert (rebuilt.sweep_mode, rebuilt.transition) == ("frenet", "round")

    low = _rect("Низ", -10, -10, 20, 20, _plane("XY"))
    top = _circle("Верх", 0, 0, 3, _plane("XY", 40.0))
    session = _session(D.LOFT_CUT)
    session.click(D.sketch_pick(low))
    session.click(D.sketch_pick(top))
    session.set_value("ruled", True)
    loft = session.commit()
    again = _session(D.LOFT_CUT)
    D.LOFT_CUT.load(again, loft, again.context)
    rebuilt = again.commit()
    assert rebuilt.sketch is low and rebuilt.sections == [top] and rebuilt.ruled
