"""Диалоги параметров шагов подготовки.

Каждый диалог — форма с разумными значениями по умолчанию. Ноль там, где
он означает «подобрать самому», подписан словом «авто»: человек должен
видеть, что поле не забыто, а оставлено на выбор программы.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets


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


class PlaneDialog(_Form):
    """Плоскость: точка и нормаль. Если выбрана плоская грань — её плоскость."""

    def __init__(self, title: str, note: str, origin=(0.0, 0.0, 0.0),
                 normal=(1.0, 0.0, 0.0), keep: bool = True, group: str = "",
                 parent=None):
        super().__init__(title, note, parent)
        self.origin = [_number(value, -1e7, 1e7, 4) for value in origin]
        self.normal = [_number(value, -1.0, 1.0, 6, "") for value in normal]
        row = QtWidgets.QHBoxLayout()
        for box in self.origin:
            row.addWidget(box)
        self.form.addRow("Точка", row)
        row = QtWidgets.QHBoxLayout()
        for box in self.normal:
            row.addWidget(box)
        self.form.addRow("Нормаль", row)
        axes = QtWidgets.QHBoxLayout()
        for label, vector in (("X", (1, 0, 0)), ("Y", (0, 1, 0)), ("Z", (0, 0, 1))):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(lambda _=False, v=vector: self._set_normal(v))
            axes.addWidget(button)
        self.form.addRow("По оси", axes)
        self.keep = None
        if keep:
            self.keep = QtWidgets.QComboBox()
            self.keep.addItem("куда смотрит нормаль", "positive")
            self.keep.addItem("с обратной стороны", "negative")
            self.form.addRow("Оставить часть", self.keep)
        self.group = QtWidgets.QLineEdit(group)
        self.form.addRow("Группа граней разреза", self.group)

    def _set_normal(self, vector) -> None:
        for box, value in zip(self.normal, vector):
            box.setValue(float(value))

    def values(self) -> dict:
        data = {"origin": [box.value() for box in self.origin],
                "normal": [box.value() for box in self.normal],
                "group": self.group.text().strip()}
        if self.keep is not None:
            data["keep"] = self.keep.currentData()
        return data


class EnclosureDialog(_Form):
    def __init__(self, size: float, parent=None):
        super().__init__("Область течения",
                         "Коробка вокруг модели минус тела. Стенки сразу "
                         "раскладываются по группам inlet, outlet, farfield, "
                         "wall.", parent)
        self.padding = [_number(size, 0.0, 1e7, 3) for _ in range(6)]
        self.padding[1].setValue(size * 3.0)
        grid = QtWidgets.QGridLayout()
        for column, label in enumerate(("−", "+")):
            grid.addWidget(QtWidgets.QLabel(label), 0, column + 1)
        for row, axis in enumerate("XYZ"):
            grid.addWidget(QtWidgets.QLabel(axis), row + 1, 0)
            grid.addWidget(self.padding[row * 2], row + 1, 1)
            grid.addWidget(self.padding[row * 2 + 1], row + 1, 2)
        self.form.addRow("Запас от модели", grid)
        self.flow = QtWidgets.QComboBox()
        for label, vector in (("вдоль +X", (1, 0, 0)), ("вдоль −X", (-1, 0, 0)),
                              ("вдоль +Y", (0, 1, 0)), ("вдоль −Y", (0, -1, 0)),
                              ("вдоль +Z", (0, 0, 1)), ("вдоль −Z", (0, 0, -1)),
                              ("нет выделенного потока", None)):
            self.flow.addItem(label, vector)
        self.form.addRow("Поток", self.flow)
        self.keep = QtWidgets.QCheckBox("оставить тела (сопряжённый теплообмен)")
        self.form.addRow(self.keep)

    def values(self) -> dict:
        flow = self.flow.currentData()
        return {"padding": [box.value() for box in self.padding],
                "flow": list(flow) if flow is not None else None,
                "keep_solids": self.keep.isChecked()}


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


FORMATS = ("CalculiX / Abaqus (*.inp);;gmsh (*.msh);;Code_Aster, Salome (*.med);;"
           "Universal (*.unv);;CGNS (*.cgns);;SU2 (*.su2);;Nastran (*.bdf);;"
           "ParaView (*.vtk);;STL (*.stl)")


class MeshDialog(_Form):
    def __init__(self, auto_size: float, groups, sizes: dict, glued: bool,
                 bodies: int, folder: str, name: str, parent=None):
        note = "Сетку строит gmsh в отдельном процессе."
        if bodies > 1 and not glued:
            note += (" Тела НЕ склеены: сетки на стыках не совпадут — если "
                     "детали работают вместе, сначала «Склеить».")
        super().__init__("Построить сетку", note, parent)
        self.size = _number(0.0, 0.0, 1e6, 3, auto=True)
        self.size.setToolTip(f"авто — {auto_size:.4g} мм (1/20 диагонали)")
        self.order = QtWidgets.QComboBox()
        self.order.addItem("квадратичные (для прочности)", 2)
        self.order.addItem("линейные", 1)
        self.algorithm = QtWidgets.QComboBox()
        for label, key in (("Делоне (надёжный)", "delaunay"),
                           ("HXT (быстрый)", "hxt"), ("фронтальный", "frontal")):
            self.algorithm.addItem(label, key)
        self.curvature = QtWidgets.QSpinBox()
        self.curvature.setRange(0, 100)
        self.curvature.setSpecialValueText("нет")
        self.curvature.setToolTip("элементов на полную окружность кривизны")
        self.form.addRow("Размер элемента", self.size)
        self.form.addRow("Элементы", self.order)
        self.form.addRow("Алгоритм", self.algorithm)
        self.form.addRow("Сгущение по кривизне", self.curvature)
        self.local = {}
        for name_ in groups:
            box = _number(float(sizes.get(name_, 0.0)), 0.0, 1e6, 3, auto=True)
            box.setSpecialValueText("как везде")
            self.local[name_] = box
            self.form.addRow(f"  у «{name_}»", box)
        self.scale = QtWidgets.QComboBox()
        self.scale.addItem("миллиметры (как модель)", 1.0)
        self.scale.addItem("метры (OpenFOAM, SU2)", 0.001)
        self.form.addRow("Единицы файла", self.scale)
        row = QtWidgets.QHBoxLayout()
        self.path = QtWidgets.QLineEdit(f"{folder}/{name}.inp")
        browse = QtWidgets.QPushButton("…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.path)
        row.addWidget(browse)
        self.form.addRow("Файл", row)
        self.extra = QtWidgets.QLineEdit()
        self.extra.setPlaceholderText("ещё форматы через пробел: msh med vtk")
        self.form.addRow("Заодно", self.extra)

    def _browse(self) -> None:
        name, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Файл сетки", self.path.text(), FORMATS)
        if name:
            self.path.setText(name)

    def values(self) -> dict:
        from pathlib import Path

        main = Path(self.path.text().strip())
        outputs = [str(main)]
        for extension in self.extra.text().replace(",", " ").split():
            outputs.append(str(main.with_suffix("." + extension.strip(".").lower())))
        return {"size": self.size.value(), "order": self.order.currentData(),
                "algorithm": self.algorithm.currentData(),
                "curvature": self.curvature.value(),
                "local": {name: box.value() for name, box in self.local.items()
                          if box.value() > 0.0},
                "scale": self.scale.currentData(), "outputs": outputs}


def ask_number(parent, title: str, label: str, value: float) -> float | None:
    number, ok = QtWidgets.QInputDialog.getDouble(parent, title, label, value,
                                                  0.0, 1e6, 3)
    return number if ok else None
