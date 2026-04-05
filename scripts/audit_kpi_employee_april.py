#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
대시보드 KPI와 동일한 배분식으로 특정 기간·직원의 sales→마진 기여를 행 단위로 출력.
사용: 프로젝트 루트에서
  python scripts/audit_kpi_employee_april.py [db_filename]
db_filename 생략 시 sales 행에 나온 db_filename(또는 order의 db_filename)별로 분리 출력.

주의: .streamlit/secrets.toml 의 Supabase 연결만 사용 (Streamlit 미실행).
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / ".streamlit" / "secrets.toml"

START = "2026-04-01"
END = "2026-04-05"
TARGET_EMP = "김나래"


def _load_supabase():
    if not SECRETS.exists():
        raise SystemExit(f"secrets 없음: {SECRETS}")
    with open(SECRETS, "rb") as f:
        data = tomllib.load(f)
    sup = data.get("supabase") or {}
    url = (sup.get("url") or "").strip()
    key = (sup.get("key") or sup.get("anon_key") or "").strip()
    sales_tenant = (sup.get("sales_tenant_column") or "").strip() or None
    if not url or not key:
        raise SystemExit("secrets [supabase] url/key 필요")
    from supabase import create_client

    return create_client(url, key), sales_tenant


def _sales_in_range(client, db_fn: str | None, sales_tenant: str | None) -> pd.DataFrame:
    q = client.table("sales").select("transaction_date, amount, order_id, employee_names")
    if sales_tenant and db_fn:
        q = q.eq(sales_tenant, db_fn)
    r = q.execute()
    df = pd.DataFrame(r.data or [])
    if df.empty:
        return df
    df["transaction_date"] = pd.to_datetime(df["transaction_date"], errors="coerce")
    df = df.dropna(subset=["transaction_date"])
    m = (df["transaction_date"].dt.date >= pd.Timestamp(START).date()) & (
        df["transaction_date"].dt.date <= pd.Timestamp(END).date()
    )
    return df.loc[m].copy()


def _row_names(s: str) -> list[str]:
    return [x.strip() for x in str(s or "").split(",") if x.strip()]


def _emp_in_row(emp: str, names: list[str]) -> bool:
    return any(emp == n or emp in n for n in names)


def _orders_for_ids(client, oids: list[int]) -> pd.DataFrame:
    if not oids:
        return pd.DataFrame()
    # in_ 배치 (너무 크면 쪼갬)
    chunks = [oids[i : i + 80] for i in range(0, len(oids), 80)]
    rows = []
    for ch in chunks:
        r = (
            client.table("app_orders")
            .select("id, db_filename, total_amount, actual_margin, display_sales_amount, employee_names")
            .in_("id", ch)
            .execute()
        )
        rows.extend(r.data or [])
    return pd.DataFrame(rows)


def audit(db_fn: str | None):
    client, sales_tenant = _load_supabase()
    s_df = _sales_in_range(client, db_fn, sales_tenant)
    if s_df.empty:
        print(f"{START}~{END} sales 없음 (tenant={sales_tenant!r}, db_fn={db_fn!r})")
        return

    # 대상 직원이 이름에 포함된 sales 행만
    hit = []
    for _, row in s_df.iterrows():
        emps = _row_names(row.get("employee_names"))
        if not emps:
            continue
        if _emp_in_row(TARGET_EMP, emps):
            hit.append(row)
    if not hit:
        print(f"{START}~{END} 구간에 employee_names 기준 '{TARGET_EMP}' sales 행 없음.")
        return

    h_df = pd.DataFrame(hit)
    oids = sorted({int(x) for x in h_df["order_id"].dropna().unique()})
    o_df = _orders_for_ids(client, oids)
    if o_df.empty:
        print("주문 조회 실패 또는 빈 결과")
        return

    total_map = o_df.set_index("id")["total_amount"].fillna(0).astype(float).to_dict()
    margin_map = o_df.set_index("id")["actual_margin"].fillna(0).astype(float).to_dict()
    display_map = o_df.set_index("id")["display_sales_amount"].fillna(0).astype(float).to_dict()

    print(f"=== {TARGET_EMP} | {START} ~ {END} | db_filename 필터={db_fn!r} tenant_col={sales_tenant!r} ===\n")
    lines = []
    sum_sales = sum_margin = 0.0
    for _, r in h_df.iterrows():
        emps = _row_names(r.get("employee_names"))
        n = len(emps)
        amt = float(r.get("amount") or 0)
        oid = int(r["order_id"])
        tot = float(total_map.get(oid, 0) or 0)
        base_m = float(margin_map.get(oid, 0) or 0)
        base_d = float(display_map.get(oid, 0) or 0)
        if TARGET_EMP not in emps:
            # 부분 일치 제거 후 정확히 타겟만
            emps = [e for e in emps if TARGET_EMP in e or e == TARGET_EMP]
            n = len(emps) if emps else 1
        per_s = amt / n if n else amt
        if tot != 0:
            ratio = amt / tot
            per_m = (base_m * ratio) / n
            per_d = (base_d * ratio) / n
            impl_rate = base_m / tot * 100 if tot else 0
        else:
            per_m = per_d = 0.0
            ratio = 0.0
            impl_rate = 0.0
        sum_sales += per_s
        sum_margin += per_m
        lines.append(
            {
                "tx": str(r["transaction_date"])[:10],
                "order_id": oid,
                "amount": amt,
                "n": n,
                "total_amt": tot,
                "actual_margin": base_m,
                "order_margin%": round(impl_rate, 2),
                "amt/total": round(ratio, 6) if tot else None,
                "per_emp_sales": round(per_s, 2),
                "per_emp_margin": round(per_m, 2),
                "employees_on_row": ",".join(_row_names(r.get("employee_names"))),
            }
        )

    out = pd.DataFrame(lines)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 200)
    print(out.to_string(index=False))
    print()
    print(f"합계 총판매액(직원분)={sum_sales:,.0f}  마진액(직원분)={sum_margin:,.0f}")
    if sum_sales != 0:
        print(f"집계 마진/매출 비율={sum_margin / sum_sales * 100:.2f}%")
    print()
    print("※ amt/total 이 비정상적으로 크면(>>1) total_amount와 sales.amount 불일치·미갱신 가능성 점검.")


if __name__ == "__main__":
    db_arg = sys.argv[1].strip() if len(sys.argv) > 1 else None
    audit(db_arg)
