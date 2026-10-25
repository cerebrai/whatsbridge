"""Client for the WhatsApp Agent Platform API, standard library only."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_BASE_URL = "https://api.whatsapp.com/agent/v1"
# Long-poll reads hold the connection for up to 25s, so the socket timeout
# has to outlast that or every idle poll dies as a timeout.
SOCKET_TIMEOUT = 45.0


class WhatsAppError(Exception):
    """An error returned by the WhatsApp Agent Platform API."""

    def __init__(self, status: int, code: int | None, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class WhatsAppClient:
    """Talks to the WhatsApp Agent Platform API for one agent."""

    def __init__(self, access_token: str, base_url: str = DEFAULT_BASE_URL) -> None:
        self.access_token = access_token
        self.base_url = base_url.rstrip("/")

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, object] | None = None,
        body: dict[str, object] | None = None,
    ) -> bytes:
        """Send one request and return the raw body, or raise WhatsAppError."""
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=SOCKET_TIMEOUT) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise self._error(exc) from exc

    def _error(self, exc: urllib.error.HTTPError) -> WhatsAppError:
        """Build a WhatsAppError carrying the API's own error code."""
        raw = exc.read()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        error = data.get("error") or {}
        message = (
            error.get("message") or raw.decode(errors="replace") or "unknown error"
        )
        return WhatsAppError(exc.code, error.get("code"), message)

    def get_updates(
        self, offset: int | None, limit: int = 100, timeout: int = 25
    ) -> dict[str, object] | None:
        """Long-poll for inbound messages and receipts.

        Returns the parsed body when updates arrive, or None when the poll
        times out with nothing to deliver.
        """
        params: dict[str, object] = {"limit": limit, "timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        raw = self._request("GET", "/updates", params=params)
        # A 204 "nothing to deliver" arrives as an empty body.
        return json.loads(raw) if raw else None

    def send_text(self, to: str, body: str) -> str:
        """Send a text message and return the wamid WhatsApp assigned it."""
        raw = self._request(
            "POST",
            "/messages",
            body={
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": body},
            },
        )
        return json.loads(raw)["messages"][0]["id"]

    def mark_read(self, message_id: str, typing: bool = False) -> None:
        """Mark an inbound message read, optionally showing a typing indicator."""
        payload: dict[str, object] = {
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        }
        if typing:
            payload["typing_indicator"] = {"type": "text"}
        self._request("POST", "/statuses", body=payload)
