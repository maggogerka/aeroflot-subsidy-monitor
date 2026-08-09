from datetime import date
import logging
from types import SimpleNamespace

from app.browser import BrowserController
from app.detector import DetectionResult, DetectionState


def test_real_aeroflot_short_date_label_is_supported():
    pattern = BrowserController._date_label_pattern(date(2026, 8, 29))
    assert pattern.search("29 авг, сб")
    assert pattern.search("29 августа 2026")


def test_date_pattern_matches_card_with_price():
    pattern = BrowserController._date_pattern(date(2026, 8, 30))
    assert pattern.search("30 авг, вс от 24 679 ₽")


def test_each_check_refreshes_and_refills_form(monkeypatch):
    controller = object.__new__(BrowserController)
    controller.logger = logging.getLogger("test")
    controller.settings = SimpleNamespace(
        route=SimpleNamespace(
            return_date=None,
            origin="Москва",
            destination="Владивосток",
            passenger_count=1,
        ),
        safety=SimpleNamespace(save_unknown_html=False),
    )
    controller.page = SimpleNamespace(content=lambda: "<html></html>")
    calls: list[tuple[str, date]] = []
    outbound = date(2026, 8, 30)

    controller._refresh_search_form = lambda value: calls.append(
        ("refresh", value)
    )
    controller._recover_search_context = lambda value: (
        calls.append(("refill", value)) or True
    )
    controller._wait_for_result = lambda value: (
        calls.append(("result", value))
        or DetectionResult(DetectionState.UNAVAILABLE, ("negative:test",))
    )
    controller.screenshot = lambda label: None
    controller.save_limited_diagnostic = lambda value: None
    monkeypatch.setattr(
        "app.browser.classify_html",
        lambda *args, **kwargs: DetectionResult(
            DetectionState.UNKNOWN, ("initial_form",)
        ),
    )

    check = controller.check_date(outbound)

    assert check.result.state == DetectionState.UNAVAILABLE
    assert calls == [
        ("refresh", outbound),
        ("refill", outbound),
        ("result", outbound),
    ]


def test_followup_check_reuses_results_page_without_refresh(monkeypatch):
    controller = object.__new__(BrowserController)
    controller.logger = logging.getLogger("test")
    controller.settings = SimpleNamespace(
        route=SimpleNamespace(
            return_date=None,
            origin="Москва",
            destination="Владивосток",
            passenger_count=1,
        ),
        safety=SimpleNamespace(save_unknown_html=False),
    )
    controller.page = SimpleNamespace(
        content=lambda: "<html></html>",
        wait_for_timeout=lambda milliseconds: None,
    )
    calls: list[tuple[str, date]] = []
    outbound = date(2026, 8, 30)
    controller.ensure_search_page = lambda: calls.append(
        ("reuse", outbound)
    )
    controller._select_date = lambda value: (
        calls.append(("select", value)) or True
    )
    controller._wait_for_result = lambda value: (
        calls.append(("result", value))
        or DetectionResult(DetectionState.UNAVAILABLE, ("negative:test",))
    )
    controller.screenshot = lambda label: None
    controller.save_limited_diagnostic = lambda value: None
    monkeypatch.setattr(
        "app.browser.classify_html",
        lambda *args, **kwargs: DetectionResult(
            DetectionState.UNKNOWN, ("old_result",)
        ),
    )

    check = controller.check_date(outbound, refresh_page=False)

    assert check.result.state == DetectionState.UNAVAILABLE
    assert calls == [
        ("reuse", outbound),
        ("select", outbound),
        ("result", outbound),
    ]
