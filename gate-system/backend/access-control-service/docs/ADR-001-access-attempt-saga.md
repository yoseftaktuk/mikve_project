# ADR-001: Access Attempt Saga with Compensation

- **Status:** Accepted (design)
- **Date:** 2026-08-04
- **Context:** Gate entrance (chip, fingerprint, cash)

## Context

Today `access-control-service` charges (or takes cash) and then asks `hardware-service` to open the door. The door endpoint returns HTTP 204 immediately while unlock runs in a background task. If the relay fails after charge, the visitor loses money without access.

Requirements:

- Every attempt has a UUID and is persisted before charge.
- Charge and refund are idempotent.
- Door success requires positive confirmation (with timeout and retries).
- Automatic compensation on door failure after charge.
- Cash has no physical change dispenser — compensation is a staff receipt.

## Decision

Adopt a **Saga / compensation** pattern owned by `access-control-service` (AccessOrchestrator):

1. Persist `AccessAttempt` in `CREATED`.
2. Validate → `VALIDATED`.
3. Idempotent charge (chip balance or atomic cash take) → `CHARGED`.
4. Door open with confirmation and retries → `DOOR_OPENING` → `COMPLETED`.
5. On door exhaustion after charge → `FAILED` → `REFUND_PENDING`:
   - Chip/fingerprint: idempotent balance refund → `REFUNDED`.
   - Cash: best-effort session restore + **cash receipt** issue → `REFUNDED`.
6. If compensation fails → `MANUAL_REVIEW` + alert.

Orchestrator depends on ports (ledger, cash, door, audit, notifications). No new microservice is required for v1.

## Alternatives considered

### A. Charge after door open

Open first, then charge.

- **Pros:** Never charges if door command fails.
- **Cons:** Door can open without payment on charge failure; race-friendly for free entry; worse for a paid physical gate.

**Rejected** for monetary integrity at a fee gate.

### B. Distributed 2PC / XA

- **Pros:** Strong consistency across services.
- **Cons:** Chip-service and hardware-service are not XA participants; high operational cost; door is not a transactional resource.

**Rejected** as impractical.

### C. Saga with compensation (chosen)

- **Pros:** Fits existing HTTP services; uses existing chip idempotency keys; explicit states for ops; cash receipt models real-world till process.
- **Cons:** Eventual consistency window; needs reconciler for crashes; door “confirmed” ≠ person walked through.

## Consequences

- Hardware door API must grow confirmation semantics (sync success or correlated `door.opened` / `door.failed`).
- New persistence: attempts, payment/refund rows, door operations, cash receipts, audit log.
- Management UI needs MANUAL_REVIEW queue and cash receipt redeem.
- Reconciler must resume stale `CHARGED` / `DOOR_OPENING` / `REFUND_PENDING` attempts.

## References

- [SDS-access-attempt-saga.md](./SDS-access-attempt-saga.md)
- [state-machine.md](./state-machine.md)
- [../ACCESS_LOGIC_REVIEW.md](../ACCESS_LOGIC_REVIEW.md) § High #1
