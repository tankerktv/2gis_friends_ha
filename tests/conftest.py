"""Общая подготовка для тестов.

Задача этого файла — дать тестам импортировать модули интеграции, не таща
за собой Home Assistant.

Проблема в том, что ``custom_components/twogis_friends/__init__.py`` в первых
же строках делает ``from homeassistant.config_entries import ConfigEntry``.
Обычный ``import custom_components.twogis_friends.models`` сначала выполнит
этот ``__init__.py`` и упадёт: в тестовом окружении Home Assistant не стоит
и ставить его ради разбора JSON-фреймов незачем — это сотни мегабайт
зависимостей и привязка к версии Python, которую требует HA.

Поэтому пакет собирается вручную: создаётся модуль-пустышка с правильным
``__path__``, и дальше стандартный механизм импорта сам находит внутри
``models.py``, ``const.py`` и ``zond.py``. Относительные импорты между ними
(``from .const import ...``) при этом работают как обычно, потому что
родительский пакет в ``sys.modules`` есть.

Тестируются только модули, не зависящие от Home Assistant: разбор протокола
и построение рамки. Координатор, config flow и сущности завязаны на HA и
требуют ``pytest-homeassistant-custom-component`` — это отдельный разговор.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

PACKAGE = "twogis_friends"
SOURCE = Path(__file__).resolve().parents[1] / "custom_components" / PACKAGE


def _install_package() -> None:
    """Регистрирует пакет в sys.modules, не выполняя его __init__.py."""
    if PACKAGE in sys.modules:
        return
    if not SOURCE.is_dir():
        raise RuntimeError(f"не найден каталог интеграции: {SOURCE}")

    spec = importlib.util.spec_from_loader(PACKAGE, loader=None, is_package=True)
    module = importlib.util.module_from_spec(spec)
    module.__path__ = [str(SOURCE)]
    sys.modules[PACKAGE] = module


_install_package()
