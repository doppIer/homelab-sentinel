import json
import os
import sys
import time
import urllib.request

from dotenv import load_dotenv

ICONS = {"escalate": "🔴", "notify": "🟠", "digest": "🔵", "drop": "⚪"}


def api_call(token, method, payload=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(url, data=data, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)


def format_message(event, result, host="homeserver", analysis=None):
    icon = ICONS.get(result.route, "")
    lines = [
        f"{icon} {result.route.upper()} · {host} · {event.source}/{event.subject}",
        f"{event.kind}",
        f"Decider: {result.choice} ({result.confidence:.2f}) in {result.latency_ms / 1000:.1f}s",
        f"Why: {result.reason}",
        "",
        event.state[:900],
    ]
    if analysis:
        lines += ["", "🧠 Bedrock:", analysis[:1800]]
    return "\n".join(lines)


class Telegram:
    def __init__(self, token, chat_id, cooldown=600, post=None):
        self.token = token
        self.chat_id = chat_id
        self.cooldown = cooldown
        self.post = post or (lambda text: api_call(self.token, "sendMessage", {"chat_id": self.chat_id, "text": text, "link_preview_options": {"is_disabled": True}}))
        self.sent = {}

    @classmethod
    def from_env(cls):
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        if not token or not chat_id:
            return None
        return cls(token, chat_id, int(os.getenv("TELEGRAM_COOLDOWN", "600")))

    def allowed(self, key, now):
        last = self.sent.get(key)
        if last is not None and now - last < self.cooldown:
            return False
        self.sent[key] = now
        return True

    def send_decision(self, event, result, now=None, analysis=None):
        now = time.time() if now is None else now
        if result.route not in ("notify", "escalate"):
            return False
        if result.route == "notify" and not self.allowed((event.source, event.subject, result.route), now):
            return False
        self.post(format_message(event, result, os.getenv("SENTINEL_HOST", "homeserver"), analysis))
        return True


def whoami():
    load_dotenv()
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("TELEGRAM_BOT_TOKEN missing in .env")
    me = api_call(token, "getMe")["result"]
    print(f"bot: @{me['username']}")
    updates = api_call(token, "getUpdates")["result"]
    chats = {}
    for u in updates:
        msg = u.get("message") or u.get("edited_message") or {}
        chat = msg.get("chat")
        if chat:
            chats[chat["id"]] = chat.get("username") or chat.get("title") or chat.get("first_name")
    if not chats:
        print("no messages yet: send your bot any message in Telegram, then run this again")
    for chat_id, name in chats.items():
        print(f"TELEGRAM_CHAT_ID={chat_id}  ({name})")


def test_message():
    load_dotenv()
    bot = Telegram.from_env()
    if not bot:
        sys.exit("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing in .env")
    bot.post("🟢 homelab-sentinel is online")
    print("sent")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        test_message()
    else:
        whoami()
