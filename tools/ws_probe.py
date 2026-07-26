#!/usr/bin/env python3
"""Ручной коннект к zond — проверить токен и посмотреть живые фреймы.

    python tools/ws_probe.py --token "<40hex access_token>"
    python tools/ws_probe.py --token "..." --keepalive 120 --minutes 10

Протокол (снят с HAR веб-версии):
    URL   wss://zond.api.2gis.ru/api/1.1/user/ws?appVersion=..&channels=..&token=..
    ->    {"type":"viewportChanged","payload":{"viewport":{...},"zoom":11}}
    ->    {"type":"bindRoutes","payload":{"sharers":[]}}
    <-    {"type":"sharingSubscriptionsInitialState",...}
    <-    {"type":"initialState","payload":{"profiles":[...],"states":[...]}}

Полезно запустить на 10 минут с --keepalive, чтобы поймать инкрементальные
апдейты и убедиться, что сокет не отваливается по idle-таймауту (300 с).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import websockets
from websockets.asyncio.client import connect

DEFAULT_URL = "wss://zond.api.2gis.ru/api/1.1/user/ws"
DEFAULT_TOKEN_FILE = Path(__file__).resolve().parent.parent / "data" / "token.json"
HEX40 = re.compile(r"^[0-9a-f]{40}$")
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36")


def resolve_token(args: argparse.Namespace) -> str:
    """--token > --token-file > ZOND_TOKEN. Так токен не попадает в историю консоли."""
    if args.token:
        return args.token.strip()
    if args.token_file and args.token_file.exists():
        try:
            return str(json.loads(args.token_file.read_text(encoding="utf-8"))["token"]).strip()
        except (ValueError, KeyError) as e:
            print(f"Не смог прочитать {args.token_file}: {e}", file=sys.stderr)
    return os.environ.get("ZOND_TOKEN", "").strip()


def ts() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%H:%M:%S.%f")[:-3]


def viewport_frame(bbox: str, zoom: int) -> dict:
    top_lat, left_lon, bottom_lat, right_lon = (float(x) for x in bbox.split(","))
    return {
        "type": "viewportChanged",
        "payload": {
            "viewport": {
                "topLeft": {"lon": left_lon, "lat": top_lat},
                "bottomRight": {"lon": right_lon, "lat": bottom_lat},
            },
            "zoom": zoom,
        },
    }


def show(direction: str, data, brief: bool) -> None:
    arrow = "->" if direction == "out" else "<-"
    if isinstance(data, (bytes, bytearray)):
        print(f"{ts()} {arrow} [BINARY {len(data)}b] {data[:64].hex(' ')}")
        return
    try:
        obj = json.loads(data)
    except (ValueError, TypeError):
        print(f"{ts()} {arrow} {str(data)[:2000]}")
        return

    if brief and isinstance(obj, dict):
        payload = obj.get("payload") or {}
        states = payload.get("states") or []
        if not states and isinstance(payload.get("state"), dict):
            states = [payload["state"]]
        # friendState: payload сам является состоянием
        if not states and payload.get("id") and isinstance(payload.get("location"), dict):
            states = [payload]
        head = f"{ts()} {arrow} {obj.get('type')}"
        if states:
            print(head + f"  ({len(states)} states)")
            for s in states:
                loc = s.get("location") or {}
                bat = s.get("battery") or {}
                print(f"        {str(s.get('id'))[:8]}… "
                      f"{loc.get('lat')},{loc.get('lon')} "
                      f"acc={loc.get('accuracy')} bat={bat.get('level')} "
                      f"chg={bat.get('isCharging')} "
                      f"mv={(s.get('movement') or {}).get('status')} "
                      f"seen={s.get('lastSeen')}")
        else:
            print(head + f"  {json.dumps(obj, ensure_ascii=False)[:300]}")
        return

    print(f"{ts()} {arrow} {json.dumps(obj, ensure_ascii=False)[:4000]}")


async def run(args: argparse.Namespace) -> None:
    query = urlencode({"appVersion": args.app_version, "channels": args.channels,
                       "token": args.token}, safe=",")
    url = f"{args.url}?{query}"
    print(f"{ts()} connect {args.url}  channels={args.channels}")

    async with connect(
        url,
        additional_headers={"Origin": "https://2gis.ru", "User-Agent": USER_AGENT},
        open_timeout=15,
        ping_interval=args.ping_interval,
        ping_timeout=20,
        max_size=16 * 1024 * 1024,
    ) as ws:
        print(f"{ts()} OPEN")
        vp = viewport_frame(args.viewport, args.zoom)

        for frame in (vp, {"type": "bindRoutes", "payload": {"sharers": []}}):
            show("out", json.dumps(frame, ensure_ascii=False), args.brief)
            await ws.send(json.dumps(frame, ensure_ascii=False))

        async def heartbeat() -> None:
            if not args.keepalive:
                return
            while True:
                await asyncio.sleep(args.keepalive)
                show("out", json.dumps(vp, ensure_ascii=False), args.brief)
                await ws.send(json.dumps(vp, ensure_ascii=False))

        hb = asyncio.create_task(heartbeat())
        deadline = time.monotonic() + args.minutes * 60 if args.minutes else None
        started = time.monotonic()
        try:
            while True:
                timeout = None if deadline is None else max(0.1, deadline - time.monotonic())
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
                except asyncio.TimeoutError:
                    print(f"{ts()} лимит --minutes истёк, выхожу")
                    break
                show("in", msg, args.brief)
        except websockets.ConnectionClosed as e:
            print(f"{ts()} CLOSED code={e.code} reason={e.reason!r} "
                  f"после {time.monotonic() - started:.0f} c")
            if e.code in (1008, 3000, 4000, 4001, 4401, 4403):
                print("      код похож на отказ авторизации — токен протух?")
        finally:
            hb.cancel()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--token", help="access_token (40 hex). Если не задан — берётся из "
                                    "--token-file, иначе из переменной ZOND_TOKEN")
    ap.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE,
                    help=f"JSON с полем token (по умолчанию {DEFAULT_TOKEN_FILE.name})")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--app-version", default="6.31.0")
    ap.add_argument("--channels", default="markers,sharing,routes")
    ap.add_argument("--viewport", default="57.5,83.0,55.5,86.5",
                    help="верх_lat,лево_lon,низ_lat,право_lon")
    ap.add_argument("--zoom", type=int, default=11)
    ap.add_argument("--ping-interval", type=float, default=20.0)
    ap.add_argument("--keepalive", type=float, default=120,
                    help="секунды между повторами viewportChanged (0 = не слать)")
    ap.add_argument("--minutes", type=float, default=0, help="выйти через N минут (0 = до Ctrl+C)")
    ap.add_argument("--brief", action="store_true", default=True,
                    help="компактный вывод states (по умолчанию)")
    ap.add_argument("--full", dest="brief", action="store_false", help="печатать фреймы целиком")
    args = ap.parse_args()

    # прогон длинный, а вывод часто уходит в файл — пишем построчно,
    # иначе всё копится в буфере до самого выхода
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    args.token = resolve_token(args)
    if not args.token:
        print("Токен не найден. Любой из вариантов:\n"
              f"  python tools/token_from_har.py <файл.har>   # создаст {DEFAULT_TOKEN_FILE}\n"
              "  set ZOND_TOKEN=<40hex>\n"
              "  --token <40hex>", file=sys.stderr)
        return 2
    if not HEX40.match(args.token):
        print(f"Это не похоже на токен (ожидается 40 hex-символов, получено {len(args.token)} "
              f"символов). Похоже, в команду попал плейсхолдер.", file=sys.stderr)
        return 2

    if args.ping_interval == 0:
        args.ping_interval = None

    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass
    except websockets.InvalidStatus as e:
        print(f"HTTP {e.response.status_code} на handshake — токен не принят")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
