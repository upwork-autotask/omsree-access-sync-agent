# Sync Contract (OmSree real schema — DRAFT v0.1)

Filled against the real Access schema dumped from **`db tables.accdb`** (the clean
108-table set). `CRM Backend v6.0.accdb` (156 tables) is the same database plus
working/`_test`/`_backup`/`Copy Of` tables — sync ignores those. The two agree on
every core table below except two trivial column deltas (see *Version notes*).

> **Status: DRAFT.** This is reverse-engineered from the schema, not yet signed off
> by the client. Per the plan (Phase 0) nothing writes until this is agreed. Open
> items are flagged with ⚠.

Conventions used here:
- **Direction** is push (web→Access) for everything in v1 — the web app is the source of truth.
- **Conflict rule** is web-wins on every push table.
- **Cursor column** is the column the outbound API orders/filters by (`?since=`); `MODIFIED_DATE` where present.
- "Sync?" = `key` (unique key) / `yes` (synced) / `no` (never leaves web / Access-local).

---

## Master / reference tables (web → Access, full refresh)

These are small and change rarely; the agent can refresh them whole.

### tbl_Property  ⇄  project / property master
- Direction: push (web→Access) · Key: `PROPERTY_ID` · Conflict: web-wins · Cursor: `MODIFIED_DATE`
- Columns:
  | Access column | Type | Sync? | Notes |
  |---|---|---|---|
  | PROPERTY_ID | int | key | |
  | PROPERTY_NAME | text | yes | |
  | PROPERTY_CODE / CODE | text | yes | project code |
  | LOCATION / STATE | text | yes | |
  | EAST_FACING_CHARGE_PER_SFT | int | yes | present in db tables only (see version notes) |
  | PLC_CHARGES_PER_SFT | int | yes | present in db tables only |
  | OCCUPANCY_CERTIFICATE_DATE | date | yes | |
  | BANK_NAME / IFSC_CODE / ACCOUNT_NUMBER / BRANCH / VIRTUAL_ACCOUNT_PREFIX | text | ⚠ no | bank/virtual-account config — keep web-side unless client wants it on the office machine |
  | ISACTIVE | bool | yes | |
  | CREATED_*/MODIFIED_* | — | no | audit, Access-local |

### tbl_Block  ⇄  block master
- Key: `ID` · web-wins · Cursor: `MODIFIED_DATE`
- Sync: `ID`(key), `BLOCK_NAME`, `IS_ACTIVE`. Audit columns: no.

### tbl_PropertyBlock  ⇄  project↔block mapping
- Key: `ID` · web-wins · Cursor: `MODIFIED_DATE`
- Sync: `ID`(key), `PROPERTY_ID`, `BLOCK_ID`, `IS_ACTIVE`. Audit columns: no.

### tbl_Property_Status  ⇄  unit status master
- Key: `PROPERTY_STATUS_ID` · web-wins · full refresh (4 rows)
- Sync: `PROPERTY_STATUS_ID`(key), `PROPERTY_STATUS`, `ISACTIVE`, `SEQ`.

### Other small masters (full refresh, key + label + active flag)
`tbl_Property_Type`, `tbl_Parking_Type`, `tbl_MilestoneStatus`, `tbl_DocumentType`,
`tbl_Discount_Type`, `tbl_Booking_Type_ID`, `tbl_Marketing_Scheme_ID`,
`tbl_Registration_Type_ID`, `tbl_Registration_Doc_Status_ID`, `tbl_TDS_Status_ID`,
`tbl_TDS_Payment_Type_ID`, `tbl_Stamp_Duty_Status_ID`, `tbl_Stamp_Duty_Paidby_ID`,
`tbl_Salutation`, `tbl_Occupation`, `tbl_Bank`. — all push, web-wins, ⚠ confirm which the office DB actually needs.

---

## Transactional tables (web → Access, changed-rows only)

### tbl_Property_Details  ⇄  unit / booking record  ★ central table
The heart of the sync — one row per unit, carrying booking, status, pricing, the
linked customer, and the registration/CPP lifecycle.
- Direction: push (web→Access) · Key: `PROPERTY_DETAILS_ID` · Conflict: web-wins · Cursor: `MODIFIED_DATE`
- Columns:
  | Access column | Type | Sync? | Notes |
  |---|---|---|---|
  | PROPERTY_DETAILS_ID | int | key | |
  | PROPERTY_ID | int | yes | → tbl_Property |
  | CUSTOMER_ID | int | yes | → tbl_Customer |
  | BLOCK_NAME / FLAT_NO / FLOOR_NUMBER | text | yes | unit identity |
  | FLAT_SIZE | float | yes | |
  | PROPERTY_TYPE_ID / PROPERTY_STATUS_ID | int | yes | status/type |
  | STATUS_CHANGE | date | yes | |
  | BOOKING_DATE / BOOKING_TYPE_ID | date/int | yes | |
  | BASE_PRICE, FLAT_AMOUNT | num | yes | base pricing |
  | FLOOR_RISE_CHARGES, EAST_FACING_CHARGES, PREFERRED_LOCATION_CHARGES, CAR_PARKING_CHARGES, CLUB_HOUSE_CHARGES, INFRASTRUCTURE_CHARGES, SHIFTING_CHARGES | num | yes | charge breakup |
  | DISCOUNT_TYPE, DISCOUNT_AMOUNT | int | yes | present in db tables only (version notes) |
  | NEXT_AMOUNT / NEXT_DUE_DATE | num/date | yes | |
  | PARKING_SLOT / PARKING_TYPE_ID | text/int | yes | |
  | SALES_PERSON_ID / LANDLORD_ID | int | yes | |
  | AGREEMENT_FOR_SALE_* / CONSTRUCTION_AGREEMENT_* / SALE_DEED_* | date/num | yes | eSign/agreement status + value (status, not the file) |
  | TDS_* / STAMP_DUTY_* / REGISTRATION_* | mixed | yes | registration lifecycle status + dates |
  | INTERIOR_HANDOVER_DATE / POSSESSION_HANDOVER_DATE | date | yes | |
  | CANCELLATION_* | mixed | yes | cancellation status |
  | IS_* request flags (CANCELLATION/NAME_REVISION/SHIFTING/TRANSFER) | bool | yes | workflow state |
  | CPP_REMARKS / POSSESSION_REMARK / COMMENTS | memo | ⚠ yes? | free text — confirm it may leave web |
  | VIRTUAL_ACCOUNT_NUMBER | text | ⚠ no | financial identifier — confirm |
  | REFERRER_NAME / REFERRER_EMAIL / REFERRER_PHONE_NUMBER | text/num | ⚠ no | third-party PII — confirm |
  | LOAN_AMOUNT / LOAN_BANK_ID / LOAN_BANKER_NAME / LOAN_BANKER_PHONE_NUMBER | mixed | ⚠ no | loan/banker detail — confirm |
  | CREATED_*/MODIFIED_* | — | no | audit, Access-local |

### tbl_Customer  ⇄  customer (NON-confidential fields only)
- Key: `CUSTOMER_ID` · web-wins · Cursor: `MODIFIED_DATE`
- Plan rule: customer basic info only; **KYC identifiers never leave the web app.**
  | Access column | Sync? | Notes |
  |---|---|---|
  | CUSTOMER_ID | key | |
  | SALUTATION_* / CUSTOMER_NAME_1ST..4TH | yes | names |
  | RELATED_PERSON_* / RELATION_OF_RP_* | yes | |
  | PRIMARY_CONTACT_NO / SECONDARY_CONTACT_NO | ⚠ yes? | phone — confirm |
  | EMAIL_ADDRESS* | ⚠ yes? | email — confirm |
  | ADDRESS* | ⚠ yes? | confirm |
  | **AADHAR_*** | **no** | KYC — never leaves web |
  | **PAN_NO_*** | **no** | KYC — never leaves web |
  | **PASSPORT_*** | **no** | KYC — never leaves web |
  | ADDRESS_PROOF* / ADDRESS_PROOF_ID* | no | KYC doc refs — never leaves web |
  | DOB_* / AGE_* / OCCUPATION_ID_* | ⚠ confirm | |
  | ISACTIVE | yes | |

### tbl_PropertyDetailsPaymentSchedule  ⇄  payment schedule / milestones
- Key: `ID` · web-wins · Cursor: `MODIFIED_DATE`
- Sync: `ID`(key), `PROPERTY_DETAILS_ID`, `DESCRIPTION`, `PERCENTAGE`, `CUMULATIVE_PCT`,
  `SCHEDULE_DATE`, `DUE_DATE`, `AMOUNT`, `AMOUNT_RCVD`, `MILESTONE_STATUS_ID`,
  `MILESTONE_NO`, `IS_ACTIVE`. Audit columns: no.

### tbl_Other_Charges_Receipts  ⇄  payment receipts
- Key: `RECEIPT_ID` · web-wins · Cursor: `MODIFIED_DATE`
- Sync: `RECEIPT_ID`(key), `PROPERTY_DETAILS_ID`, `OTHER_CHARGES_ID`, `RECEIPT_NO`,
  `TRANSACTION_DATE`, `TRANSACTION_MODE`, `TRANSACTION_TYPE`, `BASE_AMOUNT`,
  `GST_AMOUNT`, `TOTAL_AMOUNT`, `SOURCE`, `REMARKS`.
  ⚠ `VOUCHER_REF` / `TRANSACTION_NO` / `BANK_NAME` — confirm whether bank refs may leave web.

### tbl_DocumentRecord  ⇄  generated documents (CPP / eSign references)
- Key: `ID` · web-wins · Cursor: `MODIFIED_DATE`
- Sync: `ID`(key), `PROPERTY_DETAILS_ID`, `DOCUMENT_TYPE_ID`, `GENRATION_DATE`, `COMMENTS`.
  `PATH` = **no** (server-side file path/reference, not the file; do not push the document itself).

---

## Out of scope (never synced)
Working/staging/junk tables: `Table1..4`, `Copy Of *`, `Paste Errors`, `HARD CODED`,
anything ending `_test` / `_backup` / `_Transfers` (transfer audit), `tbl_temp*`,
date-suffixed snapshots (`*_06142024`, `*_06152024`). Employee/role/access tables
(`tbl_Employee*`, `tbl_Report_Access`) stay web-side.

## Version notes (CRM Backend v6.0 vs db tables)
- `tbl_Property`: `db tables` adds `EAST_FACING_CHARGE_PER_SFT`, `PLC_CHARGES_PER_SFT`.
- `tbl_Property_Details`: `db tables` has `DISCOUNT_TYPE`, `DISCOUNT_AMOUNT`; `CRM Backend v6.0` has `PROJECT_LAYOUT_IMAGE` instead.
- All other core tables (Block, PropertyBlock, Property_Status, Customer, PaymentSchedule, Receipts, DocumentRecord) are column-identical across both files.
- The agent only writes whitelisted columns, so a column missing on one side is simply skipped — the contract is safe against either file.

## Open questions for the client (⚠ above)
1. Which customer contact/address fields (phone, email, address, DOB, occupation) may leave the web app onto the office machine? KYC (Aadhaar/PAN/Passport) is assumed **no**.
2. May financial identifiers (virtual account number, bank refs, voucher/transaction numbers, loan/banker details) be pushed to Access, or kept web-only?
3. Are free-text memos (CPP_REMARKS, POSSESSION_REMARK) allowed on the office copy?
4. Which reference masters does the office Access DB actually consume? (full list above is a superset).
5. Sync frequency — every N minutes vs manual.
