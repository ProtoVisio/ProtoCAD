"""Построение сетки в ОТДЕЛЬНОМ процессе на gmsh.

Запускается питоном, у которого есть gmsh и numpy:

    python gmsh_runner.py job.json

и ничего не импортирует из ProtoCAD — у этого питона ядра OCP может не
быть вовсе. Разговор только файлами: задание в JSON, геометрия в BREP,
итог — JSON рядом.

Отдельный процесс по трём причинам, и каждой достаточно:

* gmsh распространяется под GPL-2+; ProtoCAD его не импортирует, а
  запускает как отдельную программу — так же, как это делает FreeCAD FEM;
* в колесе gmsh своя сборка OCCT, у OCP своя; две сборки в одном
  процессе — та же беда, что с FreeCAD (`docs/08_ENGINE_BACKEND.md`, §16.3);
* построитель на плохой геометрии может упасть целиком — и уронить окно,
  если живёт в нём.

Грани и тела gmsh нумерует по-своему, поэтому группы узнаются по ГЕОМЕТРИИ:
центру масс и площади (объёму) — теми же числами, что посчитал ProtoCAD.
Не нашлась грань или нашлись две — это отказ, а не догадка: граничное
условие на соседней грани хуже, чем никакого.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import traceback

#: Коды элементов gmsh → имя для итога и тип CalculiX.
ELEMENTS = {
    4: ("tetra4", "C3D4"),
    11: ("tetra10", "C3D10"),
    2: ("triangle3", ""),
    9: ("triangle6", ""),
    1: ("line2", ""),
    8: ("line3", ""),
    15: ("point", ""),
    5: ("hexa8", "C3D8"),
    6: ("prism6", "C3D6"),
    7: ("pyramid5", ""),
}

#: Порядок узлов квадратичного тетраэдра. У gmsh узлы 8 и 9 лежат на
#: рёбрах (2,3) и (1,3), у CalculiX/Abaqus — наоборот. Перепутать — значит
#: получить вывернутые элементы, которые решатель примет молча.
TET10_TO_CCX = [0, 1, 2, 3, 4, 5, 6, 7, 9, 8]

#: Грани тетраэдра по CalculiX (номера углов с нуля): S1..S4.
TET_FACES = [(0, 1, 2), (0, 3, 1), (1, 3, 2), (2, 3, 0)]

#: Алгоритмы объёмной сетки gmsh.
ALGORITHMS_3D = {"delaunay": 1, "frontal": 4, "hxt": 10}


class JobError(RuntimeError):
    pass


def main() -> int:
    if len(sys.argv) < 2:
        print("использование: gmsh_runner.py job.json", file=sys.stderr)
        return 2
    with open(sys.argv[1], encoding="utf-8") as stream:
        job = json.load(stream)
    started = time.perf_counter()
    result = {"ok": False, "message": "", "files": [], "log": [], "stats": {},
              "quality": {}, "notes": []}
    try:
        import gmsh
    except Exception as failure:  # noqa: BLE001
        result["message"] = f"gmsh не загрузился: {failure}"
        _finish(job, result, started)
        return 1
    gmsh.initialize(["gmsh", "-noenv"], readConfigFiles=False)
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.logger.start()
        run(gmsh, job, result)
        result["ok"] = True
    except JobError as failure:
        result["message"] = str(failure)
    except Exception as failure:  # noqa: BLE001 — gmsh бросает Exception
        result["message"] = f"построитель сетки отказал: {failure}"
        result["notes"].append(traceback.format_exc()[-1500:])
    finally:
        try:
            result["log"] = [line for line in gmsh.logger.get()
                             if line.startswith(("Warning", "Error"))][-60:]
        except Exception:  # noqa: BLE001
            pass
        gmsh.finalize()
    _finish(job, result, started)
    return 0 if result["ok"] else 1


def _finish(job, result, started) -> None:
    result["elapsed"] = round(time.perf_counter() - started, 3)
    with open(job["result"], "w", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=1)


def run(gmsh, job: dict, result: dict) -> None:
    import numpy as np

    spec = job["spec"]
    gmsh.model.add("protocad")
    gmsh.model.occ.importShapes(job["brep"], highestDimOnly=False)
    gmsh.model.occ.synchronize()

    tolerance = float(job["tolerance"])
    volumes = _match_bodies(gmsh, job, tolerance)
    surfaces = _match_faces(gmsh, job, volumes, tolerance)
    dimension = int(spec.get("dimension", 3))

    physical = _physical_groups(gmsh, job, volumes, surfaces, dimension)
    _sizes(gmsh, job, spec, surfaces)
    gmsh.model.mesh.generate(dimension)
    order = int(spec.get("order", 1))
    if order > 1:
        gmsh.model.mesh.setOrder(order)

    result["stats"] = _stats(gmsh, physical)
    result["quality"] = _quality(gmsh, dimension)
    if result["quality"].get("inverted"):
        result["notes"].append(
            f"вывернутых элементов: {result['quality']['inverted']} — считать "
            f"по такой сетке нельзя. Уменьшите размер элемента у тонких мест "
            f"или уберите мелочи")

    scale = float(job.get("scale", 1.0))
    for output in job["outputs"]:
        lower = output.lower()
        if lower.endswith(".inp"):
            _write_ccx(gmsh, np, job, output, volumes, surfaces, scale, result)
        else:
            gmsh.option.setNumber("Mesh.ScalingFactor", scale)
            if lower.endswith(".msh"):
                gmsh.option.setNumber("Mesh.MshFileVersion",
                                      float(spec.get("msh_version", 4.1)))
            gmsh.write(output)
            gmsh.option.setNumber("Mesh.ScalingFactor", 1.0)
        result["files"].append(output)
    result["message"] = (f"узлов {result['stats']['nodes']}, элементов "
                         f"{sum(result['stats']['elements'].values())}")


# --- опознание -------------------------------------------------------------


def _close(first, second, tolerance: float) -> bool:
    return math.dist(first, second) <= tolerance


def _match_bodies(gmsh, job: dict, tolerance: float) -> dict:
    """Номера объёмов gmsh по телам: {номер тела: [объёмы]}."""
    available = []
    for _dim, tag in gmsh.model.getEntities(3):
        available.append((tag, gmsh.model.occ.getCenterOfMass(3, tag),
                          gmsh.model.occ.getMass(3, tag)))
    found = {}
    for number, body in enumerate(job["bodies"]):
        tags = []
        for solid in body["solids"]:
            hits = [tag for tag, centre, volume in available
                    if _close(centre, solid["center"], tolerance)
                    and abs(volume - solid["volume"]) <= 1e-6 * max(abs(volume), 1.0)]
            if len(hits) != 1:
                raise JobError(
                    f"тело «{body['name']}» не опознано в построителе сетки "
                    f"(совпадений {len(hits)}) — геометрия пришла не той, что "
                    f"отправлена")
            tags.append(hits[0])
        found[number] = tags
    return found


def _match_faces(gmsh, job: dict, volumes: dict, tolerance: float) -> dict:
    """Поверхности gmsh по группам: {имя группы: [(поверхность, объём-хозяин)]}."""
    measured = {}
    for _dim, tag in gmsh.model.getEntities(2):
        measured[tag] = (gmsh.model.occ.getCenterOfMass(2, tag),
                         gmsh.model.occ.getMass(2, tag))
    owners = {}
    for body, tags in volumes.items():
        for volume in tags:
            _up, down = gmsh.model.getAdjacencies(3, volume)
            for surface in down:
                owners.setdefault(int(surface), []).append((body, volume))
    found = {}
    for group in job["face_groups"]:
        members = []
        for face in group["faces"]:
            hits = [tag for tag, (centre, area) in measured.items()
                    if _close(centre, face["center"], tolerance)
                    and abs(area - face["area"]) <= 1e-6 * max(abs(area), 1.0)]
            if face.get("body", -1) >= 0 and len(hits) > 1:
                hits = [tag for tag in hits
                        if any(body == face["body"] for body, _ in owners.get(tag, ()))]
            if len(hits) != 1:
                raise JobError(
                    f"группа «{group['name']}»: грань у {face['center']} не "
                    f"опознана в построителе сетки (совпадений {len(hits)})")
            owner = -1
            for body, volume in owners.get(hits[0], ()):
                if body == face.get("body", -1) or owner < 0:
                    owner = volume
            members.append((hits[0], owner))
        found[group["name"]] = members
    return found


def _physical_groups(gmsh, job: dict, volumes: dict, surfaces: dict,
                     dimension: int) -> dict:
    """Физические группы. Каждый объём — ровно в ОДНОЙ объёмной группе.

    В двух группах сразу gmsh пишет элементы объёма в часть форматов
    дважды, и решатель получает удвоенную жёсткость. Поэтому материалы
    (группы тел) забирают свои тела, а остальные тела идут группами по
    своим именам.
    """
    made = {}
    taken = set()
    if dimension == 3:
        for group in job["body_groups"]:
            tags = []
            for body in group["bodies"]:
                if body in taken:
                    continue
                taken.add(body)
                tags.extend(volumes.get(body, ()))
            if tags:
                made[group["solver_name"]] = (3, gmsh.model.addPhysicalGroup(
                    3, sorted(set(tags)), name=group["solver_name"]))
        for number, body in enumerate(job["bodies"]):
            if number in taken or not volumes.get(number):
                continue
            made[body["solver_name"]] = (3, gmsh.model.addPhysicalGroup(
                3, sorted(set(volumes[number])), name=body["solver_name"]))
    for group in job["face_groups"]:
        tags = sorted({surface for surface, _owner in surfaces[group["name"]]})
        if tags:
            made[group["solver_name"]] = (2, gmsh.model.addPhysicalGroup(
                2, tags, name=group["solver_name"]))
    if dimension == 2:
        grouped = {surface for members in surfaces.values()
                   for surface, _owner in members}
        for number, body in enumerate(job["bodies"]):
            rest = set()
            for volume in volumes.get(number, ()):
                _up, down = gmsh.model.getAdjacencies(3, volume)
                rest.update(int(tag) for tag in down if int(tag) not in grouped)
            if rest:
                made[body["solver_name"]] = (2, gmsh.model.addPhysicalGroup(
                    2, sorted(rest), name=body["solver_name"]))
    return made


def _sizes(gmsh, job: dict, spec: dict, surfaces: dict) -> None:
    size = float(spec["size"])
    gmsh.option.setNumber("Mesh.MeshSizeMax", size)
    gmsh.option.setNumber("Mesh.MeshSizeMin", float(spec.get("min_size", 0.0)))
    curvature = int(spec.get("curvature", 0))
    if curvature > 0:
        gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", curvature)
    gmsh.option.setNumber("Mesh.Algorithm3D",
                          ALGORITHMS_3D.get(spec.get("algorithm", "delaunay"), 1))
    if spec.get("optimize", True):
        gmsh.option.setNumber("Mesh.OptimizeNetgen", 1)
    gmsh.option.setNumber("Mesh.ElementOrder", 1)
    local = spec.get("local") or {}
    fields = []
    for name, value in local.items():
        members = surfaces.get(name)
        if not members:
            continue
        field = gmsh.model.mesh.field.add("Constant")
        gmsh.model.mesh.field.setNumbers(
            field, "SurfacesList", sorted({surface for surface, _ in members}))
        gmsh.model.mesh.field.setNumber(field, "VIn", float(value))
        gmsh.model.mesh.field.setNumber(field, "VOut", 1e22)
        gmsh.model.mesh.field.setNumber(field, "IncludeBoundary", 1)
        fields.append(field)
    if fields:
        smallest = gmsh.model.mesh.field.add("Min")
        gmsh.model.mesh.field.setNumbers(smallest, "FieldsList", fields)
        gmsh.model.mesh.field.setAsBackgroundMesh(smallest)


# --- итог ------------------------------------------------------------------


def _stats(gmsh, physical: dict) -> dict:
    nodes = gmsh.model.mesh.getNodes()[0]
    elements = {}
    for kind in gmsh.model.mesh.getElementTypes():
        tags, _nodes = gmsh.model.mesh.getElementsByType(kind)
        name = ELEMENTS.get(int(kind), (f"type{int(kind)}", ""))[0]
        elements[name] = elements.get(name, 0) + len(tags)
    groups = {}
    for name, (dim, tag) in physical.items():
        count = 0
        for entity in gmsh.model.getEntitiesForPhysicalGroup(dim, tag):
            _types, tags, _nodes = gmsh.model.mesh.getElements(dim, int(entity))
            count += sum(len(item) for item in tags)
        groups[name] = count
    return {"nodes": len(nodes), "elements": elements, "groups": groups}


def _quality(gmsh, dimension: int) -> dict:
    """Качество по minSICN: 1 — правильный элемент, ≤ 0 — вывернутый."""
    import numpy as np

    tags = []
    for kind in gmsh.model.mesh.getElementTypes(dimension):
        element_tags, _nodes = gmsh.model.mesh.getElementsByType(kind)
        tags.extend(element_tags)
    if not tags:
        return {}
    values = np.asarray(gmsh.model.mesh.getElementQualities(tags, "minSICN"))
    return {"measure": "minSICN", "min": float(values.min()),
            "mean": float(values.mean()),
            "poor": int((values < 0.1).sum()),
            "inverted": int((values <= 0.0).sum()),
            "count": int(len(values))}


# --- CalculiX ---------------------------------------------------------------


def _write_ccx(gmsh, np, job: dict, path: str, volumes: dict, surfaces: dict,
               scale: float, result: dict) -> None:
    """Входной файл CalculiX/Abaqus: узлы, объёмные элементы, наборы.

    Свой писатель, а не `gmsh.write(".inp")`: тот кладёт группы граней
    плоскими элементами CPS3, и CalculiX считает их настоящими элементами
    плоского напряжённого состояния — модель молча меняется. Здесь группы
    граней уходят тем, чем их используют: набором узлов (``*NSET``) для
    закреплений и поверхностью по граням элементов (``*SURFACE``) для
    давления.
    """
    tags, coords, _ = gmsh.model.mesh.getNodes()
    tags = np.asarray(tags, dtype=np.int64)
    coords = np.asarray(coords, dtype=float).reshape(-1, 3) * scale
    # Только латиница: CalculiX читает входной файл как ASCII.
    lines = [f"** ProtoCAD: {job.get('title', '')}",
             f"** units: mm x {scale:g}",
             "*NODE, NSET=NALL"]
    lines.extend(f"{tag}, {x:.12g}, {y:.12g}, {z:.12g}"
                 for tag, (x, y, z) in zip(tags.tolist(), coords.tolist()))

    tets = []            # (номер элемента, углы) — для поиска граней
    by_volume = {}
    elsets = []
    for number, body in enumerate(job["bodies"]):
        name = body["solver_name"]
        written = False
        for volume in volumes.get(number, ()):
            kinds, element_tags, element_nodes = gmsh.model.mesh.getElements(3, volume)
            for kind, etags, enodes in zip(kinds, element_tags, element_nodes):
                label, ccx = ELEMENTS.get(int(kind), (str(kind), ""))
                if not ccx:
                    raise JobError(f"элементы {label} CalculiX не принимает")
                width = int(len(enodes) // len(etags))
                table = np.asarray(enodes, dtype=np.int64).reshape(-1, width)
                if int(kind) == 11:
                    table = table[:, TET10_TO_CCX]
                etags = np.asarray(etags, dtype=np.int64)
                lines.append(f"*ELEMENT, TYPE={ccx}, ELSET={name}")
                lines.extend(", ".join(str(value) for value in (tag, *row))
                             for tag, row in zip(etags.tolist(), table.tolist()))
                written = True
                if int(kind) in (4, 11):
                    tets.append((etags, table[:, :4], volume))
                by_volume.setdefault(volume, []).append(etags)
        if written:
            elsets.append(name)
    lines.append("*ELSET, ELSET=EALL")
    lines.extend(_rows(elsets))
    for group in job["body_groups"]:
        members = [job["bodies"][body]["solver_name"] for body in group["bodies"]
                   if job["bodies"][body]["solver_name"] in elsets]
        if members:
            lines.append(f"*ELSET, ELSET={group['solver_name']}")
            lines.extend(_rows(members))

    missing = 0
    for group in job["face_groups"]:
        name = group["solver_name"]
        members = surfaces.get(group["name"], ())
        node_set = set()
        triangles = []
        owners = set()
        for surface, owner in members:
            kinds, _etags, enodes = gmsh.model.mesh.getElements(2, surface)
            for kind, nodes in zip(kinds, enodes):
                count = gmsh.model.mesh.getElementProperties(int(kind))[3]
                table = np.asarray(nodes, dtype=np.int64).reshape(-1, count)
                node_set.update(table.reshape(-1).tolist())
                triangles.append((table[:, :3], owner))
            owners.add(owner)
        lines.append(f"*NSET, NSET={name}")
        lines.extend(_rows(sorted(node_set)))
        faces = _element_faces(np, tets, triangles)
        missing += sum(1 for item in faces if item is None)
        found = [item for item in faces if item is not None]
        if found:
            lines.append(f"*SURFACE, NAME={name}, TYPE=ELEMENT")
            lines.extend(f"{element}, S{side}" for element, side in found)
    if missing:
        result["notes"].append(
            f"CalculiX: {missing} треугольников групп не нашли свой элемент — "
            f"такие части поверхностей в *SURFACE не вошли")
    # Во временный файл и переименование: оборванная запись не должна
    # оставлять на диске файл, похожий на готовый.
    draft = path + ".part"
    with open(draft, "w", encoding="ascii", newline="\n") as stream:
        stream.write("\n".join(lines) + "\n")
    os.replace(draft, path)


def _rows(values, per_line: int = 8) -> list:
    values = [str(value) for value in values]
    return [", ".join(values[index:index + per_line])
            for index in range(0, len(values), per_line)]


def _element_faces(np, tets, triangles) -> list:
    """Треугольники поверхности → (элемент, номер грани) по CalculiX.

    Ищется только среди элементов объёма-хозяина грани: у грани на стыке
    двух тел элементы есть с обеих сторон, а давление прикладывают к одной.
    """
    answers = []
    by_owner = {}
    for corners, owner in triangles:
        by_owner.setdefault(owner, []).append(corners)
    for owner, chunks in by_owner.items():
        corners = np.concatenate(chunks)
        pool = [(etags, table) for etags, table, volume in tets if volume == owner]
        if not pool:
            answers.extend([None] * len(corners))
            continue
        etags = np.concatenate([item[0] for item in pool])
        table = np.concatenate([item[1] for item in pool])
        sides = np.stack([table[:, list(face)] for face in TET_FACES], axis=1)
        keys = np.sort(sides.reshape(-1, 3), axis=1)
        wanted = np.sort(corners, axis=1)
        unique, inverse = np.unique(np.concatenate([keys, wanted]), axis=0,
                                    return_inverse=True)
        inverse = inverse.reshape(-1)
        known, asked = inverse[:len(keys)], inverse[len(keys):]
        # Первое вхождение каждой грани: запись в обратном порядке оставляет
        # в ячейке самое раннее место.
        first = np.full(len(unique), -1, dtype=np.int64)
        first[known[::-1]] = np.arange(len(keys), dtype=np.int64)[::-1]
        places = first[asked]
        for place in places.tolist():
            if place < 0:
                answers.append(None)
                continue
            element, side = divmod(place, 4)
            answers.append((int(etags[element]), side + 1))
    return answers


if __name__ == "__main__":
    raise SystemExit(main())
