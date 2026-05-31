#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
김태완 5월 전시품 판매액 검증.
- 직원 보고: 박시은(580) 2,430,000 + 최수영(581) 240,000 + 정혜인(522) 480,000 + 김도순(698) 반반 611,000 = 3,761,000
- KPI 화면(옵션 A 적용 후): 김태완 전시품 판매액 = 2,299,000
- 차이 1,462,000 의 원천 추적.

확인 항목:
  1) 모모 매장 db_filename 식별
  2) 4건 주문(522, 580, 581, 698) 의 employee_names · total_amount · display_sales_amount
  3) 각 주문의 5월 sales 행 (transaction_date, amount, employee_names, note)
  4) 옵션 A 분배 시뮬레이션 (display × 1/n × 1회) → 김태완 분배액
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

with open(SECRETS, "rb") as f:
    data = tomllib.load(f)
sup = data["supabase"]
from supabase import create_client

client = create_client(
    sup["url"].strip(),
    (sup.get("service_role_key") or sup.get("key") or sup.get("anon_key", "")).strip(),
)

TARGET_OIDS = [522, 580, 581, 698]
TARGET_EMP = "김태완"
START = "2026-05-01"
END = "2026-05-31"

print("=" * 80)
print("[1] app_stores: 모모 매장 식별")
print("=" * 80)
r_st = client.table("app_stores").select("id, store_name, db_filename").execute()
df_st = pd.DataFrame(r_st.data or [])
print(df_st.to_string(index=False))
print()

print("=" * 80)
print(f"[2] app_orders: 대상 주문 {TARGET_OIDS}")
print("=" * 80)
r_ord = (
    client.table("app_orders")
    .select(
        "id, db_filename, order_date, delivery_date, employee_names, "
        "total_amount, cost_price, display_sales_amount, display_cost_amount, "
        "actual_margin, balance_status, customer_id"
    )
    .in_("id", TARGET_OIDS)
    .execute()
)
df_ord = pd.DataFrame(r_ord.data or [])
print(df_ord.to_string(index=False))
print()

cust_ids = sorted({int(x) for x in df_ord["customer_id"].dropna().tolist()})
r_c = (
    client.table("app_customers")
    .select("id, name, phone1")
    .in_("id", cust_ids)
    .execute()
)
df_c = pd.DataFrame(r_c.data or [])
print("[고객명 매핑]")
print(df_c.to_string(index=False))
print()

print("=" * 80)
print(f"[3] sales: 5월 ({START}~{END}) 대상 주문 매출 거래")
print("=" * 80)
r_s = (
    client.table("sales")
    .select("id, order_id, transaction_date, amount, employee_names, note")
    .in_("order_id", TARGET_OIDS)
    .gte("transaction_date", START)
    .lte("transaction_date", END)
    .order("transaction_date")
    .execute()
)
df_s = pd.DataFrame(r_s.data or [])
print(df_s.to_string(index=False))
print()

print("=" * 80)
print(f"[3b] sales: 대상 주문의 전체 sales 행 (5월 외 포함)")
print("=" * 80)
r_s_all = (
    client.table("sales")
    .select("id, order_id, transaction_date, amount, employee_names, note")
    .in_("order_id", TARGET_OIDS)
    .order("transaction_date")
    .execute()
)
df_s_all = pd.DataFrame(r_s_all.data or [])
print(df_s_all.to_string(index=False))
print()


def _names(s):
    return [x.strip() for x in str(s or "").split(",") if x.strip()]


print("=" * 80)
print(f"[4] 옵션 A 분배 시뮬레이션 (5월 KPI 기간, 대상직원={TARGET_EMP!r})")
print("    공식: display_sales_amount × 1/n × 주문당 1회 (net amount sign 기준)")
print("=" * 80)

# 주문별 employee_names 우선 (코드 동작과 동일)
order_emp = df_ord.set_index("id")["employee_names"].to_dict()
order_disp = df_ord.set_index("id")["display_sales_amount"].fillna(0).astype(float).to_dict()
order_total = df_ord.set_index("id")["total_amount"].fillna(0).astype(float).to_dict()

# 주문별 5월 sales 합계 (net amount)
if df_s.empty:
    order_net = {}
else:
    order_net = (
        df_s.assign(amount=lambda d: d["amount"].fillna(0).astype(float))
        .groupby("order_id")["amount"]
        .sum()
        .to_dict()
    )

rows = []
for oid in TARGET_OIDS:
    emp_str = order_emp.get(oid) or ""
    emps = _names(emp_str)
    n = len(emps) if emps else 0
    base_d = float(order_disp.get(oid, 0) or 0)
    tot = float(order_total.get(oid, 0) or 0)
    net_amt = float(order_net.get(oid, 0) or 0)
    if base_d == 0 or n == 0:
        per_emp = 0.0
    elif net_amt > 0:
        per_emp = base_d / n
    elif net_amt < 0:
        per_emp = -base_d / n
    else:
        per_emp = 0.0
    target_in = TARGET_EMP in emps
    target_share = per_emp if target_in else 0.0
    rows.append(
        {
            "order_id": oid,
            "employees": emp_str,
            "n": n,
            "total_amount": int(tot),
            "display_sales_amount": int(base_d),
            "5월_sales_net_amt": int(net_amt),
            "주문당_1/n_분배액": int(per_emp),
            f"{TARGET_EMP}_포함?": target_in,
            f"{TARGET_EMP}_분배액": int(target_share),
        }
    )

df_sim = pd.DataFrame(rows)
print(df_sim.to_string(index=False))
print()
total_target = sum(r[f"{TARGET_EMP}_분배액"] for r in rows)
print(f"→ 시뮬레이션 합계 ({TARGET_EMP} 5월 전시품 판매액) = {total_target:,}원")
print(f"  직원 보고: 3,761,000원")
print(f"  KPI 화면:  2,299,000원")
print()

print("=" * 80)
print("[5] 김태완 5월 전시품이 등장하는 다른 주문 (참고: 같은 매장의 모든 주문 중)")
print("=" * 80)
# 모모 매장 db_filename 추정 (대상 주문 4건에서 공통)
db_fns = sorted({x for x in df_ord["db_filename"].dropna().tolist()})
print(f"대상 주문들의 db_filename = {db_fns}")
for db_fn in db_fns:
    r_more = (
        client.table("app_orders")
        .select("id, employee_names, display_sales_amount, total_amount, order_date")
        .eq("db_filename", db_fn)
        .gt("display_sales_amount", 0)
        .gte("order_date", START)
        .lte("order_date", END)
        .execute()
    )
    df_more = pd.DataFrame(r_more.data or [])
    if df_more.empty:
        print(f"  [{db_fn}] {START}~{END} 계약된 전시품 포함 주문 없음")
        continue
    df_more["has_target"] = df_more["employee_names"].fillna("").apply(
        lambda s: TARGET_EMP in _names(s)
    )
    only_target = df_more[df_more["has_target"]].copy()
    if only_target.empty:
        print(f"  [{db_fn}] {START}~{END} 김태완 포함 전시품 주문 없음")
    else:
        print(f"  [{db_fn}] {TARGET_EMP} 포함 5월 계약 전시품 주문:")
        print(only_target.to_string(index=False))
print()
