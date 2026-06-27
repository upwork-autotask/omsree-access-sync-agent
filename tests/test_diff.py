from agent.diff import compute_diff, format_diff


def test_insert_when_key_absent():
    diff = compute_diff(
        "unit_master",
        "unit_code",
        existing_rows=[],
        incoming_rows=[{"unit_code": "A-1", "status": "BOOKED"}],
    )
    assert len(diff.inserts) == 1
    assert not diff.updates
    assert diff.inserts[0].key_value == "A-1"


def test_update_detects_field_changes():
    diff = compute_diff(
        "unit_master",
        "unit_code",
        existing_rows=[{"unit_code": "A-1", "status": "AVAILABLE", "base_price": 100}],
        incoming_rows=[{"unit_code": "A-1", "status": "BOOKED", "base_price": 100}],
    )
    assert not diff.inserts
    assert len(diff.updates) == 1
    changes = diff.updates[0].field_changes
    assert len(changes) == 1
    assert changes[0].column == "status"
    assert changes[0].old == "AVAILABLE"
    assert changes[0].new == "BOOKED"


def test_unchanged_row_is_counted_not_updated():
    diff = compute_diff(
        "unit_master",
        "unit_code",
        existing_rows=[{"unit_code": "A-1", "status": "BOOKED"}],
        incoming_rows=[{"unit_code": "A-1", "status": "BOOKED"}],
    )
    assert not diff.inserts and not diff.updates
    assert diff.unchanged == 1


def test_whitespace_is_normalized():
    diff = compute_diff(
        "t", "k",
        existing_rows=[{"k": "1", "v": "hello"}],
        incoming_rows=[{"k": "1", "v": "  hello  "}],
    )
    assert diff.unchanged == 1


def test_extra_access_columns_are_left_untouched():
    # incoming has no 'internal_note'; it must not appear as a change
    diff = compute_diff(
        "t", "k",
        existing_rows=[{"k": "1", "status": "X", "internal_note": "secret"}],
        incoming_rows=[{"k": "1", "status": "X"}],
    )
    assert diff.unchanged == 1
    assert not diff.updates


def test_rows_without_key_are_skipped():
    diff = compute_diff(
        "t", "k",
        existing_rows=[],
        incoming_rows=[{"status": "X"}, {"k": None, "status": "Y"}],
    )
    assert diff.skipped_no_key == 2
    assert not diff.inserts


def test_format_diff_is_readable():
    diff = compute_diff(
        "unit_master", "unit_code",
        existing_rows=[{"unit_code": "A-1", "status": "AVAILABLE"}],
        incoming_rows=[
            {"unit_code": "A-1", "status": "BOOKED"},
            {"unit_code": "A-2", "status": "AVAILABLE"},
        ],
    )
    text = format_diff(diff)
    assert "1 insert" in text and "1 update" in text
    assert "A-2" in text and "A-1" in text
