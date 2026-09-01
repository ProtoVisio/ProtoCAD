"""Минимальный workbench ProtoCAD.

Задание требует addon, устанавливаемый копированием в ``Mod/``, поэтому здесь
есть регистрация верстака и одна команда — создать лист по ГОСТ. Полный набор
команд и воркфлоу задание делать запрещает.

Две ловушки FreeCAD, на которые этот файл наткнулся:

* ``__file__`` здесь не определён — обращение роняет инициализацию;
* объекты уровня модуля не видны из методов верстака, поэтому классы команд
  вынесены в импортируемый ``protocad.commands``.

PySide намеренно не используется: в проверяемой сборке он не грузится, и
верстак с собственными диалогами не поднялся бы.
"""

import FreeCADGui as Gui


class ProtoCadWorkbench(Gui.Workbench):
    MenuText = "ProtoCAD"
    ToolTip = "Электронные изделия: определения ЭРИ, платы и чертежи по ГОСТ"

    def Initialize(self):
        from protocad import commands

        names = commands.register(Gui)
        self.appendToolbar("ProtoCAD", names)
        self.appendMenu("ProtoCAD", names)

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(ProtoCadWorkbench())
