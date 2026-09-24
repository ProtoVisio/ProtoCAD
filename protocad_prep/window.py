"""Окно подготовки к расчёту.

Всё, что окно делает с моделью, — вызовы `protocad.prep`, те же, что у
рецепта и командной строки. Своей геометрии у окна нет: оно показывает
исследование, собирает выбор граней и отдаёт шаги. Поэтому то, что
сделано в окне, записывается рецептом и повторяется без окна.

Отмена — снимками исследования: формы ядра неизменяемы, снимок стоит
копирования списков, а не геометрии.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PySide6 import QtCore, QtGui, QtWidgets

from protocad import prep
from protocad.prep import io as prep_io
from protocad.prep import recipe as prep_recipe
from protocad.prep.display import scene
from protocad.prep.model import BODY_GROUP, ERROR, FACE_GROUP, WARNING
from protocad_gl import SceneBuffers, Viewport
from protocad_gl.viewport import PALETTE, PROBLEM_COLOUR, SELECTION_COLOUR

from . import dialogs

OPEN_FILTER = ("Геометрия (*.step *.stp *.iges *.igs *.brep *.brp *.prcadAsm "
               "*.prcadPart);;Все файлы (*)")


def _swatch(colour) -> QtGui.QIcon:
    pixmap = QtGui.QPixmap(14, 14)
    pixmap.fill(QtGui.QColor.fromRgbF(*colour))
    return QtGui.QIcon(pixmap)


class _MeshWorker(QtCore.QThread):
    """Сетка в фоне: построитель работает минуты, окно не должно замирать."""

    done = QtCore.Signal(object)

    def __init__(self, study, spec, outputs, scale):
        super().__init__()
        self.arguments = (study, spec, outputs, scale)

    def run(self) -> None:
        study, spec, outputs, scale = self.arguments
        try:
            report = prep.mesh(study, spec, outputs, scale=scale, preview=True)
        except Exception as failure:  # noqa: BLE001 — показать, а не уронить окно
            report = prep.Report("mesh")
            report.fail("MESH_CRASHED", f"{type(failure).__name__}: {failure}")
        self.done.emit(report)


class PrepWindow(QtWidgets.QMainWindow):
    """Подготовка геометрии к расчёту."""

    def __init__(self, study=None, folder: str = ""):
        super().__init__()
        self.study = None
        self.folder = folder or str(Path.cwd())
        self.selected: set = set()
        self.problem_faces: set = set()
        self.undo_stack: list = []
        self.local_sizes: dict = {}
        self.worker = None
        #: Треугольники последней сетки. Сбрасываются любой правкой модели:
        #: сетка от прежней геометрии — неправда о нынешней.
        self.mesh_triangles = None
        self.resize(1500, 920)

        self.viewport = Viewport()
        self.viewport.pick_kinds = ("face",)
        self.viewport.selected.connect(self._picked)

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Модель", ""])
        self.tree.setColumnWidth(0, 260)
        self.tree.itemClicked.connect(self._tree_clicked)
        self.tree.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)

        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Здесь — итог последнего шага.")

        side = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        side.addWidget(self.tree)
        side.addWidget(self.output)
        side.setSizes([560, 300])
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.addWidget(side)
        splitter.addWidget(self.viewport)
        splitter.setSizes([380, 1120])
        self.setCentralWidget(splitter)

        self.selection_label = QtWidgets.QLabel("")
        self.statusBar().addPermanentWidget(self.selection_label)
        self._build_actions()
        self._set_study(study)

    # --- команды ---------------------------------------------------------

    def _build_actions(self) -> None:
        bar = self.addToolBar("Подготовка")
        bar.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
        bar.setMovable(False)
        self.needs_model = []

        self.needs_idle = []

        def action(title, handler, tip="", shortcut=None, model=True, menu=None,
                   idle=True):
            item = QtGui.QAction(title, self)
            item.setToolTip(tip or title)
            item.setStatusTip(tip)
            if shortcut:
                item.setShortcut(QtGui.QKeySequence(shortcut))
            item.triggered.connect(handler)
            if model:
                self.needs_model.append(item)
            elif idle:
                # Пока строится сетка, модель подменять нельзя: построитель
                # работает с ней в фоне, и итог лёг бы на чужую модель.
                self.needs_idle.append(item)
            (menu or bar).addAction(item)
            return item

        files = self.menuBar().addMenu("Файл")
        action("Открыть…", self._open, "STEP, IGES, BREP или сборка ProtoCAD",
               QtGui.QKeySequence.Open, model=False, menu=files)
        action("Образец: кронштейн", self._demo, model=False, menu=files)
        files.addSeparator()
        action("Сохранить геометрию…", self._export, "STEP с именами тел, BREP или STL",
               menu=files)
        action("Сохранить рецепт…", self._save_recipe,
               "шаги этой подготовки — чтобы повторить на новой версии", menu=files)
        action("Прогнать рецепт…", self._run_recipe, model=False, menu=files)
        files.addSeparator()
        action("Закрыть", self.close, model=False, menu=files, idle=False)

        edit = self.menuBar().addMenu("Правка")
        self.undo_action = action("Отменить", self._undo, "вернуть модель до шага",
                                  QtGui.QKeySequence.Undo, menu=edit)
        action("Снять выбор", self._clear_selection, "", "Esc", menu=edit)

        action("Открыть", self._open, "STEP, IGES, BREP или сборка ProtoCAD",
               model=False)
        bar.addSeparator()
        action("Проверить", self._check, "найти то, на чём споткнётся сетка", "F5")
        action("Исправить", self._heal, "сшить, починить, слить лишние грани")
        action("Упростить", self._defeature, "убрать мелкие отверстия и скругления")
        bar.addSeparator()
        action("Симметрия", self._cut, "оставить половину модели")
        action("Разделить", self._split, "разрезать тело на склеенные части")
        action("Склеить", self._glue, "сделать общие грани стыков")
        action("Область течения", self._enclosure, "воздух вокруг модели для CFD")
        bar.addSeparator()
        action("Группа граней", self._group_faces,
               "выбранные грани — в группу (граничное условие)", "Ctrl+G")
        action("Материал", self._group_bodies,
               "тела выбранных граней — в группу тел")
        bar.addSeparator()
        action("Сетка", self._mesh, "построить сетку и записать файл решателя",
               "Ctrl+M")
        bar.addSeparator()
        action("Отменить", self._undo, "вернуть модель до шага")

        view = self.menuBar().addMenu("Вид")
        action("Вписать", self.viewport.fit_view, "", "F", menu=view)
        self.mesh_action = QtGui.QAction("Показывать сетку", self, checkable=True)
        self.mesh_action.setShortcut(QtGui.QKeySequence("Ctrl+Shift+M"))
        self.mesh_action.toggled.connect(lambda _on: self._refresh())
        view.addAction(self.mesh_action)
        edges = QtGui.QAction("Рёбра", self, checkable=True, checked=True)
        edges.toggled.connect(self._toggle_edges)
        view.addAction(edges)

    def _toggle_edges(self, on: bool) -> None:
        self.viewport.show_edges = on
        self.viewport.update()

    def _enable(self) -> None:
        busy = self.worker is not None
        for item in self.needs_model:
            item.setEnabled(self.study is not None and not busy)
        for item in self.needs_idle:
            item.setEnabled(not busy)
        self.undo_action.setEnabled(bool(self.undo_stack) and not busy)
        # Показ сетки перестраивает сцену, а это чтение той же геометрии,
        # которую в фоне пишет построитель.
        self.mesh_action.setEnabled(self.mesh_triangles is not None and not busy)

    # --- исследование -----------------------------------------------------

    def _set_study(self, study, keep_undo: bool = False) -> None:
        self.study = study
        if not keep_undo:
            self.undo_stack = []
        self.selected = set()
        self.problem_faces = set()
        self._forget_mesh()
        self._refresh(fit=True)
        title = f"ProtoCAD — подготовка к расчёту"
        if study is not None:
            title += f" — {study.name}"
        self.setWindowTitle(title)

    def _open(self) -> None:
        name, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Открыть геометрию", self.folder, OPEN_FILTER)
        if name:
            self.open_path(name)

    def open_path(self, path) -> bool:
        path = Path(path)
        self.folder = str(path.parent)
        try:
            with self._busy():
                study = prep_io.load(path)
        except Exception as failure:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "Открыть", str(failure))
            return False
        self._set_study(study)
        self._say(f"Открыто: {path.name} — тел {len(study.bodies)}, граней "
                  f"{study.face_count}. Начните с «Проверить».")
        return True

    def _demo(self) -> None:
        from protocad.prep import demo

        study = prep.Study(name="Кронштейн")
        study.add_body(demo.bracket(), "Кронштейн")
        self._set_study(study)
        self._say("Образец: кронштейн с крепёжными отверстиями Ø9, отверстием "
                  "под вал Ø20 и скруглениями R2.")

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

    def _step(self, run, *args, **kwargs):
        """Выполнить шаг с отменой. Отказ модель не меняет — снимок не нужен."""
        if self.study is None:
            return None
        before = self.study.snapshot()
        try:
            with self._busy():
                report = run(self.study, *args, **kwargs)
        except Exception as failure:  # noqa: BLE001 — показать, а не уронить окно
            self.study.restore(before)
            QtWidgets.QMessageBox.critical(self, "ProtoCAD",
                                           f"{type(failure).__name__}: {failure}")
            return None
        reshaped = report.ok and report.op not in ("check", "group", "ungroup")
        if report.ok and report.op != "check":
            self.undo_stack.append(before)
            # Номера граней после шага — уже другие грани. Оставленный выбор
            # подсветил бы и отдал в следующую команду случайные грани.
            self.selected = set()
            self.problem_faces = set()
        if reshaped:
            self._forget_mesh()
        self._show_report(report)
        # Разбивка на треугольники — самое долгое в показе, и после шагов,
        # не менявших геометрию, она не нужна: хватает перекраски.
        self._refresh(rebuild=reshaped)
        return report

    def _forget_mesh(self) -> None:
        self.mesh_triangles = None
        self.mesh_action.blockSignals(True)
        self.mesh_action.setChecked(False)
        self.mesh_action.blockSignals(False)

    def _undo(self) -> None:
        if not self.undo_stack:
            return
        self.study.restore(self.undo_stack.pop())
        self.selected = set()
        self._forget_mesh()
        self._refresh()
        self._say("Отменено: модель как до последнего шага.")

    # --- шаги -------------------------------------------------------------

    def _check(self) -> None:
        report = self._step(prep.check)
        if report is not None:
            self.problem_faces = {face for item in report.findings for face in item.faces}
            self._paint()

    def _heal(self) -> None:
        dialog = dialogs.HealDialog(self)
        if dialog.exec():
            self._step(prep.heal, **dialog.values())

    def _defeature(self) -> None:
        dialog = dialogs.DefeatureDialog(len(self.selected), self)
        if not dialog.exec():
            return
        values = dialog.values()
        faces = sorted(self.selected) if values.pop("use_selected") else []
        self._step(prep.defeature, faces=faces, **values)
        self.selected = set()
        self._paint()

    def _plane_of_selection(self):
        """Плоскость выбранной плоской грани — чтобы не вводить числа."""
        if len(self.selected) != 1:
            return None
        data = prep.describe(self.study.face(next(iter(self.selected))))
        if data["type"] != "plane":
            return None
        return data["center"], data["normal"]

    def _cut(self) -> None:
        plane = self._plane_of_selection()
        centre = self.study.bounds().center
        origin, normal = plane if plane else (centre, (1.0, 0.0, 0.0))
        dialog = dialogs.PlaneDialog(
            "Симметрия", "Оставить часть модели по одну сторону плоскости. "
            "Выбранная плоская грань задаёт плоскость сама.",
            origin, normal, keep=True, group="symmetry", parent=self)
        if dialog.exec():
            self._step(prep.cut_by_plane, **dialog.values())

    def _split(self) -> None:
        plane = self._plane_of_selection()
        centre = self.study.bounds().center
        origin, normal = plane if plane else (centre, (1.0, 0.0, 0.0))
        dialog = dialogs.PlaneDialog(
            "Разделить", "Разрезать тела на части, склеенные по разрезу: сетка "
            "по обе стороны совпадёт.", origin, normal, keep=False, group="",
            parent=self)
        if dialog.exec():
            self._step(prep.split_by_plane, **dialog.values())

    def _glue(self) -> None:
        self._step(prep.glue)

    def _enclosure(self) -> None:
        size = max(self.study.bounds().size)
        dialog = dialogs.EnclosureDialog(size, self)
        if dialog.exec():
            self._step(prep.enclosure, **dialog.values())

    def _group_faces(self) -> None:
        if not self.selected:
            self._say("Сначала выберите грани: щелчок — грань, Ctrl+щелчок — "
                      "добавить или убрать.")
            return
        names = [name for name, group in self.study.groups.items()
                 if group.kind == FACE_GROUP]
        dialog = dialogs.GroupDialog(
            "Группа граней", f"Выбрано граней: {len(self.selected)}. Группа — это "
            f"граничное условие: закрепление, давление, вход потока.", names, self)
        if not dialog.exec():
            return
        values = dialog.values()
        rule = prep.picked_rule(self.study, sorted(self.selected))
        self._step(prep.make_group, values["name"], rule=rule, add=values["add"])
        self.selected = set()
        self._paint()

    def _group_bodies(self) -> None:
        owners = sorted({name for face in self.selected for name in self.study.owners(face)})
        if not owners:
            owners = [body.name for body in self.study.bodies]
        names = [name for name, group in self.study.groups.items()
                 if group.kind == BODY_GROUP]
        dialog = dialogs.GroupDialog(
            "Материал", "Тела: " + ", ".join(owners) + ". Группа тел — это "
            "материал: в файле решателя она станет набором элементов.", names, self)
        if dialog.exec():
            values = dialog.values()
            self._step(prep.make_group, values["name"], kind=BODY_GROUP,
                       bodies=owners, add=values["add"])

    def _mesh(self) -> None:
        if self.worker is not None:
            return
        python, reason = prep.find_python()
        if python is None:
            QtWidgets.QMessageBox.warning(self, "Сетка", reason)
            return
        faces = [name for name, group in self.study.groups.items()
                 if group.kind == FACE_GROUP]
        dialog = dialogs.MeshDialog(
            self.study.diagonal() / 20.0, faces, self.local_sizes, self.study.glued,
            len(self.study.bodies), self.folder, _file_stem(self.study.name), self)
        if not dialog.exec():
            return
        values = dialog.values()
        self.local_sizes.update(values["local"])
        spec = prep.MeshSpec(size=values["size"], order=values["order"],
                             algorithm=values["algorithm"],
                             curvature=values["curvature"], local=values["local"])
        self.worker = _MeshWorker(self.study, spec, values["outputs"], values["scale"])
        self.worker.done.connect(self._meshed)
        self._enable()
        self.statusBar().showMessage("Строю сетку… окно можно вертеть")
        self.worker.start()

    def _meshed(self, report) -> None:
        worker, self.worker = self.worker, None
        worker.wait()
        if worker.arguments[0] is not self.study:
            self._enable()
            return
        self.statusBar().clearMessage()
        self._show_report(report)
        stats = report.after.get("stats") if report.ok else None
        if stats:
            lines = [f"узлов: {stats['nodes']}"]
            lines += [f"{kind}: {count}" for kind, count in stats["elements"].items()]
            lines += [f"группа {name}: {count} эл." for name, count in stats["groups"].items()]
            lines += [f"записано: {path}" for path in report.after.get("files", ())]
            self.output.appendPlainText("\n".join(lines))
        if report.preview is not None:
            self.mesh_triangles = report.preview["triangles"]
            self.mesh_action.blockSignals(True)
            self.mesh_action.setChecked(True)
            self.mesh_action.blockSignals(False)
        self._refresh()

    # --- файлы ------------------------------------------------------------

    def _export(self) -> None:
        name, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Сохранить геометрию", str(Path(self.folder) / f"{_file_stem(self.study.name)}.step"),
            "STEP (*.step);;BREP (*.brep);;STL (*.stl)")
        if not name:
            return
        report = prep_recipe.run_step(self.study, {"op": "export", "path": name},
                                      self.folder)
        self._show_report(report)
        self._refresh()

    def _save_recipe(self) -> None:
        name, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Сохранить рецепт",
            str(Path(self.folder) / f"{_file_stem(self.study.name)}.prep.json"),
            "Рецепт (*.json)")
        if not name:
            return
        data = prep_recipe.record(self.study, base=Path(name).parent)
        prep_recipe.save(data, name)
        picked = [step for step in data["steps"]
                  if step["op"] == "group" and "near" in (step.get("rule") or {})]
        note = ""
        if picked:
            note = (f"\nГрупп, выбранных мышью: {len(picked)}. Они записаны точками "
                    f"на гранях и найдутся, пока грани не сдвинулись. Для "
                    f"изменчивой геометрии замените их правилом в файле.")
        self._say(f"Рецепт сохранён: {Path(name).name}, шагов {len(data['steps'])}.{note}")

    def _run_recipe(self) -> None:
        name, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Прогнать рецепт", self.folder, "Рецепт (*.json)")
        if not name:
            return
        try:
            with self._busy():
                study, reports = prep_recipe.run(name)
        except Exception as failure:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "Рецепт", str(failure))
            return
        self.folder = str(Path(name).parent)
        self._set_study(study)
        self.output.setPlainText("\n\n".join(
            f"[{number}] {report.text()}" for number, report in enumerate(reports, 1)))
        failed = [report for report in reports if not report.ok]
        self.statusBar().showMessage(
            "Рецепт выполнен" if not failed else f"Рецепт остановлен: {failed[0].message}",
            10000)

    # --- показ ------------------------------------------------------------

    def _say(self, text: str) -> None:
        self.output.setPlainText(text)
        self.statusBar().showMessage(text.split("\n")[0], 8000)

    def _show_report(self, report) -> None:
        self.output.setPlainText(report.text())
        self.statusBar().showMessage(report.text().split("\n")[0], 10000)

    def _refresh(self, fit: bool = False, rebuild: bool = True) -> None:
        showing_mesh = (self.mesh_triangles is not None
                        and self.mesh_action.isChecked())
        self.viewport.pick_kinds = () if showing_mesh else ("face",)
        if not rebuild:
            pass
        elif self.study is None:
            self.viewport.set_scene(SceneBuffers.empty_scene())
        elif showing_mesh:
            self.viewport.set_scene(_mesh_scene(self.mesh_triangles))
        else:
            data = scene(self.study)
            labels = {index + 1: body.name for index, body in enumerate(self.study.bodies)}
            faces = np.unique(data["face_ids"])
            buffers = SceneBuffers(
                positions=data["positions"], normals=data["normals"],
                ids=data["body_ids"], edge_positions=data["edge_positions"],
                edge_ids=np.ones(len(data["edge_positions"]), np.uint32),
                labels=labels, face_ids=data["face_ids"],
                face_labels={int(value): {"label": f"Грань {int(value) - 1}",
                                          "face": int(value) - 1} for value in faces},
                edge_indices=data["edge_ids"])
            self.viewport.set_scene(buffers)
            if fit:
                self.viewport.fit_view()
        self.selected = {face for face in self.selected
                         if self.study is not None and face < self.study.face_count}
        self._paint()
        self._fill_tree()
        self._enable()

    def _group_colour(self, name: str) -> int:
        order = [key for key, group in self.study.groups.items()
                 if group.kind == FACE_GROUP]
        return order.index(name) % 8 + 1 if name in order else 0

    def _paint(self) -> None:
        colours = {}
        if self.study is not None:
            for name, group in self.study.groups.items():
                if group.kind != FACE_GROUP:
                    continue
                colour = self._group_colour(name)
                for face in group.faces:
                    colours.setdefault(face + 1, colour)
            for face in self.problem_faces:
                colours[face + 1] = PROBLEM_COLOUR
            for face in self.selected:
                colours[face + 1] = SELECTION_COLOUR
        self.viewport.highlight_face = 0
        self.viewport.set_face_colors(colours)
        self.selection_label.setText(
            f"выбрано граней: {len(self.selected)}" if self.selected else "")

    def _fill_tree(self) -> None:
        self.tree.clear()
        if self.study is None:
            return
        study = self.study
        bodies = QtWidgets.QTreeWidgetItem(self.tree, [f"Тела ({len(study.bodies)})", ""])
        for body in study.bodies:
            kind = f"{body.volume:.6g} мм³" if body.is_solid else "поверхность"
            item = QtWidgets.QTreeWidgetItem(bodies, [body.name, kind])
            item.setData(0, QtCore.Qt.UserRole, ("body", body.name))
        groups = QtWidgets.QTreeWidgetItem(self.tree, [f"Группы ({len(study.groups)})", ""])
        for name, group in study.groups.items():
            if group.kind == FACE_GROUP:
                text = f"{group.size} гр."
                if name in self.local_sizes:
                    text += f", сетка {self.local_sizes[name]:.3g}"
                item = QtWidgets.QTreeWidgetItem(groups, [name, text])
                item.setIcon(0, _swatch(PALETTE[self._group_colour(name) - 1]))
            else:
                item = QtWidgets.QTreeWidgetItem(groups, [name, f"тела: {group.size}"])
                item.setToolTip(0, ", ".join(sorted(group.bodies)))
            item.setData(0, QtCore.Qt.UserRole, ("group", name))
        checks = [report for report in study.log if report.op == "check"]
        if checks:
            last = checks[-1]
            node = QtWidgets.QTreeWidgetItem(
                self.tree, [f"Замечания ({len(last.findings)})",
                            "проверка" if last.ok else "есть ошибки"])
            for index, finding in enumerate(last.findings):
                mark = {ERROR: "✖ ", WARNING: "! "}.get(finding.severity, "· ")
                item = QtWidgets.QTreeWidgetItem(node, [mark + finding.message, ""])
                item.setToolTip(0, finding.message)
                item.setData(0, QtCore.Qt.UserRole, ("finding", index))
            node.setExpanded(True)
        log = QtWidgets.QTreeWidgetItem(self.tree, [f"Журнал ({len(study.log)})", ""])
        for report in study.log:
            item = QtWidgets.QTreeWidgetItem(log, [report.op, "готово" if report.ok
                                                   else "ОТКАЗ"])
            item.setToolTip(0, report.text())
            item.setData(0, QtCore.Qt.UserRole, ("log", id(report)))
        bodies.setExpanded(True)
        groups.setExpanded(True)

    # --- выбор ------------------------------------------------------------

    def _picked(self, what: dict) -> None:
        if self.study is None:
            return
        adding = bool(QtWidgets.QApplication.keyboardModifiers()
                      & QtCore.Qt.ControlModifier)
        if what.get("kind") != "face":
            if not adding:
                self._clear_selection()
            return
        face = int(what.get("index", -1))
        if face < 0:
            return
        if adding:
            self.selected ^= {face}
        else:
            self.selected = {face}
        self.problem_faces = set()
        self._paint()
        data = prep.describe(self.study.face(face))
        owners = ", ".join(self.study.owners(face))
        groups = ", ".join(self.study.groups_of_face(face)) or "нет"
        detail = f"грань {face}: {data['type']}, {data['area']:.4g} мм²"
        if "radius" in data:
            detail += f", R{data['radius']:.4g}"
        self.statusBar().showMessage(f"{detail}; тело: {owners}; группы: {groups}", 8000)

    def _clear_selection(self) -> None:
        self.selected = set()
        self.problem_faces = set()
        self._paint()

    def _tree_clicked(self, item, _column) -> None:
        payload = item.data(0, QtCore.Qt.UserRole)
        if not payload or self.study is None:
            return
        kind, key = payload
        if kind == "body":
            self.selected = set(self.study.faces_of(key))
            self.problem_faces = set()
        elif kind == "group":
            group = self.study.groups.get(key)
            if group is None:
                return
            if group.kind == FACE_GROUP:
                self.selected = set(group.faces)
            else:
                self.selected = {face for name in group.bodies
                                 for face in self.study.faces_of(name)}
            self.problem_faces = set()
        elif kind == "finding":
            checks = [report for report in self.study.log if report.op == "check"]
            finding = checks[-1].findings[key]
            self.selected = set()
            self.problem_faces = set(finding.faces)
            for name in finding.bodies:
                if not finding.faces:
                    self.problem_faces |= set(self.study.faces_of(name))
            self.viewport.set_edge_selection(finding.edges)
            self.output.setPlainText(finding.message)
        elif kind == "log":
            for report in self.study.log:
                if id(report) == key:
                    self.output.setPlainText(report.text())
            return
        self._paint()

    def _tree_menu(self, position) -> None:
        item = self.tree.itemAt(position)
        payload = item.data(0, QtCore.Qt.UserRole) if item else None
        if not payload or payload[0] != "group":
            return
        name = payload[1]
        menu = QtWidgets.QMenu(self)
        group = self.study.groups.get(name)
        if group is not None and group.kind == FACE_GROUP:
            menu.addAction("Размер сетки у группы…",
                           lambda: self._local_size(name))
        menu.addAction("Удалить группу", lambda: self._step(prep.drop_group, name))
        menu.exec(self.tree.viewport().mapToGlobal(position))

    def _local_size(self, name: str) -> None:
        value = dialogs.ask_number(self, "Размер сетки", f"Размер элемента у «{name}», мм "
                                   f"(0 — как везде)", self.local_sizes.get(name, 0.0))
        if value is None:
            return
        if value > 0:
            self.local_sizes[name] = value
        else:
            self.local_sizes.pop(name, None)
        self._fill_tree()


def _mesh_scene(triangles) -> SceneBuffers:
    """Сетка как сцена: треугольники поверхности и их рёбра."""
    positions = np.ascontiguousarray(triangles.reshape(-1, 3), dtype=np.float32)
    a, b, c = triangles[:, 0], triangles[:, 1], triangles[:, 2]
    normals = np.cross(b - a, c - a)
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    lengths[lengths == 0.0] = 1.0
    normals = np.repeat((normals / lengths).astype(np.float32), 3, axis=0)
    edges = np.stack([a, b, b, c, c, a], axis=1).reshape(-1, 3).astype(np.float32)
    return SceneBuffers(positions=positions, normals=normals,
                        ids=np.ones(len(positions), np.uint32),
                        edge_positions=edges,
                        edge_ids=np.ones(len(edges), np.uint32),
                        labels={1: "сетка"})


def _file_stem(name: str) -> str:
    return "".join(character if character.isalnum() or character in "-_." else "_"
                   for character in name) or "model"
