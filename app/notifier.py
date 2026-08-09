from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import requests

from app.configuration import Settings


MONTHS_RU = (
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


class NotificationError(RuntimeError):
    pass


class TelegramNotifier:
    def __init__(self, settings: Settings, logger: logging.Logger):
        self.enabled = settings.notifications.telegram_enabled
        self._token = settings.telegram_bot_token
        self._chat_id = settings.telegram_chat_id
        self._site_url = settings.browser.search_url
        self.logger = logger

    def _request(
        self,
        method: str,
        *,
        data: dict[str, str],
        files: dict[str, object] | None = None,
    ) -> dict:
        if not self.enabled:
            return {"ok": True, "disabled": True}
        url = f"https://api.telegram.org/bot{self._token}/{method}"
        try:
            response = requests.post(url, data=data, files=files, timeout=(10, 30))
        except requests.RequestException as exc:
            raise NotificationError(
                f"Telegram недоступен: {type(exc).__name__}"
            ) from None
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not response.ok or not payload.get("ok"):
            description = str(payload.get("description", "неизвестная ошибка"))[:300]
            raise NotificationError(
                f"Telegram API вернул HTTP {response.status_code}: {description}"
            )
        return payload

    def send(
        self,
        text: str,
        attachment: Path | None = None,
        *,
        include_site_button: bool = False,
    ) -> None:
        data = {"chat_id": self._chat_id}
        if include_site_button:
            data["reply_markup"] = json.dumps(
                {
                    "inline_keyboard": [
                        [{"text": "Открыть сайт Аэрофлота", "url": self._site_url}]
                    ]
                },
                ensure_ascii=False,
            )
        if attachment and attachment.is_file():
            method = "sendPhoto" if attachment.suffix.lower() in {".png", ".jpg", ".jpeg"} else "sendDocument"
            caption_key = "caption"
            file_key = "photo" if method == "sendPhoto" else "document"
            data[caption_key] = text[:1024]
            with attachment.open("rb") as stream:
                self._request(method, data=data, files={file_key: stream})
        else:
            data["text"] = text[:4096]
            self._request("sendMessage", data=data)

    def test(self, artifact_dir: Path) -> None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        path = artifact_dir / "telegram-test.txt"
        path.write_text(
            "Тестовое вложение монитора Аэрофлота.\n"
            f"Создано: {datetime.now().astimezone().isoformat()}\n",
            encoding="utf-8",
        )
        self.send(
            "✅ Тестовое уведомление монитора билетов. Telegram настроен верно.",
            path,
            include_site_button=True,
        )


@dataclass(frozen=True)
class AlertPayload:
    outbound_date: date
    text: str
    screenshot: Path | None


class AlertManager:
    """Один поток повторных Telegram-напоминаний на каждую доступную дату."""

    def __init__(
        self,
        notifier: TelegramNotifier,
        repeat_after_seconds: tuple[int, ...],
        persistent_repeat_minutes: int,
        logger: logging.Logger,
    ):
        self.notifier = notifier
        self.repeat_after_seconds = tuple(sorted(set(repeat_after_seconds)))
        self.persistent_seconds = persistent_repeat_minutes * 60
        self.logger = logger
        self._lock = threading.RLock()
        self._active: dict[str, tuple[threading.Event, AlertPayload]] = {}

    def is_active(self, key: str) -> bool:
        with self._lock:
            return key in self._active

    def activate(self, key: str, payload: AlertPayload, *, initial: bool) -> bool:
        with self._lock:
            if key in self._active:
                event, _ = self._active[key]
                self._active[key] = (event, payload)
                return False
            stop = threading.Event()
            self._active[key] = (stop, payload)
        # При первом AVAILABLE в текущем процессе сообщение отправляется всегда.
        # Это защищает от пропуска после перезапуска со старым state-файлом.
        self._safe_send(payload.text, payload.screenshot, button=True)
        thread = threading.Thread(
            target=self._repeat_loop,
            args=(key, stop, True),
            name=f"telegram-alert-{key}",
            daemon=True,
        )
        thread.start()
        return True

    def deactivate(self, key: str) -> None:
        with self._lock:
            item = self._active.pop(key, None)
        if item:
            item[0].set()

    def deactivate_all(self) -> None:
        with self._lock:
            keys = list(self._active)
        for key in keys:
            self.deactivate(key)

    def _payload(self, key: str) -> AlertPayload | None:
        with self._lock:
            item = self._active.get(key)
            return item[1] if item else None

    def _repeat_loop(
        self, key: str, stop: threading.Event, sent_initial: bool
    ) -> None:
        previous = 0
        if sent_initial:
            for absolute_delay in self.repeat_after_seconds:
                if stop.wait(max(0, absolute_delay - previous)):
                    return
                payload = self._payload(key)
                if not payload:
                    return
                self._safe_send(
                    f"🔥 Билет на {payload.outbound_date:%d.%m.%Y} всё ещё доступен. "
                    "Откройте сайт Аэрофлота прямо сейчас.",
                    None,
                    button=True,
                )
                previous = absolute_delay
        while not stop.wait(self.persistent_seconds):
            payload = self._payload(key)
            if not payload:
                return
            self._safe_send(
                f"🔥 Напоминание: билет на {payload.outbound_date:%d.%m.%Y} "
                "по-прежнему отображается доступным.",
                None,
                button=True,
            )

    def _safe_send(
        self, text: str, attachment: Path | None, *, button: bool
    ) -> bool:
        # Telegram может кратковременно не отвечать. Несколько попыток плюс
        # последующие напоминания существенно уменьшают риск пропуска тревоги.
        for attempt, delay in enumerate((0, 5, 15, 30), start=1):
            if delay:
                time.sleep(delay)
            try:
                self.notifier.send(
                    text, attachment, include_site_button=button
                )
                self.logger.info(
                    "Telegram-тревога успешно отправлена (попытка %d)", attempt
                )
                return True
            except NotificationError as exc:
                self.logger.error(
                    "Telegram-тревога: попытка %d не удалась: %s",
                    attempt,
                    exc,
                )
        return False


class SoundAlarm:
    def __init__(self, enabled: bool, stop_flag: Path, logger: logging.Logger):
        self.enabled = enabled
        self.stop_flag = stop_flag
        self.logger = logger
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._dialog_thread: threading.Thread | None = None

    def start(self) -> None:
        if not self.enabled:
            return
        if self._thread and self._thread.is_alive():
            if not self._stop.is_set():
                return
            self._thread.join(timeout=2)
            if self._thread.is_alive():
                return
        self.stop_flag.unlink(missing_ok=True)
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="local-ticket-alarm", daemon=True
        )
        self._thread.start()
        self._dialog_thread = threading.Thread(
            target=self._show_acknowledgement,
            name="local-ticket-alarm-dialog",
            daemon=True,
        )
        self._dialog_thread.start()

    def stop(self) -> None:
        self._stop.set()

    def wait_for_acknowledgement(self) -> None:
        while not self._stop.wait(0.5):
            pass

    def _loop(self) -> None:
        try:
            import winsound
        except ImportError:
            self.logger.warning("winsound недоступен: локальный звук отключён.")
            return
        while not self._stop.is_set():
            if self.stop_flag.exists():
                self.logger.info("Звуковая тревога остановлена файлом %s", self.stop_flag)
                self._stop.set()
                return
            try:
                # Резкий чередующийся сигнал без длинных пауз. Фактическая
                # громкость ограничена уровнем громкости Windows/динамиков.
                winsound.Beep(2200, 1_000)
                winsound.Beep(900, 700)
                winsound.Beep(1700, 1_000)
            except RuntimeError:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            self._stop.wait(0.15)

    def _show_acknowledgement(self) -> None:
        try:
            import ctypes

            flags = 0x00000030 | 0x00001000 | 0x00010000 | 0x00040000
            ctypes.windll.user32.MessageBoxW(
                0,
                "НАЙДЕН СУБСИДИРОВАННЫЙ БИЛЕТ!\n\n"
                "Откройте Telegram и сайт Аэрофлота.\n"
                "Нажмите «ОК», чтобы остановить звуковую тревогу.",
                "ТРЕВОГА: НАЙДЕН БИЛЕТ",
                flags,
            )
            self.stop_flag.parent.mkdir(parents=True, exist_ok=True)
            self.stop_flag.touch(exist_ok=True)
            self._stop.set()
            self.logger.info("Звуковая тревога подтверждена на ноутбуке")
        except Exception:
            self.logger.debug(
                "Не удалось показать окно подтверждения тревоги",
                exc_info=True,
            )


def format_available_message(
    settings: Settings,
    outbound: date,
    price: str | None,
) -> str:
    detected = datetime.now().astimezone().strftime("%H:%M:%S %Z")
    return_line = ""
    if settings.route.return_date is not None:
        return_line = (
            f"Обратно: {settings.route.return_date.day} "
            f"{MONTHS_RU[settings.route.return_date.month]} "
            f"{settings.route.return_date.year}\n"
        )
    passengers = ", ".join(
        f"{item.label}: {item.count}" for item in settings.route.passengers
    )
    return (
        "🔥 НАЙДЕН СУБСИДИРОВАННЫЙ БИЛЕТ\n\n"
        f"Программа: {settings.route.subsidy_program}\n"
        f"{settings.route.origin} → {settings.route.destination}\n"
        f"Вылет: {outbound.day} {MONTHS_RU[outbound.month]} {outbound.year}\n"
        f"{return_line}"
        f"Пассажиры: {passengers}\n"
        f"Обнаружен: {detected}\n"
        f"Цена: {price or 'не удалось достоверно извлечь'}\n\n"
        "Срочно откройте уже работающий ноутбук или официальный сайт Аэрофлота.\n"
        "Покупка программой не выполнялась."
    )
