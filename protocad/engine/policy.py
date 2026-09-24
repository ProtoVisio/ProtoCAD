"""Правила ProtoCAD поверх любого движка.

По `docs/08_ENGINE_BACKEND.md`, §7.5: автоматический разворот операции и
пользовательские проверки — высокоуровневая семантика ProtoCAD, а не
обязанность движка. FreeCAD её и не берёт на себя: `PartDesign::Pocket` с
эскизом на нижней грани честно режет вниз, мимо детали, и сообщает об
успехе. Формально он прав — просили именно это.

Поэтому правила живут ЗДЕСЬ, над движком, и применяются одинаково к
любому. Иначе прототип и FreeCAD ведут себя по-разному на одном и том же
запросе, а интерфейс приходится учить двум движкам вместо одного.

Правил два:

1. **Разворот по материалу.** Если операция ничего не изменила, она
   повторяется в обратную сторону. Нормаль плоскости эскиза не всегда
   смотрит туда, куда человек имел в виду: грань, снятая с тела после
   булевой операции, бывает ориентирована внутрь.
2. **Ничего не произошло — это отказ.** Операция, не изменившая объём ни
   в одну сторону, возвращает не «успех с нулём», а внятный отказ.
   Молчаливый успех выглядит на экране как пропавшая команда.
"""

from __future__ import annotations

from dataclasses import replace

from .protocol import (HELIX_MODES, SWEEP_MODES, SWEEP_TRANSITIONS,
                       Diagnostic, FeatureResult, PadRequest, Status, error)

#: Насколько должен измениться объём, чтобы операция считалась
#: состоявшейся, мм³. Не ноль: булева операция оставляет крохи на стыке
#: граней, и строгое сравнение принимало бы их за работу.
MINIMUM = 1e-6


def _key(request) -> tuple:
    """Чьё это тело. Документ И имя тела, а не одно имя.

    «Тело» называется так в каждой детали. Пока ключом было одно имя, две
    детали на одном движке подменяли друг другу «объём до».
    """
    return (getattr(request, "document_id", ""), request.body_id)


class Policy:
    """Движок с правилами ProtoCAD. Снаружи выглядит как обычный движок."""

    def __init__(self, backend):
        self.backend = backend
        self.name = backend.name
        #: Объём тела до операции. Движок сообщает объём ПОСЛЕ, а судить
        #: о том, что операция сделала, можно только по разнице.
        #:
        #: Ключ — документ И тело. По одному имени тела не годится: имя
        #: «Тело» есть в каждой детали, и две детали на одном движке
        #: подменяли друг другу «объём до». Вторая деталь получала чужое
        #: число, разница выходила нулевой, срабатывал разворот — и вместо
        #: одной операции создавались две.
        self._volumes: dict = {}

    def capabilities(self):
        return self.backend.capabilities()

    def __getattr__(self, name):
        """Всё, чего нет в правилах, спрашивается у движка.

        Сохранение, открытие и выдача сцены правил не требуют: они ничего
        не строят. Перечислять их здесь значило бы дублировать движок ради
        того, чтобы ничего не добавить.
        """
        return getattr(self.backend, name)

    def available(self) -> tuple:
        return self.backend.available()

    def clear(self, document_id: str) -> None:
        """Забыть один документ — и у движка, и в собственном учёте."""
        for key in [key for key in self._volumes if key[0] == document_id]:
            self._volumes.pop(key, None)
        if hasattr(self.backend, "clear"):
            self.backend.clear(document_id)

    def reset(self) -> None:
        self._volumes.clear()
        if hasattr(self.backend, "reset"):
            self.backend.reset()

    def shutdown(self) -> None:
        if hasattr(self.backend, "shutdown"):
            self.backend.shutdown()

    def pad(self, request: PadRequest) -> FeatureResult:
        # Объём тела спрашивается ДО операции. После неё он уже включает
        # её вклад, разница выходит нулевой, срабатывает разворот — и
        # вместо одной операции создаются две. Ровно это и происходило:
        # первый же пересчёт давал двойной объём.
        before = None if request.feature_id else self._known_volume(request)
        result = self.backend.pad(request)
        if not result.ok:
            return result
        result.reversed = request.reversed

        if request.feature_id:
            # Правка СУЩЕСТВУЮЩЕЙ операции. Сравнивать «до» и «после»
            # здесь бессмысленно: движок пересчитывает всё тело, и в
            # «до» уже входит вклад самой этой операции. Повторный
            # пересчёт без изменений законно даёт тот же объём — а
            # правило приняло бы это за «ничего не произошло» и отвергло
            # операцию, которая на самом деле работает.
            self._volumes[_key(request)] = result.volume
            return result

        if self._changed(before, result.volume):
            # Предпросмотр НЕ меняет учёт: он ничего не построил, и
            # запомненный объём должен остаться прежним. Иначе следующая
            # операция сравнивает себя с тем, чего в детали нет, и
            # «ничего не произошло» срабатывает на ровном месте.
            if not request.preview:
                self._volumes[_key(request)] = result.volume
            return result

        # Ничего не изменилось. Пробуем обратную сторону — ТОЙ ЖЕ
        # операцией: ей передаётся имя уже созданной, и движок правит её на
        # месте. Пока сюда уходил новый запрос, неудачная попытка
        # оставалась в теле лишней операцией: сейчас она ничего не меняет,
        # а после правки эскиза выше по дереву начинает резать деталь — и
        # выглядит это как «сломалась первая операция».
        flipped = self.backend.pad(replace(
            request, reversed=not request.reversed,
            feature_id=result.feature_id or request.feature_id))
        if flipped.ok and self._changed(before, flipped.volume):
            flipped.reversed = not request.reversed
            if not request.preview:
                self._volumes[_key(request)] = flipped.volume
            flipped.diagnostics.append(Diagnostic(
                "REVERSED_AUTOMATICALLY",
                (f"{request.body_id}: выполнено в обратную сторону — "
                 f"по нормали плоскости эскиза операция уходила мимо материала"),
                "warning", ["reversed"], self.name,
            ))
            return flipped

        # Не помогла ни одна сторона — убираем за собой. Операция,
        # созданная и ничего не изменившая, останется в дереве движка и
        # начнёт мешать при первой же правке выше по дереву.
        self._take_back(request, result)

        # Обратная сторона ОТКАЗАЛА — и отказ у неё внятный. Отвечаем им, а
        # не своим «не задевает деталь»: вырез, съевший тело целиком, не
        # задевает его меньше всего, и такое объяснение уводит в сторону.
        if not flipped.ok:
            return flipped

        # Не помогла и обратная. Это уже не про сторону.
        if request.subtract:
            return error(
                "NO_MATERIAL_REMOVED",
                f"{request.body_id}: ничего не снято — инструмент не задевает "
                f"деталь. Проверьте плоскость эскиза, направление и длину",
                ["length", "end_condition"], self.name)
        return error(
            "NO_MATERIAL_ADDED",
            f"{request.body_id}: ничего не добавлено — инструмент целиком "
            f"внутри детали. Проверьте плоскость эскиза, направление и длину",
            ["length", "end_condition"], self.name)

    def dress_up(self, request) -> FeatureResult:
        """Скругление и фаска. Разворачивать здесь нечего — сторона у них
        не выбирается, — но «ничего не произошло» проверяется так же.

        Операция без выбранных рёбер отвергается до движка: у FreeCAD она
        в этом случае просто ничего не делает и отчитывается успехом, а на
        экране это выглядит как пропавшая команда.
        """
        if not request.edges and not request.faces:
            return error("NO_EDGES",
                         f"{request.body_id}: не выбрано ни одного ребра",
                         ["edges"], self.name)
        if request.size <= 0.0:
            return error("BAD_SIZE",
                         f"{request.body_id}: размер должен быть больше нуля",
                         ["size"], self.name)
        return self._guarded(
            self.backend.dress_up, request, ["edges", "size"],
            f"{request.body_id}: форма не изменилась — проверьте, те ли рёбра "
            f"выбраны и не мал ли размер")

    def draft(self, request) -> FeatureResult:
        """Уклон. Отказ до движка на том, что он молча стерпит.

        Уклон нулевого угла у FreeCAD строится и не меняет ничего: в
        дереве появляется операция, которая ничего не делает.
        """
        if not request.faces:
            return error("NO_FACES",
                         f"{request.body_id}: не выбрано ни одной грани",
                         ["faces"], self.name)
        if request.angle_deg <= 0.0:
            return error("BAD_ANGLE",
                         f"{request.body_id}: угол уклона должен быть больше "
                         f"нуля", ["angle_deg"], self.name)
        return self._guarded(
            self.backend.draft, request, ["angle_deg", "faces"],
            f"{request.body_id}: форма не изменилась — проверьте, те ли "
            f"грани выбраны и не мал ли угол")

    def revolve(self, request) -> FeatureResult:
        """Вращение. Автоматического разворота здесь нет: у выдавливания
        сторона выбирается из двух, а вращение либо строится, либо режет
        само себя, и молча пробовать обратную сторону значило бы иногда
        строить не то, что просили."""
        if request.angle_deg <= 0.0:
            return error("BAD_ANGLE",
                         f"{request.body_id}: угол должен быть больше нуля",
                         ["angle_deg"], self.name)
        return self._guarded(
            self.backend.revolve, request, ["angle_deg", "axis"],
            f"{request.body_id}: форма не изменилась — проверьте ось "
            f"вращения и угол")

    def hole(self, request) -> FeatureResult:
        if request.diameter <= 0.0:
            return error("BAD_DIAMETER",
                         f"{request.body_id}: диаметр должен быть больше нуля",
                         ["diameter"], self.name)
        return self._guarded(
            self.backend.hole, request, ["diameter", "depth"],
            f"{request.body_id}: ничего не просверлено — отверстие проходит "
            f"мимо материала")

    def shell(self, request) -> FeatureResult:
        if not request.faces:
            return error("NO_FACES",
                         f"{request.body_id}: не выбрано ни одной грани — "
                         f"оболочка не знает, где открыть",
                         ["faces"], self.name)
        if request.thickness <= 0.0:
            return error("BAD_THICKNESS",
                         f"{request.body_id}: толщина должна быть больше нуля",
                         ["thickness"], self.name)
        return self._guarded(
            self.backend.shell, request, ["thickness", "faces"],
            f"{request.body_id}: форма не изменилась — проверьте толщину "
            f"стенки и выбранные грани")

    def pattern(self, request) -> FeatureResult:
        if not request.features and not request.whole_shape:
            return error("NO_ORIGINALS",
                         f"{request.body_id}: не выбрано ни одной операции для "
                         f"размножения",
                         ["features"], self.name)
        if request.kind != "mirror" and request.count < 2:
            return error("BAD_COUNT",
                         f"{request.body_id}: в массиве должно быть хотя бы "
                         f"два вхождения",
                         ["count"], self.name)
        return self._guarded(
            self.backend.pattern, request, ["count", "spacing", "features"],
            f"{request.body_id}: форма не изменилась — копии легли туда же, "
            f"где уже есть материал, либо мимо детали")

    def sweep(self, request) -> FeatureResult:
        """Протяжка. Разворачивать нечего: сторону задаёт траектория."""
        if request.path.empty:
            return error("NO_PATH",
                         f"{request.body_id}: траектория не задана",
                         ["path"], self.name)
        if request.mode not in SWEEP_MODES:
            return error("BAD_MODE",
                         f"{request.body_id}: неизвестный способ ведения "
                         f"профиля {request.mode!r}", ["mode"], self.name)
        if request.transition not in SWEEP_TRANSITIONS:
            return error("BAD_TRANSITION",
                         f"{request.body_id}: неизвестный переход на изломе "
                         f"{request.transition!r}", ["transition"], self.name)
        return self._guarded(
            self.backend.sweep, request, ["path", "profile"],
            self._nothing(request, "проверьте, где лежит профиль "
                                   "относительно траектории"))

    def loft(self, request) -> FeatureResult:
        if not request.sections:
            return error("NO_SECTIONS",
                         f"{request.body_id}: нужно хотя бы два сечения — "
                         f"укажите ещё одно", ["sections"], self.name)
        if any(item.empty for item in request.sections):
            return error("EMPTY_SECTION",
                         f"{request.body_id}: одно из сечений пустое — в его "
                         f"эскизе нет замкнутого контура", ["sections"],
                         self.name)
        return self._guarded(
            self.backend.loft, request, ["sections", "profile"],
            self._nothing(request, "проверьте сечения"))

    def helix(self, request) -> FeatureResult:
        if request.mode not in HELIX_MODES:
            return error("BAD_MODE",
                         f"{request.body_id}: неизвестный способ задать "
                         f"спираль {request.mode!r}", ["mode"], self.name)
        # Проверяются только те величины, что задаются: выведенную считает
        # движок, и её значение в запросе ничего не значит.
        given = {"pitch-height": ("pitch", "height"),
                 "pitch-turns": ("pitch", "turns"),
                 "height-turns": ("height", "turns")}[request.mode]
        names = {"pitch": "шаг должен", "height": "высота должна",
                 "turns": "число витков должно"}
        for key in given:
            if float(getattr(request, key)) <= 0.0:
                return error("BAD_" + key.upper(),
                             f"{request.body_id}: {names[key]} быть больше "
                             f"нуля", [key], self.name)
        if abs(request.angle_deg) >= 89.0:
            return error("BAD_ANGLE",
                         f"{request.body_id}: угол конуса должен быть меньше "
                         f"89°", ["angle_deg"], self.name)
        return self._guarded(
            self.backend.helix, request, ["pitch", "height", "turns", "axis"],
            self._nothing(request, "проверьте ось, шаг и высоту"))

    @staticmethod
    def _nothing(request, advice: str) -> str:
        """Жалоба «ничего не произошло» — своя у прилива и у выреза."""
        if request.subtract:
            return (f"{request.body_id}: ничего не снято — инструмент не "
                    f"задевает деталь; {advice}")
        return (f"{request.body_id}: ничего не добавлено — тело вышло пустым "
                f"либо целиком внутри детали; {advice}")

    def _guarded(self, run, request, arguments, complaint: str) -> FeatureResult:
        """Операция под правилом «ничего не произошло — это отказ».

        Разворота здесь нет: сторону выбирает не правило, а сама операция.
        Но молчаливый успех выглядит на экране одинаково у всех — как
        пропавшая команда, — и потому проверяется одинаково.
        """
        before = None if request.feature_id else self._known_volume(request)
        result = run(request)
        if not result.ok:
            return result
        if request.feature_id or self._changed(before, result.volume):
            if not request.preview:
                self._volumes[_key(request)] = result.volume
            return result
        return error("NOTHING_CHANGED", complaint, list(arguments), self.name)

    def _take_back(self, request, result) -> None:
        """Убрать операцию, которую сами же и создали ради пробы.

        Правка существующей и предпросмотр не трогаются: у первой имя
        пришло снаружи и убирать её никто не просил, у второго движок и так
        не оставляет следов.
        """
        if request.feature_id or request.preview:
            return
        name = getattr(result, "feature_id", "")
        drop = getattr(self.backend, "drop_feature", None)
        if name and drop is not None:
            drop(request.document_id, request.body_id, name)

    def _known_volume(self, request):
        """Объём тела ДО операции.

        Если он нам неизвестен — например, документ только что открыт, —
        спрашиваем движок. Без этого правило «ничего не произошло» молчит
        на первой же операции после открытия: сравнивать не с чем, и любой
        ненулевой объём считается работой. Вырез, не задевший деталь,
        проходил бы как успешный.
        """
        known = self._volumes.get(_key(request))
        if known is not None:
            return known
        scene = getattr(self.backend, "scene", None)
        if scene is None:
            return None
        try:
            answer = scene(request.document_id, request.body_id)
        except Exception:  # noqa: BLE001 — движок может не знать такого тела
            return None
        if not answer.ok:
            return None
        self._volumes[_key(request)] = answer.volume
        return answer.volume

    @staticmethod
    def _changed(before, after: float) -> bool:
        """Сделала ли операция что-нибудь.

        Первое тело сравнивать не с чем: у него ``before`` пустой, и любой
        ненулевой объём — работа.
        """
        if before is None:
            return after > MINIMUM
        return abs(after - before) > MINIMUM
