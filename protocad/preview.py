"""Просмотровые данные: тесселяция состава в буферы для GPU.

Кладутся внутрь файла ``.prcad*``, поэтому ПРОТО показывает модель, не имея
ни ядра геометрии, ни решателя.

Тесселяция считается ОДИН раз при сборке сцены, дальше кадр — это один вызов
отрисовки. Именно перенос работы из «каждый кадр» в «один раз при открытии»
дал разницу с обходом сценографа: 0,6 мс против 43 мс на приборе из 1800
компонентов (Spike 3).

Одинаковые изделия тесселируются однократно: на плате из тысячи ЭРИ
уникальных форм всего десяток.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from . import kernel
from .model import Assembly, Item



@dataclass
class Preview:
    """Готовые к загрузке в GPU буферы плюс сведения о разборе."""

    positions: np.ndarray
    normals: np.ndarray
    ids: np.ndarray
    edge_positions: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 3), np.float32)
    )
    edge_ids: np.ndarray = field(default_factory=lambda: np.zeros(0, np.uint32))
    id_to_object: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)
    # Номер ГРАНИ у каждой вершины — отдельным массивом, а не подмешанный в
    # ``ids``. Смешивать нельзя: ``ids`` уходит в файл и по нему ПРОТО
    # находит вхождение и запись ЭРИ. Поменять его смысл значило бы сломать
    # чтение всех уже сохранённых контейнеров ради удобства редактора.
    face_ids: np.ndarray = field(default_factory=lambda: np.zeros(0, np.uint32))
    face_to_object: dict = field(default_factory=dict)
    # Номер ребра у каждой вершины линейного буфера. Без него по точке на
    # экране видно, что это ребро, но не какое именно — а операциям нужно
    # именно «какое».
    edge_indices: np.ndarray = field(default_factory=lambda: np.zeros(0, np.uint32))
    # Вершины детали для привязки. Пишутся отдельно: в буфере треугольников
    # их не отличить от точек разбиения.
    vertex_positions: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 3), np.float32)
    )
    vertex_ids: np.ndarray = field(default_factory=lambda: np.zeros(0, np.uint32))

    @property
    def triangle_count(self) -> int:
        return len(self.positions) // 3

    @property
    def edge_count(self) -> int:
        return len(self.edge_positions) // 2

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        if len(self.positions) == 0:
            return np.zeros(3, np.float32), np.ones(3, np.float32)
        return self.positions.min(axis=0), self.positions.max(axis=0)


def _engine_edges(item):
    """Рёбра детали от движка: ``(точки, номера)`` или ``None``.

    Движок присылает рёбра отрезками — по две точки на отрезок, — и в этом
    же виде их ждёт сцена. Пересчитывать нечего.
    """
    mesh = getattr(getattr(item, "document", None), "mesh", None)
    if mesh is None or not mesh.edge_positions:
        return None
    points = np.asarray(mesh.edge_positions, np.float32).reshape(-1, 3)
    numbers = np.asarray(mesh.edge_ids, np.uint32)
    return points, numbers


def build(
    root: Item,
    deflection: float = 0.1,
    with_edges: bool = True,
    edge_deflection: float = 0.05,
    cache: dict | None = None,
) -> Preview:
    """Собрать просмотровые данные из состава изделия.

    Идентификатор каждого тела ведёт к вхождению: по нему ПРОТО находит ЭРИ и
    связанную запись. Ради этой адресации стабильные идентификаторы и
    протянуты через все слои.

    ``cache`` — словарь, который вызывающий держит между вызовами. Разбивка
    определения на треугольники — самое дорогое в показе, и в окне сборки
    она нужна ОДИН раз на деталь, а не на каждую правку положения: сдвиг
    вхождения меняет только матрицу. Ключ — изделие и его форма: новая
    форма (деталь поправили) разбивается заново.
    """
    started = time.perf_counter()
    held = cache if cache is not None else {}
    mesh_cache = held.setdefault("mesh", {})
    edge_cache = held.setdefault("edge", {})
    vertex_cache = held.setdefault("vertex", {})
    # Формы держатся живыми, пока их разбивка в кэше: иначе адрес
    # освобождённой формы мог бы достаться новой, и ключ совпал бы.
    alive = held.setdefault("alive", {})
    id_to_object: dict[int, dict] = {}
    face_to_object: dict[int, dict] = {}
    tessellated = 0
    instances = 0
    next_face_id = 1
    #: Что куда класть: (ключ определения, матрица, номер тела, первый
    #: номер грани). Сначала обходится состав, потом буферы выделяются ОДИН
    #: раз и заполняются по местам — без промежуточных кусков и склейки:
    #: на плате в тысячу компонентов склейка стоила больше самой геометрии.
    records: list = []

    def emit(item: Item, matrix: np.ndarray, label: str, occurrence_id: str,
             path: tuple = ()) -> None:
        nonlocal tessellated, instances, next_face_id
        # Деталь, построенная ДВИЖКОМ, приносит готовую сетку числами:
        # разбивать её ядром в нашем процессе не надо и нельзя — второй
        # сборки OCCT здесь быть не должно (§16.3).
        from_engine = item.engine_mesh() if hasattr(item, "engine_mesh") else None
        shape = item.shape
        if from_engine is None and shape is None:
            return
        key = (item.stable_id, id(shape),
               id(getattr(getattr(item, "document", None), "result", None)),
               deflection, edge_deflection)
        if key not in mesh_cache:
            alive[key] = (shape, getattr(getattr(item, "document", None), "result", None))
            if from_engine is not None:
                mesh_cache[key] = from_engine
                if with_edges:
                    edge_cache[key] = _engine_edges(item)
                    vertex_cache[key] = None
            else:
                mesh_cache[key] = kernel.tessellate_faces(shape, deflection)
                if with_edges:
                    edge_cache[key] = kernel.edge_segments(
                        shape, edge_deflection, with_index=True
                    )
                    vertex_cache[key] = kernel.vertices(shape)
            tessellated += 1
        base_pos, _base_nrm, base_faces = mesh_cache[key]
        if len(base_pos) == 0:
            return

        instances += 1
        identifier = len(id_to_object) + 1
        face_count = int(base_faces.max()) + 1 if len(base_faces) else 0
        id_to_object[identifier] = {
            "label": label,
            "occurrence": occurrence_id,
            "item": item.stable_id,
            "designation": item.designation,
            "name": item.name,
            "proto_id": item.proto_id,
            "faces": face_count,
            # Номера граней тела в сцене — подряд с этого. По ним вхождение
            # выделяется целиком, не перебирая словарь всех граней сцены.
            "first_face": next_face_id,
            # Путь вхождений от корня до детали. Во вложенной сборке
            # щелчок попадает в деталь подсборки, а сопрягают и двигают
            # вхождение верхнего уровня — без пути его не найти.
            "path": list(path) or ([occurrence_id] if occurrence_id else []),
        }
        # Номера граней сквозные по всей сцене: одинаковые детали разделяют
        # тесселяцию, но не выбор — щёлкнув по грани одного экземпляра,
        # выделять оба было бы неверно.
        for local in range(face_count):
            face_to_object[next_face_id + local] = {
                "body": identifier,
                "face": local,
                "item": item.stable_id,
                "label": f"{label}: грань {local + 1}",
            }
        records.append((key, matrix, identifier, next_face_id))
        next_face_id += face_count

    def walk(assembly: Assembly, parent: np.ndarray, trail: tuple) -> None:
        for occurrence in assembly.placements:
            matrix = parent @ occurrence.transform
            item = occurrence.item
            path = trail + (occurrence.stable_id,)
            if isinstance(item, Assembly):
                walk(item, matrix, path)
            else:
                emit(item, matrix, occurrence.label, occurrence.stable_id, path)

    if isinstance(root, Assembly):
        walk(root, np.eye(4), ())
    else:
        emit(root, np.eye(4), root.label, "")

    def edges_of(key):
        found = edge_cache.get(key) if with_edges else None
        return found if found is not None and found[0] is not None and len(found[0]) \
            else None

    def vertices_of(key):
        found = vertex_cache.get(key) if with_edges else None
        return found if found is not None and len(found) else None

    count = sum(len(mesh_cache[key][0]) for key, *_ in records)
    edge_count = sum(len(edges_of(key)[0]) for key, *_ in records if edges_of(key))
    vertex_count = sum(len(vertices_of(key)) for key, *_ in records
                       if vertices_of(key) is not None)
    positions = np.empty((count, 3), np.float32)
    normals = np.empty((count, 3), np.float32)
    ids = np.empty(count, np.uint32)
    face_ids = np.empty(count, np.uint32)
    edge_positions = np.empty((edge_count, 3), np.float32)
    edge_ids = np.empty(edge_count, np.uint32)
    edge_indices = np.empty(edge_count, np.uint32)
    vertex_positions = np.empty((vertex_count, 3), np.float32)
    vertex_ids = np.empty(vertex_count, np.uint32)

    at = edge_at = vertex_at = 0
    for key, matrix, identifier, first_face in records:
        base_pos, base_nrm, base_faces = mesh_cache[key]
        # Счёт в float32 целиком: смешение с float64 матрицы удваивало и
        # время, и память на каждом вхождении.
        rotation = np.ascontiguousarray(matrix[:3, :3].T, dtype=np.float32)
        shift = matrix[:3, 3].astype(np.float32)
        size = len(base_pos)
        target = positions[at:at + size]
        np.matmul(base_pos, rotation, out=target)
        target += shift
        # Нормали поворачиваются, но не переносятся.
        np.matmul(base_nrm, rotation, out=normals[at:at + size])
        ids[at:at + size] = identifier
        face_ids[at:at + size] = base_faces + first_face
        at += size
        edges = edges_of(key)
        if edges is not None:
            base_edges, base_edge_index = edges
            size = len(base_edges)
            target = edge_positions[edge_at:edge_at + size]
            np.matmul(base_edges, rotation, out=target)
            target += shift
            edge_ids[edge_at:edge_at + size] = identifier
            edge_indices[edge_at:edge_at + size] = base_edge_index
            edge_at += size
        points = vertices_of(key)
        if points is not None:
            size = len(points)
            target = vertex_positions[vertex_at:vertex_at + size]
            np.matmul(points, rotation, out=target)
            target += shift
            vertex_ids[vertex_at:vertex_at + size] = identifier
            vertex_at += size

    total_bytes = sum(
        array.nbytes
        for array in (positions, normals, ids, face_ids, edge_positions, edge_ids)
    )
    return Preview(
        positions=positions,
        normals=normals,
        ids=ids,
        edge_positions=edge_positions,
        edge_ids=edge_ids,
        id_to_object=id_to_object,
        face_ids=face_ids,
        face_to_object=face_to_object,
        edge_indices=edge_indices,
        vertex_positions=vertex_positions,
        vertex_ids=vertex_ids,
        stats={
            "build_s": time.perf_counter() - started,
            "unique_tessellations": tessellated,
            "instances": instances,
            "triangles": len(positions) // 3,
            "vertices": len(positions),
            "edge_segments": len(edge_positions) // 2,
            "deflection": deflection,
            "buffer_mb": round(total_bytes / (1024 * 1024), 1),
        },
    )
