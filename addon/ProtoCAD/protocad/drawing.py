"""Построение чертежа: проекция изделия в лист по ГОСТ.

Spike 3 показал, что TechDraw без GUI отдаёт спроецированную геометрию
настоящими кривыми (`Part::Line`, `Part::Circle`), а не растром. Значит виды
можно класть в свой SVG-лист и не зависеть от интерфейса FreeCAD.

Кривые разбиваются на ломаные: для чертежа этого достаточно, а разбор всех
типов кривых в SVG-дуги — работа, которую стоит делать после того, как
воркфлоу устоится.
"""

from __future__ import annotations

import FreeCAD as App
import Part

from . import gost_sheet

# Толщины линий по ГОСТ 2.303: сплошная основная и тонкая.
LINE_MAIN = 0.6
LINE_THIN = 0.25

# Система координат каждого вида задаётся ЯВНО: (вправо, вверх, название).
# Полагаться на то, в какой плоскости TechDraw вернёт кривые, нельзя — вид
# спереди приходил повёрнутым на 90°, и деталь 120×18 рисовалась узкой и
# высокой. Тело поворачивается в систему вида, затем проецируется вдоль Z.
VIEWS = {
    "front": (App.Vector(1, 0, 0), App.Vector(0, 0, 1), "Вид спереди"),
    "top": (App.Vector(1, 0, 0), App.Vector(0, 1, 0), "Вид сверху"),
    "left": (App.Vector(0, 1, 0), App.Vector(0, 0, 1), "Вид слева"),
}


def _to_view_frame(shape, right: App.Vector, up: App.Vector):
    """Повернуть тело так, чтобы «вправо» легло на X, «вверх» — на Y."""
    forward = right.cross(up)
    matrix = App.Matrix(
        right.x, right.y, right.z, 0.0,
        up.x, up.y, up.z, 0.0,
        forward.x, forward.y, forward.z, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )
    rotated = shape.copy()
    rotated.transformShape(matrix, True)
    return rotated


def _project(shape, right: App.Vector, up: App.Vector):
    """Спроецировать тело в плоские кривые в системе координат вида."""
    import TechDraw

    try:
        rotated = _to_view_frame(shape, right, up)
        # После поворота направление взгляда всегда -Z, поэтому получаемые
        # координаты предсказуемы: X вправо, Y вверх.
        result = TechDraw.project(rotated, App.Vector(0, 0, -1))
    except Exception:  # noqa: BLE001 — на вырожденной геометрии проекция падает
        return None
    # project() возвращает кортеж: видимые рёбра, скрытые, и прочие группы.
    visible = result[0] if result else None
    if visible is None or visible.isNull():
        return None
    return visible


def _edges_to_paths(shape, deviation: float = 0.05) -> list[list[tuple[float, float]]]:
    paths = []
    for edge in shape.Edges:
        try:
            points = edge.discretize(Deflection=deviation)
        except Exception:  # noqa: BLE001
            continue
        if len(points) < 2:
            continue
        paths.append([(p.x, p.y) for p in points])
    return paths


def _bounds(paths) -> tuple[float, float, float, float]:
    xs = [x for path in paths for x, _ in path]
    ys = [y for path in paths for _, y in path]
    if not xs:
        return 0.0, 0.0, 1.0, 1.0
    return min(xs), min(ys), max(xs), max(ys)


def _polyline(points, offset_x, offset_y, scale, sheet_height, width) -> str:
    coords = " ".join(
        f"{offset_x + x * scale:.3f},{sheet_height - (offset_y + y * scale):.3f}"
        for x, y in points
    )
    return (
        f'<polyline points="{coords}" fill="none" stroke="#000000" '
        f'stroke-width="{width}" stroke-linecap="round"/>'
    )


def build_drawing_svg(
    item,
    fmt: str = "A3",
    landscape: bool = True,
    views: tuple[str, ...] = ("front", "top"),
    scale: float | None = None,
) -> str:
    """Чертёж изделия: рамка, основная надпись и проекции.

    Масштаб подбирается так, чтобы виды поместились в поле листа, и
    округляется до ряда ГОСТ 2.302.
    """
    shape = getattr(item, "Shape", None)
    if shape is None or shape.isNull():
        raise ValueError(f"{item.Label!r}: нет геометрии для чертежа")

    sheet_width, sheet_height = gost_sheet.sheet_size(fmt, landscape)
    field_left = gost_sheet.MARGIN_BINDING + 10.0
    field_bottom = gost_sheet.MARGIN_OTHER + gost_sheet.STAMP_HEIGHT_FORM1 + 10.0
    field_width = sheet_width - field_left - gost_sheet.MARGIN_OTHER - 10.0
    field_height = sheet_height - field_bottom - gost_sheet.MARGIN_OTHER - 10.0

    projected = {}
    for key in views:
        right, up, title = VIEWS[key]
        result = _project(shape, right, up)
        if result is None:
            continue
        paths = _edges_to_paths(result)
        if paths:
            projected[key] = (title, paths, _bounds(paths))
    if not projected:
        raise ValueError(f"{item.Label!r}: проекции не построены")

    def size(key):
        if key not in projected:
            return 0.0, 0.0
        _t, _p, (min_x, min_y, max_x, max_y) = projected[key]
        return max_x - min_x, max_y - min_y

    # Раскладка по ГОСТ 2.305, первый угол проецирования: вид сверху ПОД видом
    # спереди на одной вертикали, вид слева — справа от него на одной
    # горизонтали. Виды в ряд были бы грубой ошибкой чертежа.
    gap = 15.0
    front_w, front_h = size("front")
    top_w, top_h = size("top")
    left_w, left_h = size("left")

    block_width = front_w + (gap + left_w if left_w else 0.0)
    block_height = front_h + (gap + top_h if top_h else 0.0)
    if scale is None:
        fit = min(
            field_width / max(block_width, 1e-6),
            field_height / max(block_height, 1e-6),
        )
        scale = _gost_scale(fit)

    scaled_w = block_width * scale
    scaled_h = block_height * scale
    origin_x = field_left + max(0.0, (field_width - scaled_w) / 2)
    origin_y = field_bottom + max(0.0, (field_height - scaled_h) / 2)

    # Вид спереди — сверху слева блока; вид сверху под ним; вид слева справа.
    front_x = origin_x
    front_y = origin_y + (top_h + gap) * scale if top_h else origin_y
    positions = {
        "front": (front_x, front_y),
        "top": (front_x, origin_y),
        "left": (front_x + (front_w + gap) * scale, front_y),
    }

    base = gost_sheet.build_template_svg(fmt, landscape, "1")
    body = []
    for key, (title, paths, (min_x, min_y, max_x, max_y)) in projected.items():
        place_x, place_y = positions[key]
        offset_x = place_x - min_x * scale
        offset_y = place_y - min_y * scale
        for path in paths:
            body.append(
                _polyline(path, offset_x, offset_y, scale, sheet_height, LINE_MAIN)
            )
        # Подпись под своим видом, а не общей строкой внизу листа.
        label_y = sheet_height - (place_y - 5.0)
        body.append(
            f'<text x="{place_x:.2f}" y="{label_y:.2f}" font-family="osifont" '
            f'font-size="5" fill="#000000">{title}</text>'
        )

    svg = base.replace("</svg>", "\n".join(body) + "\n</svg>")
    svg = _fill_titleblock(svg, item, scale)
    return svg


def _gost_scale(fit: float) -> float:
    """Ближайший меньший масштаб из ряда ГОСТ 2.302."""
    increase = (2.0, 2.5, 4.0, 5.0, 10.0)
    reduce = (1 / 2, 1 / 2.5, 1 / 4, 1 / 5, 1 / 10, 1 / 15, 1 / 20, 1 / 25, 1 / 40)
    if fit >= 1.0:
        best = 1.0
        for value in increase:
            if value <= fit:
                best = value
        return best
    best = reduce[-1]
    for value in reduce:
        if value <= fit:
            best = value
            break
    return best


def scale_text(scale: float) -> str:
    if abs(scale - 1.0) < 1e-6:
        return "1:1"
    if scale > 1.0:
        return f"{scale:g}:1"
    return f"1:{round(1 / scale):g}"


def _fill_titleblock(svg: str, item, scale: float) -> str:
    """Заполнить графы основной надписи из свойств изделия."""
    replacements = {
        "Designation": item.Designation,
        "Title": item.ItemName,
        "Scale": scale_text(scale),
        "Sheet": "1",
        "Sheets": "1",
    }
    for field, value in replacements.items():
        marker = f'freecad:editable="{field}"><tspan'
        start = svg.find(marker)
        if start < 0:
            continue
        open_tag_end = svg.find(">", start + len(marker))
        close_tag = svg.find("</tspan>", open_tag_end)
        if open_tag_end < 0 or close_tag < 0:
            continue
        svg = svg[: open_tag_end + 1] + _escape(value) + svg[close_tag:]
    return svg


def _escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )
