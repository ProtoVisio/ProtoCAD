"""Состав изделия → STEP со структурой.

Сборка уходит в STEP СБОРКОЙ: вхождения — компонентами с положениями и
позиционными обозначениями, одинаковые детали — одним определением на все
вхождения. Расчётная система видит «R12» и «Корпус», а не «Solid1», и
получает 300 резисторов одной формой, а не 300 копий.

Имена пишутся в UTF-8: без этого кириллица в файле превращается в «Ð¡Ð±».
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from ..model import Assembly, Item


class ExportFailed(RuntimeError):
    """STEP не записан — с объяснением для человека."""


def write_step_tree(root: Item, path) -> Path:
    """Записать изделие в STEP. Сборка — со структурой, деталь — телом."""
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPCAFControl import STEPCAFControl_Writer
    from OCP.STEPControl import STEPControl_AsIs
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFDoc import XCAFDoc_DocumentTool

    from ..prep.io import quiet

    path = Path(path)
    document = TDocStd_Document(TCollection_ExtendedString("XmlOcaf"))
    tool = XCAFDoc_DocumentTool.ShapeTool_s(document.Main())
    labels: dict = {}
    if _definition(tool, root, labels) is None:
        raise ExportFailed(f"{root.label or 'изделие'}: геометрии нет — писать нечего")
    tool.UpdateAssemblies()
    writer = STEPCAFControl_Writer()
    writer.SetNameMode(True)
    with quiet():
        if not writer.Transfer(document, STEPControl_AsIs):
            raise ExportFailed("STEP: перенос геометрии не удался")
        if writer.Write(str(path)) != IFSelect_RetDone:
            raise ExportFailed(f"STEP: файл не записан: {path}")
    return path


def _named(label, text: str) -> None:
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.TDataStd import TDataStd_Name

    if text:
        TDataStd_Name.Set_s(label, TCollection_ExtendedString(text, True))


def _definition(tool, item: Item, labels: dict):
    """Метка определения — одна на изделие, сколько бы раз оно ни входило."""
    if item.stable_id in labels:
        return labels[item.stable_id]
    if isinstance(item, Assembly):
        label = tool.NewShape()
        labels[item.stable_id] = label
        _named(label, item.label)
        placed = 0
        for occurrence in item.placements:
            child = _definition(tool, occurrence.item, labels)
            if child is None:
                continue
            component = tool.AddComponent(label, child, _location(occurrence.transform))
            _named(component, occurrence.reference or occurrence.item.label)
            placed += 1
        if not placed:
            labels[item.stable_id] = None
            return None
        return label
    shape = item.shape
    if shape is None:
        labels[item.stable_id] = None
        return None
    from OCP.TopLoc import TopLoc_Location

    label = tool.AddShape(shape.Located(TopLoc_Location()), False, False)
    _named(label, item.label)
    labels[item.stable_id] = label
    return label


def _location(matrix):
    """Матрица 4×4 → положение OCCT.

    Поворот сначала приводится к строго ортогональному: решатель отдаёт его
    с погрешностью счёта, а OCCT такую матрицу принимает за масштаб и
    отказывается ставить.
    """
    from OCP.gp import gp_Trsf
    from OCP.TopLoc import TopLoc_Location

    matrix = np.asarray(matrix, float)
    left, _values, right = np.linalg.svd(matrix[:3, :3])
    rotation = left @ right
    if np.linalg.det(rotation) < 0:
        raise ExportFailed("положение вхождения зеркальное — STEP такого не хранит")
    shift = matrix[:3, 3]
    transform = gp_Trsf()
    transform.SetValues(*rotation[0], shift[0], *rotation[1], shift[1],
                        *rotation[2], shift[2])
    return TopLoc_Location(transform)
