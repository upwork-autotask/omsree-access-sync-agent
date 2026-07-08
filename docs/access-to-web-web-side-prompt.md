# Prompt: CRM (web) changes needed for the ACCESS → WEB sync

Hand this to the web/CRM developer (or an AI coding assistant on the OmSree backend).
It is self-contained and contains no credentials. Companion to
[booking-applications-sync-spec.md](booking-applications-sync-spec.md) and
[mapping-review-decisions.md](mapping-review-decisions.md).

---

```
You are working on the OmSree CRM backend (Django + PostgreSQL, database `omsree_crm`).

## Background
An external "sync agent" mirrors data between the office MS Access database and this
CRM. It connects to PostgreSQL as the role `sync_agent`. Today `sync_agent` has
SELECT-only access through read-only views (`sync.*` and `public.tbl_*`), and the sync
only runs WEB → ACCESS.

We now need the reverse direction, ACCESS → WEB: changes made in Access must be written
back into the CRM. The agent is already built for this and is waiting on the CRM side.

## Exactly what the agent does when writing
For each changed row it runs ONE statement, as `sync_agent`, inside a per-table
transaction:

    INSERT INTO <target_relation> (<col1>, <col2>, ...)
    VALUES (%s, %s, ...)
    ON CONFLICT (<key_column>) DO UPDATE
      SET <col2> = EXCLUDED.<col2>, ... ;   -- every non-key column

`<target_relation>` is the SAME relation the agent reads for diffing (the `pg_table`
in its mapping — e.g. `public.tbl_Customer`). So each target must be BOTH readable and
writable by `sync_agent`, and must have a UNIQUE/PRIMARY KEY constraint on
`<key_column>` so `ON CONFLICT` works.

## Your task
For the agreed set of tables below, make the CRM accept these writes. For each target,
choose ONE mechanism and implement it:

  (A) Point the target at a real writable table `sync_agent` owns, OR
  (B) Keep the existing `public.tbl_*` view name but make it UPDATABLE via
      `INSTEAD OF INSERT` and `INSTEAD OF UPDATE` triggers that write to the correct
      base tables (`bookings_*`, etc.) with validation. [Preferred — keeps the agent
      config unchanged.] OR
  (C) If you prefer an RPC boundary, expose a SECURITY DEFINER upsert function per
      entity and tell us — the agent's write path would then be pointed at the function
      instead of a direct upsert.

For each target you must:
  1. GRANT SELECT, INSERT, UPDATE ON <target> TO sync_agent;
  2. Ensure a UNIQUE or PRIMARY KEY constraint exists on the key column.
  3. Ensure every listed column exists, is correctly typed, and maps to the right
     base-table column.
  4. Validate/whitelist inbound columns server-side. NEVER accept KYC or financial
     fields (Aadhaar, PAN, passport, address proof, bank/loan/registration/TDS fields)
     from the inbound path unless explicitly signed off. Log/audit every inbound write.

## Tables, targets and keys currently configured for ACCESS → WEB
(target relation — key column — columns the agent will send today)

  public.tbl_Customer                       — customer_id         — customer_id
  public.tbl_Property                       — property_id         — property_id, property_name
  public.tbl_Property_Status                — property_status_id  — property_status_id
  public.tbl_Property_Type                  — property_type_id    — property_type_id, property_type, isactive
  public.tbl_Property_Details               — property_details_id — property_details_id, property_status_id
  public.tbl_Block                          — id                  — id
  public.tbl_PropertyBlock                  — id                  — id
  public.tbl_DocumentRecord                 — id                  — id
  public.tbl_Other_Charges_Receipts         — receipt_id          — receipt_id
  public.tbl_PropertyDetailsPaymentSchedule — id                  — id

IMPORTANT: most of the above currently have ONLY their key column enabled to flow
(a placeholder). Before real data moves Access → web, the OmSree side must agree which
non-key columns are allowed for each table, and we will enable exactly those fields in
the agent (role sign-off). Please tell us, per table, the full set of columns you want
to accept inbound.

## Two problems the agent's dry-run already found — fix these
  1. `tbl_PropertyMilestones`: there is NO such relation in the CRM. Either create the
     target (readable + writable) or tell us to drop this mapping.
  2. `public.tbl_Property_Details` has no `property_status_id` column, but the inbound
     mapping writes it. Add the column to the view/base table (map it to the unit/
     booking status), or tell us to drop that field from the mapping.

## Please confirm scope before enabling
The agent's dry-run showed it WOULD touch reference tables (Block, PropertyBlock,
Property_Status, Property_Type). Confirm which tables are actually allowed to flow
Access → web, so we only enable those. Everything else stays web → access only.

## Acceptance criteria
  - `sync_agent` can run the INSERT ... ON CONFLICT statement above against each agreed
    target and it lands in the correct base tables.
  - A sample round-trip works: edit a row in Access → run the inbound sync → the change
    appears in the CRM UI.
  - No KYC/financial field can be written via the inbound path.
  - Provide: the DDL you applied (triggers/grants/constraints), the per-table list of
    inbound columns you accept, and confirmation of which targets are live.
```

---

## Agent-side status (already done, for reference)
- The inbound engine (`run_access_to_web`) is built and verified by dry-run: it reads
  Access, diffs against the CRM target, isolates per-table errors, and writes nothing
  until the direction is enabled.
- Writes are transactional per table (`INSERT ... ON CONFLICT DO UPDATE`), with a
  blank-key guard and per-table rollback so one failure can't cascade.
- Only `sync`-role (active) fields flow, so KYC/no-leave columns can't travel back.
- The `access → web` direction is toggled **OFF** and stays off until a writable CRM
  target + grants exist. Then: toggle on → inbound dry-run to review the diff → go live.
