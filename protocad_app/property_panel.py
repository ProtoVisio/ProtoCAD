"""Панель свойств: аргументы незавершённой операции.

Панель ничего не решает. Она показывает состояние сеанса команды и
передаёт ему события — вся логика в `command_session`. Разделение не ради
чистоты: логику надо проверять без окна, а окно без логики проверять
нечем.

Устройство повторяет контракт из спецификации: кнопки подтверждения
сверху, под ними строка диагностики, дальше группы. Активное поле выбора
выделено — следующий щелчок в трёхмерном виде попадёт именно в него.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

import icons

ACTIVE_STYLE = "border: 2px solid #2f7fd0; background: palette(base);"
IDLE_STYLE = "border: 1px solid palette(mid); background: palette(base);"


class _SelectionView(QtWidgets.QWidget):
    """Одно поле выбора: подпись, список набранного, кнопка очистки."""

    activated = QtCore.Signal(str)
    cleared = QtCore.Signal(str)

    def __init__(self, box):
        super().__init__()
        self.box_id = box.id
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(2)

        header = QtWidgets.QHBoxLayout()
        self.caption = QtWidgets.QLabel(box.label)
        font = self.caption.font()
        font.setBold(True)
        self.caption.setFont(font)
        header.addWidget(self.caption)
        header.addStretch(1)
        self.clear = QtWidgets.QToolButton()
        self.clear.setText("×")
        self.clear.setToolTip("Очистить поле")
        self.clear.clicked.connect(lambda: self.cleared.emit(self.box_id))
        header.addWidget(self.clear)
        layout.addLayout(header)

        self.items = QtWidgets.QListWidget()
        self.items.setMaximumHeight(76)
        self.items.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        # Щелчок по списку делает поле активным: так его и выбирают, когда
        # надо вернуться к уже заполненному аргументу.
        self.items.viewport().installEventFilter(self)
        layout.addWidget(self.items)

    def eventFilter(self, watched, event):
        if event.type() == QtCore.QEvent.MouseButtonPress:
            self.activated.emit(self.box_id)
        return super().eventFilter(watched, event)

    def refresh(self, box, active: bool) -> None:
        self.items.clear()
        for pick in box.items:
            title = pick.label or f"{pick.kind} {pick.id}"
            entry = QtWidgets.QListWidgetItem(title)
            entry.setIcon(icons.icon(_GLYPHS.get(pick.kind, "select"), 16))
            self.items.addItem(entry)
        if not box.items:
            hint = QtWidgets.QListWidgetItem(
                "укажите в виде: " + ", ".join(_TITLES.get(k, k) for k in box.accepts)
            )
            hint.setForeground(QtGui.QBrush(QtGui.QColor("#8a94a6")))
            self.items.addItem(hint)
        self.items.setStyleSheet(ACTIVE_STYLE if active else IDLE_STYLE)


_GLYPHS = {
    "sketch": "sketch", "face": "rectangle", "edge": "line",
    "vertex": "point", "plane": "rectangle", "body": "pad", "feature": "measure",
}

_TITLES = {
    "sketch": "эскиз", "face": "грань", "edge": "ребро", "vertex": "точку",
    "plane": "плоскость", "body": "тело", "feature": "операцию",
}


class PropertyPanel(QtWidgets.QWidget):
    """Панель незавершённой операции."""

    committed = QtCore.Signal()
    cancelled = QtCore.Signal()
    changed = QtCore.Signal()
    box_activated = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.session = None
        self._views: dict[str, _SelectionView] = {}
        self._editors: dict[str, QtWidgets.QWidget] = {}
        #: (описание группы, её рамка) — чтобы прятать группу целиком.
        self._groups: list = []

        self.setMinimumWidth(300)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        self.title = QtWidgets.QLabel("")
        font = self.title.font()
        font.setBold(True)
        font.setPointSizeF(font.pointSizeF() + 1.0)
        self.title.setFont(font)
        outer.addWidget(self.title)

        # Подтверждение сверху — по спецификации. Кнопки внизу длинной
        # панели уезжают под прокрутку ровно тогда, когда нужны.
        buttons = QtWidgets.QHBoxLayout()
        self.ok = QtWidgets.QPushButton("Готово")
        self.ok.setShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Return))
        self.ok.clicked.connect(self._accept)
        self.cancel = QtWidgets.QPushButton("Отмена")
        self.cancel.setShortcut(QtGui.QKeySequence(QtCore.Qt.Key_Escape))
        self.cancel.clicked.connect(self.cancelled.emit)
        buttons.addWidget(self.ok)
        buttons.addWidget(self.cancel)
        outer.addLayout(buttons)

        self.message = QtWidgets.QLabel("")
        self.message.setWordWrap(True)
        outer.addWidget(self.message)

        self.body = QtWidgets.QWidget()
        self.body_layout = QtWidgets.QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(6)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        # Горизонтальной прокрутки у панели быть не должно: поле, уехавшее
        # вправо, не читается и не правится, а прокручивать панель вбок
        # никто не догадается.
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        scroll.setWidget(self.body)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        outer.addWidget(scroll, 1)

    # --- построение по описанию ---

    def show_session(self, session) -> None:
        self.session = session
        self.title.setText(session.descriptor.title)
        self._clear_body()
        for group in session.descriptor.groups:
            self.body_layout.addWidget(self._build_group(group))
        self.body_layout.addStretch(1)
        self.refresh()

    def _clear_body(self) -> None:
        self._views.clear()
        self._editors.clear()
        self._groups.clear()
        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _build_group(self, group) -> QtWidgets.QWidget:
        holder = QtWidgets.QGroupBox(group.title)
        # Группа и её поля запоминаются, чтобы прятать их целиком: у
        # спецификации видимость задана условием, а не только флажком.
        self._groups.append((group, holder))
        layout = QtWidgets.QVBoxLayout(holder)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.setSpacing(4)

        if group.toggle_key:
            switch = QtWidgets.QCheckBox("включить")
            switch.setChecked(bool(self.session.value(group.toggle_key)))
            switch.toggled.connect(
                lambda on, key=group.toggle_key: self._set(key, on)
            )
            self._editors[group.toggle_key] = switch
            layout.addWidget(switch)

        for box in group.boxes:
            view = _SelectionView(box)
            view.activated.connect(self.box_activated.emit)
            view.cleared.connect(self._clear_box)
            self._views[box.id] = view
            layout.addWidget(view)

        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 2, 0, 0)
        form.setHorizontalSpacing(8)
        form.setVerticalSpacing(4)
        # Подпись и поле — каждое своей строкой по ширине панели. В одну
        # строку они помещались только за счёт того, что поле сжималось до
        # нечитаемого, и панель уезжала в горизонтальную прокрутку.
        form.setRowWrapPolicy(QtWidgets.QFormLayout.WrapLongRows)
        form.setFieldGrowthPolicy(QtWidgets.QFormLayout.ExpandingFieldsGrow)
        for parameter in group.parameters:
            editor = self._build_editor(parameter)
            self._editors[parameter.key] = editor
            form.addRow(parameter.label, editor)
        layout.addLayout(form)
        return holder

    def _build_editor(self, parameter) -> QtWidgets.QWidget:
        key = parameter.key
        if parameter.kind == "icons":
            # Выбор ЗНАЧКОМ, а не строкой списка. Тип отверстия — это
            # форма, и показывать её словами значит заставлять человека
            # переводить «Цековка и зенковка» в картинку у себя в голове.
            # Значки свои: копировать чужие нельзя (docs/00_CONTEXT.md).
            return self._build_icons(parameter)
        if parameter.kind == "choice":
            widget = QtWidgets.QComboBox()
            # Варианты бывают ВЫЧИСЛЯЕМЫМИ: список тел детали заранее не
            # известен. Записанный заранее, он был бы списком тел какой-то
            # другой детали — то есть ложью.
            choices = (parameter.choices(self.session)
                       if callable(parameter.choices) else parameter.choices)
            widget.addItems([str(choice) for choice in choices])
            widget.setCurrentText(str(self.session.value(key)))
            widget.currentTextChanged.connect(lambda value: self._set(key, value))
            return widget
        if parameter.kind == "flag":
            widget = QtWidgets.QCheckBox()
            widget.setChecked(bool(self.session.value(key)))
            widget.toggled.connect(lambda value: self._set(key, value))
            return widget
        if parameter.kind == "integer":
            widget = QtWidgets.QSpinBox()
            widget.setRange(int(parameter.minimum), int(parameter.maximum))
            widget.setValue(int(self.session.value(key)))
        elif parameter.kind == "text":
            widget = QtWidgets.QLineEdit(str(self.session.value(key)))
            widget.textChanged.connect(lambda value: self._set(key, value))
            return widget
        else:
            widget = QtWidgets.QDoubleSpinBox()
            widget.setRange(parameter.minimum, parameter.maximum)
            widget.setDecimals(parameter.decimals)
            widget.setValue(float(self.session.value(key)))
        widget.setSuffix(parameter.suffix)
        widget.setKeyboardTracking(False)
        widget.valueChanged.connect(lambda value: self._set(key, value))
        return widget

    def _build_icons(self, parameter) -> QtWidgets.QWidget:
        """Ряд значков: один выбран, остальные подняты."""
        holder = QtWidgets.QWidget()
        grid = QtWidgets.QGridLayout(holder)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        group = QtWidgets.QButtonGroup(holder)
        group.setExclusive(True)
        holder.buttons = {}
        chosen = str(self.session.value(parameter.key))
        choices = (parameter.choices(self.session)
                   if callable(parameter.choices) else parameter.choices)
        for index, item in enumerate(choices):
            label, glyph = item if isinstance(item, tuple) else (item, "select")
            button = QtWidgets.QToolButton()
            button.setCheckable(True)
            button.setAutoRaise(True)
            button.setIcon(icons.icon(glyph, 34))
            button.setIconSize(QtCore.QSize(34, 34))
            button.setToolTip(label)
            button.setChecked(label == chosen)
            button.clicked.connect(
                lambda _=False, key=parameter.key, value=label:
                self._set(key, value))
            group.addButton(button)
            grid.addWidget(button, index // 4, index % 4)
            holder.buttons[label] = button
        return holder

    # --- состояние ---

    def flush(self) -> None:
        """Применить то, что НАБРАНО в полях, но ещё не подтверждено.

        Числовое поле стоит без слежения за клавиатурой: значение берётся
        не из каждой цифры, а по окончании ввода. Иначе «12» на пути к
        «120» успевает пересчитать деталь дважды, и предпросмотр дёргается
        на каждый знак.

        Расплата за это — набранное живёт в ТЕКСТЕ поля, пока его не
        применят. Enter до поля не доходит: его перехватывает горячая
        клавиша «Готово». Выходило так, что человек вводил размер, нажимал
        Enter — и операция строилась по старому значению, будто ввода не
        было. Здесь набранное применяется явно, перед самым подтверждением.
        """
        for widget in self._editors.values():
            if isinstance(widget, QtWidgets.QAbstractSpinBox):
                widget.interpretText()

    def _accept(self) -> None:
        self.flush()
        self.committed.emit()

    def _set(self, key: str, value) -> None:
        if self.session is None:
            return
        self.session.set_value(key, value)
        self.refresh()
        self.changed.emit()

    def _clear_box(self, box_id: str) -> None:
        if self.session is None:
            return
        self.session.clear(box_id)
        self.session.activate(box_id)
        self.refresh()
        self.changed.emit()

    def refresh(self) -> None:
        """Перерисовать состояние: списки, доступность, диагностику.

        Поле, которого сейчас нет, ПРЯЧЕТСЯ, а не гасится: погашенная
        строка всё равно занимает место и заставляет гадать, при каких
        условиях она оживёт. Спецификация задаёт видимость условием —
        значит, поле либо есть, либо его нет.
        """
        if self.session is None:
            return
        shown_boxes = {box.id for box in self.session.visible_boxes()}
        for box in self.session.boxes:
            view = self._views.get(box.id)
            if view is None:
                continue
            view.setVisible(box.id in shown_boxes)
            view.refresh(box, box.id == self.session.active_box_id)

        visible = {p.key for p in self.session.visible_parameters()}
        for group in self.session.descriptor.groups:
            for parameter in group.parameters:
                editor = self._editors.get(parameter.key)
                if editor is None:
                    continue
                shown = parameter.key in visible
                editor.setVisible(shown)
                label = self._label_for(editor)
                if label is not None:
                    label.setVisible(shown)

        # Группа, в которой не осталось НИ ОДНОГО видимого поля, прячется
        # целиком. Пустая рамка с заголовком занимает место и обещает
        # настройки, которых там нет: у простого сквозного отверстия так
        # висели «Сторона входа» и «Область действия» — пустые.
        for group, holder in self._groups:
            if not self.session.shown(group):
                holder.setVisible(False)
                continue
            has_box = any(box.id in shown_boxes for box in group.boxes)
            has_field = any(item.key in visible for item in group.parameters)
            holder.setVisible(bool(has_box or has_field or group.toggle_key))

        check = self.session.validate()
        self.ok.setEnabled(not check.blocking)
        note = self.session.diagnostics or (check.message if check.blocking else "")
        self.message.setText(note)
        self.message.setStyleSheet(
            "color: #c0392b;" if note else "color: palette(mid);"
        )

    def _label_for(self, editor: QtWidgets.QWidget):
        parent = editor.parentWidget()
        if parent is None:
            return None
        layout = parent.layout()
        for child in parent.findChildren(QtWidgets.QFormLayout):
            layout = child
            break
        if not isinstance(layout, QtWidgets.QFormLayout):
            return None
        return layout.labelForField(editor)
