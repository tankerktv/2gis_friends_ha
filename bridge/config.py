"""Конфигурация моста — всё из переменных окружения (см. .env.example)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _int(name: str, default: int) -> int:
    raw = _env(name)
    return int(raw) if raw else default


def _float(name: str, default: float) -> float:
    raw = _env(name)
    return float(raw) if raw else default


def _bool(name: str, default: bool = False) -> bool:
    raw = _env(name).lower()
    return raw in ("1", "true", "yes", "on") if raw else default


@dataclass(frozen=True)
class ZondConfig:
    # Реальный эндпоинт из HAR веб-версии: /api/1.1/user/ws, токен в query.
    url: str = field(default_factory=lambda: _env("ZOND_WS_URL", "wss://zond.api.2gis.ru/api/1.1/user/ws"))
    origin: str = field(default_factory=lambda: _env("ZOND_ORIGIN", "https://2gis.ru"))
    app_version: str = field(default_factory=lambda: _env("ZOND_APP_VERSION", "6.31.0"))
    channels: str = field(default_factory=lambda: _env("ZOND_CHANNELS", "markers,sharing,routes"))
    query_param: str = field(default_factory=lambda: _env("ZOND_QUERY_PARAM", "token"))

    # Область, по которой сервер фильтрует апдейты маркеров. Ставим заведомо шире
    # города: "верх_lat,лево_lon,низ_lat,право_lon".
    viewport: str = field(default_factory=lambda: _env("ZOND_VIEWPORT", "57.5,83.0,55.5,86.5"))
    viewport_zoom: int = field(default_factory=lambda: _int("ZOND_VIEWPORT_ZOOM", 11))

    # keepalive: friendsSocketIdleTimeout = 300000 мс, держим запас.
    # Прикладной ping в протоколе не замечен, поэтому вместо него повторяем
    # viewportChanged — заведомо валидный для сервера фрейм.
    ws_ping_interval: float = field(default_factory=lambda: _float("ZOND_WS_PING_INTERVAL", 20.0))
    app_keepalive_interval: float = field(default_factory=lambda: _float("ZOND_KEEPALIVE_INTERVAL", 120.0))
    idle_timeout: float = field(default_factory=lambda: _float("ZOND_IDLE_TIMEOUT", 900.0))

    reconnect_min: float = field(default_factory=lambda: _float("ZOND_RECONNECT_MIN", 2.0))
    reconnect_max: float = field(default_factory=lambda: _float("ZOND_RECONNECT_MAX", 120.0))

    def viewport_payload(self) -> dict:
        top_lat, left_lon, bottom_lat, right_lon = (float(x) for x in self.viewport.split(","))
        return {
            "type": "viewportChanged",
            "payload": {
                "viewport": {
                    "topLeft": {"lon": left_lon, "lat": top_lat},
                    "bottomRight": {"lon": right_lon, "lat": bottom_lat},
                },
                "zoom": self.viewport_zoom,
            },
        }


@dataclass(frozen=True)
class MqttConfig:
    host: str = field(default_factory=lambda: _env("MQTT_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: _int("MQTT_PORT", 1883))
    username: str = field(default_factory=lambda: _env("MQTT_USERNAME"))
    password: str = field(default_factory=lambda: _env("MQTT_PASSWORD"))
    tls: bool = field(default_factory=lambda: _bool("MQTT_TLS"))
    client_id: str = field(default_factory=lambda: _env("MQTT_CLIENT_ID", "2gis-friends-bridge"))
    base_topic: str = field(default_factory=lambda: _env("MQTT_BASE_TOPIC", "2gis/friends"))
    discovery_prefix: str = field(default_factory=lambda: _env("MQTT_DISCOVERY_PREFIX", "homeassistant"))
    retain: bool = field(default_factory=lambda: _bool("MQTT_RETAIN", True))


@dataclass(frozen=True)
class TokenConfig:
    static: str = field(default_factory=lambda: _env("ZOND_TOKEN"))
    store_path: str = field(default_factory=lambda: _env("TOKEN_STORE", "/data/token.json"))
    provider: str = field(default_factory=lambda: _env("TOKEN_PROVIDER", "static"))  # static | file | playwright
    cookie: str = field(default_factory=lambda: _env("TWOGIS_COOKIE"))
    # для playwright-провайдера
    storage_state: str = field(default_factory=lambda: _env("PLAYWRIGHT_STORAGE_STATE", "/data/storage_state.json"))
    friends_url: str = field(default_factory=lambda: _env("TWOGIS_FRIENDS_URL", "https://2gis.ru/tomsk/friendsList"))
    refresh_margin: float = field(default_factory=lambda: _float("TOKEN_REFRESH_MARGIN", 300.0))


@dataclass(frozen=True)
class Config:
    zond: ZondConfig = field(default_factory=ZondConfig)
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    token: TokenConfig = field(default_factory=TokenConfig)
    log_level: str = field(default_factory=lambda: _env("LOG_LEVEL", "INFO"))
    log_frames: bool = field(default_factory=lambda: _bool("LOG_FRAMES", False))
    # состояние друга считается протухшим после N секунд без апдейта
    stale_after: float = field(default_factory=lambda: _float("STALE_AFTER", 900.0))


def load() -> Config:
    return Config()
