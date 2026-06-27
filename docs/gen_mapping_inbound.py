"""Generate the Access->web (inbound, Phase 4) field mapping CSV.

Per docs/implementation-plan.md the web app is the source of truth, so inbound is
OPTIONAL and opt-in: by default nothing flows Access->web. This file lists the same
tables/fields as the outbound map for symmetry, defaulting every field to
'inbound-disabled', and flags a curated set of plausible Access-origin CANDIDATES
(events a back-office/site team may still record locally in Access) for the client
to confirm. Conflict rule for any agreed Access-origin row is newest-wins.

Decision column: key | candidate | no
  key       - unique key for the table
  candidate - plausible Access-origin field; push Access->web ONLY if client agrees
  no        - web-origin; inbound disabled (web always wins)
"""
import csv, json, os
from collections import Counter

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
    # Access-origin candidate: construction progress is often updated at the site/office.
    ("tbl_PropertyMilestones", "construction milestone (site-updated)", "ID"),
]

AUDIT = {"CREATED_BY", "CREATED_DATE", "MODIFIED_BY", "MODIFIED_DATE", "GENERATED_BY", "INSERT_ID"}

# Fields that plausibly ORIGINATE in Access (back-office/site events) -> Access->web candidates.
CANDIDATES = {
    "tbl_Property_Details": {
        "INTERIOR_HANDOVER_DATE", "POSSESSION_HANDOVER_DATE", "POSSESSION_REMARK",
        "REGISTRATION_DATE", "REGISTRATION_DOCUMENT_NUMBER", "REGISTRATION_DOCUMENT_STATUS",
        "REGISTRATION_REMARKS", "SALE_DEED_DATE",
    },
    "tbl_PropertyMilestones": {"Project", "SUB_PROJECT", "MILESTONE", "PLAN_DATE", "ACTUAL_DATE"},
}


def to_crm_field(col):
    return col.lower()


def cursor_for(cols):
    names = {c["name"] for c in cols}
    for cand in ("MODIFIED_DATE", "CREATED_DATE", "TRANSACTION_DATE"):
        if cand in names:
            return cand
    return ""


def decide(table, name):
    if name == KEY:
        return "key", "unique key"
    if name in AUDIT:
        return "no", "audit column, Access-local"
    if name in CANDIDATES.get(table, set()):
        return "candidate", "possible Access-origin event - confirm with client (newest-wins)"
    return "no", "web-origin; inbound disabled (web wins)"


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
        decision, note = decide(table, col["name"])
        rows.append({
            "access_table": table,
            "web_entity": entity,
            "access_column": col["name"],
            "crm_field": to_crm_field(col["name"]),
            "type": col["type"],
            "direction": "access->web",
            "decision": decision,
            "conflict_rule": "newest-wins",
            "cursor_column": cursor,
            "notes": note,
        })

out = os.path.join(SP, "sync-field-mapping-inbound.csv")
fields = ["access_table", "web_entity", "access_column", "crm_field", "type",
          "direction", "decision", "conflict_rule", "cursor_column", "notes"]
with open(out, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)

print("rows:", len(rows))
print("by decision:", dict(Counter(r["decision"] for r in rows)))
print("candidates:")
for r in rows:
    if r["decision"] == "candidate":
        print(f"  {r['access_table']}.{r['access_column']}")
print("written:", out)
