# Sequence Diagrams — Access Attempt Saga

Companion to [SDS](./SDS-access-attempt-saga.md) and [state-machine](./state-machine.md).

---

## 1. Successful access (chip / fingerprint)

```mermaid
sequenceDiagram
  participant Orch as AccessOrchestrator
  participant Repo as AttemptRepo
  participant Ledger as ChipService
  participant Door as HardwareService
  participant Audit as AuditLog
  participant Notify as Notification

  Orch->>Repo: insert CREATED attempt_id
  Orch->>Audit: CREATED
  Orch->>Orch: validate subject
  Orch->>Repo: CREATED to VALIDATED
  Orch->>Audit: VALIDATED
  Orch->>Ledger: charge key access-charge:id
  Ledger-->>Orch: charged
  Orch->>Repo: VALIDATED to CHARGED
  Orch->>Audit: CHARGED
  Orch->>Repo: CHARGED to DOOR_OPENING
  Orch->>Door: open operation_id wait confirm
  Door-->>Orch: CONFIRMED
  Orch->>Repo: DOOR_OPENING to COMPLETED
  Orch->>Audit: COMPLETED
  Orch->>Notify: access.granted
```

---

## 2. Door failure → balance refund success

```mermaid
sequenceDiagram
  participant Orch as AccessOrchestrator
  participant Ledger as ChipService
  participant Door as HardwareService
  participant Notify as Notification
  participant Audit as AuditLog

  Orch->>Ledger: charge OK
  Orch->>Door: open try 1
  Door-->>Orch: TIMEOUT
  Orch->>Notify: alert DoorTimeout
  Orch->>Door: open try 2
  Door-->>Orch: TIMEOUT
  Orch->>Orch: DOOR_OPENING to FAILED door_exhausted
  Orch->>Orch: FAILED to REFUND_PENDING
  Orch->>Notify: alert DoorFailedAfterCharge
  Orch->>Ledger: refund key access-refund:id
  Ledger-->>Orch: refunded
  Orch->>Orch: REFUND_PENDING to REFUNDED
  Orch->>Audit: each transition
```

---

## 3. Door failure → refund failure → MANUAL_REVIEW

```mermaid
sequenceDiagram
  participant Orch as AccessOrchestrator
  participant Ledger as ChipService
  participant Door as HardwareService
  participant Notify as Notification

  Orch->>Ledger: charge OK
  Orch->>Door: open retries exhausted
  Door-->>Orch: HARDWARE_UNAVAILABLE
  Orch->>Orch: FAILED then REFUND_PENDING
  Orch->>Ledger: refund
  Ledger-->>Orch: error unavailable
  Note over Orch: Retry refund up to REFUND_MAX_RETRIES
  Orch->>Ledger: refund still failing
  Orch->>Orch: REFUND_PENDING to MANUAL_REVIEW
  Orch->>Notify: alert RefundFailed
```

Staff later: `POST .../resolve` → `REFUNDED`.

---

## 4. Cash path — door failure → receipt issued

```mermaid
sequenceDiagram
  participant Orch as AccessOrchestrator
  participant Cash as CashSession
  participant Door as HardwareService
  participant Receipt as CashReceiptService
  participant UI as Dashboard
  participant Notify as Notification

  Orch->>Cash: take_fee atomic attempt_id
  Cash-->>Orch: OK
  Orch->>Door: open retries fail
  Orch->>Orch: REFUND_PENDING
  Orch->>Notify: alert DoorFailedAfterCharge
  Orch->>Cash: restore_fee best_effort
  Orch->>Receipt: issue receipt for attempt_id
  Receipt-->>Orch: redeem_code
  Orch->>Notify: alert CashReceiptIssued
  Orch->>UI: show redeem_code to staff
  Orch->>Orch: REFUNDED
```

If `issue` fails after retries → `MANUAL_REVIEW` + `RefundFailed`.

---

## 5. Door retry flow

```mermaid
sequenceDiagram
  participant Orch as AccessOrchestrator
  participant Door as HardwareService
  participant Audit as AuditLog

  Orch->>Orch: status DOOR_OPENING count=1
  Orch->>Door: open op_1
  Door-->>Orch: TIMEOUT
  Orch->>Audit: door_retry duration_ms
  Orch->>Orch: delay DOOR_RETRY_DELAY_MS
  Orch->>Orch: count=2 same status DOOR_OPENING
  Orch->>Door: open op_2
  Door-->>Orch: CONFIRMED
  Orch->>Orch: COMPLETED
```

Parameters: `DOOR_OPEN_TIMEOUT_MS`, `DOOR_MAX_RETRIES`, `DOOR_RETRY_DELAY_MS`.

---

## 6. Door timeout (single try detail)

```mermaid
sequenceDiagram
  participant Orch as AccessOrchestrator
  participant Door as HardwareService
  participant Notify as Notification

  Orch->>Door: open operation_id
  Note over Door: GPIO / confirm slower than DOOR_OPEN_TIMEOUT_MS
  Door-->>Orch: TIMEOUT
  Orch->>Notify: alert DoorTimeout attempt_index timeout_ms
  alt retries remaining
    Orch->>Orch: schedule retry
  else exhausted
    Orch->>Orch: FAILED door_exhausted
  end
```

---

## 7. Duplicate request / lost response after charge

```mermaid
sequenceDiagram
  participant Client as EventWorker
  participant Orch as AccessOrchestrator
  participant Ledger as ChipService
  participant Repo as AttemptRepo

  Client->>Orch: process attempt_id (first)
  Orch->>Ledger: charge access-charge:id
  Ledger-->>Orch: OK
  Note over Client: Response lost / worker crash
  Client->>Orch: process same attempt_id again
  Orch->>Repo: read status CHARGED or DOOR_OPENING
  Orch->>Ledger: charge access-charge:id
  Ledger-->>Orch: same balance no second debit
  Orch->>Orch: continue door or compensation from status
```

---

## 8. Successful cash access (happy path)

```mermaid
sequenceDiagram
  participant Orch as AccessOrchestrator
  participant Cash as CashSession
  participant Door as HardwareService

  Orch->>Orch: CREATED then VALIDATED
  Orch->>Cash: take_fee
  Cash-->>Orch: OK CHARGED
  Orch->>Door: open confirm
  Door-->>Orch: CONFIRMED
  Orch->>Orch: COMPLETED
  Note over Cash: Overpay discarded; session balance is 0 (not carried to next visitor)
```
