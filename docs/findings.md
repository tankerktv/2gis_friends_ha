# Результаты разведки

Источник: HAR веб-версии `2gis.ru/tomsk` (Edge 150, 417 запросов, 26.07.2026).
Всё ниже подтверждено на реальном дампе. Секреты и чужие данные вырезаны.

---

## Итог: трек Б

**Трек А закрыт.** `sharing/sessions` в HAR **не вызывается ни разу** — этого
эндпоинта в веб-потоке нет. Все координаты приходят только по WebSocket.

---

## 1. Токен — не JWE

Главная поправка к исходной гипотезе. Для zond используется **непрозрачный
`access_token` из 40 hex-символов**, а не JWE:

```
https://zond.api.2gis.ru/api/1.1/user/ws?...&token=8d456c29…<40 hex>
https://api.auth.2gis.com/2.1/users/me?access_token=<тот же токен>
```

JWE `RSA-OAEP-256` действительно существует, но это **другой** токен и не для
друзей — его выдаёт `passepartout.2gis.com/token?surface=HYBRID_SEARCH` для
поиска. К «Друзьям на карте» он отношения не имеет.

Следствия — всё сильно проще, чем ожидалось:

- токен непрозрачный, парсить/расшифровывать нечего, просто пробрасываем;
- проверка живости: `GET https://api.auth.2gis.com/2.1/users/me?access_token=…`
  → 200 = жив (`python -m bridge.tokens`);
- в HAR нет запроса, выдающего этот токен — он уже был в сессии, значит живёт
  долго (обычный OAuth access token), refresh-эндпоинт не наблюдался.

---

## 2. WebSocket handshake

```
wss://zond.api.2gis.ru/api/1.1/user/ws
    ?appVersion=6.31.0
    &channels=markers,sharing,routes
    &token=<40 hex>
```

| Параметр | Значение |
|---|---|
| статус | `101 Switching Protocols` |
| `Sec-WebSocket-Protocol` | **не используется** (ни в запросе, ни в ответе) |
| `Cookie` | **не отправляется** — авторизация чисто по query-токену |
| `Origin` | `https://2gis.ru` (отправляется; отправляем и мы, на всякий случай) |
| `Sec-WebSocket-Extensions` | `permessage-deflate` (библиотека договорится сама) |
| сервер | nginx, отдаёт `X-Request-Id` |

→ `ZOND_AUTH_MODE` больше не нужен: способ один — query-параметр `token`.

---

## 3. Фреймы: кто начинает разговор

Все 8 фреймов за 5 с записи. **Сервер молчит, пока клиент не пришлёт
`viewportChanged`** — это ключевой момент, без него `initialState` не придёт.

```
->  viewportChanged   {"viewport":{"topLeft":{lon,lat},"bottomRight":{lon,lat}},"zoom":11}
->  bindRoutes        {"sharers":[]}
<-  sharingSubscriptionsInitialState   {"subscriptions":[]}
<-  initialState      {serverTime, profiles[], states[], markerSettings, sharersMarkerSettings}
<-  friendState       {…состояние одного друга…}      # дальше только они
->  bindRoutes        {"sharers":["<id друга>"]}      # при выборе друга в UI
->  viewportChanged   … (при каждом движении/зуме карты)
```

`topLeft` — это северо-запад (бóльшая `lat`, меньшая `lon`).

**Область важна:** сервер фильтрует апдейты по viewport, поэтому мост шлёт
заведомо широкую рамку (`ZOND_VIEWPORT`, по умолчанию с запасом вокруг Томска).

---

## 4. Keepalive — проверено 10-минутным прогоном

Прикладного `ping`/`pong` в протоколе нет. Мост раз в 120 с повторяет
`viewportChanged` (идемпотентный и заведомо валидный фрейм), плюс протокольный
WS-ping раз в 20 с, плюс idle-watchdog на 900 с.

Результат прогона `tools/ws_probe.py --minutes 10` (26.07.2026, 17:30–17:40):

| | |
|---|---|
| длительность сессии | **600 с, ни одного разрыва** |
| исходящие `viewportChanged` | на 0, 120, 240, 360, 480, 600 с — ровно по расписанию |
| максимальный простой по входящим | **209 с** |
| входящих `friendState` | 17 |

Оговорка: входящие ни разу не молчали дольше 209 с, так что 300-секундный
`friendsSocketIdleTimeout` в чистом виде не воспроизводился. Но исходящий
keepalive на 120 с гарантированно держит сокет с точки зрения сервера,
и за 10 минут соединение не дрогнуло.

---

## 5. Структура данных

### `payload.profiles[]` — справочник, приходит один раз

```json
{"id": "<hex32>", "name": "Имя Фамилия", "logo": null,
 "logoTpl": null, "bonuses": null, "sharing": null,
 "isFriend": true, "isChild": false, "isParent": false, "commonGroups": []}
```

### `payload.states[]` — гео

Значения ниже синтетические (координаты — Красная площадь), структура настоящая:

```json
{"id": "<hex32>",
 "lastSeen": 1700000000000,
 "location": {"lat": 55.7558260, "lon": 37.6172999,
              "azimuth": null, "speed": null, "accuracy": 93.774},
 "battery": {"level": 0.53, "isCharging": false},
 "movement": {"status": "stopped", "stoppedAt": 1699999000000,
              "manualAt": null, "unreliableAt": null},
 "locationPlace": {"object": {"id": "<hex>", "regionId": "3"},
                   "status": {"id": "home", "iconUrl": "…",
                              "iconUrlTpl": "…", "lottieUrl": null,
                              "type": "frequent"}}}
```

`profiles[]` и `states[]` параллельны по `id` (в дампе — 5 и 5).

### `movement.status = noGeo` — координаты есть, но устаревшие

Ловушка, которую легко не заметить. Замер (serverTime `1785371533096`):

| Друг | movement | Координаты | Возраст `lastSeen` |
|---|---|---|---|
| 1 | `stopped` | есть | 0 мин |
| 2 | `stopped` | есть | 0 мин |
| **3** | **`noGeo`** | **есть** | **109 мин** |
| 4 | `stopped` | есть | 2 мин |
| 5 | `stopped` | есть | 0 мин |

У друга с `noGeo` весь `movement` — с обнулёнными метками:

```json
{"status": "noGeo", "stoppedAt": null, "manualAt": null, "unreliableAt": null}
```

а `location` заполнен, только `accuracy`, `azimuth` и `speed` равны `null`.

Значит `noGeo` — это **замороженная последняя известная позиция**: друг перестал
делиться геоданными или у него отвалился GPS, а сервер продолжает отдавать
последнюю точку. `locationPlace` при этом сохраняется.

**Почему это важно для HA:** координаты приходят как обычные, поэтому на карте
друг выглядит стоящим на месте в реальном времени, хотя данным может быть
несколько часов. Отличить можно двумя способами — по атрибуту `movement` и по
сенсору `last_seen`. Примеры того, как подсветить это в интерфейсе, —
в [lovelace.md](lovelace.md).

### `friendState` — инкрементальный апдейт

```json
{"type": "friendState",
 "payload": {"id": "<hex32>", "lastSeen": 1785062392437,
             "location": {...}, "battery": {...},
             "movement": {...}, "locationPlace": null}}
```

**Внимание:** `payload` здесь **сам является** состоянием — обёртки `states[]`
или `state` нет. Легко проглядеть и написать парсер, который молча ничего не
находит. Имени друга в апдейте нет, оно только в `initialState`, поэтому
`ZondParser` держит кэш `id -> имя`.

zond шлёт **повторы**: один и тот же `id` с идентичным `lastSeen` приходил
подряд по 3 раза. Мост сравнивает сигнатуру (координаты, батарея, `lastSeen`,
движение, место) с предыдущей и не публикует, если ничего не изменилось —
иначе история в HA засоряется дублями.

### Маппинг в `bridge/models.py`

| Поле | Путь | Тип | Примечание |
|---|---|---|---|
| friend_id | `id` | str | hex32, стабилен → `unique_id` в HA |
| name | `profiles[].name` | str | **только в `initialState`** → парсер кэширует |
| latitude | `location.lat` | float | |
| longitude | `location.lon` | float | |
| accuracy | `location.accuracy` | float / **null** | метры; null → в HA уходит 0 |
| course | `location.azimuth` | float / null | |
| speed | `location.speed` | float / null | |
| battery | `battery.level` | float **0..1** | 0.53 → 53 % |
| charging | `battery.isCharging` | bool | |
| timestamp | `lastSeen` | int, **unix ms** | |
| movement | `movement.status` | str | `stopped`, `noGeo` — см. раздел выше |
| place | `locationPlace.status.id` | str / **null** | `home`, `type: frequent` — это «частое место» *друга*, не твоя зона в HA |

Что может быть `null`: `accuracy`, `azimuth`, `speed`, весь `locationPlace`
(в дампе — у одного из пяти). `location` у друга без шаринга, вероятно, тоже —
такие состояния мост просто не публикует.

---

## 6. Что осталось непроверенным

Закрыто 10-минутным прогоном: тип апдейт-фрейма (`friendState`), стабильность
соединения, дубликаты. Открыто:

- **код закрытия при протухшем токене** — увидим, когда токен реально протухнет;
  мост считает признаком отказа HTTP 401/403 на handshake и коды из
  `AUTH_CLOSE_CODES` (1008, 3000, 4000, 4001, 4003, 4401, 4403);
- **срок жизни токена и refresh-эндпоинт** — запроса, выдающего токен, в HAR нет;
- **значения `movement.status` для движения** — `stopped` и `noGeo` наблюдались,
  а вот как называется состояние «едет», пока не видели: друзья за все прогоны
  не двигались. По полям `manualAt` и `unreliableAt` в том же объекте можно
  предположить существование статусов `manual` и `unreliable`, но это догадка;
- поведение при простое строго дольше 300 с (входящие ни разу не молчали
  дольше 209 с).

---

## 7. Приватность дампа

`2gis.ru.har` содержит:

- **живой `access_token`** — даёт полный доступ к аккаунту (`users/me` отдаёт
  email, дату рождения, привязанный Google-профиль);
- имена, аватары и **домашние координаты пяти друзей**.

Никуда его не выкладывай. Токен разумно ротировать (выйти из аккаунта и зайти
заново) — а для моста взять свежий.
