"""Движок построения: граница между интерфейсом и геометрией.

По `docs/08_ENGINE_BACKEND.md`. Интерфейс ProtoCAD отвечает за порядок
аргументов, выбор, предпросмотр и дерево; движок — за точную геометрию.
Между ними проходят только данные (`protocol.py`).

Движков два, и это временно:

* ``LocalBackend`` — нынешний, на OCP/OCCT. По §15 и §16 он больше не
  основной путь: остаётся исследовательским прототипом и **эталоном
  сверки**;
* ``FreeCADBackend`` — целевой, headless FreeCAD в отдельном процессе.

Смысл держать оба одновременно ровно один: переключение должно быть
проверяемым. Один и тот же набор приёмочных проверок гоняется по обоим, и
расхождение видно числом, а не на глаз (§15.3).

Оба движка обязаны отвечать одинаково — включая формулировки отказов.
Разные слова на одну и ту же беду означают, что интерфейс придётся учить
двум движкам вместо одного.
"""

from __future__ import annotations

from .policy import Policy  # noqa: F401
from .protocol import (  # noqa: F401
    PROTOCOL,
    DraftRequest,
    DressUpRequest,
    HoleRequest,
    HoleToolRequest,
    PatternRequest,
    Profile,
    Capabilities,
    Diagnostic,
    EndCondition,
    EntityRef,
    FeatureResult,
    Mesh,
    PadRequest,
    RevolveRequest,
    SectionRequest,
    SectionResult,
    ShellRequest,
    Status,
    ThinType,
    error,
)


class Backend:
    """Что обязан уметь любой движок.

    Нарочно узкий: чем меньше здесь методов, тем меньше поверхность, по
    которой два движка могут разойтись.
    """

    name = "?"

    def capabilities(self) -> Capabilities:
        raise NotImplementedError

    def available(self) -> tuple:
        """(готов ли, почему нет). Проверяется до первой команды."""
        raise NotImplementedError

    def dress_up(self, request) -> FeatureResult:
        """Скругление или фаска по выбранным рёбрам."""
        raise NotImplementedError

    def pad(self, request: PadRequest) -> FeatureResult:
        """Предпросмотр или применение выдавливания.

        Разницы между предпросмотром и применением на этом уровне нет
        намеренно (§12.1): предпросмотр обязан считаться тем же кодом,
        иначе он показывает не то, что построится.
        """
        raise NotImplementedError

    def revolve(self, request) -> FeatureResult:
        """Вращение профиля вокруг оси."""
        raise NotImplementedError

    def hole(self, request) -> FeatureResult:
        """Отверстие по эскизу."""
        raise NotImplementedError

    def draft(self, request) -> FeatureResult:
        """Уклон: наклонить выбранные грани вокруг нейтральной."""
        raise NotImplementedError

    def shell(self, request) -> FeatureResult:
        """Оболочка: снять выбранные грани, остальное сделать стенкой."""
        raise NotImplementedError

    def pattern(self, request) -> FeatureResult:
        """Массив операций: линейный, круговой или зеркало."""
        raise NotImplementedError

    def export(self, document_id: str, path, body_id: str = "") -> FeatureResult:
        """Выгрузить построенную деталь в STEP (с именами тел) или BREP.

        Не операция, а обмен: так деталь уходит в подготовку к расчёту
        (`protocad.prep`) и в соседние системы. Форма у движка — ему и
        писать файл; через границу идёт только путь (§10.2).
        """
        raise NotImplementedError

    def section(self, request) -> SectionResult:
        """След детали на плоскости — рёбрами, а не сеткой.

        Здесь, а не среди служебного, потому что это ГЕОМЕТРИЯ: точное
        пересечение считает ядро, и считать его над движком значило бы
        завести второе ядро в окне.
        """
        raise NotImplementedError


def profile_of(sketch, regions=None, open_too: bool = False) -> Profile:
    """Области эскиза → профиль в общем виде.

    Кривые переводятся в координаты ДЕТАЛИ: движок не обязан знать про
    наши плоскости. Дуги передаются тремя точками — угол отсчитывается от
    собственной оси окружности ядра, а она у каждого ядра своя.

    ``regions`` — устойчивые ссылки на выбранные области. Пусто означает
    «весь эскиз»: наружный контур, вложенные становятся отверстиями. Это
    НЕ то же самое, что выбор всех областей, при котором они объединяются
    и отверстия исчезают.

    ``open_too`` добавляет РАЗОМКНУТЫЕ контуры эскиза. Обычному
    выдавливанию они не нужны — области из них не получается, — но у
    тонкой стенки это основной случай: разомкнутый контур иначе не
    выдавить вовсе.

    Разомкнутое берётся только там, где областей НЕТ НИ ОДНОЙ. Профиль
    либо замкнутый, либо разомкнутый, и смешивать их нельзя: выбирают в
    эскизе области, разомкнутый контур выбрать нечем, и добавить его к
    выбранным областям значило бы расширить операцию за пределы того, что
    человек указал.
    """
    from ..sketch import geom2d
    from ..sketch import regions as regions_module
    from .protocol import (
        curve_arc, curve_circle, curve_ellipse, curve_hyperbola,
        curve_line, curve_parabola, curve_spline)

    found = regions_module.build(sketch)
    if regions:
        chosen = []
        for reference in regions:
            region = regions_module.find(found, reference)
            if region is None:
                raise ValueError(
                    "выбранная область больше не существует — эскиз "
                    "изменился так, что она пропала или разделилась")
            chosen.append(region)
    else:
        # Без выбора: наружные области (глубина чётная) с их отверстиями.
        chosen = [region for region in found if region.depth % 2 == 0]

    plane = sketch.plane

    def curves(loop) -> list:
        result = []
        for piece in (loop.pieces if hasattr(loop, "pieces") else loop):
            segment = piece.segment
            if segment.kind == "line":
                result.append(curve_line(plane.point_at(*piece.start),
                                         plane.point_at(*piece.end)))
                continue
            whole = piece.t_start <= 1e-12 and piece.t_end >= 1.0 - 1e-12
            if segment.kind == "circle" and whole:
                centre, radius = sketch.circle_geometry(segment)
                result.append(curve_circle(plane.point_at(*centre),
                                           plane.normal, radius))
                continue
            if segment.kind == "spline":
                geometry = sketch.spline_geometry(segment)
                result.append(curve_spline(
                    [plane.point_at(*pole) for pole in geometry.poles],
                    geometry.knots, geometry.mults, geometry.degree,
                    0.0 if whole else piece.t_start,
                    0.0 if whole else piece.t_end))
                continue
            if segment.kind in ("ellipse", "ellipse_arc"):
                geometry = sketch.ellipse_geometry(segment)
                centre = geometry.center
                tip = geom2d.point_at_angle(
                    centre, geometry.major, geometry.rotation)
                # Углы у движка АБСОЛЮТНЫЕ, а доля куска считается вдоль
                # собственного параметра объекта: у целого эллипса это весь
                # оборот, у дуги — её размах.
                sweep = (geom2d.TAU if segment.kind == "ellipse"
                         else geom2d.arc_sweep(geometry.start_angle,
                                               geometry.end_angle))
                low = geometry.start_angle + sweep * piece.t_start
                high = geometry.start_angle + sweep * piece.t_end
                if segment.kind == "ellipse" and whole:
                    low = high = 0.0
                result.append(curve_ellipse(
                    plane.point_at(*centre), plane.point_at(*tip),
                    geometry.minor, low, high))
                continue
            if segment.kind == "hyperbola_arc":
                geometry = sketch.hyperbola_geometry(segment)
                low, high = geometry.start_angle, geometry.end_angle
                span = high - low
                tip = geom2d.point_at_angle(geometry.center, geometry.major,
                                            geometry.rotation)
                result.append(curve_hyperbola(
                    plane.point_at(*geometry.center),
                    plane.point_at(*tip), geometry.minor,
                    low + span * piece.t_start,
                    low + span * piece.t_end))
                continue
            if segment.kind == "parabola_arc":
                geometry = sketch.parabola_geometry(segment)
                low, high = geometry.start_angle, geometry.end_angle
                span = high - low
                result.append(curve_parabola(
                    plane.point_at(*geometry.vertex),
                    plane.point_at(*geometry.focus),
                    low + span * piece.t_start,
                    low + span * piece.t_end))
                continue
            middle = _midpoint_of(sketch, piece)
            result.append(curve_arc(plane.point_at(*piece.start),
                                    plane.point_at(*middle),
                                    plane.point_at(*piece.end)))
        return result

    return Profile(
        regions=[
            {"outer": curves(region.outer),
             "inner": [curves(hole) for hole in region.inner]}
            for region in chosen
        ],
        chains=([curves(chain) for chain in regions_module.chains(sketch)]
                if open_too and not found else []),
        # Плоскость эскиза передаётся ЧИСЛАМИ вместе с кривыми. Движку её
        # не восстановить: знак нормали грани зависит от обхода контура, а
        # от него зависит, в какую сторону сверлить и с какой стороны
        # отсчитывать угол вращения.
        origin=tuple(plane.origin),
        normal=tuple(plane.normal),
        x_direction=tuple(plane.x_direction),
    )


def _midpoint_of(sketch, piece):
    """Середина куска дуги на ИСТИННОЙ кривой, а не на ломаной.

    Ломаная вписана в окружность и отстоит от неё на сотые доли
    миллиметра. Дуга, построенная через такую точку, получилась бы
    другого радиуса — и контур не сошёлся бы с соседним куском.
    """
    from ..sketch import geom2d
    from ..sketch import tools as tools_module

    shape = tools_module.describe(sketch, piece.segment)
    fraction = (piece.t_start + piece.t_end) / 2.0
    if shape.kind == "circle":
        return geom2d.point_at_angle(shape.center, shape.radius,
                                     geom2d.TAU * fraction)
    sweep = geom2d.arc_sweep(shape.start_angle, shape.end_angle)
    return geom2d.point_at_angle(shape.center, shape.radius,
                                 shape.start_angle + sweep * fraction)


def make_backend(prefer: str = ""):
    """Движок для работы. Без указания — целевой, если он поднимается.

    Молчаливого отката на прототип НЕ делается: два источника истины на
    одну операцию (§16.2) расходятся в допусках, именах и диагностике, и
    узнаётся это на чужой детали.
    """
    from .freecad_backend import FreeCADBackend
    from .local_backend import LocalBackend
    from .policy import Policy

    backends = {"freecad": FreeCADBackend, "local": LocalBackend}
    if prefer:
        chosen = backends.get(prefer)
        if chosen is None:
            raise ValueError(f"неизвестный движок: {prefer!r}")
        # Правила ProtoCAD надеваются на ЛЮБОЙ движок: разворот по
        # материалу и «ничего не произошло — это отказ» одинаковы для
        # обоих, иначе интерфейс придётся учить двум движкам.
        return Policy(chosen())
    engine = FreeCADBackend()
    ready, reason = engine.available()
    if ready:
        return Policy(engine)
    raise RuntimeError(
        f"движок FreeCAD недоступен: {reason}. "
        f"Прототип на OCP не подставляется автоматически — "
        f"запросите его явно: make_backend('local')"
    )
