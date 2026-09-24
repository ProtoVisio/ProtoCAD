"""Позиционные обозначения ЭРИ: разбор, вид компонента, диапазоны.

Обозначение — главное, что несёт STEP платы: «R12», «DD3», «XS1». По нему
компонент попадает в группу своего вида, а полукомплект — в свой
расчётный случай («R1–R40 — первый, R41–R80 — второй»).

Правил два, и они расходятся в буквах:

* ГОСТ 2.710-81: D — микросхемы, VD — диоды, VT — транзисторы, X —
  соединители, Q — силовые выключатели;
* западная практика: D — диоды, Q — транзисторы, U — микросхемы, J и P —
  разъёмы, Y и X — кварцы.

Одна и та же «D5» — микросхема по ГОСТ и диод по западному правилу.
Поэтому правило определяется по самой плате — по буквам, которые бывают
только в одном из правил (VD, DD — только ГОСТ; U, J — только западные), —
и человек может его сменить.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

GOST = "gost"
WESTERN = "western"
CONVENTIONS = {GOST: "ГОСТ 2.710", WESTERN: "западное"}

#: Русские буквы, похожие на латинские. В обозначениях их пишут по ошибке —
#: «С1» русской «эс», — и без замены такое обозначение не узнаётся.
_LOOKALIKE = str.maketrans("АВСЕНКМОРТХУ", "ABCEHKMOPTXY")

#: Обозначение: необязательная приставка узла («A1-», «A2.»), буквы вида,
#: номер и необязательная буква исполнения.
_PATTERN = re.compile(
    r"^(?:(?P<group>[A-Z]{1,3}\d{1,4})[.\-:/_])?"
    r"(?P<prefix>[A-Z]{1,4})(?P<number>\d{1,5})(?P<suffix>[A-Z]?)$")

#: Виды по ГОСТ 2.710-81 (таблица 1): точное двухбуквенное обозначение, а
#: если его нет — по первой букве.
GOST_KINDS = {
    "A": "Устройства (узлы, модули)",
    "B": "Преобразователи неэлектрических величин", "BA": "Громкоговорители",
    "BK": "Тепловые датчики", "BQ": "Пьезоэлементы",
    "C": "Конденсаторы",
    "D": "Микросхемы", "DA": "Микросхемы аналоговые", "DD": "Микросхемы цифровые",
    "DS": "Микросхемы памяти",
    "E": "Элементы разные", "EK": "Нагревательные элементы", "EL": "Лампы",
    "F": "Устройства защиты", "FU": "Предохранители", "FV": "Разрядники",
    "G": "Генераторы и источники питания", "GB": "Батареи",
    "H": "Устройства индикации", "HA": "Звуковые сигнализаторы",
    "HG": "Индикаторы символьные", "HL": "Индикаторы световые",
    "K": "Реле",
    "L": "Катушки индуктивности и дроссели",
    "M": "Двигатели",
    "P": "Измерительные приборы",
    "Q": "Выключатели силовые",
    "R": "Резисторы", "RK": "Терморезисторы", "RP": "Потенциометры",
    "RS": "Шунты", "RU": "Варисторы",
    "S": "Коммутационные устройства", "SA": "Выключатели", "SB": "Кнопки",
    "T": "Трансформаторы", "TA": "Трансформаторы тока",
    "TV": "Трансформаторы напряжения",
    "U": "Преобразователи электрических величин",
    "V": "Полупроводниковые приборы", "VD": "Диоды", "VT": "Транзисторы",
    "VS": "Тиристоры", "VL": "Электровакуумные приборы",
    "W": "Линии СВЧ и антенны",
    "X": "Соединители", "XP": "Соединители (вилки)", "XS": "Соединители (розетки)",
    "XT": "Соединения разборные", "XW": "Соединители высокочастотные",
    "Y": "Электромеханические устройства",
    "Z": "Фильтры", "ZQ": "Кварцевые резонаторы",
}

WESTERN_KINDS = {
    "A": "Узлы", "ANT": "Антенны",
    "BT": "Батареи", "BZ": "Звуковые сигнализаторы",
    "C": "Конденсаторы", "CN": "Разъёмы", "CON": "Разъёмы", "CR": "Диоды",
    "D": "Диоды", "DS": "Индикаторы",
    "E": "Прочие", "F": "Предохранители", "FB": "Ферритовые фильтры",
    "FID": "Реперные знаки", "FL": "Фильтры",
    "H": "Крепёж и отверстия", "IC": "Микросхемы",
    "J": "Разъёмы", "JP": "Перемычки",
    "K": "Реле", "L": "Катушки индуктивности", "LED": "Светодиоды",
    "LS": "Динамики", "M": "Модули", "MH": "Крепёжные отверстия",
    "MOD": "Модули", "OSC": "Генераторы",
    "P": "Разъёмы", "PS": "Источники питания",
    "Q": "Транзисторы",
    "R": "Резисторы", "RL": "Реле", "RN": "Резисторные сборки",
    "RT": "Терморезисторы", "RV": "Переменные резисторы",
    "S": "Переключатели", "SW": "Переключатели",
    "T": "Трансформаторы", "TP": "Контрольные точки",
    "U": "Микросхемы", "VR": "Стабилизаторы напряжения",
    "X": "Кварцевые резонаторы", "XTAL": "Кварцевые резонаторы",
    "Y": "Кварцевые резонаторы", "Z": "Стабилитроны", "ZD": "Стабилитроны",
}

#: Буквы, которые бывают только в одном из правил, — по ним оно и узнаётся.
_ONLY_GOST = {"DA", "DD", "VD", "VT", "VS", "XS", "XP", "XT", "XW", "ZQ",
              "HL", "HG", "SA", "SB", "FU", "GB", "RK", "RP", "BQ", "TV", "TA"}
_ONLY_WESTERN = {"U", "IC", "J", "CN", "CON", "LED", "SW", "Y", "XTAL", "OSC",
                 "FB", "TP", "MH", "BT", "CR", "RN", "RV", "RT", "JP", "FID",
                 "MOD", "BZ", "LS"}

_KNOWN = set(GOST_KINDS) | set(WESTERN_KINDS)

#: Виды, которые по смыслу — разъёмы (для роли «прочее: разъём» вне платы).
CONNECTORS = {GOST: {"X", "XP", "XS", "XT", "XW"},
              WESTERN: {"J", "P", "CN", "CON"}}


@dataclass(frozen=True)
class Designator:
    """Разобранное обозначение: «A1-DD12» → узел A1, вид DD, номер 12."""

    text: str
    prefix: str
    number: int
    group: str = ""
    suffix: str = ""

    @property
    def sort_key(self) -> tuple:
        return (self.group, self.prefix, self.number, self.suffix)


def normalized(text: str) -> str:
    return str(text or "").strip().upper().translate(_LOOKALIKE).replace(" ", "")


def parse(text: str) -> Designator | None:
    """Обозначение или ``None``, если строка обозначением не выглядит.

    Буквы вида должны быть известными: иначе «SOIC8» и «LQFP64» — имена
    корпусов — сошли бы за обозначения.
    """
    found = _PATTERN.match(normalized(text))
    if not found:
        return None
    prefix = found["prefix"]
    if prefix not in _KNOWN:
        return None
    return Designator(text=normalized(text), prefix=prefix,
                      number=int(found["number"]), group=found["group"] or "",
                      suffix=found["suffix"] or "")


def guess_convention(designators) -> str:
    """Правило обозначений по набору: какого «только своего» больше."""
    gost = western = 0
    for designator in designators:
        if designator is None:
            continue
        gost += designator.prefix in _ONLY_GOST
        western += designator.prefix in _ONLY_WESTERN
    return WESTERN if western > gost else GOST


def kind_name(prefix: str, convention: str = GOST) -> str:
    """Вид компонента словами: «VD» → «Диоды»."""
    table = GOST_KINDS if convention == GOST else WESTERN_KINDS
    if prefix in table:
        return table[prefix]
    if prefix[:1] in table:
        return table[prefix[:1]]
    return "Прочие"


def is_connector(prefix: str, convention: str = GOST) -> bool:
    return prefix in CONNECTORS[convention] or (
        convention == GOST and prefix[:1] == "X")


# --- диапазоны --------------------------------------------------------------

_DASHES = re.compile(r"\s*(?:\.\.\.|…|–|—|-)\s*")
_ITEM = re.compile(r"^(?P<prefix>[A-Z]{1,4})(?P<low>\d{1,5})"
                   r"(?:~(?:(?P<prefix2>[A-Z]{1,4}))?(?P<high>\d{1,5}))?$")


class RangeError(ValueError):
    """Диапазон не разобран — с указанием, какой кусок непонятен."""


def parse_ranges(text: str) -> list:
    """«R1–R20, C1…C15; DD3» → [(«R», 1, 20), («C», 1, 15), («DD», 3, 3)].

    Второй конец можно писать без букв: «R1-20». Разные буквы на концах
    («R1–C5») — ошибка: такой диапазон ничего осмысленного не значит.
    """
    ranges = []
    # Пробелы здесь — разделители, поэтому не выбрасываются, как в
    # `normalized`: «R1 R2» — два обозначения, а не «R1R2».
    cleaned = _DASHES.sub("~", str(text or "").upper().translate(_LOOKALIKE)
                          .replace(";", ","))
    for piece in re.split(r"[,\s]+", cleaned):
        if not piece:
            continue
        found = _ITEM.match(piece)
        shown = piece.replace("~", "–")
        if not found or found["prefix"] not in _KNOWN:
            raise RangeError(f"не понял «{shown}»: ждал вида R1, R1–R20 или R1–20")
        prefix = found["prefix"]
        if found["prefix2"] and found["prefix2"] != prefix:
            raise RangeError(f"«{shown}»: у концов диапазона разные буквы")
        low = int(found["low"])
        high = int(found["high"]) if found["high"] else low
        if high < low:
            low, high = high, low
        ranges.append((prefix, low, high))
    return ranges


def in_ranges(designator: Designator | None, ranges) -> bool:
    if designator is None:
        return False
    return any(designator.prefix == prefix and low <= designator.number <= high
               for prefix, low, high in ranges)


def compact(designators) -> str:
    """Список обозначений коротко: R1–R4, R7, C1, C2 → «C1, C2, R1–R4, R7»."""
    parsed = sorted((item for item in designators if item is not None),
                    key=lambda item: item.sort_key)
    pieces = []
    run = []

    def flush():
        if not run:
            return
        first, last = run[0], run[-1]
        if len(run) >= 3:
            pieces.append(f"{first.text}–{last.text}")
        else:
            pieces.extend(item.text for item in run)
        run.clear()

    for item in parsed:
        if run and (item.group, item.prefix, item.suffix) == (
                run[-1].group, run[-1].prefix, run[-1].suffix) \
                and item.number == run[-1].number + 1:
            run.append(item)
            continue
        flush()
        run.append(item)
    flush()
    return ", ".join(pieces)
