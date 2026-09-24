"""Документ сборки: состав по ЕСКД, сопряжения и происхождение деталей.

Здесь сходятся две модели, которые в проекте жили порознь:

* `protocad.model.Assembly` — СОСТАВ: вхождения с положениями,
  спецификация, контейнер `.prcadAsm`, быстрый показ;
* `protocad.assembly` — ПРАВИЛА взаимного положения: сопряжения и решатель.

Состав остаётся главным: положение вхождения хранится в нём
(`Occurrence.transform`), решатель только вычисляет его по сопряжениям и
записывает обратно. Поэтому всё, что уже умеет работать с составом, —
спецификация, показ, запись в контейнер, просмотрщик ПРОТО — работает с
собранной сборкой без переделки.

Сопрягаются вхождения ВЕРХНЕГО уровня. Подсборка внутри сборки — жёсткое
целое: её грани — грани её деталей, переведённые в её координаты. Так
сборку и собирают руками: узел ставят целиком, а не по деталям.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .. import model as model_module
from ..model import KIND_ASSEMBLY, Assembly as Composition, Item
from . import faces as faces_module
from .model import TITLES, Assembly as MateSet, Instance, Mate, Reference
from .solve import solve

#: Имя записи сопряжений в контейнере.
EXTRA = "assembly"

#: Состояния вхождения — как их показывает дерево.
FIXED = "закреплено"
MATED = "сопряжено"
FREE = "свободно"
BROKEN = "ошибка"


class AssemblyDocument:
    """Сборка, которую правят: состав, сопряжения, закреплённые вхождения."""

    def __init__(self, name: str = "Сборка", designation: str = "",
                 root: Composition | None = None):
        self.root = root or Composition(designation, name, kind=KIND_ASSEMBLY)
        self.mates: list = []
        #: Закреплённые вхождения (по `stable_id`). Закреплённое стоит там,
        #: где его поставили; остальные ставят сопряжения.
        self.fixed: set = set()
        #: Откуда взято определение: {stable_id изделия: путь к файлу}.
        self.sources: dict = {}
        self.path: Path | None = None
        self.diagnostics: list = []
        #: Описания граней по изделию: одно определение — один разбор, сколько
        #: бы вхождений у него ни было.
        self._faces: dict = {}
        #: Разбивка определений для показа — живёт между пересборками сцены:
        #: сдвиг вхождения меняет матрицу, а не треугольники.
        self._scene_cache: dict = {}

    # --- состав -------------------------------------------------------------

    @property
    def occurrences(self) -> list:
        return list(self.root.placements)

    def occurrence(self, stable_id: str):
        for occurrence in self.root.placements:
            if occurrence.stable_id == stable_id:
                return occurrence
        return None

    def insert(self, path, reference: str = "", transform=None, backend=None):
        """Вставить изделие из файла. Повторная вставка того же файла даёт
        ещё одно ВХОЖДЕНИЕ того же определения, а не вторую копию формы."""
        from .importer import load_component

        path = Path(path).resolve()
        item = None
        for stable_id, source in self.sources.items():
            if Path(source) == path:
                item = self._item(stable_id)
                if item is not None:
                    break
        if item is None:
            item = load_component(path, backend=backend)
            self.sources[item.stable_id] = str(path)
        return self.add(item, reference, transform)

    def add(self, item: Item, reference: str = "", transform=None):
        """Поставить изделие в сборку. Первое вхождение закрепляется: сборке
        нужна точка отсчёта, и обычно это то, что поставили первым."""
        occurrence = self.root.place(item, reference or self._next_reference(item),
                                     None if transform is None
                                     else np.asarray(transform, float))
        if len(self.root.placements) == 1:
            self.fixed.add(occurrence.stable_id)
        return occurrence

    def _next_reference(self, item: Item) -> str:
        base = item.name or item.designation or "Деталь"
        taken = {occurrence.reference for occurrence in self.root.placements}
        number = 1
        while f"{base}:{number}" in taken:
            number += 1
        return f"{base}:{number}"

    def _item(self, stable_id: str):
        for occurrence in self.root.placements:
            if occurrence.item.stable_id == stable_id:
                return occurrence.item
        return None

    def remove(self, occurrence) -> list:
        """Убрать вхождение вместе с его сопряжениями. Возвращает снятые."""
        self.root.placements = [item for item in self.root.placements
                                if item is not occurrence]
        self.fixed.discard(occurrence.stable_id)
        gone = [mate for mate in self.mates
                if occurrence.stable_id in (mate.first.instance, mate.second.instance)]
        self.mates = [mate for mate in self.mates if mate not in gone]
        return gone

    def status(self, occurrence) -> str:
        """Что держит вхождение: закрепление, сопряжения или ничего."""
        mine = self.mates_of(occurrence)
        if any(not mate.ok for mate in mine):
            return BROKEN
        if occurrence.stable_id in self.fixed:
            return FIXED
        return MATED if mine else FREE

    def mates_of(self, occurrence) -> list:
        return [mate for mate in self.mates
                if occurrence.stable_id in (mate.first.instance, mate.second.instance)]

    def fix(self, occurrence, on: bool = True) -> None:
        if on:
            self.fixed.add(occurrence.stable_id)
        else:
            self.fixed.discard(occurrence.stable_id)

    def move(self, occurrence, transform) -> None:
        """Поставить вхождение руками. Имеет смысл для закреплённого или
        свободного: у сопряжённого положение пересчитает решатель."""
        occurrence.transform = np.asarray(transform, float)

    # --- грани ----------------------------------------------------------------

    def faces(self, occurrence) -> list:
        """Описания граней вхождения в ЕГО собственных координатах."""
        return _faces_of(occurrence.item, self._faces)

    def forget_faces(self) -> None:
        """Забыть описания — после правки деталей."""
        self._faces = {}

    def pick(self, preview, face_id: int):
        """Что выбрано щелчком: (вхождение верхнего уровня, описание грани в
        его координатах). ``None`` — щелчок не по грани.

        Показ знает деталь, в которую попал щелчок, и путь к ней. Во
        вложенной сборке это деталь подсборки, а сопрягают вхождение
        верхнего уровня, — поэтому описание переводится в его координаты.
        """
        entry = preview.face_to_object.get(int(face_id))
        if entry is None:
            return None
        body = preview.id_to_object.get(entry["body"]) or {}
        path = body.get("path") or []
        if not path:
            return None
        top = self.occurrence(path[0])
        if top is None:
            return None
        chain = np.eye(4)
        holder = top.item
        for stable_id in path[1:]:
            inner = next((item for item in getattr(holder, "placements", ())
                          if item.stable_id == stable_id), None)
            if inner is None:
                return None
            chain = chain @ inner.transform
            holder = inner.item
        local = _faces_of(holder, self._faces)
        face = next((item for item in local if item["index"] == entry["face"]), None)
        if face is None:
            return None
        return top, _moved(face, chain)

    # --- сопряжения ------------------------------------------------------------

    def mate(self, kind: str, first, second, value_mm: float = 0.0,
             flip: bool = False) -> Mate:
        """Добавить сопряжение. ``first``/``second`` — пары (вхождение,
        описание грани в его координатах), как их отдаёт `pick`."""
        first_occurrence, first_face = first
        second_occurrence, second_face = second
        mate = Mate(kind=kind,
                    first=Reference(first_occurrence.stable_id,
                                    faces_module.mark_of(first_face)),
                    second=Reference(second_occurrence.stable_id,
                                     faces_module.mark_of(second_face)),
                    value_mm=float(value_mm), flip=bool(flip))
        self.mates.append(mate)
        return mate

    def remove_mate(self, mate) -> None:
        self.mates = [item for item in self.mates if item is not mate]

    def describe(self, mate) -> str:
        """Сопряжение словами: «Соосность: Плита — Болт»."""
        names = []
        for reference in (mate.first, mate.second):
            occurrence = self.occurrence(reference.instance)
            names.append(occurrence.label if occurrence is not None else "(нет)")
        text = f"{TITLES.get(mate.kind, mate.kind)}: {names[0]} — {names[1]}"
        if mate.kind == "distance":
            text += f", {mate.value_mm:g} мм"
        if mate.flip:
            text += ", развёрнуто"
        return text

    def solve(self) -> list:
        """Расставить вхождения по сопряжениям. Замечания — и в итог, и в
        ``diagnostics``: окно показывает их, не вызывая решение повторно."""
        by_id = {}
        instances = []
        # Грани нужны только тем, на кого ссылаются сопряжения: разбирать
        # ради решения всю плату с тысячей ЭРИ, стоящую на закреплении,
        # незачем.
        mated = {reference.instance for mate in self.mates
                 for reference in (mate.first, mate.second)}
        for occurrence in self.root.placements:
            rotation = occurrence.transform[:3, :3]
            shift = occurrence.transform[:3, 3]
            instance = Instance(
                name=occurrence.label, id=occurrence.stable_id,
                placement=(tuple(float(v) for v in shift),
                           tuple(tuple(float(v) for v in row) for row in rotation)),
                fixed=occurrence.stable_id in self.fixed,
                faces=(self.faces(occurrence) if occurrence.stable_id in mated
                       else []))
            instances.append(instance)
            by_id[instance.id] = occurrence
        found = solve(MateSet(self.root.name, instances, self.mates))
        for instance in instances:
            if instance.fixed:
                self.fixed.add(instance.id)
            shift, basis = instance.placement
            matrix = np.eye(4)
            matrix[:3, :3] = np.asarray(basis, float)
            matrix[:3, 3] = np.asarray(shift, float)
            by_id[instance.id].transform = matrix
        self.diagnostics = found
        return found

    # --- показ и спецификация -----------------------------------------------------

    def scene(self, deflection: float = 0.1):
        """Буферы показа: определение разбивается один раз, вхождения
        раскладываются по одному буферу видеопамяти."""
        from ..preview import build

        return build(self.root, deflection, cache=self._scene_cache)

    def bodies_of(self, preview, occurrence) -> list:
        """Номера тел показа, принадлежащих вхождению верхнего уровня."""
        return [number for number, body in preview.id_to_object.items()
                if (body.get("path") or [None])[0] == occurrence.stable_id]

    # --- отмена -------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Всё, что меняет окно сборки. Формы деталей окно не правит, поэтому
        снимок — это списки и матрицы, а не геометрия."""
        return {"placements": list(self.root.placements),
                "transforms": {occurrence.stable_id: occurrence.transform.copy()
                               for occurrence in self.root.placements},
                "mates": [Mate.from_dict(mate.to_dict()) for mate in self.mates],
                "fixed": set(self.fixed),
                "sources": dict(self.sources)}

    def restore(self, state: dict) -> None:
        self.root.placements = list(state["placements"])
        for occurrence in self.root.placements:
            occurrence.transform = state["transforms"][occurrence.stable_id].copy()
        self.mates = [Mate.from_dict(mate.to_dict()) for mate in state["mates"]]
        self.fixed = set(state["fixed"])
        self.sources = dict(state["sources"])
        self.diagnostics = []

    def bom(self) -> list:
        return self.root.bom()

    # --- файл ---------------------------------------------------------------------

    def save(self, path) -> Path:
        """Записать `.prcadAsm`: состав с формами, просмотр, сопряжения.

        Деталь на движке кладётся СНИМКОМ формы, выгруженным самим движком:
        сборка должна открываться и показываться там, где движка нет, — в
        просмотрщике и в ПРОТО. Путь к исходной детали записывается рядом,
        чтобы снимок можно было обновить.
        """
        from .. import format as fmt
        from ..preview import build

        _snapshot_engine_parts(self.root)
        path = fmt.write(self.root, path,
                         preview=build(self.root, cache=self._scene_cache),
                         extras={EXTRA: self.to_dict()})
        self.path = Path(path)
        return self.path

    def export_step(self, path) -> Path:
        """STEP для расчётной системы: сборка со структурой и именами."""
        from .export import write_step_tree

        _snapshot_engine_parts(self.root)
        return write_step_tree(self.root, path)

    def to_dict(self) -> dict:
        return {"schema": 1,
                "mates": [mate.to_dict() for mate in self.mates],
                "fixed": sorted(self.fixed),
                "sources": dict(self.sources)}

    @classmethod
    def open(cls, path) -> "AssemblyDocument":
        from .. import format as fmt

        root, _manifest = fmt.read(path)
        if not isinstance(root, Composition):
            raise ValueError(f"{Path(path).name}: это не сборка")
        document = cls(root=root)
        extra = fmt.read_extra(path, EXTRA) or {}
        document.mates = [Mate.from_dict(item) for item in extra.get("mates") or ()]
        document.fixed = set(extra.get("fixed") or ())
        document.sources = dict(extra.get("sources") or {})
        if not document.fixed and root.placements:
            document.fixed.add(root.placements[0].stable_id)
        document.path = Path(path)
        return document


# --- грани изделия ----------------------------------------------------------------


def _faces_of(item, cache: dict | None = None) -> list:
    """Грани изделия в его координатах. У сборки — грани всех её деталей.

    ``cache`` — {stable_id изделия: описания}: одинаковые детали разбираются
    один раз, а у сборки переставляются готовые описания.
    """
    if cache is not None and item.stable_id in cache:
        return cache[item.stable_id]
    if isinstance(item, Composition):
        found = []
        for occurrence in item.placements:
            for face in _faces_of(occurrence.item, cache):
                moved = _moved(face, occurrence.transform)
                moved["index"] = len(found)
                found.append(moved)
    else:
        found = faces_module.item_faces(item)
    if cache is not None:
        cache[item.stable_id] = found
    return found


def _moved(face: dict, matrix) -> dict:
    """Описание грани, переставленное матрицей 4×4."""
    matrix = np.asarray(matrix, float)
    rotation, shift = matrix[:3, :3], matrix[:3, 3]
    result = dict(face)
    for key in ("center", "origin"):
        if face.get(key) is not None:
            result[key] = list(rotation @ np.asarray(face[key], float) + shift)
    for key in ("normal", "axis"):
        if face.get(key) is not None:
            result[key] = list(rotation @ np.asarray(face[key], float))
    return result


def _snapshot_engine_parts(root) -> None:
    """Деталям на движке — снимок формы для записи в контейнер."""
    import tempfile

    seen = set()
    stack = [root]
    while stack:
        item = stack.pop()
        if item.stable_id in seen:
            continue
        seen.add(item.stable_id)
        if isinstance(item, model_module.Assembly):
            stack.extend(occurrence.item for occurrence in item.placements)
            continue
        document = getattr(item, "document", None)
        if document is None:
            continue
        with tempfile.TemporaryDirectory(prefix="protocad-asm-") as folder:
            target = Path(folder) / "деталь.brep"
            result = document.export(target)
            if result.ok and target.is_file():
                from .. import kernel

                item.shape = kernel.import_brep(target)
