#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
신용카드·체크카드 수수료(fee_amount) 계산 검증 스크립트 (읽기 전용)

검증 기준:
  신용카드·메인페이 → amount * 0.025 (2.5%)
  체크카드          → amount * 0.015 (1.5%)
  그 외             → 0

사용: python scripts/_verify_fee_calculation.py
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

FEE_CREDIT  = 0.025   # 신용카드·메인페이
FEE_CHECK   = 0.015   # 체크카드
TOLERANCE   = 1.0     # 반올림 허용 오차 (1원)


def expected_fee(method: str, amount: float) -> float:
    if not method or amount <= 0:
        return 0.0
    if method in ("신용카드", "메인페이"):
        return round(float(amount) * FEE_CREDIT, 0)
    if method == "체크카드":
        return round(float(amount) * FEE_CHECK, 0)
    return 0.0


def load_client():
    with open(SECRETS, "rb") as f:
        data = tomllib.load(f)
    sup = data["supabase"]
    url = sup["url"].strip()
    key = (sup.get("key") or sup.get("anon_key", "")).strip()
    from supabase import create_client
    return create_client(url, key)


def main():
    client = load_client()

    # 전체 결제 내역 조회 (Supabase 1000건 제한 우회 - 페이지네이션)
    print("Supabase app_payments 전체 조회 중...")
    rows = []
    page_size = 1000
    offset = 0
    while True:
        r = client.table("app_payments").select(
            "id, order_id, db_filename, payment_date, amount, payment_method, card_company, fee_amount"
        ).range(offset, offset + page_size - 1).execute()
        batch = r.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size

    if not rows:
        print("결제 데이터가 없습니다.")
        return

    df = pd.DataFrame(rows)
    df["amount"]     = pd.to_numeric(df["amount"],     errors="coerce").fillna(0)
    df["fee_amount"] = pd.to_numeric(df["fee_amount"], errors="coerce").fillna(0)
    df["payment_method"] = df["payment_method"].fillna("").astype(str).str.strip()
    df["card_company"]   = df["card_company"].fillna("").astype(str).str.strip()

    print(f"총 {len(df)}건 결제 데이터 로드 완료.\n")

    # 예상 수수료 계산
    df["expected_fee"] = df.apply(
        lambda row: expected_fee(row["payment_method"], row["amount"]), axis=1
    )
    df["fee_diff"] = df["fee_amount"] - df["expected_fee"]
    df["is_wrong"] = df["fee_diff"].abs() > TOLERANCE

    # ── 전체 요약 ──────────────────────────────────────
    print("=" * 70)
    print("[ 1 ] 결제수단별 수수료 계산 현황 요약")
    print("=" * 70)
    card_methods = ["신용카드", "메인페이", "체크카드"]
    target_df = df[df["payment_method"].isin(card_methods)].copy()
    wrong_df  = target_df[target_df["is_wrong"]]

    for method in card_methods:
        sub  = target_df[target_df["payment_method"] == method]
        sub_wrong = sub[sub["is_wrong"]]
        print(f"  {method:8s} | 총 {len(sub):4d}건 | 오류 {len(sub_wrong):4d}건"
              + (f"  ← 오류 있음!" if len(sub_wrong) else ""))

    print(f"\n  ▶ 수수료 대상 전체 {len(target_df)}건 중 오류: {len(wrong_df)}건")
    print()

    # ── 신한카드 필터 ─────────────────────────────────
    print("=" * 70)
    print("[ 2 ] 신한카드(card_company='신한카드') 수수료 검증")
    print("=" * 70)
    shinhan_df = target_df[target_df["card_company"] == "신한카드"].copy()
    shinhan_wrong = shinhan_df[shinhan_df["is_wrong"]]

    if shinhan_df.empty:
        print("  신한카드 결제 내역 없음.\n")
    else:
        print(f"  신한카드 총 {len(shinhan_df)}건 | 오류 {len(shinhan_wrong)}건\n")
        print(shinhan_df[["id","order_id","payment_date","payment_method",
                           "amount","expected_fee","fee_amount","fee_diff","is_wrong"
                           ]].to_string(index=False))
        print()

    # ── 오류 전체 목록 출력 ───────────────────────────
    if wrong_df.empty:
        print("=" * 70)
        print("[ 3 ] 수수료 오류 없음 — 모든 카드 결제 수수료가 정상입니다.")
        print("=" * 70)
        return

    print("=" * 70)
    print(f"[ 3 ] 수수료 오류 {len(wrong_df)}건 전체 목록 (결제수단 카드류)")
    print("=" * 70)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 280)
    print(wrong_df[["id","order_id","db_filename","payment_date",
                     "payment_method","card_company",
                     "amount","expected_fee","fee_amount","fee_diff"
                     ]].sort_values("fee_diff", key=abs, ascending=False).to_string(index=False))
    print()

    # ── 카드사별 오류 집계 ────────────────────────────
    print("=" * 70)
    print("[ 4 ] 오류 건 카드사별 집계")
    print("=" * 70)
    agg = (wrong_df.groupby(["payment_method","card_company"])
           .agg(건수=("id","count"), 오류합계=("fee_diff","sum"))
           .reset_index()
           .sort_values("건수", ascending=False))
    print(agg.to_string(index=False))
    print()

    # ── 음수 금액(상계 전표) 제외 후 재확인 ───────────
    real_wrong = wrong_df[wrong_df["amount"] > 0]
    reversal_wrong = wrong_df[wrong_df["amount"] <= 0]
    print("=" * 70)
    print("[ 5 ] 실 결제(amount>0) vs 상계 전표(amount<=0) 구분")
    print("=" * 70)
    print(f"  실 결제 오류   : {len(real_wrong)}건")
    print(f"  상계 전표 오류 : {len(reversal_wrong)}건  (음수 amount, 정상적 상계 처리)")
    print()

    if not real_wrong.empty:
        print("[ 5-1 ] 실 결제(amount>0) 오류 상세")
        print(real_wrong[["id","order_id","db_filename","payment_date",
                           "payment_method","card_company",
                           "amount","expected_fee","fee_amount","fee_diff"
                           ]].to_string(index=False))
        print()


if __name__ == "__main__":
    main()
