"""Первый разбор прибора: что плата, что корпус, что крепёж.

Всё здесь — ПРЕДЛОЖЕНИЕ, а не решение: у каждой единицы записано, почему
она так названа, и окно показывает разбор человеку до того, как с ним
работать. Ошибиться в разборе легко — модели приходят из разных систем, —
а незамеченная ошибка разбора всплывёт только в результатах расчёта.

Порядок проверок — от надёжного к догадкам:

1. **Платы** — узел, в котором есть тонкая пластина и на ней мелкие детали,
   обычно с позиционными обозначениями. Пластина — основание, детали на
   ней — компоненты.
2. **Крепёж** — по имени (винт, гайка, шайба, ГОСТ 17473, ISO 4762, DIN 912…),
   а без имени — по форме: тело вращения с головкой, шестигранник с
   отверстием, тонкое кольцо.
3. **Прочее** — втулки и стойки (по форме и имени), разъёмы вне плат,
   провода, прокладки.
4. **Корпус** — по имени (корпус, крышка, основание, радиатор…) или по
   размеру: крупные детали.
"""

from __future__ import annotations

import re

import numpy as np

from ..model import Assembly
from . import designators as designators_module
from .model import (COMPONENT, FASTENER, HOUSING, OTHER, SUBSTRATE, Board, Device,
                    key_of)

# --- имена ----------------------------------------------------------------------

_FASTENER_WORDS = (
    (r"винт|саморез|screw", "винт"),
    (r"болт|bolt", "болт"),
    (r"гайк|\bnut\b|\bnuts\b", "гайка"),
    (r"шайб|washer", "шайба"),
    (r"шпильк|\bstud\b", "шпилька"),
    (r"заклёпк|заклепк|rivet", "заклёпка"),
    (r"штифт|dowel", "штифт"),
)

#: Номера стандартов крепежа — у изделий из библиотек имя часто только из
#: номера стандарта и размера.
_STANDARDS = {
    "ГОСТ 1491": "винт", "ГОСТ 11738": "винт", "ГОСТ 17473": "винт",
    "ГОСТ 17474": "винт", "ГОСТ 17475": "винт", "ГОСТ 10619": "винт",
    "ГОСТ 10621": "винт", "ГОСТ 11644": "винт", "ГОСТ 11650": "винт",
    "ГОСТ 7798": "болт", "ГОСТ 7805": "болт", "ГОСТ 7808": "болт",
    "ГОСТ 5915": "гайка", "ГОСТ 5927": "гайка", "ГОСТ 15521": "гайка",
    "ГОСТ 11371": "шайба", "ГОСТ 6402": "шайба", "ГОСТ 10450": "шайба",
    "ГОСТ 6958": "шайба", "ГОСТ 10463": "шайба",
    "ISO 4762": "винт", "ISO 7045": "винт", "ISO 7046": "винт", "ISO 7380": "винт",
    "ISO 10642": "винт", "ISO 1207": "винт", "ISO 14580": "винт", "ISO 14583": "винт",
    "ISO 4014": "болт", "ISO 4017": "болт",
    "ISO 4032": "гайка", "ISO 4035": "гайка", "ISO 10511": "гайка",
    "ISO 7089": "шайба", "ISO 7090": "шайба", "ISO 7091": "шайба", "ISO 7092": "шайба",
    "DIN 84": "винт", "DIN 912": "винт", "DIN 963": "винт", "DIN 965": "винт",
    "DIN 7985": "винт", "DIN 7991": "винт",
    "DIN 931": "болт", "DIN 933": "болт",
    "DIN 934": "гайка", "DIN 985": "гайка", "DIN 439": "гайка",
    "DIN 125": "шайба", "DIN 127": "шайба", "DIN 433": "шайба", "DIN 6798": "шайба",
    "DIN 9021": "шайба",
}
_STANDARD = re.compile(r"(ГОСТ|GOST|ISO|DIN)\s*[-_ ]?\s*(\d{2,5})", re.IGNORECASE)
#: Размер: «М3х8», «M3 x 8», «М3-6gx8» (с полем допуска резьбы, как в
#: обозначениях по ГОСТ).
_SIZE = re.compile(r"(?<![A-ZА-Я0-9])[MМ]\s?(\d{1,2}(?:[.,]\d+)?)"
                   r"(?:-\d[a-hA-H](?:\d[a-hA-H])?)?"
                   r"(?:\s?[xх×*]\s?(\d{1,3}))?", re.IGNORECASE)

_HOUSING_WORDS = re.compile(
    r"корпус|крышк|основани|кожух|панел|\bрам[аы]?\b|стенк|днищ|\bдно\b|радиатор|"
    r"кронштейн|экран|поддон|housing|enclosure|chassis|cover|\blid\b|\bbase\b|"
    r"frame|bracket|heat\s?sink|panel|shield", re.IGNORECASE)

_OTHER_WORDS = (
    (r"втулк|стойк|standoff|spacer|bushing|дистанц", "втулка"),
    (r"разъ[её]м|соединител|connector|вилк|розетк|\bснп|\bонц|d-?sub", "разъём"),
    (r"провод|кабел|жгут|\bwire|cable|harness", "провод"),
    (r"прокладк|gasket|термоинтерфейс|thermal\s?pad", "прокладка"),
    (r"шильд|табличк|\blabel", "шильдик"),
)

_BOARD_WORDS = re.compile(r"плата|\bпп\b|pcb|board", re.IGNORECASE)


def fastener_name(name: str):
    """(вид, пояснение) крепежа по имени или ``None``."""
    text = str(name or "")
    standard = _STANDARD.search(text)
    if standard:
        key = f"{'ГОСТ' if standard[1].upper() in ('ГОСТ', 'GOST') else standard[1].upper()} {standard[2]}"
        if key in _STANDARDS:
            return _STANDARDS[key], f"стандарт {key}"
    lowered = text.lower()
    for pattern, kind in _FASTENER_WORDS:
        if re.search(pattern, lowered):
            return kind, f"в имени «{text}»"
    return None


def fastener_size(name: str) -> str:
    """Размер крепежа из имени: «Винт М3х8» → «М3×8». Пусто — не нашёлся."""
    found = _SIZE.search(str(name or ""))
    if not found:
        return ""
    size = f"М{found[1].replace('.', ',')}"
    # Длина — целое: «.58» после неё в «М3-6gx6.58» — класс прочности 5.8,
    # а «M4x0.7» — шаг резьбы, не длина.
    if found[2] and int(found[2]) >= 1:
        size += f"×{found[2]}"
    return size


def housing_name(name: str):
    found = _HOUSING_WORDS.search(str(name or ""))
    return f"в имени «{name}»" if found else None


def _first(test, *names):
    """Первое срабатывание проверки по списку имён — пояснение называет то
    имя, по которому узнали, а не склейку всех."""
    for name in names:
        found = test(name)
        if found:
            return found
    return None


def other_name(name: str):
    lowered = str(name or "").lower()
    for pattern, kind in _OTHER_WORDS:
        if re.search(pattern, lowered):
            return kind, f"в имени «{name}»"
    return None


# --- форма ----------------------------------------------------------------------

#: Крепёж и втулки — мелочь: диаметр до 30 мм.
FASTENER_DIAMETER = 30.0


def fastener_shape(facts):
    """(роль, вид, пояснение) по форме или ``None``."""
    revolution = facts.revolution
    if facts.hex_flats and facts.hex_flats <= FASTENER_DIAMETER:
        length = float(facts.size[-1]) if facts.hex_axis is None else _along(facts)
        if facts.hex_hole and length <= 1.3 * facts.hex_flats:
            return FASTENER, "гайка", f"шестигранник S{facts.hex_flats:.3g} с отверстием"
        if facts.hex_hole:
            return OTHER, "втулка", f"шестигранная стойка S{facts.hex_flats:.3g}"
        if revolution is not None and not revolution["hole"] \
                and revolution["length"] > 1.5 * min(revolution["radii"] or [1e9]) * 2:
            return FASTENER, "болт", f"стержень с шестигранной головкой S{facts.hex_flats:.3g}"
    if revolution is None or revolution["fraction"] < 0.75:
        return None
    diameter, length = revolution["diameter"], revolution["length"]
    if diameter > FASTENER_DIAMETER:
        return None
    if revolution["hole"]:
        if length <= 0.35 * diameter:
            return FASTENER, "шайба", f"кольцо Ø{diameter:.3g}×{length:.2g}"
        return OTHER, "втулка", f"втулка Ø{diameter:.3g}×{length:.3g}"
    radii = revolution["radii"]
    if len(radii) >= 2 and length >= 2.0 * radii[0] * 1.2:
        return FASTENER, "винт", (f"стержень Ø{2 * radii[0]:.3g} с головкой "
                                  f"Ø{2 * radii[-1]:.3g}, длина {length:.3g}")
    return None


def _along(facts) -> float:
    axis = np.asarray(facts.hex_axis, float)
    extent = np.abs(facts.axes @ axis) @ facts.half
    return float(2.0 * extent)


# --- платы ----------------------------------------------------------------------

#: Компонент стоит на плате, если зазор между ним и пластиной по нормали не
#: больше этого (приподнятые корпуса, выводы в отверстиях — дают ноль).
ON_BOARD_GAP = 2.0


def find_boards(device: Device) -> list:
    """Узлы-платы: [(ключ узла, имя, ключ основания, пояснение)]."""
    found = []

    def visit(item, path, label):
        if not isinstance(item, Assembly):
            return
        verdict = _board_verdict(device, item, path, label)
        if verdict is not None:
            found.append(verdict)
        for occurrence in item.placements:
            if isinstance(occurrence.item, Assembly) and not _component_node(occurrence):
                visit(occurrence.item, path + (occurrence.stable_id,), occurrence.label)

    visit(device.root, (), device.root.label)
    return found


def _component_node(occurrence) -> bool:
    """Узел — компонент (корпус с выводами), а не плата: у него обозначение
    не «A» и внутри только тела. Разбирать такие на платы незачем — на
    плате в тысячу компонентов это тысяча лишних разборов формы."""
    designator = designators_module.parse(occurrence.reference)
    if designator is None or designator.prefix == "A":
        return False
    return not any(isinstance(inner.item, Assembly) for inner in occurrence.item.placements)


def _board_verdict(device, node, path, label):
    """Плата ли узел. Пластина — крупнейшая тонкая деталь среди детей;
    компоненты — мелкие дети, стоящие на ней."""
    plates = []
    for occurrence in node.placements:
        if isinstance(occurrence.item, Assembly) or occurrence.item.shape is None:
            continue
        # Крышка и панель корпуса — тоже тонкие пластины, но не платы.
        if housing_name(occurrence.label) or housing_name(occurrence.item.name):
            continue
        facts = device.facts(occurrence.item)
        if facts.plate is not None:
            plates.append((facts.plate["length"] * facts.plate["width"], occurrence, facts))
    if not plates:
        return None
    _area, plate, plate_facts = max(plates, key=lambda entry: entry[0])
    normal = plate.transform[:3, :3] @ np.asarray(plate_facts.plate["normal"])
    plate_box = _box_in_node(plate_facts, plate.transform)
    on_board = with_designator = 0
    for occurrence in node.placements:
        if occurrence is plate:
            continue
        box = _child_box(device, occurrence)
        if box is None or not _small(box, plate_facts):
            continue
        if not _on_plate(box, plate_box, normal):
            continue
        # Винты и шайбы на пластине есть у любой крышки: платой её
        # делают компоненты, а не крепёж.
        if fastener_name(f"{occurrence.label} {occurrence.item.name}"):
            continue
        on_board += 1
        if designators_module.parse(occurrence.reference) is not None \
                or designators_module.parse(occurrence.item.name) is not None:
            with_designator += 1
    named = bool(_BOARD_WORDS.search(label or "")) or bool(
        _BOARD_WORDS.search(getattr(plate.item, "name", "") or ""))
    if with_designator >= 2 or on_board >= 4 or (named and on_board >= 1):
        reason = (f"пластина «{plate.label}» {plate_facts.plate['length']:.4g}×"
                  f"{plate_facts.plate['width']:.4g}×{plate_facts.plate['thickness']:.3g} мм, "
                  f"на ней деталей {on_board}, из них с обозначениями {with_designator}")
        return key_of(path), label or "Плата", key_of(path + (plate.stable_id,)), reason
    return None


def _box_in_node(facts, matrix) -> tuple:
    from .model import _obb_corners

    corners = _obb_corners(facts) @ matrix[:3, :3].T + matrix[:3, 3]
    return corners.min(axis=0), corners.max(axis=0)


def _child_box(device, occurrence):
    from .model import _box_corners

    low, high = device.item_box(occurrence.item)
    if low is None:
        return None
    matrix = occurrence.transform
    corners = _box_corners(low, high) @ matrix[:3, :3].T + matrix[:3, 3]
    return corners.min(axis=0), corners.max(axis=0)


def _small(box, plate_facts) -> bool:
    size = float(np.max(box[1] - box[0]))
    return size < 0.5 * plate_facts.plate["length"]


def _on_plate(box, plate_box, normal) -> bool:
    """Деталь над пластиной (в её контуре) и не дальше ON_BOARD_GAP от неё."""
    axis = int(np.argmax(np.abs(normal)))
    across = [index for index in range(3) if index != axis]
    for index in across:
        if box[1][index] < plate_box[0][index] or box[0][index] > plate_box[1][index]:
            return False
    gap = max(box[0][axis] - plate_box[1][axis], plate_box[0][axis] - box[1][axis], 0.0)
    return gap <= ON_BOARD_GAP


# --- разбор целиком ---------------------------------------------------------------


def classify(device: Device) -> list:
    """Разобрать прибор: найти платы, разложить на единицы, назвать роли.

    Возвращает замечания разбора — то, что человеку стоит проверить.
    """
    notes = []
    boards = find_boards(device)
    device.boards = {}
    substrates = {}
    for key, name, substrate, reason in boards:
        device.boards[key] = Board(key, name)
        substrates[substrate] = (key, reason)
    device.build_units()
    for board in device.boards.values():
        inside = [unit for unit in device.units.values() if _under(unit.key, board.key)]
        board.convention = designators_module.guess_convention(
            unit.designator for unit in inside)
    total = _device_size(device)
    # Сначала основания: компоненты узнаются по тому, что стоят на них.
    plates = {}
    for unit in device.units.values():
        unit.kind, unit.board = "", ""
        if unit.key in substrates:
            unit.role, unit.board = SUBSTRATE, substrates[unit.key][0]
            unit.reason = "тонкая пластина под компонентами платы"
            plates[unit.board] = unit
    for unit in device.units.values():
        if unit.role != SUBSTRATE or unit.key not in substrates:
            _classify_unit(device, unit, plates, total)
    for key, (board_key, reason) in substrates.items():
        board = device.boards[board_key]
        count = len(device.on_board(board_key))
        notes.append(f"Плата «{board.name}»: {reason}; компонентов {count}, "
                     f"обозначения по правилу «"
                     f"{designators_module.CONVENTIONS[board.convention]}».")
    if not device.boards:
        notes.append("Плат не найдено: нет узла с тонкой пластиной и деталями на ней. "
                     "Укажите плату вручную.")
    return notes


def _under(key: str, board_key: str) -> bool:
    return board_key == "" or key.startswith(board_key + "/")


def _device_size(device) -> float:
    low = np.full(3, np.inf)
    high = np.full(3, -np.inf)
    for unit in device.units.values():
        box = device.world_box(unit)
        low = np.minimum(low, box[0])
        high = np.maximum(high, box[1])
    return float(np.max(high - low)) if np.all(np.isfinite(low)) else 1.0


def _classify_unit(device, unit, plates, device_size) -> None:
    board_key = _board_of(device, unit, plates)
    name = unit.label if unit.label == unit.item.name else f"{unit.label} {unit.item.name}"
    by_name = fastener_name(unit.label) or fastener_name(unit.item.name)
    if board_key is not None and by_name is None:
        unit.role, unit.board = COMPONENT, board_key
        unit.kind = unit.designator.prefix if unit.designator is not None else ""
        unit.reason = ("позиционное обозначение " + unit.designator.text
                       if unit.designator is not None else "стоит на плате")
        return
    if by_name is not None:
        unit.role, unit.kind = FASTENER, by_name[0]
        unit.reason = by_name[1]
        size = fastener_size(name)
        if size:
            unit.reason += f", размер {size}"
        return
    facts = device.facts(unit.item)
    by_shape = fastener_shape(facts)
    if by_shape is not None:
        unit.role, unit.kind, unit.reason = by_shape
        unit.reason = "по форме: " + unit.reason
        return
    other = _first(other_name, unit.label, unit.item.name)
    if other is not None:
        unit.role, unit.kind, unit.reason = OTHER, other[0], other[1]
        return
    if unit.designator is not None and designators_module.is_connector(
            unit.designator.prefix, designators_module.guess_convention([unit.designator])):
        unit.role, unit.kind = OTHER, "разъём"
        unit.reason = f"обозначение {unit.designator.text} вне платы"
        return
    housing = _first(housing_name, unit.label, unit.item.name)
    if housing is not None:
        unit.role, unit.reason = HOUSING, housing
        return
    size = float(np.max(np.subtract(*device.world_box(unit)[::-1])))
    if size >= 0.3 * device_size:
        unit.role = HOUSING
        unit.reason = f"крупная деталь: {size:.4g} мм при размере прибора {device_size:.4g} мм"
        return
    unit.role = OTHER
    unit.reason = "не опознана — проверьте"


def _board_of(device, unit, plates):
    """Плата, компонентом которой может быть единица: лежит в узле платы
    и стоит на её основании (для платы-корня — только второе)."""
    for board in sorted(device.boards.values(), key=lambda item: -len(item.key)):
        if not _under(unit.key, board.key):
            continue
        substrate = plates.get(board.key)
        if substrate is None:
            return board.key
        if board.key and unit.designator is not None:
            return board.key
        plate = device.facts(substrate.item)
        normal = substrate.matrix[:3, :3] @ np.asarray(plate.plate["normal"])
        box = device.world_box(unit)
        if _small(box, plate) and _on_plate(box, device.world_box(substrate), normal):
            return board.key
    return None

