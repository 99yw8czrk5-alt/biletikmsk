import unittest

from flight_watch.monitor import (
    Settings,
    Ticket,
    baggage_matches,
    dedupe_key,
    filter_tickets,
    format_alert,
    format_help_message,
    format_ticket_list,
    handle_telegram_update,
    handle_webhook_payload,
    is_price_command,
    is_start_command,
    nearest_tickets,
    next_offset,
    process_updates,
    render_webhook_url,
    startup_offset,
)


class FlightWatchTests(unittest.TestCase):
    def test_filter_tickets_keeps_cheap_matching_route(self):
        tickets = [
            Ticket("MOW", "BKK", "2026-08-10", 45000, "rub", "cheap", "unknown"),
            Ticket("MOW", "BKK", "2026-08-11", 80000, "rub", "expensive", "unknown"),
        ]
        result = filter_tickets(tickets, max_price=60000, baggage_mode="allow_unknown")
        self.assertEqual([ticket.link for ticket in result], ["cheap"])

    def test_baggage_modes(self):
        self.assertTrue(baggage_matches("included", "strict"))
        self.assertFalse(baggage_matches("unknown", "strict"))
        self.assertTrue(baggage_matches("unknown", "allow_unknown"))
        self.assertFalse(baggage_matches("not_included", "allow_unknown"))

    def test_dedupe_key_is_stable_for_same_ticket(self):
        ticket = Ticket("MOW", "BKK", "2026-09-01", 51234, "rub", "https://example.com/ticket", "included")
        self.assertEqual(dedupe_key(ticket), "MOW:BKK:2026-09-01:51234:https://example.com/ticket")

    def test_format_alert_mentions_baggage_uncertainty(self):
        ticket = Ticket("MOW", "BKK", "2026-08-10", 45000, "rub", "https://example.com/cheap", "unknown")
        message = format_alert(ticket)
        self.assertIn("MOW -> BKK", message)
        self.assertIn("45 000 RUB", message)
        self.assertIn("Багаж: надо проверить", message)

    def test_commands(self):
        self.assertTrue(is_price_command("цена"))
        self.assertTrue(is_price_command("/price@my_bot"))
        self.assertFalse(is_price_command("привет"))
        self.assertTrue(is_start_command("/start@my_bot"))
        self.assertFalse(is_start_command("цена"))

    def test_format_help_message_explains_price_command(self):
        message = format_help_message()
        self.assertIn("цена", message)
        self.assertIn("/price", message)
        self.assertIn("Москва", message)

    def test_nearest_tickets_sorts_by_departure_date_then_price(self):
        tickets = [
            Ticket("MOW", "BKK", "2026-09-10", 30000, "rub", "late", "unknown"),
            Ticket("MOW", "BKK", "2026-08-01", 50000, "rub", "soon-expensive", "unknown"),
            Ticket("MOW", "BKK", "2026-08-01", 42000, "rub", "soon-cheap", "unknown"),
        ]
        self.assertEqual([ticket.link for ticket in nearest_tickets(tickets, limit=2)], ["soon-cheap", "soon-expensive"])

    def test_format_ticket_list_limits_and_numbers_results(self):
        tickets = [
            Ticket("MOW", "BKK", "2026-08-01", 42000, "rub", "https://example.com/1", "unknown"),
            Ticket("MOW", "BKK", "2026-08-02", 43000, "rub", "https://example.com/2", "included"),
        ]
        message = format_ticket_list(tickets, limit=1)
        self.assertIn("1. 2026-08-01", message)
        self.assertIn("42 000 RUB", message)
        self.assertNotIn("2026-08-02", message)

    def test_format_ticket_list_handles_empty_results(self):
        self.assertIn("Подходящих билетов сейчас не нашёл", format_ticket_list([], limit=5))

    def test_offsets(self):
        self.assertEqual(next_offset([{"update_id": 10}, {"update_id": 12}]), 13)
        self.assertIsNone(next_offset([]))
        self.assertEqual(startup_offset(saved_offset=7, existing_updates=[{"update_id": 10}]), 7)
        self.assertEqual(startup_offset(saved_offset=None, existing_updates=[{"update_id": 10}, {"update_id": 11}]), 12)

    def test_process_updates_returns_next_offset(self):
        handled = []
        result = process_updates(
            [{"update_id": 20}, {"update_id": 21}],
            lambda update: handled.append(update["update_id"]),
        )
        self.assertEqual(result, 22)
        self.assertEqual(handled, [20, 21])

    def test_price_command_sends_searching_message_before_results(self):
        settings = Settings("travel-token", "telegram-token", "1", origin="MOW", destination="BKK")
        sent_messages = []

        def fake_fetch_tickets(settings):
            return [Ticket("MOW", "BKK", "2026-08-01", 42000, "rub", "https://example.com/1", "unknown")]

        def fake_send(settings, chat_id, message):
            sent_messages.append((chat_id, message))

        handle_telegram_update(
            settings,
            {"message": {"chat": {"id": 123}, "text": "цена"}},
            fetch_tickets_func=fake_fetch_tickets,
            send_telegram_func=fake_send,
        )
        self.assertEqual(sent_messages[0], (123, "Ищу билеты Москва -> Бангкок..."))
        self.assertIn("42 000 RUB", sent_messages[1][1])

    def test_webhook_payload_processes_price_command(self):
        settings = Settings("travel-token", "telegram-token", "1", origin="MOW", destination="BKK")
        sent_messages = []

        def fake_fetch_tickets(settings):
            return [Ticket("MOW", "BKK", "2026-08-01", 42000, "rub", "https://example.com/1", "unknown")]

        def fake_send(settings, chat_id, message):
            sent_messages.append((chat_id, message))

        status, body = handle_webhook_payload(
            settings,
            b'{"message":{"chat":{"id":123},"text":"/price"}}',
            fetch_tickets_func=fake_fetch_tickets,
            send_telegram_func=fake_send,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body, b"OK")
        self.assertEqual(sent_messages[0], (123, "Ищу билеты Москва -> Бангкок..."))

    def test_webhook_payload_rejects_invalid_json(self):
        status, body = handle_webhook_payload(Settings("travel-token", "telegram-token", "1"), b"{not-json")
        self.assertEqual(status, 400)
        self.assertEqual(body, b"Invalid JSON")

    def test_render_webhook_url_builds_secret_path(self):
        settings = Settings("travel-token", "telegram-token", "1", webhook_secret="secret value")
        self.assertEqual(render_webhook_url(settings, "https://example.onrender.com/"), "https://example.onrender.com/telegram/secret%20value")


if __name__ == "__main__":
    unittest.main()
