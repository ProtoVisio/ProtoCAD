"""Окно сборки: состав, сопряжения, спецификация, выгрузка STEP.

Своей логики у окна нет: всё, что оно делает со сборкой, — вызовы
`protocad.assembly.AssemblyDocument`. Окно показывает, собирает щелчки и
отдаёт команды; то же самое делается и без окна.

Сопряжение ставится так, как принято в инженерных пакетах: команда → две
грани → деталь сразу встаёт на место → «Применить» или «Отмена». Пока
панель открыта, развернуть сопряжение и поменять расстояние можно, не
начиная заново.

Показ — один буфер видеопамяти на всю сборку, а разбивка каждого
определения на треугольники считается один раз за жизнь окна: сдвиг
детали меняет матрицу, а не треугольники.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from protocad.assembly import TITLES, AssemblyDocument
from protocad.assembly import faces as faces_module
from protocad.assembly.document import BROKEN, FIXED, FREE, MATED
from protocad_gl import SceneBuffers, Viewport
from protocad_gl.viewport import PROBLEM_COLOUR, SELECTION_COLOUR

from . import dialogs

INSERT_FILTER = ("Детали и сборки (*.step *.stp *.iges *.igs *.brep *.brp "
                 "*.prcadPart *.prcadAsm);;Все файлы (*)")
ASSEMBLY_FILTER = "Сборка ProtoCAD (*.prcadAsm)"

#: Какая грань нужна сопряжению.
WANTS = {"coincident": "plane", "distance": "plane", "concentric": "cylinder"}
SURFACE = {"plane": "плоская", "cylinder": "цилиндрическая"}
#: То же в винительном падеже — для подсказки «щёлкните …».
SURFACE_TO_PICK = {"plane": "плоскую", "cylinder": "цилиндрическую"}

#: Цвета первой и второй грани сопряжения — разные: видно, что с чем.
FIRST_COLOUR = 1
SECOND_COLOUR = 4

STATUS_TEXT = {FIXED: "закреплено", MATED: "сопряжено", FREE: "свободно",
               BROKEN: "ошибка"}


class AssemblyWindow(QtWidgets.QMainWindow):
    """Сборка на сопряжениях."""

    def __init__(self, document: AssemblyDocument | None = None, folder: str = "",
                 backend=None):
        super().__init__()
        self.document = document or AssemblyDocument()
        self.backend = backend
        self.folder = folder or str(Path.cwd())
        self.preview = None
        #: Выбранное: путь вхождений от верхнего уровня. Команды работают с
        #: вхождением верхнего уровня — сопрягают и двигают узел целиком.
        self.current: tuple = ()
        #: Выбранное сопряжение (его id) — для подсветки его граней.
        self.current_mate = ""
        self.undo_stack: list = []
        self.changed = False
        #: Идущее сопряжение: вид, выбранные грани, добавленное правило и
        #: снимок сборки до него.
        self.mate: dict | None = None
        self.resize(1500, 920)

        self.viewport = Viewport()
        self.viewport.pick_kinds = ("face",)
        self.viewport.selected.connect(self._picked)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Сборка", "Состояние"])
        self.tree.setColumnWidth(0, 270)
        self.tree.itemClicked.connect(self._tree_clicked)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)

        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Здесь — итог последней команды.")

        # Панель сопряжения — над деревом, вне разделителя: в разделителе
        # скрытая панель при показе получала нулевую высоту и оставалась
        # невидимой.
        self.panel = self._build_mate_panel()
        lists = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        lists.addWidget(self.tree)
        lists.addWidget(self.output)
        lists.setSizes([600, 220])
        side = QtWidgets.QWidget()
        column = QtWidgets.QVBoxLayout(side)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(self.panel)
        column.addWidget(lists, 1)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.addWidget(side)
        splitter.addWidget(self.viewport)
        splitter.setSizes([400, 1100])
        self.setCentralWidget(splitter)

        self.scene_label = QtWidgets.QLabel("")
        self.statusBar().addPermanentWidget(self.scene_label)
        self._build_actions()
        self._refresh(fit=True)

    # --- команды ---------------------------------------------------------

    def _build_actions(self) -> None:
        bar = self.addToolBar("Сборка")
        bar.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        bar.setMovable(False)
        self.needs_parts = []
        self.needs_choice = []

        def action(title, handler, tip="", shortcut=None, menu=None, needs=None):
            item = QtGui.QAction(title, self)
            item.setToolTip(tip or title)
            item.setStatusTip(tip)
            if shortcut:
                item.setShortcut(QtGui.QKeySequence(shortcut))
            item.triggered.connect(handler)
            if needs is not None:
                needs.append(item)
            (menu or bar).addAction(item)
            return item

        files = self.menuBar().addMenu("Файл")
        action("Новая сборка", self._new, "", QtGui.QKeySequence.New, files)
        action("Открыть…", self._open, "сборка ProtoCAD", QtGui.QKeySequence.Open, files)
        files.addSeparator()
        action("Сохранить", self._save, "", QtGui.QKeySequence.Save, files)
        action("Сохранить как…", self._save_as, "", QtGui.QKeySequence.SaveAs, files)
        action("Экспорт STEP…", self._export_step,
               "сборка со структурой и именами — для расчётной системы",
               menu=files, needs=self.needs_parts)
        files.addSeparator()
        action("Закрыть", self.close, menu=files)

        edit = self.menuBar().addMenu("Правка")
        self.undo_action = action("Отменить", self._undo, "вернуть сборку до команды",
                                  QtGui.QKeySequence.Undo, edit)
        action("Удалить", self._delete, "выбранное вхождение или сопряжение",
               QtGui.QKeySequence.Delete, edit)
        action("Снять выбор", self._escape, "", "Esc", edit)

        action("Вставить…", self._insert,
               "деталь или сборка: STEP, IGES, BREP, файл ProtoCAD", "Ins")
        bar.addSeparator()
        action("Закрепить", self._toggle_fixed,
               "закреплённое стоит на месте, остальное ставят сопряжения",
               needs=self.needs_choice)
        action("Переместить…", self._move, "сдвиг и поворот вхождения",
               needs=self.needs_choice)
        bar.addSeparator()
        for kind, key in (("coincident", "C"), ("concentric", "O"), ("distance", "D")):
            action(TITLES[kind], lambda _checked=False, kind=kind: self.start_mate(kind),
                   {"coincident": "две плоские грани — в одну плоскость",
                    "concentric": "две цилиндрические грани — на одну ось",
                    "distance": "две плоские грани — на расстоянии"}[kind],
                   key, needs=self.needs_parts)
        bar.addSeparator()
        action("Пересчитать", self._solve, "расставить вхождения по сопряжениям",
               "F5", needs=self.needs_parts)
        action("Спецификация", self._bom, "состав сборки по разделам",
               needs=self.needs_parts)

        view = self.menuBar().addMenu("Вид")
        action("Вписать", self.viewport.fit_view, "", "F", view)
        edges = QtGui.QAction("Рёбра", self, checkable=True, checked=True)
        edges.toggled.connect(self._toggle_edges)
        view.addAction(edges)

    def _build_mate_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QGroupBox("Сопряжение")
        layout = QtWidgets.QFormLayout(panel)
        self.mate_hint = QtWidgets.QLabel("")
        self.mate_hint.setWordWrap(True)
        self.mate_first = QtWidgets.QLabel("—")
        self.mate_second = QtWidgets.QLabel("—")
        self.mate_value = QtWidgets.QDoubleSpinBox()
        self.mate_value.setRange(-1e5, 1e5)
        self.mate_value.setDecimals(3)
        self.mate_value.setSuffix(" мм")
        self.mate_value.setKeyboardTracking(False)
        self.mate_value.valueChanged.connect(self._mate_changed)
        self.mate_flip = QtWidgets.QCheckBox("Развернуть")
        self.mate_flip.toggled.connect(self._mate_changed)
        self.mate_state = QtWidgets.QLabel("")
        self.mate_state.setWordWrap(True)
        buttons = QtWidgets.QDialogButtonBox()
        self.mate_ok = buttons.addButton("Применить", QtWidgets.QDialogButtonBox.AcceptRole)
        buttons.addButton("Отмена", QtWidgets.QDialogButtonBox.RejectRole)
        buttons.accepted.connect(self._mate_apply)
        buttons.rejected.connect(self._mate_cancel)
        layout.addRow(self.mate_hint)
        layout.addRow("Грань 1", self.mate_first)
        layout.addRow("Грань 2", self.mate_second)
        self.mate_value_row = QtWidgets.QLabel("Расстояние")
        layout.addRow(self.mate_value_row, self.mate_value)
        layout.addRow(self.mate_flip)
        layout.addRow(self.mate_state)
        layout.addRow(buttons)
        panel.setVisible(False)
        return panel

    def _toggle_edges(self, on: bool) -> None:
        self.viewport.show_edges = on
        self.viewport.update()

    def _enable(self) -> None:
        parts = bool(self.document.occurrences)
        chosen = self._chosen() is not None
        for item in self.needs_parts:
            item.setEnabled(parts)
        for item in self.needs_choice:
            item.setEnabled(chosen)
        self.undo_action.setEnabled(bool(self.undo_stack))

    # --- документ ------------------------------------------------------------

    def set_document(self, document: AssemblyDocument) -> None:
        self._end_mate(keep=False)
        self.document = document
        self.undo_stack = []
        self.current = ()
        self.current_mate = ""
        self.changed = False
        self._refresh(fit=True)

    def _title(self) -> None:
        name = self.document.root.label or "Сборка"
        where = f" — {self.document.path.name}" if self.document.path else ""
        self.setWindowTitle(f"ProtoCAD — сборка {name}{where}{' *' if self.changed else ''}")

    def _new(self) -> None:
        if self._may_drop():
            self.set_document(AssemblyDocument())

    def _open(self) -> None:
        if not self._may_drop():
            return
        name, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Открыть сборку", self.folder, ASSEMBLY_FILTER)
        if name:
            self.open_path(name)

    def open_path(self, path) -> bool:
        path = Path(path)
        self.folder = str(path.parent)
        try:
            with self._busy():
                document = AssemblyDocument.open(path)
                document.solve()
        except Exception as failure:  # noqa: BLE001 — показать, а не уронить окно
            QtWidgets.QMessageBox.critical(self, "Открыть сборку", str(failure))
            return False
        self.set_document(document)
        self._say(f"Открыто: {path.name} — вхождений {len(document.occurrences)}, "
                  f"сопряжений {len(document.mates)}.")
        return True

    def _save(self) -> bool:
        if self.document.path is None:
            return self._save_as()
        return self.save_path(self.document.path)

    def _save_as(self) -> bool:
        suggested = Path(self.folder) / f"{_file_stem(self.document.root.label)}.prcadAsm"
        name, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Сохранить сборку", str(suggested), ASSEMBLY_FILTER)
        return bool(name) and self.save_path(name)

    def save_path(self, path) -> bool:
        try:
            with self._busy():
                path = self.document.save(path)
        except Exception as failure:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "Сохранить", str(failure))
            return False
        self.folder = str(Path(path).parent)
        self.changed = False
        self._title()
        self._say(f"Сохранено: {Path(path).name}")
        return True

    def _export_step(self) -> None:
        suggested = Path(self.folder) / f"{_file_stem(self.document.root.label)}.step"
        name, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Экспорт STEP", str(suggested), "STEP (*.step *.stp)")
        if name:
            self.export_step(name)

    def export_step(self, path) -> bool:
        try:
            with self._busy():
                path = self.document.export_step(path)
        except Exception as failure:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "Экспорт STEP", str(failure))
            return False
        self._say(f"Выгружено: {Path(path).name} — сборкой, с позиционными "
                  f"обозначениями и именами деталей.")
        return True

    def _may_drop(self) -> bool:
        """Можно ли бросить текущую сборку: не изменена или человек согласен."""
        if not self.changed:
            return True
        answer = QtWidgets.QMessageBox.question(
            self, "Сборка изменена", "Сохранить изменения?",
            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard
            | QtWidgets.QMessageBox.Cancel)
        if answer == QtWidgets.QMessageBox.Save:
            return self._save()
        return answer == QtWidgets.QMessageBox.Discard

    def closeEvent(self, event) -> None:  # noqa: N802 — имя задано Qt
        if self._may_drop():
            event.accept()
        else:
            event.ignore()

    # --- состав ------------------------------------------------------------------

    def _insert(self) -> None:
        name, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Вставить в сборку", self.folder, INSERT_FILTER)
        if name:
            self.insert_path(name)

    def insert_path(self, path):
        """Вставить изделие из файла. Новое встаёт рядом со сборкой, а не
        внутрь неё: иначе его не видно и не за что взять."""
        path = Path(path)
        self.folder = str(path.parent)
        before = self.document.snapshot()
        try:
            with self._busy():
                occurrence = self.document.insert(path, backend=self.backend)
                self.preview = self.document.scene()
                self._beside(occurrence)
        except Exception as failure:  # noqa: BLE001
            self.document.restore(before)
            QtWidgets.QMessageBox.critical(self, "Вставить", str(failure))
            return None
        self._done(before)
        self.current = (occurrence.stable_id,)
        self._refresh(fit=len(self.document.occurrences) == 1)
        held = ("закреплено — от него отсчитываются остальные"
                if occurrence.stable_id in self.document.fixed
                else "свободно — поставьте сопряжения")
        self._say(f"Вставлено: {occurrence.label} ({path.name}), {held}.")
        return occurrence

    def _beside(self, occurrence) -> None:
        """Сдвинуть только что вставленное вхождение вправо от остальных."""
        if len(self.document.occurrences) < 2 or self.preview is None:
            return
        mine = np.isin(self.preview.ids, self.document.bodies_of(self.preview, occurrence))
        if not mine.any() or mine.all():
            return
        others = self.preview.positions[~mine]
        own = self.preview.positions[mine]
        size = float(np.max(own.max(axis=0) - own.min(axis=0)))
        gap = max(5.0, 0.2 * size)
        matrix = occurrence.transform.copy()
        matrix[0, 3] += float(others[:, 0].max() - own[:, 0].min()) + gap
        self.document.move(occurrence, matrix)

    def _chosen(self):
        """Вхождение верхнего уровня, выбранное сейчас."""
        return self.document.occurrence(self.current[0]) if self.current else None

    def _toggle_fixed(self) -> None:
        occurrence = self._chosen()
        if occurrence is None:
            return
        on = occurrence.stable_id not in self.document.fixed
        before = self.document.snapshot()
        self.document.fix(occurrence, on)
        self._solve_after(before)
        self._say(f"{occurrence.label}: {'закреплено' if on else 'освобождено'}.")

    def _move(self) -> None:
        occurrence = self._chosen()
        if occurrence is None:
            return
        held = (occurrence.stable_id not in self.document.fixed
                and bool(self.document.mates_of(occurrence)))
        dialog = dialogs.MoveDialog(occurrence.label, held, self)
        if not dialog.exec():
            return
        values = dialog.values()
        before = self.document.snapshot()
        self.document.move(occurrence, dialogs.moved(
            occurrence.transform, values["shift"], values["turn"],
            self._centre_of(occurrence)))
        self._solve_after(before)

    def _centre_of(self, occurrence) -> np.ndarray:
        if self.preview is None:
            return occurrence.transform[:3, 3]
        mine = np.isin(self.preview.ids, self.document.bodies_of(self.preview, occurrence))
        if not mine.any():
            return occurrence.transform[:3, 3]
        points = self.preview.positions[mine]
        return (points.min(axis=0) + points.max(axis=0)) / 2.0

    def _delete(self) -> None:
        if self.mate is not None:
            return
        if self.current_mate:
            mate = next((item for item in self.document.mates
                         if item.id == self.current_mate), None)
            if mate is None:
                return
            before = self.document.snapshot()
            self.document.remove_mate(mate)
            self.current_mate = ""
            self._solve_after(before)
            self._say(f"Удалено сопряжение: {self.document.describe(mate)}")
            return
        occurrence = self._chosen()
        if occurrence is None:
            return
        before = self.document.snapshot()
        gone = self.document.remove(occurrence)
        self.current = ()
        self._solve_after(before)
        note = f", с ним сопряжений: {len(gone)}" if gone else ""
        self._say(f"Удалено: {occurrence.label}{note}.")

    def _solve(self) -> None:
        before = self.document.snapshot()
        self._solve_after(before)
        found = self.document.diagnostics
        if not found:
            self._say("Сборка решена: все сопряжения выполняются.")

    def _solve_after(self, before) -> list:
        """Решить после правки, записать отмену, показать итог."""
        with self._busy():
            found = self.document.solve()
        self._done(before)
        self._refresh()
        if found:
            self._say("\n".join(f"{'✖' if note.blocking else '!'} {note.message}"
                                for note in found))
        return found

    def _done(self, before) -> None:
        self.undo_stack.append(before)
        self.changed = True

    def _undo(self) -> None:
        if self.mate is not None:
            self._mate_cancel()
            return
        if not self.undo_stack:
            return
        self.document.restore(self.undo_stack.pop())
        self.document.solve()
        self.current_mate = ""
        self._refresh()
        self._say("Отменено.")

    def _bom(self) -> None:
        dialogs.BomDialog(self.document.bom(), self.document.root.label, self).exec()

    # --- сопряжение ------------------------------------------------------------

    def start_mate(self, kind: str) -> None:
        """Начать сопряжение: дальше щелчками выбираются две грани."""
        if len(self.document.occurrences) < 2:
            self._say("Сопрягать нечего: в сборке меньше двух вхождений. "
                      "Вставьте детали.")
            return
        self._end_mate(keep=False)
        self.mate = {"kind": kind, "picks": [], "added": None,
                     "before": self.document.snapshot()}
        self.current_mate = ""
        surface = SURFACE_TO_PICK[WANTS[kind]]
        self.panel.setTitle(TITLES[kind])
        self.mate_hint.setText(f"Щёлкните {surface} грань одной детали, потом "
                               f"{surface} грань другой. Деталь встанет сразу.")
        distance = kind == "distance"
        self.mate_value.setVisible(distance)
        self.mate_value_row.setVisible(distance)
        self.mate_value.blockSignals(True)
        self.mate_value.setValue(0.0)
        self.mate_value.blockSignals(False)
        self.mate_flip.blockSignals(True)
        self.mate_flip.setChecked(False)
        self.mate_flip.blockSignals(False)
        self.panel.setVisible(True)
        self._show_mate()
        self._paint()

    def _mate_pick(self, face_id: int) -> None:
        picked = self.document.pick(self.preview, face_id)
        if picked is None:
            return
        occurrence, face = picked
        wanted = WANTS[self.mate["kind"]]
        mark = faces_module.mark_of(face)
        if faces_module.kind_of(mark) != wanted:
            self.mate_state.setText(f"Нужна {SURFACE[wanted]} грань, а выбрана "
                                    f"{_surface_name(face)}. Выберите другую.")
            return
        picks = self.mate["picks"]
        if len(picks) == 2:
            # Третий щелчок — начать выбор заново с этой грани.
            self.document.restore(self.mate["before"])
            self.mate["added"] = None
            picks.clear()
            self._refresh()
        if picks and picks[0][0] is occurrence:
            self.mate_state.setText("Вторая грань должна быть у ДРУГОЙ детали.")
            return
        picks.append((occurrence, face, int(face_id)))
        if len(picks) == 2:
            self._mate_try()
        else:
            self._show_mate()
            self._paint()

    def _mate_changed(self, *_args) -> None:
        if self.mate is not None and self.mate["added"] is not None:
            self._mate_try()

    def _mate_try(self) -> None:
        """Поставить сопряжение и решить — на пробу, до «Применить»."""
        (first, first_face, _), (second, second_face, _) = self.mate["picks"]
        self.document.restore(self.mate["before"])
        self.mate["added"] = self.document.mate(
            self.mate["kind"], (first, first_face), (second, second_face),
            self.mate_value.value(), self.mate_flip.isChecked())
        with self._busy():
            found = self.document.solve()
        self._refresh()
        mine = [note for note in found if note.mate == self.mate["added"].id]
        others = [note for note in found if note.mate != self.mate["added"].id
                  and note.blocking]
        if mine:
            text = "✖ " + mine[0].message
        elif others:
            text = "Поставлено, но в сборке есть ошибки: " + others[0].message
        else:
            text = "Поставлено. «Применить» — оставить, «Отмена» — вернуть как было."
        self.mate_state.setText(text)
        self._show_mate()

    def _show_mate(self) -> None:
        picks = self.mate["picks"] if self.mate else []
        labels = (self.mate_first, self.mate_second)
        for index, label in enumerate(labels):
            if index < len(picks):
                occurrence, face, _ = picks[index]
                label.setText(f"{occurrence.label}: {_surface_name(face)}")
            else:
                label.setText("—")
        if len(picks) < 2:
            self.mate_state.setText(f"Выбрано граней: {len(picks)} из 2.")
        self.mate_ok.setEnabled(self.mate is not None and self.mate["added"] is not None)

    def _mate_apply(self) -> None:
        if self.mate is None:
            return
        if self.mate["added"] is None:
            self._mate_cancel()
            return
        added = self.mate["added"]
        self._done(self.mate["before"])
        self._end_mate(keep=True)
        self.current_mate = added.id
        self._refresh()
        self._say(f"Сопряжение: {self.document.describe(added)}"
                  + ("" if added.ok else f" — {added.message}"))

    def _mate_cancel(self) -> None:
        if self.mate is None:
            return
        self._end_mate(keep=False)
        self._refresh()
        self._say("Сопряжение отменено.")

    def _end_mate(self, keep: bool) -> None:
        if self.mate is None:
            return
        if not keep:
            self.document.restore(self.mate["before"])
        self.mate = None
        self.panel.setVisible(False)

    def _escape(self) -> None:
        if self.mate is not None:
            self._mate_cancel()
            return
        self.current = ()
        self.current_mate = ""
        self._paint()
        self._enable()

    # --- показ ------------------------------------------------------------------

    def _busy(self):
        window = self

        class _Busy:
            def __enter__(self):
                QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
                window.statusBar().showMessage("Считаю…")
                QtWidgets.QApplication.processEvents()

            def __exit__(self, *_):
                QtWidgets.QApplication.restoreOverrideCursor()
                window.statusBar().clearMessage()
                return False

        return _Busy()

    def _say(self, text: str) -> None:
        self.output.setPlainText(text)
        self.statusBar().showMessage(text.split("\n")[0], 8000)

    def _refresh(self, fit: bool = False) -> None:
        if self.document.occurrences:
            self.preview = self.document.scene()
            self.viewport.set_scene(SceneBuffers.from_preview(self.preview))
            stats = self.preview.stats
            self.scene_label.setText(
                f"вхождений {stats['instances']}, треугольников "
                f"{stats['triangles']:,}".replace(",", " ")
                + f", сцена собрана за {stats['build_s'] * 1000:.1f} мс")
        else:
            self.preview = None
            self.viewport.set_scene(SceneBuffers.empty_scene())
            self.scene_label.setText("")
        if fit:
            self.viewport.fit_view()
        if self.current and self.document.occurrence(self.current[0]) is None:
            self.current = ()
        self._paint()
        self._fill_tree()
        self._enable()
        self._title()

    def _bodies_under(self, path: tuple) -> list:
        if self.preview is None or not path:
            return []
        size = len(path)
        return [number for number, body in self.preview.id_to_object.items()
                if tuple((body.get("path") or [])[:size]) == tuple(path)]

    def _scene_faces(self, occurrence) -> list:
        """Номера граней сцены в порядке описаний граней вхождения."""
        found = []
        for number in sorted(self.document.bodies_of(self.preview, occurrence)):
            body = self.preview.id_to_object[number]
            first = body.get("first_face", 0)
            found.extend(range(first, first + body.get("faces", 0)))
        return found

    def _mate_faces(self, mate) -> dict:
        """{номер грани сцены: цвет} для граней сопряжения."""
        colours = {}
        for reference, colour in ((mate.first, FIRST_COLOUR),
                                  (mate.second, SECOND_COLOUR)):
            occurrence = self.document.occurrence(reference.instance)
            if occurrence is None:
                continue
            face = faces_module.find(self.document.faces(occurrence), reference.mark)
            scene_faces = self._scene_faces(occurrence)
            if face is not None and face["index"] < len(scene_faces):
                colours[scene_faces[face["index"]]] = colour
        return colours

    def _paint(self) -> None:
        bodies, faces = {}, {}
        if self.preview is not None:
            for mate in self.document.mates:
                if mate.ok:
                    continue
                for reference in (mate.first, mate.second):
                    for number in self._bodies_under((reference.instance,)):
                        bodies[number] = PROBLEM_COLOUR
            if self.mate is not None:
                for index, (_occurrence, _face, face_id) in enumerate(self.mate["picks"]):
                    faces[face_id] = FIRST_COLOUR if index == 0 else SECOND_COLOUR
            elif self.current_mate:
                mate = next((item for item in self.document.mates
                             if item.id == self.current_mate), None)
                if mate is not None:
                    faces.update(self._mate_faces(mate))
            elif self.current:
                for number in self._bodies_under(self.current):
                    bodies[number] = SELECTION_COLOUR
        self.viewport.highlight_face = 0
        self.viewport.set_body_colors(bodies)
        self.viewport.set_face_colors(faces)

    def _fill_tree(self) -> None:
        self.tree.clear()
        document = self.document
        root = QtWidgets.QTreeWidgetItem(self.tree, [document.root.label or "Сборка", ""])
        parts = QtWidgets.QTreeWidgetItem(
            root, [f"Состав ({len(document.occurrences)})", ""])
        chosen = None
        for occurrence in document.occurrences:
            status = document.status(occurrence)
            item = QtWidgets.QTreeWidgetItem(parts, [occurrence.label, STATUS_TEXT[status]])
            item.setData(0, QtCore.Qt.UserRole, ("path", (occurrence.stable_id,)))
            tip = occurrence.item.label
            source = document.sources.get(occurrence.item.stable_id)
            if source:
                tip += f"\n{source}"
            item.setToolTip(0, tip)
            if status == BROKEN:
                item.setForeground(1, QtGui.QBrush(QtGui.QColor(200, 40, 40)))
            self._fill_inner(item, occurrence.item, (occurrence.stable_id,))
            if self.current and self.current[0] == occurrence.stable_id:
                chosen = item
        mates = QtWidgets.QTreeWidgetItem(root, [f"Сопряжения ({len(document.mates)})", ""])
        for mate in document.mates:
            item = QtWidgets.QTreeWidgetItem(
                mates, [document.describe(mate), "выполнено" if mate.ok else "ошибка"])
            item.setData(0, QtCore.Qt.UserRole, ("mate", mate.id))
            if not mate.ok:
                item.setForeground(1, QtGui.QBrush(QtGui.QColor(200, 40, 40)))
                note = next((entry for entry in document.diagnostics
                             if entry.mate == mate.id), None)
                if note is not None:
                    item.setToolTip(0, note.message)
            if mate.id == self.current_mate:
                chosen = item
        if document.diagnostics:
            notes = QtWidgets.QTreeWidgetItem(
                root, [f"Замечания ({len(document.diagnostics)})", ""])
            for note in document.diagnostics:
                item = QtWidgets.QTreeWidgetItem(
                    notes, [("✖ " if note.blocking else "! ") + note.message, ""])
                item.setToolTip(0, note.message)
                item.setData(0, QtCore.Qt.UserRole, ("mate", note.mate))
            notes.setExpanded(True)
        root.setExpanded(True)
        parts.setExpanded(True)
        mates.setExpanded(True)
        if chosen is not None:
            self.tree.setCurrentItem(chosen)

    def _fill_inner(self, parent, item, path: tuple) -> None:
        """Состав подсборки — для обзора и подсветки; сопрягается узел целиком."""
        for occurrence in getattr(item, "placements", ()):
            inner = path + (occurrence.stable_id,)
            node = QtWidgets.QTreeWidgetItem(parent, [occurrence.label, ""])
            node.setToolTip(0, occurrence.item.label)
            node.setData(0, QtCore.Qt.UserRole, ("path", inner))
            self._fill_inner(node, occurrence.item, inner)

    # --- выбор ------------------------------------------------------------------

    def _picked(self, what: dict) -> None:
        if what.get("kind") != "face":
            if self.mate is None:
                self._escape()
            return
        face_id = int(what.get("id", 0))
        if self.mate is not None:
            self._mate_pick(face_id)
            return
        entry = self.preview.face_to_object.get(face_id) if self.preview else None
        body = self.preview.id_to_object.get(entry["body"]) if entry else None
        if not body or not body.get("path"):
            return
        # Щелчок выбирает вхождение ВЕРХНЕГО уровня: его двигают, крепят и
        # сопрягают. Деталь внутри узла выбирается в дереве.
        self.current = (body["path"][0],)
        self.current_mate = ""
        self._paint()
        self._fill_tree()
        self._enable()
        occurrence = self._chosen()
        self.statusBar().showMessage(
            f"{occurrence.label}: {STATUS_TEXT[self.document.status(occurrence)]}; "
            f"деталь под курсором — {body.get('label', '')}", 8000)

    def _tree_clicked(self, item, _column) -> None:
        payload = item.data(0, QtCore.Qt.UserRole)
        if not payload or self.mate is not None:
            return
        kind, key = payload
        if kind == "path":
            self.current = tuple(key)
            self.current_mate = ""
        elif kind == "mate" and key:
            self.current_mate = key
            self.current = ()
            mate = next((entry for entry in self.document.mates if entry.id == key), None)
            if mate is not None:
                note = next((entry for entry in self.document.diagnostics
                             if entry.mate == key), None)
                self._say(self.document.describe(mate)
                          + (f"\n{note.message}" if note else ""))
        self._paint()
        self._enable()

    def _tree_menu(self, position) -> None:
        item = self.tree.itemAt(position)
        payload = item.data(0, QtCore.Qt.UserRole) if item else None
        if not payload or self.mate is not None:
            return
        self._tree_clicked(item, 0)
        menu = QtWidgets.QMenu(self)
        kind, key = payload
        if kind == "path":
            occurrence = self._chosen()
            if occurrence is None:
                return
            fixed = occurrence.stable_id in self.document.fixed
            menu.addAction("Освободить" if fixed else "Закрепить", self._toggle_fixed)
            menu.addAction("Переместить…", self._move)
            menu.addSeparator()
            menu.addAction("Удалить из сборки", self._delete)
        elif kind == "mate" and key:
            menu.addAction("Развернуть", lambda: self._flip_mate(key))
            mate = next((entry for entry in self.document.mates if entry.id == key), None)
            if mate is not None and mate.kind == "distance":
                menu.addAction("Расстояние…", lambda: self._set_distance(key))
            menu.addSeparator()
            menu.addAction("Удалить сопряжение", self._delete)
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _flip_mate(self, key: str) -> None:
        mate = next((entry for entry in self.document.mates if entry.id == key), None)
        if mate is None:
            return
        before = self.document.snapshot()
        mate.flip = not mate.flip
        self._solve_after(before)

    def _set_distance(self, key: str) -> None:
        mate = next((entry for entry in self.document.mates if entry.id == key), None)
        if mate is None:
            return
        value, ok = QtWidgets.QInputDialog.getDouble(
            self, "Расстояние", "Расстояние, мм", mate.value_mm, -1e5, 1e5, 3)
        if not ok:
            return
        before = self.document.snapshot()
        mate.value_mm = float(value)
        self._solve_after(before)


def _surface_name(face: dict) -> str:
    surface = face.get("surface", "")
    if surface == "cylinder":
        return f"цилиндр Ø{2 * face.get('radius', 0.0):.4g}"
    if surface == "plane":
        return "плоскость"
    return {"cone": "конус", "sphere": "сфера", "torus": "тор"}.get(surface,
                                                                     "сложная грань")


def _file_stem(name: str) -> str:
    return "".join(character if character.isalnum() or character in "-_." else "_"
                   for character in name).strip("_") or "сборка"
