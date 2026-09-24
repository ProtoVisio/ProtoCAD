"""Помощники проверок препроцессора.

Каталог назван `prep_tests`, а не `tests`: `tests/` в .gitignore —
там живут внутренние проверки, включая карантин GPL (THIRD_PARTY.md).
"""

from protocad.prep import Study


def study_of(*named_shapes, name="проба"):
    study = Study(name=name)
    for label, shape in named_shapes:
        study.add_body(shape, label)
    return study
