from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterable


TRAVELPAYOUTS_ENDPOINT = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
TELEGRAM_ENDPOINT = "https://api.telegram.org/bot{token}/sendMessage"
HTTP_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class Settings:
    travelpayouts_token: str
    telegram_bot_token: str
    telegram_chat_id: str
    origin: str = "MOW"
    destination: str = "BKK"
    currency: str = "rub"
    max_price: int = 60000
    check_interval_hours: int = 6
    days_ahead: int = 180
    limit: int = 100
    baggage_mode: str = "allow_unknown"
    state_path: Path = Path("data/seen.json")
    telegram_offset_path: Path = Path("data/telegram_offset.json")
    bot_poll_seconds: int = 5
    bot_results_limit: int = 5
    webhook_secret: str = "telegram"
    port: int = 8000


@dataclass(frozen=True)
class Ticket:
    origin: str
    destination: str
    depart_date: str
    price: int
    currency: str
    link: str
    baggage_status: str
    airline: str | None = None
    changes: int | None = None
    duration_minutes: int | None = None


def load_settings(env_path: str | Path = ".env") -> Settings:
    load_env_file(Path(env_path))
    return Settings(
        travelpayouts_token=os.getenv("TRAVELPAYOUTS_TOKEN", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        origin=os.getenv("ORIGIN", "MOW").upper(),
        destination=os.getenv("DESTINATION", "BKK").upper(),
        currency=os.getenv("CURRENCY", "rub").lower(),
        max_price=int(os.getenv("MAX_PRICE", "60000")),
        check_interval_hours=int(os.getenv("CHECK_INTERVAL_HOURS", "6")),
        days_ahead=int(os.getenv("DAYS_AHEAD", "180")),
        limit=int(os.getenv("LIMIT", "100")),
        baggage_mode=os.getenv("BAGGAGE_MODE", "allow_unknown"),
        state_path=Path(os.getenv("STATE_PATH", "data/seen.json")),
        telegram_offset_path=Path(os.getenv("TELEGRAM_OFFSET_PATH", "data/telegram_offset.json")),
        bot_poll_seconds=int(os.getenv("BOT_POLL_SECONDS", "1")),
        bot_results_limit=int(os.getenv("BOT_RESULTS_LIMIT", "5")),
        webhook_secret=os.getenv("WEBHOOK_SECRET", "telegram"),
        port=int(os.getenv("PORT", "8000")),
    )


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _required_env(name: str, value: str) -> str:
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def http_get_json(url: str, params: dict[str, Any]) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(f"{url}?{query}", headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_tickets(settings: Settings) -> list[Ticket]:
    travelpayouts_token = _required_env("TRAVELPAYOUTS_TOKEN", settings.travelpayouts_token)
    params = {
        "origin": settings.origin,
        "destination": settings.destination,
        "currency": settings.currency,
        "departure_at": date.today().isoformat(),
        "return_at": "",
        "sorting": "price",
        "direct": "false",
        "limit": settings.limit,
        "token": travelpayouts_token,
    }
    payload = http_get_json(TRAVELPAYOUTS_ENDPOINT, params)
    return [parse_ticket(item, settings) for item in payload.get("data", [])]


def parse_ticket(item: dict[str, Any], settings: Settings) -> Ticket:
    raw_price = item.get("price") or item.get("value") or 0
    link = item.get("link") or item.get("deep_link") or ""
    if link.startswith("/"):
        link = f"https://www.aviasales.ru{link}"
    return Ticket(
        origin=(item.get("origin") or settings.origin).upper(),
        destination=(item.get("destination") or settings.destination).upper(),
        depart_date=str(item.get("departure_at") or item.get("depart_date") or ""),
        price=int(raw_price),
        currency=settings.currency,
        link=link,
        baggage_status=detect_baggage_status(item),
        airline=item.get("airline"),
        changes=_optional_int(item.get("number_of_changes")),
        duration_minutes=_optional_int(item.get("duration")),
    )


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def detect_baggage_status(item: dict[str, Any]) -> str:
    for value in [item.get("baggage"), item.get("has_baggage"), item.get("bagage"), item.get("with_baggage")]:
        if value is True:
            return "included"
        if value is False:
            return "not_included"
        if isinstance(value, str):
            normalized = value.lower()
            if normalized in {"included", "true", "yes", "1"}:
                return "included"
            if normalized in {"not_included", "false", "no", "0"}:
                return "not_included"
    return "unknown"


def baggage_matches(status: str, mode: str) -> bool:
    if mode == "strict":
        return status == "included"
    if mode == "allow_unknown":
        return status in {"included", "unknown"}
    raise ValueError("BAGGAGE_MODE must be strict or allow_unknown")


def filter_tickets(tickets: Iterable[Ticket], *, max_price: int, baggage_mode: str, days_ahead: int = 180) -> list[Ticket]:
    latest_departure = date.today() + timedelta(days=days_ahead)
    result = []
    for ticket in tickets:
        if ticket.price > max_price or not baggage_matches(ticket.baggage_status, baggage_mode):
            continue
        if ticket.depart_date:
            try:
                depart_date = date.fromisoformat(ticket.depart_date[:10])
            except ValueError:
                depart_date = None
            if depart_date and depart_date > latest_departure:
                continue
        result.append(ticket)
    return sorted(result, key=lambda ticket: ticket.price)


def nearest_tickets(tickets: Iterable[Ticket], *, limit: int) -> list[Ticket]:
    return sorted(tickets, key=lambda ticket: (_ticket_date_key(ticket), ticket.price))[:limit]


def _ticket_date_key(ticket: Ticket) -> str:
    return ticket.depart_date[:10] if ticket.depart_date else "9999-12-31"


def dedupe_key(ticket: Ticket) -> str:
    return f"{ticket.origin}:{ticket.destination}:{ticket.depart_date}:{ticket.price}:{ticket.link}"


def load_seen(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(json.loads(path.read_text(encoding="utf-8")))


def save_seen(path: Path, seen: set[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(seen), indent=2), encoding="utf-8")


def format_alert(ticket: Ticket) -> str:
    baggage_label = {"included": "есть", "not_included": "нет", "unknown": "надо проверить"}.get(ticket.baggage_status, "надо проверить")
    price = f"{ticket.price:,}".replace(",", " ")
    parts = [
        f"Найден билет {ticket.origin} -> {ticket.destination}",
        f"Дата: {ticket.depart_date or 'не указана'}",
        f"Цена: {price} {ticket.currency.upper()}",
        f"Багаж: {baggage_label}",
    ]
    if ticket.airline:
        parts.append(f"Авиакомпания: {ticket.airline}")
    if ticket.changes is not None:
        parts.append(f"Пересадки: {ticket.changes}")
    if ticket.duration_minutes:
        hours, minutes = divmod(ticket.duration_minutes, 60)
        parts.append(f"В пути: {hours}ч {minutes:02d}м")
    parts.append(ticket.link)
    return "\n".join(parts)


def is_price_command(text: str | None) -> bool:
    if not text:
        return False
    command = text.strip().lower()
    if "@" in command and command.startswith("/"):
        command = command.split("@", 1)[0]
    return command in {"цена", "/price", "price"}


def is_start_command(text: str | None) -> bool:
    if not text:
        return False
    command = text.strip().lower()
    if "@" in command and command.startswith("/"):
        command = command.split("@", 1)[0]
    return command == "/start"


def format_help_message() -> str:
    return "Я показываю ближайшие билеты Москва -> Бангкок.\n\nНапиши `цена` или `/price`, и я пришлю актуальные варианты."


def format_searching_message(settings: Settings) -> str:
    labels = {("MOW", "BKK"): ("Москва", "Бангкок")}
    origin, destination = labels.get((settings.origin, settings.destination), (settings.origin, settings.destination))
    return f"Ищу билеты {origin} -> {destination}..."


def format_ticket_list(tickets: Iterable[Ticket], *, limit: int) -> str:
    selected = nearest_tickets(tickets, limit=limit)
    if not selected:
        return "Подходящих билетов сейчас не нашёл. Попробуй позже или подними MAX_PRICE в .env."
    first = selected[0]
    lines = [f"Ближайшие билеты {first.origin} -> {first.destination}", f"Показываю до {limit} вариантов:", ""]
    for index, ticket in enumerate(selected, start=1):
        baggage_label = {"included": "есть", "not_included": "нет", "unknown": "надо проверить"}.get(ticket.baggage_status, "надо проверить")
        price = f"{ticket.price:,}".replace(",", " ")
        lines.extend([f"{index}. {ticket.depart_date[:10] or 'дата не указана'}", f"{price} {ticket.currency.upper()}", f"Багаж: {baggage_label}"])
        if ticket.changes is not None:
            lines.append(f"Пересадки: {ticket.changes}")
        if ticket.link:
            lines.append(ticket.link)
        lines.append("")
    return "\n".join(lines).strip()


def send_telegram(settings: Settings, message: str) -> None:
    chat_id = _required_env("TELEGRAM_CHAT_ID", settings.telegram_chat_id)
    send_telegram_to_chat(settings, chat_id, message)


def send_telegram_to_chat(settings: Settings, chat_id: str | int, message: str) -> None:
    telegram_bot_token = _required_env("TELEGRAM_BOT_TOKEN", settings.telegram_bot_token)
    url = TELEGRAM_ENDPOINT.format(token=telegram_bot_token)
    body = json.dumps({"chat_id": chat_id, "text": message, "disable_web_page_preview": False}).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
        response.read()


def fetch_telegram_updates(settings: Settings, offset: int | None) -> list[dict[str, Any]]:
    telegram_bot_token = _required_env("TELEGRAM_BOT_TOKEN", settings.telegram_bot_token)
    params: dict[str, Any] = {"timeout": 20}
    if offset is not None:
        params["offset"] = offset
    return list(http_get_json(f"https://api.telegram.org/bot{telegram_bot_token}/getUpdates", params).get("result", []))


def next_offset(updates: Iterable[dict[str, Any]]) -> int | None:
    update_ids = [int(update["update_id"]) for update in updates]
    return max(update_ids) + 1 if update_ids else None


def startup_offset(*, saved_offset: int | None, existing_updates: Iterable[dict[str, Any]]) -> int | None:
    return saved_offset if saved_offset is not None else next_offset(existing_updates)


def load_telegram_offset(path: Path) -> int | None:
    if not path.exists():
        return None
    return int(json.loads(path.read_text(encoding="utf-8"))["offset"])


def save_telegram_offset(path: Path, offset: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"offset": offset}, indent=2), encoding="utf-8")


def reply_with_prices(settings: Settings, chat_id: str | int, *, fetch_tickets_func: Callable[[Settings], list[Ticket]] = fetch_tickets, send_telegram_func: Callable[[Settings, str | int, str], None] = send_telegram_to_chat) -> None:
    print(f"Fetching prices for chat_id={chat_id}...")
    tickets = filter_tickets(fetch_tickets_func(settings), max_price=settings.max_price, baggage_mode=settings.baggage_mode, days_ahead=settings.days_ahead)
    send_telegram_func(settings, chat_id, format_ticket_list(tickets, limit=settings.bot_results_limit))
    print(f"Sent price reply to chat_id={chat_id}.")


def handle_telegram_update(settings: Settings, update: dict[str, Any], *, fetch_tickets_func: Callable[[Settings], list[Ticket]] = fetch_tickets, send_telegram_func: Callable[[Settings, str | int, str], None] = send_telegram_to_chat) -> None:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if not chat_id:
        return
    text = message.get("text")
    if text:
        print(f"Received Telegram message from chat_id={chat_id}: {text!r}")
    if is_start_command(text):
        send_telegram_func(settings, chat_id, format_help_message())
        print(f"Sent start help to chat_id={chat_id}.")
    elif is_price_command(text):
        print(f"Price command detected for chat_id={chat_id}.")
        send_telegram_func(settings, chat_id, format_searching_message(settings))
        reply_with_prices(settings, chat_id, fetch_tickets_func=fetch_tickets_func, send_telegram_func=send_telegram_func)
    elif text:
        send_telegram_func(settings, chat_id, format_help_message())
        print(f"Sent generic help to chat_id={chat_id}.")


def process_updates(updates: Iterable[dict[str, Any]], handle_update: Callable[[dict[str, Any]], None]) -> int | None:
    offset = None
    for update in updates:
        update_id = int(update["update_id"])
        handle_update(update)
        offset = update_id + 1
    return offset


def handle_webhook_payload(settings: Settings, payload: bytes, *, fetch_tickets_func: Callable[[Settings], list[Ticket]] = fetch_tickets, send_telegram_func: Callable[[Settings, str | int, str], None] = send_telegram_to_chat) -> tuple[int, bytes]:
    try:
        update = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 400, b"Invalid JSON"
    if not isinstance(update, dict):
        return 400, b"Invalid JSON"
    handle_telegram_update(settings, update, fetch_tickets_func=fetch_tickets_func, send_telegram_func=send_telegram_func)
    return 200, b"OK"


def run_webhook_server(settings: Settings) -> None:
    webhook_path = f"/telegram/{settings.webhook_secret}"

    class WebhookHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._send_response(200, b"Flight Watch is running") if self.path == "/" else self._send_response(404, b"Not Found")

        def do_POST(self) -> None:
            if self.path != webhook_path:
                self._send_response(404, b"Not Found")
                return
            length = int(self.headers.get("Content-Length", "0"))
            status, body = handle_webhook_payload(settings, self.rfile.read(length))
            self._send_response(status, body)

        def log_message(self, format: str, *args: Any) -> None:
            print(f"Webhook server: {format % args}")

        def _send_response(self, status: int, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("0.0.0.0", settings.port), WebhookHandler)
    print(f"Webhook server started on port {settings.port}. Path: {webhook_path}")
    server.serve_forever()


def render_webhook_url(settings: Settings, base_url: str) -> str:
    return f"{base_url.rstrip('/')}/telegram/{urllib.parse.quote(settings.webhook_secret, safe='')}"


def poll_bot_once(settings: Settings) -> int:
    offset = load_telegram_offset(settings.telegram_offset_path)
    if offset is None:
        updates = fetch_telegram_updates(settings, None)
        offset = startup_offset(saved_offset=None, existing_updates=updates)
        if offset is not None:
            save_telegram_offset(settings.telegram_offset_path, offset)
        print("Initialized Telegram offset; old messages skipped.")
        return 0
    updates = fetch_telegram_updates(settings, offset)
    new_offset = process_updates(updates, lambda update: handle_telegram_update(settings, update))
    if new_offset is not None:
        save_telegram_offset(settings.telegram_offset_path, new_offset)
    print(f"Processed {len(updates)} Telegram update(s).")
    return len(updates)


def run_bot(settings: Settings) -> None:
    offset = load_telegram_offset(settings.telegram_offset_path)
    if offset is None:
        offset = startup_offset(saved_offset=None, existing_updates=fetch_telegram_updates(settings, None))
        if offset is not None:
            save_telegram_offset(settings.telegram_offset_path, offset)
        print("Bot started. Old Telegram messages were skipped; send 'цена' now.")
    else:
        print(f"Bot started. Listening from Telegram offset {offset}.")
    while True:
        try:
            updates = fetch_telegram_updates(settings, offset)
            for update in updates:
                update_id = int(update["update_id"])
                try:
                    handle_telegram_update(settings, update)
                except Exception as exc:
                    print(f"Failed to handle update_id={update_id}: {exc!r}")
                offset = update_id + 1
                save_telegram_offset(settings.telegram_offset_path, offset)
        except Exception as exc:
            print(f"Telegram polling failed: {exc!r}")
        time.sleep(settings.bot_poll_seconds)


def check_once(settings: Settings) -> list[Ticket]:
    seen = load_seen(settings.state_path)
    tickets = filter_tickets(fetch_tickets(settings), max_price=settings.max_price, baggage_mode=settings.baggage_mode, days_ahead=settings.days_ahead)
    new_tickets = [ticket for ticket in tickets if _hashed_key(ticket) not in seen]
    for ticket in new_tickets:
        send_telegram(settings, format_alert(ticket))
        seen.add(_hashed_key(ticket))
    save_seen(settings.state_path, seen)
    return new_tickets


def _hashed_key(ticket: Ticket) -> str:
    return hashlib.sha256(dedupe_key(ticket).encode("utf-8")).hexdigest()


def run_daemon(settings: Settings) -> None:
    while True:
        check_once(settings)
        time.sleep(settings.check_interval_hours * 60 * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="Watch cheap MOW -> BKK flights")
    parser.add_argument("--once", action="store_true", help="Run one check and exit")
    parser.add_argument("--bot", action="store_true", help="Listen for Telegram commands")
    parser.add_argument("--poll-once", action="store_true", help="Process Telegram commands once and exit")
    parser.add_argument("--webhook-server", action="store_true", help="Run HTTP webhook server")
    parser.add_argument("--print-webhook-url", metavar="BASE_URL", help="Print Telegram webhook URL for a public base URL")
    args = parser.parse_args()
    settings = load_settings()
    if args.print_webhook_url:
        print(render_webhook_url(settings, args.print_webhook_url))
    elif args.webhook_server:
        run_webhook_server(settings)
    elif args.poll_once:
        print(f"Poll once handled {poll_bot_once(settings)} update(s).")
    elif args.bot:
        run_bot(settings)
    elif args.once:
        print(f"Sent {len(check_once(settings))} new alert(s).")
    else:
        run_daemon(settings)


if __name__ == "__main__":
    main()
