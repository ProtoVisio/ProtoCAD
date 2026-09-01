"""Сеанс команды: аргументы, активное поле, проверка, предпросмотр.

Разница с прежним устройством не в оформлении. Модальное окно — это форма,
которая задаёт вопросы и возвращает числа; сеанс команды — это **редактор
незавершённой операции**: у него есть поля выбора, одно из них активно, и
следующий щелчок в трёхмерном виде попадает именно в него. Предпросмотр
пересобирается на каждое изменение и не трогает ни документ, ни дерево, ни
историю отмены.

Модуль намеренно без Qt. Здесь маршрутизация щелчков, состояния и проверки
— то, что должно работать и проверяться без окна. Панель поверх него —
отдельный слой, который только показывает и передаёт события.

Три уровня проверки разведены, потому что отвечают на разные вопросы и
по-разному сообщаются человеку:

* **входная** — заполнены ли обязательные поля; пока нет, команду нельзя
  даже пробовать;
* **смысловая** — годятся ли типы и количества, не противоречат ли
  параметры друг другу;
* **геометрическая** — строит ли ядро результат; ответ известен только
  после попытки.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Callable

# Что вообще можно выбрать. Список общий с трёхмерным видом: он присылает
# ровно эти виды, и расхождение здесь означало бы, что щелчок не попадает
# никуда без всякого сообщения.
ENTITY_KINDS = (
    "sketch", "face", "edge", "vertex", "plane", "body", "feature",
    # Область эскиза — то, что в SolidWorks зовётся Selected Contour.
    # Выбирается щелчком по залитой области показанного эскиза.
    "sketch_region",
)


@dataclass(frozen=True)
class Pick:
    """Выбранная сущность.

    Равенство — по виду и номеру, а не по вложенному объекту: повторный
    щелчок по той же грани обязан её СНЯТЬ, а объект ядра при пересчёте
    подменяется другим экземпляром с теми же координатами.
    """

    kind: str
    id: int = 0
    label: str = ""
    data: Any = field(default=None, compare=False, hash=False)

    def __post_init__(self):
        if self.kind not in ENTITY_KINDS:
            raise ValueError(f"неизвестный вид сущности: {self.kind!r}")


@dataclass
class SelectionBox:
    """Поле выбора: что принимает, сколько и в каком порядке."""

    id: str
    label: str
    accepts: tuple[str, ...]
    minimum: int = 1
    maximum: int | None = None      # None — сколько угодно
    ordered: bool = False
    #: переходить ли к следующему полю, когда это заполнено
    auto_advance: bool = True
    items: list = field(default_factory=list)
    #: (session) -> bool. Пусто — поле видно всегда. Невидимое поле не
    #: показывается, не принимает щелчков и НЕ ТРЕБУЕТСЯ для готовности:
    #: спрашивать грань, когда концевое условие её не использует, значит
    #: держать команду незавершённой из-за поля, которого не видно.
    visible_when: Any = None

    def accepts_kind(self, kind: str) -> bool:
        return kind in self.accepts

    @property
    def complete(self) -> bool:
        return len(self.items) >= self.minimum

    @property
    def full(self) -> bool:
        """Заполнено ДО ПРЕДЕЛА — только тогда переход дальше однозначен.

        Для списков без верхней границы перехода не бывает: человек ещё не
        закончил выбирать рёбра, а поле уже сменилось бы под рукой.
        """
        return self.maximum is not None and len(self.items) >= self.maximum

    def toggle(self, pick: Pick) -> str:
        """Добавить или снять. Возвращает, что произошло."""
        if not self.accepts_kind(pick.kind):
            return "rejected"
        if pick in self.items:
            self.items.remove(pick)
            return "removed"
        if self.maximum is not None and len(self.items) >= self.maximum:
            # Поле уже полно: заменяем последнее, а не молча теряем щелчок.
            # Иначе указание «до этой грани» после промаха исправить нельзя.
            self.items[-1] = pick
            return "replaced"
        self.items.append(pick)
        return "added"

    def clear(self) -> None:
        self.items.clear()


@dataclass
class Parameter:
    """Число, флажок или список — всё, что не выбирается в виде."""

    key: str
    label: str
    kind: str = "number"           # number | integer | choice | flag | text
    default: Any = 0.0
    minimum: float = -1.0e6
    maximum: float = 1.0e6
    decimals: int = 3
    choices: tuple = ()
    suffix: str = ""
    #: ключ флажка, при выключении которого параметр не показывается
    depends_on: str = ""
    #: (session) -> bool. Условие посложнее флажка: «видно при Blind».
    visible_when: Any = None


@dataclass
class Group:
    """Группа панели: заголовок, поля выбора и параметры."""

    title: str
    boxes: list = field(default_factory=list)
    parameters: list = field(default_factory=list)
    #: группа целиком включается флажком (второе направление, тонкая стенка)
    toggle_key: str = ""
    collapsed: bool = False
    #: (session) -> bool. Группа целиком показывается по условию.
    visible_when: Any = None


@dataclass
class Validation:
    """Ответ на вопрос «можно ли строить» с указанием, где именно затык."""

    level: str = "input"           # input | semantic | geometric
    ok: bool = True
    message: str = ""
    box_id: str = ""               # поле, к которому относится замечание

    @property
    def blocking(self) -> bool:
        return not self.ok


# Состояния сеанса. Заведены явно, потому что от них зависит поведение
# кнопок и предпросмотра, а «догадываться по набору заполненных полей»
# каждый раз означает догадываться по-разному.
CREATED = "created"
COLLECTING = "collecting"
PREVIEW_VALID = "preview_valid"
PREVIEW_FAILED = "preview_failed"
COMMITTED = "committed"
CANCELLED = "cancelled"

# Состояния предпросмотра.
NO_PREVIEW = "none"
PREVIEW_OK = "ok"
PREVIEW_ERROR = "failed"


@dataclass
class CommandDescriptor:
    """Описание команды: что спрашивает и что строит."""

    key: str
    title: str
    groups: list = field(default_factory=list)
    #: какие виды сущностей забирать из предварительного выбора
    preselection: tuple[str, ...] = ()
    needs_body: bool = True
    #: (session, context) -> Feature
    build: Callable | None = None
    #: (session, context) -> Validation | None — смысловая проверка
    check: Callable | None = None
    #: (session, operation, context) -> None — заполнить панель значениями
    #: УЖЕ построенной операции. Без этого правку пришлось бы делать
    #: отдельным окном, а окно и панель разошлись бы на первой же новой
    #: возможности: одна и та же команда обязана спрашивать одно и то же,
    #: создаётся она или правится.
    load: Callable | None = None

    def all_boxes(self) -> list:
        return [box for group in self.groups for box in group.boxes]

    def all_parameters(self) -> list:
        return [item for group in self.groups for item in group.parameters]


class CommandSession:
    """Незавершённая операция: аргументы собираются, результат ещё не создан."""

    def __init__(self, descriptor: CommandDescriptor, context=None,
                 preselection=()):
        self.descriptor = descriptor
        self.context = context
        self.state = CREATED
        self.preview_state = NO_PREVIEW
        self.preview_shape = None
        self.diagnostics = ""
        # Поля выбора КОПИРУЮТСЯ. Описание команды одно на всё приложение;
        # если сеанс станет писать выбор прямо в него, второй вызов той же
        # команды начнётся с рёбрами, выбранными в первый раз, — и скруглит
        # не то, что указали. Обнаруживается это только по объёму.
        self._order = [
            replace(box, items=list(box.items)) for box in descriptor.all_boxes()
        ]
        self._boxes = {box.id: box for box in self._order}
        self._values = {
            parameter.key: parameter.default
            for parameter in descriptor.all_parameters()
        }
        self.active_box_id = ""
        self._take_preselection(preselection)
        self._activate_first_missing()
        self.state = COLLECTING

    # --- предварительный выбор ---

    def _take_preselection(self, preselection) -> None:
        """Забрать из выбранного только то, что подходит.

        Несовместимые сущности НЕ попадают никуда: команда, молча
        поменявшая смысл из-за постороннего выбора, — источник деталей,
        построенных не по тому, что имели в виду.
        """
        for pick in preselection:
            for box in self._boxes.values():
                if box.accepts_kind(pick.kind) and not box.full:
                    box.toggle(pick)
                    break

    def _activate_first_missing(self) -> None:
        for box in self.visible_boxes():
            if not box.complete:
                self.active_box_id = box.id
                return
        shown = self.visible_boxes()
        self.active_box_id = shown[0].id if shown else ""

    # --- поля ---

    @property
    def boxes(self) -> list:
        return list(self._order)

    def box(self, box_id: str) -> SelectionBox | None:
        return self._boxes.get(box_id)

    @property
    def active_box(self) -> SelectionBox | None:
        return self._boxes.get(self.active_box_id)

    def activate(self, box_id: str) -> bool:
        if box_id not in self._boxes:
            return False
        self.active_box_id = box_id
        return True

    def click(self, pick: Pick) -> str:
        """Щелчок в виде. Возвращает, что произошло с активным полем."""
        box = self.active_box
        if box is None or self._box_disabled(box):
            # Условие изменилось, и активное поле спряталось. Фокус
            # переносится на первое видимое, а щелчок не теряется.
            self._activate_first_missing()
            box = self.active_box
        if box is None:
            return "rejected"
        result = box.toggle(pick)
        if result == "rejected":
            self.diagnostics = (
                f"«{box.label}» принимает: {', '.join(box.accepts)}"
            )
            return result
        self.diagnostics = ""
        if result in ("added", "replaced"):
            self._maybe_advance(box)
        return result

    def _maybe_advance(self, box: SelectionBox) -> None:
        """Перейти к следующему незаполненному полю, если это поле закрыто.

        Невидимые поля пропускаются: фокус, уехавший в поле, которого нет
        на экране, выглядит как «щелчки перестали приниматься».
        """
        if not box.auto_advance or not box.full:
            return
        shown = self.visible_boxes()
        if box not in shown:
            return
        position = shown.index(box)
        for following in shown[position + 1:]:
            if not following.complete:
                self.active_box_id = following.id
                return

    def clear(self, box_id: str = "") -> None:
        target = self._boxes.get(box_id or self.active_box_id)
        if target is not None:
            target.clear()

    # --- параметры ---

    def value(self, key: str, fallback=None):
        return self._values.get(key, fallback)

    def set_value(self, key: str, value) -> None:
        """Задать параметр. Смена значения может открыть новое поле.

        Спецификация требует перехода: выбрали «до грани» — стало активным
        поле цели (`editing_direction_1 → waiting_target_1`). Без него
        человек выбирает условие, щёлкает по грани и не понимает, почему
        ничего не происходит: щелчок уходит в поле профиля, которое и так
        заполнено.

        Фокус переносится ТОЛЬКО если текущее поле уже закрыто или
        спряталось. Иначе смена числа отбирала бы фокус посреди набора
        рёбер.
        """
        self._values[key] = value
        current = self.active_box
        if current is not None and not self._box_disabled(current) \
                and not current.complete:
            return
        for box in self.visible_boxes():
            if not box.complete:
                self.active_box_id = box.id
                return

    @property
    def values(self) -> dict:
        return dict(self._values)

    def shown(self, item) -> bool:
        """Видно ли поле или группу сейчас.

        Условие — это функция от сеанса, а не строка: выражение из YAML
        разбирать пришлось бы своим языком, а язык нужен разработчику
        ровно один — тот, на котором написано остальное.
        """
        condition = getattr(item, "visible_when", None)
        return True if condition is None else bool(condition(self))

    def visible_groups(self) -> list:
        return [group for group in self.descriptor.groups
                if self.shown(group)
                and not (group.toggle_key
                         and group.collapsed
                         and not self._values.get(group.toggle_key))]

    def visible_parameters(self) -> list:
        """Параметры, которые сейчас имеет смысл показывать.

        Группа, выключенная флажком, не показывает своих полей: список из
        двадцати строк, половина которых ни на что не влияет, читается
        хуже, чем короткий.
        """
        result = []
        for group in self.descriptor.groups:
            if group.toggle_key and not self._values.get(group.toggle_key):
                continue
            if not self.shown(group):
                continue
            for parameter in group.parameters:
                if parameter.depends_on and not self._values.get(parameter.depends_on):
                    continue
                if not self.shown(parameter):
                    continue
                result.append(parameter)
        return result

    def visible_boxes(self) -> list:
        """Поля выбора, которые сейчас показываются и принимают щелчки."""
        return [box for box in self._order
                if self.shown(box) and not self._box_disabled(box)]

    # --- проверка ---

    def validate(self) -> Validation:
        for box in self._order:
            if self._box_disabled(box):
                continue
            if not box.complete:
                need = box.minimum - len(box.items)
                return Validation(
                    "input", False,
                    f"«{box.label}»: не хватает объектов ({need})", box.id,
                )
        if self.descriptor.check is not None:
            found = self.descriptor.check(self, self.context)
            if found is not None and found.blocking:
                return found
        return Validation("semantic", True)

    def _box_disabled(self, box: SelectionBox) -> bool:
        if not self.shown(box):
            return True
        for group in self.descriptor.groups:
            # Сравнение по номеру, а не по объекту: поля сеанса — копии.
            if group.toggle_key and any(item.id == box.id for item in group.boxes):
                if not self._values.get(group.toggle_key):
                    return True
                if not self.shown(group):
                    return True
        return False

    @property
    def ready(self) -> bool:
        return not self.validate().blocking

    # --- предпросмотр ---

    def build_preview(self, evaluate: Callable) -> str:
        """Собрать предпросмотр. ``evaluate`` строит форму по сеансу.

        Ни документ, ни дерево, ни история отмены при этом не меняются —
        иначе отменённая команда оставляла бы за собой след, а «отмена»
        превращалась бы в ещё одну правку.
        """
        check = self.validate()
        if check.blocking:
            self.preview_state = NO_PREVIEW
            self.preview_shape = None
            self.diagnostics = check.message
            self.state = COLLECTING
            return NO_PREVIEW
        try:
            self.preview_shape = evaluate(self)
        except Exception as error:  # noqa: BLE001 — отказ ядра это ответ
            self.preview_shape = None
            self.preview_state = PREVIEW_ERROR
            self.diagnostics = f"{type(error).__name__}: {error}"
            self.state = PREVIEW_FAILED
            return PREVIEW_ERROR
        self.preview_state = PREVIEW_OK
        self.diagnostics = ""
        self.state = PREVIEW_VALID
        return PREVIEW_OK

    # --- завершение ---

    def commit(self):
        """Создать операцию. Отказ проверки — исключение, а не тихий None."""
        check = self.validate()
        if check.blocking:
            raise CommandError(check.message)
        if self.descriptor.build is None:
            raise CommandError(f"{self.descriptor.title}: нечего строить")
        feature = self.descriptor.build(self, self.context)
        self.state = COMMITTED
        return feature

    def cancel(self) -> None:
        self.state = CANCELLED
        self.preview_state = NO_PREVIEW
        self.preview_shape = None

    def __repr__(self) -> str:
        return (
            f"<CommandSession {self.descriptor.key} {self.state} "
            f"поле={self.active_box_id!r}>"
        )


class CommandError(RuntimeError):
    """Команду выполнить нельзя, и причина названа."""
