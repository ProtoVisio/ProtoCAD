"""Что окно знает о детали: сетка и подэлементы с описанием.

Окно перестаёт разбирать формы ядра. Оно спрашивает: какая грань под
номером 4, где концы ребра 7, какие есть вершины — и получает числа. По
`docs/08_ENGINE_BACKEND.md`, §10.2 и §22.1 именно это и должно доходить до
интерфейса; форм ядра здесь нет, и загружать вторую сборку OCCT окну
незачем (§16.3).

Источник один — ``FeatureResult``. Он приходит либо от движка FreeCAD,
либо от прототипа на OCP: описание у них одинаковой формы, и окну не надо
знать, кто считал.
"""

from __future__ import annotations

from protocad.engine import FeatureResult
from protocad.sketch.plane import Plane


class ModelView:
    """Деталь глазами окна. Ничего, кроме данных."""

    def __init__(self, result: FeatureResult | None = None):
        self.result = result

    # --- состав ---------------------------------------------------------

    @property
    def empty(self) -> bool:
        return self.result is None or not self.result.ok

    @property
    def mesh(self):
        return None if self.empty else self.result.mesh

    @property
    def volume(self) -> float:
        return 0.0 if self.empty else self.result.volume

    @property
    def bounds(self) -> list:
        return [] if self.empty else list(self.result.bounds)

    def _of_kind(self, kind: str) -> list:
        if self.empty:
            return []
        return [item for item in self.result.entities if item.kind == kind]

    @property
    def faces(self) -> list:
        return self._of_kind("face")

    @property
    def edges(self) -> list:
        return self._of_kind("edge")

    @property
    def vertices(self) -> list:
        return self._of_kind("vertex")

    # --- поиск по номеру, который вернул выбор мышью --------------------

    def _at(self, kind: str, index: int):
        for item in self._of_kind(kind):
            if item.index == index:
                return item
        return None

    def face(self, index: int):
        return self._at("face", int(index))

    def edge(self, index: int):
        return self._at("edge", int(index))

    def vertex(self, index: int):
        return self._at("vertex", int(index))

    # --- то, ради чего это всё --------------------------------------------

    def plane_of(self, index: int) -> Plane | None:
        """Плоскость эскиза на грани. ``None`` — грань неплоская.

        Раньше окно строило её из формы ядра, а значит, ядро приходилось
        держать в его процессе. Теперь начало, нормаль и ось X приходят
        числами, и плоскость собирается из них.
        """
        face = self.face(index)
        if face is None or not face.planar:
            return None
        origin, normal, x_direction = face.plane
        return Plane(origin, normal, x_direction, "грань")

    def straight_edge(self, index: int) -> tuple:
        """Концы прямого ребра. Пусто — ребро не прямое.

        Опорой для параллельности и перпендикуляра годится только прямое:
        к дуге эти связи не прикладывают.
        """
        edge = self.edge(index)
        if edge is None or edge.data.get("curve") != "line":
            return ()
        return tuple(edge.data["start"]), tuple(edge.data["end"])

    def points(self) -> list:
        """Все вершины детали. Для привязки и для ссылок на деталь."""
        return [tuple(item.data["point"]) for item in self.vertices]

    def name_of(self, kind: str, index: int) -> str:
        """Устойчивое имя подэлемента. Пусто — движок его не ведёт.

        Именно оно уходит в операцию: номер годен ровно до правки выше по
        дереву, а хранить ссылку одним лишь номером запрещено (§26).
        """
        item = self._at(kind, int(index))
        return getattr(item, "name", "") if item is not None else ""

    def label_of(self, kind: str, index: int) -> str:
        """Название подэлемента для строки состояния."""
        item = self._at(kind, index)
        if item is None:
            return ""
        titles = {"face": "Грань", "edge": "Ребро", "vertex": "Точка"}
        title = titles.get(kind, kind)
        if kind == "face":
            surface = item.data.get("surface", "")
            names = {"plane": "плоская", "cylinder": "цилиндрическая"}
            note = names.get(surface, surface)
            area = item.data.get("area")
            tail = f", {note}" if note else ""
            return (f"{title} {index}{tail}"
                    + (f", {area:.1f} мм²" if area else ""))
        if kind == "edge":
            curve = item.data.get("curve", "")
            names = {"line": "прямое", "circle": "круглое"}
            length = item.data.get("length")
            return (f"{title} {index}, {names.get(curve, curve)}"
                    + (f", {length:.2f} мм" if length else ""))
        point = item.data.get("point") or ()
        return f"{title} {index}: " + ", ".join(f"{value:.2f}" for value in point)


def edge_signature_of(edge, precision: int = 3) -> tuple:
    """Подпись ребра по ОПИСАНИЮ: середина и длина.

    Та же, что считает ядро (``kernel.edge_signature``): по ней операция
    находит выбранное ребро после пересчёта. Середина берётся из
    описания — у дуги центр масс не совпадает с полусуммой концов, и
    считать её здесь заново значило бы разойтись с ядром на дугах.

    Пустой кортеж — ребра нет. Это НЕ топологическое имя: правка размера
    выше по дереву сдвигает рёбра, подпись перестаёт совпадать, и
    операция обязана об этом сказать, а не скруглить не то.
    """
    if edge is None:
        return ()
    start = edge.data.get("start") or (0.0, 0.0, 0.0)
    end = edge.data.get("end") or (0.0, 0.0, 0.0)
    middle = edge.data.get("middle") or [
        (start[i] + end[i]) / 2.0 for i in range(3)]
    return tuple(round(float(value), precision) for value in middle) + (
        round(float(edge.data.get("length", 0.0)), precision),)


def from_shape(shape, feature: str = "Деталь") -> ModelView:
    """Вид детали по форме ядра.

    **Окно этим не пользуется**: описание оно получает от движка. Остаётся
    для проверок, которым нужно описание формы, посчитанной прототипом, —
    и считается ТЕМ ЖЕ кодом, что и у прототипа-движка: иначе сравнение
    двух путей сравнивало бы два разных ответа на один вопрос.
    """
    from protocad import kernel
    from protocad.engine import Status
    from protocad.engine.local_backend import _bounds, _entities, _mesh

    if shape is None or kernel.is_empty(shape):
        return ModelView(None)
    return ModelView(FeatureResult(
        status=Status.VALID,
        feature_id=feature,
        volume=kernel.volume(shape),
        bounds=list(_bounds(shape)),
        mesh=_mesh(shape),
        entities=_entities(shape, feature),
    ))
