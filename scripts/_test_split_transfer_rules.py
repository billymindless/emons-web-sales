#!/usr/bin/env python3
"""초과이관 전표가 한쪽만 삭제 대상으로 분류되지 않는지 확인."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from split_payment_rules import (
    TRANSFER_METHOD,
    hide_payment_method_fields,
    is_transfer_alloc,
    new_transfer_group,
    protects_from_single_delete,
    transfer_group_token,
)


def test_pure_transfer_marks_both_sides():
    assert is_transfer_alloc(True, 0, -393_000)
    assert is_transfer_alloc(True, 0, 393_000)


def test_new_money_positive_is_not_transfer_row():
    assert is_transfer_alloc(True, 100_000, 493_000) is False
    assert is_transfer_alloc(True, 100_000, -393_000) is True


def test_plain_split_stays_a_real_payment():
    assert is_transfer_alloc(False, 1_000_000, 700_000) is False


def test_blank_negative_is_protected_but_real_offset_is_not():
    assert protects_from_single_delete(None, -393_000)
    assert protects_from_single_delete("", -393_000)
    assert protects_from_single_delete(float("nan"), -393_000)
    assert protects_from_single_delete("신용카드", -393_000) is False
    assert protects_from_single_delete(TRANSFER_METHOD, 393_000)
    assert protects_from_single_delete("현금(수금)", 393_000) is False


def test_zero_deposit_transfer_hides_approval_fields():
    assert hide_payment_method_fields(True, 0)
    assert hide_payment_method_fields(True, 100_000) is False
    assert hide_payment_method_fields(False, 0) is False


def test_group_token_roundtrip():
    token = new_transfer_group()
    assert transfer_group_token(token) == token
    assert transfer_group_token("신한카드") is None
    assert transfer_group_token(None) is None


if __name__ == "__main__":
    test_pure_transfer_marks_both_sides()
    test_new_money_positive_is_not_transfer_row()
    test_plain_split_stays_a_real_payment()
    test_blank_negative_is_protected_but_real_offset_is_not()
    test_zero_deposit_transfer_hides_approval_fields()
    test_group_token_roundtrip()
    print("ok")
