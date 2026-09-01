"""Операции по профилю: общий слой для прилива, выреза и вращения.

Устройство повторяет разделение, принятое в FreeCAD PartDesign
(`FeatureSketchBased` → `FeatureExtrude` → `FeaturePad`/`FeaturePocket`):
один слой разбирает профиль, направление и концевое условие, второй
решает, прибавить материал или снять. Это **портирование подхода**, а не
исходного кода: строки и структура FreeCAD не копировались, упомянут как
технический референс по `docs/05_POLICY.md`, класс B.

Почему так, а не по операции на каждый случай. Раньше прилив и вырез были
двумя расходящимися путями: у выреза был собственный эскиз, он не смотрел
на профиль, пришедший сверху, и на выбранные области; направление каждый
искал по-своему. Расхождение двух путей и есть то место, где заводятся
необъяснимые результаты — один путь чинишь, второй остаётся.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import kernel

#: Концевые условия. Порядок и смысл — как в отраслевой практике.
BLIND = "blind"            # на заданное расстояние
THROUGH = "through"        # насквозь через всё тело
TO_FACE = "to_face"        # до указанной грани
TO_FIRST = "to_first"      # до первой встреченной грани детали
TO_LAST = "to_last"        # до последней встреченной грани детали
MIDPLANE = "midplane"      # симметрично от плоскости эскиза

#: Все концевые условия с названиями для панели.
END_CONDITIONS = {
    BLIND: "На расстояние",
    THROUGH: "Насквозь",
    TO_FIRST: "До первой грани",
    TO_LAST: "До последней грани",
    TO_FACE: "До указанной грани",
    MIDPLANE: "Симметрично",
}

#: Что делать с построенным инструментом.
CREATE = "create"          # первое тело
ADD = "add"                # прибавить к детали
CUT = "cut"                # снять с детали


class OperationError(RuntimeError):
    """Операция не может дать осмысленного результата."""


@dataclass
class Extrusion:
    """Параметры выдавливания профиля.

    Отделены от операции дерева намеренно: те же параметры нужны
    предпросмотру, который ничего в дерево не пишет.
    """

    length: float = 10.0
    end: str = BLIND
    reverse: bool = False
    #: Куда выдавливать. ``None`` — по нормали профиля.
    direction: tuple | None = None
    #: Грань, до которой доводить при ``TO_FACE``.
    until: object = None

    def vector(self, profile) -> tuple:
        """Направление и длина одним вектором.

        Направление берётся у САМОГО ПРОФИЛЯ, а не задаётся глобальной Z:
        профиль знает, в какой плоскости лежит, и выдавливание обязано
        идти по её нормали — иначе эскиз на вертикальной плоскости даёт
        тело, растущее вбок от собственного контура.
        """
        axis = self.direction or _normal_of(profile)
        length = self.length
        if self.reverse:
            axis = tuple(-value for value in axis)
        return tuple(value * length for value in axis)


def build_tool(profile, extrusion: Extrusion, target=None):
    """Инструмент операции — призма по профилю.

    ``target`` нужен концевым условиям, которые смотрят на деталь:
    «насквозь» меряется по её габариту, «до грани» — по положению грани.
    """
    from OCP.BRepPrimAPI import BRepPrimAPI_MakePrism
    from OCP.gp import gp_Vec

    face = _as_face(profile)
    if face is None:
        raise OperationError("нет профиля: эскиз пуст либо уже израсходован")

    axis = extrusion.direction or _normal_of(face)
    if extrusion.reverse:
        axis = tuple(-value for value in axis)

    if extrusion.end == THROUGH:
        length = _through_length(target, face)
    elif extrusion.end in (TO_FIRST, TO_LAST, TO_FACE):
        length = _up_to_length(target, face, axis, extrusion)
    else:
        length = extrusion.length
    if length <= 0.0:
        raise OperationError("длина выдавливания должна быть больше нуля")

    if extrusion.end == MIDPLANE:
        # От средней плоскости: профиль отодвигается на половину назад и
        # оттуда выдавливается на всю длину. Это НЕ то же, что удвоенная
        # длина в одну сторону.
        face = kernel.translated(
            face, tuple(-value * length / 2.0 for value in axis))
    if extrusion.end == THROUGH:
        # Насквозь начинается чуть ДО профиля: иначе грань инструмента
        # ложится ровно на грань детали, и булева операция получает
        # касание вместо пересечения — самый частый источник «операция
        # прошла, а дырки нет».
        back = max(length * 0.01, 1e-3)
        face = kernel.translated(face, tuple(-value * back for value in axis))
        length += 2.0 * back

    return BRepPrimAPI_MakePrism(
        face, gp_Vec(*(value * length for value in axis))
    ).Shape()


def apply(shape, tool, mode: str, title: str,
          require_change: bool = True) -> tuple:
    """Приложить инструмент к детали. Возвращает (форма, замечание).

    Проверка результата здесь ОБЯЗАТЕЛЬНА, а не желательна. Булева
    операция умеет вернуть форму, которая строится и рисуется, но телом не
    является; поймать это надо там, где оно возникло, а не тремя
    операциями позже.
    """
    if mode == CREATE or shape is None:
        result = tool
    elif mode == ADD:
        result = kernel.fuse(shape, tool)
    elif mode == CUT:
        result = kernel.cut(shape, tool)
    else:
        raise OperationError(f"неизвестный режим операции: {mode!r}")

    if kernel.is_empty(result):
        # Отдельное сообщение: «операция сняла весь материал» и «получилась
        # не деталь» — разные беды, и чинят их по-разному.
        raise OperationError(
            f"{title}: от детали ничего не осталось — "
            f"операция сняла весь материал"
            if mode == CUT else f"{title}: операция не дала тела"
        )
    problems = kernel.validate(result)
    if problems:
        raise OperationError(f"{title}: результат не является телом — "
                             + "; ".join(problems))

    note = ""
    # Многотельность проверяется при ЛЮБОМ режиме. Раньше — только при
    # добавлении к телу, и первая же операция, создавшая два несвязанных
    # тела, проходила молча: узнать об этом было неоткуда до операции,
    # которая на этом спотыкалась.
    bodies = kernel.solid_count(result)
    if bodies > 1:
        note = (f"{title}: получилось тел: {bodies} — "
                f"они не соединены между собой")

    if require_change and shape is not None and mode in (ADD, CUT):
        before = kernel.volume(shape)
        after = kernel.volume(result)
        if mode == ADD and after - before <= _MINIMUM:
            raise OperationError(
                f"{title}: ничего не добавлено — инструмент целиком внутри "
                f"детали. Проверьте плоскость эскиза, направление и длину"
            )
        if mode == CUT and before - after <= _MINIMUM:
            raise OperationError(
                f"{title}: ничего не снято — инструмент не задевает деталь. "
                f"Проверьте плоскость эскиза, направление и длину"
            )
    return result, note


def choose_direction(shape, profile, extrusion: Extrusion, mode: str) -> Extrusion:
    """Выбрать сторону, с которой операция вообще что-то делает.

    Нормаль плоскости эскиза не всегда смотрит туда, куда человек имел в
    виду: грань, снятая с тела после булевой операции, может быть
    ориентирована внутрь. Вместо того чтобы отказать, пробуем обе стороны
    и берём ту, где есть материал, — так же, как это давно сделано у
    выреза.

    Отказ остаётся, если не работает ни одна: это уже не про сторону.
    """
    if shape is None or mode == CREATE:
        return extrusion
    from dataclasses import replace

    best, best_gain = None, _MINIMUM
    for reverse in (extrusion.reverse, not extrusion.reverse):
        candidate = replace(extrusion, reverse=reverse)
        try:
            tool = build_tool(profile, candidate, shape)
            joined = (kernel.fuse(shape, tool) if mode == ADD
                      else kernel.cut(shape, tool))
            gain = abs(kernel.volume(joined) - kernel.volume(shape))
        except Exception:  # noqa: BLE001 — сторона может не строиться вовсе
            continue
        if gain > best_gain:
            best, best_gain = candidate, gain
    # Сравниваются ОБЕ стороны целиком, а не берётся первая, которая
    # что-нибудь делает. «Насквозь» начинается чуть до профиля, поэтому и
    # неверная сторона срезает тонкий слой; приняв её, вырез уходил на
    # три миллиметра вместо всей толщины — и выглядел просто мелким.
    return best or extrusion


# --- вспомогательное -------------------------------------------------------

#: Насколько операция должна изменить объём, чтобы считаться состоявшейся.
#: Не ноль: булева операция оставляет крохи на стыке граней.
_MINIMUM = 1e-6


def _as_face(profile):
    """Профиль как грань или составной объект из граней."""
    if profile is None or profile.IsNull():
        return None
    return profile


def _normal_of(profile) -> tuple:
    """Нормаль профиля. У составного берётся у первой грани: все они лежат
    в одной плоскости эскиза, и различаться нормали не могут."""
    for face in kernel.iter_faces(profile):
        try:
            return kernel.face_normal(face)
        except Exception:  # noqa: BLE001 — неплоская грань в профиле
            continue
    raise OperationError("у профиля нет плоской грани — направление неизвестно")


def _through_length(target, profile) -> float:
    """Длина «насквозь». Считает перенесённый код FreeCAD.

    Своя прикидка «диагональ габарита на два» была близка, но не учитывала
    габарит самого профиля: эскиз на плоскости, отодвинутой от детали,
    оставался недобитым.
    """
    from ..upstream_ports.freecad import extrusion_core

    try:
        return extrusion_core.through_all_length(target, profile)
    except extrusion_core.UpToError as error:
        raise OperationError(str(error)) from error


def _up_to_length(target, profile, axis, extrusion) -> float:
    """Длина до грани: указанной, первой встреченной или последней.

    Поиск грани и все проверки — перенесённый код FreeCAD
    (`Part::findAllFacesCutBy`, `ProfileBased::getUpToFace`). Своей
    реализации здесь нет намеренно: пускать луч из центра профиля и
    разбирать, какое пересечение считать «первым», — как раз тот
    алгоритм, который проще взять готовым, чем переоткрыть.
    """
    from ..upstream_ports.freecad import extrusion_core

    if target is None:
        raise OperationError("детали ещё нет — доводить не до чего")
    try:
        if extrusion.end == TO_FACE:
            if extrusion.until is None:
                raise OperationError("не указана грань, до которой выдавливать")
            face = extrusion.until
            extrusion_core.check_up_to_face(face, profile, axis)
        else:
            face = extrusion_core.up_to_face(
                target, profile, axis,
                "first" if extrusion.end == TO_FIRST else "last")
    except extrusion_core.UpToError as error:
        raise OperationError(str(error)) from error

    start = kernel.face_center(next(iter(kernel.iter_faces(profile))))
    finish = kernel.face_center(face)
    along = sum((finish[i] - start[i]) * axis[i] for i in range(3))
    if along <= 0.0:
        raise OperationError(
            "грань лежит с другой стороны — выдавливать до неё нечем")
    # Небольшой запас: инструмент, кончающийся ровно на грани, даёт ядру
    # касание вместо пересечения.
    return along + max(along * 0.001, 1e-4)
