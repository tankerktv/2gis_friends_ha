#!/usr/bin/env python3
"""Достаёт access_token из HAR и кладёт в data/token.json.

    python tools/token_from_har.py "C:\\Users\\Tanker\\Desktop\\2gis.ru.har"

Сам токен на экран не печатается — только отпечаток и результат проверки через
api.auth.2gis.com. Формат файла тот же, что читает FileTokenProvider, поэтому
мост потом заводится с TOKEN_PROVIDER=file без правок.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bridge.tokens import fingerprint, validate  # noqa: E402

HEX40 = re.compile(r"^[0-9a-f]{40}$")
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "token.json"


def extract(har_path: Path) -> str:
    har = json.loads(har_path.read_text(encoding="utf-8-sig"))
    found: dict[str, int] = {}

    for entry in har["log"]["entries"]:
        url = entry["request"]["url"]
        query = parse_qs(urlsplit(url).query)
        host = urlsplit(url).netloc
        for param in ("token", "access_token"):
            for value in query.get(param, []):
                if HEX40.match(value):
                    # приоритет — токен из сокета zond, он точно рабочий
                    weight = 10 if "zond" in host else 1
                    found[value] = found.get(value, 0) + weight

    if not found:
        raise SystemExit("В HAR не нашлось токена вида 40 hex в параметрах token/access_token.")
    return max(found, key=lambda k: found[k])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("har", type=Path)
    ap.add_argument("-o", "--output", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args()

    token = extract(args.har)
    print("Найден токен:", fingerprint(token))

    if not args.no_validate:
        alive, detail = validate(token)
        print(("ЖИВ:   " if alive else "МЁРТВ: ") + detail)
        if not alive:
            print("\nТокен уже не работает — возьми свежий из DevTools:")
            print("  F12 -> Network -> фильтр WS -> user/ws -> Headers -> Request URL -> token=")
            return 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"token": token}, indent=2), encoding="utf-8")
    print(f"\nЗаписан в {args.output}")
    print("Дальше:  python tools/ws_probe.py --minutes 10")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
