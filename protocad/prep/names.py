"""Имена групп для файлов решателей.

Внутри ProtoCAD группа называется как угодно — «Закрепление», «Корпус
[2]». В файл решателя такое имя уйти не может: CalculiX и Abaqus читают
имена наборов латиницей без пробелов, OpenFOAM делает из имени каталог, у
CGNS предел — 32 знака. Имя переводится здесь, одним правилом для всех
форматов, и перевод возвращается вызывающему: человек должен знать, как
его «Закрепление» называется в файле.
"""

from __future__ import annotations

import re

#: Транслитерация по образцу ГОСТ 7.79 (система Б), без диакритики: так
#: имя читается и остаётся латиницей.
_TABLE = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "j", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

#: Предел длины. Меньший из распространённых: у CGNS 32 знака.
LIMIT = 32


def transliterate(text: str) -> str:
    result = []
    for character in text:
        lower = character.lower()
        if lower in _TABLE:
            value = _TABLE[lower]
            result.append(value.capitalize() if character != lower and value
                          else value)
        else:
            result.append(character)
    return "".join(result)


def solver_name(text: str) -> str:
    """Одно имя: латиница, цифры и подчёркивание, начинается с буквы."""
    plain = transliterate(text)
    plain = re.sub(r"[^A-Za-z0-9_]+", "_", plain).strip("_")
    plain = re.sub(r"_+", "_", plain)
    if not plain or not plain[0].isalpha():
        plain = f"G_{plain}" if plain else "G"
    return plain[:LIMIT]


def solver_names(names) -> dict:
    """Имена разом, без совпадений: {исходное: для решателя}.

    Совпадения регистрозависимы не везде (CalculiX переводит имена в
    верхний регистр), поэтому сравниваются без учёта регистра.
    """
    result, taken = {}, set()
    for name in names:
        base = solver_name(name)
        candidate, number = base, 2
        while candidate.upper() in taken:
            suffix = f"_{number}"
            candidate = base[:LIMIT - len(suffix)] + suffix
            number += 1
        taken.add(candidate.upper())
        result[name] = candidate
    return result
