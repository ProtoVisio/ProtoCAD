"""Окно подготовки: открывается, выбирает грани, собирает группы.

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


def test_pick_faces_and_group(application, tmp_path):
    from PySide6 import QtCore, QtTest

    from protocad import prep
    from protocad_prep.window import PrepWindow

    window = PrepWindow(folder=str(tmp_path))
    window._demo()
    window.resize(1200, 800)
    window.show()
    _wait(application, 0.8)
    view = window.viewport

    def click(point, modifiers=QtCore.Qt.NoModifier):
        x, y = view.project_point(*point)[:2]
        QtTest.QTest.mouseClick(view, QtCore.Qt.LeftButton, modifiers,
                                QtCore.QPoint(int(x), int(y)))
        _wait(application, 0.2)

    click((80, 30, 10))                                  # верх полки
    click((5, 30, 90), QtCore.Qt.ControlModifier)         # верх стенки
    assert len(window.selected) == 2
    tops = {prep.describe(window.study.face(face))["center"][2]
            for face in window.selected}
    assert tops == {10.0, 90.0}
    rule = prep.picked_rule(window.study, sorted(window.selected))
    window._step(prep.make_group, "Нагрузка", rule=rule)
    assert len(window.study.groups["Нагрузка"].faces) == 2
    assert window.selected == set()
    window._step(prep.defeature, holes=10.0)
    assert len(window.study.groups["Нагрузка"].faces) == 2
    window._undo()
    assert len(prep.find_holes(window.study, 10.0)) == 4
    window.close()
