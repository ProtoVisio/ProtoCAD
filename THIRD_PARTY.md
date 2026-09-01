# Сторонние компоненты ProtoCAD

Составлено по `docs/05_POLICY.md`. Каждая строка — то, что попадает в
поставку либо влияет на её лицензию.

## Production

| Компонент | Версия | Лицензия | Как используется |
|---|---|---|---|
| Open CASCADE Technology | 7.9.3 (через `cadquery-ocp`) | LGPL-2.1 с исключением OCCT | геометрическое ядро B-Rep, вызовы публичного API |
| PlaneGCS | 0.8.0 (`planegcs`) | LGPL-2.1-or-later | решатель эскизов; **производный от FreeCAD** |
| PySide6 / Qt | 6.11.1 | LGPL-3.0 | интерфейс и вьюпорт, динамическая линковка |
| PyOpenGL | 3.1.x | BSD-3-Clause | привязки OpenGL |
| NumPy | 2.x | BSD-3-Clause | вычисления |

Все production-зависимости — permissive либо LGPL. GPL и AGPL отсутствуют.

## Только проверки, в поставку НЕ входит

| Компонент | Версия | Лицензия | Где лежит |
|---|---|---|---|
| py-slvs (SolveSpace) | 1.0.6 | **GPL-3.0** | `tests/gpl_only/` |

`py-slvs` подключается явным вызовом `scripts/gpl_solver.enable()` и только
в процессе проверки. Production-код о нём не знает: `make_solver` собирает
единственную реализацию — PlaneGCS. Запасного варианта нет намеренно:
молчаливое переключение на GPL-решатель связало бы GPL всё приложение,
ничем этого не показав.

## Производный от FreeCAD код

| Модуль | Upstream | Режим | Manifest |
|---|---|---|---|
| `planegcs` (внешний пакет) | FreeCAD `src/Mod/Sketcher/App/planegcs` | зависимость, не изменялась | — |
| `protocad/upstream_ports/freecad/extrusion_core.py` | `PartFeature.cpp`, `FeatureSketchBased.cpp`, `FeatureExtrude.cpp` | перевод C++ → Python | `protocad/upstream_ports/freecad/UPSTREAM.toml` |

Перенесены: `Part::findAllFacesCutBy`, `ProfileBased::getUpToFace`,
`ProfileBased::getThroughAllLength`. Upstream commit
`19235e4b856ecb101fe0620907bbb61a0d4ec5ac` (3 августа 2026). Все исходные
файлы несут `SPDX-License-Identifier: LGPL-2.1-or-later`; copyright и
перечень изменений сохранены в заголовке порта.

**Важно о режиме «перевод».** Такой файл унаследовал устройство алгоритма,
но не отладку upstream: проверки к нему свои, и исправления сверху надо
переносить руками. По решению от 4 августа 2026 новых переводов не
делается — крупные подсистемы FreeCAD используются как headless-движок
(`docs/03_DECISION_RECORD.md`).

Копии изученных исходников — в `docs/upstream/freecad/`. Они не собираются
и не поставляются: лежат для того, чтобы порт можно было сверить с тем, что
было на руках, не выясняя заново, каким был upstream.

## Лицензия самого ProtoCAD

`LGPL-2.1-or-later` — по `docs/00_CONTEXT.md`. Выбрана для того, чтобы
собственный код и производный от FreeCAD лежали рядом без лицензионного шва.
