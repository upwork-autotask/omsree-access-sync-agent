# Web view spec — `tbl_Main_Transaction` (for the Access sync)

**For:** OmSree CRM web/DB team
**Purpose:** expose payment **transaction / receipt** records to the Access sync agent, so
they flow web → Access `tbl_Main_Transaction`. This mirrors the view you already built for
payment schedules (`tbl_PropertyDetailsPaymentSchedule` = `bookings_paymentmilestone`).

The sync reads **only** from `public.tbl_*` views — it has no access to the base
`bookings_*` tables. So a transaction can't sync until this view exists.

---

## Create a view: `public.tbl_Main_Transaction`

Same convention as the existing sync views: a flat `SELECT` over the transaction base
table, aliasing columns to the names below. Grant `SELECT` to `sync_agent`.

### Output columns (name, type, meaning)

| View column | Type | Must contain |
|---|---|---|
| `id` | bigint | **the transaction record's own PK** (its unique web id) |
| `property_details_id` | bigint | **the BOOKING APPLICATION id** — see the critical note below |
| `receipt_no` | text | receipt number (e.g. `OMB/R/20241008/N/00036`) |
| `transaction_date` | timestamptz / date | transaction date |
| `payment_type_id` | int | payment type (lookup — see "Lookup ids") |
| `transaction_mode_id` | int | mode: cash/cheque/online (lookup) |
| `transaction_no` | text | instrument / reference no |
| `bank_id` | int | bank (lookup) |
| `transaction_type_id` | int | transaction type (lookup) |
| `transaction_source_id` | int | source (lookup) |
| `amount` | numeric | base amount |
| `gst` | numeric | GST amount |
| `service_tax` | numeric | service tax amount |
| `total` | numeric | total (amount + taxes) |
| `record_type_id` | int | record type (lookup) |
| `cgst` | numeric | CGST amount |
| `sgst` | numeric | SGST amount |
| `remarks` | text | remarks / notes |
| `isactive` | boolean | active flag (cancelled/void = false) |
| `tds_type` | int | TDS type (lookup; NULL if n/a) |
| `updated_at` | timestamptz | last-modified time (for change detection) |

Fields the office manages in Access and the web should **NOT** send (we ignore them):
`created_by, modified_by, created_date, modified_date, edit_history,
advance_receipt_path, advance_receipt_path2, cheque_bounce*, bounce_cheque_id,
is_omb_assisted_tds, is_tds_credit, routed_transaction`.

Milestone linkage (`milestone`, `milestone_ids`, `is_milestone_payment`) — **phase 2**;
leave out for now, we'll wire it once the base transactions sync cleanly.

---

## ⚠️ CRITICAL: what `property_details_id` must be

It must be the **booking application id** — i.e. the same value you already expose as:
- `customer_id` in `public.tbl_Property_Details` (that view uses `ba.id`, the
  `bookings_bookingapplication` id), and
- `application_id` (aliased `property_details_id`) in
  `public.tbl_PropertyDetailsPaymentSchedule`.

**It must NOT be the unit id** (`bookings_unitmaster.id`). The sync links a transaction to its
Access application by this id; if it's the unit id, every transaction will be dropped as an
unresolved foreign key. Whatever FK your transaction table has to the booking application, put
**that** id in `property_details_id`.

---

## Template (adapt table/column names to your schema)

```sql
CREATE OR REPLACE VIEW public.tbl_Main_Transaction AS
SELECT
    t.id                       AS id,                    -- transaction PK
    t.application_id           AS property_details_id,   -- BOOKING APPLICATION id (not unit id)
    t.receipt_no               AS receipt_no,
    t.transaction_date         AS transaction_date,
    t.payment_type_id          AS payment_type_id,
    t.transaction_mode_id      AS transaction_mode_id,
    t.transaction_no           AS transaction_no,
    t.bank_id                  AS bank_id,
    t.transaction_type_id      AS transaction_type_id,
    t.transaction_source_id    AS transaction_source_id,
    t.amount                   AS amount,
    t.gst                      AS gst,
    t.service_tax              AS service_tax,
    t.total                    AS total,
    t.record_type_id           AS record_type_id,
    t.cgst                     AS cgst,
    t.sgst                     AS sgst,
    t.remarks                  AS remarks,
    t.is_active                AS isactive,
    t.tds_type                 AS tds_type,
    t.updated_at               AS updated_at
FROM bookings_paymenttransaction t;   -- replace with the real base table

GRANT SELECT ON public.tbl_Main_Transaction TO sync_agent;
```

Return **all** transactions — the sync itself only writes those whose application is already
loaded in Access (others are held back automatically), so no status filter is needed here.

---

## Lookup ids — one thing to confirm

`payment_type_id`, `transaction_mode_id`, `bank_id`, `transaction_type_id`,
`transaction_source_id`, `record_type_id`, `tds_type` are foreign keys to lookup tables on
**both** sides. For each, please send us a small mapping of **web id → label** (e.g.
`transaction_mode_id: 1=Cash, 3=Online, …`). We'll crosswalk them to the Access lookup ids on
our side; the ids don't need to match, we just need to know what each web id means.

---

## How to verify (before handing back)

```sql
-- 1. shape looks right
SELECT * FROM public.tbl_Main_Transaction LIMIT 5;

-- 2. property_details_id matches the application id used elsewhere (should return rows)
SELECT COUNT(*) FROM public.tbl_Main_Transaction tx
JOIN public.tbl_PropertyDetailsPaymentSchedule ps
  ON ps.property_details_id = tx.property_details_id;
```

Once the view exists and `sync_agent` can `SELECT` it, tell us — we'll build + dry-run the
Access mapping and confirm transactions land under the correct application.
