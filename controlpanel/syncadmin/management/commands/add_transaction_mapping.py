"""Idempotently set up the web->access mapping for tbl_Main_Transaction.

Adds the TRANSACTION_WEB_ID match-key column to the configured Access DB (Access keeps
owning the TRANSACTION_ID AutoNumber), then creates/refreshes the TableMapping + fields.

Run on the target machine after the web `tbl_Main_Transaction` view exists:
    python manage.py add_transaction_mapping
"""

import json

from django.core.management.base import BaseCommand

from agent.access_db import open_access_db
from syncadmin.models import AgentSettings, TableMapping, FieldMapping

ACCESS_TABLE = "tbl_Main_Transaction"
PG_VIEW = "tbl_Main_Transaction"
KEY_COL = "TRANSACTION_WEB_ID"

# web transaction_mode_id -> Access TRANSACTION_MODE_ID
#   web: 1=Cash, 2=Cheque, 3=RTGS/NEFT, 4=Razorpay/online
#   Access has no Cash -> 9=NA; web lumps RTGS/NEFT -> 4=RTGS
MODE_MAP = json.dumps({"1": 9, "2": 2, "3": 4, "4": 5})

FIELDS = [
    (KEY_COL, "id", "key", ""),
    ("PROPERTY_DETAILS_ID", "fk:property_details_id@tbl_Property_Details.WEB_SOURCE_ID", "sync", ""),
    ("TRANSACTION_DATE", "transaction_date", "sync", ""),
    ("AMOUNT", "amount", "sync", ""),
    ("TOTAL", "total", "sync", ""),
    ("ISACTIVE", "isactive", "sync", ""),
    ("TRANSACTION_MODE_ID", "transaction_mode_id", "sync", MODE_MAP),
    ("TRANSACTION_NO", "transaction_no", "sync", ""),
    ("REMARKS", "remarks", "sync", ""),
    # required NOT-NULL Yes/No flags the web doesn't send -> default False
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
            self.stderr.write("No Access DB configured (Connections screen).")
            return

        # 1. add the match-key column (Access keeps assigning TRANSACTION_ID)
        db = open_access_db(s.access_db_path, s.access_db_password)
        cols = [c["name"].upper() for c in db.table_columns(ACCESS_TABLE)]
        if KEY_COL not in cols:
            path = db.backup()
            if path:
                self.stdout.write(f"backup: {path}")
            db._conn.cursor().execute(f"ALTER TABLE {ACCESS_TABLE} ADD COLUMN {KEY_COL} LONG")
            db._conn.commit()
            self.stdout.write(self.style.SUCCESS(f"added column {ACCESS_TABLE}.{KEY_COL}"))
        else:
            self.stdout.write(f"{ACCESS_TABLE}.{KEY_COL} already present")

        # 2. create/refresh the mapping
        tm, created = TableMapping.objects.get_or_create(
            direction="web2access", access_table=ACCESS_TABLE,
            defaults=dict(pg_table=PG_VIEW, key_column=KEY_COL, order=110, is_active=True))
        tm.pg_table = PG_VIEW
        tm.key_column = KEY_COL
        tm.order = 110
        tm.is_active = True
        tm.save()
        tm.fields.all().delete()
        for acc, crm, role, vmap in FIELDS:
            FieldMapping.objects.create(table=tm, access_column=acc, crm_field=crm,
                                        role=role, value_map=vmap, is_active=True)
        self.stdout.write(self.style.SUCCESS(
            f"{'created' if created else 'updated'} mapping '{ACCESS_TABLE}' "
            f"(id={tm.id}, key={KEY_COL}, {tm.fields.count()} fields)"))
        self.stdout.write("Verify with:  python manage.py sync_now --dry-run")
