# MS Access ⇄ OmSree CRM — Sync Agent Implementation Plan

This builds on the high-level plan in the CRM repo (`generated/ms-access-sync-plan.md`).
Here we go one level deeper: architecture, payloads, conflict rules, and a phased rollout
you can actually start coding against from a laptop.

## 1. Architecture

```
  Office LAN                                  Cloud
 ┌────────────────────────┐                ┌──────────────────────────┐
 │  MS Access (.accdb)     │                │   OmSree CRM (Django)     │
 │        ▲   │            │   HTTPS        │                          │
 │        │   ▼            │  (token auth)  │   /api/sync/outbound/    │
 │   ┌──────────────┐      │ ─────────────► │   /api/sync/inbound/     │
 │   │ Sync Agent    │     │ ◄───────────── │   /api/sync/ack/         │
 │   │ (Windows/Py)  │     │                │                          │
 │   └──────────────┘      │                │   sync_audit table       │
 └────────────────────────┘                └──────────────────────────┘
```

- **Source of truth = the web app** for masters, bookings, pricing, eSign, payments.
- **Access** only pushes back a small set of agreed Access-origin reference rows (if any).
- Agent talks to Access locally via ODBC / `pyodbc` (or `mdbtools` on Linux for testing).
- All transport is HTTPS with a bearer token issued per office machine.

## 2. Sync direction (default)

| Data | Direction | Notes |
|---|---|---|
| Projects, Blocks, Floors, Unit masters | web → Access | full master refresh |
| Unit status, ownership, base pricing | web → Access | changed-rows only |
| Bookings / Applications | web → Access | new + updated |
| Customer basic info (on a booking) | web → Access | non-confidential fields only |
| CPP status, eSign status | web → Access | status + doc reference, not the file |
| Payment status / receipts | web → Access | amount, date, milestone |
| Legacy reference rows (if agreed) | Access → web | optional, phase 2+ |

> Table is rendered as a list in chat surfaces; kept as a table here for the repo reader.

## 3. Sync APIs (Django side)

### `GET /api/sync/outbound/?since=<cursor>&tables=...`
Agent pulls web-origin changes since the last cursor.

Response:
```json
{
  "cursor": "2026-06-27T11:20:00Z#4821",
  "batches": [
    {
      "table": "unit_master",
      "key": "unit_code",
      "rows": [
        {"unit_code": "GLY-A-0203", "status": "BOOKED", "ownership": "...",
         "base_price": 4520000, "updated_at": "2026-06-27T10:11:00Z"}
      ]
    }
  ],
  "has_more": false
}
```

### `POST /api/sync/inbound/`
Agent pushes agreed Access-origin rows (phase 2+). Same envelope, server validates against
the contract and rejects any column not whitelisted.

### `POST /api/sync/ack/`
Agent confirms a batch was written to Access; server advances the agent's cursor and writes
to `sync_audit`.

**Auth:** `Authorization: Bearer <agent-token>`. One token per machine, revocable, scoped to sync endpoints only.

**Audit:** every batch in/out logged with agent id, table, row count, cursor, result, timestamp.

## 4. Conflict rules

- Web-origin tables: **web always wins**. Access is overwritten on those columns.
- Access-origin tables (phase 2+): **newest `updated_at` wins**, ties → web wins.
- Agent never deletes Access rows automatically; out-of-scope rows are left untouched.
- Unknown/extra columns in Access are ignored, never overwritten.

## 5. The sync agent (Windows)

Minimal Python app:

- Config: Access DB path, CRM base URL, agent token, sync interval, table whitelist.
- Commands:
  - `sync --dry-run` — read Access + pull outbound, print a diff, write nothing.
  - `sync` — apply changes inside an Access transaction; ack on success.
  - `status` — last sync time, last cursor, last error.
- Scheduling: Windows Task Scheduler or a built-in `--loop <minutes>`.
- Reporting: writes `last-sync.json` + a rolling `sync.log`; surfaces conflicts/errors.
- Safety: takes a `.accdb` backup copy before the first write of each run.

## 6. Phase plan

1. **Phase 0 — contract.** Fill `docs/sync-contract.md` against the real Access schema. No code ships without it.
2. **Phase 1 — read-only dry run.** Outbound API + agent `--dry-run`. Prove the diff is correct on a copy of the real DB.
3. **Phase 2 — write to staging.** Agent writes to a *copy* of Access; verify row-by-row.
4. **Phase 3 — production.** Backups + rollback log + scheduled sync. Web→Access only.
5. **Phase 4 — inbound (optional).** Access→web reference rows, if the contract calls for it.

## 7. Open questions to resolve with the client

- Exact Access table + column names (need the real schema dump).
- Which customer fields are allowed to leave the web app onto the office machine.
- Sync frequency (every X minutes vs manual button).
- Who owns conflict resolution when both sides edit the same booking.

## Next steps

- [ ] Get a real Access schema dump (`mdb-tables` / `mdb-schema`) and fill the contract.
- [ ] Stand up `/api/sync/outbound/` behind a feature flag in the CRM.
- [ ] Build the agent `--dry-run` path first; ship nothing that writes until the diff is trusted.
