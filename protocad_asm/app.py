"""ProtoCAD — сборка на сопряжениях.

    python protocad_asm/app.py
    python protocad_asm/app.py узел.prcadAsm
    python protocad_asm/app.py деталь1.step деталь2.step …

Файл сборки открывается; файлы деталей и сборок STEP, IGES, BREP
вставляются в новую сборку по очереди — первая вставленная закрепляется.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6 import QtGui, QtWidgets

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from protocad_gl.viewport import default_surface_format  # noqa: E402
from protocad_asm.window import AssemblyWindow  # noqa: E402


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    QtGui.QSurfaceFormat.setDefaultFormat(default_surface_format())
    app = QtWidgets.QApplication(argv)
    from protocad_gl.translations import install

    install(app)
    window = AssemblyWindow()
    files = [Path(name) for name in argv[1:]]
    if len(files) == 1 and files[0].suffix.lower() == ".prcadasm":
        window.open_path(files[0])
    else:
        for path in files:
            window.insert_path(path)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
