"""Подготовка к расчёту из командной строки.

    python -m protocad.prep info    деталь.step
    python -m protocad.prep check   деталь.step [--deep] [--small 0.1]
    python -m protocad.prep holes   деталь.step --diameter 8
    python -m protocad.prep convert деталь.igs деталь.step
    python -m protocad.prep run     рецепт.json [--source новая.step]

``--json`` у любой команды печатает итог машиночитаемо — для сборочных
конвейеров и скриптов.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _print(data, as_json: bool) -> None:
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=1))
    elif isinstance(data, str):
        print(data)


def _info(args) -> int:
    from . import io

    study = io.load(args.file)
    summary = study.summary()
    if args.json:
        summary["names"] = [body.name for body in study.bodies]
        _print(summary, True)
        return 0
    box = summary["bounds"]
    print(f"{Path(args.file).name}: тел {summary['bodies']} (объёмов "
          f"{summary['solids']}, открытых оболочек {summary['open_shells']}), "
          f"граней {summary['faces']}, рёбер {summary['edges']}")
    print(f"габарит {box[3] - box[0]:.4g} × {box[4] - box[1]:.4g} × "
          f"{box[5] - box[2]:.4g} мм, объём {summary['volume']:.6g} мм³")
    for body in study.bodies:
        kind = "тело" if body.is_solid else "поверхность"
        print(f"  {body.name}: {kind}, {body.volume:.6g} мм³")
    return 0


def _check(args) -> int:
    from . import io
    from .check import check

    study = io.load(args.file)
    report = check(study, small=args.small, deep=args.deep)
    _print(report.to_dict() if args.json else report.text(), args.json)
    return 0 if report.ok else 1


def _holes(args) -> int:
    from . import io
    from .defeature import find_fillets, find_holes

    study = io.load(args.file)
    found = []
    if args.diameter:
        found += find_holes(study, args.diameter)
    if args.radius:
        found += find_fillets(study, args.radius)
    if args.json:
        _print([item.to_dict() for item in found], True)
    else:
        for item in found:
            print(f"«{item.body}»: {item.title}, грани {item.faces}")
        print(f"всего: {len(found)}")
    return 0


def _convert(args) -> int:
    from . import io

    study = io.load(args.source)
    target = Path(args.target)
    suffix = target.suffix.lower()
    if suffix in io.STEP_EXTENSIONS:
        io.write_step(study, target)
    elif suffix in io.BREP_EXTENSIONS:
        io.write_brep(study, target)
    else:
        print(f"пишем STEP или BREP, а не {suffix}", file=sys.stderr)
        return 2
    print(f"записано: {target}")
    return 0


def _run(args) -> int:
    from .recipe import run

    study, reports = run(args.recipe, source=args.source)
    ok = all(report.ok for report in reports)
    if args.json:
        _print({"ok": ok, "steps": [report.to_dict() for report in reports],
                "summary": study.summary()}, True)
    else:
        for number, report in enumerate(reports, start=1):
            print(f"[{number}] {report.text()}")
        print("готово" if ok else "ОСТАНОВЛЕНО на отказе")
    return 0 if ok else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m protocad.prep",
        description="Подготовка геометрии к расчёту")
    # Ключ у каждой команды, а не у общей части: его пишут после команды
    # («check деталь.step --json»), и перед командой он бы не находился.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--json", action="store_true", help="итог машиночитаемо")
    commands = parser.add_subparsers(dest="command", required=True)

    info = commands.add_parser("info", parents=[common], help="что в файле")
    info.add_argument("file")
    info.set_defaults(handler=_info)

    check = commands.add_parser("check", parents=[common], help="проверить геометрию")
    check.add_argument("file")
    check.add_argument("--small", type=float, default=0.0,
                       help="мелкое ребро, мм (по умолчанию 0,001 диагонали)")
    check.add_argument("--deep", action="store_true",
                       help="искать самопересечения (долго)")
    check.set_defaults(handler=_check)

    holes = commands.add_parser("holes", parents=[common], help="найти отверстия и скругления")
    holes.add_argument("file")
    holes.add_argument("--diameter", type=float, default=0.0,
                       help="отверстия не больше этого диаметра")
    holes.add_argument("--radius", type=float, default=0.0,
                       help="скругления не больше этого радиуса")
    holes.set_defaults(handler=_holes)

    convert = commands.add_parser("convert", parents=[common], help="перевести формат")
    convert.add_argument("source")
    convert.add_argument("target")
    convert.set_defaults(handler=_convert)

    run = commands.add_parser("run", parents=[common], help="прогнать рецепт")
    run.add_argument("recipe")
    run.add_argument("--source", default=None,
                     help="другой исходный файл вместо указанного в рецепте")
    run.set_defaults(handler=_run)

    args = parser.parse_args(argv)
    from .io import ImportError_

    try:
        return args.handler(args)
    except (ImportError_, ValueError, FileNotFoundError) as failure:
        print(f"ошибка: {failure}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
