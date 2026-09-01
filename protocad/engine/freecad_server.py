"""Движок ProtoCAD внутри процесса FreeCAD.

Запускается ЕГО питоном, не нашим: у FreeCAD 1.1 это Python 3.11 и OCCT
7.8.1, у нас 3.12 и OCP с OCCT 7.9.3. Ни то, ни другое не совмещается в
одном процессе, и это не неудобство, а причина такого устройства
(`docs/08_ENGINE_BACKEND.md`, §16.3).

Разговор — строками JSON: запрос в stdin, ответ в stdout. Ответы помечены
меткой в начале строки, потому что FreeCAD пишет в stdout и сам: без метки
его сообщения о загрузке модулей смешались бы с протоколом, и разбирать
пришлось бы вперемешку.

Здесь только перевод запроса в объекты FreeCAD и обратно. Геометрию,
пересчёт и прослеживание топологии делает C++ FreeCAD — в этом и смысл.
"""

from __future__ import annotations

import json
import math
import os
import sys
import traceback

#: Метка ответа. Всё, что без неё, — вывод самого FreeCAD.
MARK = "@@PROTOCAD@@ "

PROTOCOL = (1, 0)


def _setup(home: str) -> None:
    binary = os.path.join(home, "bin")
    if binary not in sys.path:
        sys.path.insert(0, binary)
    if hasattr(os, "add_dll_directory") and os.path.isdir(binary):
        os.add_dll_directory(binary)


def reply(payload: dict) -> None:
    sys.stdout.write(MARK + json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


class Engine:
    """Документы, тела и операции FreeCAD за одним разговором."""

    def __init__(self):
        import FreeCAD  # noqa: F401  — импорт после настройки путей

        self.documents = {}
        self.bodies = {}
        self.counter = 0

    # --- сведения -----------------------------------------------------

    def capabilities(self, _request: dict) -> dict:
        import FreeCAD
        import Part

        pad_types, pocket_types = [], []
        document = self._scratch()
        try:
            pad = document.addObject("PartDesign::Pad", "проба")
            pad_types = list(pad.getEnumerationsOfProperty("Type"))
            document.removeObject(pad.Name)
            pocket = document.addObject("PartDesign::Pocket", "проба")
            pocket_types = list(pocket.getEnumerationsOfProperty("Type"))
            document.removeObject(pocket.Name)
        except Exception:  # noqa: BLE001
            pass

        # Наборы объявляются ПО ОПЕРАЦИЯМ. У прилива и выреза они разные:
        # приливу FreeCAD 1.1 даёт «до последней», вырезу — «насквозь», и
        # ни одного из них нет у второго.
        names = {"Length": "blind", "ThroughAll": "through_all",
                 "UpToFirst": "up_to_first", "UpToLast": "up_to_last",
                 "UpToFace": "up_to_face"}
        ends = {
            "partdesign.pad": [names[item] for item in pad_types
                               if item in names] + ["mid_plane"],
            "partdesign.pocket": [names[item] for item in pocket_types
                                  if item in names] + ["mid_plane"],
        }
        return {
            "backend": "FreeCAD",
            "version": ".".join(FreeCAD.Version()[:3]),
            "protocol": {"major": PROTOCOL[0], "minor": PROTOCOL[1]},
            "features": ["partdesign.pad", "partdesign.pocket",
                         "partdesign.fillet", "partdesign.chamfer",
                         "partdesign.revolution", "partdesign.groove",
                         "partdesign.hole", "partdesign.thickness",
                         "partdesign.linearpattern",
                         "partdesign.polarpattern", "partdesign.mirrored"],
            "end_conditions": ends,
            "notes": (f"OCCT {Part.OCC_VERSION}, python "
                      f"{sys.version.split()[0]}; прилив: {pad_types}; "
                      f"вырез: {pocket_types}"),
        }

    # --- операции ------------------------------------------------------

    def pad(self, request: dict) -> dict:
        import FreeCAD
        import Part

        document = self._document(request.get("document_id", "документ"))
        body = self._body(document, request.get("body_id", "Тело"))
        preview = bool(request.get("preview"))
        editing = request.get("feature_id") or ""
        profile = request.get("profile") or {}
        regions = profile.get("regions") or []
        try:
            thin = _thin_of(request)
        except ValueError as failure:
            return _error("BAD_THIN", str(failure), ["thickness"])
        chains = (profile.get("chains") or ()) if thin else ()
        if not regions and not chains:
            return _error("NO_PROFILE", "профиль пуст: областей не передано",
                          ["profile"])

        try:
            # Профиль собирается ОДИН раз, чтобы отказ был виден до того,
            # как в документе появятся объекты.
            _face_of(regions, _into(_placement_of(profile)), thin, chains,
                     profile.get("normal") or (0.0, 0.0, 1.0))
        except Exception as failure:  # noqa: BLE001
            code = "BAD_THIN" if thin else "BAD_PROFILE"
            what = ("стенка такой толщины не строится"
                    if thin else "профиль не собрался")
            return _error(code, f"{what}: {failure}",
                          ["thickness" if thin else "profile"])

        subtract = bool(request.get("subtract"))
        if subtract and not _has_shape(body):
            return _error("NO_BASE", "нечего резать: тело ещё не построено",
                          ["body_id"])

        created = not editing
        # Вершина тела ДО операции. Добавляя операцию в тело, FreeCAD
        # переносит Tip на неё; убрав операцию предпросмотра, Tip остаётся
        # висеть в пустоте, и тело становится пустым. Возвращаем как было.
        previous_tip = getattr(body, "Tip", None)
        if editing:
            # Правка: у операции меняются профиль и параметры, сама она
            # остаётся той же. Пересоздавать её нельзя — на неё ссылаются
            # операции ниже по дереву, и ссылки порвались бы.
            feature = document.getObject(editing)
            if feature is None:
                return _error("NO_FEATURE", f"операции {editing!r} нет", [])
            carrier = _carrier_of(feature)
            _set_profile(carrier, profile,
                         float(request.get("start_offset", 0.0)), thin)
            carrier.touch()
        else:
            self.counter += 1
            carrier = document.addObject(
                "Part::Part2DObjectPython", f"Профиль{self.counter}")
            _set_profile(carrier, profile,
                         float(request.get("start_offset", 0.0)), thin)
            body.addObject(carrier)

            kind = "PartDesign::Pocket" if subtract else "PartDesign::Pad"
            feature = document.addObject(kind, f"Операция{self.counter}")
            body.addObject(feature)
            feature.Profile = carrier

        end = request.get("end_condition", "blind")
        allowed = set(feature.getEnumerationsOfProperty("Type"))
        wanted = _END_TYPES.get(end)
        if wanted is None or (wanted not in allowed):
            if created:
                self._drop(document, feature, carrier)
            return _error(
                "END_CONDITION_UNSUPPORTED",
                f"эта операция не умеет «{end}»; доступны: {sorted(allowed)}",
                ["end_condition"])

        # Второе направление у FreeCAD — не отдельное условие, а сторона:
        # «одна», «две» или «симметрично». «От средней плоскости» — это и
        # есть симметрия, поэтому со вторым направлением оно не сходится, и
        # выбирать надо одно.
        both = bool(request.get("direction2")) and end != "mid_plane"
        if hasattr(feature, "SideType"):
            feature.SideType = ("Symmetric" if end == "mid_plane"
                                else ("Two sides" if both else "One side"))
        feature.Type = wanted
        if end == "mid_plane":
            feature.Midplane = True
        if wanted == "Length":
            feature.Length = float(request.get("length", 10.0))
        if wanted == "UpToFace":
            base = previous_tip if created else _base_of(feature)
            failure = _aim(feature, base, int(request.get("target_face", -1)),
                           "UpToFace", "target_face",
                           request.get("target_face_name", ""))
            if failure is not None:
                if created:
                    self._drop(document, feature, carrier)
                return failure
        feature.Reversed = bool(request.get("reversed"))
        angle = float(request.get("taper_angle_deg", 0.0))
        if hasattr(feature, "TaperAngle"):
            feature.TaperAngle = angle

        if both:
            second = request.get("end_condition2", "blind")
            wanted2 = _END_TYPES.get(second)
            if wanted2 is None or wanted2 not in allowed:
                if created:
                    self._drop(document, feature, carrier)
                return _error(
                    "END_CONDITION_UNSUPPORTED",
                    f"второе направление не умеет «{second}»; "
                    f"доступны: {sorted(allowed)}",
                    ["end_condition2"])
            feature.Type2 = wanted2
            if wanted2 == "Length":
                feature.Length2 = float(request.get("length2", 10.0))
            if wanted2 == "UpToFace":
                base = previous_tip if created else _base_of(feature)
                failure = _aim(feature, base,
                               int(request.get("target_face2", -1)),
                               "UpToFace2", "target_face2",
                               request.get("target_face2_name", ""))
                if failure is not None:
                    if created:
                        self._drop(document, feature, carrier)
                    return failure
            if hasattr(feature, "TaperAngle2"):
                feature.TaperAngle2 = float(request.get("taper_angle2_deg", 0.0))

        document.recompute()
        problems = _problems(feature)
        if problems:
            if created:
                self._drop(document, feature, carrier)
            return _error("FEATURE_FAILED", problems, ["length", "end_condition"])

        shape = body.Shape
        if shape.isNull() or not shape.Faces:
            if created:
                self._drop(document, feature, carrier)
            return _error("EMPTY_RESULT", "операция не дала тела", [])

        # Имя берётся ДО удаления. Предпросмотр операцию убирает, и
        # обращение к имени после этого роняет весь ответ: вырез, дающий
        # два тела, не показывался вовсе — предупреждение о втором теле
        # падало на имени удалённой операции.
        name = feature.Name
        solids = len(shape.Solids)
        answer = {
            "status": "valid",
            "featureId": name,
            "revision": int(request.get("revision", 0)),
            "sessionId": request.get("session_id", ""),
            "volume": float(shape.Volume),
            "bounds": _bounds(shape),
            "entities": _entities(shape, name, _tip_shape(body), body.Name),
            "diagnostics": [],
        }
        answer.update(_meshes(shape, feature, bool(preview)))
        if solids > 1:
            answer["diagnostics"].append({
                "code": "MULTI_SOLID",
                "message": (f"{name}: получилось тел: {solids} — "
                            f"они не соединены между собой"),
                "severity": "warning", "arguments": [], "backend": "FreeCAD",
            })
        if preview and created:
            # Предпросмотр не оставляет следов: ни операции, ни носителя
            # профиля, ни записи в дереве (§12.3). Считался он тем же
            # кодом — в этом и смысл.
            self._drop(document, feature, carrier, restore_tip=(body, previous_tip))
            answer["featureId"] = ""
        return answer

    # --- служебное -----------------------------------------------------

    def _document(self, name: str):
        import FreeCAD

        if name not in self.documents:
            self.documents[name] = FreeCAD.newDocument(name)
        return self.documents[name]

    def _scratch(self):
        return self._document("__проба__")

    def _body(self, document, name: str):
        key = (document.Name, name)
        if key not in self.bodies:
            self.bodies[key] = document.addObject("PartDesign::Body", name)
        return self.bodies[key]

    def _known_body(self, document, name: str):
        """Тело по имени, НЕ заводя его. ``None`` — такого тела нет.

        Отдельно от `_body` намеренно: тот создаёт недостающее, и это
        правильно для операции — она за тем и пришла. Но запрос, который
        только СМОТРИТ на деталь, создавать ничего не должен: опечатка в
        имени тела оставляла бы в документе пустое тело, которого никто
        не просил.
        """
        found = self.bodies.get((document.Name, name))
        if found is not None:
            return found
        for item in document.Objects:
            if item.TypeId != "PartDesign::Body":
                continue
            if name in (item.Name, getattr(item, "Label", "")):
                return item
        return None

    @staticmethod
    def _drop(document, *objects, restore_tip=None) -> None:
        for item in objects:
            try:
                document.removeObject(item.Name)
            except Exception:  # noqa: BLE001
                pass
        if restore_tip is not None:
            body, tip = restore_tip
            try:
                body.Tip = tip
            except Exception:  # noqa: BLE001
                pass
        document.recompute()

    def dress_up(self, request: dict) -> dict:
        """Скругление или фаска по номерам рёбер текущего тела.

        Опирается новая операция на ВЕРШИНУ тела: в PartDesign обработка
        идёт поверх последнего построенного, и номера рёбер отсчитываются
        по его форме — той самой, номера из которой вернул выбор мышью.
        При правке опора не пересматривается: она уже записана в самой
        операции, а взять текущую вершину значило бы поставить операцию
        опорой самой себе.
        """
        document = self._document(request.get("document_id", "документ"))
        body = self._body(document, request.get("body_id", "Тело"))
        if not _has_shape(body):
            return _error("NO_BASE", "нечего обрабатывать: тела ещё нет",
                          ["body_id"])

        kind = request.get("kind", "fillet")
        editing = request.get("feature_id") or ""
        created = not editing
        previous_tip = getattr(body, "Tip", None)
        if editing:
            feature = document.getObject(editing)
            if feature is None:
                return _error("NO_FEATURE", f"операции {editing!r} нет", [])
            base = feature.Base[0]
        else:
            kinds = {"fillet": "PartDesign::Fillet",
                     "chamfer": "PartDesign::Chamfer"}
            if kind not in kinds:
                return _error("UNKNOWN_DRESSUP",
                              f"неизвестная обработка: {kind!r}", ["kind"])
            base = previous_tip
            if base is None:
                return _error("NO_BASE", "у тела нет вершины построения", [])

        numbers, failure = _resolve(
            base.Shape, "Edge", request.get("edge_names"),
            request.get("edges"), "edges")
        if failure is not None:
            return failure
        names = [f"Edge{value + 1}" for value in numbers]

        # Указанная ГРАНЬ означает все её рёбра. Разворачивает это движок:
        # какие рёбра у грани, знает форма, а не интерфейс.
        if request.get("faces") or request.get("face_names"):
            picked, failure = _resolve(
                base.Shape, "Face", request.get("face_names"),
                request.get("faces"), "faces")
            if failure is not None:
                return failure
            names.extend(_edges_of_faces(base.Shape, picked))
            names = list(dict.fromkeys(names))
        if not names:
            return _error("NO_EDGES",
                          "нечего обрабатывать: не выбрано ни одного ребра",
                          ["edges"])

        if created:
            self.counter += 1
            feature = document.addObject(kinds[kind], f"Обработка{self.counter}")
            body.addObject(feature)

        feature.Base = (base, names)
        size = float(request.get("size", 3.0))
        if feature.isDerivedFrom("PartDesign::Fillet"):
            feature.Radius = size
        else:
            wanted = _CHAMFER_TYPES.get(request.get("mode") or "equal")
            allowed = set(feature.getEnumerationsOfProperty("ChamferType"))
            if wanted is None or wanted not in allowed:
                if created:
                    self._drop(document, feature,
                               restore_tip=(body, previous_tip))
                return _error(
                    "CHAMFER_TYPE_UNSUPPORTED",
                    f"движок не умеет такую фаску; есть: {sorted(allowed)}",
                    ["mode"])
            feature.ChamferType = wanted
            feature.Size = size
            feature.Size2 = float(request.get("size2", 1.0))
            feature.Angle = float(request.get("angle_deg", 45.0))
            feature.FlipDirection = bool(request.get("flip"))

        document.recompute()
        problems = _problems(feature)
        if problems:
            if created:
                self._drop(document, feature, restore_tip=(body, previous_tip))
            return _error(
                "DRESSUP_FAILED",
                f"{problems}. Скругление или фаска такого размера на этих "
                f"рёбрах не строится — уменьшите размер либо выберите другие",
                ["size", "edges"])

        shape = body.Shape
        # Показывать надо СНИМАЕМОЕ, а не результат. У обработки рёбер
        # `AddSubShape` нет, и предпросмотр показывал всё тело целиком —
        # то есть закрашивал деталь и не говорил о ней ничего. Форма «до»
        # здесь под рукой: это форма опоры.
        changed, changed_kind = _change(base.Shape, shape)
        answer = {
            "status": "valid",
            "featureId": feature.Name,
            "revision": int(request.get("revision", 0)),
            "sessionId": request.get("session_id", ""),
            "volume": float(shape.Volume),
            "bounds": _bounds(shape),
            "mesh": None if (request.get("preview") and changed is not None)
                    else _mesh(shape),
            "toolMesh": changed,
            "toolKind": changed_kind or "removed",
            "entities": _entities(shape, feature.Name,
                                  _tip_shape(body)),
            "diagnostics": [],
        }
        if request.get("preview") and created:
            self._drop(document, feature, restore_tip=(body, previous_tip))
            answer["featureId"] = ""
        return answer

    def revolve(self, request: dict) -> dict:
        """Вращение профиля вокруг оси, заданной числами.

        Ось приходит направлением и точкой, а FreeCAD хочет ссылку на
        объект. Поэтому под операцию заводится опорная линия — датум. Она
        часть операции и снимается вместе с ней: оставлять её в теле
        значило бы копить в дереве мусор, которого человек не создавал.
        """
        document = self._document(request.get("document_id", "документ"))
        body = self._body(document, request.get("body_id", "Тело"))
        profile = request.get("profile") or {}
        regions = profile.get("regions") or []
        if not regions:
            return _error("NO_PROFILE", "профиль пуст: областей не передано",
                          ["profile"])
        try:
            # Профиль собирается ОДИН раз, чтобы отказ был виден до того,
            # как в документе появятся объекты.
            _face_of(regions, _into(_placement_of(profile)), None, None,
                       profile.get("normal") or (0.0, 0.0, 1.0))
        except Exception as failure:  # noqa: BLE001
            return _error("BAD_PROFILE", f"профиль не собрался: {failure}",
                          ["profile"])

        subtract = bool(request.get("subtract"))
        if subtract and not _has_shape(body):
            return _error("NO_BASE", "нечего резать: тело ещё не построено",
                          ["body_id"])

        editing = request.get("feature_id") or ""
        created = not editing
        previous_tip = getattr(body, "Tip", None)
        litter = []
        if editing:
            feature = document.getObject(editing)
            if feature is None:
                return _error("NO_FEATURE", f"операции {editing!r} нет", [])
            carrier = _carrier_of(feature)
            _set_profile(carrier, profile)
            carrier.touch()
            axis = feature.ReferenceAxis[0]
        else:
            self.counter += 1
            carrier = document.addObject(
                "Part::Part2DObjectPython", f"Профиль{self.counter}")
            _set_profile(carrier, profile)
            body.addObject(carrier)
            kind = ("PartDesign::Groove" if subtract
                    else "PartDesign::Revolution")
            feature = document.addObject(kind, f"Вращение{self.counter}")
            body.addObject(feature)
            feature.Profile = carrier
            axis = self._datum_line(document, body, f"Ось{self.counter}")
            litter = [carrier, axis]
        _place_axis(axis, request.get("axis_origin") or (0.0, 0.0, 0.0),
                    request.get("axis") or (0.0, 1.0, 0.0))
        feature.ReferenceAxis = (axis, [""])
        feature.Angle = float(request.get("angle_deg", 360.0))
        feature.Reversed = bool(request.get("reversed"))
        if hasattr(feature, "Midplane"):
            feature.Midplane = bool(request.get("midplane"))
        return self._finish(document, body, feature, request,
                            created, previous_tip, litter,
                            ["angle_deg", "axis"])

    def hole(self, request: dict) -> dict:
        """Отверстие по эскизу: положения берутся из окружностей профиля."""
        document = self._document(request.get("document_id", "документ"))
        body = self._body(document, request.get("body_id", "Тело"))
        if not _has_shape(body):
            return _error("NO_BASE", "нечего сверлить: тела ещё нет",
                          ["body_id"])
        profile = request.get("profile") or {}
        regions = profile.get("regions") or []
        if not regions:
            return _error("NO_PROFILE", "профиль пуст: областей не передано",
                          ["profile"])
        try:
            # Профиль собирается ОДИН раз, чтобы отказ был виден до того,
            # как в документе появятся объекты.
            _face_of(regions, _into(_placement_of(profile)), None, None,
                       profile.get("normal") or (0.0, 0.0, 1.0))
        except Exception as failure:  # noqa: BLE001
            return _error("BAD_PROFILE", f"профиль не собрался: {failure}",
                          ["profile"])

        editing = request.get("feature_id") or ""
        created = not editing
        previous_tip = getattr(body, "Tip", None)
        litter = []
        if editing:
            feature = document.getObject(editing)
            if feature is None:
                return _error("NO_FEATURE", f"операции {editing!r} нет", [])
            carrier = _carrier_of(feature)
            _set_profile(carrier, profile)
            carrier.touch()
        else:
            self.counter += 1
            carrier = document.addObject(
                "Part::Part2DObjectPython", f"Профиль{self.counter}")
            _set_profile(carrier, profile)
            body.addObject(carrier)
            feature = document.addObject("PartDesign::Hole",
                                         f"Отверстие{self.counter}")
            body.addObject(feature)
            feature.Profile = carrier
            litter = [carrier]

        feature.Diameter = float(request.get("diameter", 8.0))
        through = bool(request.get("through_all", True))
        feature.DepthType = "ThroughAll" if through else "Dimension"
        if not through:
            feature.Depth = float(request.get("depth", 10.0))
            if hasattr(feature, "DrillPoint"):
                feature.DrillPoint = ("Flat" if request.get("flat_bottom")
                                      else "Angled")
        feature.Reversed = bool(request.get("reversed"))
        counterbore = float(request.get("counterbore", 0.0))
        if counterbore > 0.0:
            feature.HoleCutType = "Counterbore"
            feature.HoleCutDiameter = counterbore
            feature.HoleCutDepth = float(request.get("counterbore_depth", 1.0))
        else:
            feature.HoleCutType = "None"
        return self._finish(document, body, feature, request,
                            created, previous_tip, litter,
                            ["diameter", "depth"])

    def draft(self, request: dict) -> dict:
        """Уклон: выбранные грани наклоняются вокруг нейтральной.

        Нейтральная грань обязательна. Без неё наклон неопределён:
        поворачивать грань можно вокруг любой лежащей на ней прямой, и
        выбрать её за человека значило бы построить не то, что задумано.
        """
        document = self._document(request.get("document_id", "документ"))
        body = self._body(document, request.get("body_id", "Тело"))
        if not _has_shape(body):
            return _error("NO_BASE", "нечего наклонять: тела ещё нет",
                          ["body_id"])

        editing = request.get("feature_id") or ""
        created = not editing
        previous_tip = getattr(body, "Tip", None)
        if editing:
            feature = document.getObject(editing)
            if feature is None:
                return _error("NO_FEATURE", f"операции {editing!r} нет", [])
            base = feature.Base[0]
        else:
            base = previous_tip
            if base is None:
                return _error("NO_BASE", "у тела нет вершины построения", [])

        numbers, failure = _resolve(
            base.Shape, "Face", request.get("face_names"),
            request.get("faces"), "faces")
        if failure is not None:
            return failure
        if not numbers:
            return _error("NO_FACES", "не выбрано ни одной грани", ["faces"])

        neutral_name = request.get("neutral_name") or ""
        neutral_number = int(request.get("neutral", -1))
        if not neutral_name and neutral_number < 0:
            return _error(
                "NO_NEUTRAL",
                "не указана нейтральная грань: без неё непонятно, вокруг "
                "чего наклонять", ["neutral"])
        neutral, failure = _resolve(
            base.Shape, "Face", [neutral_name] if neutral_name else [],
            [neutral_number] if neutral_number >= 0 else [], "neutral")
        if failure is not None:
            return failure

        if created:
            self.counter += 1
            feature = document.addObject("PartDesign::Draft",
                                         f"Уклон{self.counter}")
            body.addObject(feature)
        feature.Base = (base, [f"Face{value + 1}" for value in numbers])
        feature.NeutralPlane = (base, [f"Face{neutral[0] + 1}"])
        feature.Angle = float(request.get("angle_deg", 1.0))
        # У FreeCAD один переключатель стороны. При выключенном материал
        # ПРИБАВЛЯЕТСЯ (брусок 40 × 30 × 20 под 5° даёт 26531 вместо
        # 24000), поэтому «внутрь» — это Reversed.
        feature.Reversed = not bool(request.get("outward"))
        return self._finish(document, body, feature, request,
                            created, previous_tip, [], ["angle_deg", "faces"])

    def shell(self, request: dict) -> dict:
        """Оболочка: выбранные грани снимаются, остальное — стенка."""
        document = self._document(request.get("document_id", "документ"))
        body = self._body(document, request.get("body_id", "Тело"))
        if not _has_shape(body):
            return _error("NO_BASE", "нечего опустошать: тела ещё нет",
                          ["body_id"])

        editing = request.get("feature_id") or ""
        created = not editing
        previous_tip = getattr(body, "Tip", None)
        if editing:
            feature = document.getObject(editing)
            if feature is None:
                return _error("NO_FEATURE", f"операции {editing!r} нет", [])
            base = feature.Base[0]
        else:
            base = previous_tip
            if base is None:
                return _error("NO_BASE", "у тела нет вершины построения", [])

        numbers, failure = _resolve(
            base.Shape, "Face", request.get("face_names"),
            request.get("faces"), "faces")
        if failure is not None:
            return failure

        if created:
            self.counter += 1
            feature = document.addObject("PartDesign::Thickness",
                                         f"Оболочка{self.counter}")
            body.addObject(feature)
        feature.Base = (base, [f"Face{value + 1}" for value in numbers])
        feature.Value = float(request.get("thickness", 2.0))
        # «Внутрь» — обычный случай: стенка нарастает в материал, габарит
        # детали не меняется. У FreeCAD это Reversed.
        feature.Reversed = bool(request.get("inward", True))
        return self._finish(document, body, feature, request,
                            created, previous_tip, [], ["thickness", "faces"])

    def pattern(self, request: dict) -> dict:
        """Массив операций: линейный, круговой или зеркало.

        Размножаются операции, а не грани: массив отверстий — это те же
        отверстия, и правка диаметра меняет все сразу.
        """
        document = self._document(request.get("document_id", "документ"))
        body = self._body(document, request.get("body_id", "Тело"))
        if not _has_shape(body):
            return _error("NO_BASE", "нечего размножать: тела ещё нет",
                          ["body_id"])
        whole = bool(request.get("whole_shape"))
        names = list(request.get("features") or ())
        originals = [document.getObject(name) for name in names]
        missing = [name for name, item in zip(names, originals) if item is None]
        if missing:
            return _error("NO_FEATURE",
                          f"операций {missing} в детали нет", ["features"])
        kind = request.get("kind", "linear")
        kinds = {"linear": "PartDesign::LinearPattern",
                 "polar": "PartDesign::PolarPattern",
                 "mirror": "PartDesign::Mirrored"}
        if kind not in kinds:
            return _error("UNKNOWN_PATTERN",
                          f"неизвестный вид массива: {kind!r}", ["kind"])

        editing = request.get("feature_id") or ""
        created = not editing
        previous_tip = getattr(body, "Tip", None)
        # Сетка с пропуском ОТДЕЛЬНЫХ ячеек одним `LinearPattern` не
        # выражается: `Spacings` гасит только целый столбец. Такая сетка
        # строится по ячейке на операцию — см. `_cell_grid`.
        #
        # Путь выбирается ещё и по тому, чем операция УЖЕ является, а не
        # по одним лишь текущим пропускам. Иначе снятие последнего пропуска
        # уводило сетку в обычный обработчик, а тот перестраивал головную
        # ячейку в двунаправленный массив поверх остальных: пропуски после
        # этого переставали работать вовсе.
        cells = bool(editing) and len(_group_of(document, editing)) > 1
        if kind == "linear" and int(request.get("count2", 1) or 1) > 1 \
                and (request.get("skip") or cells):
            return self._cell_grid(document, body, originals, whole, request,
                                   previous_tip)
        if cells:
            # Второе направление выключили — сетка снова стала рядом.
            # Ячейки надо погасить: иначе они продолжают ставить копии
            # там, где ряда уже нет.
            whole_group = _group_of(document, editing)
            for extra in whole_group[1:]:
                extra.Suppressed = True
            # А головную — наоборот, ОЖИВИТЬ: пропуск мог погасить и её,
            # и тогда ряд строился бы из одного исходника.
            whole_group[0].Suppressed = False
        litter = []
        second_datum = None
        if editing:
            feature = document.getObject(editing)
            if feature is None:
                return _error("NO_FEATURE", f"операции {editing!r} нет", [])
            datum = (feature.MirrorPlane if kind == "mirror"
                     else (feature.Axis if kind == "polar"
                           else feature.Direction))[0]
        else:
            self.counter += 1
            feature = document.addObject(kinds[kind], f"Массив{self.counter}")
            body.addObject(feature)
            maker = (self._datum_plane if kind == "mirror" else self._datum_line)
            datum = maker(document, body, f"Опора{self.counter}")
            litter = [datum]
            # Опора ВТОРОГО направления заводится здесь же, рядом с первой,
            # а не там, где она понадобится. Заведённая позже, она попадает
            # в тело ПОСЛЕ самого массива, и на первом же пересчёте массив
            # считается раньше своей опоры: второй ряд молча не строится.
            # Отказа при этом нет — есть три экземпляра вместо шести, и
            # видно это только счётом.
            if kind == "linear" and int(request.get("count2", 1) or 1) > 1:
                self.counter += 1
                second_datum = self._datum_line(document, body,
                                                f"Опора{self.counter}")
                litter.append(second_datum)
        if whole and not originals:
            # Размножается тело целиком. FreeCAD всё равно требует список
            # исходных операций — годится всё построенное К ЭТОМУ МЕСТУ
            # дерева.
            #
            # У НОВОЙ операции это вершина тела. У существующей — её
            # ОПОРА, и брать вершину нельзя: вершиной стоит сама эта
            # операция, и она получила бы исходником саму себя. Ссылка на
            # себя не считается вовсе — операция остаётся «Touched», то
            # есть непересчитанной. Снаружи это выглядело так, что зеркало
            # и массив «целого тела» не едут за своими опорами: правку
            # записали, а форма прежняя.
            source = (getattr(feature, "BaseFeature", None) if editing
                      else getattr(body, "Tip", None))
            if source is None:
                return _error("NO_BASE", "у тела нет вершины построения", [])
            originals = [source]
        feature.Originals = originals
        if hasattr(feature, "TransformMode"):
            feature.TransformMode = "Whole shape" if whole else "Features"

        if kind == "mirror":
            _place_axis(datum, request.get("plane_origin") or (0.0, 0.0, 0.0),
                        request.get("plane_normal") or (1.0, 0.0, 0.0))
            feature.MirrorPlane = (datum, [""])
        elif kind == "polar":
            _place_axis(datum, request.get("axis_origin") or (0.0, 0.0, 0.0),
                        request.get("axis") or (0.0, 0.0, 1.0))
            feature.Axis = (datum, [""])
            # «Разложить поровну по углу» и «ставить через столько-то
            # градусов» — разные задачи, и у FreeCAD это разные режимы.
            count = max(2, int(request.get("count", 2)))
            equal = bool(request.get("equal_spacing", True))
            angle = float(request.get("angle_deg", 360.0))
            # Полный круг делится на ЧИСЛО экземпляров, неполный — на
            # промежутки между ними: иначе первый и последний сошлись бы.
            # Спрошено у движка и закреплено проверкой.
            step = (angle / count if abs(angle - 360.0) < 1e-9
                    else angle / max(1, count - 1)) if equal \
                else float(request.get("angle_step", 30.0))
            skip = request.get("skip") or ()
            if skip:
                kept, gaps = _gaps(count, skip, step)
                feature.Mode = "Spacing"
                feature.Offset = step
                feature.Occurrences = max(2, kept)
                feature.Spacings = gaps or [-1.0]
            else:
                feature.Spacings = [-1.0]
                if equal:
                    feature.Mode = "Extent"
                    feature.Angle = angle
                else:
                    feature.Mode = "Spacing"
                    feature.Offset = step
                feature.Occurrences = count
            feature.Reversed = bool(request.get("reversed"))
        else:
            _place_axis(datum, (0.0, 0.0, 0.0),
                        request.get("direction") or (1.0, 0.0, 0.0))
            feature.Direction = (datum, [""])
            _row(feature, request, "", request.get("count", 2))
            feature.Reversed = bool(request.get("reversed"))
            # Второй ряд: тот же массив, второе направление. Отдельной
            # операцией это делать не надо — FreeCAD держит оба ряда в
            # одной, и массив остаётся ОДНОЙ строкой дерева, как его и
            # задают: «шесть штук сеткой», а не «три, а потом ещё раз два».
            second = max(1, int(request.get("count2", 1)))
            if second > 1:
                # У правящегося массива второго направления могло не быть
                # ВОВСЕ: его включили сейчас. Тогда опору надо завести, а
                # не разыменовывать пустую ссылку.
                existing = feature.Direction2 if editing else None
                datum2 = (existing[0] if existing
                          else (second_datum if not editing else None))
                if datum2 is None:
                    self.counter += 1
                    datum2 = self._datum_line(document, body,
                                              f"Опора{self.counter}")
                    litter.append(datum2)
                _place_axis(datum2, (0.0, 0.0, 0.0),
                            request.get("direction2") or (0.0, 1.0, 0.0))
                feature.Direction2 = (datum2, [""])
                feature.Reversed2 = bool(request.get("reversed2"))
                if created:
                    # FreeCAD подхватывает второй ряд только тогда, когда
                    # число экземпляров в нём МЕНЯЕТСЯ у уже посчитанной
                    # операции. Задать его сразу при создании мало:
                    # операция отчитывается «Up-to-date», а строит один
                    # ряд вместо сетки — три экземпляра вместо шести, без
                    # единого сообщения.
                    #
                    # Проверено в чистом FreeCAD, без нашего кода: «всё
                    # сразу» даёт 3, «сперва один ряд, потом второй» даёт
                    # 6. Поэтому здесь лишний пересчёт — он не украшение,
                    # а единственный способ получить то, что заказано.
                    feature.Occurrences2 = 1
                    document.recompute()
                _row(feature, request, "2", second)
            else:
                # Единица — это «второго ряда нет». Оставить прежнее число
                # значило бы, что снятый флажок ничего не снял.
                feature.Occurrences2 = 1

        # Массив — операция преобразования: ``addObject`` вершину тела на
        # неё НЕ переносит, в отличие от прилива и выреза. Без этого
        # операция строится, отчитывается успехом, а тело остаётся прежним.
        body.Tip = feature
        return self._finish(document, body, feature, request,
                            created, previous_tip, litter,
                            ["count", "spacing", "features"])

    # --- общее окончание операции --------------------------------------

    def _finish(self, document, body, feature, request, created, previous_tip,
                litter, arguments) -> dict:
        """Пересчитать, проверить и собрать ответ.

        Одно место на все операции: отказ, пустой результат и предпросмотр
        обязаны выглядеть у них одинаково, иначе интерфейсу придётся знать,
        какая операция как отвечает.
        """
        document.recompute()
        if "Touched" in list(feature.State):
            # «Touched» значит «не пересчиталась». Обычный пересчёт иногда
            # её пропускает — после серии предпросмотров, каждый из которых
            # заводил и убирал свою операцию. Тогда правка ЗАПИСЫВАЕТСЯ, но
            # форма остаётся прежней: на экране старая деталь при новых
            # числах, и отказа не видно, пока не пересчитаешь ещё раз.
            #
            # Поэтому здесь принудительный пересчёт всего документа. Он
            # дороже обычного, и потому делается ТОЛЬКО в этом случае, а не
            # каждый раз.
            document.recompute(None, True)
        problems = _problems(feature)
        if problems:
            if created:
                self._drop(document, feature, *litter,
                           restore_tip=(body, previous_tip))
            return _error("FEATURE_FAILED", problems, list(arguments))

        shape = body.Shape
        if shape.isNull() or not shape.Faces:
            if created:
                self._drop(document, feature, *litter,
                           restore_tip=(body, previous_tip))
            return _error("EMPTY_RESULT", "операция не дала тела", [])

        # Имя берётся ДО удаления: предпросмотр операцию убирает, и
        # обращение к имени после этого роняет весь ответ.
        name = feature.Name
        solids = len(shape.Solids)
        answer = {
            "status": "valid",
            "featureId": name,
            "revision": int(request.get("revision", 0)),
            "sessionId": request.get("session_id", ""),
            "volume": float(shape.Volume),
            "bounds": _bounds(shape),
            "entities": _entities(shape, name, _tip_shape(body)),
            "diagnostics": [],
        }
        answer.update(_meshes(shape, feature, bool(request.get("preview"))))
        if solids > 1:
            answer["diagnostics"].append({
                "code": "MULTI_SOLID",
                "message": (f"{name}: получилось тел: {solids} — "
                            f"они не соединены между собой"),
                "severity": "warning", "arguments": [], "backend": "FreeCAD",
            })
        if request.get("preview") and created:
            self._drop(document, feature, *litter,
                       restore_tip=(body, previous_tip))
            answer["featureId"] = ""
        return answer

    def _datum_line(self, document, body, name: str):
        line = document.addObject("PartDesign::Line", name)
        body.addObject(line)
        return line

    def _cell_grid(self, document, body, originals, whole, request,
                   previous_tip) -> dict:
        """Сетка, в которой можно погасить ОТДЕЛЬНУЮ ячейку.

        Одним `LinearPattern` такого не выразить: пропуски он задаёт
        списком промежутков вдоль направления (`Spacings`), и ими гасится
        только целый столбец или ряд. Маски экземпляров нет ни у него, ни
        у `MultiTransform` — тот вдобавок ПЕРЕМНОЖАЕТ преобразования:
        [X(2), Y(2)] даёт четыре копии, а не три. Спрошено у движка.

        Поэтому на каждую ячейку заводится своя операция: `LinearPattern` с
        двумя вхождениями и собственной опорной осью, направленной из
        исходника в эту ячейку. Пропуск — это `Suppressed` у её операции.

        Операции только ДОБАВЛЯЮТСЯ. Уменьшилась сетка — лишние гасятся,
        а не удаляются: удаление в середине дерева рвёт цепочку опор у
        того, что стоит после массива, и чинить её пришлось бы вручную.
        Погашенная операция ничего не строит и стоит дёшево.
        """
        import FreeCAD as App

        count = max(1, int(request.get("count", 2) or 2))
        count2 = max(1, int(request.get("count2", 1) or 1))
        step = _spacing_of(request, "")
        step2 = _spacing_of(request, "2")
        first = _unit(request.get("direction") or (1.0, 0.0, 0.0))
        second = _unit(request.get("direction2") or (0.0, 1.0, 0.0))
        sign = -1.0 if request.get("reversed") else 1.0
        sign2 = -1.0 if request.get("reversed2") else 1.0
        dropped = {int(value) for value in (request.get("skip") or ())}

        editing = request.get("feature_id") or ""
        group = _group_of(document, editing) if editing else []
        if editing and not group:
            return _error("NO_FEATURE", f"операции {editing!r} нет", [])
        if not group:
            self.counter += 1
            stem = f"Сетка{self.counter}"
        else:
            stem = group[0].Name

        wanted = _grid_parts(count, count2, dropped)
        made, litter = [], []
        tip_before = getattr(body, "Tip", None)
        # Что стоит в дереве СРАЗУ ПОСЛЕ группы. Если сетка выросла, новые
        # ячейки вклиниваются между группой и этим, и опору ему надо
        # переставить: иначе он продолжает опираться на бывшую последнюю
        # ячейку, а новые повисают в стороне от дерева.
        follower = None
        if group:
            for item in document.Objects:
                if getattr(item, "BaseFeature", None) is group[-1]:
                    follower = item
                    break
            # Дописывать надо В КОНЕЦ группы, а не на вершину тела: между
            # массивом и вершиной могут стоять другие операции.
            body.Tip = group[-1]
        along = tuple(sign * value for value in first)
        across = tuple(sign2 * value for value in second)
        for index, part in enumerate(wanted):
            if index < len(group):
                feature = group[index]
                axes = (feature.Direction[0], feature.Direction2[0])
                born = False
            else:
                feature, axes = self._cell_feature(document, body, stem, index)
                made.append(feature)
                litter.extend((feature,) + axes)
                born = True
            feature.Originals = originals
            if hasattr(feature, "TransformMode"):
                feature.TransformMode = "Whole shape" if whole else "Features"
            feature.Suppressed = False
            if part[0] == "rect":
                _, columns, rows = part
                _point_axis(axes[0], along)
                _point_axis(axes[1], across)
                feature.Mode = "Spacing"
                feature.Offset = step
                feature.Occurrences = len(columns)
                feature.Spacings = _spacings_for(columns, step)
                feature.Mode2 = "Spacing"
                feature.Offset2 = step2
                if born:
                    # Число вхождений ВТОРОГО ряда движок берёт только
                    # тогда, когда оно МЕНЯЕТСЯ у уже посчитанной операции.
                    # Заданное при создании, оно молча пропадает: строится
                    # один ряд вместо сетки, и отказа при этом нет.
                    feature.Occurrences2 = 1
                    document.recompute()
                feature.Occurrences2 = len(rows)
                feature.Spacings2 = _spacings_for(rows, step2)
            else:
                _, column, row = part
                shift = tuple(column * step * along[axis]
                              + row * step2 * across[axis] for axis in range(3))
                reach = math.sqrt(sum(value * value for value in shift))
                _point_axis(axes[0], tuple(value / reach for value in shift))
                _point_axis(axes[1], across)
                feature.Mode = "Spacing"
                feature.Offset = reach
                feature.Occurrences = 2
                feature.Spacings = [-1.0]
                feature.Occurrences2 = 1
                feature.Spacings2 = [-1.0]
        # Частей стало меньше — лишние операции гасятся.
        for extra in group[len(wanted):]:
            extra.Suppressed = True

        if made and follower is not None:
            # Массив стоит в СЕРЕДИНЕ дерева: перевязываем опору следующей
            # операции на свежую последнюю ячейку, иначе дописанные ячейки
            # повисают в стороне от дерева.
            follower.BaseFeature = made[-1]
        # Вершиной становится ПОСЛЕДНЯЯ ячейка — так же, как обычный массив
        # ставит вершиной себя. Если после массива в дереве есть операции,
        # вершину подвинет их обработчик: пересчёт идёт по порядку.
        body.Tip = made[-1] if made else group[-1]

        head = group[0] if group else made[0]
        # Пересчёт здесь ПРИНУДИТЕЛЬНЫЙ, и оба раза не лишние.
        #
        # Обычный не годится дважды. Пакетно созданные ячейки он оставляет
        # «Touched» — построенным выходит одно отверстие вместо восьми. А
        # `Suppressed`, выставленный у ячейки, не помечает изменённой
        # головную операцию: обычный пересчёт проходит мимо, и погашенная
        # ячейка остаётся на месте. Отказа при этом нет — просто пропуск
        # не сработал.
        document.recompute()
        document.recompute(None, True)
        return self._finish(document, body, head, request,
                            not group, previous_tip, litter,
                            ["count", "spacing", "skip"])

    def _cell_feature(self, document, body, stem: str, index: int):
        """Одна часть сетки: её операция и две опорные оси.

        Оси заводятся ОБЕ, даже одиночной ячейке, у которой второй ряд
        выключен: заводить недостающую потом значило бы класть её в тело
        ПОСЛЕ операции, которая ею пользуется, — а такая опора считается
        позже, чем нужна.
        """
        axes = []
        for letter in ("d", "e"):
            datum = document.addObject("PartDesign::Line",
                                       f"{stem}_{letter}{index}")
            body.addObject(datum)
            axes.append(datum)
        name = stem if index == 0 else f"{stem}_c{index}"
        feature = document.addObject("PartDesign::LinearPattern", name)
        body.addObject(feature)
        feature.Direction = (axes[0], [""])
        feature.Direction2 = (axes[1], [""])
        body.Tip = feature
        return feature, tuple(axes)

    def _datum_plane(self, document, body, name: str):
        plane = document.addObject("PartDesign::Plane", name)
        body.addObject(plane)
        return plane

    def save(self, request: dict) -> dict:
        document = self._document(request.get("document_id", "документ"))
        path = request.get("path") or ""
        if not path:
            return _error("NO_PATH", "не указан путь сохранения", ["path"])
        document.saveAs(path)
        return {"status": "valid", "diagnostics": [], "path": document.FileName}

    def open(self, request: dict) -> dict:
        """Открыть документ и пересчитать его.

        Пересчёт обязателен: смысл сохранения параметрической модели в том,
        что она перестраивается, а не в том, что сохранились треугольники.
        """
        import FreeCAD

        path = request.get("path") or ""
        if not os.path.isfile(path):
            return _error("NO_FILE", f"файла нет: {path}", ["path"])
        document = FreeCAD.openDocument(path)
        document.recompute()
        name = request.get("document_id") or document.Name
        self.documents[name] = document
        bodies = []
        for item in document.Objects:
            if item.TypeId == "PartDesign::Body":
                self.bodies[(document.Name, item.Name)] = item
                bodies.append(item.Name)
        return {"status": "valid", "diagnostics": [], "documentId": name,
                "bodies": bodies,
                "features": [item.Name for item in document.Objects]}

    def scene(self, request: dict) -> dict:
        """Деталь сеткой. Без указания тела — ВСЕ тела документа.

        Тел в детали бывает несколько: операция, построенная «без
        объединения», даёт своё. Показывать при этом одно значило бы
        прятать материал, который в детали есть.
        """
        document = self._document(request.get("document_id", "документ"))
        wanted = request.get("body_id") or ""
        if wanted:
            # НЕ `_body`: тот заводит недостающее тело вместе со всей его
            # системой координат, и опечатка в имени оставляла бы в детали
            # пустое тело, которого никто не просил. Запрос только смотрит
            # и менять деталь не смеет.
            body = self._known_body(document, wanted)
            if body is None:
                return _error("NO_BODY", f"тела «{wanted}» в детали нет",
                              ["body_id"])
            bodies = [body]
        else:
            bodies = [item for item in document.Objects
                      if item.TypeId == "PartDesign::Body"]
        alive = [item for item in bodies if _has_shape(item)]
        if not alive:
            return _error("EMPTY_RESULT", "в детали нет ни одного тела", [])
        return _scene_of(alive)

    def material_span(self, request: dict) -> dict:
        """Интервалы материала вдоль оси отверстия (§32).

        Возвращает ``[[вход, выход], …]`` — расстояния вдоль оси от
        заданной точки. Их может быть несколько: ось, прошедшая через
        деталь с полостью, встречает материал дважды, и «толщина» тогда
        не одно число.

        Считать по габариту нельзя. Для стека выхода важна НАСТОЯЩАЯ
        дальняя поверхность: у ступенчатой детали габарит скажет 25 там,
        где под этой точкой десять, и зенковка выхода повисла бы в
        воздухе.
        """
        import FreeCAD as App
        import Part

        document = self._document(request.get("document_id", "документ"))
        wanted = request.get("body_id") or ""
        if wanted:
            body = self._known_body(document, wanted)
            if body is None:
                return _error("NO_BODY", f"тела «{wanted}» в детали нет",
                              ["body_id"])
            bodies = [body]
        else:
            bodies = [item for item in document.Objects
                      if item.TypeId == "PartDesign::Body"]
        alive = [item for item in bodies if _has_shape(item)]
        if not alive:
            return _error("NO_BASE", "тела ещё нет", [])
        shapes = [item.Shape for item in alive]
        # Операция меряет материал ТАКИМ, каким он был ДО НЕЁ. Иначе на
        # втором пересчёте она смотрит на деталь, которую сама же
        # просверлила: в её отверстиях материала нет, и все позиции разом
        # объявляются негодными. Ровно это и происходило — десять
        # отверстий строились, а при следующем пересчёте пропадали.
        editing = request.get("feature_id") or ""
        if editing:
            feature = document.getObject(editing)
            base = getattr(feature, "BaseFeature", None) if feature else None
            if base is not None and _has_shape(base):
                shapes = [base.Shape]
        axis = App.Vector(*[float(value) for value in
                            (request.get("axis") or (0.0, 0.0, 1.0))])
        if axis.Length < 1e-9:
            return _error("HOLE_INVALID_DIRECTION",
                          "направление оси нулевое", ["axis"])
        axis = axis.normalize()
        reach = float(request.get("reach", 1.0e4))
        found = []
        for item in request.get("points") or ():
            origin = App.Vector(*[float(value) for value in item])
            line = Part.LineSegment(origin - axis * reach,
                                    origin + axis * reach).toShape()
            spans = []
            for shape in shapes:
                for edge in shape.common(line).Edges:
                    ends = [vertex.Point for vertex in edge.Vertexes]
                    if len(ends) < 2:
                        continue
                    along = sorted((point - origin).dot(axis)
                                   for point in ends)
                    spans.append([along[0], along[-1]])
            found.append(sorted(spans))
        return {"status": "valid", "spans": found, "diagnostics": []}

    def hole_tool(self, request: dict) -> dict:
        """Отверстия по осевым контурам: N инструментов, ОДИН вычет.

        Инструмент — тело вращения по замкнутой ломаной. Все экземпляры
        складываются в КОМПАУНД и вычитаются разом через
        `PartDesign::Boolean`: это полноправная операция дерева, у неё
        есть история и откат, и в дереве она одна на сколько угодно
        отверстий.

        Компаунд, а не слияние. Измерено на тридцати отверстиях: слияние
        инструментов с последующим вычетом берёт 173 мс, тридцать
        отдельных вычетов — 276 мс, вычет компаундом — 39 мс. Сливать
        непересекающиеся инструменты значит работать впустую.
        """
        import Part

        document = self._document(request.get("document_id", "документ"))
        body = self._body(document, request.get("body_id", "Тело"))
        if not _has_shape(body):
            return _error("NO_BASE", "нечего сверлить: тела ещё нет",
                          ["body_id"])
        instances = list(request.get("instances") or ())
        if not instances:
            return _error("HOLE_NO_POSITION",
                          "не задано ни одного отверстия", ["instances"])
        tools = []
        for number, item in enumerate(instances):
            try:
                tools.append(_revolved(item))
            except Exception as failure:  # noqa: BLE001
                return _error("HOLE_BOOLEAN_FAILED",
                              f"инструмент {number + 1} не построился: "
                              f"{failure}", ["instances"])

        editing = request.get("feature_id") or ""
        created = not editing
        previous_tip = getattr(body, "Tip", None)
        if editing:
            feature = document.getObject(editing)
            if feature is None:
                return _error("NO_FEATURE", f"операции {editing!r} нет", [])
            carrier = feature.Group[0] if feature.Group else None
            if carrier is None:
                return _error("NO_FEATURE",
                              f"у операции {editing!r} нет инструмента", [])
        else:
            self.counter += 1
            carrier = document.addObject("Part::Feature",
                                         f"Инструмент{self.counter}")
            feature = document.addObject("PartDesign::Boolean",
                                         f"Отверстия{self.counter}")
            body.addObject(feature)
            feature.Type = "Cut"
            feature.Group = [carrier]
        carrier.Shape = Part.makeCompound(tools)
        body.Tip = feature
        return self._finish(document, body, feature, request, created,
                            previous_tip, [feature, carrier], ["instances"])

    def section(self, request: dict) -> dict:
        """След детали на плоскости — «кривая пересечения».

        Считает ШТАТНЫЙ `Shape.section` (§20.1). Своей математики
        пересечения здесь нет и быть не должно: ядро уже возвращает
        настоящие кривые — прямую прямой, окружность окружностью, — а по
        следу потом чертят и ставят размеры, и ломаная вместо окружности
        была бы подменой.

        Плоскость берётся БЕСКОНЕЧНОЙ. У конечной грани пришлось бы
        считать запас по габариту детали и всё равно промахнуться на
        детали, выросшей после: край срезал бы часть следа молча.
        """
        import FreeCAD as App
        import Part

        document = self._document(request.get("document_id", "документ"))
        wanted = request.get("body_id") or ""
        if wanted:
            # НЕ `_body`: тот заводит недостающее тело, и опечатка в имени
            # оставляла бы в детали пустое тело, которого никто не просил.
            # Запрос только смотрит и менять деталь не смеет.
            body = self._known_body(document, wanted)
            if body is None:
                return _error("NO_BODY", f"тела «{wanted}» в детали нет",
                              ["body_id"])
            bodies = [body]
        else:
            bodies = [item for item in document.Objects
                      if item.TypeId == "PartDesign::Body"]
        alive = [item for item in bodies if _has_shape(item)]
        if not alive:
            return _error("NO_BASE", "нечего резать: тела ещё нет", [])
        origin = App.Vector(*[float(value) for value in
                              (request.get("origin") or (0.0, 0.0, 0.0))])
        normal = App.Vector(*[float(value) for value in
                              (request.get("normal") or (0.0, 0.0, 1.0))])
        if normal.Length < 1e-9:
            return _error("BAD_PLANE", "нормаль плоскости нулевая", ["normal"])
        plane = Part.Plane(origin, normal.normalize()).toShape()
        edges = []
        for body in alive:
            for edge in body.Shape.section(plane).Edges:
                edges.append(_edge_data(edge))
        return {"status": "valid", "edges": edges, "diagnostics": []}

    def drop_feature(self, request: dict) -> dict:
        """Убрать одну операцию вместе с её носителем профиля.

        Нужно тому, кто пробует варианты: правило разворота строит
        операцию, смотрит на объём и, если ничего не вышло, обязано убрать
        за собой. Пока оно этого не делало, неудачная попытка оставалась в
        теле — операция, которая сейчас ничего не меняет, но после правки
        эскиза начинает резать деталь.
        """
        document = self._document(request.get("document_id", "документ"))
        body = self._body(document, request.get("body_id", "Тело"))
        name = request.get("feature_id") or ""
        feature = document.getObject(name)
        if feature is None:
            return {"status": "valid", "diagnostics": []}
        previous = getattr(feature, "BaseFeature", None)
        litter = []
        carrier = None
        try:
            carrier = _carrier_of(feature)
        except Exception:  # noqa: BLE001 — у обработки рёбер носителя нет
            carrier = None
        if carrier is not None:
            litter.append(carrier)
        # Сетка с пропусками — это НЕСКОЛЬКО операций движка на одну строку
        # дерева, и у каждой свои опорные оси. Убрать надо всё: оставленная
        # часть продолжает ставить копии операции, которой в дереве уже нет.
        #
        # Ищется по ИМЕНИ с разделителем: у частей и осей оно начинается с
        # имени головной и подчёркивания. Без разделителя «Сетка1» было бы
        # началом «Сетка10», и уборка одной сетки сносила бы чужую.
        litter.extend(item for item in document.Objects
                      if item.Name.startswith(f"{name}_"))
        self._drop(document, feature, *litter, restore_tip=(body, previous))
        return {"status": "valid", "diagnostics": []}

    def clear(self, request: dict) -> dict:
        """Забыть ОДИН документ, оставив остальные.

        Нужно при пересборке дерева: движок должен забыть снятое. Общий
        сброс для этого не годится — он закрывает все документы разом, и
        удаление операции в одной детали стирало бы вторую открытую.
        """
        import FreeCAD

        name = request.get("document_id", "документ")
        document = self.documents.pop(name, None)
        if document is not None:
            for key in [key for key in self.bodies if key[0] == document.Name]:
                self.bodies.pop(key, None)
            try:
                FreeCAD.closeDocument(document.Name)
            except Exception:  # noqa: BLE001 — документ мог быть уже закрыт
                pass
        return {"status": "valid", "diagnostics": []}

    def reset(self, request: dict) -> dict:
        import FreeCAD

        for document in list(self.documents.values()):
            FreeCAD.closeDocument(document.Name)
        self.documents.clear()
        self.bodies.clear()
        return {"status": "valid", "diagnostics": []}


# --- перевод форм в данные --------------------------------------------


def _error(code: str, message: str, arguments) -> dict:
    return {
        "status": "invalid",
        "diagnostics": [{"code": code, "message": message, "severity": "error",
                         "arguments": list(arguments), "backend": "FreeCAD"}],
    }


def _set_profile(carrier, profile: dict, start_offset: float = 0.0,
                 thin=None) -> None:
    """Положить профиль на носитель вместе с ПЛОСКОСТЬЮ ЭСКИЗА.

    Носитель — двумерный объект FreeCAD, и PartDesign считает его местную
    плоскость XY плоскостью эскиза: нормаль профиля берётся из положения
    носителя, а не из геометрии грани. Пока положение было единичным,
    нормалью считалась мировая Z — и вращение вокруг оси, лежащей в
    настоящей плоскости профиля, отвергалось как «ось перпендикулярна
    профилю».
    """
    import FreeCAD as App

    place = _placement_of(profile, start_offset)
    carrier.Shape = _face_of(profile.get("regions") or (), _into(place), thin,
                             (profile.get("chains") or ()) if thin else (),
                             profile.get("normal") or (0.0, 0.0, 1.0))
    # Порядок важен: присвоение ``Shape`` вбирает положение формы в себя,
    # и ``Placement`` надо ставить ПОСЛЕ.
    carrier.Placement = place


def _placement_of(profile: dict, start_offset: float = 0.0):
    """Положение носителя = плоскость эскиза, пришедшая от ProtoCAD.

    ``start_offset`` сдвигает её вдоль нормали. Это и есть «Начало»
    операции: у PartDesign своего свойства для него нет, а двигать
    плоскость профиля — точно и ровно то, что имеется в виду.
    """
    import FreeCAD as App

    origin = App.Vector(*[float(v) for v in (profile.get("origin") or (0, 0, 0))])
    normal = App.Vector(*[float(v) for v in (profile.get("normal") or (0, 0, 1))])
    x_axis = App.Vector(*[float(v) for v in
                          (profile.get("x_direction") or (1, 0, 0))])
    if normal.Length < 1e-12:
        raise ValueError("нормаль плоскости эскиза нулевой длины")
    normal.normalize()
    # Ось X спрямляется в плоскость: приходит она от ProtoCAD и может быть
    # чуть скошена накопленной ошибкой, а поворот требует точной тройки.
    x_axis = x_axis - normal * normal.dot(x_axis)
    if x_axis.Length < 1e-12:
        x_axis = App.Vector(1.0, 0.0, 0.0) if abs(normal.x) < 0.9 \
            else App.Vector(0.0, 1.0, 0.0)
        x_axis = x_axis - normal * normal.dot(x_axis)
    x_axis.normalize()
    return App.Placement(origin + normal * float(start_offset),
                         App.Rotation(x_axis, normal.cross(x_axis), normal))


def _into(place):
    """Перевод точки детали в координаты плоскости эскиза.

    Кривые строятся СРАЗУ в местных координатах, а не переносятся туда
    готовой гранью. Перенос готовой пришлось бы делать через
    ``transformGeometry``, а он превращает окружность в сплайн — и
    отверстие, которое ищет в профиле окружности, переставало строиться.
    """
    inverse = place.inverse()

    def convert(point):
        import FreeCAD as App

        moved = inverse.multVec(App.Vector(*[float(v) for v in point]))
        return App.Vector(moved.x, moved.y, 0.0)

    return convert


def _place_axis(datum, origin, direction) -> None:
    """Поставить опорную линию или плоскость по числам.

    У датума FreeCAD собственная ось — местная Z: у линии это её
    направление, у плоскости — нормаль. Поэтому размещение задаётся
    поворотом, переводящим Z в нужное направление.
    """
    import FreeCAD as App

    vector = App.Vector(*[float(value) for value in direction])
    if vector.Length < 1e-12:
        raise ValueError("направление нулевой длины")
    datum.Placement = App.Placement(
        App.Vector(*[float(value) for value in origin]),
        App.Rotation(App.Vector(0.0, 0.0, 1.0), vector))


def _wire(curves, into=None, normal=(0.0, 0.0, 1.0)):
    """Петля по кривым. ``into`` — перевод точки в нужные координаты.

    ``normal`` — нормаль плоскости профиля. Нужна не всем кривым, а
    параболе: она задана вершиной и фокусом, и без плоскости ось у неё
    определена, а сторона — нет.


    Кривые строятся уже в тех координатах, в которых лягут: окружность
    остаётся окружностью, дуга дугой. Перенос готовой грани сделал бы из
    них сплайны, а отверстие ищет в профиле именно окружности.
    """
    import FreeCAD as App
    import Part

    def point(values):
        return into(values) if into else App.Vector(*values)

    edges = []
    for curve in curves:
        kind = curve.get("kind")
        if kind == "line":
            edges.append(Part.LineSegment(point(curve["a"]),
                                          point(curve["b"])).toShape())
        elif kind == "arc":
            edges.append(Part.Arc(point(curve["a"]), point(curve["m"]),
                                  point(curve["b"])).toShape())
        elif kind == "hyperbola":
            # Гипербола задана так же, как эллипс: центр, точка на
            # действительной оси и мнимая полуось. У `Part.Hyperbola`
            # ПАРАМЕТР ТОТ ЖЕ, что у нашего решателя, — проверено на обоих,
            # и переводить его не надо.
            centre = point(curve["c"])
            tip = point(curve["m"])
            axis = App.Vector(*[float(value) for value in normal])
            along = tip.sub(centre)
            hyperbola = Part.Hyperbola()
            hyperbola.Center = centre
            hyperbola.MajorRadius = along.Length or 1e-9
            hyperbola.MinorRadius = float(curve["b"])
            hyperbola.Axis = axis
            # Направление действительной оси задаётся поворотом: у
            # `Part.Hyperbola` она идёт по местному X, и совместить её с
            # нашей надо явно, иначе кривая ляжет повёрнутой.
            hyperbola.XAxis = along.normalize()
            edges.append(Part.Edge(hyperbola, float(curve.get("t0", 0.0)),
                                   float(curve.get("t1", 0.0))))
        elif kind == "parabola":
            # Парабола задана вершиной и фокусом. У `Part.Parabola` есть
            # ровно такой конструктор — своей математики не нужно (§20.1).
            # Ось строится из тех же двух точек, а нормаль плоскости эскиза
            # приходит вместе с профилем.
            vertex = point(curve["v"])
            focus = point(curve["f"])
            axis = App.Vector(*[float(value) for value in normal])
            parabola = Part.Parabola(focus, vertex, axis)
            first = float(curve.get("t0", 0.0))
            last = float(curve.get("t1", 0.0))
            # Собственный параметр у нас — местная координата поперёк оси,
            # а у ядра свой. Переводится он ЧЕРЕЗ ТОЧКИ: считаем концы по
            # нашей формуле и спрашиваем ядро, какие у них параметры. Так
            # договариваться о параметризации через границу не приходится.
            focal = vertex.distanceToPoint(focus) or 1e-9
            along = (focus - vertex).normalize()
            across = axis.cross(along).normalize()
            def _at(value: float):
                return (vertex + along * (value * value / (4.0 * focal))
                        + across * value)
            edges.append(Part.Edge(parabola,
                                   parabola.parameter(_at(first)),
                                   parabola.parameter(_at(last))))
        elif kind == "spline":
            # Полюсы приходят точками и переводятся тем же преобразованием,
            # что и всё остальное. Ядро строит по ним ту же кривую, что
            # показывает эскиз: узлы и степень идут вместе с полюсами.
            spline = Part.BSplineCurve()
            spline.buildFromPolesMultsKnots(
                [point(pole) for pole in curve["p"]],
                [int(value) for value in curve["m"]],
                [float(value) for value in curve["k"]],
                False, int(curve["d"]))
            first = float(curve.get("t0", 0.0))
            last = float(curve.get("t1", 0.0))
            if abs(last - first) < 1e-12:
                edges.append(spline.toShape())
            else:
                low, high = spline.FirstParameter, spline.LastParameter
                edges.append(spline.toShape(low + (high - low) * first,
                                            low + (high - low) * last))
        elif kind == "ellipse":
            # Точка на большой полуоси приходит вместе с центром, поэтому
            # направление получается вычитанием уже В ТЕХ координатах, в
            # которых строится кривая: поворачивать отдельно нечего.
            centre = point(curve["c"])
            tip = point(curve["m"])
            along = tip.sub(centre)
            major = along.Length
            if major < 1e-12:
                raise ValueError("вырожденный эллипс: большая полуось нулевая")
            axis = (App.Vector(0.0, 0.0, 1.0) if into
                    else App.Vector(*curve.get("n", (0.0, 0.0, 1.0))))
            across = axis.cross(along).normalize().multiply(float(curve["b"]))
            ellipse = Part.Ellipse(tip, centre.add(across), centre)
            first, last = float(curve.get("t0", 0.0)), float(curve.get("t1", 0.0))
            if abs(last - first) < 1e-12:
                edges.append(ellipse.toShape())
            else:
                edges.append(Part.ArcOfEllipse(ellipse, first, last).toShape())
        elif kind == "circle":
            # Ось окружности в местных координатах — местная Z: плоскость
            # эскиза и есть плоскость XY носителя.
            axis = (App.Vector(0.0, 0.0, 1.0) if into
                    else App.Vector(*curve["n"]))
            edges.append(Part.Circle(point(curve["c"]), axis,
                                     float(curve["r"])).toShape())
        else:
            raise ValueError(f"неизвестная кривая: {kind!r}")
    return Part.Wire(Part.__sortEdges__(edges))


#: Сборщик граней FreeCAD, разбирающийся во вложенности сам.
FACE_MAKER = "Part::FaceMakerBullseye"

#: Чем заполнять наружный угол при смещении контура: 0 — дугой. Так же
#: ведёт себя смещение в SolidWorks, и так толщина стенки одинакова везде,
#: включая угол.
OFFSET_JOIN = 0


def _thin_of(request: dict):
    """``(наружу, внутрь)`` для тонкой стенки. ``None`` — стенки нет.

    Материал по умолчанию идёт ВНУТРЬ нарисованного контура: контур —
    наружная кромка стенки. При «двустороннем» первая толщина остаётся
    внутренней, вторая уходит наружу — тогда двусторонний с нулевой
    второй толщиной совпадает с односторонним, а не расходится с ним.
    """
    kind = request.get("thin") or ""
    if not kind:
        return None
    first = float(request.get("thickness", 1.0) or 0.0)
    second = float(request.get("thickness2", 1.0) or 0.0)
    if kind == "mid":
        above = below = first / 2.0
    elif kind == "two":
        above, below = second, first
    else:
        above, below = 0.0, first
    if request.get("thin_flip"):
        above, below = below, above
    if above + below <= 1e-9:
        raise ValueError("толщина стенки нулевая")
    return (above, below)


def _offset(wire, distance: float, fill: bool = False, opened: bool = False):
    """Смещение контура средствами FreeCAD.

    Положительное расстояние уводит ЗАМКНУТЫЙ контур наружу независимо от
    того, в какую сторону он обойдён: OCCT приводит обход сам. Проверено —
    прямоугольник и окружность, по часовой и против, дают один и тот же
    знак. Полагаться на обход было бы нельзя: у окружности эскиза он уже
    оказывался не тем, что у прямоугольника.
    """
    return wire.makeOffset2D(distance, OFFSET_JOIN, fill, opened, False)


def _thin_face(wire, above: float, below: float):
    """Кольцо между двумя смещениями контура — грань тонкой стенки.

    Замкнутый контур: оба смещения строятся НЕЗАВИСИМО от исходного и
    собираются штатным сборщиком. Через промежуточный контур считать
    нельзя: смещение наружу скругляет угол, обратное смещение уже не
    возвращает его острым, и на прямоугольнике 40 × 30 со стенкой 2 мм
    расхождение выходит 0.86 мм² — немного, но это ошибка на ровном месте.

    Разомкнутый контур собрать так нельзя: у него нет «внутри», и две
    смещённые кривые надо ещё соединить по торцам. Это умеет смещение с
    заливкой — но только на ОДНУ сторону. Двусторонняя стенка получается
    сложением двух односторонних, и это не хитрость, а единственный
    точный способ: если сместить контур на одну сторону, а потом от
    полученного отложить полную толщину, стенка перестанет быть
    расположенной по контуру. На угле 90° и толщине 2 мм разница выходит
    139.14 мм² вместо 139.79 — стенка сдвигается, оставаясь ровной.
    """
    import Part

    if not wire.isClosed():
        if below <= 1e-9:
            return _offset(wire, above, fill=True, opened=True)
        if above <= 1e-9:
            return _offset(wire, -below, fill=True, opened=True)
        joined = _offset(wire, above, fill=True, opened=True).fuse(
            _offset(wire, -below, fill=True, opened=True))
        # Две половины смыкаются по самому контуру, и без очистки шов
        # остаётся ребром внутри грани.
        return joined.removeSplitter()
    outer = wire if above <= 1e-9 else _offset(wire, above)
    inner = wire if below <= 1e-9 else _offset(wire, -below)
    return Part.makeFace([outer, inner], FACE_MAKER)


def _face_of(regions, into=None, thin=None, chains=None,
             normal=(0.0, 0.0, 1.0)):
    """Грань или составной объект по областям.

    Собирает грань ШТАТНЫЙ сборщик FreeCAD. Он сам определяет, какая петля
    наружная, какая — отверстие, и сам приводит их направления.

    Раньше внутренние петли разворачивались вручную, и это работало ровно
    до тех пор, пока все петли выходили из разбора эскиза в одну сторону.
    У окружности направление оказывалось другим — разворот делал её петлю
    сонаправленной наружной, площадь ПРИБАВЛЯЛАСЬ вместо вычитания
    (3770 мм² вместо 2960), а разбиение грани разваливалось: на детали
    появлялся тёмный клин, которого нет в геометрии. Отсюда и «ломается
    только с окружностями».

    Направление петли — не то, о чём должен думать интерфейс. У FreeCAD
    для этого есть готовое, и брать надо его (docs/08_ENGINE_BACKEND.md,
    §20.1: сначала проверить, что уже умеет backend).
    """
    import Part

    faces = []
    for region in regions:
        wires = [_wire(region.get("outer") or (), into, normal)]
        for loop in region.get("inner") or ():
            wires.append(_wire(loop, into, normal))
        if thin:
            # Стенка наращивается от КАЖДОГО контура области, включая
            # контуры отверстий: отверстие в тонкостенной детали — это
            # своя стенка, а не дырка в чужой.
            faces.extend(_thin_face(one, thin[0], thin[1]) for one in wires)
        else:
            faces.append(Part.makeFace(wires, FACE_MAKER))
    for chain in chains or ():
        wire = _wire(chain, into, normal)
        if not thin:
            raise ValueError(
                "разомкнутый контур выдавливается только тонкой стенкой: "
                "площади он не ограничивает")
        faces.append(_thin_face(wire, thin[0], thin[1]))
    if not faces:
        raise ValueError("профиль пуст")
    if len(faces) == 1:
        return faces[0]
    joined = faces[0]
    for face in faces[1:]:
        joined = joined.fuse(face)
    return joined


def _kept(count: int, skip) -> list:
    """Какие экземпляры строить. Номера с нуля, ноль не пропускается."""
    dropped = {int(value) for value in (skip or ()) if int(value) > 0}
    return [index for index in range(int(count)) if index not in dropped]


def _gaps(count: int, skip, step: float) -> tuple:
    """(сколько строить, промежутки) с учётом пропущенных экземпляров.

    Пропуск выражается ПРОМЕЖУТКАМИ, а не отдельным свойством: своего
    «пропустить» у `PartDesign` нет, зато есть `Spacings` — расстояние
    между соседними по списку. Выбросить второй из четырёх — значит
    поставить три штуки с промежутками «шаг, два шага». Проверено
    опытом: `Spacings=[20, 40, 20]` ставит приливы на 11, 31, 71, 91.

    Так и должно быть: остальные экземпляры остаются РОВНО на своих
    местах. Сдвинуть их, убрав один, значило бы построить другую деталь.
    """
    kept = _kept(count, skip)
    gaps = [(kept[i + 1] - kept[i]) * float(step)
            for i in range(len(kept) - 1)]
    return len(kept), gaps


def _row(feature, request: dict, suffix: str, count: int) -> None:
    """Один ряд линейного массива: чем он задан и сколько в нём штук.

    ``suffix`` — «» для первого направления, «2» для второго: у FreeCAD
    свойства второго ряда называются так же с двойкой на конце.

    Шаг между соседними и общая длина — РАЗНЫЕ задачи. «Через каждые 20
    мм» и «уложить пять штук на 80 мм» дают разные детали, и подмена
    одного другим расходится тем сильнее, чем больше экземпляров.
    """
    mode = str(request.get(f"mode{suffix}" if suffix else "mode", "spacing"))
    length = float(request.get(f"length{suffix}" if suffix else "length", 100.0))
    step = float(request.get(f"spacing{suffix}" if suffix else "spacing", 20.0))
    # Пропуск задаётся только в ПЕРВОМ ряду: во втором номера экземпляров
    # означали бы уже пару (строка, столбец), и один список их не выразит.
    skip = request.get("skip") if not suffix else ()
    if skip and int(count) > 1:
        # Разложенное «на длину» переводится в шаг: чтобы выбросить один
        # экземпляр, надо знать расстояние между соседними. Правило взято
        # у движка и закреплено проверкой, а не выведено на глаз.
        if mode == "extent":
            step = length / max(1, int(count) - 1)
        kept, gaps = _gaps(int(count), skip, step)
        setattr(feature, f"Mode{suffix}", "Spacing")
        setattr(feature, f"Offset{suffix}", step)
        setattr(feature, f"Occurrences{suffix}", max(2, kept))
        setattr(feature, f"Spacings{suffix}", gaps or [-1.0])
        return
    setattr(feature, f"Spacings{suffix}", [-1.0])
    if mode == "extent":
        setattr(feature, f"Mode{suffix}", "Extent")
        setattr(feature, f"Length{suffix}", length)
    else:
        setattr(feature, f"Mode{suffix}", "Spacing")
        setattr(feature, f"Offset{suffix}", step)
    setattr(feature, f"Occurrences{suffix}", max(2, int(count)))


def _before_shape(feature):
    """Форма тела ДО этой операции. ``None`` — операция первая."""
    base = getattr(feature, "BaseFeature", None)
    try:
        if base is None or base.Shape.isNull() or not base.Shape.Faces:
            return None
        return base.Shape
    except Exception:  # noqa: BLE001
        return None


def _change(before, after):
    """Что операция ДЕЙСТВИТЕЛЬНО сделала с телом: ``(сетка, вид)``.

    Вид — ``"added"`` или ``"removed"``. Считается вычитанием форм, а не
    берётся у `AddSubShape`, и это принципиально:

    * `AddSubShape` — ИНСТРУМЕНТ, а не снятое им. У кармана инструмент
      торчит наружу детали: на решете он показывал 36000 мм³ там, где
      снимается 16303. Предпросмотр обещал вырез вдвое больше настоящего;
    * у обработки рёбер `AddSubShape` нет вовсе, и фаска показывала
      предпросмотром ВСЁ ТЕЛО — то есть не показывала ничего.

    Дороже, но не дороже того, что уже платится: на решете из тридцати
    отверстий вычитание берёт 31 мс против 56 мс на тесселяцию самого
    тела, которую предпросмотр делает и так.
    """
    if before is None or after is None:
        return None, ""
    try:
        removed = before.cut(after)
        if removed.Faces and removed.Volume > _CHANGE_MINIMUM:
            return _mesh(removed), "removed"
        added = after.cut(before)
        if added.Faces and added.Volume > _CHANGE_MINIMUM:
            return _mesh(added), "added"
    except Exception:  # noqa: BLE001 — булева операция может не сойтись
        return None, ""
    return None, ""


#: Ниже этого объём считается крохами на стыке граней, а не работой, мм³.
_CHANGE_MINIMUM = 1e-6


def _tool_mesh(feature):
    """Сетка ТОЛЬКО того, что операция добавляет или снимает."""
    mesh, _ = _change(_before_shape(feature), _feature_shape(feature))
    if mesh is not None:
        return mesh
    # Первая операция тела: вычитать не из чего, и добавленное — это она
    # сама. Здесь `AddSubShape` уже не врёт: вычитать его не из чего.
    shape = getattr(feature, "AddSubShape", None)
    try:
        if shape is None or shape.isNull() or not shape.Faces:
            return None
        return _mesh(shape)
    except Exception:  # noqa: BLE001 — у обработки рёбер такого нет
        return None


def _feature_shape(feature):
    try:
        shape = feature.Shape
        return None if shape.isNull() or not shape.Faces else shape
    except Exception:  # noqa: BLE001
        return None


def _meshes(shape, feature, preview: bool) -> dict:
    """Сетки для ответа: тела и инструмента.

    При ПРЕДПРОСМОТРЕ тело не отправляется, если есть сетка инструмента:
    окно её всё равно не рисует — оно показывает инструмент поверх уже
    построенной детали, а деталь у него на экране и так. Отправлялись обе,
    и это была ровно половина ответа: на решете из тридцати отверстий 3,45
    МБ текста считались, сериализовались, шли по трубе и разбирались,
    чтобы быть выброшенными.

    Когда инструмента нет (движок его не выделил), тело шлётся: показать
    результат целиком лучше, чем не показать ничего, — и окно на это
    рассчитывает.
    """
    changed, kind = _change(_before_shape(feature), _feature_shape(feature))
    tool = changed if changed is not None else _tool_mesh(feature)
    if preview and tool is not None:
        return {"mesh": None, "toolMesh": tool, "toolKind": kind or "added"}
    return {"mesh": _mesh(shape), "toolMesh": tool, "toolKind": kind or "added"}


#: Концевые условия протокола → свойство `Type` у PartDesign.
_END_TYPES = {
    "blind": "Length", "through_all": "ThroughAll",
    "up_to_first": "UpToFirst", "up_to_last": "UpToLast",
    "up_to_face": "UpToFace",
    # «От средней плоскости» — это длина плюс симметрия, а не свой тип.
    "mid_plane": "Length",
}


def _aim(feature, base, target: int, prop: str, argument: str,
         name: str = ""):
    """Указать операции грань, до которой строить. ``None`` — получилось.

    Грань называется устойчивым именем, если оно есть: номер годен ровно
    до первой правки выше по дереву.
    """
    if base is None:
        return _error("NO_TARGET", "«до грани» просит грань, а тела ещё нет",
                      [argument])
    found, failure = _resolve(base.Shape, "Face", [name] if name else [],
                              [target], argument)
    if failure is not None:
        return failure
    if not found:
        return _error("NO_TARGET", "не указана грань, до которой строить",
                      [argument])
    setattr(feature, prop, (base, [f"Face{found[0] + 1}"]))
    return None


def _resolve(shape, kind: str, names, numbers, argument: str):
    """Номера подэлементов по устойчивым именам. ``(индексы, отказ)``.

    Имя главнее номера: номер годен ровно до правки выше по дереву. Если
    имя было записано, а сейчас не находится, это ОТКАЗ — молча взять
    подэлемент с тем же номером значило бы обработать не то, что просили,
    и заметить это можно было бы только по форме детали.
    """
    total = len(shape.Faces if kind == "Face" else shape.Edges)
    found = []
    lost = []
    names = list(names or ())
    numbers = [int(value) for value in (numbers or ())]
    for position, number in enumerate(numbers):
        name = names[position] if position < len(names) else ""
        if name:
            try:
                back = shape.getElementName(name, 2)
            except Exception:  # noqa: BLE001
                back = ""
            if back and back.startswith(kind):
                found.append(int(back[len(kind):]) - 1)
                continue
            lost.append(name)
            continue
        # Имени нет — запись сделана до появления карты элементов.
        found.append(number)
    what = "рёбер" if kind == "Edge" else "граней"
    if lost:
        return [], _error(
            "ELEMENT_LOST",
            f"этих {what} в детали нет: пропало {len(lost)} из "
            f"{len(numbers)}, всего сейчас {total}. Правка выше по дереву "
            f"их убрала — выберите заново",
            [argument])
    beyond = [value for value in found if not 0 <= value < total]
    if beyond:
        return [], _error(
            "ELEMENT_LOST",
            f"{what} с номерами {beyond} в детали нет: их всего {total}. "
            f"Похоже, форма изменилась выше по дереву",
            [argument])
    return found, None


def _scene_of(bodies) -> dict:
    """Ответ по нескольким телам: сетки склеены, номера не пересекаются.

    Номер грани в картинке выбора обязан быть уникальным на всю деталь,
    иначе щелчок по грани второго тела попадает в грань первого. Поэтому
    номера граней сдвигаются, а ``entities`` сохраняют СВОИ, внутри тела:
    операция называет грань в пределах своего тела, и подменять там номер
    сквозной нумерацией значило бы обработать не то.
    """
    positions, normals, faces, bodies_per_vertex = [], [], [], []
    edge_positions, edge_ids, edge_bodies = [], [], []
    entities, volume = [], 0.0
    low = [None] * 3
    high = [None] * 3
    shift = 0
    for number, body in enumerate(bodies, start=1):
        shape = body.Shape
        mesh = _mesh(shape)
        positions.extend(mesh["positions"])
        normals.extend(mesh["normals"])
        faces.extend(value + shift for value in mesh["face_ids"])
        bodies_per_vertex.extend([number] * (len(mesh["positions"]) // 3))
        edge_positions.extend(mesh["edge_positions"])
        edge_ids.extend(mesh["edge_ids"])
        edge_bodies.extend([number] * (len(mesh["edge_positions"]) // 3))
        for item in _entities(shape, body.Name, _tip_shape(body)):
            item["body"] = body.Name
            entities.append(item)
        volume += float(shape.Volume)
        box = _bounds(shape)
        for axis in range(3):
            low[axis] = box[axis] if low[axis] is None else min(low[axis], box[axis])
            high[axis] = (box[axis + 3] if high[axis] is None
                          else max(high[axis], box[axis + 3]))
        shift += len(shape.Faces)
    return {
        "status": "valid", "diagnostics": [],
        "featureId": ";".join(item.Name for item in bodies),
        "volume": volume,
        "bounds": [*low, *high],
        "mesh": {"positions": positions, "normals": normals,
                 "face_ids": faces, "body_ids": bodies_per_vertex,
                 "edge_positions": edge_positions, "edge_ids": edge_ids,
                 "edge_body_ids": edge_bodies},
        "entities": entities,
    }


#: Типы фаски ProtoCAD → типы FreeCAD. Пересказ, а не своя реализация:
#: `PartDesign::Chamfer` умеет все три сам (§20.1).
_CHAMFER_TYPES = {
    "equal": "Equal distance",
    "two": "Two distances",
    "angle": "Distance and Angle",
}


def _edges_of_faces(shape, faces) -> list:
    """Имена всех рёбер указанных граней.

    Ребро принадлежит двум граням, и выбрав две соседние, легко получить
    его дважды. Повторов быть не должно: FreeCAD на повторяющемся ребре
    отказывается строить обработку целиком.
    """
    wanted = []
    for number in faces:
        if not (0 <= number < len(shape.Faces)):
            continue
        for edge in shape.Faces[number].Edges:
            for index, other in enumerate(shape.Edges):
                if other.isSame(edge):
                    wanted.append(f"Edge{index + 1}")
                    break
    return list(dict.fromkeys(wanted))


def _tip_shape(body):
    """Форма вершины тела — источник устойчивых имён подэлементов."""
    tip = getattr(body, "Tip", None)
    try:
        return None if tip is None else tip.Shape
    except Exception:  # noqa: BLE001
        return None


def _base_of(feature):
    """На чём стоит операция: то, что было построено до неё.

    Нужно при правке «до грани»: номера граней человек видел на детали ДО
    этой операции, и разрешать их надо там же.
    """
    return getattr(feature, "BaseFeature", None)


def _carrier_of(feature):
    """Носитель профиля операции.

    ``Profile`` у PartDesign бывает и объектом, и парой «объект,
    подэлементы»: второе — когда профилем служит грань готового тела.
    Разбирать это надо здесь, а не в вызывающем коде.
    """
    profile = feature.Profile
    if isinstance(profile, (tuple, list)):
        return profile[0]
    return profile


def _problems(feature) -> str:
    state = list(feature.State)
    if "Invalid" in state or "Error" in state or "Touched" in state:
        note = getattr(feature, "Error", "") or ""
        return note or f"операция не пересчиталась: {state}"
    return ""


def _revolved(item: dict):
    """Осевой контур, точка и направление → тело вращения.

    Вырожденные звенья снимаются здесь ещё раз, хотя контур приходит уже
    чистым: ядро на отрезке нулевой длины отвечает `Both points are equal`
    и роняет построение целиком, а стоит проверка одно сравнение.
    """
    import FreeCAD as App
    import Part

    axis = App.Vector(*[float(value)
                        for value in (item.get("axis") or (0, 0, 1))])
    if axis.Length < 1e-9:
        raise ValueError("направление оси нулевое")
    axis = axis.normalize()
    origin = App.Vector(*[float(value)
                          for value in (item.get("origin") or (0, 0, 0))])
    # Направление поперёк оси — любое, лишь бы не вдоль неё: контур
    # вращается на полный круг, и с какого места он начат, безразлично.
    sideways = (App.Vector(0.0, 0.0, 1.0) if abs(axis.z) < 0.9
                else App.Vector(1.0, 0.0, 0.0))
    across = axis.cross(sideways).normalize()
    points = [origin + across * float(radius) + axis * float(depth)
              for radius, depth in (item.get("contour") or ())]
    if len(points) < 3:
        raise ValueError("в контуре меньше трёх точек")
    kept = [points[0]]
    for point in points[1:]:
        if (point - kept[-1]).Length > 1e-9:
            kept.append(point)
    if (kept[0] - kept[-1]).Length < 1e-9:
        kept.pop()
    edges = [Part.LineSegment(kept[index], kept[index + 1]).toShape()
             for index in range(len(kept) - 1)]
    edges.append(Part.LineSegment(kept[-1], kept[0]).toShape())
    return Part.Face(Part.Wire(edges)).revolve(origin, axis, 360)


def _spacing_of(request: dict, suffix: str) -> float:
    """Шаг ряда: заданный прямо либо выведенный из общей длины.

    Считается ТЕМИ ЖЕ правилами, что и метки экземпляров в окне
    (`document.pattern_places`): расхождение здесь означало бы, что щелчок
    по метке гасит не ту ячейку, по которой щёлкнули.
    """
    count = max(1, int(request.get(f"count{suffix}", 2) or 2))
    if str(request.get(f"mode{suffix}") or "spacing") == "extent":
        return float(request.get(f"length{suffix}", 100.0)) / max(1, count - 1)
    return float(request.get(f"spacing{suffix}", 20.0))


def _unit(vector) -> tuple:
    values = [float(item) for item in vector]
    length = math.sqrt(sum(value * value for value in values))
    if length < 1e-12:
        return (1.0, 0.0, 0.0)
    return tuple(value / length for value in values)


def _grid_parts(count: int, count2: int, dropped) -> list:
    """Сетка с пропусками — списком частей, по одной операции на часть.

    Считать надо ЧАСТИ, а не ячейки: цена массива у движка идёт на
    операции, а не на копии. Измерено — ряд из 48 копий одной операцией
    строится за 365 мс, тот же ряд сорока семью операциями по копии — за
    5323 мс, в четырнадцать раз дольше. Значит чем меньше частей, тем
    лучше, даже если копий в них поровну.

    Одна операция строит ПРОИЗВЕДЕНИЕ: набор столбцов на набор строк
    (`Spacings` и `Spacings2` задают промежутки). Оба набора обязаны
    содержать нулевой: копии отсчитываются от исходника, и без него
    произведение не выразить.

    Отсюда разложение. «Плохой» столбец — тот, где что-то пропущено; то же
    про строку. Тогда:

    * все ХОРОШИЕ столбцы на все строки — одна операция;
    * все столбцы на все ХОРОШИЕ строки — вторая;
    * что осталось (ячейка и в плохом столбце, и в плохой строке) — по
      операции на ячейку.

    Одна пропущенная ячейка укладывается в ДВЕ операции вместо сорока
    семи: 8 × 6 без одной ячейки строится за 428 мс вместо 12776.

    Худший случай — пропуски по всей сетке — вырождается в ячейку на
    операцию, то есть в то, что было. Хуже не станет.
    """
    skipped = {(number % count, number // count) for number in dropped
               if 0 <= number < count * count2}
    kept = {(column, row) for row in range(count2) for column in range(count)
            if (column, row) not in skipped}
    bad_columns = {column for column, _ in skipped}
    bad_rows = {row for _, row in skipped}

    parts, covered = [], set()
    good_columns = [column for column in range(count)
                    if column not in bad_columns]
    if 0 in good_columns and len(good_columns) * count2 > 1:
        rows = list(range(count2))
        parts.append(("rect", good_columns, rows))
        covered |= {(column, row) for column in good_columns for row in rows}
    good_rows = [row for row in range(count2) if row not in bad_rows]
    if 0 in good_rows and count * len(good_rows) > 1:
        columns = list(range(count))
        parts.append(("rect", columns, good_rows))
        covered |= {(column, row) for column in columns for row in good_rows}
    for column, row in sorted(kept - covered, key=lambda cell: (cell[1], cell[0])):
        if (column, row) == (0, 0):
            continue          # нулевая ячейка — сам исходник
        parts.append(("cell", column, row))
    return parts


def _spacings_for(indexes, step: float) -> list:
    """Промежутки между соседними копиями, в миллиметрах.

    Пустой список движку не годится — у него это «промежутков нет»;
    одиночное «-1» означает «считать по шагу».
    """
    found = [(indexes[k + 1] - indexes[k]) * step
             for k in range(len(indexes) - 1)]
    return found or [-1.0]


def _point_axis(datum, direction) -> None:
    """Направить опорную ось. Точка у неё не важна — важно направление."""
    import FreeCAD as App

    datum.Placement = App.Placement(
        App.Vector(0.0, 0.0, 0.0),
        App.Rotation(App.Vector(0.0, 0.0, 1.0), App.Vector(*direction)))


def _group_of(document, stem: str) -> list:
    """Операции сетки по имени головной, по порядку ячеек.

    Разделитель в имени обязателен: без него «Сетка1» было бы началом
    «Сетка10», и правка одной сетки трогала бы чужие ячейки.
    """
    head = document.getObject(stem)
    if head is None:
        return []
    found = []
    for item in document.Objects:
        if not item.Name.startswith(f"{stem}_c"):
            continue
        tail = item.Name[len(stem) + 2:]
        if tail.isdigit():
            found.append((int(tail), item))
    return [head] + [item for _, item in sorted(found)]


def _has_shape(body) -> bool:
    try:
        return bool(body.Shape.Faces)
    except Exception:  # noqa: BLE001
        return False


def _bounds(shape) -> list:
    box = shape.BoundBox
    return [box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax]


def _points_inward(face, first, second, third, normal) -> bool:
    """Смотрит ли нормаль треугольника внутрь детали.

    Сверяется с нормалью ПОВЕРХНОСТИ в середине треугольника: ``normalAt``
    уже учитывает ориентацию грани, и второй раз её учитывать нельзя.
    """
    middle = (first.add(second).add(third)) * (1.0 / 3.0)
    try:
        surface = face.normalAt(*face.Surface.parameter(middle))
    except Exception:  # noqa: BLE001 — точка вне области определения
        try:
            surface = face.normalAt(0.0, 0.0)
        except Exception:  # noqa: BLE001
            return False
    return normal.dot(surface) < 0.0


def _mesh(shape, deflection: float = 0.1) -> dict:
    """Треугольники детали с нормалями НАРУЖУ.

    Сторона треугольника определяется не по ``Orientation``: разбиение уже
    приходит намотанным согласованно с настоящей ориентацией грани, и
    переворот по этому признаку делал ровно обратное — у бруска десять
    нормалей из двенадцати смотрели внутрь, и деталь чернела при повороте.

    Сторона сверяется с ``normalAt`` — одной пробой на грань: внутри одной
    грани разбиение намотано одинаково, и опрашивать поверхность на каждом
    треугольнике значило бы платить за то же самое сотни раз.
    """
    positions, normals, faces = [], [], []
    for index, face in enumerate(shape.Faces):
        nodes, triangles = face.tessellate(deflection)
        flip = None
        for a, b, c in triangles:
            first, second, third = nodes[a], nodes[b], nodes[c]
            normal = second.sub(first).cross(third.sub(first))
            if flip is None:
                flip = _points_inward(face, first, second, third, normal)
            if flip:
                second, third = third, second
                normal = normal * -1.0
            for point in (first, second, third):
                positions.extend([point.x, point.y, point.z])
                faces.append(index)
            length = normal.Length or 1.0
            for _ in range(3):
                normals.extend([normal.x / length, normal.y / length,
                                normal.z / length])
    edge_positions, edge_ids = [], []
    for index, edge in enumerate(shape.Edges):
        try:
            points = edge.discretize(Deflection=0.05)
        except Exception:  # noqa: BLE001 — вырожденное ребро
            # У конического дна глухого отверстия ребро вырождается в
            # точку. Рисовать там нечего, а ронять из-за него показ всей
            # детали нельзя.
            continue
        for first, second in zip(points, points[1:]):
            edge_positions.extend([first.x, first.y, first.z,
                                   second.x, second.y, second.z])
            edge_ids.extend([index, index])
    return {"positions": positions, "normals": normals, "face_ids": faces,
            "edge_positions": edge_positions, "edge_ids": edge_ids}


def _stable_name(shape, kind: str, index: int) -> str:
    """Устойчивое имя подэлемента из карты элементов FreeCAD.

    Пустая строка — карты нет. Своего именования ProtoCAD не заводит: у
    backend оно уже есть и годами обкатано (§20.1 решения — сначала
    проверить, что умеет backend).
    """
    if shape is None:
        return ""
    try:
        name = shape.getElementName(f"{kind}{index + 1}", 1)
    except Exception:  # noqa: BLE001 — у формы может не быть карты
        return ""
    # Карта возвращает исходное короткое имя, когда сопоставить не с чем.
    return "" if name == f"{kind}{index + 1}" else str(name)


def _entities(shape, feature: str, named=None, body: str = "") -> list:
    """Грани, рёбра и вершины результата вместе с их геометрией.

    Описание идёт числами, а не формой: окно строит по нему плоскость
    эскиза, привязку и опору для связей, не загружая ядро (§10.2).

    У каждого подэлемента есть ДВА имени. ``index`` — номер по месту, им
    удобно указывать мышью прямо сейчас. ``name`` — устойчивое имя из
    карты элементов FreeCAD: оно переживает правку выше по дереву, и
    именно оно хранится в операции. Держать одно только место запрещено
    (§26: не хранить ссылки как `Face6`/`Edge12`).

    ``named`` — форма, чья карта даёт имена. Она НЕ та же, что показанная:
    карта привязана к объекту, и имя от `body.Shape` в вершине тела не
    находится. Имена берутся у ВЕРШИНЫ — там же, где операция потом будет
    их разрешать. Порядок подэлементов у тела и вершины один, это
    проверено; при расхождении имена не выдаются вовсе.
    """
    names = named if named is not None else shape
    if (len(getattr(names, "Faces", ())) != len(shape.Faces)
            or len(getattr(names, "Edges", ())) != len(shape.Edges)):
        names = None
    found = []
    for index, face in enumerate(shape.Faces):
        centre = face.CenterOfMass
        signature = f"{centre.x:.3f},{centre.y:.3f},{centre.z:.3f}"
        found.append({"id": f"{feature}.face@{signature}", "kind": "face",
                      "feature": feature, "index": index,
                      "name": _stable_name(names, "Face", index),
                      "body": body,
                      "origin": "карта элементов FreeCAD",
                      "data": _face_data(face)})
    for index, edge in enumerate(shape.Edges):
        found.append({"id": f"{feature}.edge{index}", "kind": "edge",
                      "feature": feature, "index": index,
                      "name": _stable_name(names, "Edge", index),
                      "body": body,
                      "origin": "карта элементов FreeCAD",
                      "data": _edge_data(edge)})
    for index, vertex in enumerate(shape.Vertexes):
        point = vertex.Point
        found.append({"id": f"{feature}.vertex{index}", "kind": "vertex",
                      "feature": feature, "index": index,
                      "origin": "по месту, не по происхождению",
                      "data": {"point": [point.x, point.y, point.z]}})
    return found


def _face_data(face) -> dict:
    """Описание грани. У плоской — то, из чего строится плоскость эскиза."""
    surface = face.Surface
    kind = type(surface).__name__.lower()
    centre = face.CenterOfMass
    data = {"surface": "plane" if kind == "plane" else kind,
            "area": float(face.Area),
            "center": [centre.x, centre.y, centre.z]}
    if kind == "plane":
        # ``normalAt`` УЖЕ учитывает ориентацию грани: у нижней грани
        # бруска он даёт -Z, у верхней +Z. Переворачивать ещё раз по
        # ``Orientation`` нельзя — обе горизонтальные грани отчитывались
        # тогда нормалью вверх, и эскиз на нижней ушёл бы в материал.
        normal = face.normalAt(0.0, 0.0)
        # Начало плоскости — ПРОЕКЦИЯ НУЛЯ ДЕТАЛИ на неё, а не собственное
        # начало поверхности и не центр грани. От центра координаты эскиза
        # съезжают вместе с формой грани: обрезали угол — и точка (10, 10)
        # оказалась в другом месте.
        # Проекция НУЛЯ ДЕТАЛИ на плоскость грани: точка на плоскости,
        # ближайшая к началу координат. Это ``n * (n · P)``, где P — любая
        # точка грани. Обратное выражение — проекция центра грани на
        # плоскость через ноль — даёт точку не на той плоскости: у верхней
        # грани бруска получалось Z = 0 вместо 12.
        along = normal.dot(centre)
        origin = (normal.x * along, normal.y * along, normal.z * along)
        data.update({
            "origin": [origin[0], origin[1], origin[2]],
            "normal": [normal.x, normal.y, normal.z],
            "x_direction": _x_direction_of(normal),
        })
    elif kind in ("cylinder", "cone"):
        axis = surface.Axis
        # ``center`` остаётся центром МАСС грани — тем же, что у прототипа.
        # Собственное начало поверхности лежит отдельно: подменять им общее
        # поле значило бы, что у двух движков «center» значит разное.
        data.update({"axis": [axis.x, axis.y, axis.z],
                     "radius": float(getattr(surface, "Radius", 0.0)),
                     "axis_origin": [surface.Center.x, surface.Center.y,
                                     surface.Center.z]})
    return data


def _x_direction_of(normal) -> list:
    """Ось X плоскости эскиза при данной нормали.

    Выбирается устойчиво: та из мировых осей, что дальше от нормали. Иначе
    у горизонтальной грани и у вертикальной ось X оказывалась бы разной от
    случая к случаю, и один и тот же эскиз ложился бы повёрнутым.
    """
    import FreeCAD as App

    candidates = [App.Vector(1, 0, 0), App.Vector(0, 1, 0), App.Vector(0, 0, 1)]
    best = min(candidates, key=lambda axis: abs(axis.dot(normal)))
    along = best.sub(normal.multiply(best.dot(normal)))
    if along.Length < 1e-9:
        along = App.Vector(1, 0, 0)
    along.normalize()
    return [along.x, along.y, along.z]


def _edge_data(edge) -> dict:
    """Описание ребра: концы всегда, а для прямого, круглого и эллипса —
    то, чем оно задано. По этому окно строит опоры для связей эскиза."""
    curve = edge.Curve
    kind = type(curve).__name__.lower()
    try:
        points = edge.discretize(Number=2)
    except Exception:  # noqa: BLE001 — вырожденное ребро
        # Ребро, выродившееся в точку: у конического дна глухого
        # отверстия такое есть. Концы у него всё равно есть — вершины.
        ends = edge.Vertexes or ()
        points = [item.Point for item in ends] or [curve.value(0.0)] * 2
        if len(points) == 1:
            points = points * 2
    # Середина — центр масс, а не полусумма концов: у дуги это разные
    # точки, а подпись ребра считается именно по центру масс, и окно
    # должно получить ту же, что ядро, иначе ссылки перестанут находиться.
    middle = edge.CenterOfMass
    data = {"curve": kind, "length": float(edge.Length),
            "middle": [middle.x, middle.y, middle.z],
            "start": [points[0].x, points[0].y, points[0].z],
            "end": [points[-1].x, points[-1].y, points[-1].z]}
    if kind == "circle":
        data.update({"center": [curve.Center.x, curve.Center.y, curve.Center.z],
                     "normal": [curve.Axis.x, curve.Axis.y, curve.Axis.z],
                     "radius": float(curve.Radius)})
    if kind == "ellipse":
        # Наклонный разрез цилиндра — эллипс, и это самый частый нетривиальный
        # случай «Кривой пересечения». Без осей и полуосей окну остаётся одно
        # разбиение, а по ломаной размер не поставить.
        #
        # ``XAxis`` — направление БОЛЬШОЙ полуоси, ``Axis`` — нормаль
        # плоскости эллипса. Имена полей общие с окружностью там, где смысл
        # общий: центр и нормаль читаются одинаково, чем бы ребро ни было.
        major = curve.XAxis
        data.update({"center": [curve.Center.x, curve.Center.y, curve.Center.z],
                     "normal": [curve.Axis.x, curve.Axis.y, curve.Axis.z],
                     "major_axis": [major.x, major.y, major.z],
                     "major_radius": float(curve.MajorRadius),
                     "minor_radius": float(curve.MinorRadius)})
    if kind != "line":
        # Разбиение — для переноса ребра в эскиз. Прямому оно не нужно, а
        # у остальных без него окну пришлось бы восстанавливать кривую по
        # её виду и параметрам, то есть заводить второе ядро.
        #
        # Разбиться может не всё: у конического дна глухого отверстия
        # ребро вырождается в точку, и ядро отказывается его делить.
        # Отказ здесь означал бы, что вся деталь не описалась из-за одного
        # ребра, которое всё равно не переносят в эскиз.
        try:
            sampled = edge.discretize(Deflection=0.05)
            data["points"] = [[point.x, point.y, point.z] for point in sampled]
        except Exception:  # noqa: BLE001 — вырожденное ребро
            data["points"] = [data["start"], data["end"]]
            data["degenerate"] = True
    return data


# --- разговор ---------------------------------------------------------


def main() -> int:
    home = sys.argv[1] if len(sys.argv) > 1 else ""
    if home:
        _setup(home)
    try:
        engine = Engine()
    except Exception as failure:  # noqa: BLE001
        reply({"status": "invalid", "diagnostics": [{
            "code": "ENGINE_START_FAILED", "message": str(failure),
            "severity": "error", "arguments": [], "backend": "FreeCAD"}]})
        return 1

    reply({"status": "ready"})
    handlers = {
        "capabilities": engine.capabilities,
        "pad": engine.pad,
        "dress_up": engine.dress_up,
        "revolve": engine.revolve,
        "hole": engine.hole,
        "draft": engine.draft,
        "shell": engine.shell,
        "pattern": engine.pattern,
        "save": engine.save,
        "open": engine.open,
        "scene": engine.scene,
        "section": engine.section,
        "hole_tool": engine.hole_tool,
        "material_span": engine.material_span,
        "drop_feature": engine.drop_feature,
        "clear": engine.clear,
        "reset": engine.reset,
    }
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except Exception as failure:  # noqa: BLE001
            reply(_error("BAD_REQUEST", f"не разобрано: {failure}", []))
            continue
        command = request.get("command", "")
        if command == "shutdown":
            reply({"status": "valid"})
            return 0
        handler = handlers.get(command)
        if handler is None:
            reply(_error("UNKNOWN_COMMAND", f"неизвестная команда: {command!r}", []))
            continue
        try:
            reply(handler(request))
        except Exception as failure:  # noqa: BLE001
            reply(_error("ENGINE_ERROR",
                         f"{type(failure).__name__}: {failure}",
                         []) | {"trace": traceback.format_exc()[-800:]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
