#!/usr/bin/env python3
"""Проверка, что в отслеживаемых файлах нет данных друзей.

Зачем. Репозиторий зеркалируется в публичный GitHub, а данные, с которыми
он работает, — чужие: имена, идентификаторы 2ГИС и координаты друзей.
Полтора месяца в публичном корне пролежал сырой лог сокета с координатами —
его никто не заметил, потому что смотреть было некому. Теперь смотрит CI,
и стоит эта задача в стадии validate — **до** стадии зеркала. Утечка не
может доехать до GitHub физически: пайплайн до неё не дойдёт.

Две группы правил.

**Структурные** — работают всегда, без секретов:
  * в корне репозитория нет файлов-проб: .txt (кроме requirements*), .har,
    .jsonl, .log;
  * .har нет нигде;
  * вне tests/ и docs/ нет строк с координатами полной точности — так
    выглядят сырые кадры zond.

**По закрытому списку** — если он есть. Список настоящих значений лежит
в data/private_values.txt (под .gitignore) либо приходит в CI файловой
переменной PRIVATE_VALUES_FILE. Ни одно значение из него не должно
встречаться ни в одном отслеживаемом файле, включая тесты. Сами значения
в вывод не попадают — печатается файл, строка и первые символы.

Проверяются только отслеживаемые файлы (`git ls-files`): то, что под
.gitignore, наружу не уезжает.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip())

# расширения проб, которым в корне не место
ROOT_FORBIDDEN_EXT = {".txt", ".har", ".jsonl", ".log"}
ROOT_ALLOWED = re.compile(r"^requirements[\w-]*\.txt$")

# Широта в JSON с четырьмя и больше знаками после точки — настоящая точность,
# так выглядят сырые кадры zond. Пример здесь не приводится намеренно: эта
# проверка читает и саму себя, а первый вариант с примером-координатой
# в комментарии уронил пайплайн на собственном файле.
RAW_COORD = re.compile(r'"lat"\s*:\s*-?\d+\.\d{4,}')
COORD_EXEMPT_DIRS = ("tests/", "docs/")

TEXT_EXT = {".py", ".md", ".txt", ".yml", ".yaml", ".json", ".sh", ".toml",
            ".cfg", ".ini", ".env", ".example", ".html", ".css", ".js"}


def tracked_files() -> list[str]:
    out = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def is_text(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXT or path.name in (".gitignore", ".gitattributes"):
        return True
    try:
        with open(path, "rb") as f:
            return b"\0" not in f.read(4096)
    except OSError:
        return False


def load_private_values() -> tuple[list[str], str]:
    """Закрытый список и откуда он взят — для сообщения, без содержимого."""
    for candidate, source in (
        (os.environ.get("PRIVATE_VALUES_FILE"), "переменная PRIVATE_VALUES_FILE"),
        (ROOT / "data" / "private_values.txt", "data/private_values.txt"),
    ):
        if candidate and Path(candidate).is_file():
            lines = Path(candidate).read_text(encoding="utf-8").splitlines()
            values = [v.strip() for v in lines if v.strip() and not v.startswith("#")]
            return values, source
    return [], ""


def masked(value: str) -> str:
    return value[:3] + "…" if len(value) > 3 else "…"


def main() -> int:
    problems: list[str] = []
    files = tracked_files()

    # --- структурные правила ------------------------------------------------
    for rel in files:
        path = ROOT / rel
        name = os.path.basename(rel)
        ext = os.path.splitext(name)[1].lower()
        at_root = "/" not in rel

        if ext == ".har":
            problems.append(f"{rel}: HAR-дамп в репозитории — в нём токен и координаты")
        elif at_root and ext in ROOT_FORBIDDEN_EXT and not ROOT_ALLOWED.match(name):
            problems.append(f"{rel}: файл-проба в корне, ему место в data/ (под .gitignore)")

        if ext == ".har" or not is_text(path):
            continue
        if rel.startswith(COORD_EXEMPT_DIRS):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if RAW_COORD.search(line):
                problems.append(f"{rel}:{lineno}: координаты полной точности — похоже на сырой кадр zond")
                break

    # --- закрытый список ----------------------------------------------------
    values, source = load_private_values()
    if values:
        for rel in files:
            path = ROOT / rel
            if not is_text(path):
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for lineno, line in enumerate(text.splitlines(), 1):
                for value in values:
                    if value in line:
                        problems.append(f"{rel}:{lineno}: значение из закрытого списка ({masked(value)})")
        print(f"закрытый список: {len(values)} значений из {source}")
    else:
        print("закрытый список не найден — проверены только структурные правила")

    for p in problems:
        print("  ОШИБКА " + p)
    print("-" * 60)
    if problems:
        print(f"находок: {len(problems)} — публиковать нельзя")
        return 1
    print(f"проверено файлов: {len(files)}, данных друзей не найдено")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
