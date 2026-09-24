"""Путь к коду проекта для проверок сборок. Помощники — в `asm_helpers`:
у `conftest` имя общее с соседним каталогом проверок, и при общем прогоне
импорт из него попадал бы в чужой файл."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
