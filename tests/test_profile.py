from migproto import profile


def test_render_is_generic_numbers_only():
    p = {"table": "account_user", "rows": 245, "columns": [
        {"column": "last_name", "type": "character varying(256)", "not_null": True,
         "default": None, "rows": 245, "null_values": 0, "distinct_non_null_values": 244,
         "max_char_length": 12},
        {"column": "referrer_id", "type": "integer", "not_null": False, "default": None,
         "rows": 245, "null_values": 219, "distinct_non_null_values": 26}],
        "constraints": [], "indexes": ["CREATE INDEX i ON account_user (last_name)"]}
    text = profile.render(p)
    assert "245 rows" in text and "244 distinct non-NULL values" in text
    assert "longest value 12 characters" in text
    assert "referrer_id: type integer, NULL allowed, no default, 219 NULL values" in text
    assert "Constraints on these columns: none." in text
    # Facts only: the profile never interprets the numbers for the planner.
    assert "duplicate" not in text.lower() and "truncat" not in text.lower()


def test_no_existing_columns_lists_only_the_table():
    text = profile.render({"table": "account_user", "rows": 3, "columns": [],
                           "constraints": [], "indexes": []})
    assert text.count("\n") == 1 and "3 rows" in text
