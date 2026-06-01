# -*- coding: utf-8 -*-
"""입금 관리 백엔드 모듈.

기업은행 입금 SMS(webhook 수신, source='auto_sms')와 수기 입력(source='manual')을
app_deposits 테이블로 관리한다. 계좌-매장 매핑은 app_bank_accounts.
Supabase 클라이언트는 app.py와 공유.
"""

from __future__ import annotations

from datetime import datetime, timezone

import streamlit as st


def _client():
    from app import get_supabase_client  # noqa: WPS433
    return get_supabase_client()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────
# 입금 원장 (app_deposits)
# ─────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def load_deposits_cached(store_name: str | None = None, include_unmatched: bool = False) -> list[dict]:
    """입금 목록. store_name=None이면 전체(superadmin).
    include_unmatched=True면 미분류(store_name IS NULL)도 포함."""
    client, err = _client()
    if err or not client:
        return []
    try:
        q = client.table("app_deposits").select("*")
        if store_name:
            if include_unmatched:
                q = q.or_(f"store_name.eq.{store_name},store_name.is.null")
            else:
                q = q.eq("store_name", store_name)
        return (q.order("txn_at", desc=True).execute().data or [])
    except Exception:
        return []


def clear_deposit_caches() -> None:
    try:
        load_deposits_cached.clear()
    except Exception:
        pass


def create_manual_deposit(
    txn_at: str,
    counterparty: str,
    amount: float,
    store_name: str | None,
    created_by: str,
    bank_name: str = "기업은행",
    memo: str | None = None,
) -> tuple[bool, str | None]:
    """수기 입금 등록 (source='manual')."""
    client, err = _client()
    if err or not client:
        return False, err or "Supabase 연결 불가"
    row = {
        "txn_at": txn_at,
        "counterparty": (counterparty or "").strip() or None,
        "amount": amount,
        "bank_name": bank_name,
        "store_name": store_name,
        "source": "manual",
        "memo": (memo or "").strip() or None,
        "created_by": created_by,
    }
    try:
        client.table("app_deposits").insert(row).execute()
        clear_deposit_caches()
        return True, None
    except Exception as e:
        return False, str(e)


def update_deposit(deposit_id: int, **fields) -> tuple[bool, str | None]:
    """입금 건 수정. store_name/counterparty/amount/memo/linked_sale_id 허용."""
    client, err = _client()
    if err or not client:
        return False, err
    allowed = {"store_name", "counterparty", "amount", "memo", "linked_sale_id"}
    patch = {k: v for k, v in fields.items() if k in allowed}
    if not patch:
        return True, None
    try:
        client.table("app_deposits").update(patch).eq("id", deposit_id).execute()
        clear_deposit_caches()
        return True, None
    except Exception as e:
        return False, str(e)


def link_sale(deposit_id: int, sale_id: int | None) -> tuple[bool, str | None]:
    """입금 건을 매출(sales.id)에 연결. sale_id=None이면 연결 해제."""
    return update_deposit(deposit_id, linked_sale_id=sale_id)


def delete_deposit(deposit_id: int) -> tuple[bool, str | None]:
    client, err = _client()
    if err or not client:
        return False, err
    try:
        client.table("app_deposits").delete().eq("id", deposit_id).execute()
        clear_deposit_caches()
        return True, None
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────────────
# 계좌-매장 매핑 (app_bank_accounts)
# ─────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def load_bank_accounts_cached() -> list[dict]:
    client, err = _client()
    if err or not client:
        return []
    try:
        return (client.table("app_bank_accounts").select("*")
                .order("created_at", desc=True).execute().data or [])
    except Exception:
        return []


def clear_bank_account_caches() -> None:
    try:
        load_bank_accounts_cached.clear()
    except Exception:
        pass


def upsert_bank_account(
    account_suffix: str,
    store_name: str,
    account_masked: str | None = None,
    account_alias: str | None = None,
    bank_name: str = "기업은행",
    is_active: bool = True,
) -> tuple[bool, str | None]:
    """계좌-매장 매핑 등록/갱신 (account_suffix UNIQUE 기준 upsert)."""
    client, err = _client()
    if err or not client:
        return False, err
    suffix = (account_suffix or "").strip()
    if not suffix:
        return False, "계좌 끝자리(account_suffix)는 필수입니다."
    row = {
        "account_suffix": suffix,
        "store_name": store_name,
        "account_masked": (account_masked or "").strip() or None,
        "account_alias": (account_alias or "").strip() or None,
        "bank_name": bank_name,
        "is_active": bool(is_active),
    }
    try:
        client.table("app_bank_accounts").upsert(row, on_conflict="account_suffix").execute()
        clear_bank_account_caches()
        return True, None
    except Exception as e:
        return False, str(e)


def delete_bank_account(account_id: int) -> tuple[bool, str | None]:
    client, err = _client()
    if err or not client:
        return False, err
    try:
        client.table("app_bank_accounts").delete().eq("id", account_id).execute()
        clear_bank_account_caches()
        return True, None
    except Exception as e:
        return False, str(e)
