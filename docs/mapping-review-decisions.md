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

## Engine capabilities added this pass

- **`const:` fields** — a `FieldMapping.crm_field` of `const:<value>` injects a literal
  into every row (for Access-required columns with no CRM source).
- **Blank-key guard** — `run_web_to_access` skips any incoming row whose key column is
  null/blank, so an uncoded web record can never create a junk row in Access.
