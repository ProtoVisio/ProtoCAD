"""Путь к коду для проверок подготовки прибора. Помощники — в
`device_helpers`: у `conftest` имя общее с соседними каталогами проверок."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
