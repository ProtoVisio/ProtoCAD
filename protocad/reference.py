"""Справочные плоскости: ПРАВИЛО построения, а не запомненные числа.

Плоскость, записанная числами, перестаёт быть справочной после первой же
правки: подвинули грань — а плоскость, «отстоящая от неё на 10 мм»,
осталась где была. Поэтому здесь хранится то, ОТ ЧЕГО она построена, и
пересчитывается она каждый раз вместе с деталью.

Опоры называются устойчивыми именами из карты элементов движка, а номер по
месту — запасной адрес. Номер годен ровно до правки выше по дереву
(`docs/08_ENGINE_BACKEND.md`, §26: не хранить ссылки как `Face6`).

Отказ здесь — это отказ, а не подстановка чего-нибудь похожего. Плоскость,
которую не удалось построить, помечается сообщением и не строится вовсе:
эскиз, лёгший на выдуманную плоскость, даёт деталь, которую никто не
заказывал.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .sketch.plane import Plane

#: Как построена плоскость.
KINDS = {
    "offset": "Параллельно грани на расстоянии",
    "midway": "Посередине между двумя",
    "through": "Через вершину параллельно грани",
}

#: Насколько нормали считаются сонаправленными. Грани детали редко бывают
#: параллельны идеально: их положение приходит числами с округлением.
PARALLEL = 1e-6


def _normalize(vector) -> tuple:
    length = sum(value * value for value in vector) ** 0.5
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return tuple(float(value) / length for value in vector)


def _dot(a, b) -> float:
    return sum(a[i] * b[i] for i in range(3))


def _across(normal) -> tuple:
    """Любое направление В плоскости с такой нормалью.

    Нужно, чтобы у плоскости было «вправо». Берётся ось, наименее
    совпадающая с нормалью: иначе на нормали (0, 0, 1) выбор оси Z дал бы
    вырожденную пару.
    """
    axes = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    axis = min(axes, key=lambda item: abs(_dot(item, normal)))
    along = _dot(axis, normal)
    return _normalize(tuple(axis[i] - along * normal[i] for i in range(3)))


@dataclass
class Reference:
    """Опора плоскости: подэлемент детали или стандартная плоскость."""

    #: "face" | "vertex" | "standard"
    kind: str = "face"
    #: Номер по месту. Годен до правки выше по дереву.
    index: int = -1
    #: Устойчивое имя из карты элементов движка. Главнее номера.
    name: str = ""
    #: Для "standard" — "XY" | "XZ" | "YZ".
    standard: str = ""

    def to_dict(self) -> dict:
        return {"kind": self.kind, "index": self.index, "name": self.name,
                "standard": self.standard}

    @classmethod
    def from_dict(cls, data: dict) -> "Reference":
        return cls(kind=str(data.get("kind") or "face"),
                   index=int(data.get("index", -1)),
                   name=str(data.get("name") or ""),
                   standard=str(data.get("standard") or ""))


@dataclass
class ReferencePlane:
    """Справочная плоскость детали. Пересчитывается вместе с ней."""

    name: str
    kind: str = "offset"
    references: list = field(default_factory=list)
    distance: float = 10.0
    #: Развернуть нормаль. Сторона у плоскости есть, и выбирает её человек.
    flip: bool = False
    #: Итог последнего пересчёта. ``None`` — построить не удалось.
    plane: Plane | None = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.plane is not None

    def to_dict(self) -> dict:
        return {"name": self.name, "kind": self.kind,
                "references": [item.to_dict() for item in self.references],
                "distance": self.distance, "flip": self.flip}

    @classmethod
    def from_dict(cls, data: dict) -> "ReferencePlane":
        return cls(
            name=str(data.get("name") or "Плоскость"),
            kind=str(data.get("kind") or "offset"),
            references=[Reference.from_dict(item)
                        for item in (data.get("references") or ())],
            distance=float(data.get("distance", 10.0)),
            flip=bool(data.get("flip", False)),
        )


def _found(entities, reference: Reference):
    """Подэлемент детали по устойчивому имени, иначе по номеру.

    Имя ищется ПЕРВЫМ: номер переживает не всякую правку выше по дереву, и
    найденный по номеру подэлемент может оказаться совсем другим.
    """
    if reference.name:
        for item in entities:
            if getattr(item, "name", "") == reference.name \
                    and item.kind == reference.kind:
                return item
    for item in entities:
        if item.kind == reference.kind and item.index == reference.index:
            return item
    return None


def _plane_of(entities, reference: Reference):
    """(начало, нормаль) опоры-плоскости. ``None`` — опора не плоская."""
    from .sketch import plane as plane_module

    if reference.kind == "standard":
        standard = plane_module.STANDARD.get(reference.standard)
        if standard is None:
            return None
        return (tuple(standard.origin), tuple(standard.normal))
    item = _found(entities, reference)
    if item is None:
        return None
    data = getattr(item, "data", None) or {}
    if data.get("surface") != "plane" or not data.get("normal"):
        return None
    return (tuple(float(v) for v in data.get("origin") or data["center"]),
            tuple(float(v) for v in data["normal"]))


def _point_of(entities, reference: Reference):
    """Точка опоры-вершины. ``None`` — не вершина или не найдена."""
    item = _found(entities, reference)
    if item is None:
        return None
    data = getattr(item, "data", None) or {}
    point = data.get("point")
    return tuple(float(v) for v in point) if point else None


def resolve(record: ReferencePlane, entities) -> ReferencePlane:
    """Построить плоскость по её правилу. Отказ пишется в ``message``."""
    record.plane = None
    record.message = ""
    builder = _BUILDERS.get(record.kind)
    if builder is None:
        record.message = f"{record.name}: неизвестный способ «{record.kind}»"
        return record
    try:
        plane, failure = builder(record, entities)
    except Exception as error:  # noqa: BLE001 — отказ опоры это ответ
        plane, failure = None, f"{type(error).__name__}: {error}"
    if plane is None:
        record.message = f"{record.name}: {failure}"
        return record
    if record.flip:
        plane = Plane(plane.origin,
                      tuple(-value for value in plane.normal),
                      plane.x_direction, plane.name)
    record.plane = plane
    return record


def _offset(record: ReferencePlane, entities):
    """Параллельно грани или стандартной плоскости, на расстоянии."""
    if len(record.references) != 1:
        return None, "нужна одна опора — грань или плоскость"
    base = _plane_of(entities, record.references[0])
    if base is None:
        return None, ("опора не найдена или не плоская — "
                      "параллель к ней не определена")
    origin, normal = base
    moved = tuple(origin[i] + record.distance * normal[i] for i in range(3))
    return Plane(moved, normal, _across(normal), record.name), ""


def _cross(a, b) -> tuple:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _on_both(origin_a, normal_a, origin_b, normal_b):
    """Точка на линии пересечения двух плоскостей. ``None`` — параллельны.

    Берётся ближайшая к нулю точка этой линии — ровно та же, что и у
    начала плоскости грани (`_face_data`): начало считается проекцией нуля
    детали, и держаться одного правила здесь важнее, чем выбрать «покрасивее».
    """
    direction = _cross(normal_a, normal_b)
    square = _dot(direction, direction)
    if square < 1e-18:
        return None
    da, db = _dot(normal_a, origin_a), _dot(normal_b, origin_b)
    first = _cross(normal_b, direction)
    second = _cross(direction, normal_a)
    return tuple((da * first[i] + db * second[i]) / square for i in range(3))


def _bisector(origin_a, normal_a, origin_b, normal_b, name: str):
    """Плоскость, делящая угол между двумя непараллельными пополам.

    Это НЕ «середина расстояния»: у пересекающихся плоскостей расстояния
    между ними нет. Берётся геометрическое место точек, одинаково
    отстоящих от обеих СО ЗНАКОМ: ``n₁·(x−o₁) = n₂·(x−o₂)``. Отсюда и
    нормаль ``n₁ − n₂``.

    Знак важен. Второй биссектор, с нормалью ``n₁ + n₂``, тоже делит угол
    пополам, но другой — смежный. Выбран тот, что переходит в обычную
    середину, когда грани становятся параллельными: у плиты с нормалями
    ``+Z`` и ``−Z`` формула даёт ровно её среднюю плоскость. Одно правило
    на оба случая лучше двух похожих.
    """
    normal = tuple(normal_a[i] - normal_b[i] for i in range(3))
    if sum(value * value for value in normal) ** 0.5 < 1e-9:
        return None, ""
    point = _on_both(origin_a, normal_a, origin_b, normal_b)
    if point is None:
        return None, ""
    return Plane(point, _normalize(normal), _across(_normalize(normal)),
                 name), ""


def _midway(record: ReferencePlane, entities):
    """Посередине между двумя опорами.

    Две плоские грани — плоскость посередине между ними. Две вершины —
    плоскость, перпендикулярная отрезку между ними, через его середину:
    это и есть «посередине» для точек.
    """
    if len(record.references) != 2:
        return None, "нужны две опоры"
    first, second = record.references
    a, b = _plane_of(entities, first), _plane_of(entities, second)
    if a is not None and b is not None:
        (origin_a, normal_a), (origin_b, normal_b) = a, b
        along = _dot(normal_a, normal_b)
        if abs(abs(along) - 1.0) > PARALLEL:
            # Грани пересекаются: «посередине» между ними — плоскость,
            # делящая угол пополам. Расстояния между пересекающимися
            # плоскостями не существует, и середины расстояния тоже.
            plane, failure = _bisector(origin_a, normal_a, origin_b, normal_b,
                                       record.name)
            if plane is None:
                return None, (failure or "угол между гранями не определён")
            return plane, ""
        # Расстояние берётся ВДОЛЬ нормали первой: у противоположных
        # граней нормали смотрят в разные стороны, и полусумма начал дала
        # бы точку, не лежащую посередине.
        gap = _dot(tuple(origin_b[i] - origin_a[i] for i in range(3)), normal_a)
        middle = tuple(origin_a[i] + gap / 2.0 * normal_a[i] for i in range(3))
        return Plane(middle, normal_a, _across(normal_a), record.name), ""

    point_a = _point_of(entities, first)
    point_b = _point_of(entities, second)
    if point_a is not None and point_b is not None:
        direction = tuple(point_b[i] - point_a[i] for i in range(3))
        if sum(value * value for value in direction) ** 0.5 < 1e-9:
            return None, "вершины совпали — середина между ними не плоскость"
        normal = _normalize(direction)
        middle = tuple((point_a[i] + point_b[i]) / 2.0 for i in range(3))
        return Plane(middle, normal, _across(normal), record.name), ""

    return None, ("опоры разного рода: посередине строится между двумя "
                  "гранями либо между двумя вершинами")


def _through(record: ReferencePlane, entities):
    """Через вершину, параллельно грани или стандартной плоскости."""
    if len(record.references) != 2:
        return None, "нужны две опоры: вершина и грань"
    point, base = None, None
    for reference in record.references:
        if base is None:
            base = _plane_of(entities, reference)
            if base is not None:
                continue
        if point is None:
            point = _point_of(entities, reference)
    if base is None or point is None:
        return None, "нужны вершина и плоская грань"
    _, normal = base
    return Plane(point, normal, _across(normal), record.name), ""


_BUILDERS = {"offset": _offset, "midway": _midway, "through": _through}


def _face_for(support, entities):
    """Грань, на которой лежит эскиз. ``None`` — не нашлась.

    Ищется тремя способами по убыванию надёжности:

    1. **устойчивое имя** из карты элементов движка;
    2. **положение** — грань, смотрящая в ту же сторону и ближайшая к
       запомненной. Так грань опознаётся после перестройки, когда имена
       выходят другими: подняли плиту — верхняя грань уехала вверх, но
       других граней, глядящих вверх, у неё нет;
    3. **номер по месту** — и только если больше не по чему.

    Порядок здесь не вкусовщина. Номер после перестройки указывает на
    другую грань: у плиты верхняя грань была пятой, стала четвёртой, а
    пятой оказалась нижняя — и эскиз ушёл бы под деталь, не сказав ни
    слова (`docs/08_ENGINE_BACKEND.md`, §26).
    """
    flat = [item for item in entities
            if item.kind == "face"
            and (getattr(item, "data", None) or {}).get("surface") == "plane"]
    name = str(support.get("name") or "")
    if name:
        for item in flat:
            if getattr(item, "name", "") == name:
                return item

    normal = support.get("normal")
    origin = support.get("origin")
    if normal and origin:
        facing = [item for item in flat
                  if _dot((item.data or {}).get("normal") or (0, 0, 0),
                          normal) > 1.0 - 1e-3]
        if len(facing) == 1:
            return facing[0]
        if facing:
            # Смотрящих туда же несколько: берём ближайшую к запомненному
            # месту. Двусмысленность тут возможна, и врать о ней нельзя —
            # но выбор по расстоянию хотя бы объясним, в отличие от номера.
            def away(item) -> float:
                point = (item.data or {}).get("origin") or (0, 0, 0)
                return sum((point[i] - origin[i]) ** 2 for i in range(3))
            return min(facing, key=away)

    index = int(support.get("index", -1))
    for item in flat:
        if item.index == index:
            return item
    return None


def plane_of_support(support, entities, planes=()) -> tuple:
    """Плоскость эскиза по его опоре. ``(плоскость, сообщение)``.

    ``support`` — словарь из эскиза: ``kind`` и адрес. Опора считается ПО
    ТОМУ, ЧТО УЖЕ ПОСТРОЕНО к этому месту дерева: эскиз на грани не может
    опираться на грань, которую делает его же операция.

    Ничего не найдено — возвращается ``None`` и причина. Подставлять
    прежнюю плоскость нельзя: она и есть тот снимок, от которого уходим.
    """
    if not support:
        return None, ""
    kind = str(support.get("kind") or "")
    offset = float(support.get("offset") or 0.0)
    if kind == "standard":
        from .sketch import plane as plane_module

        base = plane_module.STANDARD.get(str(support.get("standard") or ""))
        if base is None:
            return None, f"нет стандартной плоскости {support.get('standard')!r}"
        return (base.offset(offset) if offset else base), ""
    if kind == "plane":
        name = str(support.get("name") or "")
        for record in planes:
            if record.name == name:
                if not record.ok:
                    return None, f"справочная плоскость «{name}» не построена"
                base = record.plane
                return (base.offset(offset) if offset else base), ""
        return None, f"справочной плоскости «{name}» в детали нет"
    if kind == "face":
        found = _face_for(support, entities)
        if found is None:
            return None, "грань, на которой лежит эскиз, не найдена"
        data = getattr(found, "data", None) or {}
        base = Plane(tuple(float(v) for v in data["origin"]),
                     tuple(float(v) for v in data["normal"]),
                     tuple(float(v) for v in data["x_direction"]), "грань")
        # Опора обновляется НА МЕСТЕ: имя и номер грани после перестройки
        # другие, и хранить прежние значит с каждым разом всё вернее
        # находить не ту грань. Запоминается и положение — по нему грань и
        # опознаётся, когда имя не совпало.
        support["index"] = int(found.index)
        support["name"] = getattr(found, "name", "") or ""
        support["origin"] = list(base.origin)
        support["normal"] = list(base.normal)
        return (base.offset(offset) if offset else base), ""
    return None, f"неизвестная опора эскиза: {kind!r}"
