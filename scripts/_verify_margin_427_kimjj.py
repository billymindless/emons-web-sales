#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
4/27 삼산 김정진 직원 마진 -204,320 검증 스크립트 (읽기 전용, 프로그램 미수정)
사용: python scripts/_verify_margin_427_kimjj.py
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

TARGET_DATE = "2026-04-27"
TARGET_EMP  = "김정진"
TARGET_STORE_KEYWORD = "삼산"

# ──────────────────────────────────────────
def load_client():
    with open(SECRETS, "rb") as f:
        data = tomllib.load(f)
    sup = data["supabase"]
    url = sup["url"].strip()
    key = (sup.get("key") or sup.get("anon_key", "")).strip()
    from supabase import create_client
    return create_client(url, key), sup

# ──────────────────────────────────────────
def main():
    client, sup = load_client()
    sales_tenant = (sup.get("sales_tenant_column") or "").strip() or None

    # 1) 삼산 매장 db_filename 조회
    stores_r = client.table("app_stores").select("id, store_name, db_filename").execute()
    stores = pd.DataFrame(stores_r.data or [])
    samsan_stores = stores[stores["store_name"].str.contains(TARGET_STORE_KEYWORD, na=False)]
    if samsan_stores.empty:
        # store_name에 없으면 db_filename 기준 탐색
        samsan_stores = stores[stores["db_filename"].str.contains(TARGET_STORE_KEYWORD, na=False)]
    if samsan_stores.empty:
        print("❌ 삼산 매장을 app_stores에서 찾지 못했습니다. 전체 매장 목록:")
        print(stores.to_string(index=False))
        return

    print("=== [1] 삼산 매장 정보 ===")
    print(samsan_stores.to_string(index=False))
    db_fn = samsan_stores.iloc[0]["db_filename"]
    print(f"\n▶ 사용할 db_filename: {db_fn}\n")

    # 2) 해당 날짜 sales 전체 조회 (삼산 tenant 필터)
    q = client.table("sales").select(
        "id, transaction_date, amount, order_id, employee_names, db_filename"
    )
    if sales_tenant:
        q = q.eq(sales_tenant, db_fn)
    sales_r = q.execute()
    s_df = pd.DataFrame(sales_r.data or [])
    s_df["transaction_date"] = pd.to_datetime(s_df["transaction_date"], errors="coerce")
    s_df = s_df.dropna(subset=["transaction_date"])
    s_day = s_df[s_df["transaction_date"].dt.strftime("%Y-%m-%d") == TARGET_DATE].copy()

    print(f"=== [2] 4/27 삼산 sales 전체 ({len(s_day)}건) ===")
    if not s_day.empty:
        print(s_day[["id","transaction_date","employee_names","amount","order_id"]].to_string(index=False))
    print()

    # 3) 김정진 관련 행만 필터
    def emp_match(names_str):
        names = [x.strip() for x in str(names_str or "").split(",") if x.strip()]
        return any(TARGET_EMP == n or TARGET_EMP in n for n in names)

    hit = s_day[s_day["employee_names"].apply(emp_match)].copy()
    print(f"=== [3] 김정진 포함 sales 행 ({len(hit)}건) ===")
    if hit.empty:
        print(f"  {TARGET_DATE} 삼산 sales에 '{TARGET_EMP}' 이름이 없습니다.")
        return
    print(hit[["id","transaction_date","employee_names","amount","order_id"]].to_string(index=False))
    print()

    # 4) 연관 주문 조회
    oids = sorted({int(x) for x in hit["order_id"].dropna().unique()})
    print(f"=== [4] 연관 주문 ID: {oids} ===")

    orders_r = client.table("app_orders").select(
        "id, db_filename, order_date, employee_names, total_amount, cost_price, "
        "display_sales_amount, display_cost_amount, actual_margin, balance_status, category"
    ).in_("id", oids).execute()
    o_df = pd.DataFrame(orders_r.data or [])
    print(o_df.to_string(index=False))
    print()

    # 5) 연관 결제 조회
    payments_r = client.table("app_payments").select(
        "id, order_id, payment_date, amount, payment_method, card_company, fee_amount"
    ).in_("order_id", oids).eq("db_filename", db_fn).execute()
    p_df = pd.DataFrame(payments_r.data or [])
    print(f"=== [5] 연관 결제 내역 ({len(p_df)}건) ===")
    print(p_df.to_string(index=False))
    print()

    # 6) 주문별 마진 재계산 (app.py 로직과 동일)
    print("=== [6] 주문별 마진 재계산 검증 ===")
    print(f"  공식: basic_margin = total_amount - (cost_price + display_cost_amount)")
    print(f"        actual_margin = basic_margin - SUM(fee_amount of payments)")
    print()

    grand_total_margin = 0.0
    for _, order in o_df.iterrows():
        oid = int(order["id"])
        total_amt    = float(order["total_amount"] or 0)
        cost_price   = float(order["cost_price"] or 0)
        disp_cost    = float(order["display_cost_amount"] or 0)
        basic_m      = total_amt - (cost_price + disp_cost)
        stored_margin = float(order["actual_margin"] or 0)

        ops = p_df[p_df["order_id"] == oid]
        sum_fee = float(ops["fee_amount"].fillna(0).sum())
        recalc_margin = basic_m - sum_fee

        print(f"  주문 #{oid}  [{order.get('order_date','')}] {order.get('category','')} / 담당: {order.get('employee_names','')}")
        print(f"    total_amount       = {total_amt:>12,.0f}")
        print(f"    cost_price         = {cost_price:>12,.0f}")
        print(f"    display_cost_amount= {disp_cost:>12,.0f}")
        print(f"    ──────────────────────────────────")
        print(f"    basic_margin       = {basic_m:>12,.0f}  (total - cost - disp_cost)")
        print(f"    SUM(fee_amount)    = {sum_fee:>12,.0f}  ({len(ops)}건 결제 수수료 합계)")
        print(f"    ──────────────────────────────────")
        print(f"    재계산 actual_margin= {recalc_margin:>12,.0f}")
        match_str = "[일치]" if abs(recalc_margin - stored_margin) < 1 else "[불일치!]"
        print(f"    저장된 actual_margin= {stored_margin:>12,.0f}  {match_str}")
        print()

        # 결제 상세
        if not ops.empty:
            print(f"    결제 상세:")
            for _, pm in ops.iterrows():
                print(f"      - {pm['payment_date']} | {pm['payment_method']} {pm.get('card_company','')} "
                      f"| 결제액={float(pm['amount'] or 0):,.0f} | 수수료={float(pm['fee_amount'] or 0):,.0f}")
        print()

    # 7) 당일 마진 배분 (KPI 대시보드 동일 로직)
    print("=== [7] 4/27 김정진 당일 마진 배분 계산 (KPI 로직과 동일) ===")
    total_map  = o_df.set_index("id")["total_amount"].fillna(0).astype(float).to_dict()
    margin_map = o_df.set_index("id")["actual_margin"].fillna(0).astype(float).to_dict()

    sum_sales = sum_margin = 0.0
    rows_out = []
    for _, r in hit.iterrows():
        emps = [x.strip() for x in str(r.get("employee_names") or "").split(",") if x.strip()]
        n = len(emps)
        amt  = float(r.get("amount") or 0)
        oid  = int(r["order_id"])
        tot  = float(total_map.get(oid, 0) or 0)
        base_m = float(margin_map.get(oid, 0) or 0)

        # 정확히 TARGET_EMP 포함 인원만 카운트
        matched = [e for e in emps if TARGET_EMP == e or TARGET_EMP in e]
        n_eff = len(matched) if matched else n

        ratio  = (amt / tot) if tot else 0.0
        per_m  = (base_m * ratio) / n_eff if n_eff else 0.0
        per_s  = amt / n_eff if n_eff else amt

        sum_sales  += per_s
        sum_margin += per_m

        rows_out.append({
            "sales_id"       : int(r["id"]),
            "order_id"       : oid,
            "sales.amount"   : amt,
            "order.total_amt": tot,
            "order.actual_mg": base_m,
            "비율(amt/total)" : round(ratio, 6),
            "담당인원수"       : n_eff,
            "직원담당매출"    : round(per_s, 0),
            "직원담당마진"    : round(per_m, 0),
            "employee_names" : r.get("employee_names",""),
        })

    out_df = pd.DataFrame(rows_out)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 250)
    print(out_df.to_string(index=False))
    print()
    print(f"  ▶ 합계 김정진 당일 매출  : {sum_sales:>12,.0f} 원")
    print(f"  ▶ 합계 김정진 당일 마진  : {sum_margin:>12,.0f} 원  ← 이것이 KPI 표시값")
    print()
    if abs(sum_margin - (-204320)) < 500:
        print("  [확인] 집계 결과가 -204,320에 부합합니다.")
    else:
        print(f"  [주의] 집계 결과({sum_margin:,.0f})와 -204,320 사이에 차이가 있습니다. 추가 확인 필요.")

if __name__ == "__main__":
    main()
