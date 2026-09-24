"""Документ сборки: файл, STEP со структурой, выбор граней, детали движка."""

import numpy as np

from asm_helpers import bolt, face, part, place_of
from protocad import engine, kernel
from protocad.assembly import AssemblyDocument, read_step_tree
from protocad.model import Assembly


def _bolted_plate():
    document = AssemblyDocument("Узел", designation="АБВГ.300000.001")
    plate = document.add(part("Плита", kernel.cut(
        kernel.box(60, 40, 10), kernel.cylinder(5, 10, origin=(20, 15, 0)))), "Плита")
    screw = document.add(part("Болт", bolt()), "Болт")
    document.mate("concentric", face(document, plate, "cylinder", radius=5.0),
                  face(document, screw, "cylinder", radius=5.0))
    document.mate("coincident", face(document, plate, "plane", normal=(0, 0, 1)),
                  face(document, screw, "plane", normal=(0, 0, -1), near=(6.5, 0, 30)))
    assert document.solve() == []
    return document, plate, screw


def test_save_and_open_keeps_mates_and_places(tmp_path):
    document, plate, screw = _bolted_plate()
    path = document.save(tmp_path / "узел.prcadAsm")
    again = AssemblyDocument.open(path)
    assert [item.reference for item in again.occurrences] == ["Плита", "Болт"]
    assert len(again.mates) == 2 and again.fixed == {plate.stable_id}
    moved = again.occurrence(screw.stable_id)
    assert np.allclose(moved.transform, screw.transform)
    # Положение не только записано, но и снова выводится из сопряжений.
    moved.transform = np.eye(4)
    assert again.solve() == []
    assert np.allclose(moved.transform, screw.transform, atol=1e-6)
    assert again.bom()[0]["quantity"] == 1


def test_same_file_inserted_twice_shares_definition(tmp_path):
    from protocad.prep import Study, write_step

    study = Study(name="Стойка")
    study.add_body(kernel.box(5, 5, 20), "Стойка")
    write_step(study, tmp_path / "стойка.step")
    document = AssemblyDocument("Две стойки")
    one = document.insert(tmp_path / "стойка.step")
    two = document.insert(tmp_path / "стойка.step")
    assert one.item is two.item and one.reference != two.reference
    scene = document.scene()
    assert scene.stats["unique_tessellations"] == 1 and scene.stats["instances"] == 2


def _board_step(path):
    """STEP «плата»: подложка и три одинаковых резистора с обозначениями."""
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopLoc import TopLoc_Location
    from OCP.XCAFDoc import XCAFDoc_DocumentTool
    from OCP.gp import gp_Trsf, gp_Vec

    def named(label, text):
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(text, True))

    document = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    root = tool.NewShape()
    named(root, "Плата А1")
    board = tool.AddShape(kernel.box(50, 30, 1.5), False, False)
    named(board, "Плата печатная")
    chip = tool.AddShape(kernel.box(1.6, 0.8, 0.45), False, False)
    named(chip, "R_0603")
    component = tool.AddComponent(root, board, TopLoc_Location())
    named(component, "Плата печатная")
    for number, x in enumerate((5.0, 15.0, 25.0), start=1):
        move = gp_Trsf()
        move.SetTranslation(gp_Vec(x, 10.0, 1.5))
        named(tool.AddComponent(root, chip, TopLoc_Location(move)), f"R{number}")
    tool.UpdateAssemblies()
    writer = STEPCAFControl_Writer()
    writer.SetNameMode(True)
    writer.Transfer(document, STEPControl_AsIs)
    writer.Write(str(path))


def test_step_tree_keeps_designators_and_shared_definitions(tmp_path):
    _board_step(tmp_path / "плата.step")
    root = read_step_tree(tmp_path / "плата.step")
    assert isinstance(root, Assembly) and root.name == "Плата А1"
    references = [item.reference for item in root.placements]
    assert references[1:] == ["R1", "R2", "R3"]
    chips = [item.item for item in root.placements[1:]]
    assert chips[0] is chips[1] is chips[2] and chips[0].name == "R_0603"
    assert np.allclose(root.placements[3].transform[:3, 3], (25, 10, 1.5))
    rows = {row["name"]: row for row in root.bom()}
    assert rows["R_0603"]["quantity"] == 3
    assert rows["R_0603"]["references"] == ["R1", "R2", "R3"]


def test_face_of_subassembly_is_picked_in_top_coordinates(tmp_path):
    _board_step(tmp_path / "плата.step")
    document = AssemblyDocument("Прибор")
    box = document.add(part("Корпус", kernel.box(80, 60, 5)), "Корпус")
    board = document.insert(tmp_path / "плата.step", "А1",
                            transform=_shift((100, 0, 0)))
    scene = document.scene()
    # Грань, попавшая под щелчок: низ подложки платы (деталь подсборки).
    wanted = None
    for face_id, entry in scene.face_to_object.items():
        body = scene.id_to_object[entry["body"]]
        if body["path"][0] == board.stable_id and body["label"] == "Плата печатная":
            picked = document.pick(scene, face_id)
            if picked and np.allclose(picked[1].get("normal", (0, 0, 0)), (0, 0, -1)):
                wanted = picked
    assert wanted is not None and wanted[0] is board
    document.mate("coincident", face(document, box, "plane", normal=(0, 0, 1)), wanted)
    assert document.solve() == []
    assert np.isclose(place_of(board)[2], 5.0)       # плата легла на корпус


def test_engine_part_in_assembly_and_snapshot(tmp_path):
    from protocad.document import Document, Operation
    from protocad.model import Item
    from protocad.sketch import Sketch

    sketch = Sketch("Контур")
    sketch.polyline([(0, 0), (40, 0), (40, 20), (0, 20)])
    sketch.solve()
    part_document = Document("Планка", backend=engine.make_backend("local"),
                             designation="АБВГ.741124.002")
    part_document.add(Operation("pad", "Выдавливание", sketch=sketch, length=4))
    assert part_document.rebuild().ok
    item = Item(part_document.designation, part_document.name, document=part_document)
    document = AssemblyDocument("С планкой")
    host = document.add(part("Основание", kernel.box(60, 40, 10)))
    strip = document.add(item)
    faces = document.faces(strip)
    assert any(face["surface"] == "plane" for face in faces)
    document.mate("coincident", face(document, host, "plane", normal=(0, 0, 1)),
                  face(document, strip, "plane", normal=(0, 0, -1)))
    assert document.solve() == []
    assert np.isclose(place_of(strip)[2], 10.0)
    path = document.save(tmp_path / "с планкой.prcadAsm")
    again = AssemblyDocument.open(path)
    snapshot = again.occurrences[1].item
    assert snapshot.shape is not None and abs(kernel.volume(snapshot.shape) - 3200) < 1e-6


def _shift(vector):
    matrix = np.eye(4)
    matrix[:3, 3] = vector
    return matrix
