#!/usr/bin/env python3
"""Достаёт раздел версии из CHANGELOG.md — для описания GitHub Release.

    python tools/changelog_section.py 0.1.2

Печатает содержимое раздела без заголовка. Если раздела нет — падает, чтобы
релиз не ушёл с пустым описанием и это было заметно сразу.
"""

from __future__ import annotations

import pathlib
import re
import sys


def section(text: str, version: str) -> str:
    pattern = re.compile(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |\Z)",
        re.MULTILINE | re.DOTALL,
    )
    match = pattern.search(text)
    if not match:
        raise SystemExit(
            f"В CHANGELOG.md нет раздела для версии {version}.\n"
            f"Добавь '## [{version}] — ГГГГ-ММ-ДД' перед выпуском."
        )
    body = match.group(1).strip()
    if not body:
        raise SystemExit(f"Раздел {version} в CHANGELOG.md пуст.")
    return body


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("использование: changelog_section.py <версия>")
    path = pathlib.Path(__file__).resolve().parent.parent / "CHANGELOG.md"
    print(section(path.read_text(encoding="utf-8"), sys.argv[1].lstrip("v")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
