"""Каталог резьб: собственная база ProtoCAD, отдельная от кода.

По `docs/09_HOLES.md`, §16.1. Данные лежат в `catalogs/*.yaml` вместе с
указанием источника: документ, редакция, класс данных, формулы. Без
provenance запись — это число неизвестного происхождения, и проверить его
потом нечем.

Три правила, которые здесь важнее удобства:

* **§19: номинальный диаметр резьбы — НЕ диаметр выреза.** Под М6 сверлят
  не 6. Каталог даёт внутренний диаметр резьбы (`minor_diameter_mm`), а
  диаметр подготовительного отверстия — отдельный технологический слой,
  которого в присланном источнике нет. Он и не выдумывается: спросить
  его — значит получить отказ, а не догадку.
* **§18: правки человека хранятся ОТДЕЛЬНО** от каталожных значений и при
  смене стандарта не переносятся.
* **§34/H-034: пропавший каталог — предупреждение, а не подмена.**
  Сохранённые числа остаются читаемыми, но другой стандарт вместо
  потерянного не подставляется.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field

HERE = pathlib.Path(__file__).resolve().parent / "catalogs"


class CatalogError(Exception):
    """Каталог не отвечает. Несёт код диагностики."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class ThreadPitch:
    value_mm: float
    preferred: bool = False
    series: str = ""
    pitch_diameter_mm: float | None = None
    minor_diameter_mm: float | None = None
    root_diameter_mm: float | None = None


@dataclass
class ThreadSize:
    designation: str
    nominal_major_diameter_mm: float
    series: str = ""
    pitches: list = field(default_factory=list)

    def pitch(self, value_mm: float | None = None) -> ThreadPitch:
        """Шаг по числу. Без числа — предпочтительный.

        Ближайший НЕ подбирается (§52): шаг, которого в этом семействе
        нет, — это ошибка выбора, а не повод округлить. Молча взятый
        соседний шаг даёт резьбу, которой никто не заказывал.
        """
        if value_mm is None:
            for item in self.pitches:
                if item.preferred:
                    return item
            if self.pitches:
                return self.pitches[0]
            raise CatalogError("HOLE_THREAD_CATALOG_ENTRY_MISSING",
                               f"{self.designation}: шагов не записано")
        for item in self.pitches:
            if abs(item.value_mm - float(value_mm)) < 1e-9:
                return item
        allowed = ", ".join(f"{item.value_mm:g}" for item in self.pitches)
        raise CatalogError(
            "HOLE_THREAD_CATALOG_ENTRY_MISSING",
            f"{self.designation}: шага {float(value_mm):g} в этом семействе "
            f"нет; есть {allowed}")


@dataclass
class ThreadFamily:
    """Одно семейство резьб: стандарт и его размеры."""

    catalog_id: str
    display_name: str = ""
    standard: dict = field(default_factory=dict)
    data_revision: int = 1
    tolerance_classes: list = field(default_factory=list)
    sizes: list = field(default_factory=list)

    def size(self, designation: str) -> ThreadSize:
        for item in self.sizes:
            if item.designation == designation:
                return item
        raise CatalogError(
            "HOLE_THREAD_CATALOG_ENTRY_MISSING",
            f"{self.catalog_id}: размера {designation!r} в каталоге нет")

    def first_size(self) -> ThreadSize:
        if not self.sizes:
            raise CatalogError("HOLE_THREAD_CATALOG_ENTRY_MISSING",
                               f"{self.catalog_id}: каталог пуст")
        return self.sizes[0]

    def designations(self) -> list:
        return [item.designation for item in self.sizes]


class ThreadCatalog:
    """Все семейства. Читается один раз и держится в памяти."""

    def __init__(self, folder: pathlib.Path | None = None):
        self.folder = pathlib.Path(folder or HERE)
        self._families: dict = {}
        self._loaded = False

    def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        for path in sorted(self.folder.glob("*.yaml")):
            if path.name == "index.yaml":
                continue
            family = _family_from(_parse(path.read_text(encoding="utf-8")))
            if family is not None:
                self._families[family.catalog_id] = family

    def families(self) -> list:
        self._load()
        return [self._families[key] for key in sorted(self._families)]

    def family(self, catalog_id: str) -> ThreadFamily:
        self._load()
        found = self._families.get(catalog_id)
        if found is None:
            # Похожий каталог НЕ подставляется (§H-034): сохранённые числа
            # остаются читаемыми, а стандарт объявляется потерянным.
            raise CatalogError(
                "HOLE_THREAD_CATALOG_ENTRY_MISSING",
                f"каталога {catalog_id!r} нет; сохранённые размеры остаются "
                f"как есть, другой стандарт вместо него не подставляется")
        return found

    def resolve(self, thread) -> dict:
        """Каталожные значения для резьбы. Правки человека НЕ применяются.

        Возвращает то, что говорит стандарт. Что из этого человек
        переопределил — знает сама резьба (`overrides`), и смешивать одно
        с другим нельзя: иначе не отличить норму от чужого решения.
        """
        family = self.family(thread.catalog_id)
        size = family.size(thread.size_id)
        pitch = size.pitch(thread.pitch_mm)
        return {
            "catalog_id": family.catalog_id,
            "standard": dict(family.standard),
            "designation": size.designation,
            "nominal_major_diameter_mm": size.nominal_major_diameter_mm,
            "pitch_mm": pitch.value_mm,
            "pitch_diameter_mm": pitch.pitch_diameter_mm,
            "minor_diameter_mm": pitch.minor_diameter_mm,
            "tolerance_classes": list(family.tolerance_classes),
        }

    def prepared_bore_mm(self, thread) -> float:
        """Диаметр подготовительного отверстия. ВСЕГДА отказ, пока нет данных.

        §19 и §52 запрещают брать номинальный диаметр резьбы как диаметр
        выреза, а технологического каталога с диаметрами сверления в
        присланном источнике нет — он прямо назван отдельным слоем.
        Догадка здесь означала бы отверстие, которого никто не заказывал,
        поэтому её нет: диаметр задаёт человек либо технологический пресет.
        """
        raise CatalogError(
            "SPEC_GAP",
            "диаметра подготовительного отверстия в каталоге нет: он "
            "относится к технологическому слою. Задайте диаметр основного "
            "канала сами либо примените технологический пресет")


def _family_from(data: dict) -> ThreadFamily | None:
    if not data.get("catalog_id"):
        return None
    internal = data.get("internal_thread") or {}
    sizes = []
    for item in data.get("sizes") or ():
        pitches = []
        for entry in item.get("pitches") or ():
            basic = entry.get("basic_dimensions") or {}
            pitches.append(ThreadPitch(
                value_mm=float(entry.get("value_mm", 0.0)),
                preferred=bool(entry.get("preferred")),
                series=str(entry.get("series") or ""),
                pitch_diameter_mm=_maybe(basic.get("pitch_diameter_mm")),
                minor_diameter_mm=_maybe(basic.get("minor_diameter_mm")),
                root_diameter_mm=_maybe(basic.get("root_diameter_mm")),
            ))
        sizes.append(ThreadSize(
            designation=str(item.get("designation") or ""),
            nominal_major_diameter_mm=float(
                item.get("nominal_major_diameter_mm", 0.0)),
            series=str(item.get("series") or ""),
            pitches=pitches))
    return ThreadFamily(
        catalog_id=str(data["catalog_id"]),
        display_name=str(data.get("display_name") or ""),
        standard=dict(data.get("standard") or {}),
        data_revision=int(data.get("data_revision", 1)),
        tolerance_classes=list(internal.get("tolerance_classes") or ()),
        sizes=sizes)


def _maybe(value):
    return None if value is None else float(value)


# --- чтение YAML -----------------------------------------------------------
#
# Свой разбор, а не сторонняя библиотека: читаются ТОЛЬКО собственные
# файлы каталога, которые пишет `scripts/build_thread_catalog.py`, и их
# подмножество YAML заведомо узкое. Заводить зависимость ради него значило
# бы тащить в поставку разбор произвольного YAML со всеми его правилами.


def _parse(text: str):
    lines = [line for line in text.splitlines()
             if line.strip() and not line.lstrip().startswith("#")]
    value, _ = _block(lines, 0, 0)
    return value


def _block(lines, index: int, indent: int):
    if index >= len(lines):
        return None, index
    if lines[index].strip().startswith("- "):
        return _sequence(lines, index, indent)
    return _mapping(lines, index, indent)


def _mapping(lines, index: int, indent: int):
    found = {}
    while index < len(lines):
        line = lines[index]
        depth = len(line) - len(line.lstrip())
        if depth < indent or line.strip().startswith("- "):
            break
        key, _, tail = line.strip().partition(":")
        tail = tail.strip()
        if tail:
            found[key] = _value(tail)
            index += 1
            continue
        index += 1
        if index < len(lines):
            deeper = len(lines[index]) - len(lines[index].lstrip())
            if deeper > depth:
                found[key], index = _block(lines, index, deeper)
                continue
        found[key] = None
    return found, index


def _sequence(lines, index: int, indent: int):
    found = []
    while index < len(lines):
        line = lines[index]
        depth = len(line) - len(line.lstrip())
        if depth < indent or not line.strip().startswith("- "):
            break
        head = line.strip()[2:]
        if ":" in head:
            piece = {}
            key, _, tail = head.partition(":")
            piece[key.strip()] = _value(tail.strip()) if tail.strip() else None
            index += 1
            if index < len(lines):
                deeper = len(lines[index]) - len(lines[index].lstrip())
                if deeper > depth:
                    rest, index = _mapping(lines, index, deeper)
                    piece.update(rest)
            found.append(piece)
        else:
            found.append(_value(head))
            index += 1
    return found, index


def _value(text: str):
    text = text.strip()
    if text in ("null", "~", ""):
        return None
    if text == "true":
        return True
    if text == "false":
        return False
    if text.startswith('"') and text.endswith('"'):
        return text[1:-1]
    try:
        return int(text)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return text


#: Общий каталог. Читается лениво, при первом обращении.
CATALOG = ThreadCatalog()
