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
- **Reliable in-process.** In-memory queue, batching, retry with backoff
  (429/5xx), malformed-4xx drop, per-event idempotency key. On `401/403` or
  exhausted retries, delivery pauses and **events stay buffered for the next
  flush** rather than being dropped. The queue is not crash-durable — call
  `flush()`/`shutdown()` before exit (see *Short-lived processes*).
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

## Traits: `app_language`

`app_language` is a reserved trait. It is the language the user picked in your
product, not the device or browser locale. Use a BCP 47 tag (`de`, `pt-BR`).
Whisperr writes messages in this language. When it is not set, Whisperr uses
the event `context.locale`, then the `locale` trait.

The server SDK has no special API for it. Pass it in `traits`:

```python
whisperr.identify("user_8842", traits={"app_language": "pt-BR"})
```

The server keeps the last value you send. A server SDK never guesses the
language: the process locale is not the user's language.

## Context the SDK adds

Every event carries these keys in `context`:

| Key | Value |
|---|---|
| `sdk_name` | `whisperr-python` |
| `sdk_version` | The SDK version, for example `0.2.0` |
| `app_version` | The `app_version` option. Not set: no key. |

A key that you pass in the `context` argument of `track()` wins over the SDK
value. The SDK always sets `$message_id`.

```python
whisperr = Whisperr(
    api_key=os.environ["WHISPERR_API_KEY"],
    app_version=os.environ.get("RELEASE_VERSION"),  # for example "2026.10.1" or a git SHA
)
```

With Django, set `WHISPERR_APP_VERSION` in `settings.py`.

## Billing events

If Whisperr has no billing source for your app (for example no RevenueCat or
Stripe connection), send billing changes yourself. Use the reserved names in
`BillingEvent`:

| Constant | Event name |
|---|---|
| `BillingEvent.TRIAL_STARTED` | `trial_started` |
| `BillingEvent.SUBSCRIPTION_STARTED` | `subscription_started` |
| `BillingEvent.SUBSCRIPTION_RENEWED` | `subscription_renewed` |
| `BillingEvent.PAYMENT_FAILED` | `payment_failed` |
| `BillingEvent.SUBSCRIPTION_CANCELLED` | `subscription_cancelled` |
| `BillingEvent.PURCHASE_COMPLETED` | `purchase_completed` |

```python
from whisperr import BillingEvent

whisperr.track(
    "user_8842",
    BillingEvent.SUBSCRIPTION_STARTED,
    {
        "product_id": "pro_monthly",
        "price": 9.99,  # major units, not cents
        "currency": "USD",  # ISO 4217, upper case
        "period": "P1M",  # ISO 8601 duration
        "transaction_id": "1000000912345678",
        "store": "app_store",
    },
)
```

> **Register these names first.** Today the server stores these events only
> after your app registers them (the Whisperr MCP does this during setup). Until
> the server accepts them without registration, an unregistered name is not
> stored.

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
| `app_version` | — | Your release version. Sent as `context.app_version`. |
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
