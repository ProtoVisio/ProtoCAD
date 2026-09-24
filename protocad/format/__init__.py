"""Формат файлов ProtoCAD: запись и чтение. Без FreeCAD.

Контейнер — ZIP, схема своя, расширение по виду изделия ЕСКД:

* ``.prcadPart`` — деталь и прочие несборочные изделия;
* ``.prcadAsm``  — сборочная единица;
* ``.prcadDraw`` — чертёж.

```
manifest.json     версия схемы, вид, обозначение, состав контейнера
structure.json    изделия, состав, спецификация
bom.json          готовые строки спецификации
geometry/*.brp    точная геометрия B-Rep
preview/scene.npz тесселяция и рёбра для просмотра без CAD-ядра
meta/links.json   связи с записями ПРОТО
drawings/*.svg    листы чертежей
```

Ключевое решение: **производные для просмотра лежат внутри файла**. ПРОТО
открывает контейнер и читает ``preview/`` и ``bom.json``, не имея ни ядра
геометрии, ни решателя. Читатель для этого — ``protocad_reader``.
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .. import kernel
from ..model import (
    KIND_ASSEMBLY,
    KIND_COMPLEX,
    KIND_KIT,
    Assembly,
    BomEntry,
    Item,
    Occurrence,
)

SCHEMA_VERSION = 1
PRODUCER = "ProtoCAD"

ASSEMBLY_EXTENSION = ".prcadAsm"
PART_EXTENSION = ".prcadPart"
DRAWING_EXTENSION = ".prcadDraw"
KNOWN_EXTENSIONS = {ASSEMBLY_EXTENSION, PART_EXTENSION, DRAWING_EXTENSION}


class FormatError(RuntimeError):
    """Контейнер не соответствует схеме."""


def extension_for(item: Item) -> str:
    return (
        ASSEMBLY_EXTENSION
        if item.kind in (KIND_ASSEMBLY, KIND_COMPLEX, KIND_KIT)
        else PART_EXTENSION
    )


def _safe(stable_id: str) -> str:
    return stable_id.replace(":", "_").replace("/", "_")


def _target_path(root: Item, path: str | Path) -> Path:
    path = Path(path)
    # Обозначения ЕСКД содержат точки (АБВГ.687281.001), поэтому Path.suffix
    # возвращает ".001" — проверять «расширения нет» бессмысленно.
    if path.suffix not in KNOWN_EXTENSIONS:
        path = path.with_name(path.name + extension_for(root))
    return path


#: Где в контейнере лежит дерево намерений и документ движка.
INTENT_NAME = "intent/tree.json"
ENGINE_NAME = "geometry/engine.FCStd"


def write_document(document, path: str | Path, drawings=None) -> Path:
    """Записать деталь, которую считает движок.

    В контейнер идут ТРИ разные вещи, и путать их нельзя:

    * ``intent/tree.json`` — что человек попросил: эскизы, выбранные
      области, длины. По нему деталь перестраивается;
    * ``geometry/engine.FCStd`` — документ движка с точной геометрией.
      Он же — то, что открывается без пересчёта;
    * ``preview/scene.npz`` — треугольники для просмотра без ядра. Их
      читает ПРОТО, у которого ни движка, ни решателя нет.

    Геометрия не пересчитывается ради записи: берётся то, что движок уже
    построил. Пересчёт «на всякий случай» дал бы второй ответ на вопрос
    «какой формы деталь».
    """
    path = Path(path)
    if path.suffix not in KNOWN_EXTENSIONS:
        path = path.with_name(path.name + PART_EXTENSION)
    path.parent.mkdir(parents=True, exist_ok=True)

    engine_file = path.parent / f"._{_safe(document.designation)}.FCStd"
    saved = document.save(engine_file)
    if not saved.ok:
        raise FormatError(f"движок не сохранил документ: {saved.message}")
    # Движок мог дописать своё расширение — берём путь, который он назвал.
    written = Path(getattr(saved, "path", "") or engine_file)
    if not written.is_file():
        written = engine_file
    if not written.is_file():
        raise FormatError(f"движок сообщил об успехе, но файла нет: {written}")

    manifest = {
        "format": PRODUCER,
        "schema_version": SCHEMA_VERSION,
        "kind": "деталь",
        "designation": document.designation,
        "name": document.name,
        "stable_id": document.designation,
        "created": datetime.now(timezone.utc).isoformat(),
        "producer": f"{PRODUCER} (движок {document.backend.name})",
        "contents": {
            "structure": "structure.json",
            "bom": "bom.json",
            "geometry": [ENGINE_NAME],
            "intent": INTENT_NAME,
            "preview": "preview/scene.npz" if document.mesh else None,
            "drawings": [],
        },
    }
    structure = {
        "root": document.designation,
        "items": [{"stable_id": document.designation,
                   "designation": document.designation,
                   "name": document.name, "kind": "деталь",
                   "geometry": ENGINE_NAME, "intent": INTENT_NAME}],
        "assemblies": [],
    }

    # Пишется во ВРЕМЕННЫЙ файл и переименовывается в конце. Иначе сбой на
    # полпути оставлял контейнер, который открывается, но неполон: у
    # человека на диске лежал файл с геометрией и без дерева намерений —
    # деталь открывалась, а править её было нечем. Полуфабрикат, похожий
    # на готовый файл, хуже отсутствия файла.
    draft = path.with_name(path.name + ".пишется")
    try:
        with zipfile.ZipFile(draft, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(written, ENGINE_NAME)
            archive.writestr(INTENT_NAME, json.dumps(document.to_dict(),
                                                     ensure_ascii=False,
                                                     indent=2))
            if document.mesh is not None:
                archive.writestr("preview/scene.npz", _preview_blob(document))
            for index, drawing in enumerate(drawings or []):
                drawing_path = Path(drawing)
                name = f"drawings/{index:02d}_{drawing_path.name}"
                archive.write(drawing_path, name)
                manifest["contents"]["drawings"].append(name)
            archive.writestr("structure.json",
                             json.dumps(structure, ensure_ascii=False, indent=2))
            archive.writestr("bom.json", json.dumps([], ensure_ascii=False))
            archive.writestr("manifest.json",
                             json.dumps(manifest, ensure_ascii=False, indent=2))
        draft.replace(path)
    except BaseException:
        draft.unlink(missing_ok=True)
        raise
    finally:
        # Выгрузка движка убирается в любом случае: она нужна была только
        # чтобы попасть в контейнер.
        written.unlink(missing_ok=True)
    return path


def read_document(path: str | Path, document) -> dict:
    """Прочитать контейнер в переданный документ. Возвращает манифест.

    Дерево намерений восстанавливается, геометрия ОТКРЫВАЕТСЯ у движка —
    не пересчитывается. Пересчёт остаётся за вызывающим: он знает, надо ли
    ему число прямо сейчас.
    """
    path = Path(path)
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if "manifest.json" not in names:
            raise FormatError(f"{path.name}: нет manifest.json")
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        if INTENT_NAME not in names:
            raise FormatError(
                f"{path.name}: нет дерева намерений — этот контейнер записан "
                f"старым путём, где геометрия лежала формами ядра")
        payload = json.loads(archive.read(INTENT_NAME).decode("utf-8"))
        engine_file = path.parent / f"._{_safe(payload.get('name') or 'деталь')}.FCStd"
        if ENGINE_NAME in names:
            engine_file.write_bytes(archive.read(ENGINE_NAME))

    document.load(payload)
    if engine_file.is_file():
        document.open(engine_file)
        engine_file.unlink(missing_ok=True)
    return manifest


def _preview_blob(document) -> bytes:
    """Треугольники для просмотра без ядра — из сетки движка."""
    mesh = document.mesh
    positions = np.asarray(mesh.positions, np.float32).reshape(-1, 3)
    buffer = io.BytesIO()
    np.savez_compressed(
        buffer,
        positions=positions,
        normals=np.asarray(mesh.normals, np.float32).reshape(-1, 3),
        ids=np.ones(len(positions), np.uint32),
        edge_positions=np.asarray(mesh.edge_positions, np.float32).reshape(-1, 3),
        edge_ids=np.ones(len(mesh.edge_positions) // 3, np.uint32),
        meta=np.frombuffer(
            json.dumps({"stats": {"triangles": len(positions) // 3,
                                  "volume": document.volume},
                        "id_to_object": {"1": {"label": document.name}}},
                       ensure_ascii=False).encode("utf-8"),
            dtype=np.uint8),
    )
    return buffer.getvalue()


def write(root: Item, path: str | Path, preview=None, drawings=None,
          extras=None) -> Path:
    """Записать изделие со всем составом в контейнер.

    ``extras`` — {имя: данные JSON}: то, что знает о составе не сам состав,
    а тот, кто его правит, — сопряжения сборки, происхождение деталей.
    Ложится в ``extras/<имя>.json``; читатель в ПРОТО его пропускает.
    """
    path = _target_path(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)

    items = root.all_items() if isinstance(root, Assembly) else [root]
    geometry_names: dict[str, str] = {}
    feature_data: dict[str, tuple[str, dict]] = {}
    structure = {"root": root.stable_id, "items": [], "assemblies": []}

    for item in items:
        record = {
            "stable_id": item.stable_id,
            "designation": item.designation,
            "name": item.name,
            "kind": item.kind,
            "proto_id": item.proto_id,
            "note": item.note,
        }
        if item.shape is not None and not isinstance(item, Assembly):
            name = f"geometry/{_safe(item.stable_id)}.brp"
            geometry_names[item.stable_id] = name
            record["geometry"] = name
        # История построения пишется вместе с результатом: без неё деталь
        # после открытия нельзя править, и параметрика существует лишь до
        # закрытия окна. Читателю в ПРОТО она не нужна — он её игнорирует.
        # Только у детали на СТАРОМ дереве: у детали на движке `is_parametric`
        # тоже истинно, а дерева `features` нет — её намерения живут в своём
        # файле `.prcadPart`, и запись падала на любой сборке с такой деталью.
        if getattr(item, "is_parametric", False) and item.features is not None:
            name = f"features/{_safe(item.stable_id)}.json"
            feature_data[item.stable_id] = (name, item.features.to_dict())
            record["features"] = name
        structure["items"].append(record)

        if not isinstance(item, Assembly):
            continue
        structure["assemblies"].append(
            {
                "stable_id": item.stable_id,
                "placements": [
                    {
                        "stable_id": occurrence.stable_id,
                        "item": occurrence.item.stable_id,
                        "reference": occurrence.reference,
                        "side": occurrence.side,
                        "note": occurrence.note,
                        "transform": [float(v) for v in occurrence.transform.reshape(-1)],
                    }
                    for occurrence in item.placements
                ],
                "bom_only": [
                    {
                        "stable_id": entry.stable_id,
                        "item": entry.item.stable_id,
                        "quantity": entry.quantity,
                        "note": entry.note,
                    }
                    for entry in item.bom_only
                ],
            }
        )

    manifest = {
        "format": PRODUCER,
        "schema_version": SCHEMA_VERSION,
        "kind": root.kind,
        "designation": root.designation,
        "name": root.name,
        "stable_id": root.stable_id,
        "created": datetime.now(timezone.utc).isoformat(),
        "producer": f"{PRODUCER} (OCCT via OCP)",
        "contents": {
            "structure": "structure.json",
            "bom": "bom.json",
            "geometry": sorted(geometry_names.values()),
            "features": sorted(name for name, _payload in feature_data.values()),
            "preview": "preview/scene.npz" if preview is not None else None,
            "drawings": [],
            "extras": sorted(f"extras/{name}.json" for name in (extras or {})),
        },
    }
    bom_rows = root.bom() if isinstance(root, Assembly) else []

    # Во временный файл и переименование в конце — как у детали: сбой на
    # полпути не должен оставлять контейнер, который открывается, но
    # неполон.
    draft = path.with_name(path.name + ".пишется")
    try:
        with zipfile.ZipFile(draft, "w", zipfile.ZIP_DEFLATED) as archive:
            for item in items:
                name = geometry_names.get(item.stable_id)
                if not name:
                    continue
                # BRepTools пишет только в файл, поэтому через временный путь.
                temporary = path.parent / f"._{_safe(item.stable_id)}.brp"
                kernel.export_brep(item.shape, temporary)
                archive.write(temporary, name)
                temporary.unlink(missing_ok=True)

            for name, payload in feature_data.values():
                archive.writestr(name, json.dumps(payload, ensure_ascii=False, indent=2))

            for name, payload in (extras or {}).items():
                archive.writestr(f"extras/{name}.json",
                                 json.dumps(payload, ensure_ascii=False, indent=2))

            if preview is not None:
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
                            {"stats": preview.stats, "id_to_object": preview.id_to_object},
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
                    {item.stable_id: item.proto_id for item in items if item.proto_id},
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            archive.writestr(
                "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2)
            )
        draft.replace(path)
    except BaseException:
        draft.unlink(missing_ok=True)
        raise
    return path


def read_extra(path: str | Path, name: str):
    """Данные ``extras/<имя>.json`` контейнера. ``None`` — их там нет."""
    with zipfile.ZipFile(Path(path)) as archive:
        entry = f"extras/{name}.json"
        if entry not in archive.namelist():
            return None
        return json.loads(archive.read(entry).decode("utf-8"))


def read(path: str | Path) -> tuple[Item, dict]:
    """Полное чтение с восстановлением точной геометрии."""
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

        by_id: dict[str, Item] = {}
        for record in structure["items"]:
            shape = None
            if record.get("geometry"):
                temporary = path.parent / f"._read_{_safe(record['stable_id'])}.brp"
                temporary.write_bytes(archive.read(record["geometry"]))
                shape = kernel.import_brep(temporary)
                temporary.unlink(missing_ok=True)
            features = None
            if record.get("features"):
                from ..feature import FeatureTree

                features = FeatureTree.from_dict(
                    json.loads(archive.read(record["features"]).decode("utf-8"))
                )
            common = {
                "designation": record["designation"],
                "name": record["name"],
                "kind": record["kind"],
                "proto_id": record.get("proto_id", ""),
                "note": record.get("note", ""),
                "stable_id": record["stable_id"],
            }
            by_id[record["stable_id"]] = (
                Assembly(**common)
                if record["kind"] in (KIND_ASSEMBLY, KIND_COMPLEX, KIND_KIT)
                else Item(shape=shape, features=features, **common)
            )

        for assembly_record in structure["assemblies"]:
            assembly = by_id[assembly_record["stable_id"]]
            for entry in assembly_record["placements"]:
                item = by_id.get(entry["item"])
                if item is None:
                    continue
                assembly.placements.append(
                    Occurrence(
                        item=item,
                        reference=entry.get("reference", ""),
                        side=entry.get("side") or "Top",
                        transform=np.asarray(entry["transform"], float).reshape(4, 4),
                        note=entry.get("note", ""),
                        stable_id=entry.get("stable_id", ""),
                    )
                )
            for entry in assembly_record.get("bom_only", []):
                item = by_id.get(entry["item"])
                if item is None:
                    continue
                assembly.bom_only.append(
                    BomEntry(
                        item=item,
                        quantity=int(entry.get("quantity", 1)),
                        note=entry.get("note", ""),
                        stable_id=entry.get("stable_id", ""),
                    )
                )
        return by_id[structure["root"]], manifest
