"""Заряд батареи, время последнего обновления и расход заряда."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.icon import icon_for_battery_level
from homeassistant.helpers.restore_state import ExtraStoredData, RestoreEntity
from homeassistant.util import dt as dt_util

from . import TwoGisConfigEntry
from .const import DOMAIN, PERENOS_RASKHODA
from .coordinator import TwoGisCoordinator
from .entity import TwoGisFriendEntity
from .models import (
    OKNO_SEKUND,
    FriendPosition,
    dobavit_tochku,
    friends_ready_for_entities,
    prirost_raskhoda,
    srednee_po_oknu,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class TwoGisSensorDescription(SensorEntityDescription):
    value_fn: Callable[[FriendPosition], int | datetime | None]
    #: Своя иконка нужна только заряду — чтобы она менялась на «заряжается».
    icon_fn: Callable[[FriendPosition], str] | None = None


SENSORS: tuple[TwoGisSensorDescription, ...] = (
    TwoGisSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda position: position.battery,
        # Штатный помощник Home Assistant: сам подбирает mdi:battery-* по
        # уровню заряда и подставляет «заряжается», когда телефон на зарядке.
        # Ровно те же иконки, что у батарей во всём остальном интерфейсе.
        icon_fn=lambda position: icon_for_battery_level(
            position.battery, bool(position.charging)
        ),
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
        new: list[SensorEntity] = []
        for friend_id in friends_ready_for_entities(coordinator.data):
            if friend_id in known:
                continue
            known.add(friend_id)
            new.extend(
                TwoGisFriendSensor(coordinator, friend_id, description)
                for description in SENSORS
            )
            # Счётчик расхода создаётся первым и передаётся суточному: тот
            # берёт накопленное прямо из объекта, а не через states. Оба
            # живут в одном процессе и обновляются одним тиком координатора,
            # поэтому лишний слой был бы только помехой.
            vsego = TwoGisRaskhodVsego(coordinator, friend_id)
            new.append(vsego)
            new.append(TwoGisRaskhodVSutki(coordinator, friend_id, vsego))
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

    @property
    def icon(self) -> str | None:
        """Пересчитывается на каждое обновление, поэтому и меняется на лету."""
        if self.entity_description.icon_fn is None or self.position is None:
            return None
        return self.entity_description.icon_fn(self.position)


@dataclass
class SostoyanieRaskhoda(ExtraStoredData):
    """Что переживает перезапуск Home Assistant.

    Накопленного мало — нужен и **последний виденный заряд**. Без него после
    перезапуска сравнивать будет не с чем, и первое же обновление либо
    потеряется, либо (если взять за предыдущее ноль) припишет другу расход,
    которого не было.
    """

    vsego: float
    posledniy_zaryad: int | None
    schet_s: str | None
    #: Опорные отметки скользящего окна: ``[[момент, накоплено], ...]``.
    #: Не полная история, а по одной отметке в час — этого хватает для
    #: суточного среднего, а список остаётся коротким.
    tochki: list[list[float]] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "vsego": self.vsego,
            "posledniy_zaryad": self.posledniy_zaryad,
            "schet_s": self.schet_s,
            "tochki": self.tochki or [],
        }


class TwoGisRaskhodVsego(TwoGisFriendEntity, RestoreEntity, SensorEntity):
    """Сколько заряда друг израсходовал за всё время наблюдения.

    Растёт вечно и не сбрасывается — как счётчик пробега. «Сколько тратит в
    день» считает соседняя сущность, деля это на прошедшее время.

    Хранение — штатное восстановление состояния платформы. В Home Assistant
    те же накопители для телефона и часов пришлось держать в ``input_number``
    с автоматизацией сверху: состояние шаблонного сенсора перезапуск не
    переживает. Здесь такой пляски не нужно — это полноценная сущность
    интеграции, и восстановлением занимается сама платформа.
    """

    _attr_translation_key = "battery_drain"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:battery-arrow-down-outline"
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: TwoGisCoordinator, friend_id: str) -> None:
        super().__init__(coordinator, friend_id)
        self._attr_unique_id = f"{friend_id}_battery_drain"
        self._vsego: float = 0.0
        self._posledniy: int | None = None
        self._schet_s: datetime | None = None
        self._tochki: list[tuple[float, float]] = []

    @property
    def available(self) -> bool:
        """Доступен всегда, даже когда друг не делится геопозицией.

        Накопленное никуда не девается от того, что друг ушёл из эфира, и
        прятать его в «недоступно» значило бы терять посчитанное из виду.
        """
        return True

    @property
    def native_value(self) -> float:
        return round(self._vsego, 1)

    @property
    def schet_s(self) -> datetime | None:
        """С какого момента копится — для справки в атрибутах."""
        return self._schet_s

    @property
    def tochki(self) -> list[tuple[float, float]]:
        """Опорные отметки окна — из них соседний сенсор считает среднее."""
        return self._tochki

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            "posledniy_zamer_zariada": self._posledniy,
            "schet_s": self._schet_s.isoformat() if self._schet_s else None,
        }

    @property
    def extra_restore_state_data(self) -> SostoyanieRaskhoda:
        return SostoyanieRaskhoda(
            vsego=self._vsego,
            posledniy_zaryad=self._posledniy,
            schet_s=self._schet_s.isoformat() if self._schet_s else None,
            tochki=[[t, v] for t, v in self._tochki],
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        if (data := await self.async_get_last_extra_data()) is not None:
            sohranyonnoe = data.as_dict()
            self._vsego = float(sohranyonnoe.get("vsego") or 0.0)
            self._posledniy = sohranyonnoe.get("posledniy_zaryad")
            if (s_momenta := sohranyonnoe.get("schet_s")) :
                self._schet_s = dt_util.parse_datetime(s_momenta)
            self._tochki = [
                (float(t), float(v))
                for t, v in (sohranyonnoe.get("tochki") or [])
            ]
        # Друг сменил идентификатор, и переезд оставил здесь то, что успел
        # накопить убранный дубль. Без этого сложения цифра откатилась бы к
        # значению на момент смены, а расход за последние дни просто исчез.
        perenos = self.hass.data.get(DOMAIN, {}).get(PERENOS_RASKHODA, {})
        if (dobavka := perenos.pop(self.friend_id, None)):
            self._vsego += float(dobavka)
            _LOGGER.info(
                "%s: к накопленному добавлено %.1f%% от убранного дубля, стало %.1f%%",
                self.entity_id or self.friend_id, dobavka, self._vsego,
            )

        if self._schet_s is None:
            self._schet_s = dt_util.utcnow()
        # Первый замер берётся сразу при создании: иначе расход начал бы
        # считаться только со второго обновления координатора.
        if self._posledniy is None and self.position is not None:
            self._posledniy = self.position.battery

    @callback
    def _handle_coordinator_update(self) -> None:
        position = self.position
        if position is not None and position.battery is not None:
            ubylo = prirost_raskhoda(self._posledniy, position.battery)
            if ubylo:
                self._vsego += ubylo
            elif (
                self._posledniy is not None
                and self._posledniy > position.battery
            ):
                # Единственный случай, когда падение есть, а в расход оно не
                # идёт. Молчать здесь нельзя: именно так накопитель незаметно
                # занижает цифру, и заметить это потом можно только замером.
                _LOGGER.info(
                    "%s: падение заряда %d -> %d отброшено как разрыв связи, "
                    "в расход не пошло",
                    self.entity_id or self.friend_id,
                    self._posledniy,
                    position.battery,
                )
            self._posledniy = position.battery
        # Отметка кладётся на каждом обновлении, но сама функция добавит
        # новую не чаще раза в час и выбросит всё, что старше окна.
        self._tochki = dobavit_tochku(
            self._tochki, dt_util.utcnow().timestamp(), self._vsego
        )
        super()._handle_coordinator_update()


class TwoGisRaskhodVSutki(TwoGisFriendEntity, SensorEntity):
    """Средний расход заряда в сутки.

    Ответ на вопрос «сколько друг тратит за день»: накопленное, делённое на
    время наблюдения. Чем дольше наблюдаем, тем устойчивее цифра.
    """

    _attr_translation_key = "battery_drain_daily"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:battery-clock-outline"
    _attr_suggested_display_precision = 0

    def __init__(
        self,
        coordinator: TwoGisCoordinator,
        friend_id: str,
        vsego: TwoGisRaskhodVsego,
    ) -> None:
        super().__init__(coordinator, friend_id)
        self._attr_unique_id = f"{friend_id}_battery_drain_daily"
        self._vsego = vsego

    @property
    def available(self) -> bool:
        return bool(self._vsego.tochki)

    @property
    def native_value(self) -> float | None:
        srednee = srednee_po_oknu(
            self._vsego.tochki,
            dt_util.utcnow().timestamp(),
            float(self._vsego.native_value),
        )
        return None if srednee is None else round(srednee, 1)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        tochki = self._vsego.tochki
        shirina = None
        if tochki:
            shirina = round(
                (dt_util.utcnow().timestamp() - tochki[0][0]) / 86400.0, 2
            )
        return {
            "izrashodovano_vsego": self._vsego.native_value,
            "okno_sutok": round(OKNO_SEKUND / 86400.0, 1),
            "shirina_okna_sutok": shirina,
            "otmetok_v_okne": len(tochki),
        }
