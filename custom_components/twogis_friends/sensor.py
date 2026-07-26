"""Заряд батареи и время последнего обновления."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TwoGisConfigEntry
from .coordinator import TwoGisCoordinator
from .entity import TwoGisFriendEntity
from .models import FriendPosition


@dataclass(frozen=True, kw_only=True)
class TwoGisSensorDescription(SensorEntityDescription):
    value_fn: Callable[[FriendPosition], int | datetime | None]


SENSORS: tuple[TwoGisSensorDescription, ...] = (
    TwoGisSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda position: position.battery,
    ),
    TwoGisSensorDescription(
        key="last_seen",
        translation_key="last_seen",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda position: position.last_seen,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TwoGisConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_friends() -> None:
        new: list[TwoGisFriendSensor] = []
        for friend_id in coordinator.data:
            if friend_id in known:
                continue
            known.add(friend_id)
            new.extend(
                TwoGisFriendSensor(coordinator, friend_id, description)
                for description in SENSORS
            )
        if new:
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_add_new_friends))
    _add_new_friends()


class TwoGisFriendSensor(TwoGisFriendEntity, SensorEntity):
    entity_description: TwoGisSensorDescription

    def __init__(
        self,
        coordinator: TwoGisCoordinator,
        friend_id: str,
        description: TwoGisSensorDescription,
    ) -> None:
        super().__init__(coordinator, friend_id)
        self.entity_description = description
        self._attr_unique_id = f"{friend_id}_{description.key}"

    @property
    def native_value(self) -> int | datetime | None:
        if self.position is None:
            return None
        return self.entity_description.value_fn(self.position)
