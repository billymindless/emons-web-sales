#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
card_company 미입력 20건 고객명 조회 스크립트 (읽기 전용)
사용: python scripts/_verify_missing_card_company.py
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

MISSING_IDS = [732, 1298, 1556, 1593, 1596, 1830, 1861, 1862, 1863, 1864,
               1930, 2005, 2023, 2102, 2168, 2169, 2179, 2203, 2273, 2335]


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

    # 1) 결제 내역 조회
    r = client.table("app_payments").select(
        "id, order_id, db_filename, payment_date, amount, payment_method, created_by"
    ).in_("id", MISSING_IDS).execute()
    pay_df = pd.DataFrame(r.data or [])
    pay_df["amount"] = pd.to_numeric(pay_df["amount"], errors="coerce").fillna(0)

    # 2) 연관 주문 → customer_id 조회
    oid_list = pay_df["order_id"].dropna().astype(int).unique().tolist()
    order_rows = []
    for chunk in [oid_list[i:i+100] for i in range(0, len(oid_list), 100)]:
        rr = client.table("app_orders").select("id, db_filename, customer_id").in_("id", chunk).execute()
        order_rows.extend(rr.data or [])
    order_df = pd.DataFrame(order_rows).rename(columns={"id": "order_id"})

    # 3) customer_id → 고객명 조회
    cid_list = [int(x) for x in order_df["customer_id"].dropna().unique()]
    cust_rows = []
    for chunk in [cid_list[i:i+100] for i in range(0, len(cid_list), 100)]:
        rc = client.table("app_customers").select("id, name").in_("id", chunk).execute()
        cust_rows.extend(rc.data or [])
    cust_df = pd.DataFrame(cust_rows).rename(columns={"id": "customer_id"})

    # 4) 조인
    merged = (pay_df
              .merge(order_df[["order_id", "customer_id"]], on="order_id", how="left")
              .merge(cust_df, on="customer_id", how="left"))

    merged = merged.sort_values("id")
    merged["amount"] = merged["amount"].apply(lambda x: f"{int(x):,}")

    print("=" * 80)
    print("card_company 미입력 20건 - 고객명 포함 상세")
    print("=" * 80)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 300)
    print(merged[["id", "order_id", "payment_date", "payment_method",
                  "amount", "name", "created_by", "db_filename"]
                 ].rename(columns={"name": "고객명"}).to_string(index=False))


if __name__ == "__main__":
    main()
