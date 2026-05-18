#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
신한카드 인식 문제 검증 스크립트 (읽기 전용)

검증 항목:
  1. card_company = '신한카드' 로 저장된 결제 내역
  2. card_company 값이 '신한' 관련이나 정확히 '신한카드'가 아닌 값
  3. 신용카드/체크카드인데 card_company가 없거나 비어있는 경우
  4. _to_detailed 로직 시뮬레이션 결과 확인

사용: python scripts/_verify_shinhan_card.py
"""
from __future__ import annotations
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / ".streamlit" / "secrets.toml"

CARD_COMPANY_OPTIONS = [
    "신한카드", "KB국민카드", "우리카드", "NH농협카드", "하나카드",
    "카카오뱅크", "토스뱅크", "케이뱅크",
    "삼성카드", "현대카드", "롯데카드", "BC카드", "기타",
]

_CARD_SHORT = {
    "신한카드": "신한", "삼성카드": "삼성", "KB국민카드": "국민",
    "현대카드": "현대", "롯데카드": "롯데", "우리카드": "우리",
    "하나카드": "하나", "BC카드": "BC", "NH농협카드": "농협", "기타": "기타",
    "카카오뱅크": "카카오", "토스뱅크": "토스", "케이뱅크": "케이",
}


def to_detailed(meth: str, cc: str) -> str:
    """app.py _to_detailed 로직 동일 재현."""
    meth = (meth or "").strip() or "미지정"
    if meth == "메인페이":
        return "메인페이"
    if meth in ("신용카드", "체크카드"):
        cc = (cc or "").strip()
        if cc in ("nan", "None", "none"):
            cc = ""
        short = _CARD_SHORT.get(cc, cc or "미지정")
        prefix = "신용" if meth == "신용카드" else "체크"
        return f"{prefix}_{short}" if cc else meth
    return meth


def load_client():
    with open(SECRETS, "rb") as f:
        data = tomllib.load(f)
    sup = data["supabase"]
    url = sup["url"].strip()
    key = (sup.get("key") or sup.get("anon_key", "")).strip()
    from supabase import create_client
    return create_client(url, key)


def fetch_all(client) -> pd.DataFrame:
    rows, offset, page_size = [], 0, 1000
    while True:
        r = client.table("app_payments").select(
            "id, order_id, db_filename, payment_date, amount, payment_method, card_company, created_by, created_at"
        ).range(offset, offset + page_size - 1).execute()
        batch = r.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    df = pd.DataFrame(rows)
    df["payment_method"] = df["payment_method"].fillna("").astype(str).str.strip()
    df["card_company"]   = df["card_company"].fillna("").astype(str).str.strip()
    df["card_company"]   = df["card_company"].replace({"nan": "", "None": "", "none": ""})
    df["amount"]         = pd.to_numeric(df["amount"], errors="coerce").fillna(0)
    return df


def main():
    client = load_client()
    print("app_payments 전체 조회 중...")
    df = fetch_all(client)
    print(f"총 {len(df)}건 로드.\n")

    card_df = df[df["payment_method"].isin(("신용카드", "체크카드"))].copy()
    card_df["detailed"] = card_df.apply(
        lambda r: to_detailed(r["payment_method"], r["card_company"]), axis=1
    )

    # ── 1. card_company 값 분포 ─────────────────────────────────
    print("=" * 65)
    print("[ 1 ] 신용카드/체크카드 결제의 card_company 값 분포")
    print("=" * 65)
    dist = (card_df.groupby(["payment_method", "card_company"])
            .size().reset_index(name="건수")
            .sort_values("건수", ascending=False))
    print(dist.to_string(index=False))
    print()

    # ── 2. card_company 가 CARD_COMPANY_OPTIONS 에 없는 이상한 값 ──
    print("=" * 65)
    print("[ 2 ] CARD_COMPANY_OPTIONS 에 없는 card_company 값")
    print("=" * 65)
    valid_set = set(CARD_COMPANY_OPTIONS) | {""}
    odd_mask = ~card_df["card_company"].isin(valid_set)
    odd_df   = card_df[odd_mask]
    if odd_df.empty:
        print("  이상 값 없음 - 모든 카드사 값이 유효합니다.\n")
    else:
        print(f"  이상 값 {len(odd_df)}건:")
        print(odd_df[["id","order_id","db_filename","payment_date",
                       "payment_method","card_company","amount","created_by"
                       ]].to_string(index=False))
        print()

    # ── 3. 신한 관련 card_company 상세 ──────────────────────────
    print("=" * 65)
    print("[ 3 ] '신한' 포함 card_company 값 전수 확인")
    print("=" * 65)
    shinhan_mask = card_df["card_company"].str.contains("신한", na=False)
    shinhan_df   = card_df[shinhan_mask]
    if shinhan_df.empty:
        print("  '신한' 포함 card_company 없음.\n")
    else:
        print(f"  총 {len(shinhan_df)}건:\n")
        print(shinhan_df[["id","order_id","db_filename","payment_date",
                           "payment_method","card_company","detailed","amount","created_by"
                           ]].to_string(index=False))
        print()
        # 정확히 '신한카드'가 아닌 케이스
        not_exact = shinhan_df[shinhan_df["card_company"] != "신한카드"]
        if not_exact.empty:
            print("  -> 모두 정확히 '신한카드'로 저장됨 (인식 문제 없음)\n")
        else:
            print(f"  -> '신한카드'와 다르게 저장된 값 {len(not_exact)}건:")
            print(not_exact[["id","card_company"]].to_string(index=False))
            print()

    # ── 4. detailed 결과가 '신용_신한'/'체크_신한'이 되어야 하는데 안된 경우 ──
    print("=" * 65)
    print("[ 4 ] card_company='신한카드' 인데 detailed 결과 확인")
    print("=" * 65)
    shinhan_exact = card_df[card_df["card_company"] == "신한카드"].copy()
    if shinhan_exact.empty:
        print("  card_company='신한카드' 데이터 없음.\n")
    else:
        expected_map = {"신용카드": "신용_신한", "체크카드": "체크_신한"}
        shinhan_exact["expected_detailed"] = shinhan_exact["payment_method"].map(expected_map)
        mismatch = shinhan_exact[shinhan_exact["detailed"] != shinhan_exact["expected_detailed"]]
        if mismatch.empty:
            print(f"  {len(shinhan_exact)}건 모두 올바르게 '신용_신한'/'체크_신한'으로 변환됩니다.\n")
        else:
            print(f"  변환 오류 {len(mismatch)}건:")
            print(mismatch[["id","payment_method","card_company","detailed","expected_detailed"]].to_string(index=False))
            print()

    # ── 5. amount>0 실 결제 중 card_company 빈값 ─────────────────
    print("=" * 65)
    print("[ 5 ] 실 결제(amount>0) 신용/체크카드 중 card_company 비어있는 건")
    print("=" * 65)
    real_card = card_df[card_df["amount"] > 0]
    empty_cc  = real_card[real_card["card_company"] == ""]
    if empty_cc.empty:
        print("  card_company 빈 값 없음.\n")
    else:
        print(f"  총 {len(empty_cc)}건 - 카드사 미입력:\n")
        print(empty_cc[["id","order_id","db_filename","payment_date",
                         "payment_method","amount","created_by"
                         ]].to_string(index=False))
        print()

    # ── 6. 최근 신한카드 30건 ───────────────────────────────────
    print("=" * 65)
    print("[ 6 ] 최근 신한카드 결제 30건 (amount>0)")
    print("=" * 65)
    recent_sh = (shinhan_exact[shinhan_exact["amount"] > 0]
                 .sort_values("payment_date", ascending=False)
                 .head(30))
    if recent_sh.empty:
        print("  없음.\n")
    else:
        print(recent_sh[["id","order_id","db_filename","payment_date",
                          "payment_method","card_company","detailed","amount","created_by"
                          ]].to_string(index=False))
        print()


if __name__ == "__main__":
    main()
