from django.core.management.base import BaseCommand
from agent.access_db import open_access_db
from syncadmin.models import AgentSettings, TableMapping, FieldMapping

ACCESS_TABLE = "tbl_Main_Transaction"
PG_VIEW = "tbl_Main_Transaction"
KEY_COL = "TRANSACTION_WEB_ID"
# The web view aligns its lookup ids to the Access lookup tables (mode/type/source/bank),
# so all four sync directly -- no value_map. (Razorpay mode=11 must exist in Access
# tbl_Transaction_Mode.)
FIELDS = [
    (KEY_COL, "id", "key", ""),
    ("PROPERTY_DETAILS_ID", "fk:property_details_id@tbl_Property_Details.WEB_SOURCE_ID", "sync", ""),
    ("TRANSACTION_DATE", "transaction_date", "sync", ""),
    ("AMOUNT", "amount", "sync", ""),
    ("TOTAL", "total", "sync", ""),
    ("ISACTIVE", "isactive", "sync", ""),
    ("TRANSACTION_MODE_ID", "transaction_mode_id", "sync", ""),
    ("TRANSACTION_TYPE_ID", "transaction_type_id", "sync", ""),
    ("TRANSACTION_SOURCE_ID", "transaction_source_id", "sync", ""),
    ("BANK_ID", "bank_id", "sync", ""),
    ("TRANSACTION_NO", "transaction_no", "sync", ""),
    ("REMARKS", "remarks", "sync", ""),
    ("ROUTED_TRANSACTION", "const:false", "sync", ""),
    ("IS_MILESTONE_PAYMENT", "const:false", "sync", ""),
    ("IS_TDS_CREDIT", "const:false", "sync", ""),
    ("CHEQUE_BOUNCE_DEBIT", "const:false", "sync", ""),
    ("IS_OMB_ASSISTED_TDS", "const:false", "sync", ""),
]

class Command(BaseCommand):
    help = "Add TRANSACTION_WEB_ID + create the tbl_Main_Transaction web->access mapping."
    def handle(self, *args, **opts):
        s = AgentSettings.get_solo()
        if not s.access_db_path:
            self.stderr.write("No Access DB configured (Connections screen)."); return
        db = open_access_db(s.access_db_path, s.access_db_password)
        cols = [c["name"].upper() for c in db.table_columns(ACCESS_TABLE)]
        if KEY_COL not in cols:
            p = db.backup()
            if p: self.stdout.write(f"backup: {p}")
            db._conn.cursor().execute(f"ALTER TABLE {ACCESS_TABLE} ADD COLUMN {KEY_COL} LONG")
            db._conn.commit()
            self.stdout.write(self.style.SUCCESS(f"added column {ACCESS_TABLE}.{KEY_COL}"))
        else:
            self.stdout.write(f"{ACCESS_TABLE}.{KEY_COL} already present")
        tm, created = TableMapping.objects.get_or_create(
            direction="web2access", access_table=ACCESS_TABLE,
            defaults=dict(pg_table=PG_VIEW, key_column=KEY_COL, order=110, is_active=True))
        tm.pg_table = PG_VIEW; tm.key_column = KEY_COL; tm.order = 110; tm.is_active = True; tm.save()
        # Only map columns that actually exist in this Access table (schema versions differ).
        present = {c["name"].upper() for c in db.table_columns(ACCESS_TABLE)}
        tm.fields.all().delete()
        skipped = []
        for acc, crm, role, vmap in FIELDS:
            if acc.upper() not in present:
                skipped.append(acc); continue
            FieldMapping.objects.create(table=tm, access_column=acc, crm_field=crm, role=role, value_map=vmap, is_active=True)
        if skipped:
            self.stdout.write(self.style.WARNING(f"skipped (no such Access column): {', '.join(skipped)}"))
        self.stdout.write(self.style.SUCCESS(f"mapping ready (id={tm.id}, {tm.fields.count()} fields)"))
