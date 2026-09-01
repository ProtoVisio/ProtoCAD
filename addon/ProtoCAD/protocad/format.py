"""Формат файлов ProtoCAD: запись и полное чтение.

Контейнер — ZIP по механике ``.FCStd``, схема своя. Расширение по виду
изделия, как в ЕСКД:

* ``.prcadPart`` — деталь;
* ``.prcadAsm``  — сборочная единица;
* ``.prcadDraw`` — чертёж.

```
manifest.json     версия схемы, вид, обозначение, состав контейнера
structure.json    изделия, состав, спецификация — модель из protocad.model
bom.json          готовые строки спецификации (ПРОТО не пересчитывает)
geometry/*.brp    точная геометрия B-Rep
preview/scene.npz тесселяция и рёбра для просмотра без CAD-ядра
meta/links.json   связи с записями ПРОТО
```

Ключевое решение: **производные для просмотра лежат внутри файла**. ПРОТО
открывает контейнер и читает ``preview/`` и ``bom.json``, не имея ни FreeCAD,
ни OCCT. Читатель для этого — ``protocad_reader``, он от FreeCAD не зависит.

Этот модуль требует FreeCAD: он пишет и читает точную геометрию.
"""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import FreeCAD as App
import Part

from . import model

SCHEMA_VERSION = 1
PRODUCER = "ProtoCAD"

EXTENSION_BY_KIND = {
    model.KIND_DETAIL: ".prcadPart",
    model.KIND_PURCHASED: ".prcadPart",
    model.KIND_STANDARD: ".prcadPart",
    model.KIND_MATERIAL: ".prcadPart",
    model.KIND_ASSEMBLY: ".prcadAsm",
    model.KIND_COMPLEX: ".prcadAsm",
    model.KIND_KIT: ".prcadAsm",
}
DRAWING_EXTENSION = ".prcadDraw"


class FormatError(RuntimeError):
    """Контейнер не соответствует схеме."""


def extension_for(item) -> str:
    return EXTENSION_BY_KIND.get(item.Kind, ".prcadPart")


def _placement_values(placement) -> list[float]:
    return [float(v) for v in placement.toMatrix().A]


def _placement_from(values) -> App.Placement:
    matrix = App.Matrix()
    matrix.A = [float(v) for v in values]
    return App.Placement(matrix)


def _collect_items(root) -> list:
    """Все изделия дерева, каждое по одному разу."""
    seen: dict[str, object] = {}
    stack = [root]
    while stack:
        current = stack.pop()
        if current is None or current.StableId in seen:
            continue
        seen[current.StableId] = current
        for link in getattr(current, "Placements", []) or []:
            stack.append(link.LinkedObject)
        for entry in getattr(current, "BomOnly", []) or []:
            stack.append(entry.LinkedObject)
    return list(seen.values())


def _safe(stable_id: str) -> str:
    return stable_id.replace(":", "_").replace("/", "_")


def write(root, path: str | Path, preview=None, drawings=None) -> Path:
    """Записать изделие со всем составом в контейнер.

    ``preview`` — объект ``Scene`` из вьюпорта либо None. ``drawings`` —
    список путей к SVG-листам.
    """
    path = Path(path)
    # Обозначения ЕСКД содержат точки (АБВГ.687281.001), поэтому Path.suffix
    # для них возвращает ".001" — проверять «расширения нет» бессмысленно.
    # Сверяемся со своим списком и дописываем через конкатенацию имени.
    known = set(EXTENSION_BY_KIND.values()) | {DRAWING_EXTENSION}
    if path.suffix not in known:
        path = path.with_name(path.name + extension_for(root))
    path.parent.mkdir(parents=True, exist_ok=True)

    items = _collect_items(root)
    geometry_names: dict[str, str] = {}
    structure = {"root": root.StableId, "items": [], "assemblies": []}

    for item in items:
        record = {
            "stable_id": item.StableId,
            "designation": item.Designation,
            "name": item.ItemName,
            "kind": item.Kind,
            "proto_id": getattr(item, "ProtoItemId", ""),
            "note": getattr(item, "Note", ""),
        }
        shape = getattr(item, "Shape", None)
        if shape is not None and not shape.isNull() and shape.Faces:
            name = f"geometry/{_safe(item.StableId)}.brp"
            geometry_names[item.StableId] = name
            record["geometry"] = name
        structure["items"].append(record)

        if item.Kind != model.KIND_ASSEMBLY:
            continue
        structure["assemblies"].append(
            {
                "stable_id": item.StableId,
                "placements": [
                    {
                        "stable_id": getattr(link, "StableId", ""),
                        "item": link.LinkedObject.StableId if link.LinkedObject else "",
                        "reference": getattr(link, "Reference", ""),
                        "side": getattr(link, "Side", ""),
                        "note": getattr(link, "SpecNote", ""),
                        "placement": _placement_values(link.Placement),
                    }
                    for link in item.Placements
                    if link.LinkedObject is not None
                ],
                "bom_only": [
                    {
                        "item": entry.LinkedObject.StableId,
                        "quantity": int(getattr(entry, "Quantity", 1)),
                        "note": getattr(entry, "SpecNote", ""),
                    }
                    for entry in item.BomOnly
                    if entry.LinkedObject is not None
                ],
            }
        )

    manifest = {
        "format": PRODUCER,
        "schema_version": SCHEMA_VERSION,
        "kind": root.Kind,
        "designation": root.Designation,
        "name": root.ItemName,
        "stable_id": root.StableId,
        "created": datetime.now(timezone.utc).isoformat(),
        "producer": f"{PRODUCER} (FreeCAD {'.'.join(str(v) for v in App.Version()[:3])})",
        "contents": {
            "structure": "structure.json",
            "bom": "bom.json",
            "geometry": sorted(geometry_names.values()),
            "preview": "preview/scene.npz" if preview is not None else None,
            "drawings": [],
        },
    }

    bom_rows = model.bom(root) if root.Kind == model.KIND_ASSEMBLY else []

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in items:
            name = geometry_names.get(item.StableId)
            if not name:
                continue
            # exportBrep пишет только в файл, поэтому через временный путь.
            temporary = path.parent / f"._{_safe(item.StableId)}.brp"
            item.Shape.exportBrep(str(temporary))
            archive.write(temporary, name)
            temporary.unlink(missing_ok=True)

        if preview is not None:
            import io

            import numpy as np

            buffer = io.BytesIO()
            np.savez_compressed(
                buffer,
                positions=preview.positions,
                normals=preview.normals,
                ids=preview.ids,
                edge_positions=preview.edge_positions,
                edge_ids=preview.edge_ids,
                meta=np.frombuffer(
                    json.dumps(
                        {
                            "stats": preview.stats,
                            "id_to_object": preview.id_to_object,
                        },
                        ensure_ascii=False,
                    ).encode("utf-8"),
                    dtype=np.uint8,
                ),
            )
            archive.writestr("preview/scene.npz", buffer.getvalue())

        for index, drawing in enumerate(drawings or []):
            drawing_path = Path(drawing)
            name = f"drawings/{index:02d}_{drawing_path.name}"
            archive.write(drawing_path, name)
            manifest["contents"]["drawings"].append(name)

        archive.writestr(
            "structure.json", json.dumps(structure, ensure_ascii=False, indent=2)
        )
        archive.writestr("bom.json", json.dumps(bom_rows, ensure_ascii=False, indent=2))
        archive.writestr(
            "meta/links.json",
            json.dumps(
                {
                    item.StableId: getattr(item, "ProtoItemId", "")
                    for item in items
                    if getattr(item, "ProtoItemId", "")
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.writestr(
            "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
        )
    return path


def read(path: str | Path, doc=None):
    """Полное чтение с восстановлением точной геометрии. Требует FreeCAD."""
    path = Path(path)
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        if manifest.get("format") != PRODUCER:
            raise FormatError(f"не контейнер ProtoCAD: {manifest.get('format')!r}")
        if manifest.get("schema_version", 0) > SCHEMA_VERSION:
            raise FormatError(
                f"схема версии {manifest['schema_version']} новее поддерживаемой "
                f"{SCHEMA_VERSION} — обновите ProtoCAD"
            )
        structure = json.loads(archive.read("structure.json").decode("utf-8"))

        if doc is None:
            doc = App.newDocument(manifest.get("designation") or "protocad")

        by_id: dict[str, object] = {}
        for record in structure["items"]:
            shape = None
            if record.get("geometry"):
                temporary = path.parent / f"._read_{_safe(record['stable_id'])}.brp"
                temporary.write_bytes(archive.read(record["geometry"]))
                shape = Part.Shape()
                shape.read(str(temporary))
                temporary.unlink(missing_ok=True)
            if record["kind"] == model.KIND_ASSEMBLY:
                obj = model.make_assembly(doc, record["designation"], record["name"])
            else:
                obj = model.make_item(
                    doc,
                    record["designation"],
                    record["name"],
                    record["kind"],
                    shape=shape,
                    proto_id=record.get("proto_id", ""),
                )
            obj.StableId = record["stable_id"]
            obj.Note = record.get("note", "")
            by_id[record["stable_id"]] = obj

        for assembly_record in structure["assemblies"]:
            assembly = by_id[assembly_record["stable_id"]]
            for entry in assembly_record["placements"]:
                item = by_id.get(entry["item"])
                if item is None:
                    continue
                link = model.place(
                    doc,
                    assembly,
                    item,
                    reference=entry.get("reference", ""),
                    placement=_placement_from(entry["placement"]),
                    side=entry.get("side") or "Top",
                )
                if entry.get("stable_id"):
                    link.StableId = entry["stable_id"]
                link.SpecNote = entry.get("note", "")
            for entry in assembly_record.get("bom_only", []):
                item = by_id.get(entry["item"])
                if item is None:
                    continue
                model.add_bom_only(
                    doc, assembly, item, entry.get("quantity", 1), entry.get("note", "")
                )

        doc.recompute()
        return doc, by_id[structure["root"]], manifest
