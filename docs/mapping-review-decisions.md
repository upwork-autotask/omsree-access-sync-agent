# Mapping review & decisions (web → Access)

Findings and decisions from reviewing the CRM (PostgreSQL views) ⇄ MS Access mapping.

## Corrected table targets

The CRM exposes **views** (`public.tbl_*`, `sync.*`) that `sync_agent` reads. Two
Access tables were being conflated:

| Access table | Holds | Correct CRM source |
|---|---|---|
| `tbl_PropertyDefaults` (~4,590 rows) | **Unit & pricing master** | `sync.unit` (key `UNIT_CODE` = `sync.unit.code`) |
| `tbl_Property_Details` | **Booking / application details** | booking applications (`sync.booking` + `sync.customer`) |

The original mapping pushed **unit data into `tbl_Property_Details`**, which is why
Access kept demanding booking-only required fields (`PROPERTY_TYPE_ID`,
`PROPERTY_STATUS_ID`). Fixed: unit & pricing now targets `tbl_PropertyDefaults`.

## #1 — Unit & Pricing → `tbl_PropertyDefaults` (DONE)

Key = `UNIT_CODE`; `sync.unit.code` matches Access on 4,559/4,561 rows.

Synced (web-wins): `UNIT_CODE`, `Block`, `Floor_No`, `Flat_No`, `Types`, `SBUA`,
`BASE_PRICE`, `Club_House`, `Infrastructure_Charges`, `Car_Parking`.

**Held columns (excluded — would corrupt, not refresh):**
- `PROPERTY_TYPE` — CRM is defaulted to `omsree` for every unit; Access holds the
  real ownership (`Landlord`/`AGPA`/`GPA`/`Common`/`Builder`). **Access is
  authoritative for ownership; never overwrite from web.**
- `Project` — CRM `property_id` equals *newer* Access `PROPERTY_ID`s, but Access has
  duplicate property master rows (e.g. 14 and 26 both = "Brilliance"); re-pointing is a
  whole-DB cleanup, not a data refresh.
- `Floor_Rise_Charges` — Access stores the **total**; CRM `floor_rise` is a **rate**
  (₹100/sqft). Verified Access total = rate × `SBUA` on 1,710/1,710 rows. Access is
  already correct; derive (rate × area) when productionised.

**Data gaps to fix on the web side:** 519 units have a blank `UNIT_CODE`
(all of `OM SREE MADHUBAN` (property 39) and `OM SREE SKY Park` (property 32)).
They cannot key into Access until codes are assigned.

## #2 — Applications → `tbl_Property_Details` (IMPLEMENTED on working copy)

Source: `public.tbl_Property_Details` view, filtered to rows that have a booking
(`customer_id` present). Key `PROPERTY_DETAILS_ID` = unit id (explicit-id insert +
AutoNumber reseed).

Note: the view's `customer_id` is the **booking-application id**, which is exactly what
Access `CUSTOMER_ID` expects (Access `tbl_Customer` is one row per booking with up to
four applicants in columns). It is NOT mislabeled; customers already sync into Access
and applications reference them cleanly.

**Sequencing (required):** sync `tbl_Customer` **before** applications so the
`CUSTOMER_ID` foreign key resolves.

**FK-integrity filter:** only applications whose `CUSTOMER_ID` exists in Access
`tbl_Customer` are written. Draft bookings with no customer entered (e.g. booking ids
125, 130) are excluded — they would be FK orphans.

**Mapped (additive):** `PROPERTY_DETAILS_ID`, `CUSTOMER_ID`, `PROPERTY_ID`,
`BLOCK_NAME`, `FLAT_NO`, `FLOOR_NUMBER`, `FLAT_SIZE`, `FLAT_AMOUNT`, `BOOKING_DATE`,
`CANCELLATION_REASON`, `CANCELLATION_DATE`, `ISACTIVE`, plus required
`PROPERTY_STATUS_ID` = `const:1` (BOOKED) and `PROPERTY_TYPE_ID` = `const:1` (BUILDER).

**Never touched (office-managed in Access):** all legal/financial fields — `LOAN_*`,
`AGREEMENT_*`, `SALE_DEED_*`, `REGISTRATION_*`, `TDS_*`, `STAMP_DUTY_*`, `POSSESSION_*`.

Initial load on the working copy inserted 13 applications (of 15 units with a booking;
2 excluded as draft/no-customer). The web currently holds cutover **test** bookings;
real bookings will flow the same way.

**To productionise:** move the `customer_id`-present + FK filter into a CRM view or the
engine (currently applied in the load), confirm the production Access path, and decide
whether `PROPERTY_STATUS_ID`/`PROPERTY_TYPE_ID` should be derived per-booking rather
than constant.

## #5 — Applications: project translation + ID-collision floor

> **Superseded by #6 for the ID-collision half.** The project-translation fix (part 1)
> still stands. The explicit-id + AutoNumber-floor scheme (part 2) has been **replaced**
> by a `WEB_SOURCE_ID` match key — Access now owns `PROPERTY_DETAILS_ID` outright, so the
> floor/reseed is no longer used for `tbl_Property_Details`. Kept below for history.

Two bugs found when a real web booking (BRILLIANCE A-103) reached Access:

1. **Wrong project.** The applications mapping wrote `PROPERTY_ID <- property_id`
   untranslated, so web `property_id 26` (Brilliance) landed as Access `PROPERTY_ID 26`
   = **OM SREE GLORY**. Fix: outbound now applies a field `value_map` (added to
   `_pg_rows_for`) as a strict whitelist; `PROPERTY_ID.value_map` carries the crosswalk
   `{20:7, 23:10, 25:13, 26:14, 31:19, 32:20, 33:21, 34:22, 38:26}` (web→Access project
   id). Unmapped projects (e.g. web 39 Madhuban) skip the row rather than write a wrong id.

2. **ID collision.** Web applications insert with `PROPERTY_DETAILS_ID = web unit id`
   (4590–9668); `PROPERTY_DETAILS_ID` is an Access AutoNumber. The old reseed set the
   next id to `MAX+1`, which lands inside the web range — so an office booking could get
   an id a future web unit also owns, and the web sync would overwrite the office row.
   Fix: `reseed_autonumber(..., floor=AUTONUMBER_FLOOR=1_000_000)` → next office id is
   `max(MAX+1, 1_000_000)`, permanently above the web range, and a later web insert can't
   pull it back down. Applies to every AutoNumber table the outbound path inserts into
   (tbl_Property_Details, tbl_Customer). Id bands become disjoint: legacy office 224–357,
   web 4590–9668, new office ≥1,000,000.

Production Access file is now `C:\Users\skyfe\Desktop\zoho test\CRM Backend v6.0.accdb`
(clean: 67 office bookings 224–357, 4589 uncoded PropertyDefaults, 2 customers).

## #6 — Applications: `WEB_SOURCE_ID` match key (retires the AutoNumber floor)

> **Corrected in #9:** this section originally said the booked view's `property_details_id`
> is the booking's own PK. It is **not** — the view aliases the **unit id** to that column.
> The booking's true identity is the **application id** (the view's `customer_id`), so
> `WEB_SOURCE_ID` was later repointed to `customer_id`. Read #9 for the corrected model.

The office added a new column **`WEB_SOURCE_ID`** (int) to `tbl_Property_Details`. This
lets us stop seeding an explicit `PROPERTY_DETAILS_ID` from the web and instead let Access
own that AutoNumber like any office booking — which **dissolves the ID-collision class
entirely** (office and web bookings now draw from the same Access sequence, so they cannot
overlap by construction; no floor, no reseed).

**How it works.** The web view's `property_details_id` is the **booking record's own PK**
(one web booking = one value; a re-booking of a unit is a *new* web record → new id). We
store it in `WEB_SOURCE_ID` and match on it:

- Mapping (outbound `tbl_Property_Details` id=24): `key_column` → **`WEB_SOURCE_ID`**;
  new field `WEB_SOURCE_ID ← property_details_id` role=`key`; the old
  `PROPERTY_DETAILS_ID ← property_details_id` field is **deactivated** so it is never
  written → Access auto-assigns it on INSERT.
- `upsert_rows` matches existing rows by `WEB_SOURCE_ID`: same booking → **UPDATE**;
  unseen booking → **INSERT** (no explicit id). The `reseed_autonumber` call self-disables
  because `WEB_SOURCE_ID` is not a COUNTER column, so no engine code change was needed.

**One-time backfill.** The two web rows already written under the old scheme (5621 A/102,
5622 A/103, both PROPERTY_ID=14 Brilliance) had `WEB_SOURCE_ID = NULL`. Ran (after backup)
`UPDATE tbl_Property_Details SET WEB_SOURCE_ID = PROPERTY_DETAILS_ID WHERE
PROPERTY_DETAILS_ID > 357 AND WEB_SOURCE_ID IS NULL` — 2 rows set, 67 legacy office rows
left NULL. Dry-run after the switch: **0 insert, 0 update, 2 unchanged** (idempotent match,
no duplicates).

**Follow-up done in #7 and #8:** `tbl_Customer` (#7) and `tbl_PropertyDetailsPaymentSchedule`
(#8) have since been given the same treatment, so **no outbound mapping inserts an explicit
id into an AutoNumber anymore** and `AUTONUMBER_FLOOR` is now genuinely unused (kept in code,
inert). All four outbound keys are source-id/natural columns; Access owns every PK.

## #7 — Customers own their key + generic FK resolver (`fk:`)

The office added `CUSTOMER_WEB_ID` (Number) to `tbl_Customer`. Applied the same pattern as
#6 so **Access owns `CUSTOMER_ID`** — office and web customers share one AutoNumber, no
collision, floor retired. The wrinkle vs applications: **applications reference customers**
(`tbl_Property_Details.CUSTOMER_ID`), so once Access assigns its own `CUSTOMER_ID` the child
must store the *Access* id, not the web id. That needs a resolver.

**Generic FK resolver (engine).** A field whose `crm_field` is
`fk:<pg_col>@<access_table>.<match_col>` carries a web id that is resolved to the parent's
Access-assigned key at run time. `_pg_rows_for` SELECTs the inner `<pg_col>` and stashes the
raw value under the field's Access column; `_resolve_fk_columns(db, tm, rows)` then looks it
up in the parent Access table (`match_col == value`) and replaces it with the parent's key
(the field's own Access column). Rows whose parent isn't in Access yet are **dropped as FK
orphans** (never write a dangling FK). Tests: `tests/test_fk_resolver.py`.

**Config applied:**
- `tbl_Customer` (id=6): `key_column` → `CUSTOMER_WEB_ID`; new `CUSTOMER_WEB_ID ←
  customer_id` role=`key`; old `CUSTOMER_ID ← customer_id` deactivated (Access auto-assigns);
  `order` → **55** so customers sync *before* applications.
- `tbl_Property_Details` (id=24): `CUSTOMER_ID ← fk:customer_id@tbl_Customer.CUSTOMER_WEB_ID`.
- **Processing order** is now Defaults(50) → Customer(55) → Applications(60) → Payment(100):
  every parent writes before its child resolves.

**One-time backfill.** `UPDATE tbl_Customer SET CUSTOMER_WEB_ID = CUSTOMER_ID WHERE
CUSTOMER_ID IN (<web ids>) AND CUSTOMER_WEB_ID IS NULL` — 37 web customers set, legacy office
(1, 2) left NULL (backup first). Dry-run: `tbl_Customer` 37 unchanged, `tbl_Property_Details`
2 unchanged (CUSTOMER_ID 166→166, 167→167 resolves) — idempotent, no dups.

**Payment schedule:** resolved separately in **#8** below (was briefly reverted mid-analysis,
then done properly once the business rule was confirmed).

## #8 — Payment schedule: `PAYSCH_WEB_ID` key + FK-resolved, approval-gated

**Business rule (from the office):** a payment schedule loads into Access **only after its
application is approved** and present in `tbl_Property_Details`; it saves as **new records**
carrying the **Access-assigned `PROPERTY_DETAILS_ID`** of that application (not the web id).

The office added `PAYSCH_WEB_ID` (Number) to `tbl_PropertyDetailsPaymentSchedule`. Applied the
same source-id pattern plus the `fk:` resolver:

- Mapping (id=8): `key_column` → `PAYSCH_WEB_ID`; new `PAYSCH_WEB_ID ← id` role=`key`; old
  `ID ← id` deactivated so **Access owns the `ID` AutoNumber**; `PROPERTY_DETAILS_ID ←
  fk:property_details_id@tbl_Property_Details.WEB_SOURCE_ID`.
- Because the parent (applications) syncs first (order 60 < 100), the schedule resolves its
  `PROPERTY_DETAILS_ID` against the *approved* Access application. A schedule whose application
  isn't approved/loaded yet is **dropped** (FK orphan) — this is exactly the approval gate.

**Data facts that shaped it.** All 101 web schedules (web `id` 245–428) already existed in
Access as exact copies (Access `ID` == web id, same content), and they reference bookings
121–167 that are **not** approved applications — i.e. pre-existing orphans from the old
explicit-`ID` scheme. The other 493 Access rows are office-owned. **Backfill:** tagged those
101 rows `PAYSCH_WEB_ID = ID` (backup first) so they UPDATE rather than duplicate once their
applications are approved; the 493 office rows stay NULL and are never matched.

**Dry-run:** payment 101 skipped (unresolved FK — bookings not approved), 0 writes — the gate
works. Positive path proven directly: a schedule for approved app 5621 resolves to Access
`PROPERTY_DETAILS_ID` 5621; one for unapproved booking 150 is dropped. If a future app gets an
Access id ≠ its web id, the schedule resolves to whatever Access assigned.

**Result:** with #8 done, **no outbound mapping inserts an explicit id into an AutoNumber** —
the whole id-collision class is gone by construction across all four tables, and the
AutoNumber floor is retired. Open item retired.

**Pre-existing orphans not cleaned:** the 101 old orphan rows (Access `ID` 245–428 pointing at
unapproved bookings 121–167) are left in place, just tagged. They self-correct if those
bookings are ever approved; can be cleaned separately if desired.

## #9 — Corrected id model: the booking key is the APPLICATION id, not the unit id

Milestones weren't saving. Root cause was a mislabel in the web views (confirmed via
`pg_get_viewdef`):

- `tbl_Property_Details.property_details_id = u.id` — the **UNIT** id (`bookings_unitmaster`),
  e.g. 5549.
- `tbl_Property_Details.customer_id = ba.id` — the **BOOKING APPLICATION** id
  (`bookings_bookingapplication`), e.g. 168. (`tbl_Customer` is keyed by this same
  application id — it's one row per booking, not per person.)
- `tbl_PropertyDetailsPaymentSchedule.property_details_id = bookings_paymentmilestone.application_id`
  — the **application id** (168).

So milestones key off the **application id**, but `WEB_SOURCE_ID` had been set to the **unit
id** (#6) — they could never match, and it was also a latent bug (a unit can be re-booked, so
the unit id isn't a per-booking identity). **Fix:** `WEB_SOURCE_ID ← customer_id` (the
application id). Existing rows repointed (unit→app): 5621→167, 5622→166, 5623→168 (backup
first). The payment resolver already targeted `WEB_SOURCE_ID`, so no change there — milestones
now resolve `application_id → WEB_SOURCE_ID → Access PROPERTY_DETAILS_ID`.

**Engine change (TDD):** `_pg_rows_for` now lets **one web column feed several Access columns**
(the application id lands in both `WEB_SOURCE_ID` and `CUSTOMER_ID`) — its target list replaced
the old crm_field-keyed dict that silently dropped the duplicate. Test:
`tests/test_fk_resolver.py::test_two_access_columns_from_one_pg_column` (65 tests pass).

**Live result:** app 5623 (Test 001) now has its **12 milestones** saved with
`PROPERTY_DETAILS_ID = 5623`; apps 5621/5622 got their 4 each (8 updated, 12 inserted, 93
unapproved dropped); applications 3 unchanged (no dup). Re-bookings are now correct too — a new
application on the same unit is a new record because the key is the application id.

## Engine capabilities added this pass

- **`const:` fields** — a `FieldMapping.crm_field` of `const:<value>` injects a literal
  into every row (for Access-required columns with no CRM source).
- **Blank-key guard** — `run_web_to_access` skips any incoming row whose key column is
  null/blank, so an uncoded web record can never create a junk row in Access.
- **`fk:` fields** — `fk:<pg_col>@<access_table>.<match_col>` resolves a web id to the
  Access-assigned parent id at run time (`_resolve_fk_columns`); FK orphans are dropped.
- **`scale:` fields** — `scale:<pg_col>:<factor>` multiplies a web value by a constant.
  Used to convert a web percentage (4.1) to the office 0–1 fraction (0.041) so Access's
  Percent format renders 4.1%, not 410%.
- **`cumsum:` fields** — `cumsum:<value_col>:<partition_col>:<order_col>` fills a running
  total per group (`_apply_cumulative`), computed from sibling Access columns after FK
  resolution. Used for `CUMULATIVE_PCT` (running `PERCENTAGE` per booking, ordered by
  `MILESTONE_NO`) since the web view hard-codes `cumulative_pct` to NULL.
- **Multi-target columns** — one web column may feed several Access columns (the booking
  application id lands in both `WEB_SOURCE_ID` and `CUSTOMER_ID`).

## #3 — Inbound booking status → web inventory (natural-key resolver)

Rule (supersedes the earlier "only cancellations flow inbound"): an office booking in
Access sets the web unit's inventory status — **`BOOKED (1) → booked`**,
**`CANCELLED (3) → available`**. Other Access statuses (HANDED OVER, REGISTERED, NULL)
are ignored via the `value_map` whitelist.

Problem: a booking born in Access gets an Access-native `PROPERTY_DETAILS_ID`
(AutoNumber, 224–385) that is **not** a web unit id (4590–9668), and web `code` is blank,
so id/code matching finds nothing. Solution — a **natural-key resolver**
(`resolve_booking_status`) that matches on **project + block + flat**:

- `role="match"` fields carry the match columns; `PROPERTY_ID.value_map` holds the
  **project crosswalk** (Access `PROPERTY_ID` → web `property_id`):
  `{7:20, 10:23, 13:25, 14:26, 19:31, 20:32, 21:33}` (confirmed by name; HEIGHTS→HEIGHTS EE;
  MALABAR GREENS unmapped/skipped).
- Match uses the `sync.unit` view (`property_id, block_name, flat_no → unit_id`);
  `unit_id == bookings_unitmaster.id` (verified), written UPDATE-only by id.
- **`ISACTIVE=False` rows are ignored** (superseded bookings; else a dead `booked` record
  would wrongly mark a unit booked). A unit can have **many applications over time**
  (booked → cancelled → re-booked by someone else). Conflicting active records → the one
  with the newest **effective time wins**, where effective time = `MODIFIED_DATE` if the
  row was ever modified, else `CREATED_DATE`. (Ranking modified above created is a bug: a
  fresh re-booking has `modified=NULL` but a recent `created` and must beat an older row
  that was `modified`-at-cancel — real case A-406: rebooking created 20:53 beats cancel
  modified 20:34.) An exact-effective-time tie is reported and **skipped**, never guessed.
- Non-unique / unmapped rows (no_project, no_unit, ambiguous_unit, conflict_tie) are
  **reported in the run detail, not written**.

First dry-run over live data: 58 units resolved → 31 web status changes; 10 inactive,
4 lifecycle, 1 no-project, 3 no-unit, 3 ambiguous, 1 tie all correctly set aside.

## #4 — web → access units (natural-key UPDATE, tbl_PropertyDefaults)

The client Access file (`CRM Backend v6.0.accdb`) has 4589 `tbl_PropertyDefaults` rows
but **zero `UNIT_CODE`s**, while the web keys unit sync on `UNIT_CODE`. A code-keyed diff
therefore saw all 4560 web units as new and would **INSERT 4560 duplicate rows**. Fix:
same natural-key approach as inbound, reversed — match web unit → existing Access row by
**project + block + flat**, then **UPDATE-only** (fill `UNIT_CODE` + pricing), never insert.

- Access `tbl_PropertyDefaults.Project` stores the **Access PROPERTY_ID as a string**
  (`'7'`,`'10'`…), so the crosswalk is the inbound id-map **reversed** plus two
  name-verified adds: web→access `{20:7, 23:10, 25:13, 26:14, 31:19, 32:20, 33:21,
  34:22, 38:26}` (DELIGHT 34→22, GLORY 38→26). Stored in `Project.value_map`.
- Roles on the mapping: `Project`/`Block`/`Flat_No` = `match`; `UNIT_CODE` flipped from
  `key` to `sync` (it is now *written*, not matched on); write key is the Access `id`.
- New engine bits: `resolve_defaults_updates()`, `_web_to_access_natural_key()`, and
  `AccessDatabase.update_rows_where()` (composite-key UPDATE, never inserts).
- Result: **4616 UPDATEs, 0 inserts**; 460 skipped (web project 39 MADHUBAN has no
  Access rows), 2 no-row, 1 ambiguous — reported, not written. 4558 codes filled,
  **0 duplicate UNIT_CODEs**.

> **CRITICAL bug found on the first live write (rolled back, then fixed):**
> `tbl_PropertyDefaults.id` is **NOT unique** — 4589 rows share only 3948 id values. The
> first resolver keyed UPDATEs on `id`, so `UPDATE ... WHERE id=X` hit multiple/wrong
> rows and smeared 641 codes onto wrong units (e.g. Gallaxy `OGL-H-105` landed on
> Brilliance A-204). Rolled back from the auto-backup, then rewrote to UPDATE by the
> **composite key (Project+Block+Flat_No, which IS unique)** using the matched Access
> row's own values in the WHERE, with change-detection in the resolver for idempotency.
> Lesson: never assume an Access column named `id` is a unique key — verify DISTINCT
> count before using it as a write key.
- **Removed a landmine:** a `PROPERTY_TYPE ← property_type` field had been added to this
  mapping. Web `property_type` is text (`omsree`/`landlord`) but Access `PROPERTY_TYPE`
  is a numeric FK (1–5) — a live write would fail. Deactivated it; ownership stays
  Access-authoritative (see #3 in mapping-review and the lookup-tables note).
