"""Интеграция «2GIS Friends» — друзья с карты 2ГИС в Home Assistant."""

from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import restore_state
from homeassistant.helpers.device_registry import DeviceEntry

from .const import (
    DOMAIN,
    DRAIN_HANDOVER,
    KEY_TOTAL,
    MIGRATION_ATTEMPTS,
    SUFFIX_DRAIN,
)
from .coordinator import TwoGisCoordinator
from .models import can_remove_device, match_migration_pairs, unattempted_pairs

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
    _migrate_friends(hass, entry, coordinator)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    # А это — тот же переезд, но для смены идентификатора на ходу.
    entry.async_on_unload(
        coordinator.async_add_listener(_migration_watchdog(hass, entry, coordinator))
    )
    return True


@callback
def _migrate_friends(
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

    Подбор пар — в :func:`.models.match_migration_pairs`, там же объяснено,
    почему он намеренно осторожен до отказа.
    """
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)
    pairs, devices = _find_pairs(hass, entry, coordinator)

    for old_id, new_id in pairs.items():
        try:
            _migrate_one(hass, dev_reg, ent_reg, devices, old_id, new_id)
        except Exception:  # noqa: BLE001
            # Неудачный переезд оставляет дубль — неприятно, но терпимо.
            # Уронить из-за него всю интеграцию было бы куда хуже.
            _LOGGER.exception("Не удалось перенести друга %s -> %s", old_id, new_id)


@callback
def _find_pairs(
    hass: HomeAssistant, entry: TwoGisConfigEntry, coordinator: TwoGisCoordinator
) -> tuple[dict[str, str], dict[str, DeviceEntry]]:
    """Кого на кого переносить прямо сейчас и какие устройства для этого есть."""
    dev_reg = dr.async_get(hass)

    names: dict[str, str] = {}
    devices: dict[str, DeviceEntry] = {}
    for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id):
        for domain, value in device.identifiers:
            # Служебное устройство интеграции опознаётся по идентификатору
            # записи и в переезде не участвует.
            if domain != DOMAIN or value == entry.entry_id:
                continue
            devices[value] = device
            names[value] = device.name_by_user or device.name or ""

    live = {
        friend_id: (position.name or "")
        for friend_id, position in coordinator.data.items()
    }
    return match_migration_pairs(names, live), devices


@callback
def _migration_watchdog(
    hass: HomeAssistant, entry: TwoGisConfigEntry, coordinator: TwoGisCoordinator
) -> Callable[[], None]:
    """Ловит смену идентификатора на ходу, не дожидаясь перезапуска.

    Переезд при настройке чинит реестр только на старте, поэтому всё время от
    смены идентификатора до ближайшего перезапуска дубль живёт своей жизнью.
    Стоит это дорого: у трекера ``force_update`` включён (координатор push-овый,
    опроса нет), поэтому замершая сущность пишет строку в историю на **каждое**
    обновление — за сутки набегает больше тысячи одинаковых точек.

    **Сторож не переселяет сам, а просит перезагрузить запись.** Так и задумано.
    К этому моменту сущности живут в памяти со старым идентификатором внутри;
    поправить реестр мало — пришлось бы ещё и переписывать сами объекты на
    ходу. Перезагрузка отдаёт работу тому же коду, что и при старте, а он уже
    проверен на живых переездах.
    """

    @callback
    def check() -> None:
        pairs, _ = _find_pairs(hass, entry, coordinator)
        attempted: dict[str, str] = (
            hass.data.setdefault(DOMAIN, {})
            .setdefault(MIGRATION_ATTEMPTS, {})
            .setdefault(entry.entry_id, {})
        )
        fresh = unattempted_pairs(pairs, attempted)
        if not fresh:
            return

        # Помечаем ДО перезагрузки: если переезд не удастся, пара найдётся
        # снова, и без этой отметки перезагрузки пошли бы по кругу.
        attempted.update(fresh)
        _LOGGER.info(
            "Друг сменил идентификатор на ходу (%s). Перезагружаю запись, "
            "чтобы перенести устройство и сущности вместе с историей",
            ", ".join(f"{old_id} -> {new_id}" for old_id, new_id in fresh.items()),
        )
        # Задача НЕ привязана к записи: перезагрузка эту запись выгружает,
        # и привязанная задача была бы отменена на середине.
        hass.async_create_task(
            hass.config_entries.async_reload(entry.entry_id),
            f"{DOMAIN}_migration",
            eager_start=False,
        )

    return check


@callback
def _migrate_one(
    hass: HomeAssistant,
    dev_reg: dr.DeviceRegistry,
    ent_reg: er.EntityRegistry,
    devices: dict[str, DeviceEntry],
    old_id: str,
    new_id: str,
) -> None:
    """Переносит одно устройство со старого идентификатора на новый."""
    old_device = devices[old_id]
    duplicate = devices.get(new_id)

    # 1. Дубль, если его уже успели завести, убираем. Сначала забираем
    #    накопленный расход: вместе с сущностью он пропал бы безвозвратно,
    #    а это единственное, чего не восстановить из истории.
    if duplicate is not None:
        handover = hass.data.setdefault(DOMAIN, {}).setdefault(DRAIN_HANDOVER, {})
        for record in er.async_entries_for_device(
            ent_reg, duplicate.id, include_disabled_entities=True
        ):
            if record.unique_id.endswith(SUFFIX_DRAIN):
                accumulated = _stored_drain(hass, record.entity_id)
                if accumulated:
                    handover[new_id] = handover.get(new_id, 0.0) + accumulated
            ent_reg.async_remove(record.entity_id)
        dev_reg.async_remove_device(duplicate.id)
        _LOGGER.info("Дубль друга %s убран, его сущности удалены", new_id)

    # 2. Сущности переводим на новый идентификатор. Порядок важен: пока дубль
    #    не убран, Home Assistant не даст занять его unique_id и бросит ошибку.
    for record in er.async_entries_for_device(
        ent_reg, old_device.id, include_disabled_entities=True
    ):
        if not record.unique_id.startswith(old_id):
            continue
        ent_reg.async_update_entity(
            record.entity_id,
            new_unique_id=new_id + record.unique_id[len(old_id):],
        )

    # 3. И само устройство.
    dev_reg.async_update_device(old_device.id, new_identifiers={(DOMAIN, new_id)})
    _LOGGER.info(
        "Друг сменил идентификатор: %s -> %s. Устройство и сущности перенесены, "
        "история сохранена", old_id, new_id,
    )


def _stored_drain(hass: HomeAssistant, entity_id: str) -> float:
    """Сколько накопил счётчик расхода удаляемого дубля.

    Берётся из штатного хранилища восстановления, потому что сама сущность в
    этот момент ещё не создана: переезд идёт до подъёма платформ.
    """
    try:
        stored = restore_state.async_get(hass).last_states.get(entity_id)
        if stored is None or stored.extra_data is None:
            return 0.0
        return float(stored.extra_data.as_dict().get(KEY_TOTAL) or 0.0)
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
