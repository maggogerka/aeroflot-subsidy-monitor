from __future__ import annotations

import json
import sys
from datetime import datetime

from app.browser import BrowserController
from app.configuration import ConfigurationError, load_settings
from app.detector import DetectionState, classify_html
from app.logging_setup import configure_logging


def main() -> int:
    try:
        settings = load_settings()
    except ConfigurationError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    logger = configure_logging(settings.logs_dir)
    passengers = ", ".join(
        f"{item.label}: {item.count}" for item in settings.route.passengers
    )
    return_description = (
        "обратный билет не нужен"
        if settings.route.return_date is None
        else f"обратно {settings.route.return_date:%d.%m.%Y}"
    )
    print(
        "\nПервоначальная ручная настройка\n"
        "1. Примите cookies и вручную пройдите CAPTCHA, если она появится.\n"
        f"2. Выберите программу «{settings.route.subsidy_program}».\n"
        f"3. Задайте {settings.route.origin} → {settings.route.destination}.\n"
        f"4. Пассажиры: {passengers}; класс: {settings.route.cabin}.\n"
        "5. Выберите одну из дат туда: "
        + ", ".join(item.strftime("%d.%m.%Y") for item in settings.route.check_dates)
        + f"; {return_description}.\n"
        "6. Нажмите «Найти» и дождитесь ленты дат.\n"
        "Программа не нажимает кнопки покупки и не обходит CAPTCHA.\n"
    )
    with BrowserController(settings, logger) as browser:
        try:
            browser.ensure_search_page()
        except Exception as exc:
            logger.error("Не удалось открыть сайт: %s", type(exc).__name__)
            logger.debug("Ошибка открытия сайта", exc_info=True)
            return 1
        input("Когда страница будет полностью настроена, нажмите Enter здесь...")
        page = browser.page
        assert page
        visible_text = browser._body_text()  # ограниченная проверка видимого текста
        first_date = settings.route.check_dates[0]
        result = classify_html(
            page.content(),
            first_date,
            settings.route.return_date,
            settings.route.origin,
            settings.route.destination,
            settings.route.passenger_count,
        )
        checks = {
            "origin": settings.route.origin.lower() in visible_text.lower(),
            "destination": settings.route.destination.lower() in visible_text.lower(),
            "result_or_unavailable": result.state
            in {DetectionState.AVAILABLE, DetectionState.UNAVAILABLE},
        }
        screenshot = browser.screenshot("initial-setup")
        report = {
            "generated_at": datetime.now().astimezone().isoformat(),
            "url": page.url,
            "checks": checks,
            "detection": result.state.value,
            "signals": list(result.signals),
            "screenshot": screenshot.name if screenshot else None,
            "privacy": "Cookies, localStorage, поля ввода и полный DOM не сохранялись.",
        }
        report_path = settings.artifacts_dir / (
            f"{datetime.now():%Y%m%d-%H%M%S}-initial-setup-report.json"
        )
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    confirmed = [name for name, ok in checks.items() if ok]
    unconfirmed = [name for name, ok in checks.items() if not ok]
    print("\nПодтверждено: " + (", ".join(confirmed) or "ничего"))
    print("Не удалось подтвердить: " + (", ".join(unconfirmed) or "ничего"))
    print(f"Скриншот: {screenshot or 'не сохранён'}")
    print(f"Диагностический отчёт: {report_path}")
    if unconfirmed:
        print(
            "Настройка сохранена в профиле, но перед мониторингом проверьте "
            "неподтверждённые пункты. При необходимости запустите --diagnose."
        )
        return 1
    print("Первоначальная настройка подтверждена и сохранена.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
