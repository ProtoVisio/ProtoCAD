"""Помощники проверок подготовки прибора: образец, прочитанный из STEP."""

import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from protocad.assembly import read_step_tree, write_step_tree  # noqa: E402
from protocad.device import Device, classify, demo  # noqa: E402


@lru_cache(maxsize=None)
def _demo_step(problems: bool, folder: str) -> str:
    path = Path(folder) / f"образец{'-ошибки' if problems else ''}.step"
    write_step_tree(demo.device(problems=problems), path)
    return str(path)


def demo_device(tmp_path_factory, problems=False) -> Device:
    """Образец прибора, прошедший через STEP, — как его получит человек."""
    folder = tmp_path_factory.getbasetemp()
    device = Device(read_step_tree(_demo_step(problems, str(folder))))
    device.notes = classify(device)
    return device


def by_label(device, label):
    found = [unit for unit in device.units.values() if unit.label == label]
    assert len(found) == 1, f"{label}: найдено {len(found)}"
    return found[0]
