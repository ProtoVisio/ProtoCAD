"""Диалоги окна сборки: перемещение вхождения и спецификация."""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np
from PySide6 import QtWidgets

from protocad_prep.dialogs import _Form, _number


class MoveDialog(_Form):
    """Сдвиг и поворот вхождения — ОТНОСИТЕЛЬНО того места, где оно стоит.

    Поворот — вокруг середины габарита вхождения: так деталь поворачивается
    «на месте», а не улетает по дуге вокруг нуля сборки.
    """

    def __init__(self, label: str, held: bool, parent=None):
        note = f"«{label}»: сдвиг в миллиметрах, поворот в градусах."
        if held:
            note += (" Вхождение держат сопряжения: после пересчёта оно встанет "
                     "туда, куда они велят, и сдвиг останется только по "
                     "свободным направлениям.")
        super().__init__("Переместить", note, parent)
        self.shift = [_number(0.0, -1e6, 1e6, 3) for _axis in "XYZ"]
        self.turn = [_number(0.0, -360.0, 360.0, 2, suffix="°") for _axis in "XYZ"]
        for axis, box in zip("XYZ", self.shift):
            self.form.addRow(f"Сдвиг по {axis}", box)
        for axis, box in zip("XYZ", self.turn):
            self.form.addRow(f"Поворот вокруг {axis}", box)

    def values(self) -> dict:
        return {"shift": [box.value() for box in self.shift],
                "turn": [box.value() for box in self.turn]}


def moved(transform, shift, turn_degrees, centre) -> np.ndarray:
    """Новое положение: поворот вокруг ``centre`` (X, затем Y, затем Z в
    осях сборки), потом сдвиг."""
    rotation = np.eye(3)
    for axis, degrees in enumerate(turn_degrees):
        if not degrees:
            continue
        c, s = np.cos(np.radians(degrees)), np.sin(np.radians(degrees))
        step = (np.array([[1, 0, 0], [0, c, -s], [0, s, c]]),
                np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]]),
                np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]]))[axis]
        rotation = step @ rotation
    change = np.eye(4)
    change[:3, :3] = rotation
    centre = np.asarray(centre, float)
    change[:3, 3] = centre - rotation @ centre + np.asarray(shift, float)
    return change @ np.asarray(transform, float)


class BomDialog(QtWidgets.QDialog):
    """Спецификация сборки — по составу, как её считает модель."""

    COLUMNS = ("Поз.", "Обозначение", "Наименование", "Кол.", "Вхождения", "Раздел")

    def __init__(self, rows: list, title: str, parent=None):
        super().__init__(parent)
        self.rows = rows
        self.setWindowTitle(f"Спецификация — {title}")
        self.resize(820, 420)
        table = QtWidgets.QTableWidget(len(rows), len(self.COLUMNS))
        table.setHorizontalHeaderLabels(self.COLUMNS)
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        for number, row in enumerate(rows):
            for column, text in enumerate(self._cells(number, row)):
                table.setItem(number, column, QtWidgets.QTableWidgetItem(text))
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)
        self.table = table
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Close)
        save = buttons.addButton("Сохранить CSV…", QtWidgets.QDialogButtonBox.ActionRole)
        save.clicked.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(table)
        layout.addWidget(buttons)

    @staticmethod
    def _cells(number: int, row: dict) -> list:
        return [str(number + 1), row["designation"], row["name"],
                str(row["quantity"]), ", ".join(row["references"]), row["kind"]]

    def _save(self) -> None:
        name, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Спецификация", "спецификация.csv", "CSV (*.csv)")
        if name:
            write_csv(self.rows, name)


def write_csv(rows: list, path) -> Path:
    """Спецификация в CSV: точка с запятой и UTF-8 с меткой — так её
    открывает русский Excel без мастера импорта."""
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow(BomDialog.COLUMNS)
        for number, row in enumerate(rows):
            writer.writerow(BomDialog._cells(number, row))
    return path
