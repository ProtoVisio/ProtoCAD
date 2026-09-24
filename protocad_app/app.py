"""ProtoCAD — рабочее место конструктора.

    .venv\\Scripts\\python.exe protocad_app/app.py
    .venv\\Scripts\\python.exe protocad_app/app.py work/АБВГ.741141.001.prcadPart

Окно устроено вокруг одного правила: **режим определяет всё**. В режиме
модели по центру трёхмерный вид, слева дерево построения, лента показывает
операции над телом. В режиме эскиза по центру поле чертежа, слева список
объектов и связей, лента показывает инструменты рисования. Один и тот же
«Зеркало» в двух режимах означает разное, и разводятся они здесь — там, где
режим известен.

Переход в эскиз не открывает отдельное окно. Эскиз — часть детали, а не
приложение внутри приложения: возврат из него обязан пересчитать дерево,
и модальное окно этот порядок только запутывало бы.

Приложение самостоятельное: OCCT через OCP, решатель эскизов, свой вьюпорт.
FreeCAD не требуется.
"""

from __future__ import annotations

import math
import sys
import time
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from protocad import format as fmt  # noqa: E402
from protocad import document as document_module  # noqa: E402
from protocad.document import TITLES, Document, Operation  # noqa: E402
from protocad.sketch import Sketch, available_solvers  # noqa: E402
from protocad.sketch import convert  # noqa: E402
import view_cube  # noqa: E402
from view_cube import ViewCube  # noqa: E402
from decor import SceneDecor  # noqa: E402
from model_view import ModelView, edge_signature_of  # noqa: E402
from overlays import ConfirmCorner, OverlayBar  # noqa: E402
from protocad.sketch import plane as plane_module  # noqa: E402
from protocad.sketch import repair as repair_module  # noqa: E402
from protocad.sketch import tools as T  # noqa: E402
from protocad_gl import SceneBuffers, Viewport  # noqa: E402
from protocad_gl.viewport import default_surface_format  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import icons  # noqa: E402
import ribbon as ribbon_module  # noqa: E402
from commands import (  # noqa: E402
    make_operation,
)
import descriptors as D  # noqa: E402
import sketch_view  # noqa: E402
from command_session import CommandError, CommandSession, Pick  # noqa: E402
from hole_stack_editor import HoleStackEditor  # noqa: E402
from line_panel import LineProperties  # noqa: E402
from property_panel import PropertyPanel  # noqa: E402
from sketch_view import SketchCanvas  # noqa: E402

MODE_MODEL = "model"
MODE_SKETCH = "sketch"

#: Цвет предпросмотра. Прилив — тёплый, вырез — красный: это две
#: противоположные вещи, и одним цветом они не показываются.
PREVIEW_ADD = (0.93, 0.85, 0.35)
PREVIEW_CUT = (0.90, 0.35, 0.30)


def demo_document() -> Document:
    """Деталь-образец, чтобы окно открывалось не пустым."""
    sketch = Sketch("Контур")
    bottom, right, top, left = sketch.polyline(
        [(0.0, 0.0), (90.0, 2.0), (95.0, 55.0), (3.0, 50.0)]
    )
    sketch.horizontal(bottom)
    sketch.vertical(right)
    sketch.horizontal(top)
    sketch.vertical(left)
    sketch.anchor(bottom.points[0])
    sketch.dimension(bottom.points[0], bottom.points[1], 120.0, "Ширина")
    sketch.dimension(right.points[0], right.points[1], 80.0, "Высота")

    sketch.solve()
    document = Document("Рамка", designation="АБВГ.741141.001")
    document.add(Operation("pad", "Выдавливание", sketch=sketch, length=8.0))
    document.rebuild()

    # Крепёжные отверстия — одной операцией по эскизу с четырьмя
    # окружностями: движку так же, а в дереве это одна запись, а не
    # четыре одинаковые.
    from model_view import ModelView as _View

    holes = make_operation(
        "hole_pattern",
        {"x": 10.0, "y": 10.0, "radius": 3.0,
         "count_x": 2, "step_x": 100.0, "count_y": 2, "step_y": 60.0},
        document, model=_View(document.result))
    document.add(holes)
    document.rebuild()

    edges = make_operation("chamfer", {"size": 1.5}, document,
                           model=_View(document.result))
    document.add(edges)
    document.rebuild()
    return document


class ParameterEditor(QtWidgets.QWidget):
    """Правка параметров выбранной операции."""

    changed = QtCore.Signal(str, str, float)

    def __init__(self):
        super().__init__()
        self._layout = QtWidgets.QFormLayout(self)
        self._layout.setContentsMargins(8, 8, 8, 8)
        self._feature = ""
        self._title = QtWidgets.QLabel("Операция не выбрана")
        font = self._title.font()
        font.setBold(True)
        self._title.setFont(font)
        self._layout.addRow(self._title)

    def show_feature(self, name: str, kind: str, parameters: dict) -> None:
        while self._layout.rowCount() > 1:
            self._layout.removeRow(1)
        self._feature = name
        self._title.setText(f"{kind}: {name}")
        for parameter, value in parameters.items():
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(-100000.0, 100000.0)
            spin.setDecimals(3)
            spin.setValue(float(value) if value is not None else 10.0)
            spin.setKeyboardTracking(False)
            spin.valueChanged.connect(
                lambda new, p=parameter: self.changed.emit(self._feature, p, new)
            )
            self._layout.addRow(parameter, spin)


class SketchPanel(QtWidgets.QWidget):
    """Слева в режиме эскиза: объекты, связи, размеры.

    Список нужен не для красоты. Связь, наложенную по ошибке, на поле
    чертежа видно плохо — значок мелкий и стоит рядом с десятком таких же.
    Здесь она названа словом, и её можно снять, не выцеливая мышью.
    """

    remove_relation = QtCore.Signal(object)
    select_entity = QtCore.Signal(object)
    #: Область включена или выключена галкой: её номер и новое состояние.
    region_toggled = QtCore.Signal(int, bool)

    def __init__(self):
        super().__init__()
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Эскиз", "Значение"])
        self.tree.setColumnWidth(0, 190)
        self.tree.itemSelectionChanged.connect(self._selected)
        self.tree.itemChanged.connect(self._checked)
        layout.addWidget(self.tree)

        row = QtWidgets.QHBoxLayout()
        self.remove = QtWidgets.QPushButton("Снять связь")
        self.remove.setEnabled(False)
        self.remove.clicked.connect(self._remove)
        row.addWidget(self.remove)
        row.addStretch(1)
        layout.addLayout(row)

    def fill(self, sketch: Sketch, selection: list, canvas=None) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()

        where = QtWidgets.QTreeWidgetItem(["Плоскость", sketch.plane.name])
        where.setIcon(0, icons.icon("rectangle", 16))
        where.setToolTip(1, "нормаль {:.3g}, {:.3g}, {:.3g}\nначало {:.3g}, {:.3g}, {:.3g}"
                         .format(*sketch.plane.normal, *sketch.plane.origin))
        self.tree.addTopLevelItem(where)

        geometry = QtWidgets.QTreeWidgetItem(["Объекты", str(len(sketch.segments))])
        for segment in sketch.segments:
            note = "вспомогательный" if segment.construction else ""
            node = QtWidgets.QTreeWidgetItem([segment.label, note])
            node.setIcon(0, icons.icon(
                {"line": "line", "arc": "arc", "circle": "circle"}[segment.kind], 16
            ))
            node.setData(0, QtCore.Qt.UserRole, segment)
            if segment in selection:
                node.setSelected(True)
            geometry.addChild(node)
        self.tree.addTopLevelItem(geometry)

        relations = QtWidgets.QTreeWidgetItem(["Связи", str(len(sketch.constraints))])
        for constraint in sketch.constraints:
            targets = ", ".join(
                getattr(target, "label", "?") for target in constraint.targets
            )
            node = QtWidgets.QTreeWidgetItem([constraint.title, targets])
            node.setIcon(0, icons.icon(constraint.kind, 16))
            node.setData(0, QtCore.Qt.UserRole, constraint)
            relations.addChild(node)
        self.tree.addTopLevelItem(relations)

        # Области — то, что станет профилем операции. Список нужен потому,
        # что иначе о самой возможности выбора догадаться нельзя: на
        # чертеже области видны заливкой, но что их МОЖНО отмечать —
        # ниоткуда не следует.
        if canvas is not None:
            found = canvas.regions()
            picked = sum(1 for region in found if canvas.region_is_picked(region))
            summary = (f"{len(found)}, выбрано {picked}" if picked
                       else f"{len(found)} — профилем станет весь эскиз")
            areas = QtWidgets.QTreeWidgetItem(["Области", summary])
            areas.setIcon(0, icons.icon("region", 16))
            areas.setToolTip(
                1,
                "Без отметки вложенный контур считается отверстием — как в "
                "отраслевой практике. Отметьте области, чтобы задать профиль "
                "явно: отмеченные объединяются, и отверстие между ними исчезает."
            )
            for number, region in enumerate(found, 1):
                holes = (f", отверстий {len(region.inner)}" if region.inner else "")
                node = QtWidgets.QTreeWidgetItem(
                    [f"Область {number}", f"{region.area:.1f} мм²{holes}"])
                node.setFlags(node.flags() | QtCore.Qt.ItemIsUserCheckable)
                node.setCheckState(
                    0, QtCore.Qt.Checked if canvas.region_is_picked(region)
                    else QtCore.Qt.Unchecked)
                node.setData(0, QtCore.Qt.UserRole + 1, number - 1)
                areas.addChild(node)
            self.tree.addTopLevelItem(areas)

        sizes = QtWidgets.QTreeWidgetItem(["Размеры", str(len(sketch.dimensions))])
        for dimension in sketch.dimensions:
            node = QtWidgets.QTreeWidgetItem([dimension.name, dimension.text])
            node.setIcon(0, icons.icon("dimension", 16))
            node.setData(0, QtCore.Qt.UserRole, dimension)
            sizes.addChild(node)
        self.tree.addTopLevelItem(sizes)

        for index in range(self.tree.topLevelItemCount()):
            self.tree.topLevelItem(index).setExpanded(True)
        self.tree.blockSignals(False)

    def _checked(self, item, column: int) -> None:
        number = item.data(0, QtCore.Qt.UserRole + 1)
        if column != 0 or number is None:
            return
        # Что отметили, читается СЕЙЧАС, пока строка жива, а сообщается
        # ОТЛОЖЕННО — следующим проходом очереди событий.
        #
        # Иначе выходит так: обработчик отметки перезаполняет панель, а
        # перезаполнение начинается с `tree.clear()`, который удаляет в
        # C++ ту самую строку, чей `setCheckState` ещё не вернулся. Qt
        # после возврата продолжает работать с освобождённой памятью, и
        # процесс умирает целиком, не оставив ни строчки вывода. Именно
        # это падение ловилось трижды и не воспроизводилось.
        state = item.checkState(0) == QtCore.Qt.Checked
        QtCore.QTimer.singleShot(
            0, lambda: self.region_toggled.emit(int(number), state))

    def _current(self):
        items = self.tree.selectedItems()
        return items[0].data(0, QtCore.Qt.UserRole) if items else None

    def _selected(self) -> None:
        entity = self._current()
        self.remove.setEnabled(
            entity is not None and hasattr(entity, "kind")
            and not hasattr(entity, "points")
        )
        if entity is not None:
            self.select_entity.emit(entity)

    def _remove(self) -> None:
        entity = self._current()
        if entity is not None:
            self.remove_relation.emit(entity)


class _BarDelegate(QtWidgets.QStyledItemDelegate):
    """Шторка рисуется ЛИНИЕЙ, а не строкой с подписью.

    Подпись здесь лишняя: линия поперёк дерева и так говорит «докуда
    построено», а слова занимают строку, спорят с названиями операций и
    заставляют читать там, где достаточно взглянуть.
    """

    #: Высота полосы под линию. Меньше строки: это не запись дерева.
    HEIGHT = 11
    COLOUR = QtGui.QColor("#c0392b")

    def _is_bar(self, index) -> bool:
        return bool(index.data(BuildTree.MARK))

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        if self._is_bar(index):
            size.setHeight(self.HEIGHT)
        return size

    def paint(self, painter, option, index) -> None:
        if not self._is_bar(index):
            super().paint(painter, option, index)
            return
        painter.save()
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = option.rect
        y = rect.center().y()
        # Утолщение под курсором и при переносе: за линию берутся мышью, и
        # она обязана показывать, что её МОЖНО взять.
        hot = bool(option.state & QtWidgets.QStyle.State_MouseOver)
        pen = QtGui.QPen(self.COLOUR, 3.0 if hot else 2.0)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        painter.setPen(pen)
        painter.drawLine(rect.left() + 4, y, rect.right() - 4, y)
        # Кружок слева — место, за которое берут. Без него линия читается
        # как разделитель, а не как то, что двигают.
        painter.setBrush(QtGui.QBrush(self.COLOUR))
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawEllipse(QtCore.QPointF(rect.left() + 8, y),
                            4.0 if hot else 3.0, 4.0 if hot else 3.0)
        painter.restore()


class BuildTree(QtWidgets.QTreeWidget):
    """Дерево построения со ШТОРКОЙ ОТКАТА, которую тащат мышью.

    Название не случайное. Именем `FeatureTree` в этом проекте зовётся
    СТАРОЕ дерево операций (`protocad.feature`), от которого окно
    избавлено, и сторож архитектуры следит, чтобы оно сюда не вернулось.
    Назвать так виджет значило бы обмануть сторожа и запутать читателя.

    Шторка — не операция и не папка: это отметка «докуда деталь
    построена». Тащить её надо рукой, как в чертёжной практике, поэтому
    она и сделана перетаскиваемой строкой, а не полем с номером: номер
    операции человек не помнит и помнить не должен.
    """

    #: Роль, по которой строка опознаётся как шторка.
    MARK = QtCore.Qt.UserRole + 7

    rollback_moved = QtCore.Signal(int)

    def __init__(self):
        super().__init__()
        # Перетаскивание Qt здесь не годится. Оно кладёт строку туда, куда
        # пришёлся курсор, — в том числе ВНУТРЬ операции и между эскизом и
        # его операцией, где шторке места нет: она стоит МЕЖДУ операциями,
        # а не внутри одной. Поэтому захват свой, и шторка ходит
        # ступенями — по одному промежутку между соседними операциями.
        self.setDragDropMode(QtWidgets.QAbstractItemView.NoDragDrop)
        self.setDragEnabled(False)
        self.setAcceptDrops(False)
        self.setItemDelegate(_BarDelegate(self))
        # Слежение за мышью — чтобы линия утолщалась под курсором: без
        # этого она выглядит нарисованной, а не берущейся.
        self.setMouseTracking(True)
        #: Несут ли шторку прямо сейчас.
        self._carrying = False

    def is_bar(self, item) -> bool:
        return bool(item is not None and item.data(0, self.MARK))

    def bar_row(self):
        """Строка шторки. ``None`` — её в дереве нет."""
        if not self.topLevelItemCount():
            return None
        root = self.topLevelItem(0)
        for index in range(root.childCount()):
            row = root.child(index)
            if self.is_bar(row):
                return row
        return None

    def bar_place(self) -> int:
        """Сколько операций стоит НАД шторкой сейчас."""
        bar = self.bar_row()
        if bar is None:
            return len(self.operation_rows())
        root = self.topLevelItem(0)
        above = 0
        for index in range(root.childCount()):
            row = root.child(index)
            if row is bar:
                break
            if not self.is_bar(row) and row.data(0, QtCore.Qt.UserRole):
                above += 1
        return above

    def step_at(self, point) -> int:
        """Ближайшая СТУПЕНЬ под курсором: промежуток между операциями.

        Ступеней ровно на одну больше, чем операций: перед первой, между
        соседними и после последней. Промежуточных положений нет — шторка
        либо выше операции, либо ниже, и «немного внутри» не бывает.
        """
        rows = self.operation_rows()
        for index, row in enumerate(rows):
            rect = self.visualItemRect(row)
            if not rect.isValid():
                continue
            # Строка операции вместе с её эскизом: середина считается по
            # ВИДИМОЙ высоте всей ветки, иначе развёрнутый эскиз сдвигает
            # порог и шторка перескакивает через операцию.
            bottom = rect.bottom()
            for child in range(row.childCount()):
                below = self.visualItemRect(row.child(child))
                if below.isValid():
                    bottom = max(bottom, below.bottom())
            if point.y() < (rect.top() + bottom) / 2:
                return index
        return len(rows)

    def carry_to(self, point) -> bool:
        """Переставить шторку на ступень под курсором. ``True`` — сдвинулась.

        Двигается только СТРОКА: деталь пересчитывается один раз, когда
        шторку отпустят. Пересчитывать на каждой ступени значило бы
        перестраивать деталь по нескольку раз за одно движение руки.
        """
        bar = self.bar_row()
        if bar is None:
            return False
        step = self.step_at(point)
        if step == self.bar_place():
            return False
        root = self.topLevelItem(0)
        root.removeChild(bar)
        rows = self.operation_rows()
        if step >= len(rows):
            root.addChild(bar)
        else:
            root.insertChild(root.indexOfChild(rows[step]), bar)
        return True

    def mousePressEvent(self, event) -> None:
        if event.button() == QtCore.Qt.LeftButton \
                and self.is_bar(self.itemAt(event.position().toPoint())):
            self._carrying = True
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._carrying:
            self.carry_to(event.position().toPoint())
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._carrying:
            self._carrying = False
            self.rollback_moved.emit(self.bar_place())
            return
        super().mouseReleaseEvent(event)

    def operation_rows(self) -> list:
        """Строки операций верхнего уровня, без шторки и без папок."""
        if not self.topLevelItemCount():
            return []
        root = self.topLevelItem(0)
        rows = []
        for index in range(root.childCount()):
            row = root.child(index)
            if self.is_bar(row):
                continue
            if row.data(0, QtCore.Qt.UserRole):
                rows.append(row)
        return rows

    def place_of(self, target, below: bool = False) -> int:
        """Сколько операций окажется НАД шторкой при таком приземлении."""
        rows = self.operation_rows()
        if target is None:
            return len(rows)
        # Приземлиться могли и на эскиз внутри операции — считается его
        # операция: шторка стоит между операциями, а не внутри одной.
        while target is not None and target.parent() is not None \
                and target.parent().parent() is not None:
            target = target.parent()
        if target in rows:
            return rows.index(target) + (1 if below else 0)
        return len(rows)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, document: Document):
        super().__init__()
        #: Деталь: дерево НАМЕРЕНИЙ и движок, который их выполняет. Формы
        #: ядра в окно не попадают вовсе (docs/08_ENGINE_BACKEND.md, §10.1).
        self.document = document
        self.path: Path | None = None
        self.mode = MODE_MODEL
        self.active_sketch: Sketch | None = None
        #: Операция, чей эскиз правится. Пусто — эскиз ещё ничего не строит.
        self.active_operation = None
        #: Выбранные области по эскизам, у которых операции ещё нет. Выбор
        #: сделан до того, как создана операция, и потерять его нельзя:
        #: человек уже указал, что именно выдавливать.
        self._pending_regions: dict = {}
        # Что выбрано в виде. От грани запоминается ПЛОСКОСТЬ, чтобы
        # «Эскиз» строился именно на ней, а не спрашивал заново. Форма
        # грани окну не нужна: плоскость приходит от движка числами.
        self.selected_face_plane = None
        self.selected_face_index = -1
        self.selected_point = None
        # Выбранные рёбра копятся: скругление обычно снимают сразу с
        # нескольких, и заставлять повторять команду ради каждого — значит
        # не понимать, зачем её вызывают.
        self.selected_edges: list[int] = []
        self.model_picking = ""
        #: незавершённая операция; пока она есть, щелчки идут в её поля
        self.session: CommandSession | None = None
        #: Операция, которую правят. Пусто — команда создаёт новую.
        self.editing = None
        #: Схваченная мышью стрелка: с чего начали и сколько миллиметров в
        #: пикселе. Пусто — стрелку не тянут.
        self._arrow_drag = None
        #: Сколько миллисекунд занял последний пересчёт предпросмотра. По
        #: нему и решается, тянуть ли тело за рукой: порог по замеру, а не
        #: по догадке о том, какая деталь «тяжёлая».
        self._preview_cost = 0.0
        #: Предпросмотр отложен до отпускания стрелки.
        self._preview_owed = False
        self._saved_scene = None
        self.resize(1680, 980)

        self.viewport = Viewport()
        self.viewport.picked.connect(self._picked)
        self.viewport.selected.connect(self._picked_in_view)
        # Эскиз — не окно, а наложение поверх вида. Он рисуется тем же
        # кодом, что и раньше, но координаты берёт у камеры: деталь под ним
        # видно, и её можно повернуть, не выходя из эскиза.
        self.canvas = SketchCanvas(Sketch("пусто"), self.viewport)
        self.canvas.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.canvas.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        self.canvas.hide()
        self.canvas.changed.connect(self._sketch_changed)
        self.canvas.status.connect(self._sketch_status)
        self.canvas.hint.connect(lambda text: self.hint.setText(text))
        self.canvas.tool_finished.connect(self._tool_finished)
        self.canvas.tool_changed.connect(self._tool_changed)
        self.canvas.model_pick.connect(self._pick_model_from_sketch)
        self.overlay = None
        # Второе поле — только для показа: эскиз выбранной операции виден в
        # детали, но не правится. Отдельный объект нужен, чтобы показ не
        # сбивал выбор и незаконченный инструмент в том эскизе, который
        # человек правит.
        self.preview_canvas = SketchCanvas(Sketch("показ"), self.viewport)
        # Разметка лежит ПОД полем эскиза: плоскость, нарисованная поверх
        # чертежа, закрыла бы то, что на ней чертят.
        self.decor = SceneDecor(self.viewport)
        # Мышь по виду сначала спрашивают у окна: за стрелку тянут
        # прямо в виде, и пока её тянут, камера вращаться не должна.
        self.viewport.installEventFilter(self)
        self.decor.setGeometry(self.viewport.rect())
        self.decor.lower()
        # Кубик видов — ребёнок ВИДА, а не окна: он должен лежать в углу
        # картинки и не мешать полю эскиза, которое накрывает вид целиком.
        self.view_cube = ViewCube(self.viewport)
        self.view_cube.oriented.connect(self._orient)
        self.view_bar = self._build_view_bar()
        self.confirm = ConfirmCorner(self.viewport)
        self.confirm.accepted.connect(self._finish_sketch)
        self.confirm.rejected.connect(self._cancel_sketch)
        self.confirm.hide()
        self.viewport.camera_moved.connect(self.view_cube.follow)
        self.viewport.camera_moved.connect(
            lambda *_: self.decor.update())
        self.viewport.installEventFilter(self)
        self.view_cube.raise_()
        self.preview_canvas.setAttribute(QtCore.Qt.WA_TranslucentBackground, True)
        self.preview_canvas.setAttribute(QtCore.Qt.WA_NoSystemBackground, True)
        # Показ не принимает мышь: щелчок по детали должен доходить до вида
        # и выбирать грань, а не проваливаться в невидимое поле эскиза.
        self.preview_canvas.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        self.preview_canvas.hide()
        self.preview_canvas.show_grid = False
        self.preview_canvas.show_relations = False

        self.center = self.viewport

        self.tree = BuildTree()
        self.tree.setHeaderLabels(["Дерево построения", "Состояние"])
        self.tree.setColumnWidth(0, 210)
        self.tree.rollback_moved.connect(self._move_rollback)
        self.tree.currentItemChanged.connect(self._feature_selected)
        self.tree.itemDoubleClicked.connect(self._edit_feature)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)

        self.editor = ParameterEditor()
        self.editor.changed.connect(self._parameter_changed)

        self.sketch_panel = SketchPanel()
        self.sketch_panel.remove_relation.connect(self._remove_relation)
        self.sketch_panel.select_entity.connect(self._select_from_panel)
        self.sketch_panel.region_toggled.connect(self._toggle_region_from_panel)

        model_side = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        model_side.addWidget(self.tree)
        model_side.addWidget(self.editor)
        model_side.setSizes([560, 340])

        self.panel = PropertyPanel()
        self.panel.committed.connect(self._commit_command)
        self.panel.cancelled.connect(self._cancel_command)
        self.panel.changed.connect(self._session_changed)
        self.panel.box_activated.connect(self._activate_box)

        # Свойства выбранного отрезка. Показываются ВМЕСТО списка эскиза,
        # пока выбран ровно один отрезок: они про него и есть, а список
        # всего эскиза в этот момент отвечает не на тот вопрос.
        self.line_panel = LineProperties()
        self.line_panel.changed.connect(self._line_edited)

        self.side = QtWidgets.QStackedWidget()
        self.side.addWidget(model_side)
        self.side.addWidget(self.sketch_panel)
        self.side.addWidget(self.line_panel)

        # Составное отверстие правится СТЕКОМ (§13): порядок элементов
        # задаёт человек, и полями с готовыми именами его не выразить.
        self.hole_stack = HoleStackEditor()
        self.hole_stack.changed.connect(self._hole_stack_changed)
        self.side.addWidget(self.hole_stack)

        # Панель незавершённой команды и ДЕРЕВО стоят РЯДОМ, а не одно
        # вместо другого. Пока они делили одно место, во время команды
        # дерево пропадало — а указывать операцию для массива или эскиз
        # для отверстий надо именно в нём. Выходило, что команда просит
        # то, чего в этот момент не видно.
        self.left = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.left.addWidget(self.panel)
        self.left.addWidget(self.side)
        self.panel.hide()

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.addWidget(self.left)
        splitter.addWidget(self.viewport)
        splitter.setSizes([380, 1300])

        self.ribbon = ribbon_module.Ribbon(ribbon_module.TABS)
        self.ribbon.activated.connect(self._command)
        self.ribbon.switched.connect(self._switch)
        self.ribbon.install_shortcuts(self)

        body = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.ribbon)
        layout.addWidget(splitter, 1)
        self.setCentralWidget(body)

        self.viewport.installEventFilter(self)

        # Что окно знает о детали. Формы ядра оно больше не разбирает:
        # грани, рёбра и вершины приходят описанием (docs/08_ENGINE_BACKEND,
        # §10.2). Пока источник — пересчёт на месте; когда деталь считает
        # движок, сюда ляжет его ответ, и остальной код не изменится.
        self.model = ModelView(None)

        self._build_menu()
        # Del — ОДНО действие на всё окно, разбирающееся по режиму. Два
        # действия на одной клавише Qt считает неоднозначными и не
        # выполняет ни одного: удаление молча не работало ни в эскизе, ни
        # в дереве, хотя обе кнопки на ленте были на месте.
        self._delete_action = QtGui.QAction(self)
        self._delete_action.setShortcut(QtGui.QKeySequence.Delete)
        self._delete_action.setShortcutContext(QtCore.Qt.WindowShortcut)
        self._delete_action.triggered.connect(self._delete_current)
        self.addAction(self._delete_action)
        self.status = self.statusBar()
        self.hint = QtWidgets.QLabel("")
        self.hint.setStyleSheet("color: palette(mid);")
        self.status.addPermanentWidget(self.hint)

        # Что видно при открытии. Плоскости и оси по умолчанию скрыты: на
        # пустой детали они полезны, на готовой — застилают её.
        for key, state in (("view.edges", True), ("view.grid", False),
                           ("view.relations", True), ("view.origin", True),
                           ("view.dimensions", True), ("view.points", True),
                           ("view.regions", True)):
            self.set_toggle(key, state)
        self._refresh_decor()
        self._place_cube()
        self.ribbon.check("sketch.select", True)
        self._set_mode(MODE_MODEL)
        self._refresh(rebuilt=None)

    # --- меню (то, чего нет на ленте) ---

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("Файл")
        file_menu.addAction("Новая деталь", QtGui.QKeySequence.New, self._new_part)
        file_menu.addAction("Открыть…", QtGui.QKeySequence.Open, self._open)
        file_menu.addAction("Сохранить как…", QtGui.QKeySequence.SaveAs, self._save_as)
        file_menu.addSeparator()
        file_menu.addAction("Экспорт STEP…", self._export_step)
        file_menu.addAction("Подготовка к расчёту…", self._to_prep)
        file_menu.addSeparator()
        file_menu.addAction("Выход", QtGui.QKeySequence.Quit, self.close)

        edit_menu = self.menuBar().addMenu("Правка")
        # Отмена пока работает внутри эскиза. В режиме модели её роль
        # выполняет удаление операции из дерева — там история и так видна
        # целиком, и откатывать вслепую нечего.
        edit_menu.addAction("Отменить", QtGui.QKeySequence.Undo, self._undo)
        edit_menu.addAction("Повторить", QtGui.QKeySequence.Redo, self._redo)

        help_menu = self.menuBar().addMenu("Справка")
        help_menu.addAction("О решателях…", self._about_solvers)

    def _undo(self) -> None:
        if self.mode == MODE_SKETCH:
            self.canvas.undo()
            self._sketch_changed()
        else:
            self.status.showMessage(
                "Отмена работает в эскизе; в модели операция удаляется из дерева", 5000
            )

    def _redo(self) -> None:
        if self.mode == MODE_SKETCH:
            self.canvas.redo()
            self._sketch_changed()

    def _about_solvers(self) -> None:
        lines = [
            f"{'+' if info.available else '-'} {info.name} {info.version}\n"
            f"    {info.license} — {info.reason or info.copyleft_scope}"
            for info in available_solvers()
        ]
        current = self.active_sketch or Sketch("проба")
        lines.append(f"\nСейчас работает: {current.solver_info.name}")
        QtWidgets.QMessageBox.information(self, "Решатели эскизов", "\n".join(lines))

    def eventFilter(self, watched, event):
        """Единственный перехватчик событий вида.

        ЕДИНСТВЕННЫЙ намеренно: второй `eventFilter` в классе не
        добавляется к первому, а молча заменяет его — Python оставляет
        последнее определение в теле класса. Так уже пропадали и наложение
        эскиза, и кубик видов: отказа нет, просто перестаёт работать то, о
        чём здесь больше никто не помнит. Всё, что нужно от событий вида,
        разбирается отсюда.
        """
        if watched is not self.viewport:
            return super().eventFilter(watched, event)
        kind = event.type()
        if kind == QtCore.QEvent.Resize:
            self._viewport_resized()
        elif self._arrow_event(kind, event):
            return True
        return super().eventFilter(watched, event)

    def _viewport_resized(self) -> None:
        """Наложения следуют за размером вида.

        Поле эскиза: без этого оно остаётся прежнего размера, и щелчки за
        его краем уходят в трёхмерный вид — рисование обрывается на
        середине окна без всякой видимой причины. Разметка и кубик — то же
        самое: кубик съезжает с угла, а стрелка перестаёт попадать под
        руку там, где её видно.
        """
        for candidate in (self.canvas, self.preview_canvas):
            if candidate.isVisible():
                candidate.setGeometry(self.viewport.rect())
        self.decor.setGeometry(self.viewport.rect())
        self._place_cube()

    # --- режимы ---

    #: Всё, что показывается или прячется. Ключ может иметь кнопку и на
    #: ленте, и на панели видов — состояние у него одно.
    VISIBILITY_KEYS = frozenset({
        "view.edges", "view.grid", "view.relations", "view.dimensions",
        "view.points", "view.origin", "view.axes", "view.planes",
        "view.sketches", "view.perspective", "view.regions",
    })

    #: Ключ переключателя → поле поля эскиза, которым он управляет.
    SKETCH_VISIBILITY = {
        "view.grid": "show_grid",
        "view.relations": "show_relations",
        "view.dimensions": "show_dimensions",
        "view.points": "show_points",
        "view.regions": "show_regions",
    }

    def _refresh_decor(self) -> None:
        """Собрать разметку вида: плоскости и эскизы детали.

        Правящийся эскиз в разметку не попадает: он уже нарисован полем
        эскиза поверх вида, и вторая копия под ним дала бы двойные линии.
        """
        planes = list(plane_module.STANDARD.values())
        seen = {plane.name for plane in planes}
        sketches = []
        for sketch in self.document.sketches:
            if sketch is self.active_sketch:
                continue
            sketches.append(sketch)
            if sketch.plane.name not in seen:
                planes.append(sketch.plane)
                seen.add(sketch.plane.name)
        self.decor.planes = planes
        self.decor.sketches = sketches

    def _set_mode(self, mode: str) -> None:
        if self.session is not None:
            self._cancel_command()
        self.mode = mode
        sketching = mode == MODE_SKETCH
        if self.session is None:
            self.side.setCurrentIndex(1 if sketching else 0)
        self.ribbon.set_tab_enabled("sketch", sketching)
        # Вкладка операций остаётся доступной и в эскизе: выбрать
        # «Выдавливание», не выходя из эскиза, — обычный порядок работы, и
        # запрещать его значит заставлять делать лишний шаг.
        self.ribbon.set_tab_enabled("part", True)
        self.ribbon.show_tab("sketch" if sketching else "part")
        # В эскизе мышь принадлежит эскизу, а деталь уходит на второй план:
        # тонкие линии на полностью освещённом теле не читаются.
        self._show_overlay(self.canvas if sketching else None)
        self.viewport.dim = 0.55 if sketching else 0.0
        self.viewport.pick_kinds = (
            () if sketching else ("vertex", "edge", "face", "body")
        )
        if not sketching:
            self.viewport.hover_face = 0
        self.viewport.setFocus()
        self.viewport.update()

    def _enter_sketch(self, sketch: Sketch, operation=None) -> None:
        self.active_sketch = sketch
        self.active_operation = operation
        # Снимок «как было» для отказа от правок. Берётся ДО первого
        # изменения: восстанавливать по журналу отмены нельзя — отмена
        # хранит шаги, а нужен весь эскиз целиком.
        self._sketch_before = sketch.to_dict()
        self._sketch_is_new = not any(
            not segment.construction for segment in sketch.segments
        )
        self.confirm.show()
        self._place_cube()
        self.canvas.sketch = sketch
        self.canvas.selection.clear()
        self.canvas.selected_regions = list(
            (operation.regions if operation is not None else None)
            or self._pending_regions.get(id(sketch)) or [])
        self.canvas.projector = sketch_view.PlaneProjector(self.viewport, sketch.plane)
        # Размер умеет спросить про грань детали под курсором — этим.
        self.canvas.face_probe = self._part_reference_at
        self.canvas.resize(self.viewport.size())
        self.canvas.set_tool("sketch.select")
        self._look_at_plane(sketch.plane)
        self.ribbon.clear_checks("sketch")
        self.ribbon.check("sketch.select", True)
        self._set_mode(MODE_SKETCH)
        self._sketch_changed()
        self.setWindowTitle(
            f"ProtoCAD — {self.document.designation} — "
            f"эскиз «{sketch.name}» на плоскости {sketch.plane.name}"
        )

    #: Чем указывают на деталь и что при этом принимает вид.
    MODEL_PICKING = {
        "convert": (("edge",),
                    "Перенос: щёлкайте рёбра детали — они лягут в эскиз."),
        "reference": (("vertex", "edge", "face"),
                      "Ссылка: щёлкайте вершины, рёбра и грани детали — "
                      "они станут опорой для связей эскиза."),
    }

    def _set_model_picking(self, mode: str) -> None:
        """Режим указания на ДЕТАЛЬ из эскиза: мышь уходит виду, а не эскизу.

        Поле эскиза лежит поверх вида и обычно перехватывает мышь; на время
        такого режима оно становится прозрачным для щелчков, иначе указать
        ребро невозможно — палец всегда попадает в эскиз.
        """
        self.model_picking = mode
        kinds, hint = self.MODEL_PICKING.get(mode, ((), ""))
        self.canvas.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, bool(mode))
        self.viewport.pick_kinds = kinds
        self.canvas.set_tool("sketch.select")
        if hint:
            self.hint.setText(hint)
        self.viewport.update()

    @property
    def converting(self) -> bool:
        return self.model_picking == "convert"

    def _set_converting(self, on: bool) -> None:
        self._set_model_picking("convert" if on else "")

    def _reference_vertex(self, position) -> None:
        """Взять вершину детали опорой для связей эскиза."""
        sketch = self.active_sketch
        if sketch is None:
            return
        x, y = sketch.plane.project(position)
        source = _vertex_source(self.model.points(), position)
        for existing in sketch.references:
            if existing.external == source:
                self.status.showMessage("Эта вершина уже перенесена", 4000)
                return
        self.canvas._begin_change()
        point = sketch.reference_point(x, y, source)
        self.canvas._after_change()
        self._sketch_changed()
        self.status.showMessage(
            f"{point.label}: вершина детали в эскизе ({x:.3f}, {y:.3f}). "
            f"Свяжите с ней объект — совпадением, размером, «точкой на отрезке».",
            7000,
        )

    def _pick_model_from_sketch(self, position) -> None:
        """Щелчок в эскизе пришёлся мимо — посмотреть, нет ли там детали.

        Отдельный режим для этого не нужен: указывают на вершину, ребро
        или грань тем же щелчком, что и на объект эскиза. Найденное сразу
        становится опорой — вспомогательной точкой или прямой, — и к нему
        прикладывают обычные связи.
        """
        if self.mode != MODE_SKETCH or self.model.empty:
            return
        if self.active_sketch is None or self.model_picking:
            return
        vertex = self.viewport.pick_vertex(position)
        if vertex is not None:
            self._reference_vertex(vertex)
            return
        edge = self.viewport.pick_edge(position)
        index = edge[0] if isinstance(edge, tuple) else edge
        if index is not None and index >= 0:
            self._reference_edge(int(index))
            return
        face = self.viewport.pick_face(position)
        if face:
            self._reference_face(int(face) - 1)

    def _reference_face(self, index: int) -> None:
        """Взять плоскую грань детали опорой для связей эскиза.

        Грань — не линия, и напрямую связью её не возьмёшь. В эскиз
        ложится СЛЕД грани: прямая, по которой её плоскость пересекает
        плоскость эскиза. К этому следу прикладывают параллельность,
        перпендикуляр и совпадение — то, ради чего грань и указывают.

        Грань, параллельная плоскости эскиза, следа не даёт: пересечения
        нет. Про такую честно говорим, что опорой она не станет, и
        предлагаем перенести её рёбра.
        """
        sketch = self.active_sketch
        if sketch is None or self.model.empty:
            return
        other = self.model.plane_of(index)
        if other is None:
            self.status.showMessage(
                "Грань не плоская — опорой для связей не станет", 5000)
            return
        trace = _plane_trace(sketch.plane, other)
        if trace is None:
            self.status.showMessage(
                "Грань параллельна плоскости эскиза — следа не даёт. "
                "Перенесите её рёбра через «Преобразовать».", 7000)
            return
        faces = len(self.model.faces)
        source = f"f:{index}/{faces}:{_face_source_of(self.model.face(index))}"
        for existing in sketch.references:
            if existing.external.split("#")[0] == source:
                self.status.showMessage("Эта грань уже перенесена", 4000)
                return
        (x1, y1), (x2, y2) = trace
        self.canvas._begin_change()
        segment = sketch.reference_line(x1, y1, x2, y2, source)
        self.canvas._after_change()
        self._sketch_changed()
        self.status.showMessage(
            f"{segment.label}: след грани детали в эскизе. Приложите к нему "
            f"параллельность, перпендикуляр или совпадение.", 7000)

    #: Насколько близко к ребру надо целиться, чтобы указать ЕГО, а не
    #: грань под ним, пикселей. Обычные восемь тут не годятся: ребро есть у
    #: каждой грани, и с таким запасом до самой грани не дотянуться —
    #: ребро перехватывает почти везде.
    EDGE_REACH = 4.0

    def _part_reference_at(self, position):
        """Подэлемент детали под курсором → его след в эскизе.

        Нужно размеру: указать грань или ребро хочется прямо в размере, а
        не отдельной командой, после которой в эскизе появляется прямая, и
        её ещё надо найти среди своих. След заводится сам и повторно не
        плодится — на один подэлемент он один.

        Возвращается ОБЫЧНЫЙ отрезок эскиза: дальше размер работает с ним
        так же, как с нарисованным от руки, и отдельного случая «размер до
        детали» в нём не появляется.

        Сперва спрашивается РЕБРО: оно тоньше грани и лежит на её краю, а
        указывают его прицельно. Спроси мы грань первой, попасть в ребро
        было бы нечем — оно всегда на какой-нибудь грани.
        """
        sketch = self.active_sketch
        if sketch is None or self.model.empty:
            return None
        # Через ГЛОБАЛЬНЫЕ координаты: `mapFrom` переводит только между
        # родителем и потомком, а поле эскиза виду не потомок — оно лежит
        # поверх него. Прямой перевод давал бы сдвинутую точку, и грань
        # находилась бы не та либо не находилась вовсе.
        spot = self.viewport.mapFromGlobal(
            self.canvas.mapToGlobal(position.toPoint()))
        found = self.viewport.pick_edge(spot, self.EDGE_REACH)
        if found is not None:
            return self._edge_reference(sketch, int(found[0]))
        index = int(self.viewport.pick_face(spot)) - 1
        if index < 0:
            return None
        plane = self.model.plane_of(index)
        if plane is None:
            self.status.showMessage(
                "Грань не плоская — размер к ней не ставится", 5000)
            return None
        trace = _plane_trace(sketch.plane, plane)
        if trace is None:
            self.status.showMessage(
                "Грань параллельна плоскости эскиза — следа не даёт, "
                "и расстояние до неё в эскизе не строится", 7000)
            return None
        source = (f"f:{index}/{len(self.model.faces)}:"
                  f"{_face_source_of(self.model.face(index))}")
        for segment in sketch.segments:
            if getattr(segment, "external", "") == source:
                return segment
        (x1, y1), (x2, y2) = trace
        self.canvas._begin_change()
        return sketch.reference_line(x1, y1, x2, y2, source)

    def _edge_reference(self, sketch, index: int):
        """Прямое ребро детали → его след в эскизе. ``None`` — не годится."""
        ends = self.model.straight_edge(index)
        if not ends:
            self.status.showMessage(
                "Ребро криволинейное — расстояние до него в эскизе "
                "не строится. Укажите грань либо прямое ребро", 6000)
            return None
        first, last = (sketch.plane.project(ends[0]),
                       sketch.plane.project(ends[1]))
        if abs(first[0] - last[0]) < 1e-9 and abs(first[1] - last[1]) < 1e-9:
            self.status.showMessage(
                "Ребро смотрит в плоскость эскиза торцом — прямой не даёт, "
                "и расстояние до него не построить", 6000)
            return None
        source = (f"e:{index}/{len(self.model.edges)}:"
                  f"{_edge_source_of(self.model.edge(index))}")
        for segment in sketch.segments:
            if getattr(segment, "external", "") == source:
                return segment
        self.canvas._begin_change()
        return sketch.reference_line(first[0], first[1], last[0], last[1],
                                     source)

    def _reference_edge(self, index: int) -> None:
        """Взять прямое ребро детали опорой для параллельности и перпендикуляра."""
        sketch = self.active_sketch
        if sketch is None or self.model.empty:
            return
        ends = self.model.straight_edge(index)
        if not ends:
            self.status.showMessage(
                "Ребро криволинейное — опорой для параллельности не станет. "
                "Для переноса в контур есть «Преобразовать ребро».", 6000)
            return
        first, last = ends
        source = (f"e:{index}/{len(self.model.edges)}:"
                  f"{_edge_source_of(self.model.edge(index))}")
        for existing in sketch.references:
            if existing.external == source:
                self.status.showMessage("Это ребро уже перенесено", 4000)
                return
        (x1, y1), (x2, y2) = (sketch.plane.project(first), sketch.plane.project(last))
        if abs(x1 - x2) < 1e-9 and abs(y1 - y2) < 1e-9:
            self.status.showMessage(
                "Ребро смотрит в плоскость эскиза торцом — прямой не даёт", 5000)
            return
        self.canvas._begin_change()
        segment = sketch.reference_line(x1, y1, x2, y2, source)
        self.canvas._after_change()
        self._sketch_changed()
        self.status.showMessage(
            f"{segment.label}: ребро детали в эскизе. Приложите к нему "
            f"параллельность или перпендикуляр.", 7000)

    def _convert_edge(self, index: int) -> None:
        """Перенести указанное ребро в правящийся эскиз."""
        if self.active_sketch is None or self.model.empty:
            return
        edge = self.model.edge(index)
        if edge is None:
            self.status.showMessage("Ребро не найдено в теле", 4000)
            return
        self.canvas._begin_change()
        result = convert.from_entities(self.active_sketch, [edge])
        if not result.created:
            self.status.showMessage("Из этого ребра контур не получился", 4000)
            return
        message = f"перенесено объектов: {len(result.created)}"
        if result.note:
            message += f"; {result.note}"
        self.status.showMessage(message, 5000)
        self.canvas._after_change()
        self._sketch_changed()

    def _sketch_intersect(self) -> None:
        """Кривая пересечения: след детали на плоскости правящегося эскиза.

        Ребро за ребром это не обвести: след проходит там, где у детали
        никакого ребра нет — по гладкой боковине цилиндра, например.
        Считает его ядро (`Document.section`), а строится он теми же
        кривыми, что и перенос ребра: окружность окружностью, прямая
        прямой.
        """
        sketch = self.active_sketch
        if sketch is None:
            return
        if self.model.empty:
            self.status.showMessage("Детали ещё нет — пересекать нечего", 4000)
            return
        answer = self.document.section(sketch.plane)
        if not answer.ok:
            self.status.showMessage(
                answer.message or "Сечение не посчиталось", 6000)
            return
        if not answer.edges:
            # Пустой след — законный ответ, а не поломка: плоскость эскиза
            # проходит мимо детали. Сказать надо именно это, иначе человек
            # ищет отказ там, где его нет.
            self.status.showMessage(
                "Плоскость эскиза не пересекает деталь — след пуст", 6000)
            return
        self.canvas._begin_change()
        result = convert.from_entities(sketch, answer.edges)
        if not result.created:
            self.canvas._after_change()
            self.status.showMessage("Из следа контур не получился", 4000)
            return
        message = f"след детали: объектов {len(result.created)}"
        if result.note:
            message += f"; {result.note}"
        self.status.showMessage(message, 7000)
        self.canvas._after_change()
        self._sketch_changed()

    def _look_normal(self) -> None:
        """Развернуть вид перпендикулярно плоскости правящегося эскиза."""
        if self.active_sketch is not None:
            self._look_at_plane(self.active_sketch.plane)
            self.canvas.update()

    def _look_at_plane(self, plane) -> None:
        """Повернуть камеру нормально к плоскости эскиза.

        Рисовать под углом можно, но начинают всегда «в плоскости»: под
        косым взглядом даже прямоугольник получается кривым, потому что
        глаз врёт про длины.
        """
        normal = plane.normal
        self.viewport.pitch = math.degrees(
            math.asin(max(-1.0, min(1.0, normal[2])))
        )
        if abs(normal[2]) < 0.999:
            self.viewport.yaw = math.degrees(math.atan2(normal[1], normal[0]))
        else:
            self.viewport.yaw = 0.0
        # Взгляд строго по нормали даёт вырожденный поворот камеры, поэтому
        # отклоняемся на волос: плоскость остаётся фронтальной, а «верх»
        # определён.
        self.viewport.pitch = max(-89.4, min(89.4, self.viewport.pitch))
        # Без вписывания камера остаётся там, где её оставили в модели, и
        # эскиз открывается в произвольном приближении — иногда упёртым в
        # одну грань.
        self.viewport.zoom = 1.0
        self.viewport._pan[:] = 0.0
        # «Верх» экрана — вторая ось плоскости. Без этого при взгляде
        # сверху эскиз ложится повёрнутым на прямой угол: ось X уходит
        # вверх экрана, и нарисованный прямоугольник 100 × 60 выглядит
        # как 60 × 100.
        import numpy as _np

        self.viewport.up_hint = _np.array(plane.y_direction, _np.float32)
        self.viewport.update()

    def _cancel_sketch(self) -> None:
        """Выйти из эскиза, отменив всё, что в нём сделано.

        Эскиз, созданный в этот заход и оставшийся пустым, из дерева
        убирается: пустой эскиз ничего не строит, а место в дереве
        занимает и сбивает счёт операций.
        """
        sketch = self.active_sketch
        if sketch is None:
            return
        try:
            sketch.restore(self._sketch_before)
        except Exception as error:  # noqa: BLE001
            self.status.showMessage(f"Не удалось отменить правки: {error}", 6000)
        drop = self._sketch_is_new
        self.canvas.selection.clear()
        self.canvas.reset_history()
        self._finish_sketch(applied=False)
        if drop and sketch in self.document.sketches:
            self.document.remove_sketch(sketch)
            self._rebuild(force=True)
        self.status.showMessage("Правки эскиза отменены", 4000)

    def _finish_sketch(self, applied: bool = True) -> None:
        self.confirm.hide()
        self._place_cube()
        if applied and self.active_sketch is not None:
            # Выбранные области запоминаются при ЭСКИЗЕ: именно они станут
            # профилем. Пустой выбор означает «весь эскиз», где вложенные
            # контуры — отверстия; это не то же самое, что выбор всех
            # областей, при котором отверстия исчезают.
            chosen = list(self.canvas.selected_regions)
            self._pending_regions[id(self.active_sketch)] = chosen
            for operation in self.document.operations:
                if operation.sketch is self.active_sketch:
                    operation.regions = chosen
        self.active_sketch = None
        self.active_operation = None
        self.canvas.projector = None
        self.canvas.face_probe = None
        self._set_model_picking("")
        import numpy as _np

        self.viewport.up_hint = _np.array([0.0, 0.0, 1.0], _np.float32)
        self._set_mode(MODE_MODEL)
        self.setWindowTitle(f"ProtoCAD — {self.document.designation}")
        self._rebuild()

    # --- лента ---

    def _switch(self, key: str, on: bool) -> None:
        """Переключаемая кнопка: инструмент рисования или режим показа."""
        if key in self.VISIBILITY_KEYS:
            # Ключ живёт в двух местах; вторая кнопка догоняет молча.
            self.ribbon.check(key, on, silent=True)
            self.view_bar.check(key, on)
        if key == "view.edges":
            self.viewport.show_edges = on
            self.viewport.update()
            return
        if key in self.SKETCH_VISIBILITY:
            for canvas in (self.canvas, self.preview_canvas):
                setattr(canvas, self.SKETCH_VISIBILITY[key], on)
                canvas.update()
            return
        if key in ("view.planes", "view.origin", "view.axes"):
            setattr(self.decor, f"show_{key.split('.')[1]}", on)
            self._refresh_decor()
            self.decor.update()
            return
        if key == "view.sketches":
            self.decor.show_sketches = on
            self._refresh_decor()
            self.decor.update()
            return
        if key == "view.perspective":
            self.viewport.perspective = on
            self.viewport.update()
            self.status.showMessage(
                "Перспектива включена — для показа; мерить глазом по экрану нельзя"
                if on else "Ортогональная проекция", 4000
            )
            return

        if self.mode != MODE_SKETCH:
            return
        # Числа берутся из полей ленты, а не спрашиваются окном: радиус
        # скругления нужен ПЕРЕД щелчком по чертежу, и закрывать чертёж
        # диалогом ровно в этот момент — худшее, что можно сделать.
        self.canvas.polygon_sides = int(self.ribbon.value("value.sides", 6))
        self.canvas._fillet_radius = self.ribbon.value("value.fillet", 5.0)
        self.canvas._chamfer_distance = self.ribbon.value("value.chamfer", 5.0)
        if key in ("draw.convert", "draw.reference"):
            self._set_model_picking(key.split(".")[1] if on else "")
            return
        self._set_model_picking("")
        self.canvas.set_tool(key if on else "sketch.select")
        if not on:
            self.ribbon.check("sketch.select", True)

    def _command(self, key: str) -> None:
        handlers = {
            "sketch.finish": self._finish_sketch,
            "sketch.normal": self._look_normal,
            "edit.construction": self._toggle_construction,
            "edit.delete": lambda: self.canvas.delete_selected(),
            "edit.mirror": self._sketch_mirror,
            "edit.transform": self._sketch_transform,
            "dim.define": self._sketch_define,
            "edit.check": self._sketch_check,
            "edit.repair": self._sketch_repair,
            "edit.pattern_linear": self._sketch_pattern_linear,
            "edit.pattern_circular": self._sketch_pattern_circular,
            "edit.offset": self._sketch_offset,
            "draw.intersect": self._sketch_intersect,
            "part.sketch": self._new_sketch,
            "part.edit": self._edit_current,
            "part.delete": self._delete_feature,
            "part.rebuild": lambda: self._rebuild(force=True),
            "view.fit": self._fit,
            "view.front": lambda: self._view("front"),
            "view.back": lambda: self._view("back"),
            "view.left": lambda: self._view("left"),
            "view.right": lambda: self._view("right"),
            "view.top": lambda: self._view("top"),
            "view.bottom": lambda: self._view("bottom"),
            "view.iso": lambda: self._view("iso"),
            "view.dimetric": lambda: self._view("dimetric"),
        }
        if key in handlers:
            handlers[key]()
            return
        if key.startswith("dim.") and self.mode == MODE_SKETCH:
            # Вид размера выбирается из списка, а нажатой остаётся одна
            # кнопка «Размер»: инструмент всё равно один.
            self.canvas.set_tool(key)
            self.ribbon.check("dim.smart", True, silent=True)
            return
        if key.startswith("rel."):
            self.canvas.apply_relation(key[4:])
            self._sketch_changed()
            return
        if key.startswith("part."):
            descriptor = D.BY_KEY.get(key[5:])
            if descriptor is None:
                # Раньше отсюда был запасной путь в модальное окно с
                # полями. Он убран: модальное окно останавливает работу и
                # ждёт нажатия, а панель незавершённой команды показывает
                # предпросмотр и даёт править выбор. Кнопка без описания —
                # это недоделка ленты, и молчать о ней нельзя.
                self.status.showMessage(
                    f"Команда «{key}» не описана: сообщите об этом", 5000)
                return
            if self.mode == MODE_SKETCH:
                # Операция, выбранная ИЗ эскиза, сначала его закрывает.
                # Так и работают: нарисовал контур и тут же вытянул, не
                # разрывая действие на «закрыть эскиз» и «выбрать команду».
                # Эскиз при этом уходит внутрь операции в дереве — он её
                # исходные данные, а не отдельный шаг.
                self._finish_sketch()
            self._start_command(descriptor)
            return

    def _fit(self) -> None:
        if self.mode == MODE_SKETCH:
            self.canvas.fit_view()
        else:
            self.viewport.fit_view()

    def _orient(self, yaw: float, pitch: float, up=None) -> None:
        """Поставить камеру в заданное положение.

        Верх экрана задаётся вместе с углами. Без него взгляд строго сверху
        разворачивает деталь на произвольный угол: направление взгляда
        совпадает с осью Z, и «вверх» становится неопределённым.
        """
        if self.mode == MODE_SKETCH:
            # В эскизе вид закреплён на его плоскости. Молчать нельзя:
            # кубик нажали, ничего не произошло, и непонятно — сломано или
            # так задумано.
            self.status.showMessage(
                "В эскизе вид закреплён на его плоскости. "
                "Закройте эскиз или поверните вид мышью.", 5000)
            return
        import numpy as _np

        self.viewport.yaw = float(yaw)
        self.viewport.pitch = float(pitch)
        if up is not None:
            self.viewport.up_hint = _np.array(up, _np.float32)
        self.view_cube.follow(yaw, pitch, up)
        self.viewport.update()

    def _view(self, key: str) -> None:
        """Именованный вид. Углы берутся из той же таблицы, что у кубика."""
        direction = view_cube.SIDES[key][0]
        self._orient(*view_cube.angles(direction), view_cube.up_for(direction))

    def set_toggle(self, key: str, on: bool) -> None:
        """Переключатель показа, где бы его кнопка ни жила.

        Один и тот же ключ бывает и на ленте, и на панели поверх картинки:
        «Сетка» нужна под рукой в эскизе и в общем списке видимости.
        Состояние у него одно, поэтому и вход один — иначе две кнопки
        начинают показывать разное.
        """
        self._switch(key, on)
        self.ribbon.check(key, on, silent=True)
        self.view_bar.check(key, on)

    def _build_view_bar(self) -> OverlayBar:
        """Панель видов поверх картинки.

        Прежде это была вкладка ленты. Пользоваться видами приходится
        постоянно и вперемешку с построением, а уход на другую вкладку
        стоит двух лишних движений и теряет то, что было под рукой.
        """
        bar = OverlayBar(self.viewport)
        bar.add("view.fit", "view_fit", "Показать деталь целиком  (F)")
        bar.add_menu("view.orientation", "view_orientation", "Ориентация вида", [
            ("view.front", "Спереди", False),
            ("view.back", "Сзади", False),
            ("view.left", "Слева", False),
            ("view.right", "Справа", False),
            ("view.top", "Сверху", False),
            ("view.bottom", "Снизу", False),
            ("-", "", False),
            ("view.iso", "Изометрия", False),
            ("view.dimetric", "Диметрия", False),
            ("-", "", False),
            ("sketch.normal", "Нормально к плоскости эскиза", False),
        ])
        bar.separator()
        bar.add("view.edges", "line", "Показывать рёбра  (E)", checkable=True)
        bar.add_menu("view.visibility", "visibility", "Условия видимости", [
            ("view.origin", "Начало координат", True),
            ("view.axes", "Оси координат", True),
            ("view.planes", "Плоскости", True),
            ("view.sketches", "Эскизы", True),
            ("-", "", False),
            ("view.relations", "Связи эскиза", True),
            ("view.dimensions", "Размеры эскиза", True),
            ("view.points", "Точки эскиза", True),
            ("view.regions", "Закрашенные области эскиза", True),
            ("view.grid", "Сетка эскиза", True),
        ])
        bar.add("view.perspective", "perspective",
                "Перспективная проекция вместо ортогональной", checkable=True)
        bar.activated.connect(self._command)
        bar.switched.connect(self._switch)
        bar.adjustSize()
        return bar

    def _place_cube(self) -> None:
        """Разложить наложения по углам картинки.

        Кубик уходит ВНИЗ под угол подтверждения: в эскизе тот занимает
        правый верхний угол, и наложить их друг на друга значило бы
        закрыть одно другим ровно тогда, когда нужны оба.
        """
        margin = ViewCube.MARGIN
        width = self.viewport.width()
        self.view_bar.adjustSize()
        self.view_bar.move(max(margin, (width - self.view_bar.width()) // 2), 6)
        self.view_bar.raise_()

        self.confirm.adjustSize()
        top = 6
        self.confirm.move(max(0, width - self.confirm.width() - margin), top)
        self.confirm.raise_()

        below = (self.confirm.height() + 10) if self.confirm.isVisible() else 0
        self.view_cube.move(
            max(0, width - self.view_cube.width() - margin), margin + below
        )
        self.view_cube.raise_()

    def _toggle_construction(self) -> None:
        self.canvas.toggle_construction()
        self._sketch_changed()

    def _tool_changed(self, key: str) -> None:
        """Показать в ленте тот инструмент, который включён на самом деле.

        Молча: лента здесь ДОГОНЯЕТ поле, а не командует им. Сигнал обратно
        замкнул бы связку в кольцо.
        """
        self.ribbon.check(key, True, silent=True)

    def _tool_finished(self) -> None:
        self.ribbon.clear_checks("sketch")
        self.ribbon.check("sketch.select", True)
        self.canvas.set_tool("sketch.select")

    # --- инструменты эскиза, которым нужен диалог ---

    def _selected_segments(self) -> list:
        return [e for e in self.canvas.selection if hasattr(e, "points")]

    def _sketch_offset(self) -> None:
        segments = self._selected_segments()
        if not segments:
            self.status.showMessage("Смещение: выберите объекты контура", 4000)
            return
        value, ok = QtWidgets.QInputDialog.getDouble(
            self, "Смещение", "Расстояние, мм (знак задаёт сторону):", 5.0, -1e5, 1e5, 3
        )
        if not ok:
            return
        self._apply_tool(lambda: T.offset(self.canvas.sketch, segments, value))

    def _sketch_check(self) -> None:
        """Найти, из-за чего эскиз не даёт контура (спецификация, 050).

        Только ИЩЕТ. Чинить по этой кнопке нельзя: у совпавших объектов и
        ветвления однозначного исправления нет, и молчаливая починка
        испортила бы эскиз незаметно.
        """
        sketch = self.canvas.sketch
        if sketch is None:
            return
        found = repair_module.scan(sketch)
        if not found:
            self.status.showMessage("Эскиз в порядке: ничего не найдено", 5000)
            return
        # Найденное ВЫДЕЛЯЕТСЯ: «есть разрыв» без «вот он» не помогает.
        self.canvas.selection = list(
            {id(item): item for problem in found
             for item in problem.segments}.values())
        self.canvas.update()
        fixable = sum(1 for problem in found if problem.fixable)
        tail = (f"; исправимых {fixable} — кнопка «Исправить»"
                if fixable else "; исправлять за вас нечего")
        self.status.showMessage(
            f"Найдено {len(found)}: {found[0].text}{tail}", 12000)

    def _sketch_repair(self) -> None:
        """Исправить то, что исправляется однозначно."""
        sketch = self.canvas.sketch
        if sketch is None:
            return
        result = repair_module.repair(sketch, repair_module.scan(sketch))
        if result.fixed:
            self._sketch_changed()
        self.status.showMessage(result.note, 8000)

    def _sketch_define(self) -> None:
        """Довести эскиз до нуля степеней свободы (спецификация, 051).

        Работает по ВЫБРАННОМУ, а без выбора — по всему эскизу: чаще всего
        именно это и нужно, а требовать выделить всё значило бы просить
        лишнее действие ради того же самого.
        """
        sketch = self.canvas.sketch
        if sketch is None:
            return
        segments = self._selected_segments() or None
        try:
            result = T.fully_define(sketch, segments)
        except T.ToolError as failure:
            self.status.showMessage(str(failure), 5000)
            return
        self._sketch_changed()
        self.status.showMessage(result.note, 8000)

    def _sketch_transform(self) -> None:
        """Перенос, поворот или масштаб выбранного (спецификация, 038).

        Точка отсчёта вводится числами, а не указывается в виде: указание
        мышью — отдельная работа по вводу, и делать вид, что оно есть,
        нельзя. Записано TRANSFORM-GAP-002.
        """
        segments = self._selected_segments()
        if not segments:
            self.status.showMessage(
                "Преобразование: выберите объекты", 4000)
            return
        dialog = _TransformDialog(self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        values = dialog.values()
        mode = values["mode"]
        sketch = self.canvas.sketch
        keep = values["keep_relations"]
        copy = values["copy"]
        if mode == "Перенести":
            self._apply_tool(lambda: T.move(
                sketch, segments, (values["dx"], values["dy"]),
                copy=copy, keep_relations=keep))
        elif mode == "Повернуть":
            self._apply_tool(lambda: T.rotate(
                sketch, segments, (values["x"], values["y"]),
                values["angle"], copy=copy, keep_relations=keep))
        else:
            self._apply_tool(lambda: T.scale(
                sketch, segments, (values["x"], values["y"]),
                values["factor"], copy=copy, keep_relations=keep))

    def _sketch_mirror(self) -> None:
        segments = self._selected_segments()
        axes = [s for s in segments if s.kind == "line" and s.construction]
        if not axes:
            axes = [s for s in segments if s.kind == "line"]
        if len(segments) < 2 or not axes:
            self.status.showMessage(
                "Зеркало: выберите объекты и осевую линию (лучше вспомогательную)", 5000
            )
            return
        axis = axes[-1]
        source = [s for s in segments if s is not axis]
        self._apply_tool(lambda: T.mirror(self.canvas.sketch, source, axis))

    def _sketch_pattern_linear(self) -> None:
        segments = self._selected_segments()
        if not segments:
            self.status.showMessage("Массив: выберите объекты", 4000)
            return
        dialog = _LinearPatternDialog(self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        v = dialog.values()
        self._apply_tool(lambda: T.linear_pattern(
            self.canvas.sketch, segments, v["dx"], v["dy"], v["count"],
            v["dx2"], v["dy2"], v["count2"],
        ))

    def _sketch_pattern_circular(self) -> None:
        segments = self._selected_segments()
        if not segments:
            self.status.showMessage("Массив: выберите объекты", 4000)
            return
        dialog = _CircularPatternDialog(self)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        v = dialog.values()
        self._apply_tool(lambda: T.circular_pattern(
            self.canvas.sketch, segments, (v["x"], v["y"]), v["count"], v["angle"]
        ))

    def _apply_tool(self, action) -> None:
        try:
            result = action()
        except Exception as error:  # noqa: BLE001 — отказ показываем, а не глотаем
            QtWidgets.QMessageBox.warning(self, "ProtoCAD", str(error))
            return
        self.canvas.selection.clear()
        self._sketch_changed()
        message = f"создано объектов: {len(result.created)}"
        if result.note:
            message += f"; {result.note}"
        self.status.showMessage(message, 5000)

    def _sketch_changed(self) -> None:
        if self.active_sketch is None:
            return
        self.sketch_panel.fill(self.active_sketch, self.canvas.selection, self.canvas)
        self._show_line_properties()
        self.canvas.update()
        self._sketch_status(self.canvas._status_text())

    def _sketch_to_session(self, sketch, label: str) -> bool:
        """Отдать эскиз активному полю команды. ``True`` — приняли."""
        if self.session is None:
            return False
        box = self.session.active_box
        if box is None or "sketch" not in box.accepts:
            # Поле ждёт не эскиз — щелчок ему не адресован, и трогать
            # набранное нельзя (§47: несовместимое не сбрасывает выбор).
            return False
        result = self.session.click(
            Pick("sketch", 0, sketch.name, sketch))
        if result == "rejected":
            self.status.showMessage(self.session.diagnostics, 5000)
            return True
        self.panel.refresh()
        self._sync_pick_kinds()
        self._update_preview()
        self.status.showMessage(f"{sketch.name}: взят в «{box.label}»", 4000)
        return True

    def _show_command_panel(self, on: bool) -> None:
        """Показать панель команды рядом с деревом либо убрать её.

        Дерево при этом ОСТАЁТСЯ: команда просит указать в нём операцию
        или эскиз, и прятать его на время команды значит просить
        невозможного.
        """
        self.panel.setVisible(bool(on))
        if on:
            self.side.setCurrentIndex(0)      # дерево детали
            self.left.setSizes([320, 260])
        else:
            self.left.setSizes([0, 380])

    def _show_line_properties(self) -> None:
        """Показать свойства отрезка, если выбран ровно один.

        Ровно один: у двух выбранных «длина» и «угол» — числа неизвестно
        чьи, а показать их всё равно чем-то придётся. Лучше не показывать.
        """
        if self.session is not None or self.mode != MODE_SKETCH:
            return
        chosen = [item for item in self.canvas.selection
                  if getattr(item, "kind", "") == "line"]
        if len(chosen) == 1 and len(self.canvas.selection) == 1:
            self.line_panel.show_segment(self.active_sketch, chosen[0])
            self.side.setCurrentWidget(self.line_panel)
        elif self.side.currentWidget() is self.line_panel:
            self.line_panel.show_segment(self.active_sketch, None)
            self.side.setCurrentWidget(self.sketch_panel)

    def _hole_stack_changed(self) -> None:
        """Стек правили — пересчитать предпросмотр незавершённой команды."""
        if self.session is not None:
            self.panel.refresh()
            self._update_preview()

    def _edit_hole_stack(self) -> None:
        """Открыть редактор стека для правящегося отверстия."""
        if self.session is None or self.session.descriptor.key != "hole_feature":
            self.status.showMessage(
                "Редактор стека открывается при незавершённой команде "
                "«Отверстие»", 5000)
            return
        import descriptors as descriptors_module

        definition = descriptors_module._hole_definition(self.session)
        self.session.stack_definition = definition
        self.hole_stack.show_definition(definition)
        self.side.setCurrentWidget(self.hole_stack)

    def _line_edited(self) -> None:
        """Отрезок правили числами в панели — эскиз пересчитан ею самой."""
        self.sketch_panel.fill(self.active_sketch, self.canvas.selection,
                               self.canvas)
        self.canvas.update()
        self._sketch_status(self.canvas._status_text())

    def _sketch_status(self, text: str) -> None:
        if self.mode == MODE_SKETCH:
            self.status.showMessage(text)
            # Смена ВЫБОРА приходит сюда, а не в `changed`: выбор геометрию
            # не меняет. Панель свойств зависит именно от выбора, поэтому
            # переключается здесь.
            self._show_line_properties()

    def _toggle_region_from_panel(self, number: int, on: bool) -> None:
        found = self.canvas.regions()
        if not 0 <= number < len(found):
            return
        region = found[number]
        if self.canvas.region_is_picked(region) == on:
            return
        self.canvas.toggle_region(region, add=True)
        self._sketch_changed()

    def _remove_relation(self, relation) -> None:
        try:
            self.canvas.sketch.delete_constraint(relation)
        except Exception as error:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "ProtoCAD", str(error))
            return
        self._sketch_changed()

    def _select_from_panel(self, entity) -> None:
        if hasattr(entity, "points") or hasattr(entity, "handle"):
            self.canvas.selection = [entity]
            self.canvas.update()

    # --- файл ---

    def _open(self) -> None:
        name, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Открыть", str(ROOT / "work"),
            "ProtoCAD (*.prcadPart *.prcadAsm);;Все файлы (*)",
        )
        if not name:
            return
        fresh = Document("Деталь", backend=self.document.backend)
        fresh.document_id = f"деталь:{Path(name).stem}"
        try:
            fmt.read_document(name, fresh)
        except Exception as error:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "ProtoCAD", str(error))
            return
        # Прежний документ движка больше не нужен: деталь в окне одна.
        self.document.forget()
        self.document = fresh
        self.path = Path(name)
        self._set_mode(MODE_MODEL)
        self._refresh(rebuilt=None)
        self.setWindowTitle(f"ProtoCAD — {self.document.designation}")

    def _save_as(self) -> None:
        suggested = str(ROOT / "work" / self.document.designation)
        name, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Сохранить как", suggested,
            "ProtoCAD (*.prcadPart *.prcadAsm);;Все файлы (*)",
        )
        if not name:
            return
        try:
            self.path = fmt.write_document(self.document, name)
        except Exception as error:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "ProtoCAD", str(error))
            return
        self.status.showMessage(f"Сохранено: {self.path.name}", 4000)

    def _export_step(self) -> None:
        """Выгрузить деталь для соседних систем. Пишет движок: форма у него."""
        suggested = str(ROOT / "work" / f"{self.document.designation}.step")
        name, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Экспорт", suggested, "STEP (*.step *.stp);;BREP (*.brep)")
        if not name:
            return
        result = self.document.export(name)
        if not result.ok:
            QtWidgets.QMessageBox.critical(self, "ProtoCAD", result.message)
            return
        self.status.showMessage(f"Выгружено: {Path(name).name}", 4000)

    def _to_prep(self) -> None:
        """Передать деталь в подготовку к расчёту — отдельным окном.

        Передаётся КОПИЯ формы, выгруженная движком: подготовка режет,
        упрощает и склеивает, и делать это с самой деталью нельзя. Правки
        детали в подготовку не едут сами — для этого есть рецепт.
        """
        from protocad.prep.io import from_document
        from protocad_prep.window import PrepWindow

        try:
            study = from_document(self.document)
        except Exception as failure:  # noqa: BLE001 — показать, а не уронить окно
            QtWidgets.QMessageBox.critical(self, "ProtoCAD", str(failure))
            return
        folder = self.path.parent if self.path else ROOT / "work"
        window = PrepWindow(study, folder=str(folder))
        window.show()
        # Окно живёт, пока на него есть ссылка: без неё сборщик мусора
        # закрыл бы его сразу после открытия.
        self._prep_windows = [item for item in getattr(self, "_prep_windows", [])
                              if item.isVisible()] + [window]

    # --- модель ---

    def _rebuild(self, force: bool = False):
        """Пересчитать деталь. Возвращает ИТОГ — его нельзя терять.

        Раньше итог оставался внутри, и вызывающий печатал «Изменено» уже
        после того, как отказ был показан: сообщение об успехе затирало
        сообщение об отказе, и на экране оставалось «Изменено» при
        непересчитанной операции. Хуже неверного результата только
        неверный результат, названный верным.
        """
        started = time.perf_counter()
        report = self.document.rebuild()
        if report is not None and not report.ok:
            # Отказ операции показывается сразу: тихо оставить старую форму
            # значило бы показать деталь, которой в модели уже нет.
            self.status.showMessage(
                f"ОТКАЗ на «{report.failed_at}»: {report.message}", 8000
            )
        self._refresh_decor()
        self.decor.update()
        self._refresh(rebuilt=report, elapsed=time.perf_counter() - started)
        return report

    #: Что можно править числом прямо в дереве. Ключ — как называется у
    #: операции, значение — как подписано человеку.
    EDITABLE = {"length": "Длина", "size": "Размер", "angle": "Угол",
                "diameter": "Диаметр", "thickness": "Толщина",
                "spacing": "Шаг", "count": "Количество"}

    def _parameter_changed(self, name: str, parameter: str, value: float) -> None:
        operation = self.document.by_name(name)
        if operation is None or parameter not in self.EDITABLE:
            return
        setattr(operation, parameter,
                int(value) if parameter == "count" else float(value))
        self._rebuild()

    def _refresh(self, rebuilt, elapsed: float | None = None) -> None:
        self._fill_tree()
        # Всё, что окно знает о детали, приходит ОТВЕТОМ ДВИЖКА: сетка,
        # грани, рёбра, вершины. Формы ядра здесь не появляются.
        self.model = ModelView(self.document.result)
        scene = (SceneBuffers.from_engine(self.document.mesh)
                 if self.document.mesh is not None
                 else SceneBuffers.empty_scene())
        self.viewport.set_scene(scene)

        has_body = not self.model.empty
        for key in ("part.pocket", "part.hole", "part.hole_pattern", "part.fillet",
                    "part.chamfer", "part.mirror", "part.shell",
                    "part.draft"):
            self.ribbon.enable(key, has_body)

        if self.mode == MODE_SKETCH:
            return
        parts = [f"{self.document.designation}  {self.document.name}"]
        if has_body and not self.model.empty:
            # Габарит и объём берутся из ОПИСАНИЯ детали, а не из формы:
            # строка состояния — часть интерфейса, и лезть за ней в ядро
            # окну незачем.
            low = self.model.bounds[:3]
            high = self.model.bounds[3:]
            size = [high[i] - low[i] for i in range(3)]
            parts.append(
                f"габарит {size[0]:.1f} × {size[1]:.1f} × {size[2]:.1f} мм")
            parts.append(
                f"объём {self.model.volume:,.0f} мм³".replace(",", " "))
            bodies = len(self.document.bodies)
            if bodies > 1:
                parts.append(f"тел: {bodies}")
        parts.append(f"{scene.triangle_count} треуг.")
        if rebuilt is not None:
            if rebuilt.ok:
                spent = f"{elapsed * 1000:.0f} мс" if elapsed else "—"
                parts.append(
                    f"пересчёт {spent} ({len(self.document.operations)} операций)")
            else:
                parts.append(f"ОТКАЗ на «{rebuilt.failed_at}»: {rebuilt.message}")
        self.status.showMessage("    |    ".join(parts))

    FEATURE_GLYPHS = {
        "Эскиз": "sketch", "Выдавливание": "pad", "Вырез по эскизу": "pocket",
        "Отверстие": "hole", "Массив отверстий": "pattern_linear",
        "Скругление": "fillet", "Фаска": "chamfer", "Вращение": "revolve",
        "Зеркало": "mirror_body", "Оболочка": "shell", "Уклон": "draft",
    }

    #: Как в дереве помечен эскиз. Операции хранятся по имени, эскизы —
    #: с приставкой: имена у них независимые, и путать их нельзя.
    SKETCH_MARK = "эскиз:"
    #: Роль, по которой строка опознаётся как справочная плоскость.
    PLANE_MARK = QtCore.Qt.UserRole + 9

    def _fill_tree(self) -> None:
        """Дерево построения: эскиз ВЛОЖЕН в операцию, которая его израсходовала.

        Эскиз — не самостоятельный шаг, а исходные данные операции. Списком
        вровень они выглядят как два равных действия, и найти, каким эскизом
        сделано выдавливание, можно только по порядку строк.
        """
        self.tree.blockSignals(True)
        self.tree.clear()
        label = f"{self.document.designation}  {self.document.name}"
        root = QtWidgets.QTreeWidgetItem([label, ""])
        font = root.font(0)
        font.setBold(True)
        root.setFont(0, font)
        self.tree.addTopLevelItem(root)

        built = len(self.document.built)
        for number, (row, operation) in enumerate(
                zip(self.document.describe(), self.document.operations)):
            if number == built:
                root.addChild(self._rollback_node())
            node = self._operation_node(row, operation)
            if number >= built:
                # Откаченное показывается приглушённым: оно в дереве есть,
                # а в детали его нет, и путать это нельзя.
                grey = QtGui.QBrush(QtGui.QColor("#8a97a5"))
                node.setForeground(0, grey)
                node.setForeground(1, grey)
                node.setText(1, "откачено")
            if operation.sketch is not None:
                node.addChild(self._sketch_node(operation.sketch))
                node.setExpanded(True)
            root.addChild(node)
        if built >= len(self.document.operations):
            root.addChild(self._rollback_node())
        # Нарисованные, но ещё ничем не израсходованные — в корень: они
        # существуют, и не показать их значило бы потерять работу.
        for sketch in self.document.free_sketches():
            root.addChild(self._sketch_node(sketch))
        self._fill_planes(root)
        self._fill_bodies(root)
        root.setExpanded(True)
        self.tree.blockSignals(False)

    def _rollback_node(self):
        """Шторка: ЛИНИЯ поперёк дерева, без подписи.

        Слов у неё нет намеренно. Что построено, а что отложено, видно и
        так: отложенные операции приглушены и помечены «откачено». Подпись
        на самой линии повторяла бы это третий раз и занимала строку.
        """
        node = QtWidgets.QTreeWidgetItem(["", ""])
        node.setData(0, BuildTree.MARK, True)
        node.setToolTip(0, "Шторка отката: тащите вверх или вниз, "
                           "чтобы строить деталь не до конца")
        # Выбирать её незачем — её берут и несут. Выделение только сбивало
        # бы показ выбранной операции.
        node.setFlags(QtCore.Qt.ItemIsEnabled)
        return node

    def _move_rollback(self, place: int) -> None:
        """Поставить шторку так, чтобы сверху оказалось ``place`` операций."""
        self.document.rollback = place
        self._rebuild(force=True)
        left = len(self.document.operations) - len(self.document.built)
        self.status.showMessage(
            "Построение откачено: отложено операций "
            f"{left}" if left else "Построение восстановлено целиком", 5000)

    def _fill_planes(self, root) -> None:
        """Папка «Плоскости»: справочные плоскости детали.

        Отдельно от операций: плоскость ничего не строит, она опора. В
        одном списке с выдавливанием она читалась бы как шаг построения, а
        порядок среди операций у неё смысла не имеет.
        """
        if not self.document.planes:
            return
        folder = QtWidgets.QTreeWidgetItem(
            [f"Плоскости ({len(self.document.planes)})", ""])
        folder.setIcon(0, icons.icon("datum_plane", 16))
        for record in self.document.planes:
            row = QtWidgets.QTreeWidgetItem(
                [record.name, "" if record.ok else "не построена"])
            row.setIcon(0, icons.icon("datum_plane", 16))
            row.setData(0, self.PLANE_MARK, record.name)
            if not record.ok:
                row.setForeground(1, QtGui.QBrush(QtGui.QColor("#c0392b")))
                row.setToolTip(0, record.message)
            folder.addChild(row)
        folder.setExpanded(True)
        root.addChild(folder)

    def _fill_bodies(self, root) -> None:
        """Папка «Твёрдые тела» — сколько тел вышло из детали.

        Показывается ВСЕГДА, когда тело есть, а не только когда их больше
        одного. Число тел — свойство детали, а не редкий случай: по нему
        сразу видно, распалась ли деталь на куски, которых не заказывали.
        Раньше папка появлялась со второго тела, и «одно тело» приходилось
        выводить из её отсутствия.
        """
        # Спрашивается ДОКУМЕНТ, а не описание детали: дерево заполняется
        # раньше, чем окно получает описание, и по нему папка тел выходила
        # бы пустой на первом же показе.
        bodies = self.document.bodies
        if not bodies or self.document.mesh is None:
            return
        holder = QtWidgets.QTreeWidgetItem(
            [f"Твёрдые тела ({len(bodies)})", ""])
        holder.setIcon(0, icons.icon("shell", 16))
        holder.setData(0, QtCore.Qt.UserRole, "")
        for name in bodies:
            inside = [item.name for item in self.document.operations
                      if (item.body or self.document.body_id) == name]
            node = QtWidgets.QTreeWidgetItem(
                [name, f"операций: {len(inside)}"])
            node.setIcon(0, icons.icon("pad", 16))
            holder.addChild(node)
        holder.setExpanded(len(bodies) > 1)
        root.addChild(holder)

    def _operation_node(self, row: dict, operation) -> QtWidgets.QTreeWidgetItem:
        state = "ok" if row["ok"] else "ОТКАЗ"
        node = QtWidgets.QTreeWidgetItem([row["title"], state])
        node.setIcon(0, icons.icon(self.FEATURE_GLYPHS.get(row["kind"], "measure"), 16))
        node.setData(0, QtCore.Qt.UserRole, row["name"])
        if not row["ok"]:
            node.setForeground(1, QtGui.QBrush(QtGui.QColor("#c0392b")))
            node.setToolTip(1, row["message"])
        elif row["note"]:
            node.setToolTip(1, row["note"])
        return node

    def _sketch_node(self, sketch) -> QtWidgets.QTreeWidgetItem:
        # Плоскость видна прямо в дереве: без неё два одинаковых с виду
        # эскиза различить нельзя, а строят они разное.
        node = QtWidgets.QTreeWidgetItem(
            [f"Эскиз: {sketch.name}  [{sketch.plane.name}]", ""])
        node.setIcon(0, icons.icon("sketch", 16))
        node.setData(0, QtCore.Qt.UserRole, self.SKETCH_MARK + sketch.name)
        return node

    def _sketch_named(self, name: str):
        for sketch in self.document.sketches:
            if sketch.name == name:
                return sketch
        return None

    def _feature_selected(self, current, _previous) -> None:
        if current is None:
            return
        name = current.data(0, QtCore.Qt.UserRole)
        if not name:
            return
        if name.startswith(self.SKETCH_MARK):
            sketch = self._sketch_named(name[len(self.SKETCH_MARK):])
            # Щелчок по эскизу при открытой команде, которая ЖДЁТ эскиз, —
            # это выбор для неё. Иначе указать его нечем: в трёхмерном виде
            # эскиз не выбирается, а поле требует именно его, и команда
            # просила невозможного.
            if sketch is not None and self._sketch_to_session(sketch, name):
                return
            if sketch is not None and self.mode != MODE_SKETCH:
                self.viewport.set_face_group(())
                self._preview_sketch(sketch)
            return
        operation = self.document.by_name(name)
        if operation is None:
            return
        # Когда открыта команда, которая набирает ОПЕРАЦИИ, щелчок по
        # дереву — это выбор для неё, а не переход к правке. Иначе набрать
        # объекты массива нечем: указывать их можно только здесь.
        box = self.session.active_box if self.session is not None else None
        if box is not None and box.accepts_kind("feature"):
            self.session.click(self._feature_pick(name))
            self.panel.refresh()
            self._update_preview()
            return
        self.editor.show_feature(name, TITLES.get(operation.kind, operation.kind),
                                 self._parameters_of(operation))
        self._show_selected_feature(operation)

    def _parameters_of(self, operation) -> dict:
        """Числа операции, которые можно править прямо в дереве.

        Показываются только те, что у этой операции есть на самом деле:
        поле «Диаметр» у скругления сбивало бы с толку, а правка его
        ничего бы не меняла.
        """
        by_kind = {
            "pad": ("length",), "pocket": ("length",),
            "revolve": ("angle",), "groove": ("angle",),
            "hole": ("diameter",), "shell": ("thickness",),
            "draft": ("angle",),
            "fillet": ("size",), "chamfer": ("size",),
            "linear": ("count", "spacing"), "polar": ("count", "angle"),
        }
        keys = list(by_kind.get(operation.kind, ()))
        if operation.kind in ("pad", "pocket") and operation.thin:
            # У тонкостенного выдавливания толщина стенки — такое же
            # число операции, как длина, и править её надо там же.
            keys.append("thickness")
        return {self.EDITABLE[key]: getattr(operation, key)
                for key in keys if key in self.EDITABLE}

    def _show_selected_feature(self, operation) -> None:
        """Показать выбранную операцию в виде: подсветить её грани.

        Эскиз при этом НЕ показывается, даже если он у операции есть: его
        показывает выбор самого эскиза. Показать и то и другое сразу
        значило бы нарисовать контур поверх подсвеченных граней — видно
        стало бы хуже, а не лучше.
        """
        if operation is None or self.mode == MODE_SKETCH:
            return
        self._preview_sketch(None)
        self.viewport.set_face_group(self.document.faces_of(operation))

    def _preview_sketch(self, sketch) -> None:
        """Наложить эскиз на вид, не входя в режим правки."""
        if sketch is None:
            if self.mode != MODE_SKETCH:
                self._show_overlay(None)
            return
        self.preview_canvas.sketch = sketch
        self.preview_canvas.projector = sketch_view.PlaneProjector(
            self.viewport, sketch.plane
        )
        self._show_overlay(self.preview_canvas)

    def _show_overlay(self, canvas) -> None:
        """Показать одно поле эскиза поверх вида и убрать остальные."""
        self.overlay = canvas
        self.viewport.overlay_widget = canvas
        self.viewport._last_camera = None
        for candidate in (self.canvas, self.preview_canvas):
            if candidate is canvas:
                candidate.setGeometry(self.viewport.rect())
                candidate.show()
                candidate.raise_()
            else:
                candidate.hide()
        self.viewport.update()

    # --- построение детали ---

    def _new_part(self) -> None:
        designation, ok = QtWidgets.QInputDialog.getText(
            self, "Новая деталь", "Обозначение:", text="АБВГ.741141.001"
        )
        if not ok:
            return
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Новая деталь", "Наименование:", text="Деталь"
        )
        if not ok:
            return
        self.document.forget()
        self.document = Document(name, backend=self.document.backend,
                                 designation=designation)
        self.document.document_id = f"деталь:{designation}"
        self._pending_regions.clear()
        self.path = None
        self._set_mode(MODE_MODEL)
        self._refresh(rebuilt=None)
        self.setWindowTitle(f"ProtoCAD — {designation}")

    def _new_sketch(self) -> None:
        """Создать эскиз. Если грань выбрана в виде — сразу на ней."""
        if self.selected_face_plane is not None:
            plane = self.selected_face_plane
            # Выбор в виде — уже ответ на вопрос «где». Спрашивать после
            # него ещё раз значит не замечать сделанного человеком.
            support = self._face_support(self.selected_face_index)
            self.selected_face_plane = None
            self.viewport.highlight_face = 0
            self.create_sketch(plane, support)
            return
        dialog = _PlaneDialog(self.model, self, self.document.planes)
        if dialog.exec() != QtWidgets.QDialog.Accepted:
            return
        try:
            plane = dialog.plane()
        except Exception as error:  # noqa: BLE001
            QtWidgets.QMessageBox.warning(self, "ProtoCAD", str(error))
            return
        self.create_sketch(plane, dialog.support())

    def _face_support(self, index: int):
        """Опора эскиза на грани детали: имя, номер и её положение.

        Положение запоминается наравне с именем: после перестройки имена
        граней выходят другими, и опознаётся грань по тому, что смотрит
        туда же и стоит ближе всего к запомненному месту.
        """
        item = self.model.face(int(index)) if index is not None and index >= 0             else None
        if item is None:
            return None
        data = item.data or {}
        if data.get("surface") != "plane":
            return None
        return {"kind": "face", "index": int(item.index),
                "name": getattr(item, "name", "") or "",
                "origin": list(data.get("origin") or ()),
                "normal": list(data.get("normal") or ())}

    def create_sketch(self, plane=None, support=None) -> Sketch:
        """Создать эскиз на плоскости и перейти к его правке.

        Отделено от вопроса о плоскости намеренно: создание эскиза нужно и
        без диалога — из проверок и из будущих команд, которые плоскость уже
        знают.

        ``support`` — НА ЧЁМ эскиз лежит: грань, справочная плоскость или
        стандартная. С опорой эскиз следует за ней при каждом пересчёте; без
        опоры плоскость остаётся снимком, и подвинувшаяся грань уедет
        из-под эскиза молча.
        """
        name = _unique_sketch_name(self.document, "Эскиз")
        sketch = Sketch(name, plane=plane)
        sketch.support = dict(support) if support else None
        self.document.add_sketch(sketch)
        self._fill_tree()
        self._enter_sketch(sketch, None)
        return sketch

    def _attach_profile(self, operation) -> None:
        """Доложить операции профиль: эскиз и ВЫБРАННЫЕ В НЁМ ОБЛАСТИ.

        Одно место на все пути создания операции — из диалога, из панели и
        из предпросмотра. Раньше области доставлялись каждым путём отдельно,
        и панель их теряла: команда получала эскиз из своего поля профиля, а
        выбор оставался в окне. Молча строился «весь эскиз» — наружный
        контур с вложенными как отверстиями, и вырез оставлял в кармане
        остров, которого никто не просил.
        """
        if (operation.dress_up or operation.pattern
                or operation.kind in ("shell", "draft")):
            return
        if operation.sketch is None:
            operation.sketch = self._free_sketch()
        if operation.sketch is None:
            return
        if not operation.regions:
            operation.regions = list(
                self._pending_regions.get(id(operation.sketch)) or [])

    def _regions_note(self, operation) -> str:
        """Сколько областей ушло в операцию — видно сразу, а не после
        пересчёта: неверный выбор иначе замечают по форме детали."""
        if operation.sketch is None:
            return ""
        if not operation.regions:
            return " (весь эскиз)"
        return f" (областей: {len(operation.regions)})"

    def _selected_faces(self) -> list:
        """Грани, выбранные в виде. Пусто — команда решит правилом."""
        return [self.selected_face_index] if self.selected_face_index >= 0 else []

    # --- незавершённая операция ---

    #: Поля записи операции, которые правка НЕ переносит: имя и место в
    #: дереве принадлежат операции, а не набранным в панели значениям, а
    #: имя в движке держит связь с уже построенным.
    KEEP_ON_EDIT = ("name", "feature_id", "body", "faces_after")

    def _start_command(self, descriptor, editing=None) -> None:
        """Открыть панель команды. Выбранное в виде уходит в её поля.

        ``editing`` — операция, которую правят. Панель при этом та же
        самая: команда обязана спрашивать одно и то же, создаётся она или
        правится, иначе правка и создание разойдутся на первой же новой
        возможности.
        """
        if descriptor.needs_body and self.model.empty and editing is None:
            self.status.showMessage(
                f"«{descriptor.title}»: сначала нужно тело — выдавите эскиз", 5000
            )
            return
        self._cancel_command()
        self.editing = editing
        context = D.BuildContext(document=self.document, model=self.model)
        self.session = CommandSession(
            descriptor, context,
            preselection=([] if editing is not None
                          else self._preselection(descriptor))
        )
        if editing is not None and descriptor.load is not None:
            descriptor.load(self.session, editing, context)
        self._saved_scene = self.viewport.scene
        self.panel.show_session(self.session)
        self._show_command_panel(True)
        self._show_command_sketch()
        self._show_direction_arrow()
        self._sync_pick_kinds()
        self._update_preview()
        self.hint.setText(
            f"Правка «{editing.name}»: измените параметры; Готово — применить"
            if editing is not None else
            "Укажите объекты в виде и задайте параметры; Готово — создать"
        )

    def _new_plane(self) -> None:
        """Открыть команду построения справочной плоскости."""
        self._start_command(D.PLANE)

    def _preselection(self, descriptor) -> list:
        """Что из уже выбранного годится команде.

        Эскиз берётся последний свободный, рёбра — накопленные щелчками.
        Несовместимое сеанс отбросит сам, но собирать заведомо чужое здесь
        незачем.
        """
        picks: list[Pick] = []
        if "sketch" in descriptor.preselection:
            sketch = self._free_sketch()
            if sketch is not None:
                picks.append(Pick("sketch", id(sketch) % 100000, sketch.name,
                                  sketch))
                # Области, выбранные в самом эскизе, — это тот же выбор.
                # Не подставить их значило бы попросить человека повторить
                # уже сделанное, а забыть — построить не то.
                for number, reference in enumerate(
                        self._pending_regions.get(id(sketch)) or []):
                    picks.append(Pick("sketch_region", number,
                                      f"Область {number + 1}", reference))
        if "edge" in descriptor.preselection and not self.model.empty:
            for index in self.selected_edges:
                edge = self.model.edge(index)
                if edge is not None:
                    picks.append(Pick("edge", index, f"Ребро {index + 1}", edge))
        if "feature" in descriptor.preselection:
            # Массив размножает ОПЕРАЦИИ, а операции указывают в дереве.
            # Выделенная строка — это уже ответ на вопрос «что размножать»,
            # и спрашивать после неё ещё раз значит не замечать сделанного.
            for name in self._selected_features():
                picks.append(self._feature_pick(name))
        return picks

    def _selected_features(self) -> list:
        """Имена операций, выделенных в дереве построения."""
        names = []
        for row in self.tree.selectedItems():
            name = row.data(0, QtCore.Qt.UserRole)
            if not name or name.startswith(self.SKETCH_MARK):
                continue
            if self.document.by_name(name) is not None and name not in names:
                names.append(name)
        return names

    def _feature_pick(self, name: str) -> Pick:
        """Операция как выбранный объект команды.

        Подписью служит ИМЯ В ДЕРЕВЕ, а не номер: массив ссылается на
        операции по имени, и номер строки после вставки другой операции
        указывал бы уже не туда.
        """
        from protocad.engine import EntityRef

        operation = self.document.by_name(name)
        index = (self.document.operations.index(operation)
                 if operation in self.document.operations else -1)
        return Pick("feature", index, name,
                    EntityRef(id=f"feature:{name}", kind="feature",
                              index=index, name=name))

    def _free_sketch(self):
        free = self.document.free_sketches()
        return free[-1] if free else None

    def _show_command_sketch(self) -> None:
        """Показать эскиз профиля, пока идёт команда.

        По спецификации SolidWorks контуры выбираются щелчком в виде, а
        щёлкать можно только по тому, что видно. Показывается тот эскиз,
        который лежит в поле профиля; выбранные области подсвечены.
        """
        if self.session is None:
            self._preview_sketch(None)
            return
        sketch = self._command_sketch()
        if sketch is None:
            self._preview_sketch(None)
            return
        box = self.session.box("selected_regions")
        self.preview_canvas.selected_regions = [
            pick.data for pick in (box.items if box else ()) if pick.data]
        self.preview_canvas.show_regions = True
        self._preview_sketch(sketch)

    def _sketch_point(self, point):
        """Точка детали → координаты плоскости эскиза команды."""
        sketch = self._command_sketch()
        if sketch is None:
            return (0.0, 0.0)
        return sketch.plane.project(tuple(point))

    def _command_sketch(self):
        """Эскиз, с которым работает открытая команда."""
        if self.session is None:
            return None
        box = self.session.box("profile")
        for pick in (box.items if box else ()):
            if isinstance(pick.data, Sketch):
                return pick.data
        return None

    def _pick_region_for_command(self, point) -> bool:
        """Щелчок по области показанного эскиза. ``False`` — мимо.

        Возвращает, был ли щелчок израсходован: если да, обычный выбор в
        виде его больше не получает, иначе указание области заодно меняло
        бы выбранную грань.
        """
        if self.session is None:
            return False
        box = self.session.box("selected_regions")
        if box is None or self.session._box_disabled(box):
            return False
        sketch = self._command_sketch()
        if sketch is None:
            return False
        self.preview_canvas.sketch = sketch
        region = self.preview_canvas.region_at(point)
        if region is None:
            return False
        self.session.activate("selected_regions")
        number = len(box.items)
        result = self.session.click(Pick(
            "sketch_region", number, f"Область {number + 1}",
            region.reference()))
        if result == "rejected":
            return False
        self._show_command_sketch()
        self.panel.refresh()
        self._update_preview()
        chosen = len(box.items)
        self.status.showMessage(
            f"Выбранные контуры: {chosen}" if chosen
            else "Выбранные контуры сняты — будет взят весь эскиз", 4000)
        return True

    def _session_pick(self, what: dict) -> None:
        kind = what["kind"]
        index = int(what.get("index", -1))
        # В поле уходит ОПИСАНИЕ подэлемента, а не форма ядра: команде
        # нужна подпись ребра и плоскость грани, и то и другое приходит
        # числами (docs/08_ENGINE_BACKEND.md, §10.2).
        data = None
        if kind == "face":
            data = self.model.face(index)
        elif kind == "edge":
            data = self.model.edge(index)
        titles = {"face": "Грань", "edge": "Ребро", "vertex": "Точка", "body": "Тело"}
        label = what.get("label") or f"{titles.get(kind, kind)} {index + 1}"
        result = self.session.click(Pick(kind, index, label, data))
        if result == "rejected":
            self.status.showMessage(self.session.diagnostics, 5000)
        self.panel.refresh()
        self._sync_pick_kinds()
        self._update_preview()

    def _sync_pick_kinds(self) -> None:
        """Вид принимает ровно то, что ждёт активное поле."""
        if self.session is None:
            return
        box = self.session.active_box
        self.viewport.pick_kinds = tuple(box.accepts) if box is not None else ()

    def _activate_box(self, box_id: str) -> None:
        if self.session is None:
            return
        self.session.activate(box_id)
        self.panel.refresh()
        self._sync_pick_kinds()

    def _session_changed(self) -> None:
        # Панель перерисовывается: смена значения могла открыть новое поле
        # и перевести в него фокус, и на экране это должно быть видно.
        self.panel.refresh()
        self._show_command_sketch()
        self._show_direction_arrow()
        self._sync_pick_kinds()
        self._update_preview()

    #: Какое поле тянется за стрелкой у какой команды. Тянуть можно то,
    #: что стрелка и показывает: её длину. У остальных команд стрелки нет.
    ARROW_FIELD = {"pad": "depth", "pocket": "depth"}

    def arrow_grab(self, point) -> bool:
        """Схватить стрелку за наконечник. ``False`` — щелчок мимо.

        Тянуть стрелку — это менять длину операции рукой, не отрываясь от
        детали. Числу в поле это не замена: тянут, чтобы ПОДОБРАТЬ, а
        вводят, чтобы задать.
        """
        session = self.session
        if session is None or self._arrow_drag is not None:
            return False
        field = self.ARROW_FIELD.get(session.descriptor.key)
        if field is None or not self.decor.near_arrow_head(point):
            return False
        line = self.decor.arrow_line()
        if line is None:
            return False
        start, end = line
        span = ((end.x() - start.x()) ** 2 + (end.y() - start.y()) ** 2) ** 0.5
        length = float(session.value(field) or 0.0)
        if span < 1.0 or length <= 0.0:
            return False
        self._arrow_drag = {
            "field": field,
            "length": length,
            #: Сколько миллиметров в пикселе. Считается ОДИН раз, на
            #: захвате: во время протяжки камера не движется, и пересчёт
            #: на каждом шаге только копил бы дрожь.
            "scale": length / span,
            "along": ((end.x() - start.x()) / span, (end.y() - start.y()) / span),
            "start": start,
            "span": span,
        }
        self._arrow_drag["from"] = self._along_arrow(point)
        return True

    def _along_arrow(self, point) -> float:
        """Сколько пикселей от начала стрелки вдоль неё.

        Считается по СХВАЧЕННОЙ стрелке, а не по нынешней. Пока считалось
        по нынешней, протяжка разгоняла сама себя: стрелка удлинялась,
        вместе с ней рос и отсчёт, и вместо 20 мм выходило 30. На экране
        это выглядело бы как убегающая от курсора стрелка.
        """
        drag = self._arrow_drag
        if drag is None:
            return 0.0
        start, along = drag["start"], drag["along"]
        return ((point.x() - start.x()) * along[0]
                + (point.y() - start.y()) * along[1])

    def arrow_drag_to(self, point) -> bool:
        """Протянуть стрелку. Возвращает, изменилось ли что-нибудь."""
        drag = self._arrow_drag
        if drag is None:
            return False
        moved = self._along_arrow(point) - drag["from"]
        value = drag["length"] + moved * drag["scale"]
        # Меньше сотой миллиметра длины не бывает: ноль и минус — это уже
        # другая сторона, и молча разворачивать операцию протяжкой нельзя.
        value = max(0.01, round(value, 3))
        self.panel._set(drag["field"], value)
        # Отставший показ, выданный молча, читается как ответ — «столько и
        # получится», — а получится то, что на стрелке.
        tail = " — тело догонит, когда отпустите" if self._preview_owed else ""
        self.status.showMessage(f"Длина: {value:g} мм{tail}", 2000)
        return True

    def arrow_release(self) -> None:
        """Отпустить стрелку и догнать показ, если он отставал."""
        self._arrow_drag = None
        if self._preview_owed:
            self._preview_owed = False
            self._update_preview()

    #: Команды, у которых экземпляры показываются и щёлкаются.
    INSTANCE_COMMANDS = ("linear_pattern", "circular_pattern")

    def _show_instances(self) -> None:
        """Показать метки экземпляров задаваемого массива.

        Без меток пропуск экземпляра пришлось бы задавать номером, а номер
        человек не видит: он видит деталь. Метки и есть то, во что можно
        ткнуть.
        """
        session = self.session
        if session is None \
                or session.descriptor.key not in self.INSTANCE_COMMANDS:
            if self.decor.instances:
                self.decor.instances = []
                self.decor.update()
            return
        try:
            operation = session.descriptor.build(session, session.context)
        except Exception:  # noqa: BLE001 — набор ещё неполон, меток пока нет
            operation = None
        seed = self._pattern_seed(operation) if operation is not None else None
        if seed is None:
            self.decor.instances = []
            self.decor.update()
            return
        dropped = set(operation.skip or ())
        self.decor.instances = [
            (number, point, number in dropped)
            for number, point in document_module.pattern_places(operation, seed)
        ]
        self.decor.update()

    def _pattern_seed(self, operation):
        """Точка размножаемой операции. ``None`` — брать неоткуда.

        Берётся середина ЕЁ эскиза: метка обязана стоять там, где стоит сам
        экземпляр, а не в начале координат детали.
        """
        for name in operation.sources:
            source = self.document.by_name(name)
            if source is not None and source.sketch is not None:
                return self._arrow_origin(source.sketch)
        return None

    def _toggle_instance(self, position) -> bool:
        """Пропустить или вернуть экземпляр под указателем."""
        session = self.session
        if session is None \
                or session.descriptor.key not in self.INSTANCE_COMMANDS:
            return False
        number = self.decor.instance_at(position)
        if number is None:
            return False
        if number == 0:
            # Нулевой — сам исходник. Пропустить его нельзя: пропадёт то,
            # что размножают, и массив остался бы без образца.
            self.status.showMessage(
                "Первый экземпляр — сам исходник, его не пропускают", 4000)
            return True
        current = D._skipped(session)
        if number in current:
            current.remove(number)
        else:
            current.append(number)
        session.set_value("skip", ", ".join(str(value + 1)
                                            for value in sorted(current)))
        self.panel.refresh()
        self._update_preview()
        return True

    def _arrow_event(self, kind, event) -> bool:
        """Мышь по стрелке предпросмотра. `True` — событие забрано.

        Пока стрелку тянут, вид не должен вращаться — иначе деталь уезжает
        из-под руки вместе со стрелкой. Зовётся из `eventFilter`, который
        в классе один: см. пояснение при нём.
        """
        if self.mode == MODE_SKETCH:
            return False
        if kind == QtCore.QEvent.MouseButtonPress \
                and event.button() == QtCore.Qt.LeftButton:
            # Метка экземпляра пробуется ПЕРВОЙ: она мелкая и лежит на
            # детали, и если её перехватит выбор грани, ткнуть в неё будет
            # нечем.
            if self._toggle_instance(event.position()):
                return True
            return self.arrow_grab(event.position())
        if self._arrow_drag is None:
            return False
        if kind == QtCore.QEvent.MouseMove:
            self.arrow_drag_to(event.position())
            return True
        if kind == QtCore.QEvent.MouseButtonRelease:
            self.arrow_release()
            return True
        return False

    def _show_direction_arrow(self) -> None:
        """Стрелка направления незавершённой операции.

        Показывает то, чего не видно в числе: откуда операция считает
        длину и в какую сторону идёт. Разворот направления виден сразу, а
        не после пересчёта.
        """
        self.decor.arrow = None
        session = self.session
        if session is None or self.mode == MODE_SKETCH:
            self.decor.update()
            return
        sketch = self._command_sketch()
        if sketch is None:
            self.decor.update()
            return
        plane = sketch.plane
        normal = tuple(plane.normal)
        if bool(session.value("reverse")) != bool(
                session.value("flip_side_to_cut")):
            normal = tuple(-value for value in normal)
        # Вырез идёт В материал, то есть против нормали плоскости эскиза:
        # эскиз для него рисуют на поверхности, а снимают под ней.
        if session.descriptor.key == "pocket":
            normal = tuple(-value for value in normal)
        length = float(session.value("depth") or 0.0)
        if length <= 0.0:
            # У «насквозь» и «до грани» своего числа нет: стрелка всё
            # равно нужна, и её длину берём от размера детали.
            span = self.model.bounds
            length = (max(span[3] - span[0], span[4] - span[1],
                          span[5] - span[2]) * 0.4) if span else 20.0
            label = str(session.value("end_condition") or "")
        else:
            label = f"{length:g} мм"
        self.decor.arrow = (self._arrow_origin(sketch), normal, length, label)
        self.decor.update()

    def _arrow_origin(self, sketch) -> tuple:
        """Откуда рисовать стрелку: середина габарита эскиза на его плоскости."""
        points = [sketch.coordinates(point) for point in sketch.points
                  if not point.construction] or [(0.0, 0.0)]
        middle = (sum(x for x, _ in points) / len(points),
                  sum(y for _, y in points) / len(points))
        return tuple(sketch.plane.point_at(*middle))

    #: Дороже этого предпросмотр не пересчитывают на каждое движение мыши.
    #: Порог, а не запрет: на лёгкой детали протяжка остаётся полностью
    #: живой, и отнимать это у неё незачем.
    LIVE_PREVIEW_MS = 150.0

    def _update_preview(self) -> None:
        """Показать предпросмотр. Документ и дерево при этом не меняются."""
        if self.session is None:
            return
        if self._arrow_drag is not None \
                and self._preview_cost > self.LIVE_PREVIEW_MS:
            # Тяжёлая деталь: пересчёт на каждое движение мыши превращает
            # протяжку в череду замираний по полсекунды — рука уходит
            # вперёд, а вид догоняет рывками. Число и стрелка идут за
            # рукой сразу, тело догоняет на отпускании.
            #
            # Сказать об этом должен тот, кто ведёт протяжку: строку
            # состояния он пишет всё равно, и два места, пишущие в неё по
            # очереди, затирали бы друг друга.
            self._preview_owed = True
            return
        began = time.perf_counter()
        state = self.session.build_preview(self._evaluate_preview)
        self.panel.refresh()
        shown = self.session.preview_shape
        # Показывается ТОЛЬКО то, что операция добавляет или снимает: всё
        # тело поверх него самого закрашивает деталь целиком, и по картинке
        # не понять, что операция с ней сделает. Если движок инструмент не
        # выделил — показываем результат целиком, это лучше пустоты.
        # У справочной плоскости формы нет вовсе: она рисуется разметкой,
        # а не сеткой. Спрашивать у неё сетку — то же, что спрашивать длину
        # у грани: ответа нет, и подставлять вместо него нечего.
        drawn = (shown.display_mesh
                 if state == "ok" and shown is not None
                 and hasattr(shown, "display_mesh") else None)
        if drawn is not None:
            # Отдельным слоем, а не заменой сцены: во время команды
            # указывают на настоящую деталь. Замена ломала выбор — щелчок
            # попадал в ребро предпросмотра, а операция искала его в
            # исходном теле, и рёбра набирались не те.
            self.viewport.set_preview_scene(SceneBuffers.from_engine(drawn))
            # Прилив и вырез красятся ПО-РАЗНОМУ. Показывать снимаемый
            # материал тем же цветом, что и прирастающий, значит обещать
            # противоположное тому, что операция сделает.
            self.viewport.preview_color = (
                PREVIEW_CUT if getattr(shown, "tool_kind", "") == "removed"
                else PREVIEW_ADD)
            # Деталь гасится только под ЦЕЛЫМ телом: инструмент кладётся
            # поверх неё, и гасить её под ним значило бы притушить то, с
            # чем сравнивают.
            self.viewport.dim = 0.0 if shown.tool_mesh is not None else 0.45
        else:
            self.viewport.set_preview_scene(None)
            self.viewport.dim = 0.0
        self._preview_owed = False
        self._preview_cost = (time.perf_counter() - began) * 1000.0
        self._show_instances()
        self.status.showMessage(
            self.session.diagnostics or f"{self.session.descriptor.title}: готово к созданию",
            0,
        )

    def _show_planes(self) -> None:
        """Показать справочные плоскости в виде.

        Плоскость, которую не удалось построить, не рисуется вовсе:
        нарисованная «примерно» она врёт молча, а эскиз на ней ложится не
        туда, и обнаружится это уже на детали.
        """
        self.decor.reference_planes = [
            record.plane for record in self.document.planes if record.ok
        ]
        self.decor.update()

    def _evaluate_preview(self, session):
        """Показать операцию, не оставляя её в дереве.

        Считает ТОТ ЖЕ движок и тем же кодом, что и применение (§12.1):
        предпросмотр, считаемый иначе, показывает не то, что построится, и
        расхождение обнаруживается уже после подтверждения.
        """
        from protocad.reference import ReferencePlane, resolve

        operation = session.descriptor.build(session, session.context)
        if isinstance(operation, ReferencePlane):
            # Плоскость считается НАШИМ кодом, без движка: она не форма, а
            # правило, и спрашивать о ней другой процесс незачем.
            entities = (self.document.result.entities
                        if self.document.result is not None else ())
            resolve(operation, entities)
            if not operation.ok:
                raise CommandError(operation.message)
            self.decor.pending_plane = operation.plane
            self.decor.update()
            return operation
        self.decor.pending_plane = None
        self._attach_profile(operation)
        if operation.sketch is None and not operation.dress_up \
                and not operation.pattern \
                and operation.kind not in ("shell", "draft"):
            raise CommandError("нет свободного эскиза для профиля")
        answer = self.document.preview(operation)
        if not answer.ok:
            raise CommandError(answer.message)
        return answer

    def _commit_command(self) -> None:
        if self.session is None:
            return
        try:
            operation = self.session.commit()
        except CommandError as error:
            self.status.showMessage(str(error), 6000)
            return
        except Exception as error:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "ProtoCAD", str(error))
            return
        from protocad.reference import ReferencePlane

        if isinstance(operation, ReferencePlane):
            # Плоскость ничего не строит — она ОПОРА, и в дереве операций
            # ей не место. Разводится по типу построенного, а не по ключу
            # команды: тип и есть то, чем они различаются.
            record = self.document.add_plane(operation)
            self.session = None
            self.editing = None
            self.viewport.set_preview_scene(None)
            self.viewport.dim = 0.0
            self._saved_scene = None
            self._clear_edge_selection()
            self.decor.arrow = None
            self.decor.pending_plane = None
            self._fill_tree()
            self._show_planes()
            # Панель закрывается тем же способом, что и у операций: два
            # способа её закрыть разойдутся на первой же новой команде.
            self._show_command_panel(False)
            self.viewport.pick_kinds = ("vertex", "edge", "face", "body")
            self.status.showMessage(
                f"Добавлена плоскость: {record.name}" if record.ok
                else record.message, 4000)
            return
        self._attach_profile(operation)
        editing = self.editing
        if editing is None:
            self.document.add(operation)
        else:
            # Правка идёт НА МЕСТЕ: у операции остаются её имя, место в
            # дереве и имя в движке. Пересоздать её нельзя — на неё
            # ссылаются те, что ниже по дереву.
            _absorb(editing, operation)
        self.editing = None
        self.session = None
        self.viewport.set_preview_scene(None)
        self.viewport.dim = 0.0
        self._saved_scene = None
        self._clear_edge_selection()
        self.decor.arrow = None
        self.decor.instances = []
        self.decor.update()
        self._show_command_panel(False)
        self.viewport.pick_kinds = ("vertex", "edge", "face", "body")
        report = self._rebuild(force=True)
        if report is not None and not report.ok:
            # Отказ уже показан пересчётом. Печатать поверх него «Изменено»
            # значит сообщить об успехе, которого не было.
            return
        self.status.showMessage(
            (f"Изменено: {editing.title}{self._regions_note(editing)}"
             if editing is not None else
             f"Добавлено: {operation.title}{self._regions_note(operation)}"),
            4000)

    def _cancel_command(self) -> None:
        """Закрыть панель, не оставив следа: ни в дереве, ни в отмене."""
        self.editing = None
        if self.session is None:
            return
        self.session.cancel()
        self.session = None
        self.viewport.set_preview_scene(None)
        self.viewport.dim = 0.0
        self._saved_scene = None
        self.decor.arrow = None
        self.decor.instances = []
        self.decor.update()
        # Убрать панель, а не просто перелистнуть страницу рядом с ней:
        # панель стоит ОТДЕЛЬНО от стопки `side`, и смена страницы её на
        # экране не трогает. Отмена оставляла панель открытой.
        self._show_command_panel(False)
        self.side.setCurrentIndex(1 if self.mode == MODE_SKETCH else 0)
        self.viewport.pick_kinds = (
            () if self.mode == MODE_SKETCH else ("vertex", "edge", "face", "body")
        )
        self.hint.setText("")

    def _selected_signatures(self) -> list:
        """Подписи выбранных рёбер для операции.

        Передаётся подпись, а не номер: номер обхода меняется при каждой
        правке дерева, и «ребро №7» после пересчёта оказывается другим.
        """
        if not self.selected_edges or self.model.empty:
            return []
        found = []
        for index in self.selected_edges:
            signature = edge_signature_of(self.model.edge(index))
            if signature:
                found.append(signature)
        return found

    def _clear_edge_selection(self) -> None:
        self.selected_edges.clear()
        self.viewport.set_edge_selection(())

    def _sketch_for(self, command):
        """Эскиз для операции: последний нарисованный и ещё не израсходованный.

        Операции, которым нужен профиль, берут ближайший СВОБОДНЫЙ эскиз
        выше по дереву. Использовать уже израсходованный нельзя: повторное
        выдавливание того же контура молча дало бы неожиданное тело.
        """
        free = self.document.free_sketches()
        if free:
            return free[-1]
        self.status.showMessage(
            f"«{command.title}»: сначала создайте эскиз и нарисуйте профиль", 5000
        )
        return None

    def _edit_current(self) -> None:
        node = self.tree.currentItem()
        if node is not None:
            self._edit_feature(node, 0)

    def _delete_current(self) -> None:
        """Удалить то, что выбрано СЕЙЧАС, — по режиму и по фокусу.

        В эскизе это объекты эскиза, в модели — операция из дерева. Если в
        эскизе ничего не выбрано, клавиша молчит, а не удаляет операцию за
        спиной у правящегося эскиза.
        """
        if self.mode == MODE_SKETCH:
            if self.canvas.selection or self.canvas.selected_relation is not None:
                self.canvas.delete_selected()
                self._sketch_changed()
            else:
                self.status.showMessage(
                    "Удалять нечего: выберите объекты эскиза", 4000)
            return
        self._delete_feature()

    def _delete_feature(self) -> None:
        if self.mode == MODE_SKETCH:
            self.canvas.delete_selected()
            return
        node = self.tree.currentItem()
        name = node.data(0, QtCore.Qt.UserRole) if node else None
        if not name:
            return
        if name.startswith(self.SKETCH_MARK):
            sketch = self._sketch_named(name[len(self.SKETCH_MARK):])
            if sketch is None:
                return
            # Эскиз уносит с собой операции, которые на нём стоят: оставить
            # их значило бы оставить операции без профиля.
            self._pending_regions.pop(id(sketch), None)
            self.document.remove_sketch(sketch)
            self._rebuild(force=True)
            self.status.showMessage(f"Удалён эскиз: {sketch.name}", 4000)
            return
        operation = self.document.by_name(name)
        if operation is None:
            return
        self.document.remove(operation)
        self._rebuild(force=True)
        self.status.showMessage(f"Удалено: {name}", 4000)

    def _edit_feature(self, node, _column: int) -> None:
        """Двойной щелчок правит: эскиз — в эскизе, операцию — панелью.

        Раньше двойной щелчок по операции уводил В ЭСКИЗ, а операции без
        эскиза правке не поддавались вовсе. Между тем правят чаще всего не
        контур, а числа — глубину, радиус, сторону, — и добираться до них
        через эскиз незачем.
        """
        name = node.data(0, QtCore.Qt.UserRole)
        if not name:
            return
        if name.startswith(self.SKETCH_MARK):
            sketch = self._sketch_named(name[len(self.SKETCH_MARK):])
            if sketch is None:
                return
            owner = next((item for item in self.document.operations
                          if item.sketch is sketch), None)
            self._enter_sketch(sketch, owner)
            return
        operation = self.document.by_name(name)
        if operation is None:
            return
        self.edit_operation(operation)

    def edit_operation(self, operation) -> None:
        """Открыть панель правки операции. Отказ — если её нечем править."""
        descriptor = D.BY_KEY.get(operation.kind)
        if descriptor is None or descriptor.load is None:
            self.status.showMessage(
                f"«{operation.name}»: правка этой операции ещё не сделана"
                + ("; её эскиз можно открыть двойным щелчком по нему"
                   if operation.sketch is not None else ""), 6000)
            return
        self._start_command(descriptor, editing=operation)

    def _tree_menu(self, position) -> None:
        """Меню правой кнопки в дереве построения.

        Показывается ровно то, что применимо к выбранному: у эскиза свои
        действия, у операции свои. Пункт, который ничего не сделает, здесь
        хуже отсутствующего — по меню судят о том, что вообще можно.
        """
        node = self.tree.itemAt(position)
        if node is None:
            return
        self.tree.setCurrentItem(node)
        menu = self.tree_menu_for(node)
        if menu is not None and menu.actions():
            menu.exec(self.tree.viewport().mapToGlobal(position))

    def tree_menu_for(self, node) -> "QtWidgets.QMenu | None":
        """Собрать меню для узла дерева. Показ — отдельно от сборки.

        Разделено намеренно: показ модален и в проверке останавливает всё,
        а состав меню проверять надо — по нему судят о том, что вообще
        можно сделать с операцией.
        """
        name = node.data(0, QtCore.Qt.UserRole) or ""
        menu = QtWidgets.QMenu(self.tree)

        if name.startswith(self.SKETCH_MARK):
            sketch = self._sketch_named(name[len(self.SKETCH_MARK):])
            if sketch is None:
                return
            menu.addAction(icons.icon("sketch", 16), "Править эскиз",
                           lambda: self._edit_feature(node, 0))
            menu.addAction(icons.icon("check_sketch", 16), "Проверить эскиз",
                           lambda: self._check_named_sketch(sketch))
            menu.addSeparator()
            menu.addAction(icons.icon("delete", 16), "Удалить",
                           self._delete_feature)
            return menu

        operation = self.document.by_name(name) if name else None
        if operation is None:
            return None
        edit = menu.addAction(icons.icon("edit_feature", 16),
                              "Изменить операцию",
                              lambda: self.edit_operation(operation))
        edit.setEnabled(D.BY_KEY.get(operation.kind) is not None
                        and D.BY_KEY[operation.kind].load is not None)
        if operation.sketch is not None:
            menu.addAction(icons.icon("sketch", 16), "Править эскиз",
                           lambda: self._enter_sketch(operation.sketch,
                                                      operation))
        menu.addSeparator()
        place = self.document.operations.index(operation)
        menu.addAction(icons.icon("rebuild", 16), "Откатить сюда",
                       lambda: self._move_rollback(place))
        menu.addAction(icons.icon("rebuild", 16), "Строить до конца",
                       lambda: self._move_rollback(
                           len(self.document.operations)))
        menu.addSeparator()
        menu.addAction(icons.icon("visibility", 16), "Показать её грани",
                       lambda: self._show_selected_feature(operation))
        menu.addAction(icons.icon("rebuild", 16), "Пересчитать деталь",
                       lambda: self._rebuild(force=True))
        menu.addSeparator()
        menu.addAction(icons.icon("delete", 16), "Удалить",
                       self._delete_feature)
        return menu

    def _check_named_sketch(self, sketch) -> None:
        """Проверить эскиз, не входя в него."""
        from protocad.sketch import repair as repair_module

        found = repair_module.scan(sketch)
        if not found:
            self.status.showMessage(
                f"«{sketch.name}»: ничего не найдено", 5000)
            return
        self.status.showMessage(
            f"«{sketch.name}»: найдено {len(found)} — {found[0].text}", 12000)

    def _picked(self, identifier: int) -> None:
        label = self.viewport.label_of(identifier)
        if label:
            self.status.showMessage(f"выбрано: {label}", 4000)

    def _picked_in_view(self, what: dict) -> None:
        """Щелчок по детали: грань, ребро, точка или тело."""
        kind = what.get("kind")
        if self.session is not None and kind in ("face", "edge", "vertex", "body"):
            # Сначала — области показанного эскиза: они лежат ПОВЕРХ детали,
            # и щелчок по контуру должен попасть в контур, а не в грань под
            # ним. Если под указателем области нет, щелчок идёт дальше.
            point = what.get("point")
            if point is not None and self._pick_region_for_command(
                    self._sketch_point(point)):
                return
            # Пока команда не завершена, выбор принадлежит ЕЙ. Обычный
            # выбор здесь только мешал бы: подсветилось бы одно, а в поле
            # ушло другое.
            self._session_pick(what)
            return
        if kind == "face":
            index = int(what.get("index", -1))
            plane = self.model.plane_of(index) if index >= 0 else None
            self.selected_face_plane = plane
            self.selected_face_index = index
            if self.model_picking == "reference" and index >= 0:
                self._reference_face(index)
                return
            if self.model.face(index) is None:
                self.status.showMessage("Грань выбрана, но в теле не найдена", 4000)
                return
            if plane is None:
                self.status.showMessage(
                    f"{what.get('label', 'Грань')}: не плоская, эскиз на ней не строится",
                    5000,
                )
                return
            self.status.showMessage(
                f"{what.get('label', 'Грань')} — плоская, нормаль "
                f"{plane.normal[0]:.2f}, {plane.normal[1]:.2f}, {plane.normal[2]:.2f}. "
                f"«Эскиз» построит его на ней.",
                6000,
            )
        elif kind == "edge":
            index = int(what.get("index", -1))
            self.selected_point = tuple(what["point"])
            if index < 0:
                return
            if self.model_picking == "convert":
                self._convert_edge(index)
                return
            if self.model_picking == "reference":
                self._reference_edge(index)
                return
            if index in self.selected_edges:
                self.selected_edges.remove(index)
            else:
                self.selected_edges.append(index)
            self.viewport.set_edge_selection(self.selected_edges)
            self.status.showMessage(
                f"Рёбер выбрано: {len(self.selected_edges)}. "
                f"«Скругление» и «Фаска» снимут именно их.", 6000
            )
        elif kind == "vertex":
            x, y, z = what["point"]
            self.selected_point = (x, y, z)
            if self.model_picking == "reference":
                self._reference_vertex((x, y, z))
                return
            self.status.showMessage(f"Точка: {x:.3f}, {y:.3f}, {z:.3f}", 5000)
        elif kind == "body":
            self.selected_face_plane = None
            label = what.get("label", "")
            if label:
                self.status.showMessage(f"выбрано: {label}", 4000)


def _plane_trace(sketch_plane, face_plane, reach: float = 500.0):
    """След одной плоскости на другой — в плоских координатах эскиза.

    Возвращает два конца прямой либо ``None``, если плоскости параллельны.
    Длина следа условная: прямая в эскизе вспомогательная, важно её
    положение и направление, а не то, докуда она нарисована.
    """
    import numpy as _np

    normal_a = _np.array(sketch_plane.normal, float)
    normal_b = _np.array(face_plane.normal, float)
    along = _np.cross(normal_a, normal_b)
    if float(_np.linalg.norm(along)) < 1e-9:
        return None
    along = along / float(_np.linalg.norm(along))
    # Точка на линии пересечения: решаем систему двух плоскостей плюс
    # плоскость, перпендикулярную обеим, — она делает решение единственным.
    matrix = _np.array([normal_a, normal_b, along])
    right = _np.array([
        float(_np.dot(normal_a, _np.array(sketch_plane.origin, float))),
        float(_np.dot(normal_b, _np.array(face_plane.origin, float))),
        0.0,
    ])
    try:
        point = _np.linalg.solve(matrix, right)
    except _np.linalg.LinAlgError:
        return None
    first = sketch_plane.project(tuple(point - along * reach))
    second = sketch_plane.project(tuple(point + along * reach))
    return first, second


def _unique_sketch_name(document, base: str) -> str:
    """Имя эскиза без совпадений: по нему его находят в дереве."""
    existing = {sketch.name for sketch in document.sketches}
    if base not in existing:
        return base
    index = 2
    while f"{base} {index}" in existing:
        index += 1
    return f"{base} {index}"


def _face_source_of(face, precision: int = 3) -> str:
    """Подпись грани по её ОПИСАНИЮ: нормаль и удаление плоскости от нуля.

    Считается по числам, пришедшим от движка, а не по форме ядра: окно
    формы больше не разбирает.
    """
    if face is None or not face.planar:
        return ""
    _, normal, _ = face.plane
    centre = face.data.get("center") or (0.0, 0.0, 0.0)
    offset = sum(normal[i] * centre[i] for i in range(3))
    values = [round(float(value), precision) for value in normal] + [
        round(float(offset), precision)]
    return ",".join(str(value) for value in values)


def _edge_source_of(edge, precision: int = 3) -> str:
    """Та же подпись строкой — для ссылки эскиза на ребро детали."""
    signature = edge_signature_of(edge, precision)
    if not signature:
        return ""
    return "(" + ", ".join(str(value) for value in signature) + ")"


def _vertex_source(found, position, precision: int = 3) -> str:
    """Подпись вершины детали: её номер, общее число вершин и место.

    Номер даёт ссылке пережить правку размеров, место — запасной путь,
    когда число вершин изменилось. Подробности отбора — в ``feature._locate``.

    Вершины приходят СПИСКОМ ТОЧЕК, а не формой: окно их не извлекает.
    """
    index = -1
    best = None
    for current, vertex in enumerate(found):
        distance = sum((float(vertex[i]) - float(position[i])) ** 2 for i in range(3))
        if best is None or distance < best:
            index, best = current, distance
    x, y, z = (round(float(value), precision) for value in position)
    return f"v:{index}/{len(found)}:{x},{y},{z}"


class _PlaneDialog(QtWidgets.QDialog):
    """Выбор плоскости эскиза.

    Кроме трёх основных плоскостей предлагаются грани готового тела —
    верхняя и нижняя. Выбирать грань щелчком в трёхмерном виде пока нельзя:
    вид опознаёт деталь целиком, а не отдельные грани. Пока этого нет,
    список именованных граней покрывает большинство случаев честнее, чем
    единственная плоскость XY.
    """

    def __init__(self, model, parent=None, planes=()):
        super().__init__(parent)
        self.setWindowTitle("Плоскость эскиза")
        self._choices: list[tuple[str, object]] = [
            (plane_module.TITLES[key], plane_module.STANDARD[key])
            for key in ("XY", "XZ", "YZ")
        ]
        #: Опора для каждого пункта списка — ровно в том же порядке.
        #: Двумя списками, а не парами, чтобы прежний код, читающий
        #: `_choices`, продолжал работать.
        self._kinds: list = [{"kind": "standard", "standard": key}
                             for key in ("XY", "XZ", "YZ")]
        # Справочные плоскости идут ПЕРЕД гранями: человек завёл их сам и
        # именно ради того, чтобы на них чертить. Непостроенные не
        # предлагаются вовсе — чертить на них нечего.
        for record in planes:
            if record.ok:
                self._choices.append((f"{record.name} (справочная)",
                                      record.plane))
                self._kinds.append({"kind": "plane", "name": record.name})
        if model is not None and not model.empty:
            faces = _named_faces(model)
            self._choices.extend(faces)
            for _, plane in faces:
                self._kinds.append(_support_of_plane(model, plane))

        form = QtWidgets.QFormLayout()
        self.selector = QtWidgets.QComboBox()
        self.selector.addItems([title for title, _ in self._choices])
        form.addRow("Плоскость", self.selector)

        self.offset = QtWidgets.QDoubleSpinBox()
        self.offset.setRange(-100000.0, 100000.0)
        self.offset.setDecimals(3)
        self.offset.setSuffix(" мм")
        self.offset.setToolTip("Смещение вдоль нормали выбранной плоскости")
        form.addRow("Смещение", self.offset)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def plane(self):
        _, base = self._choices[self.selector.currentIndex()]
        distance = self.offset.value()
        if abs(distance) < 1e-12:
            return base
        return base.offset(distance, f"{base.name}{distance:+g}")

    def support(self):
        """На чём выбранная плоскость стоит. ``None`` — ни на чём.

        Смещение входит В ОПОРУ, а не остаётся в координатах: эскиз «на 5
        мм над верхней гранью» обязан ехать за гранью вместе со своими
        пятью миллиметрами.
        """
        index = self.selector.currentIndex()
        kind = self._kinds[index] if index < len(self._kinds) else None
        if kind is None:
            return None
        return dict(kind, offset=float(self.offset.value()))


def _support_of_plane(model, plane):
    """Опора для плоскости, взятой у грани: по совпадению начала и нормали.

    Список именованных граней отдаёт готовые плоскости, а опоре нужен
    адрес грани. Сопоставляется по числам, которые в плоскость и попали, —
    искать заново нечего.
    """
    for item in model.faces:
        data = item.data or {}
        origin = data.get("origin")
        normal = data.get("normal")
        if not origin or not normal:
            continue
        if all(abs(origin[i] - plane.origin[i]) < 1e-9 for i in range(3))                 and all(abs(normal[i] - plane.normal[i]) < 1e-9
                        for i in range(3)):
            return {"kind": "face", "index": int(item.index),
                    "name": getattr(item, "name", "") or "",
                    "origin": list(origin), "normal": list(normal)}
    return None


def _named_faces(model) -> list[tuple[str, object]]:
    """Плоские грани тела, названные по положению.

    Названия даются по центру масс, а не по номеру грани: порядок граней
    меняется при любой правке дерева, и «грань №4» после пересчёта
    оказывается другой поверхностью.

    Берутся грани из ОПИСАНИЯ детали: нормаль и центр приходят числами, и
    сразу же строится плоскость эскиза — форм ядра здесь нет.
    """
    named = []
    for item in model.faces:
        plane = model.plane_of(item.index)
        if plane is None:
            continue
        normal = item.plane[1]
        center = item.data.get("center") or (0.0, 0.0, 0.0)
        for axis, positive, negative in (
            (2, "верхняя", "нижняя"), (1, "задняя", "передняя"), (0, "правая", "левая")
        ):
            if abs(abs(normal[axis]) - 1.0) > 1e-6:
                continue
            title = positive if normal[axis] > 0 else negative
            named.append((f"{title} грань ({center[axis]:.1f} мм)", plane, axis,
                          center[axis], normal[axis]))
            break
    # Из параллельных граней оставляем крайние: внутренние поверхности
    # кармана в списке только мешают.
    result = []
    for axis in (2, 1, 0):
        group = [item for item in named if item[2] == axis]
        for sign in (1.0, -1.0):
            side = [item for item in group if item[4] * sign > 0]
            if not side:
                continue
            best = max(side, key=lambda item: item[3] * sign)
            result.append((best[0], best[1]))
    return result


class _TransformDialog(QtWidgets.QDialog):
    """Что сделать с выбранным: перенести, повернуть, изменить размер.

    Поля показываются по виду преобразования: смещение у переноса, центр и
    угол у поворота, центр и коэффициент у масштаба. Показывать всё сразу
    значило бы предлагать ввести числа, которые ни на что не влияют.
    """

    MODES = ("Перенести", "Повернуть", "Изменить размер")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Преобразовать")
        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(self.MODES)
        form = QtWidgets.QFormLayout()
        form.addRow("Что сделать", self.mode)
        self._fields, self._rows = {}, {}
        for key, label, default, low, high in (
            ("dx", "Смещение по X, мм", 10.0, -1e5, 1e5),
            ("dy", "Смещение по Y, мм", 0.0, -1e5, 1e5),
            ("x", "Центр X, мм", 0.0, -1e5, 1e5),
            ("y", "Центр Y, мм", 0.0, -1e5, 1e5),
            ("angle", "Угол, град", 90.0, -3600.0, 3600.0),
            ("factor", "Коэффициент", 2.0, 0.001, 1000.0),
        ):
            widget = QtWidgets.QDoubleSpinBox()
            widget.setRange(low, high)
            widget.setDecimals(3)
            widget.setValue(default)
            self._fields[key] = widget
            self._rows[key] = QtWidgets.QLabel(label)
            form.addRow(self._rows[key], widget)
        self.copy = QtWidgets.QCheckBox("Оставить исходные (копия)")
        self.keep = QtWidgets.QCheckBox("Сохранить связи")
        self.keep.setChecked(True)
        form.addRow("", self.copy)
        form.addRow("", self.keep)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)
        self.mode.currentTextChanged.connect(self._shown)
        self._shown(self.mode.currentText())

    def _shown(self, mode: str) -> None:
        needed = {
            "Перенести": ("dx", "dy"),
            "Повернуть": ("x", "y", "angle"),
            "Изменить размер": ("x", "y", "factor"),
        }[mode]
        for key, widget in self._fields.items():
            widget.setVisible(key in needed)
            self._rows[key].setVisible(key in needed)
        # Связи снимать при копии нечего: исходные объекты остаются как
        # были, а у копий связей ещё нет.
        self.keep.setEnabled(not self.copy.isChecked())

    def values(self) -> dict:
        answer = {key: widget.value() for key, widget in self._fields.items()}
        answer["mode"] = self.mode.currentText()
        answer["copy"] = self.copy.isChecked()
        answer["keep_relations"] = self.keep.isChecked()
        return answer


class _LinearPatternDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Линейный массив")
        form = QtWidgets.QFormLayout()
        self._fields = {}
        for key, label, default, integer in (
            ("dx", "Шаг по X, мм", 20.0, False),
            ("dy", "Шаг по Y, мм", 0.0, False),
            ("count", "Количество", 3, True),
            ("dx2", "Второе направление, X", 0.0, False),
            ("dy2", "Второе направление, Y", 20.0, False),
            ("count2", "Количество во втором", 1, True),
        ):
            widget = QtWidgets.QSpinBox() if integer else QtWidgets.QDoubleSpinBox()
            widget.setRange(1 if integer else -1e5, 999 if integer else 1e5)
            widget.setValue(default)
            if not integer:
                widget.setDecimals(3)
            self._fields[key] = widget
            form.addRow(label, widget)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> dict:
        return {key: widget.value() for key, widget in self._fields.items()}


class _CircularPatternDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Круговой массив")
        form = QtWidgets.QFormLayout()
        self._fields = {}
        for key, label, default, integer in (
            ("x", "Центр X, мм", 0.0, False),
            ("y", "Центр Y, мм", 0.0, False),
            ("count", "Количество", 6, True),
            ("angle", "Полный угол, град", 360.0, False),
        ):
            widget = QtWidgets.QSpinBox() if integer else QtWidgets.QDoubleSpinBox()
            widget.setRange(2 if integer else -1e5, 999 if integer else 1e5)
            widget.setValue(default)
            if not integer:
                widget.setDecimals(3)
            self._fields[key] = widget
            form.addRow(label, widget)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> dict:
        return {key: widget.value() for key, widget in self._fields.items()}



def _absorb(target, source) -> None:
    """Перенести набранные значения в УЖЕ существующую операцию.

    Имя, место в дереве и имя в движке остаются прежними: правка обязана
    остаться правкой. Пересоздать операцию нельзя — на неё ссылаются те,
    что ниже по дереву, и ссылки порвались бы.
    """
    from dataclasses import fields

    for item in fields(source):
        if item.name in MainWindow.KEEP_ON_EDIT:
            continue
        setattr(target, item.name, getattr(source, item.name))


def main() -> int:
    QtGui.QSurfaceFormat.setDefaultFormat(default_surface_format())
    app = QtWidgets.QApplication(sys.argv)

    try:
        if len(sys.argv) > 1:
            document = Document("Деталь")
            fmt.read_document(sys.argv[1], document)
        else:
            document = demo_document()
    except Exception as failure:  # noqa: BLE001
        # Движок может не подняться — например, FreeCAD не найден. Молча
        # открыть пустое окно нельзя: человек решит, что деталь пропала.
        QtWidgets.QMessageBox.critical(None, "ProtoCAD", str(failure))
        return 1

    window = MainWindow(document)
    window.setWindowTitle(f"ProtoCAD — {document.designation}")
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
