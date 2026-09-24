"""Диалоги окна подготовки прибора: разбор, расчётные случаи, упрощение."""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from protocad.device import (COMPONENT, EXCLUDED, FASTENER, HOUSING, OTHER, ROLE_TITLES,
                             SUBSTRATE, classify)
from protocad.device import designators
from protocad.device.designators import RangeError

#: Роли, которые можно назначить детали вне платы.
FREE_ROLES = (HOUSING, FASTENER, OTHER, EXCLUDED)


def _note(text: str) -> QtWidgets.QLabel:
    label = QtWidgets.QLabel(text)
    label.setWordWrap(True)
    label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
    return label


# --- разбор ---------------------------------------------------------------------------


class ClassifyDialog(QtWidgets.QDialog):
    """«Что здесь что»: платы и роли деталей вне плат — до начала работы.

    Разбор делает программа, но проверяет человек: модели приходят из
    разных систем, и ошибка разбора всплывёт только в результатах расчёта.
    """

    def __init__(self, device, parent=None):
        super().__init__(parent)
        self.device = device
        self.forced: set = set()
        self.ignored: set = set()
        self.setWindowTitle("Разбор прибора")
        self.resize(900, 720)
        layout = QtWidgets.QVBoxLayout(self)
        self.summary = _note("")
        layout.addWidget(self.summary)

        boards = QtWidgets.QGroupBox("Платы с компонентами")
        board_layout = QtWidgets.QVBoxLayout(boards)
        self.boards = QtWidgets.QTreeWidget()
        self.boards.setHeaderLabels(["Плата", "Основание", "Компонентов", "Обозначения"])
        self.boards.setRootIsDecorated(False)
        self.boards.itemChanged.connect(self._board_toggled)
        board_layout.addWidget(self.boards)
        pick = QtWidgets.QHBoxLayout()
        self.nodes = QtWidgets.QComboBox()
        pick.addWidget(QtWidgets.QLabel("Узел:"))
        pick.addWidget(self.nodes, 1)
        make = QtWidgets.QPushButton("Сделать платой")
        make.clicked.connect(self._force_board)
        pick.addWidget(make)
        board_layout.addLayout(pick)
        layout.addWidget(boards, 2)

        others = QtWidgets.QGroupBox("Детали вне плат — роль можно поменять")
        other_layout = QtWidgets.QVBoxLayout(others)
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Деталь", "Роль", "Почему так"])
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        other_layout.addWidget(self.table)
        layout.addWidget(others, 3)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Принять")
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._fill()

    def _fill(self) -> None:
        device = self.device
        counts = {role: len(device.of_role(role)) for role in ROLE_TITLES}
        lines = [f"«{device.name}»: деталей {len(device.units)} — "
                 + ", ".join(f"{ROLE_TITLES[role].lower()}: {count}"
                             for role, count in counts.items() if count)]
        lines += list(getattr(device, "notes", []) or [])
        self.summary.setText("\n".join(lines))
        self.boards.blockSignals(True)
        self.boards.clear()
        self.conventions = {}
        for board in device.boards.values():
            substrate = next((unit for unit in device.units.values()
                              if unit.role == SUBSTRATE and unit.board == board.key), None)
            item = QtWidgets.QTreeWidgetItem(
                self.boards, [board.name, substrate.label if substrate else "—",
                              str(len(device.on_board(board.key))), ""])
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(0, QtCore.Qt.Checked)
            item.setData(0, QtCore.Qt.UserRole, board.key)
            combo = QtWidgets.QComboBox()
            for key, title in designators.CONVENTIONS.items():
                combo.addItem(title, key)
            combo.setCurrentIndex(max(0, combo.findData(board.convention)))
            self.boards.setItemWidget(item, 3, combo)
            self.conventions[board.key] = combo
        for key in sorted(self.ignored):
            item = QtWidgets.QTreeWidgetItem(self.boards, [device.node_label(key),
                                                           "отклонена", "", ""])
            item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
            item.setCheckState(0, QtCore.Qt.Unchecked)
            item.setData(0, QtCore.Qt.UserRole, key)
        self.boards.resizeColumnToContents(0)
        self.boards.blockSignals(False)
        self.nodes.clear()
        for key, label, depth in device.assembly_nodes():
            if key in device.boards:
                continue
            self.nodes.addItem("    " * depth + label, key)

        units = [unit for unit in device.units.values()
                 if unit.role not in (SUBSTRATE, COMPONENT)]
        self.table.setRowCount(len(units))
        self.rows = []
        for row, unit in enumerate(units):
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(unit.name))
            combo = QtWidgets.QComboBox()
            for role in FREE_ROLES:
                combo.addItem(ROLE_TITLES[role], role)
            combo.setCurrentIndex(max(0, combo.findData(unit.role)))
            self.table.setCellWidget(row, 1, combo)
            kind = f" ({unit.kind})" if unit.kind else ""
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(unit.reason + kind))
            self.rows.append((unit, combo))
        self.table.resizeColumnToContents(0)
        self.table.setColumnWidth(1, 190)

    def _board_toggled(self, item, _column) -> None:
        key = item.data(0, QtCore.Qt.UserRole)
        if item.checkState(0) == QtCore.Qt.Checked:
            self.ignored.discard(key)
        else:
            self.ignored.add(key)
            self.forced.discard(key)
        self._reclassify()

    def _force_board(self) -> None:
        key = self.nodes.currentData()
        if key is None:
            return
        self.forced.add(key)
        self.ignored.discard(key)
        self._reclassify()

    def _reclassify(self) -> None:
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self.device.notes = classify(self.device, self.forced, self.ignored)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        QtCore.QTimer.singleShot(0, self._fill)

    def _accept(self) -> None:
        for unit, combo in self.rows:
            role = combo.currentData()
            if role != unit.role:
                self.device.set_role([unit], role)
        for key, combo in self.conventions.items():
            if key in self.device.boards:
                self.device.boards[key].convention = combo.currentData()
        self.accept()


# --- расчётные случаи ------------------------------------------------------------------


class CasesDialog(QtWidgets.QDialog):
    """Расчётные случаи: полукомплекты по нумерации и отнесение по диапазонам."""

    def __init__(self, device, parent=None, changed=None):
        super().__init__(parent)
        self.device = device
        self.changed = changed or (lambda: None)
        self.setWindowTitle("Расчётные случаи")
        self.resize(640, 560)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(_note(
            "Случай — какие компоненты работают в данном расчёте. Полукомплекты "
            "одной платы нумеруются подряд (R1–R40 и R41–R80): их граница "
            "находится по составу половин, общее — в обоих случаях."))
        row = QtWidgets.QHBoxLayout()
        self.cases = QtWidgets.QListWidget()
        row.addWidget(self.cases, 1)
        buttons = QtWidgets.QVBoxLayout()
        for title, handler in (("Добавить…", self._add), ("Переименовать…", self._rename),
                               ("Удалить", self._remove)):
            button = QtWidgets.QPushButton(title)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        buttons.addStretch(1)
        row.addLayout(buttons)
        layout.addLayout(row, 1)

        board_row = QtWidgets.QHBoxLayout()
        board_row.addWidget(QtWidgets.QLabel("Плата:"))
        self.board = QtWidgets.QComboBox()
        for board in device.boards.values():
            self.board.addItem(board.name, board.key)
        board_row.addWidget(self.board, 1)
        halves = QtWidgets.QPushButton("Разделить на полукомплекты")
        halves.clicked.connect(self._halves)
        board_row.addWidget(halves)
        layout.addLayout(board_row)

        form = QtWidgets.QFormLayout()
        self.case = QtWidgets.QComboBox()
        self.ranges = QtWidgets.QLineEdit()
        self.ranges.setPlaceholderText("R1–R20, C1…C15, DD1, VD3–VD4")
        form.addRow("Случай", self.case)
        form.addRow("Обозначения", self.ranges)
        layout.addLayout(form)
        act = QtWidgets.QHBoxLayout()
        for title, on in (("Отнести к случаю", True), ("Убрать из случая", False)):
            button = QtWidgets.QPushButton(title)
            button.clicked.connect(lambda _checked=False, on=on: self._by_ranges(on))
            act.addWidget(button)
        layout.addLayout(act)
        self.result = _note("")
        layout.addWidget(self.result)
        close = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        close.rejected.connect(self.accept)
        layout.addWidget(close)
        self._fill()

    def _fill(self) -> None:
        self.cases.clear()
        self.case.clear()
        for case in self.device.cases:
            count = sum(1 for unit in self.device.units.values() if case.name in unit.cases)
            self.cases.addItem(f"{case.name} — компонентов {count}")
            self.case.addItem(case.name)

    def _current(self):
        row = self.cases.currentRow()
        return self.device.cases[row].name if 0 <= row < len(self.device.cases) else None

    def _add(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(
            self, "Новый случай", "Имя случая:",
            text=f"Случай {len(self.device.cases) + 1}")
        if ok:
            self._apply(lambda: self.device.add_case(name))

    def _rename(self) -> None:
        old = self._current()
        if old is None:
            return
        name, ok = QtWidgets.QInputDialog.getText(self, "Переименовать", "Имя:", text=old)
        if ok and name != old:
            self._apply(lambda: self.device.rename_case(old, name))

    def _remove(self) -> None:
        name = self._current()
        if name is not None:
            self._apply(lambda: self.device.remove_case(name))

    def _halves(self) -> None:
        board = self.board.currentData()
        if board is None:
            self.result.setText("На приборе нет плат — делить нечего.")
            return
        notes = []
        self._apply(lambda: notes.extend(self.device.split_halves(board)))
        self.result.setText("Разделено по нумерации.\n" + "\n".join(notes))

    def _by_ranges(self, on: bool) -> None:
        case = self.case.currentText()
        if not case:
            self.result.setText("Сначала добавьте случай.")
            return
        try:
            found, missing = self.device.by_ranges(self.ranges.text(),
                                                   self.board.currentData() or "")
        except RangeError as failure:
            self.result.setText(f"Диапазон не разобран: {failure}")
            return
        touched = []
        self._apply(lambda: touched.append(self.device.assign(found, case, on)))
        text = (f"{'Отнесено к' if on else 'Убрано из'} «{case}»: {touched[0]} из "
                f"{len(found)} найденных.")
        if missing:
            text += " На плате нет: " + ", ".join(missing[:30]) + (
                " …" if len(missing) > 30 else "")
        self.result.setText(text)

    def _apply(self, action) -> None:
        try:
            self.changed(before=True)
            action()
        except ValueError as failure:
            QtWidgets.QMessageBox.warning(self, "Расчётные случаи", str(failure))
        self.changed()
        self._fill()


# --- упрощение -------------------------------------------------------------------------


class SimplifyDialog(QtWidgets.QDialog):
    """Что упростить. Каждый пункт — отдельный шаг, отменяемый целиком."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Упростить для расчёта")
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(_note(
            "Правится определение детали — для всех её вхождений сразу. "
            "Всё отменяется через «Отменить»."))
        form = QtWidgets.QFormLayout()
        self.holes = QtWidgets.QCheckBox("Снять отверстия до Ø")
        self.holes.setChecked(True)
        self.diameter = QtWidgets.QDoubleSpinBox()
        self.diameter.setRange(0.1, 100.0)
        self.diameter.setValue(1.2)
        self.diameter.setSuffix(" мм")
        form.addRow(self.holes, self.diameter)
        self.on_board = QtWidgets.QCheckBox("у оснований плат")
        self.on_board.setChecked(True)
        self.on_housing = QtWidgets.QCheckBox("у корпусных деталей")
        self.on_other = QtWidgets.QCheckBox("у прочих деталей")
        for box in (self.on_board, self.on_housing, self.on_other):
            form.addRow("", box)
        self.boxes = QtWidgets.QCheckBox("Компоненты заменить брусками по габариту")
        form.addRow(self.boxes)
        self.small = QtWidgets.QCheckBox("Исключить компоненты меньше")
        self.small_size = QtWidgets.QDoubleSpinBox()
        self.small_size.setRange(0.1, 50.0)
        self.small_size.setValue(1.2)
        self.small_size.setSuffix(" мм")
        form.addRow(self.small, self.small_size)
        self.fasteners = QtWidgets.QCheckBox("Исключить крепёж")
        form.addRow(self.fasteners)
        layout.addLayout(form)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Упростить")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def values(self) -> dict:
        roles = [role for role, box in ((SUBSTRATE, self.on_board), (HOUSING, self.on_housing),
                                        (OTHER, self.on_other)) if box.isChecked()]
        return {"holes": self.diameter.value() if self.holes.isChecked() and roles else 0.0,
                "roles": roles, "boxes": self.boxes.isChecked(),
                "small": self.small_size.value() if self.small.isChecked() else 0.0,
                "fasteners": self.fasteners.isChecked()}
