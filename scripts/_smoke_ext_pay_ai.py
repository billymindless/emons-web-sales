# -*- coding: utf-8 -*-
"""ext_pay_ai_reconcile 스모크: 마스킹 · build_ai_context · sanitize · save/load feedback (구조)."""
from __future__ import annotations

import os
import sys
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd

import ext_pay_ai_reconcile as epai


def test_mask() -> None:
    assert epai._mask_name("장상국") == "장*국"
    assert epai._mask_name("최형") == "최*"
    assert epai._mask_name("홍") == "홍"
    assert epai._mask_name("") == ""
    assert epai._mask_name(None) == ""
    assert epai._mask_name("홍길동재") == "홍*동재"
    assert epai._mask_phone("010-1234-5678") == "****-5678"
    assert epai._mask_phone("5678") == "****-5678"
    assert epai._mask_phone(None) == ""
    assert epai._mask_phone("12") == ""


def test_build_context() -> None:
    df = pd.DataFrame([
        {
            "row_id": 101, "공식일자": "2026-09-10", "뒤4": "1234", "승인번호": "005379",
            "공식금액": 250000, "공식상태": "정상", "구매자": "장상국", "결과": "official_only",
            "고객명": "", "고객전화": "", "담당매니저": "",
            "ERP일자": "", "ERP금액": 0,
            "_payment_id": None, "_order_id": None,
            "_amount_int": 0, "_payment_method": "",
        },
        {
            "row_id": 0, "공식일자": "", "뒤4": "1234", "승인번호": "",
            "공식금액": 0, "공식상태": "", "구매자": "", "결과": "erp_only",
            "고객명": "최형", "고객전화": "010-9999-1234", "담당매니저": "홍매니저",
            "ERP일자": "2026-09-11", "ERP금액": 250000,
            "_payment_id": 501, "_order_id": 900,
            "_amount_int": 250000, "_payment_method": "온누리상품권",
        },
        {
            "row_id": 200, "공식일자": "2026-09-01", "뒤4": "5678", "승인번호": "111111",
            "공식금액": 300000, "공식상태": "정상", "구매자": "홍길동", "결과": "matched_ok",
            "고객명": "홍길동", "고객전화": "010-1111-2222", "담당매니저": "김매니저",
            "ERP일자": "2026-09-01", "ERP금액": 300000,
            "_payment_id": 700, "_order_id": 800,
            "_amount_int": 300000, "_payment_method": "온누리상품권",
        },
    ])
    ctx = epai.build_ai_context(df, "onnuri")
    assert ctx["source"] == "onnuri"
    assert len(ctx["unmatched_official"]) == 1
    assert ctx["unmatched_official"][0]["buyer_masked"] == "장*국"
    assert ctx["unmatched_official"][0]["last4"] == "1234"
    assert len(ctx["erp_only"]) == 1
    assert ctx["erp_only"][0]["payment_id"] == 501
    assert ctx["erp_only"][0]["customer_masked"] == "최*"
    assert ctx["erp_only"][0]["phone_masked"] == "****-1234"
    assert len(ctx["matched_examples"]) == 1
    assert ctx["matched_examples"][0]["official"]["buyer_masked"] == "홍*동"


def test_sanitize() -> None:
    pairs = epai._sanitize_pairs([
        {"row_id": 1, "payment_id": 10, "confidence": 0.9, "reason": "승인번호 일치"},
        {"row_id": 2, "payment_id": 10, "confidence": 0.8, "reason": "중복 pid"},  # drop
        {"row_id": 1, "payment_id": 11, "confidence": 0.9, "reason": "중복 row"},  # drop
        {"row_id": 3, "payment_id": 20, "confidence": 0.4, "reason": "낮은 신뢰"},  # drop
        {"row_id": "bad", "payment_id": 21, "confidence": 0.9, "reason": ""},  # drop
    ])
    assert len(pairs) == 1
    assert pairs[0]["payment_id"] == 10

    splits = epai._sanitize_splits([
        {"row_id": 1, "payment_ids": [1, 2, 3], "confidence": 0.8, "reason": "분할"},
        {"row_id": 2, "payment_ids": [1], "confidence": 0.9, "reason": "1개"},  # drop
        {"row_id": 1, "payment_ids": [4, 5], "confidence": 0.9, "reason": "중복 row"},  # drop
        {"row_id": 3, "payment_ids": [3, 4], "confidence": 0.9, "reason": "pid 중복"},  # drop
    ])
    assert len(splits) == 1
    assert splits[0]["payment_ids"] == [1, 2, 3]

    flags = epai._sanitize_flags([
        {"row_id": 1, "payment_id": None, "type": "date_far", "confidence": 0.7,
         "reason": "날짜차이"},
        {"row_id": None, "payment_id": 5, "type": "amount_diff", "confidence": 0.3, "reason": ""},
    ])
    assert len(flags) == 2


def test_kind_pattern() -> None:
    assert epai._kind_pattern("onnuri") == "extpay_%_onnuri"
    assert epai._kind_pattern("ulsanpay") == "extpay_%_ulsanpay"


def test_gemini_no_key() -> None:
    # 키 없음 → 빈 결과 + error
    os.environ.pop("GEMINI_API_KEY", None)
    out = epai.suggest_matches_with_gemini(
        api_key="", source="onnuri",
        context={"unmatched_official": [{"row_id": 1}], "erp_only": []},
        feedback=[],
    )
    assert out["error"] == "GEMINI_API_KEY 없음"
    assert out["pairs"] == []


def test_gemini_bad_source() -> None:
    out = epai.suggest_matches_with_gemini(
        api_key="dummy", source="foo",
        context={"unmatched_official": [{"row_id": 1}]}, feedback=[],
    )
    assert "지원하지 않는 소스" in (out["error"] or "")


if __name__ == "__main__":
    test_mask()
    test_build_context()
    test_sanitize()
    test_kind_pattern()
    test_gemini_no_key()
    test_gemini_bad_source()
    print(json.dumps({"ok": True}, ensure_ascii=False))
