"""Документ ProtoCAD: дерево операций поверх движка.

Заменяет собой `protocad.feature.FeatureTree` в рабочем пути. Разница не в
устройстве, а в том, кто считает: здесь дерево хранит НАМЕРЕНИЯ — эскиз,
выбранные области, длину, концевое условие, — а геометрию строит движок
(`docs/08_ENGINE_BACKEND.md`, §5.1). Формы ядра сюда не попадают вовсе.

Почему не оставить старое дерево и не подменить в нём геометрию. Потому
что оно устроено вокруг форм: кэширует их, сравнивает, передаёт между
операциями. Подмена превратила бы его в переводчик между двумя моделями —
а перевод между моделями и есть то место, где расходятся допуски и
диагностика.

**Правка идёт на месте.** У операции, уже построенной движком, запомнен её
идентификатор; при изменении параметров ей передаётся он же. Пересоздавать
нельзя: на операцию ссылаются те, что ниже по дереву.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from . import engine as engine_module
from . import reference as reference_module
from .engine import (
    DressUpRequest,
    EndCondition,
    FeatureResult,
    HoleRequest,
    PadRequest,
    PatternRequest,
    DraftRequest,
    HoleToolRequest,
    RevolveRequest,
    SectionRequest,
    ShellRequest,
    ThinType,
)

#: Версия схемы дерева намерений. Растёт, когда меняется состав полей.
SCHEMA = 1

#: Названия операций для дерева. Отдельно от ключей: ключ — для программы,
#: название — для человека, и менять их надо порознь.
TITLES = {"pad": "Выдавливание", "pocket": "Вырез",
          "fillet": "Скругление", "chamfer": "Фаска",
          "revolve": "Вращение", "groove": "Вырез вращением",
          "hole": "Отверстие", "shell": "Оболочка", "draft": "Уклон",
          "linear": "Линейный массив", "polar": "Круговой массив",
          "mirror": "Зеркало"}

#: Операции по рёбрам, а не по эскизу. Эскиза у них нет вовсе.
DRESS_UPS = ("fillet", "chamfer")
#: Операции по профилю эскиза.
BY_PROFILE = ("pad", "pocket", "revolve", "groove", "hole")
#: Массивы: размножают другие операции, а не строят новую геометрию.
PATTERNS = {"linear": "linear", "polar": "polar", "mirror": "mirror"}


@dataclass
class Operation:
    """Одна запись дерева. Только намерение, никакой геометрии."""

    #: Один из ключей ``TITLES``.
    kind: str
    name: str
    sketch: object = None
    regions: list = field(default_factory=list)
    length: float = 10.0
    end: EndCondition = EndCondition.BLIND
    reversed: bool = False
    #: Объединять ли результат с уже построенным. Выключено — операция
    #: даёт ОТДЕЛЬНОЕ тело: у `PartDesign::Body` свойства «слить» нет, и
    #: «не объединять» означает именно новое тело.
    merge: bool = True
    #: В каком теле живёт операция. Пусто — в текущем; заполняется при
    #: пересчёте, и по нему видно, что к чему относится.
    body: str = ""
    #: ОБЛАСТЬ ДЕЙСТВИЯ: в каком теле операция работает, если тел несколько.
    #: Пусто — «само»: по умолчанию операция идёт в то тело, где строится
    #: цепочка, а «не объединять» начинает новое. Явное имя перебивает это
    #: правило.
    #:
    #: Раздельно с `body` намеренно: `body` — ИТОГ раскладки, его пишет
    #: пересчёт; `scope` — НАМЕРЕНИЕ, его задаёт человек. Держать их в одном
    #: поле значило бы, что пересчёт затирает заданное.
    scope: str = ""
    #: Начало операции: сдвиг плоскости профиля вдоль её нормали.
    start_offset: float = 0.0
    #: Уклон стенок, градусы. Плюс — наружу, минус — внутрь.
    taper: float = 0.0
    #: Второе направление со своим концевым условием, длиной и уклоном.
    direction2: bool = False
    end2: EndCondition = EndCondition.BLIND
    length2: float = 10.0
    taper2: float = 0.0
    #: Грань-цель второго направления (номер в теле); −1 — нет.
    target2: int = -1
    target2_name: str = ""
    #: Для скругления и фаски: рёбра тела и размер. Хранятся ДВА адреса:
    #: устойчивое имя из карты элементов движка и номер по месту. Имя
    #: главнее — номер годен ровно до правки выше по дереву.
    edges: list = field(default_factory=list)
    edge_names: list = field(default_factory=list)
    size: float = 3.0
    #: Тип ФАСКИ: "equal" — равные катеты, "two" — два расстояния,
    #: "angle" — расстояние и угол. У фасок, записанных до появления
    #: типов, поля нет, и они остаются с равными катетами — тем, чем и
    #: были. У скругления не используется.
    chamfer_type: str = "equal"
    size2: float = 1.0
    #: Поменять катеты местами: какой на какой грани.
    flip: bool = False
    #: Для вращения: ось, точка на ней и угол.
    axis: tuple = (0.0, 1.0, 0.0)
    axis_origin: tuple = (0.0, 0.0, 0.0)
    angle: float = 360.0
    #: Для отверстия: диаметр и насквозь ли.
    diameter: float = 8.0
    through: bool = True
    #: Для оболочки и «до грани»: грани тела и толщина стенки. Толщина —
    #: одна и та же величина у оболочки и у тонкостенного выдавливания, и
    #: заводить под неё второе поле значило бы развести одно понятие.
    faces: list = field(default_factory=list)
    face_names: list = field(default_factory=list)
    thickness: float = 2.0
    #: Наружу или внутрь. У оболочки — куда нарастает стенка, у уклона —
    #: куда идёт материал. Умолчание «внутрь» у обоих: габарит детали при
    #: этом не меняется, и это обычный случай.
    outward: bool = False
    #: УКЛОН: нейтральная грань, вокруг которой наклоняются выбранные.
    #: Без неё наклон неопределён — поворачивать грань можно вокруг любой
    #: лежащей на ней прямой.
    neutral: int = -1
    neutral_name: str = ""
    #: ТОНКОСТЕННЫЙ ЭЛЕМЕНТ: пусто — выдавливать сплошным, иначе куда
    #: наращивать стенку от контура (см. ``ThinType``). Стенка — свойство
    #: ПРОФИЛЯ, а не отдельная операция: лишней записи в дереве не будет.
    thin: str = ""
    thickness2: float = 1.0
    thin_flip: bool = False
    #: Для массива: какие операции размножать (имена в дереве), сколько,
    #: с каким шагом и вдоль чего.
    sources: list = field(default_factory=list)
    #: Размножать тело целиком, а не перечисленные операции.
    whole_shape: bool = False
    count: int = 2
    spacing: float = 20.0
    direction: tuple = (1.0, 0.0, 0.0)
    #: Чем задан ряд: "spacing" — шагом между соседними, "extent" — общей
    #: длиной. Имена с приставкой `pattern_`, потому что `direction2` у
    #: операции уже занято ВТОРЫМ НАПРАВЛЕНИЕМ ВЫДАВЛИВАНИЯ, а это совсем
    #: другое: там сторона, здесь второй ряд массива.
    pattern_mode: str = "spacing"
    pattern_length: float = 100.0
    pattern_direction2: tuple = (0.0, 1.0, 0.0)
    spacing2: float = 20.0
    #: Единица — второго ряда нет.
    count2: int = 1
    pattern_mode2: str = "spacing"
    pattern_length2: float = 100.0
    reversed2: bool = False
    #: Круговой: разложить поровну по углу или ставить через `angle_step`.
    equal_spacing: bool = True
    angle_step: float = 30.0
    #: Номера экземпляров, которые не строить (с нуля; ноль — исходник).
    skip: list = field(default_factory=list)
    plane_normal: tuple = (1.0, 0.0, 0.0)
    plane_origin: tuple = (0.0, 0.0, 0.0)
    #: Описание отверстия, если это операция «Отверстие». Одно описание на
    #: сколько угодно позиций (`docs/09_HOLES.md`, §2.1): в дереве это одна
    #: строка, а не отдельная операция на каждую дыру.
    hole: object = None
    #: Имя СОЗДАННОЙ плоскости, если отражают относительно неё. Пусто —
    #: плоскость задана числами выше.
    #:
    #: Хранится имя, а не снятые с плоскости числа: справочная плоскость
    #: параметрическая и пересчитывается вместе с деталью. Записав её
    #: нормаль один раз, зеркало осталось бы там, где плоскость была
    #: когда-то, — и разошлось бы с ней при первой же правке размера.
    plane_name: str = ""
    #: Имя операции в движке. Пусто — ещё не строилась.
    feature_id: str = ""
    #: Итог последнего пересчёта: пусто — всё в порядке.
    message: str = ""
    ok: bool = True
    note: str = ""
    #: Подписи граней детали СРАЗУ ПОСЛЕ этой операции. Заполняется при
    #: пересчёте и нужно, чтобы показать «какие грани сделала вот эта
    #: запись»: движок называет подэлементы по месту, а не по
    #: происхождению, и спросить его об этом нельзя. Разница с предыдущей
    #: операцией — приближение, но считается оно по фактическим ответам
    #: движка и не выдумывает того, чего не знает.
    faces_after: tuple = ()

    @property
    def title(self) -> str:
        return f"{TITLES.get(self.kind, self.kind)}: {self.name}"

    @property
    def subtract(self) -> bool:
        return self.kind in ("pocket", "groove")

    @property
    def dress_up(self) -> bool:
        return self.kind in DRESS_UPS

    @property
    def pattern(self) -> bool:
        return self.kind in PATTERNS


class _Holder:
    """Свободный эскиз в виде «операции» — только ради имени и ссылки.

    Пересчёт опоры одинаков для эскиза в операции и без неё, и разводить
    его по двум почти одинаковым кускам значило бы завести два места, где
    он может разойтись.
    """

    __slots__ = ("sketch", "name")

    def __init__(self, sketch):
        self.sketch = sketch
        self.name = getattr(sketch, "name", "Эскиз")


@dataclass
class Report:
    """Итог пересчёта всего дерева."""

    ok: bool = True
    message: str = ""
    failed_at: str = ""
    volume: float = 0.0
    notes: list = field(default_factory=list)


class Document:
    """Деталь: список операций и движок, который их выполняет."""

    def __init__(self, name: str = "Деталь", backend=None,
                 designation: str = ""):
        self.name = name
        self.designation = designation or name
        self.backend = backend or engine_module.make_backend()
        self.operations: list = []
        #: Эскизы детали в порядке появления. Держатся ЗДЕСЬ, а не только
        #: на операциях: нарисованный эскиз существует до того, как им
        #: что-то построили, и пропадать между этими двумя мгновениями он
        #: не должен.
        self.sketches: list = []
        self.result: FeatureResult | None = None
        #: Справочные плоскости: правила построения, а не числа. Считаются
        #: вместе с деталью — иначе после первой правки плоскость «на 10 мм
        #: от грани» остаётся там, где была грань раньше.
        self.planes: list = []
        self._numbers = itertools.count(1)
        self.body_id = "Тело"
        self.document_id = name
        #: Расходится ли документ движка с деревом намерений. Так бывает
        #: после чтения контейнера: геометрия открыта из файла, а дерево
        #: ещё ни разу не выполнялось. Первый пересчёт начинает с чистого.
        self._stale = False
        #: Докуда деталь построена. ``None`` — до конца. Число означает,
        #: что выполняются только первые столько операций, а остальные
        #: ОТКАЧЕНЫ: они остаются в дереве со всеми своими параметрами, но
        #: детали не касаются.
        #:
        #: Это не «выключить операцию». Откат нужен, чтобы ВСТАВИТЬ новую
        #: операцию в середину дерева: пока последующие откачены, новая
        #: строится на той форме, которая была тогда, а не на готовой
        #: детали.
        self._rollback: int | None = None

    # --- состав ---------------------------------------------------------

    def add(self, operation: Operation) -> Operation:
        """Добавить операцию. При откате — ПЕРЕД шторкой, а не в конец.

        В этом и смысл отката: новая операция встаёт туда, докуда деталь
        построена, и строится на той форме, которая там была. Добавь её в
        конец — и она встала бы после откаченных, то есть совсем не там,
        куда её вставляли.
        """
        if not operation.name:
            operation.name = f"{TITLES.get(operation.kind, operation.kind)} " \
                             f"{next(self._numbers)}"
        if self._rollback is None:
            self.operations.append(operation)
        else:
            self.operations.insert(self._rollback, operation)
            self._rollback += 1
        if operation.sketch is not None and operation.sketch not in self.sketches:
            self.sketches.append(operation.sketch)
        return operation

    def add_sketch(self, sketch) -> object:
        """Записать эскиз в деталь. Операции у него может ещё не быть."""
        if sketch not in self.sketches:
            self.sketches.append(sketch)
        return sketch

    def free_sketches(self) -> list:
        """Эскизы, которые ещё ничего не построили.

        Операция берёт ближайший свободный: повторно использовать
        израсходованный нельзя — второе выдавливание того же контура молча
        дало бы неожиданное тело.
        """
        used = {id(item.sketch) for item in self.operations
                if item.sketch is not None}
        return [sketch for sketch in self.sketches if id(sketch) not in used]

    def remove_sketch(self, sketch) -> None:
        """Убрать эскиз вместе с операциями, которые на нём стоят."""
        for item in [op for op in self.operations if op.sketch is sketch]:
            self.operations.remove(item)
        if sketch in self.sketches:
            self.sketches.remove(sketch)
        self._forget()

    def remove(self, operation: Operation) -> None:
        """Убрать операцию. Дерево строится заново — движок должен забыть
        снятое, а выборочно убрать одну операцию из его документа сложнее,
        чем построить всё: операций у детали десятки, не тысячи."""
        if operation in self.operations:
            index = self.operations.index(operation)
            self.operations.remove(operation)
            # Шторка держится за МЕСТО в списке. Убрали операцию выше неё —
            # место сдвинулось, и без поправки шторка съезжала на соседнюю.
            if self._rollback is not None and index < self._rollback:
                self._rollback -= 1
        self._forget()

    def forget(self) -> None:
        """Забыть построенное движком. Дерево намерений остаётся."""
        self._forget()

    def _forget(self) -> None:
        """Забыть построенное: дерево пойдёт заново с чистого документа.

        Чистится ТОЛЬКО свой документ движка. Общий сброс закрывает все
        сразу, и удаление операции в одной детали стирало бы вторую
        открытую.
        """
        for item in self.operations:
            item.feature_id = ""
            item.faces_after = ()
        clear = getattr(self.backend, "clear", None)
        if clear is not None:
            clear(self.document_id)
        else:
            self.backend.reset()
        self.result = None

    @property
    def rollback(self) -> int | None:
        """Докуда построено. ``None`` — до конца дерева."""
        return self._rollback

    @rollback.setter
    def rollback(self, value: int | None) -> None:
        limit = len(self.operations)
        if value is not None:
            value = max(0, min(int(value), limit))
            if value >= limit:
                value = None
        if value == self._rollback:
            return
        self._rollback = value
        # Откат меняет СОСТАВ построенного, а не параметры. Убрать из
        # документа движка отдельные операции нельзя так, чтобы не
        # разъехались ссылки у оставшихся, поэтому следующий пересчёт
        # идёт с чистого — тем же путём, что и после чтения файла.
        self._stale = True

    @property
    def built(self) -> list:
        """Операции, которые выполняются. Остальные откачены."""
        return (self.operations if self._rollback is None
                else self.operations[:self._rollback])

    def rolled_back(self, operation: Operation) -> bool:
        return operation in self.operations[len(self.built):]

    # --- пересчёт -------------------------------------------------------

    def rebuild(self) -> Report:
        """Выполнить дерево. Возвращает итог, а не бросает исключение.

        Отказ операции не прекращает существование детали: она остаётся
        такой, какой была до неудавшейся операции, и это видно в дереве.
        Бросать отсюда значило бы ронять окно на каждой опечатке в длине.
        """
        # Имена подэлементов привязаны к документу движка. Дерево,
        # прочитанное из файла, строится в НОВОМ документе, и прежние
        # имена там не находятся. Поэтому при пересборке с нуля ссылки
        # заново привязываются ПО НОМЕРУ и получают свежие имена.
        #
        # Это не лазейка: номер верен ровно тогда, когда дерево строится
        # тем же составом операций, что и при записи, — а это и есть
        # пересборка с нуля. Имя нужно для другого случая — правки внутри
        # сеанса, где номер как раз и уезжает.
        reanchor = self._stale
        if self._stale:
            # Дерево прочитано, а в документе движка лежит то, что открыли
            # из файла. Строить поверх него значило бы получить каждую
            # операцию дважды. Чистим — и считаем заново из намерений: в
            # этом и состоит параметрическая модель.
            self._stale = False
            self._forget()
        report = Report()
        previous = None
        # Тела раскладываются ЗДЕСЬ, а не в движке: «объединить результаты»
        # — понятие интерфейса, движку уходит уже готовое имя тела.
        self._assign_bodies()
        # Откаченные операции не выполняются, но и не забываются: их
        # параметры остаются, а состояние прямо говорит, что они отложены.
        for operation in self.operations[len(self.built):]:
            operation.ok = True
            operation.message = ""
            operation.note = ""
            operation.feature_id = ""
            operation.faces_after = ()
        for operation in self.built:
            if reanchor:
                _forget_names(operation)
            self._settle_sketch(operation, previous, report)
            self._settle_plane(operation, previous)
            self._settle_marks(operation.sketch, previous)
            answer = self._run(operation)
            operation.ok = answer.ok
            operation.message = answer.message
            operation.note = _note_of(answer)
            if operation.note:
                report.notes.append(operation.note)
            if not answer.ok:
                report.ok = False
                report.failed_at = operation.name
                report.message = answer.message
                return report
            operation.feature_id = answer.feature_id or operation.feature_id
            # Сторону мог выбрать не человек, а правило разворота.
            # Запоминаем: при следующем пересчёте операция строится по
            # запомненным параметрам, и без этого разворот терялся бы.
            if not operation.dress_up:
                operation.reversed = answer.reversed
            operation.faces_after = _face_signatures(answer)
            if reanchor and previous is not None:
                _catch_names(operation, previous)
            previous = answer
            self.result = answer
            report.volume = answer.volume
        if len(self.bodies) > 1:
            # Тел несколько — показывать надо ВСЕ. Ответ последней операции
            # знает только своё тело, и деталь выглядела бы урезанной.
            whole = self.backend.scene(self.document_id, "")
            if whole.ok:
                self.result = whole
                report.volume = whole.volume
        self._resolve_planes()
        # Свободные эскизы — те, что ещё не пошли в операцию, — тоже стоят
        # на опорах и тоже обязаны за ними ехать. Места в дереве у них нет,
        # поэтому считаются они по ГОТОВОЙ детали: другого «к этому месту»
        # у них не существует.
        for sketch in self.free_sketches():
            self._settle_sketch(_Holder(sketch), self.result, report)
            self._settle_marks(sketch, self.result)
        return report

    def _run_hole(self, operation, common):
        """Операция «Отверстие»: ОДНА строка дерева на сколько угодно дыр.

        Позиции берутся из точек эскиза, толщина под каждой — у движка
        (§32), негодная позиция останавливает всю операцию (§31).
        """
        from .holes import (blocking, check, instances, positions_from,
                            positions_from_3d)

        feature = operation.hole
        if feature is None:
            return engine_module.error(
                "HOLE_NO_POSITION", f"{operation.name}: отверстие не описано",
                ["hole"])
        if operation.sketch is None:
            return engine_module.error(
                "HOLE_NO_POSITION",
                f"{operation.name}: эскиз позиций не задан", ["sketch"])
        spatial = hasattr(operation.sketch, "resolve")
        if spatial:
            # Трёхмерный эскиз: точки стоят на СВОИХ опорах, и ось у каждой
            # своя. Опоры пересчитываются по граням, построенным к этому
            # месту дерева.
            faces = [dict(item.data or {}) for item in
                     (self.result.entities if self.result is not None else ())
                     if item.kind == "face"]
            operation.sketch.resolve(faces)
            feature.placement.source_type = "position_sketch_3d"
            feature.placement.positions, notes = positions_from_3d(
                operation.sketch, feature.placement.positions)
            if notes:
                return engine_module.error(
                    notes[0].code, f"{operation.name}: {notes[0].message}",
                    ["hole"])
        else:
            feature.placement.positions = positions_from(
                operation.sketch, feature.placement.positions)
        if not feature.placement.positions:
            return engine_module.error(
                "HOLE_NO_POSITION",
                f"{operation.name}: в эскизе нет ни одной свободной точки — "
                f"отверстия ставить негде", ["sketch"])
        if spatial:
            axis = (0.0, 0.0, -1.0)
        else:
            axis = tuple(-float(value)
                         for value in operation.sketch.plane.normal)
        # Интервалы спрашиваются ПО ГРУППАМ ОСЕЙ: у трёхмерного эскиза оси
        # разные, и одним запросом их не покрыть — движок считает луч по
        # одному направлению.
        spans = self._hole_spans(
            feature.placement.positions, axis,
            operation.body or self.body_id,
            # Имя берётся из общих полей: там оно уже пустое у
            # предпросмотра — тому мерить нечего, своей операции в детали
            # ещё нет.
            str(common.get("feature_id") or ""))
        made, notes = instances(feature, spans)
        stopped = blocking(check(feature))
        if stopped:
            first = stopped[0]
            return engine_module.error(
                first.code, f"{operation.name}: {first.message}", ["hole"])
        if not made:
            return engine_module.error(
                "HOLE_NO_MATERIAL_INTERSECTION",
                f"{operation.name}: ни одно отверстие не попало в материал",
                ["hole"])
        if not spatial:
            # У ПЛОСКОГО эскиза ось одна на всех — нормаль его плоскости.
            # У трёхмерного она СВОЯ у каждой позиции (§30), и общая здесь
            # ставила инструмент вдоль чужого направления: два отверстия из
            # трёх оказывались касательными к поверхности и срезали по
            # половине вместо полного.
            for item in made:
                item["axis"] = list(axis)
        return self.backend.hole_tool(HoleToolRequest(
            instances=made, **common))

    def _hole_spans(self, positions, axis, body: str, feature_id: str):
        """Интервалы материала для позиций, по ГРУППАМ ОСЕЙ.

        Движок считает луч по одному направлению за запрос, а у точек
        трёхмерного эскиза оси разные (§30). Позиции с одинаковой осью
        собираются в один запрос, разные — в свои.
        """
        groups: dict = {}
        for index, place in enumerate(positions):
            along = tuple(round(float(value), 9)
                          for value in (place.direction or axis))
            groups.setdefault(along, []).append(index)
        found = [[] for _ in positions]
        for along, indexes in groups.items():
            answer = self.backend.material_span(
                self.document_id, [positions[i].xyz for i in indexes],
                along, body, feature_id)
            for slot, spans in zip(indexes, answer):
                found[slot] = spans
        return found

    def _settle_marks(self, sketch, previous) -> None:
        """Пересадить ссылки эскиза на деталь — ПЕРЕД его операцией.

        Ссылка на грань — не снимок положения, а правило: «там, где эта
        грань». Пока пересадки не было, ссылка держалась там, где грань
        стояла в момент указания: плита росла с 60 до 90, а след грани
        оставался на 60. Размер, поставленный к такому следу, продолжал
        показывать своё число и мерил место, где грани уже нет.

        Перед операцией, а не после всего: ссылка входит в эскиз, эскиз —
        в профиль операции. Пересаженная позже, она опоздала бы на целый
        пересчёт, и деталь выходила бы то верной, то нет — через раз.
        """
        # Трёхмерный эскиз позиций устроен иначе: у него нет ни
        # плоскости, ни ссылок — свои опоры он пересчитывает сам.
        if sketch is None or not hasattr(sketch, "references"):
            return
        if not sketch.references:
            return
        entities = ()
        if previous is not None:
            entities = previous.entities
        elif self.result is not None:
            entities = self.result.entities
        faces = [item for item in entities
                 if item.kind == "face"
                 and (item.data or {}).get("surface") == "plane"]
        if not faces:
            return

        edges = [item for item in entities if item.kind == "edge"
                 and (item.data or {}).get("curve") == "line"]

        def place(source):
            body, _, rest = str(source).partition(":")
            _, _, mark = rest.partition(":")
            mark, _, end = mark.partition("#")
            if body == "f":
                found = _face_by_mark(faces, mark)
                if found is None:
                    return None
                trace = _plane_trace(sketch.plane, found)
                if trace is None:
                    return None
                return trace[1] if end == "1" else trace[0]
            if body == "e":
                ends = _edge_by_mark(edges, mark)
                if ends is None:
                    return None
                return sketch.plane.project(ends[1 if end == "1" else 0])
            return None

        sketch.rebind(place)

    def _settle_plane(self, operation, previous) -> None:
        """Пересчитать опорную плоскость операции ПЕРЕД её построением.

        Перед, а не после: плоскости считались все разом в конце пересчёта,
        и операция, отражающая деталь относительно построенной плоскости,
        видела её ПРОШЛОЕ положение. Сдвинули плоскость — деталь осталась
        прежней, а на следующем пересчёте вдруг переезжала. Отказа при
        этом нет, и выглядит это как «зеркало отстаёт на один шаг».

        Считается по тому, что построено К ЭТОМУ МЕСТУ дерева: плоскость
        не может опираться на грань, которую делает операция, стоящая
        ПОСЛЕ неё.
        """
        if not operation.plane_name:
            return
        record = self.plane_named(operation.plane_name)
        if record is None:
            return
        entities = ()
        if previous is not None:
            entities = previous.entities
        elif self.result is not None:
            entities = self.result.entities
        reference_module.resolve(record, entities)

    def _settle_sketch(self, operation, previous, report) -> None:
        """Поставить эскиз операции на его опору — перед её построением.

        ПЕРЕД, а не после: эскиз на грани опирается на то, что построено к
        этому месту дерева, и никак не может опираться на грань, которую
        делает его собственная операция.

        Эскиз без опоры не трогается вовсе: его плоскость задана числами, и
        менять её здесь было бы самоуправством.
        """
        sketch = getattr(operation, "sketch", None)
        support = getattr(sketch, "support", None)
        if sketch is None or not support:
            return
        entities = previous.entities if previous is not None else ()
        plane, failure = reference_module.plane_of_support(
            support, entities, self.planes)
        if plane is None:
            # Опора потерялась. Оставляем эскиз там, где он есть, и говорим
            # об этом вслух: молча построить по устаревшей плоскости —
            # значит выдать деталь, которую никто не заказывал.
            note = f"{operation.name}: {failure or 'опора эскиза не найдена'}"
            if note not in report.notes:
                report.notes.append(note)
            return
        sketch.plane = plane

    def _resolve_planes(self) -> None:
        """Пересчитать справочные плоскости по готовой детали.

        После операций, а не до: плоскость «на 10 мм от верхней грани»
        стоит на грани, которой до пересчёта ещё нет. Плоскость, которую
        построить не удалось, помечается сообщением и не подставляет
        вместо себя ничего похожего.
        """
        entities = self.result.entities if self.result is not None else ()
        for record in self.planes:
            reference_module.resolve(record, entities)

    def plane_named(self, name: str):
        """Справочная плоскость по имени. ``None`` — такой нет."""
        for record in self.planes:
            if record.name == name:
                return record
        return None

    def add_plane(self, record):
        """Завести справочную плоскость и сразу посчитать её."""
        self.planes.append(record)
        reference_module.resolve(
            record, self.result.entities if self.result is not None else ())
        return record

    def remove_plane(self, record) -> bool:
        if record in self.planes:
            self.planes.remove(record)
            return True
        return False

    def _assign_bodies(self) -> None:
        """Разложить операции по телам.

        Операция с выключенным «объединить» начинает НОВОЕ тело; всё, что
        идёт за ней, попадает туда же, пока следующая снова не начнёт своё.
        Так это и понимают: выключил объединение — получил отдельное тело,
        и дальше строишь в нём.
        """
        current = self.body_id
        made = 1
        for operation in self.operations:
            if not operation.merge:
                made += 1
                current = f"{self.body_id} {made}"
            # Заданная область действия перебивает раскладку по умолчанию, но
            # НЕ сдвигает её: следующие операции продолжают ту цепочку, в
            # которой шли. Иначе одна операция, отправленная в чужое тело,
            # утаскивала бы за собой всё, что построено после неё.
            operation.body = operation.scope or current

    @property
    def bodies(self) -> list:
        """Тела детали в порядке появления."""
        found = [self.body_id]
        for operation in self.operations:
            if operation.body and operation.body not in found:
                found.append(operation.body)
        return found

    def faces_of(self, operation: Operation) -> set:
        """Номера граней ГОТОВОЙ детали, появившиеся из-за этой операции.

        Сравнивается описание детали до и после неё: грани, которой раньше
        не было, эта операция и сделала. Пересечение с готовой деталью
        обязательно — операции ниже по дереву могли её срезать, и
        подсвечивать то, чего уже нет, нельзя.

        Это приближение, а не топологическое имя: движок называет
        подэлементы по месту. Но считается оно по фактическим ответам
        движка, и когда сопоставить не удалось, набор пуст — подсветки
        просто нет, а не подсвечено наугад.
        """
        if operation not in self.operations or self.result is None:
            return set()
        place = self.operations.index(operation)
        before = set()
        for earlier in reversed(self.operations[:place]):
            if earlier.faces_after:
                before = set(earlier.faces_after)
                break
        created = set(operation.faces_after) - before
        if not created:
            return set()
        return {item.index for item in self.result.entities
                if item.kind == "face" and _face_signature(item) in created}

    def preview(self, operation: Operation, revision: int = 0) -> FeatureResult:
        """Показать операцию, не оставляя её в дереве.

        Считает тот же движок и тем же кодом (§12.1). Операция при этом
        может ещё не быть в списке — предпросмотр нужен именно до того, как
        человек согласился.
        """
        return self._run(operation, preview=True, revision=revision)

    def _run(self, operation: Operation, preview: bool = False,
             revision: int = 0) -> FeatureResult:
        common = dict(
            document_id=self.document_id,
            body_id=operation.body or self.body_id,
            preview=preview,
            revision=revision,
            feature_id="" if preview else operation.feature_id,
        )
        if operation.dress_up:
            return self.backend.dress_up(DressUpRequest(
                kind=operation.kind, edges=list(operation.edges),
                edge_names=list(operation.edge_names),
                faces=list(operation.faces),
                face_names=list(operation.face_names),
                size=operation.size, mode=operation.chamfer_type,
                size2=operation.size2, angle_deg=operation.angle,
                flip=operation.flip, **common))
        if operation.pattern:
            # Массив ссылается на операции ПО ИМЕНИ В ДЕРЕВЕ, а имя в
            # движке у них своё. Перевод делается здесь: наружу имена
            # движка не выходят вовсе.
            sources, missing = [], []
            for name in operation.sources:
                source = self.by_name(name)
                if source is None or not source.feature_id:
                    missing.append(name)
                else:
                    sources.append(source.feature_id)
            if missing:
                return engine_module.error(
                    "NO_ORIGINALS",
                    f"{operation.name}: размножать нечего — операций "
                    f"{missing} в детали нет либо они ещё не построены",
                    ["sources"])
            normal = tuple(operation.plane_normal)
            origin = tuple(operation.plane_origin)
            if operation.plane_name:
                # Отражают относительно СОЗДАННОЙ плоскости. Числа берутся
                # у неё СЕЙЧАС, а не те, что были при создании зеркала:
                # плоскость параметрическая и едет за деталью.
                record = self.plane_named(operation.plane_name)
                if record is None:
                    return engine_module.error(
                        "NO_PLANE",
                        f"{operation.name}: плоскости "
                        f"«{operation.plane_name}» в детали нет",
                        ["plane_name"])
                if not record.ok:
                    return engine_module.error(
                        "PLANE_FAILED",
                        f"{operation.name}: плоскость "
                        f"«{operation.plane_name}» не построилась"
                        + (f" — {record.message}" if record.message else ""),
                        ["plane_name"])
                normal = tuple(float(value) for value in record.plane.normal)
                origin = tuple(float(value) for value in record.plane.origin)
            return self.backend.pattern(PatternRequest(
                kind=operation.kind, features=sources,
                whole_shape=operation.whole_shape, count=operation.count,
                direction=tuple(operation.direction), spacing=operation.spacing,
                mode=operation.pattern_mode, length=operation.pattern_length,
                direction2=tuple(operation.pattern_direction2),
                spacing2=operation.spacing2, count2=operation.count2,
                mode2=operation.pattern_mode2,
                length2=operation.pattern_length2,
                reversed2=operation.reversed2,
                axis=tuple(operation.axis),
                axis_origin=tuple(operation.axis_origin),
                angle_deg=operation.angle,
                equal_spacing=operation.equal_spacing,
                angle_step=operation.angle_step,
                skip=list(operation.skip),
                plane_normal=normal, plane_origin=origin,
                reversed=operation.reversed, **common))
        if operation.kind == "hole_feature":
            return self._run_hole(operation, common)
        if operation.kind == "shell":
            return self.backend.shell(ShellRequest(
                faces=list(operation.faces),
                face_names=list(operation.face_names),
                thickness=operation.thickness,
                inward=not operation.outward, **common))
        if operation.kind == "draft":
            return self.backend.draft(DraftRequest(
                faces=list(operation.faces),
                face_names=list(operation.face_names),
                neutral=operation.neutral,
                neutral_name=operation.neutral_name,
                angle_deg=operation.angle,
                outward=operation.outward, **common))

        if operation.sketch is None:
            return engine_module.error(
                "NO_SKETCH", f"{operation.name}: эскиз не задан", ["sketch"])
        try:
            profile = engine_module.profile_of(
                operation.sketch, operation.regions,
                open_too=bool(operation.thin))
        except ValueError as failure:
            return engine_module.error(
                "REGION_LOST", f"{operation.name}: {failure}", ["profile"])

        if operation.kind == "hole":
            return self.backend.hole(HoleRequest(
                profile=profile, diameter=operation.diameter,
                depth=operation.length, through_all=operation.through,
                reversed=operation.reversed, **common))
        if operation.kind in ("revolve", "groove"):
            return self.backend.revolve(RevolveRequest(
                profile=profile, angle_deg=operation.angle,
                axis=tuple(operation.axis),
                axis_origin=tuple(operation.axis_origin),
                reversed=operation.reversed, subtract=operation.subtract,
                **common))
        return self.backend.pad(PadRequest(
            profile=profile,
            length=operation.length,
            end_condition=operation.end,
            reversed=operation.reversed,
            subtract=operation.subtract,
            # «До грани» просит грань — тем же номером, что и оболочка.
            target_face=int(operation.faces[0]) if operation.faces else -1,
            target_face_name=(operation.face_names[0]
                              if operation.face_names else ""),
            target_face2_name=operation.target2_name,
            start_offset=operation.start_offset,
            taper_angle_deg=operation.taper,
            thin=ThinType(operation.thin or ""),
            thickness=operation.thickness,
            thickness2=operation.thickness2,
            thin_flip=operation.thin_flip,
            direction2=operation.direction2,
            end_condition2=operation.end2,
            length2=operation.length2,
            taper_angle2_deg=operation.taper2,
            target_face2=operation.target2,
            **common))

    # --- то, что нужно окну ---------------------------------------------

    @property
    def mesh(self):
        """Сетка последнего удавшегося пересчёта. ``None`` — детали нет."""
        return self.result.mesh if self.result is not None else None

    @property
    def volume(self) -> float:
        return self.result.volume if self.result is not None else 0.0

    @property
    def bounds(self) -> list:
        return list(self.result.bounds) if self.result is not None else []

    def describe(self) -> list:
        """Дерево для показа. Ровно то, что нужно строке списка."""
        return [
            {
                "name": item.name,
                "kind": TITLES.get(item.kind, item.kind),
                "title": item.title,
                "ok": item.ok,
                "message": item.message,
                "note": item.note,
                "sketch": getattr(item.sketch, "name", ""),
                "plane": getattr(getattr(item.sketch, "plane", None), "name", ""),
                "regions": len(item.regions),
                "edges": len(item.edges),
                "faces": len(item.faces),
                "sources": list(item.sources),
            }
            for item in self.operations
        ]

    def by_name(self, name: str) -> Operation | None:
        for item in self.operations:
            if item.name == name:
                return item
        return None

    # --- дерево намерений как данные --------------------------------------

    def to_dict(self) -> dict:
        """Дерево намерений целиком: эскизы и что из них построено.

        Сохраняется НАМЕРЕНИЕ, а не геометрия. Геометрия есть у движка, и
        дублировать её здесь значило бы завести второй ответ на вопрос
        «какой формы деталь» — расходиться они начнут на первой же правке.

        Эскизы лежат списком, операции ссылаются на них НОМЕРОМ: один
        эскиз может быть исходным для нескольких операций, и записывать
        его дважды значило бы получить после открытия два разных эскиза
        вместо одного.
        """
        places = {id(sketch): number
                  for number, sketch in enumerate(self.sketches)}
        return {
            "schema": SCHEMA,
            "name": self.name,
            "designation": self.designation,
            "body_id": self.body_id,
            "rollback": self._rollback,
            "sketches": [sketch.to_dict() for sketch in self.sketches],
            "operations": [
                {
                    "kind": item.kind,
                    "name": item.name,
                    "sketch": places.get(id(item.sketch), -1),
                    "regions": [dict(reference) for reference in item.regions],
                    "length": item.length,
                    "end": item.end.value,
                    "reversed": item.reversed,
                    "merge": item.merge,
                    "start_offset": item.start_offset,
                    "taper": item.taper,
                    "direction2": item.direction2,
                    "end2": item.end2.value,
                    "length2": item.length2,
                    "taper2": item.taper2,
                    "target2": item.target2,
                    "edges": list(item.edges),
                    "edge_names": list(item.edge_names),
                    "face_names": list(item.face_names),
                    "target2_name": item.target2_name,
                    "size": item.size,
                    "chamfer_type": item.chamfer_type,
                    "size2": item.size2,
                    "flip": item.flip,
                    "axis": list(item.axis),
                    "axis_origin": list(item.axis_origin),
                    "angle": item.angle,
                    "diameter": item.diameter,
                    "through": item.through,
                    "faces": list(item.faces),
                    "thickness": item.thickness,
                    "outward": item.outward,
                    "neutral": item.neutral,
                    "neutral_name": item.neutral_name,
                    "thin": item.thin,
                    "thickness2": item.thickness2,
                    "thin_flip": item.thin_flip,
                    "scope": item.scope,
                    "sources": list(item.sources),
                    "whole_shape": item.whole_shape,
                    "count": item.count,
                    "spacing": item.spacing,
                    "direction": list(item.direction),
                    "pattern_mode": item.pattern_mode,
                    "pattern_length": item.pattern_length,
                    "pattern_direction2": list(item.pattern_direction2),
                    "spacing2": item.spacing2,
                    "count2": item.count2,
                    "pattern_mode2": item.pattern_mode2,
                    "pattern_length2": item.pattern_length2,
                    "reversed2": item.reversed2,
                    "equal_spacing": item.equal_spacing,
                    "angle_step": item.angle_step,
                    "skip": list(item.skip),
                    "plane_normal": list(item.plane_normal),
                    "plane_origin": list(item.plane_origin),
                    "plane_name": item.plane_name,
                    "hole": (item.hole.to_dict() if item.hole is not None
                             else None),
                }
                for item in self.operations
            ],
            "planes": [item.to_dict() for item in self.planes],
        }

    def load(self, data: dict, solver=None) -> None:
        """Восстановить дерево намерений. Геометрию считает движок заново.

        Пересчёт после чтения обязателен и делается вызывающим: смысл
        параметрической модели в том, что она ПЕРЕСТРАИВАЕТСЯ, а не в том,
        что сохранились треугольники.
        """
        from .sketch import Sketch

        self.operations.clear()
        self._stale = True
        self.sketches = [Sketch.from_dict(item, solver)
                         for item in (data.get("sketches") or ())]
        self.name = data.get("name") or self.name
        self.designation = data.get("designation") or self.designation
        self.body_id = data.get("body_id") or self.body_id
        rollback = data.get("rollback")
        self._rollback = None if rollback is None else int(rollback)
        highest = 0
        for record in data.get("operations") or ():
            number = int(record.get("sketch", -1))
            operation = Operation(
                kind=record.get("kind", "pad"),
                name=record.get("name", ""),
                sketch=self.sketches[number] if 0 <= number < len(self.sketches)
                else None,
                regions=[dict(item) for item in (record.get("regions") or ())],
                length=float(record.get("length", 10.0)),
                end=EndCondition(record.get("end", "blind")),
                reversed=bool(record.get("reversed")),
                merge=bool(record.get("merge", True)),
                start_offset=float(record.get("start_offset", 0.0)),
                taper=float(record.get("taper", 0.0)),
                direction2=bool(record.get("direction2")),
                end2=EndCondition(record.get("end2", "blind")),
                length2=float(record.get("length2", 10.0)),
                taper2=float(record.get("taper2", 0.0)),
                target2=int(record.get("target2", -1)),
                edges=list(record.get("edges") or ()),
                edge_names=list(record.get("edge_names") or ()),
                face_names=list(record.get("face_names") or ()),
                target2_name=record.get("target2_name", ""),
                size=float(record.get("size", 3.0)),
                axis=tuple(record.get("axis") or (0.0, 1.0, 0.0)),
                axis_origin=tuple(record.get("axis_origin") or (0.0, 0.0, 0.0)),
                angle=float(record.get("angle", 360.0)),
                diameter=float(record.get("diameter", 8.0)),
                through=bool(record.get("through", True)),
                faces=list(record.get("faces") or ()),
                thickness=float(record.get("thickness", 2.0)),
                outward=bool(record.get("outward", False)),
                neutral=int(record.get("neutral", -1)),
                neutral_name=str(record.get("neutral_name") or ""),
                chamfer_type=str(record.get("chamfer_type") or "equal"),
                size2=float(record.get("size2", 1.0)),
                flip=bool(record.get("flip", False)),
                thin=str(record.get("thin") or ""),
                thickness2=float(record.get("thickness2", 1.0)),
                thin_flip=bool(record.get("thin_flip", False)),
                scope=str(record.get("scope") or ""),
                sources=list(record.get("sources") or ()),
                whole_shape=bool(record.get("whole_shape")),
                count=int(record.get("count", 2)),
                spacing=float(record.get("spacing", 20.0)),
                direction=tuple(record.get("direction") or (1.0, 0.0, 0.0)),
                pattern_mode=str(record.get("pattern_mode") or "spacing"),
                pattern_length=float(record.get("pattern_length", 100.0)),
                pattern_direction2=tuple(
                    record.get("pattern_direction2") or (0.0, 1.0, 0.0)),
                spacing2=float(record.get("spacing2", 20.0)),
                count2=int(record.get("count2", 1)),
                pattern_mode2=str(record.get("pattern_mode2") or "spacing"),
                pattern_length2=float(record.get("pattern_length2", 100.0)),
                reversed2=bool(record.get("reversed2", False)),
                equal_spacing=bool(record.get("equal_spacing", True)),
                angle_step=float(record.get("angle_step", 30.0)),
                skip=[int(v) for v in (record.get("skip") or ())],
                plane_normal=tuple(record.get("plane_normal") or (1.0, 0.0, 0.0)),
                plane_origin=tuple(record.get("plane_origin") or (0.0, 0.0, 0.0)),
                plane_name=str(record.get("plane_name") or ""),
                hole=(_hole_from(record.get("hole"))
                      if record.get("hole") else None),
            )
            self.operations.append(operation)
            highest = max(highest, _trailing_number(operation.name))
        self.planes = [reference_module.ReferencePlane.from_dict(item)
                       for item in (data.get("planes") or ())]
        # Нумерация продолжается с большего из прочитанных: иначе новая
        # операция получит имя, которое в дереве уже есть, и ссылки массива
        # начнут указывать не туда.
        self._numbers = itertools.count(highest + 1)

    @classmethod
    def from_dict(cls, data: dict, backend=None, solver=None) -> "Document":
        document = cls(data.get("name") or "Деталь", backend=backend,
                       designation=data.get("designation") or "")
        document.load(data, solver)
        return document

    # --- сечение ---------------------------------------------------------

    def section(self, plane, body: str = ""):
        """След детали на плоскости — рёбрами в координатах детали.

        Тело не указано — режутся все: деталь бывает из нескольких, и след
        одного выглядел бы как контур с пропавшим куском.

        Считает движок (`SectionRequest`): пересечение — это геометрия, и
        второго ядра для неё в окне заводить нельзя (§26).
        """
        return self.backend.section(SectionRequest(
            document_id=self.document_id, body_id=body,
            origin=tuple(float(value) for value in plane.origin),
            normal=tuple(float(value) for value in plane.normal)))

    # --- сохранение ------------------------------------------------------

    def save(self, path) -> FeatureResult:
        return self.backend.save(self.document_id, path)

    def open(self, path) -> FeatureResult:
        """Открыть документ ДВИЖКА (его собственный формат).

        Дерево намерений при этом НЕ восстанавливается: в файле движка
        лежат его операции, а наши записи — про эскизы и выбранные
        области. Для них есть ``to_dict``/``load``, и хранятся они в
        контейнере ProtoCAD.
        """
        self.result = self.backend.open(self.document_id, path)
        if self.result.ok:
            self.result = self.backend.scene(self.document_id, self.body_id)
        return self.result

    def close(self) -> None:
        if hasattr(self.backend, "shutdown"):
            self.backend.shutdown()


def _note_of(answer: FeatureResult) -> str:
    for item in answer.diagnostics:
        if item.severity == "warning":
            return item.message
    return ""


def _forget_names(operation: Operation) -> None:
    """Забыть имена подэлементов: они от прежнего документа движка."""
    operation.edge_names = []
    operation.face_names = []
    operation.target2_name = ""
    operation.neutral_name = ""


def _catch_names(operation: Operation, before: FeatureResult) -> None:
    """Записать свежие имена подэлементов по их номерам.

    ``before`` — ответ ПРЕДЫДУЩЕЙ операции: именно на её форме операция
    называет свои рёбра и грани, и имена там того же охвата, что нужен
    движку.
    """
    names = {(item.kind, item.index): item.name for item in before.entities}
    operation.edge_names = [names.get(("edge", int(value)), "")
                            for value in operation.edges]
    operation.face_names = [names.get(("face", int(value)), "")
                            for value in operation.faces]
    if operation.target2 >= 0:
        operation.target2_name = names.get(("face", int(operation.target2)), "")
    if operation.neutral >= 0:
        operation.neutral_name = names.get(("face", int(operation.neutral)), "")


#: Насколько нормали граней считаются одинаковыми при поиске опоры.
SAME_NORMAL = 1e-3


def _face_by_mark(faces, mark: str):
    """Плоскость грани по описанию «нормаль и удаление». ``None`` — нет.

    Точное совпадение описания не годится: удаление в него и входит, а
    опора нужна как раз тогда, когда грань ПЕРЕЕХАЛА — плита выросла с 60
    до 90, и описание перестало совпадать с самим собой.

    Ищется по НАПРАВЛЕНИЮ: у переехавшей грани нормаль та же. Если
    сонаправленных несколько, берётся ближайшая к прежнему удалению.
    """
    from .sketch.plane import Plane

    try:
        values = [float(piece) for piece in mark.split(",")]
    except ValueError:
        return None
    if len(values) < 4:
        return None
    wanted, offset = tuple(values[:3]), values[3]
    best, best_gap = None, None
    for face in faces:
        data = face.data or {}
        normal = data.get("normal")
        centre = data.get("center")
        if not normal or not centre:
            continue
        along = sum(float(normal[i]) * wanted[i] for i in range(3))
        if along < 1.0 - SAME_NORMAL:
            continue
        gap = abs(sum(float(normal[i]) * float(centre[i])
                      for i in range(3)) - offset)
        if best_gap is None or gap < best_gap:
            best, best_gap = (tuple(float(v) for v in centre),
                              tuple(float(v) for v in normal)), gap
    if best is None:
        return None
    origin, normal = best
    return Plane(origin=origin, normal=normal,
                 x_direction=reference_module._across(normal))


#: Насколько два одинаково подходящих ребра считаются НЕРАЗЛИЧИМЫМИ, мм.
#: Если ближайшее и следующее за ним отстоят меньше чем на столько, выбор
#: между ними — гадание, и опора объявляется потерянной.
SAME_PLACE = 1e-3


def _edge_by_mark(edges, mark: str):
    """Концы прямого ребра по описанию «середина и длина». ``None`` — нет.

    Как и у грани, точное совпадение не годится: в описание входит место, а
    опора нужна именно тогда, когда ребро переехало. Ищется по
    НАПРАВЛЕНИЮ и длине, из подходящих берётся ближайшее к прежней
    середине.

    Но у бруска четыре одинаковых вертикальных ребра, и «ближайшее» между
    ними — гадание. Поэтому при неоднозначности опора объявляется
    потерянной: эскиз скажет об этом, и место укажут заново. Молча взять
    не то ребро — худший исход, о нём никто не узнает.
    """
    try:
        values = [float(piece) for piece in mark.strip("()").split(",")]
    except ValueError:
        return None
    if len(values) < 4:
        return None
    middle, length = tuple(values[:3]), values[3]
    scored = []
    for edge in edges:
        data = edge.data or {}
        start, finish = data.get("start"), data.get("end")
        if not start or not finish:
            continue
        if abs(float(data.get("length", 0.0)) - length) > 1e-3:
            continue
        spot = data.get("middle") or [
            (float(start[i]) + float(finish[i])) / 2.0 for i in range(3)]
        gap = sum((float(spot[i]) - middle[i]) ** 2 for i in range(3)) ** 0.5
        scored.append((gap, tuple(float(v) for v in start),
                       tuple(float(v) for v in finish)))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0])
    if len(scored) > 1 and scored[1][0] - scored[0][0] < SAME_PLACE:
        return None
    return scored[0][1], scored[0][2]


def _plane_trace(sketch_plane, face_plane, reach: float = 500.0):
    """След одной плоскости на другой — в плоских координатах эскиза.

    ``None`` — плоскости параллельны и следа нет. Длина следа условная:
    прямая вспомогательная, важно её положение и направление.
    """
    import numpy as _np

    first = _np.array(sketch_plane.normal, float)
    second = _np.array(face_plane.normal, float)
    along = _np.cross(first, second)
    if float(_np.linalg.norm(along)) < 1e-9:
        return None
    along = along / float(_np.linalg.norm(along))
    matrix = _np.array([first, second, along])
    right = _np.array([
        float(_np.dot(first, _np.array(sketch_plane.origin, float))),
        float(_np.dot(second, _np.array(face_plane.origin, float))),
        0.0,
    ])
    try:
        point = _np.linalg.solve(matrix, right)
    except Exception:  # noqa: BLE001 — вырожденная система
        return None
    return (sketch_plane.project(tuple(point - along * reach)),
            sketch_plane.project(tuple(point + along * reach)))


def _face_signature(face, precision: int = 3) -> tuple:
    """Опознавательная подпись грани: центр масс и площадь.

    Номер грани для этого не годится — обход меняется при любой правке
    дерева. Подпись переживает пересчёт, пока сама грань не сдвинулась.
    """
    centre = face.data.get("center") or (0.0, 0.0, 0.0)
    return tuple(round(float(value), precision) for value in centre) + (
        round(float(face.data.get("area", 0.0)), precision),)


def _face_signatures(answer: FeatureResult) -> tuple:
    return tuple(_face_signature(item) for item in answer.entities
                 if item.kind == "face")


def _trailing_number(name: str) -> int:
    """Число в конце имени операции. Ноль — его там нет."""
    digits = ""
    for character in reversed(name):
        if not character.isdigit():
            break
        digits = character + digits
    return int(digits) if digits else 0


def pattern_places(operation, seed) -> list:
    """Где стоят экземпляры массива. ``[(номер, точка), …]``.

    Считается ТЕМИ ЖЕ правилами, по которым массив строит движок: иначе
    щелчок по экземпляру попадал бы не в тот, который потом пропадёт.
    Нулевой номер — сам исходник; он в списке есть, но пропускать его
    нельзя, и это решает вызывающий.

    ``seed`` — точка исходной операции в пространстве детали. Своей
    геометрии у записи дерева нет, и брать её неоткуда, кроме как у того,
    кто вызывает: он же и знает, что размножается.
    """
    import math as _math

    places = []
    if operation.kind == "linear":
        first = tuple(float(value) for value in operation.direction)
        second = tuple(float(value) for value in operation.pattern_direction2)
        step = float(operation.spacing)
        step2 = float(operation.spacing2)
        if operation.pattern_mode == "extent":
            step = float(operation.pattern_length) / max(1, operation.count - 1)
        if operation.pattern_mode2 == "extent":
            step2 = float(operation.pattern_length2) / max(1, operation.count2 - 1)
        sign = -1.0 if operation.reversed else 1.0
        sign2 = -1.0 if operation.reversed2 else 1.0
        number = 0
        for row in range(max(1, int(operation.count2))):
            for column in range(max(1, int(operation.count))):
                places.append((number, tuple(
                    seed[i] + sign * column * step * first[i]
                    + sign2 * row * step2 * second[i] for i in range(3))))
                number += 1
        return places
    if operation.kind == "polar":
        axis = tuple(float(value) for value in operation.axis)
        length = sum(value * value for value in axis) ** 0.5 or 1.0
        axis = tuple(value / length for value in axis)
        origin = tuple(float(value) for value in operation.axis_origin)
        count = max(2, int(operation.count))
        angle = float(operation.angle)
        if operation.equal_spacing:
            step = (angle / count if abs(angle - 360.0) < 1e-9
                    else angle / max(1, count - 1))
        else:
            step = float(operation.angle_step)
        if operation.reversed:
            step = -step
        for number in range(count):
            turn = _math.radians(step * number)
            places.append((number, _turned(seed, origin, axis, turn)))
        return places
    return places


def _turned(point, origin, axis, angle: float) -> tuple:
    """Точка, повёрнутая вокруг оси. Формула Родрига."""
    import math as _math

    relative = tuple(point[i] - origin[i] for i in range(3))
    cos, sin = _math.cos(angle), _math.sin(angle)
    dot = sum(relative[i] * axis[i] for i in range(3))
    cross = (axis[1] * relative[2] - axis[2] * relative[1],
             axis[2] * relative[0] - axis[0] * relative[2],
             axis[0] * relative[1] - axis[1] * relative[0])
    return tuple(origin[i] + relative[i] * cos + cross[i] * sin
                 + axis[i] * dot * (1.0 - cos) for i in range(3))


def _hole_from(data):
    """Запись отверстия → описание. Ленивый ввоз: подсистема тяжёлая."""
    from .holes import HoleFeature

    return HoleFeature.from_dict(data)
