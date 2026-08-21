#!/usr/bin/env python3
"""Снимок всех строковых литералов боевого кода.

Зачем. Переименование идентификаторов безопасно: Python их нигде не отражает.
Опасны строковые литералы — часть из них уходит в ``.storage``, в ``unique_id``
и в атрибуты сущностей, и молчаливая правка такого литерала стоит истории или
накопленных значений.

Снимок до и после правки доказывает механически, что изменилось ровно то, что
собирались менять. Docstring исключены намеренно: их переводить можно.

    python tools/literal_snapshot.py before.txt
    ... правки ...
    python tools/literal_snapshot.py after.txt
    diff before.txt after.txt

Путь к файлу обязателен: перенаправление ``>`` на Windows пишет в кодировке
консоли и портит русские строки, а сравнивать надо побайтово.
"""

from __future__ import annotations

import ast
import io
import pathlib
import sys

KORNI = ("custom_components", "bridge")


def literaly(put: pathlib.Path) -> list[str]:
    """Все строковые константы файла, кроме docstring."""
    derevo = ast.parse(io.open(put, encoding="utf-8").read())

    docstrings = set()
    for uzel in ast.walk(derevo):
        if isinstance(
            uzel, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            tekst = ast.get_docstring(uzel, clean=False)
            if tekst is not None:
                docstrings.add(tekst)

    naydeno = []
    for uzel in ast.walk(derevo):
        if isinstance(uzel, ast.Constant) and isinstance(uzel.value, str):
            if uzel.value not in docstrings:
                naydeno.append(uzel.value)
    return naydeno


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2

    stroki = []
    for koren in KORNI:
        for put in sorted(pathlib.Path(koren).rglob("*.py")):
            if "__pycache__" in put.parts:
                continue
            # Сортируем: порядок литералов в файле — не то, что мы охраняем.
            # Важно, что множество литералов осталось прежним.
            for znachenie in sorted(literaly(put)):
                stroki.append("%s\t%r" % (put.as_posix(), znachenie))

    io.open(sys.argv[1], "w", encoding="utf-8", newline="\n").write(
        "\n".join(stroki) + "\n"
    )
    print("литералов: %d -> %s" % (len(stroki), sys.argv[1]), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
