#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
박승현 직원 마진 검증 스크립트 (읽기 전용, 프로그램 미수정)
- 취소금액 -3,460,000 / 마진차감 -4,371,800 원인 규명
사용: python scripts/_verify_margin_parksh.py
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

TARGET_EMP = "박승현"
# 최근 2주 범위로 조회
SEARCH_START = "2026-04-01"
SEARCH_END   = "2026-04-28"


def load_client():
    with open(SECRETS, "rb") as f:
        data = tomllib.load(f)
    sup = data["supabase"]
    url = sup["url"].strip()
    key = (sup.get("key") or sup.get("anon_key", "")).strip()
    from supabase import create_client
    return create_client(url, key), sup


def main():
    client, sup = load_client()
    sales_tenant = (sup.get("sales_tenant_column") or "").strip() or None

    # 1) 전체 매장 목록
    stores_r = client.table("app_stores").select("id, store_name, db_filename").execute()
    stores = pd.DataFrame(stores_r.data or [])
    print("=== [0] 전체 매장 목록 ===")
    print(stores.to_string(index=False))
    print()

    # 2) 전 매장 sales에서 박승현 포함 행 조회
    print(f"=== [1] 전 매장 sales 중 '{TARGET_EMP}' 포함 행 ({SEARCH_START}~{SEARCH_END}) ===")
    all_sales = []
    for _, store in stores.iterrows():
        db_fn = store["db_filename"]
        q = client.table("sales").select(
            "id, transaction_date, amount, order_id, employee_names, db_filename"
        )
        if sales_tenant:
            q = q.eq(sales_tenant, db_fn)
        r = q.execute()
        df = pd.DataFrame(r.data or [])
        if df.empty:
            continue
        df["transaction_date"] = pd.to_datetime(df["transaction_date"], errors="coerce")
        df = df.dropna(subset=["transaction_date"])
        mask_date = (
            (df["transaction_date"].dt.date >= pd.Timestamp(SEARCH_START).date()) &
            (df["transaction_date"].dt.date <= pd.Timestamp(SEARCH_END).date())
        )
        df = df.loc[mask_date]
        mask_emp = df["employee_names"].apply(
            lambda s: any(TARGET_EMP == x.strip() or TARGET_EMP in x.strip()
                          for x in str(s or "").split(",") if x.strip())
        )
        hit = df.loc[mask_emp].copy()
        if not hit.empty:
            hit["store_name"] = store["store_name"]
            all_sales.append(hit)

    if not all_sales:
        print(f"  '{TARGET_EMP}' sales 행이 없습니다.")
        return

    s_df = pd.concat(all_sales, ignore_index=True)
    s_df = s_df.sort_values("transaction_date")
    print(s_df[["store_name","transaction_date","employee_names","amount","order_id"]].to_string(index=False))
    print()

    # 3) 연관 주문 조회
    oids = sorted({int(x) for x in s_df["order_id"].dropna().unique()})
    print(f"=== [2] 연관 주문 ID: {oids} ===")
    chunks = [oids[i:i+80] for i in range(0, len(oids), 80)]
    o_rows = []
    for ch in chunks:
        r = client.table("app_orders").select(
            "id, db_filename, order_date, employee_names, category, "
            "total_amount, cost_price, display_sales_amount, display_cost_amount, "
            "actual_margin, balance_status"
        ).in_("id", ch).execute()
        o_rows.extend(r.data or [])
    o_df = pd.DataFrame(o_rows)
    print(o_df.to_string(index=False))
    print()

    # 4) 연관 결제 조회
    p_rows = []
    for _, store in stores.iterrows():
        db_fn = store["db_filename"]
        r = client.table("app_payments").select(
            "id, order_id, payment_date, amount, payment_method, card_company, fee_amount"
        ).in_("order_id", oids).eq("db_filename", db_fn).execute()
        p_rows.extend(r.data or [])
    p_df = pd.DataFrame(p_rows)
    print(f"=== [3] 연관 결제 내역 ({len(p_df)}건) ===")
    print(p_df.to_string(index=False))
    print()

    # 5) 주문별 마진 재계산
    print("=== [4] 주문별 마진 재계산 검증 ===")
    print("  공식: basic_margin = total_amount - (cost_price + display_cost_amount)")
    print("        actual_margin = basic_margin - SUM(fee_amount)\n")

    for _, order in o_df.iterrows():
        oid = int(order["id"])
        total_amt  = float(order["total_amount"] or 0)
        cost_price = float(order["cost_price"] or 0)
        disp_cost  = float(order["display_cost_amount"] or 0)
        basic_m    = total_amt - (cost_price + disp_cost)
        stored_m   = float(order["actual_margin"] or 0)

        ops = p_df[p_df["order_id"] == oid]
        sum_fee = float(ops["fee_amount"].fillna(0).sum())
        recalc  = basic_m - sum_fee

        print(f"  주문 #{oid}  [{order.get('order_date','')}] {order.get('category','')} / {order.get('employee_names','')}")
        print(f"    total_amount        = {total_amt:>12,.0f}")
        print(f"    cost_price          = {cost_price:>12,.0f}")
        print(f"    display_cost_amount = {disp_cost:>12,.0f}")
        print(f"    {'─'*38}")
        print(f"    basic_margin        = {basic_m:>12,.0f}")
        print(f"    SUM(fee_amount)     = {sum_fee:>12,.0f}  ({len(ops)}건)")
        print(f"    {'─'*38}")
        print(f"    재계산 actual_margin= {recalc:>12,.0f}")
        match_str = "[일치]" if abs(recalc - stored_m) < 1 else "[불일치!]"
        print(f"    저장된 actual_margin= {stored_m:>12,.0f}  {match_str}")
        if not ops.empty:
            print(f"    결제 상세:")
            for _, pm in ops.iterrows():
                print(f"      {pm['payment_date']} | {pm['payment_method']} | "
                      f"결제={float(pm['amount'] or 0):,.0f} | 수수료={float(pm['fee_amount'] or 0):,.0f}")
        print()

    # 6) 날짜별 마진 배분 (KPI 로직 동일)
    total_map  = o_df.set_index("id")["total_amount"].fillna(0).astype(float).to_dict()
    margin_map = o_df.set_index("id")["actual_margin"].fillna(0).astype(float).to_dict()

    for tx_date, grp in s_df.groupby(s_df["transaction_date"].dt.date):
        print(f"=== [5] KPI 배분 계산 - {tx_date} ===")
        rows_out = []
        sum_sales = sum_margin = 0.0
        for _, r in grp.iterrows():
            emps = [x.strip() for x in str(r.get("employee_names") or "").split(",") if x.strip()]
            n_eff = len([e for e in emps if TARGET_EMP == e or TARGET_EMP in e]) or len(emps) or 1
            amt    = float(r.get("amount") or 0)
            oid    = int(r["order_id"])
            tot    = float(total_map.get(oid, 0) or 0)
            base_m = float(margin_map.get(oid, 0) or 0)
            ratio  = (amt / tot) if tot else 0.0
            per_m  = (base_m * ratio) / n_eff if n_eff else 0.0
            per_s  = amt / n_eff if n_eff else amt
            sum_sales  += per_s
            sum_margin += per_m

            rows_out.append({
                "sales_id"       : int(r["id"]),
                "order_id"       : oid,
                "sales.amount"   : f"{amt:,.0f}",
                "order.total"    : f"{tot:,.0f}",
                "order.margin"   : f"{base_m:,.0f}",
                "비율(amt/tot)"  : round(ratio, 4),
                "인원수"         : n_eff,
                "직원매출"       : f"{per_s:,.0f}",
                "직원마진"       : f"{per_m:,.0f}",
                "employee_names" : r.get("employee_names",""),
            })

        out_df = pd.DataFrame(rows_out)
        pd.set_option("display.max_columns", None)
        pd.set_option("display.width", 260)
        print(out_df.to_string(index=False))
        print(f"\n  >> {tx_date} {TARGET_EMP} 당일 매출 합계 : {sum_sales:>12,.0f} 원")
        print(f"  >> {tx_date} {TARGET_EMP} 당일 마진 합계 : {sum_margin:>12,.0f} 원  <-- KPI 표시값")

        if abs(sum_margin - (-4371800)) < 500:
            print(f"  [확인] 집계 결과가 -4,371,800에 부합합니다.")
        else:
            print(f"  [참고] 이 날짜 마진 합계: {sum_margin:,.0f}")
        print()


if __name__ == "__main__":
    main()
