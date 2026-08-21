"""Константы интеграции «2GIS Friends»."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "twogis_friends"

# --- API 2ГИС (см. docs/findings.md) ---------------------------------------
WS_URL: Final = "wss://zond.api.2gis.ru/api/1.1/user/ws"
USERS_ME_URL: Final = "https://api.auth.2gis.com/2.1/users/me"
ORIGIN: Final = "https://2gis.ru"
APP_VERSION: Final = "6.31.0"
CHANNELS: Final = "markers,sharing,routes"
USER_AGENT: Final = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)

# --- поведение соединения ---------------------------------------------------
# friendsSocketIdleTimeout у 2ГИС = 300 с; шлём viewportChanged заметно чаще.
KEEPALIVE_INTERVAL: Final = 120.0
# протокольный ping aiohttp
WS_HEARTBEAT: Final = 20.0
# если столько нет входящих — рвём и пересоздаём соединение
IDLE_TIMEOUT: Final = 900.0
RECONNECT_MIN: Final = 2.0
RECONNECT_MAX: Final = 300.0
# сколько ждём initialState при первичной настройке
FIRST_DATA_TIMEOUT: Final = 45.0

# --- опции ------------------------------------------------------------------
CONF_VIEWPORT_RADIUS: Final = "viewport_radius"
#: сколько минут без входящих данных считать поводом пересоздать соединение.
#: Замер на живом соединении (28 минут, 5 друзей): максимальная тишина 3,4 мин,
#: медиана промежутка между входящими 3 с. Восемь минут — двойной запас.
CONF_IDLE_RECONNECT_MIN: Final = "idle_reconnect_min"
DEFAULT_IDLE_RECONNECT_MIN: Final = 8.0
# Сервер отдаёт апдейты только по друзьям внутри рамки, поэтому берём с запасом.
DEFAULT_VIEWPORT_RADIUS: Final = 2.0   # градусы вокруг координат HA
VIEWPORT_ZOOM: Final = 11

# коды закрытия WS, означающие отказ авторизации
AUTH_CLOSE_CODES: Final = frozenset({1008, 3000, 4000, 4001, 4003, 4401, 4403})

#: Ключи в ``.storage/core.restore_state`` у счётчика расхода. Транслит здесь
#: оставлен намеренно: эти имена уже записаны у всех, кто пользуется
#: интеграцией, и при переименовании восстановление молча не нашло бы своих
#: данных — накопленный расход у каждого друга обнулился бы без единой ошибки
#: в журнале. Наружу они не видны, в отличие от атрибутов сущностей.
KEY_TOTAL: Final = "vsego"
KEY_LAST_BATTERY: Final = "posledniy_zaryad"
KEY_COUNTING_SINCE: Final = "schet_s"
KEY_POINTS: Final = "tochki"

#: Ключ в ``hass.data``, под которым переезд оставляет накопленный расход
#: удаляемого дубля, чтобы сущность-переселенец забрала его при создании.
DRAIN_HANDOVER: Final = "perenos_raskhoda"

#: Ключ в ``hass.data``: какие переезды сторож уже пробовал сделать на лету,
#: ``entry_id -> {старый: новый}``. Живёт вне данных записи намеренно — иначе
#: перезагрузка стирала бы память о попытке, и неудачный переезд крутил бы
#: перезагрузки по кругу.
MIGRATION_ATTEMPTS: Final = "popytki_pereezda"

#: Окончание ``unique_id`` накопителя расхода. Суточный сенсор оканчивается
#: на ``_battery_drain_daily`` и под это условие намеренно не подходит.
SUFFIX_DRAIN: Final = "_battery_drain"
