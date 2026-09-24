"""Подготовка геометрии к расчёту: проверить, вылечить, упростить.

Путь модели::

    STEP / IGES / BREP / сборка ProtoCAD ─► проверить ─► вылечить ─►
    упростить (отверстия, скругления, мелкие грани) ─► группы ─► STEP

На выходе — геометрия, а не сетка: сетку строит та расчётная система,
в которую модель уходит (SolidWorks Flow Simulation, Ansys и любые
другие, читающие STEP).

Каждый шаг — функция над исследованием (`Study`), возвращающая итог
(`Report`), а не бросающая исключение. Отказ шага геометрию не меняет.
Шаги пишутся в журнал, журнал становится рецептом (`recipe.py`), рецепт
повторяет подготовку на новой версии изделия одной командой.

**Почему на OCP напрямую, а не через движок детали.** Движок строит
ОПЕРАЦИИ детали по дереву намерений, и правило «новое только в движке»
касается их. Здесь операций детали нет: подготовка работает с готовой
формой — чужой или своей, выгруженной движком. Это допустимое применение
OCCT напрямую (`docs/08_ENGINE_BACKEND.md`, §16.1: автономные конвертеры и
операции, которых нет в FreeCAD), тот же слой, что чертежи и просмотр.

Быстрый путь из Python::

    from protocad import prep

    study = prep.load("кронштейн.step")
    print(prep.check(study).text())
    prep.heal(study)
    prep.defeature(study, holes=6.0, fillets=2.0)
    prep.write_step(study, "кронштейн-упрощённый.step")
"""

from .check import check
from .defeature import defeature, find_fillets, find_holes, find_small_faces
from .heal import heal
from .io import from_document, load, write_brep, write_step
from .model import Body, Finding, Group, Report, Study
from .recipe import record, run, run_step
from .select import describe, drop_group, make_group, picked_rule, select

__all__ = [
    "Body", "Finding", "Group", "Report", "Study",
    "check", "defeature", "describe", "drop_group", "find_fillets",
    "find_holes", "find_small_faces", "from_document", "heal", "load",
    "make_group", "picked_rule", "record", "run", "run_step", "select",
    "write_brep", "write_step",
]
