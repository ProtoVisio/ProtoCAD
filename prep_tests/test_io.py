"""Чтение и запись: имена, размещения, поверхности, контейнер."""

import numpy as np

from conftest import study_of
from protocad import kernel
from protocad.prep import io
from protocad.prep.io import _repaired


def test_step_round_trip_keeps_names_and_places(tmp_path):
    shaft = kernel.cylinder(3, 20)
    study = study_of(("Корпус", kernel.box(10, 10, 10)),
                     ("Вал", kernel.translated(shaft, (30, 0, 0))),
                     ("Вал [2]", kernel.translated(shaft, (60, 0, 0))))
    path = io.write_step(study, tmp_path / "сборка.step")
    back = io.load(path)
    assert [body.name for body in back.bodies] == ["Корпус", "Вал", "Вал [2]"]
    centres = [kernel.bounds(body.shape).center for body in back.bodies]
    assert np.allclose(centres[1], (30, 0, 10)) and np.allclose(centres[2], (60, 0, 10))
    assert abs(back.bodies[0].volume - 1000.0) < 1e-6


def test_mojibake_names_are_repaired():
    broken = "Корпус".encode("utf-8").decode("latin-1")
    assert _repaired(broken) == "Корпус"
    assert _repaired("Bolt:1") == "Bolt:1"
    assert _repaired("Ä") == "Ä"          # не UTF-8 байтами — не трогаем


def test_loose_faces_become_one_surface_body():
    from protocad.prep.model import faces_of_shape

    faces = faces_of_shape(kernel.box(5, 5, 5))
    bodies = io.bodies_of(kernel.compound(faces), "кожа")
    assert len(bodies) == 1 and not bodies[0].is_solid


def test_iges_surfaces_are_read(tmp_path):
    from OCP.IGESControl import IGESControl_Writer

    writer = IGESControl_Writer("MM", 1)      # 1 — грани (BRep)
    writer.AddShape(kernel.box(10, 20, 30))
    writer.ComputeModel()
    path = tmp_path / "деталь.igs"
    assert writer.Write(str(path))
    study = io.load(path)
    assert study.face_count >= 6


def test_brep_and_stl(tmp_path):
    study = study_of(("брусок", kernel.box(10, 20, 30)))
    study.add_group("верх", faces=[study.face_count - 1])
    io.write_brep(study, tmp_path / "a.brep")
    assert io.load(tmp_path / "a.brep").bodies[0].volume == 6000.0
    written = io.write_stl(study, tmp_path / "a.stl", scale=0.001)
    text = (tmp_path / "a.stl").read_text()
    assert "solid verkh" in text and "solid brusok" in text
    assert sum(written["regions"].values()) == 12


def test_container_with_assembly(tmp_path):
    from protocad import format as fmt
    from protocad.model import Assembly, Item, placement

    part = Item("АБВГ.001", "Стойка", shape=kernel.box(5, 5, 20))
    root = Assembly("АБВГ.100", "Узел")
    root.place(part, "С1", placement(0, 0, 0))
    root.place(part, "С2", placement(50, 0, 0))
    path = fmt.write(root, tmp_path / "узел")
    study = io.load(path)
    assert [body.name for body in study.bodies] == ["С1", "С2"]
    assert abs(kernel.bounds(study.bodies[1].shape).xmin - 50.0) < 1e-6
