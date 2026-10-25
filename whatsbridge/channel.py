"""The MCP side: the piece Claude Code spawns and talks to over stdin and stdout."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable

from . import __version__

CHANNEL_NOTIFICATION = "notifications/claude/channel"

REPLY_TOOL = {
    "name": "reply",
    "description": "Send a message back to the WhatsApp conversation",
    "inputSchema": {
        "type": "object",
        "properties": {
            "chat_id": {
                "type": "string",
                "description": "The conversation to reply in",
            },
            "text": {"type": "string", "description": "The message to send"},
        },
        "required": ["chat_id", "text"],
    },
}


def _as_dict(value: object) -> dict[str, object]:
    """Return value as a dict, or an empty one if it is anything else."""
    return value if isinstance(value, dict) else {}


class Channel:
    """Speaks the slice of MCP that a channel needs."""

    def __init__(
        self, name: str, instructions: str, send: Callable[[str, str], None]
    ) -> None:
        self.name = name
        self.instructions = instructions
        self._send = send
        self._on_ready: Callable[[], None] | None = None
        self._started = False

    def ready(self, handler: Callable[[], None]) -> None:
        """Set what runs once the handshake completes."""
        self._on_ready = handler

    def push(self, content: str, **meta: str) -> None:
        """Inject a message into the running session."""
        self._write(
            {
                "jsonrpc": "2.0",
                "method": CHANNEL_NOTIFICATION,
                "params": {"content": content, "meta": meta},
            }
        )

    def _write(self, message: dict[str, object]) -> None:
        """One line of JSON on stdout, flushed as it goes."""
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()

    def run(self) -> None:
        """Read requests from Claude Code until stdin closes."""
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                self._handle(line)
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                print(f"whatsbridge: bad message: {exc}", file=sys.stderr)

    def _handle(self, line: str) -> None:
        message = json.loads(line)
        method = message.get("method")
        if method == "initialize":
            self._initialize(message)
        elif method == "notifications/initialized":
            self._start()
        elif method == "tools/list":
            self._result(message["id"], {"tools": [REPLY_TOOL]})
        elif method == "tools/call":
            self._call_tool(message)

    def _result(self, request_id: object, result: dict[str, object]) -> None:
        """Reply to a request. Notifications carry no id, so they get no reply."""
        self._write({"jsonrpc": "2.0", "id": request_id, "result": result})

    def _initialize(self, message: dict[str, object]) -> None:
        """Answer the opening handshake.

        Declaring the capability is the whole point: that is what makes Claude
        Code treat this process as a channel.
        """
        params = _as_dict(message.get("params"))
        self._result(
            message["id"],
            {
                "protocolVersion": params.get("protocolVersion"),
                "capabilities": {
                    "experimental": {"claude/channel": {}},
                    # Without this, Claude Code never asks what tools we offer,
                    # so the reply tool does not exist as far as the session
                    # is concerned.
                    "tools": {},
                },
                "serverInfo": {"name": self.name, "version": __version__},
                "instructions": self.instructions,
            },
        )

    def _start(self) -> None:
        """Run the ready handler exactly once."""
        if self._started:
            return
        self._started = True
        if self._on_ready is not None:
            self._on_ready()

    def _call_tool(self, message: dict[str, object]) -> None:
        """Pass a reply to the send function, reporting failure to Claude."""
        args = _as_dict(_as_dict(message.get("params")).get("arguments"))
        try:
            self._send(str(args.get("chat_id", "")), str(args.get("text", "")))
        except Exception as exc:  # noqa: BLE001 -- boundary around the caller's send
            # send() comes from the caller and can fail any way a network call
            # can. Making that a tool error tells Claude the reply did not go
            # out. Letting it escape kills the bridge.
            self._result(
                message["id"],
                {
                    "content": [{"type": "text", "text": f"send failed: {exc}"}],
                    "isError": True,
                },
            )
            return
        self._result(message["id"], {"content": [{"type": "text", "text": "sent"}]})
