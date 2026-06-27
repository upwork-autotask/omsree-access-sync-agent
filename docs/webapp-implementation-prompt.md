# Prompt — implement sync-compatibility in the OmSree CRM web app

Paste the block below to a Claude agent running **inside the OmSree CRM (Django + PostgreSQL)
repository**. It assumes the agent can read the files referenced; if the CRM repo doesn't
contain them, also paste `docs/webapp-sync-compatibility-plan.md`, `docs/sync-contract.md`,
and `docs/sync-field-mapping.csv` from the sync-agent repo.

---

You are working in the **OmSree CRM** codebase (Django + PostgreSQL). A separate **MS Access
sync agent** reads this database directly and pushes web-origin data into a legacy MS Access
file in the office. Your job is to make the CRM database **safe to read incrementally** and
**safe to expose** to that agent, following the plan in
`docs/webapp-sync-compatibility-plan.md`. The web app stays the source of truth.

Authoritative references (read them first):
- `docs/webapp-sync-compatibility-plan.md` — what to build and why.
- `docs/sync-contract.md` — which tables/fields may be exposed; KYC/financial fields are
  marked `no` and must NEVER be exposed.
- `docs/sync-field-mapping.csv` — canonical `crm_field` names the read views must expose, per
  table, with each field's decision (`key` / `yes` / `no` / `review`).

Hard constraints:
- **Never expose** columns whose decision is `no` (Aadhaar, PAN, passport, address-proof, audit)
  or `review` (until explicitly told they're signed off). Exclude them from the views entirely.
- All timestamps are `timestamptz` in **UTC**; `USE_TZ=True`.
- Every change ships with **migrations** and **tests**. Don't break existing CRM behaviour.
- Least privilege: the agent's DB role gets `SELECT` on the `sync` views only.
- Work in small, reviewable commits, one phase at a time. Run the test suite after each.

Do this in order (stop after each phase and summarise what changed):

**Phase W0 — timestamps & indexes**
1. Inventory the models that participate in sync (map them from `sync-field-mapping.csv`).
2. Ensure each has `created_at` and `updated_at` (`auto_now_add` / `auto_now`). Add where
   missing. Add a DB trigger that bumps `updated_at` on UPDATE as a backstop for raw-SQL writes.
3. Add a composite index `(updated_at, id)` per synced table. Backfill
   `updated_at = COALESCE(updated_at, created_at, now())`.
4. Tests: `updated_at` bumps via ORM save AND raw SQL UPDATE.

**Phase W1 — `sync` schema (outbound surface)**
5. Create a PostgreSQL schema `sync` (raw-SQL migration). For each agreed web→access table,
   create a **read-only view** `sync.<name>` selecting only `key`+`yes` columns, renamed to the
   canonical `crm_field` names, plus the key and `updated_at`. Exclude every `no`/`review` column.
6. Create a login role `sync_agent` with `USAGE` on schema `sync` and `SELECT` on `sync.*` only.
7. Tests: each view exposes exactly the agreed columns; asserts KYC columns are absent; the
   `sync_agent` role cannot read base tables.

**Phase W2 — deletions**
8. Standardise soft-delete (`is_active`/`is_deleted`) on synced models; surface it in the views.
   For any model that hard-deletes, write to a `sync_tombstone(table, pk, deleted_at)` table.
9. Tests: deleting a row shows as inactive/tombstoned in the sync surface.

**Phase W3 — audit + ack**
10. Add `sync_audit(ts, direction, table, row_count, cursor, agent_id, result, error)` and a
    token-authenticated `POST /api/sync/ack/` the agent calls after writing to Access; record it.

**Phase W4 — inbound (only after contract sign-off; keep behind a flag)**
11. Add `POST /api/sync/inbound/` (token auth, `SYNC_INBOUND_ENABLED` default False) accepting
    ONLY a server-side allowlist of `(table, column)` pairs — realistically Customer basic info
    and application/registration reference fields. Apply newest-wins (`updated_at`, ties → web),
    validate through models, log to `sync_audit`, reject any non-allowlisted column.
12. Tests: rejects a non-allowlisted column; accepts an allowlisted one; enforces newest-wins.

Deliverables: migrations, the `sync` views + role, tests passing, and a short `docs/SYNC.md`
in the CRM repo describing the `sync` schema, the `sync_agent` role, and how the agent connects.
Do NOT hard-code credentials. Ask me before exposing any field marked `review`.
