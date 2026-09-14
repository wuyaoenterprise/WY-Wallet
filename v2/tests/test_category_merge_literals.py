from __future__ import annotations

import inspect

import wywallet.db as db


def test_ilike_literal_escapes_sql_wildcards_and_backslashes():
    assert db._escape_ilike_literal("50%_off\\promo") == "50\\%\\_off\\\\promo"


def test_category_merge_never_mutates_with_ilike_pattern():
    source = inspect.getsource(db.merge_category_safely)
    assert ".ilike(" not in source
    assert '.in_("id", chunk)' in source
    assert '.delete().eq("name", exact_name)' in source
