"""Рецепт подготовки: шаги, которые повторяются на новой версии геометрии.

Подготовка к расчёту — не разовая работа. Конструктор меняет деталь,
выгружает STEP заново, и всё — лечение, упрощение, группы — надо сделать
снова. Рецепт записывает шаги, и повтор — одна команда:

    python -m protocad.prep run bracket.prep.json

Рецепт — JSON::

    {
      "schema": 1,
      "source": "bracket.step",
      "steps": [
        {"op": "heal"},
        {"op": "defeature", "holes": 6, "fillets": 2},
        {"op": "group", "name": "Опора",
         "rule": {"type": "plane", "normal": [0, 0, -1], "at": "max"}},
        {"op": "group", "name": "Сталь", "kind": "bodies", "bodies": ["*"]},
        {"op": "export", "path": "bracket-prepared.step"}
      ]
    }

Пути — относительно файла рецепта. Шаг, закончившийся отказом,
останавливает прогон: всё, что после него, строилось бы на не той модели.

Группы, выбранные мышью, записываются точками на гранях. Точка переживает
мелкие правки, но не перенос грани — для повторяемых рецептов правило
надёжнее выбора, и об этом стоит помнить, записывая рецепт из окна.
"""

from __future__ import annotations

import json
from pathlib import Path

from .model import Report, Study

SCHEMA = 1


def _export_step(study, step: dict, base: Path) -> Report:
    from . import io

    path = _path(base, step["path"])
    report = Report("export", params={"path": step["path"]})
    suffix = path.suffix.lower()
    try:
        if suffix in io.STEP_EXTENSIONS:
            io.write_step(study, path)
        elif suffix in io.BREP_EXTENSIONS:
            io.write_brep(study, path)
        else:
            return report.fail("BAD_FORMAT", f"геометрию в {suffix} не пишем: "
                               f"STEP или BREP")
    except Exception as failure:  # noqa: BLE001
        return report.fail("EXPORT_FAILED", f"{path.name}: {failure}")
    report.message = f"записано: {path.name}"
    study.log.append(report)
    return report


def _operations() -> dict:
    from .check import check
    from .defeature import defeature
    from .heal import heal
    from .select import drop_group, make_group

    return {
        "check": check,
        "heal": heal,
        "defeature": defeature,
        "group": make_group,
        "ungroup": drop_group,
    }


#: Шаги, которые знают о путях: им нужен каталог рецепта.
_WITH_PATHS = {"export": _export_step}


def run_step(study: Study, step: dict, base=".") -> Report:
    """Выполнить один шаг рецепта. Итог уже в журнале исследования."""
    step = dict(step)
    op = step.pop("op", "")
    step.pop("comment", None)
    strict = step.pop("strict", False) if op == "check" else False
    if op in _WITH_PATHS:
        return _WITH_PATHS[op](study, step, Path(base))
    function = _operations().get(op)
    if function is None:
        report = Report(op or "?")
        report.fail("UNKNOWN_STEP", f"неизвестный шаг: {op!r}. Известны: "
                    f"{', '.join(sorted(list(_operations()) + list(_WITH_PATHS)))}")
        study.log.append(report)
        return report
    try:
        report = function(study, **step)
    except TypeError as failure:
        report = Report(op)
        report.fail("BAD_PARAMS", f"шаг «{op}»: {failure}")
        study.log.append(report)
        return report
    if op == "check" and not strict:
        # Проверка без «strict» сообщает, но не останавливает: шаги после
        # неё часто и есть лечение того, что она нашла.
        report = _softened(report)
    return report


def _softened(report: Report) -> Report:
    if not report.ok:
        report.ok = True
        report.message = f"{report.message} (прогон продолжается)"
    return report


def run(recipe, source=None, base=None, stop_on_failure: bool = True):
    """Прогнать рецепт. Возвращает (исследование, [итоги шагов]).

    ``recipe`` — словарь или путь к JSON. ``source`` подменяет исходный
    файл рецепта: тот же рецепт на новой версии детали.
    """
    from . import io

    if isinstance(recipe, (str, Path)):
        path = Path(recipe)
        base = Path(base) if base else path.parent
        recipe = json.loads(path.read_text(encoding="utf-8"))
    base = Path(base or ".")
    if int(recipe.get("schema", SCHEMA)) > SCHEMA:
        raise ValueError(f"рецепт схемы {recipe['schema']} новее поддерживаемой "
                         f"{SCHEMA} — обновите ProtoCAD")
    origin = source or recipe.get("source")
    if not origin:
        raise ValueError("в рецепте не указан исходный файл (source)")
    study = io.load(_path(base, origin))
    reports = []
    for step in recipe.get("steps") or ():
        report = run_step(study, step, base)
        reports.append(report)
        if not report.ok and stop_on_failure:
            break
    return study, reports


def record(study: Study, base=None) -> dict:
    """Рецепт по журналу исследования — то, что сделали, в том же порядке.

    Берутся удавшиеся шаги, меняющие модель или пишущие файлы. Пути
    записываются относительно ``base`` — туда же ляжет рецепт.
    """
    base = Path(base).resolve() if base else None
    steps = []
    for report in study.log:
        if not report.ok or report.op not in set(_operations()) | set(_WITH_PATHS):
            continue
        step = {"op": report.op}
        params = dict(report.params)
        if report.op == "export":
            params["path"] = _relative(base, params["path"])
        step.update(params)
        steps.append(step)
    source = study.source
    return {"schema": SCHEMA, "name": study.name,
            "source": _relative(base, source) if source else "",
            "steps": steps}


def save(recipe: dict, path) -> Path:
    path = Path(path)
    path.write_text(json.dumps(recipe, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    return path


def _path(base: Path, value) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (Path(base) / path).resolve()


def _relative(base, value) -> str:
    if base is None:
        return str(value)
    try:
        return str(Path(value).resolve().relative_to(base))
    except ValueError:
        return str(value)
