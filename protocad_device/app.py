"""ProtoCAD — подготовка прибора к тепловому расчёту.

    python protocad_device/app.py
    python protocad_device/app.py прибор.step
    python protocad_device/app.py прибор.prcadAsm

Без файла открывается образец — блок управления с платой из двух
полукомплектов, корпусом и крепежом, с нарочно оставленными ошибками модели.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6 import QtGui, QtWidgets

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from protocad_device.window import DeviceWindow  # noqa: E402
from protocad_gl.viewport import default_surface_format  # noqa: E402


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    QtGui.QSurfaceFormat.setDefaultFormat(default_surface_format())
    app = QtWidgets.QApplication(argv)
    from protocad_gl.translations import install

    install(app)
    window = DeviceWindow()
    window.show()
    if len(argv) > 1:
        window.open_path(argv[1])
    else:
        window._demo()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
