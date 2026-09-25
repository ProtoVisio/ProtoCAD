"""Решение сопряжений: где встают вхождения.

Разбор идёт ОТ ЗАКРЕПЛЁННЫХ вхождений наружу, волной: деталь встаёт,
когда с ней сопряжён уже поставленный сосед.

Как ставится одна деталь:

1. **Построением** — по первому её сопряжению с поставленным соседом,
   ОТ ТОГО МЕСТА, ГДЕ ДЕТАЛЬ СТОИТ: поворот — наименьший, сдвиг — только
   вдоль того, что сопряжение требует. Свободное сопряжение оставляет
   нетронутым: деталь, которую подвинули вдоль оси или по плоскости,
   остаётся там, куда её подвинули, и следующий пересчёт её не
   возвращает. Прежде построение шло от собственных осей детали и
   сбрасывало всё свободное — перетащить деталь было нельзя: пересчёт
   ставил её обратно.
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


#: Какие грани берёт каждое сопряжение.
SURFACES = {"coincident": ("plane",), "distance": ("plane",),
            "concentric": ("cylinder",),
            "angle": ("plane", "cylinder"), "parallel": ("plane", "cylinder"),
            "perpendicular": ("plane", "cylinder"),
            "tangent": ("plane", "cylinder")}
_SURFACE_NAMES = {"plane": "плоские", "cylinder": "цилиндрические"}


def solve(assembly, drag=None) -> list:
    """Расставить вхождения по сопряжениям. Возвращает замечания.

    ``drag`` — ``(id вхождения, точка в его координатах, точка сборки)``:
    деталь тянут за эту точку к указанному месту — настолько, насколько
    позволяют её сопряжения. Остальные встают по своим сопряжениям уже от
    её нового положения: то, что к ней прикреплено, едет следом.
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
            pull = drag[1:] if drag is not None and drag[0] == target.id else None
            notes = _place(target, links, pull)
            found.extend(notes)
            placed.add(target.id)
            used.update(mate.id for mate, _base, _first in links)
            moved = True

    if drag is not None and drag[0] not in placed:
        # Деталь без сопряжений с поставленными: её ничего не держит, и
        # тянется она просто сдвигом за указателем.
        target = assembly.by_id(drag[0])
        if target is not None and not target.fixed:
            rotation, shift = _pose(target)
            grab = rotation @ np.asarray(drag[1], float) + shift
            target.placement = _placement(
                rotation, shift + np.asarray(drag[2], float) - grab)

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
    wanted = SURFACES.get(mate.kind)
    if wanted is None:
        return ("ASSEMBLY_UNKNOWN_MATE", f"{_title(mate)}: такого сопряжения нет")
    kinds = []
    for reference, instance in ((mate.first, first), (mate.second, second)):
        kind = faces_module.kind_of(reference.mark)
        if kind not in wanted:
            what = " или ".join(_SURFACE_NAMES[item] for item in wanted)
            return ("ASSEMBLY_BAD_REFERENCE",
                    f"{_title(mate)}: нужны {what} грани, а у «{instance.name}» "
                    f"выбрано другое")
        if faces_module.find(instance.faces, reference.mark) is None:
            return ("ASSEMBLY_LOST_FACE",
                    f"{_title(mate)}: грань «{instance.name}» не найдена — "
                    f"укажите её заново")
        kinds.append(kind)
    if mate.kind == "tangent" and kinds == ["plane", "plane"]:
        return ("ASSEMBLY_BAD_REFERENCE",
                f"{_title(mate)}: касаются цилиндр и плоскость или два "
                f"цилиндра; две плоскости ставятся совпадением")
    return None


# --- постановка одной детали -------------------------------------------------


def _place(target, links, pull=None) -> list:
    """Поставить ``target`` по всем его сопряжениям с поставленными соседями.

    ``pull`` — ``(точка детали, точка сборки)``: тянуть деталь за точку
    туда, куда ведут мышью, в пределах того, что оставили сопряжения.
    """
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
    if pull is not None:
        rotation, shift = _pull(accepted, target, rotation, shift, *pull)
    target.placement = _placement(rotation, shift)
    return notes


def _pull(links, target, rotation, shift, grab, point):
    """Сдвинуть деталь к указателю, не нарушая её сопряжений.

    Тяга идёт наименьшими квадратами вместе с сопряжениями, с малым весом:
    деталь уходит к указателю только по тем направлениям, которые
    сопряжения оставили свободными.

    Сначала — ОДНИМ СДВИГОМ. Одной точкой за деталь поворот не задан: к
    указателю её можно и сдвинуть, и повернуть вокруг чего угодно, и
    вместе они давали бы то немного сдвига, то немного поворота. Потом —
    с поворотом, но только на то, чего сдвигом не достать: так палец на
    оси проворачивается за рычагом, а брусок на плоскости просто едет.

    В конце — доводка по одним сопряжениям: из-за малого веса тяги они
    выполнены лишь приближённо, а сдвиг доводки ничтожен. Если и после неё
    сопряжения не сходятся — деталь остаётся где была: лучше не сдвинуть,
    чем сдвинуть с нарушением.
    """
    grab = np.asarray(grab, float)
    point = np.asarray(point, float)

    def tug(turned, moved):
        return PULL_WEIGHT * (turned @ grab + moved - point)

    trial = _refine(links, target, rotation, shift, extra=tug, turn=False)
    trial = _refine(links, target, *trial, extra=tug)
    trial = _refine(links, target, *trial)
    if _satisfied(links, target, *trial):
        return trial
    return rotation, shift


#: Вес тяги мышью против невязок сопряжений: мал, чтобы сопряжения
#: перевешивали, но не настолько, чтобы потеряться в округлении.
PULL_WEIGHT = 1e-4


def _placement(rotation, shift) -> tuple:
    return (tuple(float(v) for v in shift),
            tuple(tuple(float(v) for v in row) for row in rotation))


def _satisfied(links, target, rotation, shift) -> bool:
    for mate, base, base_is_first in links:
        holder, guest = _pair(mate, base, target, base_is_first, rotation, shift)
        angular, linear = _mate_parts(mate, holder, guest)
        if (float(np.linalg.norm(angular)) > ANGLE_TOLERANCE
                or float(np.linalg.norm(linear)) > PLACE_TOLERANCE):
            return False
    return True


def _construct(mate, base, target, base_is_first):
    """Положение цели по одному сопряжению — построением ОТ ТЕКУЩЕГО.

    Сначала наименьший поворот, приводящий направления к нужным, — вокруг
    самой грани, чтобы она осталась на месте. Потом наименьший сдвиг —
    только вдоль того, что сопряжение задаёт. Всё, что оно оставляет
    свободным (сдвиг по плоскости, вдоль оси, поворот вокруг оси), остаётся
    как было.
    """
    rotation, shift = _pose(target)
    holder, guest = _pair(mate, base, target, base_is_first, rotation, shift)
    turn = _needed_turn(mate, holder, guest)
    pivot = guest.get("center") if guest.get("normal") is not None \
        else guest.get("origin")
    pivot = np.asarray(pivot, float)
    rotation = turn @ rotation
    shift = turn @ (shift - pivot) + pivot
    holder, guest = _pair(mate, base, target, base_is_first, rotation, shift)
    return rotation, shift + _needed_shift(mate, holder, guest)


def _needed_turn(mate, holder, guest) -> np.ndarray:
    """Наименьший поворот цели, после которого направления как надо."""
    kind = mate.kind
    if kind in ("coincident", "distance"):
        wanted = holder["normal"] if mate.flip else -holder["normal"]
        return _turn(guest["normal"], wanted)
    if kind == "concentric":
        wanted = -holder["axis"] if mate.flip else holder["axis"]
        return _turn(guest["axis"], wanted)
    if kind == "tangent":
        # Цилиндр с плоскостью: ось лежит вдоль плоскости. Два цилиндра:
        # оси параллельны, в какую сторону — всё равно. «Развернуть» у
        # касания выбирает сторону, а не направление.
        mixed = ("normal" in holder) != ("normal" in guest)
        rule = ("cos", 0.0) if mixed else ("any",)
    else:
        rule = _direction_rule(mate, holder, guest)
    have, want = _direction(guest), _direction(holder)
    return _turn(have, _aimed(rule, want, have))


def _needed_shift(mate, holder, guest) -> np.ndarray:
    """Наименьший сдвиг цели — только вдоль того, что сопряжение задаёт."""
    kind = mate.kind
    if kind in ("coincident", "distance"):
        gap = mate.value_mm if kind == "distance" else 0.0
        along = float(holder["normal"] @ (guest["center"] - holder["center"])) - gap
        return -holder["normal"] * along
    if kind == "concentric":
        offset = guest["origin"] - holder["origin"]
        return -(offset - holder["axis"] * float(offset @ holder["axis"]))
    if kind != "tangent":
        return np.zeros(3)
    if "normal" in holder or "normal" in guest:
        plane_is_guest = "normal" in guest
        plane, cylinder = (guest, holder) if plane_is_guest else (holder, guest)
        height = _tangent_height(mate, plane, cylinder)
        # Сдвиг гостя на v меняет высоту оси над плоскостью на +n·v, если
        # гость — цилиндр, и на −n·v, если гость — сама плоскость.
        return plane["normal"] * (height if plane_is_guest else -height)
    offset = guest["origin"] - holder["origin"]
    across = offset - holder["axis"] * float(offset @ holder["axis"])
    distance = float(np.linalg.norm(across))
    if distance < 1e-9:
        helper = np.eye(3)[int(np.argmin(np.abs(holder["axis"])))]
        across = np.cross(holder["axis"], helper)
        distance, direction = 0.0, across / np.linalg.norm(across)
    else:
        direction = across / distance
    return direction * (_tangent_reach(mate, holder, guest) - distance)


def _refine(links, target, rotation, shift, extra=None, turn: bool = True):
    """Довести положение так, чтобы выполнялись все сопряжения сразу.

    Метод Левенберга — Марквардта по шести числам: малый поворот и сдвиг.
    Начало — построенное положение; оттуда деталь сдвигается ровно
    настолько, насколько требуют остальные сопряжения.

    ``extra(поворот, сдвиг)`` — дополнительные невязки: ими мышь тянет
    деталь (`_pull`). ``turn=False`` — только сдвиг, поворот не трогается.
    """
    count = 6 if turn else 3

    def pose(params):
        if not turn:
            return rotation, shift + params
        return _rotation_vector(params[:3]) @ rotation, shift + params[3:]

    def residual(params):
        turned, moved = pose(params)
        chunks = []
        for mate, base, base_is_first in links:
            chunks.append(_mate_residual(mate, base, target, base_is_first,
                                         turned, moved))
        if extra is not None:
            chunks.append(extra(turned, moved))
        return np.concatenate(chunks)

    params = np.zeros(count)
    current = residual(params)
    cost = float(current @ current)
    damping = 1e-3
    for _ in range(100):
        if cost < 1e-24:
            break
        jacobian = np.empty((len(current), count))
        for column in range(count):
            step = np.zeros(count)
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
    return pose(params)


def _mate_residual(mate, base, target, base_is_first, rotation, shift):
    """Невязки одного сопряжения при данном положении цели — одним рядом."""
    holder, guest = _pair(mate, base, target, base_is_first, rotation, shift)
    angular, linear = _mate_parts(mate, holder, guest)
    return np.concatenate([angular, linear])


def _mate_parts(mate, holder, guest):
    """(угловые невязки, линейные невязки, мм) одного сопряжения.

    Порознь, потому что и допуски у них разные: направление сравнивается
    безразмерно, место — в миллиметрах.
    """
    kind = mate.kind
    if kind in ("coincident", "distance"):
        gap = mate.value_mm if kind == "distance" else 0.0
        wanted = holder["normal"] if mate.flip else -holder["normal"]
        along = float(holder["normal"] @ (guest["center"] - holder["center"])) - gap
        return guest["normal"] - wanted, np.array([along])
    if kind == "concentric":
        wanted = -holder["axis"] if mate.flip else holder["axis"]
        offset = guest["origin"] - holder["origin"]
        across = offset - holder["axis"] * float(offset @ holder["axis"])
        return guest["axis"] - wanted, across
    if kind == "tangent":
        if "normal" in holder or "normal" in guest:
            plane, cylinder = ((holder, guest) if "normal" in holder
                               else (guest, holder))
            tilt = np.array([float(plane["normal"] @ cylinder["axis"])])
            return tilt, np.array([_tangent_height(mate, plane, cylinder)])
        twist = np.cross(holder["axis"], guest["axis"])
        offset = guest["origin"] - holder["origin"]
        across = offset - holder["axis"] * float(offset @ holder["axis"])
        return twist, np.array([float(np.linalg.norm(across))
                                - _tangent_reach(mate, holder, guest)])
    rule = _direction_rule(mate, holder, guest)
    have, want = _direction(guest), _direction(holder)
    if rule[0] == "same":
        return have - rule[1] * want, np.zeros(0)
    if rule[0] == "any":
        return np.cross(want, have), np.zeros(0)
    return np.array([float(want @ have) - rule[1]]), np.zeros(0)


def _direction(face) -> np.ndarray:
    """Направление грани: нормаль плоскости, ось цилиндра."""
    return face["normal"] if "normal" in face else face["axis"]


def _direction_rule(mate, holder, guest) -> tuple:
    """Что требуется от направлений (нормаль плоскости, ось цилиндра).

    * ``("same", s)`` — направление гостя равно ``s``·направление хозяина;
    * ``("any",)`` — параллельны, в любую сторону;
    * ``("cos", c)`` — косинус угла между ними равен ``c``.

    Смешанная пара «плоскость — цилиндр» читается как в чертеже: ось
    ПАРАЛЛЕЛЬНА плоскости, когда она перпендикулярна её нормали, и угол
    оси с плоскостью отсчитывается от плоскости, а не от нормали.
    """
    mixed = ("normal" in holder) != ("normal" in guest)
    if mate.kind == "parallel":
        if mixed:
            return ("cos", 0.0)
        return ("same", -1.0 if mate.flip else 1.0)
    if mate.kind == "perpendicular":
        return ("any",) if mixed else ("cos", 0.0)
    theta = math.radians(float(mate.angle_deg))
    cosine = math.sin(theta) if mixed else math.cos(theta)
    if mate.flip:
        cosine = -cosine
    # Угол 0° и 180° — это параллельность. Косинус там не годится: у него
    # в этих точках нулевая производная, и доводка к ним еле ползёт.
    if abs(abs(cosine) - 1.0) < 1e-12:
        return ("same", math.copysign(1.0, cosine))
    return ("cos", cosine)


def _aimed(rule, want, have) -> np.ndarray:
    """Куда повернуть направление ``have``, чтобы выполнить правило."""
    if rule[0] == "same":
        return rule[1] * want
    if rule[0] == "any":
        return want if float(want @ have) >= 0.0 else -want
    cosine = rule[1]
    across = have - want * float(want @ have)
    length = float(np.linalg.norm(across))
    if length < 1e-9:
        helper = np.eye(3)[int(np.argmin(np.abs(want)))]
        across = np.cross(want, helper)
        length = float(np.linalg.norm(across))
    across = across / length
    return cosine * want + math.sqrt(max(0.0, 1.0 - cosine * cosine)) * across


def _tangent_height(mate, plane, cylinder) -> float:
    """Невязка касания цилиндра с плоскостью: ось должна стоять на радиус
    от плоскости — со стороны её нормали, то есть снаружи детали, а с
    «Развернуть» — с другой стороны."""
    side = -1.0 if mate.flip else 1.0
    height = float(plane["normal"] @ (cylinder["origin"] - plane["center"]))
    return height - side * float(cylinder.get("radius", 0.0))


def _tangent_reach(mate, holder, guest) -> float:
    """Расстояние между осями касающихся цилиндров: снаружи — сумма
    радиусов, с «Развернуть» (один в другом) — разность."""
    first = float(holder.get("radius", 0.0))
    second = float(guest.get("radius", 0.0))
    return abs(first - second) if mate.flip else first + second


def _residual_size(mate, first, second):
    """Насколько сопряжение не выполнено при текущих положениях обоих.

    ``None`` — выполнено. Иначе — строка для человека: угол или зазор.
    """
    rotation, shift = _pose(second)
    holder, guest = _pair(mate, first, second, True, rotation, shift)
    angular, linear = _mate_parts(mate, holder, guest)
    angle = float(np.linalg.norm(angular))
    place = float(np.linalg.norm(linear))
    if angle <= ANGLE_TOLERANCE and place <= PLACE_TOLERANCE:
        return None
    parts = []
    if angle > ANGLE_TOLERANCE:
        parts.append(f"направления расходятся на "
                     f"{_angle_error(mate, holder, guest):.3g}°")
    if place > PLACE_TOLERANCE:
        parts.append(f"расхождение {place:.4g} мм")
    return ", ".join(parts)


def _angle_error(mate, holder, guest) -> float:
    """На сколько градусов направления не такие, как требует сопряжение."""
    def between(a, b) -> float:
        return math.degrees(math.acos(max(-1.0, min(1.0, float(a @ b)))))

    kind = mate.kind
    if kind in ("coincident", "distance"):
        return between(guest["normal"],
                       holder["normal"] if mate.flip else -holder["normal"])
    if kind == "concentric":
        return between(guest["axis"],
                       -holder["axis"] if mate.flip else holder["axis"])
    if kind == "tangent":
        if "normal" in holder or "normal" in guest:
            plane, cylinder = ((holder, guest) if "normal" in holder
                               else (guest, holder))
            return abs(90.0 - between(plane["normal"], cylinder["axis"]))
        return min(between(holder["axis"], guest["axis"]),
                   between(holder["axis"], -guest["axis"]))
    rule = _direction_rule(mate, holder, guest)
    have, want = _direction(guest), _direction(holder)
    if rule[0] == "same":
        return between(have, rule[1] * want)
    if rule[0] == "any":
        return min(between(have, want), between(have, -want))
    return abs(between(have, want)
               - math.degrees(math.acos(max(-1.0, min(1.0, rule[1])))))


# --- геометрия ---------------------------------------------------------------


def _pose(instance):
    shift, basis = instance.placement
    return np.asarray(basis, float), np.asarray(shift, float)


def _world(instance, face) -> dict:
    """Описание грани в координатах сборки."""
    rotation, shift = _pose(instance)
    return _placed(face, rotation, shift)


def _placed(face, rotation, shift) -> dict:
    """Описание грани при данном положении детали.

    Плоскость — нормалью и центром, цилиндр — осью, её точкой и радиусом.
    Ось у цилиндра берётся, даже если у описания есть и нормаль: касание
    держится за ось.
    """
    result = {"surface": face.get("surface", "")}
    if face.get("axis") is not None:
        result["axis"] = rotation @ np.asarray(face["axis"], float)
        result["origin"] = rotation @ np.asarray(face["origin"], float) + shift
        result["radius"] = float(face.get("radius", 0.0))
    elif face.get("normal") is not None:
        result["normal"] = rotation @ np.asarray(face["normal"], float)
        result["center"] = rotation @ np.asarray(face["center"], float) + shift
    return result


def _pair(mate, base, target, base_is_first, rotation, shift):
    """(грань хозяина, грань цели) в координатах сборки; цель — при данном
    положении, хозяин — там, где стоит."""
    base_ref = mate.first if base_is_first else mate.second
    target_ref = mate.second if base_is_first else mate.first
    holder = _world(base, faces_module.find(base.faces, base_ref.mark))
    guest = _placed(faces_module.find(target.faces, target_ref.mark),
                    rotation, shift)
    return holder, guest


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
