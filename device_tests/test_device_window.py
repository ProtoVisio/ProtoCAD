"""Окно подготовки прибора: разбор, дерево, случаи, проверка, контакты, выбор.

Нужен дисплей с OpenGL 3.3 (на сервере — xvfb-run). Без него проверка
пропускается: окно без контекста GL проверить честно нельзя.
"""

import os
import time

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


def _texts(item, found=None):
    found = [] if found is None else found
    for index in range(item.childCount()):
        child = item.child(index)
        found.append(child.text(0))
        _texts(child, found)
    return found


def test_device_window_path(application, tmp_path):
    from PySide6 import QtCore, QtTest

    from device_helpers import _demo_step
    from protocad.device import HOUSING, thermal
    from protocad_device import dialogs
    from protocad_device.window import CASES, NOTES, DeviceWindow

    window = DeviceWindow(folder=str(tmp_path))
    window.resize(1400, 900)
    window.show()
    assert window.open_path(_demo_step(True, str(tmp_path)), ask=False)
    _wait(application, 0.3)
    device = window.device
    root = window.tree.topLevelItem(0)
    names = _texts(root)
    assert "Плата A1 (58)" in names and "R — Резисторы (24)" in names
    assert "Корпус (2)" in names and "Крепёж (12)" in names

    # Разбор: отклонить плату — компоненты перестают быть компонентами; вернуть.
    dialog = dialogs.ClassifyDialog(device, window)
    board = dialog.boards.topLevelItem(0)
    board.setCheckState(0, QtCore.Qt.Unchecked)
    _wait(application, 0.2)
    assert not device.boards
    dialog.boards.topLevelItem(0).setCheckState(0, QtCore.Qt.Checked)
    _wait(application, 0.2)
    assert len(device.boards) == 1
    dialog._accept()

    window._push()
    device.split_halves(next(iter(device.boards)))
    window.mode.setCurrentText(CASES)
    tops = [window.tree.topLevelItem(index).text(0)
            for index in range(window.tree.topLevelItemCount())]
    assert tops[:2] == ["Полукомплект 1 (30)", "Полукомплект 2 (30)"]

    window._check()
    assert window.mode.currentText() == NOTES
    notes = _texts(window.tree.topLevelItem(0))
    assert notes[0].startswith("✖ «VT2»") and notes[1].startswith("✖ «Шильдик»")

    window.hide_housing.setChecked(True)
    housing = {body for unit in device.of_role(HOUSING)
               for body in window.unit_bodies[unit.key]}
    assert housing <= window.viewport.hidden_bodies

    # Щелчок по DD1 в виде выбирает его и в дереве.
    view = window.viewport
    target = next(unit for unit in device.units.values() if unit.name == "DD1")
    body = window.unit_bodies[target.key][0]
    points = window.preview.positions[window.preview.ids == body].reshape(-1, 3, 3).mean(axis=1)
    faces = window.preview.face_to_object
    for x, y, front in view.project(points):
        if front <= 0:
            continue
        face = view.pick_face(QtCore.QPointF(x, y))
        if face and faces[face]["body"] == body:
            QtTest.QTest.mouseClick(view, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier,
                                    QtCore.QPoint(int(x), int(y)))
            break
    else:
        pytest.fail("DD1 не виден ни в одной точке")
    _wait(application, 0.2)
    assert window.selected == {target.key}
    assert window.leaf_items[target.key].isSelected()

    window._show_contacts()
    assert len(window.rows) == len(thermal.table(device, window.report))
    assert window.contact_table.rowCount() == len(window.rows)

    assert window.save_path(tmp_path / "прибор.prcadAsm")
    assert window.export_step(tmp_path / "для расчёта.step")
    window._undo()
    assert not device.cases
    window.changed = False
    window.close()
