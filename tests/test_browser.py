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
    controller._date_card_has_no_flights = lambda value: False
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


def test_no_flights_card_is_unavailable_without_clicking(monkeypatch):
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
    outbound = date(2026, 8, 30)
    calls: list[str] = []
    controller.ensure_search_page = lambda: calls.append("reuse")
    controller._date_card_has_no_flights = lambda value: (
        calls.append("no_flights") or True
    )
    controller._select_date = lambda value: calls.append("unexpected_click")
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
    assert check.result.signals == ("date_card:no_flights",)
    assert calls == ["reuse", "no_flights"]


def test_disabled_date_button_is_treated_as_no_flights():
    class FakeNode:
        def is_visible(self):
            return True

        def is_enabled(self):
            return False

        def inner_text(self):
            return "30 авг, вс"

        def locator(self, selector):
            raise AssertionError("Disabled button should be handled immediately")

    class FakeLocator:
        def __init__(self, nodes):
            self.nodes = nodes

        def count(self):
            return len(self.nodes)

        def nth(self, index):
            return self.nodes[index]

    class FakePage:
        def get_by_role(self, role, name):
            return FakeLocator([FakeNode()])

        def get_by_text(self, pattern):
            return FakeLocator([])

    controller = object.__new__(BrowserController)
    controller.logger = logging.getLogger("test")
    controller.page = FakePage()

    assert controller._date_card_has_no_flights(date(2026, 8, 30))


def test_date_outside_visible_strip_uses_fresh_search(monkeypatch):
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
    outbound = date(2026, 8, 30)
    calls: list[str] = []
    controller.ensure_search_page = lambda: calls.append("reuse")
    controller._date_card_has_no_flights = lambda value: False
    controller._select_date = lambda value: calls.append("missing") or False
    controller._refresh_search_form = lambda value: calls.append("refresh")
    controller._recover_search_context = lambda value: calls.append("refill") or True
    controller._wait_for_result = lambda value: (
        calls.append("result")
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
    assert calls == ["reuse", "missing", "refresh", "refill", "result"]
