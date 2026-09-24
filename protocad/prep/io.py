"""Чтение и запись геометрии для подготовки к расчёту.

Геометрия под расчёт почти всегда приходит ИЗВНЕ: из STEP соседнего
отдела, из IGES поставщика, из своей же сборки. Поэтому чтение здесь
держит то, без чего препроцессор слеп:

* **имена деталей** из STEP (дерево XCAF) — по ним назначают материалы, и
  «Тело 17» вместо «Корпус» делает это гаданием;
* **размещения** сборки — деталь, вставленная трижды, даёт три тела;
* **поверхностные модели** (IGES, «кожа») не выбрасываются: из них тело
  сшивают (`heal.py`), а выдать их за тело нельзя.

Единицы — миллиметры: так читает STEP ядро по умолчанию, так живёт весь
ProtoCAD. Перевод в единицы решателя — при записи сетки (`mesh.py`).
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from .. import kernel
from .model import Body, Study, loose_parts, solids_of

STEP_EXTENSIONS = (".step", ".stp")
IGES_EXTENSIONS = (".iges", ".igs")
BREP_EXTENSIONS = (".brep", ".brp")
CONTAINER_EXTENSIONS = (".prcadasm", ".prcadpart")
KNOWN = STEP_EXTENSIONS + IGES_EXTENSIONS + BREP_EXTENSIONS + CONTAINER_EXTENSIONS


class ImportError_(RuntimeError):
    """Файл не прочитан. Своё имя — чтобы не путать со встроенным ImportError."""


@contextlib.contextmanager
def quiet():
    """Заглушить отчёты ядра в консоль на время обмена.

    Трансляторы STEP и IGES печатают в stdout статистику переноса. В окне
    её никто не видит, а в консольном прогоне она тонет среди настоящих
    сообщений — и прячет их.
    """
    from OCP.Message import Message, Message_Gravity

    printers = list(Message.DefaultMessenger_s().Printers())
    levels = [printer.GetTraceLevel() for printer in printers]
    for printer in printers:
        printer.SetTraceLevel(Message_Gravity.Message_Fail)
    try:
        yield
    finally:
        for printer, level in zip(printers, levels):
            printer.SetTraceLevel(level)


def load(path, name: str = "") -> Study:
    """Прочитать файл в новое исследование."""
    path = Path(path)
    if not path.is_file():
        raise ImportError_(f"файла нет: {path}")
    suffix = path.suffix.lower()
    if suffix in STEP_EXTENSIONS:
        bodies = read_step(path)
    elif suffix in IGES_EXTENSIONS:
        bodies = read_iges(path)
    elif suffix in BREP_EXTENSIONS:
        bodies = bodies_of(kernel.import_brep(path), path.stem)
    elif suffix in CONTAINER_EXTENSIONS:
        bodies = read_container(path)
    else:
        raise ImportError_(
            f"{path.name}: формат {suffix or 'без расширения'} не поддерживается. "
            f"Читаются STEP, IGES, BREP и контейнеры ProtoCAD")
    if not bodies:
        raise ImportError_(f"{path.name}: в файле нет геометрии")
    study = Study(name=name or path.stem, source=str(path))
    for body in bodies:
        study.add_body(body.shape, body.name)
    return study


def bodies_of(shape, name: str) -> list:
    """Форма → тела: каждое SOLID отдельно, свободные оболочки и грани — тоже.

    Одно тело из нескольких SOLID не делается: у каждого свой материал и
    своя сетка, и различать их потом было бы нечем.
    """
    if shape is None or shape.IsNull():
        return []
    solids = solids_of(shape)
    loose = loose_parts(shape)
    parts = solids + loose
    if len(parts) == 1:
        return [Body(name, parts[0])]
    bodies = [Body(f"{name} [{index + 1}]", solid)
              for index, solid in enumerate(solids)]
    if loose:
        # Свободные грани одной детали — это одна недошитая оболочка, а не
        # сотня тел: сшивать их всё равно вместе.
        tail = f"{name} (поверхности)" if solids else name
        bodies.append(Body(tail, kernel.compound(loose)))
    return bodies


# --- STEP ----------------------------------------------------------------


def read_step(path) -> list:
    """STEP через XCAF: с именами деталей и размещениями сборки."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDF import TDF_LabelSequence
    from OCP.TDocStd import TDocStd_Document
    from OCP.TopLoc import TopLoc_Location
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    document = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    with quiet():
        if reader.ReadFile(str(path)) != IFSelect_RetDone:
            raise ImportError_(f"{Path(path).name}: STEP не читается")
        if not reader.Transfer(document):
            raise ImportError_(f"{Path(path).name}: STEP прочитан, но "
                               f"геометрия из него не переносится")
    tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    roots = TDF_LabelSequence()
    tool.GetFreeShapes(roots)
    found = []
    for index in range(1, roots.Length() + 1):
        _walk(roots.Value(index), TopLoc_Location(), found, Path(path).stem)
    return found


def _label_name(label) -> str:
    from OCP.TDataStd import TDataStd_Name

    attribute = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), attribute):
        return _repaired(attribute.Get().ToExtString().strip())
    return ""


def _repaired(name: str) -> str:
    """Починить имя, записанное байтами UTF-8 как латиница.

    Часть систем кладёт в STEP кириллицу байтами UTF-8 без пометки, и после
    чтения «Корпус» превращается в «Ð\\x9aÐ¾Ñ\\x80...». Если имя целиком из
    знаков латиницы-1 и как байты складывается в правильный UTF-8 — это
    оно и есть. Иначе имя возвращается как было: гадать дальше нельзя.
    """
    if not name or not any("\x80" <= character <= "\xff" for character in name):
        return name
    try:
        return name.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return name


def _walk(label, location, found: list, fallback: str) -> None:
    """Обход дерева сборки XCAF с накоплением размещений."""
    from OCP.TDF import TDF_Label, TDF_LabelSequence
    from OCP.XCAFDoc import XCAFDoc_ShapeTool

    if XCAFDoc_ShapeTool.IsAssembly_s(label):
        components = TDF_LabelSequence()
        XCAFDoc_ShapeTool.GetComponents_s(label, components, False)
        for index in range(1, components.Length() + 1):
            component = components.Value(index)
            referred = TDF_Label()
            if not XCAFDoc_ShapeTool.GetReferredShape_s(component, referred):
                continue
            placed = location.Multiplied(XCAFDoc_ShapeTool.GetLocation_s(component))
            # Имя вхождения лучше имени детали, когда оно осмысленное:
            # «Болт:3» различает три одинаковых болта. Служебное «=>[0:1:1:2]»
            # и голый номер — не осмысленные: номер ядро подставляет само,
            # когда имя вхождения совпадает с именем детали.
            own = _label_name(component)
            name = own if own and not own.startswith("=>") and not own.isdigit() \
                else ""
            _walk_part(referred, placed, found, name, fallback)
        return
    _walk_part(label, location, found, "", fallback)


def _walk_part(label, location, found: list, instance: str, fallback: str) -> None:
    from OCP.XCAFDoc import XCAFDoc_ShapeTool

    if XCAFDoc_ShapeTool.IsAssembly_s(label):
        _walk(label, location, found, fallback)
        return
    shape = XCAFDoc_ShapeTool.GetShape_s(label)
    if shape is None or shape.IsNull():
        return
    name = instance or _label_name(label) or fallback
    found.extend(bodies_of(shape.Moved(location), name))


def write_step(study: Study, path) -> Path:
    """STEP с именами тел: соседняя система увидит «Корпус», а не «Solid1»."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    from OCP.TDF import TDF_Label
    from OCP.TopLoc import TopLoc_Location

    def named(label, text: str) -> None:
        # Второй аргумент — строка в UTF-8. Без него каждый байт становится
        # отдельной буквой, и кириллица в файле превращается в «Ð¡Ð±».
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(text, True))

    path = Path(path)
    document = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    # Сборкой, а не россыпью: тело, вставленное дважды, — это одна деталь в
    # двух местах. Россыпью XCAF узнаёт в них одну форму и теряет имя
    # второго вхождения — после чтения у тел оставались номера «1», «2».
    root = tool.NewShape()
    named(root, study.name)
    for body in study.bodies:
        bare = body.shape.Located(TopLoc_Location())
        part = TDF_Label()
        if not tool.FindShape(bare, part, False):
            part = tool.AddShape(bare, False, False)
            named(part, body.name)
        component = tool.AddComponent(root, part, body.shape.Location())
        named(component, body.name)
    tool.UpdateAssemblies()
    writer = STEPCAFControl_Writer()
    writer.SetNameMode(True)
    with quiet():
        if not writer.Transfer(document, STEPControl_AsIs):
            raise RuntimeError("STEP: перенос геометрии не удался")
        if writer.Write(str(path)) != IFSelect_RetDone:
            raise RuntimeError(f"STEP: файл не записан: {path}")
    return path


# --- IGES ----------------------------------------------------------------


def read_iges(path) -> list:
    """IGES: чаще всего поверхности, а не тела. Сшивает `heal`."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.IGESControl import IGESControl_Reader

    reader = IGESControl_Reader()
    with quiet():
        if reader.ReadFile(str(path)) != IFSelect_RetDone:
            raise ImportError_(f"{Path(path).name}: IGES не читается")
        reader.TransferRoots()
    return bodies_of(reader.OneShape(), Path(path).stem)


# --- деталь ProtoCAD -----------------------------------------------------


def from_document(document, folder=None) -> Study:
    """Деталь из окна конструирования → исследование.

    Форма живёт у движка детали (FreeCAD в отдельном процессе), поэтому она
    выгружается ИМ в STEP — с именами тел — и читается здесь. Передавать
    формы ядра между процессами нельзя (`docs/08_ENGINE_BACKEND.md`, §16.3),
    и файл — это та самая граница, только видимая.
    """
    import tempfile

    from .names import solver_name

    folder = Path(folder) if folder else Path(tempfile.mkdtemp(prefix="protocad-prep-"))
    folder.mkdir(parents=True, exist_ok=True)
    title = getattr(document, "designation", "") or getattr(document, "name", "деталь")
    path = folder / f"{solver_name(title)}.step"
    result = document.export(path)
    if not result.ok:
        raise ImportError_(f"движок не выгрузил деталь: {result.message}")
    return load(path, name=title)


# --- контейнер ProtoCAD --------------------------------------------------


def read_container(path) -> list:
    """Сборка или деталь ProtoCAD с точной геометрией внутри.

    Читаются контейнеры с формами (`geometry/*.brp`) — так пишутся сборки.
    Деталь, построенная движком, хранит геометрию документом FreeCAD, и
    прочитать её без движка нельзя: её передают через экспорт движка
    (`protocad.document.Document.export`).
    """
    import zipfile

    from .. import format as fmt
    from ..model import Assembly

    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
    except zipfile.BadZipFile as failure:
        raise ImportError_(f"{Path(path).name}: не контейнер ProtoCAD") from failure
    if fmt.INTENT_NAME in names or fmt.ENGINE_NAME in names:
        raise ImportError_(
            f"{Path(path).name}: деталь построена движком и хранит геометрию "
            f"его документом — прочитать её без движка нельзя. Откройте её "
            f"в ProtoCAD и передайте в подготовку оттуда")
    try:
        root, _manifest = fmt.read(path)
    except (fmt.FormatError, KeyError) as failure:
        raise ImportError_(f"{Path(path).name}: {failure}") from failure

    found = []

    def walk(item, matrix, prefix: str) -> None:
        if isinstance(item, Assembly):
            for occurrence in item.placements:
                label = occurrence.reference or occurrence.item.name
                walk(occurrence.item, matrix @ occurrence.transform,
                     f"{prefix}{label}" if not prefix else f"{prefix}/{label}")
            return
        if item.shape is None:
            return
        found.extend(bodies_of(kernel.transformed(item.shape, matrix),
                               prefix or item.name))

    import numpy as np

    walk(root, np.eye(4), "")
    return found


# --- BREP и STL ----------------------------------------------------------


def write_brep(study: Study, path) -> Path:
    return Path(kernel.export_brep(study.compound(), path))


def write_stl(study: Study, path, deflection: float = 0.0, scale: float = 1.0,
              by_groups: bool = True) -> dict:
    """ASCII STL с ИМЕНОВАННЫМИ частями — по одной на группу граней.

    Так его ждёт snappyHexMesh (OpenFOAM): каждое `solid <имя>` становится
    отдельной границей. Грани вне групп идут частью по имени тела. Грань в
    двух группах уходит в первую — и об этом возвращается замечание, иначе
    граничное условие молча окажется не там.

    Возвращает {"regions": {имя: треугольников}, "notes": [...]}.
    """
    import numpy as np

    from .display import face_triangles
    from .names import solver_names

    path = Path(path)
    deflection = deflection or max(study.diagonal() * 1e-3, 1e-3)
    regions: dict = {}
    notes = []
    for index in range(study.face_count):
        groups = study.groups_of_face(index) if by_groups else []
        if len(groups) > 1:
            notes.append(f"грань {index} входит в группы {groups}; записана "
                         f"в «{groups[0]}»")
        owner = groups[0] if groups else (study.owners(index) or ["поверхность"])[0]
        regions.setdefault(owner, []).append(index)
    names = solver_names(list(regions))
    counts = {}
    with open(path, "w", encoding="ascii") as stream:
        for region, faces in regions.items():
            label = names[region]
            stream.write(f"solid {label}\n")
            total = 0
            for index in faces:
                triangles = face_triangles(study.face(index), deflection)
                if triangles is None:
                    notes.append(f"грань {index}: не разбилась на треугольники")
                    continue
                triangles = triangles * scale
                a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
                normals = np.cross(b - a, c - a)
                lengths = np.linalg.norm(normals, axis=1, keepdims=True)
                lengths[lengths == 0.0] = 1.0
                normals = normals / lengths
                for normal, corners in zip(normals, triangles):
                    stream.write("  facet normal {:.6e} {:.6e} {:.6e}\n"
                                 "    outer loop\n".format(*normal))
                    for point in corners:
                        stream.write("      vertex {:.9e} {:.9e} {:.9e}\n"
                                     .format(*point))
                    stream.write("    endloop\n  endfacet\n")
                total += len(triangles)
            stream.write(f"endsolid {label}\n")
            counts[label] = total
    return {"regions": counts, "notes": notes, "names": names}
