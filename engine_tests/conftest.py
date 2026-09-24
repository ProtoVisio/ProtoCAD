"""Путь к коду для проверок движка. Проверки на настоящем FreeCAD
пропускаются, если его нет (`python -m protocad.doctor` скажет, где искать)."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for folder in (ROOT, ROOT / "protocad_app"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
