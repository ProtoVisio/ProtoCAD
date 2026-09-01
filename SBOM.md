# SBOM ProtoCAD

Машиночитаемый `SBOM.spdx.json` соберётся при первой сборке поставки; пока
состав фиксируется здесь и в `THIRD_PARTY.md`, чтобы он не расходился с
действительностью.

Проверить состав окружения:

```bash
python scripts/check_licenses.py
```

Проверка отказывает, если в production-окружении появился GPL или AGPL, —
по `docs/05_POLICY.md`, §9.
