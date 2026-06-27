# OmSree Access Sync Agent

A small Windows-side sync agent that keeps the legacy **MS Access** database (`.accdb` / `.mdb`)
running in the office in sync with the **OmSree CRM web app** over HTTPS.

The Access file never leaves the LAN. Only agreed columns move across the wire.

## Repo layout

- `docs/implementation-plan.md` — detailed build plan (table mapping, API payloads, conflict rules, rollout)
- `docs/sync-contract.md` — **filled** against the real OmSree schema (DRAFT v0.1, pending client sign-off)
- `agent/` — Windows sync agent (Python)
  - `config.py` — `.env`-driven config
  - `diff.py` — pure web-vs-Access diff engine (the dry-run core)
  - `crm_client.py` — outbound / inbound / ack HTTP client
  - `access_db.py` — Access via pyodbc (password + ACE driver) plus an in-memory fake for tests
  - `sync.py` — pull → diff → backup → apply → ack orchestration
  - `state.py` — `last-sync.json` cursor + rolling `sync.log`
  - `__main__.py` — `sync` / `schema` / `status` CLI
- `tests/` — unit + integration tests (`pytest`)
- `.env.example` — config the agent expects

## The real database

The OmSree CRM lives in MS Access. Two copies of the same database exist:

- **`db tables.accdb`** — clean 108-table set; the reference used for the sync contract.
- **`CRM Backend v6.0.accdb`** — same schema plus working/`_test`/`_backup`/`Copy Of` tables.

Both are **password-protected** — set `ACCESS_DB_PASSWORD` in `.env`. The core tables
are column-identical across the two (two trivial deltas noted in the contract), and the
agent only ever writes whitelisted columns, so it is safe against either file.

## Quick start (for the laptop)

```bash
git clone <this-repo-url>
cd omsree-access-sync-agent
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
copy .env.example .env                            # then edit values (set ACCESS_DB_PASSWORD)

python -m agent schema                            # list tables in the Access DB (row counts; * = whitelisted)
python -m agent schema tbl_Property_Details       # dump one table's columns
python -m agent sync --dry-run                    # read Access + pull outbound, print a diff, write nothing
python -m agent status                            # last sync time, cursor, last error
```

Run tests with `pip install -r requirements-dev.txt && python -m pytest`.

## Commands

- `python -m agent schema [table]` — introspect the Access DB (no CRM calls).
- `python -m agent sync [--dry-run] [--loop]` — pull web-origin changes and apply to Access (web wins; never deletes; backs up before the first write of a run).
- `python -m agent status` — print the persisted cursor / last result / last error.

## Status

Phase 1 (read-only dry run) implemented; sync contract drafted against the real schema.
Web→Access only. Nothing writes by default (`DRY_RUN=true`). Read
`docs/implementation-plan.md` and `docs/sync-contract.md` before enabling writes.
