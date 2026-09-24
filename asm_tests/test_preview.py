"""Показ сборки: определение разбивается один раз, вхождения — по местам."""

import numpy as np

from asm_helpers import part
from protocad import kernel, preview
from protocad.model import Assembly


def _turn_z(angle, shift):
    c, s = np.cos(angle), np.sin(angle)
    matrix = np.eye(4)
    matrix[:3, :3] = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
    matrix[:3, 3] = shift
    return matrix


def test_instances_land_where_their_matrices_say():
    brick = part("Брусок", kernel.box(4, 2, 1))
    root = Assembly("", "Две штуки")
    root.place(brick, "A", np.eye(4))
    moved = _turn_z(np.pi / 2, (10, 20, 30))
    root.place(brick, "B", moved)
    scene = preview.build(root)
    base_pos, _normals, base_faces = kernel.tessellate_faces(brick.shape, 0.1)
    second = scene.positions[scene.ids == 2]
    expected = base_pos @ moved[:3, :3].T.astype(np.float32) + moved[:3, 3]
    assert np.allclose(second, expected, atol=1e-5)
    faces_a = set(scene.face_ids[scene.ids == 1].tolist())
    faces_b = set(scene.face_ids[scene.ids == 2].tolist())
    assert not faces_a & faces_b and len(faces_a) == len(faces_b) == 6
    assert scene.id_to_object[2]["path"] == [root.placements[1].stable_id]
    edges_b = scene.edge_positions[scene.edge_ids == 2]
    assert np.allclose(edges_b.min(axis=0), (8, 20, 30), atol=1e-5)


def test_cache_reuses_definitions_until_shape_changes():
    brick = part("Брусок", kernel.box(4, 2, 1))
    root = Assembly("", "Кэш")
    for number in range(5):
        root.place(brick, f"R{number}", _turn_z(0.0, (number * 10, 0, 0)))
    cache = {}
    first = preview.build(root, cache=cache)
    again = preview.build(root, cache=cache)
    assert first.stats["unique_tessellations"] == 1
    assert again.stats["unique_tessellations"] == 0
    assert np.array_equal(first.positions, again.positions)
    brick.shape = kernel.box(4, 2, 3)
    changed = preview.build(root, cache=cache)
    assert changed.stats["unique_tessellations"] == 1
    assert np.isclose(changed.positions[:, 2].max(), 3.0)
