"""Разбор прибора: платы, компоненты, корпус, крепёж, прочее."""

from collections import Counter

import numpy as np

from device_helpers import by_label, demo_device
from protocad import kernel
from protocad.assembly import read_step_tree, write_step_tree
from protocad.device import (COMPONENT, FASTENER, HOUSING, OTHER, SUBSTRATE, Device,
                             classify, demo)
from protocad.device.classify import fastener_name, fastener_size
from protocad.model import KIND_DETAIL, Assembly, Item


def test_demo_device_is_understood(tmp_path_factory):
    device = demo_device(tmp_path_factory)
    roles = Counter(unit.role for unit in device.units.values())
    assert roles == {COMPONENT: 57, FASTENER: 12, OTHER: 5, HOUSING: 2, SUBSTRATE: 1}
    assert list(device.boards) and device.boards[next(iter(device.boards))].name == "A1"
    # Компонент — целиком, с выводами: у SOIC-8 семнадцать тел, единица одна.
    dd1 = by_label(device, "DD1")
    assert dd1.role == COMPONENT and dd1.kind == "DD" and len(dd1.item.placements) == 17
    kinds = Counter(device.kind_title(unit) for unit in device.of_role(COMPONENT))
    assert kinds["R — Резисторы"] == 24 and kinds["VD — Диоды"] == 4
    assert by_label(device, "Стойка:1").kind == "втулка"
    assert by_label(device, "Шайба:1").kind == "шайба"
    assert by_label(device, "Винт крышки:1").kind == "винт"
    assert {by_label(device, name).role for name in ("Основание", "Крышка")} == {HOUSING}
    assert by_label(device, "Шильдик").role == OTHER
    assert all(unit.reason for unit in device.units.values())


def test_board_as_root_like_ecad_export(tmp_path):
    """САПР плат выгружает саму плату корнем: основание и компоненты рядом."""
    board = demo._board(problems=False, extra=0)
    path = write_step_tree(board, tmp_path / "плата.step")
    device = Device(read_step_tree(path))
    classify(device)
    assert list(device.boards) == [""]
    assert len(device.of_role(COMPONENT)) == 57
    assert len(device.of_role(SUBSTRATE)) == 1


def test_unnamed_fasteners_are_found_by_shape(tmp_path):
    from asm_like import bolt_shape

    root = Assembly("", "Узел")
    root.place(Item("", "Part1", kind=KIND_DETAIL, shape=kernel.box(200, 150, 20)), "Part1")
    root.place(Item("", "Part2", kind=KIND_DETAIL, shape=bolt_shape()), "Part2",
               np.eye(4))
    ring = kernel.cut(kernel.cylinder(3.5, 0.5), kernel.cylinder(1.6, 0.5))
    root.place(Item("", "Part3", kind=KIND_DETAIL, shape=ring), "Part3")
    device = Device(read_step_tree(write_step_tree(root, tmp_path / "узел.step")))
    classify(device)
    assert by_label(device, "Part1").role == HOUSING
    assert (by_label(device, "Part2").role, by_label(device, "Part2").kind) == (FASTENER, "винт")
    assert (by_label(device, "Part3").role, by_label(device, "Part3").kind) == (FASTENER, "шайба")


def test_fastener_names():
    assert fastener_name("Винт А.М3-6gx6.58.016 ГОСТ 17473-80")[0] == "винт"
    assert fastener_name("ISO 4762 M4 x 12 --- 12N")[0] == "винт"
    assert fastener_name("DIN 934 - M5")[0] == "гайка"
    assert fastener_name("Шайба 4.65Г")[0] == "шайба"
    assert fastener_name("Корпус") is None
    assert fastener_size("Винт А.М3-6gx6.58.016 ГОСТ 17473-80") == "М3×6"
    assert fastener_size("ISO 4762 M4 x 12") == "М4×12"
