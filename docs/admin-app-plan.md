# Sync Agent Admin App — Implementation Plan

Turns the headless CLI agent (`agent/`) into a locally-managed application with an
**admin-login web UI** for configuring everything — database path, on/off toggles per
direction, scheduler, editable field mappings, logs, and email alerts.

Decisions (locked):
- **UI:** local **Django** web admin, bound to `127.0.0.1` only, protected by admin login.
- **Scheduler:** **embedded cron via APScheduler** (in-process, runs while the admin app is
  up) — frequency in minutes or a cron expression. No OS-level Task Scheduler.
- **CRM side:** direct **PostgreSQL** connection (the OmSree CRM's database), so mapping
  dropdowns can be populated from the *live* CRM schema. Access side stays `.accdb` via pyodbc.
- **Mapping store:** **SQLite** is the live source of truth; CSV **import/export** on top.
- **Connections screen:** Access path + password **and** PostgreSQL credentials, each with a
  **Test connection** button.
- **Mapping screen:** table and field pickers are **dropdowns populated from the live
  databases** (Access tables/columns and PostgreSQL tables/columns), not free text.

The existing `agent/` package (diff engine, Access layer, CRM client, sync orchestration)
is reused as the **sync engine library**. The Django app is a control panel around it; it
does not reimplement sync.

---

## 1. Architecture

```
  Office PC (single machine)
 ┌────────────────────────────────────────────────────────────────────┐
 │  Browser  →  http://127.0.0.1:8787   (admin login required)         │
 │      │                                                              │
 │      ▼                                                              │
 │  Django control panel  (waitress WSGI, localhost only)              │
 │   ├─ auth (admin login)        ├─ Dashboard / Sync Now / toggles    │
 │   ├─ Connections (Access + PG) ├─ Mapping editor (live dropdowns)   │
 │   ├─ Settings / Email          ├─ Schedule (cron / N minutes)       │
 │   └─ APScheduler (in-process)  └─ Log viewer                        │
 │      │                 ▲                                            │
 │      ▼                 │ reads settings + active mappings           │
 │  SQLite  agent.sqlite3 │                                            │
 │      ▲                 │                                            │
 │      │   ┌─────────────┴───────────────┐   pyodbc ─► MS Access      │
 │      └── │  sync engine (reuses agent/) │ ──────────► (.accdb)      │
 │          │                              │   psycopg2 ─► PostgreSQL   │
 │          └──────────────────────────────┘ ──────────► (OmSree CRM)  │
 └────────────────────────────────────────────────────────────────────┘
```

One process: `waitress-serve` runs the Django app on `127.0.0.1:8787` and hosts the
**APScheduler** cron in-process. While the admin app is running, the scheduler fires the
sync on the configured interval; `Sync Now` runs the same engine on demand in a background
thread (status surfaced live). Put the `waitress-serve` command in the user's Startup folder
so it launches at logon.

---

## 2. Project layout (added to this repo)

```
omsree-access-sync-agent/
  agent/                     # existing engine — refactored to accept a mapping provider
  controlpanel/              # NEW Django project
    manage.py
    controlpanel/settings.py, urls.py, wsgi.py
    syncadmin/               # NEW Django app
      models.py              # AgentSettings, TableMapping, FieldMapping, SyncRun
      admin.py               # Django admin registrations (CRUD on mappings)
      views.py               # dashboard, settings, schedule, logs, csv up/download
      services/
        engine.py            # bridges Django models -> agent.Syncer
        mappings.py          # DbMappingProvider, CSV import/export
        scheduler.py         # schtasks register/unregister helpers
        mailer.py            # SMTP alerts from AgentSettings
        secrets.py           # encrypt/decrypt secrets at rest (Fernet/DPAPI)
      management/commands/
        sync_now.py          # one pass (manual)
        sync_tick.py         # scheduled entry point (due-check + run)
        import_mappings.py   # seed SQLite from docs/*.csv
        export_mappings.py
        seed_admin.py        # create the admin login
      templates/syncadmin/   # dashboard.html, settings.html, schedule.html, logs.html, mappings.html
      static/syncadmin/
  docs/                      # plan + contract + the two mapping CSVs (seed data)
```

---

## 3. Data model (SQLite via Django ORM)

**AgentSettings** (singleton row, edited in Connections/Settings):
- Access: `access_db_path`, `access_db_password 🔒`.
- PostgreSQL (CRM): `pg_host`, `pg_port`, `pg_dbname`, `pg_user`, `pg_password 🔒`, `pg_sslmode`.
- Direction toggles: `web_to_access_enabled` (bool), `access_to_web_enabled` (bool).
- Behaviour: `dry_run` (bool), `sync_interval_minutes` (int), `cron_expr` (optional),
  `outbound_cursor`, `inbound_cursor`, `state_dir`.
- Email: `smtp_host`, `smtp_port`, `smtp_user`, `smtp_password 🔒`, `smtp_use_tls`,
  `alert_from`, `alert_recipients` (comma list), `alerts_enabled`.
- 🔒 = encrypted at rest (see §8).

**TableMapping** (one per synced table):
- `access_table`, `web_entity`, `key_column`, `direction` (`web2access` | `access2web`),
  `conflict_rule` (`web-wins` | `newest-wins`), `cursor_column`, `is_active`, `order`.

**FieldMapping** (FK → TableMapping):
- `access_column`, `crm_field`, `data_type`, `role` (`key` | `sync` | `no` | `review` | `candidate`),
  `is_active`, `notes`. Unique on (table, access_column).

**SyncRun** (log, one per pass):
- `started_at`, `finished_at`, `trigger` (`manual` | `scheduled`), `direction`,
  `status` (`ok` | `dry-run` | `error`), `tables_processed`, `rows_written`,
  `cursor`, `error_message`, `duration_ms`.

The engine pushes only **active FieldMappings** (`role` in {key, sync}) of **active
TableMappings** whose `direction` matches an **enabled** toggle. Flipping `is_active`
or a toggle in the UI changes what syncs on the next run — no redeploy.

---

## 4. Screens (all require admin login)

1. **Dashboard** — last run status, last error, next scheduled run; **[Sync Now]**;
   **[Sync Now (dry-run)]**; two big toggles **web→access** / **access→web**; live status.
2. **Connections** — Access DB path + password with **[Test Access]** (opens the `.accdb`,
   reports table count); PostgreSQL host/port/db/user/password/sslmode with **[Test
   PostgreSQL]** (`SELECT version()`); state dir. Both tests report ok/error inline.
3. **Settings** — dry-run default, behaviour flags.
4. **Email** — SMTP host/port/user/password/TLS, from, recipients, alerts on/off,
   **[Send test email]**.
5. **Schedule** — frequency in minutes (or a cron expression); **[Apply]** reschedules the
   APScheduler job live; shows next fire time; **[Pause]/[Resume]**.
6. **Mappings** — table list with active toggles; **Add mapping**: pick the Access table and
   the PostgreSQL table from **dropdowns populated from the live databases**, then map
   fields — each row picks `access_column` and `crm_field` from **dropdowns of that table's
   live columns** (no typos, always valid). Per-field `role` + active toggle; add/delete
   rows; **[Upload CSV]** / **[Download CSV]** / **[Re-import from docs/*.csv]**. Django admin
   also exposes the models for power editing.
7. **Logs** — paginated `SyncRun` table (filter by status/direction) + a tail of `sync.log`;
   per-run detail with error text.

---

## 5. Scheduler (embedded cron via APScheduler)

- A single `BackgroundScheduler` runs inside the web-admin process (started from the app's
  `AppConfig.ready()` when serving, not during migrate/test). One job, `sync_tick`, fires on
  an `IntervalTrigger(minutes=N)` or a `CronTrigger.from_crontab(cron_expr)` if a cron
  expression is set.
- The **Schedule** screen calls `scheduler.reschedule(interval_or_cron)` to update the job
  live (no restart); `pause()`/`resume()` map to APScheduler's job pause/resume; the page
  shows `job.next_run_time`.
- `sync_tick` honours the direction toggles and `dry_run`, runs a pass, records a `SyncRun`,
  and on failure sends the email alert (§7). Misfire grace + `max_instances=1` prevent
  overlap if a run exceeds the interval.
- The scheduler only runs while the admin app is up; put `waitress-serve` in Startup so it
  launches at logon. (A headless `manage.py sync_now` remains available for cron/Task
  Scheduler if ever wanted.)

---

## 6. Sync engine integration (refactor of `agent/`)

Small, surgical changes — the engine already isolates concerns:
- Add a **MappingProvider** interface; `Syncer` takes one instead of the static
  `table_whitelist`. `DbMappingProvider` returns active tables/columns from SQLite;
  the existing env/whitelist path stays for headless CLI use.
- `diff.compute_diff` already restricts to incoming columns — we additionally **intersect
  with active FieldMappings** so deactivated fields are never written, and rename
  Access↔CRM columns via the mapping (crm_field ↔ access_column).
- Add the **access→web** path (`Syncer.run_inbound`): read changed Access rows since
  `inbound_cursor` for active `access2web` tables, `POST /api/sync/inbound/`, apply
  `newest-wins`. Guarded by the `access_to_web_enabled` toggle (off by default).
- Wrap each pass so any exception → `SyncRun(status=error)` + email alert (§7).

Engine stays unit-testable with the in-memory fake; new logic (mapping intersection,
inbound, due-check) gets its own tests.

---

## 7. Error handling + email alerts

- Every pass is recorded as a `SyncRun`. On `status=error`, `mailer.send_alert()` emails
  `alert_recipients` via the configured SMTP with the error and the last ~30 `sync.log`
  lines. Throttled (no duplicate alert within e.g. 30 min for the same error signature).
- **[Send test email]** validates SMTP before you rely on it.
- Connection/auth failures (bad DB password, unreachable CRM, bad token) are caught and
  surfaced both in the UI (Dashboard banner) and by email.

---

## 8. Security

- Web server bound to **127.0.0.1** only; never exposed on the LAN.
- **Admin login** required for all views (`@login_required` + `is_staff`); `seed_admin`
  creates the first account; passwords hashed by Django auth.
- **Secrets at rest** (DB password, agent token, SMTP password) encrypted with Fernet;
  key stored via Windows DPAPI / a local key file outside source control. SQLite lives in
  `state_dir`, not in the repo.
- CSRF on all POST forms; secret fields are write-only in the UI (never rendered back).
- `.gitignore` already covers `.env`, `*.accdb`, logs; add `agent.sqlite3` and the key file.

---

## 9. CSV import / export

- Seed: `import_mappings` loads `docs/sync-field-mapping.csv` (web→access) and
  `docs/sync-field-mapping-inbound.csv` (access→web) into TableMapping/FieldMapping,
  preserving the `decision` column as `role`.
- Upload (UI): validate header against the known columns, preview a diff (new/changed/
  removed fields), then upsert on confirm. Bad rows reported, not silently dropped.
- Download (UI): export current active mappings back to the same CSV shape — round-trips
  with the generators in `docs/gen_mapping*.py`.

---

## 10. Phase plan

- [ ] **A. Scaffold** — Django project + app, SQLite, models, Django admin, `seed_admin`,
  `import_mappings` from the two CSVs. Read-only dashboard.
- [ ] **B. Settings + secrets** — Settings/Email screens, encrypted secrets, Test
  connection, Test email, direction toggles.
- [ ] **C. Sync Now + logs** — background run, `SyncRun` logging, Dashboard live status,
  Logs screen. (web→access only, reusing the proven engine path.)
- [ ] **D. Mapping editor** — per-table/per-field grid, activate/deactivate, add/remove,
  CSV upload/download; engine reads active mappings from SQLite.
- [ ] **E. Scheduler** — frequency + schtasks register/pause/resume, `sync_tick` due-check.
- [ ] **F. Email alerts** — throttled error emails wired to every pass.
- [ ] **G. Access→web (inbound)** — `run_inbound`, newest-wins, behind its toggle; off by
  default until the inbound contract is signed off.
- [ ] **H. Hardening + docs** — install script, Task Scheduler setup doc, operator runbook.

Phases A–F deliver the full web→access product you described; G is the optional inbound
direction; H is packaging.

## 11. Open questions
- One shared admin account or per-user logins (audit of who changed a mapping)?
- Should changing a mapping require a dry-run preview before it can go live?
- Email: use the office SMTP relay, or an external provider (SendGrid/Gmail app password)?
- Retain row-level sync history (`SyncRowLog`) for audit, or just per-run summaries?
```
