"""Тесты WS-клиента: построение рамки и цикл чтения.

Сеть здесь не поднимается. Проверяется то, что ломается тихо: неверная рамка
означает, что сервер не пришлёт ни одного состояния, а незамеченное исключение
в цикле чтения обрывает соединение и оставляет интеграцию без обновлений.
"""

from __future__ import annotations

import json

import aiohttp
import pytest

from twogis_friends.zond import Viewport, ZondClient


class TestViewport:
    """Рамка, по которой сервер решает, о ком присылать апдейты."""

    def test_рамка_строится_вокруг_точки(self):
        viewport = Viewport(55.75, 37.62, 1.0)
        assert viewport.top_lat == pytest.approx(56.75)
        assert viewport.bottom_lat == pytest.approx(54.75)
        assert viewport.left_lon == pytest.approx(36.62)
        assert viewport.right_lon == pytest.approx(38.62)

    def test_широта_обрезается_у_полюса(self):
        """Иначе уехали бы за 90 градусов, и сервер отверг бы рамку."""
        assert Viewport(89.0, 0.0, 5.0).top_lat == 90.0
        assert Viewport(-89.0, 0.0, 5.0).bottom_lat == -90.0

    def test_долгота_обрезается_у_антимеридиана(self):
        assert Viewport(0.0, 179.0, 5.0).right_lon == 180.0
        assert Viewport(0.0, -179.0, 5.0).left_lon == -180.0

    def test_нулевой_радиус_даёт_точку(self):
        viewport = Viewport(55.75, 37.62, 0.0)
        assert viewport.top_lat == viewport.bottom_lat == pytest.approx(55.75)
        assert viewport.left_lon == viewport.right_lon == pytest.approx(37.62)

    def test_структура_фрейма(self):
        """Форма важна буквально: сервер молча игнорирует непонятный фрейм."""
        frame = Viewport(55.75, 37.62, 1.0).frame()

        assert frame["type"] == "viewportChanged"
        viewport = frame["payload"]["viewport"]
        assert set(viewport) == {"topLeft", "bottomRight"}
        assert set(viewport["topLeft"]) == {"lat", "lon"}
        assert "zoom" in frame["payload"]

    def test_углы_не_перепутаны(self):
        """topLeft — север и запад, bottomRight — юг и восток."""
        viewport = Viewport(55.75, 37.62, 1.0).frame()["payload"]["viewport"]
        assert viewport["topLeft"]["lat"] > viewport["bottomRight"]["lat"]
        assert viewport["topLeft"]["lon"] < viewport["bottomRight"]["lon"]

    def test_фрейм_воспроизводим(self):
        """Тот же фрейм уходит как keepalive — он обязан быть идемпотентным."""
        viewport = Viewport(55.75, 37.62, 1.0)
        assert viewport.frame() == viewport.frame()


# --- цикл чтения ------------------------------------------------------------


class FakeMessage:
    def __init__(self, type_, data=""):
        self.type = type_
        self.data = data

    def json(self):
        return json.loads(self.data)


class FakeWS:
    """Минимальная замена ClientWebSocketResponse: только асинхронный обход."""

    def __init__(self, messages):
        self._messages = messages
        self.closed = False

    def __aiter__(self):
        async def generator():
            for message in self._messages:
                yield message
        return generator()

    async def close(self):
        self.closed = True


def make_client(collector):
    return ZondClient(
        session=None,                       # сеть в этих тестах не нужна
        token="токен",
        viewport=Viewport(55.75, 37.62, 1.0),
        on_positions=collector,
    )


def text(payload):
    return FakeMessage(aiohttp.WSMsgType.TEXT, json.dumps(payload))


FRIEND_STATE = {
    "type": "friendState",
    "payload": {"id": "f1", "location": {"lat": 55.75, "lon": 37.62}},
}


class TestReadLoop:
    @pytest.mark.asyncio
    async def test_позиции_доходят_до_колбэка(self):
        received = []
        client = make_client(received.extend)

        await client._read_loop(FakeWS([text(FRIEND_STATE)]))

        assert len(received) == 1
        assert received[0].friend_id == "f1"

    @pytest.mark.asyncio
    async def test_не_json_не_роняет_цикл(self):
        """Один битый фрейм не должен обрывать соединение на часы."""
        received = []
        client = make_client(received.extend)

        await client._read_loop(FakeWS([
            FakeMessage(aiohttp.WSMsgType.TEXT, "{это не json"),
            text(FRIEND_STATE),
        ]))

        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_пустой_разбор_колбэк_не_дёргает(self):
        """Колбэк вызывает обновление в Home Assistant — впустую его звать незачем."""
        calls = []
        client = make_client(calls.append)

        await client._read_loop(FakeWS([text({"type": "ping", "payload": {}})]))

        assert calls == []

    @pytest.mark.asyncio
    async def test_двоичный_фрейм_пропускается(self):
        received = []
        client = make_client(received.extend)

        await client._read_loop(FakeWS([
            FakeMessage(aiohttp.WSMsgType.BINARY, b"\x00\x01"),
            text(FRIEND_STATE),
        ]))

        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_ошибка_прерывает_цикл(self):
        """После ERROR читать нечего — надо выходить и переподключаться."""
        received = []
        client = make_client(received.extend)

        await client._read_loop(FakeWS([
            FakeMessage(aiohttp.WSMsgType.ERROR),
            text(FRIEND_STATE),          # сюда дойти не должно
        ]))

        assert received == []

    @pytest.mark.asyncio
    async def test_закрытие_прерывает_цикл(self):
        received = []
        client = make_client(received.extend)

        await client._read_loop(FakeWS([
            FakeMessage(aiohttp.WSMsgType.CLOSED),
            text(FRIEND_STATE),
        ]))

        assert received == []

    @pytest.mark.asyncio
    async def test_имена_помнятся_между_фреймами(self):
        """Состояние парсера живёт в клиенте, а не создаётся на каждый фрейм."""
        received = []
        client = make_client(received.extend)

        await client._read_loop(FakeWS([
            text({"type": "initialState",
                  "payload": {"profiles": [{"id": "f1", "name": "Аня"}], "states": []}}),
            text(FRIEND_STATE),
        ]))

        assert received[0].name == "Аня"

    @pytest.mark.asyncio
    async def test_отметка_времени_обновляется(self):
        """На неё смотрит сторож простоя: не обновится — разорвёт живое соединение."""
        client = make_client(lambda _: None)
        client._last_rx = 0.0

        await client._read_loop(FakeWS([text(FRIEND_STATE)]))

        assert client._last_rx > 0.0
