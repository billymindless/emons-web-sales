#!/usr/bin/env python3
"""주문 #10443 결제 행 현재 상태를 Supabase 에서 조회만 한다. 수정은 하지 않는다."""
from __future__ import annotations

import sys
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import toml as tomllib  # type: ignore[no-redef]

from supabase import create_client

ROOT = Path(__file__).resolve().parent.parent
SECRETS_PATH = ROOT / ".streamlit" / "secrets.toml"
ORDER_ID = 10443


def main() -> int:
    if not SECRETS_PATH.exists():
        print(f"secrets 파일을 찾을 수 없습니다: {SECRETS_PATH}", file=sys.stderr)
        return 1
    try:
        with SECRETS_PATH.open("rb") as f:
            secrets = tomllib.load(f)
    except TypeError:
        # fallback for `toml` which expects text mode
        with SECRETS_PATH.open("r") as f:  # type: ignore[arg-type]
            secrets = tomllib.load(f)  # type: ignore[arg-type]
    sb = secrets.get("supabase") or {}
    url = (sb.get("url") or "").strip()
    key = (
        sb.get("service_role_key")
        or sb.get("service_role")
        or sb.get("key")
        or sb.get("anon_key")
        or ""
    ).strip()
    if not url or not key:
        print("Supabase URL 또는 Key 가 secrets.toml 에 없습니다.", file=sys.stderr)
        return 1
    client = create_client(url, key)

    # 1) 결제 행
    res = (
        client.table("app_payments")
        .select(
            "id, order_id, payment_date, amount, payment_method, card_company, "
            "onnuri_approval_code, fee_amount, created_by, created_at"
        )
        .eq("order_id", ORDER_ID)
        .order("id")
        .execute()
    )
    rows = list(res.data or [])
    print(f"[app_payments] order #{ORDER_ID} 결제 행 {len(rows)} 건")
    print("-" * 100)
    total = 0
    for r in rows:
        amt = int(r.get("amount") or 0)
        total += amt
        print(
            f"id={r.get('id'):<8} date={str(r.get('payment_date'))[:10]:<12} "
            f"amount={amt:>12,}원  method={r.get('payment_method') or '-':<10} "
            f"card={r.get('card_company') or '-':<8} "
            f"onnuri={r.get('onnuri_approval_code') or '-':<12} "
            f"by={r.get('created_by') or '-'}  at={str(r.get('created_at'))[:19]}"
        )
    print("-" * 100)
    print(f"합계: {total:,}원")

    # 2) 주문 메타 (db_filename, customer_name, actual_margin)
    print()
    try:
        ores = (
            client.table("app_orders")
            .select("id, db_filename, customer_name, store_name, actual_margin")
            .eq("id", ORDER_ID)
            .limit(1)
            .execute()
        )
        print(f"[app_orders] {ores.data}")
    except Exception as e:
        print(f"[app_orders] 조회 실패: {e}")

    # 3) 기존 결제변경 요청
    print()
    try:
        pcr = (
            client.table("app_payment_change_requests")
            .select("*")
            .eq("sale_id", ORDER_ID)
            .execute()
        )
        print(f"[app_payment_change_requests] {len(pcr.data or [])}건")
        for row in pcr.data or []:
            print(row)
    except Exception as e:
        print(f"[app_payment_change_requests] 조회 실패: {e}")

    # 4) payment_history
    print()
    try:
        ph = (
            client.table("app_payment_history")
            .select("id, action_type, old_payment_data, new_payment_data, changed_by, changed_at, reason")
            .eq("sale_id", ORDER_ID)
            .order("id")
            .execute()
        )
        print(f"[app_payment_history] {len(ph.data or [])}건")
        for row in ph.data or []:
            print(row)
    except Exception as e:
        print(f"[app_payment_history] 조회 실패: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
