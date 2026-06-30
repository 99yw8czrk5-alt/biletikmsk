from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

from flight_watch.monitor import load_env_file


def main() -> None:
    load_env_file(Path(".env"))
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN in .env")
    with urllib.request.urlopen(f"https://api.telegram.org/bot{token}/getUpdates", timeout=30) as response:
        updates = json.loads(response.read().decode("utf-8")).get("result", [])
    if not updates:
        print("No Telegram updates yet. Send any message to your bot, then run this again.")
        return
    for update in updates:
        message = update.get("message") or update.get("channel_post") or {}
        chat = message.get("chat") or {}
        if chat.get("id"):
            print(f"TELEGRAM_CHAT_ID={chat['id']}")


if __name__ == "__main__":
    main()
