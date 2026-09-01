"""Свойства выбранного объекта эскиза.

Панель отвечает на два вопроса, которые иначе приходится держать в
голове: ЧЕМ объект уже держится и КАКОЙ он сейчас. Первое — список
наложенных связей: пока его нет, «почему линия не двигается» выясняется
перебором. Второе — числа: длина, угол, концы. Их правят прямо здесь, и
это единственный способ задать линию точно, не рисуя её на глаз и не
обвешивая размерами.

Числа НЕ размеры. Правка числа двигает точки и решает эскиз заново — как
если бы линию перетащили мышью, только точно. Определённую линию это не
сдвинет: её держат размеры, и решатель вернёт всё на место. Так и надо —
иначе поле молча отменяло бы поставленный размер.
"""

from __future__ import annotations

import math

from PySide6 import QtCore, QtWidgets

#: Связи, которые накладываются одной кнопкой на один отрезок.
QUICK = (
    ("horizontal", "Горизонтальный"),
    ("vertical", "Вертикальный"),
    ("anchor", "Зафиксированный"),
)


class LineProperties(QtWidgets.QWidget):
    """Свойства отрезка: связи, вспомогательность, числа."""

    #: Что-то изменено — эскиз надо пересчитать и перерисовать.
    changed = QtCore.Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sketch = None
        self.segment = None
        self._filling = False

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(6)

        self.title = QtWidgets.QLabel("Свойства линии")
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        outer.addWidget(self.title)

        existing = QtWidgets.QGroupBox("Существующие взаимосвязи")
        box = QtWidgets.QVBoxLayout(existing)
        box.setContentsMargins(6, 4, 6, 6)
        self.relations = QtWidgets.QListWidget()
        self.relations.setMaximumHeight(96)
        box.addWidget(self.relations)
        self.drop = QtWidgets.QPushButton("Снять выбранную связь")
        self.drop.setEnabled(False)
        self.drop.clicked.connect(self._drop_relation)
        self.relations.itemSelectionChanged.connect(
            lambda: self.drop.setEnabled(bool(self.relations.selectedItems())))
        box.addWidget(self.drop)
        outer.addWidget(existing)

        self.state = QtWidgets.QLabel("")
        self.state.setWordWrap(True)
        outer.addWidget(self.state)

        adding = QtWidgets.QGroupBox("Добавить взаимосвязи")
        row = QtWidgets.QVBoxLayout(adding)
        row.setContentsMargins(6, 4, 6, 6)
        row.setSpacing(3)
        self._quick = {}
        for kind, label in QUICK:
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(lambda _=False, k=kind: self._add(k))
            row.addWidget(button)
            self._quick[kind] = button
        outer.addWidget(adding)

        options = QtWidgets.QGroupBox("Параметры")
        column = QtWidgets.QVBoxLayout(options)
        column.setContentsMargins(6, 4, 6, 6)
        self.construction = QtWidgets.QCheckBox("Вспомогательная геометрия")
        self.construction.toggled.connect(self._set_construction)
        column.addWidget(self.construction)

        form = QtWidgets.QFormLayout()
        form.setContentsMargins(0, 4, 0, 0)
        self._fields = {}
        for key, label, suffix in (
            ("length", "Длина", " мм"),
            ("angle", "Угол", "°"),
            ("x1", "X начала", " мм"),
            ("y1", "Y начала", " мм"),
            ("x2", "X конца", " мм"),
            ("y2", "Y конца", " мм"),
            ("dx", "ΔX", " мм"),
            ("dy", "ΔY", " мм"),
        ):
            field = QtWidgets.QDoubleSpinBox()
            field.setRange(-1.0e6, 1.0e6)
            field.setDecimals(3)
            field.setSuffix(suffix)
            field.setKeyboardTracking(False)
            field.valueChanged.connect(
                lambda value, k=key: self._apply(k, value))
            self._fields[key] = field
            form.addRow(label, field)
        column.addLayout(form)
        outer.addWidget(options)
        outer.addStretch(1)

    # --- показ ---------------------------------------------------------

    def show_segment(self, sketch, segment) -> None:
        """Показать отрезок. ``segment`` ``None`` — панель пустеет."""
        self.sketch = sketch
        self.segment = segment
        self.refresh()

    def refresh(self) -> None:
        if self.sketch is None or self.segment is None:
            self.relations.clear()
            self.state.setText("")
            return
        # Пока панель заполняется, правки полей не применяются: иначе
        # выставленное число тут же уходит обратно в геометрию, и линия
        # ползёт от одного показа панели.
        self._filling = True
        try:
            self._fill()
        finally:
            self._filling = False

    def _fill(self) -> None:
        sketch, segment = self.sketch, self.segment
        self.title.setText(f"Свойства линии — {segment.label}")

        self.relations.clear()
        for relation in sketch.relations_on(segment):
            self.relations.addItem(_title_of(relation))
        # Связи, наложенные на КОНЦЫ, держат отрезок не меньше: совпадение
        # с концом соседнего — самая частая из них. Не показать их значило
        # бы ответить на «чем держится» наполовину.
        for point in segment.ends:
            for relation in sketch.relations_on(point):
                self.relations.addItem(f"{_title_of(relation)} (конец)")
        if self.relations.count() == 0:
            self.relations.addItem("связей нет")

        status = sketch.status()
        if not status.ok:
            self.state.setText("Эскиз противоречив")
        elif status.dof == 0:
            self.state.setText("Определён")
        else:
            self.state.setText(f"Недоопределён — степеней свободы {status.dof}")

        self.construction.setChecked(bool(segment.construction))

        first, second = (sketch.coordinates(end) for end in segment.ends)
        dx, dy = second[0] - first[0], second[1] - first[1]
        values = {
            "length": math.hypot(dx, dy),
            "angle": math.degrees(math.atan2(dy, dx)) % 360.0,
            "x1": first[0], "y1": first[1],
            "x2": second[0], "y2": second[1],
            "dx": dx, "dy": dy,
        }
        for key, value in values.items():
            self._fields[key].setValue(value)

    # --- правка --------------------------------------------------------

    def _apply(self, key: str, value: float) -> None:
        """Число из поля — в геометрию."""
        if self._filling or self.sketch is None or self.segment is None:
            return
        sketch, segment = self.sketch, self.segment
        start, end = segment.ends
        first = sketch.coordinates(start)
        second = sketch.coordinates(end)
        dx, dy = second[0] - first[0], second[1] - first[1]
        length = math.hypot(dx, dy)
        angle = math.atan2(dy, dx)

        if key == "length":
            if length < 1e-9:
                return
            target = (first[0] + math.cos(angle) * value,
                      first[1] + math.sin(angle) * value)
            sketch.move(end, *target)
        elif key == "angle":
            radians = math.radians(value)
            target = (first[0] + math.cos(radians) * length,
                      first[1] + math.sin(radians) * length)
            sketch.move(end, *target)
        elif key == "x1":
            sketch.move(start, value, first[1])
        elif key == "y1":
            sketch.move(start, first[0], value)
        elif key == "x2":
            sketch.move(end, value, second[1])
        elif key == "y2":
            sketch.move(end, second[0], value)
        elif key == "dx":
            sketch.move(end, first[0] + value, second[1])
        elif key == "dy":
            sketch.move(end, second[0], first[1] + value)
        sketch.solve()
        self.refresh()
        self.changed.emit()

    def _add(self, kind: str) -> None:
        if self.sketch is None or self.segment is None:
            return
        try:
            if kind == "horizontal":
                self.sketch.horizontal(self.segment)
            elif kind == "vertical":
                self.sketch.vertical(self.segment)
            else:
                # «Зафиксированный» закрепляет ОБА конца: закреплённый один
                # оставляет отрезок вращаться вокруг него, а кнопка обещает
                # не это.
                for point in self.segment.ends:
                    self.sketch.anchor(point)
        except Exception as failure:  # noqa: BLE001 — связь может спорить
            self.state.setText(f"Связь не наложена: {failure}")
            return
        self.sketch.solve()
        self.refresh()
        self.changed.emit()

    def _drop_relation(self) -> None:
        rows = self.relations.selectedItems()
        if not rows or self.sketch is None or self.segment is None:
            return
        wanted = rows[0].text()
        for owner in (self.segment,) + tuple(self.segment.ends):
            for relation in self.sketch.relations_on(owner):
                title = _title_of(relation)
                if title == wanted or f"{title} (конец)" == wanted:
                    try:
                        self.sketch.delete_constraint(relation)
                    except Exception as failure:  # noqa: BLE001
                        self.state.setText(f"Связь не снята: {failure}")
                        return
                    self.sketch.solve()
                    self.refresh()
                    self.changed.emit()
                    return

    def _set_construction(self, on: bool) -> None:
        if self._filling or self.sketch is None or self.segment is None:
            return
        self.segment.construction = bool(on)
        self.changed.emit()


def _title_of(relation) -> str:
    """Название связи или размера так, как его показывают человеку."""
    name = getattr(relation, "name", "")
    if name:
        return name
    titles = getattr(type(relation), "TITLES", {})
    return titles.get(relation.kind, relation.kind)
