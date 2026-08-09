from __future__ import annotations

import html as html_module
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    BrowserContext,
    Locator,
    Page,
    Playwright,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

from app.configuration import Settings
from app.detector import DetectionResult, DetectionState, classify_html


MONTHS_RU = (
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
MONTH_PATTERNS_RU = (
    "",
    r"янв(?:аря)?",
    r"фев(?:раля)?",
    r"мар(?:та)?",
    r"апр(?:еля)?",
    r"мая",
    r"июн(?:я)?",
    r"июл(?:я)?",
    r"авг(?:уста)?",
    r"сен(?:тября)?",
    r"окт(?:ября)?",
    r"ноя(?:бря)?",
    r"дек(?:абря)?",
)
MONTHS_RU_NOMINATIVE = (
    "",
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
)


@dataclass(frozen=True)
class LocatorCatalog:
    search_button: re.Pattern[str] = re.compile(
        r"^\s*(найти|поиск|найти рейсы|показать рейсы)\s*$", re.IGNORECASE
    )
    outbound_field: re.Pattern[str] = re.compile(
        r"дата (?:вылета|туда)|туда", re.IGNORECASE
    )
    results_region: re.Pattern[str] = re.compile(
        r"результат|вариант|рейс|перел[её]т", re.IGNORECASE
    )
    meaningful_result: re.Pattern[str] = re.compile(
        r"субсидированные билеты не найдены|выберите другую дату вылета|"
        r"субсид\w*|captcha|доступ временно ограничен|access denied|"
        r"техническ\w+ ошибк", re.IGNORECASE
    )


@dataclass(frozen=True)
class BrowserCheck:
    result: DetectionResult
    screenshot: Path | None
    diagnostic_file: Path | None
    duration_seconds: float


class BrowserController:
    """Единственный видимый браузер с постоянным профилем."""

    def __init__(self, settings: Settings, logger: logging.Logger):
        self.settings = settings
        self.logger = logger
        self.locators = LocatorCatalog()
        self._playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.last_selected_date: date | None = None
        self._search_page_ready = False

    def __enter__(self) -> "BrowserController":
        self.open()
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self.close()

    def open(self) -> Page:
        if self.page and not self.page.is_closed():
            return self.page
        self.settings.profile_dir.mkdir(parents=True, exist_ok=True)
        self.settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._playwright = sync_playwright().start()
        self.context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.settings.profile_dir),
            headless=False,
            slow_mo=self.settings.browser.slow_mo_ms,
            viewport={"width": 1440, "height": 950},
            args=["--start-maximized"],
        )
        self.context.set_default_timeout(self.settings.browser.timeout_seconds * 1000)
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        return self.page

    def close(self) -> None:
        if self.context:
            try:
                self.context.close()
            except Exception:
                self.logger.debug("Не удалось штатно закрыть контекст", exc_info=True)
        if self._playwright:
            try:
                self._playwright.stop()
            except Exception:
                self.logger.debug("Не удалось штатно остановить Playwright", exc_info=True)
        self.context = None
        self.page = None
        self._playwright = None
        self._search_page_ready = False

    def ensure_search_page(self) -> Page:
        page = self.open()
        if "aeroflot.ru" not in page.url or "/pbsa/" not in page.url:
            page.goto(
                self.settings.browser.search_url,
                wait_until="domcontentloaded",
                timeout=self.settings.browser.timeout_seconds * 1000,
            )
        if not self._search_page_ready:
            try:
                page.wait_for_function(
                    "() => document.body && document.body.innerText.length > 100",
                    timeout=self.settings.browser.timeout_seconds * 1000,
                )
                page.wait_for_timeout(1_500)
            except PlaywrightTimeoutError:
                self.logger.debug("Интерфейс поиска не успел полностью загрузиться")
            self._search_page_ready = True
        return page

    def _refresh_search_form(self, outbound: date) -> Page:
        """Обновляет SPA перед каждым новым поиском и ждёт чистую форму."""
        page = self.ensure_search_page()
        self.logger.info(
            "Обновление страницы перед новым поиском даты %s", outbound
        )
        page.reload(
            wait_until="domcontentloaded",
            timeout=self.settings.browser.timeout_seconds * 1000,
        )
        self.last_selected_date = None
        page.wait_for_function(
            "() => document.body && document.body.innerText.length > 100",
            timeout=self.settings.browser.timeout_seconds * 1000,
        )

        program_pattern = re.compile(
            r"^\s*Программа субсидирования\s*$", re.IGNORECASE
        )
        deadline = time.monotonic() + min(
            15, self.settings.browser.timeout_seconds
        )
        while time.monotonic() < deadline:
            if self._first_visible(
                page.get_by_placeholder(program_pattern)
            ) is not None:
                page.wait_for_timeout(500)
                return page
            page.wait_for_timeout(300)

        # Иногда hash-SPA сохраняет экран результатов после reload. Переход
        # на исходный URL заново монтирует форму поиска.
        page.goto(
            self.settings.browser.search_url,
            wait_until="domcontentloaded",
            timeout=self.settings.browser.timeout_seconds * 1000,
        )
        field = self._first_visible(page.get_by_placeholder(program_pattern))
        if field is None:
            try:
                page.get_by_placeholder(program_pattern).first.wait_for(
                    state="visible",
                    timeout=self.settings.browser.timeout_seconds * 1000,
                )
            except PlaywrightTimeoutError:
                self.logger.debug(
                    "После обновления не появилась исходная форма поиска"
                )
        page.wait_for_timeout(500)
        return page

    @staticmethod
    def _date_pattern(value: date) -> re.Pattern[str]:
        month = MONTH_PATTERNS_RU[value.month]
        return re.compile(
            rf"(?:\b0?{value.day}\s+{month}(?:\s+{value.year})?\b|"
            rf"\b{value.day:02d}[./]{value.month:02d}(?:[./]{value.year})?\b)",
            re.IGNORECASE,
        )

    @staticmethod
    def _date_label_pattern(value: date) -> re.Pattern[str]:
        month = MONTH_PATTERNS_RU[value.month]
        return re.compile(
            rf"^\s*0?{value.day}\s+{month}(?:\s+{value.year})?"
            rf"(?:,\s*[а-яё]{{2,3}})?\s*$",
            re.IGNORECASE,
        )

    @staticmethod
    def _first_visible(locator: Locator) -> Locator | None:
        try:
            for index in range(min(locator.count(), 30)):
                candidate = locator.nth(index)
                if candidate.is_visible():
                    return candidate
        except Exception:
            return None
        return None

    def _body_text(self) -> str:
        assert self.page
        try:
            return self.page.locator("body").inner_text(timeout=10_000)
        except Exception:
            return ""

    @staticmethod
    def _normalize_text(value: str) -> str:
        return " ".join(value.split()).casefold().replace("ё", "е")

    @staticmethod
    def _exact_text_pattern(value: str) -> re.Pattern[str]:
        escaped = re.escape(value.strip())
        # На сайте встречаются оба написания «е/ё».
        escaped = escaped.replace("ё", "[её]").replace("Ё", "[ЕЁ]")
        return re.compile(rf"^\s*{escaped}\s*$", re.IGNORECASE)

    def _dismiss_cookie_banner(self) -> None:
        assert self.page
        buttons = self.page.get_by_role(
            "button", name=re.compile(r"^\s*Хорошо\s*$", re.IGNORECASE)
        )
        try:
            for index in range(min(buttons.count(), 10)):
                item = buttons.nth(index)
                if item.is_visible():
                    # У баннера Аэрофлота бывают два перекрывающихся экземпляра.
                    item.evaluate("(element) => element.click()")
                    self.page.wait_for_timeout(300)
                    return
        except Exception:
            self.logger.debug("Не удалось закрыть баннер cookies", exc_info=True)

    def _choose_form_value(self, placeholder: str, value: str) -> bool:
        assert self.page
        field = self._first_visible(
            self.page.get_by_placeholder(self._exact_text_pattern(placeholder))
        )
        if field is None:
            return False
        try:
            if self._normalize_text(
                field.input_value(timeout=1_000)
            ) == self._normalize_text(value):
                return True
        except Exception:
            pass
        field.click()
        self.page.wait_for_timeout(500)
        option = self._first_visible(
            self.page.get_by_text(self._exact_text_pattern(value))
        )
        if option is None:
            return False
        option.click()
        self.page.wait_for_timeout(900)
        try:
            return self._normalize_text(
                field.input_value(timeout=1_000)
            ) == self._normalize_text(value)
        except Exception:
            return True

    def _calendar_day(self, value: date) -> Locator | None:
        assert self.page
        month_title = MONTHS_RU_NOMINATIVE[value.month]
        months = self.page.locator("[data-month-index]").filter(
            has_text=re.compile(re.escape(month_title), re.IGNORECASE)
        )
        day_pattern = re.compile(rf"^\s*0?{value.day}\s*$")
        try:
            for month_index in range(min(months.count(), 24)):
                day = self._first_visible(
                    months.nth(month_index).get_by_role("button", name=day_pattern)
                )
                if day is not None:
                    return day
        except Exception:
            return None
        return None

    def _open_date_field(self, placeholder: str) -> Locator | None:
        assert self.page
        field = self._first_visible(
            self.page.get_by_placeholder(self._exact_text_pattern(placeholder))
        )
        if field is None:
            return None
        field.locator("..").click(force=True, timeout=5_000)
        self.page.wait_for_timeout(700)
        return field

    def _choose_initial_outbound(self, outbound: date) -> bool:
        assert self.page
        outbound_field = self._open_date_field("Туда")
        if outbound_field is None:
            return False
        day = self._calendar_day(outbound)
        if day is None:
            outbound_field.click(force=True, timeout=5_000)
            self.page.wait_for_timeout(700)
            day = self._calendar_day(outbound)
        if day is None:
            return False
        day.click()
        self.page.wait_for_timeout(800)

        return_date = self.settings.route.return_date
        if return_date is None:
            no_return = self._first_visible(
                self.page.get_by_text(
                    re.compile(
                        r"^\s*Обратный билет не нужен\s*$", re.IGNORECASE
                    )
                )
            )
            if no_return is not None:
                no_return.click()
                self.page.wait_for_timeout(500)
        else:
            return_day = self._calendar_day(return_date)
            if return_day is None:
                if self._open_date_field("Обратно") is None:
                    return False
                return_day = self._calendar_day(return_date)
            if return_day is None:
                return False
            return_day.click()
            self.page.wait_for_timeout(800)
        self.last_selected_date = outbound
        return True

    def _configure_passengers(self) -> bool:
        assert self.page
        passenger_field: Locator | None = None
        fields = self.page.locator("input")
        field_pattern = re.compile(r"^\s*(\d+),\s*(.+?)\s*$", re.IGNORECASE)
        try:
            for index in range(min(fields.count(), 30)):
                item = fields.nth(index)
                if not item.is_visible():
                    continue
                match = field_pattern.match(item.input_value(timeout=1_000))
                if match:
                    passenger_field = item
                    current_cabin = match.group(2).strip()
                    break
        except Exception:
            self.logger.debug("Не удалось прочитать поле пассажиров", exc_info=True)
            return False
        if passenger_field is None:
            self.logger.debug("Поле пассажиров не найдено")
            return False

        self._dismiss_cookie_banner()
        passenger_field.locator("..").click(force=True, timeout=5_000)

        def wait_for_group(label: str) -> Locator | None:
            for _ in range(12):
                candidate = self._first_visible(
                    self.page.get_by_text(self._exact_text_pattern(label))
                )
                if candidate is not None:
                    return candidate
                self.page.wait_for_timeout(250)
            return None

        first_group = self.settings.route.passengers[0]
        group_node = wait_for_group(first_group.label)
        if group_node is None:
            # В части адаптивных раскладок обработчик висит на самом input.
            passenger_field.click(force=True, timeout=5_000)
            group_node = wait_for_group(first_group.label)
        if group_node is None:
            self.logger.warning(
                "В окне пассажиров не найдена строка %r", first_group.label
            )
            return False

        if self._normalize_text(current_cabin) != self._normalize_text(
            self.settings.route.cabin
        ):
            cabin = self._first_visible(
                self.page.get_by_text(
                    self._exact_text_pattern(self.settings.route.cabin)
                )
            )
            if cabin is None:
                self.logger.warning(
                    "В окне пассажиров не найден класс %r",
                    self.settings.route.cabin,
                )
                return False
            cabin.click()
            self.page.wait_for_timeout(400)

        for group in self.settings.route.passengers:
            node = wait_for_group(group.label)
            if node is None:
                self.logger.warning(
                    "В окне пассажиров не найдена строка %r", group.label
                )
                return False
            row = node.locator("xpath=ancestor::div[.//button][1]")
            buttons = row.get_by_role("button")
            if buttons.count() < 2:
                self.logger.warning(
                    "В строке пассажира %r не найдены кнопки +/-", group.label
                )
                return False
            numbers = [
                int(line.strip())
                for line in row.inner_text().splitlines()
                if re.fullmatch(r"\s*\d+\s*", line)
            ]
            current_count = numbers[-1] if numbers else 0
            delta = group.count - current_count
            control = buttons.last if delta > 0 else buttons.first
            for _ in range(abs(delta)):
                if not control.is_enabled():
                    self.logger.warning(
                        "Нельзя установить %r = %d", group.label, group.count
                    )
                    return False
                control.evaluate("(element) => element.click()")
                self.page.wait_for_timeout(200)

        continue_button = self._first_visible(
            self.page.get_by_role(
                "button", name=re.compile(r"^\s*Продолжить\s*$", re.IGNORECASE)
            )
        )
        if continue_button is None:
            # В широком интерфейсе это выпадающая панель без отдельной кнопки.
            self.page.keyboard.press("Escape")
        else:
            # В узком интерфейсе сайт показывает полноэкранное модальное окно.
            continue_button.evaluate("(element) => element.click()")
        self.page.wait_for_timeout(700)
        try:
            final_value = passenger_field.input_value(timeout=1_000)
            final_match = field_pattern.match(final_value)
            return bool(
                final_match
                and int(final_match.group(1))
                == self.settings.route.passenger_count
                and self._normalize_text(final_match.group(2))
                == self._normalize_text(self.settings.route.cabin)
            )
        except Exception:
            self.logger.debug(
                "Не удалось подтвердить итоговое число пассажиров", exc_info=True
            )
            return False

    def _recover_search_context(self, outbound: date) -> bool:
        """Восстанавливает форму после перезапуска SPA с постоянным профилем."""
        assert self.page
        program = self._first_visible(
            self.page.get_by_placeholder(
                re.compile(
                    r"^\s*Программа субсидирования\s*$", re.IGNORECASE
                )
            )
        )
        if program is None:
            self.page.goto(
                self.settings.browser.search_url,
                wait_until="domcontentloaded",
                timeout=self.settings.browser.timeout_seconds * 1000,
            )
            self.page.wait_for_function(
                "() => document.body && document.body.innerText.length > 100",
                timeout=self.settings.browser.timeout_seconds * 1000,
            )
            self.page.wait_for_timeout(1_500)

        self._dismiss_cookie_banner()
        steps = (
            self._choose_form_value(
                "Программа субсидирования",
                self.settings.route.subsidy_program,
            ),
            self._choose_form_value("Откуда", self.settings.route.origin),
            self._choose_form_value("Куда", self.settings.route.destination),
        )
        if not all(steps):
            self.logger.warning("Не удалось восстановить поля маршрута")
            return False
        if not self._choose_initial_outbound(outbound):
            self.logger.warning("Не удалось выбрать начальную дату %s", outbound)
            return False
        self.logger.info("Начальная дата %s установлена", outbound)
        self.page.wait_for_timeout(1_200)
        if not self._configure_passengers():
            self.logger.warning("Не удалось настроить пассажиров")
            return False
        self.logger.info(
            "Пассажиры настроены: %s",
            ", ".join(
                f"{item.label} — {item.count}"
                for item in self.settings.route.passengers
            ),
        )

        search = self._first_visible(
            self.page.get_by_role(
                "button", name=re.compile(r"^\s*Найти\s*$", re.IGNORECASE)
            )
        )
        if search is None or not search.is_enabled():
            self.logger.warning("Кнопка поиска не найдена или недоступна")
            return False
        search.evaluate("(element) => element.click()")
        trip_description = (
            "без обратного билета"
            if self.settings.route.return_date is None
            else f"обратно {self.settings.route.return_date}"
        )
        self.logger.info(
            "Форма поиска заполнена автоматически: программа %r, %s → %s, "
            "туда %s, %s",
            self.settings.route.subsidy_program,
            self.settings.route.origin,
            self.settings.route.destination,
            outbound,
            trip_description,
        )
        self.page.wait_for_timeout(2_000)
        return True

    def _route_context_signals(self) -> tuple[bool, list[str]]:
        text = self._body_text()
        if self.page:
            try:
                values = []
                inputs = self.page.locator("input")
                for index in range(min(inputs.count(), 30)):
                    item = inputs.nth(index)
                    if item.is_visible():
                        value = item.input_value(timeout=1_000).strip()
                        if value:
                            values.append(value)
                text = f"{text}\n" + "\n".join(values)
            except Exception:
                self.logger.debug("Не удалось прочитать значения полей поиска")
        text = text.lower()
        signals: list[str] = []
        origin_ok = self.settings.route.origin.lower() in text
        destination_ok = self.settings.route.destination.lower() in text
        if origin_ok:
            signals.append("origin_seen")
        if destination_ok:
            signals.append("destination_seen")
        return origin_ok and destination_ok, signals

    def _select_date(self, outbound: date) -> bool:
        assert self.page
        pattern = self._date_pattern(outbound)

        # Сначала используем семантическую кнопку в ленте дат/календаре.
        candidate = self._first_visible(self.page.get_by_role("button", name=pattern))
        if candidate:
            if not candidate.is_enabled():
                self.logger.debug("Карточка даты %s недоступна для нажатия", outbound)
                return False
            selected = (
                candidate.get_attribute("aria-selected") == "true"
                or candidate.get_attribute("aria-pressed") == "true"
            )
            if not selected:
                candidate.click()
                self.page.wait_for_timeout(1_500)
            self.last_selected_date = outbound
            self.logger.info(
                "Дата %s выбрана через семантическую кнопку", outbound
            )
            return True

        # На текущем интерфейсе Аэрофлота карточки ленты не имеют role=button.
        # Кликаем точную видимую подпись вида «29 авг, сб»: событие всплывает к
        # кликабельной карточке, при этом CSS-классы сайта нам не нужны.
        candidate = self._first_visible(
            self.page.get_by_text(self._date_label_pattern(outbound))
        )
        if candidate is None:
            # Запасной вариант для DOM, где дата и цена находятся в одном
            # текстовом узле карточки.
            matches = self.page.get_by_text(pattern)
            visible_matches: list[tuple[int, Locator]] = []
            try:
                for index in range(min(matches.count(), 30)):
                    item = matches.nth(index)
                    if item.is_visible():
                        visible_matches.append((len(item.inner_text()), item))
            except Exception:
                visible_matches = []
            if visible_matches:
                candidate = min(visible_matches, key=lambda item: item[0])[1]
        if candidate:
            try:
                candidate.click()
                self.page.wait_for_timeout(1_500)
                self.last_selected_date = outbound
                self.logger.info(
                    "Дата %s выбрана через текстовую карточку ленты", outbound
                )
                return True
            except Exception:
                self.logger.debug(
                    "Текст даты найден, но карточка не нажалась", exc_info=True
                )

        # Если лента скрыта, открываем поле даты и снова ищем дату по тексту.
        date_field = self._first_visible(
            self.page.get_by_label(self.locators.outbound_field)
        )
        if date_field is None:
            date_field = self._first_visible(
                self.page.get_by_role("button", name=self.locators.outbound_field)
            )
        if date_field:
            date_field.click()
            candidate = self._first_visible(self.page.get_by_text(pattern))
            if candidate:
                candidate.click()
                self.page.wait_for_timeout(1_500)
                self.last_selected_date = outbound
                self.logger.info(
                    "Дата %s выбрана через календарное поле", outbound
                )
                return True
        return False

    def _date_card_has_no_flights(self, outbound: date) -> bool:
        """Проверяет именно карточку нужной даты, не соседние даты ленты."""
        assert self.page
        date_pattern = self._date_pattern(outbound)
        no_flights = re.compile(r"\bрейсов\s+нет\b", re.IGNORECASE)
        sources = (
            self.page.get_by_role("button", name=date_pattern),
            self.page.get_by_text(date_pattern),
        )
        for source in sources:
            try:
                for index in range(min(source.count(), 30)):
                    node = source.nth(index)
                    if not node.is_visible():
                        continue
                    node_text = " ".join(node.inner_text().split())
                    if (
                        date_pattern.search(node_text)
                        and not node.is_enabled()
                    ):
                        return True
                    current = node
                    # Самая маленькая карточка содержит дату и подпись
                    # «Рейсов нет». До общего контейнера всей ленты не доходим.
                    for _ in range(5):
                        text = " ".join(current.inner_text().split())
                        if (
                            len(text) <= 240
                            and date_pattern.search(text)
                            and no_flights.search(text)
                        ):
                            return True
                        current = current.locator("..")
            except Exception:
                self.logger.debug(
                    "Не удалось проверить карточку даты %s", outbound,
                    exc_info=True,
                )
        return False

    def _click_search_if_available(self) -> bool:
        assert self.page
        button = self._first_visible(
            self.page.get_by_role("button", name=self.locators.search_button)
        )
        if not button:
            return False
        try:
            if button.is_enabled():
                button.click()
                return True
        except Exception:
            return False
        return False

    def _wait_for_result(self, outbound: date) -> DetectionResult:
        assert self.page
        deadline = time.monotonic() + self.settings.browser.timeout_seconds
        last = DetectionResult(DetectionState.UNKNOWN, ("result_wait_started",))
        while time.monotonic() < deadline:
            try:
                content = self.page.content()
            except Exception:
                time.sleep(1)
                continue
            last = classify_html(
                content,
                outbound,
                self.settings.route.return_date,
                self.settings.route.origin,
                self.settings.route.destination,
                self.settings.route.passenger_count,
                selected_date_confirmed=self.last_selected_date == outbound,
            )
            if last.state != DetectionState.UNKNOWN:
                return last
            if self._date_card_has_no_flights(outbound):
                self.last_selected_date = outbound
                self.logger.info("Для даты %s карточка сообщает «Рейсов нет»", outbound)
                return DetectionResult(
                    DetectionState.UNAVAILABLE,
                    ("date_card:no_flights",),
                )
            self.page.wait_for_timeout(1_000)
        return last

    def check_date(
        self, outbound: date, *, refresh_page: bool = True
    ) -> BrowserCheck:
        started = time.monotonic()
        screenshot: Path | None = None
        diagnostic: Path | None = None
        try:
            if refresh_page:
                self._refresh_search_form(outbound)
            else:
                self.ensure_search_page()
                self.logger.info(
                    "Без обновления страницы переключаемся на дату %s",
                    outbound,
                )
            assert self.page
            preliminary = classify_html(
                self.page.content(),
                outbound,
                self.settings.route.return_date,
                self.settings.route.origin,
                self.settings.route.destination,
                self.settings.route.passenger_count,
            )
            if preliminary.state in {
                DetectionState.BLOCKED,
                DetectionState.SESSION_EXPIRED,
                DetectionState.NETWORK_ERROR,
            }:
                result = preliminary
            elif refresh_page:
                if not self._recover_search_context(outbound):
                    result = DetectionResult(
                        DetectionState.UNKNOWN,
                        ("fresh_search_form_not_completed",),
                    )
                else:
                    result = self._wait_for_result(outbound)
            elif self._date_card_has_no_flights(outbound):
                self.last_selected_date = outbound
                self.logger.info(
                    "Дата %s недоступна: в карточке указано «Рейсов нет»",
                    outbound,
                )
                result = DetectionResult(
                    DetectionState.UNAVAILABLE,
                    ("date_card:no_flights",),
                )
            elif not self._select_date(outbound):
                # Дата может быть за стрелкой текущего диапазона ленты. Вместо
                # UNKNOWN выполняем надёжный свежий поиск через исходную форму.
                self.logger.info(
                    "Дата %s отсутствует в видимой ленте; выполняем свежий поиск",
                    outbound,
                )
                self._refresh_search_form(outbound)
                if not self._recover_search_context(outbound):
                    result = DetectionResult(
                        DetectionState.UNKNOWN,
                        ("fallback_search_form_not_completed",),
                    )
                else:
                    result = self._wait_for_result(outbound)
            else:
                # Смена карточки запускает новый запрос внутри SPA. Небольшая
                # задержка не даёт классификатору прочитать старый результат.
                self.page.wait_for_timeout(1_500)
                result = self._wait_for_result(outbound)
        except PlaywrightTimeoutError:
            result = DetectionResult(
                DetectionState.NETWORK_ERROR, ("playwright_timeout",)
            )
        except Exception as exc:
            self.logger.warning(
                "Ошибка браузера типа %s (детали в debug-логе)", type(exc).__name__
            )
            self.logger.debug("Ошибка браузера", exc_info=True)
            result = DetectionResult(
                DetectionState.NETWORK_ERROR, (f"browser_error:{type(exc).__name__}",)
            )

        if result.state in {
            DetectionState.AVAILABLE,
            DetectionState.UNKNOWN,
            DetectionState.BLOCKED,
            DetectionState.NETWORK_ERROR,
            DetectionState.SESSION_EXPIRED,
        }:
            screenshot = self.screenshot(
                f"{outbound.isoformat()}-{result.state.value.lower()}"
            )
        if result.state == DetectionState.UNKNOWN and self.settings.safety.save_unknown_html:
            diagnostic = self.save_limited_diagnostic(outbound)
        return BrowserCheck(
            result=result,
            screenshot=screenshot,
            diagnostic_file=diagnostic,
            duration_seconds=time.monotonic() - started,
        )

    def screenshot(self, label: str) -> Path | None:
        if not self.page or self.page.is_closed():
            return None
        safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "-", label).strip("-")[:80]
        path = self.settings.artifacts_dir / (
            f"{datetime.now():%Y%m%d-%H%M%S}-{safe_label}.png"
        )
        try:
            self.page.screenshot(path=str(path), full_page=True)
            return path
        except Exception:
            self.logger.debug("Не удалось сохранить скриншот", exc_info=True)
            return None

    @staticmethod
    def _redact_text(value: str) -> str:
        value = re.sub(
            r"\b\d{13,19}\b", "[СКРЫТО: возможный номер карты]", value
        )
        value = re.sub(
            r"(?i)(паспорт|passport)\s*[:№]?\s*[A-ZА-Я0-9 -]{5,}",
            r"\1: [СКРЫТО]",
            value,
        )
        value = re.sub(
            r"(?i)(token|токен)\s*[:=]\s*\S+", r"\1=[СКРЫТО]", value
        )
        return value

    def save_limited_diagnostic(self, outbound: date) -> Path | None:
        if not self.page:
            return None
        try:
            text = self._redact_text(self._body_text())[:50_000]
            path = self.settings.artifacts_dir / (
                f"{datetime.now():%Y%m%d-%H%M%S}-{outbound.isoformat()}-diagnostic.html"
            )
            # Это не полный DOM: только обезличенный видимый текст.
            document = (
                "<!doctype html><meta charset='utf-8'>"
                "<title>Ограниченная диагностика</title><pre>"
                f"{html_module.escape(text)}</pre>"
            )
            path.write_text(document, encoding="utf-8")
            return path
        except Exception:
            self.logger.debug("Не удалось сохранить диагностику", exc_info=True)
            return None

    def diagnose(self) -> tuple[dict[str, Any], Path | None]:
        page = self.ensure_search_page()

        def texts(locator: Locator, limit: int = 40) -> list[str]:
            result: list[str] = []
            try:
                for index in range(min(locator.count(), limit)):
                    value = " ".join(locator.nth(index).inner_text().split())
                    value = self._redact_text(value)
                    if value and value not in result:
                        result.append(value[:300])
            except Exception:
                pass
            return result

        body = self._body_text()
        date_lines = [
            line[:200]
            for line in body.splitlines()
            if re.search(
                r"\b\d{1,2}\s+(?:августа|сентября)\b|\b\d{2}[./]\d{2}\b",
                line,
                re.IGNORECASE,
            )
        ][:40]
        regions = texts(page.get_by_role("region"), 20)
        report: dict[str, Any] = {
            "generated_at": datetime.now().astimezone().isoformat(),
            "url": page.url,
            "title": page.title(),
            "headings": texts(page.get_by_role("heading")),
            "buttons": texts(page.get_by_role("button")),
            "date_texts": date_lines,
            "candidate_regions": regions,
            "note": "Отчёт содержит только ограниченный видимый текст, без cookies и HTML DOM.",
        }
        path = self.settings.artifacts_dir / (
            f"{datetime.now():%Y%m%d-%H%M%S}-diagnose.json"
        )
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report, self.screenshot("diagnose")
