from wywallet.config import EXPENSE
from wywallet.receipt_identity import add_line_ids, receipt_presence


def test_same_root_with_no_matching_semantic_lines_is_blocked_conservatively():
    root = "abcdef1234567890"
    existing = add_line_ids([
        {"date": "2026-09-01", "item": "Meal", "type": EXPENSE, "amount": 10.0},
    ], root)[0]["receipt_id"]
    current = add_line_ids([
        {"date": "2026-09-01", "item": "Meal OCR drift", "type": EXPENSE, "amount": 11.0},
    ], root)[0]["receipt_id"]
    presence = receipt_presence(root, [current], [existing])
    assert presence["ambiguous"] is True
    assert presence["complete"] is True
    assert presence["partial"] is False
