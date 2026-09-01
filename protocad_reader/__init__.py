"""Лёгкий читатель файлов ProtoCAD — для встраивания в ПРОТО.

**Не зависит ни от FreeCAD, ни от OCCT.** Только стандартная библиотека и
numpy для мешей. Это принципиально: ПРОТО должен уметь показать модель,
покрутить её, приблизить и снять состав, не таща CAD-ядро.

    from protocad_reader import open_container

    doc = open_container("АБВГ.687281.001.prcadAsm")
    doc.designation            # обозначение
    doc.bom()                  # строки спецификации
    mesh = doc.preview()       # вершины, нормали, рёбра для отрисовки
    doc.resolve(mesh_id=17)    # что за тело под курсором

Точная геометрия (``geometry/*.brp``) читателем не разбирается — она нужна
редактору, а не просмотрщику. При необходимости её можно извлечь методом
``extract_geometry`` и передать тому, кто умеет её читать.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

SUPPORTED_SCHEMA = 1
EXTENSIONS = (".prcadPart", ".prcadAsm", ".prcadDraw")


class ReaderError(RuntimeError):
    """Файл не является контейнером ProtoCAD или повреждён."""


class SchemaTooNew(ReaderError):
    """Схема новее той, что понимает этот читатель."""


@dataclass
class Preview:
    """Данные для отрисовки: треугольники и рёбра."""

    positions: object
    normals: object
    ids: object
    edge_positions: object
    edge_ids: object
    stats: dict = field(default_factory=dict)
    id_to_object: dict = field(default_factory=dict)

    @property
    def triangle_count(self) -> int:
        return len(self.positions) // 3

    @property
    def edge_count(self) -> int:
        return len(self.edge_positions) // 2


class Container:
    """Открытый файл ProtoCAD."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.is_file():
            raise ReaderError(f"файл не найден: {self.path}")
        try:
            self._zip = zipfile.ZipFile(self.path)
        except zipfile.BadZipFile as error:
            raise ReaderError(f"не контейнер ProtoCAD: {error}") from error

        names = set(self._zip.namelist())
        if "manifest.json" not in names:
            raise ReaderError("в контейнере нет manifest.json")
        self.manifest = json.loads(self._zip.read("manifest.json").decode("utf-8"))
        if self.manifest.get("format") != "ProtoCAD":
            raise ReaderError(f"чужой формат: {self.manifest.get('format')!r}")

        version = int(self.manifest.get("schema_version", 0))
        if version > SUPPORTED_SCHEMA:
            # Читатель в ПРОТО живёт дольше, чем версия редактора, поэтому
            # отказ должен быть внятным, а не разбором по частям.
            raise SchemaTooNew(
                f"схема версии {version}, читатель понимает до {SUPPORTED_SCHEMA}"
            )
        self.schema_version = version
        self._structure = None
        self._bom = None

    # --- метаданные ---

    @property
    def designation(self) -> str:
        return self.manifest.get("designation", "")

    @property
    def name(self) -> str:
        return self.manifest.get("name", "")

    @property
    def kind(self) -> str:
        return self.manifest.get("kind", "")

    @property
    def stable_id(self) -> str:
        return self.manifest.get("stable_id", "")

    # --- содержимое ---

    def structure(self) -> dict:
        if self._structure is None:
            self._structure = json.loads(
                self._zip.read("structure.json").decode("utf-8")
            )
        return self._structure

    def bom(self) -> list[dict]:
        """Строки спецификации. Считаны, а не пересчитаны."""
        if self._bom is None:
            try:
                self._bom = json.loads(self._zip.read("bom.json").decode("utf-8"))
            except KeyError:
                self._bom = []
        return self._bom

    def items(self) -> dict[str, dict]:
        return {record["stable_id"]: record for record in self.structure()["items"]}

    def tree(self) -> dict:
        """Дерево состава: изделие, его вхождения, вложенные сборки."""
        items = self.items()
        assemblies = {
            record["stable_id"]: record for record in self.structure()["assemblies"]
        }

        def node(stable_id: str, depth: int = 0) -> dict:
            record = dict(items.get(stable_id, {"stable_id": stable_id}))
            record["depth"] = depth
            children = []
            for entry in assemblies.get(stable_id, {}).get("placements", []):
                children.append(
                    {
                        "occurrence": entry.get("stable_id", ""),
                        "reference": entry.get("reference", ""),
                        "side": entry.get("side", ""),
                        "item": node(entry["item"], depth + 1),
                    }
                )
            record["children"] = children
            return record

        return node(self.structure()["root"])

    def preview(self) -> Preview | None:
        """Меш и рёбра для отрисовки. None — если производных в файле нет."""
        name = (self.manifest.get("contents") or {}).get("preview")
        if not name or name not in self._zip.namelist():
            return None
        import io

        import numpy as np

        with self._zip.open(name) as handle:
            data = np.load(io.BytesIO(handle.read()), allow_pickle=False)
        meta = {}
        if "meta" in data:
            meta = json.loads(bytes(data["meta"]).decode("utf-8"))
        empty_v = np.zeros((0, 3), np.float32)
        empty_i = np.zeros(0, np.uint32)
        return Preview(
            positions=data["positions"] if "positions" in data else empty_v,
            normals=data["normals"] if "normals" in data else empty_v,
            ids=data["ids"] if "ids" in data else empty_i,
            edge_positions=data["edge_positions"] if "edge_positions" in data else empty_v,
            edge_ids=data["edge_ids"] if "edge_ids" in data else empty_i,
            stats=meta.get("stats", {}),
            id_to_object={int(k): v for k, v in meta.get("id_to_object", {}).items()},
        )

    def drawings(self) -> list[str]:
        return list((self.manifest.get("contents") or {}).get("drawings") or [])

    def read_drawing(self, name: str) -> bytes:
        return self._zip.read(name)

    def proto_links(self) -> dict[str, str]:
        """Соответствие изделий записям ПРОТО."""
        try:
            return json.loads(self._zip.read("meta/links.json").decode("utf-8"))
        except KeyError:
            return {}

    def resolve(self, mesh_id: int) -> dict:
        """Что за тело под курсором: вхождение, изделие, запись в ПРОТО.

        Это и есть адресация, ради которой стабильные идентификаторы
        протянуты во все слои: пикнули пиксель — получили конкретный ЭРИ.
        """
        preview = self.preview()
        if preview is None:
            return {}
        entry = preview.id_to_object.get(mesh_id)
        if entry is None:
            return {}

        # Новый формат несёт полную адресацию сразу; старый — только подпись,
        # и её приходится искать по составу.
        if isinstance(entry, dict):
            return {
                "label": entry.get("label", ""),
                "reference": entry.get("label", ""),
                "occurrence": entry.get("occurrence", ""),
                "item_stable_id": entry.get("item", ""),
                "designation": entry.get("designation", ""),
                "name": entry.get("name", ""),
                "proto_id": entry.get("proto_id", ""),
            }

        label = str(entry)
        items = self.items()
        for assembly in self.structure()["assemblies"]:
            for placement in assembly["placements"]:
                if placement.get("reference") and placement["reference"] == label:
                    item = items.get(placement["item"], {})
                    return {
                        "label": label,
                        "occurrence": placement.get("stable_id", ""),
                        "reference": placement.get("reference", ""),
                        "item_stable_id": placement["item"],
                        "designation": item.get("designation", ""),
                        "name": item.get("name", ""),
                        "proto_id": item.get("proto_id", ""),
                    }
        return {"label": label}

    def mesh_labels(self) -> dict[int, str]:
        """Подписи тел по идентификатору — для связи выбора с деревом."""
        preview = self.preview()
        if preview is None:
            return {}
        return {
            key: (value.get("label", "") if isinstance(value, dict) else str(value))
            for key, value in preview.id_to_object.items()
        }

    def extract_geometry(self, stable_id: str, target: str | Path) -> Path | None:
        """Достать точную геометрию B-Rep. Читателем не разбирается."""
        record = self.items().get(stable_id) or {}
        name = record.get("geometry")
        if not name:
            return None
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self._zip.read(name))
        return target

    def close(self) -> None:
        self._zip.close()

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        self.close()


def open_container(path: str | Path) -> Container:
    return Container(path)
