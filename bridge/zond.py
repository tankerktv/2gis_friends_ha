"""WebSocket-клиент к zond.api.2gis.ru с keepalive и переподключением."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any, AsyncIterator
from urllib.parse import urlencode

import websockets
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

from .config import ZondConfig
from .tokens import TokenError, TokenProvider, fingerprint

log = logging.getLogger(__name__)

# Коды закрытия, после которых нет смысла переподключаться со старым токеном.
AUTH_CLOSE_CODES = {1008, 3000, 4000, 4001, 4003, 4401, 4403}

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")


class ZondClient:
    def __init__(self, cfg: ZondConfig, tokens: TokenProvider, log_frames: bool = False) -> None:
        self._cfg = cfg
        self._tokens = tokens
        self._log_frames = log_frames
        self._last_rx = 0.0

    # --- сборка параметров подключения --------------------------------------

    def _connect_args(self, token: str) -> tuple[str, dict[str, str]]:
        # safe="," — веб шлёт channels с необэкранированными запятыми, повторяем
        query = urlencode({
            "appVersion": self._cfg.app_version,
            "channels": self._cfg.channels,
            self._cfg.query_param: token,
        }, safe=",")
        sep = "&" if "?" in self._cfg.url else "?"
        headers = {
            "Origin": self._cfg.origin,
            "User-Agent": USER_AGENT,
        }
        return f"{self._cfg.url}{sep}{query}", headers

    async def _handshake(self, ws) -> None:
        """Веб шлёт ровно это: viewportChanged, затем bindRoutes.

        initialState прилетает в ответ на viewportChanged, так что без него
        сервер молчит.
        """
        await self._send(ws, self._cfg.viewport_payload())
        await self._send(ws, {"type": "bindRoutes", "payload": {"sharers": []}})

    async def _send(self, ws, payload) -> None:
        raw = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
        if self._log_frames:
            log.debug("-> %s", raw[:500])
        await ws.send(raw)

    # --- фоновые задачи ------------------------------------------------------

    async def _keepalive(self, ws) -> None:
        """Прикладной keepalive: повторяем viewportChanged.

        Отдельного ping/pong в протоколе не видно, а протокольного WS-ping может
        не хватить — сервер обычно считает «простоем» отсутствие именно
        прикладных сообщений (friendsSocketIdleTimeout = 300 с).
        viewportChanged идемпотентен и заведомо валиден, поэтому годится.
        """
        interval = self._cfg.app_keepalive_interval
        if interval <= 0:
            return
        payload = self._cfg.viewport_payload()
        while True:
            await asyncio.sleep(interval)
            await self._send(ws, payload)

    async def _idle_watchdog(self, ws) -> None:
        """Если давно ничего не приходило — рвём соединение, чтобы пересоздать.

        Защита от «полузакрытого» сокета: TCP жив, а данные не идут.
        """
        timeout = self._cfg.idle_timeout
        if timeout <= 0:
            return
        while True:
            await asyncio.sleep(timeout / 3)
            idle = time.monotonic() - self._last_rx
            if idle > timeout:
                log.warning("Нет входящих %.0f с (> %.0f) — переподключаюсь", idle, timeout)
                await ws.close(code=1000, reason="idle")
                return

    # --- основной цикл -------------------------------------------------------

    async def stream(self) -> AsyncIterator[Any]:
        """Бесконечный поток распарсенных фреймов с автопереподключением."""
        attempt = 0
        while True:
            try:
                async for frame in self._session():
                    attempt = 0
                    yield frame
                log.info("Сессия завершилась штатно")
            except InvalidStatus as e:
                status = e.response.status_code
                log.error("Handshake отклонён: HTTP %s", status)
                if status in (401, 403):
                    self._tokens.invalidate()
            except TokenError as e:
                log.error("Проблема с токеном: %s", e)
            except ConnectionClosed as e:
                log.warning("Сокет закрыт: code=%s reason=%r", e.code, e.reason)
                if e.code in AUTH_CLOSE_CODES:
                    log.error("Код %s похож на отказ авторизации — сбрасываю токен", e.code)
                    self._tokens.invalidate()
            except (OSError, asyncio.TimeoutError, websockets.WebSocketException) as e:
                log.warning("Обрыв связи: %s: %s", type(e).__name__, e)

            attempt += 1
            delay = min(self._cfg.reconnect_min * 2 ** (attempt - 1), self._cfg.reconnect_max)
            delay *= 0.5 + random.random()  # джиттер, чтобы не долбить сервер ровным ритмом
            log.info("Реконнект #%d через %.1f с", attempt, delay)
            await asyncio.sleep(delay)

    async def _session(self) -> AsyncIterator[Any]:
        token = await self._tokens.get()
        url, headers = self._connect_args(token)
        log.info("Подключаюсь к %s (token=%s)", url.split("?")[0], fingerprint(token))

        async with connect(
            url,
            additional_headers=headers,
            open_timeout=20,
            close_timeout=5,
            ping_interval=self._cfg.ws_ping_interval or None,
            ping_timeout=20,
            max_size=16 * 1024 * 1024,
        ) as ws:
            log.info("Соединение установлено")
            self._last_rx = time.monotonic()
            await self._handshake(ws)

            tasks = [
                asyncio.create_task(self._keepalive(ws), name="keepalive"),
                asyncio.create_task(self._idle_watchdog(ws), name="idle-watchdog"),
            ]
            try:
                async for raw in ws:
                    self._last_rx = time.monotonic()
                    frame = self._decode(raw)
                    if frame is not None:
                        yield frame
            finally:
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    def _decode(self, raw: str | bytes) -> Any:
        if isinstance(raw, (bytes, bytearray)):
            # бинарный транспорт (protobuf/msgpack) — сюда добавляется декодер,
            # когда станет известен формат; пока пробуем UTF-8 JSON
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                log.warning("Бинарный фрейм %d байт, декодер не настроен: %s",
                            len(raw), bytes(raw[:32]).hex(" "))
                return None
        if self._log_frames:
            log.debug("<- %s", raw[:800])
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            log.debug("Не-JSON фрейм: %r", raw[:200])
            return None
