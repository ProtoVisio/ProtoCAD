"""Путь к коду и общие заготовки проверок препроцессора. Помощники — в
`prep_helpers`: у `conftest` имя общее с соседним каталогом проверок."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from protocad import kernel  # noqa: E402


@pytest.fixture
def plate():
    """Пластина 100×60×10 со сквозными Ø6 и Ø20, глухим Ø5 и скруглениями R4."""
    from OCP.BRepPrimAPI import BRepPrimAPI_MakeCone
    from OCP.gp import gp_Ax2, gp_Dir, gp_Pnt

    shape = kernel.box(100, 60, 10)
    shape = kernel.cut(shape, kernel.cylinder(3, 10, origin=(15, 15, 0)))
    shape = kernel.cut(shape, kernel.cylinder(10, 10, origin=(50, 30, 0)))
    drill = kernel.fuse(
        kernel.cylinder(2.5, 6, origin=(85, 45, 4)),
        BRepPrimAPI_MakeCone(gp_Ax2(gp_Pnt(85, 45, 4), gp_Dir(0, 0, -1)),
                             2.5, 0.0, 1.5).Shape())
    shape = kernel.cut(shape, drill)
    return kernel.fillet(shape, 4.0, kernel.vertical_edges(shape))
