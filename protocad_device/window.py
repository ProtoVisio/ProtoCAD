"""Окно подготовки прибора к тепловому расчёту.

Путь человека: открыть STEP → проверить разбор («что плата, что корпус,
что крепёж») → разложить компоненты по расчётным случаям → проверить
контакты и пересечения → выбрать слои в контактах → упростить → выгрузить
STEP и таблицу тепловых сопротивлений.

Своей логики у окна нет: всё — вызовы `protocad.device`. Дерево строится из
свойств единиц заново при каждой правке, поэтому перестановка единицы из
группы в группу не может разойтись с данными.

Показ: вся геометрия прибора — один буфер видеопамяти. Раскраска по видам,
случаям и замечаниям, выбор и скрытие исключённых меняют только буфер
признаков — геометрия не перезаливается (`protocad_gl.viewport`).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from protocad import device as device_module
from protocad.device import (COMPONENT, EXCLUDED, FASTENER, HOUSING, OTHER, ROLE_GROUPS,
                             ROLE_TITLES, SUBSTRATE, Device, classify, demo)
from protocad.device import contacts as contacts_module
from protocad.device import simplify, thermal
from protocad.device.export import export_step as write_step
from protocad.model import Assembly
from protocad_gl import SceneBuffers, Viewport
from protocad_gl.viewport import PALETTE, PROBLEM_COLOUR, SELECTION_COLOUR

from . import dialogs

OPEN_FILTER = ("Прибор (*.step *.stp *.prcadAsm);;STEP (*.step *.stp);;"
               "Прибор ProtoCAD (*.prcadAsm);;Все файлы (*)")

KINDS = "Виды"
TYPES = "Типы ЭРИ"
CASES = "Расчётные случаи"
NOTES = "Замечания"
MODES = (KINDS, TYPES, CASES, NOTES)

#: Цвета по ролям (номер в палитре вьюпорта; 0 — свой серый).
ROLE_COLOUR = {SUBSTRATE: 8, COMPONENT: 1, FASTENER: 7, OTHER: 5, HOUSING: 0}
TYPE_COLOURS = (1, 2, 4, 3, 5, 6, 7)
CASE_COLOURS = (1, 4, 6, 3, 5, 7)
COMMON_COLOUR = 2
WARNING_COLOUR = 5


def _swatch(colour: int) -> str:
    red, green, blue = (int(255 * value) for value in PALETTE[colour - 1])
    return f"<span style='color:#{red:02x}{green:02x}{blue:02x}'>■</span>"


class DeviceWindow(QtWidgets.QMainWindow):
    """Подготовка прибора к тепловому расчёту."""

    def __init__(self, device: Device | None = None, folder: str = ""):
        super().__init__()
        self.device = None
        self.folder = folder or str(Path.cwd())
        self.report = None
        self.findings: list = []
        self.rows: list = []
        self.selected: set = set()
        self.undo_stack: list = []
        self.changed = False
        self.preview = None
        self.unit_bodies: dict = {}
        self.body_units: dict = {}
        self.leaf_items: dict = {}
        #: Скрытые для обзора (в расчёте остаются): ключи единиц.
        self.hidden_units: set = set()
        self._scene_cache: dict = {}
        self.resize(1560, 960)

        self.viewport = Viewport()
        self.viewport.pick_kinds = ("face",)
        self.viewport.selected.connect(self._picked)

        self.mode = QtWidgets.QComboBox()
        self.mode.addItems(MODES)
        self.mode.currentTextChanged.connect(self._mode_changed)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText("Найти: R12, DD, Винт…")
        self.search.textChanged.connect(lambda _text: self._fill_tree())
        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Прибор", "Изделие", "Случаи / почему"])
        self.tree.setColumnWidth(0, 300)
        self.tree.setColumnWidth(1, 170)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self._tree_selected)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.legend = QtWidgets.QLabel("")
        self.legend.setWordWrap(True)
        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Здесь — итог последнего шага.")

        top = QtWidgets.QHBoxLayout()
        top.addWidget(QtWidgets.QLabel("Показ:"))
        top.addWidget(self.mode, 1)
        panel = QtWidgets.QWidget()
        column = QtWidgets.QVBoxLayout(panel)
        column.setContentsMargins(4, 4, 0, 0)
        column.addLayout(top)
        column.addWidget(self.search)
        column.addWidget(self.tree, 1)
        column.addWidget(self.legend)
        side = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        side.addWidget(panel)
        side.addWidget(self.output)
        side.setSizes([720, 200])
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.addWidget(side)
        splitter.addWidget(self.viewport)
        splitter.setSizes([560, 1000])
        self.setCentralWidget(splitter)

        self._build_contacts_dock()
        self.scene_label = QtWidgets.QLabel("")
        self.statusBar().addPermanentWidget(self.scene_label)
        self._build_actions()
        self.set_device(device)

    # --- команды ------------------------------------------------------------

    def _build_actions(self) -> None:
        bar = self.addToolBar("Прибор")
        bar.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        bar.setMovable(False)
        self.needs_device = []

        def action(title, handler, tip="", shortcut=None, menu=None, device=True):
            item = QtGui.QAction(title, self)
            item.setToolTip(tip or title)
            item.setStatusTip(tip)
            if shortcut:
                item.setShortcut(QtGui.QKeySequence(shortcut))
            item.triggered.connect(handler)
            if device:
                self.needs_device.append(item)
            (menu or bar).addAction(item)
            return item

        files = self.menuBar().addMenu("Файл")
        action("Открыть…", self._open, "STEP прибора или сохранённый разбор",
               QtGui.QKeySequence.Open, files, device=False)
        action("Образец: блок управления", self._demo, "", menu=files, device=False)
        files.addSeparator()
        action("Сохранить", self._save, "", QtGui.QKeySequence.Save, files)
        action("Сохранить как…", self._save_as, "", QtGui.QKeySequence.SaveAs, files)
        files.addSeparator()
        action("STEP для расчёта…", self._export_step,
               "упрощённая модель с именами: по видам или как пришло", menu=files)
        action("Таблица контактов (CSV)…", self._export_csv,
               "тепловые сопротивления для Flow Simulation и Ansys", menu=files)
        action("Данные для сценариев (JSON)…", self._export_json, menu=files)
        action("Состав расчётных случаев (CSV)…", self._export_cases, menu=files)
        files.addSeparator()
        action("Закрыть", self.close, menu=files, device=False)

        edit = self.menuBar().addMenu("Правка")
        self.undo_action = action("Отменить", self._undo, "вернуть до последнего шага",
                                  QtGui.QKeySequence.Undo, edit)
        action("Снять выбор", self._clear_selection, "", "Esc", edit)

        action("Открыть", self._open, "STEP прибора или сохранённый разбор", device=False)
        bar.addSeparator()
        action("Разбор…", self._classify, "что плата, что корпус, что крепёж")
        action("Случаи…", self._cases, "расчётные случаи и полукомплекты")
        action("Проверить", self._check, "контакты, зазоры, пересечения, одиночки", "F5")
        action("Контакты", self._show_contacts, "тепловые сопротивления контактов")
        action("Упростить…", self._simplify, "отверстия, габариты компонентов, мелочь")
        bar.addSeparator()
        self.hide_housing = QtGui.QAction("Скрыть корпус", self, checkable=True)
        self.hide_housing.setToolTip("корпус закрывает платы — скрыть его для обзора; "
                                     "в расчёте он остаётся")
        self.hide_housing.toggled.connect(lambda _on: self._paint())
        bar.addAction(self.hide_housing)
        self.needs_device.append(self.hide_housing)
        bar.addSeparator()
        action("STEP для расчёта…", self._export_step, "")
        action("Таблица…", self._export_csv, "тепловые сопротивления в CSV")

        view = self.menuBar().addMenu("Вид")
        action("Вписать", self.viewport.fit_view, "", "F", view, device=False)
        edges = QtGui.QAction("Рёбра", self, checkable=True, checked=True)
        edges.toggled.connect(self._toggle_edges)
        view.addAction(edges)
        view.addAction(self.contacts_dock.toggleViewAction())

    def _toggle_edges(self, on: bool) -> None:
        self.viewport.show_edges = on
        self.viewport.update()

    def _enable(self) -> None:
        for item in self.needs_device:
            item.setEnabled(self.device is not None)
        self.undo_action.setEnabled(bool(self.undo_stack))

    # --- прибор ------------------------------------------------------------

    def set_device(self, device: Device | None) -> None:
        self.device = device
        self.report, self.findings, self.rows = None, [], []
        self.selected = set()
        self.hidden_units = set()
        self.undo_stack = []
        self.changed = False
        self._scene_cache = {}
        self._rebuild_scene(fit=True)
        self._fill_tree()
        self._fill_contacts()
        self._enable()
        self._title()

    def _title(self) -> None:
        name = self.device.name if self.device is not None else ""
        where = f" — {self.device.path.name}" if self.device is not None and self.device.path \
            else ""
        mark = " *" if self.changed else ""
        self.setWindowTitle(f"ProtoCAD — подготовка прибора к тепловому расчёту"
                            f"{' — ' + name if name else ''}{where}{mark}")

    def _open(self) -> None:
        if not self._may_drop():
            return
        name, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Открыть прибор", self.folder, OPEN_FILTER)
        if name:
            self.open_path(name)

    def open_path(self, path, ask: bool = True) -> bool:
        """Открыть STEP (с разбором) или сохранённый прибор."""
        path = Path(path)
        self.folder = str(path.parent)
        try:
            with self._busy("Читаю и разбираю…"):
                device = device_module.load(path)
        except Exception as failure:  # noqa: BLE001 — показать, а не уронить окно
            QtWidgets.QMessageBox.critical(self, "Открыть", f"{path.name}: {failure}")
            return False
        self.set_device(device)
        fresh = device.path is None
        self._say(f"Открыто: {path.name} — единиц {len(device.units)}, плат "
                  f"{len(device.boards)}.\n" + "\n".join(getattr(device, "notes", [])))
        if fresh and ask:
            self._classify()
        return True

    def _demo(self) -> None:
        if not self._may_drop():
            return
        with self._busy("Собираю образец…"):
            device = Device(demo.device(problems=True))
            device.notes = classify(device)
        self.set_device(device)
        self._say("Образец: блок управления с платой A1 (два полукомплекта), корпусом, "
                  "стойками и крепежом. В модели нарочно оставлены ошибки: транзистор "
                  "VT2 висит над платой, шильдик не касается крышки. Начните с "
                  "«Разбор…», затем «Проверить».")
        self._classify()

    def _save(self) -> bool:
        if self.device.path is None:
            return self._save_as()
        return self.save_path(self.device.path)

    def _save_as(self) -> bool:
        name, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Сохранить прибор", str(Path(self.folder) / f"{_stem(self.device.name)}.prcadAsm"),
            "Прибор ProtoCAD (*.prcadAsm)")
        return bool(name) and self.save_path(name)

    def save_path(self, path) -> bool:
        try:
            with self._busy("Сохраняю…"):
                path = self.device.save(path)
        except Exception as failure:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "Сохранить", str(failure))
            return False
        self.changed = False
        self._title()
        self._say(f"Сохранено: {Path(path).name}")
        return True

    def _may_drop(self) -> bool:
        if not self.changed or self.device is None:
            return True
        answer = QtWidgets.QMessageBox.question(
            self, "Прибор изменён", "Сохранить изменения?",
            QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.Discard
            | QtWidgets.QMessageBox.Cancel)
        if answer == QtWidgets.QMessageBox.Save:
            return self._save()
        return answer == QtWidgets.QMessageBox.Discard

    def closeEvent(self, event) -> None:  # noqa: N802 — имя задано Qt
        event.accept() if self._may_drop() else event.ignore()

    # --- шаги -----------------------------------------------------------------

    def _push(self) -> None:
        """Снимок для отмены — до любой правки."""
        self.undo_stack.append(simplify.snapshot(self.device))
        self.changed = True

    def _undo(self) -> None:
        if not self.undo_stack:
            return
        simplify.restore(self.device, self.undo_stack.pop())
        self.report, self.findings, self.rows = None, [], []
        self._rebuild_scene()
        self._fill_tree()
        self._fill_contacts()
        self._enable()
        self._say("Отменено: прибор как до последнего шага. Проверку повторите.")

    def _classify(self) -> None:
        before = simplify.snapshot(self.device)
        dialog = dialogs.ClassifyDialog(self.device, self)
        if dialog.exec():
            self.undo_stack.append(before)
            self.changed = True
            self._say("Разбор принят. Дальше — «Случаи…» для полукомплектов и "
                      "«Проверить» для контактов.")
        else:
            # Поиск плат в диалоге мог разложить прибор заново — вернуть как было.
            simplify.restore(self.device, before)
        self.report, self.findings, self.rows = None, [], []
        self._rebuild_scene()
        self._fill_tree()
        self._fill_contacts()
        self._enable()

    def _cases(self) -> None:
        dialogs.CasesDialog(self.device, self, changed=self._cases_changed).exec()
        self.mode.setCurrentText(CASES)

    def _cases_changed(self, before: bool = False) -> None:
        if before:
            self._push()
            return
        self._fill_tree()
        self._paint()
        self._enable()

    def _check(self) -> None:
        with self._busy("Ищу контакты и пересечения…"):
            self.report = contacts_module.check(self.device)
            self.findings = contacts_module.findings(self.device, self.report)
        stats = self.report.stats
        counts = {kind: len(self.report.of_kind(kind)) for kind in
                  (contacts_module.TOUCH, contacts_module.GAP, contacts_module.CLASH)}
        errors = sum(1 for item in self.findings if item.severity == contacts_module.ERROR)
        self._say(f"Проверено за {stats['total_s']:.1f} с: контактов {counts['контакт']}, "
                  f"зазоров {counts['зазор']}, пересечений {counts['пересечение']}; "
                  f"ошибок {errors}.\n"
                  + "\n".join(("✖ " if item.severity == contacts_module.ERROR else
                               "! " if item.severity == contacts_module.WARNING else "· ")
                              + item.text for item in self.findings[:40]))
        self.mode.setCurrentText(NOTES)
        self._fill_tree()
        self._fill_contacts()
        self._paint()

    def _simplify(self) -> None:
        dialog = dialogs.SimplifyDialog(self)
        if not dialog.exec():
            return
        values = dialog.values()
        self._push()
        lines = []
        try:
            with self._busy("Упрощаю…"):
                if values["holes"]:
                    result = simplify.remove_holes(self.device, values["holes"],
                                                   roles=values["roles"])
                    lines += [result.message, *result.notes]
                if values["boxes"]:
                    result = simplify.components_to_boxes(self.device)
                    lines += [result.message, *result.notes]
                if values["small"]:
                    lines.append(simplify.exclude_small(self.device, values["small"]).message)
                if values["fasteners"]:
                    lines.append("крепёж: " + simplify.exclude_role(self.device, FASTENER)
                                 .message)
        except Exception as failure:  # noqa: BLE001
            simplify.restore(self.device, self.undo_stack.pop())
            QtWidgets.QMessageBox.critical(self, "Упростить", f"{type(failure).__name__}: "
                                                              f"{failure}")
            return
        self.report, self.findings, self.rows = None, [], []
        self._rebuild_scene()
        self._fill_tree()
        self._fill_contacts()
        self._enable()
        self._say("Упрощено:\n" + "\n".join(line for line in lines if line)
                  + "\nКонтакты после упрощения другие — нажмите «Проверить».")

    # --- выгрузка ----------------------------------------------------------------

    def _ask_path(self, title, suffix, pattern) -> str:
        name, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, title, str(Path(self.folder) / f"{_stem(self.device.name)}{suffix}"), pattern)
        return name

    def _export_step(self) -> None:
        choice = QtWidgets.QMessageBox.question(
            self, "STEP для расчёта",
            "Структура: «Да» — по видам (корпус, платы с компонентами по типам, крепёж, "
            "прочее); «Нет» — как пришло. Исключённые из расчёта детали не выгружаются.",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
            | QtWidgets.QMessageBox.Cancel)
        if choice == QtWidgets.QMessageBox.Cancel:
            return
        name = self._ask_path("STEP для расчёта", ".step", "STEP (*.step *.stp)")
        if name:
            self.export_step(name, "roles" if choice == QtWidgets.QMessageBox.Yes else "source")

    def export_step(self, path, structure: str = "roles") -> bool:
        try:
            with self._busy("Пишу STEP…"):
                write_step(self.device, path, structure)
        except Exception as failure:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "STEP", str(failure))
            return False
        self._say(f"Выгружено: {Path(path).name} ({'по видам' if structure == 'roles' else 'как пришло'}).")
        return True

    def _need_report(self) -> bool:
        if self.report is None:
            self._check()
        return self.report is not None

    def _export_csv(self) -> None:
        if not self._need_report():
            return
        name = self._ask_path("Таблица контактов", "-контакты.csv", "CSV (*.csv)")
        if name:
            thermal.write_csv(self.rows, name)
            self._say(f"Таблица контактов: {Path(name).name}, строк {len(self.rows)}. "
                      f"r — для Flow Simulation, h — для Ansys, R — полное сопротивление.")

    def _export_json(self) -> None:
        if not self._need_report():
            return
        name = self._ask_path("Данные прибора", ".json", "JSON (*.json)")
        if name:
            thermal.write_json(self.device, self.report, self.rows, name)
            self._say(f"Данные: {Path(name).name}")

    def _export_cases(self) -> None:
        name = self._ask_path("Состав случаев", "-случаи.csv", "CSV (*.csv)")
        if name:
            thermal.write_cases_csv(self.device, name)
            self._say(f"Состав расчётных случаев: {Path(name).name}")

    # --- показ -----------------------------------------------------------------------

    def _busy(self, text: str = "Считаю…"):
        window = self

        class _Busy:
            def __enter__(self):
                QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
                window.statusBar().showMessage(text)
                QtWidgets.QApplication.processEvents()

            def __exit__(self, *_):
                QtWidgets.QApplication.restoreOverrideCursor()
                window.statusBar().clearMessage()
                return False

        return _Busy()

    def _say(self, text: str) -> None:
        self.output.setPlainText(text)
        self.statusBar().showMessage(text.split("\n")[0], 10000)

    def _rebuild_scene(self, fit: bool = False) -> None:
        from protocad.preview import build

        self.unit_bodies, self.body_units = {}, {}
        if self.device is None:
            self.preview = None
            self.viewport.set_scene(SceneBuffers.empty_scene())
            self.scene_label.setText("")
            return
        with self._busy("Готовлю показ…"):
            self.preview = build(self.device.root, cache=self._scene_cache)
        for number, body in self.preview.id_to_object.items():
            key = self.device.unit_of_path(tuple(body.get("path") or ()))
            if key:
                self.body_units[number] = key
                self.unit_bodies.setdefault(key, []).append(number)
        self.viewport.set_scene(SceneBuffers.from_preview(self.preview))
        if fit:
            self.viewport.fit_view()
        stats = self.preview.stats
        self.scene_label.setText(
            f"единиц {len(self.device.units)}, тел {stats['instances']}, треугольников "
            f"{stats['triangles']:,}".replace(",", " ")
            + f", определений {len(self._scene_cache.get('mesh', {}))}, "
              f"сцена {stats['build_s']:.2f} с")
        self._paint()

    def _mode_changed(self, _mode: str) -> None:
        self._fill_tree()
        self._paint()

    def _colour(self, unit, mode: str, kinds: list, problems: dict) -> int:
        if mode == KINDS:
            return ROLE_COLOUR.get(unit.role, 0)
        if mode == TYPES:
            if unit.role == COMPONENT:
                title = self.device.kind_title(unit)
                return TYPE_COLOURS[kinds.index(title) % len(TYPE_COLOURS)]
            return ROLE_COLOUR[SUBSTRATE] if unit.role == SUBSTRATE else 0
        if mode == CASES:
            if unit.role == SUBSTRATE:
                return ROLE_COLOUR[SUBSTRATE]
            if unit.role != COMPONENT:
                return 0
            names = self.device.case_names()
            mine = [name for name in names if name in unit.cases]
            if len(mine) > 1:
                return COMMON_COLOUR
            if mine:
                return CASE_COLOURS[names.index(mine[0]) % len(CASE_COLOURS)]
            return 0
        return problems.get(unit.key, 0)

    def _kinds(self) -> list:
        return sorted({self.device.kind_title(unit) for unit in self.device.of_role(COMPONENT)})

    def _problems(self) -> dict:
        found = {}
        for item in self.findings:
            colour = {contacts_module.ERROR: PROBLEM_COLOUR,
                      contacts_module.WARNING: WARNING_COLOUR}.get(item.severity)
            if colour is None:
                continue
            for key in item.focus or item.units:
                if found.get(key) != PROBLEM_COLOUR:
                    found[key] = colour
        return found

    def _paint(self) -> None:
        if self.device is None or self.preview is None:
            return
        mode = self.mode.currentText()
        kinds, problems = self._kinds(), self._problems()
        colours, hidden = {}, []
        for key, bodies in self.unit_bodies.items():
            unit = self.device.units.get(key)
            if unit is None:
                continue
            if unit.role == EXCLUDED or key in self.hidden_units or (
                    unit.role == HOUSING and self.hide_housing.isChecked()):
                hidden.extend(bodies)
                continue
            colour = SELECTION_COLOUR if key in self.selected else self._colour(
                unit, mode, kinds, problems)
            if colour:
                for body in bodies:
                    colours[body] = colour
        self.viewport.highlight_face = 0
        self.viewport.set_body_colors(colours)
        self.viewport.set_hidden_bodies(hidden)
        self._legend(mode, kinds)

    def _legend(self, mode: str, kinds: list) -> None:
        if mode == KINDS:
            parts = [f"{_swatch(colour)} {ROLE_GROUPS[role]}" for role, colour in
                     ROLE_COLOUR.items() if colour]
            parts.append("серый — корпус")
        elif mode == TYPES:
            parts = [f"{_swatch(TYPE_COLOURS[index % len(TYPE_COLOURS)])} {title}"
                     for index, title in enumerate(kinds)]
        elif mode == CASES:
            names = self.device.case_names()
            parts = [f"{_swatch(CASE_COLOURS[index % len(CASE_COLOURS)])} {name}"
                     for index, name in enumerate(names)]
            parts.append(f"{_swatch(COMMON_COLOUR)} в нескольких случаях")
            parts.append("серый — не отнесено" if names else "случаев пока нет — «Случаи…»")
        else:
            parts = [f"{_swatch(PROBLEM_COLOUR)} ошибка", f"{_swatch(WARNING_COLOUR)} "
                     f"предупреждение"]
            if self.report is None:
                parts.append("проверки ещё не было — «Проверить»")
        hidden = len(self.device.of_role(EXCLUDED)) if self.device else 0
        if hidden:
            parts.append(f"скрыто исключённых: {hidden}")
        if self.hidden_units:
            parts.append(f"скрыто для обзора: {len(self.hidden_units)}")
        self.legend.setText(" &nbsp; ".join(parts))

    # --- дерево ----------------------------------------------------------------------

    def _fill_tree(self) -> None:
        self.tree.blockSignals(True)
        self.tree.clear()
        self.leaf_items = {}
        if self.device is None:
            self.tree.blockSignals(False)
            return
        wanted = self.search.text().strip().lower()
        mode = self.mode.currentText()
        if mode == NOTES and self.findings:
            self._fill_findings()
        if mode == CASES:
            self._fill_cases(wanted)
        else:
            self._fill_roles(wanted)
        for key in self.selected:
            item = self.leaf_items.get(key)
            if item is not None:
                item.setSelected(True)
        self.tree.blockSignals(False)

    def _matches(self, unit, wanted: str) -> bool:
        return not wanted or wanted in unit.name.lower() or wanted in unit.item.name.lower()

    def _group(self, parent, title: str, units: list, wanted: str, expand=False,
               note: str = ""):
        shown = [unit for unit in units if self._matches(unit, wanted)]
        if not shown:
            return None
        node = QtWidgets.QTreeWidgetItem(parent, [f"{title} ({len(shown)})", "", note])
        node.setData(0, QtCore.Qt.UserRole, ("units", [unit.key for unit in shown]))
        node.setExpanded(expand or bool(wanted))
        return node

    def _leaves(self, parent, units: list, wanted: str) -> None:
        for unit in sorted(units, key=_order):
            if not self._matches(unit, wanted):
                continue
            extra = ", ".join(sorted(unit.cases)) if unit.role == COMPONENT else unit.reason
            leaf = QtWidgets.QTreeWidgetItem(parent, [unit.name, unit.item.name, extra])
            leaf.setToolTip(0, f"{ROLE_TITLES[unit.role]}: {unit.reason}")
            leaf.setData(0, QtCore.Qt.UserRole, ("unit", unit.key))
            self.leaf_items[unit.key] = leaf

    def _fill_board(self, parent, board, units, wanted, expand=True) -> None:
        substrate = [unit for unit in units if unit.role == SUBSTRATE]
        components = [unit for unit in units if unit.role == COMPONENT]
        node = self._group(parent, f"Плата {board.name}", substrate + components, wanted,
                           expand)
        if node is None:
            return
        if substrate:
            self._leaves(node, substrate, wanted)
        by_kind: dict = {}
        for unit in components:
            by_kind.setdefault(self.device.kind_title(unit), []).append(unit)
        for title in sorted(by_kind):
            group = self._group(node, title, by_kind[title], wanted)
            if group is not None:
                self._leaves(group, by_kind[title], wanted)

    def _fill_roles(self, wanted: str) -> None:
        device = self.device
        root = QtWidgets.QTreeWidgetItem(self.tree, [f"{device.name} "
                                                     f"({len(device.units)})", "", ""])
        root.setData(0, QtCore.Qt.UserRole, ("units", list(device.units)))
        root.setExpanded(True)
        for board in device.boards.values():
            units = [unit for unit in device.units.values()
                     if unit.board == board.key and unit.role in (SUBSTRATE, COMPONENT)]
            self._fill_board(root, board, units, wanted)
        for role in (HOUSING, FASTENER, OTHER, EXCLUDED):
            units = device.of_role(role)
            node = self._group(root, ROLE_GROUPS[role], units, wanted, expand=role == HOUSING)
            if node is None:
                continue
            by_kind: dict = {}
            for unit in units:
                by_kind.setdefault(unit.kind, []).append(unit)
            if role in (FASTENER, OTHER) and any(by_kind):
                for kind in sorted(by_kind):
                    group = self._group(node, kind or "без вида", by_kind[kind], wanted)
                    if group is not None:
                        self._leaves(group, by_kind[kind], wanted)
            else:
                self._leaves(node, units, wanted)

    def _fill_cases(self, wanted: str) -> None:
        device = self.device
        components = device.of_role(COMPONENT)
        for case in device.cases:
            units = [unit for unit in components if case.name in unit.cases]
            node = self._group(self.tree, case.name, units, wanted, expand=True, note=case.note)
            if node is None:
                continue
            for board in device.boards.values():
                self._fill_board(node, board, [unit for unit in units
                                               if unit.board == board.key], wanted, expand=False)
        loose = [unit for unit in components if not unit.cases]
        node = self._group(self.tree, "Не отнесены к случаям", loose, wanted, expand=not
                           device.cases)
        if node is not None:
            for board in device.boards.values():
                self._fill_board(node, board, [unit for unit in loose
                                               if unit.board == board.key], wanted, expand=False)
        hint = QtWidgets.QTreeWidgetItem(self.tree, [
            "Корпус, крепёж и прочее — во всех случаях", "", ""])
        hint.setDisabled(True)

    def _fill_findings(self) -> None:
        errors = sum(1 for item in self.findings if item.severity == contacts_module.ERROR)
        node = QtWidgets.QTreeWidgetItem(self.tree, [f"Замечания ({len(self.findings)})", "",
                                                     f"ошибок {errors}"])
        node.setExpanded(True)
        marks = {contacts_module.ERROR: "✖ ", contacts_module.WARNING: "! ",
                 contacts_module.INFO: "· "}
        for index, finding in enumerate(self.findings):
            item = QtWidgets.QTreeWidgetItem(node, [marks[finding.severity] + finding.text,
                                                    "", ""])
            item.setToolTip(0, finding.text)
            item.setData(0, QtCore.Qt.UserRole, ("units", list(finding.units)))
            if finding.severity == contacts_module.ERROR:
                item.setForeground(0, QtGui.QBrush(QtGui.QColor(190, 30, 30)))

    # --- выбор ------------------------------------------------------------------------

    def _tree_selected(self) -> None:
        keys = set()
        for item in self.tree.selectedItems():
            payload = item.data(0, QtCore.Qt.UserRole)
            if not payload:
                continue
            kind, value = payload
            keys.update([value] if kind == "unit" else value)
        self.selected = {key for key in keys if key in self.device.units}
        self._paint()
        self._describe_selection()

    def _describe_selection(self) -> None:
        if len(self.selected) == 1:
            unit = self.device.units[next(iter(self.selected))]
            material = thermal.material_of(unit)
            text = (f"{unit.name}: {ROLE_TITLES[unit.role]}"
                    + (f", {self.device.kind_title(unit)}" if unit.kind else "")
                    + f"; изделие «{unit.item.name}»; материал {material.name}"
                    + (f"; случаи: {', '.join(sorted(unit.cases))}" if unit.cases else "")
                    + f"; почему: {unit.reason}")
            self.statusBar().showMessage(text, 15000)
        elif self.selected:
            self.statusBar().showMessage(f"выбрано единиц: {len(self.selected)}", 8000)

    def _picked(self, what: dict) -> None:
        if self.device is None:
            return
        adding = bool(QtWidgets.QApplication.keyboardModifiers() & QtCore.Qt.ControlModifier)
        key = ""
        if what.get("kind") == "face" and self.preview is not None:
            entry = self.preview.face_to_object.get(int(what.get("id", 0)))
            if entry is not None:
                key = self.body_units.get(entry["body"], "")
        if not key:
            if not adding:
                self._clear_selection()
            return
        if adding:
            self.selected ^= {key}
        else:
            self.selected = {key}
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        for chosen in self.selected:
            item = self.leaf_items.get(chosen)
            if item is not None:
                item.setSelected(True)
                self.tree.scrollToItem(item)
        self.tree.blockSignals(False)
        self._paint()
        self._describe_selection()

    def _clear_selection(self) -> None:
        self.selected = set()
        self.tree.clearSelection()
        self._paint()

    def _tree_menu(self, position) -> None:
        if self.device is None or not self.selected:
            return
        units = [self.device.units[key] for key in sorted(self.selected)]
        menu = QtWidgets.QMenu(self)
        roles = menu.addMenu(f"Роль ({len(units)})")
        for role in (COMPONENT, SUBSTRATE, HOUSING, FASTENER, OTHER, EXCLUDED):
            if role in (COMPONENT, SUBSTRATE) and len(self.device.boards) > 1:
                sub = roles.addMenu(ROLE_TITLES[role])
                for board in self.device.boards.values():
                    sub.addAction(f"плата {board.name}", lambda role=role, key=board.key:
                                  self._set_role(units, role, key))
            else:
                roles.addAction(ROLE_TITLES[role], lambda role=role: self._set_role(units, role))
        menu.addAction("Вид…", lambda: self._set_kind(units))
        materials = menu.addMenu("Материал")
        for name, material in thermal.MATERIALS.items():
            materials.addAction(f"{name} (λ = {material.conductivity:g})",
                                lambda name=name: self._change(
                                    lambda: self.device.set_material(units, name)))
        cases = menu.addMenu("Расчётный случай")
        for name in self.device.case_names():
            cases.addAction(f"Включить в «{name}»", lambda name=name: self._change(
                lambda: self.device.assign(units, name, True)))
            cases.addAction(f"Исключить из «{name}»", lambda name=name: self._change(
                lambda: self.device.assign(units, name, False)))
        if self.device.case_names():
            cases.addSeparator()
        cases.addAction("Новый случай…", lambda: self._new_case(units))
        menu.addSeparator()
        menu.addAction("Скрыть (для обзора)", lambda: self._hide(units))
        if self.hidden_units:
            menu.addAction("Показать всё", self._show_all)
        if len(units) == 1:
            unit = units[0]
            parent = "/".join(unit.path[:-1])
            if isinstance(unit.item, Assembly):
                menu.addAction("Разобрать на детали",
                               lambda: self._granularity(unit.key, split=True))
            elif parent and parent not in self.device.boards:
                menu.addAction("Считать узел одной деталью",
                               lambda: self._granularity(parent, split=False))
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _hide(self, units) -> None:
        self.hidden_units |= {unit.key for unit in units}
        self.selected -= self.hidden_units
        self._paint()

    def _show_all(self) -> None:
        self.hidden_units = set()
        self.hide_housing.setChecked(False)
        self._paint()

    def _change(self, action) -> None:
        self._push()
        action()
        self._fill_tree()
        self._paint()
        self._enable()
        self._title()

    def _set_role(self, units, role, board="") -> None:
        self._change(lambda: self.device.set_role(units, role, board))
        self._say(f"Роль «{ROLE_TITLES[role]}» — единиц {len(units)}.")

    def _set_kind(self, units) -> None:
        text, ok = QtWidgets.QInputDialog.getText(self, "Вид", "Вид (например, винт, втулка):",
                                                  text=units[0].kind)
        if ok:
            self._change(lambda: self.device.set_kind(units, text.strip()))

    def _new_case(self, units) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, "Новый случай", "Имя случая:",
                                                  text=f"Случай {len(self.device.cases) + 1}")
        if not ok:
            return
        try:
            self._push()
            self.device.add_case(name)
            self.device.assign(units, name.strip())
        except ValueError as failure:
            QtWidgets.QMessageBox.warning(self, "Случай", str(failure))
        self._fill_tree()
        self._paint()
        self._enable()

    def _granularity(self, key: str, split: bool) -> None:
        self._push()
        if split:
            self.device.split.add(key)
            self.device.whole.discard(key)
        else:
            self.device.whole.add(key)
            self.device.split.discard(key)
        self.device.build_units()
        for unit in self.device.units.values():
            if not unit.reason:
                unit.reason = "после разбора узла — проверьте роль"
        self.selected = set()
        self.report, self.findings, self.rows = None, [], []
        self._rebuild_scene()
        self._fill_tree()
        self._fill_contacts()
        self._enable()

    # --- контакты ---------------------------------------------------------------------

    CONTACT_COLUMNS = ("№", "Деталь 1", "Деталь 2", "Вид", "S, мм²", "Слой", "t, мм",
                       "λ, Вт/(м·К)", "r, м²·К/Вт", "h, Вт/(м²·К)", "R, К/Вт")

    def _build_contacts_dock(self) -> None:
        self.contacts_dock = QtWidgets.QDockWidget("Тепловые контакты", self)
        self.contacts_dock.setObjectName("contacts")
        body = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(body)
        row = QtWidgets.QHBoxLayout()
        self.contact_filter = QtWidgets.QComboBox()
        self.contact_filter.addItems(["Все", "Компоненты с платой", "Кроме компонентов",
                                      "Зазоры"])
        self.contact_filter.currentIndexChanged.connect(lambda _index: self._fill_contacts())
        row.addWidget(self.contact_filter)
        layer = QtWidgets.QPushButton("Слой выбранным…")
        layer.clicked.connect(self._set_layer)
        row.addWidget(layer)
        self.contact_summary = QtWidgets.QLabel("")
        row.addWidget(self.contact_summary, 1)
        layout.addLayout(row)
        self.contact_table = QtWidgets.QTableWidget(0, len(self.CONTACT_COLUMNS))
        self.contact_table.setHorizontalHeaderLabels(self.CONTACT_COLUMNS)
        self.contact_table.verticalHeader().setVisible(False)
        self.contact_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.contact_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.contact_table.itemSelectionChanged.connect(self._contact_selected)
        layout.addWidget(self.contact_table)
        self.contacts_dock.setWidget(body)
        self.addDockWidget(QtCore.Qt.BottomDockWidgetArea, self.contacts_dock)
        self.contacts_dock.hide()

    def _show_contacts(self) -> None:
        if not self._need_report():
            return
        self.contacts_dock.show()
        self._fill_contacts()

    def _fill_contacts(self) -> None:
        table = self.contact_table
        table.setRowCount(0)
        if self.device is None or self.report is None:
            self.rows = []
            self.contact_summary.setText("проверки ещё не было")
            return
        self.rows = thermal.table(self.device, self.report)
        by_name = {unit.name: unit for unit in self.device.units.values()}
        choice = self.contact_filter.currentIndex()

        def shown(row):
            roles = {by_name[row.first].role, by_name[row.second].role} \
                if row.first in by_name and row.second in by_name else set()
            if choice == 1:
                return roles == {COMPONENT, SUBSTRATE}
            if choice == 2:
                return COMPONENT not in roles
            if choice == 3:
                return row.kind == contacts_module.GAP
            return True

        visible = [row for row in self.rows if shown(row)]
        table.setRowCount(len(visible))
        for index, row in enumerate(visible):
            values = (row.number, row.first, row.second, row.kind, f"{row.area:.4g}",
                      row.interface, f"{row.thickness:.3g}", f"{row.conductivity:g}",
                      f"{row.r:.3g}", f"{row.h:.4g}", f"{row.resistance:.4g}")
            for column, value in enumerate(values):
                cell = QtWidgets.QTableWidgetItem(str(value))
                cell.setData(QtCore.Qt.UserRole, row.key)
                table.setItem(index, column, cell)
        table.resizeColumnsToContents()
        groups = thermal.groups(self.rows)
        self.contact_summary.setText(f"контактов и зазоров {len(self.rows)}, разных "
                                     f"сопротивлений {len(groups)} — столько условий "
                                     f"Contact Resistance в Flow Simulation")

    def _selected_contact_keys(self) -> list:
        keys = []
        for index in sorted({item.row() for item in self.contact_table.selectedItems()}):
            cell = self.contact_table.item(index, 0)
            if cell is not None:
                keys.append(cell.data(QtCore.Qt.UserRole))
        return keys

    def _contact_selected(self) -> None:
        keys = self._selected_contact_keys()
        self.selected = {unit for key in keys for unit in key.split("|")
                         if unit in self.device.units}
        self._paint()

    def _set_layer(self) -> None:
        keys = self._selected_contact_keys()
        if not keys:
            self._say("Выберите строки контактов (Ctrl, Shift — несколько).")
            return
        dialog = LayerDialog(self)
        if not dialog.exec():
            return
        name, thickness = dialog.values()
        self._push()
        thermal.set_interface(self.device, keys, name, thickness)
        self._fill_contacts()
        self._say(f"Слой «{name}» задан контактам: {len(keys)}.")


class LayerDialog(QtWidgets.QDialog):
    """Слой в контакте: из библиотеки, толщина — своя или по умолчанию."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Слой в контакте")
        layout = QtWidgets.QFormLayout(self)
        self.layer = QtWidgets.QComboBox()
        for name, item in thermal.INTERFACES.items():
            thickness = "по зазору" if item.thickness is None else f"{item.thickness:g} мм"
            self.layer.addItem(f"{name} — λ {item.conductivity:g}, {thickness}", name)
        self.own = QtWidgets.QCheckBox("своя толщина")
        self.thickness = QtWidgets.QDoubleSpinBox()
        self.thickness.setRange(0.001, 50.0)
        self.thickness.setDecimals(3)
        self.thickness.setValue(0.1)
        self.thickness.setSuffix(" мм")
        layout.addRow("Слой", self.layer)
        layout.addRow(self.own, self.thickness)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def values(self) -> tuple:
        return (self.layer.currentData(),
                self.thickness.value() if self.own.isChecked() else None)


def _order(unit):
    return unit.designator.sort_key if unit.designator is not None else ("", unit.name, 0, "")


def _stem(name: str) -> str:
    return "".join(character if character.isalnum() or character in "-_." else "_"
                   for character in name).strip("_") or "прибор"
