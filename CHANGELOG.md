# Changelog

## Unreleased

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
