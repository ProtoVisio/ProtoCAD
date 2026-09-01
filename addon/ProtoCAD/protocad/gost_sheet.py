"""Листы по ГОСТ для TechDraw: рамка 2.301 и основная надпись 2.104.

Требование B из 02_SPIKE_WORKBENCH.md. Задание просит **минимальный** ГОСТ-лист
и прямо запрещает делать полный набор форм, поэтому реализованы форма 1
(первый лист) и форма 2а (последующие).

Шаблон строится как SVG с полями `freecad:editable` — тем самым механизмом,
который TechDraw использует для штатных шаблонов. Ничего, кроме публичного
API, не задействуется.

Оговорка, важная при чтении результата: внешние габариты (185×55 для формы 1,
185×15 для формы 2а) и поля рамки соответствуют стандарту, но разбивка
внутренних граф выполнена по типовой схеме и требует сверки с бумажным ГОСТ
перед применением в продукте. Для проверки реализуемости этого достаточно,
для выпуска документов — нет.
"""

from __future__ import annotations

# Форматы по ГОСТ 2.301, мм.
FORMATS = {
    "A4": (210.0, 297.0),
    "A3": (420.0, 297.0),
}

# Поля рамки: слева поле подшивки 20 мм, остальные по 5 мм.
MARGIN_BINDING = 20.0
MARGIN_OTHER = 5.0

STAMP_WIDTH = 185.0
STAMP_HEIGHT_FORM1 = 55.0
STAMP_HEIGHT_FORM2A = 15.0

NS = (
    'xmlns="http://www.w3.org/2000/svg" '
    'xmlns:freecad="https://www.freecad.org/wiki/index.php?title=Svg_Namespace"'
)

_LINE_THICK = 1.0
_LINE_THIN = 0.35


def sheet_size(fmt: str, landscape: bool) -> tuple[float, float]:
    width, height = FORMATS[fmt]
    if landscape:
        width, height = max(width, height), min(width, height)
    else:
        width, height = min(width, height), max(width, height)
    return width, height


def _rect(x: float, y: float, w: float, h: float, width: float) -> str:
    return (
        f'<rect x="{x:.2f}" y="{y:.2f}" width="{w:.2f}" height="{h:.2f}" '
        f'fill="none" stroke="#000000" stroke-width="{width}"/>'
    )


def _line(x1: float, y1: float, x2: float, y2: float, width: float) -> str:
    return (
        f'<line x1="{x1:.2f}" y1="{y1:.2f}" x2="{x2:.2f}" y2="{y2:.2f}" '
        f'stroke="#000000" stroke-width="{width}"/>'
    )


def _label(x: float, y: float, text: str, size: float = 2.5) -> str:
    return (
        f'<text x="{x:.2f}" y="{y:.2f}" font-family="osifont" font-size="{size}" '
        f'fill="#000000">{text}</text>'
    )


def _editable(x: float, y: float, name: str, value: str, size: float = 3.5) -> str:
    """Поле, редактируемое из TechDraw через ``EditableTexts``.

    Значение обязано лежать во вложенном ``<tspan>``: разбор TechDraw берёт
    текст именно оттуда. При тексте прямо в ``<text>`` поле в EditableTexts
    не попадает — проверено на FreeCAD 1.1.3, поля молча терялись.
    """
    return (
        f'<text id="proto_{name}" x="{x:.2f}" y="{y:.2f}" font-family="osifont" '
        f'font-size="{size}" fill="#000000" freecad:editable="{name}">'
        f'<tspan id="tspan_{name}" x="{x:.2f}" y="{y:.2f}">{value}</tspan></text>'
    )


# Графы основной надписи формы 1. Координаты — от левого нижнего угла штампа,
# в миллиметрах, ось Y направлена вверх (пересчитывается при выводе в SVG).
FORM1_FIELDS = (
    # (имя поля, x подписи, y строки, ширина, подпись графы, значение по умолчанию)
    ("Designation", 70.0, 30.0, 120.0, "", "АБВГ.XXXXXX.XXX"),
    ("Title", 70.0, 15.0, 120.0, "", "Наименование изделия"),
    ("Litera", 125.0, 45.0, 15.0, "Лит.", ""),
    ("Mass", 140.0, 45.0, 20.0, "Масса", ""),
    ("Scale", 160.0, 45.0, 25.0, "Масштаб", "1:1"),
    ("Sheet", 125.0, 5.0, 20.0, "Лист", "1"),
    ("Sheets", 145.0, 5.0, 40.0, "Листов", "1"),
)

FORM1_ROLES = (
    # (роль, имя поля фамилии, имя поля даты, y строки)
    ("Разраб.", "Author", "AuthorDate", 45.0),
    ("Пров.", "Checker", "CheckerDate", 40.0),
    ("Т.контр.", "TechController", "TechControllerDate", 35.0),
    ("Н.контр.", "Normcontrol", "NormcontrolDate", 25.0),
    ("Утв.", "Approver", "ApproverDate", 20.0),
)


def _form1(origin_x: float, origin_y: float, sheet_height: float) -> list[str]:
    """Основная надпись формы 1: 185×55, правый нижний угол внутри рамки."""
    parts: list[str] = []

    def sy(y_from_bottom: float) -> float:
        """SVG-координата Y: отсчёт сверху, штамп задан снизу."""
        return sheet_height - (origin_y + y_from_bottom)

    parts.append(
        _rect(origin_x, sy(STAMP_HEIGHT_FORM1), STAMP_WIDTH, STAMP_HEIGHT_FORM1, _LINE_THICK)
    )
    # Вертикаль, отделяющая блок подписей (65 мм) от блока обозначения.
    parts.append(_line(origin_x + 65.0, sy(STAMP_HEIGHT_FORM1), origin_x + 65.0, sy(0.0), _LINE_THICK))

    # Строки блока подписей: 11 строк по 5 мм.
    for index in range(1, 11):
        y = index * 5.0
        parts.append(_line(origin_x, sy(y), origin_x + 65.0, sy(y), _LINE_THIN))
    # Колонки блока подписей: 7 / 10 / 23 / 15 / 10.
    for offset in (7.0, 17.0, 40.0, 55.0):
        parts.append(_line(origin_x + offset, sy(50.0), origin_x + offset, sy(0.0), _LINE_THIN))

    # Правый блок: обозначение, наименование, литера/масса/масштаб, лист/листов.
    parts.append(_line(origin_x + 65.0, sy(40.0), origin_x + STAMP_WIDTH, sy(40.0), _LINE_THIN))
    parts.append(_line(origin_x + 65.0, sy(25.0), origin_x + STAMP_WIDTH, sy(25.0), _LINE_THIN))
    parts.append(_line(origin_x + 65.0, sy(10.0), origin_x + STAMP_WIDTH, sy(10.0), _LINE_THIN))
    for offset in (120.0, 140.0, 160.0):
        parts.append(_line(origin_x + offset, sy(25.0), origin_x + offset, sy(10.0), _LINE_THIN))
    parts.append(_line(origin_x + 120.0, sy(10.0), origin_x + 120.0, sy(0.0), _LINE_THIN))
    parts.append(_line(origin_x + 145.0, sy(10.0), origin_x + 145.0, sy(0.0), _LINE_THIN))

    # Подписи граф.
    for text, x, y in (
        ("Лит.", 122.0, 22.5),
        ("Масса", 141.5, 22.5),
        ("Масштаб", 161.0, 22.5),
        ("Лист", 122.0, 7.5),
        ("Листов", 147.0, 7.5),
    ):
        parts.append(_label(origin_x + x, sy(y), text, 2.0))

    # Блок подписей: роль, фамилия, подпись, дата.
    for role, author_field, date_field, y in FORM1_ROLES:
        parts.append(_label(origin_x + 0.5, sy(y - 3.5), role, 2.0))
        parts.append(_editable(origin_x + 18.0, sy(y - 3.5), author_field, "", 2.5))
        parts.append(_editable(origin_x + 41.0, sy(y - 3.5), date_field, "", 2.5))

    # Значимые графы.
    parts.append(_editable(origin_x + 70.0, sy(30.0), "Designation", "АБВГ.XXXXXX.XXX", 5.0))
    parts.append(_editable(origin_x + 70.0, sy(15.0), "Title", "Наименование", 3.5))
    parts.append(_editable(origin_x + 128.0, sy(15.0), "Litera", "", 3.5))
    parts.append(_editable(origin_x + 146.0, sy(15.0), "Mass", "", 3.5))
    parts.append(_editable(origin_x + 165.0, sy(15.0), "Scale", "1:1", 3.5))
    parts.append(_editable(origin_x + 133.0, sy(2.5), "Sheet", "1", 3.5))
    parts.append(_editable(origin_x + 160.0, sy(2.5), "Sheets", "1", 3.5))
    return parts


def _form2a(origin_x: float, origin_y: float, sheet_height: float) -> list[str]:
    """Основная надпись формы 2а для последующих листов: 185×15."""
    parts: list[str] = []

    def sy(y_from_bottom: float) -> float:
        return sheet_height - (origin_y + y_from_bottom)

    parts.append(
        _rect(origin_x, sy(STAMP_HEIGHT_FORM2A), STAMP_WIDTH, STAMP_HEIGHT_FORM2A, _LINE_THICK)
    )
    parts.append(_line(origin_x + 65.0, sy(STAMP_HEIGHT_FORM2A), origin_x + 65.0, sy(0.0), _LINE_THICK))
    for index in (1, 2):
        parts.append(_line(origin_x, sy(index * 5.0), origin_x + 65.0, sy(index * 5.0), _LINE_THIN))
    for offset in (7.0, 17.0, 40.0, 55.0):
        parts.append(_line(origin_x + offset, sy(15.0), origin_x + offset, sy(0.0), _LINE_THIN))
    parts.append(_line(origin_x + 165.0, sy(STAMP_HEIGHT_FORM2A), origin_x + 165.0, sy(0.0), _LINE_THIN))
    parts.append(_label(origin_x + 166.0, sy(11.0), "Лист", 2.0))
    parts.append(_editable(origin_x + 70.0, sy(5.0), "Designation", "АБВГ.XXXXXX.XXX", 5.0))
    parts.append(_editable(origin_x + 172.0, sy(4.0), "Sheet", "2", 3.5))
    return parts


def build_template_svg(fmt: str = "A4", landscape: bool = False, form: str = "1") -> str:
    """SVG-шаблон листа целиком: рамка по 2.301 и основная надпись по 2.104."""
    if fmt not in FORMATS:
        raise ValueError(f"неизвестный формат: {fmt}")
    if form not in ("1", "2a"):
        raise ValueError(f"неизвестная форма основной надписи: {form}")

    width, height = sheet_size(fmt, landscape)
    frame_x = MARGIN_BINDING
    frame_y = MARGIN_OTHER
    frame_w = width - MARGIN_BINDING - MARGIN_OTHER
    frame_h = height - 2 * MARGIN_OTHER

    parts = [
        f'<svg {NS} width="{width}mm" height="{height}mm" '
        f'viewBox="0 0 {width} {height}" version="1.1">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#ffffff" stroke="none"/>',
        # Рамка: поле подшивки 20 мм слева, по 5 мм с остальных сторон.
        _rect(frame_x, frame_y, frame_w, frame_h, _LINE_THICK),
    ]

    stamp_height = STAMP_HEIGHT_FORM1 if form == "1" else STAMP_HEIGHT_FORM2A
    stamp_x = frame_x + frame_w - STAMP_WIDTH
    if stamp_x < frame_x:
        raise ValueError(
            f"основная надпись {STAMP_WIDTH} мм не помещается в рамку {frame_w:.1f} мм"
        )
    builder = _form1 if form == "1" else _form2a
    parts.extend(builder(stamp_x, MARGIN_OTHER, height))
    parts.append("</svg>")
    return "\n".join(parts)


def editable_names(form: str = "1") -> tuple[str, ...]:
    """Имена редактируемых полей — контракт заполнения из свойств документа."""
    if form == "2a":
        return ("Designation", "Sheet")
    names = ["Designation", "Title", "Litera", "Mass", "Scale", "Sheet", "Sheets"]
    for _role, author_field, date_field, _y in FORM1_ROLES:
        names.extend((author_field, date_field))
    return tuple(names)
