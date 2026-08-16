"""Общая база для сущностей: друга и самой интеграции."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
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
        # Пустое имя сюда передавать нельзя ни при каких обстоятельствах.
        # Home Assistant строит идентификатор сущности от имени устройства
        # один раз, при создании, и навсегда. Без имени он берёт название
        # записи интеграции — так у друга появляется трекер, названный по
        # другому человеку. Идентификатор друга некрасив, но однозначен.
        # Основную защиту даёт friends_ready_for_entities в models.py:
        # сущности не создаются, пока имя не пришло. Это второй рубеж.
        position = self.position
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, friend_id)},
            name=position.name if position is not None and position.name else friend_id,
            manufacturer="2GIS",
            model="Друзья на карте",
        )

    @property
    def position(self) -> FriendPosition | None:
        return self.coordinator.data.get(self.friend_id)

    @property
    def available(self) -> bool:
        return super().available and self.position is not None


class TwoGisHubEntity(CoordinatorEntity[TwoGisCoordinator]):
    """Сущности самой интеграции, не привязанные к конкретному другу.

    Отдельное служебное устройство, чтобы не приписывать состояние соединения
    кому-то из друзей: оно общее для всех.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: TwoGisCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="2GIS Friends",
            manufacturer="2GIS",
            model="Друзья на карте",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        """Всегда доступна — и это принципиально.

        Сущность, сообщающая о потере связи, обязана оставаться доступной
        именно тогда, когда связь потеряна. Иначе вместо внятного
        «не подключено» пользователь увидит «недоступно» и не поймёт, то ли
        связи нет, то ли сломалась сама сущность.
        """
        return True
