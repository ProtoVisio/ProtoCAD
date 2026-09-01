"""Редактор осевого стека отверстия.

По `docs/09_HOLES.md`, §13. В составном режиме вместо готовых секций
показывается сам СТЕК: что идёт со стороны входа, что за канал, что со
стороны выхода. Порядок задаёт человек перетаскиванием.

Обе стороны хранятся «от внешней поверхности внутрь материала» (§4.2), и
список показывает их именно так. Мысленно переворачивать порядок для
выхода не приходится — переворачивает построение.

Редактор правит `HoleDefinition` НА МЕСТЕ. Своей копии описания он не
держит: две копии одного стека разошлись бы на первой же правке, и какая
из них попадёт в деталь, стало бы вопросом порядка вызовов.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from protocad.holes import Counterbore, Countersink, StraightBore

#: Что можно добавить в сторону. Ключ — как называется у модели.
KINDS = (("counterbore", "Цековка"),
         ("countersink", "Зенковка"),
         ("bore", "Цилиндр"))


def title_of(element) -> str:
    """Строка списка: что это и какое оно."""
    if isinstance(element, Counterbore):
        return (f"Цековка  ⌀{element.diameter_mm:g} × {element.depth_mm:g}")
    if isinstance(element, Countersink):
        angle = element.included_angle_deg
        return (f"Зенковка  ⌀{element.diameter_mm:g}"
                + (f"  {angle:g}°" if angle is not None else ""))
    if isinstance(element, StraightBore):
        return f"Цилиндр  ⌀{element.diameter_mm:g} × {element.depth_mm:g}"
    return type(element).__name__


def make(kind: str, core_diameter_mm: float = 5.0):
    """Новый элемент стека. Размеры — от диаметра канала, а не с потолка."""
    if kind == "counterbore":
        return Counterbore(diameter_mm=core_diameter_mm * 1.8,
                           depth_mm=max(1.0, core_diameter_mm * 0.8))
    if kind == "countersink":
        return Countersink(definition_mode="diameter_angle",
                           diameter_mm=core_diameter_mm * 2.0,
                           included_angle_deg=90.0)
    return StraightBore(diameter_mm=core_diameter_mm * 1.4,
                        depth_mm=max(1.0, core_diameter_mm))


class _SideList(QtWidgets.QListWidget):
    """Список одной стороны. Перетаскивание — внутри своего списка."""

    reordered = QtCore.Signal()

    def __init__(self):
        super().__init__()
        self.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.setDefaultDropAction(QtCore.Qt.MoveAction)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.setMaximumHeight(110)

    def dropEvent(self, event) -> None:
        super().dropEvent(event)
        # Порядок в списке И порядок в модели обязаны совпасть СРАЗУ.
        # Отложенная синхронизация означала бы, что показанное и
        # построенное расходятся, пока никто не нажал «Готово».
        self.reordered.emit()


class HoleStackEditor(QtWidgets.QWidget):
    """Стек отверстия: вход, канал, выход."""

    changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.definition = None

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        title = QtWidgets.QLabel("Составное отверстие")
        font = title.font()
        font.setBold(True)
        title.setFont(font)
        outer.addWidget(title)

        self.lists = {}
        self._blocks = {}
        for side, caption in (("near", "Сторона входа"),
                              ("far", "Сторона выхода")):
            box = QtWidgets.QGroupBox(caption)
            column = QtWidgets.QVBoxLayout(box)
            column.setContentsMargins(6, 4, 6, 6)
            column.setSpacing(3)
            widget = _SideList()
            widget.reordered.connect(lambda s=side: self._reorder(s))
            widget.currentRowChanged.connect(lambda _, s=side: self._enable(s))
            column.addWidget(widget)
            self.lists[side] = widget

            row = QtWidgets.QHBoxLayout()
            row.setSpacing(3)
            for kind, name in KINDS:
                button = QtWidgets.QPushButton(f"+ {name}")
                button.clicked.connect(
                    lambda _=False, s=side, k=kind: self._add(s, k))
                row.addWidget(button)
            column.addLayout(row)

            tools = QtWidgets.QHBoxLayout()
            tools.setSpacing(3)
            block = {}
            for key, name, slot in (
                ("up", "▲", self._up), ("down", "▼", self._down),
                ("copy", "Дублировать", self._duplicate),
                ("drop", "Удалить", self._remove),
            ):
                button = QtWidgets.QPushButton(name)
                button.setEnabled(False)
                button.clicked.connect(lambda _=False, s=side, f=slot: f(s))
                tools.addWidget(button)
                block[key] = button
            column.addLayout(tools)
            self._blocks[side] = block
            outer.addWidget(box)

            if side == "near":
                self.core = QtWidgets.QLabel("")
                self.core.setStyleSheet("color: palette(mid);")
                outer.addWidget(self.core)

        mirror = QtWidgets.QHBoxLayout()
        self.to_far = QtWidgets.QPushButton("Копировать вход → выход")
        self.to_far.clicked.connect(self._copy_to_far)
        mirror.addWidget(self.to_far)
        outer.addLayout(mirror)
        outer.addStretch(1)

    # --- показ ---------------------------------------------------------

    def show_definition(self, definition) -> None:
        self.definition = definition
        self.refresh()

    def refresh(self) -> None:
        if self.definition is None:
            for widget in self.lists.values():
                widget.clear()
            return
        for side in ("near", "far"):
            widget = self.lists[side]
            row = widget.currentRow()
            widget.blockSignals(True)
            widget.clear()
            for element in self._stack(side):
                widget.addItem(title_of(element))
            if 0 <= row < widget.count():
                widget.setCurrentRow(row)
            widget.blockSignals(False)
            self._enable(side)
        core = self.definition.core
        end = self.definition.end_condition
        self.core.setText(
            f"Основной канал:  ⌀{self.definition.core_diameter_mm:g}"
            f"  /  {'насквозь' if end.type != 'blind' else f'{end.depth_mm:g} мм'}")

    def _stack(self, side: str) -> list:
        return (self.definition.near_stack if side == "near"
                else self.definition.far_stack)

    def _enable(self, side: str) -> None:
        row = self.lists[side].currentRow()
        stack = self._stack(side) if self.definition is not None else []
        chosen = 0 <= row < len(stack)
        block = self._blocks[side]
        block["up"].setEnabled(chosen and row > 0)
        block["down"].setEnabled(chosen and row < len(stack) - 1)
        block["copy"].setEnabled(chosen)
        block["drop"].setEnabled(chosen)

    # --- правка --------------------------------------------------------

    def _add(self, side: str, kind: str) -> None:
        if self.definition is None:
            return
        self._stack(side).append(
            make(kind, self.definition.core_diameter_mm))
        self._after()

    def _remove(self, side: str) -> None:
        row = self.lists[side].currentRow()
        stack = self._stack(side)
        if 0 <= row < len(stack):
            stack.pop(row)
            self._after()

    def _duplicate(self, side: str) -> None:
        import copy

        row = self.lists[side].currentRow()
        stack = self._stack(side)
        if 0 <= row < len(stack):
            twin = copy.deepcopy(stack[row])
            # У копии СВОЙ номер: одинаковые номера сделали бы два элемента
            # одним для всего, что на них ссылается.
            from protocad.holes import new_id

            twin.id = new_id()
            stack.insert(row + 1, twin)
            self._after()

    def _up(self, side: str) -> None:
        self._shift(side, -1)

    def _down(self, side: str) -> None:
        self._shift(side, 1)

    def _shift(self, side: str, step: int) -> None:
        row = self.lists[side].currentRow()
        stack = self._stack(side)
        target = row + step
        if 0 <= row < len(stack) and 0 <= target < len(stack):
            stack[row], stack[target] = stack[target], stack[row]
            self._after(select=(side, target))

    def _reorder(self, side: str) -> None:
        """Перетащили строку — тот же порядок в модели."""
        widget = self.lists[side]
        stack = self._stack(side)
        names = [widget.item(index).text() for index in range(widget.count())]
        if len(names) != len(stack):
            return
        # Сопоставление по ПОДПИСИ: одинаковых подписей у разных элементов не
        # бывает, пока различаются размеры, а если совпали — элементы и так
        # взаимозаменяемы.
        rest = list(stack)
        ordered = []
        for name in names:
            for element in rest:
                if title_of(element) == name:
                    ordered.append(element)
                    rest.remove(element)
                    break
        if len(ordered) == len(stack):
            stack[:] = ordered
            self._after()

    def _copy_to_far(self) -> None:
        """Копировать вход в выход (§5.6).

        Именно КОПИЯ, а не связь: связанные параметры — отдельное действие,
        и путать их нельзя. Скопированное правится независимо.
        """
        import copy

        if self.definition is None:
            return
        from protocad.holes import new_id

        made = []
        for element in self.definition.near_stack:
            twin = copy.deepcopy(element)
            twin.id = new_id()
            made.append(twin)
        self.definition.far_stack[:] = made
        self._after()

    def _after(self, select=None) -> None:
        self.refresh()
        if select is not None:
            side, row = select
            self.lists[side].setCurrentRow(row)
        self.changed.emit()
