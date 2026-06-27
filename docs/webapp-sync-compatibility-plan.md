# OmSree CRM (web app) — Sync-Compatibility Plan

Changes the **OmSree CRM Django app + PostgreSQL** needs so the sync agent can read
a stable, contract-bounded, change-trackable surface — and (optionally, later) accept
agreed reference data back from Access.

This is the *web-app* counterpart to `implementation-plan.md` (the agent) and
`sync-contract.md` (the field-level agreement). The agent connects **directly to
PostgreSQL** and maps `crm_field → access_column` per `sync-field-mapping.csv`.

> Guiding principle: the web app stays the source of truth. These changes make its data
> **safe to read incrementally** and **safe to expose** without leaking confidential fields.

---

## 1. Why changes are needed

The agent pulls "rows that changed since a cursor", maps a whitelist of columns, and
writes them to Access. For that to be correct and safe, the CRM must guarantee:

1. **A reliable change marker** on every synced row (so incremental pulls don't miss or
   double-process rows).
2. **A stable key** matching the Access key.
3. **A way to represent deletions** (the agent never deletes Access rows).
4. **A bounded surface** that exposes only agreed columns and *never* KYC/financial secrets.
5. **Least-privilege access** for the agent's DB login.
6. **(Phase 4)** a guarded inbound path for agreed Customer / application reference data.

---

## 2. Change tracking (cursors)  ★ required first

- Every synced model gets `created_at` and `updated_at` (`timestamptz`, `USE_TZ=True`,
  stored in UTC). `updated_at` via Django `auto_now=True` **and** a DB trigger as a backstop
  (so raw SQL / admin bulk updates still bump it).
- Add a composite index `(updated_at, id)` on each synced table.
- **Cursor semantics:** the agent's cursor is the pair `(updated_at, id)`. Pull is
  `WHERE (updated_at, id) > (:ts, :id) ORDER BY updated_at, id LIMIT :n`. The pair avoids
  skipping rows that share a timestamp.
- Backfill `updated_at = COALESCE(updated_at, created_at, now())` in the migration.

## 3. Deletions → soft-delete, not hard delete

- The agent never deletes Access rows. To reflect web-side removals, synced models use
  **soft delete**: an `is_active`/`is_deleted` flag (most OmSree tables already carry
  `ISACTIVE`). Deletions flip the flag; the agent then deactivates the matching Access row
  instead of deleting it.
- If a model must hard-delete, write a tombstone row to `sync_tombstone(table, pk,
  deleted_at)` so the agent can act on it. Prefer soft delete everywhere feasible.

## 4. The `sync` schema — read-only contract views  ★ the key change

Create a dedicated PostgreSQL schema `sync` with **one read-only view per sync table**,
named to match `pg_table` in the agent's mappings (e.g. `sync.unit_master`,
`sync.customer`, `sync.booking`, `sync.payment_schedule`, `sync.payment_receipt`,
`sync.document_record`). Each view:

- selects **only the whitelisted columns** from `sync-field-mapping.csv` (decision = `yes`/`key`);
- **renames** internal columns to the canonical `crm_field` names the mapping expects;
- **excludes KYC/financial secrets entirely** (Aadhaar/PAN/passport/address-proof, and any
  field marked `no`) — defense in depth, so a mapping mistake can't leak them;
- exposes the stable key and `updated_at`.

Benefits: internal table refactors don't break sync; the contract is enforced at the DB
boundary; the agent's "review" columns simply aren't in the view until agreed.

> The agent then points each mapping's `pg_table` at `sync.<view>` instead of the base table.

## 5. Least-privilege DB role for the agent

- Create role `sync_agent` (LOGIN) with **`USAGE` on schema `sync`** and **`SELECT` only on
  the `sync.*` views** — no access to base tables, no write.
- The agent's PostgreSQL credentials (Connections screen) use this role.
- (Phase 4 inbound is a separate authenticated API, not DB write — see §6.)

## 6. Inbound (access → web) — agreed reference data only  ·  Phase 4

Only for fields the sync contract signs off (realistically **Customer** basic info and
**application/registration reference** fields). Implement as a Django endpoint, not DB write:

- `POST /api/sync/inbound/` — token auth (per-agent bearer token), feature-flagged
  (`SYNC_INBOUND_ENABLED`), **server-side allowlist** of `(table, column)` pairs.
- Rejects any column not on the allowlist (415/422), applies **newest-wins**
  (`updated_at` compare, ties → web), and writes through normal model validation.
- Optionally land rows in an `inbound_staging` table for human review before commit.
- Every batch logged to `sync_audit` (§7).

## 7. Audit + ack

- `sync_audit(id, ts, direction, table, row_count, cursor, agent_id, result, error)` —
  written by the inbound endpoint, and by an optional `POST /api/sync/ack/` the agent calls
  after a successful outbound→Access write (lets the CRM record what reached the office).

## 8. Config / feature flags (Django settings)

- `SYNC_ENABLED` (expose `sync` schema / endpoints at all),
- `SYNC_INBOUND_ENABLED` (default off),
- `SYNC_INBOUND_ALLOWLIST` (table → columns),
- per-agent tokens table for auth + revocation.

## 9. Tests (must ship with the code)

- Each `sync.*` view exposes **exactly** the agreed columns; **KYC columns are absent**.
- `updated_at` bumps on update via both ORM and raw SQL (trigger backstop).
- Cursor query returns rows once and in order across a timestamp tie.
- Soft-delete flips the flag and shows in the view as inactive.
- Inbound endpoint **rejects** a non-allowlisted column; accepts an allowlisted one;
  enforces newest-wins; writes `sync_audit`.
- `sync_agent` role cannot read base tables or write.

## 10. Rollout (mirror the agent's phases)

1. **W0 — timestamps & indexes.** `created_at`/`updated_at` + triggers + indexes + backfill. No behaviour change.
2. **W1 — sync schema (outbound).** `sync.*` views for the agreed web→access tables + the `sync_agent` role. Agent points `pg_table` at the views; run dry-run end-to-end.
3. **W2 — deletions.** Soft-delete/tombstones reflected in the views.
4. **W3 — audit + ack.** `sync_audit` + ack endpoint.
5. **W4 — inbound (optional).** `/api/sync/inbound/` for the agreed Customer/application reference fields, behind the flag, after contract sign-off.

## 11. Open questions for the CRM team

- Which models already have `updated_at`, and are any updated via raw SQL (need the trigger)?
- Hard deletes anywhere that must be mirrored (→ tombstones), or is everything soft-delete?
- Confirm the exact `crm_field` names per the mapping CSV so the views rename correctly.
- Inbound auth: reuse existing CRM auth/users, or a dedicated agent-token table?
