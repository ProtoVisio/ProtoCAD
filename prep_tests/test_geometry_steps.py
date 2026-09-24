"""Проверка, лечение, упрощение."""

import math

from prep_helpers import study_of
from protocad import kernel
from protocad.prep import check, defeature, find_fillets, find_holes, heal
from protocad.prep.model import faces_of_shape
from protocad.prep.select import select


def codes(report):
    return {item.code for item in report.findings}


def test_check_finds_surface_overlap_and_contact():
    loose = kernel.compound(faces_of_shape(kernel.box(5, 5, 5)))
    study = study_of(("кожа", loose), ("A", kernel.box(10, 10, 10, origin=(20, 0, 0))),
                     ("B", kernel.box(10, 10, 10, origin=(30, 0, 0))),
                     ("C", kernel.box(10, 10, 10, origin=(35, 0, 0))))
    report = check(study)
    assert not report.ok
    assert {"NOT_SOLID", "OVERLAP", "CONTACT"} <= codes(report)
    assert study.log[-1] is report


def test_check_open_shell_reports_free_edges():
    faces = faces_of_shape(kernel.box(5, 5, 5))[:-1]      # без одной грани
    study = study_of(("ящик", kernel.compound(faces)))
    report = heal(study)
    assert "STILL_OPEN" in codes(report)
    assert not study.bodies[0].is_solid


def test_heal_sews_surfaces_into_solid():
    study = study_of(("кожа", kernel.compound(faces_of_shape(kernel.box(10, 20, 30)))))
    heal(study)
    assert study.bodies[0].is_solid and abs(study.bodies[0].volume - 6000) < 1e-6
    assert check(study).ok


def test_unify_merges_inside_group_but_keeps_group_border():
    two = kernel.fuse(kernel.box(10, 10, 10), kernel.box(10, 10, 10, origin=(10, 0, 0)))
    study = study_of(("сплав", two))
    study.add_group("верх", faces=select(study, {"type": "plane", "normal": [0, 0, 1]}))
    side = select(study, {"type": "plane", "normal": [0, -1, 0]})
    study.add_group("бок", faces=side[:1])
    heal(study)
    assert len(study.groups["верх"].faces) == 1          # половинки слились
    assert len(select(study, {"type": "plane", "normal": [0, -1, 0]})) == 2  # граница цела


def test_defeature_removes_small_features_exactly(plate):
    study = study_of(("пластина", plate))
    study.add_group("низ", faces=select(study, {"type": "plane", "normal": [0, 0, -1]}))
    assert [round(item.size, 3) for item in find_holes(study, 8.0)] == [6.0, 5.0]
    assert len(find_fillets(study, 5.0)) == 4
    report = defeature(study, holes=8.0, fillets=5.0)
    assert report.ok
    expected = 100 * 60 * 10 - math.pi * 10 ** 2 * 10
    assert abs(study.bodies[0].volume - expected) < 1e-3
    assert len(study.groups["низ"].faces) == 1
    assert [round(item.size, 3) for item in find_holes(study, 100.0)] == [20.0]
