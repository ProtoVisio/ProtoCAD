"""Параметрическая история на формах ядра. **НЕ рабочий путь детали.**

Деталь в ProtoCAD считает движок, а дерево её намерений живёт в
`protocad.document.Document` (`docs/08_ENGINE_BACKEND.md`, §5.1). Окно
сюда не обращается вовсе, и это закреплено проверкой.

Модуль остаётся по двум причинам, и обе временные:

* **сборки** ещё не переведены на движок: `protocad.model.Item.rebuild`
  строит по этому дереву формы деталей, из которых собирается сборка и её
  просмотр в контейнере;
* по нему идут проверки, сравнивающие два пути на одних числах.

Расширять его нельзя. Новое — только в `Document` и в движке.

---

То, что отличает CAD от построителя геометрии. Деталь хранит не форму, а
последовательность операций; форма — результат их выполнения. Правка эскиза
или размера операции перестраивает деталь, а не требует лепить её заново.

    Эскиз ──> Выдавливание ──> Вырез ──> Фаска ──> форма детали

Дерево пересчитывается лениво: операция знает, что устарела, и перестраивает
себя вместе со всеми последующими. Пересчёт от начала на каждое изменение
был бы расточителен уже на десятке операций.

Ошибка одной операции не роняет всё дерево: она фиксируется в её состоянии,
а деталь сохраняет последнюю удавшуюся форму. Молча подставить предыдущий
результат и сделать вид, что всё в порядке, — худшее, что может сделать CAD.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from .. import kernel
from .. import operations


class FeatureError(RuntimeError):
    """Операция не выполнена."""


def _new_id() -> str:
    return f"feat-{uuid.uuid4()}"


@dataclass
class FeatureState:
    """Что случилось при последнем выполнении операции."""

    ok: bool = False
    message: str = ""
    duration_s: float = 0.0


class Feature:
    """Операция дерева построения.

    ``execute`` получает форму, накопленную предыдущими операциями (``None``
    для первой), и возвращает новую.
    """

    kind = "feature"

    def __init__(self, name: str = ""):
        self.name = name or self.kind
        self.stable_id = _new_id()
        self.state = FeatureState()
        #: Что операция сделала не буквально по параметрам. Пустая строка —
        #: сделала ровно то, что просили. Отказ сюда НЕ пишется: для отказа
        #: есть исключение.
        self.note = ""
        self._stale = True

    def touch(self) -> None:
        """Пометить операцию требующей пересчёта."""
        self._stale = True

    @property
    def stale(self) -> bool:
        return self._stale

    # Операция либо даёт ПРОФИЛЬ (эскиз), либо меняет ТЕЛО (всё остальное).
    # Разделение появилось не сразу: пока эскиз был один и всегда первым,
    # хватало одной формы, идущей по цепочке. Как только эскизов стало два,
    # выяснилось, что второй затирал собой накопленное тело — операция
    # получала контур там, где ожидала деталь.
    produces_profile = False

    def execute(self, shape, profile=None):
        """Выполнить операцию.

        ``shape`` — накопленное тело (может быть None у первой операции),
        ``profile`` — грань последнего эскиза, если он был.
        """
        raise NotImplementedError

    def parameters(self) -> dict:
        """Управляющие параметры операции — для правки и для записи в файл."""
        return {}

    def set_parameter(self, name: str, value) -> None:
        raise FeatureError(f"{self.name}: нет параметра {name!r}")

    # --- сохранение ---

    def to_dict(self) -> dict:
        """Операция как данные. Без этого история живёт до закрытия окна."""
        return {
            "type": type(self).__name__,
            "name": self.name,
            "stable_id": self.stable_id,
            "parameters": self.parameters(),
        }

    @classmethod
    def from_dict(cls, data: dict, solver=None) -> "Feature":
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name!r}>"


def _locate(shape, source: str, plane):
    """Где сейчас объект детали с такой подписью, в координатах плоскости.

    Подпись хранит и НОМЕР объекта, и его прежнее место. Номер — главный:
    правка размера выше по дереву двигает вершину, но не меняет ни их
    число, ни порядок, и найденная по номеру вершина — та самая, даже если
    уехала на полметра. Место — запасной путь на случай, когда число
    объектов изменилось: тогда номера перестали значить что-либо, и
    остаётся искать ближайший.

    Это НЕ топологическое имя. Добавленное выше по дереву отверстие меняет
    число рёбер, и ссылка честно теряется, а не цепляется за чужой объект.
    """
    if shape is None or kernel.is_empty(shape):
        return None
    kind, _, rest = source.partition(":")
    address, _, payload = rest.partition(":")
    payload, _, which = payload.partition("#")
    try:
        index, count = (int(part) for part in address.split("/"))
    except ValueError:
        return None

    if kind == "v":
        found = kernel.vertices(shape)
        target = _numbers(payload)
        place = None
        if len(found) == count and 0 <= index < len(found):
            place = tuple(float(value) for value in found[index])
        elif target is not None:
            place = _nearest(found, target, _tolerance(shape))
        return plane.project(place) if place is not None else None

    if kind == "f":
        faces = list(kernel.iter_faces(shape))
        from ..sketch import plane as plane_module

        found = None
        if len(faces) == count and 0 <= index < len(faces):
            found = faces[index]
        else:
            for candidate in faces:
                if _face_signature(candidate) == payload:
                    found = candidate
                    break
        if found is None:
            return None
        try:
            other = plane_module.from_face(found)
        except Exception:  # noqa: BLE001
            return None
        trace = _trace(plane, other)
        if trace is None:
            return None
        return trace[1 if which == "1" else 0]

    if kind == "e":
        edges = list(kernel.iter_edges(shape))
        edge = None
        if len(edges) == count and 0 <= index < len(edges):
            edge = edges[index]
        else:
            for candidate in edges:
                if str(kernel.edge_signature(candidate)) == payload:
                    edge = candidate
                    break
        if edge is None:
            return None
        ends = kernel.straight_ends(edge)
        return plane.project(ends[1 if which == "1" else 0]) if ends else None
    return None


def _face_signature(face, precision: int = 3) -> str:
    normal = kernel.face_normal(face)
    center = kernel.face_center(face)
    offset = sum(normal[i] * center[i] for i in range(3))
    values = [round(float(value), precision) for value in normal] + [
        round(float(offset), precision)]
    return ",".join(str(value) for value in values)


def _trace(sketch_plane, face_plane, reach: float = 500.0):
    """След плоскости грани на плоскости эскиза — те же два конца, что и
    при создании ссылки. Считается здесь заново, а не запоминается: грань
    после пересчёта могла переехать, ради чего ссылка и существует."""
    import numpy as np

    normal_a = np.array(sketch_plane.normal, float)
    normal_b = np.array(face_plane.normal, float)
    along = np.cross(normal_a, normal_b)
    if float(np.linalg.norm(along)) < 1e-9:
        return None
    along = along / float(np.linalg.norm(along))
    matrix = np.array([normal_a, normal_b, along])
    right = np.array([
        float(np.dot(normal_a, np.array(sketch_plane.origin, float))),
        float(np.dot(normal_b, np.array(face_plane.origin, float))),
        0.0,
    ])
    try:
        point = np.linalg.solve(matrix, right)
    except np.linalg.LinAlgError:
        return None
    return (sketch_plane.project(tuple(point - along * reach)),
            sketch_plane.project(tuple(point + along * reach)))


def _numbers(payload: str):
    try:
        return tuple(float(value) for value in payload.split(","))
    except ValueError:
        return None


def _nearest(points, target, tolerance: float):
    """Ближайшая точка, если она заметно ближе остальных.

    Порог нужен обоюдный: слишком далёкая точка — не та вершина, а две
    одинаково близкие означают, что выбор произволен, и молча угадывать
    здесь хуже, чем признать потерю.
    """
    ranked = sorted(
        (sum((float(point[i]) - target[i]) ** 2 for i in range(3)) ** 0.5, index)
        for index, point in enumerate(points)
    )
    if not ranked or ranked[0][0] > tolerance:
        return None
    if len(ranked) > 1 and ranked[1][0] < ranked[0][0] * 2.0 + 1e-9:
        return None
    return tuple(float(value) for value in points[ranked[0][1]])


def _tolerance(shape) -> float:
    """Насколько далеко разрешено уехать вершине, чтобы её ещё узнали.

    В долях от габарита детали, а не в миллиметрах: у детали размером с
    ноготь и у корпуса метровой длины «близко» значит разное.
    """
    size = kernel.bounds(shape).size
    diagonal = sum(value ** 2 for value in size) ** 0.5
    return max(diagonal * 0.1, 1e-3)


class SketchFeature(Feature):
    """Эскиз: даёт замкнутый профиль для следующей операции."""

    kind = "Эскиз"
    produces_profile = True

    def __init__(self, sketch, name: str = "", regions=None):
        super().__init__(name)
        self.sketch = sketch
        #: Устойчивые ссылки на выбранные области. ``None`` — областей не
        #: выбирали, и профилем служит весь эскиз: наружный контур с
        #: вложенными в качестве отверстий. Это НЕ то же самое, что «все
        #: области»: выбор всех областей объединяет их и отверстия
        #: исчезают.
        self.regions = list(regions) if regions else None

    def execute(self, shape, profile=None):
        # Ссылки на деталь пересаживаются ПЕРЕД решением эскиза и по телу,
        # накопленному ДО него: эскиз опирается на то, что построено выше по
        # дереву, а не на готовую деталь. Иначе ссылка ловила бы ребро,
        # которого в её момент ещё не существует.
        self.rebind(shape)
        if not self.has_geometry:
            # Пустой эскиз — обычное промежуточное состояние: его только что
            # создали и ещё не нарисовали. Ронять из-за него пересчёт всей
            # детали нельзя; профиля просто нет, и следующая операция сама
            # скажет, что ей нечего выдавливать.
            return None
        solution = self.sketch.solve()
        if not solution.ok:
            raise FeatureError(f"{self.name}: эскиз не решён — {solution.message}")
        # Профиль возвращается ГРАНЬЮ, а не контуром: только грань несёт
        # внутренние контуры, и без неё выдавливание эскиза с отверстиями
        # давало бы сплошную пластину.
        if self.regions:
            return self.sketch.region_face(self.regions)
        return self.sketch.to_occt_face()

    def rebind(self, shape) -> list[str]:
        """Пересадить ссылки эскиза на текущее состояние тела.

        Потерянная ссылка не роняет пересчёт: эскиз остаётся там, где был,
        и помечает её. Отказ здесь был бы хуже — деталь перестала бы
        строиться целиком из-за одной опорной точки, тогда как остальной
        эскиз в порядке.
        """
        if not getattr(self.sketch, "references", None):
            return []
        return self.sketch.rebind(lambda source: _locate(shape, source, self.sketch.plane))

    @property
    def dangling(self) -> list:
        return list(getattr(self.sketch, "dangling", []))

    @property
    def has_geometry(self) -> bool:
        """Есть ли в эскизе хоть что-то, кроме вспомогательного."""
        return any(
            not segment.construction for segment in self.sketch.segments
        )

    def parameters(self) -> dict:
        """Управляющие размеры эскиза с их текущими значениями."""
        return {
            dimension.name: dimension.value for dimension in self.sketch.dimensions
        }

    def set_parameter(self, name: str, value) -> None:
        self.sketch.set_dimension(name, float(value))
        self.touch()

    def to_dict(self) -> dict:
        data = super().to_dict() | {"sketch": self.sketch.to_dict()}
        if self.regions:
            data["regions"] = [dict(reference) for reference in self.regions]
        return data

    @classmethod
    def from_dict(cls, data: dict, solver=None) -> "SketchFeature":
        from ..sketch import Sketch

        feature = cls(Sketch.from_dict(data["sketch"], solver), data.get("name", ""),
                      data.get("regions"))
        feature.stable_id = data.get("stable_id", feature.stable_id)
        return feature


#: Насколько прилив должен вырасти, чтобы считаться состоявшимся, мм³.
#: Не ноль: булева операция оставляет крохи на стыке граней.
_PAD_MINIMUM = 1e-6


class PadFeature(Feature):
    """Выдавливание профиля на заданную длину."""

    kind = "Выдавливание"

    def __init__(self, length: float, name: str = "", reverse: bool = False,
                 symmetric: bool = False, end: str = "", until=None):
        super().__init__(name)
        self.length = float(length)
        #: Концевое условие: на расстояние, насквозь, до первой/последней
        #: грани, до указанной. Пустая строка — по ``symmetric`` и длине,
        #: как было до появления условий.
        self.end = end or ""
        #: Грань для условия «до указанной».
        self.until = until
        # Концевые условия. «Вслепую» на длину — основной случай; от
        # средней плоскости выдавливает в обе стороны по половине, и это
        # не то же самое, что удвоенная длина в одну.
        self.reverse = bool(reverse)
        self.symmetric = bool(symmetric)

    def execute(self, shape, profile=None):
        # Профиль обязателен. Раньше при его отсутствии на вход шло само
        # тело — остаток тех времён, когда эскиз был первой операцией и
        # единственная форма служила и профилем, и телом. Теперь это
        # означало бы попытку выдавить готовую деталь.
        if profile is None or kernel.is_empty(profile):
            raise FeatureError(
                f"{self.name}: нет профиля для выдавливания — "
                f"эскиз выше по дереву пуст либо уже израсходован"
            )
        wanted = operations.Extrusion(
            length=self.length,
            end=self.end or (operations.MIDPLANE if self.symmetric
                             else operations.BLIND),
            reverse=self.reverse,
            until=self.until,
        )
        mode = operations.CREATE if shape is None else operations.ADD
        # Сторона выбирается по материалу, а не по одной лишь нормали:
        # грань, снятая с тела после булевой операции, бывает ориентирована
        # внутрь, и прилив уходил в деталь, ничего не добавляя.
        chosen = operations.choose_direction(shape, profile, wanted, mode)
        if chosen.reverse != wanted.reverse:
            self.note = (
                f"{self.name}: выдавлено в обратную сторону — по нормали "
                f"плоскости эскиза прилив уходил внутрь детали"
            )
        try:
            tool = operations.build_tool(profile, chosen, shape)
            result, note = operations.apply(shape, tool, mode, self.name)
        except operations.OperationError as error:
            raise FeatureError(str(error)) from error
        if note:
            self.note = note
        return result

    def parameters(self) -> dict:
        return {"length": self.length}

    def set_parameter(self, name: str, value) -> None:
        if name != "length":
            raise FeatureError(f"{self.name}: нет параметра {name!r}")
        self.length = float(value)
        self.touch()

    def to_dict(self) -> dict:
        return super().to_dict() | {
            "reverse": self.reverse, "symmetric": self.symmetric
        }

    @classmethod
    def from_dict(cls, data: dict, solver=None) -> "PadFeature":
        feature = cls(
            data["parameters"]["length"], data.get("name", ""),
            data.get("reverse", False), data.get("symmetric", False),
        )
        feature.stable_id = data.get("stable_id", feature.stable_id)
        return feature


class HoleFeature(Feature):
    """Отверстие: цилиндрический вырез насквозь."""

    kind = "Отверстие"

    def __init__(self, x: float, y: float, radius: float, name: str = ""):
        super().__init__(name)
        self.x, self.y, self.radius = float(x), float(y), float(radius)

    def execute(self, shape, profile=None):
        if shape is None:
            raise FeatureError(f"{self.name}: нет тела для выреза")
        box = kernel.bounds(shape)
        depth = (box.zmax - box.zmin) + 2.0
        tool = kernel.cylinder(self.radius, depth, (self.x, self.y, box.zmin - 1.0))
        return kernel.cut(shape, tool)

    def parameters(self) -> dict:
        return {"x": self.x, "y": self.y, "radius": self.radius}

    def set_parameter(self, name: str, value) -> None:
        if name not in ("x", "y", "radius"):
            raise FeatureError(f"{self.name}: нет параметра {name!r}")
        setattr(self, name, float(value))
        self.touch()


class ChamferFeature(Feature):
    """Фаска по рёбрам тела: по выбранным или по вертикальным."""

    kind = "Фаска"

    def __init__(self, size: float, name: str = "", edges=None):
        super().__init__(name)
        self.size = float(size)
        self.selector = "vertical"
        self.edges = [tuple(item) for item in (edges or ())]

    def execute(self, shape, profile=None):
        if shape is None:
            raise FeatureError(f"{self.name}: нет тела для фаски")
        edges = _resolve_edges(self, shape)
        if not edges:
            raise FeatureError(f"{self.name}: не найдено вертикальных рёбер")
        return kernel.chamfer(shape, self.size, edges)

    def parameters(self) -> dict:
        return {"size": self.size}

    def set_parameter(self, name: str, value) -> None:
        if name != "size":
            raise FeatureError(f"{self.name}: нет параметра {name!r}")
        self.size = float(value)
        self.touch()

    def to_dict(self) -> dict:
        return super().to_dict() | {
            "edges": [list(signature) for signature in self.edges]
        }

    @classmethod
    def from_dict(cls, data: dict, solver=None) -> "ChamferFeature":
        feature = cls(
            data["parameters"]["size"], data.get("name", ""), data.get("edges")
        )
        feature.stable_id = data.get("stable_id", feature.stable_id)
        return feature


class RevolveFeature(Feature):
    """Вращение профиля вокруг оси."""

    kind = "Вращение"

    def __init__(self, angle: float = 360.0, axis: str = "Y", name: str = ""):
        super().__init__(name)
        self.angle = float(angle)
        self.axis = axis

    def _axis_vector(self):
        return {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}.get(
            self.axis, (0.0, 1.0, 0.0)
        )

    def execute(self, shape, profile=None):
        face = profile
        if face is None:
            raise FeatureError(
                f"{self.name}: нет профиля для вращения — "
                f"эскиз выше по дереву пуст либо уже израсходован"
            )
        body = kernel.revolve(face, self.angle, (0.0, 0.0, 0.0), self._axis_vector())
        if shape is None or profile is None:
            return body
        return kernel.fuse(shape, body)

    def parameters(self) -> dict:
        return {"angle": self.angle}

    def set_parameter(self, name: str, value) -> None:
        if name != "angle":
            raise FeatureError(f"{self.name}: нет параметра {name!r}")
        self.angle = float(value)
        self.touch()

    def to_dict(self) -> dict:
        return super().to_dict() | {"axis": self.axis}

    @classmethod
    def from_dict(cls, data: dict, solver=None) -> "RevolveFeature":
        feature = cls(
            data["parameters"]["angle"], data.get("axis", "Y"), data.get("name", "")
        )
        feature.stable_id = data.get("stable_id", feature.stable_id)
        return feature


class PocketFeature(Feature):
    """Вырез: профиль эскиза выдавливается и вычитается из тела."""

    kind = "Вырез"

    def __init__(self, sketch, depth: float, name: str = "", regions=None,
                 end: str = "", until=None):
        super().__init__(name)
        self.sketch = sketch
        self.depth = float(depth)
        #: Концевое условие. Пустая строка — по глубине: ноль означает
        #: «насквозь», как было принято до появления условий.
        self.end = end or ""
        self.until = until
        #: Выбранные области собственного эскиза. Обычно профиль приходит
        #: от эскиза выше по дереву, и тогда это поле не используется.
        self.regions = list(regions) if regions else None

    def execute(self, shape, profile=None):
        if shape is None:
            raise FeatureError(f"{self.name}: нет тела для выреза")
        solution = self.sketch.solve()
        if not solution.ok:
            raise FeatureError(f"{self.name}: эскиз не решён — {solution.message}")
        # Готовый профиль от предыдущей операции имеет приоритет: в нём
        # учтён выбор областей, а собственный эскиз выреза о нём не знает
        # и построил бы контур целиком.
        if profile is None or kernel.is_empty(profile):
            profile = (self.sketch.region_face(self.regions) if self.regions
                       else self.sketch.to_occt_face())
        wanted = operations.Extrusion(
            length=self.depth if self.depth > 0 else 0.0,
            end=self.end or (operations.BLIND if self.depth > 0
                             else operations.THROUGH),
            until=self.until,
        )
        # Сторона выбирается по материалу — тем же кодом, что и у прилива.
        # Раньше у выреза был свой перебор сторон, а у прилива своего не
        # было вовсе: одна и та же задача решалась в двух местах по-разному.
        chosen = operations.choose_direction(
            shape, profile, wanted, operations.CUT)
        try:
            tool = operations.build_tool(profile, chosen, shape)
            result, note = operations.apply(
                shape, tool, operations.CUT, self.name)
        except operations.OperationError as error:
            raise FeatureError(
                f"{error}. Проверьте плоскость эскиза «{self.sketch.name}»"
            ) from error
        if note:
            self.note = note
        return result

    def parameters(self) -> dict:
        return {"depth": self.depth}

    def set_parameter(self, name: str, value) -> None:
        if name != "depth":
            raise FeatureError(f"{self.name}: нет параметра {name!r}")
        self.depth = float(value)
        self.touch()

    def to_dict(self) -> dict:
        data = super().to_dict() | {"sketch": self.sketch.to_dict()}
        if self.regions:
            data["regions"] = [dict(reference) for reference in self.regions]
        return data

    @classmethod
    def from_dict(cls, data: dict, solver=None) -> "PocketFeature":
        from ..sketch import Sketch

        feature = cls(
            Sketch.from_dict(data["sketch"], solver),
            data["parameters"]["depth"],
            data.get("name", ""),
            data.get("regions"),
        )
        feature.stable_id = data.get("stable_id", feature.stable_id)
        return feature


class FilletFeature(Feature):
    """Скругление рёбер."""

    kind = "Скругление"

    def __init__(self, radius: float, selector: str = "vertical", name: str = "",
                 edges=None):
        super().__init__(name)
        self.radius = float(radius)
        self.selector = selector
        # Подписи выбранных рёбер. Если они есть, отбор по признаку не
        # применяется: человек указал рёбра явно, и подменять его выбор
        # правилом «все вертикальные» нельзя.
        self.edges = [tuple(item) for item in (edges or ())]

    def execute(self, shape, profile=None):
        if shape is None:
            raise FeatureError(f"{self.name}: нет тела для скругления")
        edges = _resolve_edges(self, shape)
        if not edges:
            raise FeatureError(f"{self.name}: рёбра по условию {self.selector!r} не найдены")
        return kernel.fillet(shape, self.radius, edges)

    def parameters(self) -> dict:
        return {"radius": self.radius}

    def set_parameter(self, name: str, value) -> None:
        if name != "radius":
            raise FeatureError(f"{self.name}: нет параметра {name!r}")
        self.radius = float(value)
        self.touch()

    def to_dict(self) -> dict:
        return super().to_dict() | {
            "selector": self.selector,
            "edges": [list(signature) for signature in self.edges],
        }

    @classmethod
    def from_dict(cls, data: dict, solver=None) -> "FilletFeature":
        feature = cls(
            data["parameters"]["radius"],
            data.get("selector", "vertical"),
            data.get("name", ""),
            data.get("edges"),
        )
        feature.stable_id = data.get("stable_id", feature.stable_id)
        return feature


class HolePatternFeature(Feature):
    """Массив отверстий по сетке.

    Отдельная операция, а не «массив предыдущей»: повторение произвольной
    операции в линейной истории требует знать, что именно она изменила, а это
    отдельная задача. Сетка крепёжных отверстий закрывает частый случай честно.
    """

    kind = "Массив отверстий"

    def __init__(
        self,
        x: float,
        y: float,
        radius: float,
        count_x: int = 2,
        step_x: float = 10.0,
        count_y: int = 1,
        step_y: float = 10.0,
        name: str = "",
    ):
        super().__init__(name)
        self.x, self.y, self.radius = float(x), float(y), float(radius)
        self.count_x, self.step_x = int(count_x), float(step_x)
        self.count_y, self.step_y = int(count_y), float(step_y)

    def execute(self, shape, profile=None):
        if shape is None:
            raise FeatureError(f"{self.name}: нет тела для массива")
        if self.count_x < 1 or self.count_y < 1:
            raise FeatureError(f"{self.name}: количество должно быть не меньше 1")
        box = kernel.bounds(shape)
        depth = (box.zmax - box.zmin) + 2.0
        result = shape
        for row in range(self.count_y):
            for column in range(self.count_x):
                tool = kernel.cylinder(
                    self.radius,
                    depth,
                    (self.x + column * self.step_x, self.y + row * self.step_y,
                     box.zmin - 1.0),
                )
                result = kernel.cut(result, tool)
        return result

    def parameters(self) -> dict:
        return {
            "x": self.x, "y": self.y, "radius": self.radius,
            "count_x": self.count_x, "step_x": self.step_x,
            "count_y": self.count_y, "step_y": self.step_y,
        }

    def set_parameter(self, name: str, value) -> None:
        if name not in self.parameters():
            raise FeatureError(f"{self.name}: нет параметра {name!r}")
        setattr(self, name, int(value) if name.startswith("count") else float(value))
        self.touch()

    @classmethod
    def from_dict(cls, data: dict, solver=None) -> "HolePatternFeature":
        parameters = data["parameters"]
        feature = cls(
            parameters["x"], parameters["y"], parameters["radius"],
            parameters["count_x"], parameters["step_x"],
            parameters["count_y"], parameters["step_y"],
            data.get("name", ""),
        )
        feature.stable_id = data.get("stable_id", feature.stable_id)
        return feature


class MirrorFeature(Feature):
    """Зеркальное отражение тела с объединением — для симметричных деталей."""

    kind = "Зеркало"

    def __init__(self, plane: str = "YZ", offset: float = 0.0, name: str = ""):
        super().__init__(name)
        self.plane = plane
        self.offset = float(offset)

    def _normal(self):
        return {"YZ": (1.0, 0.0, 0.0), "XZ": (0.0, 1.0, 0.0), "XY": (0.0, 0.0, 1.0)}.get(
            self.plane, (1.0, 0.0, 0.0)
        )

    def execute(self, shape, profile=None):
        if shape is None:
            raise FeatureError(f"{self.name}: нет тела для отражения")
        normal = self._normal()
        origin = tuple(component * self.offset for component in normal)
        return kernel.fuse(shape, kernel.mirrored(shape, origin, normal))

    def parameters(self) -> dict:
        return {"offset": self.offset}

    def set_parameter(self, name: str, value) -> None:
        if name != "offset":
            raise FeatureError(f"{self.name}: нет параметра {name!r}")
        self.offset = float(value)
        self.touch()

    def to_dict(self) -> dict:
        return super().to_dict() | {"plane": self.plane}

    @classmethod
    def from_dict(cls, data: dict, solver=None) -> "MirrorFeature":
        feature = cls(
            data.get("plane", "YZ"),
            data["parameters"]["offset"],
            data.get("name", ""),
        )
        feature.stable_id = data.get("stable_id", feature.stable_id)
        return feature


def _simple_from_dict(feature_class, data: dict, order: tuple[str, ...]):
    """Восстановить операцию, у которой параметры — просто числа."""
    parameters = data.get("parameters", {})
    feature = feature_class(*(parameters[key] for key in order), data.get("name", ""))
    feature.stable_id = data.get("stable_id", feature.stable_id)
    return feature


# Заготовка навешивается ТОЛЬКО на те операции, у которых нет своего
# `from_dict`. Раньше присваивание шло безусловно и тихо затирало
# написанный в классе метод: концевое условие выдавливания сохранялось в
# файл и не читалось обратно, а на глаз деталь выглядела прежней.
HoleFeature.from_dict = classmethod(
    lambda cls, data, solver=None: _simple_from_dict(cls, data, ("x", "y", "radius"))
)

# Реестр видов операций. Тип пишется в файл строкой, а не ссылкой на класс:
# файл должен переживать переименования и переезды модулей.
def _resolve_edges(feature, shape) -> list:
    """Рёбра операции: выбранные человеком или отобранные по признаку.

    Потерянное ребро — это ОТКАЗ, а не повод скруглить оставшиеся. Подпись
    ребра держится за его геометрию, и если размер выше по дереву сдвинул
    ребро, подпись перестаёт совпадать. Скруглить в этом случае «сколько
    нашлось» значит выдать деталь, которой никто не задавал, и молчать об
    этом. Настоящее решение — топологические имена; их нет, и притворяться
    незачем.
    """
    if not getattr(feature, "edges", None):
        selector = getattr(feature, "selector", "vertical")
        if selector == "all":
            return list(kernel.iter_edges(shape))
        return kernel.vertical_edges(shape)

    found, missing = kernel.edges_matching(shape, feature.edges)
    if missing:
        raise FeatureError(
            f"{feature.name}: выбранных рёбер больше нет в теле "
            f"({len(missing)} из {len(feature.edges)}). Рёбра сдвинула правка "
            f"выше по дереву — выберите их заново."
        )
    return found


def _as_face(shape):
    """Привести вход к грани. Контур достраивается, тело возвращается как есть."""
    if shape is None:
        return None
    from OCP.TopAbs import TopAbs_WIRE

    if shape.ShapeType() == TopAbs_WIRE:
        from OCP.BRepBuilderAPI import BRepBuilderAPI_MakeFace
        from OCP.TopoDS import TopoDS

        return BRepBuilderAPI_MakeFace(TopoDS.Wire_s(shape)).Face()
    return shape


def _profile_normal(face) -> tuple[float, float, float]:
    """Нормаль профиля. Для неплоского входа — глобальная Z.

    Отказывать здесь нельзя: на вход операции может прийти не грань эскиза,
    а готовое тело от предыдущей операции, и выдавливание такого случая
    имеет смысл.
    """
    try:
        return kernel.face_normal(face)
    except Exception:  # noqa: BLE001
        return (0.0, 0.0, 1.0)


class ShellFeature(Feature):
    """Оболочка: выбрать материал изнутри, оставив стенку."""

    kind = "Оболочка"

    def __init__(self, thickness: float, name: str = ""):
        super().__init__(name)
        self.thickness = float(thickness)

    def execute(self, shape, profile=None):
        if shape is None:
            raise FeatureError(f"{self.name}: нет тела для оболочки")
        try:
            return kernel.shell(shape, self.thickness)
        except Exception as error:  # noqa: BLE001
            # Толщина больше половины наименьшего размера — обычная причина;
            # сообщение ядра при этом малопонятно, поэтому дополняем своим.
            raise FeatureError(
                f"{self.name}: оболочка не построилась ({error}). "
                f"Толщина {self.thickness} мм может не помещаться в теле."
            ) from error

    def parameters(self) -> dict:
        return {"thickness": self.thickness}

    def set_parameter(self, name: str, value) -> None:
        if name != "thickness":
            raise FeatureError(f"{self.name}: нет параметра {name!r}")
        self.thickness = float(value)
        self.touch()


FEATURE_TYPES = {
    "SketchFeature": SketchFeature,
    "ShellFeature": ShellFeature,
    "PadFeature": PadFeature,
    "HoleFeature": HoleFeature,
    "ChamferFeature": ChamferFeature,
    "RevolveFeature": RevolveFeature,
    "PocketFeature": PocketFeature,
    "FilletFeature": FilletFeature,
    "HolePatternFeature": HolePatternFeature,
    "MirrorFeature": MirrorFeature,
}


def feature_from_dict(data: dict, solver=None) -> Feature:
    kind = data.get("type")
    feature_class = FEATURE_TYPES.get(kind)
    if feature_class is None:
        raise FeatureError(
            f"неизвестный вид операции: {kind!r}. Файл создан более новой "
            f"версией ProtoCAD либо повреждён."
        )
    return feature_class.from_dict(data, solver)


@dataclass
class RecomputeReport:
    """Что пересчиталось и чем закончилось."""

    ok: bool
    executed: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    failed_at: str = ""
    message: str = ""
    duration_s: float = 0.0


class FeatureTree:
    """Последовательность операций, дающая форму детали."""

    def __init__(self, features=None):
        self.features: list[Feature] = list(features or [])
        self._cache: dict[str, object] = {}
        self._shape = None

    def add(self, feature: Feature) -> Feature:
        self.features.append(feature)
        feature.touch()
        return feature

    def __len__(self) -> int:
        return len(self.features)

    def __iter__(self):
        return iter(self.features)

    def by_name(self, name: str) -> Feature:
        for feature in self.features:
            if feature.name == name:
                return feature
        raise FeatureError(f"нет операции {name!r}")

    def set_parameter(self, feature_name: str, parameter: str, value) -> None:
        """Изменить параметр операции. Последующие операции устаревают."""
        feature = self.by_name(feature_name)
        feature.set_parameter(parameter, value)
        self._invalidate_from(feature)

    def _invalidate_from(self, feature: Feature) -> None:
        found = False
        for item in self.features:
            if item is feature:
                found = True
            if found:
                item.touch()
                self._cache.pop(item.stable_id, None)

    @property
    def shape(self):
        return self._shape

    def recompute(self, force: bool = False) -> RecomputeReport:
        """Выполнить устаревшие операции, начиная с первой такой.

        Результаты предыдущих берутся из кэша: пересчёт всего дерева на
        каждое изменение не нужен и на длинной истории заметен.
        """
        import time

        started = time.perf_counter()
        report = RecomputeReport(ok=True)
        # Тело и профиль идут по цепочке РАЗДЕЛЬНО. Одной формы хватало,
        # пока эскиз был один и всегда первым; со вторым эскизом он затирал
        # собой накопленное тело, и следующая операция получала контур там,
        # где ожидала деталь.
        shape = None
        profile = None

        for index, feature in enumerate(self.features):
            cached = self._cache.get(feature.stable_id)
            if cached is not None and not feature.stale and not force:
                shape, profile = cached
                report.skipped.append(feature.name)
                continue

            feature_started = time.perf_counter()
            try:
                produced = feature.execute(shape, profile)
                if feature.produces_profile:
                    profile = produced
                else:
                    if kernel.is_empty(produced):
                        # Операция, не оставившая ничего, — это ОТКАЗ.
                        # Раньше пустой результат принимался как успех:
                        # дерево отчитывалось «ok», деталь исчезала, а
                        # первым признаком беды была трассировка из
                        # отрисовки окна.
                        raise FeatureError(
                            f"{feature.name}: от детали ничего не осталось — "
                            f"операция сняла весь материал"
                        )
                    # Форма проверяется СРАЗУ, а не когда её попытаются
                    # показать или взять следующей операцией. Неправильное
                    # тело строится молча и ведёт себя необъяснимо: тёмное
                    # пятно на экране, пропавшие грани при выборе, отказ
                    # где-то дальше по дереву. Место, где оно возникло,
                    # тогда уже не найти.
                    problems = kernel.validate(produced)
                    if problems:
                        raise FeatureError(
                            f"{feature.name}: результат не является телом — "
                            + "; ".join(problems)
                        )
                    bodies = kernel.solid_count(produced)
                    if bodies > 1:
                        # Не отказ: многотельность бывает нужна. Но молчать
                        # нельзя — чаще это признак того, что прилив не
                        # коснулся детали.
                        feature.note = (
                            f"{feature.name}: получилось тел: {bodies} — "
                            f"они не соединены между собой"
                        )
                    # Профиль расходуется операцией: повторное выдавливание
                    # того же контура следующей операцией было бы неожиданным.
                    shape, profile = produced, None
            except Exception as error:  # noqa: BLE001 — отказ фиксируем в дереве
                feature.state = FeatureState(
                    ok=False,
                    message=f"{type(error).__name__}: {error}",
                    duration_s=time.perf_counter() - feature_started,
                )
                report.ok = False
                report.failed_at = feature.name
                report.message = feature.state.message
                # Форма остаётся от последнего удавшегося пересчёта, но
                # отказ виден: ok=False и failed_at заполнены.
                report.duration_s = time.perf_counter() - started
                return report

            feature.state = FeatureState(
                ok=True, duration_s=time.perf_counter() - feature_started
            )
            feature._stale = False
            self._cache[feature.stable_id] = (shape, profile)
            report.executed.append(feature.name)

        self._shape = shape
        report.duration_s = time.perf_counter() - started
        return report

    # --- сохранение ---

    def to_dict(self) -> dict:
        return {"features": [feature.to_dict() for feature in self.features]}

    @classmethod
    def from_dict(cls, data: dict, solver=None) -> "FeatureTree":
        return cls(
            feature_from_dict(record, solver) for record in data.get("features", [])
        )

    def describe(self) -> list[dict]:
        """Дерево построения для интерфейса: имя, вид, параметры, состояние."""
        return [
            {
                "name": feature.name,
                "kind": feature.kind,
                "parameters": feature.parameters(),
                "ok": feature.state.ok,
                "message": feature.state.message,
                "stale": feature.stale,
                "duration_ms": round(feature.state.duration_s * 1000, 3),
            }
            for feature in self.features
        ]
