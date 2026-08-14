"""Тесты извлечения описания релиза из CHANGELOG.md.

Стоит покрывать потому, что ошибка здесь видна только в момент выпуска и
ломает именно его: GitHub Release уйдёт с пустым описанием либо не создастся
вовсе. Проверять это вручную перед каждым тегом никто не станет.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"


def _load():
    """Скрипт лежит в tools/ и пакетом не является — грузим по пути."""
    path = ROOT / "tools" / "changelog_section.py"
    spec = importlib.util.spec_from_file_location("changelog_section", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["changelog_section"] = module
    spec.loader.exec_module(module)
    return module


changelog_section = _load()

SAMPLE = """# Изменения

## [Не выпущено]

## [0.2.0] — 2026-09-01

### Добавлено

- Что-то новое.

## [0.1.0] — 2026-08-01

Первый выпуск.
"""


class TestSection:
    def test_достаёт_нужный_раздел(self):
        assert changelog_section.section(SAMPLE, "0.2.0") == (
            "### Добавлено\n\n- Что-то новое."
        )

    def test_не_цепляет_соседний(self):
        """Раздел заканчивается на следующем заголовке, а не на конце файла."""
        assert "Первый выпуск" not in changelog_section.section(SAMPLE, "0.2.0")

    def test_последний_раздел_доходит_до_конца(self):
        assert changelog_section.section(SAMPLE, "0.1.0") == "Первый выпуск."

    def test_нет_раздела_это_ошибка(self):
        """Молча выпустить релиз с пустым описанием — хуже, чем упасть."""
        with pytest.raises(SystemExit):
            changelog_section.section(SAMPLE, "9.9.9")

    def test_пустой_раздел_это_ошибка(self):
        text = "## [0.3.0] — 2026-10-01\n\n## [0.2.0] — 2026-09-01\n\nтекст\n"
        with pytest.raises(SystemExit):
            changelog_section.section(text, "0.3.0")

    def test_версия_не_путается_с_похожей(self):
        """`0.1.0` не должна найтись в разделе `0.1.10`."""
        text = "## [0.1.10] — 2026-09-01\n\nдесятая\n\n## [0.1.0] — 2026-08-01\n\nпервая\n"
        assert changelog_section.section(text, "0.1.0") == "первая"
        assert changelog_section.section(text, "0.1.10") == "десятая"


class TestРеальныйChangelog:
    """Проверки на настоящем файле — тем и ценны."""

    def test_у_текущей_версии_есть_раздел(self):
        import json
        manifest = json.loads(
            (ROOT / "custom_components" / "twogis_friends" / "manifest.json")
            .read_text(encoding="utf-8")
        )
        body = changelog_section.section(
            CHANGELOG.read_text(encoding="utf-8"), manifest["version"]
        )
        assert body.strip(), "раздел текущей версии пуст"

    def test_описание_кодируется_в_utf8(self):
        """Описания у нас человеческие: стрелки, тире, ёлочки.

        Ровно на стрелке в разделе 0.1.7 выпуск однажды и упал, потому что
        печаталось в кодировке окружения.
        """
        text = CHANGELOG.read_text(encoding="utf-8")
        for version in ("0.1.7", "0.1.6", "0.1.5"):
            body = changelog_section.section(text, version)
            assert body.encode("utf-8").decode("utf-8") == body

    def test_есть_символы_вне_ascii(self):
        """Если их нет, предыдущая проверка ничего не проверяет."""
        body = changelog_section.section(CHANGELOG.read_text(encoding="utf-8"), "0.1.7")
        assert any(ord(char) > 127 for char in body)
