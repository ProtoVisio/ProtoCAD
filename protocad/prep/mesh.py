"""Сетка для решателя: задание построителю и разбор его ответа.

Сетку строит gmsh — в отдельном процессе (`gmsh_runner.py`, там же
причины). Отсюда уходят геометрия (BREP), группы (центрами и площадями
граней — gmsh нумерует грани по-своему) и размеры; обратно приходят
файлы, счёт элементов и качество.

Какой питон запускать: переменная ``PROTOCAD_GMSH_PYTHON`` → свой питон,
если в нём есть gmsh → ``.venv-mesh`` рядом с репозиторием. Молча собрать
сетку чем-то другим нельзя — как и с движком детали (§16.2).

Форматы — по расширению файла:

========  =============================================================
``.inp``  CalculiX / Abaqus — свой писатель: объёмные элементы, ``*NSET``
          и ``*SURFACE`` для групп граней, ``*ELSET`` для тел и материалов
``.msh``  gmsh (4.1; для OpenFOAM `gmshToFoam` — ``msh_version: 2.2``)
``.med``  Code_Aster, Salome
``.unv``  I-DEAS Universal — Salome, многие решатели
``.cgns``  CGNS — CFD-решатели
``.su2``  SU2
``.bdf``  Nastran
``.vtk``  ParaView (посмотреть)
``.stl``  поверхность треугольниками
========  =============================================================
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps

from .model import BODY_GROUP, ERROR, FACE_GROUP, INFO, WARNING, Report, logged, solids_of
from .names import solver_names

RUNNER = Path(__file__).with_name("gmsh_runner.py")
ROOT = Path(__file__).resolve().parents[2]
FORMATS = (".inp", ".msh", ".med", ".unv", ".cgns", ".su2", ".bdf", ".vtk",
           ".stl", ".mesh")

_found: dict = {}


@dataclass
class MeshSpec:
    """Что за сетку строить. Размеры — в миллиметрах геометрии."""

    #: Наибольший размер элемента. Ноль — двадцатая доля диагонали.
    size: float = 0.0
    min_size: float = 0.0
    #: 1 — линейные элементы, 2 — квадратичные (для прочности почти всегда 2:
    #: линейный тетраэдр в изгибе завышает жёсткость в разы).
    order: int = 1
    #: Алгоритм объёма: delaunay (надёжный), hxt (быстрый, многопоточный),
    #: frontal (ровнее, медленнее).
    algorithm: str = "delaunay"
    #: Сколько элементов на полную окружность кривизны; 0 — не сгущать.
    curvature: int = 0
    optimize: bool = True
    #: Размер у групп граней: {"давление": 1.0}.
    local: dict = field(default_factory=dict)
    #: 3 — объём, 2 — только поверхность.
    dimension: int = 3
    msh_version: float = 4.1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data) -> "MeshSpec":
        data = dict(data or {})
        known = {item for item in cls.__dataclass_fields__}
        strange = sorted(set(data) - known)
        if strange:
            raise ValueError(f"в параметрах сетки непонятное: {strange}")
        return cls(**data)


def find_python() -> tuple:
    """(питон с gmsh, почему нет). Ответ запоминается на время работы."""
    if "python" in _found:
        return _found["python"], _found["reason"]
    candidates = []
    stated = os.environ.get("PROTOCAD_GMSH_PYTHON", "").strip()
    if stated:
        candidates.append(Path(stated))
    else:
        candidates.append(Path(sys.executable))
        for folder in (ROOT / ".venv-mesh",):
            candidates += [folder / "bin" / "python", folder / "Scripts" / "python.exe"]
    reason = ("gmsh не найден: поставьте его отдельно (requirements-mesh.txt) и "
              "укажите питон переменной PROTOCAD_GMSH_PYTHON")
    for candidate in candidates:
        if not candidate.is_file():
            if stated:
                reason = f"по указанному пути питона нет: {stated}"
            continue
        try:
            probe = subprocess.run(
                [str(candidate), "-c", "import gmsh, numpy; print(gmsh.__version__)"],
                capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired) as failure:
            reason = f"{candidate}: {failure}"
            continue
        if probe.returncode == 0:
            _found.update(python=str(candidate), reason="",
                          version=probe.stdout.strip())
            return str(candidate), ""
        if stated:
            reason = f"в {stated} нет gmsh или numpy"
    _found.update(python=None, reason=reason)
    return None, reason


@logged
def mesh(study, spec: MeshSpec | None = None, outputs=(), scale: float = 1.0,
         python: str | None = None, timeout: float = 1800.0,
         keep_job: str | None = None) -> Report:
    """Построить сетку и записать файлы. Геометрию исследования не меняет.

    ``scale`` — множитель координат при записи: 0.001 переводит миллиметры
    в метры (OpenFOAM, SU2 и большинство CFD ждут метры).
    """
    spec = MeshSpec.from_dict((spec or MeshSpec()).to_dict())
    outputs = [str(Path(item).resolve()) for item in outputs]
    report = Report("mesh", params={"spec": spec.to_dict(), "outputs": list(outputs),
                                    "scale": scale})
    if not spec.size:
        spec.size = round(max(study.diagonal() / 20.0, 1e-6), 6)
    report.used["size"] = spec.size
    report.before = study.summary()
    strange = [item for item in outputs if Path(item).suffix.lower() not in FORMATS]
    if strange:
        return report.fail("BAD_FORMAT", f"формат не поддерживается: {strange}. "
                           f"Известны {', '.join(FORMATS)}")
    if spec.dimension == 3:
        open_bodies = [body.name for body in study.bodies if not body.is_solid]
        if open_bodies:
            return report.fail("NOT_SOLID", f"тела {open_bodies} — поверхности, "
                               f"объёмной сетки у них не будет. Вылечите их или "
                               f"уберите", bodies=open_bodies)
    unknown = sorted(set(spec.local) - set(study.groups))
    if unknown:
        return report.fail("NO_GROUP", f"размер задан для групп, которых нет: {unknown}")
    if not study.glued and len(study.bodies) > 1:
        report.note("NOT_GLUED", "тела не склеены: сетки на стыках не совпадут. "
                    "Если детали должны работать вместе — сначала «Склеить»",
                    WARNING)

    python = python or find_python()[0]
    if not python:
        return report.fail("NO_MESHER", find_python()[1])

    folder = Path(keep_job) if keep_job else Path(tempfile.mkdtemp(prefix="protocad-mesh-"))
    folder.mkdir(parents=True, exist_ok=True)
    try:
        job, names = _job(study, spec, outputs, scale, folder)
        report.used["names"] = names
        job_path = folder / "job.json"
        job_path.write_text(json.dumps(job, ensure_ascii=False, indent=1),
                            encoding="utf-8")
        try:
            finished = subprocess.run([python, str(RUNNER), str(job_path)],
                                      capture_output=True, text=True,
                                      timeout=timeout)
        except subprocess.TimeoutExpired:
            return report.fail("MESH_TIMEOUT",
                               f"построитель не уложился в {timeout:.0f} с — "
                               f"увеличьте размер элемента или упростите модель")
        answer_path = Path(job["result"])
        if not answer_path.is_file():
            tail = (finished.stderr or finished.stdout or "").strip()[-600:]
            return report.fail("MESHER_CRASHED",
                               f"построитель сетки упал, не ответив"
                               + (f": {tail}" if tail else ""))
        answer = json.loads(answer_path.read_text(encoding="utf-8"))
    finally:
        if not keep_job:
            shutil.rmtree(folder, ignore_errors=True)

    for line in answer.get("log") or ():
        report.note("MESHER_LOG", line, WARNING if line.startswith("Warning") else ERROR)
    for line in answer.get("notes") or ():
        report.note("MESHER_NOTE", line, WARNING)
    report.after = {"stats": answer.get("stats") or {},
                    "quality": answer.get("quality") or {},
                    "files": answer.get("files") or [],
                    "elapsed": answer.get("elapsed", 0.0)}
    if not answer.get("ok"):
        report.ok = False
        report.message = answer.get("message") or "сетка не построена"
        report.note("MESH_FAILED", report.message, ERROR)
        return report
    quality = answer.get("quality") or {}
    report.message = answer.get("message", "")
    if quality:
        report.message += (f"; качество minSICN: мин {quality['min']:.3f}, "
                           f"среднее {quality['mean']:.3f}")
        if quality.get("inverted"):
            report.ok = False
            report.note("INVERTED_ELEMENTS",
                        f"вывернутых элементов {quality['inverted']} — по такой "
                        f"сетке считать нельзя", ERROR)
        elif quality.get("poor"):
            report.note("POOR_ELEMENTS",
                        f"плохих элементов (minSICN < 0,1): {quality['poor']} "
                        f"из {quality['count']}", WARNING)
    renamed = {name: value for name, value in names.items() if name != value}
    if renamed:
        report.note("RENAMED", "в файлах группы называются так: " + ", ".join(
            f"«{name}» → {value}" for name, value in renamed.items()), INFO)
    return report


def _measure(shape, volume: bool) -> tuple:
    props = GProp_GProps()
    if volume:
        BRepGProp.VolumeProperties_s(shape, props)
    else:
        BRepGProp.SurfaceProperties_s(shape, props)
    centre = props.CentreOfMass()
    return [centre.X(), centre.Y(), centre.Z()], props.Mass()


def _job(study, spec: MeshSpec, outputs, scale: float, folder: Path) -> tuple:
    """Задание построителю и перевод имён групп в имена для решателя."""
    from .io import write_brep

    brep = folder / "model.brep"
    write_brep(study, brep)
    body_names = [body.name for body in study.bodies]
    face_groups = [group for group in study.groups.values()
                   if group.kind == FACE_GROUP and group.faces]
    body_groups = [group for group in study.groups.values()
                   if group.kind == BODY_GROUP and group.bodies]
    names = solver_names(body_names + [group.name for group in body_groups]
                         + [group.name for group in face_groups])
    number = {name: index for index, name in enumerate(body_names)}
    bodies = []
    for body in study.bodies:
        solids = []
        for solid in solids_of(body.shape):
            centre, volume = _measure(solid, True)
            solids.append({"center": centre, "volume": volume})
        bodies.append({"name": body.name, "solver_name": names[body.name],
                       "solids": solids})
    groups = []
    for group in face_groups:
        faces = []
        for index in sorted(group.faces):
            centre, area = _measure(study.face(index), False)
            owners = study.owners(index)
            faces.append({"center": centre, "area": area,
                          "body": number.get(owners[0], -1) if owners else -1})
        groups.append({"name": group.name, "solver_name": names[group.name],
                       "faces": faces})
    materials = [{"name": group.name, "solver_name": names[group.name],
                  "bodies": sorted(number[name] for name in group.bodies
                                   if name in number)}
                 for group in body_groups]
    local = {group: value for group, value in spec.local.items()}
    job = {
        "name": study.name,
        "title": solver_names([study.name])[study.name],
        "brep": str(brep),
        "result": str(folder / "result.json"),
        "tolerance": max(study.diagonal() * 1e-6, 1e-7),
        "bodies": bodies,
        "face_groups": groups,
        "body_groups": materials,
        "spec": {**spec.to_dict(), "local": local},
        "outputs": list(outputs),
        "scale": scale,
    }
    return job, names
