"""WebSocket-клиент к zond.api.2gis.ru на aiohttp.

Специально на aiohttp, а не на websockets: он уже есть в Home Assistant,
поэтому у интеграции пустой `requirements` и ничего не доустанавливается.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Callable, Iterable
from typing import Any

import aiohttp

from .const import (
    APP_VERSION,
    AUTH_CLOSE_CODES,
    CHANNELS,
    IDLE_TIMEOUT,
    KEEPALIVE_INTERVAL,
    ORIGIN,
    RECONNECT_MAX,
    RECONNECT_MIN,
    USER_AGENT,
    VIEWPORT_ZOOM,
    WS_HEARTBEAT,
    WS_URL,
)
from .models import FriendPosition, ZondParser

_LOGGER = logging.getLogger(__name__)


class ZondAuthError(Exception):
    """Токен отвергнут — нужен новый (реавторизация в HA)."""


class Viewport:
    """Рамка, по которой сервер фильтрует апдейты."""

    def __init__(self, latitude: float, longitude: float, radius_deg: float) -> None:
        self.top_lat = min(90.0, latitude + radius_deg)
        self.bottom_lat = max(-90.0, latitude - radius_deg)
        self.left_lon = max(-180.0, longitude - radius_deg)
        self.right_lon = min(180.0, longitude + radius_deg)

    def frame(self) -> dict[str, Any]:
        return {
            "type": "viewportChanged",
            "payload": {
                "viewport": {
                    "topLeft": {"lon": self.left_lon, "lat": self.top_lat},
                    "bottomRight": {"lon": self.right_lon, "lat": self.bottom_lat},
                },
                "zoom": VIEWPORT_ZOOM,
            },
        }


class ZondClient:
    """Держит соединение и отдаёт распарсенные позиции через колбэк.

    ``run()`` работает вечно: переподключается с экспоненциальной задержкой,
    а при отказе авторизации выходит с ZondAuthError, чтобы HA запустил reauth.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        token: str,
        viewport: Viewport,
        on_positions: Callable[[Iterable[FriendPosition]], None],
        idle_timeout: float = IDLE_TIMEOUT,
        on_connection_change: Callable[[bool], None] | None = None,
    ) -> None:
        self._session = session
        self._token = token
        self._viewport = viewport
        self._on_positions = on_positions
        self._idle_timeout = idle_timeout
        self._on_connection_change = on_connection_change
        self._parser = ZondParser()
        self._last_rx = 0.0
        self.connected = False

    def _set_connected(self, value: bool) -> None:
        """Меняет флаг и сообщает наружу — но только о самой смене.

        Без уведомления сущность состояния связи узнавала бы о разрыве лишь
        при следующем входящем фрейме, то есть никогда: связи-то нет.

        Ошибку в колбэке гасим намеренно. Он ведёт в Home Assistant, и если
        оттуда прилетит исключение, оно поднимется в цикл переподключения и
        уронит фоновую задачу — интеграция замрёт целиком из-за декоративной
        сущности.
        """
        if self.connected == value:
            return
        self.connected = value
        if self._on_connection_change is None:
            return
        try:
            self._on_connection_change(value)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Колбэк состояния связи упал, продолжаю работу")

    @property
    def _url(self) -> str:
        return WS_URL

    def _params(self) -> dict[str, str]:
        return {"appVersion": APP_VERSION, "channels": CHANNELS, "token": self._token}

    async def run(self) -> None:
        attempt = 0
        while True:
            try:
                await self._connect_once()
                attempt = 0
                _LOGGER.debug("Сессия zond завершилась штатно")
            except asyncio.CancelledError:
                raise
            except ZondAuthError:
                raise
            except aiohttp.WSServerHandshakeError as err:
                if err.status in (401, 403):
                    raise ZondAuthError(f"HTTP {err.status} при подключении") from err
                _LOGGER.warning("Handshake отклонён: HTTP %s", err.status)
            except (aiohttp.ClientError, OSError, asyncio.TimeoutError) as err:
                _LOGGER.warning("Обрыв связи с zond: %s: %s", type(err).__name__, err)
            except Exception:  # noqa: BLE001
                # Ловим всё остальное осознанно. Если сюда прилетит незнакомое
                # исключение и мы его выпустим, цикл переподключения прекратится,
                # фоновая задача завершится, и интеграция замрёт до перезапуска
                # Home Assistant: сущности останутся, а обновлений не будет.
                _LOGGER.exception("Непредвиденная ошибка в сессии zond, переподключаюсь")
            finally:
                self._set_connected(False)

            attempt += 1
            delay = min(RECONNECT_MIN * 2 ** (attempt - 1), RECONNECT_MAX)
            delay *= 0.5 + random.random()   # джиттер, чтобы не долбить ровным ритмом
            _LOGGER.info("Переподключение к zond #%d через %.0f с", attempt, delay)
            await asyncio.sleep(delay)

    async def _connect_once(self) -> None:
        headers = {"Origin": ORIGIN, "User-Agent": USER_AGENT}
        async with self._session.ws_connect(
            self._url,
            params=self._params(),
            headers=headers,
            heartbeat=WS_HEARTBEAT,
            max_msg_size=16 * 1024 * 1024,
        ) as ws:
            _LOGGER.info("Соединение с zond установлено")
            self._set_connected(True)
            self._last_rx = time.monotonic()

            # Сервер молчит, пока не получит viewportChanged — без него не будет
            # ни initialState, ни последующих friendState.
            await ws.send_json(self._viewport.frame())
            await ws.send_json({"type": "bindRoutes", "payload": {"sharers": []}})

            tasks = [
                asyncio.create_task(self._keepalive(ws)),
                asyncio.create_task(self._idle_watchdog(ws)),
            ]
            try:
                await self._read_loop(ws)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

            if ws.close_code in AUTH_CLOSE_CODES:
                raise ZondAuthError(f"Сокет закрыт с кодом {ws.close_code}")

    async def _read_loop(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for msg in ws:
            if msg.type is aiohttp.WSMsgType.TEXT:
                self._last_rx = time.monotonic()
                try:
                    frame = msg.json()
                except ValueError:
                    _LOGGER.debug("Не-JSON фрейм: %s", msg.data[:200])
                    continue
                if positions := self._parser.feed(frame):
                    self._on_positions(positions)
            elif msg.type is aiohttp.WSMsgType.BINARY:
                self._last_rx = time.monotonic()
                _LOGGER.debug("Бинарный фрейм (%d байт) — декодер не нужен", len(msg.data))
            elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                break

    async def _keepalive(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Прикладной keepalive: повторяем viewportChanged.

        Отдельного ping/pong в протоколе нет, а протокольного ping может не
        хватить — сервер считает простоем отсутствие именно прикладных фреймов.
        viewportChanged идемпотентен, поэтому годится.
        """
        frame = self._viewport.frame()
        while True:
            await asyncio.sleep(KEEPALIVE_INTERVAL)
            await ws.send_json(frame)

    async def _idle_watchdog(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        """Рвём «полузакрытое» соединение: TCP жив, а данные не идут."""
        while True:
            await asyncio.sleep(self._idle_timeout / 3)
            idle = time.monotonic() - self._last_rx
            if idle > self._idle_timeout:
                _LOGGER.warning("Нет входящих %.0f с — переподключаюсь", idle)
                await ws.close()
                return


async def async_validate_token(session: aiohttp.ClientSession, token: str) -> dict[str, Any]:
    """Проверяет токен и возвращает профиль. Тот же access_token, что у zond."""
    from .const import USERS_ME_URL

    async with session.get(
        USERS_ME_URL,
        params={"access_token": token},
        headers={"User-Agent": USER_AGENT},
        timeout=aiohttp.ClientTimeout(total=15),
    ) as resp:
        if resp.status in (401, 403):
            raise ZondAuthError(f"HTTP {resp.status}")
        resp.raise_for_status()
        return await resp.json()
