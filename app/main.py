from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from app.browser import BrowserCheck, BrowserController
from app.configuration import ConfigurationError, RouteConfig, Settings, load_settings
from app.detector import DetectionState
from app.logging_setup import configure_logging
from app.notifier import (
    AlertManager,
    AlertPayload,
    NotificationError,
    SoundAlarm,
    TelegramNotifier,
    format_available_message,
)
from app.state import MonitorState, StateStore, utc_now_iso


ERROR_STATES = {
    DetectionState.UNKNOWN,
    DetectionState.BLOCKED,
    DetectionState.SESSION_EXPIRED,
    DetectionState.NETWORK_ERROR,
}
NETWORK_BACKOFF_SECONDS = (60, 120, 300, 900)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Монитор субсидированных билетов Аэрофлота"
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--once", action="store_true", help="один цикл и выход")
    modes.add_argument(
        "--diagnose", action="store_true", help="безопасная диагностика DOM и выход"
    )
    modes.add_argument(
        "--test-alert", action="store_true", help="тест Telegram и выход"
    )
    modes.add_argument(
        "--test-alarm",
        action="store_true",
        help="громкая локальная тревога до ручного подтверждения",
    )
    parser.add_argument(
        "--config", default=None, help="путь к config.yaml (по умолчанию ./config.yaml)"
    )
    parser.add_argument(
        "--verbose", action="store_true", help="подробный вывод в консоль"
    )
    return parser


def cleanup_old_artifacts(settings: Settings, logger: logging.Logger) -> None:
    """Удаляет только обычные файлы непосредственно внутри artifacts."""
    root = settings.artifacts_dir.resolve()
    root.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - settings.safety.artifact_retention_days * 86400
    removed = 0
    for path in root.iterdir():
        try:
            if (
                path.is_file()
                and not path.is_symlink()
                and path.resolve().parent == root
                and path.stat().st_mtime < cutoff
            ):
                path.unlink()
                removed += 1
        except OSError:
            logger.debug("Не удалось проверить/удалить старый артефакт %s", path)
    if removed:
        logger.info("Удалено старых диагностических артефактов: %d", removed)


def safe_notify(
    notifier: TelegramNotifier,
    logger: logging.Logger,
    text: str,
    attachment: Path | None = None,
) -> None:
    try:
        notifier.send(text, attachment)
    except NotificationError as exc:
        logger.error("Ошибка Telegram: %s", exc)


def parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def update_health(
    settings: Settings,
    state: MonitorState,
    store: StateStore,
    notifier: TelegramNotifier,
    logger: logging.Logger,
    *,
    successful_this_cycle: bool,
) -> None:
    now = datetime.now(timezone.utc)
    last_success = parse_timestamp(state.last_successful_check)
    if last_success:
        if not state.last_heartbeat:
            # Отсчёт heartbeat начинается с первой успешной проверки, без
            # лишнего сообщения сразу после запуска.
            state.last_heartbeat = utc_now_iso()
        heartbeat_due = successful_this_cycle and now - (
            parse_timestamp(state.last_heartbeat)
            or datetime.min.replace(tzinfo=timezone.utc)
        ) >= timedelta(hours=settings.monitoring.heartbeat_hours)
        if heartbeat_due:
            local_time = last_success.astimezone().strftime("%d.%m.%Y %H:%M:%S %Z")
            safe_notify(
                notifier,
                logger,
                f"✅ Монитор работает, последняя успешная проверка: {local_time}",
            )
            state.last_heartbeat = utc_now_iso()
    baseline = last_success or parse_timestamp(state.monitor_started_at)
    stale = bool(
        baseline
        and now - baseline
        >= timedelta(minutes=settings.monitoring.stale_monitor_minutes)
    )
    if stale and not state.stale_warning_sent:
        safe_notify(
            notifier,
            logger,
            "⚠️ Монитор давно не выполнял успешных проверок. "
            "Проверьте интернет, браузер и CAPTCHA.",
        )
        state.stale_warning_sent = True
    store.save(state)


def dates_for_cycle(settings: Settings, cycle: int) -> list[date]:
    del cycle  # Последовательность теперь задаётся напрямую и может иметь повторы.
    return list(settings.route.check_dates)


@dataclass(frozen=True)
class CheckRequest:
    route: RouteConfig
    travel_date: date
    refresh_page: bool
    leg: str


def return_leg_route(route: RouteConfig) -> RouteConfig | None:
    """Создаёт независимый поиск обратного направления без оформления билета."""
    if route.trip_type != "round_trip" or route.return_date is None:
        return None
    return replace(
        route,
        origin=route.destination,
        destination=route.origin,
        check_dates=(route.return_date,),
        trip_type="one_way",
        return_date=None,
    )


def checks_for_cycle(settings: Settings) -> list[CheckRequest]:
    checks = [
        CheckRequest(
            route=settings.route,
            travel_date=travel_date,
            refresh_page=(
                settings.monitoring.refresh_mode == "date" or index == 0
            ),
            leg="outbound",
        )
        for index, travel_date in enumerate(settings.route.check_dates)
    ]
    reverse = return_leg_route(settings.route)
    if reverse is not None:
        checks.append(
            CheckRequest(
                route=reverse,
                travel_date=reverse.check_dates[0],
                refresh_page=True,
                leg="return",
            )
        )
    return checks


def route_check_key(settings: Settings, outbound: date) -> str:
    return "|".join(
        (
            settings.route.subsidy_program,
            settings.route.origin,
            settings.route.destination,
            outbound.isoformat(),
            settings.route.return_date.isoformat()
            if settings.route.return_date
            else "one-way",
        )
    )


def _error_notice(
    state: DetectionState, outbound: date, signals: tuple[str, ...]
) -> str:
    label = {
        DetectionState.UNKNOWN: "не удалось уверенно распознать результаты",
        DetectionState.BLOCKED: "обнаружена CAPTCHA или блокировка",
        DetectionState.SESSION_EXPIRED: "похоже, сессия истекла",
        DetectionState.NETWORK_ERROR: "сетевая или браузерная ошибка",
    }[state]
    return (
        f"⚠️ Монитор: {label} при проверке {outbound:%d.%m.%Y}.\n"
        f"Признаки: {', '.join(signals) or 'нет'}.\n"
        "Автоматического обхода CAPTCHA нет; при необходимости откройте браузер."
    )


def process_check(
    settings: Settings,
    outbound: date,
    check: BrowserCheck,
    state: MonitorState,
    store: StateStore,
    notifier: TelegramNotifier,
    alerts: AlertManager,
    sound: SoundAlarm,
    logger: logging.Logger,
) -> None:
    result = check.result
    key = route_check_key(settings, outbound)
    previous_detection = state.date_state(key).last_detection
    logger.info(
        "cycle=%d outbound=%s state=%s signals=%s duration=%.2fs errors=%d",
        state.cycle,
        outbound.isoformat(),
        result.state.value,
        ",".join(result.signals),
        check.duration_seconds,
        state.consecutive_errors,
    )

    if result.state == DetectionState.AVAILABLE:
        appeared = state.record_availability(key, True, result.state.value)
        recovered = state.record_success()
        payload = AlertPayload(
            outbound_date=outbound,
            text=format_available_message(settings, outbound, result.price),
            screenshot=check.screenshot,
        )
        new_alert_session = not alerts.is_active(key)
        if appeared or new_alert_session:
            # Локальная тревога запускается до сетевой отправки Telegram.
            sound.start()
        alerts.activate(key, payload, initial=appeared)
        if appeared:
            state.date_state(key).last_alert_at = utc_now_iso()
        if recovered:
            safe_notify(notifier, logger, "✅ Монитор восстановился после ошибок.")
    elif result.state == DetectionState.UNAVAILABLE:
        state.record_availability(key, False, result.state.value)
        alerts.deactivate(key)
        recovered = state.record_success()
        if recovered:
            safe_notify(notifier, logger, "✅ Монитор восстановился после ошибок.")
    else:
        count = state.record_error()
        # UNKNOWN отправляется при смене состояния; прочие критические состояния — сразу.
        should_send = (
            result.state != DetectionState.UNKNOWN
            or previous_detection != DetectionState.UNKNOWN.value
        )
        state.date_state(key).last_detection = result.state.value
        state.date_state(key).last_checked_at = utc_now_iso()
        if should_send:
            safe_notify(
                notifier,
                logger,
                _error_notice(result.state, outbound, result.signals),
                check.screenshot or check.diagnostic_file,
            )
        if count >= 2 and not state.error_warning_sent:
            safe_notify(
                notifier,
                logger,
                f"⚠️ Уже {count} последовательные ошибки. "
                "Проверьте открытый браузер и соединение.",
            )
            state.error_warning_sent = True
    store.save(state)


def run_monitor(
    settings: Settings,
    logger: logging.Logger,
    *,
    once: bool,
) -> int:
    store = StateStore(settings.state_file)
    state = store.load()
    if not state.monitor_started_at:
        state.monitor_started_at = utc_now_iso()
        store.save(state)
    notifier = TelegramNotifier(settings, logger)
    alerts = AlertManager(
        notifier,
        settings.notifications.repeat_after_seconds,
        settings.notifications.persistent_repeat_minutes,
        logger,
    )
    sound = SoundAlarm(
        settings.notifications.local_sound_enabled,
        settings.artifacts_dir / "STOP_SOUND.flag",
        logger,
    )
    cleanup_old_artifacts(settings, logger)

    try:
        with BrowserController(settings, logger) as browser:
            logger.info(
                "Видимый браузер запущен. Покупка билетов программой не выполняется."
            )
            while True:
                state.cycle += 1
                store.save(state)
                cycle_dates = dates_for_cycle(settings, state.cycle)
                cycle_checks = checks_for_cycle(settings)
                logger.info(
                    "Цикл %d: даты туда %s%s",
                    state.cycle,
                    ", ".join(item.isoformat() for item in cycle_dates),
                    (
                        f"; обратно {settings.route.destination} → "
                        f"{settings.route.origin} "
                        f"{settings.route.return_date}"
                        if settings.route.return_date is not None
                        else ""
                    ),
                )
                block_cycle = False
                successful_this_cycle = False
                for index, request in enumerate(cycle_checks):
                    leg_settings = replace(settings, route=request.route)
                    if request.leg == "return":
                        logger.info(
                            "Отдельная проверка обратного направления %s → %s "
                            "на %s (не зависит от результата рейса туда)",
                            request.route.origin,
                            request.route.destination,
                            request.travel_date,
                        )
                    check = browser.check_date_for_route(
                        request.route,
                        request.travel_date,
                        refresh_page=request.refresh_page,
                    )
                    process_check(
                        leg_settings,
                        request.travel_date,
                        check,
                        state,
                        store,
                        notifier,
                        alerts,
                        sound,
                        logger,
                    )
                    if check.result.state in {
                        DetectionState.AVAILABLE,
                        DetectionState.UNAVAILABLE,
                    }:
                        successful_this_cycle = True

                    if check.result.state == DetectionState.UNKNOWN and not once:
                        factor = (
                            2
                            if state.consecutive_errors
                            >= settings.safety.max_consecutive_errors
                            else 1
                        )
                        delay = settings.monitoring.unknown_retry_seconds * factor
                        logger.warning(
                            "UNKNOWN для %s; повтор через %d секунд",
                            request.travel_date,
                            delay,
                        )
                        time.sleep(delay)
                        retry = browser.check_date_for_route(
                            request.route,
                            request.travel_date,
                            refresh_page=request.refresh_page,
                        )
                        process_check(
                            leg_settings,
                            request.travel_date,
                            retry,
                            state,
                            store,
                            notifier,
                            alerts,
                            sound,
                            logger,
                        )
                        check = retry
                        if retry.result.state in {
                            DetectionState.AVAILABLE,
                            DetectionState.UNAVAILABLE,
                        }:
                            successful_this_cycle = True

                    if check.result.state in {
                        DetectionState.BLOCKED,
                        DetectionState.SESSION_EXPIRED,
                    }:
                        pause = settings.safety.block_pause_minutes * 60
                        logger.error(
                            "Цикл остановлен на %d минут. Решите проблему вручную "
                            "в оставленном открытым браузере.",
                            settings.safety.block_pause_minutes,
                        )
                        if not once:
                            time.sleep(pause)
                        block_cycle = True
                        break

                    if check.result.state == DetectionState.NETWORK_ERROR and not once:
                        error_index = min(
                            max(0, state.consecutive_errors - 1),
                            len(NETWORK_BACKOFF_SECONDS) - 1,
                        )
                        backoff = NETWORK_BACKOFF_SECONDS[error_index]
                        logger.warning("Сетевой backoff: %d секунд", backoff)
                        time.sleep(backoff)

                    if index < len(cycle_checks) - 1 and not block_cycle:
                        delay = random.randint(
                            settings.monitoring.min_date_delay_seconds,
                            settings.monitoring.max_date_delay_seconds,
                        )
                        logger.info("Пауза между датами: %d секунд", delay)
                        time.sleep(delay)

                update_health(
                    settings,
                    state,
                    store,
                    notifier,
                    logger,
                    successful_this_cycle=successful_this_cycle,
                )
                if once:
                    break
                delay = random.randint(
                    settings.monitoring.min_cycle_delay_seconds,
                    settings.monitoring.max_cycle_delay_seconds,
                )
                logger.info("Цикл завершён; следующая проверка через %d секунд", delay)
                time.sleep(delay)
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C.")
    finally:
        alerts.deactivate_all()
        sound.stop()
        store.save(state)
    return 0


def run_with_pid_lock(
    settings: Settings,
    logger: logging.Logger,
    *,
    once: bool,
) -> int:
    pid_file = settings.pid_file
    if pid_file.is_file():
        try:
            existing_pid = int(pid_file.read_text(encoding="ascii").strip())
            os.kill(existing_pid, 0)
        except (OSError, ValueError):
            pid_file.unlink(missing_ok=True)
        else:
            logger.error(
                "Монитор уже запущен (PID %d). Используйте scripts\\stop_monitor.bat.",
                existing_pid,
            )
            return 3
    current_pid = os.getpid()
    pid_file.write_text(str(current_pid), encoding="ascii")
    try:
        return run_monitor(settings, logger, once=once)
    finally:
        try:
            if pid_file.read_text(encoding="ascii").strip() == str(current_pid):
                pid_file.unlink(missing_ok=True)
        except OSError:
            pass


def run_diagnostics(settings: Settings, logger: logging.Logger) -> int:
    with BrowserController(settings, logger) as browser:
        report, screenshot = browser.diagnose()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nДиагностический скриншот: {screenshot or 'не сохранён'}")
    print(
        "Локаторы не считаются подтверждёнными, пока вы вручную не настроите "
        "реальный поиск и не проверите этот отчёт."
    )
    return 0


def cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    # До загрузки настроек логируем только в консоль, чтобы показать понятную ошибку.
    try:
        settings = load_settings(config_path=args.config)
    except ConfigurationError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2
    logger = configure_logging(settings.logs_dir, verbose=args.verbose)
    if args.test_alert:
        try:
            TelegramNotifier(settings, logger).test(settings.artifacts_dir)
        except NotificationError as exc:
            logger.error("Тест Telegram не пройден: %s", exc)
            return 1
        logger.info("Тестовое уведомление успешно отправлено.")
        return 0
    if args.test_alarm:
        sound = SoundAlarm(
            True,
            settings.artifacts_dir / "STOP_SOUND.flag",
            logger,
        )
        logger.warning(
            "Запущен тест тревоги. Нажмите «ОК» в системном окне "
            "или выполните scripts\\stop_alarm.bat."
        )
        sound.start()
        sound.wait_for_acknowledgement()
        logger.info("Тест звуковой тревоги подтверждён.")
        return 0
    if args.diagnose:
        return run_diagnostics(settings, logger)
    return run_with_pid_lock(settings, logger, once=args.once)


if __name__ == "__main__":
    raise SystemExit(cli())
