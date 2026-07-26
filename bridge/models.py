"""Разбор фреймов zond.

Протокол снят с HAR веб-версии (см. docs/findings.md). Основное:

* ``initialState`` — ``payload.profiles[]`` (id -> имя) и ``payload.states[]`` (гео);
  приходит один раз в ответ на ``viewportChanged``.
* ``friendState`` — инкрементальный апдейт одного друга. Внимание: ``payload``
  **сам является** состоянием (``payload.id``, ``payload.location``), обёртки
  ``states[]``/``state`` в нём нет.
* элемент состояния::

      {"id": "<hex32>", "lastSeen": 1785060866258,
       "location": {"lat": .., "lon": .., "azimuth": null, "speed": null, "accuracy": 93.7},
       "battery": {"level": 0.53, "isCharging": false},
       "movement": {"status": "stopped", "stoppedAt": .., "manualAt": null, "unreliableAt": null},
       "locationPlace": {"object": {...}, "status": {"id": "home", ...}}}

  ``accuracy``/``azimuth``/``speed`` и весь ``locationPlace`` могут быть ``null``.

``ZondParser`` ориентируется на структуру, а не на имя типа: подходит любой фрейм,
где в payload есть ``states[]``, ``state`` или сам payload похож на состояние.
Незнакомое уходит в эвристику ``heuristic_extract``, которая найдёт координаты
где угодно в дереве.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

log = logging.getLogger(__name__)

LAT_KEYS = ("lat", "latitude")
LON_KEYS = ("lon", "lng", "long", "longitude")
ID_KEYS = ("id", "user_id", "userid", "friend_id", "member_id", "uid")
NAME_KEYS = ("name", "nickname", "display_name", "first_name", "title")
BATTERY_KEYS = ("battery", "battery_level", "batteryLevel", "level", "charge", "power")
ACCURACY_KEYS = ("accuracy", "gps_accuracy", "precision", "radius", "error")
TS_KEYS = ("lastSeen", "timestamp", "ts", "time", "updated_at", "last_seen", "date")
SPEED_KEYS = ("speed", "velocity")
COURSE_KEYS = ("azimuth", "course", "bearing", "heading", "direction")

#: Типы фреймов, в которых заведомо нет координат — эвристику не гоняем.
NO_GEO_TYPES = {
    "sharingSubscriptionsInitialState",
    "pong",
    "ping",
    "error",
}


@dataclass
class FriendPosition:
    friend_id: str
    latitude: float
    longitude: float
    name: str | None = None
    battery: int | None = None
    charging: bool | None = None
    accuracy: float | None = None
    speed: float | None = None
    course: float | None = None
    timestamp: float | None = None          # unix seconds, UTC
    movement: str | None = None             # stopped / moving / ...
    place: str | None = None                # locationPlace.status.id: home, work, ...
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def iso_timestamp(self) -> str:
        ts = self.timestamp or time.time()
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

    def attributes(self) -> dict[str, Any]:
        """Payload для json_attributes_topic. latitude/longitude/gps_accuracy —
        то, по чему HA рисует device_tracker на карте."""
        attrs: dict[str, Any] = {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "gps_accuracy": self.accuracy if self.accuracy is not None else 0,
            "source_type": "gps",
            "last_seen": self.iso_timestamp,
        }
        for key, value in (
            ("friend_name", self.name),
            ("battery_level", self.battery),
            ("battery_charging", self.charging),
            ("speed", self.speed),
            ("course", self.course),
            ("movement", self.movement),
            ("place_status", self.place),
        ):
            if value is not None:
                attrs[key] = value
        attrs.update({k: v for k, v in self.extra.items() if k not in attrs})
        return attrs


# --- утилиты ---------------------------------------------------------------


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def to_unix(value: Any, unit: str = "auto") -> float | None:
    """Приводит timestamp к unix-секундам. Понимает s / ms / us / ISO-8601."""
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    n = _num(value)
    if n is None:
        return None
    if unit == "s":
        return n
    if unit == "ms":
        return n / 1000
    if unit == "us":
        return n / 1_000_000
    if n > 1e14:
        return n / 1_000_000
    if n > 1e11:
        return n / 1000
    return n


def normalize_battery(value: Any) -> int | None:
    """zond отдаёт 0..1 (0.53). Понимаем также 87 и "87%"."""
    if isinstance(value, str):
        value = value.rstrip("% ").strip()
    n = _num(value)
    if n is None:
        return None
    if 0 <= n <= 1:
        n *= 100
    return max(0, min(100, int(round(n))))


def _valid_coords(lat: float | None, lon: float | None) -> bool:
    return (
        lat is not None and lon is not None
        and -90 <= lat <= 90 and -180 <= lon <= 180
        and not (lat == 0 and lon == 0)
    )


# --- разбор известного протокола zond --------------------------------------


def _looks_like_state(node: Any) -> bool:
    """Похож ли объект на элемент states[] — есть id и вложенный location."""
    return (
        isinstance(node, dict)
        and bool(node.get("id"))
        and isinstance(node.get("location"), dict)
    )


class ZondParser:
    """Держит справочник id -> имя из profiles и разбирает фреймы состояния.

    Имена приходят один раз в ``initialState``, а координаты потом — в апдейтах,
    поэтому парсер обязан быть с состоянием.
    """

    def __init__(self) -> None:
        self.names: dict[str, str] = {}
        self._unknown_types: set[str] = set()

    def feed(self, frame: Any) -> list[FriendPosition]:
        if not isinstance(frame, dict):
            return []
        ftype = frame.get("type")
        payload = frame.get("payload")
        if not isinstance(payload, dict):
            return [] if ftype in NO_GEO_TYPES else heuristic_extract(frame)

        for profile in payload.get("profiles") or []:
            if isinstance(profile, dict) and profile.get("id"):
                self.names[str(profile["id"])] = profile.get("name") or None

        states = payload.get("states")
        if states is None:
            single = payload.get("state")
            if isinstance(single, dict):
                states = [single]
        if states is None and _looks_like_state(payload):
            # friendState: payload сам является состоянием, без обёртки
            states = [payload]
        if states is None:
            if ftype in NO_GEO_TYPES:
                return []
            if ftype and ftype not in self._unknown_types:
                self._unknown_types.add(ftype)
                log.info("Незнакомый тип фрейма %r, разбираю эвристикой: %s",
                         ftype, str(frame)[:400])
            return heuristic_extract(frame)

        out: list[FriendPosition] = []
        for state in states:
            pos = self._state_to_position(state)
            if pos is not None:
                out.append(pos)
        return out

    def _state_to_position(self, state: Any) -> FriendPosition | None:
        if not isinstance(state, dict) or not state.get("id"):
            return None
        location = state.get("location") or {}
        lat, lon = _num(location.get("lat")), _num(location.get("lon"))
        if not _valid_coords(lat, lon):
            # друг есть, но координат нет (выключил шаринг) — не публикуем
            return None

        battery = state.get("battery") or {}
        movement = state.get("movement") or {}
        place = state.get("locationPlace") or {}
        place_status = (place.get("status") or {}).get("id") if isinstance(place, dict) else None

        fid = str(state["id"])
        return FriendPosition(
            friend_id=fid,
            latitude=lat,
            longitude=lon,
            name=self.names.get(fid),
            battery=normalize_battery(battery.get("level")),
            charging=battery.get("isCharging") if isinstance(battery.get("isCharging"), bool) else None,
            accuracy=_num(location.get("accuracy")),
            speed=_num(location.get("speed")),
            course=_num(location.get("azimuth")),
            timestamp=to_unix(state.get("lastSeen"), "ms"),
            movement=movement.get("status"),
            place=place_status,
        )


# --- запасная эвристика (для незнакомых фреймов) ----------------------------


def _first(node: dict, keys: Iterable[str]) -> Any:
    lowered = {k.lower(): v for k, v in node.items()}
    for key in keys:
        if key.lower() in lowered:
            return lowered[key.lower()]
    return None


def _flat_context(parent: dict, exclude: Any) -> dict:
    """Скаляры родителя плюс скаляры вложенных на один уровень словарей.

    Для формы ``{"user": {"id": 1}, "location": {...}, "battery": {"level": .5}}``,
    где id и заряд лежат не рядом с координатами, а в соседних объектах.
    """
    ctx: dict[str, Any] = {}
    for key, value in parent.items():
        if not isinstance(value, (dict, list)):
            ctx.setdefault(key, value)
    for key, value in parent.items():
        if isinstance(value, dict) and value is not exclude:
            for sub_key, sub_value in value.items():
                if not isinstance(sub_value, (dict, list)):
                    ctx.setdefault(sub_key, sub_value)
    return ctx


def _candidate_dicts(node: Any) -> Iterable[dict]:
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _candidate_dicts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _candidate_dicts(value)


def heuristic_extract(frame: Any) -> list[FriendPosition]:
    """Ищет объекты с координатами где угодно в дереве."""
    results: list[FriendPosition] = []
    seen: set[int] = set()

    for parent in _candidate_dicts(frame):
        for scope in (parent, *(v for v in parent.values() if isinstance(v, dict))):
            if id(scope) in seen:
                continue
            lat = _num(_first(scope, LAT_KEYS))
            lon = _num(_first(scope, LON_KEYS))
            if not _valid_coords(lat, lon):
                continue
            seen.add(id(scope))
            ctx = _flat_context(parent, exclude=scope)

            def pick(keys, _scope=scope, _ctx=ctx):
                value = _first(_scope, keys)
                return value if value is not None else _first(_ctx, keys)

            fid = pick(ID_KEYS)
            if fid is None:
                fid = f"{lat:.5f}_{lon:.5f}"
                log.debug("Позиция без id, ключом будут координаты")

            consumed = {k.lower() for k in
                        LAT_KEYS + LON_KEYS + ID_KEYS + NAME_KEYS + BATTERY_KEYS
                        + ACCURACY_KEYS + TS_KEYS + SPEED_KEYS + COURSE_KEYS}
            extra = {
                k: v for k, v in {**ctx, **scope}.items()
                if k.lower() not in consumed and isinstance(v, (str, int, float, bool))
            }

            name = pick(NAME_KEYS)
            results.append(FriendPosition(
                friend_id=str(fid),
                latitude=lat,
                longitude=lon,
                name=str(name) if isinstance(name, (str, int)) else None,
                battery=normalize_battery(pick(BATTERY_KEYS)),
                accuracy=_num(pick(ACCURACY_KEYS)),
                speed=_num(pick(SPEED_KEYS)),
                course=_num(pick(COURSE_KEYS)),
                timestamp=to_unix(pick(TS_KEYS)),
                extra=extra,
            ))
    return results
