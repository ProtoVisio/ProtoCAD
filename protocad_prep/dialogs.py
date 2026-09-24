"""Диалоги параметров шагов подготовки.

Каждый диалог — форма с разумными значениями по умолчанию. Ноль там, где
он означает «подобрать самому», подписан словом «авто»: человек должен
видеть, что поле не забыто, а оставлено на выбор программы.
"""

from __future__ import annotations

from PySide6 import QtWidgets


def _number(value: float = 0.0, low: float = 0.0, high: float = 1e6,
            decimals: int = 3, suffix: str = " мм", auto: bool = False):
    box = QtWidgets.QDoubleSpinBox()
    box.setRange(low, high)
    box.setDecimals(decimals)
    box.setValue(value)
    box.setSuffix(suffix)
    if auto:
        box.setSpecialValueText("авто")
    return box


class _Form(QtWidgets.QDialog):
    def __init__(self, title: str, note: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.form = QtWidgets.QFormLayout()
        layout = QtWidgets.QVBoxLayout(self)
        if note:
            label = QtWidgets.QLabel(note)
            label.setWordWrap(True)
            label.setStyleSheet("color: palette(mid);")
            layout.addWidget(label)
        layout.addLayout(self.form)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class HealDialog(_Form):
    def __init__(self, parent=None):
        super().__init__("Исправить геометрию",
                         "Сшить поверхности в тела, починить контуры и слить "
                         "грани, разрезанные при выгрузке. Границы групп "
                         "сохраняются.", parent)
        self.tolerance = _number(0.0, 0.0, 100.0, 4, auto=True)
        self.sew = QtWidgets.QCheckBox("сшивать поверхности")
        self.fix = QtWidgets.QCheckBox("чинить контуры и ориентацию")
        self.unify = QtWidgets.QCheckBox("сливать грани одной поверхности")
        for box in (self.sew, self.fix, self.unify):
            box.setChecked(True)
        self.form.addRow("Допуск сшивки", self.tolerance)
        self.form.addRow(self.sew)
        self.form.addRow(self.fix)
        self.form.addRow(self.unify)

    def values(self) -> dict:
        return {"tolerance": self.tolerance.value(), "sew": self.sew.isChecked(),
                "fix": self.fix.isChecked(), "unify": self.unify.isChecked()}


class DefeatureDialog(_Form):
    def __init__(self, selected: int = 0, parent=None):
        super().__init__("Упростить модель",
                         "Убрать то, что не влияет на результат, но стоит "
                         "тысяч элементов. Ноль — этот вид не трогать.", parent)
        self.holes = _number(0.0, 0.0, 1e4, 3)
        self.fillets = _number(0.0, 0.0, 1e4, 3)
        self.small = _number(0.0, 0.0, 1e6, 3, " мм²")
        self.selected = QtWidgets.QCheckBox(f"и выбранные грани ({selected})")
        self.selected.setEnabled(selected > 0)
        self.form.addRow("Отверстия до Ø", self.holes)
        self.form.addRow("Скругления до R", self.fillets)
        self.form.addRow("Грани площадью до", self.small)
        self.form.addRow(self.selected)

    def values(self) -> dict:
        return {"holes": self.holes.value(), "fillets": self.fillets.value(),
                "small_faces": self.small.value(),
                "use_selected": self.selected.isChecked()}


class GroupDialog(_Form):
    def __init__(self, title: str, what: str, existing, parent=None):
        super().__init__(title, what, parent)
        self.name = QtWidgets.QComboBox()
        self.name.setEditable(True)
        self.name.addItems(list(existing))
        self.name.setCurrentText("")
        self.add = QtWidgets.QCheckBox("добавить к группе, если она уже есть")
        self.add.setChecked(True)
        self.form.addRow("Имя", self.name)
        self.form.addRow(self.add)

    def values(self) -> dict:
        return {"name": self.name.currentText().strip(), "add": self.add.isChecked()}
