"""Объектная модель ProtoCAD: изделие, состав, сборочная единица.

Структура сборки отражает структуру документации по ЕСКД. Каждый узел дерева —
документируемое изделие со своим обозначением и своим комплектом КД, а состав
сборочной единицы и есть её спецификация. Правило одно на всех уровнях:

    Прибор ─┬─ Подсборка ─┬─ Рамка (деталь)
            │             └─ Плата ─┬─ Плата печатная (деталь)
            │                       ├─ ЭРИ (покупные)
            │                       └─ Планка, прокладка (детали)
            └─ …

Решения, вытекающие из замеров исследовательской фазы (см. 03_DECISION_RECORD):

* геометрия изделия — одно ``Part::FeaturePython`` со слитой формой, а НЕ
  ``App::Part``: последний несёт Origin, который ``App::Link`` размножает на
  каждый экземпляр и роняет отрисовку впятеро;
* размещение — ``App::Link`` с динамическими свойствами, без Python-прокси:
  ядровые типы восстанавливаются без импорта пользовательского кода.
"""

from __future__ import annotations

import uuid

import FreeCAD as App
import Part

GROUP = "ProtoCAD"

# Виды изделий по ГОСТ 2.101. Определяют и раздел спецификации.
KIND_DETAIL = "Деталь"
KIND_ASSEMBLY = "Сборочная единица"
KIND_COMPLEX = "Комплекс"
KIND_KIT = "Комплект"
KIND_PURCHASED = "Прочие изделия"
KIND_STANDARD = "Стандартные изделия"
KIND_MATERIAL = "Материалы"

KINDS = (
    KIND_COMPLEX,
    KIND_ASSEMBLY,
    KIND_DETAIL,
    KIND_STANDARD,
    KIND_PURCHASED,
    KIND_MATERIAL,
)

# Порядок разделов спецификации по ГОСТ 2.106.
SECTION_ORDER = {
    KIND_COMPLEX: 1,
    KIND_ASSEMBLY: 2,
    KIND_DETAIL: 3,
    KIND_STANDARD: 4,
    KIND_PURCHASED: 5,
    KIND_MATERIAL: 6,
}


class CycleError(RuntimeError):
    """Изделие включено в собственный состав — прямо или через вложенность."""


class _Proxy:
    """Общий прокси. FreeCAD 1.x зовёт dumps/loads, старые версии — __getstate__."""

    Type = "ProtoCAD::Base"

    def dumps(self):
        return self.Type

    def loads(self, state):
        if state:
            self.Type = state
        return None

    __getstate__ = dumps
    __setstate__ = loads

    def onDocumentRestored(self, obj):
        obj.Proxy = self


def _add(obj, prop_type: str, name: str, tooltip: str, group: str = GROUP) -> None:
    """Идемпотентное добавление свойства: при восстановлении оно уже есть."""
    if name not in obj.PropertiesList:
        obj.addProperty(prop_type, name, group, tooltip)


class Item(_Proxy):
    """Изделие: деталь, покупное, стандартное — всё, что имеет обозначение.

    Собственная геометрия хранится одной формой. Многотельность сохраняется
    внутри compound, но отдельными объектами документа тела не становятся:
    именно это давало пятикратную разницу в отрисовке.
    """

    Type = "ProtoCAD::Item"

    def __init__(self, obj, kind: str = KIND_DETAIL):
        obj.Proxy = self
        _add(obj, "App::PropertyString", "StableId", "Стабильный идентификатор изделия")
        _add(obj, "App::PropertyString", "Designation", "Обозначение (децимальный номер)")
        _add(obj, "App::PropertyString", "ItemName", "Наименование по спецификации")
        _add(obj, "App::PropertyEnumeration", "Kind", "Вид изделия по ГОСТ 2.101")
        _add(obj, "App::PropertyString", "ProtoItemId", "Идентификатор записи в ПРОТО")
        _add(obj, "App::PropertyString", "Note", "Примечание для спецификации")
        obj.Kind = list(KINDS)
        obj.Kind = kind
        if not obj.StableId:
            obj.StableId = f"item-{uuid.uuid4()}"

    def execute(self, obj):
        """Форма изделия задаётся снаружи и не пересчитывается сама."""
        if obj.Shape.isNull():
            obj.Shape = Part.Shape()


class Assembly(Item):
    """Сборочная единица. Её состав — это её спецификация.

    Состав хранится двумя списками, потому что у вхождения две проекции:

    * ``Placements`` — геометрические размещения (``App::Link``). Триста
      резисторов дают триста размещений с R1…R300;
    * ``BomOnly`` — вхождения без геометрии (материалы, изделия без модели).

    Строка спецификации выводится группировкой, а не хранится отдельно: иначе
    количество и фактический состав неизбежно разойдутся.
    """

    Type = "ProtoCAD::Assembly"

    def __init__(self, obj):
        super().__init__(obj, KIND_ASSEMBLY)
        _add(obj, "App::PropertyLinkList", "Placements", "Геометрические размещения")
        _add(obj, "App::PropertyLinkList", "BomOnly", "Вхождения без геометрии")
        _add(obj, "App::PropertyLink", "Merged", "Слитое представление для вышестоящих сборок")
        _add(obj, "App::PropertyBool", "MergedStale", "Слитое представление устарело")
        obj.Kind = KIND_ASSEMBLY

    def execute(self, obj):
        shapes = []
        for link in obj.Placements:
            shape = getattr(link, "Shape", None)
            if shape is not None and not shape.isNull():
                shapes.append(shape)
        obj.Shape = Part.makeCompound(shapes) if shapes else Part.Shape()

    def onChanged(self, obj, prop):
        # Правка состава делает слитое представление несвежим. Пересборка —
        # отдельное явное действие: она дорогая и не должна идти на каждый чих.
        if prop in ("Placements", "BomOnly") and "Merged" in obj.PropertiesList:
            if obj.Merged is not None:
                obj.MergedStale = True


def _new(doc, name: str, kind: str):
    obj = doc.addObject("Part::FeaturePython", name)
    if kind == KIND_ASSEMBLY:
        Assembly(obj)
    else:
        Item(obj, kind)
    return obj


def make_item(
    doc,
    designation: str,
    name: str,
    kind: str = KIND_DETAIL,
    shape=None,
    proto_id: str = "",
):
    """Создать изделие. ``shape`` — готовая форма или список тел."""
    obj = _new(doc, "Item", kind)
    obj.Designation = designation
    obj.ItemName = name
    obj.ProtoItemId = proto_id
    obj.Label = f"{designation} {name}".strip()
    if shape is not None:
        obj.Shape = (
            Part.makeCompound(list(shape)) if isinstance(shape, (list, tuple)) else shape
        )
    return obj


def make_assembly(doc, designation: str, name: str):
    obj = _new(doc, "Assembly", KIND_ASSEMBLY)
    obj.Designation = designation
    obj.ItemName = name
    obj.Label = f"{designation} {name}".strip()
    return obj


def _assert_no_cycle(assembly, item) -> None:
    """Изделие не может входить в собственный состав на любой глубине."""
    stack = [item]
    seen = set()
    while stack:
        current = stack.pop()
        if current is None or current.Name in seen:
            continue
        seen.add(current.Name)
        if current.Name == assembly.Name:
            raise CycleError(
                f"{item.Label!r} уже содержит {assembly.Label!r} — цикл в составе"
            )
        for link in getattr(current, "Placements", []) or []:
            stack.append(getattr(link, "LinkedObject", None))
        for entry in getattr(current, "BomOnly", []) or []:
            stack.append(getattr(entry, "LinkedObject", None))


def place(
    doc,
    assembly,
    item,
    reference: str = "",
    placement: App.Placement | None = None,
    side: str = "Top",
):
    """Разместить изделие в составе сборочной единицы.

    Возвращает ``App::Link`` — ядровой тип, переживающий открытие документа без
    импорта пользовательского кода.
    """
    _assert_no_cycle(assembly, item)
    link = doc.addObject("App::Link", "Placement")
    link.LinkedObject = item
    _add(link, "App::PropertyString", "StableId", "Идентификатор вхождения")
    _add(link, "App::PropertyString", "Reference", "Позиционное обозначение")
    _add(link, "App::PropertyEnumeration", "Side", "Сторона платы")
    _add(link, "App::PropertyString", "SpecNote", "Примечание строки спецификации")
    link.Side = ["Top", "Bottom"]
    link.Side = side
    link.StableId = f"occ-{uuid.uuid4()}"
    link.Reference = reference
    link.Label = reference or item.Label
    if placement is not None:
        link.Placement = placement
    assembly.Placements = list(assembly.Placements) + [link]
    return link


def add_bom_only(doc, assembly, item, quantity: int = 1, note: str = ""):
    """Вхождение без геометрии: материал, изделие без модели."""
    _assert_no_cycle(assembly, item)
    entry = doc.addObject("App::Link", "BomEntry")
    entry.LinkedObject = item
    _add(entry, "App::PropertyInteger", "Quantity", "Количество в спецификации")
    _add(entry, "App::PropertyString", "SpecNote", "Примечание строки спецификации")
    entry.Quantity = quantity
    entry.SpecNote = note
    entry.Label = item.Label
    if hasattr(entry, "ViewObject") and entry.ViewObject is not None:
        entry.ViewObject.Visibility = False
    assembly.BomOnly = list(assembly.BomOnly) + [entry]
    return entry


def bom(assembly) -> list[dict]:
    """Состав сборочной единицы как строки спецификации.

    Количество считается группировкой размещений: хранить его отдельно значит
    гарантированно разойтись с фактическим составом.
    """
    rows: dict[str, dict] = {}

    def _row(item, quantity: int, reference: str = "", note: str = ""):
        key = item.StableId
        if key not in rows:
            rows[key] = {
                "designation": item.Designation,
                "name": item.ItemName,
                "kind": item.Kind,
                "stable_id": item.StableId,
                "quantity": 0,
                "references": [],
                "note": note or getattr(item, "Note", ""),
            }
        rows[key]["quantity"] += quantity
        if reference:
            rows[key]["references"].append(reference)

    for link in assembly.Placements:
        item = link.LinkedObject
        if item is None:
            continue
        _row(item, 1, getattr(link, "Reference", ""), getattr(link, "SpecNote", ""))
    for entry in assembly.BomOnly:
        item = entry.LinkedObject
        if item is None:
            continue
        _row(item, int(getattr(entry, "Quantity", 1)), "", getattr(entry, "SpecNote", ""))

    result = list(rows.values())
    for row in result:
        row["references"].sort()
    result.sort(
        key=lambda row: (SECTION_ORDER.get(row["kind"], 99), row["designation"], row["name"])
    )
    return result


def walk(assembly, depth: int = 0):
    """Обход состава вглубь: (изделие, уровень вложенности)."""
    yield assembly, depth
    for link in getattr(assembly, "Placements", []) or []:
        item = link.LinkedObject
        if item is None:
            continue
        if item.Kind == KIND_ASSEMBLY:
            yield from walk(item, depth + 1)
        else:
            yield item, depth + 1
