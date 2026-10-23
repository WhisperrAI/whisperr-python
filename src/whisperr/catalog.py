"""Catalog push client (whisperr-go catalog v2 contract, section 6).

Calls are synchronous request/response, not queued like ``track()``: each
method returns once Whisperr has answered. Upserts are chunked at 500 items
(and 2 MiB), deletes at 1000. 429 and 5xx answers and network errors are
retried with backoff, honoring ``Retry-After``. Every retry resends the same
body, and the server orders writes by ``updated_at`` / ``deleted_at``, so a
retried upsert or delete can never apply twice or overwrite a newer write.

Items and results use the wire field names (snake_case), so a dict you build
here is exactly what the server receives.
"""

from __future__ import annotations

import email.utils
import json
import random
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from ._version import __version__
from .errors import WhisperrCatalogError, WhisperrConfigurationError

try:  # Python 3.8+
    from typing import TypedDict
except ImportError:  # pragma: no cover
    TypedDict = dict  # type: ignore

UPSERT_BATCH = 500
DELETE_BATCH = 1000
MAX_BODY_BYTES = 2 * 1024 * 1024
MAX_RETRY_AFTER = 60.0

DEFAULT_BASE = "https://api.whisperr.net"
USER_AGENT = f"whisperr-python/{__version__}"
_SNAPSHOT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_TIME_FIELDS = ("updated_at", "published_at")
# Room for {"items":[ ... ]}.
_ENVELOPE = 16

Time = Union[datetime, str]


class CatalogRejection(TypedDict):
    index: int  # index into the list you passed, not into one chunk
    source_item_id: str
    code: str
    message: str


class CatalogUpsertResult(TypedDict):
    accepted: int
    applied: int
    unchanged: int
    ignored_older: int
    rejected: List[CatalogRejection]


class CatalogDeleteResult(TypedDict):
    deleted: int
    ignored_older: int
    not_found: int
    rejected: List[CatalogRejection]


# (method, url, headers, body, timeout) -> (status, headers, body). Raise for a
# network error or timeout; it is retried.
HttpFunc = Callable[[str, str, Dict[str, str], Optional[bytes], float], Tuple[int, Mapping[str, str], bytes]]


class CatalogClient:
    """The catalog client: ``Whisperr(...).catalog``, or standalone.

    Needs a server key (``wrk_...``). A publishable key (``wpk_...``) raises
    :class:`WhisperrConfigurationError` before anything is sent.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE,
        max_retries: int = 5,
        timeout: float = 30.0,
        http: Optional[HttpFunc] = None,
        sleep: Callable[[float], None] = time.sleep,
        disabled: bool = False,
    ) -> None:
        self._key = (api_key or "").strip()
        self._base = base_url.rstrip("/")
        self._max_retries = max(0, max_retries)
        self._timeout = timeout
        self._http = http or _urllib_http
        self._sleep = sleep
        self._disabled = disabled

    def upsert(self, items: Sequence[Mapping[str, Any]]) -> CatalogUpsertResult:
        """Create or update items. Rejected items are listed in ``rejected``, not raised."""
        self._check_key()
        if isinstance(items, (str, bytes, Mapping)) or not isinstance(items, Sequence):
            raise WhisperrConfigurationError("upsert takes a list of items")
        result: CatalogUpsertResult = {"accepted": 0, "applied": 0, "unchanged": 0, "ignored_older": 0, "rejected": []}
        chunks: List[List[Tuple[int, str, bytes]]] = []
        chunk: List[Tuple[int, str, bytes]] = []
        size = _ENVELOPE
        for index, item in enumerate(items):
            wire = item_to_wire(item)
            sid = str(wire.get("source_item_id", ""))
            encoded = _encode(wire)
            n = len(encoded) + 1
            if n + _ENVELOPE > MAX_BODY_BYTES:
                result["rejected"].append(_too_large(index, sid, "the item is larger than the 2 MiB request limit"))
                continue
            if len(chunk) == UPSERT_BATCH or size + n > MAX_BODY_BYTES:
                chunks.append(chunk)
                chunk, size = [], _ENVELOPE
            chunk.append((index, sid, encoded))
            size += n
        if chunk:
            chunks.append(chunk)

        def send(part: List[Tuple[int, str, bytes]]) -> None:
            try:
                res = self._request("POST", "/v1/catalog/items/batch", _items_body(part))
            except WhisperrCatalogError as e:
                # The server measures the body; split and resend rather than fail.
                if e.status == 413 and len(part) > 1:
                    half = (len(part) + 1) // 2
                    send(part[:half])
                    send(part[half:])
                    return
                if e.status == 413:
                    result["rejected"].append(_too_large(part[0][0], part[0][1], str(e)))
                    return
                raise _with_partial(e, result) from None
            for key in ("accepted", "applied", "unchanged", "ignored_older"):
                result[key] += _int(res.get(key))  # type: ignore[literal-required]
            result["rejected"].extend(_rejections(res.get("rejected"), part))

        for part in chunks:
            send(part)
        result["rejected"].sort(key=lambda r: r["index"])
        return result

    def delete(
        self,
        items: Sequence[Union[str, Mapping[str, Any]]],
        deleted_at: Optional[Time] = None,
    ) -> CatalogDeleteResult:
        """Delete items by id (all get ``deleted_at``, default now) or by
        ``{"source_item_id", "deleted_at"}`` dicts. Chunks at 1000."""
        self._check_key()
        if isinstance(items, (str, bytes, Mapping)) or not isinstance(items, Sequence):
            raise WhisperrConfigurationError("delete takes a list of ids or items")
        # One timestamp for the whole call, so a retry resends the same body.
        fallback = format_time(deleted_at if deleted_at is not None else datetime.now(timezone.utc), "deleted_at")
        encoded: List[Tuple[int, str, bytes]] = []
        for index, item in enumerate(items):
            if isinstance(item, str):
                sid, at = item, fallback
            elif isinstance(item, Mapping):
                sid = str(item.get("source_item_id", ""))
                at = format_time(item["deleted_at"], "deleted_at") if item.get("deleted_at") is not None else fallback
            else:
                raise WhisperrConfigurationError("each deletion is an id or a dict with source_item_id")
            encoded.append((index, sid, _encode({"source_item_id": sid, "deleted_at": at})))
        result: CatalogDeleteResult = {"deleted": 0, "ignored_older": 0, "not_found": 0, "rejected": []}
        for start in range(0, len(encoded), DELETE_BATCH):
            part = encoded[start : start + DELETE_BATCH]
            try:
                res = self._request("POST", "/v1/catalog/items/delete", _items_body(part))
            except WhisperrCatalogError as e:
                raise _with_partial(e, result) from None
            for key in ("deleted", "ignored_older", "not_found"):
                result[key] += _int(res.get(key))  # type: ignore[literal-required]
            result["rejected"].extend(_rejections(res.get("rejected"), part))
        result["rejected"].sort(key=lambda r: r["index"])
        return result

    def begin_snapshot(self) -> Dict[str, str]:
        """Start a full sync: upsert every item, then call :meth:`complete_snapshot`.
        Returns ``{"snapshot_id", "started_at"}``."""
        self._check_key()
        res = self._request("POST", "/v1/catalog/snapshots", b"{}")
        return {"snapshot_id": _str(res.get("snapshot_id")), "started_at": _str(res.get("started_at"))}

    def complete_snapshot(self, snapshot_id: str, item_count: int) -> Dict[str, Any]:
        """End a full sync: items not upserted since :meth:`begin_snapshot` are
        removed. Returns ``{"tombstoned", "item_count", "state"}``."""
        self._check_key()
        if not isinstance(snapshot_id, str) or not _SNAPSHOT_ID.match(snapshot_id):
            raise WhisperrConfigurationError("snapshot_id must be the id begin_snapshot returned")
        _check_count(item_count)
        res = self._request("POST", f"/v1/catalog/snapshots/{snapshot_id}/complete", _encode({"item_count": item_count}))
        return {"tombstoned": _int(res.get("tombstoned")), "item_count": _int(res.get("item_count")), "state": _str(res.get("state"))}

    def heartbeat(self, item_count: int) -> Dict[str, Any]:
        """Tell Whisperr the catalog is still current. Send every 30 minutes
        with your live item count. Returns ``{"refreshed": bool, ...}``."""
        self._check_key()
        _check_count(item_count)
        res = self._request("POST", "/v1/catalog/heartbeat", _encode({"item_count": item_count}))
        out: Dict[str, Any] = {"refreshed": res.get("refreshed") is True}
        if isinstance(res.get("reason"), str):
            out["reason"] = res["reason"]
        if isinstance(res.get("stored_count"), int):
            out["stored_count"] = res["stored_count"]
        return out

    def status(self) -> Dict[str, Any]:
        """The health of your pushed catalog (``state``, ``item_count``, ``last_push_at``, ...)."""
        self._check_key()
        return self._request("GET", "/v1/catalog/status", None)

    # ---- internals ----

    def _check_key(self) -> None:
        if self._disabled:
            raise WhisperrConfigurationError("the Whisperr client is disabled; catalog calls are not sent")
        if not self._key:
            raise WhisperrConfigurationError("catalog calls need a server key (wrk_...)")
        if self._key.startswith("wpk_"):
            raise WhisperrConfigurationError(
                "catalog push needs a server key (wrk_...); a publishable key (wpk_...) ships in apps"
                " and browsers and is refused"
            )

    def _request(self, method: str, path: str, body: Optional[bytes]) -> Dict[str, Any]:
        headers = {"Accept": "application/json", "X-API-Key": self._key, "User-Agent": USER_AGENT}
        if body is not None:
            headers["Content-Type"] = "application/json"
        attempt = 0
        while True:
            status, code, message, retry_after = 0, "network_error", "network error or timeout", None
            try:
                status, resp_headers, raw = self._http(method, self._base + path, headers, body, self._timeout)
            except Exception:  # network error / timeout: retried below
                pass
            else:
                parsed = _parse_json(raw)
                if 200 <= status < 300:
                    if not isinstance(parsed, dict):
                        raise WhisperrCatalogError(
                            f"unreadable response from {path}", status=status, code="invalid_response", retryable=False
                        )
                    return parsed
                code, message = _error_of(parsed, status)
                if status in (429, 503):
                    retry_after = parse_retry_after(_header(resp_headers, "Retry-After"))
                if not (status == 429 or status >= 500):
                    raise WhisperrCatalogError(
                        f"{path} refused ({status} {code}): {message}", status=status, code=code, retryable=False
                    )
            if attempt >= self._max_retries:
                raise WhisperrCatalogError(
                    f"{path} failed after {attempt + 1} attempts ({status or 'network'} {code}): {message}",
                    status=status,
                    code=code,
                    retryable=True,
                )
            attempt += 1
            self._sleep(retry_after if retry_after is not None else _backoff(attempt))


def item_to_wire(item: Mapping[str, Any]) -> Dict[str, Any]:
    """A copy of ``item`` for the wire: ``None`` values are left out and
    datetimes become RFC 3339 UTC strings."""
    if not isinstance(item, Mapping):
        raise WhisperrConfigurationError("each catalog item must be a dict")
    wire = _drop_none(dict(item))
    for field in _TIME_FIELDS:
        if field in wire:
            wire[field] = format_time(wire[field], field)
    return wire


def format_time(value: Time, field: str) -> str:
    """RFC 3339 in UTC with ``Z``; milliseconds only when they are not zero. A
    naive datetime is read as UTC. A string is sent as it is."""
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        ms = value.microsecond // 1000
        base = value.strftime("%Y-%m-%dT%H:%M:%S")
        return f"{base}.{ms:03d}Z" if ms else f"{base}Z"
    raise WhisperrConfigurationError(f"{field} must be a datetime or an RFC 3339 string")


def parse_retry_after(value: Optional[str], now: Optional[float] = None) -> Optional[float]:
    """Seconds to wait from a ``Retry-After`` value (delay-seconds or an
    HTTP-date), capped at 60. ``None`` when absent or unreadable."""
    if not value:
        return None
    v = value.strip()
    if v.isdigit():
        return min(float(v), MAX_RETRY_AFTER)
    try:
        at = email.utils.parsedate_to_datetime(v)
    except (TypeError, ValueError, IndexError):
        return None
    if at is None:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    wait = at.timestamp() - (time.time() if now is None else now)
    return min(max(0.0, wait), MAX_RETRY_AFTER)


def _drop_none(v: Any) -> Any:
    if isinstance(v, Mapping):
        return {k: _drop_none(x) for k, x in v.items() if x is not None}
    if isinstance(v, (list, tuple)):
        return [_drop_none(x) for x in v]
    return v


def _encode(v: Any) -> bytes:
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _items_body(part: List[Tuple[int, str, bytes]]) -> bytes:
    return b'{"items":[' + b",".join(e for _, _, e in part) + b"]}"


def _too_large(index: int, sid: str, message: str) -> CatalogRejection:
    return {"index": index, "source_item_id": sid, "code": "item_too_large", "message": message}


def _rejections(raw: Any, part: List[Tuple[int, str, bytes]]) -> List[CatalogRejection]:
    out: List[CatalogRejection] = []
    if not isinstance(raw, list):
        return out
    for r in raw:
        r = r if isinstance(r, dict) else {}
        local = r.get("index")
        orig = part[local] if isinstance(local, int) and 0 <= local < len(part) else None
        sid = r.get("source_item_id")
        out.append(
            {
                "index": orig[0] if orig else -1,
                "source_item_id": sid if isinstance(sid, str) else (orig[1] if orig else ""),
                "code": _str(r.get("code")),
                "message": _str(r.get("message")),
            }
        )
    return out


def _with_partial(e: WhisperrCatalogError, result: Mapping[str, Any]) -> WhisperrCatalogError:
    partial = dict(result)
    partial["rejected"] = sorted(result["rejected"], key=lambda r: r["index"])
    return WhisperrCatalogError(str(e), status=e.status, code=e.code, retryable=e.retryable, partial=partial)


def _error_of(body: Any, status: int) -> Tuple[str, str]:
    b = body if isinstance(body, dict) else {}
    err = b.get("error") if isinstance(b.get("error"), dict) else b
    code = err.get("code")
    message = err.get("message")
    return (code if isinstance(code, str) and code else f"http_{status}", message if isinstance(message, str) else "")


def _header(headers: Mapping[str, str], name: str) -> Optional[str]:
    if headers is None:
        return None
    for k, v in headers.items():
        if k.lower() == name.lower():
            return v
    return None


def _parse_json(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None


def _check_count(n: Any) -> None:
    if isinstance(n, bool) or not isinstance(n, int) or n < 0:
        raise WhisperrConfigurationError("item_count must be a whole number, at least 0")


def _backoff(attempt: int) -> float:
    return min(30.0, 0.5 * (2**attempt)) + random.random() * 0.25


def _int(v: Any) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def _str(v: Any) -> str:
    return v if isinstance(v, str) else ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # Never forward the server key to another location.
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _urllib_http(
    method: str, url: str, headers: Dict[str, str], body: Optional[bytes], timeout: float
) -> Tuple[int, Mapping[str, str], bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with _opener.open(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers.items()), resp.read()
    except urllib.error.HTTPError as e:
        try:
            raw = e.read()
        except Exception:
            raw = b""
        return e.code, dict(e.headers.items()) if e.headers else {}, raw
