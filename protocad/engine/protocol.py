"""Запросы и ответы к движку. Только данные, никакой геометрии.

По `docs/08_ENGINE_BACKEND.md`, §10 и §18: через эту границу проходят
устойчивые идентификаторы, треугольники, габариты и диагностика — и
никогда формы ядра. Причина не в чистоте, а в §16.3: у FreeCAD своя сборка
OCCT, у OCP своя, и передача объекта одной библиотеки в другую роняет
процесс в месте, не связанном с ошибкой.

Отсюда же следует, что типы здесь обязаны быть пригодны к сериализации:
числа, строки, списки чисел. Как только сюда попадёт `TopoDS_Shape`,
граница перестанет быть границей — а обнаружится это на другой машине, при
другой сборке.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum

#: Версия протокола. Меняется, когда меняется состав запросов или ответов.
#: Движок сообщает свою в ``Capabilities``; несовпадение старшей части —
#: отказ работать, а не попытка договориться.
PROTOCOL = (1, 0)


class EndCondition(str, Enum):
    """Концевые условия. Значения совпадают с теми, что понимает движок."""

    BLIND = "blind"
    THROUGH_ALL = "through_all"
    UP_TO_FIRST = "up_to_first"
    UP_TO_LAST = "up_to_last"
    UP_TO_FACE = "up_to_face"
    MID_PLANE = "mid_plane"


class ThinType(str, Enum):
    """Куда нарастить стенку от контура. Пусто — выдавливать сплошным.

    Материал по умолчанию идёт ВНУТРЬ нарисованного контура: контур —
    наружная кромка стенки. ``flip`` в запросе выворачивает это наружу.
    """

    NONE = ""
    ONE = "one"          # односторонний: вся толщина на одну сторону
    MID = "mid"          # средняя плоскость: пополам на обе
    TWO = "two"          # двусторонний: своя толщина на каждую


class Status(str, Enum):
    VALID = "valid"
    INVALID = "invalid"


def curve_line(a, b) -> dict:
    return {"kind": "line", "a": [float(v) for v in a], "b": [float(v) for v in b]}


def curve_arc(a, middle, b) -> dict:
    """Дуга по трём точкам. Именно по трём, а не по углам: угол
    отсчитывается от собственной оси окружности ядра, а она у каждого
    ядра своя, и договориться о ней через границу нельзя."""
    return {"kind": "arc", "a": [float(v) for v in a],
            "m": [float(v) for v in middle], "b": [float(v) for v in b]}


def curve_spline(poles, knots, mults, degree: int,
                 start: float = 0.0, end: float = 0.0) -> dict:
    """Сплайн полюсами, узлами и кратностями — тем же, чем его держит ядро.

    Полюсы идут ТОЧКАМИ в координатах детали, как и всё остальное на этой
    границе: точку можно перевести куда угодно одним преобразованием.

    ``start`` и ``end`` — доли от 0 до 1. Равные значения означают кривую
    целиком.
    """
    return {"kind": "spline",
            "p": [[float(v) for v in pole] for pole in poles],
            "k": [float(v) for v in knots],
            "m": [int(v) for v in mults],
            "d": int(degree),
            "t0": float(start), "t1": float(end)}


def curve_hyperbola(centre, major_point, minor: float,
                    start: float, end: float) -> dict:
    """Кусок гиперболы.

    Задана так же, как эллипс: центром, ТОЧКОЙ на действительной оси и
    мнимой полуосью. Точку можно перевести в любые координаты одним
    преобразованием, а направление оси пришлось бы поворачивать отдельно.

    ``start`` и ``end`` — ГИПЕРБОЛИЧЕСКИЙ параметр: точка при ``t`` это
    ``(a·ch t, b·sh t)``. У решателя и у ядра он одинаков — проверено на
    обоих, — поэтому через границу идёт как есть.
    """
    return {"kind": "hyperbola",
            "c": [float(value) for value in centre],
            "m": [float(value) for value in major_point],
            "b": float(minor),
            "t0": float(start), "t1": float(end)}


def curve_parabola(vertex, focus, start: float, end: float) -> dict:
    """Кусок параболы.

    Задана ВЕРШИНОЙ и ФОКУСОМ — двумя точками, как и всё остальное на этой
    границе: точку можно перевести в любые координаты одним и тем же
    преобразованием, а направление оси пришлось бы поворачивать отдельно.

    ``start`` и ``end`` — собственный параметр параболы, то есть местная
    координата поперёк оси: точка при ``t`` это ``(t² / 4p, t)`` в системе
    вершины. Это НЕ угол и не доля длины; названо здесь прямо, потому что
    у параболы в ходу несколько разных параметризаций.
    """
    return {"kind": "parabola",
            "v": [float(value) for value in vertex],
            "f": [float(value) for value in focus],
            "t0": float(start), "t1": float(end)}


def curve_ellipse(centre, major_point, minor: float,
                  start: float = 0.0, end: float = 0.0) -> dict:
    """Эллипс или его дуга.

    Большая полуось передаётся ТОЧКОЙ на ней, а не длиной с направлением.
    Причина та же, по которой дуга передаётся тремя точками: точку можно
    перевести в любые координаты одним и тем же преобразованием, а
    направление пришлось бы поворачивать отдельно — и ошибиться на этом
    легко, потому что ошибка видна не сразу.

    ``start`` и ``end`` — собственный параметр эллипса, радианы. Равные
    значения означают целый эллипс.
    """
    return {"kind": "ellipse",
            "c": [float(v) for v in centre],
            "m": [float(v) for v in major_point],
            "b": float(minor),
            "t0": float(start), "t1": float(end)}


def curve_circle(centre, normal, radius: float) -> dict:
    return {"kind": "circle", "c": [float(v) for v in centre],
            "n": [float(v) for v in normal], "r": float(radius)}


@dataclass
class Profile:
    """Профиль операции в виде, понятном любому движку.

    Ни грани, ни формы ядра: только кривые в координатах ДЕТАЛИ. Каждая
    область — внешняя петля и вложенные; вложенные становятся отверстиями.

    Почему координаты детали, а не плоскости эскиза: движок не обязан
    знать про наши плоскости, а плоскость всё равно пришлось бы передать
    и договориться о её осях. Точка в пространстве однозначна.
    """

    regions: list = field(default_factory=list)   # [{"outer": [...], "inner": [[...]]}]
    #: Незамкнутые цепочки эскиза: [[кривая, ...], ...]. Области из них не
    #: получаются по определению, и обычное выдавливание их не видит. Но
    #: тонкая стенка строится как раз по разомкнутому контуру — иначе его
    #: не выдавить вовсе, — и передать его надо отдельно от областей.
    chains: list = field(default_factory=list)
    #: Плоскость эскиза. Кривые идут в координатах детали, но ПЛОСКОСТЬ
    #: приходится назвать отдельно: у профиля есть сторона, и определить
    #: её по геометрии нельзя — знак нормали грани зависит от обхода
    #: контура. Пока движок восстанавливал плоскость сам, отверстие
    #: сверлилось от детали, а вращение вокруг оси в плоскости профиля
    #: отвергалось как «ось перпендикулярна профилю».
    origin: tuple = (0.0, 0.0, 0.0)
    normal: tuple = (0.0, 0.0, 1.0)
    x_direction: tuple = (1.0, 0.0, 0.0)

    @property
    def empty(self) -> bool:
        return not self.regions and not self.chains

    def to_dict(self) -> dict:
        return {"regions": self.regions, "chains": self.chains,
                "origin": list(self.origin),
                "normal": list(self.normal),
                "x_direction": list(self.x_direction)}

    @classmethod
    def from_dict(cls, data) -> "Profile":
        if data is None:
            return cls()
        return cls(regions=list(data.get("regions") or ()),
                   chains=list(data.get("chains") or ()),
                   origin=tuple(data.get("origin") or (0.0, 0.0, 0.0)),
                   normal=tuple(data.get("normal") or (0.0, 0.0, 1.0)),
                   x_direction=tuple(data.get("x_direction") or (1.0, 0.0, 0.0)))


@dataclass
class PadRequest:
    """Выдавливание. Состав полей — из §7.2 решения.

    ``profile_regions`` — устойчивые ссылки на выбранные области эскиза, а
    не «весь эскиз»: что именно выдавливать, решает человек.
    """

    document_id: str
    body_id: str
    sketch_id: str = ""
    #: Геометрия профиля. Передаётся кривыми, а не формой ядра (§10.3).
    profile: "Profile" = field(default_factory=lambda: Profile())
    end_condition: EndCondition = EndCondition.BLIND
    length: float = 10.0
    #: До какой грани строить при «до грани». Номер грани в текущем теле —
    #: тот же, что вернул выбор мышью, и с той же оговоркой: это не
    #: топологическое имя, и правка выше по дереву может его сдвинуть.
    #: −1 — цели нет.
    target_face: int = -1
    #: Устойчивое имя той же грани. Пусто — остаётся номер.
    target_face_name: str = ""
    reversed: bool = False
    taper_angle_deg: float = 0.0
    #: НАЧАЛО операции: сдвиг плоскости профиля вдоль её нормали. У
    #: PartDesign своего свойства для этого нет — двигается сам носитель
    #: профиля, и это точно: «начало» и есть то, откуда идёт материал.
    #: Откуда взялось число — дело интерфейса: ноль, введённая величина
    #: или расстояние до указанной грани.
    start_offset: float = 0.0
    #: ВТОРОЕ НАПРАВЛЕНИЕ. У FreeCAD это `SideType = "Two sides"` с
    #: собственными `Type2`, `Length2`, `TaperAngle2`, `UpToFace2`.
    direction2: bool = False
    end_condition2: EndCondition = EndCondition.BLIND
    length2: float = 10.0
    target_face2: int = -1
    target_face2_name: str = ""
    taper_angle2_deg: float = 0.0
    #: ТОНКОСТЕННЫЙ ЭЛЕМЕНТ. Стенка задаётся не операцией, а ПРОФИЛЕМ:
    #: вместо грани по контуру берётся кольцо между двумя его смещениями,
    #: а дальше выдавливание обычное. Отдельной операции для этого не
    #: нужно, и лишней записи в дереве не появляется.
    thin: ThinType = ThinType.NONE
    thickness: float = 1.0
    thickness2: float = 1.0
    #: Вывернуть стенку на другую сторону контура. У замкнутого контура
    #: это «внутрь ↔ наружу», у разомкнутого — просто другая сторона:
    #: у него сторон две и по геометрии они неразличимы.
    thin_flip: bool = False
    #: Прибавить к телу или снять с него. Отдельного типа запроса нет: в
    #: движке это одна операция с разным знаком, и разводить их здесь
    #: значило бы разводить два пути там, где он один.
    subtract: bool = False
    #: Показать, но не оставлять. Считается ТЕМ ЖЕ кодом, что и применение
    #: (§12.1): предпросмотр, считаемый иначе, показывает не то, что
    #: построится, и расхождение обнаруживается уже после подтверждения.
    preview: bool = False
    #: Какую операцию править. Пусто — создать новую.
    feature_id: str = ""
    #: Номер запроса предпросмотра. Ответ с устаревшим номером
    #: отбрасывается: пока движок считал, человек успел подвигать поле.
    revision: int = 0
    session_id: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["end_condition"] = self.end_condition.value
        data["end_condition2"] = self.end_condition2.value
        data["thin"] = self.thin.value
        data["profile"] = self.profile.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "PadRequest":
        data = dict(data)
        data["end_condition"] = EndCondition(data.get("end_condition", "blind"))
        data["end_condition2"] = EndCondition(
            data.get("end_condition2", "blind"))
        data["thin"] = ThinType(data.get("thin", "") or "")
        data["profile"] = Profile.from_dict(data.get("profile"))
        return cls(**data)


@dataclass
class DressUpRequest:
    """Скругление или фаска по выбранным рёбрам.

    Рёбра называются НОМЕРАМИ в текущем теле — теми же, что вернул выбор
    мышью вместе с описанием. Это не топологические имена: правка размера
    выше по дереву может сдвинуть нумерацию, и тогда операция отказывает.
    Устойчивое именование берётся из прослеживания топологии FreeCAD и
    подключается отдельно; здесь оно названо, а не выдано за готовое.
    """

    document_id: str
    body_id: str
    kind: str = "fillet"          # "fillet" | "chamfer"
    edges: list = field(default_factory=list)   # номера рёбер тела
    #: Устойчивые имена тех же рёбер, по одному на номер. Главнее номеров:
    #: номер годен ровно до правки выше по дереву.
    edge_names: list = field(default_factory=list)
    #: Грани, ВСЕ рёбра которых надо обработать. Разворачивает движок: он
    #: держит форму, и какие рёбра у грани — знает точно. По
    #: спецификации поле «Объекты скругления» принимает и грань.
    faces: list = field(default_factory=list)
    face_names: list = field(default_factory=list)
    size: float = 3.0             # радиус скругления либо катет фаски
    #: Тип ФАСКИ. У скругления не используется: `PartDesign::Fillet` умеет
    #: только постоянный радиус (остальное записано пробелами).
    #: "equal" — равные катеты, "two" — два расстояния, "angle" —
    #: расстояние и угол. Значения совпадают с тем, что понимает движок.
    mode: str = "equal"
    size2: float = 1.0
    angle_deg: float = 45.0
    #: Поменять местами стороны: какой катет на какой грани.
    flip: bool = False
    feature_id: str = ""
    preview: bool = False
    revision: int = 0
    session_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DressUpRequest":
        return cls(**dict(data))


@dataclass
class RevolveRequest:
    """Вращение профиля вокруг оси.

    Ось задаётся направлением и точкой в координатах детали, а не именем
    объекта движка: имена — его внутреннее дело, и завязывать на них
    интерфейс значило бы переносить в ProtoCAD устройство FreeCAD.
    """

    document_id: str
    body_id: str
    profile: "Profile" = field(default_factory=lambda: Profile())
    angle_deg: float = 360.0
    axis: tuple = (0.0, 1.0, 0.0)
    axis_origin: tuple = (0.0, 0.0, 0.0)
    reversed: bool = False
    midplane: bool = False
    #: Прибавить к телу или снять с него — как у выдавливания.
    subtract: bool = False
    preview: bool = False
    feature_id: str = ""
    revision: int = 0
    session_id: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["profile"] = self.profile.to_dict()
        data["axis"] = list(self.axis)
        data["axis_origin"] = list(self.axis_origin)
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "RevolveRequest":
        data = dict(data)
        data["profile"] = Profile.from_dict(data.get("profile"))
        return cls(**data)


@dataclass
class HoleRequest:
    """Отверстие по эскизу: положения берутся из окружностей профиля.

    Отдельная операция, а не вырез круглым контуром: у отверстия есть
    диаметр, глубина, зенковка и резьба — то, что на чертеже пишется одной
    строкой, и разбирать эту строку обратно из формы никто не станет.
    """

    document_id: str
    body_id: str
    profile: "Profile" = field(default_factory=lambda: Profile())
    diameter: float = 8.0
    depth: float = 0.0            # 0 — насквозь
    through_all: bool = True
    #: Дно глухого отверстия. По умолчанию коническое — такое и оставляет
    #: сверло, и на чертеже оно так и показывается. Плоское дно бывает у
    #: цековки и у отверстия под фрезу, и просить его надо явно: молча
    #: спрямив дно, мы нарисовали бы деталь, которую сверлом не сделать.
    flat_bottom: bool = False
    counterbore: float = 0.0      # диаметр цековки, 0 — без неё
    counterbore_depth: float = 0.0
    reversed: bool = False
    preview: bool = False
    feature_id: str = ""
    revision: int = 0
    session_id: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        data["profile"] = self.profile.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "HoleRequest":
        data = dict(data)
        data["profile"] = Profile.from_dict(data.get("profile"))
        return cls(**data)


@dataclass
class DraftRequest:
    """Уклон: выбранные грани наклоняются на угол.

    ``neutral`` — грань, которая при этом остаётся на месте: наклон идёт
    вокруг линии её пересечения с наклоняемой. Без неё уклон неопределён —
    поворачивать грань можно вокруг любой прямой на ней.
    """

    document_id: str
    body_id: str
    faces: list = field(default_factory=list)    # номера наклоняемых граней
    face_names: list = field(default_factory=list)
    #: Нейтральная грань: номер в теле и устойчивое имя.
    neutral: int = -1
    neutral_name: str = ""
    angle_deg: float = 1.0
    #: Куда идёт материал. Ложь — внутрь (деталь худеет), истина — наружу.
    outward: bool = False
    preview: bool = False
    feature_id: str = ""
    revision: int = 0
    session_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "DraftRequest":
        return cls(**dict(data))


@dataclass
class ShellRequest:
    """Оболочка: выбранные грани снимаются, остальное становится стенкой.

    Грани называются номерами в текущем теле — как и рёбра у обработки, и
    с той же оговоркой: это не топологические имена.
    """

    document_id: str
    body_id: str
    faces: list = field(default_factory=list)   # номера граней тела
    #: Устойчивые имена тех же граней, по одному на номер.
    face_names: list = field(default_factory=list)
    thickness: float = 2.0
    #: Внутрь тела (обычный случай) или наружу.
    inward: bool = True
    preview: bool = False
    feature_id: str = ""
    revision: int = 0
    session_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ShellRequest":
        return cls(**dict(data))


@dataclass
class PatternRequest:
    """Массив операций: линейный, круговой или зеркало.

    Размножаются ОПЕРАЦИИ, а не грани готового тела: массив отверстий —
    это те же отверстия, и при правке диаметра меняются все сразу. Массив
    по граням пришлось бы пересобирать после каждой правки выше по дереву.
    """

    document_id: str
    body_id: str
    kind: str = "linear"          # "linear" | "polar" | "mirror"
    #: Какие операции размножать. Пусто — отказ: у FreeCAD пустой массив
    #: строится успешно и ничего не делает.
    features: list = field(default_factory=list)
    #: Размножать тело ЦЕЛИКОМ, а не перечисленные операции. Так работает
    #: обычное «зеркало детали»: отражается всё построенное, и перечислять
    #: операции по одной не надо.
    whole_shape: bool = False
    count: int = 2
    #: Линейный: направление и шаг между соседними.
    direction: tuple = (1.0, 0.0, 0.0)
    spacing: float = 20.0
    #: Чем задан ряд: ``spacing`` — шагом между соседними, ``extent`` —
    #: общей длиной, на которую их надо разложить. Это разные задачи:
    #: «через каждые 20 мм» и «уложить пять штук на 80 мм», — и подменять
    #: одно другим нельзя, счёт разойдётся на последнем экземпляре.
    mode: str = "spacing"
    length: float = 100.0
    #: Второе направление. ``count2 <= 1`` — второго ряда нет вовсе.
    direction2: tuple = (0.0, 1.0, 0.0)
    spacing2: float = 20.0
    count2: int = 1
    mode2: str = "spacing"
    length2: float = 100.0
    reversed2: bool = False
    #: Круговой: ось, точка на ней и полный угол.
    axis: tuple = (0.0, 0.0, 1.0)
    axis_origin: tuple = (0.0, 0.0, 0.0)
    angle_deg: float = 360.0
    #: Круговой: разложить `count` штук РАВНОМЕРНО по `angle_deg` (истина)
    #: или ставить через `angle_step` градусов (ложь).
    equal_spacing: bool = True
    angle_step: float = 30.0
    #: Какие экземпляры НЕ строить. Номера с нуля, ноль — сам исходник, и
    #: пропустить его нельзя: пропадёт то, что размножают.
    skip: list = field(default_factory=list)
    #: Зеркало: нормаль плоскости и точка на ней.
    plane_normal: tuple = (1.0, 0.0, 0.0)
    plane_origin: tuple = (0.0, 0.0, 0.0)
    reversed: bool = False
    preview: bool = False
    feature_id: str = ""
    revision: int = 0
    session_id: str = ""

    def to_dict(self) -> dict:
        data = asdict(self)
        for key in ("direction", "axis", "axis_origin", "plane_normal",
                    "plane_origin"):
            data[key] = list(getattr(self, key))
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "PatternRequest":
        return cls(**dict(data))


@dataclass
class HoleToolRequest:
    """Отверстия одной операции: N инструментов, ОДИН вычет.

    Инструмент приходит ОСЕВЫМ КОНТУРОМ — замкнутой ломаной
    ``(радиус, глубина)`` — и точкой с направлением оси. Тело вращения по
    контуру и есть снимаемое. Так через границу идут числа, а не формы
    (§10.2), и одна и та же ломаная описывает и простое отверстие, и
    цековку с зенковкой с двух сторон.

    Все экземпляры уходят ОДНИМ запросом: в дереве это одна операция на
    сколько угодно отверстий (`docs/09_HOLES.md`, §2.1), и вычитаются они
    тоже за один раз — компаундом (§36.11).
    """

    document_id: str
    body_id: str = "Тело"
    #: [{"contour": [[r, z], …], "origin": [x, y, z], "axis": [x, y, z]}]
    instances: list = field(default_factory=list)
    preview: bool = False
    feature_id: str = ""
    revision: int = 0
    session_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "HoleToolRequest":
        return cls(**dict(data))


@dataclass
class SectionRequest:
    """След детали на плоскости — «кривая пересечения».

    Плоскость задаётся точкой и нормалью в координатах ДЕТАЛИ: движок не
    обязан знать про наши плоскости эскиза (§10.2), как и профиль.

    Без указания тела режутся ВСЕ тела документа — как у сцены. Показывать
    след одного тела там, где деталь состоит из нескольких, значило бы
    молча потерять часть контура.
    """

    document_id: str
    body_id: str = ""
    origin: tuple = (0.0, 0.0, 0.0)
    normal: tuple = (0.0, 0.0, 1.0)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "SectionRequest":
        return cls(**dict(data))


@dataclass
class SectionResult:
    """Рёбра следа — тем же описанием, что и рёбра детали.

    Описание общее не для красоты: по нему уже умеет строить перенос рёбер
    в эскиз (`sketch/convert.py`). Свой вид кривых означал бы второй разбор
    того же самого, и «Кривая пересечения» расходилась бы с
    «Преобразовать ребро» на одной и той же окружности.

    Пустой список — законный ответ: плоскость детали не задела.
    """

    status: Status = Status.VALID
    edges: list = field(default_factory=list)
    diagnostics: list = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == Status.VALID

    @property
    def message(self) -> str:
        for item in self.diagnostics:
            if item.severity == "error":
                return item.message
        return ""


@dataclass
class Diagnostic:
    """Отказ или предупреждение движка, пригодные к показу человеку.

    ``code`` для программы, ``message`` для человека, ``arguments`` — какие
    поля панели подсветить. Без последнего сообщение приходится читать и
    догадываться, что править.
    """

    code: str
    message: str
    severity: str = "error"
    arguments: list = field(default_factory=list)
    backend: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Mesh:
    """Треугольники для вьюпорта. Плоские списки — их проще передать.

    Номера граней идут рядом с вершинами: без них по картинке нельзя
    сказать, какая грань под указателем, а выбор грани — основа работы.
    """

    positions: list = field(default_factory=list)   # по три числа на вершину
    normals: list = field(default_factory=list)
    face_ids: list = field(default_factory=list)    # по одному на вершину
    edge_positions: list = field(default_factory=list)
    edge_ids: list = field(default_factory=list)
    #: Номер ТЕЛА для каждой вершины и для каждой точки рёбер. В детали
    #: тел бывает несколько — несвязанные между собой, — и вид обязан их
    #: различать: выбрать одно из них щелчком иначе нельзя.
    body_ids: list = field(default_factory=list)
    edge_body_ids: list = field(default_factory=list)

    @property
    def triangles(self) -> int:
        return len(self.positions) // 9

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EntityRef:
    """Ссылка на подэлемент вместе с его описанием.

    Номер грани в обходе меняется при любой правке дерева, поэтому ссылка
    называет происхождение, а не место в списке.

    ``data`` — геометрия подэлемента в виде чисел: для плоской грани
    начало, нормаль и направление оси X; для прямого ребра концы; для
    окружности центр, нормаль и радиус. Без этого окну пришлось бы
    разбирать формы ядра самому — то есть загрузить вторую сборку OCCT в
    свой процесс, чего делать нельзя (§16.3).

    ``index`` — порядковый номер в сетке. Он связывает описание с тем, что
    вернул выбор мышью, и живёт ровно до следующего пересчёта.
    """

    id: str
    kind: str = "face"
    feature: str = ""
    origin: str = ""
    index: int = -1
    #: Тело, которому принадлежит подэлемент. Номер и имя считаются в его
    #: пределах: у второго тела своя нумерация и своя карта.
    body: str = ""
    #: Устойчивое имя из карты элементов движка. Оно переживает правку
    #: выше по дереву; номер — нет. Хранить в операции надо ЕГО (§26:
    #: ссылка не может быть одним лишь `Face6`). Пусто — движок карты не
    #: ведёт, и остаётся номер.
    name: str = ""
    data: dict = field(default_factory=dict)

    @property
    def planar(self) -> bool:
        return self.data.get("surface") == "plane"

    @property
    def plane(self) -> tuple:
        """(начало, нормаль, ось X) плоской грани. Пусто — грань неплоская."""
        if not self.planar:
            return ()
        return (tuple(self.data["origin"]), tuple(self.data["normal"]),
                tuple(self.data["x_direction"]))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FeatureResult:
    """Ответ на предпросмотр или применение операции."""

    status: Status = Status.VALID
    session_id: str = ""
    revision: int = 0
    feature_id: str = ""
    mesh: Mesh | None = None
    volume: float = 0.0
    bounds: list = field(default_factory=list)      # xmin,ymin,zmin,xmax,ymax,zmax
    entities: list = field(default_factory=list)    # EntityRef
    #: Сетка ТОЛЬКО того, что операция добавляет или снимает. Нужна
    #: предпросмотру: показывать всё тело поверх него самого бесполезно —
    #: не видно, что именно операция сделает. ``None`` — движок не выделил.
    tool_mesh: "Mesh | None" = None
    #: Какой стороной операция в итоге построена. Возвращается наружу,
    #: потому что сторону мог выбрать не человек, а правило разворота, — и
    #: при следующем пересчёте её надо повторить, а не искать заново.
    reversed: bool = False
    #: Что показывает `tool_mesh`: ``"added"`` — прирастающий материал,
    #: ``"removed"`` — снимаемый. Окно красит их по-разному: обещать
    #: вырез цветом прилива значит показать противоположное.
    tool_kind: str = ""
    diagnostics: list = field(default_factory=list)  # Diagnostic

    @property
    def display_mesh(self) -> "Mesh | None":
        """Что показывать: инструмент, если он есть, иначе тело.

        Спрашивать надо ЗДЕСЬ, а не выбирать между двумя полями самому. У
        предпросмотра тело может не прийти вовсе: окно рисует инструмент
        поверх уже построенной детали, и слать ей её же копию — это
        половина ответа впустую (на решете из тридцати отверстий 3,45 МБ
        текста). Кто возьмёт `mesh` напрямую, однажды получит `None` и не
        покажет ничего — молча.
        """
        return self.tool_mesh if self.tool_mesh is not None else self.mesh

    @property
    def ok(self) -> bool:
        return self.status == Status.VALID

    @property
    def message(self) -> str:
        """Первое сообщение об отказе. Пустая строка — отказа нет."""
        for item in self.diagnostics:
            if item.severity == "error":
                return item.message
        return ""

    @property
    def code(self) -> str:
        """Код первого отказа. Пустая строка — отказа нет.

        Разбирать причину по тексту сообщения нельзя: текст обращён к
        человеку и меняется вместе с формулировкой, код — нет.
        """
        for item in self.diagnostics:
            if item.severity == "error":
                return item.code
        return ""

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "sessionId": self.session_id,
            "revision": self.revision,
            "featureId": self.feature_id,
            "mesh": self.mesh.to_dict() if self.mesh else None,
            "volume": self.volume,
            "bounds": list(self.bounds),
            "entities": [item.to_dict() for item in self.entities],
            "toolMesh": self.tool_mesh.to_dict() if self.tool_mesh else None,
            "toolKind": self.tool_kind,
            "reversed": self.reversed,
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }


@dataclass
class Capabilities:
    """Что умеет движок. Спрашивается один раз при подключении.

    Панель операции строится по этому списку, а не по предположению: если
    движок не умеет «до последней грани», кнопки быть не должно — иначе
    человек упрётся в отказ там, где ему обещали возможность.
    """

    backend: str = ""
    version: str = ""
    protocol: tuple = PROTOCOL
    features: list = field(default_factory=list)
    #: Концевые условия ПО ОПЕРАЦИЯМ, а не общим списком. У прилива и
    #: выреза наборы разные: FreeCAD 1.1 даёт приливу «до последней», а
    #: вырезу «насквозь», и ни одного из них нет у второго. Общий список
    #: прятал бы обе возможности, а объединение обещало бы кнопку,
    #: работающую через раз.
    end_conditions: dict = field(default_factory=dict)
    notes: str = ""

    def supports(self, name: str) -> bool:
        return name in self.features

    def ends_for(self, feature: str) -> list:
        return list(self.end_conditions.get(feature) or ())

    def compatible(self) -> bool:
        """Совпадает ли старшая часть версии протокола."""
        return tuple(self.protocol)[0] == PROTOCOL[0]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["protocol"] = {"major": self.protocol[0], "minor": self.protocol[1]}
        return data


def error(code: str, message: str, arguments=None, backend: str = "") -> FeatureResult:
    """Готовый отказ. Чтобы отказ выглядел одинаково у любого движка."""
    return FeatureResult(
        status=Status.INVALID,
        diagnostics=[Diagnostic(code, message, "error",
                                list(arguments or ()), backend)],
    )
