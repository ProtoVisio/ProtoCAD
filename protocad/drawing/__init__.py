"""Чертежи по ГОСТ: проекции изделия в лист. Без FreeCAD.

Проецирование делает OCCT напрямую (`HLRBRep` через `kernel.project`) — то же,
что под капотом у TechDraw, только без посредника и в 24 раза быстрее по
замерам Spike 4.

Раскладка по ГОСТ 2.305, первый угол проецирования: вид сверху ПОД видом
спереди на одной вертикали, вид слева справа от него на одной горизонтали.
Виды в ряд были бы грубой ошибкой чертежа.

Кривые разбиваются на ломаные: для чертежа этого достаточно, а разбор всех
типов кривых в дуги SVG стоит делать после того, как воркфлоу устоится.
"""

from __future__ import annotations

from .. import kernel
from . import gost_sheet

# Толщины линий по ГОСТ 2.303.
LINE_MAIN = 0.6
LINE_THIN = 0.25

# Система координат каждого вида ЗАДАНА ЯВНО: (направление взгляда, «вправо»).
VIEWS = {
    "front": ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0), "Вид спереди"),
    "top": ((0.0, 0.0, 1.0), (1.0, 0.0, 0.0), "Вид сверху"),
    "left": ((-1.0, 0.0, 0.0), (0.0, -1.0, 0.0), "Вид слева"),
}

# Ряд масштабов по ГОСТ 2.302.
SCALES_UP = (2.0, 2.5, 4.0, 5.0, 10.0)
SCALES_DOWN = (1 / 2, 1 / 2.5, 1 / 4, 1 / 5, 1 / 10, 1 / 15, 1 / 20, 1 / 25, 1 / 40)


class DrawingError(RuntimeError):
    """Чертёж не построен."""


def _paths(shape, deflection: float = 0.05) -> list[list[tuple[float, float]]]:
    """Спроецированные рёбра как ломаные в плоскости листа."""
    result = []
    for edge in kernel.iter_edges(shape):
        points = kernel.discretize(edge, deflection)
        if len(points) < 2:
            continue
        result.append([(x, y) for x, y, _z in points])
    return result


def _bounds(paths) -> tuple[float, float, float, float]:
    xs = [x for path in paths for x, _ in path]
    ys = [y for path in paths for _, y in path]
    if not xs:
        return 0.0, 0.0, 1.0, 1.0
    return min(xs), min(ys), max(xs), max(ys)


def _gost_scale(fit: float) -> float:
    """Ближайший подходящий масштаб из ряда ГОСТ 2.302."""
    if fit >= 1.0:
        best = 1.0
        for value in SCALES_UP:
            if value <= fit:
                best = value
        return best
    for value in SCALES_DOWN:
        if value <= fit:
            return value
    return SCALES_DOWN[-1]


def scale_text(scale: float) -> str:
    if abs(scale - 1.0) < 1e-6:
        return "1:1"
    return f"{scale:g}:1" if scale > 1.0 else f"1:{round(1 / scale):g}"


def _polyline(points, offset_x, offset_y, scale, sheet_height, width) -> str:
    coords = " ".join(
        f"{offset_x + x * scale:.3f},{sheet_height - (offset_y + y * scale):.3f}"
        for x, y in points
    )
    return (
        f'<polyline points="{coords}" fill="none" stroke="#000000" '
        f'stroke-width="{width}" stroke-linecap="round"/>'
    )


def _fill_titleblock(svg: str, values: dict) -> str:
    """Заполнить графы основной надписи.

    Значение обязано попасть во вложенный ``<tspan>``: разбор SVG-шаблона
    берёт текст оттуда, и при тексте прямо в ``<text>`` поле теряется молча.
    """
    for field, value in values.items():
        marker = f'freecad:editable="{field}"><tspan'
        start = svg.find(marker)
        if start < 0:
            continue
        open_end = svg.find(">", start + len(marker))
        close = svg.find("</tspan>", open_end)
        if open_end < 0 or close < 0:
            continue
        svg = svg[: open_end + 1] + _escape(value) + svg[close:]
    return svg


def _escape(text) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build_drawing_svg(
    item,
    fmt: str = "A3",
    landscape: bool = True,
    views: tuple[str, ...] = ("front", "top"),
    scale: float | None = None,
    shape=None,
) -> str:
    """Чертёж изделия: рамка, основная надпись, проекции.

    ``shape`` позволяет передать геометрию отдельно — например слитую
    сборочной единицы, у которой собственной формы нет.
    """
    geometry = shape if shape is not None else getattr(item, "shape", None)
    if geometry is None:
        merged = getattr(item, "merged_shape", None)
        geometry = merged() if callable(merged) else None
    if geometry is None:
        raise DrawingError(f"{item.designation}: нет геометрии для чертежа")

    sheet_width, sheet_height = gost_sheet.sheet_size(fmt, landscape)
    field_left = gost_sheet.MARGIN_BINDING + 10.0
    field_bottom = gost_sheet.MARGIN_OTHER + gost_sheet.STAMP_HEIGHT_FORM1 + 10.0
    field_width = sheet_width - field_left - gost_sheet.MARGIN_OTHER - 10.0
    field_height = sheet_height - field_bottom - gost_sheet.MARGIN_OTHER - 10.0

    projected = {}
    for key in views:
        direction, x_direction, title = VIEWS[key]
        visible, _hidden = kernel.project(geometry, direction, x_direction)
        if visible is None:
            continue
        paths = _paths(visible)
        if paths:
            projected[key] = (title, paths, _bounds(paths))
    if not projected:
        raise DrawingError(f"{item.designation}: проекции не построены")

    def size(key):
        if key not in projected:
            return 0.0, 0.0
        _t, _p, (min_x, min_y, max_x, max_y) = projected[key]
        return max_x - min_x, max_y - min_y

    gap = 15.0
    front_w, front_h = size("front")
    top_w, top_h = size("top")
    left_w, _left_h = size("left")

    block_width = front_w + (gap + left_w if left_w else 0.0)
    block_height = front_h + (gap + top_h if top_h else 0.0)
    if scale is None:
        scale = _gost_scale(
            min(
                field_width / max(block_width, 1e-6),
                field_height / max(block_height, 1e-6),
            )
        )

    origin_x = field_left + max(0.0, (field_width - block_width * scale) / 2)
    origin_y = field_bottom + max(0.0, (field_height - block_height * scale) / 2)
    front_y = origin_y + (top_h + gap) * scale if top_h else origin_y
    positions = {
        "front": (origin_x, front_y),
        "top": (origin_x, origin_y),
        "left": (origin_x + (front_w + gap) * scale, front_y),
    }

    body = []
    for key, (title, paths, (min_x, min_y, _max_x, _max_y)) in projected.items():
        place_x, place_y = positions[key]
        offset_x = place_x - min_x * scale
        offset_y = place_y - min_y * scale
        for path in paths:
            body.append(_polyline(path, offset_x, offset_y, scale, sheet_height, LINE_MAIN))
        body.append(
            f'<text x="{place_x:.2f}" y="{sheet_height - (place_y - 5.0):.2f}" '
            f'font-family="osifont" font-size="5" fill="#000000">{title}</text>'
        )

    svg = gost_sheet.build_template_svg(fmt, landscape, "1")
    svg = svg.replace("</svg>", "\n".join(body) + "\n</svg>")
    return _fill_titleblock(
        svg,
        {
            "Designation": item.designation,
            "Title": item.name,
            "Scale": scale_text(scale),
            "Sheet": "1",
            "Sheets": "1",
        },
    )
