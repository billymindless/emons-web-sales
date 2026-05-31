#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""주문 #454 상세 + 모든 sales 행 + audit_log (금액 변경 이력) 조회."""
from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
with open(ROOT / ".streamlit" / "secrets.toml", "rb") as f:
    data = tomllib.load(f)
sup = data["supabase"]
from supabase import create_client
client = create_client(sup["url"].strip(), (sup.get("service_role_key") or sup.get("key") or "").strip())

OID = 454

print("=" * 80)
print(f"[1] app_orders id={OID}")
print("=" * 80)
r = client.table("app_orders").select("*").eq("id", OID).execute()
df = pd.DataFrame(r.data or [])
if df.empty:
    print("주문 없음")
    raise SystemExit
o = df.iloc[0].to_dict()
for k in [
    "id", "customer_id", "db_filename", "employee_names",
    "order_date", "delivery_date", "category",
    "total_amount", "cost_price", "actual_margin",
    "display_sales_amount", "display_cost_amount", "balance_status",
]:
    print(f"  {k:>22}: {o.get(k)}")

cust_id = o.get("customer_id")
if cust_id:
    rc = client.table("app_customers").select("id, name, phone1").eq("id", int(cust_id)).execute()
    if rc.data:
        c = rc.data[0]
        print(f"  {'customer':>22}: {c.get('name')} ({c.get('phone1')})")

print()
print("=" * 80)
print(f"[2] sales (order_id={OID}) 전체 행")
print("=" * 80)
rs = client.table("sales").select(
    "id, transaction_date, amount, employee_names, note, created_at, unpaid_balance"
).eq("order_id", OID).order("transaction_date").execute()
ds = pd.DataFrame(rs.data or [])
if ds.empty:
    print("sales 없음")
else:
    for _, row in ds.iterrows():
        print(f"  sales_id={int(row['id']):>4}  tx={str(row['transaction_date'])[:10]}  "
              f"amount={float(row['amount'] or 0):>14,.0f}  emp={row.get('employee_names')!r}")
        if row.get("note"):
            print(f"      note: {row['note']}")
        if row.get("created_at"):
            print(f"      created_at: {row['created_at']}")
    total = ds["amount"].fillna(0).astype(float).sum()
    print(f"\n  >>> sales amount 합계 = {total:,.0f}원 (현재 total_amount = {o.get('total_amount')})")

print()
print("=" * 80)
print(f"[3] app_audit_logs (Order, target_id={OID}) 변경 이력")
print("=" * 80)
try:
    ra = (
        client.table("app_audit_logs")
        .select("*")
        .eq("target_table", "Order")
        .eq("target_id", OID)
        .order("changed_at")
        .execute()
    )
    da = pd.DataFrame(ra.data or [])
    if da.empty:
        print("audit_log 없음")
    else:
        for _, row in da.iterrows():
            print(f"  {str(row.get('changed_at',''))[:19]} | {row.get('field_name')}: "
                  f"{row.get('old_value')} → {row.get('new_value')} | reason={row.get('reason')!r}")
except Exception as e:
    print(f"audit_log 조회 실패(무시): {e}")
