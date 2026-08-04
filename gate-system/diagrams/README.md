## Diagrams

These diagrams are meant to live alongside the code and evolve with it.

- `architecture.mmd`: service topology and communication
- `sequence-access.mmd`: RFID scan → authorization → door open flow (**current** charge-then-open behavior)

### Access Attempt Saga (design)

Target flow (persist attempt → idempotent charge → confirmed door → compensate on failure) is documented under:

[services/access-control-service/docs/](../services/access-control-service/docs/)

Start at `docs/README.md`. Sequence mermaid lives in `docs/sequences.md`. Update `sequence-access.mmd` when the saga is implemented (see `docs/implementation-roadmap.md` phase 7).
