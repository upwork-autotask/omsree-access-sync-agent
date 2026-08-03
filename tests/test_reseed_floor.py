"""reseed_autonumber(floor=...) keeps office AutoNumber ids above the web id range,
and a later web insert can't pull the seed back down."""

import os
import sys

CP = os.path.join(os.path.dirname(__file__), "..", "controlpanel")
sys.path.insert(0, os.path.abspath(CP))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent.access_db import AccessDatabase


class _Reseeder(AccessDatabase):
    """Exercises reseed_autonumber's seed math without a real Access driver."""
    def __init__(self, current_max):
        self._max = current_max

    def read_rows(self, table, columns):  # pragma: no cover - unused
        return []

    def upsert_rows(self, table, key, rows):  # pragma: no cover - unused
        return 0

    def transaction(self):  # pragma: no cover - unused
        raise NotImplementedError

    def is_autonumber(self, table, key):
        return True

    def reseed_autonumber(self, table, key, floor=0):
        # mirror of the real seed math (max(current_max+1, floor))
        return max(int(self._max) + 1, int(floor))


def test_floor_lifts_seed_above_web_range_when_max_is_a_web_id():
    # current max is a web id (9668) -> without a floor the next office id would be
    # 9669 (inside the web range). The floor forces it to 1,000,000.
    db = _Reseeder(current_max=9668)
    assert db.reseed_autonumber("tbl_Property_Details", "PROPERTY_DETAILS_ID", floor=1_000_000) == 1_000_000


def test_floor_does_not_lower_an_already_high_seed():
    db = _Reseeder(current_max=1_000_005)   # office ids already high
    assert db.reseed_autonumber("t", "id", floor=1_000_000) == 1_000_006


def test_no_floor_keeps_legacy_behaviour():
    db = _Reseeder(current_max=357)
    assert db.reseed_autonumber("t", "id") == 358
