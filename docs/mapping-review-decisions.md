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

## #2 — Applications → `tbl_Property_Details` (HELD)

Access is the system of record for bookings (all history + legal/financial fields:
loan, agreement, sale deed, registration, TDS, stamp duty, possession). The web is
newly adopted and has only ~15–37 bookings so far. The ~80 legal columns have **no
CRM source**.

Therefore a web→access applications sync must be **strictly additive**: insert new web
bookings, fill only CRM-backed columns (customer, booking date, status, cancellation),
and **never** overwrite Access history. web-wins is unsafe for this table.

Blocked on: (1) the **production** Access DB path (current file is a test copy with an
empty `tbl_Property_Details`); (2) a **booking-identity rule** so web bookings don't
duplicate Access records.

## Engine capabilities added this pass

- **`const:` fields** — a `FieldMapping.crm_field` of `const:<value>` injects a literal
  into every row (for Access-required columns with no CRM source).
- **Blank-key guard** — `run_web_to_access` skips any incoming row whose key column is
  null/blank, so an uncoded web record can never create a junk row in Access.
