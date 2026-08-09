import pytest

from app.configure import parse_dates, parse_passengers


def test_parse_dates_keeps_order_and_duplicates():
    assert parse_dates("2026-08-29, 2026-08-30;2026-08-29") == [
        "2026-08-29",
        "2026-08-30",
        "2026-08-29",
    ]


def test_parse_passenger_groups():
    assert parse_passengers("Взрослый=1;Ребёнок=2") == {
        "Взрослый": 1,
        "Ребёнок": 2,
    }


@pytest.mark.parametrize(
    "value",
    ["", "Молодёжь", "Молодёжь=0", "Молодёжь=x"],
)
def test_invalid_passenger_groups_are_rejected(value):
    with pytest.raises((ValueError, TypeError)):
        parse_passengers(value)
