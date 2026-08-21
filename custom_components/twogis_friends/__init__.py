"""Интеграция «2GIS Friends» — друзья с карты 2ГИС в Home Assistant."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import restore_state
from homeassistant.helpers.device_registry import DeviceEntry

from .const import DOMAIN, PERENOS_RASKHODA, SUFFIX_RASKHOD
from .coordinator import TwoGisCoordinator
from .models import can_remove_device, podobrat_pary_pereezda

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

    # Обязательно ДО создания сущностей: переезд правит реестр, и платформы
    # должны увидеть уже исправленную картину. Иначе Home Assistant успеет
    # завести вторые сущности, и разбирать придётся их, а не идентификаторы.
    _pereezd_druzey(hass, entry, coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


@callback
def _pereezd_druzey(
    hass: HomeAssistant, entry: TwoGisConfigEntry, coordinator: TwoGisCoordinator
) -> None:
    """Переводит устройство друга на новый идентификатор, если тот сменился.

    Идентификатор друга в 2ГИС не вечен: он меняется, когда человек
    переустанавливает приложение или заходит под другим аккаунтом. Без этого
    переезда интеграция считает его новым человеком — заводит второе
    устройство, и вся история разрывается надвое.

    Здесь устройство и сущности **остаются те же**, им лишь меняется
    идентификатор внутри. Поэтому не меняются и идентификаторы сущностей:
    карточки на дашбордах, автоматизации и история продолжают работать так,
    будто ничего не случилось.

    Подбор пар — в :func:`.models.podobrat_pary_pereezda`, там же объяснено,
    почему он намеренно осторожен до отказа.
    """
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    imena: dict[str, str] = {}
    ustroystva: dict[str, DeviceEntry] = {}
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        for domain, value in device.identifiers:
            # Служебное устройство интеграции опознаётся по идентификатору
            # записи и в переезде не участвует.
            if domain != DOMAIN or value == entry.entry_id:
                continue
            ustroystva[value] = device
            imena[value] = device.name_by_user or device.name or ""

    zhivye = {
        friend_id: (position.name or "")
        for friend_id, position in coordinator.data.items()
    }

    for stary, novy in podobrat_pary_pereezda(imena, zhivye).items():
        try:
            _pereselit(hass, dev_reg, ent_reg, ustroystva, stary, novy)
        except Exception:  # noqa: BLE001
            # Неудачный переезд оставляет дубль — неприятно, но терпимо.
            # Уронить из-за него всю интеграцию было бы куда хуже.
            _LOGGER.exception("Не удалось перенести друга %s -> %s", stary, novy)


@callback
def _pereselit(
    hass: HomeAssistant,
    dev_reg: dr.DeviceRegistry,
    ent_reg: er.EntityRegistry,
    ustroystva: dict[str, DeviceEntry],
    stary: str,
    novy: str,
) -> None:
    """Переносит одно устройство со старого идентификатора на новый."""
    staroe = ustroystva[stary]
    dvoynik = ustroystva.get(novy)

    # 1. Дубль, если его уже успели завести, убираем. Сначала забираем
    #    накопленный расход: вместе с сущностью он пропал бы безвозвратно,
    #    а это единственное, чего не восстановить из истории.
    if dvoynik is not None:
        perenos = hass.data.setdefault(DOMAIN, {}).setdefault(PERENOS_RASKHODA, {})
        for zapis in er.async_entries_for_device(
            ent_reg, dvoynik.id, include_disabled_entities=True
        ):
            if zapis.unique_id.endswith(SUFFIX_RASKHOD):
                nakopleno = _nakoplennyy_raskhod(hass, zapis.entity_id)
                if nakopleno:
                    perenos[novy] = perenos.get(novy, 0.0) + nakopleno
            ent_reg.async_remove(zapis.entity_id)
        dev_reg.async_remove_device(dvoynik.id)
        _LOGGER.info("Дубль друга %s убран, его сущности удалены", novy)

    # 2. Сущности переводим на новый идентификатор. Порядок важен: пока дубль
    #    не убран, Home Assistant не даст занять его unique_id и бросит ошибку.
    for zapis in er.async_entries_for_device(
        ent_reg, staroe.id, include_disabled_entities=True
    ):
        if not zapis.unique_id.startswith(stary):
            continue
        ent_reg.async_update_entity(
            zapis.entity_id,
            new_unique_id=novy + zapis.unique_id[len(stary):],
        )

    # 3. И само устройство.
    dev_reg.async_update_device(staroe.id, new_identifiers={(DOMAIN, novy)})
    _LOGGER.info(
        "Друг сменил идентификатор: %s -> %s. Устройство и сущности перенесены, "
        "история сохранена", stary, novy,
    )


def _nakoplennyy_raskhod(hass: HomeAssistant, entity_id: str) -> float:
    """Сколько накопил счётчик расхода удаляемого дубля.

    Берётся из штатного хранилища восстановления, потому что сама сущность в
    этот момент ещё не создана: переезд идёт до подъёма платформ.
    """
    try:
        sohranyonnoe = restore_state.async_get(hass).last_states.get(entity_id)
        if sohranyonnoe is None or sohranyonnoe.extra_data is None:
            return 0.0
        return float(sohranyonnoe.extra_data.as_dict().get("vsego") or 0.0)
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Не удалось прочитать накопленный расход у %s", entity_id)
        return 0.0


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
