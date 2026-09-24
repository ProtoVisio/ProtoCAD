"""Поиск FreeCAD: Windows (bin/python.exe), Linux AppImage и conda (bin/python +
lib/FreeCAD.so). Раньше находилась только раскладка Windows."""

from protocad.engine import freecad_backend


def _touch(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def test_windows_layout(tmp_path, monkeypatch):
    home = tmp_path / "FreeCAD 1.1"
    _touch(home / "bin" / "python.exe")
    _touch(home / "bin" / "FreeCAD.pyd")
    monkeypatch.setenv("PROTOCAD_FREECAD", str(home / "bin"))
    assert freecad_backend.find_freecad() == home


def test_extracted_appimage(tmp_path, monkeypatch):
    root = tmp_path / "squashfs-root"
    _touch(root / "usr" / "bin" / "python")
    _touch(root / "usr" / "lib" / "FreeCAD.so")
    monkeypatch.setenv("PROTOCAD_FREECAD", str(root))
    assert freecad_backend.find_freecad() == root / "usr"
    assert freecad_backend.interpreter_of(root / "usr") == root / "usr" / "bin" / "python"


def test_stated_path_is_not_replaced_by_another(tmp_path, monkeypatch):
    monkeypatch.setenv("PROTOCAD_FREECAD", str(tmp_path / "нет такого"))
    assert freecad_backend.find_freecad() is None
    ready, reason = freecad_backend.FreeCADBackend(None).available()
    assert not ready and "не подставляется" in reason
