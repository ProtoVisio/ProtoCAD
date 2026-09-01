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


def _apply(matrix: np.ndarray, points: np.ndarray) -> np.ndarray:
    return (points @ matrix[:3, :3].T + matrix[:3, 3]).astype(np.float32)


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
) -> Preview:
    """Собрать просмотровые данные из состава изделия.

    Идентификатор каждого тела ведёт к вхождению: по нему ПРОТО находит ЭРИ и
    связанную запись. Ради этой адресации стабильные идентификаторы и
    протянуты через все слои.
    """
    started = time.perf_counter()
    mesh_cache: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    edge_cache: dict[str, np.ndarray] = {}
    vertex_cache: dict[str, np.ndarray] = {}
    chunks: dict[str, list[np.ndarray]] = {
        "pos": [], "nrm": [], "ids": [], "faces": [],
        "edge": [], "edge_ids": [], "edge_index": [],
        "vertex": [], "vertex_ids": [],
    }
    id_to_object: dict[int, dict] = {}
    face_to_object: dict[int, dict] = {}
    tessellated = 0
    instances = 0
    next_face_id = 1

    def emit(item: Item, matrix: np.ndarray, label: str, occurrence_id: str) -> None:
        nonlocal tessellated, instances, next_face_id
        # Деталь, построенная ДВИЖКОМ, приносит готовую сетку числами:
        # разбивать её ядром в нашем процессе не надо и нельзя — второй
        # сборки OCCT здесь быть не должно (§16.3).
        from_engine = item.engine_mesh() if hasattr(item, "engine_mesh") else None
        shape = item.shape
        if from_engine is None and shape is None:
            return
        if item.stable_id not in mesh_cache:
            if from_engine is not None:
                mesh_cache[item.stable_id] = from_engine
                if with_edges:
                    edge_cache[item.stable_id] = _engine_edges(item)
                    vertex_cache[item.stable_id] = None
            else:
                mesh_cache[item.stable_id] = kernel.tessellate_faces(
                    shape, deflection)
                if with_edges:
                    edge_cache[item.stable_id] = kernel.edge_segments(
                        shape, edge_deflection, with_index=True
                    )
                    vertex_cache[item.stable_id] = kernel.vertices(shape)
            tessellated += 1
        base_pos, base_nrm, base_faces = mesh_cache[item.stable_id]
        if len(base_pos) == 0:
            return

        instances += 1
        identifier = len(id_to_object) + 1
        id_to_object[identifier] = {
            "label": label,
            "occurrence": occurrence_id,
            "item": item.stable_id,
            "designation": item.designation,
            "name": item.name,
            "proto_id": item.proto_id,
            "faces": int(base_faces.max()) + 1 if len(base_faces) else 0,
        }
        positions = _apply(matrix, base_pos)
        # Нормали поворачиваются, но не переносятся.
        normals = (base_nrm @ matrix[:3, :3].T).astype(np.float32)
        chunks["pos"].append(positions)
        chunks["nrm"].append(normals)
        chunks["ids"].append(np.full(len(positions), identifier, dtype=np.uint32))

        # Номера граней сквозные по всей сцене: одинаковые детали разделяют
        # тесселяцию, но не выбор — щёлкнув по грани одного экземпляра,
        # выделять оба было бы неверно.
        face_count = int(base_faces.max()) + 1 if len(base_faces) else 0
        chunks["faces"].append((base_faces + next_face_id).astype(np.uint32))
        for local in range(face_count):
            face_to_object[next_face_id + local] = {
                "body": identifier,
                "face": local,
                "item": item.stable_id,
                "label": f"{label}: грань {local + 1}",
            }
        next_face_id += face_count

        if with_edges:
            cached_edges = edge_cache.get(item.stable_id)
            base_edges, base_edge_index = (
                cached_edges if cached_edges is not None else (None, None)
            )
            if base_edges is not None and len(base_edges):
                chunks["edge"].append(_apply(matrix, base_edges))
                chunks["edge_ids"].append(
                    np.full(len(base_edges), identifier, dtype=np.uint32)
                )
                chunks["edge_index"].append(base_edge_index.astype(np.uint32))
            base_vertices = vertex_cache.get(item.stable_id)
            if base_vertices is not None and len(base_vertices):
                chunks["vertex"].append(_apply(matrix, base_vertices))
                chunks["vertex_ids"].append(
                    np.full(len(base_vertices), identifier, dtype=np.uint32)
                )

    def walk(assembly: Assembly, parent: np.ndarray) -> None:
        for occurrence in assembly.placements:
            matrix = parent @ occurrence.transform
            item = occurrence.item
            if isinstance(item, Assembly):
                walk(item, matrix)
            else:
                emit(item, matrix, occurrence.label, occurrence.stable_id)

    if isinstance(root, Assembly):
        walk(root, np.eye(4))
    else:
        emit(root, np.eye(4), root.label, "")

    def stack(key: str, width: int, dtype) -> np.ndarray:
        if chunks[key]:
            return np.concatenate(chunks[key])
        return np.zeros((0, width), dtype) if width > 1 else np.zeros(0, dtype)

    positions = stack("pos", 3, np.float32)
    normals = stack("nrm", 3, np.float32)
    ids = stack("ids", 1, np.uint32)
    face_ids = stack("faces", 1, np.uint32)
    edge_positions = stack("edge", 3, np.float32)
    edge_ids = stack("edge_ids", 1, np.uint32)
    edge_indices = stack("edge_index", 1, np.uint32)
    vertex_positions = stack("vertex", 3, np.float32)
    vertex_ids = stack("vertex_ids", 1, np.uint32)

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
