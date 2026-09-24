"""Целевой движок: headless FreeCAD в отдельном процессе.

По `docs/08_ENGINE_BACKEND.md`. Здесь Python только переводит запрос в
свойства объектов FreeCAD и обратно; геометрию, пересчёт и прослеживание
топологии выполняет C++ FreeCAD.

**Почему отдельный процесс, а не импорт в наш.** Две причины, каждой
достаточно:

* §16.3 — у FreeCAD 1.1 своя сборка OCCT (7.8.1), у OCP своя (7.9.3).
  Объект одной библиотеки, попавший в функцию другой, роняет процесс в
  месте, никак не связанном с ошибкой;
* у FreeCAD собственный Python 3.11, у нас 3.12. Модули для разных версий
  не загружаются в один процесс в принципе.

Второе делает выбор окончательным: даже если бы ABI совпали, питоны — нет.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .freecad_server import MARK
from .protocol import (
    Capabilities,
    Diagnostic,
    EntityRef,
    FeatureResult,
    Mesh,
    PadRequest,
    SectionResult,
    Status,
    error,
)

#: Где искать FreeCAD. Каталог рядом с репозиторием — первым: так его
#: кладут, когда не хотят ставить в систему.
SEARCH = (
    Path(__file__).resolve().parents[2] / "FreeCAD",
    Path(r"C:\Program Files\FreeCAD 1.1"),
    Path(r"C:\Program Files\FreeCAD 1.0"),
    Path(r"C:\Program Files\FreeCAD"),
)


def find_freecad() -> Path | None:
    """Корень установки FreeCAD. ``None`` — не нашёлся.

    Порядок: переменная ``PROTOCAD_FREECAD`` → каталог рядом с
    репозиторием → обычные места → PATH. Переменная первой, чтобы можно
    было указать конкретную сборку, не трогая систему.
    """
    stated = os.environ.get("PROTOCAD_FREECAD", "").strip()
    if stated:
        # Указание ОБЯЗАТЕЛЬНО. Если названная установка не подошла, поиск
        # НЕ продолжается: молча взять другую значило бы работать не с той
        # сборкой, которую просили, — и узнать об этом на расхождении
        # результатов, а не на запуске.
        path = Path(stated)
        if path.name.lower() == "bin" and path.is_dir():
            path = path.parent
        return path if (path / "bin" / "python.exe").is_file() else None
    for candidate in SEARCH:
        if (candidate / "bin" / "python.exe").is_file():
            return candidate
    found = shutil.which("FreeCADCmd") or shutil.which("freecadcmd")
    if found:
        return Path(found).parent.parent
    return None


class FreeCADBackend:
    """Движок на FreeCAD в отдельном процессе."""

    name = "freecad"
    #: Первый запуск грузит модули FreeCAD и заметно дольше остальных
    #: вызовов, поэтому у готовности свой запас времени.
    START_TIMEOUT = 240.0
    CALL_TIMEOUT = 180.0

    def __init__(self, home: Path | None = None):
        self.home = home or find_freecad()
        self._process = None
        self._capabilities = None

    # --- готовность ----------------------------------------------------

    def available(self) -> tuple:
        if self.home is None:
            stated = os.environ.get("PROTOCAD_FREECAD", "").strip()
            if stated:
                return False, (
                    f"по указанному пути FreeCAD нет: {stated}. "
                    f"Другая установка не подставляется намеренно"
                )
            return False, (
                "FreeCAD не найден. Положите его рядом с репозиторием в "
                "FreeCAD/ либо укажите путь переменной PROTOCAD_FREECAD"
            )
        interpreter = self.home / "bin" / "python.exe"
        if not interpreter.is_file():
            return False, f"в {self.home / 'bin'} нет python.exe"
        return True, ""

    def capabilities(self) -> Capabilities:
        ready, reason = self.available()
        if not ready:
            return Capabilities(backend="FreeCAD", version="", features=[],
                                end_conditions={}, notes=reason)
        if self._capabilities is None:
            answer = self._call({"command": "capabilities"})
            if answer.get("status") == "invalid":
                return Capabilities(
                    backend="FreeCAD", version="", features=[],
                    end_conditions={},
                    notes=_first_message(answer) or "движок не ответил")
            protocol = answer.get("protocol") or {}
            self._capabilities = Capabilities(
                backend=answer.get("backend", "FreeCAD"),
                version=answer.get("version", ""),
                protocol=(protocol.get("major", 0), protocol.get("minor", 0)),
                features=list(answer.get("features") or ()),
                end_conditions=dict(answer.get("end_conditions") or {}),
                notes=answer.get("notes", ""),
            )
        return self._capabilities

    # --- операции ------------------------------------------------------

    def pad(self, request: PadRequest) -> FeatureResult:
        ready, reason = self.available()
        if not ready:
            return error("BACKEND_UNAVAILABLE", reason, [], self.name)
        payload = request.to_dict()
        payload["command"] = "pad"
        return _result_of(self._call(payload))

    def dress_up(self, request) -> FeatureResult:
        return self._operation("dress_up", request)

    def revolve(self, request) -> FeatureResult:
        return self._operation("revolve", request)

    def hole(self, request) -> FeatureResult:
        return self._operation("hole", request)

    def draft(self, request) -> FeatureResult:
        return self._operation("draft", request)

    def shell(self, request) -> FeatureResult:
        return self._operation("shell", request)

    def pattern(self, request) -> FeatureResult:
        return self._operation("pattern", request)

    def _operation(self, command: str, request) -> FeatureResult:
        """Любая операция детали. Разница между ними — только в имени и
        полях запроса; всё остальное одинаково, и разводить это по методам
        значило бы плодить места, где они могут разойтись."""
        ready, reason = self.available()
        if not ready:
            return error("BACKEND_UNAVAILABLE", reason, [], self.name)
        payload = request.to_dict()
        payload["command"] = command
        return _result_of(self._call(payload))

    def hole_tool(self, request) -> FeatureResult:
        """Отверстия одной операции: N инструментов, один вычет."""
        return self._operation("hole_tool", request)

    def material_span(self, document_id: str, points, axis,
                      body_id: str = "", feature_id: str = "") -> list:
        """Интервалы материала вдоль оси для каждой точки (§32).

        Пустой список у точки означает, что ось мимо детали. Это НЕ ошибка
        связи и не повод подставить габарит: по спецификации такая позиция
        объявляется негодной и блокирует операцию.
        """
        answer = self._call({"command": "material_span",
                             "document_id": document_id,
                             "body_id": body_id,
                             "points": [list(item) for item in points],
                             "axis": list(axis),
                             "feature_id": feature_id})
        if answer.get("status") != "valid":
            return [[] for _ in points]
        return [list(item) for item in (answer.get("spans") or ())]

    def save(self, document_id: str, path) -> FeatureResult:
        """Сохранить документ. Путь абсолютный: движок в другом процессе и
        о нашем текущем каталоге ничего не знает."""
        answer = self._call({"command": "save", "document_id": document_id,
                             "path": str(Path(path).resolve())})
        return _result_of(answer)

    def open(self, document_id: str, path) -> FeatureResult:
        answer = self._call({"command": "open", "document_id": document_id,
                             "path": str(Path(path).resolve())})
        result = _result_of(answer)
        result.feature_id = ";".join(answer.get("bodies") or ())
        return result

    def export(self, document_id: str, path, body_id: str = "") -> FeatureResult:
        """Выгрузить деталь в STEP (с именами тел) или BREP. Путь абсолютный:
        движок в другом процессе и о нашем текущем каталоге ничего не знает.

        В ответе ``feature_id`` — имена выгруженных тел через «;».
        """
        ready, reason = self.available()
        if not ready:
            return error("BACKEND_UNAVAILABLE", reason, [], self.name)
        answer = self._call({"command": "export", "document_id": document_id,
                             "body_id": body_id,
                             "path": str(Path(path).resolve())})
        result = _result_of(answer)
        result.feature_id = ";".join(answer.get("bodies") or ())
        return result

    def scene(self, document_id: str, body_id: str = "") -> FeatureResult:
        """Деталь сеткой. Без имени тела — все тела документа."""
        return _result_of(self._call({"command": "scene",
                                      "document_id": document_id,
                                      "body_id": body_id}))

    def section(self, request) -> SectionResult:
        """След детали на плоскости. Рёбра приходят описанием, как у сцены."""
        ready, reason = self.available()
        if not ready:
            return SectionResult(status=Status.INVALID, diagnostics=[
                Diagnostic("BACKEND_UNAVAILABLE", reason, "error", [],
                           self.name)])
        payload = request.to_dict()
        payload["command"] = "section"
        answer = self._call(payload)
        diagnostics = [
            Diagnostic(item.get("code", ""), item.get("message", ""),
                       item.get("severity", "error"),
                       list(item.get("arguments") or ()),
                       item.get("backend", self.name))
            for item in answer.get("diagnostics") or ()
        ]
        if answer.get("status") != "valid":
            return SectionResult(status=Status.INVALID,
                                 diagnostics=diagnostics)
        return SectionResult(status=Status.VALID,
                             edges=list(answer.get("edges") or ()),
                             diagnostics=diagnostics)

    def drop_feature(self, document_id: str, body_id: str,
                     feature_id: str) -> None:
        """Убрать одну операцию. Нужно тому, кто пробовал вариант."""
        if self._process is not None and feature_id:
            self._call({"command": "drop_feature", "document_id": document_id,
                        "body_id": body_id, "feature_id": feature_id})

    def clear(self, document_id: str) -> None:
        """Забыть ОДИН документ. Остальные остаются на месте."""
        if self._process is not None:
            self._call({"command": "clear", "document_id": document_id})

    def reset(self) -> None:
        """Забыть документы. Нужно проверкам: без этого они влияют друг на
        друга через общий движок, и порядок их запуска становится важен."""
        if self._process is not None:
            self._call({"command": "reset"})

    def shutdown(self) -> None:
        if self._process is None:
            return
        try:
            self._send({"command": "shutdown"})
            self._process.wait(timeout=15)
        except Exception:  # noqa: BLE001
            self._process.kill()
        finally:
            self._process = None

    # --- разговор -------------------------------------------------------

    def _start(self):
        if self._process is not None and self._process.poll() is None:
            return self._process
        server = Path(__file__).with_name("freecad_server.py")
        interpreter = self.home / "bin" / "python.exe"
        environment = dict(os.environ)
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUNBUFFERED"] = "1"
        self._process = subprocess.Popen(
            [str(interpreter), str(server), str(self.home)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            errors="replace", env=environment, cwd=str(self.home / "bin"),
        )
        ready = self._read(self.START_TIMEOUT)
        if ready is None or ready.get("status") != "ready":
            raise RuntimeError(
                "движок FreeCAD не поднялся: "
                + (_first_message(ready or {}) or "нет ответа о готовности")
            )
        return self._process

    def _send(self, payload: dict) -> None:
        self._start()
        self._process.stdin.write(json.dumps(payload, ensure_ascii=False) + "\n")
        self._process.stdin.flush()

    def _read(self, timeout: float):
        """Прочитать помеченный ответ, пропуская вывод самого FreeCAD.

        Метка нужна потому, что FreeCAD пишет в тот же stdout сообщения о
        загрузке модулей. Без неё протокол и его вывод пришлось бы
        разбирать вперемешку — и первая же непредвиденная строка ломала бы
        разбор.
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self._process.stdout.readline()
            if line == "":
                return None
            if line.startswith(MARK):
                return json.loads(line[len(MARK):])
        return None

    def _call(self, payload: dict) -> dict:
        try:
            self._send(payload)
            answer = self._read(self.CALL_TIMEOUT)
        except Exception as failure:  # noqa: BLE001
            return _failure("ENGINE_CALL_FAILED", str(failure))
        if answer is None:
            return _failure("ENGINE_TIMEOUT",
                            "движок не ответил в отведённое время")
        return answer


def _failure(code: str, message: str) -> dict:
    return {"status": "invalid", "diagnostics": [{
        "code": code, "message": message, "severity": "error",
        "arguments": [], "backend": "FreeCAD"}]}


def _first_message(answer: dict) -> str:
    for item in answer.get("diagnostics") or ():
        if item.get("severity") == "error":
            return item.get("message", "")
    return ""


def _mesh_of(raw):
    """Сетка из словаря. ``None`` — движок её не прислал."""
    if not raw:
        return None
    return Mesh(
        positions=list(raw.get("positions") or ()),
        normals=list(raw.get("normals") or ()),
        face_ids=list(raw.get("face_ids") or ()),
        edge_positions=list(raw.get("edge_positions") or ()),
        edge_ids=list(raw.get("edge_ids") or ()),
        body_ids=list(raw.get("body_ids") or ()),
        edge_body_ids=list(raw.get("edge_body_ids") or ()),
    )


def _result_of(answer: dict) -> FeatureResult:
    """Ответ движка → общий тип.

    Формулировки отказов здесь НЕ переписываются: они обязаны совпадать у
    обоих движков, иначе интерфейс придётся учить двум движкам вместо
    одного.
    """
    diagnostics = [
        Diagnostic(item.get("code", ""), item.get("message", ""),
                   item.get("severity", "error"),
                   list(item.get("arguments") or ()),
                   item.get("backend", "FreeCAD"))
        for item in answer.get("diagnostics") or ()
    ]
    if answer.get("status") != "valid":
        return FeatureResult(status=Status.INVALID, diagnostics=diagnostics)
    raw = answer.get("mesh") or {}
    tool = answer.get("toolMesh")
    return FeatureResult(
        status=Status.VALID,
        feature_id=answer.get("featureId", ""),
        revision=int(answer.get("revision", 0)),
        session_id=answer.get("sessionId", ""),
        volume=float(answer.get("volume", 0.0)),
        bounds=list(answer.get("bounds") or ()),
        # Именно `None`, а не пустая сетка: «движок сетку не прислал» и
        # «прислал пустую» — разные вещи, и подмена первого вторым
        # выглядит на экране одинаково — деталь гаснет, и не появляется
        # ничего. Спрашивать для показа надо `display_mesh`.
        mesh=_mesh_of(raw),
        tool_mesh=_mesh_of(tool),
        tool_kind=answer.get("toolKind", ""),
        entities=[EntityRef(item.get("id", ""), item.get("kind", "face"),
                            item.get("feature", ""), item.get("origin", ""),
                            int(item.get("index", -1)),
                            item.get("body", ""),
                            item.get("name", ""),
                            dict(item.get("data") or {}))
                  for item in answer.get("entities") or ()],
        diagnostics=diagnostics,
    )
