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
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


def _extract_region_from_address(address: Any) -> str | None:
    """주소 문자열에서 시군구(구/군) 추출. app.py extract_region() 미러링.

    우선순위: 구/군 (예: '남구', '울주군')  ← 지역 표에서는 시군구가 가장 유용.
    없으면 None (호출자가 '(지역 미기입)' 등으로 처리).
    """
    if not isinstance(address, str):
        return None
    s = address.strip()
    if not s:
        return None
    m = re.search(r"([가-힣]+[구군])\b", s)
    if m:
        return m.group(1)
    return None


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
_STANDALONE_CLIENT = None


def _get_client():
    """Supabase 클라이언트 반환.

    우선순위:
      1) app.get_supabase_client() (Streamlit 컨텍스트)
      2) 환경변수 SUPABASE_URL + SUPABASE_SERVICE_KEY 로 직접 초기화 (api.py/CLI 컨텍스트)
    """
    # 1) Streamlit 컨텍스트
    try:
        from app import get_supabase_client  # noqa: WPS433
        client, err = get_supabase_client()
        if not err and client:
            return client
    except Exception:
        pass

    # 2) 독립 실행 (api.py / cron)
    global _STANDALONE_CLIENT
    if _STANDALONE_CLIENT is not None:
        return _STANDALONE_CLIENT
    url = os.environ.get("SUPABASE_URL", "")
    key = (os.environ.get("SUPABASE_SERVICE_KEY", "")
           or os.environ.get("SUPABASE_KEY", ""))
    if not url or not key:
        return None
    try:
        from supabase import create_client  # noqa: WPS433
        _STANDALONE_CLIENT = create_client(url, key)
        return _STANDALONE_CLIENT
    except Exception as _e:
        logger.warning("standalone supabase client init failed: %s", _e)
        return None


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


def _sales_tenant_column() -> str | None:
    """secrets.supabase.sales_tenant_column 값 반환. 없으면 None (order_id 기반 격리 사용).

    app.py 의 _sales_tenant_column() 미러링 — Streamlit secrets 우선, 환경변수 fallback.
    """
    # Streamlit 컨텍스트
    try:
        import streamlit as st  # noqa: WPS433
        val = (st.secrets.get("supabase") or {}).get("sales_tenant_column")
        if val is not None and str(val).strip():
            return str(val).strip()
    except Exception:
        pass
    # 독립 실행: 환경변수 fallback
    env_val = os.environ.get("SUPABASE_SALES_TENANT_COLUMN", "").strip()
    return env_val or None


def _fetch_valid_order_ids(store_keys: list[str]) -> set[int]:
    """매장의 유효 order_id 집합 (sales 2차 격리용)."""
    client = _get_client()
    if client is None or not store_keys:
        return set()
    try:
        r = client.table("app_orders").select("id")\
            .in_("db_filename", store_keys).execute()
        rows = (r.data or []) if hasattr(r, "data") else []
        return {int(x["id"]) for x in rows if x.get("id") is not None}
    except Exception:
        return set()


def _fetch_sales(store_keys: list[str], start: date, end: date) -> pd.DataFrame:
    """sales (매출 원장) 조회 — 순매출 기준.

    app.py 의 load_sales_with_employees_cached + _filter_sales_to_store_orders 로직 미러링.
    - sales_tenant_column 이 secrets/env 에 설정돼 있으면 그 컬럼으로 서버 필터
    - 없으면 서버 필터 없이 transaction_date 로만 조회 후 pandas 로 order_id 교집합 필터
    """
    client = _get_client()
    if client is None or not store_keys:
        return pd.DataFrame()
    tenant_col = _sales_tenant_column()
    try:
        q = client.table("sales").select(
            "transaction_date, amount, order_id, note, employee_names"
        )
        if tenant_col:
            q = q.in_(tenant_col, store_keys)
        q = q.gte("transaction_date", start.isoformat())
        q = q.lte("transaction_date", end.isoformat())
        r = q.execute()
        rows = (r.data or []) if hasattr(r, "data") else []
    except Exception as _e:
        logger.warning("_fetch_sales query failed: %s", _e)
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    # 2차 격리 (tenant_col 없을 때)
    if not tenant_col and "order_id" in df.columns:
        valid_oids = _fetch_valid_order_ids(store_keys)
        if not valid_oids:
            return df.iloc[0:0].copy()
        _oid = pd.to_numeric(df["order_id"], errors="coerce")
        df = df[_oid.isin(valid_oids)].copy()
    return df


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


def _fetch_customers_by_ids(customer_ids: list[int]) -> pd.DataFrame:
    """app_customers 를 customer_id 목록으로 조회 (매장 필터 없이).

    이유: orders.customer_id 가 다른 매장의 store_name 으로 등록된 고객을 참조할 수 있음
    (이관·중복등록·재구매 등). store_name 필터로 조회하면 merge 실패로 지역이 '미기입' 처리됨.
    id in_ 방식은 매장 격리 안전 (orders 는 이미 매장 필터됨).
    """
    if not customer_ids:
        return pd.DataFrame()
    client = _get_client()
    if client is None:
        return pd.DataFrame()
    _cols = "id, store_name, name, phone1, address, sigungu, bname, road_name, building_name"
    # Supabase in_() 는 URL 길이 제한 있으므로 청크 단위로 조회
    _CHUNK = 500
    rows: list[dict] = []
    for i in range(0, len(customer_ids), _CHUNK):
        _batch = customer_ids[i:i + _CHUNK]
        try:
            r = client.table("app_customers").select(_cols).in_("id", _batch).execute()
            rows.extend((r.data or []) if hasattr(r, "data") else [])
        except Exception as _e:
            logger.warning("_fetch_customers_by_ids batch failed (%d ids): %s", len(_batch), _e)
    return pd.DataFrame(rows) if rows else pd.DataFrame()


# Backward-compat: 이전 사용처 (없음). 새 구현으로 위임.
def _fetch_customers(store_names: list[str]) -> pd.DataFrame:
    """(deprecated) store_name 기반 조회 — 지역 커버리지 저하로 build_dataset 에서는 미사용."""
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


def _fetch_building_aliases(store_names: list[str]) -> dict[str, str]:
    """app_building_aliases 조회 → {keyword: canonical_building_name} dict 반환.

    신규 입주 아파트처럼 카카오 도로명이 아직 없어 building_name 이 NULL 인 고객을
    관리자가 수동 매핑한 별칭 사전. group_by_building() 의 alias fallback 에 사용.
    테이블이 없거나 매장별 매핑이 하나도 없으면 빈 dict 반환 (조용히 무시).
    """
    if not store_names:
        return {}
    client = _get_client()
    if client is None:
        return {}
    try:
        r = (
            client.table("app_building_aliases")
            .select("keyword, building_name")
            .in_("store_name", store_names)
            .execute()
        )
        rows = (r.data or []) if hasattr(r, "data") else []
    except Exception as _e:
        logger.info("_fetch_building_aliases skipped (table missing or query failed): %s", _e)
        return {}
    result: dict[str, str] = {}
    for row in rows:
        _kw = (row.get("keyword") or "").strip()
        _bn = (row.get("building_name") or "").strip()
        if _kw and _bn:
            result[_kw] = _bn
    return result


def _fetch_stores_list(include_inactive: bool = False) -> list[dict]:
    """app_stores 조회 (app.py 의존성 없이 self-contained)."""
    # 1) Streamlit 캐시 활용
    try:
        from app import _get_supabase_stores_list  # noqa: WPS433
        _r = _get_supabase_stores_list(include_inactive=include_inactive)
        if _r:
            return _r
    except Exception:
        pass
    # 2) 독립 조회
    client = _get_client()
    if client is None:
        return []
    try:
        q = client.table("app_stores").select("db_filename, store_name, is_active")
        if not include_inactive:
            q = q.eq("is_active", True)
        r = q.execute()
        return (r.data or []) if hasattr(r, "data") else []
    except Exception:
        return []


def _store_keys_and_names(store_key: str) -> tuple[list[str], list[str], str]:
    """store_key ('all' | db_filename) → (db_filenames, store_names, display_name)."""
    stores = _fetch_stores_list()
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


def group_by_region(sales: pd.DataFrame, orders: pd.DataFrame, customers: pd.DataFrame, top: int = 5) -> list[dict]:
    """시군구별 순매출(sales 원장 기준)·건수.

    KPI 「순매출」과 동일한 sales.amount 를 sales.order_id → orders.customer_id →
    customers.sigungu 경로로 조인하여 집계한다 (기존에는 orders.total_amount를 사용해
    KPI 순매출과 합계가 어긋나는 문제가 있었음).

    지역 결정 우선순위:
      1) app_customers.sigungu (카카오 지오코딩 파생, 정확)
      2) app_customers.address 문자열에서 '~구/~군' 정규식 파생 (fallback)
      3) '(지역 미기입)'

    Top(N-1)개 지역 + 나머지를 합산한 '기타' 1행으로 구성하여, 표시되는 합계가
    항상 전체 순매출과 일치하도록 한다 (기존 단순 head(top) 은 나머지 지역이
    누락되어 합계가 실제보다 작게 나오는 문제가 있었음).
    """
    if sales.empty or orders.empty or customers.empty:
        return []
    _omap = (
        orders[["id", "customer_id"]].rename(columns={"id": "order_id"})
        if "customer_id" in orders.columns else pd.DataFrame(columns=["order_id", "customer_id"])
    )
    df = sales.merge(_omap, on="order_id", how="left")
    _need = ["id", "sigungu", "address"]
    _use_cols = [c for c in _need if c in customers.columns]
    cust = customers[_use_cols].rename(columns={"id": "customer_id"})
    df = df.merge(cust, on="customer_id", how="left")
    # sigungu 우선, 없으면 address 정규식 파생
    _sigungu = df["sigungu"].astype("object") if "sigungu" in df.columns else pd.Series([None] * len(df))
    _addr = df["address"] if "address" in df.columns else pd.Series([None] * len(df))
    _resolved = _sigungu.where(
        _sigungu.notna() & (_sigungu.astype(str).str.strip() != ""),
        _addr.map(_extract_region_from_address),
    )
    df["_region"] = _resolved.fillna("(지역 미기입)")
    df["_amount"] = _to_num(df["amount"])
    grp = df.groupby("_region", as_index=False).agg(sales=("_amount", "sum"), count=("order_id", "nunique"))
    grp["sales"] = grp["sales"].round().astype(int)
    grp = grp.sort_values("sales", ascending=False)
    if len(grp) > top:
        _head = grp.head(top - 1)
        _rest = grp.iloc[top - 1:]
        _other = pd.DataFrame([{
            "_region": f"기타 ({len(_rest)}개 지역)",
            "sales": int(_rest["sales"].sum()),
            "count": int(_rest["count"].sum()),
        }])
        grp = pd.concat([_head, _other], ignore_index=True)
    else:
        grp = grp.head(top)
    return grp.rename(columns={"_region": "region"}).to_dict(orient="records")


def group_by_building(
    orders: pd.DataFrame,
    customers: pd.DataFrame,
    top: int = 10,
    aliases: dict[str, str] | None = None,
) -> list[dict]:
    """건물명(아파트/오피스텔) 별 매출·건수.

    aliases({keyword: canonical_name}) 를 address 및 building_name 문자열에
    부분 일치로 적용한다. building_name 이 이미 채워진 행도 포함하여 전체 행에
    적용하므로, 카카오 지오코딩 결과가 다양하더라도 관리자 매핑으로 통합할 수 있다.
    긴 키워드가 짧은 키워드보다 우선 적용되며, 한 번 매핑된 행은 재매핑되지 않는다.
    """
    if orders.empty or customers.empty:
        return []
    _cust_cols = ["id", "building_name"]
    if "address" in customers.columns:
        _cust_cols.append("address")
    cust = customers[_cust_cols].rename(columns={"id": "customer_id"})
    df = orders.merge(cust, on="customer_id", how="left")
    df["building_name"] = df["building_name"].fillna("").astype(str).str.strip()

    if aliases and "address" in df.columns:
        # address 와 building_name 을 합쳐 검색 대상 텍스트 구성
        _addr = df["address"].fillna("").astype(str)
        _bn = df["building_name"].fillna("").astype(str)
        _search = _addr + " " + _bn
        # 아직 canonical 로 지정되지 않은 행 전체를 대상으로 alias 적용
        # (building_name 이 비어있어도, 이미 값이 있어도 모두 포함)
        already_mapped: pd.Series = pd.Series(False, index=df.index)
        # 긴 키워드 우선 매칭 (예: '달천이파크1차' 를 '달천이파크' 보다 먼저 시도)
        for kw in sorted(aliases.keys(), key=len, reverse=True):
            canonical = aliases[kw]
            hit = (~already_mapped) & _search.str.contains(re.escape(kw), case=False, na=False)
            if hit.any():
                df.loc[hit, "building_name"] = canonical
                already_mapped = already_mapped | hit

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
    """실질 회수 대상 미수금 스냅샷.

    app.py 대시보드('배송 D-10 이내 + 잔금>0') 정의를 확장:
      - 배송일이 (오늘 - 10)일 ~ (오늘 + 10)일 사이의 잔금
      - 완납·이상결제(balance_status) 는 제외
      - 총계는 이 범위 안의 회수 대상만 합산 (매장 개설 이래 총액은 부적절)

    반환:
      total_unpaid: 회수 대상 범위(D-10 과거·미래) 미수금 총액
      total_unpaid_all: 매장 전체 미완결 잔금 (참고용, 초기 이관·부실 포함 가능)
      unpaid_d10: 우선 회수 대상 상위 리스트 (배송 지남 ~ D+10)
    """
    client = _get_client()
    if client is None or not store_keys:
        return {"unpaid_d10": [], "total_unpaid": 0, "total_unpaid_all": 0}
    try:
        r = client.table("app_orders").select(
            "id, db_filename, customer_id, delivery_date, total_amount, balance_status"
        ).in_("db_filename", store_keys).execute()
        orders = pd.DataFrame((r.data or []))
        if orders.empty:
            return {"unpaid_d10": [], "total_unpaid": 0, "total_unpaid_all": 0}
    except Exception:
        return {"unpaid_d10": [], "total_unpaid": 0, "total_unpaid_all": 0}

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

    # balance_status 로 완납·이상결제 제외 (app.py 관례: '미납' 이 회수 대상)
    if "balance_status" in orders.columns:
        _bs = orders["balance_status"].fillna("").astype(str).str.strip()
        orders = orders[~_bs.isin(["완납", "이상결제"])].copy()

    unpaid = orders[orders["balance"] > 0].copy()
    total_unpaid_all = int(unpaid["balance"].sum()) if not unpaid.empty else 0

    # 회수 대상 범위: 배송일 (오늘 - 10) ~ (오늘 + 10) — app.py 대시보드 관례
    unpaid["delivery_date"] = pd.to_datetime(unpaid["delivery_date"], errors="coerce").dt.date
    _range_lo = today - timedelta(days=10)
    _range_hi = today + timedelta(days=10)
    _in_range = unpaid[(unpaid["delivery_date"].notna()) &
                       (unpaid["delivery_date"] >= _range_lo) &
                       (unpaid["delivery_date"] <= _range_hi)].copy()
    total_unpaid = int(_in_range["balance"].sum()) if not _in_range.empty else 0

    _top = _in_range.sort_values("balance", ascending=False).head(20)
    unpaid_d10 = [
        {
            "order_id": int(r["id"]),
            "customer_id": int(r["customer_id"]) if pd.notna(r["customer_id"]) else None,
            "delivery_date": r["delivery_date"].isoformat() if pd.notna(r["delivery_date"]) else None,
            "balance": int(r["balance"]),
        }
        for _, r in _top.iterrows()
    ]
    return {
        "unpaid_d10": unpaid_d10,
        "total_unpaid": total_unpaid,           # 회수 대상 (D-10 ~ D+10)
        "total_unpaid_all": total_unpaid_all,   # 참고: 매장 전체 미완결 잔금
    }


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
    # 고객은 orders.customer_id 로 조회 (매장 필터 무관, merge 커버리지 최대화)
    if not orders.empty and "customer_id" in orders.columns:
        _cids = pd.to_numeric(orders["customer_id"], errors="coerce").dropna().astype(int).unique().tolist()
    else:
        _cids = []
    customers = _fetch_customers_by_ids(_cids)
    leads = _fetch_leads(store_names, start, end)
    building_aliases = _fetch_building_aliases(store_names)

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
        "by_region": group_by_region(sales, orders, customers),
        "by_building": group_by_building(orders, customers, aliases=building_aliases),
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


# ────────────────────────────────────────────────────────────────
# AI 요약 (Gemini 1.5 Flash) — api.py 의 VOC 패턴 재사용
# ────────────────────────────────────────────────────────────────
def _compact_dataset_for_prompt(dataset: dict, top: int = 5) -> dict:
    """LLM 프롬프트용 축약본. 원본 리스트를 Top N 으로 잘라 토큰 사용량 최소화."""
    kpi = dataset.get("kpi") or {}
    return {
        "store_name": dataset.get("store_name"),
        "period_type": dataset.get("period_type"),
        "start_date": dataset.get("start_date"),
        "end_date": dataset.get("end_date"),
        "kpi": {
            "sales_amount": kpi.get("sales_amount"),
            "sales_count": kpi.get("sales_count"),
            "aov": kpi.get("aov"),
            "margin_rate_pct": round((kpi.get("margin_rate") or 0) * 100, 1),
            "payments_amount": kpi.get("payments_amount"),
            "prev_period": {
                "sales_diff_pct": (kpi.get("prev_period") or {}).get("sales_diff_pct"),
                "aov_diff_pct": (kpi.get("prev_period") or {}).get("aov_diff_pct"),
            },
            "prev_year": ({
                "sales_diff_pct": (kpi.get("prev_year") or {}).get("sales_diff_pct"),
                "aov_diff_pct": (kpi.get("prev_year") or {}).get("aov_diff_pct"),
            } if kpi.get("prev_year") else None),
        },
        "by_employee_top": (dataset.get("by_employee") or [])[:top],
        "by_region_top": (dataset.get("by_region") or [])[:top],
        "by_building_top": (dataset.get("by_building") or [])[:top],
        "by_category_top": (dataset.get("by_category") or [])[:top],
        "by_visit_reason_top": (dataset.get("by_visit_reason") or [])[:top],
        "by_purchase_reason_top": (dataset.get("by_purchase_reason") or [])[:top],
        "visit_purchase_matrix_top5": dataset.get("visit_purchase_matrix_top5") or [],
        "leads": dataset.get("leads") or {},
        "risks": {
            "total_unpaid": (dataset.get("risks") or {}).get("total_unpaid"),
            "unpaid_d10_count": len((dataset.get("risks") or {}).get("unpaid_d10") or []),
        },
    }


_AI_SYSTEM_PROMPT = (
    "당신은 가구 매장 판매 데이터 분석가입니다. "
    "제공된 JSON 지표를 바탕으로 경영진용 리포트 요약을 작성하세요. "
    "사실 기반, 객관적, 실행 가능한 톤. "
    "반드시 다음 스키마의 JSON 만 출력하세요 (다른 텍스트·마크다운 없이):\n"
    "{\n"
    '  "executive": string,           // 3~5문장의 종합 요약\n'
    '  "highlights": string[],        // 최대 3개, 각 1문장. 잘 된 지표\n'
    '  "risks": string[],             // 최대 3개, 각 1문장. 주의할 지표\n'
    '  "actions": string[]            // 최대 5개, 다음 기간 실행 항목\n'
    "}"
)


def call_gemini(dataset: dict, api_key: str | None = None, timeout: float = 25.0) -> dict:
    """Gemini 1.5 Flash 로 dataset 을 요약해 JSON 반환.

    반환 dict:
        {"ok": bool, "data": {executive, highlights, risks, actions}, "error": str | None}

    실패 시 ok=False. 호출자는 리포트 문서에서 AI 섹션을 fallback 텍스트로 대체.
    """
    key = api_key or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return {"ok": False, "data": None, "error": "GEMINI_API_KEY 환경변수 미설정"}

    try:
        import httpx  # noqa: WPS433 (지역 import — api.py 관례 일치)
    except ImportError as _ie:
        return {"ok": False, "data": None, "error": f"httpx 미설치: {_ie}"}

    compact = _compact_dataset_for_prompt(dataset)
    user_prompt = (
        f"매장: {compact['store_name']}\n"
        f"기간유형: {compact['period_type']}\n"
        f"기간: {compact['start_date']} ~ {compact['end_date']}\n"
        f"지표(JSON):\n{json.dumps(compact, ensure_ascii=False, indent=2)}"
    )

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"gemini-flash-latest:generateContent?key={key}"
    )
    body = {
        "systemInstruction": {"parts": [{"text": _AI_SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "temperature": 0.3,
            "responseMimeType": "application/json",
        },
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            r = client.post(url, json=body)
            r.raise_for_status()
        raw = r.json()
        text = raw["candidates"][0]["content"]["parts"][0]["text"]
        data = json.loads(text)
    except Exception as _e:
        logger.warning("Gemini call failed: %s", _e)
        return {"ok": False, "data": None, "error": str(_e)}

    # 기본 필드 방어
    def _s_list(x: Any, cap: int) -> list[str]:
        if isinstance(x, list):
            return [str(i).strip() for i in x if str(i).strip()][:cap]
        return []

    normalized = {
        "executive": str(data.get("executive") or "").strip(),
        "highlights": _s_list(data.get("highlights"), 3),
        "risks": _s_list(data.get("risks"), 3),
        "actions": _s_list(data.get("actions"), 5),
    }
    if not normalized["executive"]:
        return {"ok": False, "data": None, "error": "executive 필드 비어있음"}
    return {"ok": True, "data": normalized, "error": None}


# ────────────────────────────────────────────────────────────────
# Markdown 렌더링
# ────────────────────────────────────────────────────────────────
def _fmt_krw(v: Any) -> str:
    try:
        return f"{int(v):,}원"
    except (TypeError, ValueError):
        return "-"


def _fmt_pct(v: Any, digits: int = 1) -> str:
    if v is None or isinstance(v, bool):
        return "-"
    try:
        return f"{float(v):+.{digits}f}%"
    except (TypeError, ValueError):
        return "-"


def _md_table(rows: list[dict], columns: list[tuple[str, str]]) -> str:
    """rows(list[dict]) + columns([(key, label)]) → Markdown 표."""
    if not rows:
        return "_데이터 없음_\n"
    header = "| " + " | ".join(lbl for _, lbl in columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body: list[str] = []
    for r in rows:
        cells: list[str] = []
        for k, _ in columns:
            v = r.get(k, "")
            if isinstance(v, float):
                cells.append(f"{v:,.1f}")
            elif isinstance(v, int):
                cells.append(f"{v:,}")
            else:
                cells.append(str(v))
        body.append("| " + " | ".join(cells) + " |")
    return "\n".join([header, sep, *body]) + "\n"


def render_markdown(dataset: dict, ai_summary: dict | None = None) -> str:
    """dataset + ai_summary → 최종 리포트 Markdown 문자열.

    ai_summary 가 None 이거나 실패 시 AI 섹션은 안내문으로 대체.
    """
    kpi = dataset.get("kpi") or {}
    prev = kpi.get("prev_period") or {}
    yoy = kpi.get("prev_year") or {}

    period_label = "주간" if dataset.get("period_type") == "weekly" else "월간"
    title = (
        f"# {dataset.get('store_name', '')} {period_label} 세일즈 리포트\n"
        f"### 기간: {dataset.get('start_date')} ~ {dataset.get('end_date')}\n"
        f"_생성일시: {dataset.get('generated_at', '')}_\n"
    )

    lines: list[str] = [title]

    # 1. Executive Summary (AI)
    lines.append("\n## 1. 이번 " + period_label + " 요약\n")
    if ai_summary and ai_summary.get("executive"):
        lines.append(ai_summary["executive"] + "\n")
    else:
        lines.append("_(AI 요약 미생성 — 데이터 섹션을 참고하세요.)_\n")

    # 2. 핵심 KPI
    lines.append("\n## 2. 핵심 KPI\n")
    kpi_rows = [{
        "지표": "순매출 (sales.amount)",
        "이번 기간": _fmt_krw(kpi.get("sales_amount")),
        "WoW/MoM": _fmt_pct(prev.get("sales_diff_pct")),
        "YoY": (_fmt_pct(yoy.get("sales_diff_pct")) if yoy else "N/A"),
    }, {
        "지표": "판매건수",
        "이번 기간": f"{kpi.get('sales_count', 0):,}건",
        "WoW/MoM": (f"{kpi.get('sales_count', 0) - prev.get('sales_count', 0):+,}건" if prev else "-"),
        "YoY": ((f"{kpi.get('sales_count', 0) - yoy.get('sales_count', 0):+,}건") if yoy else "N/A"),
    }, {
        "지표": "객단가",
        "이번 기간": _fmt_krw(kpi.get("aov")),
        "WoW/MoM": _fmt_pct(prev.get("aov_diff_pct")),
        "YoY": (_fmt_pct(yoy.get("aov_diff_pct")) if yoy else "N/A"),
    }, {
        "지표": "마진율",
        "이번 기간": f"{(kpi.get('margin_rate') or 0) * 100:.1f}%",
        "WoW/MoM": (f"{((kpi.get('margin_rate') or 0) - (prev.get('margin_rate') or 0)) * 100:+.1f}%p" if prev else "-"),
        "YoY": ((f"{((kpi.get('margin_rate') or 0) - (yoy.get('margin_rate') or 0)) * 100:+.1f}%p") if yoy else "N/A"),
    }, {
        "지표": "실수납액",
        "이번 기간": _fmt_krw(kpi.get("payments_amount")),
        "WoW/MoM": "-",
        "YoY": "-",
    }]
    lines.append(_md_table(kpi_rows, [("지표", "지표"), ("이번 기간", "이번 기간"),
                                         ("WoW/MoM", "WoW/MoM"), ("YoY", "YoY")]))
    if not yoy:
        lines.append("_※ 전년 동기간 데이터가 없어 YoY 는 생략됩니다._\n")

    # 3. 매출 분포
    lines.append("\n## 3. 매출 분포\n")

    lines.append("\n### 3.1 직원별 (Top 5, 1/n 배분)\n")
    lines.append(_md_table(
        dataset.get("by_employee") or [],
        [("name", "직원"), ("sales", "순매출(원)"), ("count", "참여 건수")]))

    lines.append("\n### 3.2 지역별 (시군구 Top 5)\n")
    lines.append(_md_table(
        dataset.get("by_region") or [],
        [("region", "시군구"), ("sales", "매출(원)"), ("count", "건수")]))

    lines.append("\n### 3.3 아파트/건물별 (Top 10)\n")
    lines.append(_md_table(
        dataset.get("by_building") or [],
        [("name", "건물명"), ("sales", "매출(원)"), ("count", "건수")]))

    lines.append("\n### 3.4 카테고리 (건수 · 비중)\n")
    lines.append(_md_table(
        dataset.get("by_category") or [],
        [("category", "카테고리"), ("count", "건수"), ("share_pct", "비중(%)")]))

    lines.append("\n### 3.5 결제수단별 실수납\n")
    lines.append(_md_table(
        dataset.get("by_payment_method") or [],
        [("payment_method", "결제수단"), ("amount", "금액(원)"), ("count", "건수")]))

    # 4. 고객 유입 · 구매 동기
    lines.append("\n## 4. 고객 유입 · 구매 동기\n")

    lines.append("\n### 4.1 방문 경로\n")
    lines.append(_md_table(
        dataset.get("by_visit_reason") or [],
        [("visit_reason", "방문경로"), ("count", "건수"), ("sales", "매출(원)"), ("share_pct", "비중(%)")]))

    lines.append("\n### 4.2 구매 이유\n")
    lines.append(_md_table(
        dataset.get("by_purchase_reason") or [],
        [("purchase_reason", "구매이유"), ("count", "건수"), ("sales", "매출(원)"), ("share_pct", "비중(%)")]))

    lines.append("\n### 4.3 방문 × 구매 조합 Top 5\n")
    lines.append(_md_table(
        dataset.get("visit_purchase_matrix_top5") or [],
        [("visit_reason", "방문경로"), ("purchase_reason", "구매이유"),
         ("count", "건수"), ("sales", "매출(원)")]))

    # 5. 리드 활동
    lines.append("\n## 5. 리드 활동\n")
    lead = dataset.get("leads") or {}
    if lead:
        lines.append(_md_table([{
            "신규 리드": f"{lead.get('new_leads', 0)}건",
            "계약 완료": f"{lead.get('closed_deals', 0)}건",
            "전환율": f"{(lead.get('conversion_rate') or 0) * 100:.1f}%",
            "평균 클로징(일)": (f"{lead.get('avg_closing_days')}일"
                                if lead.get('avg_closing_days') is not None else "-"),
            "사후관리율": f"{(lead.get('followup_rate') or 0) * 100:.1f}%",
        }], [
            ("신규 리드", "신규 리드"),
            ("계약 완료", "계약 완료"),
            ("전환율", "전환율"),
            ("평균 클로징(일)", "평균 클로징(일)"),
            ("사후관리율", "사후관리율"),
        ]))
    else:
        lines.append("_데이터 없음_\n")

    # 6. AI 하이라이트 · 위험 · 액션
    lines.append("\n## 6. AI 분석 하이라이트\n")
    if ai_summary:
        if ai_summary.get("highlights"):
            lines.append("\n**✅ 잘 된 지표**\n")
            for h in ai_summary["highlights"]:
                lines.append(f"- {h}")
            lines.append("")
        if ai_summary.get("risks"):
            lines.append("\n**⚠️ 주의 지표**\n")
            for r in ai_summary["risks"]:
                lines.append(f"- {r}")
            lines.append("")
        if ai_summary.get("actions"):
            lines.append("\n**🎯 다음 기간 액션 제안**\n")
            for a in ai_summary["actions"]:
                lines.append(f"- {a}")
            lines.append("")
    else:
        lines.append("_(AI 요약 미생성)_\n")

    # 7. 리스크 · 미수금
    lines.append("\n## 7. 리스크 · 미수금 회수 대상\n")
    risks = dataset.get("risks") or {}
    lines.append(f"- **회수 대상 미수금 (배송 D-10 ~ D+10):** {_fmt_krw(risks.get('total_unpaid'))}")
    if risks.get("total_unpaid_all") is not None:
        lines.append(f"- **전체 미완결 잔금 (참고):** {_fmt_krw(risks.get('total_unpaid_all'))} "
                     "_※ 매장 개설 이래 초기 이관·부실 데이터 포함 가능_")
    u10 = risks.get("unpaid_d10") or []
    lines.append(f"- **우선 회수 대상 건수 (D-10 지남 ~ D+10):** {len(u10)}건")
    if u10:
        lines.append("")
        lines.append(_md_table(u10[:10], [
            ("order_id", "주문ID"),
            ("customer_id", "고객ID"),
            ("delivery_date", "배송일"),
            ("balance", "잔금(원)"),
        ]))

    # Footer
    lines.append("\n---\n")
    lines.append(f"_생성 시각: {dataset.get('generated_at', '')} · "
                 f"매장: {dataset.get('store_name', '')} · "
                 f"기간유형: {dataset.get('period_type', '')}_\n")

    return "\n".join(lines)


# ────────────────────────────────────────────────────────────────
# Supabase 저장 · 조회
# ────────────────────────────────────────────────────────────────
def _report_title(dataset: dict) -> str:
    period_label = "주간" if dataset.get("period_type") == "weekly" else "월간"
    return (
        f"[{period_label}] {dataset.get('store_name', '')} · "
        f"{dataset.get('start_date')} ~ {dataset.get('end_date')}"
    )


def save_report(dataset: dict, markdown_body: str, ai_summary: dict | None,
                status: str = "success", error_message: str | None = None,
                generated_by: str = "manual") -> tuple[bool, str | None]:
    """app_sales_reports 테이블 upsert.

    Returns:
        (ok, error_message_or_none)
    """
    client = _get_client()
    if client is None:
        return False, "Supabase 클라이언트 없음"
    payload = {
        "period_type": dataset.get("period_type"),
        "start_date": dataset.get("start_date"),
        "end_date": dataset.get("end_date"),
        "store_key": dataset.get("store_key"),
        "store_name": dataset.get("store_name"),
        "title": _report_title(dataset),
        "markdown_body": markdown_body,
        "metrics": {k: v for k, v in dataset.items() if k != "ai_summary"},
        "ai_summary": ai_summary,
        "status": status,
        "error_message": error_message,
        "generated_by": generated_by,
    }
    try:
        client.table("app_sales_reports").upsert(
            payload,
            on_conflict="period_type,start_date,end_date,store_key",
        ).execute()
        return True, None
    except Exception as _e:
        logger.error("save_report failed: %s", _e)
        return False, str(_e)


def list_reports(store_key: str | None = None, period_type: str | None = None,
                 limit: int = 30) -> pd.DataFrame:
    """저장된 리포트 목록 (최신순)."""
    client = _get_client()
    if client is None:
        return pd.DataFrame()
    try:
        q = client.table("app_sales_reports").select(
            "id, period_type, start_date, end_date, store_key, store_name, title, "
            "status, error_message, generated_by, generated_at, ai_summary"
        )
        if store_key:
            q = q.eq("store_key", store_key)
        if period_type:
            q = q.eq("period_type", period_type)
        r = q.order("generated_at", desc=True).limit(limit).execute()
        rows = (r.data or []) if hasattr(r, "data") else []
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception as _e:
        logger.warning("list_reports failed: %s", _e)
        return pd.DataFrame()


def load_report(report_id: int) -> dict | None:
    """단일 리포트 전체 로드 (markdown_body 포함)."""
    client = _get_client()
    if client is None:
        return None
    try:
        r = client.table("app_sales_reports").select("*").eq("id", report_id).execute()
        rows = (r.data or []) if hasattr(r, "data") else []
        return rows[0] if rows else None
    except Exception as _e:
        logger.warning("load_report failed: %s", _e)
        return None


def generate_and_save_report(period_type: str, start: date, end: date, store_key: str,
                             generated_by: str = "manual",
                             use_ai: bool = True) -> dict:
    """엔드-투-엔드 리포트 생성 파이프라인.

    1) build_dataset
    2) call_gemini (use_ai=True 이면)
    3) render_markdown
    4) save_report

    Returns:
        {"ok": bool, "dataset": dict, "markdown": str,
         "ai_summary": dict | None, "ai_error": str | None,
         "save_error": str | None}
    """
    dataset = build_dataset(period_type, start, end, store_key)

    ai_summary: dict | None = None
    ai_error: str | None = None
    if use_ai:
        _ai = call_gemini(dataset)
        if _ai["ok"]:
            ai_summary = _ai["data"]
        else:
            ai_error = _ai["error"]

    markdown = render_markdown(dataset, ai_summary)

    save_status = "success" if ai_summary else ("failed" if use_ai else "success")
    save_err = ai_error if use_ai and not ai_summary else None
    ok, save_error = save_report(
        dataset=dataset,
        markdown_body=markdown,
        ai_summary=ai_summary,
        status=save_status,
        error_message=save_err,
        generated_by=generated_by,
    )

    return {
        "ok": ok,
        "dataset": dataset,
        "markdown": markdown,
        "ai_summary": ai_summary,
        "ai_error": ai_error,
        "save_error": save_error,
    }


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
    "call_gemini",
    "render_markdown",
    "save_report",
    "list_reports",
    "load_report",
    "generate_and_save_report",
]
