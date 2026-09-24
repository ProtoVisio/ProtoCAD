"""3D-вьюпорт на PySide6 и OpenGL.

Вся сцена — один буфер GPU, кадр — два вызова отрисовки. Пикинг сделан
отрисовкой идентификаторов в цвет с чтением пикселя: тот же единственный
вызов, другой фрагментный шейдер.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np
from OpenGL import GL
from PySide6 import QtCore, QtGui
from PySide6.QtOpenGLWidgets import QOpenGLWidget

VERTEX_SHADER = """
#version 330 core
layout(location = 0) in vec3 in_position;
layout(location = 1) in vec3 in_normal;
layout(location = 2) in uint in_id;
layout(location = 3) in uint in_face;
layout(location = 4) in uint in_group;
uniform mat4 mvp;
uniform mat3 normal_matrix;
uniform int mode;
out vec3 v_normal;
out vec2 v_uv;
flat out uint v_id;
flat out uint v_face;
flat out uint v_group;
void main() {
    if (mode == 4) {
        // Фон: треугольник во весь экран, построенный из номера вершины.
        // Своего буфера ему не нужно — три вершины дешевле любого VBO.
        vec2 corner = vec2(float((gl_VertexID << 1) & 2), float(gl_VertexID & 2));
        v_uv = corner;
        v_normal = vec3(0.0, 0.0, 1.0);
        v_id = 0u; v_face = 0u; v_group = 0u;
        gl_Position = vec4(corner * 2.0 - 1.0, 0.999, 1.0);
        return;
    }
    v_normal = normalize(normal_matrix * in_normal);
    v_uv = vec2(0.0);
    v_id = in_id;
    v_face = in_face;
    v_group = in_group;
    gl_Position = mvp * vec4(in_position, 1.0);
}
"""

FRAGMENT_SHADER = """
#version 330 core
in vec3 v_normal;
in vec2 v_uv;
flat in uint v_id;
flat in uint v_face;
// Набор граней, созданных выбранной операцией. Одним номером его не
// выразить: операция почти всегда порождает несколько граней, а иногда
// десятки — как массив отверстий.
flat in uint v_group;
// 0 — заливка, 1 — пикинг тел, 2 — рёбра, 3 — пикинг граней
uniform int mode;
uniform uint highlight_id;     // тело целиком
uniform uint highlight_face;   // одна грань
uniform uint hover_face;       // грань под указателем
uniform vec3 base_color;
uniform float dim;             // приглушение детали в режиме эскиза
uniform vec3 sky;              // фон вверху
uniform vec3 ground;           // фон внизу
uniform float opacity;         // 1 — непрозрачно; меньше — предпросмотр
// Цвета наборов граней. Номер набора N красится palette[N - 1]; первый —
// прежний цвет «граней операции», поэтому окно детали выглядит как было.
uniform vec3 palette[10];
out vec4 frag_color;
void main() {
    if (mode == 4) {
        frag_color = vec4(mix(ground, sky, clamp(v_uv.y, 0.0, 1.0)), 1.0);
        return;
    }
    // Скрытое тело не рисуется и не выбирается — ни заливкой, ни рёбрами,
    // ни в картинке выбора.
    if (v_group == 255u) discard;
    bool body_selected = (v_id == highlight_id && highlight_id != 0u);
    bool face_selected = (v_face == highlight_face && highlight_face != 0u);
    bool face_hover = (v_face == hover_face && hover_face != 0u);
    if (mode == 2) {
        if (v_group != 0u) { frag_color = vec4(0.98, 0.60, 0.12, 1.0); return; }
        frag_color = body_selected ? vec4(0.98, 0.60, 0.12, 1.0)
                                   : vec4(0.10, 0.11, 0.13, 1.0);
        return;
    }
    if (mode == 1 || mode == 3) {
        uint id = (mode == 1) ? v_id : v_face;
        frag_color = vec4(float(id & 0xFFu) / 255.0,
                          float((id >> 8) & 0xFFu) / 255.0,
                          float((id >> 16) & 0xFFu) / 255.0, 1.0);
        return;
    }
    // Свет в пространстве ВИДА, из двух частей. Одного направленного
    // источника не хватает: под каким угол его ни поставь, найдётся
    // положение камеры, где видимая грань смотрит от него и деталь
    // становится чёрной. Прежний свет шёл снизу и гасил всё, на что
    // смотрели сверху; это вскрылось, когда изометрия начала честно
    // смотреть сверху, а не снизу.
    //
    // Вторая часть — подсветка от зрителя. Грань, повёрнутая к камере,
    // освещена по определению, поэтому чёрной деталь не станет ни при
    // каком повороте. Первая часть даёт светотень, без которой форма
    // читается плоско.
    vec3 key = normalize(vec3(-0.35, 0.5, 0.78));
    vec3 unit_normal = normalize(v_normal);
    float shape = max(dot(unit_normal, key), 0.0);
    float facing = max(unit_normal.z, 0.0);
    float diffuse = 0.55 * facing + 0.45 * shape;

    vec3 base = base_color;
    // Порядок — это старшинство: подсветка операции слабее наведения, а
    // наведение слабее выбора. Иначе выбранная грань пропадала бы под
    // цветом операции, которая её создала.
    if (v_group != 0u)  base = palette[(v_group - 1u) % 10u];
    if (body_selected)  base = vec3(1.0, 0.72, 0.24);
    if (face_hover)     base = vec3(0.45, 0.78, 0.55);
    if (face_selected)  base = vec3(0.30, 0.62, 0.92);
    vec3 shaded = base * (0.30 + 0.70 * diffuse);
    // Приглушение — не украшение: в режиме эскиза деталь обязана уйти на
    // второй план, иначе тонкие линии эскиза на ней не читаются. На светлом
    // фоне приглушать надо В СТОРОНУ ФОНА, а не к серому: иначе деталь не
    // отступает назад, а становится грязным пятном.
    // Ограничение обязательно: без него старший канал цвета на самых
    // светлых гранях уходил за единицу и заворачивался в ноль — верх
    // жёлтого предпросмотра выходил бирюзовым (242 → 2 по красному, а
    // зелёный и синий на месте). Отказа при этом нет, есть чужой цвет на
    // части граней.
    frag_color = vec4(clamp(mix(shaded, mix(ground, sky, 0.5), dim),
                            0.0, 1.0), clamp(opacity, 0.0, 1.0));
}
"""


#: Цвета наборов граней. Первый — прежний цвет «граней операции». Синий
#: выбора и зелёный наведения сюда не входят: группа не должна выглядеть
#: выбранной. Девятый — выбор нескольких граней, десятый — замечания.
PALETTE = (
    (0.98, 0.72, 0.38),   # оранжевый
    (0.66, 0.50, 0.90),   # фиолетовый
    (0.90, 0.42, 0.42),   # красный
    (0.30, 0.74, 0.74),   # бирюзовый
    (0.94, 0.86, 0.40),   # жёлтый
    (0.95, 0.56, 0.76),   # розовый
    (0.72, 0.54, 0.36),   # коричневый
    (0.60, 0.72, 0.30),   # оливковый
    (0.30, 0.62, 0.92),   # выбор
    (0.96, 0.20, 0.20),   # замечание
)
SELECTION_COLOUR = 9
PROBLEM_COLOUR = 10
#: Признак «тело скрыто» в буфере признаков: шейдер такие точки отбрасывает.
HIDDEN = 255


@dataclass
class SceneBuffers:
    """Готовая к загрузке геометрия. Источник не важен — форма важна."""

    positions: np.ndarray
    normals: np.ndarray
    ids: np.ndarray
    edge_positions: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 3), np.float32)
    )
    edge_ids: np.ndarray = field(default_factory=lambda: np.zeros(0, np.uint32))
    labels: dict = field(default_factory=dict)
    # Второй уровень адресации — грани. Пустой массив допустим: данные из
    # ПРОТО его не несут, и вид обязан работать без него.
    face_ids: np.ndarray = field(default_factory=lambda: np.zeros(0, np.uint32))
    face_labels: dict = field(default_factory=dict)
    vertex_positions: np.ndarray = field(
        default_factory=lambda: np.zeros((0, 3), np.float32)
    )
    edge_indices: np.ndarray = field(default_factory=lambda: np.zeros(0, np.uint32))

    @property
    def triangle_count(self) -> int:
        return len(self.positions) // 3

    @property
    def edge_count(self) -> int:
        return len(self.edge_positions) // 2

    @property
    def empty(self) -> bool:
        return len(self.positions) == 0

    @classmethod
    def from_preview(cls, preview) -> "SceneBuffers":
        """Из ``protocad.preview.Preview`` или из данных ``protocad_reader``."""
        labels = {}
        for key, value in (getattr(preview, "id_to_object", None) or {}).items():
            labels[int(key)] = (
                value.get("label", "") if isinstance(value, dict) else str(value)
            )
        face_labels = {}
        for key, value in (getattr(preview, "face_to_object", None) or {}).items():
            face_labels[int(key)] = value
        return cls(
            positions=preview.positions,
            normals=preview.normals,
            ids=preview.ids,
            edge_positions=getattr(
                preview, "edge_positions", np.zeros((0, 3), np.float32)
            ),
            edge_ids=getattr(preview, "edge_ids", np.zeros(0, np.uint32)),
            labels=labels,
            face_ids=getattr(preview, "face_ids", np.zeros(0, np.uint32)),
            face_labels=face_labels,
            vertex_positions=getattr(
                preview, "vertex_positions", np.zeros((0, 3), np.float32)
            ),
            edge_indices=getattr(preview, "edge_indices", np.zeros(0, np.uint32)),
        )

    @classmethod
    def from_engine(cls, mesh, body: int = 1) -> "SceneBuffers":
        """Из сетки, пришедшей от движка (``protocad.engine.protocol.Mesh``).

        Вьюпорт перестаёт разбирать формы ядра сам: он получает готовые
        треугольники, номера граней и рёбра. Это и есть то, ради чего
        граница движка сделана данными — вьюпорт не обязан знать, какая
        сборка OCCT их посчитала, и не должен её загружать (§16.3, §22.1).

        Номер тела берётся ИЗ СЕТКИ, если движок его прислал: в детали
        тел бывает несколько, и различать их обязан вид — иначе выбрать
        одно из них щелчком нельзя. Пустой список означает одно тело, и
        тогда номер тот, что передали.
        """
        positions = np.asarray(mesh.positions, np.float32).reshape(-1, 3)
        normals = np.asarray(mesh.normals, np.float32).reshape(-1, 3)
        # Номер грани в картинке выбора СДВИНУТ на единицу: ноль там
        # означает «пусто», и грань №0 иначе была бы неотличима от фона.
        # Обратно он переводится через ``face_labels``, как и у данных из
        # ПРОТО, — вьюпорту не надо знать, кто их посчитал.
        faces = np.asarray(mesh.face_ids, np.uint32) + 1
        edges = np.asarray(mesh.edge_positions, np.float32).reshape(-1, 3)
        edge_ids = np.asarray(mesh.edge_ids, np.uint32)
        # Вершины для привязки берутся из концов рёбер: отдельного списка
        # движок не шлёт, а концы у него точные — это те же точки ядра.
        if len(edges):
            vertices = np.unique(np.round(edges, 6), axis=0).astype(np.float32)
        else:
            vertices = np.zeros((0, 3), np.float32)
        told = np.asarray(getattr(mesh, "body_ids", ()) or (), np.uint32)
        ids = (told if len(told) == len(positions)
               else np.full(len(positions), body, np.uint32))
        told_edges = np.asarray(getattr(mesh, "edge_body_ids", ()) or (),
                                np.uint32)
        edge_owner = (told_edges if len(told_edges) == len(edges)
                      else np.full(len(edges), body, np.uint32))
        labels = {int(value): (f"Тело {value}" if len(set(ids.tolist())) > 1
                               else "Деталь")
                  for value in np.unique(ids)} or {body: "Деталь"}
        return cls(
            positions=positions,
            normals=normals,
            ids=ids,
            edge_positions=edges,
            edge_ids=edge_owner,
            labels=labels,
            face_ids=faces,
            face_labels={int(value): {"label": f"Грань {int(value)}",
                                      "face": int(value) - 1}
                         for value in np.unique(faces)},
            vertex_positions=vertices,
            edge_indices=edge_ids,
        )

    @classmethod
    def empty_scene(cls) -> "SceneBuffers":
        return cls(
            positions=np.zeros((0, 3), np.float32),
            normals=np.zeros((0, 3), np.float32),
            ids=np.zeros(0, np.uint32),
        )


def _closest_on_segment(start, end, ray_origin, ray_direction) -> np.ndarray:
    """Точка отрезка, ближайшая к лучу. Классическая задача о двух прямых;
    вырожденный случай (луч вдоль отрезка) даёт середину, а не отказ."""
    segment = np.asarray(end, np.float64) - np.asarray(start, np.float64)
    offset = np.asarray(start, np.float64) - np.asarray(ray_origin, np.float64)
    direction = np.asarray(ray_direction, np.float64)
    a = segment @ segment
    b = segment @ direction
    c = direction @ direction
    d = segment @ offset
    e = direction @ offset
    denominator = a * c - b * b
    if abs(denominator) < 1e-12 or a < 1e-12:
        t = 0.5
    else:
        t = (b * e - c * d) / denominator
    t = min(1.0, max(0.0, t))
    return (np.asarray(start, np.float64) + segment * t).astype(float)


def _perspective(fov: float, aspect: float, near: float, far: float) -> np.ndarray:
    f = 1.0 / np.tan(np.radians(fov) / 2.0)
    matrix = np.zeros((4, 4), np.float32)
    matrix[0, 0] = f / max(aspect, 1e-6)
    matrix[1, 1] = f
    matrix[2, 2] = (far + near) / (near - far)
    matrix[2, 3] = (2 * far * near) / (near - far)
    matrix[3, 2] = -1.0
    return matrix


def _orthographic(half_height: float, aspect: float, near: float, far: float):
    """Ортогональная проекция — та, в которой работают.

    В перспективе сверху видно стенки отверстий, а две одинаковые детали на
    разной глубине выходят разного размера. Для чертёжной работы это не
    украшение, а помеха: глаз меряет по экрану.
    """
    half_width = max(half_height * aspect, 1e-6)
    matrix = np.zeros((4, 4), np.float32)
    matrix[0, 0] = 1.0 / half_width
    matrix[1, 1] = 1.0 / max(half_height, 1e-6)
    matrix[2, 2] = -2.0 / max(far - near, 1e-6)
    matrix[2, 3] = -(far + near) / max(far - near, 1e-6)
    matrix[3, 3] = 1.0
    return matrix


def _over_pole(yaw: float, pitch: float):
    """Свести азимут и наклон к обычному виду, пройдя через полюс.

    Наклон держится в пределах ±90°. Перевалив за 90°, камера не
    «задирается дальше», а переходит на другую сторону: наклон
    отражается, азимут разворачивается на 180°, верх экрана
    переворачивается. Без этого стрелка вверх доводила деталь до вида
    сверху и дальше картинка дёргалась — вращение переставало быть
    непрерывным ровно там, где его чаще всего и продолжают.

    Возвращает (азимут, наклон, надо ли перевернуть верх).
    """
    flip = False
    while pitch > 90.0 or pitch < -90.0:
        pitch = (180.0 - pitch) if pitch > 90.0 else (-180.0 - pitch)
        yaw += 180.0
        flip = not flip
    return yaw % 360.0, pitch, flip


def _rolled(up, direction, degrees: float):
    """Вектор верха, повёрнутый вокруг направления взгляда (формула Родрига)."""
    if abs(degrees) < 1e-9:
        return up
    axis = np.asarray(direction, np.float32)
    length = float(np.linalg.norm(axis))
    if length < 1e-9:
        return up
    axis = axis / length
    angle = np.radians(degrees)
    up = np.asarray(up, np.float32)
    return (up * np.cos(angle)
            + np.cross(axis, up) * np.sin(angle)
            + axis * float(np.dot(axis, up)) * (1.0 - np.cos(angle))).astype(np.float32)


def _look_at(eye, target, up) -> np.ndarray:
    forward = target - eye
    forward = forward / max(np.linalg.norm(forward), 1e-9)
    side = np.cross(forward, up)
    if np.linalg.norm(side) < 1e-3:
        # Взгляд вдоль «верха»: поворот камеры вырожден, и «право» экрана
        # определяется остатком в тысячные — вид разворачивается на
        # произвольный угол. Сверху пластина 80 × 120 показывалась шире,
        # чем выше. Берём другую ось: любую, лишь бы не совпадала.
        for candidate in ((0.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)):
            side = np.cross(forward, np.array(candidate, np.float32))
            if np.linalg.norm(side) > 1e-3:
                break
    side = side / max(np.linalg.norm(side), 1e-9)
    true_up = np.cross(side, forward)
    matrix = np.eye(4, dtype=np.float32)
    matrix[0, :3], matrix[1, :3], matrix[2, :3] = side, true_up, -forward
    matrix[:3, 3] = -matrix[:3, :3] @ eye
    return matrix


class Viewport(QOpenGLWidget):
    """Вид сцены: вращение, приближение, панорама, выбор тела."""

    #: масштаб вида, когда показывать ещё нечего, мм
    EMPTY_RADIUS = 50.0

    picked = QtCore.Signal(int)
    # Что выбрано щелчком: словарь с ключом "kind" — "body", "face",
    # "edge", "vertex" или "none". Сигнал общий, а не четыре разных: тот,
    # кто слушает, обычно ждёт «что-нибудь», и разбирать это удобнее в
    # одном месте.
    selected = QtCore.Signal(object)
    #: Камера сдвинулась: угол поворота и наклон в градусах. Нужен кубику
    #: видов, чтобы показывать то же положение, что и деталь.
    camera_moved = QtCore.Signal(float, float)

    def __init__(self, scene: SceneBuffers | None = None, parent=None):
        super().__init__(parent)
        self.scene = scene or SceneBuffers.empty_scene()
        self.show_edges = True
        self.base_color = (0.62, 0.66, 0.70)
        # Светлый градиент вместо тёмного поля. Так принято во всех
        # инженерных пакетах, и не ради красоты: тонкие линии эскиза и рёбра
        # на светлом читаются, а размеры и подписи не приходится делать
        # ядовитыми, чтобы их было видно.
        self.sky = (0.87, 0.90, 0.94)
        self.ground = (0.99, 0.99, 1.00)
        #: цвет незавершённой операции — она обязана отличаться от детали
        # Цвет предпросмотра — тёплый жёлтый, как принято в отрасли:
        # он не спорит с серым металлом детали и читается на нём даже
        # просвечивая.
        self.preview_color = (0.93, 0.85, 0.35)
        #: Насколько предпросмотр непрозрачен. Сквозь него обязана быть
        #: видна деталь: иначе непонятно, что операция с ней сделает.
        self.preview_opacity = 0.55
        # Перспектива по умолчанию ВЫКЛЮЧЕНА. Она нужна для показа, а не
        # для работы: сверху в ней видны стенки отверстий, и на экране
        # ничего нельзя померить глазом.
        self.perspective = False
        self.highlight_id = 0
        self.highlight_face = 0
        self.hover_face = 0
        self.face_group: set[int] = set()
        #: Цвет по грани: {номер грани в сцене: номер цвета 1..10}. Так
        #: подготовка к расчёту красит группы — у каждой свой цвет.
        self.face_colors: dict = {}
        #: Цвет по телу: {номер тела: номер цвета}. Так окно сборки выделяет
        #: вхождение целиком и красит детали по видам — одной таблицей на
        #: тело, а не перечнем тысяч граней. Цвет грани старше цвета тела.
        self.body_colors: dict = {}
        #: Скрытые тела. Скрываются признаком в буфере признаков, а не
        #: пересборкой сцены: геометрия в видеопамяти остаётся как есть.
        self.hidden_bodies: set = set()
        self.palette = list(PALETTE)
        self.edge_selection: set[int] = set()
        # Что считается «верхом» экрана. По умолчанию мировая Z, но при
        # взгляде почти вертикально вниз она почти совпадает с направлением
        # взгляда, и «право» экрана определяется остатком в сотые доли —
        # вид разворачивается на произвольный угол. В режиме эскиза сюда
        # кладётся вторая ось плоскости, и чертёж лежит как нарисован.
        self.up_hint = np.array([0.0, 0.0, 1.0], np.float32)
        #: Крен — поворот вокруг направления взгляда, третья степень
        #: свободы камеры. Без него вид нельзя поставить произвольно.
        self.roll = 0.0
        #: Перевалили ли за полюс. Наклон при этом остаётся в пределах
        #: ±90°, а азимут разворачивается — так вращение стрелками идёт
        #: НЕПРЕРЫВНО в одну сторону экрана, без рывка на полюсе.
        self.upside = False
        self.dim = 0.0
        # Что можно выбирать щелчком. Пустой набор — выбор выключен: в
        # режиме эскиза щелчок принадлежит эскизу, а не детали.
        self.pick_kinds = ("body",)
        self.fps = 0.0

        self._program = None
        self._face_vao = self._edge_vao = None
        # Предпросмотр — ОТДЕЛЬНЫЙ слой. Замена им основной сцены выглядит
        # так же, но ломает выбор: щелчок попадает в ребро предпросмотра, а
        # операция ищет его в исходном теле, и номера не совпадают —
        # выбирается не то, что показано.
        self.preview_scene = None
        self._preview_vao = None
        # Пустой массив вершин для фона: ядро OpenGL требует привязанный
        # VAO даже там, где данных нет.
        self._empty_vao = None
        self._buffers: list = []
        self._dirty = True
        #: Изменились только признаки подсветки (цвета наборов, выбранные
        #: рёбра). Перезаливать ради них всю геометрию нельзя: на плате в
        #: тысячи тел это сотни мегабайт на каждый щелчок — ровно то, от
        #: чего тормозят сборки плат. Обновляется один маленький буфер.
        self._flags_dirty = False
        self._face_flag_buffer = None
        self._edge_flag_buffer = None
        self._last_pos = None
        self._press_pos = None
        # Кто перехватывает мышь вместо вида. В режиме эскиза это эскиз:
        # рисовать и вращать деталь одновременно нельзя, и решать, кому
        # принадлежит нажатие, должен тот, кто знает режим.
        #
        # Наложения через QPainter внутри paintGL здесь НЕТ намеренно.
        # Приём распространённый, но смешивание QPainter с собственными
        # вызовами GL на этой видеокарте портит состояние контекста:
        # выбор граней переставал работать, а снятие кадра роняло процесс.
        # Эскиз накладывается обычным прозрачным дочерним виджетом — Qt
        # совмещает его сам и в наш контекст не лезет.
        self.controller = None
        # Виджет наложения. Он рисуется поверх кадра средствами Qt и сам о
        # движении камеры не знает: Qt перерисовывает дочерний виджет только
        # когда тот объявлен недействительным. Без этой связи эскиз остаётся
        # нарисованным для ПРЕЖНЕГО положения камеры — деталь под ним
        # поворачивается и уезжает, а контур висит на старом месте.
        self.overlay_widget = None
        self._last_camera = None
        self._pan = np.zeros(3, np.float32)
        self._frames, self._since = 0, time.perf_counter()
        self.setMinimumSize(480, 360)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self._fit()

        timer = QtCore.QTimer(self)
        timer.timeout.connect(self.update)
        timer.start(16)

    # --- сцена ---

    def set_scene(self, scene: SceneBuffers) -> None:
        """Заменить геометрию. Буферы перезаливаются при следующем кадре."""
        keep_view = not self.scene.empty and not scene.empty
        self.scene = scene
        self._dirty = True
        self.highlight_id = 0
        self.highlight_face = 0
        self.hover_face = 0
        self.face_group = set()
        self.face_colors = {}
        self.body_colors = {}
        self.hidden_bodies = set()
        self.edge_selection = set()
        if not keep_view:
            self._pan[:] = 0.0
        self._fit(keep_orientation=keep_view)
        self.update()

    def _fit(self, keep_orientation: bool = False) -> None:
        if self.scene.empty:
            self.center = np.zeros(3, np.float32)
            # Пустая сцена — это НОВАЯ ДЕТАЛЬ, а не точка. Радиус в один
            # миллиметр давал вид шириной в пару миллиметров: нарисовать в
            # нём прямоугольник 80 × 50 было нельзя, щелчок уходил за край
            # экрана. Полсотни миллиметров — размер обычной детали.
            self.radius = self.EMPTY_RADIUS
        else:
            low = self.scene.positions.min(axis=0)
            high = self.scene.positions.max(axis=0)
            self.center = ((low + high) / 2).astype(np.float32)
            self.radius = float(np.linalg.norm(high - low) / 2) or 1.0
        if not keep_orientation:
            self.yaw, self.pitch, self.zoom = 45.0, 28.0, 1.0

    def fit_view(self) -> None:
        self._pan[:] = 0.0
        self._fit()
        self.update()

    # --- GPU ---

    def initializeGL(self) -> None:
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glClearColor(0.16, 0.17, 0.19, 1.0)
        self._program = GL.glCreateProgram()
        for source, kind in (
            (VERTEX_SHADER, GL.GL_VERTEX_SHADER),
            (FRAGMENT_SHADER, GL.GL_FRAGMENT_SHADER),
        ):
            shader = GL.glCreateShader(kind)
            GL.glShaderSource(shader, source)
            GL.glCompileShader(shader)
            if not GL.glGetShaderiv(shader, GL.GL_COMPILE_STATUS):
                raise RuntimeError(GL.glGetShaderInfoLog(shader).decode())
            GL.glAttachShader(self._program, shader)
        GL.glLinkProgram(self._program)
        if not GL.glGetProgramiv(self._program, GL.GL_LINK_STATUS):
            raise RuntimeError(GL.glGetProgramInfoLog(self._program).decode())

    def _upload(self) -> None:
        """Перезалить буферы. Старые освобождаются, иначе память течёт при
        каждом пересчёте модели."""
        if self._buffers:
            GL.glDeleteBuffers(len(self._buffers), self._buffers)
            self._buffers = []
        for vao in (self._face_vao, self._edge_vao, self._preview_vao):
            if vao:
                GL.glDeleteVertexArrays(1, [vao])
        self._face_vao = self._edge_vao = self._preview_vao = None

        self._face_flag_buffer = self._edge_flag_buffer = None
        if not self.scene.empty:
            self._face_vao = self._make_vao(
                self.scene.positions, self.scene.normals, self.scene.ids,
                self.scene.face_ids, self._group_flags(),
            )
            self._face_flag_buffer = self._buffers[-1]
        if len(self.scene.edge_positions):
            self._edge_vao = self._make_vao(
                self.scene.edge_positions,
                np.zeros_like(self.scene.edge_positions),
                self.scene.edge_ids,
                np.zeros(len(self.scene.edge_positions), np.uint32),
                self._edge_flags(),
            )
            self._edge_flag_buffer = self._buffers[-1]
        if self.preview_scene is not None and not self.preview_scene.empty:
            self._preview_vao = self._make_vao(
                self.preview_scene.positions, self.preview_scene.normals,
                np.zeros(len(self.preview_scene.positions), np.uint32),
                np.zeros(len(self.preview_scene.positions), np.uint32),
                np.zeros(len(self.preview_scene.positions), np.uint32),
            )
        self._dirty = False
        self._flags_dirty = False

    def _upload_flags(self) -> None:
        """Перезалить ТОЛЬКО признаки подсветки — геометрия остаётся в
        видеопамяти как была."""
        for buffer, flags in ((self._face_flag_buffer, self._group_flags),
                              (self._edge_flag_buffer, self._edge_flags)):
            if buffer is None:
                continue
            data = np.ascontiguousarray(flags(), dtype=np.uint32)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, buffer)
            GL.glBufferSubData(GL.GL_ARRAY_BUFFER, 0, data.nbytes, data)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        self._flags_dirty = False

    def _group_flags(self) -> np.ndarray:
        """Номер цвета набора для каждой вершины; ноль — своего цвета нет.

        Грани выбранной операции (``face_group``) — первый цвет и старше
        раскраски групп: подсветка того, что человек выбрал сейчас, не
        должна тонуть в цвете группы.
        """
        count = len(self.scene.positions)
        flags = np.zeros(count, np.uint32)
        if self.body_colors and len(self.scene.ids) == count:
            flags = _painted(self.scene.ids, self.body_colors)
        if len(self.scene.face_ids) != count:
            return self._hide(flags, self.scene.ids)
        if self.face_colors:
            painted = _painted(self.scene.face_ids, self.face_colors)
            flags = np.where(painted > 0, painted, flags).astype(np.uint32)
        if self.face_group:
            wanted = np.fromiter(self.face_group, np.uint32, len(self.face_group))
            flags[np.isin(self.scene.face_ids, wanted)] = 1
        return self._hide(flags, self.scene.ids)

    def _edge_flags(self) -> np.ndarray:
        count = len(self.scene.edge_positions)
        if not self.edge_selection or len(self.scene.edge_indices) != count:
            flags = np.zeros(count, np.uint32)
        else:
            wanted = np.fromiter(self.edge_selection, np.uint32, len(self.edge_selection))
            flags = np.isin(self.scene.edge_indices, wanted).astype(np.uint32)
        return self._hide(flags, self.scene.edge_ids)

    def _hide(self, flags: np.ndarray, owners: np.ndarray) -> np.ndarray:
        if not self.hidden_bodies or len(owners) != len(flags):
            return flags
        wanted = np.fromiter(self.hidden_bodies, np.uint32, len(self.hidden_bodies))
        flags = np.array(flags, np.uint32, copy=True)
        flags[np.isin(owners, wanted)] = HIDDEN
        return flags

    def set_hidden_bodies(self, bodies) -> None:
        """Скрыть тела (номера тел сцены). Пустой набор — показать все."""
        bodies = set(int(value) for value in bodies or ())
        if bodies == self.hidden_bodies:
            return
        self.hidden_bodies = bodies
        self._flags_dirty = True
        self.update()

    def set_edge_selection(self, indices) -> None:
        """Подсветить выбранные рёбра."""
        chosen = set(int(value) for value in (indices or ()))
        if chosen == self.edge_selection:
            return
        self.edge_selection = chosen
        self._flags_dirty = True
        self.update()

    def set_preview_scene(self, scene) -> None:
        """Показать предпросмотр поверх детали. None — убрать.

        Слой не участвует в выборе: во время команды указывают на
        настоящую деталь, а не на её будущий вид.
        """
        self.preview_scene = scene
        self._dirty = True
        self.update()

    def set_face_group(self, face_ids) -> None:
        """Подсветить набор граней — те, что создала выбранная операция.

        Признак кладётся в буфер и перезаливается: сравнивать в шейдере с
        набором нечем, а перезаливка одного байтового массива на выбор в
        дереве незаметна.
        """
        group = set(int(value) for value in (face_ids or ()))
        if group == self.face_group:
            return
        self.face_group = group
        self._flags_dirty = True
        self.update()

    def set_face_colors(self, colours) -> None:
        """Раскрасить грани: {номер грани в сцене: номер цвета 1..10}."""
        colours = {int(face): int(value) for face, value in (colours or {}).items()
                   if int(value) > 0}
        if colours == self.face_colors:
            return
        self.face_colors = colours
        self._flags_dirty = True
        self.update()

    def set_body_colors(self, colours) -> None:
        """Цвета тел: {номер тела: номер цвета 1..10}. Пустой — снять."""
        colours = dict(colours or {})
        if colours == self.body_colors:
            return
        self.body_colors = colours
        self._flags_dirty = True
        self.update()

    def _make_vao(self, positions, normals, ids, face_ids=None, groups=None):
        vao = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(vao)
        # Номера граней могут не прийти вовсе: данные из ПРОТО их не несут.
        # Нули означают «граней нет», и выбор граней просто не срабатывает.
        if face_ids is None or len(face_ids) != len(positions):
            face_ids = np.zeros(len(positions), np.uint32)
        if groups is None or len(groups) != len(positions):
            groups = np.zeros(len(positions), np.uint32)
        buffers = GL.glGenBuffers(5)
        self._buffers.extend(buffers)
        for index, (array, size, gl_type) in enumerate(
            (
                (positions, 3, GL.GL_FLOAT),
                (normals, 3, GL.GL_FLOAT),
                (ids, 1, GL.GL_UNSIGNED_INT),
                (face_ids, 1, GL.GL_UNSIGNED_INT),
                (groups, 1, GL.GL_UNSIGNED_INT),
            )
        ):
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, buffers[index])
            GL.glBufferData(
                GL.GL_ARRAY_BUFFER,
                array.nbytes,
                np.ascontiguousarray(array),
                GL.GL_STATIC_DRAW,
            )
            GL.glEnableVertexAttribArray(index)
            if gl_type == GL.GL_UNSIGNED_INT:
                GL.glVertexAttribIPointer(index, size, gl_type, 0, None)
            else:
                GL.glVertexAttribPointer(index, size, gl_type, GL.GL_FALSE, 0, None)
        GL.glBindVertexArray(0)
        return vao

    def resizeGL(self, width: int, height: int) -> None:
        GL.glViewport(0, 0, width, height)

    def _camera_key(self) -> tuple:
        """Всё, от чего зависят матрицы. Иного состояния камеры нет."""
        return (
            float(self.yaw), float(self.pitch), float(self.roll),
            float(self.zoom), float(self.radius),
            bool(self.perspective), bool(self.flipped),
            self.width(), self.height(),
            self.center.tobytes(), self._pan.tobytes(),
            self.up_hint.tobytes(),
        )

    def _matrices(self):
        """Матрицы вида и проекции. Считаются раз на положение камеры.

        Считались они на КАЖДЫЙ спроецированный пункт: показ эскиза
        проецирует по одной точке, и на решете из тридцати отверстий
        матрицы пересчитывались больше ста тысяч раз за восемь шагов —
        шестнадцать секунд из двадцати двух уходило сюда. Камера при этом
        не двигалась ни разу.

        Память держится по СОСТОЯНИЮ камеры, а не сбрасывается вручную:
        поля камеры меняют отовсюду, и забытый сброс дал бы картинку,
        отставшую от камеры, — это хуже медленной.
        """
        key = self._camera_key()
        memo = getattr(self, "_matrix_memo", None)
        if memo is not None and memo[0] == key:
            return memo[1]
        distance = self.radius * 2.6 * self.zoom
        yaw = np.radians(self.yaw)
        # Наклон НЕ ограничивается: он проходит через полюс и идёт дальше.
        # Ровно на полюсе вектор верха вырождается — это ловит _look_at.
        pitch = np.radians(self.pitch)
        target = self.center + self._pan
        eye = target + np.array(
            [
                np.cos(pitch) * np.cos(yaw) * distance,
                np.cos(pitch) * np.sin(yaw) * distance,
                np.sin(pitch) * distance,
            ],
            np.float32,
        )
        # За полюсом верх переворачивается: без этого картинка встаёт вверх
        # ногами ровно в тот момент, когда наклон переваливает за 90°.
        upward = -self.up_hint if self.flipped else self.up_hint
        view = _look_at(eye, target, _rolled(upward, target - eye, self.roll))
        aspect = max(self.width(), 1) / max(self.height(), 1)
        near, far = self.radius * 0.02, self.radius * 30.0
        if self.perspective:
            projection = _perspective(35.0, aspect, near, far)
        else:
            # Половина высоты кадра подобрана так, чтобы при том же
            # приближении деталь занимала столько же места, сколько в
            # перспективе: переключение не должно менять масштаб.
            projection = _orthographic(
                self.radius * 1.35 * self.zoom, aspect, near, far
            )
        result = ((projection @ view).astype(np.float32),
                  view[:3, :3].astype(np.float32))
        self._matrix_memo = (key, result)
        return result

    def _prepare_state(self) -> None:
        """Вернуть состояние GL, на которое рассчитана отрисовка.

        Наложение рисуется через QPainter, а он оставляет после себя своё:
        включённые отсечение и смешивание, выключенный тест глубины. Дальше
        это выглядит как «выбор граней перестал работать после того, как
        показали эскиз» — и искать причину приходится не там, где она.
        Дешевле выставлять нужное каждый кадр, чем полагаться на то, что
        никто ничего не менял.
        """
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glDepthFunc(GL.GL_LESS)
        GL.glDepthMask(GL.GL_TRUE)
        GL.glDisable(GL.GL_SCISSOR_TEST)
        GL.glDisable(GL.GL_BLEND)
        GL.glDisable(GL.GL_STENCIL_TEST)
        GL.glDisable(GL.GL_CULL_FACE)
        # Мало ВЫКЛЮЧИТЬ смешивание — надо вернуть и то, КАК оно считается.
        # QPainter под свои режимы наложения ставит своё уравнение, и оно
        # переживает выключение: следующий же полупрозрачный слой рисуется
        # не сложением, а вычитанием. На экране это выглядит как клинья
        # чужого цвета в предпросмотре — появляются при одних размерах и
        # пропадают при других, будто «мигает видеокарта».
        GL.glBlendEquation(GL.GL_FUNC_ADD)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        ratio = self.devicePixelRatio()
        GL.glViewport(0, 0, int(self.width() * ratio), int(self.height() * ratio))

    def _draw_background(self) -> None:
        """Градиент во весь кадр. Рисуется до сцены и не пишет глубину."""
        if self._empty_vao is None:
            self._empty_vao = GL.glGenVertexArrays(1)
        GL.glUseProgram(self._program)
        GL.glUniform1i(GL.glGetUniformLocation(self._program, "mode"), 4)
        GL.glUniform3f(GL.glGetUniformLocation(self._program, "sky"), *self.sky)
        GL.glUniform3f(GL.glGetUniformLocation(self._program, "ground"), *self.ground)
        GL.glDepthMask(GL.GL_FALSE)
        GL.glBindVertexArray(self._empty_vao)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        GL.glBindVertexArray(0)
        GL.glDepthMask(GL.GL_TRUE)

    def _draw_preview(self) -> None:
        """Слой предпросмотра — поверх детали, своим цветом, ПРОСВЕЧИВАЯ.

        Непрозрачный слой закрывал деталь, и по картинке нельзя было
        понять, куда именно прирастёт материал: видно было только его
        собственную форму.
        """
        if not self._preview_vao:
            return
        self._prepare_state()
        GL.glEnable(GL.GL_BLEND)
        # Цвет смешивается по прозрачности, а альфа-канал буфера остаётся
        # ЕДИНИЦЕЙ. Раздельные множители здесь не тонкость: буфер кадра у
        # виджета хранится с домноженной альфой (`ARGB32_Premultiplied`), и
        # записанная в него 0.55 делает пиксель недопустимым — канал цвета
        # оказывается больше альфы. Дальше всякий, кто СОБИРАЕТ виджет в
        # картинку, получает заворот старшего канала: жёлтый предпросмотр
        # выходил бирюзовым. На экране это не видно, потому что там
        # композиции нет, — зато видно на любом снимке и на прозрачных
        # оформлениях окна.
        GL.glBlendFuncSeparate(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA,
                               GL.GL_ZERO, GL.GL_ONE)
        # Глубина ЧИТАЕТСЯ, но не пишется: слой ложится поверх того, что
        # ближе к камере, и не заслоняет сам себя изнутри.
        GL.glDepthMask(GL.GL_FALSE)
        mvp, normal_matrix = self._matrices()
        GL.glUseProgram(self._program)
        GL.glUniformMatrix4fv(
            GL.glGetUniformLocation(self._program, "mvp"), 1, GL.GL_TRUE, mvp
        )
        GL.glUniformMatrix3fv(
            GL.glGetUniformLocation(self._program, "normal_matrix"),
            1, GL.GL_TRUE, normal_matrix,
        )
        GL.glUniform1i(GL.glGetUniformLocation(self._program, "mode"), 0)
        GL.glUniform1ui(GL.glGetUniformLocation(self._program, "highlight_id"), 0)
        GL.glUniform1ui(GL.glGetUniformLocation(self._program, "highlight_face"), 0)
        GL.glUniform1ui(GL.glGetUniformLocation(self._program, "hover_face"), 0)
        GL.glUniform1f(GL.glGetUniformLocation(self._program, "dim"), 0.0)
        GL.glUniform3f(
            GL.glGetUniformLocation(self._program, "base_color"), *self.preview_color
        )
        GL.glUniform3f(GL.glGetUniformLocation(self._program, "sky"), *self.sky)
        GL.glUniform3f(GL.glGetUniformLocation(self._program, "ground"), *self.ground)
        GL.glUniform1f(GL.glGetUniformLocation(self._program, "opacity"),
                       float(self.preview_opacity))
        GL.glBindVertexArray(self._preview_vao)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, len(self.preview_scene.positions))
        GL.glBindVertexArray(0)
        GL.glUniform1f(GL.glGetUniformLocation(self._program, "opacity"), 1.0)
        GL.glDepthMask(GL.GL_TRUE)
        GL.glDisable(GL.GL_BLEND)

    def _draw(self, mode: int) -> None:
        self._prepare_state()
        mvp, normal_matrix = self._matrices()
        GL.glUseProgram(self._program)
        GL.glUniformMatrix4fv(
            GL.glGetUniformLocation(self._program, "mvp"), 1, GL.GL_TRUE, mvp
        )
        GL.glUniformMatrix3fv(
            GL.glGetUniformLocation(self._program, "normal_matrix"),
            1, GL.GL_TRUE, normal_matrix,
        )
        GL.glUniform1ui(
            GL.glGetUniformLocation(self._program, "highlight_id"), self.highlight_id
        )
        GL.glUniform1ui(
            GL.glGetUniformLocation(self._program, "highlight_face"), self.highlight_face
        )
        GL.glUniform1ui(
            GL.glGetUniformLocation(self._program, "hover_face"), self.hover_face
        )
        GL.glUniform1f(GL.glGetUniformLocation(self._program, "dim"), self.dim)
        # Деталь непрозрачна всегда: прозрачность заведена ради
        # предпросмотра, и без явного сброса она осталась бы от него.
        GL.glUniform1f(GL.glGetUniformLocation(self._program, "opacity"), 1.0)
        GL.glUniform3f(GL.glGetUniformLocation(self._program, "sky"), *self.sky)
        GL.glUniform3f(GL.glGetUniformLocation(self._program, "ground"), *self.ground)
        GL.glUniform3f(
            GL.glGetUniformLocation(self._program, "base_color"), *self.base_color
        )
        GL.glUniform3fv(GL.glGetUniformLocation(self._program, "palette"),
                        len(self.palette),
                        np.asarray(self.palette, np.float32).reshape(-1))
        GL.glUniform1i(GL.glGetUniformLocation(self._program, "mode"), mode)

        if self._face_vao:
            GL.glBindVertexArray(self._face_vao)
            if mode == 0:
                # Заливка отодвигается вглубь, иначе рёбра мерцают на гранях.
                GL.glEnable(GL.GL_POLYGON_OFFSET_FILL)
                GL.glPolygonOffset(1.0, 1.0)
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, len(self.scene.positions))
            if mode == 0:
                GL.glDisable(GL.GL_POLYGON_OFFSET_FILL)
            GL.glBindVertexArray(0)

        if mode == 0 and self.show_edges and self._edge_vao:
            GL.glUniform1i(GL.glGetUniformLocation(self._program, "mode"), 2)
            GL.glBindVertexArray(self._edge_vao)
            GL.glDrawArrays(GL.GL_LINES, 0, len(self.scene.edge_positions))
            GL.glBindVertexArray(0)

    def paintGL(self) -> None:
        if self._dirty:
            self._upload()
        elif self._flags_dirty:
            self._upload_flags()
        # Состояние восстанавливается ДО очистки: с оставшимся от QPainter
        # отсечением очистка глубины обрезается, и следующий кадр
        # сравнивается с мусором в буфере.
        self._prepare_state()
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        self._draw_background()
        self._draw(0)
        self._draw_preview()
        self._sync_overlay()
        self._frames += 1
        elapsed = time.perf_counter() - self._since
        if elapsed >= 0.5:
            self.fps = self._frames / elapsed
            self._frames, self._since = 0, time.perf_counter()

    def _camera_state(self) -> tuple:
        return (
            round(self.yaw, 4), round(self.pitch, 4), round(self.roll, 4),
            round(self.zoom, 6),
            tuple(round(float(value), 4) for value in self._pan),
            tuple(round(float(value), 4) for value in self.center),
            round(float(self.radius), 4), self.width(), self.height(),
        )

    def _sync_overlay(self) -> None:
        """Перерисовать наложение, если камера сдвинулась."""
        state = self._camera_state()
        if state == self._last_camera:
            return
        self._last_camera = state
        if self.overlay_widget is not None:
            self.overlay_widget.update()
        self.camera_moved.emit(self.yaw, self.pitch)

    # --- управление ---

    def mousePressEvent(self, event) -> None:
        if self.controller is not None and self.controller.handle("press", event):
            self.update()
            return
        self.apply_press(event)

    def apply_press(self, event) -> None:
        """Нажатие для КАМЕРЫ, минуя контроллер.

        Отдельный вход нужен эскизу: он перехватывает мышь, но среднюю
        кнопку отдаёт виду. Вызов открытого обработчика вернул бы
        событие обратно в контроллер — и так до переполнения стека.
        """
        self._last_pos = event.position()
        self._press_pos = event.position()
        if event.button() == QtCore.Qt.RightButton:
            self.picked.emit(self.pick(event.position()))

    def mouseMoveEvent(self, event) -> None:
        if self.controller is not None and self.controller.handle("move", event):
            self.update()
            return
        self.apply_move(event)

    def apply_move(self, event) -> None:
        if not (event.buttons() & (QtCore.Qt.LeftButton | QtCore.Qt.MiddleButton)):
            self._hover(event.position())
            return
        if self._last_pos is None:
            return
        delta = event.position() - self._last_pos
        self._last_pos = event.position()
        modifiers = event.modifiers()
        middle = bool(event.buttons() & QtCore.Qt.MiddleButton)
        # Средняя кнопка ВРАЩАЕТ, с Ctrl — панорамирует, с Shift —
        # приближает. Так устроено в отраслевых системах, и рука к этому
        # привыкла раньше, чем к любому нашему решению. Левая кнопка тоже
        # вращает: в модели ею всё равно нечего делать, кроме выбора,
        # который срабатывает на щелчке без протяжки.
        if middle and (modifiers & QtCore.Qt.ShiftModifier):
            self.zoom = max(0.05, min(20.0, self.zoom * (1.0 + delta.y() * 0.004)))
        elif middle and (modifiers & QtCore.Qt.ControlModifier):
            self.pan_by(-delta.x(), delta.y())
        elif middle or (event.buttons() & QtCore.Qt.LeftButton):
            self.rotate_by(-delta.x() * 0.4, delta.y() * 0.4)

    def rotate_by(self, yaw: float, pitch: float) -> None:
        """Повернуть камеру. Наклон идёт по кругу, а не упирается в отвес.

        Упор означал, что стрелками вверх и вниз деталь доводится до вида
        сверху и дальше не идёт — перевернуть её и посмотреть снизу тем же
        движением нельзя. Теперь наклон проходит через полюс: за 90°
        камера переваливает на другую сторону, азимут разворачивается на
        180°, а вектор верха переворачивается вместе с ней — иначе
        картинка в этот момент дёрнулась бы вверх ногами.
        """
        # Когда камера перевалила за полюс, экран перевёрнут: движение
        # «вверх» соответствует уже обратному знаку наклона. Без этого
        # стрелка вверх упиралась в полюс и начинала прыгать туда-сюда
        # между 90° и 75° вместо того, чтобы вести деталь дальше.
        if self.upside:
            yaw, pitch = -yaw, -pitch
        self.yaw, self.pitch, flip = _over_pole(
            self.yaw + yaw, self.pitch + pitch)
        if flip:
            self.upside = not self.upside
        self.camera_changed()

    @property
    def flipped(self) -> bool:
        """Смотрим ли мы из-за полюса. Тогда верх экрана — противоположный."""
        return self.upside

    def roll_by(self, degrees: float) -> None:
        """Повернуть камеру ВОКРУГ направления взгляда.

        Без этого деталь нельзя поставить в произвольное положение: азимут
        и наклон дают только две степени свободы из трёх, и, например,
        поставить ребро строго горизонтально удаётся лишь случайно.
        """
        self.roll = (self.roll + degrees) % 360.0
        self.camera_changed()

    def pan_by(self, dx: float, dy: float) -> None:
        """Сдвинуть вид ПО ЭКРАНУ.

        Раньше сдвиг шёл по мировым осям X и Z с нулевой составляющей по Y:
        под любым поворотом, кроме исходного, деталь уезжала не туда, куда
        вела рука, а сверху не двигалась вовсе.
        """
        _, rotation = self._matrices()
        right = np.array(rotation[0], np.float32)
        up = np.array(rotation[1], np.float32)
        scale = self.radius * self.zoom * 0.0035
        self._pan += (right * dx + up * dy) * scale
        self.camera_changed()

    def camera_changed(self) -> None:
        self.update()

    #: На сколько градусов поворачивают стрелки клавиатуры.
    ARROW_STEP = 15.0

    def keyPressEvent(self, event) -> None:
        """Стрелки вращают деталь, как в отраслевых системах.

        Мышью точный угол не берётся: рука всегда промахивается на
        несколько градусов, и вернуться к прежнему положению нельзя.
        Стрелка даёт ровный шаг.
        """
        if self.controller is not None and self.controller.handle("key", event):
            self.update()
            return
        if event.key() == QtCore.Qt.Key_E:
            self.show_edges = not self.show_edges
            self.update()
            return
        if event.key() == QtCore.Qt.Key_F:
            self.fit_view()
            return
        step = self.ARROW_STEP
        modifiers = event.modifiers()
        if modifiers & QtCore.Qt.ShiftModifier:
            step = 90.0
        key = event.key()
        if modifiers & QtCore.Qt.AltModifier and key in (
                QtCore.Qt.Key_Left, QtCore.Qt.Key_Right):
            self.roll_by(step if key == QtCore.Qt.Key_Right else -step)
            return
        if modifiers & QtCore.Qt.ControlModifier and key in (
                QtCore.Qt.Key_Left, QtCore.Qt.Key_Right,
                QtCore.Qt.Key_Up, QtCore.Qt.Key_Down):
            shift = {QtCore.Qt.Key_Left: (-1, 0), QtCore.Qt.Key_Right: (1, 0),
                     QtCore.Qt.Key_Up: (0, 1), QtCore.Qt.Key_Down: (0, -1)}[key]
            self.pan_by(shift[0] * 30.0, shift[1] * 30.0)
            return
        turns = {
            QtCore.Qt.Key_Left: (-step, 0.0),
            QtCore.Qt.Key_Right: (step, 0.0),
            QtCore.Qt.Key_Up: (0.0, -step),
            QtCore.Qt.Key_Down: (0.0, step),
        }
        if key in turns:
            self.rotate_by(*turns[key])
            return
        super().keyPressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self.controller is not None and self.controller.handle("release", event):
            self.update()
            return
        self.apply_release(event)

    def apply_release(self, event) -> None:
        if event.button() != QtCore.Qt.LeftButton or self._press_pos is None:
            return
        moved = event.position() - self._press_pos
        self._press_pos = None
        # Щелчок и вращение — одна и та же кнопка, и различает их только
        # сдвиг. Порог маленький: за 4 пикселя чертёж не повернуть, а рука
        # при щелчке всегда дрожит на пару.
        if abs(moved.x()) + abs(moved.y()) > 4.0:
            return
        self._select_at(event.position(), event.modifiers())

    def mouseDoubleClickEvent(self, event) -> None:
        if self.controller is not None and self.controller.handle("double", event):
            self.update()

    def _hover(self, position) -> None:
        if "face" not in self.pick_kinds or self.scene.empty:
            if self.hover_face:
                self.hover_face = 0
                self.update()
            return
        found = self.pick_face(position)
        if found != self.hover_face:
            self.hover_face = found
            self.update()

    def _select_at(self, position, modifiers) -> None:
        """Что выбрано щелчком. Порядок проверок = порядок точности:
        точка точнее ребра, ребро точнее грани."""
        if "vertex" in self.pick_kinds:
            point = self.pick_vertex(position)
            if point is not None:
                self.selected.emit({"kind": "vertex", "point": tuple(point)})
                return
        if "edge" in self.pick_kinds:
            found = self.pick_edge(position)
            if found is not None:
                index, point = found
                self.selected.emit({
                    "kind": "edge", "index": index, "point": tuple(point),
                })
                return
        if "face" in self.pick_kinds:
            identifier = self.pick_face(position)
            if identifier:
                self.highlight_face = identifier
                self.selected.emit({
                    "kind": "face",
                    "id": identifier,
                    "index": self.face_index_of(identifier),
                    "label": self.face_label_of(identifier),
                })
                self.update()
                return
        if "body" in self.pick_kinds:
            identifier = self.pick(position)
            self.highlight_id = identifier
            self.picked.emit(identifier)
            self.selected.emit({"kind": "body", "id": identifier,
                                "label": self.label_of(identifier)})
            self.update()
            return
        self.selected.emit({"kind": "none"})

    def wheelEvent(self, event) -> None:
        if self.controller is not None and self.controller.handle("wheel", event):
            self.update()
            return
        self.apply_wheel(event)

    def apply_wheel(self, event) -> None:
        factor = 0.88 if event.angleDelta().y() > 0 else 1.14
        self.zoom = max(0.02, min(12.0, self.zoom * factor))

    def pick(self, position) -> int:
        """Идентификатор тела под точкой; 0 — пусто."""
        return self._pick_id(position, 1)

    def pick_face(self, position) -> int:
        """Идентификатор грани под точкой; 0 — пусто."""
        if len(self.scene.face_ids) == 0:
            return 0
        return self._pick_id(position, 3)

    def _pick_id(self, position, mode: int) -> int:
        if self.scene.empty:
            return 0
        self.makeCurrent()
        # Признаки (скрытые тела, раскраска) доливаются и здесь, а не только
        # в кадре: щелчок сразу после «скрыть» иначе выбирал бы по прежним
        # признакам — скрытое тело перехватывало щелчок.
        if self._dirty:
            self._upload()
        elif self._flags_dirty:
            self._upload_flags()
        self._prepare_state()
        GL.glClearColor(0.0, 0.0, 0.0, 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        self._draw(mode)
        ratio = self.devicePixelRatio()
        pixel = GL.glReadPixels(
            int(position.x() * ratio),
            int((self.height() - position.y()) * ratio),
            1, 1, GL.GL_RGB, GL.GL_UNSIGNED_BYTE,
        )
        GL.glClearColor(0.16, 0.17, 0.19, 1.0)
        values = np.frombuffer(pixel, np.uint8)
        return int(values[0]) | (int(values[1]) << 8) | (int(values[2]) << 16)

    # --- проекция и выбор точек и рёбер ---

    def project(self, points: np.ndarray) -> np.ndarray:
        """Точки детали → экранные координаты и глубина.

        Возвращает массив (x, y, w). Отрицательное w означает точку позади
        камеры — такие в выбор не идут, иначе объект за спиной «попадает»
        под указатель.
        """
        mvp, _ = self._matrices()
        points = np.atleast_2d(np.asarray(points, np.float32))
        homogeneous = np.hstack([points, np.ones((len(points), 1), np.float32)])
        clip = homogeneous @ mvp.T
        w = clip[:, 3]
        safe = np.where(np.abs(w) < 1e-9, 1e-9, w)
        ndc = clip[:, :3] / safe[:, None]
        screen = np.empty((len(points), 3), np.float32)
        screen[:, 0] = (ndc[:, 0] * 0.5 + 0.5) * self.width()
        screen[:, 1] = (0.5 - ndc[:, 1] * 0.5) * self.height()
        # Третий столбец — признак «точка перед камерой», а не глубина. В
        # перспективе это w, в ортогональной проекции w всегда единица, и
        # признаком служит попадание в отсечение по глубине.
        if self.perspective:
            screen[:, 2] = w
        else:
            screen[:, 2] = np.where(np.abs(ndc[:, 2]) <= 1.0, 1.0, -1.0)
        return screen

    def project_point(self, x: float, y: float, z: float) -> tuple:
        """Одна точка детали → ``(x, y, перед_камерой)`` на экране.

        Отдельно от `project` не ради удобства: показ эскиза проецирует
        ТОЧКАМИ, по одной, и numpy на одном элементе стоит вдесятеро
        дороже самой арифметики — вся цена в сборке массива из одного
        числа. На решете из тридцати отверстий сюда уходило 720 мс на
        каждое изменение числа в панели, больше, чем в движок.

        Считает то же самое и той же матрицей, что и `project`: второй
        формулы проецирования в программе быть не должно — разойдутся, и
        нарисованное перестанет совпадать с выбираемым.
        """
        memo = getattr(self, "_flat_memo", None)
        key = self._camera_key()
        if memo is None or memo[0] != key:
            mvp, _ = self._matrices()
            memo = (key, [float(value) for value in mvp.flat])
            self._flat_memo = memo
        m = memo[1]
        cx = m[0] * x + m[1] * y + m[2] * z + m[3]
        cy = m[4] * x + m[5] * y + m[6] * z + m[7]
        cz = m[8] * x + m[9] * y + m[10] * z + m[11]
        w = m[12] * x + m[13] * y + m[14] * z + m[15]
        safe = w if abs(w) >= 1e-9 else 1e-9
        ndc_x, ndc_y, ndc_z = cx / safe, cy / safe, cz / safe
        ahead = w if self.perspective else (1.0 if abs(ndc_z) <= 1.0 else -1.0)
        return ((ndc_x * 0.5 + 0.5) * self.width(),
                (0.5 - ndc_y * 0.5) * self.height(), ahead)

    def ray(self, position) -> tuple[np.ndarray, np.ndarray]:
        """Луч от камеры через точку экрана: начало и направление."""
        mvp, _ = self._matrices()
        inverse = np.linalg.inv(mvp.astype(np.float64))
        x = position.x() / max(self.width(), 1) * 2.0 - 1.0
        y = 1.0 - position.y() / max(self.height(), 1) * 2.0
        near = inverse @ np.array([x, y, -1.0, 1.0])
        far = inverse @ np.array([x, y, 1.0, 1.0])
        near = near[:3] / near[3]
        far = far[:3] / far[3]
        direction = far - near
        length = np.linalg.norm(direction)
        return near.astype(np.float32), (direction / max(length, 1e-9)).astype(np.float32)

    def pick_vertex(self, position, radius: float = 10.0):
        """Ближайшая вершина детали к точке экрана. None — не попали.

        Вершины ищутся в экранных координатах, а не лучом: попасть лучом в
        точку невозможно, а мерить расстояние на экране — ровно то, что
        делает человек, целясь мышью.
        """
        return self._nearest_point(self.scene.vertex_positions, position, radius)

    def pick_edge_point(self, position, radius: float = 8.0):
        """Ближайшая точка НА рёбрах детали. None — не попали.

        Считается расстояние до отрезка, а не до его концов. У прямого
        ребра концов всего два: поиск по точкам находил бы ребро только у
        самых углов, а посередине — нигде.
        """
        segments = self.scene.edge_positions
        if len(segments) < 2:
            return None
        pairs = segments[: len(segments) // 2 * 2].reshape(-1, 2, 3)
        screen = self.project(pairs.reshape(-1, 3)).reshape(-1, 2, 3)
        starts, ends = screen[:, 0, :2], screen[:, 1, :2]
        visible = (screen[:, 0, 2] > 0) & (screen[:, 1, 2] > 0)
        if not visible.any():
            return None

        target = np.array([position.x(), position.y()], np.float32)
        direction = ends - starts
        length_squared = (direction ** 2).sum(axis=1)
        length_squared[length_squared == 0] = 1e-9
        t = ((target - starts) * direction).sum(axis=1) / length_squared
        t = np.clip(t, 0.0, 1.0)
        projected = starts + direction * t[:, None]
        distances = np.linalg.norm(projected - target, axis=1)
        distances[~visible] = np.inf
        index = int(np.argmin(distances))
        if distances[index] > radius:
            return None
        # Экран выбирает ОТРЕЗОК, но не точку на нём: доля вдоль отрезка на
        # экране при перспективе не равна доле в пространстве, и обратный
        # перенос давал ошибку в миллиметры. Саму точку считаем в
        # пространстве — ближайшей к лучу взгляда.
        return _closest_on_segment(pairs[index, 0], pairs[index, 1], *self.ray(position))

    def pick_edge(self, position, radius: float = 8.0):
        """Ребро под указателем: его номер и точка на нём. None — мимо.

        Номер нужен операциям: скругление по «точке где-то на ребре»
        задать нельзя, а по ребру — можно.
        """
        point = self.pick_edge_point(position, radius)
        if point is None:
            return None
        indices = self.scene.edge_indices
        if len(indices) != len(self.scene.edge_positions):
            return None
        # Ищем отрезок, которому принадлежит найденная точка: он же и дал её.
        pairs = self.scene.edge_positions.reshape(-1, 2, 3)
        target = np.asarray(point, np.float32)
        span = np.linalg.norm(pairs[:, 0] - target, axis=1) + np.linalg.norm(
            pairs[:, 1] - target, axis=1
        ) - np.linalg.norm(pairs[:, 1] - pairs[:, 0], axis=1)
        segment = int(np.argmin(span))
        return int(indices[segment * 2]), point

    #: Насколько близко точки должны сойтись на экране, чтобы спор между
    #: ними решала глубина, пикселей.
    _SAME_SPOT = 2.0

    def _toward(self) -> np.ndarray:
        """Единичный вектор от детали К КАМЕРЕ.

        Чем больше проекция точки на него, тем точка ближе к зрителю.
        """
        yaw = np.radians(self.yaw)
        pitch = np.radians(self.pitch)
        return np.array([
            np.cos(pitch) * np.cos(yaw),
            np.cos(pitch) * np.sin(yaw),
            np.sin(pitch),
        ], np.float64)

    def _nearest_point(self, points: np.ndarray, position, radius: float):
        if len(points) == 0:
            return None
        screen = self.project(points)
        visible = screen[:, 2] > 0
        if not visible.any():
            return None
        distances = np.hypot(
            screen[:, 0] - position.x(), screen[:, 1] - position.y()
        )
        distances[~visible] = np.inf
        nearest = float(distances.min())
        if nearest > radius:
            return None
        # На экране точки совпадают чаще, чем кажется: при взгляде строго
        # сверху верхний и нижний углы детали лежат в одном пикселе. Брать
        # первую попавшуюся нельзя — рука целилась в видимую, то есть в
        # БЛИЖНЮЮ к камере. Разрешаем спор глубиной.
        close_enough = distances <= nearest + self._SAME_SPOT
        # Глубину берём вдоль ВЗГЛЯДА, а не из однородной координаты: в
        # ортогональной проекции она одинакова у всех точек, и спор так не
        # решался — сверху выбирался нижний угол вместо верхнего.
        toward = self._toward()
        along = points.astype(np.float64) @ np.asarray(toward, np.float64)
        along = np.where(close_enough, -along, np.inf)
        return points[int(np.argmin(along))].astype(float)

    def label_of(self, identifier: int) -> str:
        return self.scene.labels.get(identifier, "")

    def face_label_of(self, identifier: int) -> str:
        entry = self.scene.face_labels.get(identifier)
        return entry.get("label", "") if isinstance(entry, dict) else ""

    def face_index_of(self, identifier: int) -> int:
        """Номер грани внутри тела; −1, если номер не о грани."""
        entry = self.scene.face_labels.get(identifier)
        return int(entry.get("face", -1)) if isinstance(entry, dict) else -1


def _painted(numbers: np.ndarray, colours: dict) -> np.ndarray:
    """Цвет каждой вершины по таблице {номер: цвет} — через массив-указатель,
    без обхода вершин в Python."""
    top = int(max(int(numbers.max(initial=0)), max(int(key) for key in colours)))
    lookup = np.zeros(top + 1, np.uint32)
    for key, colour in colours.items():
        if 0 <= int(key) <= top:
            lookup[int(key)] = int(colour)
    return lookup[numbers]


def default_surface_format() -> QtGui.QSurfaceFormat:
    """Формат поверхности, который приложение обязано выставить ДО создания
    окон: иначе контекст 3.3 core не гарантирован."""
    surface = QtGui.QSurfaceFormat()
    surface.setVersion(3, 3)
    surface.setProfile(QtGui.QSurfaceFormat.CoreProfile)
    surface.setDepthBufferSize(24)
    return surface
