"""Образец прибора — на нём видно всё, ради чего сделан разбор.

Блок: основание с угловыми колоннами, крышка на четырёх винтах, плата A1
на четырёх шестигранных стойках. На плате два полукомплекта с подряд
идущими обозначениями (R1–R12 и R13–R24, C1–C8 и C9–C16, …) и общие
XS1, ZQ1, C17. Модели компонентов — многотельные, как их выгружают
системы проектирования плат: корпус отдельно, выводы отдельно.

``problems=True`` добавляет то, что встречается в настоящих моделях и что
обязаны найти проверки: транзистор VT2 висит над платой на 0,3 мм,
шильдик не касается крышки, винты крышки сидят в отверстиях под резьбу
(пересечение по резьбе).

``extra`` — сколько добавить мелких резисторов и конденсаторов: так
собирается плата на тысячу компонентов для замеров скорости.
"""

from __future__ import annotations

import math

import numpy as np

from .. import kernel
from ..model import KIND_ASSEMBLY, KIND_DETAIL, KIND_STANDARD, Assembly, Item

BOARD_ORIGIN = (10.0, 10.0, 11.0)
BOARD_SIZE = (160.0, 100.0, 1.6)
MOUNTING = ((5.0, 5.0), (155.0, 5.0), (5.0, 95.0), (155.0, 95.0))


def _place(x=0.0, y=0.0, z=0.0, turn=0.0) -> np.ndarray:
    matrix = np.eye(4)
    c, s = math.cos(math.radians(turn)), math.sin(math.radians(turn))
    matrix[:3, :3] = [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]
    matrix[:3, 3] = (x, y, z)
    return matrix


def _part(name, shape, kind=KIND_DETAIL, designation="") -> Item:
    return Item(designation, name, kind=kind, shape=shape)


def _block(size, centre_xy=(0.0, 0.0), z=0.0):
    """Брусок, стоящий на высоте ``z`` с серединой в ``centre_xy``."""
    length, width, height = size
    return kernel.box(length, width, height,
                      origin=(centre_xy[0] - length / 2, centre_xy[1] - width / 2, z))


def _component(name, body, leads) -> Assembly:
    """Компонент: корпус и выводы отдельными телами."""
    component = Assembly("", name, kind=KIND_ASSEMBLY)
    component.place(_part(f"{name}: корпус", body), "Корпус")
    for number, lead in enumerate(leads, 1):
        component.place(_part(f"{name}: вывод", lead), f"Вывод {number}")
    return component


def _chip(name, length, width, height, cap):
    """Чип 0603/0805: керамика и два торцевых вывода."""
    body = _block((length - 2 * cap, width, height))
    leads = [_block((cap, width, height), (sign * (length - cap) / 2, 0.0))
             for sign in (-1, 1)]
    return _component(name, body, leads)


def _soic8():
    body = _block((4.9, 3.9, 1.45), z=0.15)
    leads = []
    for side in (-1, 1):
        for x in (-1.905, -0.635, 0.635, 1.905):
            leads.append(_block((0.4, 0.9, 0.15), (x, side * 2.5)))
            leads.append(_block((0.4, 0.35, 0.6), (x, side * 2.125), z=0.15))
    return _component("SOIC-8", body, leads)


def _qfn16():
    body = _block((4.0, 4.0, 0.85), z=0.05)
    leads = [_block((2.1, 2.1, 0.05))]
    for index in range(4):
        offset = -0.975 + 0.65 * index
        for x, y in ((offset, -1.8), (offset, 1.8), (-1.8, offset), (1.8, offset)):
            leads.append(_block((0.3, 0.3, 0.05), (x, y)))
    return _component("QFN-16", body, leads)


def _sod123():
    body = _block((2.7, 1.6, 1.0), z=0.15)
    leads = [_block((0.6, 0.6, 0.15), (sign * 1.65, 0.0)) for sign in (-1, 1)]
    return _component("SOD-123", body, leads)


def _sot23():
    body = _block((2.9, 1.3, 1.0), z=0.1)
    leads = [_block((0.4, 0.5, 0.1), (x, y))
             for x, y in ((-0.95, -0.9), (0.95, -0.9), (0.0, 0.9))]
    return _component("SOT-23", body, leads)


def _inductor():
    body = _block((4.4, 5.0, 3.0))
    leads = [_block((0.3, 5.0, 3.0), (sign * 2.35, 0.0)) for sign in (-1, 1)]
    return _component("Дроссель 5×5", body, leads)


def _crystal():
    body = _block((10.4, 4.7, 3.5), z=0.1)
    leads = [_block((1.2, 4.7, 0.1), (sign * 4.6, 0.0)) for sign in (-1, 1)]
    return _component("HC-49/SMD", body, leads)


def _electrolytic():
    """Выводной электролит: банка на плате, выводы — сквозь отверстия."""
    body = kernel.cylinder(4.0, 10.5)
    leads = [kernel.cylinder(0.3, 3.0, origin=(sign * 1.75, 0.0, -3.0)) for sign in (-1, 1)]
    return _component("Конденсатор К50-35", body, leads)


def _connector(pins=10):
    body = _block((25.0, 8.0, 9.0))
    leads = [kernel.cylinder(0.32, 3.0, origin=(-11.43 + 2.54 * index, 0.0, -3.0))
             for index in range(pins)]
    return _component("Разъём PLS-10", body, leads)


def _hex_prism(across_flats, height, hole=0.0, z=0.0):
    radius = across_flats / 2.0 / math.cos(math.radians(30))
    points = [(radius * math.cos(math.radians(60 * k)),
               radius * math.sin(math.radians(60 * k))) for k in range(6)]
    shape = kernel.extrude_polygon(points, height, z)
    if hole:
        shape = kernel.cut(shape, kernel.cylinder(hole / 2.0, height, origin=(0, 0, z)))
    return shape


def _screw(length, head=5.5, head_height=1.8, diameter=3.0):
    """Винт с цилиндрической головкой: стержень вниз от нуля, головка вверх."""
    shaft = kernel.cylinder(diameter / 2.0, length, origin=(0.0, 0.0, -length))
    cap = kernel.cylinder(head / 2.0, head_height)
    return kernel.fuse(shaft, cap)


def _washer():
    return kernel.cut(kernel.cylinder(3.5, 0.5), kernel.cylinder(1.6, 0.5))


# --- состав ------------------------------------------------------------------------

#: Полукомплект: (обозначение, определение, x, y, поворот) в координатах
#: платы; второй полукомплект — тот же, сдвинутый на 80 мм.
def _half(defs, first):
    """Компоненты одного полукомплекта с номерами от ``first``."""
    r, c, dd, vd, vt, da, l = (first[key] for key in ("R", "C", "DD", "VD", "VT", "DA", "L"))
    placed = []
    for index in range(12):
        placed.append((f"R{r + index}", defs["r0603"], 12 + 5 * (index % 6),
                       40 + 4 * (index // 6), 0))
    for index in range(8):
        placed.append((f"C{c + index}", defs["c0805"], 12 + 6 * (index % 4),
                       52 + 5 * (index // 4), 90))
    placed += [(f"DD{dd}", defs["soic8"], 20, 25, 0), (f"DD{dd + 1}", defs["soic8"], 35, 25, 0),
               (f"VD{vd}", defs["sod123"], 50, 40, 0),
               (f"VD{vd + 1}", defs["sod123"], 50, 45, 0),
               (f"VT{vt}", defs["sot23"], 58, 40, 0),
               (f"DA{da}", defs["qfn16"], 50, 25, 0),
               (f"L{l}", defs["inductor"], 62, 52, 0)]
    return placed


def device(problems: bool = False, extra: int = 0) -> Assembly:
    """Собрать образец прибора (состав с формами)."""
    root = Assembly("АБВГ.468332.001", "Блок управления", kind=KIND_ASSEMBLY)

    base = kernel.cut(kernel.box(180, 120, 40), kernel.box(174, 114, 37, origin=(3, 3, 3)))
    for x, y in ((3, 3), (169, 3), (3, 109), (169, 109)):
        column = kernel.box(8, 8, 37, origin=(x, y, 3))
        column = kernel.cut(column, kernel.cylinder(1.25, 10, origin=(x + 4, y + 4, 30)))
        base = kernel.fuse(base, column)
    root.place(_part("Основание", base, designation="АБВГ.735311.001"), "Основание")
    cover = kernel.box(180, 120, 3, origin=(0, 0, 40))
    for x, y in ((7, 7), (173, 7), (7, 113), (173, 113)):
        cover = kernel.cut(cover, kernel.cylinder(1.7, 3, origin=(x, y, 40)))
    root.place(_part("Крышка", cover, designation="АБВГ.741124.001"), "Крышка")

    root.place(_board(problems, extra), "A1", _place(*BOARD_ORIGIN))

    standoff = _part("Стойка М3×8", _hex_prism(5.5, 8.0, 2.5), KIND_STANDARD)
    screw = _part("Винт А.М3-6gx6.58.016 ГОСТ 17473-80", _screw(6.0), KIND_STANDARD)
    washer = _part("Шайба А.3.01.016 ГОСТ 11371-78", _washer(), KIND_STANDARD)
    top = BOARD_ORIGIN[2] + BOARD_SIZE[2]
    for number, (x, y) in enumerate(MOUNTING, 1):
        at_x, at_y = BOARD_ORIGIN[0] + x, BOARD_ORIGIN[1] + y
        root.place(standoff, f"Стойка:{number}", _place(at_x, at_y, 3.0))
        root.place(washer, f"Шайба:{number}", _place(at_x, at_y, top))
        root.place(screw, f"Винт платы:{number}", _place(at_x, at_y, top + 0.5))
    cover_screw = _part("Винт А.М3-6gx10.58.016 ГОСТ 17473-80", _screw(10.0), KIND_STANDARD)
    for number, (x, y) in enumerate(((7, 7), (173, 7), (7, 113), (173, 113)), 1):
        root.place(cover_screw, f"Винт крышки:{number}", _place(x, y, 43.0))

    label = _part("Шильдик", kernel.box(40, 20, 0.5), designation="АБВГ.754312.001")
    root.place(label, "Шильдик", _place(70, 50, 43.5 if problems else 43.0))
    return root


def _board(problems: bool, extra: int) -> Assembly:
    length, width, thickness = BOARD_SIZE
    defs = {"r0603": _chip("Резистор 0603", 1.6, 0.8, 0.45, 0.3),
            "c0805": _chip("Конденсатор 0805", 2.0, 1.25, 0.85, 0.4),
            "soic8": _soic8(), "qfn16": _qfn16(), "sod123": _sod123(),
            "sot23": _sot23(), "inductor": _inductor()}
    placed = _half(defs, {"R": 1, "C": 1, "DD": 1, "VD": 1, "VT": 1, "DA": 1, "L": 1})
    second = _half(defs, {"R": 13, "C": 9, "DD": 3, "VD": 3, "VT": 2, "DA": 2, "L": 2})
    placed += [(name, item, x + 80, y, turn) for name, item, x, y, turn in second]
    through = []
    placed.append(("XS1", _connector(), 80, 88, 0))
    through += [(80 - 11.43 + 2.54 * index, 88) for index in range(10)]
    placed.append(("ZQ1", _crystal(), 80, 60, 0))
    placed.append(("C17", _electrolytic(), 80, 30, 0))
    through += [(80 - 1.75, 30), (80 + 1.75, 30)]
    placed += _extra(extra, defs)

    substrate = kernel.box(length, width, thickness)
    holes = [kernel.cylinder(1.6, thickness, origin=(x, y, 0)) for x, y in MOUNTING]
    holes += [kernel.cylinder(0.5, thickness, origin=(x, y, 0)) for x, y in through]
    holes += [kernel.cylinder(0.15, thickness, origin=(8 + 9.5 * (k % 16), 8 + 84 * (k // 16), 0))
              for k in range(32)]
    substrate = kernel.cut(substrate, kernel.compound(holes))
    board = Assembly("АБВГ.687242.001", "Плата управления", kind=KIND_ASSEMBLY)
    board.place(_part("Плата печатная", substrate, designation="АБВГ.758726.001"), "Плата")
    for name, item, x, y, turn in placed:
        lift = 0.3 if problems and name == "VT2" else 0.0
        board.place(item, name, _place(x, y, thickness + lift, turn))
    return board


def _extra(count: int, defs) -> list:
    """Россыпь мелких резисторов и конденсаторов на свободных полях платы:
    над полукомплектами (y 66…82) и под ними (y 10…19,6). Больше 1063 не
    помещается — лишние не ставятся."""
    slots = [(6.0 + 2.45 * column, 66.0 + 1.6 * row)
             for row in range(11) for column in range(61)]
    slots += [(12.0 + 2.45 * column, 10.0 + 1.6 * row)
              for row in range(7) for column in range(56)]
    placed = []
    for index, (x, y) in enumerate(slots[:count]):
        if index % 2:
            placed.append((f"C{100 + index}", defs["c0805"], x, y, 0))
        else:
            placed.append((f"R{100 + index}", defs["r0603"], x, y, 0))
    return placed
