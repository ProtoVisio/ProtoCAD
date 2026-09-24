"""Общее для проверок препроцессора.

Каталог назван `prep_tests`, а не `tests`: `tests/` в .gitignore —
там живут внутренние проверки, включая карантин GPL (THIRD_PARTY.md).
"""

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from protocad import kernel  # noqa: E402
from protocad.prep import Study  # noqa: E402
from protocad.prep.mesh import find_python  # noqa: E402

needs_gmsh = pytest.mark.skipif(find_python()[0] is None,
                                reason="gmsh не найден (requirements-mesh.txt)")
needs_ccx = pytest.mark.skipif(shutil.which("ccx") is None,
                               reason="CalculiX (ccx) не установлен")


def study_of(*named_shapes, name="проба"):
    study = Study(name=name)
    for label, shape in named_shapes:
        study.add_body(shape, label)
    return study


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
