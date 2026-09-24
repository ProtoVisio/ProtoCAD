"""Сборка → STEP со структурой и обратно: имена, общие определения, места."""

import numpy as np

from asm_helpers import bolt, part
from protocad import kernel
from protocad.assembly import read_step_tree, write_step_tree
from protocad.model import Assembly


def _turn_z(angle, shift):
    c, s = np.cos(angle), np.sin(angle)
    matrix = np.eye(4)
    matrix[:3, :3] = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
    matrix[:3, 3] = shift
    return matrix


def _device():
    board = Assembly("", "Плата")
    board.place(part("Подложка", kernel.box(50, 30, 1.5)), "", np.eye(4))
    chip = part("Резистор 0603", kernel.box(1.6, 0.8, 0.45))
    for number in range(3):
        board.place(chip, f"R{number + 1}", _turn_z(0.3 * number, (5 + 5 * number, 5, 1.5)))
    root = Assembly("АБВГ.300000.001", "Блок")
    root.place(board, "A1", np.eye(4))
    root.place(part("Болт М5", bolt()), "Крепёж", _turn_z(0.0, (0, 0, 20)))
    return root, board


def test_structure_names_and_places_survive(tmp_path):
    root, board = _device()
    path = write_step_tree(root, tmp_path / "блок.step")
    back = read_step_tree(path)
    assert back.name == "АБВГ.300000.001 Блок"
    assert [item.reference for item in back.placements] == ["A1", "Крепёж"]
    inner = back.placements[0].item
    # Имя вхождения по-русски OCCT сам не доносит (считает его пустым) —
    # оно достаётся из записей файла.
    assert [item.reference for item in inner.placements] == ["Подложка", "R1", "R2", "R3"]
    resistors = [item.item for item in inner.placements[1:]]
    assert resistors[0] is resistors[1] is resistors[2]
    assert resistors[0].name == "Резистор 0603"
    for mine, theirs in zip(board.placements, inner.placements):
        assert np.allclose(mine.transform, theirs.transform, atol=1e-9)
    assert np.allclose(back.placements[1].transform[:3, 3], (0, 0, 20))


def test_prep_reader_gets_russian_instance_names(tmp_path):
    from protocad.prep import io as prep_io

    root, _board = _device()
    path = write_step_tree(root, tmp_path / "блок.step")
    names = [body.name for body in prep_io.read_step(path)]
    assert "Подложка" in names and "Крепёж" in names and "R2" in names


def test_rotation_with_rounding_noise_is_written(tmp_path):
    """Решатель отдаёт поворот с погрешностью счёта — OCCT принял бы его за
    масштаб. Запись обязана его выпрямить, а не отказать."""
    root = Assembly("", "Шум")
    matrix = _turn_z(0.7, (1, 2, 3))
    matrix[:3, :3] *= 1.0 + 1e-7
    root.place(part("Брусок", kernel.box(4, 2, 1)), "B", matrix)
    back = read_step_tree(write_step_tree(root, tmp_path / "шум.step"))
    assert np.allclose(back.placements[0].transform, _turn_z(0.7, (1, 2, 3)), atol=1e-6)
