"""Рецепт: прогон, повтор на новой версии, запись из журнала."""

import json

from prep_helpers import study_of
from protocad import kernel
from protocad.prep import demo, io, recipe
from protocad.prep.select import make_group


def _bracket_file(folder, shape=None):
    study = study_of(("Кронштейн", shape or demo.bracket()), name="Кронштейн")
    return io.write_step(study, folder / "bracket.step")


def _steps_without_export():
    return [step for step in demo.RECIPE["steps"] if step["op"] != "export"]


def test_recipe_runs_and_stops_on_failure(tmp_path):
    _bracket_file(tmp_path)
    data = {"schema": 1, "source": "bracket.step", "steps": _steps_without_export() + [
        {"op": "group", "name": "нет такого", "rule": {"type": "torus"}},
        {"op": "export", "path": "не дойдёт.step"}]}
    study, reports = recipe.run(data, base=tmp_path)
    assert [report.op for report in reports][-1] == "group"
    assert not reports[-1].ok
    assert not (tmp_path / "не дойдёт.step").exists()
    assert len(study.groups["Опора"].faces) == 1


def test_recipe_replays_on_changed_geometry(tmp_path):
    """Та же подготовка на удлинённом кронштейне: правила находят грани."""
    longer = kernel.fuse(demo.bracket(), kernel.box(40, 60, 10, origin=(118, 0, 0)))
    _bracket_file(tmp_path, longer)
    data = {"schema": 1, "source": "bracket.step", "steps": _steps_without_export()}
    study, reports = recipe.run(data, base=tmp_path)
    assert all(report.ok for report in reports)
    assert study.bounds().xmax > 150
    assert len(study.groups["Вал"].faces) == 1


def test_record_from_log_and_run_again(tmp_path):
    _bracket_file(tmp_path)
    study = io.load(tmp_path / "bracket.step")
    from protocad.prep import defeature, heal

    heal(study)
    defeature(study, holes=10.0)
    make_group(study, "Опора", {"type": "plane", "normal": [0, 0, -1], "at": "max"})
    recipe.run_step(study, {"op": "export", "path": "prepared.step"}, tmp_path)
    written = recipe.record(study, base=tmp_path)
    assert [step["op"] for step in written["steps"]] == ["heal", "defeature", "group",
                                                         "export"]
    assert written["source"] == "bracket.step"
    path = recipe.save(written, tmp_path / "r.json")
    again, reports = recipe.run(path)
    assert all(report.ok for report in reports)
    assert (tmp_path / "prepared.step").is_file()
    assert json.loads(path.read_text(encoding="utf-8"))["steps"][2]["name"] == "Опора"


def test_demo_recipe_end_to_end(tmp_path):
    _bracket_file(tmp_path)
    study, reports = recipe.run(demo.RECIPE, base=tmp_path)
    assert all(report.ok for report in reports), reports[-1].text()
    prepared = io.load(tmp_path / "bracket-prepared.step")
    assert [body.name for body in prepared.bodies] == ["Кронштейн"]
    # Четыре крепёжных Ø9 и два скругления R2 убраны, Ø20 под вал оставлено.
    from protocad.prep import find_fillets, find_holes

    assert [round(item.size, 3) for item in find_holes(prepared, 30.0)] == [20.0]
    assert find_fillets(prepared, 5.0) == []
