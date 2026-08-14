"""Признаки друга: устарели ли данные, заряжается ли телефон, дома ли он.

Всё это и раньше приезжало в атрибутах трекера, но атрибутами неудобно
пользоваться: они спрятаны под раскрывающимся списком, не строятся графиком,
не попадают в долговременную статистику, а шаблон, сославшийся на исчезнувший
атрибут, молча вернёт ``None`` и автоматизация не сработает. Отдельные
сущности снимают всё это разом.

Логика намеренно живёт в :mod:`.models` — там её покрывают тесты, не требующие
Home Assistant. Здесь остаётся только обвязка.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TwoGisConfigEntry
from .coordinator import TwoGisCoordinator
from .entity import TwoGisFriendEntity, TwoGisHubEntity
from .models import FriendPosition


@dataclass(frozen=True, kw_only=True)
class TwoGisBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[FriendPosition], bool | None]


BINARY_SENSORS: tuple[TwoGisBinarySensorDescription, ...] = (
    TwoGisBinarySensorDescription(
        key="stale",
        translation_key="stale",
        # PROBLEM, потому что «включено» здесь означает «данным верить нельзя».
        device_class=BinarySensorDeviceClass.PROBLEM,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda position: position.is_stale,
    ),
    TwoGisBinarySensorDescription(
        key="battery_charging",
        translation_key="battery_charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda position: position.charging,
    ),
    TwoGisBinarySensorDescription(
        key="at_home",
        translation_key="at_home",
        # PRESENCE даёт в интерфейсе привычное «Дома / Не дома».
        # Речь про дом самого друга по данным 2ГИС, а не про зоны Home Assistant.
        device_class=BinarySensorDeviceClass.PRESENCE,
        value_fn=lambda position: position.is_at_home,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TwoGisConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    # Одна на всю интеграцию, не на друга. Создаётся сразу и безусловно:
    # если данных ещё нет, именно она и объяснит, почему.
    async_add_entities([TwoGisConnectionSensor(coordinator)])

    @callback
    def _add_new_friends() -> None:
        new: list[TwoGisFriendBinarySensor] = []
        for friend_id in coordinator.data:
            if friend_id in known:
                continue
            known.add(friend_id)
            new.extend(
                TwoGisFriendBinarySensor(coordinator, friend_id, description)
                for description in BINARY_SENSORS
            )
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_add_new_friends))
    _add_new_friends()


class TwoGisConnectionSensor(TwoGisHubEntity, BinarySensorEntity):
    """Есть ли живое соединение с 2ГИС.

    Отвечает на вторую половину вопроса «чья это проблема». «Данные устарели»
    у друга означает, что делиться перестал он. Эта сущность показывает
    обратный случай: у нас оборвалась связь, и данные не приходят ни по кому.

    Различить их иначе нельзя, а лечатся они по-разному: в первом случае делать
    нечего, во втором помогает перезагрузка интеграции.
    """

    _attr_translation_key = "connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: TwoGisCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_connection"

    @property
    def is_on(self) -> bool:
        return self.coordinator.client.connected


class TwoGisFriendBinarySensor(TwoGisFriendEntity, BinarySensorEntity):
    entity_description: TwoGisBinarySensorDescription

    def __init__(
        self,
        coordinator: TwoGisCoordinator,
        friend_id: str,
        description: TwoGisBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, friend_id)
        self.entity_description = description
        self._attr_unique_id = f"{friend_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        """``None`` показывается в Home Assistant как «неизвестно».

        Возвращать здесь ``False`` вместо ``None`` было бы враньём: «2ГИС не
        сказал» и «2ГИС сказал нет» — разные вещи, и автоматизации должны их
        различать.
        """
        if self.position is None:
            return None
        return self.entity_description.value_fn(self.position)
