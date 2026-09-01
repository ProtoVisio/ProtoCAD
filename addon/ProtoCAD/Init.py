"""Инициализация addon ProtoCAD на уровне App (работает и в freecadcmd).

Здесь намеренно пусто. FreeCAD сам добавляет каталог addon в ``sys.path``,
поэтому пакет ``protocad`` импортируется без вмешательства.

Важно: ``Init.py`` и ``InitGui.py`` исполняются БЕЗ ``__file__`` — обращение к
нему роняет инициализацию с «name '__file__' is not defined», и addon молча не
грузится. Пути, если понадобятся, брать через ``FreeCAD.getUserAppDataDir()``.
"""
