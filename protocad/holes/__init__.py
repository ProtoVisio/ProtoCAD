"""Подсистема отверстий ProtoCAD.

Спецификация — `docs/09_HOLES.md`. Здесь этап А по её же §51: доменная
модель, каталог и проверка, БЕЗ интерфейса и без построения. Порядок не
случаен: пока отверстие не описано как осевая конструкция, всё остальное
пришлось бы переписывать.

Геометрия (этап Б), панель (этап Д) и трёхмерный эскиз позиций (этап Е)
опираются на это и добавляются поверх, не меняя формата файла.
"""

from .catalog import CATALOG, CatalogError, ThreadCatalog  # noqa: F401
from .model import (  # noqa: F401
    BOTTOM_TYPES,
    COUNTERSINK_MODES,
    Counterbore,
    Countersink,
    Diagnostic,
    ELEMENTS,
    END_CONDITIONS,
    EndCondition,
    FeatureScope,
    HoleDefinition,
    HoleError,
    HoleFeature,
    HolePlacementSet,
    HolePosition,
    REPRESENTATIONS,
    StraightBore,
    TaperBore,
    ThreadDefinition,
    new_id,
)
from . import callout, geometry  # noqa: F401
from .placement import (  # noqa: F401
    instances,
    points_of,
    positions_from,
    positions_from_3d,
)
from .presets import PRESETS, Preset, by_title, titles  # noqa: F401
from .sketch3d import (  # noqa: F401
    HolePositionSketch3D,
    Point3D,
    describe,
    offset_on,
)
from .validation import blocking, check  # noqa: F401
