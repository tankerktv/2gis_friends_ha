"""Разбор фреймов zond в FriendPosition.

Протокол разобран по HAR веб-версии и 10-минутному прогону, подробности —
в docs/findings.md. Кратко:

* ``initialState``  — ``payload.profiles[]`` (id -> имя) и ``payload.states[]``;
  приходит один раз в ответ на ``viewportChanged``.
* ``friendState``   — апдейт одного друга, где ``payload`` **сам является**
  состоянием (без обёртки ``states[]``). Имени в нём нет — берём из кэша.

Парсер ориентируется на структуру, а не на имя типа, поэтому переживёт
переименование событий на стороне 2ГИС.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_LOGGER = logging.getLogger(__name__)

#: типы фреймов, в которых заведомо нет координат
NO_GEO_TYPES = frozenset({
    "sharingSubscriptionsInitialState",
    "ping",
    "pong",
    "error",
})

#: значение ``movement.status``, которым 2ГИС помечает устаревшие координаты.
#: Друг перестал делиться геопозицией, но последняя известная точка продолжает
#: приходить как обычная — на карте это выглядит как «стоит на месте сейчас».
MOVEMENT_NO_GEO = "noGeo"

#: значение ``locationPlace.status.id`` для дома друга. В снятом дампе это
#: единственное встретившееся значение; словарь целиком неизвестен, поэтому
#: сравниваем именно с ним, а не разбираем все возможные варианты.
PLACE_HOME = "home"


@dataclass(frozen=True)
class FriendPosition:
    """Нормализованное состояние друга."""

    friend_id: str
    latitude: float
    longitude: float
    name: str | None = None
    battery: int | None = None
    charging: bool | None = None
    accuracy: float | None = None
    speed: float | None = None
    course: float | None = None
    last_seen: datetime | None = None
    movement: str | None = None
    place: str | None = None
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def signature(self) -> tuple:
        """Для отсева повторов: zond шлёт один и тот же friendState по 2-3 раза."""
        return (
            self.latitude, self.longitude, self.battery, self.charging,
            self.accuracy, self.last_seen, self.movement, self.place,
        )

    @property
    def is_stale(self) -> bool | None:
        """Устарели ли координаты.

        ``None`` — 2ГИС ничего не сказал про движение, а не «свежие».
        Разница существенная: ``False`` утверждало бы то, чего мы не знаем.
        """
        if self.movement is None:
            return None
        return self.movement == MOVEMENT_NO_GEO

    @property
    def is_at_home(self) -> bool | None:
        """Дома ли друг — по данным 2ГИС, а не по зонам Home Assistant.

        Это «частое место» самого друга, его собственный дом. С зонами твоего
        Home Assistant не связано никак: друг может быть у себя дома и при этом
        быть ``not_home`` в трекере.

        ``None`` — 2ГИС не прислал место (в дампе так у одного из пяти).

        **Осторожно:** ``locationPlace`` сохраняется и при ``noGeo``. Если друг
        перестал делиться, находясь дома, значение останется ``True`` сколь
        угодно долго. Отличать по :attr:`is_stale`.
        """
        if self.place is None:
            return None
        return self.place == PLACE_HOME


def friends_ready_for_entities(data: Mapping[str, FriendPosition]) -> list[str]:
    """Кому уже можно заводить сущности в Home Assistant.

    Только тем, чьё имя известно, — и это не придирка, а защита от порчи,
    которую потом не исправить.

    Имя друга приходит **только** в ``initialState``, в списке профилей.
    Если новый идентификатор появился в ``friendState`` раньше (так бывает,
    когда человек переустановил приложение), имени в этот момент нет.
    Home Assistant, не получив имени устройства, строит идентификаторы
    сущностей от названия записи интеграции — и получается ``sensor.battery``
    или, того хуже, трекер, названный по совсем другому человеку.

    **Идентификатор сущности назначается один раз при создании.** Имя
    устройства подтянется через минуту, когда приедет профиль, а кривой
    идентификатор останется навсегда. Именно так 13.08.2026 появился
    ``device_tracker.dmitrii_kotov_2`` у Михаила Котомина.

    Ждать почти не приходится: ``initialState`` приходит в ответ на
    ``viewportChanged``, а его шлёт keepalive каждые несколько минут.
    """
    return [
        friend_id
        for friend_id, position in data.items()
        if position is not None and position.name
    ]


#: Падение заряда больше этого за один замер считаем мусором, а не расходом.
#: Значение взято из накопителей телефона и часов в Home Assistant, чтобы
#: цифры друзей можно было сравнивать с ними напрямую.
PADENIE_MUSOR = 50


def prirost_raskhoda(
    predyduschiy: int | None,
    tekuschiy: int | None,
    porog: int = PADENIE_MUSOR,
) -> int:
    """На сколько процентов упал заряд между двумя замерами.

    Расход — это только **падение**. Рост означает зарядку и в расход не идёт.

    :param predyduschiy: заряд на прошлом замере, ``None`` — замера не было
    :param tekuschiy: заряд сейчас
    :param porog: падение больше этого отбрасывается как мусор
    :return: сколько процентов убыло; ``0``, если считать нечего

    **Сравнивать нужно с сохранённым значением, а не с предыдущим состоянием
    сущности.** Разница принципиальная: у друга связь пропадает регулярно, и
    на каждом таком разрыве предыдущего состояния просто нет. Накопитель
    коляски, построенный на ``trigger.from_state``, из-за этого потерял 98 %
    расхода — разрывов набегало 25 в сутки.

    Порог отбрасывает мусор, но за него же платим: если друг перестал делиться
    геопозицией на полдня, реальное падение больше порога уйдёт мимо счёта.
    Поэтому вызывающая сторона должна такие случаи **записывать в журнал**, а
    не проглатывать молча.
    """
    if predyduschiy is None or tekuschiy is None:
        return 0
    ubylo = predyduschiy - tekuschiy
    if ubylo <= 0 or ubylo > porog:
        return 0
    return ubylo


def srednee_v_sutki(vsego: float, sekund: float, minimum_sutok: float = 0.04) -> float:
    """Средний расход в сутки: накопленное, делённое на прошедшее время.

    :param vsego: сколько процентов израсходовано за всё время наблюдения
    :param sekund: сколько секунд идёт наблюдение
    :param minimum_sutok: нижняя граница делителя

    Делитель ограничен снизу намеренно. В первые минуты наблюдения деление на
    почти ноль давало бы «расход 4000 % в сутки» — число формально верное и
    совершенно бесполезное. Нижняя граница в сотые доли суток (около часа)
    держит первые показания в разумных пределах, а через сутки она уже ни на
    что не влияет.
    """
    sutok = max(sekund / 86400.0, minimum_sutok)
    return vsego / sutok


#: Ширина окна, по которому считается средний расход.
OKNO_SEKUND = 7 * 86400

#: Пока окно уже этого, среднее не показывается вовсе.
#:
#: Нижней границы делителя для этого мало: она защищает от деления на
#: почти ноль, но не от большого числителя. Проверено на живом обновлении
#: 18.08.2026 — окно шириной две минуты, накопитель за время простоя HA
#: подрос на 10 %, и сенсор показал 250 %/сут. Формально верно,
#: практически — мусор.
MIN_SHIRINA_SEKUND = 3 * 3600.0

#: Чаще этого отметки в окно не добавляются. Час даёт 168 отметок на неделю —
#: этого с запасом хватает для суточного среднего, а список остаётся коротким
#: и дёшево переживает перезапуск.
SHAG_TOCHKI = 3600.0


def dobavit_tochku(
    tochki: list[tuple[float, float]],
    moment: float,
    vsego: float,
    shag: float = SHAG_TOCHKI,
    okno: float = OKNO_SEKUND,
) -> list[tuple[float, float]]:
    """Кладёт отметку в окно и выбрасывает всё, что старше окна.

    :param tochki: отметки ``(момент, накоплено)``, по возрастанию времени
    :param moment: сейчас, в секундах
    :param vsego: сколько израсходовано к этому моменту
    :param shag: не добавлять отметки чаще этого
    :param okno: ширина окна
    :return: новый список отметок

    Отметки — это опорные точки, а не полная история: текущее значение в
    расчёте берётся отдельно, поэтому редкие отметки точности не портят.
    """
    novye = list(tochki)
    if not novye or moment - novye[-1][0] >= shag:
        novye.append((moment, vsego))
    granica = moment - okno
    vnutri = [t for t in novye if t[0] >= granica]
    # Хотя бы одна отметка должна остаться, иначе считать будет не от чего.
    return vnutri or novye[-1:]


def srednee_po_oknu(
    tochki: list[tuple[float, float]],
    moment: float,
    vsego: float,
    minimum_sutok: float = 0.04,
) -> float | None:
    """Средний расход в сутки по скользящему окну.

    :return: расход в сутки или ``None``, если считать не от чего

    Отличие от :func:`srednee_v_sutki` — в том, от чего считается. Там
    делится всё накопленное за всё время наблюдения, и такое среднее тем
    инертнее, чем дольше живёт: при наблюдении в год смена режима вдвое
    сдвинет цифру лишь на треть за месяц. Здесь старое выпадает из окна,
    поэтому метрика не застывает никогда.

    Делится на **фактическую** ширину окна, а не на семь суток. Поэтому
    значение верно с первого дня, пока окно ещё не заполнилось.

    Пока окно уже :data:`MIN_SHIRINA_SEKUND`, возвращается ``None``:
    на коротком промежутке любой скачок накопителя даёт бессмысленно
    большую цифру, и честнее не показывать ничего.
    """
    if not tochki:
        return None
    nachalo, bylo = tochki[0]
    sekund = moment - nachalo
    if sekund < MIN_SHIRINA_SEKUND:
        return None
    return srednee_v_sutki(vsego - bylo, sekund, minimum_sutok)


def podobrat_pary_pereezda(
    ustroystva: Mapping[str, str],
    zhivye: Mapping[str, str],
) -> dict[str, str]:
    """Кого на кого переносить при смене идентификатора: ``{старый: новый}``.

    Идентификатор друга в 2ГИС не вечен — он меняется, когда человек
    переустанавливает приложение или заходит под другим аккаунтом. Для
    интеграции это новый друг: заводится второе устройство, а прежнее остаётся
    навсегда. Снаружи человек выглядит задвоившимся, и вся его история
    разрывается надвое.

    Здесь ищется пара «осиротевшее устройство — новичок без устройства»
    по совпадению имени.

    **Осторожность важнее полноты.** Ошибка сливает истории двух разных людей,
    а это хуже, чем оставить дубль: дубль виден и чинится, а перепутанные
    истории уже не разобрать. Поэтому пара берётся, только когда она
    единственная с обеих сторон:

    * имя совпадает **точно** — ни регистра, ни пробелов не прощаем;
    * среди осиротевших это имя встречается один раз;
    * среди новичков — тоже один раз.

    Тёзки, безымянные и всё неоднозначное просто не переносятся.

    :param ustroystva: заведённые устройства, ``friend_id -> имя``
    :param zhivye: кого 2ГИС присылает сейчас, ``friend_id -> имя``
    """
    osirotevshie: dict[str, list[str]] = {}
    for friend_id, imya in ustroystva.items():
        if friend_id in zhivye or not imya:
            continue
        osirotevshie.setdefault(imya, []).append(friend_id)

    # Кандидатом считается ЛЮБОЙ живой с таким именем, даже если устройство у
    # него уже заведено. Иначе переезд не сработал бы в самом частом случае:
    # дубль уже создан, человек его увидел и только потом обновился.
    zhivye_po_imeni: dict[str, list[str]] = {}
    for friend_id, imya in zhivye.items():
        if not imya:
            continue
        zhivye_po_imeni.setdefault(imya, []).append(friend_id)

    pary: dict[str, str] = {}
    for imya, stariki in osirotevshie.items():
        kandidaty = zhivye_po_imeni.get(imya) or []
        if len(stariki) == 1 and len(kandidaty) == 1:
            pary[stariki[0]] = kandidaty[0]
    return pary


def can_remove_device(device_ids: set[str], hub_id: str, live_ids: set[str]) -> bool:
    """Можно ли убрать устройство из Home Assistant.

    Лежит здесь, а не в ``__init__.py``, по той же причине, что и остальная
    логика: сюда не тянется Home Assistant, поэтому решение покрывается
    тестами. В интеграции остаётся только достать идентификаторы и позвать.

    :param device_ids: идентификаторы устройства в нашем домене
    :param hub_id: идентификатор служебного устройства самой интеграции
    :param live_ids: те, о ком 2ГИС присылает данные прямо сейчас
    """
    if hub_id in device_ids:
        # Служебное устройство держит состояние связи. Удалив его, пользователь
        # лишится единственного признака того, что соединение живо.
        return False
    # Живого друга удалять бессмысленно: следующее же обновление создаст
    # устройство заново, и человек решит, что удаление не работает.
    return not (device_ids & live_ids)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


def _to_datetime(value: Any) -> datetime | None:
    """lastSeen приходит в unix-миллисекундах."""
    n = _num(value)
    if n is None:
        return None
    if n > 1e11:      # миллисекунды
        n /= 1000
    try:
        return datetime.fromtimestamp(n, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _battery_percent(value: Any) -> int | None:
    """zond отдаёт долю 0..1 (0.53 -> 53 %)."""
    n = _num(value)
    if n is None:
        return None
    if 0 <= n <= 1:
        n *= 100
    return max(0, min(100, round(n)))


def _valid_coords(lat: float | None, lon: float | None) -> bool:
    return (
        lat is not None and lon is not None
        and -90 <= lat <= 90 and -180 <= lon <= 180
        and not (lat == 0 and lon == 0)
    )


def _looks_like_state(node: Any) -> bool:
    """Похож ли объект на элемент states[] — есть id и вложенный location."""
    return (
        isinstance(node, dict)
        and bool(node.get("id"))
        and isinstance(node.get("location"), dict)
    )


class ZondParser:
    """Разбирает фреймы и помнит имена друзей между ними."""

    def __init__(self) -> None:
        self.names: dict[str, str] = {}
        self._unknown_types: set[str] = set()

    def feed(self, frame: Any) -> list[FriendPosition]:
        if not isinstance(frame, dict):
            return []
        frame_type = frame.get("type")
        payload = frame.get("payload")
        if not isinstance(payload, dict):
            return []

        for profile in payload.get("profiles") or []:
            if isinstance(profile, dict) and profile.get("id") and profile.get("name"):
                self.names[str(profile["id"])] = str(profile["name"])

        states = payload.get("states")
        if states is None and isinstance(payload.get("state"), dict):
            states = [payload["state"]]
        if states is None and _looks_like_state(payload):
            states = [payload]          # friendState
        if states is None:
            if frame_type not in NO_GEO_TYPES and frame_type not in self._unknown_types:
                self._unknown_types.add(str(frame_type))
                _LOGGER.debug("Фрейм без состояний, тип %s: %s", frame_type, str(frame)[:300])
            return []

        return [pos for state in states if (pos := self._to_position(state)) is not None]

    def _to_position(self, state: Any) -> FriendPosition | None:
        if not _looks_like_state(state):
            return None

        location = state["location"]
        lat, lon = _num(location.get("lat")), _num(location.get("lon"))
        if not _valid_coords(lat, lon):
            # друг есть, но координатами не делится — сущность не создаём
            return None

        battery = state.get("battery") or {}
        movement = state.get("movement") or {}
        place = state.get("locationPlace")
        place_status = None
        if isinstance(place, dict):
            place_status = (place.get("status") or {}).get("id")

        friend_id = str(state["id"])
        charging = battery.get("isCharging")
        return FriendPosition(
            friend_id=friend_id,
            latitude=lat,
            longitude=lon,
            name=self.names.get(friend_id),
            battery=_battery_percent(battery.get("level")),
            charging=charging if isinstance(charging, bool) else None,
            accuracy=_num(location.get("accuracy")),
            speed=_num(location.get("speed")),
            course=_num(location.get("azimuth")),
            last_seen=_to_datetime(state.get("lastSeen")),
            movement=movement.get("status"),
            place=place_status,
        )
