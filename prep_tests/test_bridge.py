"""Мост из конструирования: деталь → выгрузка движком → исследование."""

from pathlib import Path

from protocad import engine
from protocad.document import Document, Operation
from protocad.prep import check, from_document, load
from protocad.sketch import Sketch


def _plate_document():
    sketch = Sketch("Контур")
    sketch.polyline([(0, 0), (100, 0), (100, 60), (0, 60)])
    sketch.solve()
    document = Document("Пластина", backend=engine.make_backend("local"),
                        designation="АБВГ.741141.001")
    document.add(Operation("pad", "Выдавливание", sketch=sketch, length=10))
    assert document.rebuild().ok
    return document


def test_document_export_step_and_brep(tmp_path):
    document = _plate_document()
    step = document.export(tmp_path / "пластина.step")
    assert step.ok, step.message
    assert abs(load(tmp_path / "пластина.step").bodies[0].volume - 60000) < 1e-6
    assert document.export(tmp_path / "пластина.brep").ok


def test_from_document_gives_checked_study(tmp_path):
    study = from_document(_plate_document(), tmp_path)
    assert study.name == "АБВГ.741141.001"
    assert [body.name for body in study.bodies] == ["Тело"]
    assert check(study).ok
    assert Path(study.source).parent == tmp_path
