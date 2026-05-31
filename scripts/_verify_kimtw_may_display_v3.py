#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
옵션 A2 (전시품 분배 트리거 = order_date 기준) 검증.
모모 매장(store_1.db) 5월 KPI: 9명 직원 전시품 판매액 시뮬 vs 직원 보고.

규칙:
  a) order_date in [START, END] → +base_d/n × 1회 (정상 신규 계약).
     같은 달 전체 취소(net == -total)면 -base_d/n.
  b) order_date 외 + (net == -total OR total == 0 이고 net<0) → -base_d/n × 1회 (전체 취소).
  c) 그 외(다른 달 계약 + 단순 금액수정 delta) → 0.
  d) sales note |__dm_d:{delta}| 가 있으면 +delta/n 추가 분배.
"""
from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore
import pandas as pd
from datetime import date

ROOT = Path(__file__).resolve().parents[1]
with open(ROOT / ".streamlit" / "secrets.toml", "rb") as f:
    data = tomllib.load(f)
sup = data["supabase"]
from supabase import create_client
client = create_client(sup["url"].strip(), (sup.get("service_role_key") or sup.get("key") or "").strip())

DB = "store_1.db"
START = date(2026, 5, 1)
END = date(2026, 5, 31)


def names(s):
    return [x.strip() for x in str(s or "").split(",") if x.strip()]


def parse_dm_d(note):
    s = str(note or "").strip()
    if "|__dm_d:" not in s:
        return None
    try:
        tail = s.split("|__dm_d:", 1)[1].split("|", 1)[0].strip()
        return float(tail)
    except (ValueError, IndexError):
        return None


# 1) orders (모모 매장 전체 — KPI 함수가 다른 달 계약 주문도 비교 가능하도록)
r_o = (
    client.table("app_orders")
    .select("id, employee_names, total_amount, display_sales_amount, order_date, balance_status")
    .eq("db_filename", DB)
    .execute()
)
df_o = pd.DataFrame(r_o.data or [])
df_o["order_date"] = pd.to_datetime(df_o["order_date"], errors="coerce")

# 2) 5월 sales (모모 매장 주문에 한정)
oids_all = sorted({int(x) for x in df_o["id"].dropna().astype(int).tolist()})
chunks = [oids_all[i:i + 80] for i in range(0, len(oids_all), 80)]
sales_rows = []
for ch in chunks:
    r_s = (
        client.table("sales")
        .select("id, order_id, transaction_date, amount, employee_names, note")
        .in_("order_id", ch)
        .gte("transaction_date", START.isoformat())
        .lte("transaction_date", END.isoformat())
        .execute()
    )
    sales_rows.extend(r_s.data or [])
df_s = pd.DataFrame(sales_rows)
df_s["amount"] = df_s["amount"].fillna(0).astype(float)
df_s["order_id"] = df_s["order_id"].astype(int)

# 3) 직원 보정 (주문의 최신 employee_names 우선)
oid_emp_order = df_o.set_index("id")["employee_names"].to_dict()
df_s["employee_names_eff"] = df_s["order_id"].map(
    lambda oid: ",".join(names(oid_emp_order.get(int(oid))))
)
mask_blank = df_s["employee_names_eff"].astype(str).str.strip() == ""
df_s.loc[mask_blank, "employee_names_eff"] = df_s.loc[mask_blank, "employee_names"].fillna("")

order_emp = df_s.groupby("order_id")["employee_names_eff"].first().to_dict()
order_net = df_s.groupby("order_id")["amount"].sum().to_dict()

# 주문별 dm_d 합
order_dm_d_sum: dict[int, float] = {}
for _, row in df_s.iterrows():
    v = parse_dm_d(row.get("note"))
    if v is not None:
        oid = int(row["order_id"])
        order_dm_d_sum[oid] = order_dm_d_sum.get(oid, 0.0) + v

total_map = df_o.set_index("id")["total_amount"].fillna(0).astype(float).to_dict()
disp_map = df_o.set_index("id")["display_sales_amount"].fillna(0).astype(float).to_dict()
od_map = {int(k): v.date() for k, v in df_o.set_index("id")["order_date"].dropna().items() if pd.notna(v)}

emp_share: dict[str, float] = {}
detail = []
for oid in df_s["order_id"].unique():
    oid = int(oid)
    base_d = float(disp_map.get(oid, 0) or 0)
    emps = names(order_emp.get(oid, ""))
    if not emps:
        continue
    n = len(emps)
    order_date = od_map.get(oid)
    net_amt = float(order_net.get(oid, 0) or 0)
    order_total = float(total_map.get(oid, 0) or 0)
    in_kpi = order_date is not None and START <= order_date <= END

    disp_per = 0.0
    if base_d > 0:
        if in_kpi:
            if net_amt < 0 and order_total > 0 and abs(net_amt) >= order_total - 1:
                disp_per = -base_d / n
            else:
                disp_per = base_d / n
        else:
            if net_amt < 0 and (order_total == 0 or (order_total > 0 and abs(net_amt + order_total) < 1)):
                disp_per = -base_d / n

    dm_d = order_dm_d_sum.get(oid, 0.0)
    if dm_d != 0:
        disp_per += dm_d / n

    if disp_per == 0:
        continue
    for e in emps:
        emp_share[e] = emp_share.get(e, 0.0) + disp_per
    detail.append({
        "oid": oid, "emps": ",".join(emps), "n": n,
        "order_date": order_date.isoformat() if order_date else None,
        "in_kpi": in_kpi, "total": int(order_total), "display": int(base_d),
        "net_5월": int(net_amt), "dm_d": int(dm_d), "per_emp_disp": int(disp_per),
    })

print("=" * 90)
print("옵션 A2 직원별 전시품 판매액 시뮬")
print("=" * 90)
sim = pd.DataFrame(
    [{"직원명": k, "옵션A2_시뮬": int(round(v))} for k, v in sorted(emp_share.items(), key=lambda x: -x[1])]
)
print(sim.to_string(index=False))

print()
print("=" * 90)
print("화면(이전 옵션 A) vs 옵션 A2 시뮬 비교")
print("=" * 90)
prev = {
    "최지빈": 1_655_000, "김정진": 1_894_000, "김태완": 2_299_000, "김나래": 1_336_000,
    "김연진": 1_836_000, "구나영": 0, "김효정": 216_000, "문지현": 0, "박성진": 0,
}
expect_kimtw = {"김태완": 3_761_000}
rows = []
for emp, prev_val in prev.items():
    sim_val = int(round(emp_share.get(emp, 0)))
    rows.append({"직원명": emp, "이전(옵션A)": prev_val, "옵션A2_시뮬": sim_val, "차이": sim_val - prev_val})
print(pd.DataFrame(rows).to_string(index=False))

print()
print("=" * 90)
print("주문별 분배 상세 (옵션 A2 시뮬)")
print("=" * 90)
print(pd.DataFrame(detail).sort_values("oid").to_string(index=False))

print()
print(">>> 김태완 5월 = ", int(round(emp_share.get("김태완", 0))), "(직원 보고 3,761,000)")
