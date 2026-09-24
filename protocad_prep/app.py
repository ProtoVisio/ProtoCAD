"""ProtoCAD — подготовка геометрии к расчёту.

    python protocad_prep/app.py
    python protocad_prep/app.py деталь.step

Без файла окно открывается с образцом — кронштейном, на котором видно
всё: отверстия под упрощение, грань под закрепление, отверстие под вал.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6 import QtGui, QtWidgets

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from protocad_gl.viewport import default_surface_format  # noqa: E402
from protocad_prep.window import PrepWindow  # noqa: E402


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    QtGui.QSurfaceFormat.setDefaultFormat(default_surface_format())
    app = QtWidgets.QApplication(argv)
    window = PrepWindow()
    if len(argv) > 1:
        if not window.open_path(argv[1]):
            window._demo()
    else:
        window._demo()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
