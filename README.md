# whisperr (Python)

The Whisperr **server-side** SDK for Python — reliable churn-signal event
tracking for any Python backend. The backend is where the highest-signal churn
events live (payment failures, cancellations, trial expiry, usage drops), so
this is where Whisperr gets its most valuable signal.

```bash
pip install whisperr
```

## Quick start

```python
import os
from whisperr import Whisperr

whisperr = Whisperr(api_key=os.environ["WHISPERR_API_KEY"])

# A server-side churn signal:
whisperr.track("user_8842", "payment_failed", {"amount_cents": 4900, "reason": "card_declined"})

# Associate traits / contact channels with a user:
whisperr.identify("user_8842", email="ada@example.com", traits={"plan": "pro"})

# Deliver everything before the process exits:
whisperr.shutdown()
```

The user id (`external_user_id`) is **always explicit** here — unlike a browser
SDK, the server has no persisted session to infer it from. Pass the same id you
use everywhere else for that user, and frontend + backend events land on one
timeline automatically.

## Design

- **Same wire contract as the other Whisperr SDKs.** Events post to
  `/v1/events/batch`, identities to `/v1/identify`, authenticated with
  `X-API-Key`.
- **Non-blocking.** `track()`/`identify()` enqueue and return immediately; a
  background thread batches and delivers. Call `flush()` for a barrier.
- **Reliable.** In-memory queue, batching, retry with backoff, 429/5xx retry,
  401/403 stop, malformed-4xx drop, per-event idempotency key.
- **Process-friendly.** A daemon thread that never blocks exit; `shutdown()` is
  also registered via `atexit`.
- **Zero runtime dependencies.** Uses the standard library only.

## Django

```python
# settings.py
WHISPERR_API_KEY = os.environ["WHISPERR_API_KEY"]
MIDDLEWARE = [
    # ... after your authentication middleware ...
    "whisperr.django.WhisperrMiddleware",
]
```

```python
# views.py
def upgrade(request):
    request.whisperr.track("plan_upgraded", {"plan": "pro"})
    ...
```

`request.whisperr.track()` is bound to the request's authenticated user
(`request.user.pk` by default; override with `WHISPERR_RESOLVE_USER`, a dotted
path to `def resolve(request) -> str | None`). For events with no request —
Celery tasks, webhooks, management commands — use a client directly with the
user id from your domain data:

```python
from whisperr.django import get_client
get_client().track(subscription.user_id, "subscription_cancelled", {"reason": reason})
```

Install the Django extra if you want the dependency pinned: `pip install whisperr[django]`.

## Short-lived processes

In scripts, serverless handlers, or management commands, call `flush()` (or
`shutdown()`) before exit so queued events aren't lost:

```python
whisperr.track(user_id, "report_generated")
whisperr.flush()
```

## Options

| Option | Default | Notes |
|---|---|---|
| `api_key` | — | App ingestion key (`wrk_…`). Required. |
| `base_url` | `https://api.whisperr.net` | Ingestion base URL. |
| `flush_at` | `20` | Flush when this many events are queued. |
| `flush_interval` | `10.0` | Background flush cadence (seconds). |
| `max_queue_size` | `10000` | Oldest events drop on overflow. |
| `max_batch_size` | `500` | Events per batch (hard backend cap is 500). |
| `max_retries` | `6` | Consecutive retries before giving up a batch. |
| `request_timeout` | `10.0` | Per-request timeout (seconds). |
| `disabled` | `False` | No-op client (useful in tests). |
| `debug` | `False` | Verbose logging to stderr. |
| `on_error` | — | `Callable[[WhisperrError], None]` for observability. |

---

Whisperr — predict churn, automate interventions, recover revenue.
[whisperr.net](https://whisperr.net)
