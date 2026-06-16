from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List

# Delivery outcome of one request, mirroring the @whisperr/node SDK:
#   "ok"    — delivered
#   "retry" — transient (429, 5xx, network/timeout)
#   "auth"  — key rejected (401/403); stop and surface
#   "drop"  — other 4xx (malformed); discard to avoid an infinite retry loop
SendResult = str


class Transport:
    """HTTP transport for the Whisperr ingestion API (stdlib urllib, zero deps)."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        timeout: float,
        warn: Callable[[str], None],
    ) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._timeout = timeout
        self._warn = warn

    def send_batch(self, events: List[Dict[str, Any]]) -> SendResult:
        payload = {
            "events": [
                {
                    "external_user_id": e["external_user_id"],
                    "event_type": e["event_type"],
                    "occurred_at": e["occurred_at"],
                    "properties": e.get("properties") or {},
                    # $message_id is an idempotency key for backend dedup, nested
                    # in the free-form context so the strict ingestion accepts it.
                    "context": {**(e.get("context") or {}), "$message_id": e["message_id"]},
                }
                for e in events
            ]
        }
        if not payload["events"]:
            return "ok"
        return self._post("/v1/events/batch", payload)

    def send_identify(self, op: Dict[str, Any]) -> SendResult:
        body: Dict[str, Any] = {"external_user_id": op["external_user_id"]}
        if op.get("traits"):
            body["traits"] = op["traits"]
        if op.get("preferred_channel"):
            body["preferred_channel"] = op["preferred_channel"]
        channels = op.get("channels")
        if channels:
            body["channels"] = [
                {
                    "channel": c.get("type") or c["channel"],
                    "address": c["address"],
                    "opted_in": c.get("opted_in", True),
                    **({"verified": c["verified"]} if "verified" in c else {}),
                }
                for c in channels
            ]
        return self._post("/v1/identify", body)

    def _post(self, path: str, body: Any) -> SendResult:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self._base + path,
            data=data,
            method="POST",
            headers={"Content-Type": "application/json", "X-API-Key": self._key},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout):
                return "ok"  # urlopen raises on >= 400, so reaching here is 2xx/3xx
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                self._warn(f"auth rejected ({e.code}) — check your Whisperr API key")
                return "auth"
            if e.code == 429 or e.code >= 500:
                return "retry"
            self._warn(f"request to {path} dropped ({e.code})")
            return "drop"
        except Exception:
            # Network error / timeout — retry later.
            return "retry"
