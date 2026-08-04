# API Contracts — Access Attempt Saga

Design contracts only. Pseudocode interfaces + intended HTTP shapes. Not implemented yet.

---

## 1. Ports (orchestrator dependencies)

### `IAccessAttemptRepository`

```text
create(attempt: NewAccessAttempt) -> AccessAttempt
get(attempt_id: UUID) -> AccessAttempt | None
transition(
  attempt_id: UUID,
  expected_from: AttemptStatus,
  to: AttemptStatus,
  reason: str,
  correlation_id: UUID,
) -> AccessAttempt
  # Optimistic CAS: UPDATE ... WHERE id=? AND status=expected_from
  # Raises ConflictError if row missing or status mismatch
list_stale(states: set[AttemptStatus], older_than: datetime) -> list[AccessAttempt]
```

### `ILedgerPort` (chip / fingerprint balance)

```text
charge(attempt_id, chip_id, amount_cents, correlation_id) -> ChargeResult
refund(attempt_id, chip_id, amount_cents, correlation_id) -> RefundResult

# Idempotency keys (mandatory):
#   charge -> "access-charge:{attempt_id}"
#   refund -> "access-refund:{attempt_id}"
```

Maps to existing chip-service:

```http
POST /chips/{chip_id}/balance/adjust
Content-Type: application/json

{
  "delta_cents": -500,
  "reason": "entry_fee",
  "description": "access-charge:{attempt_id}",
  "idempotency_key": "access-charge:{attempt_id}"
}
```

Refund uses positive `delta_cents` and `idempotency_key: "access-refund:{attempt_id}"`, reason `entry_fee_refund`.

### `ICashLedgerPort`

```text
take_fee(attempt_id, fee_cents) -> TakeResult
  # Atomic: if accumulated >= fee, deduct fee, bind attempt_id; else InsufficientCash
  # Second call for same attempt_id: success no-op (idempotent)

restore_fee(attempt_id) -> RestoreResult
  # Best-effort: add fee back to CashSession if still the active session context
  # Failure does not block cash receipt issuance
```

### `ICashReceiptPort`

```text
issue(attempt_id, amount_cents, reason, correlation_id) -> Receipt
  # Idempotent on attempt_id: returns existing receipt if already issued

redeem(redeem_code, staff_id) -> Receipt   # management only
void(receipt_id, staff_id, reason) -> Receipt
```

Receipt fields: `receipt_id`, `attempt_id`, `amount_cents`, `issued_at`, `redeem_code`, `status`.

### `IDoorPort`

```text
open(attempt_id, operation_id, seconds, correlation_id) -> DoorResult

DoorResult:
  CONFIRMED
  TIMEOUT
  HARDWARE_UNAVAILABLE
  REJECTED
```

Success means positive confirmation within timeout — **not** HTTP 204 alone.

### `IAuditLogPort`

```text
append(entry: AuditEntry) -> None

AuditEntry:
  timestamp, attempt_id, subject_ref, old_status, new_status,
  reason, duration_ms, correlation_id
```

### `INotificationPort`

```text
publish_event(type: str, payload: dict) -> None
raise_alert(alert_type: str, payload: dict) -> None
```

---

## 2. Hardware-service door API (upgrade)

### Current (insufficient)

```http
POST /door/open
{ "seconds": 5 }
→ 204 No Content   # unlock may still fail in background
```

### Target

```http
POST /door/open
Content-Type: application/json

{
  "seconds": 5,
  "operation_id": "<door_operations.id>",
  "attempt_id": "<access_attempts.id>",
  "correlation_id": "<uuid>"
}
```

**Response (v1 preferred — sync confirm):**

```http
200 OK
{
  "operation_id": "...",
  "status": "confirmed",
  "unlocked_for_seconds": 5
}
```

Errors:

| HTTP | Meaning | DoorResult |
|------|---------|------------|
| 200 `status=confirmed` | Relay command succeeded | CONFIRMED |
| 504 / client timeout | No confirm in time | TIMEOUT |
| 503 | Adapter/GPIO unavailable | HARDWARE_UNAVAILABLE |
| 409 | Rejected (busy / policy) | REJECTED |

**Later (async confirm):** hardware publishes Redis:

```json
{
  "type": "door.opened",
  "operation_id": "...",
  "attempt_id": "...",
  "ts": "..."
}
```

or `door.failed` with `error_code`. ACS awaits the matching `operation_id`.

---

## 3. Access-control HTTP (management / ops)

Design surfaces for later implementation:

### List manual review

```http
GET /management/access-attempts?status=MANUAL_REVIEW
Authorization: Bearer <management JWT>
```

### Resolve manual review

```http
POST /management/access-attempts/{attempt_id}/resolve
{ "resolution": "refunded", "note": "paid from till" }
→ status REFUNDED
```

### Redeem cash receipt

```http
POST /management/cash-receipts/redeem
{ "redeem_code": "..." }
→ receipt status redeemed
```

### Attempt detail (audit trail)

```http
GET /management/access-attempts/{attempt_id}
→ attempt + payments + refunds + door_operations + audit_logs
```

Internal orchestrator entry points remain event-driven (`rfid.scan`, cash insert, fingerprint approve) — not new public charge APIs.

---

## 4. Chip-service contract notes

Already supports optional `idempotency_key` on balance adjust. Saga **must** always send keys derived from `attempt_id` as above.

No public “refund any amount” from the dashboard without going through ACS management resolve (authorized path creates the refund transaction row first).

---

## 5. Event / alert payloads (Redis)

### Transition-facing events (examples)

```json
{
  "type": "access.attempt.updated",
  "attempt_id": "...",
  "status": "COMPLETED",
  "method": "chip",
  "correlation_id": "...",
  "ts": "..."
}
```

### Alert example

```json
{
  "type": "access.alert",
  "alert": "DoorFailedAfterCharge",
  "attempt_id": "...",
  "method": "cash",
  "fee_cents": 500,
  "door_errors": ["timeout", "timeout"],
  "correlation_id": "...",
  "ts": "..."
}
```

`CashReceiptIssued` includes `redeem_code` and `amount_cents` for staff.

---

## 6. Idempotency guarantee summary

1. **Ledger:** unique `chip_activity.idempotency_key` → replay-safe adjust.
2. **ACS rows:** unique `payment_transactions.idempotency_key` / `refund_transactions.idempotency_key`.
3. **CAS:** `transition(expected_from, to)` prevents illegal jumps under concurrency.
4. **Cash take:** bound to `attempt_id`; second take is no-op success.
5. **Receipt:** unique `cash_receipts.attempt_id`.
6. **Door:** unique `(attempt_id, attempt_index)`; never open if attempt already terminal success/refund.
