# Spec: Booking Applications sync (web → Access `tbl_Property_Details`)

Prerequisites the **web/CRM team** must provide before the applications sync
(`#2`) can be built. Until these exist, `tbl_Property_Details` stays empty by
design — see [mapping-review-decisions.md](mapping-review-decisions.md).

## Why this is blocked today

1. **The web only has cutover test data.** The ~15 current booking records are
   test entries (`TEST Signer`, `Test 002`, `Test CEO Review …`), mostly status
   `draft`. Syncing them would inject test rows into the real Access booking table.
2. **The existing view is mislabeled.** In `public.tbl_Property_Details`, the field
   called `customer_id` is actually the **booking id**
   (`bookings_bookingapplication.id`), not a customer id. It cannot feed Access
   `CUSTOMER_ID` correctly.

## 1. A dedicated applications view

Provide a new view, e.g. `public.tbl_Booking_Application`, with **one row per real
booking application** and **correctly named** columns. Required outputs:

| Column (name it exactly) | Meaning | Maps to Access `tbl_Property_Details` |
|---|---|---|
| `property_details_id` | the unit id the booking is for | `PROPERTY_DETAILS_ID` (key) |
| `booking_number` | e.g. `OS-00001` | (identity / reference) |
| `customer_id` | **real** customer id (FK to the customer synced into Access `tbl_Customer`) | `CUSTOMER_ID` |
| `customer_name` | primary applicant | (verification only) |
| `booking_date` | submission/approval date | `BOOKING_DATE` |
| `property_status_id` | Access status id (`1`=BOOKED, etc.) or the status text to map | `PROPERTY_STATUS_ID` |
| `property_type_id` | ownership id, or leave to Access (Access is authoritative) | `PROPERTY_TYPE_ID` |
| `cancellation_reason`, `cancellation_date` | if cancelled | `CANCELLATION_REASON`, `CANCELLATION_DATE` |
| `isactive` | active flag | `ISACTIVE` |
| `updated_at` | change cursor | (incremental sync) |

Grant `SELECT` on it to `sync_agent`.

## 2. Filter to real, confirmed bookings only

The view must **exclude drafts and test data** — e.g. `WHERE status = 'approved'`
(and however you flag test records). Draft/`ceo_review`/`director_review` rows must
not appear.

## 3. Booking-identity rule (avoid duplicates)

Access `PROPERTY_DETAILS_ID` is an AutoNumber. Decide one:
- **(a)** The web unit id **is** the `PROPERTY_DETAILS_ID` (explicit-id insert +
  AutoNumber reseed — already supported by the engine), or
- **(b)** Match on `booking_number` and let Access assign its own id.

(a) is simplest if unit id ↔ property-details identity is 1:1.

## 4. What the sync will and will not touch

- **Will fill (additive):** the columns above — customer, booking date, status,
  cancellation.
- **Will never touch (office-managed in Access):** all legal/financial fields —
  `LOAN_*`, `AGREEMENT_*`, `SALE_DEED_*`, `REGISTRATION_*`, `TDS_*`, `STAMP_DUTY_*`,
  `POSSESSION_*`, etc. The web has no source for these; web-wins must not blank them.

## 5. Confirm the production Access DB

Confirm the live Access file path (the sync is currently pointed at a working copy).
The additive/fill-only rules above are essential once pointed at the DB that holds
real booking history.

## When ready

With the view + filter + identity rule in place and real bookings present, the
agent side is small: one `TableMapping` (additive/fill-only) + a dry-run to review
the diff before the first write — same process used for `tbl_PropertyDefaults`.
