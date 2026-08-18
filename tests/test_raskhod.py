"""Тесты подсчёта расхода батареи друзей.

Логика простая, но ошибиться в ней легко и последствия неприятные: накопитель
растёт вечно, и однажды учтённый мусор из него уже не вычесть. Поэтому
проверяются именно границы — что считается расходом, а что нет.
"""

from __future__ import annotations

import pytest

from twogis_friends.models import (
    PADENIE_MUSOR,
    prirost_raskhoda,
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
