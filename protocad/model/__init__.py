"""Объектная модель ProtoCAD: изделие, состав, сборочная единица.

Структура сборки отражает структуру документации по ЕСКД. Каждый узел дерева —
документируемое изделие со своим обозначением и своим комплектом КД, а состав
сборочной единицы и есть его спецификация:

    Прибор ─┬─ Подсборка ─┬─ Рамка (деталь)
            │             └─ Плата ─┬─ Плата печатная (деталь)
            │                       ├─ ЭРИ (покупные)
            │                       └─ Планка, прокладка (детали)
            └─ …

**Ни FreeCAD, ни его документной модели здесь нет.** Изделия — обычные
структуры Python, геометрия — формы OCCT, размещение — матрица 4×4. Прежняя
версия жила на ``App::Link`` и ``Part::FeaturePython`` с их сериализацией
прокси; после Spike 4 это не нужно, а заодно исчезли и связанные ловушки:
запрет восстановления прокси вне установленного addon, разное поведение в
консоли и GUI, дописывание номеров к совпадающим именам.

Вхождение имеет две проекции, и обе нужны: 300 одинаковых резисторов — это
ОДНА строка спецификации с количеством 300 и ТРИСТА геометрических
размещений со своими R1…R300.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import numpy as np

# Виды изделий по ГОСТ 2.101; они же определяют раздел спецификации.
KIND_COMPLEX = "Комплекс"
KIND_ASSEMBLY = "Сборочная единица"
KIND_DETAIL = "Деталь"
KIND_STANDARD = "Стандартные изделия"
KIND_PURCHASED = "Прочие изделия"
KIND_MATERIAL = "Материалы"
KIND_KIT = "Комплект"

KINDS = (
    KIND_COMPLEX,
    KIND_ASSEMBLY,
    KIND_DETAIL,
    KIND_STANDARD,
    KIND_PURCHASED,
    KIND_MATERIAL,
    KIND_KIT,
)

# Порядок разделов спецификации по ГОСТ 2.106.
SECTION_ORDER = {
    KIND_COMPLEX: 1,
    KIND_ASSEMBLY: 2,
    KIND_DETAIL: 3,
    KIND_STANDARD: 4,
    KIND_PURCHASED: 5,
    KIND_MATERIAL: 6,
    KIND_KIT: 7,
}

SIDE_TOP = "Top"
SIDE_BOTTOM = "Bottom"


class ModelError(RuntimeError):
    """Нарушение правил построения состава."""


class CycleError(ModelError):
    """Изделие включено в собственный состав — прямо или через вложенность."""


def identity() -> np.ndarray:
    return np.eye(4, dtype=np.float64)


def placement(
    x: float = 0.0, y: float = 0.0, z: float = 0.0, angle_deg: float = 0.0
) -> np.ndarray:
    """Размещение: перенос плюс поворот вокруг Z — типовой случай для плат."""
    radians = np.radians(angle_deg)
    cos, sin = np.cos(radians), np.sin(radians)
    matrix = identity()
    matrix[:3, :3] = ((cos, -sin, 0.0), (sin, cos, 0.0), (0.0, 0.0, 1.0))
    matrix[:3, 3] = (x, y, z)
    return matrix


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4()}"


@dataclass
class Item:
    """Изделие: деталь, покупное, стандартное — всё, что имеет обозначение.

    Геометрия хранится ОДНОЙ формой. Многотельность живёт внутри compound,
    но отдельными объектами тела не становятся: замеры Spike 1 показали, что
    именно дробление на объекты роняло отрисовку впятеро.

    ``features`` — необязательное дерево построения. Если оно есть, форма
    вычисляется из него, а ``shape`` хранит последний удавшийся результат:
    так деталь остаётся показуемой, даже когда правка сломала операцию.
    Покупные изделия дерева не имеют — их модель приходит извне.
    """

    designation: str
    name: str
    kind: str = KIND_DETAIL
    shape: object | None = None
    proto_id: str = ""
    note: str = ""
    features: object | None = None
    #: Деталь, построенная ДВИЖКОМ (`protocad.document.Document`). Это
    #: рабочий путь; `features` — прежнее дерево на ядре в нашем процессе,
    #: и держать их порознь надо ровно потому, что геометрию они считают
    #: РАЗНЫМ кодом. Смешать их в одном поле значило бы гадать, чья форма
    #: сейчас в `shape`.
    document: object | None = None
    stable_id: str = field(default_factory=lambda: _new_id("item"))

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ModelError(f"неизвестный вид изделия: {self.kind!r}")

    @property
    def label(self) -> str:
        return f"{self.designation} {self.name}".strip()

    @property
    def is_assembly(self) -> bool:
        return self.kind in (KIND_ASSEMBLY, KIND_COMPLEX, KIND_KIT)

    @property
    def is_parametric(self) -> bool:
        if self.document is not None:
            return bool(getattr(self.document, "operations", ()))
        return self.features is not None and len(self.features) > 0

    @property
    def volume(self) -> float:
        """Объём изделия. Ноль — геометрии нет."""
        if self.document is not None:
            return float(getattr(self.document, "volume", 0.0) or 0.0)
        shape = self.shape
        return float(getattr(shape, "Volume", 0.0) or 0.0) if shape else 0.0

    def engine_mesh(self):
        """Сетка от движка: ``(точки, нормали, номера граней)`` или ``None``.

        Числа приходят плоскими списками, здесь они только раскладываются
        по тройкам. Формы ядра при этом в наш процесс не попадают — в этом
        и смысл (`docs/08_ENGINE_BACKEND.md`, §10.1).
        """
        if self.document is None:
            return None
        mesh = getattr(self.document, "mesh", None)
        if mesh is None or not mesh.positions:
            return None
        import numpy as _np

        points = _np.asarray(mesh.positions, _np.float32).reshape(-1, 3)
        normals = _np.asarray(mesh.normals, _np.float32).reshape(-1, 3)
        faces = _np.asarray(mesh.face_ids, _np.uint32)
        return points, normals, faces

    def rebuild(self, force: bool = False):
        """Пересчитать дерево построения и обновить форму.

        Возвращает отчёт о пересчёте либо None, если дерева нет. При отказе
        форма НЕ обновляется — остаётся последняя удавшаяся, а отказ виден
        в отчёте.
        """
        if self.document is not None:
            # Деталь на движке считает себя сама; форма ядра сюда не
            # приходит вовсе, и `shape` у неё остаётся пустой.
            return self.document.rebuild()
        if not self.is_parametric:
            return None
        report = self.features.recompute(force=force)
        if report.ok and self.features.shape is not None:
            self.shape = self.features.shape
        return report

    def set_parameter(self, feature: str, parameter: str, value):
        """Изменить параметр операции и перестроить деталь."""
        if not self.is_parametric:
            raise ModelError(f"{self.label}: нет дерева построения")
        self.features.set_parameter(feature, parameter, value)
        return self.rebuild()


@dataclass
class Occurrence:
    """Геометрическое вхождение изделия в состав сборочной единицы."""

    item: Item
    reference: str = ""
    side: str = SIDE_TOP
    transform: np.ndarray = field(default_factory=identity)
    note: str = ""
    stable_id: str = field(default_factory=lambda: _new_id("occ"))

    @property
    def label(self) -> str:
        return self.reference or self.item.label


@dataclass
class BomEntry:
    """Вхождение без геометрии: материал, изделие без модели."""

    item: Item
    quantity: int = 1
    note: str = ""
    stable_id: str = field(default_factory=lambda: _new_id("bom"))


@dataclass
class Assembly(Item):
    """Сборочная единица. Её состав — это её спецификация."""

    placements: list[Occurrence] = field(default_factory=list)
    bom_only: list[BomEntry] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.kind == KIND_DETAIL:
            self.kind = KIND_ASSEMBLY
        super().__post_init__()
        if not self.is_assembly:
            raise ModelError(f"{self.designation}: вид {self.kind!r} не сборочный")

    # --- состав ---

    def place(
        self,
        item: Item,
        reference: str = "",
        transform: np.ndarray | None = None,
        side: str = SIDE_TOP,
        note: str = "",
    ) -> Occurrence:
        _assert_no_cycle(self, item)
        occurrence = Occurrence(
            item=item,
            reference=reference,
            side=side,
            transform=identity() if transform is None else np.asarray(transform, float),
            note=note,
        )
        self.placements.append(occurrence)
        return occurrence

    def add_bom_only(self, item: Item, quantity: int = 1, note: str = "") -> BomEntry:
        _assert_no_cycle(self, item)
        entry = BomEntry(item=item, quantity=quantity, note=note)
        self.bom_only.append(entry)
        return entry

    # --- выдача ---

    def bom(self) -> list[dict]:
        """Состав как строки спецификации.

        Количество считается ГРУППИРОВКОЙ, а не хранится отдельным полем:
        хранимое количество неизбежно разойдётся с фактическим составом.
        """
        rows: dict[str, dict] = {}

        def add(item: Item, quantity: int, reference: str = "", note: str = "") -> None:
            row = rows.setdefault(
                item.stable_id,
                {
                    "designation": item.designation,
                    "name": item.name,
                    "kind": item.kind,
                    "stable_id": item.stable_id,
                    "quantity": 0,
                    "references": [],
                    "note": note or item.note,
                },
            )
            row["quantity"] += quantity
            if reference:
                row["references"].append(reference)

        for occurrence in self.placements:
            add(occurrence.item, 1, occurrence.reference, occurrence.note)
        for entry in self.bom_only:
            add(entry.item, entry.quantity, "", entry.note)

        result = list(rows.values())
        for row in result:
            row["references"].sort()
        result.sort(
            key=lambda row: (
                SECTION_ORDER.get(row["kind"], 99),
                row["designation"],
                row["name"],
            )
        )
        return result

    def walk(self, depth: int = 0):
        """Обход состава вглубь: (изделие, уровень вложенности)."""
        yield self, depth
        for occurrence in self.placements:
            item = occurrence.item
            if isinstance(item, Assembly):
                yield from item.walk(depth + 1)
            else:
                yield item, depth + 1

    def all_items(self) -> list[Item]:
        """Все изделия дерева, каждое по одному разу."""
        seen: dict[str, Item] = {}
        stack: list[Item] = [self]
        while stack:
            current = stack.pop()
            if current.stable_id in seen:
                continue
            seen[current.stable_id] = current
            if isinstance(current, Assembly):
                stack.extend(occurrence.item for occurrence in current.placements)
                stack.extend(entry.item for entry in current.bom_only)
        return list(seen.values())

    def merged_shape(self, kernel=None):
        """Геометрия состава одним телом — то, чем сборка входит выше.

        Слитое представление: визуальный производный артефакт, логический
        состав им не затрагивается. Имя отличается от поля ``shape``
        намеренно: у сборочной единицы собственной геометрии нет, есть
        вычисляемая из состава.
        """
        from .. import kernel as default_kernel

        engine = kernel or default_kernel
        shapes = []
        for occurrence in self.placements:
            item = occurrence.item
            geometry = (
                item.merged_shape(engine) if isinstance(item, Assembly) else item.shape
            )
            if geometry is None:
                continue
            shapes.append(engine.transformed(geometry, occurrence.transform))
        return engine.compound(shapes) if shapes else None


def _assert_no_cycle(assembly: Assembly, item: Item) -> None:
    """Изделие не может входить в собственный состав на любой глубине."""
    stack: list[Item] = [item]
    seen: set[str] = set()
    while stack:
        current = stack.pop()
        if current.stable_id in seen:
            continue
        seen.add(current.stable_id)
        if current.stable_id == assembly.stable_id:
            raise CycleError(
                f"{item.label!r} уже содержит {assembly.label!r} — цикл в составе"
            )
        if isinstance(current, Assembly):
            stack.extend(occurrence.item for occurrence in current.placements)
            stack.extend(entry.item for entry in current.bom_only)
