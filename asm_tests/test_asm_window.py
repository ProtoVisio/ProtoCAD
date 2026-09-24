"""Окно сборки: вставка, сопряжения ЩЕЛЧКАМИ по граням, отмена, файл.

Нужен дисплей с OpenGL 3.3 (на сервере — xvfb-run). Без него проверка
пропускается: окно без контекста GL проверить честно нельзя.
"""

import os
import time

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DISPLAY"),
                                reason="нет дисплея (запускайте под xvfb-run)")


@pytest.fixture(scope="module")
def application():
    from PySide6 import QtGui, QtWidgets

    from protocad_gl.viewport import default_surface_format

    QtGui.QSurfaceFormat.setDefaultFormat(default_surface_format())
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _wait(application, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        application.processEvents()
        time.sleep(0.01)


def _spot(window, occurrence, test):
    """Точка экрана, под которой видна грань вхождения, отвечающая условию.
    Ищется так, как ищет глазами человек: по видимым треугольникам грани."""
    from PySide6 import QtCore

    view = window.viewport
    scene_faces = window._scene_faces(occurrence)
    for face in window.document.faces(occurrence):
        if not test(face):
            continue
        face_id = scene_faces[face["index"]]
        triangles = window.preview.positions[window.preview.face_ids == face_id]
        for x, y, front in view.project(triangles.reshape(-1, 3, 3).mean(axis=1)):
            if front > 0 and 0 < x < view.width() and 0 < y < view.height() \
                    and view.pick_face(QtCore.QPointF(x, y)) == face_id:
                return QtCore.QPoint(int(x), int(y))
    return None


def test_bolt_goes_into_hole_by_clicks(application, tmp_path):
    from PySide6 import QtCore, QtTest

    from asm_helpers import bolt
    from protocad import kernel
    from protocad.assembly import AssemblyDocument, read_step_tree
    from protocad_asm.window import AssemblyWindow

    kernel.export_brep(kernel.cut(kernel.box(60, 40, 10),
                                  kernel.cylinder(5, 10, origin=(20, 15, 0))),
                       str(tmp_path / "плита.brep"))
    kernel.export_brep(bolt(), str(tmp_path / "болт.brep"))
    window = AssemblyWindow(folder=str(tmp_path))
    window.resize(1300, 850)
    window.show()
    _wait(application, 0.5)
    view = window.viewport

    def click(point):
        assert point is not None, "грань не видна"
        QtTest.QTest.mouseClick(view, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier, point)
        _wait(application, 0.15)

    plate = window.insert_path(tmp_path / "плита.brep")
    screw = window.insert_path(tmp_path / "болт.brep")
    assert window.document.fixed == {plate.stable_id}
    # Вставленное встаёт рядом, а не внутрь уже стоящего.
    assert screw.transform[0, 3] > 60

    window.start_mate("concentric")
    click(_spot(window, plate, lambda face: face["surface"] == "cylinder"))
    click(_spot(window, screw, lambda face: face["surface"] == "cylinder"
                and abs(face["radius"] - 5.0) < 1e-6))
    assert np.allclose(screw.transform[:2, 3], (20, 15), atol=1e-6)
    window._mate_apply()

    window.start_mate("coincident")
    click(_spot(window, plate, lambda face: face["surface"] == "plane"
                and face["normal"][2] > 0.99))
    view.pitch = -30.0          # низ головки виден только снизу
    view.update()
    _wait(application, 0.2)
    click(_spot(window, screw, lambda face: face["surface"] == "plane"
                and face["normal"][2] < -0.99 and face["center"][2] > 1.0))
    assert window.mate["added"] is not None
    window.mate_flip.setChecked(True)          # развернуть на пробу…
    window.mate_flip.setChecked(False)         # …и вернуть
    window._mate_apply()
    # Головка (низ на z = 30 у болта) лежит на верху плиты (z = 10).
    assert np.allclose(screw.transform[:3, 3], (20, 15, -20), atol=1e-6)
    assert window.document.diagnostics == []
    assert window.document.status(screw) == "сопряжено"

    # Щелчок по детали выбирает вхождение верхнего уровня.
    view.pitch = 28.0
    view.update()
    _wait(application, 0.2)
    click(_spot(window, screw, lambda face: face["surface"] == "cylinder"))
    assert window._chosen() is screw

    assert window.save_path(tmp_path / "узел.prcadAsm")
    assert window.export_step(tmp_path / "узел.step")
    tree = read_step_tree(tmp_path / "узел.step")
    assert [item.reference for item in tree.placements] == ["плита:1", "болт:1"]
    assert np.allclose(tree.placements[1].transform[:3, 3], (20, 15, -20), atol=1e-6)

    window._undo()                              # снять совпадение
    assert len(window.document.mates) == 1
    again = AssemblyDocument.open(tmp_path / "узел.prcadAsm")
    assert len(again.mates) == 2 and again.solve() == []
    window.changed = False
    window.close()


def test_wrong_face_is_refused_with_reason(application, tmp_path):
    from asm_helpers import part
    from protocad import kernel
    from protocad_asm.window import AssemblyWindow

    window = AssemblyWindow(folder=str(tmp_path))
    first = window.document.add(part("Брусок", kernel.box(10, 10, 10)), "A")
    window.document.add(part("Брусок", kernel.box(10, 10, 10)), "B")
    window._refresh(fit=True)
    window.show()
    _wait(application, 0.3)
    window.start_mate("concentric")
    face_id = window._scene_faces(first)[0]
    window._picked({"kind": "face", "id": face_id})
    assert window.mate["picks"] == []
    assert "цилиндрическая" in window.mate_state.text()
    window._escape()
    assert window.mate is None and not window.panel.isVisible()
    window.changed = False
    window.close()
