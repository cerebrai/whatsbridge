"""Bridge: WhatsApp in, Claude Code session out, replies back.

Claude Code spawns this module as its channel server. It polls WhatsApp in a
background thread and pushes each message into the session. Claude answers by
calling the reply tool, which sends the text back to WhatsApp.

Claude Code starts this; you do not run it by hand.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

from .channel import Channel
from .whatsapp import WhatsAppClient, WhatsAppError

POLL_TIMEOUT = 25  # seconds; the API caps this at 25
POLL_LIMIT = 100  # max entries per poll
TEXT_MAX = 4096  # WhatsApp caps text bodies at 4096 characters
POLL_FLOOR = 0.5  # smallest gap between polls, so a fast empty answer cannot spin

# The repo root, so this works from whichever directory Claude Code runs in.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"

INSTRUCTIONS = (
    'WhatsApp messages arrive as <channel source="whatsbridge" chat_id="...">. '
    "Reply with the reply tool, passing the chat_id from the tag."
)


def _load_env(path: Path = ENV_FILE) -> None:
    """Copy KEY=value lines into the environment, leaving anything already set."""
    try:
        lines = path.read_text().splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def run() -> None:
    """Start the bridge and leave the main thread to the channel."""
    _load_env()
    token = os.environ.get("WHATSAPP_ACCESS_TOKEN")
    if not token:
        raise RuntimeError("WHATSAPP_ACCESS_TOKEN is not set")

    whatsapp = WhatsAppClient(token)

    def send(chat_id: str, text: str) -> None:
        """Send a reply back to WhatsApp, trimmed to what the API accepts."""
        whatsapp.send_text(chat_id, text[:TEXT_MAX])

    channel = Channel("whatsbridge", INSTRUCTIONS, send)
    channel.ready(lambda: _start_polling(whatsapp, channel))
    channel.run()


def _start_polling(whatsapp: WhatsAppClient, channel: Channel) -> None:
    """Run the poll loop off the main thread, which belongs to the channel."""
    thread = threading.Thread(
        target=_poll_forever, args=(whatsapp, channel), daemon=True
    )
    thread.start()


RETRY_DELAY = 5.0  # seconds to wait after a failed poll before asking again


def _poll_forever(whatsapp: WhatsAppClient, channel: Channel) -> None:
    """Poll WhatsApp and push every new message into the session."""
    offset: int = 0
    started_at = int(time.time())
    while True:
        try:
            update = whatsapp.get_updates(
                offset, limit=POLL_LIMIT, timeout=POLL_TIMEOUT
            )
        except WhatsAppError as exc:
            # The API answered and said no. The status says which no.
            print(f"whatsbridge: poll failed ({exc.status}): {exc}", file=sys.stderr)
            time.sleep(RETRY_DELAY)
            continue
        except OSError as exc:
            # A dropped connection, a name that will not resolve, a socket that
            # timed out. None of those are a WhatsAppError, and any of them
            # would otherwise kill the thread and leave the bridge silent.
            print(
                f"whatsbridge: poll failed ({type(exc).__name__}): {exc}",
                file=sys.stderr,
            )
            time.sleep(RETRY_DELAY)
            continue
        if update is None:
            time.sleep(POLL_FLOOR)  # the long poll normally dwarfs this
            continue
        next_offset = update.get("next_offset")
        if isinstance(next_offset, int):
            offset = next_offset
        for message in _messages(update):
            _forward(channel, message, started_at)


def _as_dict(value: object) -> dict[str, object]:
    """Return value as a dict, or an empty one if it is anything else."""
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    """Return value as a list, or an empty one if it is anything else."""
    return value if isinstance(value, list) else []


def _messages(update: dict[str, object]) -> list[dict[str, object]]:
    """Pull the individual messages out of the API's nested envelope.

    The shape runs entry, then changes, then value, then messages, and every
    level is optional, so each is checked rather than assumed.
    """
    found: list[dict[str, object]] = []
    for entry in _as_list(update.get("entry")):
        for change in _as_list(_as_dict(entry).get("changes")):
            value = _as_dict(_as_dict(change).get("value"))
            found.extend(_as_dict(item) for item in _as_list(value.get("messages")))
    return found


def _text_body(message: dict[str, object]) -> str | None:
    """The text of a text message, or None for anything else."""
    if message.get("type") != "text":
        return None
    body = _as_dict(message.get("text")).get("body")
    return body if isinstance(body, str) and body else None


def _sent_at(message: dict[str, object]) -> int:
    """The message's send time in whole seconds, or 0 when unusable."""
    stamp = message.get("timestamp")
    if isinstance(stamp, int):
        return stamp
    if isinstance(stamp, str) and stamp.isdigit():
        return int(stamp)
    return 0


def _forward(channel: Channel, message: dict[str, object], started_at: int) -> None:
    """Push one WhatsApp message into the session, if it is one we answer."""
    sender = message.get("from")
    body = _text_body(message)
    if not isinstance(sender, str) or body is None:
        return
    # Starting from offset 0 replays up to 30 days of backlog, so drop
    # anything that arrived before this process did.
    if _sent_at(message) < started_at:
        return
    channel.push(body, chat_id=sender)
