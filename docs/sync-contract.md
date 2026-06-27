# Sync Contract (template)

Fill one row per Access table that participates in sync. Nothing ships until this is agreed.

Format per table:

```
### <Access table name>  ⇄  <CRM table name>
- Direction: push (web→Access) | pull (Access→web) | two-way
- Unique key: <unit_code | application_id | booking_id | ...>
- Conflict rule: web-wins | access-wins | newest-wins
- Columns:
  | Access column | CRM field | Type | Sync? | Notes |
  |---------------|-----------|------|-------|-------|
  |               |           |      |       |       |
```

---

### tblUnitMaster  ⇄  unit_master   (EXAMPLE — replace with real schema)
- Direction: push (web→Access)
- Unique key: unit_code
- Conflict rule: web-wins
- Columns:
  | Access column | CRM field   | Type    | Sync? | Notes              |
  |---------------|-------------|---------|-------|--------------------|
  | UnitCode      | unit_code   | text    | key   | unique key         |
  | Status        | status      | text    | yes   | AVAILABLE/BOOKED…  |
  | Ownership     | ownership   | text    | yes   |                    |
  | BasePrice     | base_price  | number  | yes   |                    |
  | (none)        | internal_*  | —       | no    | never leaves web   |

> Get the real schema with `mdb-tables file.accdb` and `mdb-schema file.accdb`.
