"""Решение сопряжений: где встают вхождения.

Положение выводится ПОСТРОЕНИЕМ, а не подгонкой. Сопряжение «совпадение
граней» однозначно задаёт поворот и сдвиг детали относительно соседа, и
считать его итерациями незачем — ответ выписывается сразу.

Отсюда и граница возможного, и она названа вслух. Разбор идёт ОТ
ЗАКРЕПЛЁННЫХ вхождений наружу: деталь встаёт, когда её к чему-то
прижимает уже поставленный сосед. Замкнутые цепочки — три детали, взаимно
сопряжённые по кругу, — построением не решаются: там нужен настоящий
трёхмерный решатель. Такая сборка получает `ASSEMBLY_LOOP`, а не
приблизительное положение.

Молча поставить деталь «примерно туда» нельзя. В сборке это хуже, чем в
детали: неверное положение выглядит правдоподобно, и находят его на
сборочном чертеже или на стапеле.
"""

from __future__ import annotations

import math

from .model import Diagnostic

#: Насколько направления считаются совпавшими.
SAME = 1e-9


def solve(assembly) -> list:
    """Расставить вхождения по сопряжениям. Возвращает замечания.

    Закреплённые стоят там, где стоят. Остальные разбираются волной: на
    каждом шаге ставится то, что сопряжено с уже поставленным.
    """
    found = []
    placed = {item.id for item in assembly.instances if item.fixed}
    if not placed and assembly.instances:
        # Без единого закрепления сборка висит в пустоте: сопряжения задают
        # ВЗАИМНОЕ положение, а не место в пространстве. Первое вхождение
        # закрепляется само — это то, что делают руками в любом случае, и
        # сказать об этом честнее, чем оставить сборку нерешаемой.
        assembly.instances[0].fixed = True
        placed.add(assembly.instances[0].id)
        found.append(Diagnostic(
            "ASSEMBLY_ANCHOR_ASSUMED",
            f"«{assembly.instances[0].name}» закреплена: сборке нужна точка "
            f"отсчёта, а закреплённых не было",
            severity="warning"))

    for mate in assembly.mates:
        mate.ok, mate.message = True, ""

    pending = list(assembly.mates)
    moved = True
    while pending and moved:
        moved = False
        rest = []
        for mate in pending:
            first = assembly.by_id(mate.first.instance)
            second = assembly.by_id(mate.second.instance)
            if first is None or second is None:
                mate.ok = False
                mate.message = "ASSEMBLY_LOST_INSTANCE"
                found.append(Diagnostic(
                    "ASSEMBLY_LOST_INSTANCE",
                    f"{_title(mate)}: вхождения больше нет в сборке",
                    mate=mate.id))
                continue
            anchored = first.id in placed
            floating = second.id in placed
            if anchored == floating:
                # Либо оба ещё не стоят, либо оба уже стоят. Первое —
                # подождём другой волны; второе — сопряжение либо уже
                # выполнено, либо противоречит, и это проверяется отдельно.
                rest.append(mate)
                continue
            base, target = (first, second) if anchored else (second, first)
            note = _apply(mate, base, target, base is first)
            if note is not None:
                mate.ok = False
                mate.message = note.code
                found.append(note)
                continue
            placed.add(target.id)
            moved = True
        pending = rest

    for mate in pending:
        first = assembly.by_id(mate.first.instance)
        second = assembly.by_id(mate.second.instance)
        if first is None or second is None:
            continue
        if first.id in placed and second.id in placed:
            note = _verify(mate, first, second)
            if note is not None:
                mate.ok = False
                mate.message = note.code
                found.append(note)
            continue
        mate.ok = False
        mate.message = "ASSEMBLY_LOOP"
        found.append(Diagnostic(
            "ASSEMBLY_LOOP",
            f"{_title(mate)}: положение построением не выводится — цепочка "
            f"сопряжений замкнута. Закрепите одну из деталей либо снимите "
            f"лишнее сопряжение",
            mate=mate.id))
    return found


def blocking(notes) -> list:
    return [item for item in notes if item.blocking]


# --- применение одного сопряжения ------------------------------------------


def _apply(mate, base, target, base_is_first: bool):
    """Поставить ``target`` по сопряжению с уже стоящим ``base``."""
    if mate.kind == "fixed":
        return None
    base_mark = (mate.first if base_is_first else mate.second).mark
    target_mark = (mate.second if base_is_first else mate.first).mark
    holder = _plane_of(base, base_mark)
    guest = _plane_of(target, target_mark, local=True)
    if holder is None or guest is None:
        return Diagnostic(
            "ASSEMBLY_LOST_FACE",
            f"{_title(mate)}: грань не найдена — укажите её заново",
            mate=mate.id)
    if mate.kind in ("coincident", "distance"):
        gap = mate.value_mm if mate.kind == "distance" else 0.0
        _seat(target, guest, holder, gap, mate.flip)
        return None
    if mate.kind == "concentric":
        # Соосность по описаниям граней выражается так же, как совпадение
        # осей: направления сводятся, а расстояние вдоль оси остаётся
        # свободным. Сдвиг вдоль оси при этом НЕ трогается — иначе
        # соосность молча стала бы ещё и совпадением.
        _align(target, guest, holder, mate.flip)
        return None
    return Diagnostic("ASSEMBLY_UNKNOWN_MATE",
                      f"{_title(mate)}: такого сопряжения нет",
                      mate=mate.id)


def _verify(mate, first, second):
    """Оба вхождения уже стоят: выполняется ли сопряжение."""
    if mate.kind == "fixed":
        return None
    holder = _plane_of(first, mate.first.mark)
    guest = _plane_of(second, mate.second.mark)
    if holder is None or guest is None:
        return Diagnostic(
            "ASSEMBLY_LOST_FACE",
            f"{_title(mate)}: грань не найдена — укажите её заново",
            mate=mate.id)
    along = abs(_dot(holder[1], guest[1]))
    if abs(along - 1.0) > 1e-6:
        return Diagnostic(
            "ASSEMBLY_OVERCONSTRAINED",
            f"{_title(mate)}: не выполняется — грани не параллельны. "
            f"Сопряжений больше, чем степеней свободы",
            mate=mate.id)
    if mate.kind in ("coincident", "distance"):
        gap = mate.value_mm if mate.kind == "distance" else 0.0
        between = abs(_dot(holder[1], _sub(guest[0], holder[0])))
        if abs(between - abs(gap)) > 1e-6:
            return Diagnostic(
                "ASSEMBLY_OVERCONSTRAINED",
                f"{_title(mate)}: не выполняется — между гранями "
                f"{between:.3f} мм вместо {abs(gap):.3f}",
                mate=mate.id)
    return None


def _seat(target, guest_local, holder, gap: float, flip: bool) -> None:
    """Прижать грань гостя к грани хозяина, оставив зазор ``gap``."""
    guest_centre, guest_normal = guest_local
    holder_centre, holder_normal = holder
    # Гость поворачивается так, чтобы его нормаль смотрела НАВСТРЕЧУ
    # хозяину: прижатые грани смотрят друг на друга, а не в одну сторону.
    wanted = holder_normal if flip else tuple(-v for v in holder_normal)
    basis = _turn(guest_normal, wanted)
    spun = _rotate(basis, guest_centre)
    reach = tuple(holder_centre[i] + holder_normal[i] * gap for i in range(3))
    shift = tuple(reach[i] - spun[i] for i in range(3))
    target.placement = (shift, basis)


def _align(target, guest_local, holder, flip: bool) -> None:
    """Свести направления, не трогая положение вдоль них."""
    guest_centre, guest_normal = guest_local
    holder_centre, holder_normal = holder
    wanted = holder_normal if not flip else tuple(-v for v in holder_normal)
    basis = _turn(guest_normal, wanted)
    spun = _rotate(basis, guest_centre)
    # Поперёк оси центры сводятся, вдоль — остаётся как было.
    delta = tuple(holder_centre[i] - spun[i] for i in range(3))
    along = _dot(delta, wanted)
    shift = tuple(delta[i] - wanted[i] * along for i in range(3))
    target.placement = (shift, basis)


# --- геометрия --------------------------------------------------------------


def _plane_of(instance, mark: str, local: bool = False):
    """(центр, нормаль) грани. ``local`` — в координатах самой детали."""
    found = _face_by_mark(instance.faces, mark)
    if found is None or local:
        return found
    centre, normal = found
    shift, basis = instance.placement
    turned = _rotate(basis, centre)
    return (tuple(turned[i] + shift[i] for i in range(3)),
            _rotate(basis, normal))


#: Насколько нормали считаются одинаковыми при поиске грани.
SAME_NORMAL = 1e-3


def _face_by_mark(faces, mark: str):
    """Грань по описанию «нормаль и удаление». Как у эскиза и отверстий."""
    try:
        values = [float(piece) for piece in str(mark).split(",")]
    except ValueError:
        return None
    if len(values) < 4:
        return None
    wanted, offset = tuple(values[:3]), values[3]
    best, best_gap = None, None
    for face in faces:
        normal = face.get("normal")
        centre = face.get("center")
        if not normal or not centre:
            continue
        if _dot(_unit(normal), wanted) < 1.0 - SAME_NORMAL:
            continue
        gap = abs(_dot(_unit(normal), centre) - offset)
        if best_gap is None or gap < best_gap:
            best = (tuple(float(v) for v in centre), _unit(normal))
            best_gap = gap
    return best


def _turn(source, target) -> tuple:
    """Поворот, переводящий направление ``source`` в ``target``.

    Формула Родрига. Разворот на 180° — особый случай: ось поворота из
    векторного произведения там вырождается, и брать её нельзя.
    """
    source, target = _unit(source), _unit(target)
    along = _dot(source, target)
    if along > 1.0 - SAME:
        return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    if along < -1.0 + SAME:
        axis = _across(source)
        return _spin(axis, math.pi)
    axis = _unit(_cross(source, target))
    return _spin(axis, math.acos(max(-1.0, min(1.0, along))))


def _spin(axis, angle: float) -> tuple:
    x, y, z = axis
    c, s = math.cos(angle), math.sin(angle)
    k = 1.0 - c
    return ((c + x * x * k, x * y * k - z * s, x * z * k + y * s),
            (y * x * k + z * s, c + y * y * k, y * z * k - x * s),
            (z * x * k - y * s, z * y * k + x * s, c + z * z * k))


def _rotate(basis, vector) -> tuple:
    return tuple(sum(basis[row][col] * vector[col] for col in range(3))
                 for row in range(3))


def _across(vector) -> tuple:
    axes = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    helper = min(axes, key=lambda item: abs(_dot(item, vector)))
    return _unit(_cross(vector, helper))


def _dot(a, b) -> float:
    return sum(float(a[i]) * float(b[i]) for i in range(3))


def _cross(a, b) -> tuple:
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _sub(a, b) -> tuple:
    return tuple(float(a[i]) - float(b[i]) for i in range(3))


def _unit(vector) -> tuple:
    length = math.sqrt(sum(float(v) * float(v) for v in vector))
    if length < 1e-12:
        return (0.0, 0.0, 1.0)
    return tuple(float(v) / length for v in vector)


def _title(mate) -> str:
    from .model import TITLES

    return TITLES.get(mate.kind, mate.kind)
