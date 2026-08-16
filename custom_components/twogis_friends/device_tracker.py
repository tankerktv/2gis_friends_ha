"""Точка друга на карте."""

from __future__ import annotations

from typing import Any

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TwoGisConfigEntry
from .coordinator import TwoGisCoordinator
from .entity import TwoGisFriendEntity
from .models import friends_ready_for_entities


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TwoGisConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new_friends() -> None:
        """Друзья появляются по мере прихода данных, не только при старте."""
        new = [
            TwoGisFriendTracker(coordinator, friend_id)
            for friend_id in friends_ready_for_entities(coordinator.data)
            if friend_id not in known
        ]
        if new:
            known.update(entity.friend_id for entity in new)
            async_add_entities(new)

    entry.async_on_unload(coordinator.async_add_listener(_add_new_friends))
    _add_new_friends()


class TwoGisFriendTracker(TwoGisFriendEntity, TrackerEntity):
    """device_tracker с координатами из 2ГИС."""

    _attr_name = None   # имя берётся от устройства

    def __init__(self, coordinator: TwoGisCoordinator, friend_id: str) -> None:
        super().__init__(coordinator, friend_id)
        self._attr_unique_id = f"{friend_id}_tracker"

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        return self.position.latitude if self.position else None

    @property
    def longitude(self) -> float | None:
        return self.position.longitude if self.position else None

    @property
    def location_accuracy(self) -> float:
        # 2ГИС часто присылает accuracy = null; 0 означает «неизвестна»
        if self.position and self.position.accuracy is not None:
            return self.position.accuracy
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        position = self.position
        if position is None:
            return {}
        attributes: dict[str, Any] = {}
        for key, value in (
            ("movement", position.movement),
            ("place_status", position.place),
            ("speed", position.speed),
            ("course", position.course),
            ("battery_charging", position.charging),
        ):
            if value is not None:
                attributes[key] = value
        if position.last_seen is not None:
            attributes["last_seen"] = position.last_seen.isoformat()
        return attributes
