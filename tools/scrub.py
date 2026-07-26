#!/usr/bin/env python3
"""Обезличивание дампов из DevTools перед тем, как их куда-то отправить.

Режет токены, куки и идентификаторы, но сохраняет структуру: имена ключей,
вложенность, типы значений и формат timestamp'ов.

    python tools/scrub.py raw.txt -o clean.txt
    Get-Clipboard | python tools/scrub.py - | Set-Clipboard

Маппинг id -> алиас складывается в scrub-map.json, поэтому несколько прогонов
дают согласованные псевдонимы.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

MAP_PATH = Path(__file__).with_name("scrub-map.json")

# JWE = 5 сегментов base64url через точку, JWT = 3.
_SEG = r"[A-Za-z0-9_-]"
RE_JOSE = re.compile(rf"\b(?:{_SEG}{{8,}}\.){{2,4}}{_SEG}{{8,}}\b")

RE_COOKIE_HEADER = re.compile(r"(?im)^(cookie|set-cookie)\s*:\s*(.+)$")
RE_CURL_COOKIE = re.compile(r"(?i)(-H\s+['\"]cookie:\s*)([^'\"]+)(['\"])")
RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")
RE_PHONE = re.compile(r"\+?[78][\s(-]?\d{3}[\s)-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}")

# Ключи, значения которых заменяем на стабильный псевдоним.
ID_KEYS = {"id", "user_id", "userid", "uid", "friend_id", "friendid", "member_id",
           "owner_id", "device_id", "deviceid", "session_id", "sessionid",
           "sharing_id", "guid", "uuid"}
NAME_KEYS = {"name", "first_name", "last_name", "nickname", "nick", "display_name",
             "title", "login", "email", "phone", "avatar", "photo", "photo_url",
             "avatar_url", "picture"}
GEO_KEYS = {"lat", "latitude", "lon", "lng", "long", "longitude", "x", "y",
            "point", "coordinates", "coords"}
SECRET_KEYS = {"token", "access_token", "refresh_token", "id_token", "jwt", "auth",
               "authorization", "key", "secret", "signature", "sig", "password"}


class Scrubber:
    def __init__(self, mapping: dict[str, str] | None = None) -> None:
        self.map: dict[str, str] = mapping or {}
        self._counters: dict[str, int] = {}

    def alias(self, kind: str, value: str) -> str:
        key = f"{kind}:{value}"
        if key not in self.map:
            self._counters[kind] = self._counters.get(kind, 0) + 1
            self.map[key] = f"{kind}_{self._counters[kind]}"
        return self.map[key]

    # --- скалярные значения --------------------------------------------------

    @staticmethod
    def _jose(token: str) -> str:
        return f"<{'JWE' if token.count('.') == 4 else 'JWT'}:{token.count('.') + 1}segs:len={len(token)}>"

    def _blur_geo(self, value):
        """Округляем координату, сохраняя исходный тип (float / str)."""
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return round(float(value), 2)
        if isinstance(value, str):
            try:
                return f"{round(float(value), 2)}"
            except ValueError:
                return value
        return value

    def scalar(self, key: str | None, value):
        k = (key or "").lower()
        if isinstance(value, str):
            if k in SECRET_KEYS or RE_JOSE.fullmatch(value):
                return self._jose(value) if RE_JOSE.fullmatch(value) else f"<SECRET:len={len(value)}>"
            if k in ID_KEYS:
                return self.alias("FRIEND", value)
            if k in NAME_KEYS:
                return self.alias("PERSON", value)
            if k in GEO_KEYS:
                return self._blur_geo(value)
            return self.text(value)
        if isinstance(value, (int, float)):
            if k in GEO_KEYS:
                return self._blur_geo(value)
            if k in ID_KEYS:
                # числовой id: длина цифр диагностична, сохраняем её
                return int(self.alias("FRIEND", str(value)).split("_")[-1])
            return value
        return value

    # --- обход JSON ----------------------------------------------------------

    def walk(self, node, key: str | None = None):
        if isinstance(node, dict):
            return {k: self.walk(v, k) for k, v in node.items()}
        if isinstance(node, list):
            # координатная пара [lon, lat] приходит списком
            if (key or "").lower() in GEO_KEYS and all(
                isinstance(i, (int, float)) and not isinstance(i, bool) for i in node
            ):
                return [self._blur_geo(i) for i in node]
            return [self.walk(v, key) for v in node]
        return self.scalar(key, node)

    # --- сырой текст (cURL, заголовки, лог фреймов) --------------------------

    def text(self, s: str) -> str:
        s = RE_JOSE.sub(lambda m: self._jose(m.group(0)), s)
        s = RE_CURL_COOKIE.sub(lambda m: m.group(1) + self._cookie_names(m.group(2)) + m.group(3), s)
        s = RE_COOKIE_HEADER.sub(lambda m: f"{m.group(1)}: {self._cookie_names(m.group(2))}", s)
        s = RE_EMAIL.sub("<EMAIL>", s)
        s = RE_PHONE.sub("+7XXXXXXXXXX", s)
        return s

    def _cookie_names(self, blob: str) -> str:
        names = []
        for part in blob.split(";"):
            part = part.strip()
            if not part:
                continue
            names.append(part.split("=", 1)[0] + "=<COOKIE>")
        return "; ".join(names)

    def run(self, raw: str) -> str:
        raw = raw.strip()
        try:
            return json.dumps(self.walk(json.loads(raw)), ensure_ascii=False, indent=2)
        except (json.JSONDecodeError, ValueError):
            return self.text(raw)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="путь к файлу или '-' для stdin")
    ap.add_argument("-o", "--output", help="куда писать (по умолчанию stdout)")
    ap.add_argument("--no-map", action="store_true", help="не сохранять scrub-map.json")
    args = ap.parse_args()

    raw = sys.stdin.read() if args.input == "-" else Path(args.input).read_text(encoding="utf-8")

    mapping = json.loads(MAP_PATH.read_text(encoding="utf-8")) if MAP_PATH.exists() else {}
    sc = Scrubber(mapping)
    # восстанавливаем счётчики, чтобы алиасы не начинались заново
    for v in mapping.values():
        kind, _, num = v.rpartition("_")
        if num.isdigit():
            sc._counters[kind] = max(sc._counters.get(kind, 0), int(num))

    out = sc.run(raw)

    if not args.no_map:
        MAP_PATH.write_text(json.dumps(sc.map, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
        print(f"-> {args.output} ({len(out)} байт, {len(sc.map)} псевдонимов)", file=sys.stderr)
    else:
        sys.stdout.write(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
