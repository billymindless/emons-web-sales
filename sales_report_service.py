"""AI 세일즈 리포트 생성 서비스 (Phase 1: 데이터 집계 전용).

관련 계획서: ``docs/plans/AI_WEEKLY_SALES_REPORT_PLAN.md``

이 모듈은 순수 함수 집합입니다:
  - ``build_dataset(...)`` — Supabase 조회 + pandas 집계를 통합한 최상위 진입점
  - ``group_by_*`` — 각 차원별 그룹핑 헬퍼

Phase 1에서는 데이터 미리보기 UI 로 산출값을 검증합니다.
AI 요약 (:func:`call_gemini`) 과 문서화 (:func:`render_markdown`) 는 Phase 2에서 추가됩니다.

app.py 의 기존 함수와 로직을 최대한 재사용하되, ``import app`` 은 순환 참조를 유발하므로
필요한 헬퍼는 지역 import 로 처리합니다.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd


# ────────────────────────────────────────────────────────────────
# 라벨 정규화 맵 (app.py 의 마케팅 인사이트 로직과 일관성 유지)
# ────────────────────────────────────────────────────────────────
VISIT_REASON_NORMALIZE = {
    "매장외관": "매장외관(지나가다)",
    "재구매": "기존고객(재구매 AS)",
    "소개": "지인/업체소개",
    "광고(SNS 외)": "광고 및 홍보",
}

PURCHASE_REASON_NORMALIZE = {
    "교체(이사없이)": "단순 교체/추가",
    "이사": "일반 이사",
    "공동구매(입주, 가구쇼 등)": "신규 입주",
    "현대임직원할인": "기타(사무용 등)",
}


# ────────────────────────────────────────────────────────────────
# 기간 유틸
# ────────────────────────────────────────────────────────────────
def resolve_weekly_period(anchor: date | None = None) -> tuple[date, date]:
    """주간 리포트 기간: anchor 가 속한 주의 월~일 (ISO 주). anchor 미지정 시 지난 주.

    예: anchor=2026-07-11(토) → (2026-07-06(월), 2026-07-12(일))
    """
    if anchor is None:
        anchor = date.today() - timedelta(days=7)
    start = anchor - timedelta(days=anchor.weekday())  # Mon
    end = start + timedelta(days=6)  # Sun
    return start, end


def resolve_monthly_period(anchor: date | None = None) -> tuple[date, date]:
    """월간 리포트 기간: anchor 가 속한 달의 1일~말일. anchor 미지정 시 전월."""
    if anchor is None:
        today = date.today()
        first_this_month = today.replace(day=1)
        anchor = first_this_month - timedelta(days=1)  # 전월 말일
    start = anchor.replace(day=1)
    last_day = calendar.monthrange(anchor.year, anchor.month)[1]
    end = anchor.replace(day=last_day)
    return start, end


def prev_period(start: date, end: date) -> tuple[date, date]:
    """직전 동일 길이 기간 (WoW/MoM). 예: (7/6-7/12) → (6/29-7/5)."""
    delta = (end - start).days + 1
    return start - timedelta(days=delta), start - timedelta(days=1)


def year_ago_period(start: date, end: date) -> tuple[date, date]:
    """1년 전 동일 기간 (YoY). 윤년 2/29 는 2/28 로 안전 처리."""
    def _shift(d: date) -> date:
        try:
            return d.replace(year=d.year - 1)
        except ValueError:  # 2/29 → 2/28
            return d.replace(year=d.year - 1, day=28)
    return _shift(start), _shift(end)


# ────────────────────────────────────────────────────────────────
# Supabase 조회 (app.py 함수 재사용)
# ────────────────────────────────────────────────────────────────
def _get_client():
    """app.py 의 Supabase 싱글톤 재사용."""
    from app import get_supabase_client
    client, err = get_supabase_client()
    if err or not client:
        return None
    return client


def _fetch_orders(store_keys: list[str], start: date, end: date) -> pd.DataFrame:
    """app_orders 조회. delivery_date 기준 기간 필터.

    delivery_date 를 기준으로 하는 이유: 매출·마진 산정 시점이 배송일이며
    ``sales`` 테이블·``payments`` 와 정합성이 맞기 때문 (app.py 의 대시보드 KPI 관례).
    """
    client = _get_client()
    if client is None or not store_keys:
        return pd.DataFrame()
    cols = ("id, db_filename, customer_id, employee_names, order_date, "
            "delivery_date, category, cost_price, total_amount, visit_reason, "
            "purchase_reason, actual_margin, display_sales_amount, "
            "display_cost_amount, balance_status")
    try:
        q = client.table("app_orders").select(cols)\
            .in_("db_filename", store_keys)\
            .gte("delivery_date", start.isoformat())\
            .lte("delivery_date", end.isoformat())
        r = q.execute()
        rows = (r.data or []) if hasattr(r, "data") else []
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _fetch_sales(store_keys: list[str], start: date, end: date) -> pd.DataFrame:
    """sales (매출 원장) 조회 — 순매출 기준."""
    client = _get_client()
    if client is None or not store_keys:
        return pd.DataFrame()
    try:
        q = client.table("sales").select("*")\
            .in_("db_filename", store_keys)\
            .gte("transaction_date", start.isoformat())\
            .lte("transaction_date", end.isoformat())
        r = q.execute()
        rows = (r.data or []) if hasattr(r, "data") else []
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _fetch_payments(store_keys: list[str], start: date, end: date) -> pd.DataFrame:
    client = _get_client()
    if client is None or not store_keys:
        return pd.DataFrame()
    try:
        q = client.table("app_payments").select(
            "id, order_id, db_filename, payment_date, amount, payment_method, card_company"
        )\
            .in_("db_filename", store_keys)\
            .gte("payment_date", start.isoformat())\
            .lte("payment_date", end.isoformat())
        r = q.execute()
        rows = (r.data or []) if hasattr(r, "data") else []
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _fetch_customers(store_names: list[str]) -> pd.DataFrame:
    """app_customers.store_name 기준 조회. 지역·건물명 컬럼 포함."""
    client = _get_client()
    if client is None or not store_names:
        return pd.DataFrame()
    try:
        r = client.table("app_customers").select(
            "id, store_name, name, phone1, address, sigungu, bname, road_name, building_name"
        ).in_("store_name", store_names).execute()
        rows = (r.data or []) if hasattr(r, "data") else []
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _fetch_leads(store_names: list[str], start: date, end: date) -> pd.DataFrame:
    client = _get_client()
    if client is None or not store_names:
        return pd.DataFrame()
    try:
        r = client.table("app_leads").select("*")\
            .in_("store_name", store_names)\
            .gte("created_at", f"{start.isoformat()}T00:00:00")\
            .lte("created_at", f"{end.isoformat()}T23:59:59").execute()
        rows = (r.data or []) if hasattr(r, "data") else []
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _store_keys_and_names(store_key: str) -> tuple[list[str], list[str], str]:
    """store_key ('all' | db_filename) → (db_filenames, store_names, display_name)."""
    from app import _get_supabase_stores_list  # noqa: WPS433
    stores = _get_supabase_stores_list() or []
    if store_key == "all":
        keys = [s["db_filename"] for s in stores if s.get("db_filename")]
        names = [s["store_name"] for s in stores if s.get("store_name")]
        return keys, names, "전 매장 통합"
    for s in stores:
        if s.get("db_filename") == store_key:
            return [store_key], [s.get("store_name") or ""], (s.get("store_name") or store_key)
    return [store_key], [], store_key


# ────────────────────────────────────────────────────────────────
# 집계 함수 (차원별)
# ────────────────────────────────────────────────────────────────
def _to_num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0)


def compute_kpi(sales: pd.DataFrame, orders: pd.DataFrame, payments: pd.DataFrame) -> dict:
    """핵심 KPI 계산.
      - sales_amount: sales.amount 합 (순매출, 조정·반품 반영)
      - sales_count: 주문 건수 (app_orders)
      - aov: 판매건수 > 0 인 경우 sales_amount / sales_count
      - margin_rate: (total_amount - cost_price - display_cost_amount) / total_amount
      - payments_amount: app_payments.amount 합
    """
    sales_amount = int(_to_num(sales.get("amount", pd.Series(dtype=float))).sum()) if not sales.empty else 0
    sales_count = int(len(orders))
    aov = int(round(sales_amount / sales_count)) if sales_count > 0 else 0

    if not orders.empty:
        _tot = _to_num(orders.get("total_amount", pd.Series(dtype=float)))
        _cost = _to_num(orders.get("cost_price", pd.Series(dtype=float)))
        _display_cost = _to_num(orders.get("display_cost_amount", pd.Series(dtype=float)))
        margin_amount = int((_tot - _cost - _display_cost).sum())
        total_amount_sum = int(_tot.sum())
        margin_rate = (margin_amount / total_amount_sum) if total_amount_sum > 0 else 0.0
    else:
        margin_amount = 0
        margin_rate = 0.0

    payments_amount = int(_to_num(payments.get("amount", pd.Series(dtype=float))).sum()) if not payments.empty else 0

    return {
        "sales_amount": sales_amount,
        "sales_count": sales_count,
        "aov": aov,
        "margin_rate": round(margin_rate, 4),
        "margin_amount": margin_amount,
        "payments_amount": payments_amount,
    }


def diff_pct(current: float, prev: float) -> float | None:
    """전기 대비 증감률 (%). 전기 값이 0/미존재면 None."""
    try:
        c, p = float(current), float(prev)
    except (TypeError, ValueError):
        return None
    if p == 0:
        return None
    return round((c - p) / p * 100.0, 2)


def group_by_employee(sales: pd.DataFrame, orders: pd.DataFrame, top: int = 5) -> list[dict]:
    """직원별 매출·마진·건수 (Top N).
    sales.employee_names(콤마 구분) 를 1/n 배분하여 순매출 집계.
    """
    if sales.empty:
        return []
    df = sales.copy()
    if "employee_names" not in df.columns:
        df["employee_names"] = ""
    df["_names"] = df["employee_names"].fillna("").astype(str).str.strip()
    df = df[df["_names"] != ""]
    if df.empty:
        return []
    df["_amount"] = _to_num(df["amount"])
    rows: list[dict] = []
    for _, r in df.iterrows():
        names = [n.strip() for n in r["_names"].split(",") if n.strip()]
        if not names:
            continue
        share = float(r["_amount"]) / len(names)
        for n in names:
            rows.append({"name": n, "sales": share})
    if not rows:
        return []
    grp = pd.DataFrame(rows).groupby("name", as_index=False)["sales"].sum()
    # 건수(주문 기준) — 주문의 employee_names 에 이름이 포함된 건수
    counts = {}
    if not orders.empty and "employee_names" in orders.columns:
        for _, orow in orders.iterrows():
            _en = str(orow.get("employee_names") or "")
            for n in [x.strip() for x in _en.split(",") if x.strip()]:
                counts[n] = counts.get(n, 0) + 1
    grp["count"] = grp["name"].map(counts).fillna(0).astype(int)
    grp["sales"] = grp["sales"].round().astype(int)
    grp = grp.sort_values("sales", ascending=False).head(top)
    return grp.to_dict(orient="records")


def group_by_region(orders: pd.DataFrame, customers: pd.DataFrame, top: int = 5) -> list[dict]:
    """시군구별 매출·건수."""
    if orders.empty or customers.empty:
        return []
    cust = customers[["id", "sigungu"]].rename(columns={"id": "customer_id"})
    df = orders.merge(cust, on="customer_id", how="left")
    df["sigungu"] = df["sigungu"].fillna("(지역 미기입)")
    df["_amount"] = _to_num(df["total_amount"])
    grp = df.groupby("sigungu", as_index=False).agg(sales=("_amount", "sum"), count=("id", "count"))
    grp["sales"] = grp["sales"].round().astype(int)
    grp = grp.sort_values("sales", ascending=False).head(top)
    return grp.rename(columns={"sigungu": "region"}).to_dict(orient="records")


def group_by_building(orders: pd.DataFrame, customers: pd.DataFrame, top: int = 10) -> list[dict]:
    """건물명(아파트/오피스텔) 별 매출·건수."""
    if orders.empty or customers.empty:
        return []
    cust = customers[["id", "building_name"]].rename(columns={"id": "customer_id"})
    df = orders.merge(cust, on="customer_id", how="left")
    df["building_name"] = df["building_name"].fillna("").astype(str).str.strip()
    df = df[df["building_name"] != ""]
    if df.empty:
        return []
    df["_amount"] = _to_num(df["total_amount"])
    grp = df.groupby("building_name", as_index=False).agg(sales=("_amount", "sum"), count=("id", "count"))
    grp["sales"] = grp["sales"].round().astype(int)
    grp = grp.sort_values("sales", ascending=False).head(top)
    return grp.rename(columns={"building_name": "name"}).to_dict(orient="records")


def group_by_category(orders: pd.DataFrame, top: int = 10) -> list[dict]:
    """카테고리별 건수·비중 (콤마 분리).
    금액은 주문 단위이므로 카테고리 차원 매출은 중복 위험 → 건수만 집계.
    """
    if orders.empty or "category" not in orders.columns:
        return []
    cat_series = orders["category"].fillna("").astype(str)
    counts: dict[str, int] = {}
    for cell in cat_series:
        for c in [x.strip() for x in cell.split(",") if x.strip()]:
            counts[c] = counts.get(c, 0) + 1
    if not counts:
        return []
    total = sum(counts.values())
    rows = [{"category": k, "count": v, "share_pct": round(v / total * 100, 1)} for k, v in counts.items()]
    rows.sort(key=lambda x: x["count"], reverse=True)
    return rows[:top]


def group_by_visit_reason(orders: pd.DataFrame, top: int = 7) -> list[dict]:
    if orders.empty or "visit_reason" not in orders.columns:
        return []
    df = orders.copy()
    df["visit_reason"] = df["visit_reason"].fillna("미기입").replace(VISIT_REASON_NORMALIZE)
    df["_amount"] = _to_num(df["total_amount"])
    grp = df.groupby("visit_reason", as_index=False).agg(count=("id", "count"), sales=("_amount", "sum"))
    grp["sales"] = grp["sales"].round().astype(int)
    total_count = grp["count"].sum()
    grp["share_pct"] = (grp["count"] / total_count * 100).round(1) if total_count > 0 else 0
    grp = grp.sort_values("count", ascending=False).head(top)
    return grp.to_dict(orient="records")


def group_by_purchase_reason(orders: pd.DataFrame, top: int = 7) -> list[dict]:
    if orders.empty or "purchase_reason" not in orders.columns:
        return []
    df = orders.copy()
    df["purchase_reason"] = df["purchase_reason"].fillna("미기입").replace(PURCHASE_REASON_NORMALIZE)
    df["_amount"] = _to_num(df["total_amount"])
    grp = df.groupby("purchase_reason", as_index=False).agg(count=("id", "count"), sales=("_amount", "sum"))
    grp["sales"] = grp["sales"].round().astype(int)
    total_count = grp["count"].sum()
    grp["share_pct"] = (grp["count"] / total_count * 100).round(1) if total_count > 0 else 0
    grp = grp.sort_values("count", ascending=False).head(top)
    return grp.to_dict(orient="records")


def visit_purchase_matrix_top(orders: pd.DataFrame, top: int = 5) -> list[dict]:
    """방문 경로 × 구매 이유 조합 Top N."""
    if orders.empty:
        return []
    if "visit_reason" not in orders.columns or "purchase_reason" not in orders.columns:
        return []
    df = orders.copy()
    df["visit_reason"] = df["visit_reason"].fillna("미기입").replace(VISIT_REASON_NORMALIZE)
    df["purchase_reason"] = df["purchase_reason"].fillna("미기입").replace(PURCHASE_REASON_NORMALIZE)
    df["_amount"] = _to_num(df["total_amount"])
    grp = df.groupby(["visit_reason", "purchase_reason"], as_index=False).agg(
        count=("id", "count"), sales=("_amount", "sum"))
    grp["sales"] = grp["sales"].round().astype(int)
    grp = grp.sort_values("count", ascending=False).head(top)
    return grp.to_dict(orient="records")


def group_by_payment_method(payments: pd.DataFrame) -> list[dict]:
    if payments.empty:
        return []
    df = payments.copy()
    df["payment_method"] = df["payment_method"].fillna("(미기입)")
    df["_amount"] = _to_num(df["amount"])
    grp = df.groupby("payment_method", as_index=False).agg(amount=("_amount", "sum"), count=("id", "count"))
    grp["amount"] = grp["amount"].round().astype(int)
    grp = grp.sort_values("amount", ascending=False)
    return grp.to_dict(orient="records")


def compute_lead_kpi(leads: pd.DataFrame) -> dict:
    """리드 KPI: 신규 리드 수, 계약 완료, 전환율, 평균 클로징 기간, 사후관리 성실도."""
    if leads.empty:
        return {"new_leads": 0, "closed_deals": 0, "conversion_rate": 0.0,
                "avg_closing_days": None, "followup_rate": 0.0}
    n = int(len(leads))
    closed_df = leads[leads.get("lead_stage", pd.Series(dtype=str)) == "4_계약완료"]
    closed = int(len(closed_df))
    conv = round(closed / n, 4) if n > 0 else 0.0

    # 평균 클로징 기간 (created_at → converted_at)
    avg_days: float | None = None
    if not closed_df.empty and {"created_at", "converted_at"}.issubset(closed_df.columns):
        try:
            _c = pd.to_datetime(closed_df["created_at"], errors="coerce", utc=True)
            _v = pd.to_datetime(closed_df["converted_at"], errors="coerce", utc=True)
            _delta = (_v - _c).dt.total_seconds() / 86400.0
            _delta = _delta.dropna()
            if len(_delta) > 0:
                avg_days = round(float(_delta.mean()), 1)
        except Exception:
            avg_days = None

    # 사후관리: followup_done True 또는 contact_memo 있음
    followup_ok = 0
    if "followup_done" in leads.columns:
        followup_ok += int(leads["followup_done"].fillna(False).astype(bool).sum())
    if "contact_memo" in leads.columns:
        _has_memo = leads["contact_memo"].fillna("").astype(str).str.strip() != ""
        followup_ok = max(followup_ok, int(_has_memo.sum()))
    followup_rate = round(followup_ok / n, 4) if n > 0 else 0.0

    return {
        "new_leads": n,
        "closed_deals": closed,
        "conversion_rate": conv,
        "avg_closing_days": avg_days,
        "followup_rate": followup_rate,
    }


def collect_risks(store_keys: list[str], today: date) -> dict:
    """미수금 D-10 이내 · 전체 미수금 집계 (기간 무관, 스냅샷).

    app_orders.balance_status 대신 sales.unpaid_balance 를 신뢰 가능한 소스로 사용.
    """
    client = _get_client()
    if client is None or not store_keys:
        return {"unpaid_d10": [], "total_unpaid": 0}
    try:
        r = client.table("app_orders").select(
            "id, db_filename, customer_id, delivery_date, total_amount, balance_status"
        ).in_("db_filename", store_keys).execute()
        orders = pd.DataFrame((r.data or []))
        if orders.empty:
            return {"unpaid_d10": [], "total_unpaid": 0}
    except Exception:
        return {"unpaid_d10": [], "total_unpaid": 0}
    # 잔금 있는 주문
    try:
        r2 = client.table("app_payments").select("order_id, amount")\
            .in_("db_filename", store_keys).execute()
        pays = pd.DataFrame((r2.data or []))
    except Exception:
        pays = pd.DataFrame()

    orders["_tot"] = _to_num(orders["total_amount"])
    if not pays.empty:
        pays["_amt"] = _to_num(pays["amount"])
        paid = pays.groupby("order_id", as_index=False)["_amt"].sum().rename(columns={"_amt": "paid"})
        orders = orders.merge(paid, left_on="id", right_on="order_id", how="left")
        orders["paid"] = orders["paid"].fillna(0)
    else:
        orders["paid"] = 0
    orders["balance"] = orders["_tot"] - orders["paid"]
    unpaid = orders[orders["balance"] > 0].copy()
    total_unpaid = int(unpaid["balance"].sum()) if not unpaid.empty else 0

    # D-10 (배송일이 오늘 이전, 오늘 - 10 이내)
    unpaid["delivery_date"] = pd.to_datetime(unpaid["delivery_date"], errors="coerce").dt.date
    _d10 = unpaid[(unpaid["delivery_date"].notna()) &
                  (unpaid["delivery_date"] >= today - timedelta(days=10)) &
                  (unpaid["delivery_date"] <= today)].copy()
    _d10 = _d10.sort_values("balance", ascending=False).head(20)
    unpaid_d10 = [
        {
            "order_id": int(r["id"]),
            "customer_id": int(r["customer_id"]) if pd.notna(r["customer_id"]) else None,
            "delivery_date": r["delivery_date"].isoformat() if pd.notna(r["delivery_date"]) else None,
            "balance": int(r["balance"]),
        }
        for _, r in _d10.iterrows()
    ]
    return {"unpaid_d10": unpaid_d10, "total_unpaid": total_unpaid}


# ────────────────────────────────────────────────────────────────
# 최상위 진입점
# ────────────────────────────────────────────────────────────────
def build_dataset(period_type: str, start: date, end: date, store_key: str) -> dict:
    """기간·매장 지정 → 리포트용 원본 데이터 dict 반환.

    Args:
        period_type: 'weekly' | 'monthly'
        start: 기간 시작일 (포함)
        end: 기간 종료일 (포함)
        store_key: 'all' 또는 db_filename

    Returns:
        계획서 §6 스키마에 맞는 dict (ai_summary 제외 — Phase 2에서 추가).
    """
    store_keys, store_names, display_name = _store_keys_and_names(store_key)

    orders = _fetch_orders(store_keys, start, end)
    sales = _fetch_sales(store_keys, start, end)
    payments = _fetch_payments(store_keys, start, end)
    customers = _fetch_customers(store_names)
    leads = _fetch_leads(store_names, start, end)

    kpi_now = compute_kpi(sales, orders, payments)

    # WoW/MoM (직전 동일 길이 기간)
    prev_start, prev_end = prev_period(start, end)
    p_orders = _fetch_orders(store_keys, prev_start, prev_end)
    p_sales = _fetch_sales(store_keys, prev_start, prev_end)
    p_payments = _fetch_payments(store_keys, prev_start, prev_end)
    kpi_prev = compute_kpi(p_sales, p_orders, p_payments)

    # YoY (있으면)
    yoy_start, yoy_end = year_ago_period(start, end)
    y_orders = _fetch_orders(store_keys, yoy_start, yoy_end)
    y_sales = _fetch_sales(store_keys, yoy_start, yoy_end)
    y_payments = _fetch_payments(store_keys, yoy_start, yoy_end)
    kpi_yoy = compute_kpi(y_sales, y_orders, y_payments)
    _has_yoy_data = (kpi_yoy["sales_amount"] > 0 or kpi_yoy["sales_count"] > 0)

    kpi_now["prev_period"] = {
        "start_date": prev_start.isoformat(),
        "end_date": prev_end.isoformat(),
        "sales_amount": kpi_prev["sales_amount"],
        "sales_count": kpi_prev["sales_count"],
        "aov": kpi_prev["aov"],
        "margin_rate": kpi_prev["margin_rate"],
        "payments_amount": kpi_prev["payments_amount"],
        "sales_diff_pct": diff_pct(kpi_now["sales_amount"], kpi_prev["sales_amount"]),
        "aov_diff_pct": diff_pct(kpi_now["aov"], kpi_prev["aov"]),
    }
    kpi_now["prev_year"] = ({
        "start_date": yoy_start.isoformat(),
        "end_date": yoy_end.isoformat(),
        "sales_amount": kpi_yoy["sales_amount"],
        "sales_count": kpi_yoy["sales_count"],
        "aov": kpi_yoy["aov"],
        "margin_rate": kpi_yoy["margin_rate"],
        "payments_amount": kpi_yoy["payments_amount"],
        "sales_diff_pct": diff_pct(kpi_now["sales_amount"], kpi_yoy["sales_amount"]),
        "aov_diff_pct": diff_pct(kpi_now["aov"], kpi_yoy["aov"]),
    } if _has_yoy_data else None)

    dataset = {
        "period_type": period_type,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "store_key": store_key,
        "store_name": display_name,
        "generated_at": pd.Timestamp.now(tz="Asia/Seoul").isoformat(),
        "kpi": kpi_now,
        "by_employee": group_by_employee(sales, orders),
        "by_region": group_by_region(orders, customers),
        "by_building": group_by_building(orders, customers),
        "by_category": group_by_category(orders),
        "by_visit_reason": group_by_visit_reason(orders),
        "by_purchase_reason": group_by_purchase_reason(orders),
        "visit_purchase_matrix_top5": visit_purchase_matrix_top(orders),
        "by_payment_method": group_by_payment_method(payments),
        "leads": compute_lead_kpi(leads),
        "risks": collect_risks(store_keys, end),
        # Phase 2 에서 추가
        "ai_summary": None,
    }
    return dataset


__all__ = [
    "resolve_weekly_period",
    "resolve_monthly_period",
    "prev_period",
    "year_ago_period",
    "compute_kpi",
    "diff_pct",
    "group_by_employee",
    "group_by_region",
    "group_by_building",
    "group_by_category",
    "group_by_visit_reason",
    "group_by_purchase_reason",
    "visit_purchase_matrix_top",
    "group_by_payment_method",
    "compute_lead_kpi",
    "collect_risks",
    "build_dataset",
]
