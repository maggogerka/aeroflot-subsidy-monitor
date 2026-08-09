from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class ConfigurationError(ValueError):
    """Ошибка пользовательской конфигурации."""


@dataclass(frozen=True)
class PassengerGroup:
    label: str
    count: int


@dataclass(frozen=True)
class RouteConfig:
    subsidy_program: str
    origin: str
    destination: str
    check_dates: tuple[date, ...]
    passengers: tuple[PassengerGroup, ...]
    trip_type: str
    return_date: date | None
    cabin: str = "Эконом"

    @property
    def passenger_count(self) -> int:
        return sum(item.count for item in self.passengers)

    # Совместимые имена для старых внутренних вызовов и конфигураций.
    @property
    def category(self) -> str:
        return self.subsidy_program

    @property
    def outbound_primary(self) -> tuple[date, ...]:
        return self.check_dates

    @property
    def outbound_secondary(self) -> tuple[date, ...]:
        return ()


@dataclass(frozen=True)
class MonitoringConfig:
    min_cycle_delay_seconds: int = 240
    max_cycle_delay_seconds: int = 420
    min_date_delay_seconds: int = 8
    max_date_delay_seconds: int = 20
    secondary_date_every_n_cycles: int = 3
    unknown_retry_seconds: int = 90
    heartbeat_hours: float = 6
    stale_monitor_minutes: int = 20
    refresh_mode: str = "cycle"


@dataclass(frozen=True)
class NotificationConfig:
    telegram_enabled: bool = True
    local_sound_enabled: bool = True
    repeat_after_seconds: tuple[int, ...] = (30, 120)
    persistent_repeat_minutes: int = 10


@dataclass(frozen=True)
class SafetyConfig:
    block_pause_minutes: int = 30
    max_consecutive_errors: int = 3
    save_unknown_html: bool = True
    artifact_retention_days: int = 14


@dataclass(frozen=True)
class BrowserConfig:
    search_url: str = "https://www.aeroflot.ru/ru-ru/pbsa/search#/search"
    timeout_seconds: int = 45
    slow_mo_ms: int = 150


@dataclass(frozen=True)
class Settings:
    route: RouteConfig
    monitoring: MonitoringConfig
    notifications: NotificationConfig
    safety: SafetyConfig
    browser: BrowserConfig
    telegram_bot_token: str = field(repr=False)
    telegram_chat_id: str
    root: Path = PROJECT_ROOT

    @property
    def profile_dir(self) -> Path:
        return self.root / "browser_profile"

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def state_file(self) -> Path:
        return self.root / "monitor_state.json"

    @property
    def pid_file(self) -> Path:
        return self.root / ".monitor.pid"


def _mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise ConfigurationError(f"Раздел {key!r} должен быть объектом YAML.")
    return value


def _parse_date(value: Any, field_name: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(
            f"{field_name}: нужна дата в формате YYYY-MM-DD, получено {value!r}."
        ) from exc


def _date_tuple(value: Any, field_name: str) -> tuple[date, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigurationError(f"{field_name}: нужен непустой список дат.")
    return tuple(_parse_date(item, field_name) for item in value)


def _passenger_groups(
    value: Any,
    *,
    legacy_count: Any,
    subsidy_program: str,
) -> tuple[PassengerGroup, ...]:
    if value is None:
        count = _positive(legacy_count, "route.passenger_count")
        default_label = (
            "Молодёжь"
            if "молод" in subsidy_program.casefold()
            else "Взрослый"
        )
        return (PassengerGroup(default_label, count),)
    if not isinstance(value, dict) or not value:
        raise ConfigurationError(
            "route.passengers: нужен непустой объект «название строки: количество»."
        )
    groups: list[PassengerGroup] = []
    for raw_label, raw_count in value.items():
        label = str(raw_label).strip()
        if not label:
            raise ConfigurationError("route.passengers: название не может быть пустым.")
        count = _positive(
            raw_count,
            f"route.passengers.{label}",
            allow_zero=True,
        )
        if count:
            groups.append(PassengerGroup(label, count))
    if not groups:
        raise ConfigurationError(
            "route.passengers: укажите хотя бы одного пассажира."
        )
    return tuple(groups)


def _positive(value: Any, field_name: str, *, allow_zero: bool = False) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{field_name}: требуется целое число.") from exc
    floor = 0 if allow_zero else 1
    if result < floor:
        raise ConfigurationError(f"{field_name}: значение должно быть не меньше {floor}.")
    return result


def _number(value: Any, field_name: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{field_name}: требуется число.") from exc
    if result <= 0:
        raise ConfigurationError(f"{field_name}: значение должно быть положительным.")
    return result


def load_settings(
    config_path: str | Path | None = None,
    env_path: str | Path | None = None,
) -> Settings:
    root = PROJECT_ROOT
    config_file = Path(config_path) if config_path else root / "config.yaml"
    dotenv_file = Path(env_path) if env_path else root / ".env"
    if not dotenv_file.is_file():
        raise ConfigurationError(
            f"Не найден файл {dotenv_file}. Скопируйте .env.example в .env и заполните его."
        )
    load_dotenv(dotenv_path=dotenv_file, override=False)
    if not config_file.is_file():
        raise ConfigurationError(
            f"Не найден файл {config_file}. Скопируйте config.example.yaml в config.yaml."
        )
    try:
        raw = yaml.safe_load(config_file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Некорректный YAML в {config_file}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("Корень config.yaml должен быть объектом YAML.")

    route_raw = _mapping(raw, "route")
    monitoring_raw = _mapping(raw, "monitoring")
    notifications_raw = _mapping(raw, "notifications")
    safety_raw = _mapping(raw, "safety")
    browser_raw = _mapping(raw, "browser")

    subsidy_program = str(
        route_raw.get(
            "subsidy_program",
            route_raw.get("category", "Молодёжь и пенсионеры"),
        )
    ).strip()
    passenger_raw = os.getenv("PASSENGER_COUNT", route_raw.get("passenger_count", 1))
    raw_return_date = route_raw.get("return_date")
    return_date = (
        None
        if raw_return_date in (None, "")
        else _parse_date(raw_return_date, "route.return_date")
    )
    trip_type = str(
        route_raw.get(
            "trip_type",
            "round_trip" if return_date is not None else "one_way",
        )
    ).strip().lower()
    raw_check_dates = route_raw.get("check_dates")
    if raw_check_dates is None:
        raw_check_dates = list(route_raw.get("outbound_primary") or [])
        raw_check_dates.extend(route_raw.get("outbound_secondary") or [])
    route = RouteConfig(
        subsidy_program=subsidy_program,
        origin=str(route_raw.get("origin", "")).strip(),
        destination=str(route_raw.get("destination", "")).strip(),
        check_dates=_date_tuple(
            raw_check_dates,
            "route.check_dates",
        ),
        passengers=_passenger_groups(
            route_raw.get("passengers"),
            legacy_count=passenger_raw,
            subsidy_program=subsidy_program,
        ),
        trip_type=trip_type,
        return_date=return_date,
        cabin=str(route_raw.get("cabin", "Эконом")).strip(),
    )
    if not route.subsidy_program:
        raise ConfigurationError("route.subsidy_program не может быть пустым.")
    if not route.origin or not route.destination:
        raise ConfigurationError("route.origin и route.destination не могут быть пустыми.")
    if route.origin.casefold() == route.destination.casefold():
        raise ConfigurationError("Города отправления и назначения должны отличаться.")
    if not route.cabin:
        raise ConfigurationError("route.cabin не может быть пустым.")
    if route.trip_type not in {"one_way", "round_trip"}:
        raise ConfigurationError(
            "route.trip_type: допустимо только 'one_way' или 'round_trip'."
        )
    if route.trip_type == "one_way" and route.return_date is not None:
        raise ConfigurationError(
            "Для trip_type: one_way удалите route.return_date или задайте null."
        )
    if route.trip_type == "round_trip" and route.return_date is None:
        raise ConfigurationError(
            "Для trip_type: round_trip обязательно задайте route.return_date."
        )
    for outbound in route.check_dates:
        if route.return_date is not None and route.return_date < outbound:
            raise ConfigurationError(
                f"Обратная дата {route.return_date} раньше вылета {outbound}."
            )

    monitoring = MonitoringConfig(
        min_cycle_delay_seconds=_positive(
            monitoring_raw.get("min_cycle_delay_seconds", 240),
            "monitoring.min_cycle_delay_seconds",
        ),
        max_cycle_delay_seconds=_positive(
            monitoring_raw.get("max_cycle_delay_seconds", 420),
            "monitoring.max_cycle_delay_seconds",
        ),
        min_date_delay_seconds=_positive(
            monitoring_raw.get("min_date_delay_seconds", 8),
            "monitoring.min_date_delay_seconds",
            allow_zero=True,
        ),
        max_date_delay_seconds=_positive(
            monitoring_raw.get("max_date_delay_seconds", 20),
            "monitoring.max_date_delay_seconds",
            allow_zero=True,
        ),
        secondary_date_every_n_cycles=_positive(
            monitoring_raw.get("secondary_date_every_n_cycles", 3),
            "monitoring.secondary_date_every_n_cycles",
        ),
        unknown_retry_seconds=_positive(
            monitoring_raw.get("unknown_retry_seconds", 90),
            "monitoring.unknown_retry_seconds",
        ),
        heartbeat_hours=_number(
            monitoring_raw.get("heartbeat_hours", 6), "monitoring.heartbeat_hours"
        ),
        stale_monitor_minutes=_positive(
            monitoring_raw.get("stale_monitor_minutes", 20),
            "monitoring.stale_monitor_minutes",
        ),
        refresh_mode=str(
            monitoring_raw.get("refresh_mode", "cycle")
        ).strip().lower(),
    )
    if monitoring.min_cycle_delay_seconds > monitoring.max_cycle_delay_seconds:
        raise ConfigurationError(
            "monitoring.min_cycle_delay_seconds не может быть больше max_cycle_delay_seconds."
        )
    if monitoring.min_date_delay_seconds > monitoring.max_date_delay_seconds:
        raise ConfigurationError(
            "monitoring.min_date_delay_seconds не может быть больше max_date_delay_seconds."
        )
    if monitoring.refresh_mode not in {"cycle", "date"}:
        raise ConfigurationError(
            "monitoring.refresh_mode: допустимо только 'cycle' или 'date'."
        )

    repeats = notifications_raw.get("repeat_after_seconds", [30, 120])
    if not isinstance(repeats, list):
        raise ConfigurationError("notifications.repeat_after_seconds должен быть списком.")
    notifications = NotificationConfig(
        telegram_enabled=bool(notifications_raw.get("telegram_enabled", True)),
        local_sound_enabled=bool(notifications_raw.get("local_sound_enabled", True)),
        repeat_after_seconds=tuple(
            _positive(v, "notifications.repeat_after_seconds") for v in repeats
        ),
        persistent_repeat_minutes=_positive(
            notifications_raw.get("persistent_repeat_minutes", 10),
            "notifications.persistent_repeat_minutes",
        ),
    )
    safety = SafetyConfig(
        block_pause_minutes=max(
            30,
            _positive(
                safety_raw.get("block_pause_minutes", 30),
                "safety.block_pause_minutes",
            ),
        ),
        max_consecutive_errors=_positive(
            safety_raw.get("max_consecutive_errors", 3),
            "safety.max_consecutive_errors",
        ),
        save_unknown_html=bool(safety_raw.get("save_unknown_html", True)),
        artifact_retention_days=_positive(
            safety_raw.get("artifact_retention_days", 14),
            "safety.artifact_retention_days",
        ),
    )
    browser = BrowserConfig(
        search_url=str(
            browser_raw.get(
                "search_url", "https://www.aeroflot.ru/ru-ru/pbsa/search#/search"
            )
        ),
        timeout_seconds=_positive(
            browser_raw.get("timeout_seconds", 45), "browser.timeout_seconds"
        ),
        slow_mo_ms=_positive(
            browser_raw.get("slow_mo_ms", 150),
            "browser.slow_mo_ms",
            allow_zero=True,
        ),
    )

    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if notifications.telegram_enabled and (not token or not chat_id):
        raise ConfigurationError(
            "Telegram включён, но TELEGRAM_BOT_TOKEN или TELEGRAM_CHAT_ID не заполнены в .env."
        )
    return Settings(
        route=route,
        monitoring=monitoring,
        notifications=notifications,
        safety=safety,
        browser=browser,
        telegram_bot_token=token,
        telegram_chat_id=chat_id,
        root=root,
    )
