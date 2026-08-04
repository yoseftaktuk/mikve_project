# Access Attempt Saga — Design Pack

Design documentation for a robust access transaction flow that guarantees users are never permanently charged if the door fails to open.

**Status:** Design only. No production code in this pack.  
**Scope:** Chip, fingerprint, and cash entrance methods.  
**Cash compensation:** Staff-redeemable receipt/voucher (no physical coin returner).

## Locked decisions

| Topic | Choice |
|-------|--------|
| Methods | Chip + fingerprint + cash |
| Cash refund | Issue a staff-redeemable cash receipt; optional best-effort restore into the in-memory cash session |
| Identity | No `UserId` table — use `subject_type` + `subject_ref` (maps to audit “UserId”) |
| Deployables | Prefer existing services (`access-control-service`, `chip-service`, `hardware-service`); orchestrator owns the saga |

## Documents

| Document | Description |
|----------|-------------|
| [ADR-001-access-attempt-saga.md](./ADR-001-access-attempt-saga.md) | Why saga + compensation (vs charge-after-door / 2PC) |
| [SDS-access-attempt-saga.md](./SDS-access-attempt-saga.md) | Software design specification (architecture, logging, alerts, failures, config, security) |
| [state-machine.md](./state-machine.md) | States, valid/invalid transitions |
| [sequences.md](./sequences.md) | Sequence diagrams (success, door fail, refund, cash receipt, retry, timeout) |
| [data-model.md](./data-model.md) | Tables / entities |
| [api-contracts.md](./api-contracts.md) | Ports and HTTP/API shapes |
| [implementation-roadmap.md](./implementation-roadmap.md) | Phased build plan (later) |
| [risks.md](./risks.md) | Residual risks |

## Related code (today)

- Charge-then-open: `app/access_logic.py`
- Fire-and-forget door: `hardware-service` `POST /door/open` (204 before relay completes)
- Chip idempotency: `chip-service` `chip_activity.idempotency_key`
- Problem backlog: [../ACCESS_LOGIC_REVIEW.md](../ACCESS_LOGIC_REVIEW.md)

## Current gap

```mermaid
sequenceDiagram
  participant ACS as AccessControl
  participant CS as ChipService
  participant HS as HardwareService
  ACS->>CS: adjust_balance minus fee
  CS-->>ACS: OK
  ACS->>HS: POST door/open
  Note over HS: Returns 204 immediately; unlock is fire-and-forget
  HS-->>ACS: 204
  Note over ACS: If relay fails after 204, money already taken
```
