import logging
from datetime import date

from app.notifier import (
    AlertManager,
    AlertPayload,
    NotificationError,
)


class FakeNotifier:
    def __init__(self):
        self.calls = []

    def send(self, text, attachment=None, *, include_site_button=False):
        self.calls.append((text, attachment, include_site_button))


def test_repeated_activation_does_not_duplicate_initial_alert():
    notifier = FakeNotifier()
    manager = AlertManager(
        notifier, repeat_after_seconds=(), persistent_repeat_minutes=10_000,
        logger=logging.getLogger("test")
    )
    payload = AlertPayload(date(2026, 8, 29), "found", None)
    manager.activate("2026-08-29", payload, initial=True)
    manager.activate("2026-08-29", payload, initial=True)
    manager.deactivate_all()
    assert len(notifier.calls) == 1


def test_reappearance_after_deactivation_sends_new_initial_alert():
    notifier = FakeNotifier()
    manager = AlertManager(
        notifier, repeat_after_seconds=(), persistent_repeat_minutes=10_000,
        logger=logging.getLogger("test")
    )
    payload = AlertPayload(date(2026, 8, 29), "found", None)
    manager.activate("2026-08-29", payload, initial=True)
    manager.deactivate("2026-08-29")
    manager.activate("2026-08-29", payload, initial=True)
    manager.deactivate_all()
    assert len(notifier.calls) == 2


def test_new_manager_session_sends_even_if_state_is_already_available():
    notifier = FakeNotifier()
    manager = AlertManager(
        notifier, repeat_after_seconds=(), persistent_repeat_minutes=10_000,
        logger=logging.getLogger("test")
    )
    payload = AlertPayload(date(2026, 8, 29), "found", None)
    manager.activate("2026-08-29", payload, initial=False)
    manager.deactivate_all()
    assert len(notifier.calls) == 1


def test_telegram_alert_retries_temporary_failure(monkeypatch):
    class FlakyNotifier(FakeNotifier):
        def send(self, text, attachment=None, *, include_site_button=False):
            if not self.calls:
                self.calls.append(("failed", None, False))
                raise NotificationError("temporary")
            super().send(text, attachment, include_site_button=include_site_button)

    notifier = FlakyNotifier()
    monkeypatch.setattr("app.notifier.time.sleep", lambda seconds: None)
    manager = AlertManager(
        notifier, repeat_after_seconds=(), persistent_repeat_minutes=10_000,
        logger=logging.getLogger("test")
    )
    payload = AlertPayload(date(2026, 8, 29), "found", None)
    manager.activate("2026-08-29", payload, initial=True)
    manager.deactivate_all()
    assert len(notifier.calls) == 2
