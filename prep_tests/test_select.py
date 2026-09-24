"""Отбор граней правилами и запись выбора мышью."""

import pytest

from prep_helpers import study_of
from protocad import kernel
from protocad.prep.select import (RuleError, describe, make_group,
                                  picked_rule, select)


def test_outermost_plane_by_normal():
    study = study_of(("п", kernel.fuse(kernel.box(10, 10, 10),
                                       kernel.box(10, 10, 5, origin=(10, 0, 0)))))
    tops = select(study, {"type": "plane", "normal": [0, 0, 1]})
    highest = select(study, {"type": "plane", "normal": [0, 0, 1], "at": "max"})
    assert len(tops) == 2 and len(highest) == 1
    assert abs(describe(study.face(highest[0]))["center"][2] - 10.0) < 1e-9


def test_hole_by_radius_and_concavity(plate):
    study = study_of(("пластина", plate))
    holes = select(study, {"type": "cylinder", "concave": True, "radius": [2.9, 3.1]})
    fillets = select(study, {"type": "cylinder", "concave": False, "radius": [3.9, 4.1]})
    assert len(holes) == 1 and len(fillets) == 4


def test_picked_faces_survive_as_points():
    study = study_of(("брусок", kernel.box(10, 20, 30)))
    rule = picked_rule(study, [2, 4])
    assert select(study, rule) == [2, 4]


def test_bad_rules_are_refused():
    study = study_of(("брусок", kernel.box(1, 1, 1)))
    with pytest.raises(RuleError):
        select(study, {"colour": "red"})
    with pytest.raises(RuleError):
        select(study, {"at": "max"})
    report = make_group(study, "пусто", {"type": "torus"})
    assert not report.ok and report.findings[0].code == "RULE_EMPTY"


def test_body_groups_by_pattern():
    study = study_of(("Болт 1", kernel.box(1, 1, 1)),
                     ("Болт 2", kernel.box(1, 1, 1, origin=(5, 0, 0))),
                     ("Плита", kernel.box(1, 1, 1, origin=(9, 0, 0))))
    report = make_group(study, "сталь", kind="bodies", bodies=["Болт*"])
    assert report.ok and study.groups["сталь"].bodies == {"Болт 1", "Болт 2"}
    assert not make_group(study, "чужие", kind="bodies", bodies=["Гайка*"]).ok
