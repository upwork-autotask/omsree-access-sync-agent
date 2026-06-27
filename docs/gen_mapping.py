"""Generate the web->Access field mapping CSV from the real schema dump.

Decision column: key | yes | no | review
  key    - unique key for the table
  yes    - pushed web->Access
  no     - never leaves the web app (KYC/audit/file refs)
  review - client must confirm before it leaves the web app (PII / financial)
"""
import csv, json, os

SP = os.path.dirname(os.path.abspath(__file__))
data = json.load(open(os.path.join(SP, "db_tables.json"), encoding="utf-8-sig"))

# table -> (web/CRM entity, key column)
TABLES = [
    ("tbl_Property", "property / project master", "PROPERTY_ID"),
    ("tbl_Block", "block master", "ID"),
    ("tbl_PropertyBlock", "project<->block map", "ID"),
    ("tbl_Property_Status", "unit status master", "PROPERTY_STATUS_ID"),
    ("tbl_Property_Type", "unit type master", "PROPERTY_TYPE_ID"),
    ("tbl_Customer", "customer", "CUSTOMER_ID"),
    ("tbl_Property_Details", "unit / booking record", "PROPERTY_DETAILS_ID"),
    ("tbl_PropertyDetailsPaymentSchedule", "payment schedule / milestones", "ID"),
    ("tbl_Other_Charges_Receipts", "payment receipts", "RECEIPT_ID"),
    ("tbl_DocumentRecord", "generated docs (CPP/eSign ref)", "ID"),
]

AUDIT = {"CREATED_BY", "CREATED_DATE", "MODIFIED_BY", "MODIFIED_DATE", "GENERATED_BY", "INSERT_ID"}

# columns that must never leave the web app
NEVER = {
    "tbl_Customer": lambda c: c.startswith(("AADHAR", "PAN_NO", "PASSPORT", "ADDRESS_PROOF")),
    "tbl_DocumentRecord": lambda c: c == "PATH",
}
# columns needing client sign-off before leaving web
REVIEW = {
    "tbl_Property": {"BANK_NAME", "IFSC_CODE", "ACCOUNT_NUMBER", "BRANCH", "VIRTUAL_ACCOUNT_PREFIX"},
    "tbl_Customer": set()  # handled by prefix rule below
        | {f"PRIMARY_CONTACT_NO", "SECONDARY_CONTACT_NO"},
    "tbl_Property_Details": {
        "VIRTUAL_ACCOUNT_NUMBER", "LOAN_AMOUNT", "LOAN_BANK_ID", "LOAN_BANKER_NAME",
        "LOAN_BANKER_PHONE_NUMBER", "REFERRER_NAME", "REFERRER_EMAIL",
        "REFERRER_PHONE_NUMBER", "CPP_REMARKS", "POSSESSION_REMARK",
    },
    "tbl_Other_Charges_Receipts": {"VOUCHER_REF", "TRANSACTION_NO", "BANK_NAME"},
}
CUST_REVIEW_PREFIX = ("EMAIL_ADDRESS", "ADDRESS", "DOB", "AGE", "OCCUPATION")


def to_crm_field(col):
    return col.lower()


def cursor_for(cols):
    names = {c["name"] for c in cols}
    for cand in ("MODIFIED_DATE", "CREATED_DATE", "TRANSACTION_DATE"):
        if cand in names:
            return cand
    return ""


def decide(table, col):
    name = col["name"]
    if name == KEY:
        return "key", "unique key"
    if name in AUDIT:
        return "no", "audit column, Access-local"
    if table in NEVER and NEVER[table](name):
        return "no", "KYC / file reference - never leaves web"
    rv = REVIEW.get(table, set())
    if name in rv:
        return "review", "PII/financial - confirm with client"
    if table == "tbl_Customer" and name.startswith(CUST_REVIEW_PREFIX):
        return "review", "customer PII - confirm with client"
    return "yes", ""


rows = []
for table, entity, KEY in TABLES:
    info = data.get(table)
    if not info:
        continue
    cols = info["columns"]
    cursor = cursor_for(cols)
    for col in cols:
        if "name" not in col:
            continue
        decision, note = decide(table, col)
        rows.append({
            "access_table": table,
            "web_entity": entity,
            "access_column": col["name"],
            "crm_field": to_crm_field(col["name"]),
            "type": col["type"],
            "direction": "web->access",
            "decision": decision,
            "conflict_rule": "web-wins",
            "cursor_column": cursor,
            "notes": note,
        })

out = os.path.join(SP, "sync-field-mapping.csv")
fields = ["access_table", "web_entity", "access_column", "crm_field", "type",
          "direction", "decision", "conflict_rule", "cursor_column", "notes"]
with open(out, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)

# summary
from collections import Counter
print("rows:", len(rows))
print("by table:")
per = Counter(r["access_table"] for r in rows)
for t, n in per.items():
    print(f"  {t}: {n}")
print("by decision:", dict(Counter(r["decision"] for r in rows)))
print("written:", out)
