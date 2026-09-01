"""Команды верстака ProtoCAD.

Классы команд обязаны жить в импортируемом модуле, а не в ``InitGui.py``:
FreeCAD исполняет ``InitGui.py`` в отдельном пространстве имён, и объекты
уровня модуля не видны из методов верстака — ``Initialize()`` падает с
«name '...' is not defined».
"""

from __future__ import annotations

import os

import FreeCAD as App


def template_dir() -> str:
    return os.path.join(App.getUserAppDataDir(), "Mod", "ProtoCAD", "templates")


class CreateGostSheet:
    """Создать лист TechDraw с рамкой ГОСТ 2.301 и основной надписью ГОСТ 2.104."""

    NAME = "ProtoCAD_CreateGostSheet"

    def GetResources(self) -> dict:
        return {
            "MenuText": "Лист по ГОСТ",
            "ToolTip": "Лист TechDraw: рамка ГОСТ 2.301, основная надпись ГОСТ 2.104 форма 1",
        }

    def IsActive(self) -> bool:
        return App.ActiveDocument is not None

    def Activated(self) -> None:
        from protocad import gost_sheet

        doc = App.ActiveDocument
        directory = template_dir()
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, "A4_portrait_form1.svg")
        if not os.path.isfile(path):
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(gost_sheet.build_template_svg("A4", False, "1"))

        page = doc.addObject("TechDraw::DrawPage", "Page")
        template = doc.addObject("TechDraw::DrawSVGTemplate", "Template")
        template.Template = path
        page.Template = template
        doc.recompute()


ALL = (CreateGostSheet,)


def register(gui) -> list[str]:
    """Зарегистрировать команды и вернуть их имена для панели и меню."""
    names = []
    for command in ALL:
        gui.addCommand(command.NAME, command())
        names.append(command.NAME)
    return names
