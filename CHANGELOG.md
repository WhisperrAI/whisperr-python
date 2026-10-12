# Changelog

## 0.2.0

- Every event now carries `context.sdk_name` (`whisperr-python`) and
  `context.sdk_version`.
- New `app_version` option (Django: `WHISPERR_APP_VERSION`). When set, every
  event carries it as `context.app_version`. Not set: no key.
- A key that the caller passes in `track()` context wins over these SDK keys.
  `$message_id` is always the SDK value.
- New `BillingEvent` constants for the reserved billing fallback events
  (`trial_started`, `subscription_started`, `subscription_renewed`,
  `payment_failed`, `subscription_cancelled`, `purchase_completed`).
- README: the `app_language` trait, the context keys, and billing events.
- The package version now lives in `whisperr/_version.py`.

## 0.1.1

- Drop an invalid `event_type` client-side.
