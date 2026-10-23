# Changelog

## 0.3.0 - 2026-10-23

- New `whisperr.catalog` client for catalog push (whisperr-go catalog v2):
  `upsert(items)`, `delete(ids_or_items, deleted_at=None)`,
  `begin_snapshot()`, `complete_snapshot(snapshot_id, item_count)`,
  `heartbeat(item_count)` and `status()`. Server key only: a `wpk_` key raises
  `WhisperrConfigurationError` before any request. Calls block until Whisperr
  answers; they are not queued.
- Upserts are chunked at 500 items and 2 MiB, deletes at 1000. Rejected items
  come back with their index in the list you passed. A 413 splits the chunk.
- 429, 5xx and network errors are retried with backoff, honoring
  `Retry-After`. Retries resend the same body; `delete` uses one `deleted_at`
  per call. Refusals and exhausted retries raise `WhisperrCatalogError` with
  `status`, `code`, `retryable` and, for upsert and delete, `partial`.
  Catalog requests never follow redirects.
- `CatalogClient` for a catalog client without the event queue. New options
  `catalog_max_retries` and `catalog_timeout`.
- `to_minor_units(amount, currency)` and `currency_exponent(currency)` with the
  ISO 4217 exponents the server uses. They raise instead of rounding.
- Tests run `tests/fixtures/catalog_push.json`, the catalog push conformance
  cases (or `WHISPERR_CATALOG_SPEC_PATH`, once whisperr-spec publishes
  `conformance/catalog_push.json`).

## 0.2.1 - 2026-10-16

- Every request sends `User-Agent: whisperr-python/<version>`. Before, urllib
  sent `Python-urllib/3.x`, which Cloudflare in front of `api.whisperr.net`
  answers with 403 (error 1010). The SDK reported that as a rejected API key
  and sent nothing.

## 0.2.0 - 2026-10-12

- Every event now carries `context.sdk_name` (`whisperr-python`) and
  `context.sdk_version`.
- New `app_version` option (Django: `WHISPERR_APP_VERSION`). When set, every
  event carries it as `context.app_version`. Not set: no key.
- A key that the caller passes in `track()` context wins over these SDK keys.
  `$message_id` is always the SDK value.
- New `BillingEvent` constants for the reserved billing fallback events
  (playbook engine spec section 3.8): `trial_started`, `trial_converted`,
  `trial_auto_renew_disabled`, `membership_started`, `renewal_succeeded`,
  `auto_renew_disabled`, `auto_renew_enabled`, `membership_ended`,
  `billing_issue_started`, `billing_recovered`, `refund_granted`. Money
  properties: `plan_id`, `price`, `currency`, `plan_period`, `transaction_id`,
  `store`.
- README: the `app_language` trait, the context keys, and billing events.
- The package version now lives in `whisperr/_version.py`.

## 0.1.1

- Drop an invalid `event_type` client-side.
