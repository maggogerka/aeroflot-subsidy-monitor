from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DateState:
    available: bool = False
    last_detection: str | None = None
    last_checked_at: str | None = None
    last_alert_at: str | None = None
    occurrence: int = 0


@dataclass
class MonitorState:
    cycle: int = 0
    dates: dict[str, DateState] = field(default_factory=dict)
    monitor_started_at: str | None = None
    consecutive_errors: int = 0
    error_warning_sent: bool = False
    stale_warning_sent: bool = False
    last_successful_check: str | None = None
    last_heartbeat: str | None = None

    def date_state(self, outbound_date: str) -> DateState:
        return self.dates.setdefault(outbound_date, DateState())

    def record_availability(
        self, outbound_date: str, available: bool, detection: str
    ) -> bool:
        """Записать состояние. Возвращает True только при новом появлении билета."""
        current = self.date_state(outbound_date)
        appeared = available and not current.available
        if appeared:
            current.occurrence += 1
        current.available = available
        current.last_detection = detection
        current.last_checked_at = utc_now_iso()
        return appeared

    def record_success(self) -> bool:
        """Сбросить ошибки. Возвращает True, если монитор восстановился."""
        recovered = self.consecutive_errors > 0 and self.error_warning_sent
        self.consecutive_errors = 0
        self.error_warning_sent = False
        self.stale_warning_sent = False
        self.last_successful_check = utc_now_iso()
        return recovered

    def record_error(self) -> int:
        self.consecutive_errors += 1
        return self.consecutive_errors

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MonitorState":
        raw_dates = data.get("dates", {})
        dates = {
            key: DateState(**value)
            for key, value in raw_dates.items()
            if isinstance(key, str) and isinstance(value, dict)
        }
        return cls(
            cycle=int(data.get("cycle", 0)),
            dates=dates,
            monitor_started_at=data.get("monitor_started_at"),
            consecutive_errors=int(data.get("consecutive_errors", 0)),
            error_warning_sent=bool(data.get("error_warning_sent", False)),
            stale_warning_sent=bool(data.get("stale_warning_sent", False)),
            last_successful_check=data.get("last_successful_check"),
            last_heartbeat=data.get("last_heartbeat"),
        )


class StateStore:
    """Потокобезопасное JSON-состояние с атомарной заменой файла."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    def load(self) -> MonitorState:
        with self._lock:
            if not self.path.exists():
                return MonitorState()
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("корень JSON не является объектом")
                return MonitorState.from_dict(data)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                backup = self.path.with_suffix(
                    f"{self.path.suffix}.corrupt-{datetime.now():%Y%m%d-%H%M%S}"
                )
                try:
                    self.path.replace(backup)
                except OSError:
                    pass
                return MonitorState()

    def save(self, state: MonitorState) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
            )
            temp_path = Path(temp_name)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(state.to_dict(), stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temp_path, self.path)
            finally:
                temp_path.unlink(missing_ok=True)
