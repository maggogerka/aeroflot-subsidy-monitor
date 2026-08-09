from pathlib import Path

import pytest

from app.configuration import ConfigurationError, load_settings


BASE_CONFIG = """
route:
  origin: Москва
  destination: Владивосток
  outbound_primary: ["2026-08-29"]
  outbound_secondary: ["2026-08-28"]
  return_date: "2026-09-07"
monitoring:
  min_cycle_delay_seconds: 240
  max_cycle_delay_seconds: 420
notifications:
  telegram_enabled: false
"""


def write_files(tmp_path: Path, config: str = BASE_CONFIG):
    config_path = tmp_path / "config.yaml"
    env_path = tmp_path / ".env"
    config_path.write_text(config, encoding="utf-8")
    env_path.write_text("PASSENGER_COUNT=1\n", encoding="utf-8")
    return config_path, env_path


def test_valid_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv("PASSENGER_COUNT", raising=False)
    config, env = write_files(tmp_path)
    settings = load_settings(config, env)
    assert settings.route.passenger_count == 1
    assert settings.route.return_date.isoformat() == "2026-09-07"
    assert [item.isoformat() for item in settings.route.check_dates] == [
        "2026-08-29",
        "2026-08-28",
    ]


def test_return_date_is_optional(tmp_path, monkeypatch):
    monkeypatch.delenv("PASSENGER_COUNT", raising=False)
    config, env = write_files(
        tmp_path, BASE_CONFIG.replace('  return_date: "2026-09-07"\n', "")
    )
    settings = load_settings(config, env)
    assert settings.route.return_date is None


def test_return_before_outbound_is_rejected(tmp_path, monkeypatch):
    monkeypatch.delenv("PASSENGER_COUNT", raising=False)
    config, env = write_files(
        tmp_path, BASE_CONFIG.replace("2026-09-07", "2026-08-20")
    )
    with pytest.raises(ConfigurationError, match="раньше вылета"):
        load_settings(config, env)


def test_missing_env_is_rejected(tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(BASE_CONFIG, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="Не найден файл"):
        load_settings(config, tmp_path / ".env")


def test_flexible_route_and_passenger_groups(tmp_path, monkeypatch):
    monkeypatch.delenv("PASSENGER_COUNT", raising=False)
    config, env = write_files(
        tmp_path,
        """
route:
  subsidy_program: Многодетные семьи
  origin: Хабаровск
  destination: Москва
  check_dates: ["2026-09-01", "2026-09-02", "2026-09-01"]
  trip_type: round_trip
  return_date: "2026-09-10"
  cabin: Эконом
  passengers:
    Взрослый: 1
    Ребёнок: 2
monitoring:
  refresh_mode: date
notifications:
  telegram_enabled: false
""",
    )
    settings = load_settings(config, env)
    assert settings.route.subsidy_program == "Многодетные семьи"
    assert settings.route.passenger_count == 3
    assert [item.label for item in settings.route.passengers] == [
        "Взрослый",
        "Ребёнок",
    ]
    assert len(settings.route.check_dates) == 3
    assert settings.monitoring.refresh_mode == "date"


def test_round_trip_requires_return_date(tmp_path, monkeypatch):
    monkeypatch.delenv("PASSENGER_COUNT", raising=False)
    config, env = write_files(
        tmp_path,
        BASE_CONFIG.replace(
            '  return_date: "2026-09-07"\n',
            "  trip_type: round_trip\n",
        ),
    )
    with pytest.raises(ConfigurationError, match="обязательно"):
        load_settings(config, env)
