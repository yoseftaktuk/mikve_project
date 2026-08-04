# `access_logic.py` — improvement notes

Review of `gate-system/services/access-control-service/app/access_logic.py`.
Items are ordered by impact. This is a backlog document, not a change log.

---

## High

### 1. Charge then door can leave money taken with no entry

**Status: implemented (saga).** See [docs/README.md](./docs/README.md) and `app/saga/`.
Chip/fingerprint use idempotent charge + refund; cash uses a staff receipt on door failure.
Legacy path remains behind `ACCESS_SAGA_ENABLED=false`.

### 2. Cash `add` → check → `take_fee` is not atomic

**Status: mitigated.** `CashSession.try_pay` takes the fee under one lock and is idempotent per `attempt_id`.

### 3. Chip low-balance UX is inconsistent with fingerprints

The chip path still emits `access.denied` with `insufficient_balance`. Fingerprints emit `access.topup_needed` and offer Coins / Card on the kiosk (`fingerprint_logic.py`).

If top-up choices should apply to chips as well, this file is where that product gap lives.

---

## Medium

### 4. Heavy duplication on deny paths

Unknown / disabled / insufficient (pre-check and post-adjust) repeat the same `AccessLog` + commit + publish + response shape.

**Possible direction**

- Extract a shared `_deny(...)` helper (fingerprint logic already uses this pattern)

### 5. `publish` typing is inconsistent

`CashSession` uses `PublishFn`; `process_chip_access` and `process_cash_inserted` leave `publish` untyped.

**Possible direction**

- Type all three with `PublishFn`

### 6. `accumulated_cents` is read unlocked

`healthz` reads `cash_session.accumulated_cents` without the lock. Fine as a rough UI meter; wrong if used for money decisions.

**Possible directions**

- Document the property as approximate
- Or protect the read with the same lock

### 7. ~~`take_fee` keeps leftover cash without rescheduling reset~~ (fixed)

**Was:** Overpay left change in `_accumulated_cents`, which seeded the next visitor.

**Now:** Successful `try_pay` clears the session to 0 and emits `cash.reset` with `reason=overpay_discarded` when `paid > fee`. Chip/fingerprint completion calls `clear_for_other_method()` so abandoned partial cash cannot carry over.

### 8. No validation on `amount_cents`

`add(0)` or negative values are possible from bad hardware events.

**Possible direction**

- Reject `amount_cents <= 0` at the start of `process_cash_inserted`

---

## Low

### 9. No dedicated unit tests for this module

Fingerprint logic has `tests/test_fingerprint_logic.py`. Chip/cash logic in this file looks largely untested in-repo.

**Worth covering**

- Deny reasons
- Successful chip charge
- Cash partial → grant
- Timeout reset
- Double-pay race (once fixed)

### 10. Chip charge has no idempotency key

Chip balance adjust supports idempotency keys (used for card top-ups). Entry fee here does not. A retried scan/event could double-charge.

**Possible direction**

- Pass a stable key per hardware scan/event id when available

### 11. Global `settings` inside functions

Makes tests and fee overrides harder.

**Possible direction**

- Pass `fee_cents` / `door_seconds` as arguments

### 12. Shared deny/grant helpers with `fingerprint_logic`

Same `AccessLog` and event shapes live in two files.

**Possible direction**

- A small shared helper module to keep WebSocket payloads consistent

---

## What is already solid

- Clear separation of cash session vs chip balance charge
- Inactivity reset with cancel-on-new-coin
- Publish hooks for the dashboard
- Readable happy path for both chip and cash methods

---

## Suggested first slice (if implementing)

1. Atomic `CashSession.try_pay(fee)`
2. Shared `_deny(...)` for chip denies
3. Unit tests for chip + cash paths
