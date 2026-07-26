#!/usr/bin/env python3
"""Выжимка авторизационных запросов из HAR — с вырезанными секретами.

HAR логина весит десятки мегабайт и содержит боевые учётные данные. Скрипт
оставляет только то, что нужно для реализации входа по телефону, и вычищает
номер, SMS-код, пароль и выданные токены.

    python tools/scrub_har.py login.har -o login-clean.json

Печатает краткую сводку и пишет компактный JSON: метод, URL, тело запроса,
статус, тело ответа, имена выставленных кук.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

# Интересен либо авторизационный хост, либо авторизационный путь.
# Слишком широкий фильтр (просто слово "code" или "id") тащит пол-HAR.
AUTH_HOST = re.compile(r"(^|\.)(auth\.2gis\.com|id\.2gis\.com|passepartout\.2gis\.com)$", re.I)
AUTH_PATH = re.compile(r"/(auth|login|signin|signup|logout|token|session|sms|otp|"
                       r"confirm|verify|oauth|register)(/|$|\?)", re.I)
BORING = re.compile(r"(\.js|\.css|\.png|\.jpg|\.svg|\.woff|\.ico|/metrics|/log\b)", re.I)

HEX40 = re.compile(r"\b[0-9a-f]{40}\b")
JOSE = re.compile(r"\b(?:[A-Za-z0-9_-]{8,}\.){2,4}[A-Za-z0-9_-]{8,}\b")
# (?<!\d)/(?!\d) обязательны: без них шаблон выкусывал куски из длинных
# числовых идентификаторов и портил данные, которые нужно читать
PHONE = re.compile(r"(?<!\d)(?:\+7|8)[\s(-]?\d{3}[\s)-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}(?!\d)")
EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]+\b")

MAX_BODY = 4000

# Поля, значения которых вырезаем целиком, но имена сохраняем — форма важна.
SECRET_FIELDS = {
    "password", "pass", "pwd", "secret",
    "code", "sms_code", "smscode", "otp", "pin", "confirmation_code",
    "token", "access_token", "refresh_token", "id_token",
    "phone", "phone_number", "login", "username", "email",
}

KEEP_REQ_HEADERS = {"content-type", "authorization", "x-token", "origin", "referer"}


def redact_text(value: str) -> str:
    value = HEX40.sub("<TOKEN40>", value)
    value = JOSE.sub("<JOSE>", value)
    value = EMAIL.sub("<EMAIL>", value)
    value = PHONE.sub("<PHONE>", value)
    return value


def redact_structure(node):
    """Рекурсивно чистит значения секретных полей, сохраняя ключи и типы."""
    if isinstance(node, dict):
        out = {}
        for key, val in node.items():
            if key.lower() in SECRET_FIELDS and not isinstance(val, (dict, list)):
                out[key] = f"<{key.upper()}>"
            else:
                out[key] = redact_structure(val)
        return out
    if isinstance(node, list):
        return [redact_structure(v) for v in node]
    if isinstance(node, str):
        return redact_text(node)
    return node


def clean_body(text: str | None, mime: str = "") -> object:
    if not text:
        return None
    stripped = text.strip()
    if len(stripped) > MAX_BODY:
        # длинные тела нам не нужны: интересны структура и наличие полей
        return redact_text(stripped[:MAX_BODY]) + f"... [обрезано, всего {len(stripped)} символов]"
    try:
        return redact_structure(json.loads(stripped))
    except ValueError:
        pass
    if "form-urlencoded" in mime or ("=" in stripped and "&" in stripped and " " not in stripped[:200]):
        pairs = parse_qsl(stripped, keep_blank_values=True)
        if pairs:
            return {
                k: (f"<{k.upper()}>" if k.lower() in SECRET_FIELDS else redact_text(v))
                for k, v in pairs
            }
    return redact_text(stripped[:2000])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("har", type=Path)
    ap.add_argument("-o", "--output", type=Path, default=Path("login-clean.json"))
    ap.add_argument("--all", action="store_true", help="не фильтровать, брать все запросы")
    args = ap.parse_args()

    har = json.loads(args.har.read_text(encoding="utf-8-sig"))
    entries = har["log"]["entries"]

    kept = []
    for entry in entries:
        request, response = entry["request"], entry["response"]
        url = request["url"]
        if not args.all:
            parts = urlsplit(url)
            if BORING.search(parts.path):
                continue
            if not (AUTH_HOST.search(parts.netloc) or AUTH_PATH.search(parts.path)):
                continue

        content = response.get("content") or {}
        post = request.get("postData") or {}
        set_cookies = [
            h["value"].split("=", 1)[0]
            for h in response.get("headers", [])
            if h["name"].lower() == "set-cookie"
        ]

        kept.append({
            "method": request["method"],
            "url": redact_text(url),
            "status": response["status"],
            "request_headers": {
                h["name"]: redact_text(h["value"])
                for h in request.get("headers", [])
                if h["name"].lower() in KEEP_REQ_HEADERS
            },
            "request_body": clean_body(post.get("text"), post.get("mimeType", "")),
            "response_body": clean_body(content.get("text"), content.get("mimeType", "")),
            "set_cookie_names": set_cookies,
        })

    args.output.write_text(json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"всего запросов в HAR: {len(entries)}")
    print(f"отобрано авторизационных: {len(kept)}")
    print(f"записано в {args.output} ({args.output.stat().st_size} байт)\n")
    for item in kept:
        marker = "  <- есть тело ответа" if item["response_body"] else ""
        print(f"  {item['method']:5} {item['status']}  {item['url'][:110]}{marker}")

    if not kept:
        print("\nНичего не нашлось. Возможно, фильтр слишком строгий — попробуй --all")
        return 1
    print("\nПроверь файл глазами перед отправкой: секреты вырезаны автоматически, "
          "но лишнее лучше удалить руками.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
