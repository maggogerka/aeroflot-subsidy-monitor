from datetime import date
from types import SimpleNamespace

from app.configuration import PassengerGroup, RouteConfig
from app.main import checks_for_cycle, dates_for_cycle


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


def test_round_trip_always_adds_independent_return_check():
    route = RouteConfig(
        subsidy_program="Молодёжь и пенсионеры",
        origin="Москва",
        destination="Владивосток",
        check_dates=(date(2026, 9, 3), date(2026, 9, 4)),
        passengers=(PassengerGroup("Молодёжь", 1),),
        trip_type="round_trip",
        return_date=date(2026, 9, 7),
    )
    settings = SimpleNamespace(
        route=route,
        monitoring=SimpleNamespace(refresh_mode="cycle"),
    )

    checks = checks_for_cycle(settings)

    assert [item.leg for item in checks] == ["outbound", "outbound", "return"]
    assert [item.refresh_page for item in checks] == [True, False, True]
    assert checks[-1].travel_date == date(2026, 9, 7)
    assert checks[-1].route.origin == "Владивосток"
    assert checks[-1].route.destination == "Москва"
    assert checks[-1].route.trip_type == "one_way"
    assert checks[-1].route.return_date is None
