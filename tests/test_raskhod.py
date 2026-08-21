"""Тесты подсчёта расхода батареи друзей.

Логика простая, но ошибиться в ней легко и последствия неприятные: накопитель
растёт вечно, и однажды учтённый мусор из него уже не вычесть. Поэтому
проверяются именно границы — что считается расходом, а что нет.
"""

from __future__ import annotations

import pytest

from twogis_friends.models import (
    MIN_WINDOW_SECONDS,
    WINDOW_SECONDS,
    JUNK_DROP_THRESHOLD,
    POINT_STEP_SECONDS,
    add_window_point,
    drain_increment,
    windowed_average_per_day,
    average_per_day,
)


class TestDrainIncrement:
    def test_padenie_zaschityvaetsia(self) -> None:
        assert drain_increment(80, 73) == 7

    def test_rost_ne_raskhod(self) -> None:
        """Заряд вырос — это зарядка, а не потребление."""
        assert drain_increment(40, 95) == 0

    def test_bez_izmeneniy(self) -> None:
        assert drain_increment(50, 50) == 0

    @pytest.mark.parametrize(
        ("predyduschiy", "tekuschiy"),
        [(None, 50), (50, None), (None, None)],
    )
    def test_neizvestnyy_zamer_ne_schitaetsia(
        self, predyduschiy: int | None, tekuschiy: int | None
    ) -> None:
        """Пока сравнивать не с чем, расход равен нулю, а не всему заряду.

        Это первый замер после появления друга или после перезапуска, когда
        сохранённого значения ещё нет.
        """
        assert drain_increment(predyduschiy, tekuschiy) == 0

    def test_na_poroge_eschyo_schitaetsia(self) -> None:
        assert drain_increment(JUNK_DROP_THRESHOLD, 0) == JUNK_DROP_THRESHOLD

    def test_za_porogom_otbrasyvaetsia(self) -> None:
        """Падение на 51 пункт разом — почти наверняка разрыв связи."""
        assert drain_increment(JUNK_DROP_THRESHOLD + 1, 0) == 0

    def test_porog_nastraivaetsia(self) -> None:
        assert drain_increment(90, 10, threshold=100) == 80

    def test_polnyy_razriad(self) -> None:
        assert drain_increment(9, 0) == 9


class TestAveragePerDay:
    def test_rovno_sutki(self) -> None:
        assert average_per_day(30.0, 86400.0) == pytest.approx(30.0)

    def test_polovina_sutok_udvaivaet(self) -> None:
        assert average_per_day(30.0, 43200.0) == pytest.approx(60.0)

    def test_troe_sutok(self) -> None:
        assert average_per_day(90.0, 3 * 86400.0) == pytest.approx(30.0)

    def test_pervye_sekundy_ne_dayut_beskonechnosti(self) -> None:
        """Без нижней границы делителя тут была бы астрономическая цифра."""
        assert average_per_day(5.0, 1.0) == pytest.approx(5.0 / 0.04)

    def test_nulevoe_vremia_ne_delit_na_nol(self) -> None:
        assert average_per_day(0.0, 0.0) == 0.0

    def test_nichego_ne_izrashodovano(self) -> None:
        assert average_per_day(0.0, 86400.0) == 0.0


class TestAddWindowPoint:
    def test_pervaia_otmetka_dobavliaetsia(self) -> None:
        assert add_window_point([], 1000.0, 5.0) == [(1000.0, 5.0)]

    def test_chasche_shaga_ne_dobavliaet(self) -> None:
        """Отметки нужны редкие: текущее значение в расчёт входит отдельно."""
        est = [(1000.0, 5.0)]
        assert add_window_point(est, 1000.0 + 600, 7.0) == est

    def test_posle_shaga_dobavliaet(self) -> None:
        est = [(1000.0, 5.0)]
        novye = add_window_point(est, 1000.0 + POINT_STEP_SECONDS, 7.0)
        assert novye == [(1000.0, 5.0), (1000.0 + POINT_STEP_SECONDS, 7.0)]

    def test_staroe_vypadaet_iz_okna(self) -> None:
        staraia = 1000.0
        svezhaia = staraia + WINDOW_SECONDS + 1
        est = [(staraia, 5.0), (svezhaia, 9.0)]
        novye = add_window_point(est, svezhaia + POINT_STEP_SECONDS, 10.0)
        assert (staraia, 5.0) not in novye

    def test_hotia_by_odna_otmetka_ostayotsia(self) -> None:
        """Даже если все отметки древние — считать надо от чего-то."""
        est = [(0.0, 5.0)]
        novye = add_window_point(est, 10 * WINDOW_SECONDS, 5.0, step=1e9)
        assert len(novye) == 1


class TestWindowedAveragePerDay:
    def test_pustoe_okno(self) -> None:
        assert windowed_average_per_day([], 1000.0, 5.0) is None

    def test_vremia_ne_proshlo(self) -> None:
        assert windowed_average_per_day([(1000.0, 5.0)], 1000.0, 5.0) is None

    def test_sutki_rovno(self) -> None:
        assert windowed_average_per_day([(0.0, 10.0)], 86400.0, 40.0) == pytest.approx(30.0)

    def test_nedelia(self) -> None:
        window = [(0.0, 0.0)]
        assert windowed_average_per_day(window, 7 * 86400.0, 210.0) == pytest.approx(30.0)

    def test_schitaet_ot_starshei_otmetki(self) -> None:
        """Базой служит самая старая отметка в окне, а не последняя."""
        window = [(0.0, 10.0), (43200.0, 25.0)]
        assert windowed_average_per_day(window, 86400.0, 40.0) == pytest.approx(30.0)

    def test_ne_zalipaet_v_otlichie_ot_pozhiznennogo(self) -> None:
        """Смысл всей затеи: окно показывает нынешний режим, а не средний
        за всю жизнь. Год по 80 в сутки, затем месяц по 40."""
        god = 365 * 86400.0
        mesiats = 30 * 86400.0
        pozhiznenno = average_per_day(365 * 80 + 30 * 40, god + mesiats)
        # В окне остался только последний месяц.
        po_oknu = windowed_average_per_day([(god, 365 * 80.0)], god + mesiats,
                                  365 * 80 + 30 * 40.0)
        assert pozhiznenno == pytest.approx(77.0, abs=0.5)
        assert po_oknu == pytest.approx(40.0)

    def test_uzkoe_okno_nichego_ne_pokazyvaet(self) -> None:
        """Две минуты окна и скачок накопителя давали 250 %/сут — мусор."""
        assert windowed_average_per_day([(0.0, 20.0)], 120.0, 30.0) is None

    def test_na_granitse_uzhe_schitaet(self) -> None:
        assert windowed_average_per_day([(0.0, 0.0)], MIN_WINDOW_SECONDS,
                               10.0) is not None
