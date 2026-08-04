# Risks — Access Attempt Saga

Residual risks after adopting the design in this pack. Accepted or mitigated as noted.

---

## Product / physical

| Risk | Impact | Mitigation |
|------|--------|------------|
| Door “confirmed” ≠ person walked through | Free entry if someone holds the door; or charge without passage if they walk away | Out of scope for v1; optional future beam/sensor |
| Cash receipt depends on staff till discipline | Visitor holds code but staff delay / forget payout | Dashboard alert + unredeemed receipt queue; ops training |
| No physical coin returner | Cannot auto-eject coins | Receipt is authoritative compensation; `restore_fee` best-effort only |

---

## Consistency / distributed

| Risk | Impact | Mitigation |
|------|--------|------------|
| Crash after charge before status flush | Ambiguous money state | Charge only after attempt row exists; idempotent keys; reconciler |
| Reconciler opens door after COMPLETED lost locally | Double unlock | Never DoorPort if terminal; unique door ops; read DB status first |
| Refund succeeds but transition to REFUNDED fails | Money returned, status stuck REFUND_PENDING | Idempotent refund + reconciler retries transition |
| Charge after door (rejected alternative) not used | — | Documented in ADR-001 |

---

## Cash-specific

| Risk | Impact | Mitigation |
|------|--------|------------|
| `restore_fee` after new coins mixes sessions | Confusing session total | Receipt is source of truth; do not rely on restore |
| Receipt redeem fraud / code leak | Till loss | High-entropy codes; single-use; management auth; void audit |
| Two coin events race to take fee | Double door / double take | Atomic `take_fee` under one lock (roadmap phase 4) |

---

## Fingerprint

| Risk | Impact | Mitigation |
|------|--------|------------|
| Starting saga before staff approve | Charge without consent UX | Approval boundary: saga starts only on approve |
| Approval expires while door retries | Confusing UI | Attempt owns lifecycle after approve; approval store cleared |

---

## Operational

| Risk | Impact | Mitigation |
|------|--------|------------|
| MANUAL_REVIEW pile-up if chip-service down | Staff load; unpaid visitors | Alerts; runbook; refund retries; health checks |
| Ephemeral quick-tunnel / public URL unrelated | — | Not part of entrance saga |
| Config too aggressive timeouts | False door failures → unnecessary refunds | Tune `DOOR_*` on real Pi; metrics on false positives |

---

## Security

| Risk | Impact | Mitigation |
|------|--------|------------|
| Spoofed internal refund calls | Balance credit | Internal network only; no public refund route |
| Replay of hardware events | Double attempts | Unique `hardware_event_id`; in-flight attempt guard |

---

## Documentation drift

| Risk | Impact | Mitigation |
|------|--------|------------|
| Implementation diverges from this pack | Silent inconsistency | Update docs in same PR as code; keep ADR status current |
