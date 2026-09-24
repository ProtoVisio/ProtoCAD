"""Подготовка прибора к тепловому расчёту.

STEP прибора → расчётные единицы (платы с компонентами, корпус, крепёж,
прочее) → расчётные случаи → проверки контактов и пересечений → тепловые
сопротивления контактов → упрощённый STEP и таблицы для расчётной системы.
"""

from .classify import classify  # noqa: F401
from .model import (  # noqa: F401
    COMPONENT, EXCLUDED, FASTENER, HOUSING, OTHER, ROLE_GROUPS, ROLE_TITLES, ROLES,
    SUBSTRATE, Board, Case, Device, Unit,
)


def load(path) -> "Device":
    """Прибор из файла: STEP разбирается заново, сохранённый прибор
    (`.prcadAsm` с разбором) открывается как был."""
    from pathlib import Path

    from ..assembly.importer import load_component

    path = Path(path)
    if path.suffix.lower() == ".prcadasm":
        from .. import format as fmt

        if fmt.read_extra(path, "device") is not None:
            return Device.open(path)
    device = Device(load_component(path))
    device.source = str(path)
    device.notes = classify(device)
    return device
