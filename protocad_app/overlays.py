"""Наложения поверх вида: панель видов и угол подтверждения эскиза.

Обе вещи лежат ПОВЕРХ картинки, а не на ленте. Причина одна и та же:
пользоваться ими надо, не отрывая взгляда от детали. Команда, за которой
надо тянуться на другую вкладку, стоит дороже, чем занятое ею место в
кадре, — вкладка «Вид» ровно этим и была плоха.

Рисуются они обычными виджетами Qt поверх ``QOpenGLWidget``: никакого
``QPainter`` внутри ``paintGL``, из-за которого здесь однажды перестал
работать выбор мышью.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

import icons


class _FlatButton(QtWidgets.QToolButton):
    """Кнопка поверх картинки: заметная, но не загораживающая деталь."""

    def __init__(self, glyph: str, tip: str, size: int = 26):
        super().__init__()
        self.setIcon(icons.icon(glyph, size))
        self.setIconSize(QtCore.QSize(size, size))
        self.setToolTip(tip)
        self.setAutoRaise(True)
        self.setFocusPolicy(QtCore.Qt.NoFocus)
        self.setFixedSize(size + 12, size + 10)


class OverlayBar(QtWidgets.QWidget):
    """Полоса кнопок поверх вида."""

    activated = QtCore.Signal(str)
    switched = QtCore.Signal(str, bool)

    STYLE = """
        QWidget#bar {
            background: rgba(252, 253, 255, 232);
            border: 1px solid rgba(120, 134, 156, 110);
            border-radius: 5px;
        }
        QToolButton { border: none; border-radius: 4px; padding: 1px; }
        QToolButton:hover { background: rgba(160, 190, 225, 140); }
        QToolButton:checked { background: rgba(120, 165, 215, 170); }
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("bar")
        self.setStyleSheet(self.STYLE)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self._row = QtWidgets.QHBoxLayout(self)
        self._row.setContentsMargins(5, 3, 5, 3)
        self._row.setSpacing(1)
        self.buttons: dict[str, QtWidgets.QToolButton] = {}
        self.menu_actions: dict[str, QtGui.QAction] = {}

    def add(self, key: str, glyph: str, tip: str, checkable: bool = False):
        button = _FlatButton(glyph, tip)
        button.setCheckable(checkable)
        if checkable:
            button.toggled.connect(lambda on, k=key: self.switched.emit(k, on))
        else:
            button.clicked.connect(lambda _=False, k=key: self.activated.emit(k))
        self._row.addWidget(button)
        self.buttons[key] = button
        return button

    def add_menu(self, key: str, glyph: str, tip: str, entries):
        """Кнопка со списком. ``entries`` — (ключ, подпись, переключаемый)."""
        button = _FlatButton(glyph, tip)
        menu = QtWidgets.QMenu(button)
        for entry_key, title, checkable in entries:
            if entry_key == "-":
                menu.addSeparator()
                continue
            action = QtGui.QAction(title, menu)
            if checkable:
                action.setCheckable(True)
                action.toggled.connect(
                    lambda on, k=entry_key: self.switched.emit(k, on))
                self.menu_actions[entry_key] = action
            else:
                action.triggered.connect(
                    lambda _=False, k=entry_key: self.activated.emit(k))
            menu.addAction(action)
        button.setMenu(menu)
        button.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        self._row.addWidget(button)
        self.buttons[key] = button
        return button

    def separator(self) -> None:
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.VLine)
        line.setStyleSheet("color: rgba(120, 134, 156, 90);")
        self._row.addWidget(line)

    def check(self, key: str, on: bool) -> None:
        """Догнать состояние, не поднимая сигнала."""
        for holder in (self.buttons.get(key), self.menu_actions.get(key)):
            if holder is None or not holder.isCheckable():
                continue
            if holder.isChecked() == on:
                continue
            holder.blockSignals(True)
            holder.setChecked(on)
            holder.blockSignals(False)


class ConfirmCorner(QtWidgets.QWidget):
    """Угол подтверждения: выйти с сохранением или отказаться от правок.

    Две кнопки, а не одна. «Выйти» и «отменить всё, что я тут сделал» —
    разные намерения, и разбирать их вопросом в диалоге поздно: к моменту
    вопроса человек уже нажал, а диалог читают не всегда.
    """

    accepted = QtCore.Signal()
    rejected = QtCore.Signal()

    STYLE = """
        QWidget#corner {
            background: rgba(252, 253, 255, 236);
            border: 1px solid rgba(120, 134, 156, 120);
            border-radius: 5px;
        }
        QToolButton { border: none; border-radius: 4px; }
        QToolButton:hover { background: rgba(160, 190, 225, 150); }
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("corner")
        self.setStyleSheet(self.STYLE)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(4, 3, 4, 3)
        row.setSpacing(2)

        self.ok = _FlatButton("sketch_accept", "Выйти из эскиза, сохранив правки", 26)
        self.ok.clicked.connect(self.accepted.emit)
        self.cancel = _FlatButton(
            "sketch_reject", "Выйти из эскиза, отменив правки", 26)
        self.cancel.clicked.connect(self.rejected.emit)
        row.addWidget(self.ok)
        row.addWidget(self.cancel)
        self.adjustSize()
