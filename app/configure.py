from __future__ import annotations

import getpass
import re
import shutil
import sys
from datetime import date
from pathlib import Path

import yaml

from app.configuration import ConfigurationError, PROJECT_ROOT, load_settings


def ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    while True:
        value = input(f"{prompt}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        print("Значение не может быть пустым.")


def ask_bool(prompt: str, default: bool) -> bool:
    default_text = "Д" if default else "н"
    while True:
        value = input(f"{prompt} [Д/н, по умолчанию {default_text}]: ").strip().lower()
        if not value:
            return default
        if value in {"д", "да", "y", "yes"}:
            return True
        if value in {"н", "нет", "n", "no"}:
            return False
        print("Введите «да» или «нет».")


def parse_dates(value: str) -> list[str]:
    result: list[str] = []
    for item in re.split(r"[,;\s]+", value.strip()):
        if not item:
            continue
        try:
            result.append(date.fromisoformat(item).isoformat())
        except ValueError:
            raise ValueError(f"Некорректная дата {item!r}; нужен формат YYYY-MM-DD.")
    if not result:
        raise ValueError("Нужна хотя бы одна дата.")
    return result


def parse_passengers(value: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in value.split(";"):
        if not item.strip():
            continue
        if "=" not in item:
            raise ValueError(
                f"Некорректная группа {item!r}; пример: Молодёжь=1;Ребёнок=1"
            )
        label, raw_count = item.split("=", 1)
        label = label.strip()
        count = int(raw_count.strip())
        if not label or count < 1:
            raise ValueError("Название не должно быть пустым, количество — от 1.")
        result[label] = count
    if not result:
        raise ValueError("Нужна хотя бы одна группа пассажиров.")
    return result


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def main() -> int:
    config_path = PROJECT_ROOT / "config.yaml"
    example_path = PROJECT_ROOT / "config.example.yaml"
    env_path = PROJECT_ROOT / ".env"
    print(
        "\nМастер настройки чекера субсидированных билетов\n"
        "Используйте точные подписи программы и пассажиров с сайта Аэрофлота.\n"
    )
    if config_path.exists() and not ask_bool(
        "config.yaml уже существует. Перезаписать его", False
    ):
        print("Настройка отменена, существующий файл не изменён.")
        return 0

    raw = yaml.safe_load(example_path.read_text(encoding="utf-8"))
    route = raw["route"]
    route["subsidy_program"] = ask(
        "Программа субсидирования", route["subsidy_program"]
    )
    route["origin"] = ask("Город отправления", route["origin"])
    route["destination"] = ask("Город назначения", route["destination"])
    while True:
        try:
            route["check_dates"] = parse_dates(
                ask(
                    "Даты проверки через запятую (YYYY-MM-DD)",
                    ",".join(route["check_dates"]),
                )
            )
            break
        except ValueError as exc:
            print(exc)

    round_trip = ask_bool("Нужен обратный билет", False)
    route["trip_type"] = "round_trip" if round_trip else "one_way"
    route["return_date"] = (
        ask("Дата обратно (YYYY-MM-DD)") if round_trip else None
    )
    route["cabin"] = ask("Класс обслуживания", route["cabin"])
    while True:
        try:
            default_groups = ";".join(
                f"{label}={count}"
                for label, count in route["passengers"].items()
            )
            route["passengers"] = parse_passengers(
                ask("Пассажиры (Название=число;Название=число)", default_groups)
            )
            break
        except (ValueError, TypeError) as exc:
            print(exc)

    refresh_each = ask_bool(
        "Полностью обновлять и заполнять форму перед каждой датой", False
    )
    raw["monitoring"]["refresh_mode"] = "date" if refresh_each else "cycle"
    telegram = ask_bool("Включить Telegram-уведомления", True)
    raw["notifications"]["telegram_enabled"] = telegram

    config_path.write_text(
        yaml.safe_dump(raw, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    current_env = read_env(env_path)
    if telegram:
        token = getpass.getpass(
            "Telegram bot token (ввод скрыт; Enter — сохранить прежний): "
        ).strip() or current_env.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = ask(
            "Telegram chat ID",
            current_env.get("TELEGRAM_CHAT_ID") or None,
        )
        env_path.write_text(
            f"TELEGRAM_BOT_TOKEN={token}\nTELEGRAM_CHAT_ID={chat_id}\n",
            encoding="utf-8",
        )
    elif not env_path.exists():
        shutil.copyfile(PROJECT_ROOT / ".env.example", env_path)

    try:
        load_settings(config_path, env_path)
    except ConfigurationError as exc:
        print(f"\nФайлы созданы, но проверка не пройдена: {exc}", file=sys.stderr)
        return 2
    print(
        "\nНастройка сохранена и проверена.\n"
        "Проверьте Telegram: python -m app.main --test-alert\n"
        "Проверьте один цикл: python -m app.main --once"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
