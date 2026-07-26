"""Точка входа моста: zond WS -> нормализация -> MQTT -> Home Assistant."""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from . import config, models, tokens
from .mqtt_pub import HaMqttPublisher
from .zond import ZondClient

log = logging.getLogger("bridge")


def setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("websockets").setLevel(logging.WARNING)


async def run(cfg: config.Config) -> None:
    publisher = HaMqttPublisher(cfg.mqtt)
    publisher.start()

    client = ZondClient(cfg.zond, tokens.build(cfg), log_frames=cfg.log_frames)
    parser = models.ZondParser()

    seen_ids: set[str] = set()
    # zond повторяет friendState с тем же lastSeen по несколько раз подряд —
    # без этой проверки в MQTT и в историю HA летит мусор
    last_sig: dict[str, tuple] = {}
    unparsed = skipped = 0

    try:
        async for frame in client.stream():
            positions = parser.feed(frame)
            if not positions:
                unparsed += 1
                # первые несколько нераспознанных фреймов показываем целиком —
                # по ним и уточняется EXPLICIT_MAP
                if unparsed <= 10:
                    log.info("Фрейм без координат #%d: %s", unparsed, str(frame)[:600])
                continue
            for pos in positions:
                if pos.friend_id not in seen_ids:
                    seen_ids.add(pos.friend_id)
                    log.info("Новый друг: id=%s… name=%s battery=%s (всего %d)",
                             pos.friend_id[:8], pos.name, pos.battery, len(seen_ids))

                sig = (pos.latitude, pos.longitude, pos.battery, pos.charging,
                       pos.timestamp, pos.movement, pos.place)
                if last_sig.get(pos.friend_id) == sig:
                    skipped += 1
                    if skipped % 100 == 0:
                        log.debug("Пропущено повторов без изменений: %d", skipped)
                    continue
                last_sig[pos.friend_id] = sig
                publisher.publish(pos)
    finally:
        publisher.stop()


def main() -> int:
    cfg = config.load()
    setup_logging(cfg.log_level)
    log.info("Старт моста: zond=%s token_provider=%s viewport=%s mqtt=%s:%s",
             cfg.zond.url, cfg.token.provider, cfg.zond.viewport, cfg.mqtt.host, cfg.mqtt.port)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    task = loop.create_task(run(cfg))

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, task.cancel)
        except NotImplementedError:
            pass  # Windows: обходимся KeyboardInterrupt

    try:
        loop.run_until_complete(task)
    except (asyncio.CancelledError, KeyboardInterrupt):
        log.info("Остановка по сигналу")
    finally:
        loop.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
