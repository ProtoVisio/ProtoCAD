"""Помощники проверок сборок.

Каталог не называется `tests`: `tests/` в .gitignore — там внутренние
проверки проекта, включая карантин GPL (THIRD_PARTY.md).
"""

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from protocad import kernel  # noqa: E402
from protocad.model import KIND_DETAIL, Item  # noqa: E402


def part(name, shape):
    return Item("", name, kind=KIND_DETAIL, shape=shape)


def face(document, occurrence, surface, **where):
    """Грань вхождения по описанию: вид и близость центра/оси к точке."""
    best, best_gap = None, None
    for item in document.faces(occurrence):
        if item["surface"] != surface:
            continue
        if "normal" in where and np.dot(item.get("normal", (0, 0, 0)), where["normal"]) < 0.999:
            continue
        if "radius" in where and abs(item.get("radius", 0.0) - where["radius"]) > 1e-6:
            continue
        point = np.asarray(where.get("near", item["center"]), float)
        gap = float(np.linalg.norm(np.asarray(item["center"]) - point))
        if best_gap is None or gap < best_gap:
            best, best_gap = item, gap
    assert best is not None, f"грань {surface} {where} не найдена"
    return occurrence, best


def place_of(occurrence):
    return occurrence.transform[:3, 3]


def bolt(radius=5.0, length=30.0, head_radius=8.0, head=6.0):
    """Болт: стержень вдоль +Z от нуля, головка сверху."""
    shaft = kernel.cylinder(radius, length)
    cap = kernel.cylinder(head_radius, head, origin=(0.0, 0.0, length))
    return kernel.fuse(shaft, cap)
