"""Лента команд: вкладки, группы, кнопки.
Лента описана данными, а не собрана кодом. Так весь состав команд виден
одним списком, а не разбросан по обработчикам, и добавление операции — это
строка в таблице плюс обработчик по ключу. Заодно это единственное место,
где задан порядок команд: спорить о нём удобно, глядя на список.

Кнопка ничего не выполняет сама — она сообщает ключ. Что делать с ключом,
решает окно: в режиме эскиза и в режиме детали один и тот же «Зеркало»
означает разные вещи, и разводить их надо там, где известен режим.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6 import QtCore, QtGui, QtWidgets

import icons


@dataclass
class Tool:
    key: str
    title: str
    glyph: str = ""
    tip: str = ""
    shortcut: str = ""
    checkable: bool = False
    big: bool = False
    # Переключатели рисования взаимоисключающи, переключатели ПОКАЗА — нет.
    # Без этого различия «Рёбра» гасили «Перспективу»: обе кнопки нажимные
    # и лежат на одной вкладке, а состояние у них независимое.
    exclusive: bool = True
    # Числовое поле прямо в ленте: радиус скругления и число сторон
    # многоугольника нужны ПЕРЕД щелчком по чертежу. Спрашивать их модальным
    # окном при включении инструмента — значит закрывать чертёж ровно в тот
    # момент, когда на него надо смотреть, и переспрашивать при каждом
    # возврате к инструменту.
    kind: str = "button"      # "button" | "number" | "integer" | "menu"
    # Для kind == "menu": вложенные переключатели показа.
    items: list = field(default_factory=list)
    default: float = 0.0
    minimum: float = 0.0
    maximum: float = 1000.0
    decimals: int = 2
    suffix: str = ""

    def __post_init__(self):
        self.glyph = self.glyph or self.key


@dataclass
class Group:
    title: str
    tools: list = field(default_factory=list)


@dataclass
class Tab:
    key: str
    title: str
    groups: list = field(default_factory=list)


class _ToolButton(QtWidgets.QToolButton):
    def _needed_width(self, padding: int, icon: int = 0) -> int:
        """Сколько места нужно, чтобы подпись читалась целиком."""
        metrics = self.fontMetrics()
        widest = max(
            (metrics.horizontalAdvance(word)
             for word in self.text().replace("-", "- ").split()),
            default=0,
        )
        return int(widest + padding + icon)

    def __init__(self, tool: Tool):
        super().__init__()
        self.tool = tool
        self.setIcon(icons.icon(tool.glyph, 32 if tool.big else 20))
        self.setText(tool.title)
        self.setCheckable(tool.checkable)
        self.setAutoRaise(True)
        if tool.big:
            self.setToolButtonStyle(QtCore.Qt.ToolButtonTextUnderIcon)
            self.setIconSize(QtCore.QSize(32, 32))
            # Ширина считается по САМОМУ ДЛИННОМУ СЛОВУ подписи, а не берётся
            # постоянной. С постоянной «Выдавливание» не влезало и обрезалось
            # многоточием: подпись, которую нельзя дочитать, не отличается
            # от отсутствующей.
            self.setMinimumWidth(self._needed_width(6))
            self.setMaximumWidth(self._needed_width(28))
        else:
            # Только значок. Подпись рядом с ним съедала ширину ленты и всё
            # равно не читалась: «Прямоугольник по трём точкам» не влезает
            # ни в какую разумную кнопку. Название есть в подсказке, а
            # значок обязан быть узнаваем сам по себе — поэтому у каждого
            # инструмента он теперь свой, а не общий на три команды.
            self.setToolButtonStyle(QtCore.Qt.ToolButtonIconOnly)
            self.setIconSize(QtCore.QSize(22, 22))
            self.setFixedSize(30, 28)
        hint = tool.tip or tool.title
        if tool.shortcut:
            hint = f"{hint}  ({tool.shortcut})"
        self.setToolTip(hint)


class _GroupBox(QtWidgets.QWidget):
    """Группа ленты: кнопки сверху, название снизу.

    Крупные кнопки занимают всю высоту, мелкие складываются по три в
    столбец — так группа держит одинаковую высоту независимо от состава, и
    лента не «дышит» при переключении вкладок.
    """

    ROWS = 3

    def __init__(self, group: Group, make_button):
        super().__init__()
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(6, 4, 6, 2)
        outer.setSpacing(2)

        grid = QtWidgets.QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(1)
        column, row = 0, 0
        for tool in group.tools:
            if tool.kind != "button":
                widget = make_button(tool)
                grid.addWidget(widget, row, column, 1, 1)
                row += 1
                if row >= self.ROWS:
                    row, column = 0, column + 1
                continue
            button = make_button(tool)
            if tool.big:
                if row != 0:
                    column += 1
                    row = 0
                grid.addWidget(button, 0, column, self.ROWS, 1)
                column += 1
            else:
                grid.addWidget(button, row, column, 1, 1)
                row += 1
                if row >= self.ROWS:
                    row, column = 0, column + 1
        outer.addLayout(grid)
        outer.addStretch(1)

        caption = QtWidgets.QLabel(group.title)
        caption.setAlignment(QtCore.Qt.AlignHCenter)
        font = caption.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 1.5))
        caption.setFont(font)
        caption.setStyleSheet("color: palette(mid);")
        outer.addWidget(caption)


class Ribbon(QtWidgets.QTabWidget):
    """Лента целиком."""

    activated = QtCore.Signal(str)
    switched = QtCore.Signal(str, bool)

    def __init__(self, tabs: list[Tab]):
        super().__init__()
        self.setDocumentMode(True)
        self._buttons: dict[str, _ToolButton] = {}
        self._numbers: dict[str, QtWidgets.QAbstractSpinBox] = {}
        self._exclusive: dict[str, str] = {}  # ключ кнопки → её вкладка
        self._menu_actions: dict[str, QtGui.QAction] = {}
        self._pages: dict[str, int] = {}
        self._actions: list[QtGui.QAction] = []
        for tab in tabs:
            self._pages[tab.key] = self.count()
            self.addTab(self._build_page(tab), tab.title)
        self.setMaximumHeight(132)

    def _build_page(self, tab: Tab) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(page)
        layout.setContentsMargins(4, 2, 4, 0)
        layout.setSpacing(0)
        for index, group in enumerate(tab.groups):
            if index:
                separator = QtWidgets.QFrame()
                separator.setFrameShape(QtWidgets.QFrame.VLine)
                separator.setStyleSheet("color: palette(midlight);")
                layout.addWidget(separator)
            layout.addWidget(
                _GroupBox(group, lambda tool, t=tab: self._make_button(tool, t))
            )
        layout.addStretch(1)
        return page

    def _make_button(self, tool: Tool, tab: Tab):
        if tool.kind == "menu":
            return self._make_menu(tool)
        if tool.kind != "button":
            return self._make_number(tool)
        button = _ToolButton(tool)
        self._buttons[tool.key] = button
        if tool.checkable:
            if tool.exclusive:
                self._exclusive[tool.key] = tab.key
            button.toggled.connect(lambda on, k=tool.key: self._toggled(k, on))
        else:
            button.clicked.connect(lambda _=False, k=tool.key: self.activated.emit(k))
        if tool.shortcut:
            # Действие вешается на окно, а не на кнопку: сочетание обязано
            # работать и когда указатель в поле чертежа.
            action = QtGui.QAction(button)
            action.setShortcut(QtGui.QKeySequence(tool.shortcut))
            action.setShortcutContext(QtCore.Qt.WindowShortcut)
            action.triggered.connect(
                lambda _=False, b=button: b.toggle() if b.isCheckable() else b.click()
            )
            self._actions.append(action)
        return button

    def _make_menu(self, tool: Tool) -> QtWidgets.QWidget:
        """Список переключателей показа под одной кнопкой.

        Их с десяток, и каждому отдельная кнопка на ленте — половина
        ширины ленты под то, что трогают раз в сеанс.
        """
        button = _ToolButton(tool)
        menu = QtWidgets.QMenu(button)
        for item in tool.items:
            action = QtGui.QAction(item.title, menu)
            action.setToolTip(item.tip)
            if item.checkable:
                action.setCheckable(True)
                action.toggled.connect(
                    lambda on, k=item.key: self._menu_toggled(k, on))
                self._menu_actions[item.key] = action
            else:
                # Не переключатель, а команда: список видов размера
                # выбирает инструмент, а инструмент один на все.
                action.triggered.connect(
                    lambda _=False, k=item.key: self.activated.emit(k))
            menu.addAction(action)
        button.setMenu(menu)
        button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self._buttons[tool.key] = button
        return button

    def _make_number(self, tool: Tool) -> QtWidgets.QWidget:
        holder = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(holder)
        row.setContentsMargins(2, 0, 2, 0)
        row.setSpacing(4)
        caption = QtWidgets.QLabel(tool.title)
        font = caption.font()
        font.setPointSizeF(max(7.0, font.pointSizeF() - 0.5))
        caption.setFont(font)
        row.addWidget(caption)

        if tool.kind == "integer":
            spin = QtWidgets.QSpinBox()
            spin.setRange(int(tool.minimum), int(tool.maximum))
            spin.setValue(int(tool.default))
        else:
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(tool.minimum, tool.maximum)
            spin.setDecimals(tool.decimals)
            spin.setValue(tool.default)
        spin.setSuffix(tool.suffix)
        spin.setKeyboardTracking(False)
        spin.setMaximumWidth(84)
        spin.setToolTip(tool.tip or tool.title)
        row.addWidget(spin)
        self._numbers[tool.key] = spin
        return holder

    def value(self, key: str, fallback: float = 0.0) -> float:
        spin = self._numbers.get(key)
        return spin.value() if spin is not None else fallback

    def install_shortcuts(self, window: QtWidgets.QWidget) -> None:
        for action in self._actions:
            window.addAction(action)

    def _menu_toggled(self, key: str, on: bool) -> None:
        """Пункт списка видимости переключён.

        Кнопка с тем же ключом на вкладке эскиза догоняет молча: «Сетка» в
        двух местах обязана показывать одно состояние, иначе одна из них
        врёт.
        """
        self._sync_twin(key, on, menu=False)
        self.switched.emit(key, on)

    def _sync_twin(self, key: str, on: bool, menu: bool) -> None:
        target = self._menu_actions.get(key) if menu else self._buttons.get(key)
        if target is None or not target.isCheckable():
            return
        if target.isChecked() == on:
            return
        target.blockSignals(True)
        target.setChecked(on)
        target.blockSignals(False)

    def _toggled(self, key: str, on: bool) -> None:
        self._sync_twin(key, on, menu=True)
        if on:
            # Инструменты рисования взаимоисключающи в пределах вкладки:
            # два включённых одновременно означали бы, что щелчок мыши
            # выполняет две команды сразу.
            page = self._exclusive.get(key)
            for other, other_page in self._exclusive.items():
                if other != key and other_page == page:
                    button = self._buttons[other]
                    if button.isChecked():
                        button.blockSignals(True)
                        button.setChecked(False)
                        button.blockSignals(False)
        self.switched.emit(key, on)

    # --- управление состоянием ---

    def check(self, key: str, on: bool = True, silent: bool = False) -> None:
        """Нажать или отпустить кнопку.

        ``silent`` — не поднимая сигнала. Нужен, когда лента ДОГОНЯЕТ уже
        сменившийся инструмент: иначе она сообщит о смене обратно, поле
        сменит инструмент ещё раз, и связка зациклится.
        """
        action = self._menu_actions.get(key)
        if action is not None:
            self._sync_twin(key, on, menu=False)
            if action.isChecked() == on:
                return
            if silent:
                action.blockSignals(True)
                action.setChecked(on)
                action.blockSignals(False)
            else:
                action.setChecked(on)
            return
        button = self._buttons.get(key)
        if button is None or not button.isCheckable():
            return
        if button.isChecked() == on:
            return
        if silent:
            button.blockSignals(True)
            button.setChecked(on)
            button.blockSignals(False)
            if on:
                self._release_others(key)
            return
        button.setChecked(on)

    def _release_others(self, key: str) -> None:
        """Отпустить остальные переключатели той же вкладки, молча."""
        page = self._exclusive.get(key)
        for other, other_page in self._exclusive.items():
            if other == key or other_page != page:
                continue
            button = self._buttons[other]
            if button.isChecked():
                button.blockSignals(True)
                button.setChecked(False)
                button.blockSignals(False)

    def checked_key(self, tab_key: str) -> str:
        for key, page in self._exclusive.items():
            if page == tab_key and self._buttons[key].isChecked():
                return key
        return ""

    def clear_checks(self, tab_key: str) -> None:
        for key, page in self._exclusive.items():
            if page != tab_key:
                continue
            button = self._buttons[key]
            if button.isChecked():
                button.blockSignals(True)
                button.setChecked(False)
                button.blockSignals(False)

    def enable(self, key: str, on: bool) -> None:
        button = self._buttons.get(key)
        if button is not None:
            button.setEnabled(on)

    def show_tab(self, key: str) -> None:
        index = self._pages.get(key)
        if index is not None:
            self.setCurrentIndex(index)

    def set_tab_enabled(self, key: str, on: bool) -> None:
        index = self._pages.get(key)
        if index is not None:
            self.setTabEnabled(index, on)


# --- состав ленты -----------------------------------------------------------
#
# Порядок повторяет отраслевую практику: сначала выбор и построение, затем
# правка, затем связи и размеры. Подписи и значки — свои.

PART_TAB = Tab("part", "Элементы", [
    Group("Эскиз", [
        Tool("part.sketch", "Эскиз", "sketch",
             "Создать эскиз на плоскости или грани", "S", big=True),
    ]),
    Group("Добавление", [
        Tool("part.pad", "Выдав-\nливание", "pad",
             "Выдавить профиль эскиза", big=True),
        Tool("part.revolve", "Вращение", "revolve",
             "Повернуть профиль вокруг оси", big=True),
    ]),
    Group("Удаление", [
        Tool("part.pocket", "Вырез", "pocket",
             "Вырезать выдавливанием профиля", big=True),
        Tool("part.hole_feature", "Отверстие", "hole",
             "Отверстие как осевая конструкция: цековка, зенковка, дно, "
             "две стороны. Позиции — точки эскиза, одна операция на все",
             big=True),
        Tool("part.shell", "Оболочка", "shell",
             "Оставить стенку заданной толщины"),
    ]),
    Group("Обработка", [
        Tool("part.fillet", "Скругление", "fillet",
             "Скруглить выбранные рёбра", big=True),
        Tool("part.chamfer", "Фаска", "chamfer",
             "Срезать выбранные рёбра", big=True),
        Tool("part.draft", "Уклон", "draft",
             "Наклонить грани вокруг нейтральной"),
    ]),
    Group("Справочная геометрия", [
        Tool("part.plane", "Плоскость", "datum_plane",
             "Плоскость: параллельно грани, посередине между двумя "
             "или через вершину", big=True),
    ]),
    Group("Преобразования", [
        Tool("part.mirror", "Зеркало", "mirror_body",
             "Отразить деталь относительно плоскости", big=True),
        Tool("part.linear_pattern", "Линейный\nмассив", "linear_pattern",
             "Размножить операции рядами вдоль осей", big=True),
        Tool("part.circular_pattern", "Круговой\nмассив", "circular_pattern",
             "Размножить операции вокруг оси", big=True),
    ]),
    Group("Дерево", [
        Tool("part.edit", "Править операцию", "edit_feature",
             "Изменить параметры выбранной операции"),
        Tool("part.delete", "Удалить операцию", "delete",
             "Убрать выбранную операцию из дерева"),
        Tool("part.rebuild", "Пересчитать", "rebuild", "", "Ctrl+R"),
    ]),
])


SKETCH_TAB = Tab("sketch", "Эскиз", [
    # Порядок групп повторяет отраслевую практику: выход из эскиза,
    # размеры, построение, правка, массивы, связи, показ. Он сложился не
    # случайно — так идёт сама работа: нарисовал, задал размер, поправил.
    # Значки, названия команд и оформление здесь свои: повторять их
    # запрещено рамкой проекта (docs/00_CONTEXT.md).
    Group("Эскиз", [
        Tool("sketch.select", "Выбор", "select", "Выбор и перетаскивание",
             "Esc", checkable=True, big=True),
        Tool("sketch.finish", "Закрыть\nэскиз", "sketch_accept",
             "Выйти из эскиза, сохранив правки", big=True),
        Tool("sketch.cancel", "Отменить\nправки", "sketch_reject",
             "Выйти из эскиза, отменив всё сделанное в нём", big=True),
    ]),
    Group("Размеры", [
        Tool("dim.smart", "Размер", "dimension",
             "Размер по выбранному: длина, радиус, угол", "D",
             checkable=True, big=True),
        Tool("dim.list", "Виды\nразмеров", "dimension_baseline",
             "Размер строго по оси, радиус, диаметр, угол",
             big=True, kind="menu", items=[
                 Tool("dim.smart", "Размер по выбранному",
                      tip="Сам решает, что мерить: длину, радиус или угол"),
                 Tool("dim.horizontal", "Горизонтальный размер",
                      tip="Расстояние по оси X"),
                 Tool("dim.vertical", "Вертикальный размер",
                      tip="Расстояние по оси Y"),
                 Tool("dim.radius", "Радиус"),
                 Tool("dim.diameter", "Диаметр"),
                 Tool("dim.angle", "Угол между отрезками"),
             ]),
        Tool("dim.define", "Полностью\nопределить", "define",
             "Добавить связи и размеры, пока эскиз не определится"),
    ]),
    Group("Построение", [
        Tool("draw.line", "Отрезок", "line", "Отрезок по двум точкам",
             "L", checkable=True, big=True),
        Tool("draw.rectangle", "Прямо-\nугольник", "rectangle",
             "Прямоугольник по двум углам", "R", checkable=True, big=True),
        Tool("draw.spline", "Сплайн", "spline",
             "Кривая через указанные точки", checkable=True),
        Tool("draw.ellipse", "Эллипс", "ellipse",
             "Эллипс: центр, большая ось, малая полуось", checkable=True),
        Tool("draw.hyperbola", "Гипербола", "hyperbola",
             "Гипербола: центр, вершина, мнимая полуось, затем концы",
             checkable=True),
        Tool("draw.parabola", "Парабола", "parabola",
             "Парабола: вершина, фокус, затем концы дуги", checkable=True),
        Tool("draw.ellipse_arc", "Дуга\nэллипса", "ellipse_arc",
             "Дуга эллипса: центр, большая ось, малая полуось, "
             "затем начало и конец", checkable=True),
        Tool("draw.circle", "Окруж-\nность", "circle",
             "Окружность из центра", "C", checkable=True, big=True),
        Tool("draw.arc", "Дуга", "arc", "Дуга по центру и двум точкам",
             "A", checkable=True, big=True),
        Tool("draw.polyline", "Ломаная", "polyline",
             "Цепочка отрезков", "W", checkable=True),
        Tool("draw.center_rectangle", "Прямоугольник от центра",
             "center_rectangle", "Прямоугольник от центра", checkable=True),
        Tool("draw.rectangle_3p", "Прямоугольник по трём точкам", "rectangle_3p",
             "Наклонный прямоугольник: угол, сторона, ширина", checkable=True),
        Tool("draw.parallelogram", "Параллелограмм", "parallelogram",
             "Угол и два направления", checkable=True),
        Tool("draw.arc_3p", "Дуга по трём точкам", "arc_3p",
             "Дуга через три точки", checkable=True),
        Tool("draw.tangent_arc", "Касательная дуга", "tangent_arc",
             "Дуга, продолжающая контур по касательной", checkable=True),
        Tool("draw.circle_3p", "Окружность по трём точкам", "circle_3p",
             "Окружность через три точки", checkable=True),
        Tool("draw.slot", "Паз", "slot", "Паз по оси и ширине", checkable=True),
        Tool("draw.center_slot", "Паз от центра", "center_slot",
             "Паз: центр, половина длины, ширина", checkable=True),
        Tool("draw.polygon", "Многоугольник", "polygon",
             "Правильный многоугольник", checkable=True),
        Tool("draw.point", "Точка", "point", "Одиночная точка", checkable=True),
    ]),
    Group("Правка", [
        Tool("edit.trim", "Отсечь", "trim",
             "Убрать участок до пересечений", "T", checkable=True, big=True),
        Tool("edit.offset", "Смещение", "offset",
             "Смещённая копия контура", "O", big=True),
        Tool("edit.extend", "Продлить", "extend",
             "Продлить до встречного объекта", checkable=True),
        Tool("edit.fillet", "Скругление", "fillet",
             "Скруглить угол дугой", checkable=True),
        Tool("edit.chamfer", "Фаска", "chamfer", "Срезать угол", checkable=True),
        Tool("edit.transform", "Преобразовать", "transform",
             "Перенести, повернуть или изменить размер выбранного"),
        Tool("edit.check", "Проверить", "check_sketch",
             "Найти разрывы, дубли и ветвления контура"),
        Tool("edit.repair", "Исправить", "repair_sketch",
             "Закрыть разрывы и убрать случайные объекты"),
        Tool("edit.delete", "Удалить", "delete", "Удалить выбранное"),
    ]),
    Group("Деталь", [
        Tool("draw.convert", "Преобра-\nзовать", "convert",
             "Перенести ребро детали в эскиз проекцией на его плоскость",
             checkable=True, big=True),
        Tool("draw.reference", "Ссылка\nна деталь", "reference_point",
             "Взять вершину, ребро или грань детали опорой для связей",
             checkable=True, big=True),
        Tool("draw.intersect", "Кривая\nпересечения", "intersect",
             "Взять след детали на плоскости эскиза: точный контур сечения",
             big=True),
    ]),
    Group("Массивы", [
        Tool("edit.mirror", "Зеркало", "mirror",
             "Отразить относительно осевой"),
        Tool("edit.pattern_linear", "Линейный массив", "pattern_linear",
             "Копии по одному или двум направлениям"),
        Tool("edit.pattern_circular", "Круговой массив", "pattern_circular",
             "Копии по окружности"),
    ]),
    Group("Размер правки", [
        Tool("value.fillet", "R", tip="Радиус скругления в эскизе",
             kind="number", default=5.0, minimum=0.001, maximum=1e5,
             decimals=3, suffix=" мм"),
        Tool("value.chamfer", "Фаска", tip="Размер фаски в эскизе",
             kind="number", default=5.0, minimum=0.001, maximum=1e5,
             decimals=3, suffix=" мм"),
        Tool("value.sides", "Сторон", tip="Число сторон многоугольника",
             kind="integer", default=6, minimum=3, maximum=64),
    ]),
    Group("Связи", [
        Tool("rel.coincident", "Совпадение", "coincident", "Совместить точки"),
        Tool("rel.horizontal", "Горизонталь", "horizontal"),
        Tool("rel.vertical", "Вертикаль", "vertical"),
        Tool("rel.parallel", "Параллельность", "parallel"),
        Tool("rel.perpendicular", "Перпендикулярность", "perpendicular"),
        Tool("rel.tangent", "Касание", "tangent"),
        Tool("rel.equal", "Равенство", "equal"),
        Tool("rel.concentric", "Концентричность", "concentric"),
        Tool("rel.midpoint", "Середина", "midpoint"),
        Tool("rel.symmetric", "Симметрия", "symmetric"),
        Tool("rel.point_on", "Точка на объекте", "point_on"),
        Tool("rel.anchor", "Закрепить", "anchor"),
    ]),
    Group("Показ", [
        Tool("edit.construction", "Вспомога-\nтельная", "construction",
             "Перевести выбранное во вспомогательную геометрию", "G", big=True),
        Tool("view.relations", "Показывать связи", "relations",
             "Значки связей у объектов", checkable=True, exclusive=False),
        Tool("view.grid", "Сетка", "grid", "Сетка и привязка к ней",
             checkable=True, exclusive=False),
        Tool("view.regions", "Области", "region",
             "Закрашивать области эскиза — то, что станет профилем",
             checkable=True, exclusive=False),
    ]),
])

TABS = [PART_TAB, SKETCH_TAB]
