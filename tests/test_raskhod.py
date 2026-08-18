"""Тесты подсчёта расхода батареи друзей.

Логика простая, но ошибиться в ней легко и последствия неприятные: накопитель
растёт вечно, и однажды учтённый мусор из него уже не вычесть. Поэтому
проверяются именно границы — что считается расходом, а что нет.
"""

from __future__ import annotations

import pytest

from twogis_friends.models import (
    MIN_SHIRINA_SEKUND,
    OKNO_SEKUND,
    PADENIE_MUSOR,
    SHAG_TOCHKI,
    dobavit_tochku,
    prirost_raskhoda,
    srednee_po_oknu,
    srednee_v_sutki,
)


class TestPrirostRaskhoda:
    def test_padenie_zaschityvaetsia(self) -> None:
        assert prirost_raskhoda(80, 73) == 7

    def test_rost_ne_raskhod(self) -> None:
        """Заряд вырос — это зарядка, а не потребление."""
        assert prirost_raskhoda(40, 95) == 0

    def test_bez_izmeneniy(self) -> None:
        assert prirost_raskhoda(50, 50) == 0

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
        assert prirost_raskhoda(predyduschiy, tekuschiy) == 0

    def test_na_poroge_eschyo_schitaetsia(self) -> None:
        assert prirost_raskhoda(PADENIE_MUSOR, 0) == PADENIE_MUSOR

    def test_za_porogom_otbrasyvaetsia(self) -> None:
        """Падение на 51 пункт разом — почти наверняка разрыв связи."""
        assert prirost_raskhoda(PADENIE_MUSOR + 1, 0) == 0

    def test_porog_nastraivaetsia(self) -> None:
        assert prirost_raskhoda(90, 10, porog=100) == 80

    def test_polnyy_razriad(self) -> None:
        assert prirost_raskhoda(9, 0) == 9


class TestSredneeVSutki:
    def test_rovno_sutki(self) -> None:
        assert srednee_v_sutki(30.0, 86400.0) == pytest.approx(30.0)

    def test_polovina_sutok_udvaivaet(self) -> None:
        assert srednee_v_sutki(30.0, 43200.0) == pytest.approx(60.0)

    def test_troe_sutok(self) -> None:
        assert srednee_v_sutki(90.0, 3 * 86400.0) == pytest.approx(30.0)

    def test_pervye_sekundy_ne_dayut_beskonechnosti(self) -> None:
        """Без нижней границы делителя тут была бы астрономическая цифра."""
        assert srednee_v_sutki(5.0, 1.0) == pytest.approx(5.0 / 0.04)

    def test_nulevoe_vremia_ne_delit_na_nol(self) -> None:
        assert srednee_v_sutki(0.0, 0.0) == 0.0

    def test_nichego_ne_izrashodovano(self) -> None:
        assert srednee_v_sutki(0.0, 86400.0) == 0.0


class TestDobavitTochku:
    def test_pervaia_otmetka_dobavliaetsia(self) -> None:
        assert dobavit_tochku([], 1000.0, 5.0) == [(1000.0, 5.0)]

    def test_chasche_shaga_ne_dobavliaet(self) -> None:
        """Отметки нужны редкие: текущее значение в расчёт входит отдельно."""
        est = [(1000.0, 5.0)]
        assert dobavit_tochku(est, 1000.0 + 600, 7.0) == est

    def test_posle_shaga_dobavliaet(self) -> None:
        est = [(1000.0, 5.0)]
        novye = dobavit_tochku(est, 1000.0 + SHAG_TOCHKI, 7.0)
        assert novye == [(1000.0, 5.0), (1000.0 + SHAG_TOCHKI, 7.0)]

    def test_staroe_vypadaet_iz_okna(self) -> None:
        staraia = 1000.0
        svezhaia = staraia + OKNO_SEKUND + 1
        est = [(staraia, 5.0), (svezhaia, 9.0)]
        novye = dobavit_tochku(est, svezhaia + SHAG_TOCHKI, 10.0)
        assert (staraia, 5.0) not in novye

    def test_hotia_by_odna_otmetka_ostayotsia(self) -> None:
        """Даже если все отметки древние — считать надо от чего-то."""
        est = [(0.0, 5.0)]
        novye = dobavit_tochku(est, 10 * OKNO_SEKUND, 5.0, shag=1e9)
        assert len(novye) == 1


class TestSredneePoOknu:
    def test_pustoe_okno(self) -> None:
        assert srednee_po_oknu([], 1000.0, 5.0) is None

    def test_vremia_ne_proshlo(self) -> None:
        assert srednee_po_oknu([(1000.0, 5.0)], 1000.0, 5.0) is None

    def test_sutki_rovno(self) -> None:
        assert srednee_po_oknu([(0.0, 10.0)], 86400.0, 40.0) == pytest.approx(30.0)

    def test_nedelia(self) -> None:
        okno = [(0.0, 0.0)]
        assert srednee_po_oknu(okno, 7 * 86400.0, 210.0) == pytest.approx(30.0)

    def test_schitaet_ot_starshei_otmetki(self) -> None:
        """Базой служит самая старая отметка в окне, а не последняя."""
        okno = [(0.0, 10.0), (43200.0, 25.0)]
        assert srednee_po_oknu(okno, 86400.0, 40.0) == pytest.approx(30.0)

    def test_ne_zalipaet_v_otlichie_ot_pozhiznennogo(self) -> None:
        """Смысл всей затеи: окно показывает нынешний режим, а не средний
        за всю жизнь. Год по 80 в сутки, затем месяц по 40."""
        god = 365 * 86400.0
        mesiats = 30 * 86400.0
        pozhiznenno = srednee_v_sutki(365 * 80 + 30 * 40, god + mesiats)
        # В окне остался только последний месяц.
        po_oknu = srednee_po_oknu([(god, 365 * 80.0)], god + mesiats,
                                  365 * 80 + 30 * 40.0)
        assert pozhiznenno == pytest.approx(77.0, abs=0.5)
        assert po_oknu == pytest.approx(40.0)

    def test_uzkoe_okno_nichego_ne_pokazyvaet(self) -> None:
        """Две минуты окна и скачок накопителя давали 250 %/сут — мусор."""
        assert srednee_po_oknu([(0.0, 20.0)], 120.0, 30.0) is None

    def test_na_granitse_uzhe_schitaet(self) -> None:
        assert srednee_po_oknu([(0.0, 0.0)], MIN_SHIRINA_SEKUND,
                               10.0) is not None
