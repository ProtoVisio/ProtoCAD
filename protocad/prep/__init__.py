"""Подготовка геометрии к расчёту — препроцессор ProtoCAD.

Путь модели от CAD до решателя::

    STEP/IGES/BREP ─► проверить ─► вылечить ─► упростить ─► разрезать /
    склеить / область течения ─► группы ─► сетка ─► .inp/.med/.msh/…

Каждый шаг — функция над исследованием (`Study`), возвращающая итог
(`Report`), а не бросающая исключение. Отказ шага геометрию не меняет.
Шаги пишутся в журнал, журнал становится рецептом (`recipe.py`), рецепт
повторяет подготовку на новой версии детали одной командой.

**Почему на OCP напрямую, а не через движок детали.** Движок строит
ОПЕРАЦИИ детали по дереву намерений, и правило «новое только в движке»
касается их. Здесь операций детали нет: препроцессор работает с готовой
формой — чужой или своей, выгруженной движком. Это тот же слой, что
чертежи (`drawing`) и просмотр (`preview`), и живёт он там же.

**Почему сетка в отдельном процессе** — см. `gmsh_runner.py`: лицензия
gmsh (GPL), своя сборка OCCT в его колесе и падения построителя на
плохой геометрии.

Быстрый путь из Python::

    from protocad import prep

    study = prep.load("bracket.step")
    print(prep.check(study).text())
    prep.heal(study)
    prep.defeature(study, holes=6.0, fillets=2.0)
    prep.make_group(study, "fixed", {"type": "plane", "normal": [0, 0, -1],
                                     "at": "max"})
    prep.mesh(study, prep.MeshSpec(size=3.0, order=2), ["bracket.inp"])
"""

from .check import check
from .cut import cut_by_plane, split_by_plane
from .defeature import defeature, find_fillets, find_holes, find_small_faces
from .fluid import enclosure
from .glue import glue, overlaps
from .heal import heal
from .io import load, write_brep, write_step, write_stl
from .mesh import MeshSpec, find_python, mesh
from .model import Body, Finding, Group, Report, Study
from .recipe import record, run, run_step
from .select import describe, drop_group, make_group, picked_rule, select

__all__ = [
    "Body", "Finding", "Group", "MeshSpec", "Report", "Study",
    "check", "cut_by_plane", "defeature", "describe", "drop_group",
    "enclosure", "find_fillets", "find_holes", "find_python",
    "find_small_faces", "glue", "heal", "load", "make_group", "mesh",
    "overlaps", "picked_rule", "record", "run", "run_step", "select",
    "split_by_plane", "write_brep", "write_step", "write_stl",
]
