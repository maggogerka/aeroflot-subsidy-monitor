from datetime import date
from pathlib import Path

import pytest

from app.detector import DetectionState, classify_html


FIXTURES = Path(__file__).parent / "fixtures"
OUTBOUND = date(2026, 8, 29)
RETURN = date(2026, 9, 7)


def detect(name: str, passenger_count: int = 1):
    return classify_html(
        (FIXTURES / name).read_text(encoding="utf-8"),
        OUTBOUND,
        RETURN,
        "Москва",
        "Владивосток",
        passenger_count,
    )


def test_negative_message_is_unavailable():
    assert detect("unavailable.html").state == DetectionState.UNAVAILABLE


def test_real_subsidized_card_is_available():
    result = detect("available.html")
    assert result.state == DetectionState.AVAILABLE
    assert result.price == "7 400 ₽"


def test_calendar_price_does_not_create_false_positive():
    assert detect("calendar_price_only.html").state == DetectionState.UNAVAILABLE


@pytest.mark.parametrize("fixture", ["blocked.html", "captcha.html"])
def test_blocking_is_detected(fixture: str):
    assert detect(fixture).state == DetectionState.BLOCKED


def test_unknown_structure_is_unknown():
    assert detect("unknown.html").state == DetectionState.UNKNOWN


def test_regular_flights_without_subsidy_are_not_available():
    assert detect("regular_flights.html").state == DetectionState.UNKNOWN


def test_two_passengers_require_explicit_confirmation():
    assert detect("available.html", passenger_count=2).state == DetectionState.UNKNOWN


def test_hidden_negative_text_is_ignored():
    html = (FIXTURES / "available.html").read_text(encoding="utf-8").replace(
        "<main",
        '<div hidden>На выбранную дату субсидированные билеты не найдены</div><main',
    )
    result = classify_html(
        html, OUTBOUND, RETURN, "Москва", "Владивосток", 1
    )
    assert result.state == DetectionState.AVAILABLE


def test_signals_from_different_cards_are_not_aggregated():
    html = """
    <div>Москва Владивосток 29 августа 2026 7 сентября 2026</div>
    <main role="region" aria-label="Результаты">
      <article aria-label="рейс"><p>Москва Владивосток 29 августа 2026</p>
        <p>Субсидированный тариф</p></article>
      <article aria-label="рейс"><p>Москва Владивосток 29 августа 2026</p>
        <p>25 000 ₽</p><button>Выбрать</button></article>
    </main>
    """
    result = classify_html(
        html, OUTBOUND, RETURN, "Москва", "Владивосток", 1
    )
    assert result.state == DetectionState.UNKNOWN


def test_browser_verification_page_is_blocked():
    html = """
    <html><head><title>Доступ к сайту временно ограничен владельцем
    веб-ресурса.</title></head><body>
    <h1>Сейчас будет выполнена проверка вашего веб-браузера.</h1>
    </body></html>
    """
    result = classify_html(
        html, OUTBOUND, RETURN, "Москва", "Владивосток", 1
    )
    assert result.state == DetectionState.BLOCKED


def test_available_without_return_date_after_confirmed_date_click():
    html = (FIXTURES / "available.html").read_text(encoding="utf-8")
    html = html.replace("29 августа 2026", "").replace("7 сентября 2026", "")
    result = classify_html(
        html,
        OUTBOUND,
        None,
        "Москва",
        "Владивосток",
        1,
        selected_date_confirmed=True,
    )
    assert result.state == DetectionState.AVAILABLE
