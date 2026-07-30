"""Координатор: держит соединение с zond и хранит состояния друзей."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_TOKEN
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_IDLE_RECONNECT_MIN,
    CONF_VIEWPORT_RADIUS,
    DEFAULT_IDLE_RECONNECT_MIN,
    DEFAULT_VIEWPORT_RADIUS,
    DOMAIN,
    FIRST_DATA_TIMEOUT,
)
from .models import FriendPosition
from .zond import Viewport, ZondAuthError, ZondClient

_LOGGER = logging.getLogger(__name__)


class TwoGisCoordinator(DataUpdateCoordinator[dict[str, FriendPosition]]):
    """Push-координатор: данные приходят по сокету, а не по опросу.

    ``update_interval=None`` — периодический опрос отключён, обновления
    приходят через ``async_set_updated_data`` из колбэка WS-клиента.
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN, update_interval=None)
        self.config_entry = entry
        self.data = {}
        self._signatures: dict[str, tuple] = {}
        self._first_data = asyncio.Event()
        self._task: asyncio.Task | None = None

        radius = entry.options.get(CONF_VIEWPORT_RADIUS, DEFAULT_VIEWPORT_RADIUS)
        idle_min = entry.options.get(CONF_IDLE_RECONNECT_MIN, DEFAULT_IDLE_RECONNECT_MIN)
        viewport = Viewport(hass.config.latitude, hass.config.longitude, radius)
        self.client = ZondClient(
            async_get_clientsession(hass),
            entry.data[CONF_TOKEN],
            viewport,
            self._handle_positions,
            idle_timeout=float(idle_min) * 60,
        )

    @callback
    def _handle_positions(self, positions: Iterable[FriendPosition]) -> None:
        """Складывает новые позиции, отсеивая повторы.

        zond присылает один и тот же friendState по несколько раз подряд —
        без проверки сигнатуры история в HA засорялась бы дублями.
        """
        updated = dict(self.data)
        changed = False
        for position in positions:
            if self._signatures.get(position.friend_id) == position.signature:
                continue
            self._signatures[position.friend_id] = position.signature
            updated[position.friend_id] = position
            changed = True

        if not self._first_data.is_set():
            self._first_data.set()
        if changed:
            self.async_set_updated_data(updated)

    async def async_start(self) -> None:
        """Запускает клиент и ждёт первых данных, чтобы сущности появились сразу."""
        self._task = self.config_entry.async_create_background_task(
            self.hass, self._runner(), name=f"{DOMAIN}_ws"
        )
        try:
            async with asyncio.timeout(FIRST_DATA_TIMEOUT):
                await self._first_data.wait()
        except TimeoutError as err:
            self._task.cancel()
            raise ConfigEntryNotReady(
                "2ГИС не прислал состояния друзей за отведённое время"
            ) from err

    async def _runner(self) -> None:
        """Держит клиент живым до выгрузки записи.

        Второй рубеж защиты: даже если run() почему-то вернулся или упал,
        задача не должна завершаться молча — иначе интеграция замрёт с уже
        созданными сущностями, но без обновлений, и это никак не проявится
        в интерфейсе.
        """
        while True:
            try:
                await self.client.run()
                _LOGGER.warning("Клиент zond неожиданно завершился, перезапускаю")
            except ZondAuthError as err:
                _LOGGER.error("Токен 2ГИС отвергнут: %s", err)
                self.config_entry.async_start_reauth(self.hass)
                return
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                _LOGGER.exception("WS-клиент 2ГИС упал, перезапускаю через 60 с")
            await asyncio.sleep(60)

    async def async_shutdown(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        await super().async_shutdown()


async def async_validate_entry(hass: HomeAssistant, token: str) -> dict:
    """Проверка токена для config flow. Возвращает профиль пользователя."""
    from .zond import async_validate_token

    try:
        return await async_validate_token(async_get_clientsession(hass), token)
    except ZondAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
