"""Команды построения детали: создание операций через интерфейс.

До этого дерево строилось только кодом — открыть деталь и подправить числа
было можно, а сделать новую нельзя. Здесь описаны команды, которыми
конструктор строит деталь: эскиз, выдавливание, вырез, вращение, отверстия,
скругление, фаска, зеркало.

Каждая команда описана ДАННЫМИ: заголовок, поля, вид операции. Диалог
строится по описанию, поэтому добавление новой операции стоит несколько
строк, а не отдельного окна.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6 import QtWidgets

from protocad.engine import EndCondition
from protocad.sketch import Sketch


@dataclass
class Field:
    """Поле диалога: имя параметра, подпись, тип, границы."""

    key: str
    label: str
    default: float | int | str = 0.0
    minimum: float = -100000.0
    maximum: float = 100000.0
    decimals: int = 3
    choices: tuple = ()


@dataclass
class Command:
    """Команда построения: что спросить и что создать."""

    key: str
    title: str
    shortcut: str = ""
    fields: list = field(default_factory=list)
    needs_sketch: bool = False
    needs_body: bool = True


COMMANDS = (
    Command(
        "sketch", "Эскиз", "S",
        fields=[Field("name", "Имя", "Эскиз", choices=())],
        needs_body=False,
    ),
    Command(
        "pad", "Выдавливание", "P",
        fields=[Field("length", "Длина, мм", 10.0, 0.001)],
        needs_sketch=True, needs_body=False,
    ),
    Command(
        "revolve", "Вращение", "",
        fields=[
            Field("angle", "Угол, град", 360.0, 0.1, 360.0),
            Field("axis", "Ось", "Y", choices=("X", "Y", "Z")),
        ],
        needs_sketch=True, needs_body=False,
    ),
    Command(
        "pocket", "Вырез по эскизу", "",
        fields=[Field("depth", "Глубина, мм", 5.0, 0.0)],
        needs_sketch=True,
    ),
    Command(
        "hole", "Отверстие", "",
        fields=[
            Field("x", "X, мм", 10.0),
            Field("y", "Y, мм", 10.0),
            Field("radius", "Радиус, мм", 3.0, 0.001),
        ],
    ),
    Command(
        "hole_pattern", "Массив отверстий", "",
        fields=[
            Field("x", "X первого, мм", 10.0),
            Field("y", "Y первого, мм", 10.0),
            Field("radius", "Радиус, мм", 3.0, 0.001),
            Field("count_x", "Количество по X", 3, 1, 500, decimals=0),
            Field("step_x", "Шаг по X, мм", 20.0, 0.001),
            Field("count_y", "Количество по Y", 2, 1, 500, decimals=0),
            Field("step_y", "Шаг по Y, мм", 20.0, 0.001),
        ],
    ),
    Command(
        "fillet", "Скругление", "",
        fields=[
            Field("radius", "Радиус, мм", 2.0, 0.001),
            Field("selector", "Рёбра", "vertical", choices=("vertical", "all")),
        ],
    ),
    Command(
        "chamfer", "Фаска", "",
        fields=[Field("size", "Размер, мм", 1.0, 0.001)],
    ),
    Command(
        "mirror", "Зеркало", "",
        fields=[
            Field("plane", "Плоскость", "YZ", choices=("YZ", "XZ", "XY")),
            Field("offset", "Смещение, мм", 0.0),
        ],
    ),
    Command(
        "shell", "Оболочка", "",
        fields=[Field("thickness", "Толщина стенки, мм", 2.0, 0.001)],
    ),
)

COMMAND_BY_KEY = {command.key: command for command in COMMANDS}


class ParameterDialog(QtWidgets.QDialog):
    """Диалог по описанию команды."""

    def __init__(self, command: Command, parent=None):
        super().__init__(parent)
        self.setWindowTitle(command.title)
        self.command = command
        self._widgets: dict[str, QtWidgets.QWidget] = {}

        form = QtWidgets.QFormLayout()
        for item in command.fields:
            if item.choices:
                widget = QtWidgets.QComboBox()
                widget.addItems([str(choice) for choice in item.choices])
                widget.setCurrentText(str(item.default))
            elif isinstance(item.default, str):
                widget = QtWidgets.QLineEdit(item.default)
            elif item.decimals == 0:
                widget = QtWidgets.QSpinBox()
                widget.setRange(int(item.minimum), int(item.maximum))
                widget.setValue(int(item.default))
            else:
                widget = QtWidgets.QDoubleSpinBox()
                widget.setRange(item.minimum, item.maximum)
                widget.setDecimals(item.decimals)
                widget.setValue(float(item.default))
            self._widgets[item.key] = widget
            form.addRow(item.label, widget)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> dict:
        result = {}
        for key, widget in self._widgets.items():
            if isinstance(widget, QtWidgets.QComboBox):
                result[key] = widget.currentText()
            elif isinstance(widget, QtWidgets.QLineEdit):
                result[key] = widget.text()
            else:
                result[key] = widget.value()
        return result


#: Ось вращения по букве. Точка на оси — начало координат детали: другую
#: команда пока не спрашивает, и выдумывать её здесь нельзя.
AXES = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}

#: Нормаль плоскости зеркала по её названию.
MIRROR_PLANES = {"YZ": (1.0, 0.0, 0.0), "XZ": (0.0, 1.0, 0.0),
                 "XY": (0.0, 0.0, 1.0)}


def make_operation(key: str, values: dict, document, sketch=None, model=None,
                   edges=None, faces=None):
    """Команда интерфейса → запись дерева намерений.

    Геометрию не считает: собирает то, что человек попросил, и отдаёт
    документу. Считать будет движок (docs/08_ENGINE_BACKEND.md, §5).

    ``model`` — описание готовой детали. Нужно там, где команда задана
    ПРАВИЛОМ, а движку нужны номера: «скруглить вертикальные рёбра» —
    правило, `Edge3, Edge7` — то, что уходит в запрос. Правило применяется
    здесь, к описанию, а не внутри движка.
    """
    from protocad.document import Operation

    title = COMMAND_BY_KEY[key].title
    name = unique_operation_name(document, title)

    if key == "pad":
        return Operation("pad", name, sketch=sketch, length=values["length"],
                         merge=bool(values.get("merge", True)))
    if key == "pocket":
        # Глубина 0 означает «насквозь»: так эту команду и понимают, и
        # отдельного поля у неё нет.
        depth = float(values["depth"])
        return Operation(
            "pocket", name, sketch=sketch, length=depth or 1.0,
            end=(EndCondition.THROUGH_ALL if depth <= 0.0
                 else EndCondition.BLIND))
    if key == "revolve":
        return Operation("revolve", name, sketch=sketch,
                         angle=float(values["angle"]),
                         axis=AXES.get(values.get("axis", "Y"), AXES["Y"]))
    if key in ("hole", "hole_pattern"):
        # У отверстия координаты задаются числами, а движку нужен эскиз с
        # окружностями: он берёт из них МЕСТА, а диаметр — из самой
        # операции. Эскиз строится здесь и попадает в дерево наравне с
        # нарисованными: иначе отверстие нельзя было бы подвинуть.
        places = _hole_places(key, values)
        drawn = _sketch_of_circles(name, places, float(values["radius"]), model)
        return Operation("hole", name, sketch=drawn,
                         diameter=2.0 * float(values["radius"]), through=True)
    if key in ("fillet", "chamfer"):
        chosen = list(edges or ())
        if not chosen and model is not None:
            chosen = pick_edges(model, values.get("selector", "vertical"))
        size = float(values.get("radius") or values.get("size") or 1.0)
        return Operation(key, name, edges=chosen, size=size,
                         edge_names=_names(model, "edge", chosen))
    if key == "shell":
        chosen = list(faces or ())
        if not chosen and model is not None:
            chosen = [top_face(model)] if top_face(model) >= 0 else []
        return Operation("shell", name, faces=chosen,
                         face_names=_names(model, "face", chosen),
                         thickness=float(values["thickness"]))
    if key == "mirror":
        normal = MIRROR_PLANES.get(values.get("plane", "YZ"), (1.0, 0.0, 0.0))
        offset = float(values.get("offset", 0.0))
        return Operation("mirror", name, whole_shape=True,
                         plane_normal=normal,
                         plane_origin=tuple(value * offset for value in normal))
    raise ValueError(f"неизвестная команда: {key!r}")


def _hole_places(key: str, values: dict) -> list:
    """Центры отверстий в координатах эскиза."""
    x, y = float(values["x"]), float(values["y"])
    if key == "hole":
        return [(x, y)]
    return [
        (x + column * float(values["step_x"]), y + row * float(values["step_y"]))
        for row in range(int(values["count_y"]))
        for column in range(int(values["count_x"]))
    ]


def _sketch_of_circles(name: str, places, radius: float, model) -> Sketch:
    """Эскиз с окружностями на верхней грани детали.

    Плоскость берётся у ДЕТАЛИ, а не назначается XY: отверстие сверлят от
    поверхности, и на детали со ступенькой разница видна сразу.
    """
    from protocad.sketch import plane as plane_module

    plane = None
    if model is not None:
        number = top_face(model)
        if number >= 0:
            plane = model.plane_of(number)
    sketch = Sketch(f"{name}: места", plane=plane or plane_module.STANDARD["XY"])
    for x, y in places:
        sketch.circle(sketch.point(x, y), radius)
    if sketch.points:
        sketch.anchor(sketch.points[0])
    sketch.solve()
    return sketch


def _names(model, kind: str, numbers) -> list:
    """Устойчивые имена подэлементов по их номерам.

    Пустая строка на месте имени означает, что движок карты элементов не
    ведёт: тогда остаётся номер, и это сказано, а не спрятано.
    """
    if model is None:
        return ["" for _ in numbers]
    return [model.name_of(kind, int(value)) for value in numbers]


def pick_edges(model, selector: str) -> list:
    """Рёбра по правилу команды — номерами, которые понимает движок.

    «Вертикальные» — прямые рёбра, у которых меняется только высота. Это
    то же правило, что было у старой операции; отличие в том, что
    применяется оно к ОПИСАНИЮ детали, а не к формам ядра.
    """
    found = []
    for item in model.edges:
        if selector == "all":
            found.append(item.index)
            continue
        if item.data.get("curve") != "line":
            continue
        start, end = item.data.get("start"), item.data.get("end")
        if not start or not end:
            continue
        if abs(start[0] - end[0]) < 1e-9 and abs(start[1] - end[1]) < 1e-9:
            found.append(item.index)
    return found


def top_face(model) -> int:
    """Верхняя плоская грань детали. −1 — такой нет.

    Оболочка без выбора открывается сверху: так её и просят, когда не
    указали грань. Выбранная в виде грань имеет приоритет.
    """
    best, number = None, -1
    for item in model.faces:
        if item.data.get("surface") != "plane":
            continue
        normal = item.data.get("normal") or (0.0, 0.0, 0.0)
        if normal[2] < 0.9:
            continue
        height = (item.data.get("center") or (0.0, 0.0, 0.0))[2]
        if best is None or height > best:
            best, number = height, item.index
    return number


def unique_operation_name(document, base: str) -> str:
    """Имя операции без совпадений внутри документа."""
    existing = {item.name for item in document.operations}
    if base not in existing:
        return base
    index = 2
    while f"{base} {index}" in existing:
        index += 1
    return f"{base} {index}"
