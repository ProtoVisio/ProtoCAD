"""ЗАМОРОЖЕНО. Доказательная база Spike 2, не продуктовый код.

Модуль оставлен неизменным, потому что на нём стоят результаты в
``results/feasibility.md`` и он должен оставаться воспроизводимым. Для
разработки использовать ``protocad.model``: там структура сборки отражает
структуру документации по ЕСКД, а определение хранится одним объектом без
``App::Part`` — как показали замеры производительности.

Отличия от продуктовой модели, из-за которых этот код не годится к развитию:

* ``Board`` смешивает сборочную единицу и подложку; по ЕСКД плата — сборочная
  единица, а печатная плата внутри неё — отдельная деталь со своим чертежом;
* нет вложенности произвольной глубины и контроля циклов;
* нет состава как спецификации и разделов по ГОСТ 2.106.

---

Типы объектов ProtoCAD: определение ЭРИ, экземпляр, плата.

Требование A из 02_SPIKE_WORKBENCH.md. Всё реализуется как addon поверх
стабильного API: ни одной правки в исходном коде FreeCAD.

Ключевое ограничение сериализации: чтобы объект пережил save/open, прокси
должен восстанавливаться при загрузке, а значит этот модуль обязан быть
импортируемым — отсюда установка каталога в ``Mod/``.
"""

from __future__ import annotations

import uuid

import FreeCAD as App

GROUP = "ProtoCAD"


class _Serializable:
    """Общий прокси: FreeCAD 1.x зовёт dumps/loads, старые версии — __getstate__.

    Реализованы оба контракта, иначе объект молча терял бы тип при открытии
    документа, сохранённого другой версией.
    """

    Type = "ProtoCAD::Base"

    def dumps(self):
        return self.Type

    def loads(self, state):
        if state:
            self.Type = state
        return None

    # Совместимость с FreeCAD < 1.0.
    __getstate__ = dumps
    __setstate__ = loads

    def onDocumentRestored(self, obj):
        obj.Proxy = self


class ComponentDefinition(_Serializable):
    """Геометрия одного типа ЭРИ. Многотельная, со стабильным идентификатором."""

    Type = "ProtoCAD::ComponentDefinition"

    def __init__(self, obj):
        obj.Proxy = self
        _add(obj, "App::PropertyString", "StableId", "Стабильный идентификатор определения")
        _add(obj, "App::PropertyString", "ProtoEriId", "ID ЭРИ в базе ПРОТО")
        _add(obj, "App::PropertyString", "Designation", "Полное обозначение ЭРИ")
        _add(obj, "App::PropertyLinkList", "Bodies", "Тела, составляющие определение")
        _add(obj, "App::PropertyInteger", "Revision", "Ревизия определения")
        if not obj.StableId:
            obj.StableId = f"def-{uuid.uuid4()}"

    def execute(self, obj):
        """Собрать тела определения в одну форму.

        Compound, а не fuse: тела ЭРИ (корпус, выводы) физически раздельны,
        и склеивать их означало бы терять их число.
        """
        import Part

        shapes = [body.Shape for body in obj.Bodies if getattr(body, "Shape", None)]
        obj.Shape = Part.makeCompound(shapes) if shapes else Part.Shape()


class ComponentInstance(_Serializable):
    """Размещение определения на плате."""

    Type = "ProtoCAD::ComponentInstance"

    def __init__(self, obj):
        obj.Proxy = self
        _add(obj, "App::PropertyString", "StableId", "Стабильный идентификатор экземпляра")
        _add(obj, "App::PropertyString", "Reference", "Позиционное обозначение (R1, DD3)")
        _add(obj, "App::PropertyLink", "Definition", "Определение ЭРИ")
        _add(obj, "App::PropertyEnumeration", "Side", "Сторона платы")
        _add(obj, "App::PropertyString", "ProtoEriId", "ID ЭРИ в базе ПРОТО")
        obj.Side = ["Top", "Bottom"]
        if not obj.StableId:
            obj.StableId = f"inst-{uuid.uuid4()}"

    def execute(self, obj):
        import Part

        definition = obj.Definition
        if definition is None or not getattr(definition, "Shape", None):
            obj.Shape = Part.Shape()
            return
        shape = definition.Shape.copy()
        shape.Placement = obj.Placement
        obj.Shape = shape


class Board(_Serializable):
    """Подложка платы плюс коллекция экземпляров."""

    Type = "ProtoCAD::Board"

    def __init__(self, obj):
        obj.Proxy = self
        _add(obj, "App::PropertyString", "StableId", "Стабильный идентификатор платы")
        _add(obj, "App::PropertyString", "Designation", "Децимальный номер платы")
        _add(obj, "App::PropertyLink", "Substrate", "Тело подложки")
        _add(obj, "App::PropertyLinkList", "Instances", "Размещённые экземпляры")
        _add(obj, "App::PropertyLength", "Thickness", "Толщина платы")
        if not obj.StableId:
            obj.StableId = f"board-{uuid.uuid4()}"
        if not obj.Thickness:
            obj.Thickness = 1.6

    def execute(self, obj):
        import Part

        shapes = []
        if obj.Substrate is not None and getattr(obj.Substrate, "Shape", None):
            shapes.append(obj.Substrate.Shape)
        obj.Shape = Part.makeCompound(shapes) if shapes else Part.Shape()


def _add(obj, prop_type: str, name: str, tooltip: str) -> None:
    """Идемпотентное добавление свойства: при восстановлении оно уже есть."""
    if name not in obj.PropertiesList:
        obj.addProperty(prop_type, name, GROUP, tooltip)


def make_definition(doc, name: str, bodies=None, eri_id: str = "") -> App.DocumentObject:
    obj = doc.addObject("Part::FeaturePython", "ComponentDefinition")
    ComponentDefinition(obj)
    obj.Label = name
    obj.Designation = name
    obj.ProtoEriId = eri_id
    if bodies:
        obj.Bodies = list(bodies)
    return obj


def make_instance(
    doc,
    definition: App.DocumentObject,
    reference: str,
    placement: App.Placement | None = None,
    side: str = "Top",
    use_link: bool = True,
) -> App.DocumentObject:
    """Экземпляр определения.

    ``use_link=True`` даёт ``App::Link`` — ровно тот путь, который Spike 1
    подтвердил количественно (файл ×7.16, открытие ×4.57 против копий).
    ``ComponentInstance`` как ``Part::FeaturePython`` оставлен для проверки,
    что собственный тип вообще реализуем; в продукте основным должен быть Link.
    """
    if use_link:
        obj = doc.addObject("App::Link", "ComponentInstance")
        obj.LinkedObject = definition
        _add(obj, "App::PropertyString", "StableId", "Стабильный идентификатор экземпляра")
        _add(obj, "App::PropertyString", "Reference", "Позиционное обозначение")
        _add(obj, "App::PropertyString", "ProtoEriId", "ID ЭРИ в базе ПРОТО")
        _add(obj, "App::PropertyEnumeration", "Side", "Сторона платы")
        obj.Side = ["Top", "Bottom"]
        obj.StableId = f"inst-{uuid.uuid4()}"
    else:
        obj = doc.addObject("Part::FeaturePython", "ComponentInstance")
        ComponentInstance(obj)
        obj.Definition = definition
    obj.Reference = reference
    obj.Label = reference
    obj.Side = side
    obj.ProtoEriId = getattr(definition, "ProtoEriId", "")
    if placement is not None:
        obj.Placement = placement
    return obj


def make_board(doc, designation: str, substrate=None) -> App.DocumentObject:
    obj = doc.addObject("Part::FeaturePython", "Board")
    Board(obj)
    obj.Label = designation
    obj.Designation = designation
    if substrate is not None:
        obj.Substrate = substrate
    return obj
