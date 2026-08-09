from datetime import date
from types import SimpleNamespace

from app.main import dates_for_cycle


def test_cycle_sequence_is_29_30_29():
    settings = SimpleNamespace(
        route=SimpleNamespace(
            check_dates=(
                date(2026, 8, 29),
                date(2026, 8, 30),
                date(2026, 8, 29),
            ),
        ),
    )

    assert dates_for_cycle(settings, 1) == [
        date(2026, 8, 29),
        date(2026, 8, 30),
        date(2026, 8, 29),
    ]
