#!/usr/bin/env python3
"""Проверка .gitlab-ci.yml до пуша.

Ловит то, на чём мы уже обожглись: двоеточие с пробелом внутри
незакавыченного шага. YAML разбирает такую строку как словарь, файл
остаётся формально валидным, а GitLab отказывается запускать пайплайн со
словами «script config should be a string or a nested array of strings».

Проверять «файл разбирается» недостаточно — он разбирается. Проверять надо
то свойство, которое ломается: что каждый шаг остался строкой.
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

KLYUCHI_KOMAND = ("script", "before_script", "after_script")


def proverit(put: Path) -> list[str]:
    """Возвращает список найденных бед. Пустой список — всё в порядке."""
    bedy: list[str] = []
    try:
        dokument = yaml.safe_load(put.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        return ["YAML не разбирается: %s" % err]

    if not isinstance(dokument, dict):
        return ["на верхнем уровне не отображение, а %s" % type(dokument).__name__]

    for imya, zadacha in dokument.items():
        if not isinstance(zadacha, dict):
            continue
        for klyuch in KLYUCHI_KOMAND:
            shagi = zadacha.get(klyuch)
            if shagi is None:
                continue
            if not isinstance(shagi, list):
                bedy.append("%s.%s — не список, а %s" % (imya, klyuch, type(shagi).__name__))
                continue
            for nomer, shag in enumerate(shagi):
                if isinstance(shag, str):
                    continue
                bedy.append(
                    "%s.%s[%d] разобрался как %s, а должен быть строкой.\n"
                    "      получилось: %r\n"
                    "      скорее всего внутри «: » — закавычь шаг или сделай его блочным (- |)"
                    % (imya, klyuch, nomer, type(shag).__name__, shag)
                )
    return bedy


def main() -> int:
    put = Path(sys.argv[1] if len(sys.argv) > 1 else ".gitlab-ci.yml")
    if not put.is_file():
        print("не найден файл: %s" % put)
        return 2

    bedy = proverit(put)
    for beda in bedy:
        print("  ОШИБКА %s" % beda)
    if bedy:
        print("-" * 60)
        print("бед: %d — пайплайн не запустится" % len(bedy))
        return 1

    print("%s: шаги во всех задачах — строки" % put)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
