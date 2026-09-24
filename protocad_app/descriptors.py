"""Описания команд детали: что спрашивают и в каком порядке.

Порядок групп повторяет отраслевую практику, зафиксированную в
спецификации: сначала откуда, затем направление и его концевое условие,
затем необязательные группы, затем область действия. Порядок здесь —
единственное место, где он задан; спорить о нём удобно, глядя на список.

Каждая команда описана ДАННЫМИ. Панель строится по описанию, поэтому новая
операция стоит одного блока, а не отдельного окна с собственной логикой.
"""

from __future__ import annotations

from dataclasses import dataclass

from command_session import (
    CommandDescriptor,
    Group,
    Parameter,
    SelectionBox,
    Validation,
)

from protocad.document import Operation
from protocad.engine import EndCondition


@dataclass
class BuildContext:
    """Что команде известно о детали.

    ``document`` — дерево намерений, ``model`` — описание построенного:
    грани, рёбра, вершины числами. Форм ядра здесь нет и быть не может
    (docs/08_ENGINE_BACKEND.md, §10.1).
    """

    document: object = None
    model: object = None

    def unique_name(self, base: str) -> str:
        """Имя без совпадений. Без документа — просто основа: сеанс может
        существовать и вне детали, в проверках."""
        if self.document is None:
            return base
        from commands import unique_operation_name

        return unique_operation_name(self.document, base)


def put(session, box_id: str, picks) -> None:
    """Положить готовые объекты в поле выбора, минуя щелчки мышью.

    Нужно правке: она открывает ту же панель, что и создание, но поля у
    неё заполнены тем, что операция уже держит.
    """
    box = session.box(box_id)
    if box is None:
        return
    box.items = list(picks)


def _picks_of(context, kind: str, numbers, names=()) -> list:
    """Подэлементы операции как выбор в поле — с ЕЁ адресами, а не с чужими.

    Устойчивое имя берётся из самой операции, а не перечитывается у
    описания детали. Разница не тонкая: операция стоит на форме ДО себя, а
    описание в окне — это готовая деталь. У фаски, перечитанной с готовой
    детали, имя ребра оказывалось именем с уже срезанной кромки, и правка
    размера молча не срабатывала — деталь оставалась прежней.
    """
    from command_session import Pick

    from protocad.engine import EntityRef

    model = getattr(context, "model", None)
    titles = {"face": "Грань", "edge": "Ребро"}
    found = []
    for position, number in enumerate(numbers or ()):
        number = int(number)
        if number < 0:
            continue
        stored = names[position] if position < len(names or ()) else ""
        label = (model.label_of(kind, number) if model is not None
                 else f"{titles.get(kind, kind)}{number}")
        found.append(Pick(kind, number, label,
                          EntityRef(id=f"{kind}{number}", kind=kind,
                                    index=number, name=stored)))
    return found


def face_picks(context, numbers, names=()) -> list:
    """Грани операции как выбор в поле. Отрицательный номер — «грани нет»."""
    return _picks_of(context, "face", numbers, names)


def edge_picks(context, numbers, names=()) -> list:
    return _picks_of(context, "edge", numbers, names)


#: Умолчание области действия: «само», то есть по общему правилу — в ту
#: цепочку, где строится, а «не объединять» начинает новое тело.
SCOPE_AUTO = "само"


def _many_bodies(session) -> bool:
    """Спрашивать тело, только когда их больше одного.

    У детали из одного тела выбор был бы вопросом без вариантов — а поле,
    которое нечем заполнить, читается как недоделка.
    """
    document = getattr(getattr(session, "context", None), "document", None)
    return document is not None and len(getattr(document, "bodies", ())) > 1


def _scope_choices(session) -> tuple:
    document = getattr(getattr(session, "context", None), "document", None)
    return (SCOPE_AUTO,) + tuple(getattr(document, "bodies", ()) or ())


def _scope_of(session) -> str:
    """Заданное тело или пусто, если «само»."""
    value = str(session.value("scope") or SCOPE_AUTO)
    return "" if value == SCOPE_AUTO else value


def _profile_box(box_id: str = "profile", label: str = "Профиль") -> SelectionBox:
    """Поле профиля. Принимает эскиз или плоскую грань."""
    return SelectionBox(
        box_id, label, accepts=("sketch", "face"), minimum=1, maximum=1,
    )


# --- Выдавливание и вырез ---------------------------------------------------
#
# По `docs/ProtoCAD_SolidWorks_behavior_spec_2026/04_part_features`,
# 010_extruded_boss.yaml и 020_extruded_cut.yaml. Секции, поля и условия
# видимости взяты оттуда; названия — по-русски, как во всём остальном
# интерфейсе.

#: Концевые условия по спецификации → концевые условия протокола. Здесь
#: только те, что движок действительно умеет: остальные объявлены
#: пробелами возможностей (SPEC_GAPS, EXTRUDE-GAP-001), а не показаны в
#: панели неработающими.
ENDS = {
    "На расстояние": EndCondition.BLIND,
    "Насквозь": EndCondition.THROUGH_ALL,
    "До следующей": EndCondition.UP_TO_FIRST,
    "До последней": EndCondition.UP_TO_LAST,
    "До грани": EndCondition.UP_TO_FACE,
    "От средней плоскости": EndCondition.MID_PLANE,
}

#: Что предлагает бобышка и что — вырез. Наборы РАЗНЫЕ, и это не
#: небрежность: у `PartDesign::Pad` в FreeCAD 1.1 нет «насквозь», у
#: `Pocket` нет «до последней». Показывать то, чего движок не умеет,
#: значит обещать несуществующее.
PAD_ENDS = ("На расстояние", "До следующей", "До последней", "До грани",
            "От средней плоскости")
CUT_ENDS = ("На расстояние", "Насквозь", "До следующей", "До грани",
            "От средней плоскости")

#: Историческое имя: на него ссылаются прежние проверки.
END_CONDITIONS = ("На расстояние", "От средней плоскости")
CUT_CONDITIONS = ("На расстояние", "Насквозь")

#: Условия, при которых нужна ЦЕЛЬ — грань или плоскость.
NEEDS_TARGET = ("До грани",)

#: Откуда начинается операция. По спецификации, 010_extruded_boss.yaml,
#: секция `start`. «Поверхность или грань» и «Вершина» превращаются в то
#: же смещение: расстояние от плоскости эскиза до указанного объекта вдоль
#: её нормали. Движок про них не знает и знать не должен — он получает
#: число.
STARTS = ("Плоскость эскиза", "Смещение", "Поверхность или грань", "Вершина")

#: Начала, которым нужен указанный объект.
STARTS_WITH_REFERENCE = ("Поверхность или грань", "Вершина")


def _start_offset_shown(session) -> bool:
    return session.value("start_condition") == "Смещение"


def _start_reference_shown(session) -> bool:
    return session.value("start_condition") in STARTS_WITH_REFERENCE


def _start_box() -> SelectionBox:
    return SelectionBox(
        "start_reference", "Начало от", accepts=("face", "plane", "vertex"),
        minimum=1, maximum=1, visible_when=_start_reference_shown,
    )


def _second_shown(session) -> bool:
    """Второе направление. С «от средней плоскости» не сочетается.

    У FreeCAD сторона одна: «одна», «две» или «симметрично». Средняя
    плоскость и есть симметрия, и просить вдобавок второе направление
    значит просить две взаимоисключающие вещи.
    """
    return (bool(session.value("direction2"))
            and session.value("end_condition") != "От средней плоскости")


def _second_available(session) -> bool:
    return session.value("end_condition") != "От средней плоскости"


def _length2_shown(session) -> bool:
    return (_second_shown(session)
            and session.value("end_condition2") == "На расстояние")


def _target2_shown(session) -> bool:
    return _second_shown(session) and session.value("end_condition2") in NEEDS_TARGET


def _target2_box() -> SelectionBox:
    return SelectionBox(
        "target2", "До грани (2)", accepts=("face", "plane"),
        minimum=1, maximum=1, visible_when=_target2_shown,
    )


def _start_of(session, context: BuildContext) -> float:
    """Начало операции числом — тем, что понимает движок.

    «Плоскость эскиза» — ноль. «Смещение» — введённая величина. Указанные
    грань или вершина — расстояние до них ОТ ПЛОСКОСТИ ЭСКИЗА вдоль её
    нормали, со знаком: сзади плоскости оно отрицательное.
    """
    condition = session.value("start_condition")
    if condition == "Смещение":
        return float(session.value("start_offset") or 0.0)
    if condition not in STARTS_WITH_REFERENCE:
        return 0.0
    sketch = _picked_sketch(session)
    box = session.box("start_reference")
    if sketch is None or box is None or not box.items:
        return 0.0
    point = _reference_point(box.items[0], context)
    if point is None:
        return 0.0
    plane = sketch.plane
    return sum((point[i] - plane.origin[i]) * plane.normal[i] for i in range(3))


def _reference_point(pick, context: BuildContext):
    """Точка указанного объекта: начало плоской грани либо сама вершина."""
    entry = pick.data
    data = getattr(entry, "data", None) or {}
    if data.get("point"):
        return tuple(data["point"])
    if data.get("origin"):
        return tuple(data["origin"])
    if context is not None and context.model is not None:
        plane = context.model.plane_of(int(pick.id))
        if plane is not None:
            return tuple(plane.origin)
    return None


def _length_shown(session) -> bool:
    """Длина имеет смысл только у «на расстояние» и «от средней плоскости»."""
    return session.value("end_condition") in ("На расстояние",
                                              "От средней плоскости")


def _target_shown(session) -> bool:
    return session.value("end_condition") in NEEDS_TARGET


def _contours_shown(session) -> bool:
    """Секция «Выбранные контуры» — когда у профиля больше одной области.

    Спецификация: `visibility: profile_has_multiple_regions`. На эскизе с
    единственной областью выбирать нечего, и лишняя секция только
    заслоняет то, что важно.
    """
    return len(_regions_of(session)) > 1


#: Разобранные эскизы: ``id(эскиз) -> (ссылка, отпечаток, ответ)``.
#: Разбор на области стоит дорого — на эскизе из сорока объектов около
#: 0.2 с, — а условия видимости спрашивают его по нескольку раз на каждое
#: обновление панели. Ответ запоминается до ближайшего изменения эскиза.
#: Хранится ещё и слабая ссылка: номер объекта после сборки мусора
#: достаётся другому, и без сверки можно было бы получить чужие области.
_PARSED: dict = {}


def _parsed(sketch) -> tuple:
    """(области, разомкнутые цепочки) эскиза."""
    import weakref

    from protocad.sketch import regions as regions_module

    try:
        stamp = (len(sketch.segments),
                 tuple(round(value, 9) for point in sketch.points
                       for value in sketch.coordinates(point)))
    except Exception:  # noqa: BLE001
        stamp = None
    key = id(sketch)
    kept = _PARSED.get(key)
    if (kept is not None and stamp is not None
            and kept[0]() is sketch and kept[1] == stamp):
        return kept[2]
    try:
        found = regions_module.build(sketch)
        # Разомкнутое ищется только там, где областей нет: на замкнутом
        # эскизе искать нечего, а разбор стоит денег.
        chains = [] if found else regions_module.chains(sketch)
    except Exception:  # noqa: BLE001 — эскиз может быть и неразбираемым
        found, chains = [], []
    if stamp is not None:
        if len(_PARSED) > 32:
            _PARSED.clear()
        _PARSED[key] = (weakref.ref(sketch), stamp, (found, chains))
    return found, chains


def _regions_of(session) -> list:
    """Области эскиза, лежащего в поле профиля."""
    sketch = _picked_sketch(session)
    if sketch is None:
        return []
    return _parsed(sketch)[0]


def _contours_box() -> SelectionBox:
    """Поле выбранных контуров. Пустое означает «весь эскиз».

    Минимум ноль: пустой выбор — законный и распространённый случай, и
    требовать хотя бы одну область значило бы запретить обычное
    выдавливание замкнутого контура.
    """
    return SelectionBox(
        "selected_regions", "Выбранные контуры", accepts=("sketch_region",),
        minimum=0, maximum=None, auto_advance=False,
        visible_when=_contours_shown,
    )


def _target_box() -> SelectionBox:
    return SelectionBox(
        "target", "До грани", accepts=("face", "plane"), minimum=1, maximum=1,
        visible_when=_target_shown,
    )


#: Куда наращивать стенку от контура. По спецификации, секция `thin`.
THIN_TYPES = ("Односторонний", "Средняя плоскость", "Двусторонний")
THIN_KINDS = {"Односторонний": "one", "Средняя плоскость": "mid",
              "Двусторонний": "two"}


def _open_profile(session) -> bool:
    """У профиля есть разомкнутые контуры и ни одной области."""
    sketch = _picked_sketch(session)
    if sketch is None:
        return False
    found, chains = _parsed(sketch)
    return not found and bool(chains)


def _thin_on(session) -> bool:
    """Тонкая стенка. У разомкнутого профиля выключить её нельзя.

    Разомкнутый контур площади не ограничивает, и сплошным его не
    выдавить вовсе: осмысленный вид операции остаётся ровно один. Флажок,
    который в этом случае можно снять, обещал бы несуществующий выбор.
    """
    return bool(session.value("thin_enabled")) or _open_profile(session)


def _thin_choice_shown(session) -> bool:
    return not _open_profile(session)


def _thin_second(session) -> bool:
    return _thin_on(session) and session.value("thin_type") == "Двусторонний"


def _thin_group() -> Group:
    return Group("Тонкостенный элемент", parameters=[
        Parameter("thin_enabled", "Тонкостенный элемент", "flag", False,
                  visible_when=_thin_choice_shown),
        Parameter("thin_type", "Тип", "choice", THIN_TYPES[0],
                  choices=THIN_TYPES, visible_when=_thin_on),
        Parameter("thin_thickness", "Толщина", "number", 1.0, 0.001, 1e5, 3,
                  suffix=" мм", visible_when=_thin_on),
        Parameter("thin_thickness2", "Толщина 2", "number", 1.0, 0.001, 1e5, 3,
                  suffix=" мм", visible_when=_thin_second),
        Parameter("thin_flip", "Стенка на другую сторону", "flag", False,
                  visible_when=_thin_on),
    ])


def _picked_regions(session) -> list:
    """Устойчивые ссылки на выбранные области."""
    box = session.box("selected_regions")
    if box is None:
        return []
    return [pick.data for pick in box.items if pick.data is not None]


def _target_face(session) -> int:
    """Номер грани из поля цели; −1 — цели нет."""
    box = session.box("target")
    if box is None or not box.items:
        return -1
    return int(box.items[0].id)


def _extrusion(session, context: BuildContext, kind: str, title: str):
    """Общее для бобышки и выреза: они отличаются знаком и мелочами."""
    end = ENDS.get(session.value("end_condition"), EndCondition.BLIND)
    reverse = bool(session.value("reverse"))
    if kind == "pocket" and session.value("flip_side_to_cut"):
        # «Переставить сторону разреза» у выреза — это и есть обратное
        # направление инструмента.
        reverse = not reverse
    taper = float(session.value("draft_angle") or 0.0)
    if not session.value("draft_enabled"):
        taper = 0.0
    elif not session.value("draft_outward"):
        # По спецификации флажок называется «Уклон наружу». Внутрь —
        # тот же угол с обратным знаком.
        taper = -taper
    second = _second_shown(session)
    return Operation(
        kind, context.unique_name(title),
        sketch=_picked_sketch(session),
        regions=_picked_regions(session),
        length=float(session.value("depth") or 0.0) or 1.0,
        end=end,
        reversed=reverse,
        merge=bool(session.value("merge_result", True)),
        scope=_scope_of(session),
        start_offset=_start_of(session, context),
        taper=taper,
        direction2=second,
        end2=ENDS.get(session.value("end_condition2"), EndCondition.BLIND),
        length2=float(session.value("depth2") or 0.0) or 1.0,
        taper2=float(session.value("draft_angle2") or 0.0),
        target2=_box_face(session, "target2"),
        target2_name=(_box_names(session, "target2") or [""])[0],
        faces=[_target_face(session)] if _target_face(session) >= 0 else [],
        face_names=_box_names(session, "target"),
        thin=(THIN_KINDS.get(session.value("thin_type"), "one")
              if _thin_on(session) else ""),
        thickness=float(session.value("thin_thickness") or 0.0),
        thickness2=float(session.value("thin_thickness2") or 0.0),
        thin_flip=bool(session.value("thin_flip")),
    )


def _box_face(session, box_id: str) -> int:
    box = session.box(box_id)
    if box is None or not box.items:
        return -1
    return int(box.items[0].id)


def _picked_name(pick) -> str:
    """Устойчивое имя выбранного подэлемента. Пусто — движок его не ведёт."""
    return getattr(getattr(pick, "data", None), "name", "") or ""


def _box_names(session, box_id: str) -> list:
    box = session.box(box_id)
    return [_picked_name(pick) for pick in (box.items if box else ())]


def _load_extrusion(session, operation, context: BuildContext) -> None:
    """Заполнить панель выдавливания значениями готовой операции."""
    from command_session import Pick

    if operation.sketch is not None:
        put(session, "profile", [Pick("sketch", 1, operation.sketch.name,
                                      operation.sketch)])
    back = {value: key for key, value in ENDS.items()}
    session.set_value("end_condition", back.get(operation.end, "На расстояние"))
    session.set_value("depth", operation.length)
    session.set_value("reverse", operation.reversed)
    session.set_value("merge_result", operation.merge)
    session.set_value("scope", operation.scope or SCOPE_AUTO)
    if operation.start_offset:
        session.set_value("start_condition", "Смещение")
        session.set_value("start_offset", operation.start_offset)
    if operation.taper:
        session.set_value("draft_enabled", True)
        session.set_value("draft_angle", abs(operation.taper))
        session.set_value("draft_outward", operation.taper > 0.0)
    session.set_value("direction2", operation.direction2)
    if operation.direction2:
        session.set_value("end_condition2",
                          back.get(operation.end2, "На расстояние"))
        session.set_value("depth2", operation.length2)
        session.set_value("draft_angle2", operation.taper2)
    if operation.thin:
        session.set_value("thin_enabled", True)
        session.set_value("thin_type", _thin_title(operation.thin))
        session.set_value("thin_thickness", operation.thickness)
        session.set_value("thin_thickness2", operation.thickness2)
        session.set_value("thin_flip", operation.thin_flip)
    put(session, "target", face_picks(context, operation.faces,
                                      operation.face_names))
    if operation.target2 >= 0:
        put(session, "target2", face_picks(context, [operation.target2],
                                           [operation.target2_name]))


def _thin_title(kind: str) -> str:
    for title, value in THIN_KINDS.items():
        if value == kind:
            return title
    return THIN_TYPES[0]


def _build_pad(session, context: BuildContext):
    return _extrusion(session, context, "pad", "Выдавливание")


def _check_extrusion(session, context) -> Validation | None:
    if _length_shown(session) and session.value("depth", 0.0) <= 0.0:
        return Validation("semantic", False, "Глубина должна быть больше нуля")
    return None


#: Прежнее имя проверки — на него ссылаются описания ниже.
_check_pad = _check_extrusion


PAD = CommandDescriptor(
    key="pad",
    title="Вытянутая бобышка",
    preselection=("sketch", "face"),
    needs_body=False,
    groups=[
        Group("Профиль", boxes=[_profile_box()]),
        Group("Начало", boxes=[_start_box()], parameters=[
            Parameter("start_condition", "Начало", "choice", STARTS[0],
                      choices=STARTS),
            Parameter("start_offset", "Смещение", "number", 0.0, -1e5, 1e5, 3,
                      suffix=" мм", visible_when=_start_offset_shown),
        ]),
        Group("Направление 1", boxes=[_target_box()], parameters=[
            Parameter("end_condition", "Концевое условие", "choice",
                      PAD_ENDS[0], choices=PAD_ENDS),
            Parameter("depth", "Глубина", "number", 10.0, 0.001, 1e5, 3,
                      suffix=" мм", visible_when=_length_shown),
            Parameter("reverse", "Обратное направление", "flag", False),
            Parameter("draft_enabled", "Уклон", "flag", False),
            Parameter("draft_angle", "Угол уклона", "number", 1.0, 0.0, 89.0, 2,
                      suffix=" град", depends_on="draft_enabled"),
            Parameter("draft_outward", "Уклон наружу", "flag", True,
                      depends_on="draft_enabled"),
        ]),
        Group("Направление 2", boxes=[_target2_box()],
              visible_when=_second_available, parameters=[
            Parameter("direction2", "Второе направление", "flag", False),
            Parameter("end_condition2", "Концевое условие", "choice",
                      PAD_ENDS[0], choices=PAD_ENDS,
                      depends_on="direction2"),
            Parameter("depth2", "Глубина", "number", 10.0, 0.001, 1e5, 3,
                      suffix=" мм", visible_when=_length2_shown),
            Parameter("draft_angle2", "Угол уклона", "number", 0.0, -89.0, 89.0,
                      2, suffix=" град", depends_on="direction2"),
        ]),
        _thin_group(),
        Group("Выбранные контуры", boxes=[_contours_box()],
              visible_when=_contours_shown),
        Group("Область действия", parameters=[
            # По спецификации, секция `scope`. У `PartDesign::Body` нет
            # свойства «слить»: выключенное объединение означает НОВОЕ
            # тело, и так это и делается.
            Parameter("merge_result", "Объединить результаты", "flag", True),
            Parameter("scope", "Строить в теле", "choice", SCOPE_AUTO,
                      choices=_scope_choices,
                      visible_when=_many_bodies),
        ]),
    ],
    build=_build_pad,
    check=_check_extrusion,
    load=_load_extrusion,
)


# --- Вырез ------------------------------------------------------------------


def _build_cut(session, context: BuildContext):
    return _extrusion(session, context, "pocket", "Вырез")


def _picked_sketch(session):
    """Эскиз из поля профиля. ``None`` — в поле его нет.

    В поле может лежать и грань: тогда эскиза нет, и профиль берётся
    свободным эскизом уже в окне. Возвращать сюда грань нельзя — операция
    ждёт эскиз, и подмена обнаружилась бы на пересчёте.
    """
    from protocad.sketch import Sketch

    box = session.box("profile")
    if box is None or not box.items:
        return None
    picked = box.items[0].data
    return picked if isinstance(picked, Sketch) else None


CUT = CommandDescriptor(
    # Ключ совпадает с кнопкой ленты. Пока он был другим, «Вырез» не
    # находил своего описания и уходил в старое модальное окно: панели
    # операции у выреза не появлялось вовсе.
    key="pocket",
    title="Вытянутый вырез",
    preselection=("sketch", "face"),
    needs_body=True,
    groups=[
        Group("Профиль", boxes=[_profile_box()]),
        Group("Начало", boxes=[_start_box()], parameters=[
            Parameter("start_condition", "Начало", "choice", STARTS[0],
                      choices=STARTS),
            Parameter("start_offset", "Смещение", "number", 0.0, -1e5, 1e5, 3,
                      suffix=" мм", visible_when=_start_offset_shown),
        ]),
        Group("Направление 1", boxes=[_target_box()], parameters=[
            Parameter("end_condition", "Концевое условие", "choice",
                      CUT_ENDS[0], choices=CUT_ENDS),
            Parameter("depth", "Глубина", "number", 5.0, 0.001, 1e5, 3,
                      suffix=" мм", visible_when=_length_shown),
            Parameter("reverse", "Обратное направление", "flag", False),
            # По спецификации: `flip_side_to_cut` есть у выреза и нет
            # у бобышки. Снимается материал не внутри контура, а вне.
            Parameter("flip_side_to_cut", "Переставить сторону", "flag",
                      False),
            Parameter("draft_enabled", "Уклон", "flag", False),
            Parameter("draft_angle", "Угол уклона", "number", 1.0, 0.0, 89.0, 2,
                      suffix=" град", depends_on="draft_enabled"),
            Parameter("draft_outward", "Уклон наружу", "flag", True,
                      depends_on="draft_enabled"),
        ]),
        Group("Направление 2", boxes=[_target2_box()],
              visible_when=_second_available, parameters=[
            Parameter("direction2", "Второе направление", "flag", False),
            Parameter("end_condition2", "Концевое условие", "choice",
                      CUT_ENDS[0], choices=CUT_ENDS,
                      depends_on="direction2"),
            Parameter("depth2", "Глубина", "number", 5.0, 0.001, 1e5, 3,
                      suffix=" мм", visible_when=_length2_shown),
            Parameter("draft_angle2", "Угол уклона", "number", 0.0, -89.0, 89.0,
                      2, suffix=" град", depends_on="direction2"),
        ]),
        _thin_group(),
        Group("Выбранные контуры", boxes=[_contours_box()],
              visible_when=_contours_shown),
        Group("Область действия", parameters=[
            # По спецификации, секция `scope`. У `PartDesign::Body` нет
            # свойства «слить»: выключенное объединение означает НОВОЕ
            # тело, и так это и делается.
            Parameter("merge_result", "Объединить результаты", "flag", True),
            Parameter("scope", "Строить в теле", "choice", SCOPE_AUTO,
                      choices=_scope_choices,
                      visible_when=_many_bodies),
        ]),
    ],
    build=_build_cut,
    check=_check_extrusion,
    load=_load_extrusion,
)


# --- Вращение ---------------------------------------------------------------

AXES = ("X", "Y", "Z")


def _build_revolve(session, context: BuildContext):
    from commands import AXES

    return Operation(
        "revolve", context.unique_name("Вращение"),
        sketch=_picked_sketch(session),
        angle=session.value("angle"),
        axis=AXES.get(session.value("axis"), AXES["Y"]),
    )


def _named_axis(vector, table, default: str) -> str:
    """Название оси по её вектору. Сравнение по числам, а не по памяти.

    У операции хранится вектор, а в панели — буква. Обратный перевод
    нужен правке: без него панель открывалась бы с осью по умолчанию, и
    подтверждение молча разворачивало бы деталь.
    """
    for name, axis in table.items():
        if all(abs(float(axis[i]) - float(vector[i])) < 1e-9 for i in range(3)):
            return name
    return default


def _load_revolve(session, operation, context: BuildContext) -> None:
    """Заполнить панель вращения значениями готовой операции."""
    from command_session import Pick

    from commands import AXES as AXIS_TABLE

    if operation.sketch is not None:
        put(session, "profile", [Pick("sketch", 1, operation.sketch.name,
                                      operation.sketch)])
    session.set_value("axis", _named_axis(operation.axis, AXIS_TABLE, "Y"))
    session.set_value("angle", operation.angle)


REVOLVE = CommandDescriptor(
    key="revolve",
    title="Повёрнутая бобышка",
    preselection=("sketch",),
    needs_body=False,
    groups=[
        Group("Профиль", boxes=[_profile_box()]),
        Group("Ось вращения", parameters=[
            Parameter("axis", "Ось", "choice", "Y", choices=AXES),
        ]),
        Group("Направление", parameters=[
            Parameter("angle", "Угол", "number", 360.0, 0.1, 360.0, 2, suffix=" °"),
        ]),
    ],
    build=_build_revolve,
    load=_load_revolve,
)


# --- Скругление и фаска -----------------------------------------------------

FILLET_SCOPE = ("Выбранные рёбра", "Все вертикальные", "Все рёбра")

#: Типы фаски. Все три умеет сам `PartDesign::Chamfer` (§20.1), и здесь
#: они только названы по-русски. По спецификации умолчание — «угол и
#: расстояние» с углом 45°; на прямом ребре это то же, что равные катеты,
#: на непрямом — уже нет, и потому оба типа оставлены порознь.
CHAMFER_TYPES = ("Угол и расстояние", "Два расстояния", "Равные расстояния")
CHAMFER_KINDS = {"Угол и расстояние": "angle", "Два расстояния": "two",
                 "Равные расстояния": "equal"}


def _items_box(label: str) -> SelectionBox:
    """Объекты обработки: рёбра и грани.

    Указанная ГРАНЬ означает все её рёбра, и разворачивает это движок: он
    держит форму. По спецификации поле принимает ещё петлю и операцию
    целиком — этого нет, и записано пробелом.
    """
    return SelectionBox(
        "edges", label, accepts=("edge", "face"), minimum=0, maximum=None,
        auto_advance=False,
    )


def _chamfer_second(session) -> bool:
    return session.value("chamfer_type") == CHAMFER_TYPES[1]


def _chamfer_angle(session) -> bool:
    return session.value("chamfer_type") == CHAMFER_TYPES[0]


def _load_dressup(session, operation, context: BuildContext) -> None:
    """Заполнить панель скругления или фаски."""
    session.set_value("scope", FILLET_SCOPE[0])
    put(session, "edges",
        edge_picks(context, operation.edges, operation.edge_names)
        + face_picks(context, operation.faces, operation.face_names))
    session.set_value("radius", operation.size)
    session.set_value("size", operation.size)
    session.set_value("size2", operation.size2)
    session.set_value("angle", operation.angle)
    session.set_value("flip_direction", operation.flip)
    for title, value in CHAMFER_KINDS.items():
        if value == operation.chamfer_type:
            session.set_value("chamfer_type", title)
            break


def _build_fillet(session, context: BuildContext):
    edges, names, faces, face_names = _dressup_items(session, context)
    return Operation(
        "fillet", context.unique_name("Скругление"),
        edges=edges, edge_names=names, faces=faces, face_names=face_names,
        size=session.value("radius"),
    )


def _dressup_items(session, context: BuildContext) -> tuple:
    """Объекты обработки: (рёбра, их имена, грани, их имена).

    Область «Выбранные рёбра» берёт указанное мышью; остальные — правило,
    применённое к описанию детали. Правило живёт в интерфейсе: движок не
    знает, что человек считает вертикальным.

    У каждого подэлемента ДВА адреса: номер годен указать здесь и сейчас,
    устойчивое имя — пережить правку выше по дереву.
    """
    from commands import pick_edges

    scope = session.value("scope")
    if scope == FILLET_SCOPE[0]:
        box = session.box("edges")
        items = list(box.items if box else ())
        edges = [pick for pick in items if pick.kind == "edge"]
        faces = [pick for pick in items if pick.kind == "face"]
        return ([int(pick.id) for pick in edges],
                [_picked_name(pick) for pick in edges],
                [int(pick.id) for pick in faces],
                [_picked_name(pick) for pick in faces])
    if context.model is None:
        return [], [], [], []
    chosen = pick_edges(context.model,
                        "vertical" if scope == FILLET_SCOPE[1] else "all")
    return (chosen,
            [context.model.name_of("edge", value) for value in chosen], [], [])


def _chosen_edges(session, context: BuildContext) -> tuple:
    """Прежнее имя: возвращает только рёбра. На него ссылаются проверки."""
    edges, names, _, _ = _dressup_items(session, context)
    return edges, names


def _check_edge_scope(session, context) -> Validation | None:
    if session.value("scope") == FILLET_SCOPE[0] and not session.box("edges").items:
        return Validation(
            "input", False,
            "Выберите рёбра или грани в виде либо смените область", "edges"
        )
    return None


FILLET = CommandDescriptor(
    key="fillet",
    title="Скругление",
    preselection=("edge",),
    needs_body=True,
    groups=[
        # По спецификации, 110_fillet.yaml. Секции «Тип скругления»,
        # «Переменный радиус» и «Параметры скругления» не показаны:
        # `PartDesign::Fillet` умеет только постоянный радиус, и предлагать
        # остальное значило бы обещать несуществующее (FILLET-GAP-001).
        Group("Объекты скругления", boxes=[_items_box("Рёбра и грани")],
              parameters=[
                  Parameter("scope", "Область", "choice", FILLET_SCOPE[0],
                            choices=FILLET_SCOPE),
              ]),
        Group("Параметры", parameters=[
            Parameter("radius", "Радиус", "number", 2.0, 0.001, 1e5, 3, suffix=" мм"),
        ]),
    ],
    build=_build_fillet,
    check=_check_edge_scope,
    load=_load_dressup,
)


def _build_chamfer(session, context: BuildContext):
    edges, names, faces, face_names = _dressup_items(session, context)
    return Operation(
        "chamfer", context.unique_name("Фаска"),
        edges=edges, edge_names=names, faces=faces, face_names=face_names,
        size=session.value("size"),
        chamfer_type=CHAMFER_KINDS.get(session.value("chamfer_type"), "equal"),
        size2=float(session.value("size2") or 0.0),
        angle=float(session.value("angle") or 45.0),
        flip=bool(session.value("flip_direction")),
    )


CHAMFER = CommandDescriptor(
    key="chamfer",
    title="Фаска",
    preselection=("edge",),
    needs_body=True,
    groups=[
        # По спецификации, 120_chamfer.yaml.
        Group("Тип фаски", parameters=[
            Parameter("chamfer_type", "Тип", "choice", CHAMFER_TYPES[0],
                      choices=CHAMFER_TYPES),
        ]),
        Group("Объекты фаски", boxes=[_items_box("Рёбра и грани")],
              parameters=[
                  Parameter("scope", "Область", "choice", FILLET_SCOPE[0],
                            choices=FILLET_SCOPE[:2]),
              ]),
        Group("Параметры", parameters=[
            Parameter("size", "Расстояние", "number", 1.0, 0.001, 1e5, 3,
                      suffix=" мм"),
            Parameter("size2", "Расстояние 2", "number", 1.0, 0.001, 1e5, 3,
                      suffix=" мм", visible_when=_chamfer_second),
            Parameter("angle", "Угол", "number", 45.0, 0.1, 89.9, 2,
                      suffix=" град", visible_when=_chamfer_angle),
            Parameter("flip_direction", "Поменять стороны местами", "flag",
                      False),
        ]),
    ],
    build=_build_chamfer,
    check=_check_edge_scope,
    load=_load_dressup,
)


# --- Уклон -----------------------------------------------------------------
#
# По спецификации, 130_draft.yaml. Секция «Тип уклона» не показана:
# `PartDesign::Draft` умеет уклон только от нейтральной плоскости, а по
# линии разъёма и ступенчатый — нет (DRAFT-GAP-001).


def _faces_of(session, box_id: str) -> tuple:
    """Номера и устойчивые имена граней из поля выбора."""
    box = session.box(box_id)
    items = list(box.items if box else ())
    return ([int(pick.id) for pick in items],
            [_picked_name(pick) for pick in items])


def _load_draft(session, operation, context: BuildContext) -> None:
    put(session, "neutral", face_picks(context, [operation.neutral],
                                       [operation.neutral_name]))
    put(session, "faces", face_picks(context, operation.faces,
                                     operation.face_names))
    session.set_value("angle", operation.angle)
    session.set_value("draft_outward", operation.outward)


def _build_draft(session, context: BuildContext):
    faces, names = _faces_of(session, "faces")
    neutral, neutral_names = _faces_of(session, "neutral")
    return Operation(
        "draft", context.unique_name("Уклон"),
        faces=faces, face_names=names,
        neutral=neutral[0] if neutral else -1,
        neutral_name=neutral_names[0] if neutral_names else "",
        angle=float(session.value("angle") or 1.0),
        outward=bool(session.value("draft_outward")),
    )


def _check_draft(session, context) -> Validation | None:
    if not (session.box("faces") and session.box("faces").items):
        return Validation("input", False,
                          "Выберите грани, которые надо наклонить", "faces")
    if not (session.box("neutral") and session.box("neutral").items):
        return Validation(
            "input", False,
            "Укажите нейтральную грань: вокруг неё идёт наклон", "neutral")
    return None


DRAFT = CommandDescriptor(
    key="draft",
    title="Уклон",
    preselection=("face",),
    needs_body=True,
    groups=[
        Group("Определение", boxes=[SelectionBox(
            "neutral", "Нейтральная грань", accepts=("face", "plane"),
            minimum=1, maximum=1)]),
        # Минимум ОДНА грань, хотя верхней границы нет: без этого поле
        # никогда не считается незаполненным, и щелчок после нейтральной
        # грани не переходит сюда — попадает мимо, как когда-то «до грани».
        Group("Грани", boxes=[SelectionBox(
            "faces", "Наклоняемые грани", accepts=("face",),
            minimum=1, maximum=None, auto_advance=False)]),
        Group("Параметры", parameters=[
            # Умолчание — 1°, как в спецификации: это обычный
            # технологический минимум для съёма из формы.
            Parameter("angle", "Угол", "number", 1.0, 0.01, 89.0, 2,
                      suffix=" град"),
            Parameter("draft_outward", "Уклон наружу", "flag", False),
        ]),
    ],
    build=_build_draft,
    check=_check_draft,
    load=_load_draft,
)


# --- Оболочка, отверстие, зеркало ------------------------------------------

def _load_shell(session, operation, context: BuildContext) -> None:
    put(session, "faces", face_picks(context, operation.faces,
                                     operation.face_names))
    session.set_value("thickness", operation.thickness)
    session.set_value("outward", operation.outward)


def _build_shell(session, context: BuildContext):
    from commands import top_face

    faces, names = _faces_of(session, "faces")
    if not faces and context.model is not None:
        # Ни одной грани не указано — снимается верхняя. Это НЕ догадка о
        # намерении: оболочка без снятых граней даёт замкнутую полость,
        # которой снаружи не видно, и чаще всего человек имел в виду не её.
        number = top_face(context.model)
        faces = [number] if number >= 0 else []
        names = [context.model.name_of("face", number)] if number >= 0 else []
    return Operation("shell", context.unique_name("Оболочка"),
                     faces=faces, face_names=names,
                     thickness=session.value("thickness"),
                     outward=bool(session.value("outward")))


SHELL = CommandDescriptor(
    key="shell",
    title="Оболочка",
    preselection=("face",),
    needs_body=True,
    groups=[
        # По спецификации, 140_shell.yaml. Секции «Разная толщина» и
        # «Диагностика» не показаны: у `PartDesign::Thickness` одно
        # значение толщины на операцию (SHELL-GAP-001).
        Group("Параметры", parameters=[
            Parameter("thickness", "Толщина стенки", "number", 2.0, 0.001, 1e5, 3,
                      suffix=" мм"),
            Parameter("outward", "Наружу", "flag", False),
        ]),
        Group("Удаляемые грани", boxes=[SelectionBox(
            "faces", "Грани, которые снять", accepts=("face",),
            minimum=0, maximum=None, auto_advance=False)]),
    ],
    build=_build_shell,
    load=_load_shell,
)


def _build_hole(session, context: BuildContext):
    from commands import make_operation

    return make_operation(
        "hole", {"x": session.value("x"), "y": session.value("y"),
                 "radius": session.value("radius")},
        context.document, model=context.model)


def _load_hole(session, operation, context: BuildContext) -> None:
    """Заполнить панель отверстия значениями готовой операции.

    Место отверстия хранится не числами, а ОКРУЖНОСТЬЮ в эскизе: движку
    нужен эскиз, и подвинуть отверстие можно прямо в нём. Поэтому и читать
    координаты надо оттуда, а не из полей операции, которых нет. Радиус
    берётся из диаметра операции: это её собственная величина.
    """
    sketch = operation.sketch
    centre = (0.0, 0.0)
    if sketch is not None:
        for segment in sketch.segments:
            if segment.kind == "circle":
                centre = sketch.coordinates(segment.points[0])
                break
    session.set_value("x", centre[0])
    session.set_value("y", centre[1])
    session.set_value("radius", operation.diameter / 2.0)


HOLE = CommandDescriptor(
    key="hole",
    title="Отверстие",
    needs_body=True,
    groups=[
        Group("Положение", parameters=[
            Parameter("x", "X", "number", 10.0, -1e5, 1e5, 3, suffix=" мм"),
            Parameter("y", "Y", "number", 10.0, -1e5, 1e5, 3, suffix=" мм"),
        ]),
        Group("Размер", parameters=[
            Parameter("radius", "Радиус", "number", 3.0, 0.001, 1e5, 3, suffix=" мм"),
        ]),
    ],
    build=_build_hole,
    load=_load_hole,
)


def _build_hole_pattern(session, context: BuildContext):
    from commands import make_operation

    return make_operation(
        "hole_pattern",
        {"x": session.value("x"), "y": session.value("y"),
         "radius": session.value("radius"),
         "count_x": int(session.value("count_x")),
         "step_x": session.value("step_x"),
         "count_y": int(session.value("count_y")),
         "step_y": session.value("step_y")},
        context.document, model=context.model)


HOLE_PATTERN = CommandDescriptor(
    key="hole_pattern",
    title="Массив отверстий",
    needs_body=True,
    groups=[
        Group("Первое отверстие", parameters=[
            Parameter("x", "X", "number", 10.0, -1e5, 1e5, 3, suffix=" мм"),
            Parameter("y", "Y", "number", 10.0, -1e5, 1e5, 3, suffix=" мм"),
            Parameter("radius", "Радиус", "number", 3.0, 0.001, 1e5, 3, suffix=" мм"),
        ]),
        Group("Направление 1", parameters=[
            Parameter("count_x", "Количество", "integer", 3, 1, 500),
            Parameter("step_x", "Шаг", "number", 20.0, 0.001, 1e5, 3, suffix=" мм"),
        ]),
        Group("Направление 2", parameters=[
            Parameter("count_y", "Количество", "integer", 2, 1, 500),
            Parameter("step_y", "Шаг", "number", 20.0, 0.001, 1e5, 3, suffix=" мм"),
        ]),
    ],
    build=_build_hole_pattern,
)


PLANES = ("YZ", "XZ", "XY")


def _mirror_choices(session) -> tuple:
    """Плоскости отражения: три стандартные и все СОЗДАННЫЕ.

    Список вычисляемый: какие плоскости построены, заранее не известно, а
    записанный заранее был бы списком плоскостей какой-то другой детали.
    Без этого отразить относительно построенной плоскости было нельзя —
    её просто не предлагали.
    """
    document = getattr(getattr(session, "context", None), "document", None)
    made = tuple(record.name for record in getattr(document, "planes", ()) or ())
    return PLANES + made


def _mirror_made(session) -> bool:
    """Выбрана ли созданная плоскость, а не стандартная."""
    return str(session.value("plane") or "") not in PLANES


def _build_mirror(session, context: BuildContext):
    from commands import MIRROR_PLANES

    chosen = str(session.value("plane") or "YZ")
    if chosen not in MIRROR_PLANES:
        # Созданная плоскость запоминается ИМЕНЕМ: она параметрическая и
        # едет за деталью, а снятые с неё числа остались бы там, где она
        # была когда-то. Смещение здесь не спрашивается — плоскость сама
        # стоит там, где её построили.
        return Operation(
            "mirror", context.unique_name("Зеркало"), whole_shape=True,
            plane_name=chosen,
        )
    normal = MIRROR_PLANES[chosen]
    offset = float(session.value("offset") or 0.0)
    return Operation(
        "mirror", context.unique_name("Зеркало"), whole_shape=True,
        plane_normal=normal,
        plane_origin=tuple(value * offset for value in normal),
    )


def _load_mirror(session, operation, context: BuildContext) -> None:
    """Заполнить панель зеркала значениями готовой операции.

    Смещение считается ВДОЛЬ нормали — так же, как и записывалось: точка
    на плоскости хранится вектором, и раскладывать её обратно надо тем же
    правилом, иначе правка сдвинет зеркало.
    """
    from commands import MIRROR_PLANES

    if operation.plane_name:
        session.set_value("plane", operation.plane_name)
        session.set_value("offset", 0.0)
        return
    plane = _named_axis(operation.plane_normal, MIRROR_PLANES, "YZ")
    session.set_value("plane", plane)
    normal = MIRROR_PLANES[plane]
    session.set_value("offset", sum(float(operation.plane_origin[i]) * normal[i]
                                    for i in range(3)))


MIRROR = CommandDescriptor(
    key="mirror",
    title="Зеркало",
    needs_body=True,
    groups=[
        Group("Плоскость отражения", parameters=[
            Parameter("plane", "Плоскость", "choice", "YZ",
                      choices=_mirror_choices),
            # Смещение — только у стандартных плоскостей. У созданной оно
            # спрашивало бы второй раз то, что у неё уже задано, и два
            # места одного положения разошлись бы при первой же правке.
            Parameter("offset", "Смещение", "number", 0.0, -1e5, 1e5, 3,
                      suffix=" мм",
                      visible_when=lambda session: not _mirror_made(session)),
        ]),
    ],
    build=_build_mirror,
    load=_load_mirror,
)


#: Оси для направления ряда и оси вращения. Выбор из трёх, а не поле
#: указания ребра: ребро как направление спецификация тоже допускает, но
#: своего вида выбора у нас для него ещё нет, и притворяться, что есть, —
#: хуже, чем честно дать оси (`docs/07_SKETCHER_LOG.md`, PATTERN-GAP-001).
AXES = ("X", "Y", "Z")
AXIS_VECTORS = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0),
                "Z": (0.0, 0.0, 1.0)}

#: Чем задан ряд.
ROW_MODES = ("Шаг между соседними", "Уложить на длину")


def _seed_box():
    """Какие операции размножать. Указываются в дереве построения."""
    return SelectionBox(
        # В подписи сказано, ГДЕ указывать: поле набирается щелчками по
        # дереву построения, а не по детали в виде, и без этого человек
        # видит «не хватает объектов» и не знает, куда нажать.
        "sources", "Операции (указать в дереве)", accepts=("feature",),
        minimum=1, auto_advance=False,
    )


def _sources_of(session) -> list:
    box = session.box("sources")
    return [pick.label for pick in box.items] if box is not None else []


def _axis_box() -> SelectionBox:
    """Ребро как ОСЬ вращения: круглое задаёт ось и центр, прямое — ось."""
    return SelectionBox(
        "axis_edge", "Ребро оси (не обязательно)", accepts=("edge",),
        minimum=0, maximum=1, auto_advance=False,
    )


def _second_on(session) -> bool:
    return bool(session.value("second"))


def _edge_box(box_id: str, visible=None) -> SelectionBox:
    """Ребро как направление. Необязательное: без него берётся ось.

    Обязательным его делать нельзя. Ребра нужного направления в детали
    может не быть вовсе — например, у первой операции, где деталь ещё
    круглая, — и тогда команда стала бы недоступной без всякой причины.
    """
    return SelectionBox(
        box_id, "Ребро направления (не обязательно)", accepts=("edge",),
        minimum=0, maximum=1, auto_advance=False, visible_when=visible,
    )


def _picked_edge(session, box_id: str):
    """Описание указанного ребра. ``None`` — ребро не указано."""
    box = session.box(box_id)
    if box is None or not box.items:
        return None
    return getattr(box.items[0].data, "data", None) or {}


def _no_edge(box_id: str):
    """Условие «ребро не указано»: тогда и спрашиваем ось из списка."""
    def shown(session) -> bool:
        return _picked_edge(session, box_id) is None
    return shown


def _straight(data) -> tuple | None:
    """Направление прямого ребра единичным вектором. ``None`` — не прямое.

    Проверяется не название кривой, а концы: ребро, у которого начало и
    конец совпадают, направления не задаёт, как бы оно ни называлось.
    """
    if not data:
        return None
    start, end = data.get("start"), data.get("end")
    if not start or not end:
        return None
    vector = tuple(float(end[i]) - float(start[i]) for i in range(3))
    length = sum(value * value for value in vector) ** 0.5
    if length < 1e-9:
        return None
    # Прямое ребро идёт из конца в конец по прямой: у дуги длина по кривой
    # больше расстояния между концами, и брать её за направление нельзя.
    along = float(data.get("length") or length)
    if along > length * 1.001:
        return None
    return tuple(value / length for value in vector)


def _round(data) -> tuple | None:
    """Ось круглого ребра: ``(нормаль, центр)``. ``None`` — не круглое."""
    if not data or not data.get("center") or not data.get("normal"):
        return None
    return (tuple(float(v) for v in data["normal"]),
            tuple(float(v) for v in data["center"]))


def _direction_of(session, box_id: str, axis_key: str) -> tuple:
    """Направление ряда: по указанному ребру, иначе по выбранной оси."""
    picked = _straight(_picked_edge(session, box_id))
    return picked if picked is not None else AXIS_VECTORS[
        str(session.value(axis_key))]


def _skipped(session) -> list:
    """Номера пропускаемых экземпляров ПО ПОРЯДКУ ПОСТРОЕНИЯ, с нуля.

    Человек пишет их с единицы — «пропустить 2 и 4», — потому что считает
    сам исходник первым. Внутрь уходит счёт с нуля: там ноль это исходник,
    и пропустить его нельзя.
    """
    text = str(session.value("skip") or "")
    found = []
    for piece in text.replace(";", ",").replace(" ", ",").split(","):
        if not piece.strip():
            continue
        try:
            number = int(piece)
        except ValueError:
            continue
        if number >= 2:
            found.append(number - 1)
    return sorted(set(found))


def _skip_field() -> Parameter:
    # Подпись не повторяет заголовок группы: «Пропустить экземпляры:
    # Пропустить экземпляры» читается как ошибка вёрстки, каковой и
    # является. Здесь сказано, ЧТО писать.
    return Parameter(
        "skip", "Номера через запятую", "text", "", suffix="")


def _by_length(session) -> bool:
    return str(session.value("row_mode")) == ROW_MODES[1]


def _by_length2(session) -> bool:
    return (bool(session.value("second"))
            and str(session.value("row_mode2")) == ROW_MODES[1])


def _by_step(session) -> bool:
    return not _by_length(session)


def _by_step2(session) -> bool:
    return bool(session.value("second")) and not _by_length2(session)


def _mode_of(session, key: str) -> str:
    return "extent" if str(session.value(key)) == ROW_MODES[1] else "spacing"


def _build_linear_pattern(session, context: BuildContext):
    second = bool(session.value("second"))
    return Operation(
        "linear", context.unique_name("Массив"),
        sources=_sources_of(session),
        count=int(session.value("count")),
        spacing=float(session.value("spacing")),
        pattern_mode=_mode_of(session, "row_mode"),
        pattern_length=float(session.value("length")),
        direction=_direction_of(session, "direction_edge", "axis"),
        reversed=bool(session.value("reverse")),
        count2=int(session.value("count2")) if second else 1,
        spacing2=float(session.value("spacing2")),
        pattern_mode2=_mode_of(session, "row_mode2"),
        pattern_length2=float(session.value("length2")),
        pattern_direction2=_direction_of(session, "direction_edge2", "axis2"),
        reversed2=bool(session.value("reverse2")),
        skip=_skipped(session),
    )


def _check_pattern(session, context) -> Validation | None:
    if not _sources_of(session):
        return Validation("input", False,
                          "Укажите в дереве операции, которые размножать",
                          "sources")
    # Указанное ребро либо задаёт направление, либо не задаёт. Молча взять
    # вместо него ось из списка нельзя: человек указал ребро, а построилось
    # бы вдоль другого, и заметил бы он это по готовой детали.
    for box_id, what in (("direction_edge", "Направление 1"),
                         ("direction_edge2", "Направление 2")):
        data = _picked_edge(session, box_id)
        if data is not None and _straight(data) is None:
            return Validation(
                "semantic", False,
                f"{what}: указанное ребро не прямое — направление по нему "
                f"не определено. Укажите прямое ребро или выберите ось",
                box_id)
    return None


def _put_sources(session, operation, context: BuildContext) -> None:
    """Вернуть в поле те операции, которые массив размножает.

    Ссылка на них — ИМЯ в дереве, и подставляется оно же: номер строки
    после вставки другой операции указывал бы уже не туда.
    """
    from command_session import Pick

    from protocad.engine import EntityRef

    document = getattr(context, "document", None)
    picks = []
    for name in operation.sources:
        item = document.by_name(name) if document is not None else None
        index = (document.operations.index(item)
                 if item is not None and document is not None else -1)
        picks.append(Pick("feature", index, name,
                          EntityRef(id=f"feature:{name}", kind="feature",
                                    index=index, name=name)))
    put(session, "sources", picks)


def _skip_text(operation) -> str:
    """Пропущенные экземпляры обратно в строку — с ЕДИНИЦЫ, как их пишут."""
    return ", ".join(str(int(value) + 1) for value in sorted(operation.skip))


def _load_linear_pattern(session, operation, context: BuildContext) -> None:
    _put_sources(session, operation, context)
    session.set_value("axis", _named_axis(operation.direction, AXIS_VECTORS, "X"))
    session.set_value("count", operation.count)
    session.set_value("row_mode",
                      ROW_MODES[1] if operation.pattern_mode == "extent"
                      else ROW_MODES[0])
    session.set_value("spacing", operation.spacing)
    session.set_value("length", operation.pattern_length)
    session.set_value("reverse", operation.reversed)
    second = int(operation.count2) > 1
    session.set_value("second", second)
    if second:
        session.set_value("axis2", _named_axis(operation.pattern_direction2,
                                               AXIS_VECTORS, "Y"))
        session.set_value("count2", operation.count2)
        session.set_value("row_mode2",
                          ROW_MODES[1] if operation.pattern_mode2 == "extent"
                          else ROW_MODES[0])
        session.set_value("spacing2", operation.spacing2)
        session.set_value("length2", operation.pattern_length2)
        session.set_value("reverse2", operation.reversed2)
    session.set_value("skip", _skip_text(operation))


LINEAR_PATTERN = CommandDescriptor(
    key="linear_pattern",
    title="Линейный массив",
    needs_body=True,
    preselection=("feature",),
    groups=[
        Group("Объекты массива", boxes=[_seed_box()]),
        Group("Направление 1", boxes=[_edge_box("direction_edge")],
              parameters=[
            Parameter("axis", "Вдоль оси", "choice", AXES[0], choices=AXES,
                      visible_when=_no_edge("direction_edge")),
            Parameter("count", "Количество", "integer", 3, 2, 500),
            Parameter("row_mode", "Задать", "choice", ROW_MODES[0],
                      choices=ROW_MODES),
            Parameter("spacing", "Шаг", "number", 20.0, 0.001, 1e5, 3,
                      suffix=" мм", visible_when=_by_step),
            Parameter("length", "Общая длина", "number", 100.0, 0.001, 1e5, 3,
                      suffix=" мм", visible_when=_by_length),
            Parameter("reverse", "В обратную сторону", "flag", False),
        ]),
        Group("Направление 2",
              boxes=[_edge_box("direction_edge2", _second_on)],
              parameters=[
            Parameter("second", "Второе направление", "flag", False),
            Parameter("axis2", "Вдоль оси", "choice", AXES[1], choices=AXES,
                      depends_on="second",
                      visible_when=_no_edge("direction_edge2")),
            Parameter("count2", "Количество", "integer", 2, 2, 500,
                      depends_on="second"),
            Parameter("row_mode2", "Задать", "choice", ROW_MODES[0],
                      choices=ROW_MODES, depends_on="second"),
            Parameter("spacing2", "Шаг", "number", 20.0, 0.001, 1e5, 3,
                      suffix=" мм", visible_when=_by_step2),
            Parameter("length2", "Общая длина", "number", 100.0, 0.001, 1e5, 3,
                      suffix=" мм", visible_when=_by_length2),
            Parameter("reverse2", "В обратную сторону", "flag", False,
                      depends_on="second"),
        ]),
        Group("Пропустить экземпляры", parameters=[_skip_field()]),
    ],
    check=_check_pattern,
    build=_build_linear_pattern,
    load=_load_linear_pattern,
)


def _by_angle(session) -> bool:
    return bool(session.value("equal"))


def _by_angle_step(session) -> bool:
    return not bool(session.value("equal"))


def _axis_of(session) -> tuple:
    """Ось вращения: по указанному ребру, иначе по осям и числам.

    Круглое ребро задаёт ОСЬ И ЦЕНТР сразу — этого и ждут, указывая на
    отверстие: «размножить вокруг него». Прямое ребро задаёт ось и точку
    на ней. Без ребра остаются список осей и три числа.
    """
    data = _picked_edge(session, "axis_edge")
    circle = _round(data)
    if circle is not None:
        return circle
    straight = _straight(data)
    if straight is not None:
        return (straight, tuple(float(v) for v in (data.get("start") or
                                                   (0.0, 0.0, 0.0))))
    return (AXIS_VECTORS[str(session.value("axis"))],
            (float(session.value("centre_x")),
             float(session.value("centre_y")),
             float(session.value("centre_z"))))


def _build_circular_pattern(session, context: BuildContext):
    axis, origin = _axis_of(session)
    return Operation(
        "polar", context.unique_name("Массив"),
        sources=_sources_of(session),
        count=int(session.value("count")),
        axis=axis,
        axis_origin=origin,
        angle=float(session.value("angle")),
        equal_spacing=bool(session.value("equal")),
        angle_step=float(session.value("angle_step")),
        reversed=bool(session.value("reverse")),
        skip=_skipped(session),
    )


def _load_circular_pattern(session, operation, context: BuildContext) -> None:
    _put_sources(session, operation, context)
    session.set_value("axis", _named_axis(operation.axis, AXIS_VECTORS, "Z"))
    session.set_value("centre_x", operation.axis_origin[0])
    session.set_value("centre_y", operation.axis_origin[1])
    session.set_value("centre_z", operation.axis_origin[2])
    session.set_value("count", operation.count)
    session.set_value("equal", operation.equal_spacing)
    session.set_value("angle", operation.angle)
    session.set_value("angle_step", operation.angle_step)
    session.set_value("reverse", operation.reversed)
    session.set_value("skip", _skip_text(operation))


CIRCULAR_PATTERN = CommandDescriptor(
    key="circular_pattern",
    title="Круговой массив",
    needs_body=True,
    preselection=("feature",),
    groups=[
        Group("Объекты массива", boxes=[_seed_box()]),
        Group("Ось вращения", boxes=[_axis_box()], parameters=[
            Parameter("axis", "Ось", "choice", AXES[2], choices=AXES,
                      visible_when=_no_edge("axis_edge")),
            Parameter("centre_x", "Точка на оси, X", "number", 0.0,
                      -1e5, 1e5, 3, suffix=" мм",
                      visible_when=_no_edge("axis_edge")),
            Parameter("centre_y", "Точка на оси, Y", "number", 0.0,
                      -1e5, 1e5, 3, suffix=" мм",
                      visible_when=_no_edge("axis_edge")),
            Parameter("centre_z", "Точка на оси, Z", "number", 0.0,
                      -1e5, 1e5, 3, suffix=" мм",
                      visible_when=_no_edge("axis_edge")),
        ]),
        Group("Размещение", parameters=[
            Parameter("count", "Количество", "integer", 4, 2, 500),
            Parameter("equal", "Разложить поровну", "flag", True),
            Parameter("angle", "Угол", "number", 360.0, 0.001, 360.0, 2,
                      suffix=" град", visible_when=_by_angle),
            Parameter("angle_step", "Шаг", "number", 30.0, 0.001, 360.0, 2,
                      suffix=" град", visible_when=_by_angle_step),
            Parameter("reverse", "В обратную сторону", "flag", False),
        ]),
        Group("Пропустить экземпляры", parameters=[_skip_field()]),
    ],
    check=_check_pattern,
    build=_build_circular_pattern,
    load=_load_circular_pattern,
)


# --- Справочная плоскость ---------------------------------------------------
#
# Плоскость — не операция: она ничего не строит, она ОПОРА. Поэтому её
# запись хранится отдельно от дерева операций и пересчитывается вместе с
# деталью: плоскость, запомненная числами, после первой же правки остаётся
# там, где грань была раньше.

PLANE_KINDS = {
    "Параллельно на расстоянии": "offset",
    "Посередине между двумя": "midway",
    "Через вершину параллельно грани": "through",
}
PLANE_TITLES = tuple(PLANE_KINDS)


def _plane_kind(session) -> str:
    return PLANE_KINDS.get(str(session.value("kind")), "offset")


def _distance_shown(session) -> bool:
    return _plane_kind(session) == "offset"


def _plane_box() -> SelectionBox:
    return SelectionBox(
        "supports", "Опоры: грани или вершины",
        accepts=("face", "vertex", "plane"), minimum=1, maximum=2,
        auto_advance=False,
    )


def _supports_of(session) -> list:
    from protocad.reference import Reference

    box = session.box("supports")
    found = []
    for pick in (box.items if box is not None else ()):
        entry = pick.data
        found.append(Reference(kind=pick.kind, index=int(pick.id),
                               name=getattr(entry, "name", "") or ""))
    return found


def _build_plane(session, context: BuildContext):
    from protocad.reference import ReferencePlane

    return ReferencePlane(
        name=_unique_plane_name(context),
        kind=_plane_kind(session),
        references=_supports_of(session),
        distance=float(session.value("distance") or 0.0),
        flip=bool(session.value("flip")),
    )


def _unique_plane_name(context: BuildContext) -> str:
    document = getattr(context, "document", None)
    taken = {item.name for item in getattr(document, "planes", ())}
    number = 1
    while f"Плоскость{number}" in taken:
        number += 1
    return f"Плоскость{number}"


def _check_plane(session, context) -> Validation | None:
    """Сколько опор нужно — зависит от способа, и об этом надо сказать."""
    kind = _plane_kind(session)
    count = len(_supports_of(session))
    if kind == "offset" and count != 1:
        return Validation("input", False,
                          "Параллельной плоскости нужна одна опора — грань",
                          "supports")
    if kind in ("midway", "through") and count != 2:
        return Validation("input", False,
                          "Нужны две опоры: две грани, две вершины либо "
                          "вершина и грань", "supports")
    if kind == "offset" and abs(float(session.value("distance") or 0.0)) < 1e-9:
        return Validation("semantic", False,
                          "Расстояние ноль — это сама грань, а не новая "
                          "плоскость", "distance")
    return None


PLANE = CommandDescriptor(
    key="plane",
    title="Плоскость",
    needs_body=True,
    preselection=("face",),
    groups=[
        Group("Опоры", boxes=[_plane_box()]),
        Group("Способ", parameters=[
            Parameter("kind", "Построить", "choice", PLANE_TITLES[0],
                      choices=PLANE_TITLES),
            Parameter("distance", "Расстояние", "number", 10.0, -1e5, 1e5, 3,
                      suffix=" мм", visible_when=_distance_shown),
            Parameter("flip", "Развернуть нормаль", "flag", False),
        ]),
    ],
    check=_check_plane,
    build=_build_plane,
)


# --- Отверстие: осевая конструкция, а не диаметр ----------------------------
#
# По `docs/09_HOLES.md`. Порядок групп задан спецификацией (§6.1) и не
# переставляется; поля, не имеющие смысла при текущем типе, ПРЯЧУТСЯ, а не
# гаснут — погашенная строка занимает место и заставляет гадать, при каких
# условиях она оживёт.

#: Тип отверстия выбирается ЗНАЧКОМ: это форма, и словами её пришлось бы
#: переводить в картинку в голове. Пара — подпись и глиф.
HOLE_KINDS = (("Простое", "hole_simple"),
              ("С цековкой", "hole_counterbore"),
              ("С зенковкой", "hole_countersink"),
              ("Цековка и зенковка", "hole_both"),
              ("Коническое", "hole_taper"))
HOLE_KIND_TITLES = tuple(title for title, _ in HOLE_KINDS)
HOLE_ENDS = ("На глубину", "Насквозь")
HOLE_BOTTOMS = ("Плоское", "След сверла")
SINK_MODES = ("Диаметр и угол", "Глубина и угол", "Диаметр и глубина")
SINK_KEYS = {"Диаметр и угол": "diameter_angle",
             "Глубина и угол": "depth_angle",
             "Диаметр и глубина": "diameter_depth"}
NO_PRESET = "Без пресета"


def _hole_presets(session) -> tuple:
    from protocad.holes import titles

    return (NO_PRESET,) + tuple(titles())


def _by_preset(session) -> bool:
    return str(session.value("preset") or NO_PRESET) != NO_PRESET


def _by_hand(session) -> bool:
    """Поля описания видны только БЕЗ пресета.

    Пресет задаёт всю конструкцию целиком. Показывать рядом с ним поля,
    которые ни на что не влияют, — обещать правку, которой не будет.
    """
    return not _by_preset(session)


def _has_counterbore(session) -> bool:
    return (_by_hand(session)
            and str(session.value("kind")) in ("С цековкой",
                                               "Цековка и зенковка"))


def _has_countersink(session) -> bool:
    return (_by_hand(session)
            and str(session.value("kind")) in ("С зенковкой",
                                               "Цековка и зенковка"))


def _is_taper(session) -> bool:
    return _by_hand(session) and str(session.value("kind")) == "Коническое"


def _is_blind(session) -> bool:
    return _by_hand(session) and str(session.value("hole_end")) == "На глубину"


def _has_tip(session) -> bool:
    return _is_blind(session) and str(session.value("bottom")) == "След сверла"


def _sink_needs_diameter(session) -> bool:
    return (_has_countersink(session)
            and str(session.value("sink_mode")) != "Глубина и угол")


def _sink_needs_depth(session) -> bool:
    return (_has_countersink(session)
            and str(session.value("sink_mode")) != "Диаметр и угол")


def _sink_needs_angle(session) -> bool:
    return (_has_countersink(session)
            and str(session.value("sink_mode")) != "Диаметр и глубина")


def _far_on(session) -> bool:
    return _by_hand(session) and bool(session.value("far_side"))


def _hole_sketch(session):
    box = session.box("positions")
    for pick in (box.items if box is not None else ()):
        if getattr(pick, "data", None) is not None:
            return pick.data
    return None


# --- Резьба в отверстии (§15, §18) -----------------------------------------
#
# Порядок выбора задан §18: включили резьбу → стандарт → размер → шаг.
# Списки размеров и шагов приходят ИЗ КАТАЛОГА и вычисляются на месте:
# записанные заранее, они были бы списком другого стандарта.

THREAD_OFF = "Нет"


def _thread_families(session) -> tuple:
    from protocad.holes import CATALOG

    return (THREAD_OFF,) + tuple(item.display_name
                                 for item in CATALOG.families())


def _thread_family(session):
    """Выбранное семейство. ``None`` — резьба выключена."""
    from protocad.holes import CATALOG

    wanted = str(session.value("thread") or THREAD_OFF)
    if wanted == THREAD_OFF:
        return None
    for family in CATALOG.families():
        if family.display_name == wanted:
            return family
    return None


def _thread_on(session) -> bool:
    return _by_hand(session) and _thread_family(session) is not None


def _thread_sizes(session) -> tuple:
    family = _thread_family(session)
    return tuple(family.designations()) if family is not None else ()


def _thread_pitches(session) -> tuple:
    """Шаги ТОЛЬКО выбранного размера (§H-020).

    Ближайший не подбирается: шага, которого в этом семействе нет, быть не
    может, и молча взятый соседний дал бы резьбу, которой не заказывали.
    """
    family = _thread_family(session)
    if family is None:
        return ()
    try:
        size = family.size(str(session.value("thread_size") or ""))
    except Exception:  # noqa: BLE001 — размер ещё не выбран
        try:
            size = family.first_size()
        except Exception:  # noqa: BLE001
            return ()
    return tuple(f"{item.value_mm:g}" for item in size.pitches)


def _thread_classes(session) -> tuple:
    family = _thread_family(session)
    return tuple(family.tolerance_classes) if family is not None else ()


def _thread_of(session):
    """Описание резьбы по полям панели. ``None`` — резьба выключена."""
    from protocad.holes import ThreadDefinition

    family = _thread_family(session)
    if family is None:
        return None
    size = str(session.value("thread_size") or "")
    if not size:
        size = family.first_size().designation
    pitch = str(session.value("thread_pitch") or "")
    return ThreadDefinition(
        catalog_id=family.catalog_id,
        size_id=size,
        pitch_mm=float(pitch) if pitch else None,
        tolerance_class=str(session.value("thread_class") or ""),
        handedness=("left" if session.value("thread_left") else "right"),
        representation="cosmetic")


def _hole_definition(session):
    """Описание отверстия по полям панели либо по пресету."""
    from protocad.holes import (
        Counterbore, Countersink, EndCondition, HoleDefinition, StraightBore,
        TaperBore, by_title,
    )

    preset = by_title(str(session.value("preset") or ""))
    if preset is not None:
        return preset.definition()

    # Стек, собранный РЕДАКТОРОМ, главнее полей панели: он выражает то,
    # чего поля выразить не могут — произвольный порядок и повторы. Полями
    # его пересобрать нельзя, и молча их предпочесть значило бы отменить
    # сделанное человеком.
    made = getattr(session, "stack_definition", None)
    if made is not None:
        return made

    kind = str(session.value("kind"))
    diameter = float(session.value("diameter"))
    if kind == "Коническое":
        core = TaperBore(diameter_mm=diameter,
                         end_diameter_mm=float(session.value("end_diameter")),
                         depth_mm=float(session.value("depth")))
    else:
        core = StraightBore(diameter_mm=diameter)

    near = []
    if _has_counterbore(session):
        near.append(Counterbore(
            diameter_mm=float(session.value("cb_diameter")),
            depth_mm=float(session.value("cb_depth"))))
    if _has_countersink(session):
        near.append(Countersink(
            definition_mode=SINK_KEYS[str(session.value("sink_mode"))],
            diameter_mm=float(session.value("cs_diameter")),
            depth_mm=float(session.value("cs_depth")),
            included_angle_deg=float(session.value("cs_angle"))))
    far = []
    if _far_on(session):
        far.append(Countersink(
            definition_mode="diameter_angle",
            diameter_mm=float(session.value("far_diameter")),
            included_angle_deg=float(session.value("far_angle"))))

    threads = []
    made = _thread_of(session)
    if made is not None:
        threads.append(made)
    blind = _is_blind(session)
    return HoleDefinition(
        threads=threads,
        core=core, near_stack=near, far_stack=far,
        end_condition=EndCondition(
            type="blind" if blind else "through_all",
            depth_mm=float(session.value("depth")) if blind else None,
            bottom_type=("drill_tip"
                         if str(session.value("bottom")) == "След сверла"
                         else "flat"),
            tip_angle_deg=float(session.value("tip_angle"))))


def _build_hole_feature(session, context: BuildContext):
    from protocad.holes import HoleFeature

    return Operation(
        "hole_feature", context.unique_name("Отверстие"),
        sketch=_hole_sketch(session),
        hole=HoleFeature(definition=_hole_definition(session)),
        scope=_scope_of(session))


def _check_hole_feature(session, context) -> Validation | None:
    from protocad.holes import HoleFeature, blocking, check

    if _hole_sketch(session) is None:
        return Validation("semantic", False,
                          "Укажите эскиз с точками — по ним встанут "
                          "отверстия", "positions")
    # Описание проверяется ТЕМ ЖЕ кодом, что и при построении: отдельная
    # проверка в панели разошлась бы с настоящей на первом же случае.
    feature = HoleFeature(definition=_hole_definition(session))
    from protocad.holes import positions_from

    feature.placement.positions = positions_from(_hole_sketch(session))
    stopped = blocking(check(feature))
    if stopped:
        return Validation("semantic", False, stopped[0].message, "positions")
    return None


HOLE_FEATURE = CommandDescriptor(
    key="hole_feature",
    title="Отверстие",
    needs_body=True,
    groups=[
        Group("Пресет", parameters=[
            Parameter("preset", "Готовое", "choice", NO_PRESET,
                      choices=_hole_presets),
        ]),
        Group("Позиции", boxes=[SelectionBox(
            "positions", "Эскиз точек", accepts=("sketch",),
            minimum=1, maximum=1)]),
        Group("Тип конструкции", parameters=[
            Parameter("kind", "Тип", "icons", HOLE_KIND_TITLES[0],
                      choices=HOLE_KINDS, visible_when=_by_hand),
        ]),
        Group("Основной канал", parameters=[
            Parameter("diameter", "Диаметр", "number", 5.0, 0.001, 1e4, 3,
                      suffix=" мм", visible_when=_by_hand),
            Parameter("end_diameter", "Диаметр у дна", "number", 3.0,
                      0.001, 1e4, 3, suffix=" мм", visible_when=_is_taper),
            Parameter("hole_end", "Окончание", "choice", HOLE_ENDS[0],
                      choices=HOLE_ENDS, visible_when=_by_hand),
            Parameter("depth", "Глубина", "number", 10.0, 0.001, 1e4, 3,
                      suffix=" мм", visible_when=_is_blind),
        ]),
        Group("Сторона входа", parameters=[
            Parameter("cb_diameter", "Цековка ⌀", "number", 9.0, 0.001, 1e4,
                      3, suffix=" мм", visible_when=_has_counterbore),
            Parameter("cb_depth", "Цековка глубина", "number", 4.0, 0.001,
                      1e4, 3, suffix=" мм", visible_when=_has_counterbore),
            Parameter("sink_mode", "Зенковка задана", "choice",
                      SINK_MODES[0], choices=SINK_MODES,
                      visible_when=_has_countersink),
            Parameter("cs_diameter", "Зенковка ⌀", "number", 10.0, 0.001,
                      1e4, 3, suffix=" мм",
                      visible_when=_sink_needs_diameter),
            Parameter("cs_depth", "Зенковка глубина", "number", 2.5, 0.001,
                      1e4, 3, suffix=" мм", visible_when=_sink_needs_depth),
            Parameter("cs_angle", "Зенковка угол", "number", 90.0, 1.0,
                      179.0, 2, suffix="°", visible_when=_sink_needs_angle),
        ]),
        Group("Сторона выхода", parameters=[
            Parameter("far_side", "Обработать выход", "flag", False,
                      visible_when=_by_hand),
            Parameter("far_diameter", "Зенковка ⌀", "number", 10.0, 0.001,
                      1e4, 3, suffix=" мм", visible_when=_far_on),
            Parameter("far_angle", "Зенковка угол", "number", 90.0, 1.0,
                      179.0, 2, suffix="°", visible_when=_far_on),
        ]),
        Group("Резьба", parameters=[
            Parameter("thread", "Стандарт", "choice", THREAD_OFF,
                      choices=_thread_families, visible_when=_by_hand),
            Parameter("thread_size", "Размер", "choice", "",
                      choices=_thread_sizes, visible_when=_thread_on),
            Parameter("thread_pitch", "Шаг", "choice", "",
                      choices=_thread_pitches, visible_when=_thread_on),
            Parameter("thread_class", "Поле допуска", "choice", "",
                      choices=_thread_classes, visible_when=_thread_on),
            Parameter("thread_left", "Левая", "flag", False,
                      visible_when=_thread_on),
        ]),
        Group("Дно", parameters=[
            Parameter("bottom", "Дно", "choice", HOLE_BOTTOMS[0],
                      choices=HOLE_BOTTOMS, visible_when=_is_blind),
            Parameter("tip_angle", "Угол при вершине", "number", 118.0,
                      1.0, 179.0, 1, suffix="°", visible_when=_has_tip),
        ]),
        Group("Область действия", parameters=[
            Parameter("scope", "Тело", "choice", SCOPE_AUTO,
                      choices=_scope_choices, visible_when=_many_bodies),
        ]),
    ],
    check=_check_hole_feature,
    build=_build_hole_feature,
)


# --- По траектории, по сечениям, спираль ------------------------------------
#
# Все три строит FreeCAD (`PartDesign::Pipe`, `Loft`, `Helix`). Эскизы
# указываются ЩЕЛЧКОМ В ДЕРЕВЕ: в трёхмерном виде эскиз не выбирается.
# Вырез — та же команда с другим ключом: у движка это одна операция с
# другим знаком, и панели у них одинаковые.


def sketch_pick(sketch):
    """Эскиз как выбор в поле — с номером, который у эскиза СВОЙ.

    Равенство выбора — по виду и номеру. Пока у всех эскизов номер был
    один и тот же, второй эскиз в поле сечений считался «тем же самым» и
    снимал первый вместо того, чтобы встать за ним.
    """
    from command_session import Pick

    return Pick("sketch", id(sketch), getattr(sketch, "name", "Эскиз"), sketch)


def _sketch_box(box_id: str, label: str, many: bool = False) -> SelectionBox:
    return SelectionBox(box_id, label, accepts=("sketch",), minimum=1,
                        maximum=None if many else 1, ordered=many,
                        auto_advance=not many)


def _sketches_in(session, box_id: str) -> list:
    from protocad.sketch import Sketch

    box = session.box(box_id)
    return [pick.data for pick in (box.items if box else ())
            if isinstance(pick.data, Sketch)]


def _merge_group() -> Group:
    return Group("Область действия", parameters=[
        Parameter("merge_result", "Объединить результаты", "flag", True),
        Parameter("scope", "Строить в теле", "choice", SCOPE_AUTO,
                  choices=_scope_choices, visible_when=_many_bodies),
    ])


def _load_scope(session, operation) -> None:
    session.set_value("merge_result", operation.merge)
    session.set_value("scope", operation.scope or SCOPE_AUTO)


#: Ориентация профиля: подпись → ключ протокола.
SWEEP_ORIENTATIONS = {"По траектории": "standard",
                      "Постоянная нормаль": "fixed",
                      "По винтовой (Френе)": "frenet"}
#: Что делать на изломе траектории: подпись → ключ протокола.
SWEEP_CORNERS = {"Как есть": "transformed", "Острый угол": "right",
                 "Скругление": "round"}


def _title_of(table: dict, key: str) -> str:
    for title, value in table.items():
        if value == key:
            return title
    return next(iter(table))


def _check_sweep(session, context) -> Validation | None:
    profile = _sketches_in(session, "profile")
    path = _sketches_in(session, "path")
    if profile and path and profile[0] is path[0]:
        return Validation("semantic", False,
                          "Профиль и траектория — один и тот же эскиз: "
                          "траекторию рисуют отдельным эскизом", "path")
    return None


def _sweep(kind: str, title: str):
    def build(session, context: BuildContext):
        path = _sketches_in(session, "path")
        return Operation(
            kind, context.unique_name(title),
            sketch=_picked_sketch(session),
            path=path[0] if path else None,
            sweep_mode=SWEEP_ORIENTATIONS.get(session.value("orientation"),
                                              "standard"),
            transition=SWEEP_CORNERS.get(session.value("corner"),
                                         "transformed"),
            merge=bool(session.value("merge_result", True)),
            scope=_scope_of(session),
        )
    return build


def _load_sweep(session, operation, context: BuildContext) -> None:
    if operation.sketch is not None:
        put(session, "profile", [sketch_pick(operation.sketch)])
    if operation.path is not None:
        put(session, "path", [sketch_pick(operation.path)])
    session.set_value("orientation",
                      _title_of(SWEEP_ORIENTATIONS, operation.sweep_mode))
    session.set_value("corner", _title_of(SWEEP_CORNERS, operation.transition))
    _load_scope(session, operation)


def _sweep_descriptor(key: str, title: str, needs_body: bool) -> CommandDescriptor:
    return CommandDescriptor(
        key=key,
        title=title,
        preselection=("sketch",),
        needs_body=needs_body,
        groups=[
            Group("Профиль и траектория", boxes=[
                _sketch_box("profile", "Профиль"),
                _sketch_box("path", "Траектория"),
            ]),
            Group("Параметры", parameters=[
                Parameter("orientation", "Профиль идёт", "choice",
                          "По траектории", choices=tuple(SWEEP_ORIENTATIONS)),
                Parameter("corner", "На изломе траектории", "choice",
                          "Как есть", choices=tuple(SWEEP_CORNERS)),
            ]),
            _merge_group(),
        ],
        build=_sweep(key, title),
        check=_check_sweep,
        load=_load_sweep,
    )


SWEEP = _sweep_descriptor("sweep", "По траектории", needs_body=False)
SWEEP_CUT = _sweep_descriptor("sweep_cut", "Вырез по траектории", needs_body=True)


def _check_loft(session, context) -> Validation | None:
    profile = _sketches_in(session, "profile")
    sections = _sketches_in(session, "sections")
    if profile and any(item is profile[0] for item in sections):
        return Validation("semantic", False,
                          "Первое сечение указано ещё раз среди следующих",
                          "sections")
    if len({id(item) for item in sections}) != len(sections):
        return Validation("semantic", False,
                          "Одно и то же сечение указано дважды", "sections")
    return None


def _loft(kind: str, title: str):
    def build(session, context: BuildContext):
        return Operation(
            kind, context.unique_name(title),
            sketch=_picked_sketch(session),
            sections=_sketches_in(session, "sections"),
            ruled=bool(session.value("ruled")),
            closed=bool(session.value("closed")),
            merge=bool(session.value("merge_result", True)),
            scope=_scope_of(session),
        )
    return build


def _load_loft(session, operation, context: BuildContext) -> None:
    if operation.sketch is not None:
        put(session, "profile", [sketch_pick(operation.sketch)])
    put(session, "sections", [sketch_pick(item) for item in operation.sections
                              if item is not None])
    session.set_value("ruled", operation.ruled)
    session.set_value("closed", operation.closed)
    _load_scope(session, operation)


def _loft_descriptor(key: str, title: str, needs_body: bool) -> CommandDescriptor:
    return CommandDescriptor(
        key=key,
        title=title,
        # Без предвыбора: порядок сечений задаёт человек, а последний
        # нарисованный эскиз обычно ВЕРХНИЙ — взятый первым, он перекрутил
        # бы тело.
        preselection=(),
        needs_body=needs_body,
        groups=[
            Group("Сечения", boxes=[
                _sketch_box("profile", "Первое сечение"),
                # По порядку: тело идёт от сечения к сечению так, как их
                # указали. Повторный щелчок по эскизу снимает его.
                _sketch_box("sections", "Следующие сечения", many=True),
            ]),
            Group("Параметры", parameters=[
                Parameter("ruled", "Прямые переходы", "flag", False),
                Parameter("closed", "Замкнуть в кольцо", "flag", False),
            ]),
            _merge_group(),
        ],
        build=_loft(key, title),
        check=_check_loft,
        load=_load_loft,
    )


LOFT = _loft_descriptor("loft", "По сечениям", needs_body=False)
LOFT_CUT = _loft_descriptor("loft_cut", "Вырез по сечениям", needs_body=True)


#: Чем задана спираль: подпись → ключ протокола.
HELIX_SETS = {"Шаг и высота": "pitch-height", "Шаг и витки": "pitch-turns",
              "Высота и витки": "height-turns"}


def _helix_uses(what: str):
    def shown(session) -> bool:
        mode = HELIX_SETS.get(session.value("helix_mode"), "pitch-height")
        return what in mode
    return shown


def _helix(kind: str, title: str):
    def build(session, context: BuildContext):
        from commands import AXES as AXIS_TABLE

        return Operation(
            kind, context.unique_name(title),
            sketch=_picked_sketch(session),
            axis=AXIS_TABLE.get(session.value("axis"), AXIS_TABLE["Z"]),
            axis_origin=(0.0, 0.0, 0.0),
            helix_mode=HELIX_SETS.get(session.value("helix_mode"),
                                      "pitch-height"),
            pitch=float(session.value("pitch") or 0.0),
            height=float(session.value("height") or 0.0),
            turns=float(session.value("turns") or 0.0),
            taper=float(session.value("taper") or 0.0),
            left_handed=bool(session.value("left_handed")),
            reversed=bool(session.value("reverse")),
            merge=bool(session.value("merge_result", True)),
            scope=_scope_of(session),
        )
    return build


def _load_helix(session, operation, context: BuildContext) -> None:
    from commands import AXES as AXIS_TABLE

    if operation.sketch is not None:
        put(session, "profile", [sketch_pick(operation.sketch)])
    session.set_value("axis", _named_axis(operation.axis, AXIS_TABLE, "Z"))
    session.set_value("helix_mode", _title_of(HELIX_SETS, operation.helix_mode))
    session.set_value("pitch", operation.pitch)
    session.set_value("height", operation.height)
    session.set_value("turns", operation.turns)
    session.set_value("taper", operation.taper)
    session.set_value("left_handed", operation.left_handed)
    session.set_value("reverse", operation.reversed)
    _load_scope(session, operation)


def _helix_descriptor(key: str, title: str, needs_body: bool) -> CommandDescriptor:
    return CommandDescriptor(
        key=key,
        title=title,
        preselection=("sketch",),
        needs_body=needs_body,
        groups=[
            Group("Профиль", boxes=[_sketch_box("profile", "Профиль")]),
            Group("Ось", parameters=[
                # Ось проходит через начало координат детали, как у
                # вращения: профиль рисуют в плоскости, содержащей ось.
                Parameter("axis", "Ось", "choice", "Z", choices=AXES),
                Parameter("reverse", "Обратное направление", "flag", False),
            ]),
            Group("Спираль", parameters=[
                Parameter("helix_mode", "Задать", "choice", "Шаг и высота",
                          choices=tuple(HELIX_SETS)),
                Parameter("pitch", "Шаг", "number", 5.0, 0.01, 1e5, 3,
                          suffix=" мм", visible_when=_helix_uses("pitch")),
                Parameter("height", "Высота", "number", 20.0, 0.01, 1e5, 3,
                          suffix=" мм", visible_when=_helix_uses("height")),
                Parameter("turns", "Витков", "number", 4.0, 0.01, 1e4, 2,
                          visible_when=_helix_uses("turns")),
                Parameter("taper", "Угол конуса", "number", 0.0, -80.0, 80.0,
                          2, suffix=" °"),
                Parameter("left_handed", "Левая навивка", "flag", False),
            ]),
            _merge_group(),
        ],
        build=_helix(key, title),
        load=_load_helix,
    )


HELIX = _helix_descriptor("helix", "Спираль", needs_body=False)
HELIX_CUT = _helix_descriptor("helix_cut", "Вырез по спирали", needs_body=True)


DESCRIPTORS = (
    PAD, CUT, REVOLVE, FILLET, CHAMFER, DRAFT, SHELL, HOLE, HOLE_PATTERN,
    MIRROR, LINEAR_PATTERN, CIRCULAR_PATTERN, PLANE, HOLE_FEATURE,
    SWEEP, SWEEP_CUT, LOFT, LOFT_CUT, HELIX, HELIX_CUT,
)

BY_KEY = {descriptor.key: descriptor for descriptor in DESCRIPTORS}
