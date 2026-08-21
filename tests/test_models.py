"""Тесты разбора фреймов zond.

Это самое ценное место для тестов во всём проекте. Протокол не документирован
и разобран наблюдением, поэтому 2ГИС вправе его менять. Ломается такое молча:
парсер вернёт пустой список, координатор не получит позиций, а в Home Assistant
это выглядит как «друзья перестали двигаться» — без единой ошибки в журнале.

Координаты в тестах намеренно вымышленные (Красная площадь и круглые числа).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from twogis_friends.models import (
    FriendPosition,
    ZondParser,
    _battery_percent,
    _num,
    _to_datetime,
    _valid_coords,
    can_remove_device,
    friends_ready_for_entities,
    match_migration_pairs,
    unattempted_pairs,
)

MOSCOW_LAT = 55.7539
MOSCOW_LON = 37.6208


def make_state(friend_id="f1", lat=MOSCOW_LAT, lon=MOSCOW_LON, **extra):
    """Собирает элемент states[] в том виде, в каком его шлёт zond."""
    state = {
        "id": friend_id,
        "location": {"lat": lat, "lon": lon},
    }
    state.update(extra)
    return state


# --- вспомогательные функции ------------------------------------------------


class TestNum:
    def test_числа_проходят(self):
        assert _num(5) == 5.0
        assert _num(3.5) == 3.5
        assert _num(-1) == -1.0

    def test_строки_разбираются(self):
        assert _num("42") == 42.0
        assert _num("  3.5 ") == 3.5

    def test_мусор_даёт_none(self):
        assert _num("не число") is None
        assert _num(None) is None
        assert _num([1]) is None
        assert _num({}) is None

    def test_bool_не_число(self):
        """В Python bool наследует int, и True прошёл бы как 1.0.

        Для нас это важно: battery.isCharging — булево, и если бы оно
        просочилось в числовой разбор, заряд «True» стал бы 100 процентами.
        """
        assert _num(True) is None
        assert _num(False) is None


class TestBatteryPercent:
    def test_доля_переводится_в_проценты(self):
        """zond отдаёт 0..1, а Home Assistant ждёт 0..100."""
        assert _battery_percent(0.53) == 53
        assert _battery_percent(0.0) == 0
        assert _battery_percent(1.0) == 100

    def test_готовые_проценты_не_трогаются(self):
        assert _battery_percent(53) == 53
        assert _battery_percent(87) == 87

    def test_выход_за_границы_обрезается(self):
        assert _battery_percent(150) == 100
        assert _battery_percent(-20) == 0

    def test_округление(self):
        assert _battery_percent(0.535) == 54  # 53.5 -> 54
        assert _battery_percent(0.534) == 53

    def test_нет_данных(self):
        assert _battery_percent(None) is None
        assert _battery_percent("нет") is None


class TestToDatetime:
    def test_миллисекунды(self):
        """lastSeen приходит в unix-миллисекундах."""
        result = _to_datetime(1_700_000_000_000)
        assert result == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)

    def test_секунды_тоже_понимаются(self):
        result = _to_datetime(1_700_000_000)
        assert result == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)

    def test_всегда_с_часовым_поясом(self):
        """Наивное время в Home Assistant приводит к сдвигам истории."""
        assert _to_datetime(1_700_000_000_000).tzinfo is timezone.utc

    def test_мусор_не_роняет(self):
        assert _to_datetime(None) is None
        assert _to_datetime("вчера") is None
        assert _to_datetime(10 ** 20) is None


class TestValidCoords:
    def test_нормальные(self):
        assert _valid_coords(MOSCOW_LAT, MOSCOW_LON)
        assert _valid_coords(-33.9, 151.2)

    def test_нулевой_остров_отвергается(self):
        """0,0 — это «координат нет», а не точка в Атлантике."""
        assert not _valid_coords(0, 0)

    def test_за_границами_диапазона(self):
        assert not _valid_coords(91, 0)
        assert not _valid_coords(0, 181)
        assert not _valid_coords(-91, 0)

    def test_отсутствующие(self):
        assert not _valid_coords(None, MOSCOW_LON)
        assert not _valid_coords(MOSCOW_LAT, None)

    def test_границы_допустимы(self):
        assert _valid_coords(90, 180)
        assert _valid_coords(-90, -180)


# --- разбор фреймов ---------------------------------------------------------


class TestInitialState:
    def test_профили_и_состояния(self):
        parser = ZondParser()
        positions = parser.feed({
            "type": "initialState",
            "payload": {
                "profiles": [
                    {"id": "f1", "name": "Аня"},
                    {"id": "f2", "name": "Борис"},
                ],
                "states": [
                    make_state("f1", 55.75, 37.62),
                    make_state("f2", 55.76, 37.63),
                ],
            },
        })

        assert len(positions) == 2
        assert {p.friend_id for p in positions} == {"f1", "f2"}
        assert {p.name for p in positions} == {"Аня", "Борис"}

    def test_поля_состояния_разбираются(self):
        parser = ZondParser()
        (position,) = parser.feed({
            "type": "initialState",
            "payload": {
                "profiles": [{"id": "f1", "name": "Аня"}],
                "states": [make_state(
                    "f1",
                    location={"lat": 55.75, "lon": 37.62, "accuracy": 12.5,
                              "speed": 3.2, "azimuth": 180.0},
                    battery={"level": 0.53, "isCharging": True},
                    movement={"status": "walking"},
                    lastSeen=1_700_000_000_000,
                    locationPlace={"status": {"id": "home"}},
                )],
            },
        })

        assert position.latitude == 55.75
        assert position.longitude == 37.62
        assert position.accuracy == 12.5
        assert position.speed == 3.2
        assert position.course == 180.0
        assert position.battery == 53
        assert position.charging is True
        assert position.movement == "walking"
        assert position.place == "home"
        assert position.last_seen == datetime(2023, 11, 14, 22, 13, 20, tzinfo=timezone.utc)


class TestFriendState:
    """friendState — апдейт одного друга, payload САМ является состоянием.

    Обёртки states[] в нём нет, и парсер опознаёт такой фрейм по структуре:
    есть id и вложенный location. Из-за этой особенности апдейты когда-то
    молча терялись, поэтому случай проверяется отдельно.
    """

    def test_payload_сам_является_состоянием(self):
        parser = ZondParser()
        (position,) = parser.feed({
            "type": "friendState",
            "payload": make_state("f1", 55.80, 37.70),
        })

        assert position.friend_id == "f1"
        assert position.latitude == 55.80

    def test_имя_берётся_из_кэша(self):
        """В friendState имени нет — оно пришло раньше, в initialState."""
        parser = ZondParser()
        parser.feed({
            "type": "initialState",
            "payload": {"profiles": [{"id": "f1", "name": "Аня"}], "states": []},
        })

        (position,) = parser.feed({
            "type": "friendState",
            "payload": make_state("f1"),
        })
        assert position.name == "Аня"

    def test_без_кэша_имя_пустое(self):
        parser = ZondParser()
        (position,) = parser.feed({
            "type": "friendState",
            "payload": make_state("f1"),
        })
        assert position.name is None

    def test_имя_переживает_много_фреймов(self):
        parser = ZondParser()
        parser.feed({
            "type": "initialState",
            "payload": {"profiles": [{"id": "f1", "name": "Аня"}], "states": []},
        })
        for _ in range(10):
            (position,) = parser.feed({
                "type": "friendState",
                "payload": make_state("f1"),
            })
        assert position.name == "Аня"


class TestSingularState:
    def test_обёртка_state_в_единственном_числе(self):
        parser = ZondParser()
        (position,) = parser.feed({
            "type": "чтоТоНовое",
            "payload": {"state": make_state("f1")},
        })
        assert position.friend_id == "f1"


class TestНеваляжныеФреймы:
    """Ничто из этого не должно ронять парсер: сокет живёт часами."""

    @pytest.mark.parametrize("frame", [
        None, "строка", 42, [], {},
        {"type": "ping"},
        {"type": "x", "payload": None},
        {"type": "x", "payload": "не словарь"},
        {"type": "x", "payload": []},
    ])
    def test_пустой_результат_без_исключений(self, frame):
        assert ZondParser().feed(frame) == []

    def test_состояние_без_координат_пропускается(self):
        """Друг есть в списке, но геопозицией не делится — сущность не нужна."""
        parser = ZondParser()
        assert parser.feed({
            "type": "initialState",
            "payload": {"states": [{"id": "f1", "location": {}}]},
        }) == []

    def test_нулевые_координаты_пропускаются(self):
        parser = ZondParser()
        assert parser.feed({
            "type": "friendState",
            "payload": make_state("f1", 0, 0),
        }) == []

    def test_состояние_без_id_пропускается(self):
        parser = ZondParser()
        assert parser.feed({
            "type": "initialState",
            "payload": {"states": [{"location": {"lat": 55.7, "lon": 37.6}}]},
        }) == []

    def test_годные_состояния_не_страдают_от_негодных(self):
        """Один битый друг не должен утопить остальных."""
        parser = ZondParser()
        positions = parser.feed({
            "type": "initialState",
            "payload": {"states": [
                {"мусор": True},
                make_state("f1"),
                {"id": "f2", "location": {}},
                make_state("f3"),
            ]},
        })
        assert {p.friend_id for p in positions} == {"f1", "f3"}

    def test_профиль_без_имени_не_ломает(self):
        parser = ZondParser()
        parser.feed({
            "type": "initialState",
            "payload": {"profiles": [{"id": "f1"}, {"name": "безымянный"}], "states": []},
        })
        assert parser.names == {}


class TestSignature:
    """Сигнатура нужна координатору: zond шлёт один friendState по 2-3 раза."""

    def test_одинаковые_данные_дают_одинаковую_сигнатуру(self):
        a = FriendPosition("f1", 55.75, 37.62, battery=50)
        b = FriendPosition("f1", 55.75, 37.62, battery=50)
        assert a.signature == b.signature

    def test_имя_на_сигнатуру_не_влияет(self):
        """Имя может подъехать позже — это не повод считать позицию новой."""
        a = FriendPosition("f1", 55.75, 37.62, name=None)
        b = FriendPosition("f1", 55.75, 37.62, name="Аня")
        assert a.signature == b.signature

    @pytest.mark.parametrize("field,value", [
        ("latitude", 55.76),
        ("longitude", 37.63),
        ("battery", 49),
        ("charging", True),
        ("accuracy", 5.0),
        ("movement", "driving"),
        ("place", "work"),
    ])
    def test_изменение_значимого_поля_меняет_сигнатуру(self, field, value):
        base = dict(friend_id="f1", latitude=55.75, longitude=37.62, battery=50)
        a = FriendPosition(**base)
        b = FriendPosition(**{**base, field: value})
        assert a.signature != b.signature


# --- признаки, из которых собираются бинарные сенсоры ------------------------


def position(**kwargs):
    return FriendPosition(friend_id="f1", latitude=MOSCOW_LAT, longitude=MOSCOW_LON, **kwargs)


class TestIsStale:
    """Главный признак: координаты пришли, но им может быть несколько часов."""

    def test_noGeo_значит_устарели(self):
        assert position(movement="noGeo").is_stale is True

    @pytest.mark.parametrize("movement", ["stopped", "walking", "driving"])
    def test_обычное_движение_значит_свежие(self, movement):
        assert position(movement=movement).is_stale is False

    def test_без_данных_неизвестно_а_не_свежие(self):
        """`False` утверждало бы то, чего мы не знаем, — а на этом строят автоматизации."""
        assert position().is_stale is None

    def test_регистр_имеет_значение(self):
        """2ГИС шлёт ровно `noGeo`; совпадение по другому регистру было бы догадкой."""
        assert position(movement="nogeo").is_stale is False


class TestIsAtHome:
    """Дом самого друга по данным 2ГИС — не зона Home Assistant."""

    def test_home_значит_дома(self):
        assert position(place="home").is_at_home is True

    def test_другое_место_значит_не_дома(self):
        assert position(place="work").is_at_home is False

    def test_без_места_неизвестно(self):
        """`locationPlace` приходит не всегда — в снятом дампе его нет у одного из пяти."""
        assert position().is_at_home is None

    def test_регистр_имеет_значение(self):
        """Сравниваем точно, как у `movement`.

        Наблюдалось ровно одно значение — `home`. Совпадение по другому регистру
        было бы догадкой о словаре, которого мы не знаем: если 2ГИС однажды
        начнёт слать `Home`, лучше увидеть «не дома» и разобраться, чем тихо
        подстроиться и не заметить, что протокол поменялся.
        """
        assert position(place="Home").is_at_home is False

    def test_сохраняется_при_устаревших_данных(self):
        """Осознанное поведение, а не недосмотр.

        2ГИС продолжает отдавать `locationPlace` и после того, как друг
        перестал делиться. Значение остаётся последним известным — ровно так же,
        как остаются координаты. Признаком негодности служит `is_stale`,
        и именно поэтому обе сущности имеет смысл заводить только вместе.
        """
        stale_at_home = position(place="home", movement="noGeo")
        assert stale_at_home.is_at_home is True
        assert stale_at_home.is_stale is True

    def test_разбирается_из_фрейма(self):
        parser = ZondParser()
        (pos,) = parser.feed({
            "type": "friendState",
            "payload": make_state(
                "f1",
                locationPlace={"status": {"id": "home", "type": "frequent"}},
                movement={"status": "stopped"},
            ),
        })
        assert pos.is_at_home is True
        assert pos.is_stale is False


class TestCanRemoveDevice:
    """Уборка устройств друзей, пропавших из списка 2ГИС.

    Понадобилась после реального случая: у друга сменился идентификатор на
    стороне 2ГИС, интеграция завела второе устройство, а первое осталось
    навсегда — человек в интерфейсе задвоился.
    """

    HUB = "01JABCDEF"

    def test_пропавшего_друга_можно_убрать(self):
        assert can_remove_device({"старый_id"}, self.HUB, {"живой_id"}) is True

    def test_живого_друга_убрать_нельзя(self):
        """Иначе следующее обновление создаст его заново.

        Пользователь решит, что удаление сломано, и будет прав: устройство
        вернётся само через несколько секунд.
        """
        assert can_remove_device({"живой_id"}, self.HUB, {"живой_id"}) is False

    def test_служебное_устройство_убрать_нельзя(self):
        """На нём висит состояние связи — единственный признак, что она жива."""
        assert can_remove_device({self.HUB}, self.HUB, {"живой_id"}) is False

    def test_служебное_защищено_даже_когда_друзей_нет(self):
        assert can_remove_device({self.HUB}, self.HUB, set()) is False

    def test_когда_данных_нет_вовсе_можно_убрать_любого_друга(self):
        """Данных нет — значит, и удерживать устройство нечем."""
        assert can_remove_device({"кто_то"}, self.HUB, set()) is True

    def test_устройство_без_наших_идентификаторов(self):
        """Чужое устройство до нас дойти не должно, но и падать не будем."""
        assert can_remove_device(set(), self.HUB, {"живой_id"}) is True


class TestFriendsReadyForEntities:
    """Кому можно заводить сущности.

    Сторож против порчи, которую нельзя исправить. Идентификатор сущности
    Home Assistant назначает один раз, при создании, отталкиваясь от имени
    устройства. Если имени в этот момент нет, он берёт название записи
    интеграции — и у друга появляется трекер, названный по другому человеку.
    Именно так 13.08.2026 возник `device_tracker.dmitrii_kotov_2` у Михаила.
    """

    def make(self, friend_id="f1", name="Аня"):
        return FriendPosition(friend_id=friend_id, latitude=MOSCOW_LAT,
                              longitude=MOSCOW_LON, name=name)

    def test_друг_с_именем_готов(self):
        assert friends_ready_for_entities({"f1": self.make()}) == ["f1"]

    def test_без_имени_ждёт(self):
        """Имя приходит только в initialState — подождём его."""
        assert friends_ready_for_entities({"f1": self.make(name=None)}) == []

    def test_пустое_имя_тоже_не_годится(self):
        assert friends_ready_for_entities({"f1": self.make(name="")}) == []

    def test_безымянный_не_мешает_остальным(self):
        data = {"f1": self.make("f1", "Аня"),
                "f2": self.make("f2", None),
                "f3": self.make("f3", "Борис")}
        assert sorted(friends_ready_for_entities(data)) == ["f1", "f3"]

    def test_ожидание_не_навсегда(self):
        """Как только имя приехало, друг становится готов."""
        data = {"f1": self.make(name=None)}
        assert friends_ready_for_entities(data) == []
        data["f1"] = self.make(name="Аня")
        assert friends_ready_for_entities(data) == ["f1"]

    def test_пусто_и_none(self):
        assert friends_ready_for_entities({}) == []
        assert friends_ready_for_entities({"f1": None}) == []


class TestMatchMigrationPairs:
    """Подбор пар при смене идентификатора друга.

    Самое опасное место во всей интеграции: ошибка здесь сливает истории двух
    разных людей. Дубль виден и чинится, перепутанные истории — уже нет.
    Поэтому проверок на отказ тут больше, чем на срабатывание.
    """

    def test_обычный_переезд(self):
        """Друг пропал под старым идентификатором и появился под новым."""
        assert match_migration_pairs(
            {"старый": "Аня", "боря": "Борис"},
            {"новый": "Аня", "боря": "Борис"},
        ) == {"старый": "новый"}

    def test_двое_переезжают_разом(self):
        assert match_migration_pairs(
            {"а1": "Аня", "б1": "Борис"},
            {"а2": "Аня", "б2": "Борис"},
        ) == {"а1": "а2", "б1": "б2"}

    def test_никто_не_переезжал(self):
        assert match_migration_pairs(
            {"а": "Аня", "б": "Борис"},
            {"а": "Аня", "б": "Борис"},
        ) == {}

    def test_новый_друг_это_не_переезд(self):
        """Появился человек, которого раньше не было, — переносить нечего."""
        assert match_migration_pairs(
            {"а": "Аня"},
            {"а": "Аня", "в": "Виктор"},
        ) == {}

    def test_друг_просто_ушёл(self):
        """Пропал и никто не появился — устройство остаётся как есть."""
        assert match_migration_pairs(
            {"а": "Аня", "б": "Борис"},
            {"а": "Аня"},
        ) == {}

    # --- отказы: здесь важнее НЕ сработать -----------------------------------

    def test_тёзки_среди_пропавших_не_переносятся(self):
        """Две Ани ушли, одна Аня пришла — кто из них кто, неизвестно."""
        assert match_migration_pairs(
            {"а1": "Аня", "а2": "Аня"},
            {"а3": "Аня"},
        ) == {}

    def test_тёзки_среди_новичков_не_переносятся(self):
        assert match_migration_pairs(
            {"а1": "Аня"},
            {"а2": "Аня", "а3": "Аня"},
        ) == {}

    def test_разные_имена_не_пара(self):
        """Совпадение по времени — не основание считать людей одним человеком."""
        assert match_migration_pairs(
            {"а": "Аня"},
            {"б": "Борис"},
        ) == {}

    def test_регистр_имеет_значение(self):
        """Сравниваем точно. «аня» и «Аня» могут быть разными людьми."""
        assert match_migration_pairs({"а1": "Аня"}, {"а2": "аня"}) == {}

    def test_пробелы_имеют_значение(self):
        assert match_migration_pairs({"а1": "Аня"}, {"а2": "Аня "}) == {}

    def test_безымянные_не_переносятся(self):
        """Без имени сопоставлять не по чему."""
        assert match_migration_pairs({"а1": None}, {"а2": None}) == {}
        assert match_migration_pairs({"а1": ""}, {"а2": ""}) == {}
        assert match_migration_pairs({"а1": "Аня"}, {"а2": None}) == {}

    def test_пустые_входные_данные(self):
        assert match_migration_pairs({}, {}) == {}
        assert match_migration_pairs({}, {"а": "Аня"}) == {}
        assert match_migration_pairs({"а": "Аня"}, {}) == {}

    def test_живой_друг_не_считается_осиротевшим(self):
        """Тот, кто есть в текущем списке, никуда не переезжает."""
        assert match_migration_pairs(
            {"а": "Аня"},
            {"а": "Аня", "а2": "Аня"},
        ) == {}

    def test_настоящий_случай_21_08_2026(self):
        """Два реальных переезда, случившихся у владельца."""
        pary = match_migration_pairs(
            {
                "bd4b0dc71a1d4ed1bbee2d6bf42dc7c5": "Alex Axel",
                "8087d13508264d04a013fcb5beec16fb": "Дмитрий Котов",
                "eddb7d95e64b428488469f0062684a75": "Виктория Котова",
                "9927f2129f2f400b868d3dd70d17ec15": "Михаил Котомин",
                "28cb10e0fadd4511a1df5bb73a01a13e": "Эллис Лис",
            },
            {
                "481ea1de45d74518b56158e8287894d2": "Alex Axel",
                "2fdb17ce62f74e72861b325fc507ab9b": "Дмитрий Котов",
                "eddb7d95e64b428488469f0062684a75": "Виктория Котова",
                "9927f2129f2f400b868d3dd70d17ec15": "Михаил Котомин",
                "28cb10e0fadd4511a1df5bb73a01a13e": "Эллис Лис",
            },
        )
        assert pary == {
            "bd4b0dc71a1d4ed1bbee2d6bf42dc7c5": "481ea1de45d74518b56158e8287894d2",
            "8087d13508264d04a013fcb5beec16fb": "2fdb17ce62f74e72861b325fc507ab9b",
        }

    def test_дубль_уже_создан_переезд_всё_равно_нужен(self):
        """Самый частый случай: человек увидел дубль и только потом обновился.

        Ранняя версия считала кандидатом только того, у кого ещё нет
        устройства, — и в этом случае не срабатывала вовсе, то есть была
        бесполезна ровно тогда, когда нужна.
        """
        assert match_migration_pairs(
            {"старый": "Аня", "новый": "Аня"},
            {"новый": "Аня"},
        ) == {"старый": "новый"}

    def test_дубль_создан_у_двоих(self):
        assert match_migration_pairs(
            {"а1": "Аня", "а2": "Аня_новая", "б1": "Борис", "б2": "Борис"},
            {"а2": "Аня_новая", "б2": "Борис"},
        ) == {"б1": "б2"}


class TestUnattemptedPairs:
    """Память сторожа о том, какие переезды он уже пробовал.

    Без неё неудачный переезд крутил бы перезагрузки записи по кругу: пара
    находится снова при каждом обновлении координатора.
    """

    def test_ничего_не_пробовали(self):
        assert unattempted_pairs({"а1": "а2"}, {}) == {"а1": "а2"}

    def test_эту_пару_уже_пробовали(self):
        assert unattempted_pairs({"а1": "а2"}, {"а1": "а2"}) == {}

    def test_второй_переезд_того_же_друга_не_блокируется(self):
        """Помним пару целиком, а не старый идентификатор.

        Друг может сменить идентификатор второй раз. Неудача с ``а1 -> а2``
        не должна мешать переезду ``а1 -> а3``.
        """
        assert unattempted_pairs({"а1": "а3"}, {"а1": "а2"}) == {"а1": "а3"}

    def test_из_нескольких_остаётся_только_новая(self):
        assert unattempted_pairs(
            {"а1": "а2", "б1": "б2"},
            {"а1": "а2"},
        ) == {"б1": "б2"}

    def test_пар_нет(self):
        assert unattempted_pairs({}, {"а1": "а2"}) == {}

    def test_исходные_словари_не_меняются(self):
        pary = {"а1": "а2"}
        probovali = {"б1": "б2"}
        unattempted_pairs(pary, probovali)
        assert pary == {"а1": "а2"}
        assert probovali == {"б1": "б2"}
