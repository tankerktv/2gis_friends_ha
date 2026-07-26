#!/usr/bin/env python3
"""Трек А, шаг 1: проверить, отдаёт ли REST-эндпоинт координаты друзей.

    python track_a/probe_sessions.py --token "<JWE>"
    python track_a/probe_sessions.py --token "<JWE>" --cookie-file cookies.txt
    python track_a/probe_sessions.py --from-curl curl.txt      # вставь Copy as cURL

Перебирает несколько вариантов авторизации, потому что заранее неизвестно,
какой из них принимает zond: Bearer / X-Token / query-параметр / только cookie.
Печатает статус, тип контента и первые строки тела — этого достаточно, чтобы
понять, есть ли в ответе lat/lon.
"""

from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

import httpx

BASE = "https://zond.api.2gis.ru/api/1.1"
SESSIONS = f"{BASE}/sharing/sessions"

# Кандидаты — пробуем по очереди, останавливаемся на первом 2xx с телом.
def variants(token: str | None) -> list[tuple[str, dict, dict]]:
    """(описание, headers, params)"""
    out: list[tuple[str, dict, dict]] = [("cookie only", {}, {})]
    if token:
        out += [
            ("Authorization: Bearer", {"Authorization": f"Bearer {token}"}, {}),
            ("Authorization: raw", {"Authorization": token}, {}),
            ("X-Token header", {"X-Token": token}, {}),
            ("query ?token=", {}, {"token": token}),
            ("query ?access_token=", {}, {"access_token": token}),
        ]
    return out


def parse_curl(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Вытаскивает заголовки и куки из `Copy as cURL (bash)`."""
    tokens = shlex.split(path.read_text(encoding="utf-8").replace("\\\n", " "))
    headers: dict[str, str] = {}
    cookies: dict[str, str] = {}
    it = iter(range(len(tokens)))
    for i in it:
        t = tokens[i]
        if t in ("-H", "--header") and i + 1 < len(tokens):
            name, _, value = tokens[i + 1].partition(":")
            headers[name.strip()] = value.strip()
        elif t in ("-b", "--cookie") and i + 1 < len(tokens):
            for part in tokens[i + 1].split(";"):
                k, _, v = part.strip().partition("=")
                if k:
                    cookies[k] = v
    for part in headers.pop("Cookie", headers.pop("cookie", "")).split(";"):
        k, _, v = part.strip().partition("=")
        if k:
            cookies[k] = v
    return headers, cookies


def load_cookies(path: Path) -> dict[str, str]:
    """Понимает и `a=1; b=2`, и Netscape cookies.txt."""
    text = path.read_text(encoding="utf-8").strip()
    cookies: dict[str, str] = {}
    if "\t" in text:
        for line in text.splitlines():
            if line.startswith("#") or not line.strip():
                continue
            f = line.split("\t")
            if len(f) >= 7:
                cookies[f[5]] = f[6]
    else:
        for part in text.split(";"):
            k, _, v = part.strip().partition("=")
            if k:
                cookies[k] = v
    return cookies


GEO_HINTS = ("lat", "lon", "lng", "point", "coord", "position", "battery")


def looks_like_geo(body: str) -> list[str]:
    low = body.lower()
    return [h for h in GEO_HINTS if h in low]


def probe(client: httpx.Client, url: str, label: str, headers: dict, params: dict) -> bool:
    try:
        r = client.get(url, headers=headers, params=params)
    except httpx.HTTPError as e:
        print(f"  [{label:24}] ошибка транспорта: {e}")
        return False

    ctype = r.headers.get("content-type", "?")
    print(f"  [{label:24}] {r.status_code} {ctype} {len(r.content)}b")

    if r.status_code >= 400:
        if r.content:
            print(f"      {r.text[:200]}")
        return False

    body = r.text
    hints = looks_like_geo(body)
    try:
        print("      " + json.dumps(r.json(), ensure_ascii=False, indent=2)[:1500].replace("\n", "\n      "))
    except ValueError:
        print(f"      {body[:600]}")
    if hints:
        print(f"      >>> найдены гео-признаки: {', '.join(hints)} — похоже на ТРЕК А")
    else:
        print("      >>> координат не видно — вероятно, только метаданные сессий (ТРЕК Б)")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--token", help="JWE из passepartout/getToken")
    ap.add_argument("--cookie-file", type=Path, help="куки: 'a=1; b=2' или Netscape cookies.txt")
    ap.add_argument("--from-curl", type=Path, help="файл с 'Copy as cURL (bash)' из DevTools")
    ap.add_argument("--url", default=SESSIONS)
    ap.add_argument("--extra", action="append", default=[], metavar="PATH",
                    help="дополнительный путь для перебора, напр. /sharing/friends")
    args = ap.parse_args()

    headers = {
        "Accept": "application/json",
        "Origin": "https://2gis.ru",
        "Referer": "https://2gis.ru/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    }
    cookies: dict[str, str] = {}

    if args.from_curl:
        h, c = parse_curl(args.from_curl)
        headers.update(h)
        cookies.update(c)
    if args.cookie_file:
        cookies.update(load_cookies(args.cookie_file))
    if not args.token and not cookies:
        print("Нужен --token и/или --cookie-file (или --from-curl).", file=sys.stderr)
        return 2

    urls = [args.url] + [f"{BASE}{p if p.startswith('/') else '/' + p}" for p in args.extra]

    with httpx.Client(timeout=15, follow_redirects=False, cookies=cookies, http2=False) as client:
        for url in urls:
            print(f"\nGET {url}")
            for label, h, p in variants(args.token):
                if probe(client, url, label, {**headers, **h}, p):
                    break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
