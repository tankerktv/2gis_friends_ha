"""Интеграция «2GIS Friends» — друзья с карты 2ГИС в Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntry

from .const import DOMAIN
from .coordinator import TwoGisCoordinator
from .models import can_remove_device

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


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: TwoGisConfigEntry, device: DeviceEntry
) -> bool:
    """Разрешает убрать устройство друга, которого больше нет в списке 2ГИС.

    **Без этой функции Home Assistant вообще не показывает кнопку удаления.**
    Единственным способом избавиться от лишнего устройства остаётся удаление
    интеграции целиком — вместе с историей по всем остальным.

    Понадобилось вот зачем. Идентификатор друга в 2ГИС не вечен: он меняется,
    когда человек переустанавливает приложение или заводит другой аккаунт.
    Для интеграции это новый друг — заводится новое устройство, а прежнее
    остаётся навсегда. Внешне выглядит как задвоившийся человек, у которого
    одна карточка живая, а вторая вечно «недоступна».

    Предотвратить смену идентификатора мы не можем — она происходит на стороне
    2ГИС. Зато уборка теперь делается одной кнопкой вместо переустановки.
    """
    coordinator = entry.runtime_data
    device_ids = {value for domain, value in device.identifiers if domain == DOMAIN}
    return can_remove_device(device_ids, entry.entry_id, set(coordinator.data))
