from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from html.parser import HTMLParser


class DetectionState(str, Enum):
    UNAVAILABLE = "UNAVAILABLE"
    AVAILABLE = "AVAILABLE"
    BLOCKED = "BLOCKED"
    SESSION_EXPIRED = "SESSION_EXPIRED"
    NETWORK_ERROR = "NETWORK_ERROR"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class DetectionResult:
    state: DetectionState
    signals: tuple[str, ...] = field(default_factory=tuple)
    price: str | None = None


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str]
    own_text: list[str] = field(default_factory=list)
    children: list["_Node"] = field(default_factory=list)
    parent: "_Node | None" = field(default=None, repr=False)

    def text(self) -> str:
        style = self.attrs.get("style", "").replace(" ", "").lower()
        if (
            self.tag in {"script", "style", "template", "noscript"}
            or "hidden" in self.attrs
            or self.attrs.get("aria-hidden", "").lower() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
        ):
            return ""
        parts = [*self.own_text]
        for child in self.children:
            parts.append(child.text())
        return " ".join(parts)


class _TreeParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", {})
        self.stack = [self.root]

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        node = _Node(
            tag.lower(),
            {k.lower(): v or "" for k, v in attrs},
            parent=self.stack[-1],
        )
        self.stack[-1].children.append(node)
        if tag.lower() not in {
            "area", "base", "br", "col", "embed", "hr", "img", "input",
            "link", "meta", "param", "source", "track", "wbr",
        }:
            self.stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag.lower():
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        value = " ".join(data.split())
        if value:
            self.stack[-1].own_text.append(value)


BLOCK_PATTERNS = (
    r"\bcaptcha\b",
    r"я не робот",
    r"доступ временно ограничен",
    r"\baccess denied\b",
    r"\b(?:http\s*)?(?:403|429)\b",
    r"код блокировки",
    r"слишком много запросов",
    r"проверка вашего веб-браузера",
)
SESSION_PATTERNS = (
    r"сессия (?:истекла|завершена)",
    r"войдите в (?:систему|личный кабинет)",
    r"session expired",
)
NETWORK_PATTERNS = (
    r"нет подключения к интернету",
    r"проверьте подключение",
    r"\b(?:err_internet_disconnected|err_name_not_resolved)\b",
    r"network error",
)
UNAVAILABLE_PATTERNS = (
    r"на выбранную дату субсидированные билеты не найдены",
    r"выберите другую дату вылета",
)
PRICE_RE = re.compile(
    r"(?:от\s*)?\d{1,3}(?:[\s\u00a0]\d{3})+(?:[,.]\d{2})?\s*(?:₽|руб(?:\.|лей)?)",
    re.IGNORECASE,
)
SUBSIDY_RE = re.compile(r"субсид\w*|льготн\w*", re.IGNORECASE)
SUBSIDY_PAGE_RE = re.compile(
    r"субсидированн\w*\s+(?:рейс\w*|перевоз\w*|билет\w*)",
    re.IGNORECASE,
)
ACTION_RE = re.compile(
    r"выбрать|продолжить|далее|к выбору|оформить", re.IGNORECASE
)
FLIGHT_CONTEXT_RE = re.compile(
    r"\b(?:в пути|о рейсе|время вылета)\b|\b[A-ZА-ЯЁ]{2}\s*\d{3,4}\b",
    re.IGNORECASE,
)


def _normalized(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split()).lower()


def _matches_any(text: str, patterns: tuple[str, ...]) -> str | None:
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def _date_variants(value: date) -> tuple[str, ...]:
    months = (
        "", "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    )
    short_months = (
        "", "янв", "фев", "мар", "апр", "мая", "июн",
        "июл", "авг", "сен", "окт", "ноя", "дек",
    )
    return (
        value.isoformat(),
        value.strftime("%d.%m.%Y"),
        value.strftime("%d.%m"),
        f"{value.day} {months[value.month]} {value.year}",
        f"{value.day} {months[value.month]}",
        f"{value.day} {short_months[value.month]}",
    )


def _contains_date(text: str, value: date) -> bool:
    lowered = _normalized(text)
    return any(variant.lower() in lowered for variant in _date_variants(value))


def _is_action_element(node: _Node) -> bool:
    """Цена на текущем сайте сама является кнопкой выбора тарифа."""
    role = node.attrs.get("role", "").lower()
    if node.tag != "button" and role != "button":
        return False
    text = node.text().strip()
    return bool(ACTION_RE.search(text) or PRICE_RE.fullmatch(text))


def _contains_action(node: _Node) -> bool:
    if _is_action_element(node):
        return True
    return any(_contains_action(child) for child in node.children)


def _candidate_nodes(root: _Node) -> list[_Node]:
    result: list[_Node] = []

    def is_semantic(node: _Node) -> bool:
        attrs = " ".join(node.attrs.values()).lower()
        return (
            node.tag in {"article", "li"}
            or node.attrs.get("role", "").lower() in {"article", "listitem"}
            or "результат" in attrs
            or "result" in attrs
            or "рейс" in attrs
            or "flight" in attrs
        )

    def walk(node: _Node) -> None:
        if is_semantic(node) and len(node.text()) >= 20:
            result.append(node)
        for child in node.children:
            walk(child)

    walk(root)

    # Не объединяем цену одной карточки, слово «субсидия» из заголовка и кнопку
    # другой карточки в ложный положительный результат. Берём самые внутренние
    # семантические контейнеры.
    def has_semantic_descendant(node: _Node) -> bool:
        for child in node.children:
            if (is_semantic(child) and len(child.text()) >= 20) or has_semantic_descendant(child):
                return True
        return False

    candidates = [node for node in result if not has_semantic_descendant(node)]

    # Если сайт не назначил карточке semantic role, идём от кнопки действия к
    # ближайшему контейнеру с ценой и признаками рейса. В актуальном интерфейсе
    # синяя кнопка содержит только «7 400 ₽», без слова «Выбрать».
    def walk_actions(node: _Node) -> None:
        if _is_action_element(node):
            ancestor: _Node | None = node
            for _ in range(9):
                if ancestor is None:
                    break
                text = ancestor.text()
                if (
                    PRICE_RE.search(text)
                    and len(text) >= 20
                    and FLIGHT_CONTEXT_RE.search(text)
                ):
                    candidates.append(ancestor)
                    break
                ancestor = ancestor.parent
        for child in node.children:
            walk_actions(child)

    walk_actions(root)
    unique: list[_Node] = []
    seen: set[int] = set()
    for node in candidates:
        marker = id(node)
        if marker not in seen:
            seen.add(marker)
            unique.append(node)
    return unique


def classify_html(
    html: str,
    outbound_date: date,
    return_date: date | None,
    origin: str,
    destination: str,
    passenger_count: int = 1,
    *,
    selected_date_confirmed: bool = False,
) -> DetectionResult:
    """Консервативно классифицировать уже отображённый DOM."""
    parser = _TreeParser()
    try:
        parser.feed(html)
    except Exception:
        return DetectionResult(DetectionState.UNKNOWN, ("html_parse_error",))
    full_text_original = parser.root.text()
    full_text = _normalized(full_text_original)

    blocked = _matches_any(full_text, BLOCK_PATTERNS)
    if blocked:
        return DetectionResult(DetectionState.BLOCKED, (f"blocked:{blocked}",))
    session = _matches_any(full_text, SESSION_PATTERNS)
    if session:
        return DetectionResult(
            DetectionState.SESSION_EXPIRED, (f"session:{session}",)
        )
    network = _matches_any(full_text, NETWORK_PATTERNS)
    if network:
        return DetectionResult(
            DetectionState.NETWORK_ERROR, (f"network:{network}",)
        )
    unavailable = _matches_any(full_text, UNAVAILABLE_PATTERNS)
    if unavailable:
        return DetectionResult(
            DetectionState.UNAVAILABLE, (f"negative:{unavailable}",)
        )

    page_date = _contains_date(full_text_original, outbound_date)
    page_return = (
        return_date is None or _contains_date(full_text_original, return_date)
    )
    page_route = (
        _normalized(origin) in full_text and _normalized(destination) in full_text
    )
    candidates = _candidate_nodes(parser.root)
    for node in candidates:
        text = node.text()
        normalized = _normalized(text)
        price = PRICE_RE.search(text)
        subsidy = SUBSIDY_RE.search(text)
        action = _contains_action(node)
        direction = (
            _normalized(origin) in normalized and _normalized(destination) in normalized
        )
        flight_context = bool(direction or FLIGHT_CONTEXT_RE.search(text))
        # Дата должна находиться в самой карточке. Наличие даты только в верхней
        # календарной ленте недостаточно.
        outbound_ok = (
            _contains_date(text, outbound_date) or selected_date_confirmed
        )
        passenger_ok = passenger_count == 1 or bool(
            re.search(
                rf"\b{passenger_count}\s*(?:пассажир|мест)", normalized
            )
        )
        if (
            price
            and (subsidy or SUBSIDY_PAGE_RE.search(full_text_original))
            and action
            and flight_context
            and (direction or page_route)
            and outbound_ok
            and page_return
            and passenger_ok
        ):
            signals = [
                "result_card",
                "price_in_card",
                "continuation_action",
                "flight_context",
                "route_confirmed",
                "outbound_date_confirmed",
                f"passengers:{passenger_count}",
            ]
            signals.append(
                "subsidy_marker_in_card"
                if subsidy
                else "subsidy_page_context"
            )
            if return_date is not None:
                signals.append("return_date_in_search_context")
            return DetectionResult(
                DetectionState.AVAILABLE,
                tuple(signals),
                " ".join(price.group(0).split()),
            )

    signals = []
    if page_date:
        signals.append("outbound_date_seen")
    if return_date is not None and page_return:
        signals.append("return_date_seen")
    if page_route:
        signals.append("route_seen")
    if PRICE_RE.search(full_text_original):
        signals.append("price_seen_outside_confirmed_card")
    if candidates:
        signals.append(f"candidate_nodes:{len(candidates)}")
    return DetectionResult(DetectionState.UNKNOWN, tuple(signals or ["no_known_signals"]))
