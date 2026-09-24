"""Сетка: форматы, файл CalculiX, согласованность склейки, счёт решателем."""

import re
import subprocess

import numpy as np
import pytest

from conftest import needs_ccx, needs_gmsh, study_of
from protocad import kernel
from protocad.prep import MeshSpec, glue, mesh
from protocad.prep.select import select

pytestmark = needs_gmsh


def _read_inp(path):
    text = path.read_text(encoding="ascii")
    nodes = {}
    block = re.search(r"\*NODE, NSET=NALL\n(.*?)\n\*", text, re.S).group(1)
    for line in block.splitlines():
        tag, x, y, z = line.split(",")
        nodes[int(tag)] = np.array([float(x), float(y), float(z)])
    elements = {}
    for name, body in re.findall(r"\*ELEMENT, TYPE=C3D\d+, ELSET=(\S+)\n(.*?)(?=\n\*)",
                                 text, re.S):
        elements[name] = np.array([[int(v) for v in line.split(",")]
                                   for line in body.splitlines()])
    sets = {name: [int(v) for v in re.split(r"[,\s]+", body) if v]
            for name, body in re.findall(r"\*NSET, NSET=(\S+)\n(.*?)(?=\n\*|\Z)",
                                         text, re.S)}
    surfaces = {name: [tuple(line.split(", ")) for line in body.splitlines()]
                for name, body in re.findall(
                    r"\*SURFACE, NAME=(\S+), TYPE=ELEMENT\n(.*?)(?=\n\*|\Z)", text, re.S)}
    return text, nodes, elements, sets, surfaces


@pytest.fixture
def beam(tmp_path):
    study = study_of(("Балка", kernel.box(100, 10, 10)), name="Консоль")
    study.add_group("Заделка", faces=select(study, {"type": "plane", "normal": [-1, 0, 0],
                                                    "at": "max"}))
    study.add_group("Торец", faces=select(study, {"type": "plane", "normal": [1, 0, 0],
                                                  "at": "max"}))
    study.add_group("сталь", kind="bodies", bodies=["Балка"])
    return study


def test_formats_and_stats(beam, tmp_path):
    outputs = [tmp_path / f"b.{ext}" for ext in ("inp", "msh", "med", "unv", "vtk")]
    report = mesh(beam, MeshSpec(size=5.0, order=1), outputs)
    assert report.ok, report.text()
    assert all(path.stat().st_size > 1000 for path in outputs)
    stats = report.after["stats"]
    assert stats["groups"]["Zadelka"] > 0 and stats["groups"]["stal"] > 0
    assert report.after["quality"]["inverted"] == 0
    assert beam.log[-1] is report


def test_calculix_file_geometry(beam, tmp_path):
    """Квадратичные элементы в порядке CalculiX, поверхности — на своих гранях."""
    path = tmp_path / "b.inp"
    assert mesh(beam, MeshSpec(size=4.0, order=2), [path]).ok
    text, nodes, elements, sets, surfaces = _read_inp(path)
    assert "CPS3" not in text and text.isascii()
    table = elements["Balka"]
    assert table.shape[1] == 11
    edges = {4: (0, 1), 5: (1, 2), 6: (0, 2), 7: (0, 3), 8: (1, 3), 9: (2, 3)}
    for row in table[:200]:
        points = [nodes[tag] for tag in row[1:]]
        for middle, (a, b) in edges.items():
            assert np.allclose(points[middle], (points[a] + points[b]) / 2, atol=1e-6)
        a, b, c, d = points[:4]
        assert np.dot(np.cross(b - a, c - a), d - a) > 0      # не вывернут
    assert all(abs(nodes[tag][0]) < 1e-9 for tag in sets["Zadelka"])
    corners = {1: (0, 1, 2), 2: (0, 3, 1), 3: (1, 3, 2), 4: (2, 3, 0)}
    rows = {int(row[0]): row[1:] for row in table}
    for element, side in surfaces["Torets"]:
        face = [rows[int(element)][index] for index in corners[int(side[1:])]]
        assert all(abs(nodes[tag][0] - 100.0) < 1e-9 for tag in face)


def test_local_size_refines(beam, tmp_path):
    coarse = mesh(beam, MeshSpec(size=5.0), [tmp_path / "a.msh"])
    fine = mesh(beam, MeshSpec(size=5.0, local={"Торец": 1.0}), [tmp_path / "b.msh"])
    assert fine.after["stats"]["groups"]["Torets"] > 4 * coarse.after["stats"]["groups"]["Torets"]


def test_glued_bodies_share_interface_nodes(tmp_path):
    study = study_of(("Сталь", kernel.box(20, 20, 10)),
                     ("Алюм", kernel.box(20, 20, 10, origin=(0, 0, 10))))
    assert glue(study).ok
    path = tmp_path / "two.inp"
    assert mesh(study, MeshSpec(size=3.0), [path]).ok
    _text, _nodes, elements, sets, _surfaces = _read_inp(path)
    shared = set(elements["Stal"][:, 1:].ravel()) & set(elements["Alyum"][:, 1:].ravel())
    assert shared and shared == set(sets["styk_Alyum_Stal"])


def test_refusals(beam, tmp_path):
    assert mesh(beam, MeshSpec(local={"нет": 1.0}), [tmp_path / "a.msh"]).findings[0].code \
        == "NO_GROUP"
    assert not mesh(beam, MeshSpec(), [tmp_path / "a.xyz"]).ok


@needs_ccx
def test_calculix_solves_cantilever(beam, tmp_path):
    """Консоль 100×10×10, сталь, сила 100 Н на торце: прогиб по формуле."""
    assert mesh(beam, MeshSpec(size=2.5, order=2, local={"Заделка": 1.5}),
                [tmp_path / "beam.inp"]).ok
    _text, _nodes, _elements, sets, _surfaces = _read_inp(tmp_path / "beam.inp")
    tip = sets["Torets"]
    deck = ["*INCLUDE, INPUT=beam.inp", "*MATERIAL, NAME=STEEL", "*ELASTIC",
            "210000, 0.3", "*SOLID SECTION, ELSET=EALL, MATERIAL=STEEL", "*STEP",
            "*STATIC", "*BOUNDARY", "Zadelka, 1, 3, 0.0", "*CLOAD"]
    deck += [f"{tag}, 3, {-100.0 / len(tip):.10g}" for tag in tip]
    deck += ["*NODE PRINT, NSET=Torets", "U", "*END STEP"]
    (tmp_path / "bend.inp").write_text("\n".join(deck) + "\n")
    subprocess.run(["ccx", "-i", "bend"], cwd=tmp_path, capture_output=True, check=True,
                   timeout=300)
    rows = [line.split() for line in (tmp_path / "bend.dat").read_text().splitlines()]
    uz = [float(row[3]) for row in rows if len(row) == 4 and row[0].isdigit()]
    theory = 100 * 100 ** 3 / (3 * 210000 * (10 * 10 ** 3 / 12))
    assert abs(-np.mean(uz) - theory) / theory < 0.02
