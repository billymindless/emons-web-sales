#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
모모 매장 5월 KPI 화면(전 직원) 옵션 A 시뮬레이션 vs 화면 값 대조.
화면 값(이미지):
  최지빈 1,655,000 / 김정진 1,894,000 / 김태완 2,299,000 / 김나래 1,336,000 /
  김연진 1,836,000 / 구나영 0 / 김효정 216,000 / 문지현 0 / 박성진 0
옵션 A 시뮬: store_1 5월 sales가 1건이라도 있는 모든 주문을 대상으로
            display_sales_amount × 1/n × 주문당 1회 (net amount sign 기준).
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
client = create_client(sup["url"].strip(), (sup.get("service_role_key") or sup.get("key") or "").strip())

DB = "store_1.db"
START = "2026-05-01"
END = "2026-05-31"


def names(s):
    return [x.strip() for x in str(s or "").split(",") if x.strip()]


# 1) 모모 매장(store_1.db) 주문 (5월 KPI 기간 sales 가 있는 주문 대상)
r_o = (
    client.table("app_orders")
    .select("id, employee_names, total_amount, display_sales_amount, order_date, balance_status")
    .eq("db_filename", DB)
    .execute()
)
df_o = pd.DataFrame(r_o.data or [])
print(f"[orders] {DB} 전체: {len(df_o)} 건")

# 2) 5월 sales (모모 매장 주문에 한정)
oids_all = sorted({int(x) for x in df_o["id"].dropna().astype(int).tolist()})
# Supabase in_ 쿼리 80개씩 분할
chunks = [oids_all[i:i + 80] for i in range(0, len(oids_all), 80)]
sales_rows = []
for ch in chunks:
    r_s = (
        client.table("sales")
        .select("id, order_id, transaction_date, amount, employee_names, note")
        .in_("order_id", ch)
        .gte("transaction_date", START)
        .lte("transaction_date", END)
        .execute()
    )
    sales_rows.extend(r_s.data or [])
df_s = pd.DataFrame(sales_rows)
print(f"[sales] {START}~{END} {DB} 주문 매출 거래: {len(df_s)} 건")

if df_s.empty:
    print("5월 sales 없음.")
    raise SystemExit

# 3) 옵션 A 분배 시뮬레이션 (app.py _kpi_employee_totals_from_sales_slice 와 동일 로직)
df_s = df_s.copy()
df_s["amount"] = df_s["amount"].fillna(0).astype(float)
df_s["order_id"] = df_s["order_id"].astype(int)

# 주문의 최신 employee_names로 sales 행의 employee_names 덮기 (코드 동일)
oid_emp_order = df_o.set_index("id")["employee_names"].to_dict()


def emp_label(s):
    n = names(s)
    return ",".join(n)


df_s["employee_names_eff"] = df_s["order_id"].map(
    lambda oid: emp_label(oid_emp_order.get(int(oid)))
)
mask_blank = df_s["employee_names_eff"].astype(str).str.strip() == ""
df_s.loc[mask_blank, "employee_names_eff"] = df_s.loc[mask_blank, "employee_names"].fillna("")

# 주문별 net amount (5월 KPI 기간 내), employee_names (덮어쓴 후 첫 행)
order_net = df_s.groupby("order_id")["amount"].sum().to_dict()
order_emp = df_s.groupby("order_id")["employee_names_eff"].first().to_dict()

# 주문 정보 매핑
total_map = df_o.set_index("id")["total_amount"].fillna(0).astype(float).to_dict()
disp_map = df_o.set_index("id")["display_sales_amount"].fillna(0).astype(float).to_dict()

emp_share: dict[str, float] = {}
detail_rows = []
for oid, net_amt in order_net.items():
    base_d = float(disp_map.get(oid, 0) or 0)
    if base_d == 0:
        continue
    emps = names(order_emp.get(oid, ""))
    if not emps:
        continue
    n = len(emps)
    if net_amt > 0:
        per = base_d / n
    elif net_amt < 0:
        per = -base_d / n
    else:
        per = 0.0
    if per == 0:
        continue
    for e in emps:
        emp_share[e] = emp_share.get(e, 0.0) + per
    detail_rows.append({
        "order_id": oid,
        "emps": ",".join(emps),
        "n": n,
        "display": int(base_d),
        "net_amt(5월)": int(net_amt),
        "per_emp_disp": int(per),
    })

print()
print("=" * 90)
print("옵션 A 직원별 전시품 판매액 (시뮬)")
print("=" * 90)
sim_df = pd.DataFrame(
    [{"직원명": k, "옵션A_시뮬(원)": int(round(v))} for k, v in sorted(emp_share.items(), key=lambda x: -x[1])]
)
print(sim_df.to_string(index=False))

print()
print("=" * 90)
print("화면 값(이미지) 대조")
print("=" * 90)
screen = {
    "최지빈": 1_655_000, "김정진": 1_894_000, "김태완": 2_299_000, "김나래": 1_336_000,
    "김연진": 1_836_000, "구나영": 0, "김효정": 216_000, "문지현": 0, "박성진": 0,
}
rows = []
for emp, screen_val in screen.items():
    sim_val = int(round(emp_share.get(emp, 0)))
    rows.append({"직원명": emp, "화면값": screen_val, "옵션A_시뮬": sim_val, "차이(시뮬-화면)": sim_val - screen_val})
print(pd.DataFrame(rows).to_string(index=False))

print()
print("=" * 90)
print("주문별 분배 상세 (옵션 A 시뮬)")
print("=" * 90)
print(pd.DataFrame(detail_rows).sort_values("order_id").to_string(index=False))
