"""Интеграция «2GIS Friends» — друзья с карты 2ГИС в Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import TwoGisCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
]

TwoGisConfigEntry = ConfigEntry[TwoGisCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: TwoGisConfigEntry) -> bool:
    coordinator = TwoGisCoordinator(hass, entry)
    await coordinator.async_start()

    entry.runtime_data = coordinator
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TwoGisConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: TwoGisCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unloaded


async def async_reload_entry(hass: HomeAssistant, entry: TwoGisConfigEntry) -> None:
    """Опции поменялись (например, радиус области) — пересоздаём соединение."""
    await hass.config_entries.async_reload(entry.entry_id)
