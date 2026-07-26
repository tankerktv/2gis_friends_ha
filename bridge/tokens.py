"""Получение и обновление токена zond.

Три провайдера, от простого к автономному:

* ``static``     — токен из ZOND_TOKEN. Протух — руками поменял .env и рестартнул.
* ``file``       — токен читается из TOKEN_STORE (JSON) при каждом реконнекте.
                   Обновлять файл может кто угодно снаружи (скрипт, cron, руки).
* ``playwright`` — headless-браузер держит сессию (storage_state.json), заходит на
                   страницу друзей и вытаскивает свежий токен из приложения.

Мост дёргает ``get()`` перед каждым коннектом и ``invalidate()`` при отказе
авторизации, поэтому смена провайдера не трогает остальной код.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import time
from pathlib import Path
from typing import Protocol

log = logging.getLogger(__name__)


class TokenError(RuntimeError):
    pass


def jose_header(token: str) -> dict:
    """Первый сегмент JOSE-токена — открытые метаданные, не секрет."""
    try:
        seg = token.split(".", 1)[0]
        seg += "=" * (-len(seg) % 4)
        return json.loads(base64.urlsafe_b64decode(seg))
    except Exception:
        return {}


def fingerprint(token: str) -> str:
    """Безопасное для логов описание токена (без самого токена)."""
    if not token:
        return "<empty>"
    if token.count(".") in (2, 4):
        hdr = jose_header(token)
        kind = "JWE" if token.count(".") == 4 else "JWT"
        return f"<{kind} len={len(token)} alg={hdr.get('alg', '?')}>"
    return f"<opaque len={len(token)} sha1={hashlib.sha1(token.encode()).hexdigest()[:8]}>"


def validate(token: str, timeout: float = 10.0) -> tuple[bool, str]:
    """Проверяет токен через api.auth.2gis.com — тот же access_token, что у zond.

    Возвращает (жив, описание). Дешёвый способ отличить «протух токен» от
    «лежит сеть» до того, как лезть в сокет.
    """
    import httpx

    try:
        r = httpx.get(
            "https://api.auth.2gis.com/2.1/users/me",
            params={"access_token": token},
            timeout=timeout,
        )
    except httpx.HTTPError as e:
        return False, f"сеть недоступна: {e}"
    if r.status_code == 200:
        data = r.json()
        return True, f"ок, аккаунт {data.get('display_name') or data.get('id')}"
    return False, f"HTTP {r.status_code}: {r.text[:200]}"


class TokenProvider(Protocol):
    async def get(self) -> str: ...
    def invalidate(self) -> None: ...


class StaticTokenProvider:
    def __init__(self, token: str) -> None:
        if not token:
            raise TokenError("TOKEN_PROVIDER=static, но ZOND_TOKEN пуст")
        self._token = token
        self._dead = False

    async def get(self) -> str:
        if self._dead:
            raise TokenError(
                "Статический токен отвергнут сервером. Обнови ZOND_TOKEN в .env "
                "и перезапусти контейнер, либо переключись на TOKEN_PROVIDER=playwright."
            )
        return self._token

    def invalidate(self) -> None:
        self._dead = True


class FileTokenProvider:
    """Читает {"token": "...", "expires_at": 1753500000} из файла.

    Файл перечитывается каждый раз — внешний обновлятор может подложить новый
    токен, мост подхватит его на ближайшем реконнекте без рестарта.
    """

    def __init__(self, path: str, margin: float = 300.0) -> None:
        self._path = Path(path)
        self._margin = margin
        self._last: str | None = None

    async def get(self) -> str:
        if not self._path.exists():
            raise TokenError(f"Нет файла с токеном: {self._path}")
        data = json.loads(self._path.read_text(encoding="utf-8"))
        token = data.get("token", "")
        if not token:
            raise TokenError(f"В {self._path} нет поля 'token'")
        exp = data.get("expires_at")
        if exp and time.time() > float(exp) - self._margin:
            log.warning("Токен из %s истекает в %s — жду обновления файла", self._path, exp)
        self._last = token
        return token

    def invalidate(self) -> None:
        log.warning("Токен из %s отвергнут; ожидаю, что файл обновит внешний процесс", self._path)


class PlaywrightTokenProvider:
    """Достаёт токен из живой браузерной сессии.

    Первый запуск — интерактивный: ``python -m bridge.tokens login`` откроет окно,
    логинишься руками (пароль/SMS вводишь ты, не мост), сессия ложится в
    storage_state.json. Дальше провайдер ходит headless и переиспользует куки.

    Токен снимается перехватом WebSocket/fetch на странице — способ не зависит от
    того, где именно React Query держит кэш, поэтому переживает редизайн фронта.
    """

    _HOOK = """
    () => {
      window.__tok = window.__tok || null;
      const grab = (s) => {
        if (!s || window.__tok) return;
        const m = String(s).match(/[A-Za-z0-9_-]{8,}(?:\\.[A-Za-z0-9_-]{8,}){2,4}/);
        if (m) window.__tok = m[0];
      };
      const OW = window.WebSocket;
      window.WebSocket = function (url, protocols) {
        grab(url);
        if (protocols) grab(Array.isArray(protocols) ? protocols.join(' ') : protocols);
        const ws = new OW(url, protocols);
        const os = ws.send.bind(ws);
        ws.send = (d) => { grab(d); return os(d); };
        return ws;
      };
      window.WebSocket.prototype = OW.prototype;
      const of = window.fetch;
      window.fetch = (...a) => {
        const h = a[1] && a[1].headers;
        if (h) grab(JSON.stringify(h));
        grab(String(a[0]));
        return of(...a);
      };
    }
    """

    def __init__(self, storage_state: str, friends_url: str, margin: float = 300.0) -> None:
        self._state = Path(storage_state)
        self._url = friends_url
        self._margin = margin
        self._cached: str | None = None
        self._fetched_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self) -> str:
        async with self._lock:
            if self._cached and time.monotonic() - self._fetched_at < 3600:
                return self._cached
            self._cached = await self._fetch()
            self._fetched_at = time.monotonic()
            log.info("Токен получен из браузерной сессии: %s", fingerprint(self._cached))
            return self._cached

    def invalidate(self) -> None:
        self._cached = None

    async def _fetch(self) -> str:
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise TokenError("Нужен playwright: pip install playwright && playwright install chromium") from e
        if not self._state.exists():
            raise TokenError(f"Нет {self._state}. Сначала: python -m bridge.tokens login")

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            ctx = await browser.new_context(storage_state=str(self._state))
            try:
                page = await ctx.new_page()
                await page.add_init_script(f"({self._HOOK})()")
                await page.goto(self._url, wait_until="domcontentloaded", timeout=45_000)
                for _ in range(60):
                    token = await page.evaluate("window.__tok")
                    if token:
                        # сессия могла обновиться — сохраняем свежие куки
                        await ctx.storage_state(path=str(self._state))
                        return token
                    await asyncio.sleep(0.5)
                raise TokenError("Токен не пойман за 30 с — вероятно, сессия протухла, нужен новый login")
            finally:
                await ctx.close()
                await browser.close()


async def interactive_login(storage_state: str, url: str) -> None:
    """Открывает видимое окно, чтобы ТЫ залогинился руками, и сохраняет сессию."""
    from playwright.async_api import async_playwright

    path = Path(storage_state)
    path.parent.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        ctx = await browser.new_context(storage_state=str(path) if path.exists() else None)
        page = await ctx.new_page()
        await page.goto(url)
        print("Залогинься в открывшемся окне, дождись списка друзей и нажми Enter здесь...")
        await asyncio.get_running_loop().run_in_executor(None, input)
        await ctx.storage_state(path=str(path))
        print(f"Сессия сохранена в {path}")
        await ctx.close()
        await browser.close()


def build(cfg) -> TokenProvider:
    kind = cfg.token.provider
    if kind == "static":
        return StaticTokenProvider(cfg.token.static)
    if kind == "file":
        return FileTokenProvider(cfg.token.store_path, cfg.token.refresh_margin)
    if kind == "playwright":
        return PlaywrightTokenProvider(cfg.token.storage_state, cfg.token.friends_url, cfg.token.refresh_margin)
    raise TokenError(f"Неизвестный TOKEN_PROVIDER={kind!r}")


if __name__ == "__main__":
    import sys

    from . import config

    cfg = config.load()
    if len(sys.argv) > 1 and sys.argv[1] == "login":
        asyncio.run(interactive_login(cfg.token.storage_state, cfg.token.friends_url))
    else:
        token = asyncio.run(build(cfg).get())
        print(fingerprint(token))
        alive, detail = validate(token)
        print(("ЖИВ:  " if alive else "МЁРТВ: ") + detail)
