"""Проверка, лечение, упрощение, разрез, область течения, склейка."""

import math

from conftest import study_of
from protocad import kernel
from protocad.prep import (check, cut_by_plane, defeature, enclosure,
                           find_fillets, find_holes, glue, heal,
                           split_by_plane)
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


def test_symmetry_cut_volume_and_group():
    plate = kernel.cut(kernel.box(100, 60, 10, origin=(-50, -30, 0)),
                       kernel.cylinder(5, 10))
    study = study_of(("пластина", plate))
    report = cut_by_plane(study, (0, 0, 0), (1, 0, 0))
    assert report.ok
    assert abs(study.bodies[0].volume - (60000 - math.pi * 250) / 2) < 1e-3
    assert len(study.groups["symmetry"].faces) == 2
    missed = cut_by_plane(study, (500, 0, 0), (1, 0, 0))
    assert not missed.ok and study.bodies            # отказ модель не тронул


def test_split_keeps_parts_glued_and_materials():
    study = study_of(("Балка", kernel.box(100, 20, 20)))
    study.add_group("сталь", kind="bodies", bodies=["Балка"])
    report = split_by_plane(study, (40, 0, 0), (1, 0, 0), group="разрез")
    assert report.ok
    assert [body.name for body in study.bodies] == ["Балка/1", "Балка/2"]
    assert study.groups["сталь"].bodies == {"Балка/1", "Балка/2"}
    shared = [index for index in range(study.face_count) if len(study.owners(index)) == 2]
    assert shared == sorted(study.groups["разрез"].faces)


def test_glue_makes_interface_and_refuses_overlap():
    study = study_of(("A", kernel.box(10, 10, 10)),
                     ("B", kernel.box(10, 10, 10, origin=(10, 2, 0))))
    assert glue(study).ok and study.glued
    assert "стык A | B" in study.groups
    crossing = study_of(("A", kernel.box(10, 10, 10)),
                        ("B", kernel.box(10, 10, 10, origin=(5, 0, 0))))
    report = glue(crossing)
    assert not report.ok and "OVERLAP" in codes(report)


def test_enclosure_groups_and_moved_user_group():
    study = study_of(("Цилиндр", kernel.cylinder(5, 20)))
    study.add_group("торец", faces=select(study, {"type": "plane", "normal": [0, 0, 1]}))
    report = enclosure(study, padding=[20, 60, 20, 20, 20, 20])
    assert report.ok and [body.name for body in study.bodies] == ["fluid"]
    groups = study.groups
    assert len(groups["inlet"].faces) == 1 and len(groups["outlet"].faces) == 1
    assert len(groups["farfield"].faces) == 4 and len(groups["wall"].faces) == 3
    assert groups["торец"].faces <= groups["wall"].faces
    volume = 90 * 50 * 60 - math.pi * 25 * 20
    assert abs(study.bodies[0].volume - volume) < 1e-3


def test_enclosure_conjugate_keeps_solid_glued():
    study = study_of(("Нагреватель", kernel.cylinder(5, 20)))
    assert enclosure(study, keep_solids=True).ok
    assert [body.name for body in study.bodies] == ["fluid", "Нагреватель"]
    assert study.glued and check(study).ok
