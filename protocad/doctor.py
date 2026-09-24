"""Проверка окружения: что установлено и какие окна ProtoCAD будут работать.

    python -m protocad.doctor

Ничего не ставит и ничего не меняет — только смотрит и говорит, чего не
хватает и что с этим делать.
"""

from __future__ import annotations

import importlib
import platform
import sys
import time


def _module(name: str, attribute: str = "__version__"):
    try:
        module = importlib.import_module(name)
    except Exception as failure:  # noqa: BLE001 — сказать, а не упасть
        return None, f"{type(failure).__name__}: {failure}"
    return module, str(getattr(module, attribute, "") or "")


def main() -> int:
    lines, problems = [], []

    def say(ok: bool, what: str, detail: str = "", fix: str = "") -> None:
        lines.append(f"{'[ок]' if ok else '[НЕТ]'} {what}" + (f" — {detail}" if detail else ""))
        if not ok and fix:
            problems.append(fix)

    version = sys.version_info
    say(version >= (3, 12), f"Python {platform.python_version()} ({platform.system()})",
        "" if version >= (3, 12) else "нужен 3.12 или новее",
        "поставьте Python 3.12+ (python.org) и создайте окружение заново")

    numpy, detail = _module("numpy")
    say(numpy is not None, "numpy", detail, "pip install -r requirements.txt")
    qt, detail = _module("PySide6")
    say(qt is not None, "PySide6 (окна)", detail, "pip install -r requirements.txt")
    gl, detail = _module("OpenGL")
    say(gl is not None, "PyOpenGL (показ)", detail, "pip install -r requirements.txt")
    ocp, detail = _module("OCP")
    occt = f"OCP {detail}" if detail else ""
    say(ocp is not None, "cadquery-ocp (геометрия: сборка, подготовка, прибор)", occt,
        "pip install -r requirements.txt")
    gcs, detail = _module("planegcs")
    say(gcs is not None, "planegcs (решатель эскизов)", detail or "",
        "pip install planegcs (нужен Python 3.12+)")

    engine_ok = False
    try:
        from .engine.freecad_backend import FreeCADBackend, find_freecad

        home = find_freecad()
        if home is None:
            say(False, "FreeCAD (движок окна детали)", "не найден",
                "поставьте FreeCAD 1.1 (freecad.org) в обычное место или укажите путь "
                "переменной PROTOCAD_FREECAD")
        else:
            started = time.perf_counter()
            backend = FreeCADBackend(home)
            capabilities = backend.capabilities()
            engine_ok = bool(capabilities.version)
            say(engine_ok, f"FreeCAD {capabilities.version or '?'} в {home}",
                f"движок поднялся за {time.perf_counter() - started:.1f} с, операций "
                f"{len(capabilities.features)}" if engine_ok else capabilities.notes,
                "FreeCAD найден, но движок не поднялся — пришлите этот вывод")
            backend.shutdown()
    except Exception as failure:  # noqa: BLE001
        say(False, "FreeCAD (движок окна детали)", f"{type(failure).__name__}: {failure}",
            "пришлите этот вывод")

    base = all(item is not None for item in (numpy, qt, gl, ocp))
    lines.append("")
    lines.append("Что будет работать:")
    lines.append(f"  {'да ' if base and engine_ok and gcs else 'нет'}  деталь: "
                 f"python protocad_app/app.py (нужен FreeCAD)")
    for title, command in (("сборка", "protocad_asm/app.py"),
                           ("подготовка прибора к тепловому расчёту", "protocad_device/app.py"),
                           ("подготовка геометрии к расчёту", "protocad_prep/app.py"),
                           ("просмотрщик", "protocad_viewer/app.py файл.prcadAsm")):
        lines.append(f"  {'да ' if base else 'нет'}  {title}: python {command}")
    if problems:
        lines.append("")
        lines.append("Что сделать:")
        lines.extend(f"  - {item}" for item in dict.fromkeys(problems))
    print("\n".join(lines))
    return 0 if base else 1


if __name__ == "__main__":
    sys.exit(main())
