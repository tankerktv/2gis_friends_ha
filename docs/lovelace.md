# Друзья на карте в интерфейсе HA

Интеграция создаёт обычные `device_tracker` с `source_type: gps`, поэтому
работает со всем, что умеет показывать трекеры, — и со штатной картой, и со
сторонними карточками вроде Location Timeline Card.

## Карта со следом за последние сутки

Штатная карточка `map` умеет рисовать историю перемещений — параметр
`hours_to_show`. Отдельная карточка для «таймлайна» не нужна.

```yaml
type: map
entities:
  - device_tracker.alex_axel
  - device_tracker.friend_two
hours_to_show: 24        # 0 — только текущая точка, без следа
auto_fit: true           # подогнать масштаб под все точки
theme_mode: auto
```

`hours_to_show` берёт данные из `recorder`, поэтому след появится только за
тот период, что уже записан, и не длиннее `purge_keep_days`.

## Чтобы след был длиннее 10 дней

По умолчанию recorder хранит 10 суток. Если нужен трек подлиннее — явно
исключи трекеры из очистки, иначе история будет обрезаться:

```yaml
# configuration.yaml
recorder:
  purge_keep_days: 10
  include:
    entity_globs:
      - device_tracker.*
      - sensor.*_battery
      - sensor.*_last_seen
```

> Осторожно с большими значениями: каждая точка — строка в базе, а друзья
> обновляются часто. 90 дней по пяти друзьям заметно раздуют `home-assistant_v2.db`.

## Карточка на друга

```yaml
type: vertical-stack
cards:
  - type: map
    entities:
      - device_tracker.alex_axel
    hours_to_show: 12
    auto_fit: true
  - type: entities
    entities:
      - entity: device_tracker.alex_axel
        name: Где
      - entity: sensor.alex_axel_battery
        name: Батарея
      - entity: sensor.alex_axel_last_seen
        name: Обновлено
      - type: attribute
        entity: device_tracker.alex_axel
        attribute: movement
        name: Движение
      - type: attribute
        entity: device_tracker.alex_axel
        attribute: place_status
        name: Место по версии 2ГИС
```

## Все друзья без ручного перечисления

Друзья появляются динамически, поэтому удобнее не перечислять их руками.
Через `auto-entities` (ставится из HACS, раздел Frontend):

```yaml
type: custom:auto-entities
card:
  type: map
  hours_to_show: 24
  auto_fit: true
filter:
  include:
    - integration: twogis_friends
      domain: device_tracker
```

Без сторонних карточек то же самое даёт штатная карта, если один раз
перечислить сущности — их немного и меняются они редко.

## Location Timeline Card — выбор дня и маршрут

Штатными средствами HA «выбрать конкретный день и увидеть маршрут» нельзя:
карточка `map` считает окно от «сейчас», а панель History показывает шкалу
состояний без географии. Эту задачу закрывает сторонняя карточка
[konewka17/timeline_card](https://github.com/konewka17/timeline_card) —
у неё есть переключение по дням.

Ставится из HACS, раздел **Frontend** (это плагин интерфейса, отдельный от нашей
интеграции). Если в поиске нет — добавить как custom repository
`konewka17/timeline_card` с категорией Dashboard.

### Совместимость с нашими сущностями

Карточке нужны атрибуты `latitude` и `longitude` на `device_tracker.*` или
`person.*`. У нас они есть: `TrackerEntity` с `source_type: gps` отдаёт их
автоматически, плюс `gps_accuracy`. Данные карточка берёт из `recorder`,
а названия мест — из зон HA (`zone.*`).

### Конфигурация

```yaml
type: custom:location-timeline-card
entity:
  - device_tracker.dmitriy_kotov
  - device_tracker.alex_axel
map_height_px: 320
distance_unit: metric
map_appearance: auto
```

Опция называется `entity` (принимает список), а не `entities`.

Основные параметры, которые стоит знать:

| Опция | По умолчанию | Смысл |
|---|---|---|
| `stay_radius_m` | 75 | радиус, внутри которого точки считаются одной остановкой |
| `min_stay_minutes` | 10 | короче этого — не «остановка», а движение |
| `max_reasonable_speed_kmh` | 300 | фильтр GPS-выбросов |
| `hide_moving` | false | показывать только остановки |
| `update_interval` | 300 | как часто перечитывать, с |
| `osm_api_key` | — | email для Nominatim, чтобы получать адреса вне зон |

### Две особенности именно наших данных

**Разрешение трека — около 4 минут.** По замеру, у стоящего друга состояние
обновляется примерно раз в 4 минуты (см. [findings.md](findings.md)). Для
«где был днём» этого достаточно, для детализации поездки — грубо. Значение
`min_stay_minutes: 10` при таком темпе означает 2–3 точки на остановку.

**`movement: noGeo` карточка примет за длинную остановку.** Когда друг
перестаёт делиться геоданными, 2ГИС продолжает отдавать последнюю известную
точку — наблюдали расхождение почти в два часа. В истории это выглядит как
неподвижность, и таймлайн покажет многочасовое «стоял здесь», хотя данных
просто не было. Отличить можно по атрибуту `movement` или по
`sensor.*_last_seen` — см. раздел ниже.

### Альтернатива

В сообществе есть ещё **Track History Card** — она рисует историю за выбранный
день сразу маршрутом на большой интерактивной карте, с остановками, временем и
расстояниями. Если нужен акцент на карте, а не на списке событий, стоит
посмотреть и её.

## Уведомление, когда друг давно не выходил на связь

`last_seen` — полноценный timestamp-сенсор, поэтому condition пишется прямо:

```yaml
automation:
  - alias: Друг пропал из 2ГИС
    triggers:
      - trigger: template
        value_template: >-
          {{ (now() - states('sensor.alex_axel_last_seen') | as_datetime).total_seconds() > 7200 }}
    actions:
      - action: notify.persistent_notification
        data:
          message: >-
            От Alex нет обновлений с
            {{ states('sensor.alex_axel_last_seen') | as_datetime | as_local }}
```

## Подсветить друзей с устаревшими координатами

2ГИС при `movement: noGeo` продолжает отдавать **последнюю известную** точку —
наблюдали расхождение почти в два часа. На карте такой друг выглядит стоящим
на месте прямо сейчас, поэтому свежесть стоит выводить явно.

Шаблонный сенсор «сколько минут назад обновлялось»:

```yaml
template:
  - sensor:
      - name: "Alex давность"
        unique_id: 2gis_alex_age
        unit_of_measurement: min
        state: >-
          {{ ((now() - states('sensor.alex_axel_last_seen') | as_datetime).total_seconds() / 60) | round(0) }}
        attributes:
          stale: >-
            {{ state_attr('device_tracker.alex_axel', 'movement') == 'noGeo' }}
```

Карточка, где давность видна рядом с точкой:

```yaml
type: vertical-stack
cards:
  - type: map
    entities:
      - device_tracker.alex_axel
    hours_to_show: 12
    auto_fit: true
  - type: markdown
    content: >-
      {% set mv = state_attr('device_tracker.alex_axel', 'movement') %}
      {% set age = ((now() - states('sensor.alex_axel_last_seen') | as_datetime).total_seconds() / 60) | round(0) %}
      {% if mv == 'noGeo' %}
      ⚠️ **Геоданные не приходят.** Последняя точка {{ age }} мин назад.
      {% else %}
      ✅ Обновлено {{ age }} мин назад ({{ mv }}).
      {% endif %}
```

Значения `movement`, которые наблюдались: `stopped` и `noGeo`. Как называется
состояние «в движении», пока неизвестно — за все прогоны друзья не двигались.
Поэтому условие лучше писать на `== 'noGeo'`, а не перечислять «хорошие»
статусы: неизвестное значение тогда не будет ошибочно считаться проблемой.
