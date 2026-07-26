#!/usr/bin/env python3
"""Наблюдение за сроком жизни access_token 2ГИС.

Вопрос «токен вечный или нет» из одного дампа не решается — нужна история.
Скрипт периодически дёргает api.auth.2gis.com и пишет результат в JSONL,
по которому потом видно, сколько токен реально прожил.

    # разовая проверка (для cron / Планировщика задач)
    python tools/token_watch.py --once

    # непрерывно, раз в 6 часов
    python tools/token_watch.py --interval 6

    # что накопилось
    python tools/token_watch.py --report

Важная оговорка про интерпретацию: если у 2ГИС срок скользящий (продлевается
активностью), то постоянно подключённая интеграция может держать токен живым
неограниченно, и «смерть» наступит только при простое. Поэтому наблюдение при
работающей интеграции и без неё — это два разных эксперимента.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bridge.tokens import fingerprint, validate  # noqa: E402

TOKEN_FILE = ROOT / "data" / "token.json"
LOG_FILE = ROOT / "data" / "token_watch.jsonl"


def load_token() -> str:
    if not TOKEN_FILE.exists():
        raise SystemExit(f"Нет {TOKEN_FILE}. Сначала: python tools/token_from_har.py <файл.har>")
    return str(json.loads(TOKEN_FILE.read_text(encoding="utf-8"))["token"]).strip()


def check(token: str) -> dict:
    alive, detail = validate(token)
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "alive": alive,
        "detail": detail if alive else detail[:200],
        "token": fingerprint(token),
    }
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def report() -> int:
    if not LOG_FILE.exists():
        print("История пуста — запусти хотя бы одну проверку.")
        return 1

    records = [json.loads(line) for line in LOG_FILE.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not records:
        print("История пуста.")
        return 1

    parse = lambda r: datetime.fromisoformat(r["ts"])
    first, last = parse(records[0]), parse(records[-1])
    alive = [r for r in records if r["alive"]]
    dead = [r for r in records if not r["alive"]]

    print(f"проверок:       {len(records)}")
    print(f"первая:         {first.astimezone().isoformat(timespec='minutes')}")
    print(f"последняя:      {last.astimezone().isoformat(timespec='minutes')}")
    print(f"окно наблюдения: {(last - first).total_seconds() / 86400:.1f} суток")

    if alive:
        last_alive = parse(alive[-1])
        print(f"последний раз жив: {last_alive.astimezone().isoformat(timespec='minutes')}")
        print(f"подтверждённое время жизни: не менее {(last_alive - first).total_seconds() / 86400:.1f} суток")
    if dead:
        first_dead = parse(dead[0])
        print(f"\nПЕРВЫЙ ОТКАЗ: {first_dead.astimezone().isoformat(timespec='minutes')}")
        print(f"  причина: {dead[0]['detail']}")
        if alive:
            gap = (first_dead - parse(alive[-1])).total_seconds() / 3600
            print(f"  умер в промежутке шириной {gap:.1f} ч после последней удачной проверки")
    else:
        print("\nотказов не было — токен всё ещё жив")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="одна проверка и выход")
    ap.add_argument("--interval", type=float, default=6.0, help="часы между проверками")
    ap.add_argument("--report", action="store_true", help="показать накопленную статистику")
    args = ap.parse_args()

    if args.report:
        return report()

    token = load_token()
    while True:
        record = check(token)
        status = "жив" if record["alive"] else "МЁРТВ"
        print(f"{record['ts']}  {status}  {record['detail']}", flush=True)
        if not record["alive"]:
            print("\nТокен перестал приниматься — запусти --report и посмотри окно.")
            return 1
        if args.once:
            return 0
        time.sleep(args.interval * 3600)


if __name__ == "__main__":
    raise SystemExit(main())
