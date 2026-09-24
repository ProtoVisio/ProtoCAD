"""Кронштейн-образец: на нём показывают и проверяют подготовку.

Строится кодом, а не лежит файлом: в репозиторий идёт только код, а
модель, собранная из примитивов, заодно проверяет, что всё нужное для неё
в ядре есть.
"""

from __future__ import annotations

from .. import kernel


def bracket():
    """Уголок 120 × 60 × 90 мм: полка с четырьмя крепёжными отверстиями
    Ø9, стенка с отверстием под вал Ø20, скругления R2 по углам полки."""
    base = kernel.box(120.0, 60.0, 10.0)
    wall = kernel.box(10.0, 60.0, 90.0)
    shape = kernel.fuse(base, wall)
    for x, y in ((40.0, 15.0), (40.0, 45.0), (100.0, 15.0), (100.0, 45.0)):
        shape = kernel.cut(shape, kernel.cylinder(4.5, 10.0, origin=(x, y, 0.0)))
    shaft = kernel.cylinder(10.0, 10.0, origin=(0.0, 30.0, 60.0), axis=(1.0, 0.0, 0.0))
    shape = kernel.cut(shape, shaft)
    corners = [edge for edge in kernel.vertical_edges(shape)
               if abs(kernel.edge_endpoints(edge)[0][0] - 120.0) < 1e-6]
    return kernel.fillet(shape, 2.0, corners)


RECIPE = {
    "schema": 1,
    "source": "bracket.step",
    "steps": [
        {"op": "check"},
        {"op": "heal"},
        {"op": "defeature", "holes": 10.0, "fillets": 3.0},
        {"op": "group", "name": "Опора",
         "rule": {"type": "plane", "normal": [0, 0, -1], "at": "max"}},
        {"op": "group", "name": "Вал",
         "rule": {"type": "cylinder", "radius": [9.9, 10.1]}},
        {"op": "group", "name": "Сталь", "kind": "bodies", "bodies": ["*"]},
        {"op": "mesh", "size": 6.0, "order": 2, "local": {"Вал": 2.0},
         "outputs": ["bracket.inp", "bracket.msh"]},
    ],
}
