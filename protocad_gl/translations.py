"""Русские подписи стандартных кнопок и диалогов Qt.

Без перевода Qt подписывает кнопки по-английски: «Cancel», «Save»,
«Discard» — посреди русского интерфейса. Перевод идёт вместе с PySide6
(`qtbase_ru.qm`), его надо только подключить.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

_held: list = []


def install(app: QtWidgets.QApplication) -> bool:
    """Подключить перевод Qt на русский. ``False`` — файла перевода нет."""
    folder = QtCore.QLibraryInfo.path(QtCore.QLibraryInfo.TranslationsPath)
    translator = QtCore.QTranslator(app)
    if not translator.load("qtbase_ru", folder):
        return False
    app.installTranslator(translator)
    _held.append(translator)
    return True
