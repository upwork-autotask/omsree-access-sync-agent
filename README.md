# OmSree Access Sync Agent

A small Windows-side sync agent that keeps the legacy **MS Access** database (`.accdb` / `.mdb`)
running in the office in sync with the **OmSree CRM web app** over HTTPS.

The Access file never leaves the LAN. Only agreed columns move across the wire.

## Repo layout

- `docs/implementation-plan.md` — detailed build plan (table mapping, API payloads, conflict rules, rollout)
- `docs/sync-contract.md` — table-wise sync contract template to fill in against the real Access schema
- `agent/` — Windows sync agent (Python) — scaffold to be filled in
- `.env.example` — config the agent expects

## Quick start (for the laptop)

```bash
git clone <this-repo-url>
cd omsree-access-sync-agent
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
copy .env.example .env                            # then edit values
python -m agent sync --dry-run                    # read Access, show what WOULD change
```

## Status

Planning + scaffold stage. Read `docs/implementation-plan.md` first.
