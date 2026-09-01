"""Просмотрщик файлов ProtoCAD — то, что встраивается в ПРОТО.

    python protocad_viewer/app.py work/АБВГ.687281.001.prcadAsm

Показывает модель, состав, спецификацию и чертежи из одного контейнера.
**Ни FreeCAD, ни OCCT не требуются** — всё берётся из ``preview/`` и
``structure.json``, которые ProtoCAD положил внутрь файла.

Выбор связан в обе стороны: тычок в тело подсвечивает строку состава, выбор
в дереве подсвечивает тело.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
from OpenGL import GL
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtOpenGLWidgets import QOpenGLWidget

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from protocad_reader import ReaderError, open_container  # noqa: E402

VERTEX_SHADER = """
#version 330 core
layout(location = 0) in vec3 in_position;
layout(location = 1) in vec3 in_normal;
layout(location = 2) in uint in_id;
uniform mat4 mvp;
uniform mat3 normal_matrix;
out vec3 v_normal;
flat out uint v_id;
void main() {
    v_normal = normalize(normal_matrix * in_normal);
    v_id = in_id;
    gl_Position = mvp * vec4(in_position, 1.0);
}
"""

FRAGMENT_SHADER = """
#version 330 core
in vec3 v_normal;
flat in uint v_id;
uniform int mode;            // 0 — заливка, 1 — пикинг, 2 — рёбра
uniform uint highlight_id;
out vec4 frag_color;
void main() {
    bool selected = (v_id == highlight_id && highlight_id != 0u);
    if (mode == 2) {
        frag_color = selected ? vec4(0.98, 0.60, 0.12, 1.0) : vec4(0.10, 0.11, 0.13, 1.0);
        return;
    }
    if (mode == 1) {
        uint id = v_id;
        frag_color = vec4(float(id & 0xFFu) / 255.0,
                          float((id >> 8) & 0xFFu) / 255.0,
                          float((id >> 16) & 0xFFu) / 255.0, 1.0);
        return;
    }
    vec3 light = normalize(vec3(0.4, -0.6, 0.8));
    float diffuse = max(dot(normalize(v_normal), light), 0.0);
    vec3 base = selected ? vec3(1.0, 0.72, 0.24) : vec3(0.62, 0.66, 0.70);
    frag_color = vec4(base * (0.35 + 0.65 * diffuse), 1.0);
}
"""


def _perspective(fov, aspect, near, far):
    f = 1.0 / np.tan(np.radians(fov) / 2.0)
    m = np.zeros((4, 4), np.float32)
    m[0, 0] = f / max(aspect, 1e-6)
    m[1, 1] = f
    m[2, 2] = (far + near) / (near - far)
    m[2, 3] = (2 * far * near) / (near - far)
    m[3, 2] = -1.0
    return m


def _look_at(eye, target, up):
    forward = target - eye
    forward /= np.linalg.norm(forward)
    side = np.cross(forward, up)
    side /= max(np.linalg.norm(side), 1e-9)
    true_up = np.cross(side, forward)
    m = np.eye(4, dtype=np.float32)
    m[0, :3], m[1, :3], m[2, :3] = side, true_up, -forward
    m[:3, 3] = -m[:3, :3] @ eye
    return m


class Viewport(QOpenGLWidget):
    """3D-вид: заливка, рёбра, выбор тела правой кнопкой."""

    picked = QtCore.Signal(int)

    def __init__(self, preview):
        super().__init__()
        self.preview = preview
        low = preview.positions.min(axis=0) if len(preview.positions) else np.zeros(3)
        high = preview.positions.max(axis=0) if len(preview.positions) else np.ones(3)
        self.center = ((low + high) / 2).astype(np.float32)
        self.radius = float(np.linalg.norm(high - low) / 2) or 1.0
        self.yaw, self.pitch, self.zoom = 45.0, 30.0, 1.0
        self.highlight_id = 0
        self.show_edges = True
        self.fps = 0.0
        self._frames, self._since = 0, time.perf_counter()
        self._last = None
        self.setMinimumSize(640, 480)
        timer = QtCore.QTimer(self)
        timer.timeout.connect(self.update)
        timer.start(16)

    def initializeGL(self):
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glClearColor(0.16, 0.17, 0.19, 1.0)
        self.program = GL.glCreateProgram()
        for src, kind in ((VERTEX_SHADER, GL.GL_VERTEX_SHADER),
                          (FRAGMENT_SHADER, GL.GL_FRAGMENT_SHADER)):
            shader = GL.glCreateShader(kind)
            GL.glShaderSource(shader, src)
            GL.glCompileShader(shader)
            if not GL.glGetShaderiv(shader, GL.GL_COMPILE_STATUS):
                raise RuntimeError(GL.glGetShaderInfoLog(shader).decode())
            GL.glAttachShader(self.program, shader)
        GL.glLinkProgram(self.program)
        self.face_vao = self._make_vao(
            self.preview.positions, self.preview.normals, self.preview.ids
        )
        self.edge_vao = None
        if len(self.preview.edge_positions):
            self.edge_vao = self._make_vao(
                self.preview.edge_positions,
                np.zeros_like(self.preview.edge_positions),
                self.preview.edge_ids,
            )

    def _make_vao(self, positions, normals, ids):
        vao = GL.glGenVertexArrays(1)
        GL.glBindVertexArray(vao)
        buffers = GL.glGenBuffers(3)
        for index, (array, size, gl_type) in enumerate(
            ((positions, 3, GL.GL_FLOAT), (normals, 3, GL.GL_FLOAT),
             (ids, 1, GL.GL_UNSIGNED_INT))
        ):
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, buffers[index])
            GL.glBufferData(GL.GL_ARRAY_BUFFER, array.nbytes,
                            np.ascontiguousarray(array), GL.GL_STATIC_DRAW)
            GL.glEnableVertexAttribArray(index)
            if gl_type == GL.GL_UNSIGNED_INT:
                GL.glVertexAttribIPointer(index, size, gl_type, 0, None)
            else:
                GL.glVertexAttribPointer(index, size, gl_type, GL.GL_FALSE, 0, None)
        GL.glBindVertexArray(0)
        return vao

    def resizeGL(self, width, height):
        GL.glViewport(0, 0, width, height)

    def _mvp(self):
        distance = self.radius * 2.6 * self.zoom
        yaw, pitch = np.radians(self.yaw), np.radians(max(-85, min(85, self.pitch)))
        eye = self.center + np.array(
            [np.cos(pitch) * np.cos(yaw) * distance,
             np.cos(pitch) * np.sin(yaw) * distance,
             np.sin(pitch) * distance], np.float32)
        view = _look_at(eye, self.center, np.array([0, 0, 1], np.float32))
        aspect = max(self.width(), 1) / max(self.height(), 1)
        proj = _perspective(35.0, aspect, self.radius * 0.02, self.radius * 20)
        return (proj @ view).astype(np.float32), view[:3, :3].astype(np.float32)

    def _draw(self, mode):
        mvp, normal_matrix = self._mvp()
        GL.glUseProgram(self.program)
        GL.glUniformMatrix4fv(GL.glGetUniformLocation(self.program, "mvp"), 1, GL.GL_TRUE, mvp)
        GL.glUniformMatrix3fv(GL.glGetUniformLocation(self.program, "normal_matrix"),
                              1, GL.GL_TRUE, normal_matrix)
        GL.glUniform1ui(GL.glGetUniformLocation(self.program, "highlight_id"),
                        self.highlight_id)
        GL.glUniform1i(GL.glGetUniformLocation(self.program, "mode"), mode)
        GL.glBindVertexArray(self.face_vao)
        if mode == 0:
            GL.glEnable(GL.GL_POLYGON_OFFSET_FILL)
            GL.glPolygonOffset(1.0, 1.0)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, len(self.preview.positions))
        if mode == 0:
            GL.glDisable(GL.GL_POLYGON_OFFSET_FILL)
        GL.glBindVertexArray(0)
        if mode == 0 and self.show_edges and self.edge_vao is not None:
            GL.glUniform1i(GL.glGetUniformLocation(self.program, "mode"), 2)
            GL.glBindVertexArray(self.edge_vao)
            GL.glDrawArrays(GL.GL_LINES, 0, len(self.preview.edge_positions))
            GL.glBindVertexArray(0)

    def paintGL(self):
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        self._draw(0)
        self._frames += 1
        elapsed = time.perf_counter() - self._since
        if elapsed >= 0.5:
            self.fps = self._frames / elapsed
            self._frames, self._since = 0, time.perf_counter()

    def mousePressEvent(self, event):
        self._last = event.position()
        if event.button() == QtCore.Qt.RightButton:
            self.picked.emit(self._pick(event.position()))

    def mouseMoveEvent(self, event):
        if self._last is None:
            return
        delta = event.position() - self._last
        self._last = event.position()
        self.yaw -= delta.x() * 0.4
        self.pitch += delta.y() * 0.4

    def wheelEvent(self, event):
        self.zoom = max(0.05, min(8.0, self.zoom * (0.88 if event.angleDelta().y() > 0 else 1.14)))

    def _pick(self, position):
        self.makeCurrent()
        GL.glClearColor(0.0, 0.0, 0.0, 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        self._draw(1)
        ratio = self.devicePixelRatio()
        pixel = GL.glReadPixels(int(position.x() * ratio),
                                int((self.height() - position.y()) * ratio),
                                1, 1, GL.GL_RGB, GL.GL_UNSIGNED_BYTE)
        GL.glClearColor(0.16, 0.17, 0.19, 1.0)
        values = np.frombuffer(pixel, np.uint8)
        return int(values[0]) | (int(values[1]) << 8) | (int(values[2]) << 16)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self, container):
        super().__init__()
        self.container = container
        self.preview = container.preview()
        self.setWindowTitle(
            f"ПРОТО — просмотр {container.designation}  {container.name}"
        )
        self.resize(1500, 900)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.addWidget(self._build_left())
        splitter.addWidget(self._build_right())
        splitter.setSizes([460, 1040])
        self.setCentralWidget(splitter)

        self.status = self.statusBar()
        self._status_timer = QtCore.QTimer(self)
        self._status_timer.timeout.connect(self._refresh_status)
        self._status_timer.start(500)
        self._selected = ""

    # --- панели ---

    def _build_left(self):
        tabs = QtWidgets.QTabWidget()

        self.tree = QtWidgets.QTreeWidget()
        self.tree.setHeaderLabels(["Поз. обозн.", "Обозначение", "Наименование"])
        self.tree.setColumnWidth(0, 90)
        self.tree.setColumnWidth(1, 150)
        self._fill_tree()
        self.tree.itemSelectionChanged.connect(self._tree_selected)
        tabs.addTab(self.tree, "Состав")

        self.bom = QtWidgets.QTableWidget()
        rows = self.container.bom()
        self.bom.setColumnCount(4)
        self.bom.setHorizontalHeaderLabels(["Раздел", "Обозначение", "Наименование", "Кол."])
        self.bom.setRowCount(len(rows))
        for index, row in enumerate(rows):
            for column, value in enumerate(
                (row["kind"], row["designation"], row["name"], str(row["quantity"]))
            ):
                cell = QtWidgets.QTableWidgetItem(value)
                if column == 3:
                    cell.setTextAlignment(QtCore.Qt.AlignCenter)
                self.bom.setItem(index, column, cell)
        self.bom.resizeColumnsToContents()
        self.bom.horizontalHeader().setStretchLastSection(False)
        tabs.addTab(self.bom, f"Спецификация ({len(rows)})")
        return tabs

    def _fill_tree(self):
        tree_data = self.container.tree()
        root = QtWidgets.QTreeWidgetItem(
            ["", tree_data.get("designation", ""), tree_data.get("name", "")]
        )
        font = root.font(1)
        font.setBold(True)
        root.setFont(1, font)
        self.tree.addTopLevelItem(root)
        self._by_reference = {}
        for child in tree_data["children"]:
            item = child["item"]
            node = QtWidgets.QTreeWidgetItem(
                [child.get("reference", ""), item.get("designation", ""), item.get("name", "")]
            )
            node.setData(0, QtCore.Qt.UserRole, child.get("reference", ""))
            root.addChild(node)
            if child.get("reference"):
                self._by_reference[child["reference"]] = node
        root.setExpanded(True)

    def _build_right(self):
        tabs = QtWidgets.QTabWidget()
        if self.preview is None:
            tabs.addTab(QtWidgets.QLabel("В файле нет просмотровых данных"), "Модель")
        else:
            self.viewport = Viewport(self.preview)
            self.viewport.picked.connect(self._picked)
            panel = QtWidgets.QWidget()
            layout = QtWidgets.QVBoxLayout(panel)
            layout.setContentsMargins(0, 0, 0, 0)
            controls = QtWidgets.QHBoxLayout()
            edges = QtWidgets.QCheckBox("Рёбра")
            edges.setChecked(True)
            edges.toggled.connect(lambda on: setattr(self.viewport, "show_edges", on))
            controls.addWidget(edges)
            controls.addStretch(1)
            controls.addWidget(QtWidgets.QLabel(
                "ЛКМ — вращение, колесо — масштаб, ПКМ — выбор тела"
            ))
            layout.addLayout(controls)
            layout.addWidget(self.viewport, 1)
            tabs.addTab(panel, "Модель")

        drawings = self.container.drawings()
        if drawings:
            drawing_tabs = QtWidgets.QTabWidget()
            for name in drawings:
                drawing_tabs.addTab(self._drawing_widget(name), Path(name).stem)
            tabs.addTab(drawing_tabs, f"Чертежи ({len(drawings)})")
        return tabs

    def _drawing_widget(self, name: str):
        data = self.container.read_drawing(name)
        try:
            from PySide6.QtSvgWidgets import QSvgWidget

            widget = QSvgWidget()
            widget.load(QtCore.QByteArray(data))
            widget.setStyleSheet("background: white;")
        except Exception:  # noqa: BLE001 — без QtSvg показываем исходник
            widget = QtWidgets.QPlainTextEdit(data.decode("utf-8", "replace")[:20000])
            widget.setReadOnly(True)
        area = QtWidgets.QScrollArea()
        area.setWidget(widget)
        area.setWidgetResizable(True)
        area.setStyleSheet("background: #ffffff;")
        return area

    # --- связь выбора ---

    def _picked(self, mesh_id: int):
        if not mesh_id:
            self._selected = ""
            self.viewport.highlight_id = 0
            return
        self.viewport.highlight_id = mesh_id
        info = self.container.resolve(mesh_id)
        reference = info.get("reference", "")
        self._selected = (
            f"{reference or info.get('label','')} · {info.get('designation','')} · "
            f"{info.get('name','')}"
            + (f" · ПРОТО {info['proto_id']}" if info.get("proto_id") else "")
        )
        node = self._by_reference.get(reference)
        if node is not None:
            self.tree.setCurrentItem(node)
            self.tree.scrollToItem(node)

    def _tree_selected(self):
        items = self.tree.selectedItems()
        if not items or self.preview is None:
            return
        reference = items[0].data(0, QtCore.Qt.UserRole)
        if not reference:
            return
        # Подписи берём у читателя: он приводит и новый формат адресации,
        # и старый, где в preview лежала просто строка.
        for mesh_id, label in self.container.mesh_labels().items():
            if label == reference:
                self.viewport.highlight_id = mesh_id
                self._selected = f"{reference} · {items[0].text(1)} · {items[0].text(2)}"
                return

    def _refresh_status(self):
        parts = []
        if self.preview is not None:
            parts.append(
                f"{self.preview.triangle_count} треуг. · "
                f"{self.preview.edge_count} рёбер · "
                f"{len(self.preview.id_to_object)} тел"
            )
            if hasattr(self, "viewport"):
                parts.append(f"{self.viewport.fps:.0f} FPS")
        if self._selected:
            parts.append(f"выбрано: {self._selected}")
        self.status.showMessage("    |    ".join(parts))


def main() -> int:
    if len(sys.argv) < 2:
        print("укажите файл .prcadAsm / .prcadPart")
        return 2
    surface = QtGui.QSurfaceFormat()
    surface.setVersion(3, 3)
    surface.setProfile(QtGui.QSurfaceFormat.CoreProfile)
    surface.setDepthBufferSize(24)
    QtGui.QSurfaceFormat.setDefaultFormat(surface)

    app = QtWidgets.QApplication(sys.argv)
    try:
        container = open_container(sys.argv[1])
    except ReaderError as error:
        QtWidgets.QMessageBox.critical(None, "ProtoCAD", str(error))
        return 1
    window = MainWindow(container)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
