"""Общая база для сущностей друга."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import TwoGisCoordinator
from .models import FriendPosition


class TwoGisFriendEntity(CoordinatorEntity[TwoGisCoordinator]):
    """Каждый друг — отдельное устройство с несколькими сущностями."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TwoGisCoordinator, friend_id: str) -> None:
        super().__init__(coordinator)
        self.friend_id = friend_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, friend_id)},
            name=self.position.name if self.position else friend_id,
            manufacturer="2GIS",
            model="Друзья на карте",
        )

    @property
    def position(self) -> FriendPosition | None:
        return self.coordinator.data.get(self.friend_id)

    @property
    def available(self) -> bool:
        return super().available and self.position is not None
