# ADR 0001: Replace the fake outbox with a transactional audit log

- Status: accepted
- Date: 2026-09-13
- Scope: CHANGE-06

## Context

The former `PublishOutbox` loop only logged each row and then marked it as
published. It had no broker, downstream consumer or delivery acknowledgement,
so its pending/published/retry state claimed delivery guarantees that did not
exist. The rows are nevertheless useful as an operational and business audit
trail.

## Decision

New application code appends immutable facts to `audit_events` through the
`AuditRepository` owned by the same `SqlAlchemyUnitOfWork` as the related
business mutation. The repository exposes only `append`; PostgreSQL rejects
row updates and deletes with an immutable-audit trigger. Audit rows have no
delivery status, attempt counter or retry schedule.

No message broker or consumer is introduced: the product currently needs an
audit log, not event delivery. A real transactional outbox may be designed as a
separate feature if an actual consumer is added later.

## Rolling compatibility

The expand migration keeps `outbox_events` and installs an `AFTER INSERT`
trigger before a catch-up copy. This mirrors inserts from old replicas into
`audit_events`, deduplicated by `legacy_outbox_event_id`. New replicas write
only to `audit_events`.

The old ARQ name `publish_outbox` remains registered as a tombstone that returns
`{"status": "deprecated_noop"}` without reading the container or touching
PostgreSQL. It is no longer a `JobName`, no producer enqueues it and no cron
schedules it.

After every old replica and queued legacy job has drained, a future contract
migration may remove the mirror trigger, its function and `outbox_events`.

## Consequences

- Audit facts commit and roll back atomically with their originating state.
- The schema and API no longer imply nonexistent message delivery.
- Old replicas and stale Redis jobs remain safe during a rolling deployment.
- Audit storage is append-only and requires an explicit retention/export
  decision if its volume later becomes material.
