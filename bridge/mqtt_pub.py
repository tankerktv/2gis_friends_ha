"""Публикация в MQTT + MQTT Discovery для Home Assistant.

На каждого друга создаётся device с тремя сущностями:
  device_tracker — точка на карте (координаты идут в json_attributes)
  sensor battery — уровень заряда, device_class=battery
  sensor last_seen — время последнего апдейта, device_class=timestamp

Про device_tracker: HA рисует его на карте по атрибутам latitude/longitude,
но НЕ вычисляет зону автоматически — состояние берётся из state_topic как есть.
Поэтому зону считаем на своей стороне (HOME_LAT/HOME_LON/HOME_RADIUS) либо
оставляем not_home и добавляем нужные зоны шаблоном уже в HA.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import threading

import paho.mqtt.client as mqtt

from .config import MqttConfig
from .models import FriendPosition

log = logging.getLogger(__name__)

_SLUG = re.compile(r"[^a-z0-9_]+")


def slug(value: str) -> str:
    return _SLUG.sub("_", str(value).lower()).strip("_") or "unknown"


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


class HaMqttPublisher:
    def __init__(self, cfg: MqttConfig) -> None:
        self._cfg = cfg
        self._announced: set[str] = set()
        self._lock = threading.Lock()
        self._home = self._read_home()

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=cfg.client_id,
            protocol=mqtt.MQTTv311,
        )
        if cfg.username:
            self._client.username_pw_set(cfg.username, cfg.password or None)
        if cfg.tls:
            self._client.tls_set()
        self._client.will_set(f"{cfg.base_topic}/bridge/status", "offline", qos=1, retain=True)
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect

    @staticmethod
    def _read_home() -> tuple[float, float, float] | None:
        lat, lon = os.environ.get("HOME_LAT"), os.environ.get("HOME_LON")
        if not lat or not lon:
            return None
        return float(lat), float(lon), float(os.environ.get("HOME_RADIUS", 100))

    # --- соединение ----------------------------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties=None) -> None:
        if reason_code == 0:
            log.info("MQTT подключён к %s:%s", self._cfg.host, self._cfg.port)
            client.publish(f"{self._cfg.base_topic}/bridge/status", "online", qos=1, retain=True)
            with self._lock:
                self._announced.clear()   # после реконнекта переотправим discovery
        else:
            log.error("MQTT не подключился: %s", reason_code)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties=None) -> None:
        log.warning("MQTT отключён: %s (переподключение автоматическое)", reason_code)

    def start(self) -> None:
        self._client.connect_async(self._cfg.host, self._cfg.port, keepalive=60)
        self._client.loop_start()

    def stop(self) -> None:
        self._client.publish(f"{self._cfg.base_topic}/bridge/status", "offline", qos=1, retain=True)
        self._client.loop_stop()
        self._client.disconnect()

    # --- топики --------------------------------------------------------------

    def _topics(self, fid: str) -> dict[str, str]:
        base = f"{self._cfg.base_topic}/{fid}"
        return {
            "state": f"{base}/state",
            "attrs": f"{base}/attributes",
            "battery": f"{base}/battery",
            "last_seen": f"{base}/last_seen",
            "availability": f"{self._cfg.base_topic}/bridge/status",
        }

    def _discovery(self, pos: FriendPosition, fid: str) -> None:
        t = self._topics(fid)
        friendly = pos.name or f"2GIS {fid}"
        device = {
            "identifiers": [f"2gis_friend_{fid}"],
            "name": friendly,
            "manufacturer": "2GIS",
            "model": "Friends on map",
            "via_device": "2gis_friends_bridge",
        }
        avail = [{"topic": t["availability"], "payload_available": "online",
                  "payload_not_available": "offline"}]
        prefix = self._cfg.discovery_prefix

        configs = [
            (f"{prefix}/device_tracker/2gis_friend_{fid}/config", {
                "name": None,                       # имя берётся от device
                "unique_id": f"2gis_friend_{fid}_tracker",
                "state_topic": t["state"],
                "json_attributes_topic": t["attrs"],
                "payload_home": "home",
                "payload_not_home": "not_home",
                "source_type": "gps",
                "device": device,
                "availability": avail,
            }),
            (f"{prefix}/sensor/2gis_friend_{fid}_battery/config", {
                "name": "Battery",
                "unique_id": f"2gis_friend_{fid}_battery",
                "state_topic": t["battery"],
                "device_class": "battery",
                "unit_of_measurement": "%",
                "state_class": "measurement",
                "entity_category": "diagnostic",
                "device": device,
                "availability": avail,
            }),
            (f"{prefix}/sensor/2gis_friend_{fid}_last_seen/config", {
                "name": "Last seen",
                "unique_id": f"2gis_friend_{fid}_last_seen",
                "state_topic": t["last_seen"],
                "device_class": "timestamp",
                "entity_category": "diagnostic",
                "device": device,
                "availability": avail,
            }),
        ]
        for topic, payload in configs:
            self._client.publish(topic, json.dumps(payload, ensure_ascii=False), qos=1, retain=True)
        log.info("Discovery опубликован для %s (%s)", friendly, fid)

    # --- публикация ----------------------------------------------------------

    def _zone_state(self, pos: FriendPosition) -> str:
        if not self._home:
            return "not_home"
        lat, lon, radius = self._home
        return "home" if haversine_m(pos.latitude, pos.longitude, lat, lon) <= radius else "not_home"

    def publish(self, pos: FriendPosition) -> None:
        fid = slug(pos.friend_id)
        with self._lock:
            first_time = fid not in self._announced
            if first_time:
                self._announced.add(fid)
        if first_time:
            self._discovery(pos, fid)

        t = self._topics(fid)
        retain = self._cfg.retain
        self._client.publish(t["attrs"], json.dumps(pos.attributes(), ensure_ascii=False), qos=1, retain=retain)
        self._client.publish(t["state"], self._zone_state(pos), qos=1, retain=retain)
        self._client.publish(t["last_seen"], pos.iso_timestamp, qos=1, retain=retain)
        if pos.battery is not None:
            self._client.publish(t["battery"], str(pos.battery), qos=1, retain=retain)

        log.debug("%s -> %.5f,%.5f battery=%s", fid, pos.latitude, pos.longitude, pos.battery)
