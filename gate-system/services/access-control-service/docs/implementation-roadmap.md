# Implementation Roadmap — Access Attempt Saga

Design is complete in this folder. **No production code in the design phase.**  
This roadmap is for a later implementation effort.

---

## Phase 0 — Prerequisites

- Agree ops process for cash receipt redeem at the till.
- Confirm hardware can return sync confirm (or commit to Redis `door.opened` / `door.failed` with `operation_id`).

---

## Phase 1 — Persistence and audit

1. Postgres migrations / startup DDL for tables in [data-model.md](./data-model.md).
2. `AccessAttemptRepository` with CAS `transition`.
3. `AuditLogPort` append on every transition.
4. Unit tests: CAS conflict, transition matrix happy/invalid samples.

**Exit:** Can create attempt and move `CREATED` → `VALIDATED` → `FAILED` without calling hardware.

---

## Phase 2 — Door confirmation

1. Upgrade `hardware-service` `POST /door/open` per [api-contracts.md](./api-contracts.md).
2. Implement `IDoorPort` with timeout → `TIMEOUT`.
3. Record `door_operations` rows.
4. Tests: confirm, timeout, unavailable (fake adapter).

**Exit:** Orchestrator can drive `CHARGED` → `DOOR_OPENING` → `COMPLETED` with a mock ledger.

---

## Phase 3 — Chip / fingerprint ledger

1. Wire `ILedgerPort` to chip-service with `access-charge:` / `access-refund:` keys.
2. Ensure ACS client passes `idempotency_key` (extend current `ChipClient.adjust_balance` if needed).
3. Replace `process_chip_access` charge-then-open with orchestrator.
4. Fingerprint approve starts saga after approval only.
5. Tests: double charge same attempt, refund after door fail.

**Exit:** Chip/FP never stay charged on door failure in automated tests.

---

## Phase 4 — Cash atomic take + receipt

1. `CashSession.try_pay` / `take_fee` + `restore_fee` (fixes review item #2).
2. `CashReceiptService` issue/redeem/void.
3. Management API + dashboard UI for redeem and MANUAL_REVIEW.
4. Alerts: `CashReceiptIssued`, `DoorFailedAfterCharge`.

**Exit:** Cash door failure produces a redeemable receipt end-to-end in staging.

---

## Phase 5 — Orchestrator cutover

1. Route RFID, cash-fee-reached, and fingerprint-approve through AccessOrchestrator.
2. Keep legacy `access_logs` write on terminal states for compatibility.
3. Feature flag / staged rollout if needed.

---

## Phase 6 — Reconciler and alerts

1. Startup + periodic job for stale `{CHARGED, DOOR_OPENING, REFUND_PENDING}`.
2. Alert channel + RepeatedFailures detector.
3. Chaos tests: kill ACS mid-door, restart, expect REFUNDED/COMPLETED.

---

## Phase 7 — Hardening

1. Full state-machine matrix tests.
2. Load/duplicate scan tests.
3. Update [diagrams/sequence-access.mmd](../../../diagrams/sequence-access.mmd) to saga flow.
4. Mark [ACCESS_LOGIC_REVIEW.md](../ACCESS_LOGIC_REVIEW.md) items #1–#2 resolved when done.

---

## Suggested first slice (minimal valuable)

1. Schema + repository + audit  
2. Door confirm API + DoorPort  
3. Chip charge/refund with attempt keys + compensation on door fail  
4. Tests for that path  

Cash receipt and fingerprint cutover can follow immediately after.
