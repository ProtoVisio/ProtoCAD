"""Детали и сборки из файлов — для вставки в сборку и импорта изделия.

STEP читается СО СТРУКТУРОЙ (дерево XCAF): сборка становится сборкой, её
вхождения — вхождениями с положениями, а одинаковые детали — ОДНИМ
определением на все вхождения. Для платы это главное: 300 резисторов
0603 — это одна форма и 300 положений, а не 300 копий геометрии
(`docs/08_ENGINE_BACKEND.md`, §22.3). Показ разбивает определение на
треугольники один раз и раскладывает по вхождениям в один буфер
видеопамяти — отсюда и скорость.

Имя вхождения в STEP — это, как правило, позиционное обозначение (R12,
DD3): оно ложится в `Occurrence.reference`, как и положено по ЕСКД.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .. import kernel
from ..model import KIND_ASSEMBLY, KIND_DETAIL, Assembly, Item

STEP = (".step", ".stp")
IGES = (".iges", ".igs")
BREP = (".brep", ".brp")
CONTAINERS = (".prcadasm", ".prcadpart")


class ImportFailed(RuntimeError):
    """Файл не прочитан — с объяснением для человека."""


def load_component(path, backend=None) -> Item:
    """Изделие из файла: деталь (`Item`) или сборка (`Assembly`).

    ``backend`` — движок для деталей ProtoCAD, построенных по дереву
    намерений; без него такая деталь не открывается (её форма живёт у
    движка, а не в файле).
    """
    path = Path(path)
    if not path.is_file():
        raise ImportFailed(f"файла нет: {path}")
    suffix = path.suffix.lower()
    if suffix in STEP:
        return read_step_tree(path)
    if suffix in IGES:
        from ..prep.io import read_iges

        return _flat_item(read_iges(path), path.stem)
    if suffix in BREP:
        return Item("", path.stem, kind=KIND_DETAIL, shape=kernel.import_brep(path))
    if suffix in CONTAINERS:
        return _read_container(path, backend)
    raise ImportFailed(f"{path.name}: вставляются STEP, IGES, BREP и файлы ProtoCAD")


def _flat_item(bodies, name: str) -> Item:
    if not bodies:
        raise ImportFailed(f"{name}: в файле нет геометрии")
    shape = bodies[0].shape if len(bodies) == 1 else kernel.compound(
        [body.shape for body in bodies])
    return Item("", name, kind=KIND_DETAIL, shape=shape)


# --- STEP ----------------------------------------------------------------


def read_step_tree(path) -> Item:
    """STEP → дерево изделия. Одно определение на все его вхождения."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDF import TDF_LabelSequence
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    from ..prep.io import quiet

    path = Path(path)
    document = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    with quiet():
        if reader.ReadFile(str(path)) != IFSelect_RetDone:
            raise ImportFailed(f"{path.name}: STEP не читается")
        if not reader.Transfer(document):
            raise ImportFailed(f"{path.name}: геометрия из STEP не переносится")
    tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    roots = TDF_LabelSequence()
    tool.GetFreeShapes(roots)
    definitions: dict = {}
    items = [_definition(roots.Value(index), definitions, path.stem)
             for index in range(1, roots.Length() + 1)]
    items = [item for item in items if item is not None]
    if not items:
        raise ImportFailed(f"{path.name}: в файле нет геометрии")
    if len(items) == 1:
        return items[0]
    # Несколько корней — сборка без общего узла. Узел заводится, иначе
    # части изделия было бы не к чему приложить.
    root = Assembly("", path.stem, kind=KIND_ASSEMBLY)
    for item in items:
        root.place(item, "", np.eye(4))
    return root


def _entry(label) -> str:
    from OCP.TCollection import TCollection_AsciiString
    from OCP.TDF import TDF_Tool

    text = TCollection_AsciiString()
    TDF_Tool.Entry_s(label, text)
    return text.ToCString()


def _definition(label, definitions: dict, fallback: str):
    """Определение по метке XCAF — одно на все вхождения этой метки."""
    from OCP.TDF import TDF_Label, TDF_LabelSequence
    from OCP.XCAFDoc import XCAFDoc_ShapeTool

    from ..prep.io import _label_name

    key = _entry(label)
    if key in definitions:
        return definitions[key]
    name = _label_name(label) or fallback
    if XCAFDoc_ShapeTool.IsAssembly_s(label):
        assembly = Assembly("", name, kind=KIND_ASSEMBLY)
        definitions[key] = assembly
        components = TDF_LabelSequence()
        XCAFDoc_ShapeTool.GetComponents_s(label, components, False)
        for index in range(1, components.Length() + 1):
            component = components.Value(index)
            referred = TDF_Label()
            if not XCAFDoc_ShapeTool.GetReferredShape_s(component, referred):
                continue
            item = _definition(referred, definitions, name)
            if item is None:
                continue
            own = _label_name(component)
            reference = (own if own and not own.startswith("=>") and not own.isdigit()
                         else "")
            matrix = _matrix(XCAFDoc_ShapeTool.GetLocation_s(component))
            assembly.place(item, reference, matrix)
        return assembly if assembly.placements else None
    shape = XCAFDoc_ShapeTool.GetShape_s(label)
    if shape is None or shape.IsNull() or kernel.is_empty(shape):
        definitions[key] = None
        return None
    from OCP.TopLoc import TopLoc_Location

    item = Item("", name, kind=KIND_DETAIL, shape=shape.Located(TopLoc_Location()))
    definitions[key] = item
    return item


def _matrix(location) -> np.ndarray:
    """Положение XCAF → матрица 4×4, как у `Occurrence.transform`."""
    transform = location.Transformation()
    matrix = np.eye(4)
    for row in range(3):
        for column in range(4):
            matrix[row, column] = transform.Value(row + 1, column + 1)
    return matrix


# --- контейнеры ProtoCAD ---------------------------------------------------


def _read_container(path: Path, backend) -> Item:
    import zipfile

    from .. import format as fmt

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
    if fmt.INTENT_NAME in names:
        return _engine_part(path, backend)
    root, _manifest = fmt.read(path)
    return root


def _engine_part(path: Path, backend) -> Item:
    """Деталь ProtoCAD на движке: дерево намерений перестраивается заново.

    Форма такой детали живёт у движка, поэтому без движка она не
    открывается — и подставлять вместо неё что-то «похожее» нельзя.
    """
    from .. import format as fmt
    from ..document import Document

    if backend is None:
        from .. import engine

        try:
            backend = engine.make_backend()
        except RuntimeError as failure:
            raise ImportFailed(f"{path.name}: {failure}") from failure
    document = Document("Деталь", backend=backend)
    document.document_id = f"деталь:{path.stem}"
    try:
        fmt.read_document(path, document)
    except fmt.FormatError as failure:
        raise ImportFailed(f"{path.name}: {failure}") from failure
    report = document.rebuild()
    if not report.ok:
        raise ImportFailed(f"{path.name}: деталь не перестроилась — "
                           f"{report.failed_at}: {report.message}")
    return Item(document.designation, document.name, kind=KIND_DETAIL,
                document=document)
