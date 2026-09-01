"""Проверка, что читатель работает в чистом окружении ПРОТО.

    python protocad_reader/selftest.py work/АБВГ.687281.001.prcadAsm

Запускается обычным Python БЕЗ путей к FreeCAD. Если что-то из читателя
потянет FreeCAD или OCCT — здесь это и вскроется, а не у клиента.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from protocad_reader import open_container  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("укажите файл .prcadAsm / .prcadPart")
        return 2
    container = open_container(sys.argv[1])

    forbidden = [name for name in sys.modules if name.split(".")[0] in ("FreeCAD", "Part", "OCC")]
    print(f"Файл:        {Path(sys.argv[1]).name}")
    print(f"Обозначение: {container.designation}  ({container.kind})")
    print(f"Схема:       версия {container.schema_version}")
    print(f"Наименование: {container.name}")

    print("\nСпецификация:")
    for row in container.bom():
        references = row.get("references") or []
        tail = f"  [{references[0]}…{references[-1]}]" if len(references) > 1 else ""
        print(
            f"  {row['kind']:<20} {row['designation']:<22} {row['name']:<26} "
            f"×{row['quantity']}{tail}"
        )

    preview = container.preview()
    if preview is None:
        print("\nПросмотровых данных в файле нет.")
    else:
        print(
            f"\nПросмотр: {preview.triangle_count} треугольников, "
            f"{preview.edge_count} рёбер, {len(preview.id_to_object)} тел"
        )

    tree = container.tree()
    print(f"\nСостав: {len(tree['children'])} вхождений в корне")
    for child in tree["children"][:5]:
        item = child["item"]
        print(
            f"  {child['reference'] or '—':<8} {item.get('designation',''):<22} "
            f"{item.get('name','')}"
        )
    if len(tree["children"]) > 5:
        print(f"  … ещё {len(tree['children']) - 5}")

    print(f"\nСвязи с ПРОТО: {container.proto_links()}")
    print(f"\nЗагруженные модули CAD-ядра: {forbidden or 'НЕТ — читатель чист'}")
    container.close()
    return 0 if not forbidden else 1


if __name__ == "__main__":
    sys.exit(main())
