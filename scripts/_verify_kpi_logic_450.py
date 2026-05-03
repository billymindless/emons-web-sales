#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
주문 #450 KPI 마진 배분 로직 검증:
왜 실제 마진 감소(-3,460,000)와 4/27 KPI(-204,320)가 다른지 분석
"""
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
client = create_client(sup["url"].strip(), (sup.get("key") or sup.get("anon_key","")).strip())

# 주문 #450 현재 데이터
r = client.table("app_orders").select(
    "id, total_amount, cost_price, display_cost_amount, actual_margin"
).eq("id", 450).execute()
o = r.data[0]
total_amt  = float(o["total_amount"] or 0)
cost_price = float(o["cost_price"] or 0)
disp_cost  = float(o["display_cost_amount"] or 0)
actual_mg  = float(o["actual_margin"] or 0)

# 주문 #450 관련 sales 행 전체
r2 = client.table("sales").select(
    "id, transaction_date, amount, order_id, employee_names, note"
).execute()
df = pd.DataFrame(r2.data or [])
df["transaction_date"] = pd.to_datetime(df["transaction_date"], errors="coerce")
order450_sales = df[df["order_id"] == 450].sort_values("transaction_date")

print("=" * 65)
print("주문 #450 sales 행 전체")
print("=" * 65)
print(order450_sales[["id","transaction_date","employee_names","amount","note"]].to_string(index=False))
print()
print(f"  현재 total_amount  = {total_amt:>12,.0f}")
print(f"  현재 cost_price    = {cost_price:>12,.0f}")
print(f"  전시원가           = {disp_cost:>12,.0f}")
print(f"  현재 actual_margin = {actual_mg:>12,.0f}")
print()

# KPI 배분 시뮬레이션 (현재 값 기준)
print("=" * 65)
print("KPI 배분 시뮬레이션 (현재 actual_margin · total_amount 기준)")
print("공식: 배분마진 = (sales.amount / total_amount) × actual_margin")
print("=" * 65)
total_sum = 0.0
for _, row in order450_sales.iterrows():
    amt = float(row["amount"] or 0)
    ratio = amt / total_amt if total_amt else 0.0
    per_m = ratio * actual_mg
    total_sum += per_m
    dt = str(row["transaction_date"])[:10]
    print(f"  {dt}  id={int(row['id'])}  amount={amt:>12,.0f}  ratio={ratio:>7.4f}  배분마진={per_m:>12,.0f}")
print(f"  {'─'*62}")
print(f"  합계 배분마진 = {total_sum:,.0f}  (≈ actual_margin {actual_mg:,.0f})")
print()

# 4/26 원래 sales 행 분석
print("=" * 65)
print("핵심 분석: 4/26 sales 행과 원래 계약금액 비교")
print("=" * 65)
orig_total  = 4_547_000.0
orig_margin = 3_635_120.0

for _, row in order450_sales.iterrows():
    amt = float(row["amount"] or 0)
    dt = str(row["transaction_date"])[:10]
    if "04-26" in dt and amt > 0:
        ratio_now  = amt / total_amt if total_amt else 0.0
        margin_now = ratio_now * actual_mg
        ratio_orig = amt / orig_total
        margin_orig = ratio_orig * orig_margin
        diff = margin_now - margin_orig
        print(f"  4/26 sales.amount   = {amt:>12,.0f}")
        print(f"  원래 계약금액       = {orig_total:>12,.0f}")
        print(f"  현재 total_amount   = {total_amt:>12,.0f}")
        print()
        print(f"  [가격변경 전 기준]  ({amt:,.0f} / {orig_total:,.0f}) × {orig_margin:,.0f}")
        print(f"                      = {ratio_orig:.4f} × {orig_margin:,.0f} = {margin_orig:,.0f}원  ← 4/26에 적립되었어야 할 마진")
        print()
        print(f"  [가격변경 후 기준]  ({amt:,.0f} / {total_amt:,.0f}) × {actual_mg:,.0f}")
        print(f"                      = {ratio_now:.4f} × {actual_mg:,.0f} = {margin_now:,.0f}원  ← 현재 KPI 4/26 조회 시 마진")
        print()
        print(f"  → 4/26 KPI에서 줄어든 마진 = {diff:,.0f}원  (마진 감소가 4/26에 이미 반영됨)")

print()
print("=" * 65)
print("결론: 마진 감소 -3,460,000이 어떻게 배분되는가")
print("=" * 65)

# 4/26 마진 감소분
for _, row in order450_sales.iterrows():
    amt = float(row["amount"] or 0)
    dt  = str(row["transaction_date"])[:10]
    if "04-26" in dt and amt > 0:
        ratio_orig = amt / orig_total
        margin_orig = ratio_orig * orig_margin
        ratio_now  = amt / total_amt if total_amt else 0.0
        margin_now = ratio_now * actual_mg
        lost_in_426 = margin_now - margin_orig

        # 4/27 마진 기여분
        r_427 = order450_sales[order450_sales["transaction_date"].dt.strftime("%Y-%m-%d") == "2026-04-27"]
        if not r_427.empty:
            amt_427 = float(r_427.iloc[0]["amount"] or 0)
            ratio_427 = amt_427 / total_amt if total_amt else 0.0
            margin_427 = ratio_427 * actual_mg
        else:
            margin_427 = 0.0

        print(f"  실제 마진 감소액          = {orig_margin - actual_mg:>12,.0f}원  (3,635,120 → 175,120)")
        print()
        print(f"  KPI상 4/26 마진 감소분    = {lost_in_426:>12,.0f}원  (4/26 KPI 소급 반영)")
        print(f"  KPI상 4/27 마진 기여분    = {margin_427:>12,.0f}원  (4/27 당일 차감)")
        print(f"  {'─'*50}")
        print(f"  합계                      = {lost_in_426 + margin_427:>12,.0f}원  (≈ -{orig_margin - actual_mg:,.0f}원)")
        print()
        print(f"  즉, 마진 감소 -3,460,000은 KPI에서 이렇게 나뉩니다:")
        print(f"    4/26 KPI: {lost_in_426:,.0f}원 (4/26 결과 조회 시 이미 줄어있음)")
        print(f"    4/27 KPI: {margin_427:,.0f}원 (당일 차감으로 표시)")
        print()
        print(f"  4/27 KPI의 -204,320은 주문 #450 기여분({margin_427:,.0f}) + 다른 주문들의 합계입니다.")
        break
