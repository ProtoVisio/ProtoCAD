"""Решение сопряжений: где встают вхождения.

Разбор идёт ОТ ЗАКРЕПЛЁННЫХ вхождений наружу, волной: деталь встаёт,
когда с ней сопряжён уже поставленный сосед.

Как ставится одна деталь:

1. **Построением** — по первому её сопряжению с поставленным соседом.
   Совпадение граней однозначно задаёт поворот и сдвиг, соосность — ось;
   ответ выписывается сразу, без итераций.
2. **Уточнением** — если сопряжений с поставленными соседями у детали
   несколько. Так ставят почти всё на свете: болт в отверстие — это
   соосность И прилегание головки. Построение по одному из них оставляет
   деталь с лишней свободой, а второе проверять уже поздно. Поэтому
   положение доводится так, чтобы выполнялись ВСЕ сопряжения детали с
   поставленными соседями. Доводка начинается с построенного положения и
   двигает деталь только по тому, что первое сопряжение оставило
   свободным: если второе с первым не спорит, первое так и остаётся точным.

Чего здесь нет, названо вслух. Группа деталей, сопряжённых только между
собой и не связанных ни с одной поставленной, не ставится вовсе: ей не от
чего отсчитывать — это `ASSEMBLY_LOOP`. Сопряжения, которые нельзя
выполнить вместе, — `ASSEMBLY_OVERCONSTRAINED`, с тем, насколько они не
сходятся. Молча поставить деталь «примерно туда» нельзя: в сборке
неверное положение выглядит правдоподобно, и находят его на стапеле.
"""

from __future__ import annotations

import math

import numpy as np

from . import faces as faces_module
from .model import Diagnostic

#: Насколько направления считаются совпавшими.
SAME = 1e-9
#: Сколько невязки прощается после доводки: угол (радианы) и место (мм).
ANGLE_TOLERANCE = 1e-7
PLACE_TOLERANCE = 1e-6


def solve(assembly) -> list:
    """Расставить вхождения по сопряжениям. Возвращает замечания."""
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

    usable = []
    for mate in assembly.mates:
        mate.ok, mate.message = True, ""
        first = assembly.by_id(mate.first.instance)
        second = assembly.by_id(mate.second.instance)
        if first is None or second is None:
            _refuse(mate, found, "ASSEMBLY_LOST_INSTANCE",
                    f"{_title(mate)}: вхождения больше нет в сборке")
            continue
        if mate.kind == "fixed":
            # «Закрепление» между двумя вхождениями держит второе там, где
            # оно стоит, относительно уже поставленного первого.
            usable.append(mate)
            continue
        note = _check_references(mate, first, second)
        if note is not None:
            _refuse(mate, found, note[0], note[1])
            continue
        usable.append(mate)

    used = set()
    moved = True
    while moved:
        moved = False
        for target in assembly.instances:
            if target.id in placed:
                continue
            links = []
            for mate in usable:
                if mate.first.instance == target.id and mate.second.instance in placed:
                    links.append((mate, assembly.by_id(mate.second.instance), False))
                elif mate.second.instance == target.id and mate.first.instance in placed:
                    links.append((mate, assembly.by_id(mate.first.instance), True))
            if not links:
                continue
            notes = _place(target, links)
            found.extend(notes)
            placed.add(target.id)
            used.update(mate.id for mate, _base, _first in links)
            moved = True

    for mate in usable:
        if mate.id in used:
            continue
        first = assembly.by_id(mate.first.instance)
        second = assembly.by_id(mate.second.instance)
        if first.id in placed and second.id in placed:
            if mate.kind == "fixed":
                continue
            gap = _residual_size(mate, first, second)
            if gap is not None:
                _refuse(mate, found, "ASSEMBLY_OVERCONSTRAINED",
                        f"{_title(mate)}: не выполняется — {gap}. "
                        f"Сопряжений больше, чем степеней свободы")
            continue
        _refuse(mate, found, "ASSEMBLY_LOOP",
                f"{_title(mate)}: положение не выводится — детали не связаны "
                f"ни с одной закреплённой. Закрепите одну из них либо "
                f"сопрягите с уже поставленной")
    return found


def blocking(notes) -> list:
    return [item for item in notes if item.blocking]


def _refuse(mate, found, code: str, message: str) -> None:
    mate.ok = False
    mate.message = code
    found.append(Diagnostic(code, message, mate=mate.id))


def _check_references(mate, first, second):
    """Грани на месте и того вида, который нужен сопряжению."""
    wanted = {"coincident": "plane", "distance": "plane",
              "concentric": "cylinder"}.get(mate.kind)
    if wanted is None:
        return ("ASSEMBLY_UNKNOWN_MATE", f"{_title(mate)}: такого сопряжения нет")
    for reference, instance in ((mate.first, first), (mate.second, second)):
        if faces_module.kind_of(reference.mark) != wanted:
            what = "плоские грани" if wanted == "plane" else "цилиндрические грани"
            return ("ASSEMBLY_BAD_REFERENCE",
                    f"{_title(mate)}: нужны {what}, а у «{instance.name}» "
                    f"выбрано другое")
        if faces_module.find(instance.faces, reference.mark) is None:
            return ("ASSEMBLY_LOST_FACE",
                    f"{_title(mate)}: грань «{instance.name}» не найдена — "
                    f"укажите её заново")
    return None


# --- постановка одной детали -------------------------------------------------


def _place(target, links) -> list:
    """Поставить ``target`` по всем его сопряжениям с поставленными соседями."""
    moving = [link for link in links if link[0].kind != "fixed"]
    if not moving:
        # Только «закрепления»: деталь остаётся там, где стоит.
        return []
    mate, base, base_is_first = moving[0]
    rotation, shift = _construct(mate, base, target, base_is_first)
    accepted = [moving[0]]
    notes = []
    # Остальные — по одному: сопряжение, которое спорит с уже принятыми,
    # в отказ, а принятые остаются точными. Доводка по всем сразу нашла бы
    # середину и нарушила бы оба — так не видно, какое из них лишнее.
    for link in moving[1:]:
        trial = _refine(accepted + [link], target, rotation, shift)
        if _satisfied(accepted + [link], target, *trial):
            rotation, shift = trial
            accepted.append(link)
            continue
        mate = link[0]
        target.placement = _placement(*trial)
        base = link[1]
        first, second = (base, target) if link[2] else (target, base)
        gap = _residual_size(mate, first, second) or "не сходится"
        _refuse(mate, notes, "ASSEMBLY_OVERCONSTRAINED",
                f"{_title(mate)}: с остальными сопряжениями «{target.name}» "
                f"не выполняется — {gap}. Сопряжений больше, чем степеней "
                f"свободы")
    target.placement = _placement(rotation, shift)
    return notes


def _placement(rotation, shift) -> tuple:
    return (tuple(float(v) for v in shift),
            tuple(tuple(float(v) for v in row) for row in rotation))


def _satisfied(links, target, rotation, shift) -> bool:
    for mate, base, base_is_first in links:
        values = _mate_residual(mate, base, target, base_is_first, rotation, shift)
        if (float(np.linalg.norm(values[:3])) > ANGLE_TOLERANCE
                or float(np.linalg.norm(values[3:])) > PLACE_TOLERANCE):
            return False
    return True


def _construct(mate, base, target, base_is_first):
    """Положение цели по одному сопряжению — построением."""
    base_ref = mate.first if base_is_first else mate.second
    target_ref = mate.second if base_is_first else mate.first
    holder = _world(base, faces_module.find(base.faces, base_ref.mark))
    guest = faces_module.find(target.faces, target_ref.mark)
    if mate.kind in ("coincident", "distance"):
        gap = mate.value_mm if mate.kind == "distance" else 0.0
        wanted = holder["normal"] if mate.flip else -holder["normal"]
        rotation = _turn(np.asarray(guest["normal"], float), wanted)
        spun = rotation @ np.asarray(guest["center"], float)
        reach = holder["center"] + holder["normal"] * gap
        return rotation, reach - spun
    # Соосность: ось к оси, точка оси гостя — на ось хозяина. Вдоль оси и
    # вокруг неё деталь остаётся свободной — это дело других сопряжений.
    wanted = -holder["axis"] if mate.flip else holder["axis"]
    rotation = _turn(np.asarray(guest["axis"], float), wanted)
    spun = rotation @ np.asarray(guest["origin"], float)
    delta = holder["origin"] - spun
    shift = delta - wanted * float(delta @ wanted)
    return rotation, shift


def _refine(links, target, rotation, shift):
    """Довести положение так, чтобы выполнялись все сопряжения сразу.

    Метод Левенберга — Марквардта по шести числам: малый поворот и сдвиг.
    Начало — построенное положение; оттуда деталь сдвигается ровно
    настолько, насколько требуют остальные сопряжения.
    """
    def residual(params):
        turned = _rotation_vector(params[:3]) @ rotation
        moved = shift + params[3:]
        chunks = []
        for mate, base, base_is_first in links:
            chunks.append(_mate_residual(mate, base, target, base_is_first,
                                         turned, moved))
        return np.concatenate(chunks)

    params = np.zeros(6)
    current = residual(params)
    cost = float(current @ current)
    damping = 1e-3
    for _ in range(100):
        if cost < 1e-24:
            break
        jacobian = np.empty((len(current), 6))
        for column in range(6):
            step = np.zeros(6)
            step[column] = 1e-7
            jacobian[:, column] = (residual(params + step) - current) / 1e-7
        normal = jacobian.T @ jacobian
        gradient = jacobian.T @ current
        improved = False
        for _attempt in range(12):
            try:
                delta = np.linalg.solve(
                    normal + damping * np.diag(np.diag(normal) + 1e-12), -gradient)
            except np.linalg.LinAlgError:
                damping *= 10.0
                continue
            candidate = residual(params + delta)
            candidate_cost = float(candidate @ candidate)
            if candidate_cost < cost:
                params = params + delta
                current, cost = candidate, candidate_cost
                damping = max(damping / 3.0, 1e-12)
                improved = True
                break
            damping *= 5.0
        if not improved:
            break
    return _rotation_vector(params[:3]) @ rotation, shift + params[3:]


def _mate_residual(mate, base, target, base_is_first, rotation, shift):
    """Невязки одного сопряжения при данном положении цели."""
    base_ref = mate.first if base_is_first else mate.second
    target_ref = mate.second if base_is_first else mate.first
    holder = _world(base, faces_module.find(base.faces, base_ref.mark))
    guest = faces_module.find(target.faces, target_ref.mark)
    if mate.kind in ("coincident", "distance"):
        gap = mate.value_mm if mate.kind == "distance" else 0.0
        normal = rotation @ np.asarray(guest["normal"], float)
        centre = rotation @ np.asarray(guest["center"], float) + shift
        wanted = holder["normal"] if mate.flip else -holder["normal"]
        along = float(holder["normal"] @ (centre - holder["center"])) - gap
        return np.concatenate([normal - wanted, [along]])
    axis = rotation @ np.asarray(guest["axis"], float)
    point = rotation @ np.asarray(guest["origin"], float) + shift
    wanted = -holder["axis"] if mate.flip else holder["axis"]
    offset = point - holder["origin"]
    across = offset - holder["axis"] * float(offset @ holder["axis"])
    return np.concatenate([axis - wanted, across])


def _residual_size(mate, first, second):
    """Насколько сопряжение не выполнено при текущих положениях обоих.

    ``None`` — выполнено. Иначе — строка для человека: угол или зазор.
    """
    rotation, shift = _pose(second)
    values = _mate_residual(mate, first, second, True, rotation, shift)
    angle = float(np.linalg.norm(values[:3]))
    place = float(np.linalg.norm(values[3:]))
    if angle <= ANGLE_TOLERANCE and place <= PLACE_TOLERANCE:
        return None
    parts = []
    if angle > ANGLE_TOLERANCE:
        degrees = math.degrees(2.0 * math.asin(min(1.0, angle / 2.0)))
        parts.append(f"направления расходятся на {degrees:.3g}°")
    if place > PLACE_TOLERANCE:
        parts.append(f"расхождение {place:.4g} мм")
    return ", ".join(parts)


# --- геометрия ---------------------------------------------------------------


def _pose(instance):
    shift, basis = instance.placement
    return np.asarray(basis, float), np.asarray(shift, float)


def _world(instance, face) -> dict:
    """Описание грани в координатах сборки."""
    rotation, shift = _pose(instance)
    result = {}
    if face.get("normal") is not None:
        result["normal"] = rotation @ np.asarray(face["normal"], float)
        result["center"] = rotation @ np.asarray(face["center"], float) + shift
    if face.get("axis") is not None:
        result["axis"] = rotation @ np.asarray(face["axis"], float)
        result["origin"] = rotation @ np.asarray(face["origin"], float) + shift
    return result


def _turn(source, target) -> np.ndarray:
    """Поворот, переводящий направление ``source`` в ``target``.

    Формула Родрига. Разворот на 180° — особый случай: ось поворота из
    векторного произведения там вырождается, и брать её нельзя.
    """
    source = source / np.linalg.norm(source)
    target = np.asarray(target, float) / np.linalg.norm(target)
    along = float(source @ target)
    if along > 1.0 - SAME:
        return np.eye(3)
    if along < -1.0 + SAME:
        helper = np.eye(3)[int(np.argmin(np.abs(source)))]
        axis = np.cross(source, helper)
        return _spin(axis / np.linalg.norm(axis), math.pi)
    axis = np.cross(source, target)
    return _spin(axis / np.linalg.norm(axis),
                 math.acos(max(-1.0, min(1.0, along))))


def _spin(axis, angle: float) -> np.ndarray:
    x, y, z = axis
    c, s = math.cos(angle), math.sin(angle)
    k = 1.0 - c
    return np.array([[c + x * x * k, x * y * k - z * s, x * z * k + y * s],
                     [y * x * k + z * s, c + y * y * k, y * z * k - x * s],
                     [z * x * k - y * s, z * y * k + x * s, c + z * z * k]])


def _rotation_vector(vector) -> np.ndarray:
    angle = float(np.linalg.norm(vector))
    if angle < 1e-15:
        return np.eye(3)
    return _spin(np.asarray(vector) / angle, angle)


def _title(mate) -> str:
    from .model import TITLES

    return TITLES.get(mate.kind, mate.kind)
