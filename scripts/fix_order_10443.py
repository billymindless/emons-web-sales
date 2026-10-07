#!/usr/bin/env python3
"""주문 #10443 결제 수동 정정.

- app_payments.id=15358 (-8,124,000원 전액 상계 전표) 삭제
- app_payments.id=15281 원 계좌이체 금액 8,124,000 → 5,624,000 으로 감액
- app_payment_history 에 수동 정정 audit 2건 삽입 (payment_change_cancel_undo + payment_change_partial)
- app_orders.actual_margin / balance_status 재계산

실행 전/후 상태를 모두 출력한다. --apply 플래그가 없으면 dry-run.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:
    import toml as tomllib  # type: ignore[no-redef]

from supabase import create_client

ROOT = Path(__file__).resolve().parent.parent
SECRETS_PATH = ROOT / ".streamlit" / "secrets.toml"

ORDER_ID = 10443
DB_FILENAME = "store_1.db"
CUSTOMER_NAME = "이은경"
PAYMENT_ID_ORIGINAL = 15281       # 계좌이체 8,124,000원 (감액 대상)
PAYMENT_ID_OFFSET = 15358         # 계좌이체 -8,124,000원 (삭제 대상)
ORIG_AMOUNT = 8_124_000
NEW_PARTIAL_AMOUNT = 2_500_000    # 변경 후 합계 (유지되지 않는 부분)
KEPT_AMOUNT = ORIG_AMOUNT - NEW_PARTIAL_AMOUNT  # 5,624,000
PCR_TASK_ID = 68
PCR_REQUEST_ID = 53
MANUAL_FIX_REASON = (
    f"수동 정정: 원 계좌이체 {ORIG_AMOUNT:,}원 → {KEPT_AMOUNT:,}원 감액 및 "
    f"-{ORIG_AMOUNT:,}원 상계 전표 삭제 "
    f"(부분 결제변경 로직 보완 적용 전 요청건, PCR task_id={PCR_TASK_ID} / request_id={PCR_REQUEST_ID})"
)
CHANGED_BY = "manual_admin_fix"  # 수동 정정 수행자 식별자
KST = timezone(timedelta(hours=9))


def _load_client():
    if not SECRETS_PATH.exists():
        raise SystemExit(f"secrets 파일을 찾을 수 없습니다: {SECRETS_PATH}")
    try:
        with SECRETS_PATH.open("rb") as f:
            secrets = tomllib.load(f)
    except TypeError:
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
        raise SystemExit("Supabase URL/Key 가 secrets.toml 에 없습니다.")
    return create_client(url, key)


def _print_payments(client) -> None:
    res = (
        client.table("app_payments")
        .select("id, payment_date, amount, payment_method, card_company, onnuri_approval_code, created_at")
        .eq("order_id", ORDER_ID)
        .order("id")
        .execute()
    )
    total = 0
    print(f"  order #{ORDER_ID} 결제 행 {len(res.data or [])} 건")
    for r in res.data or []:
        amt = int(r.get("amount") or 0)
        total += amt
        print(
            f"    id={r.get('id'):<6} {str(r.get('payment_date'))[:10]:<12} "
            f"{amt:>12,}원  {r.get('payment_method') or '-':<10} "
            f"card={r.get('card_company') or '-':<8} onnuri={r.get('onnuri_approval_code') or '-'}"
        )
    print(f"  합계: {total:,}원")


def _balance_status(remaining: float) -> str:
    if remaining == 0:
        return "완납"
    if remaining < 0:
        return "이상결제"
    return "미납"


def _recalc_margin(client) -> dict:
    order_res = (
        client.table("app_orders")
        .select("id, total_amount, cost_price, display_cost_amount, actual_margin, balance_status")
        .eq("db_filename", DB_FILENAME)
        .eq("id", ORDER_ID)
        .maybe_single()
        .execute()
    )
    order = order_res.data if order_res and order_res.data else None
    if not order:
        raise SystemExit(f"app_orders 에서 order_id={ORDER_ID} 를 찾을 수 없습니다.")
    total_amt = float(order.get("total_amount") or 0)
    cost_general = float(order.get("cost_price") or 0)
    cost_display = float(order.get("display_cost_amount") or 0)
    pay_res = (
        client.table("app_payments")
        .select("amount, fee_amount")
        .eq("order_id", ORDER_ID)
        .execute()
    )
    paid = sum(float(r.get("amount") or 0) for r in pay_res.data or [])
    fees = sum(float(r.get("fee_amount") or 0) for r in pay_res.data or [])
    basic_m = total_amt - (cost_general + cost_display)
    actual_margin = basic_m - fees
    remaining = total_amt - paid
    balance = _balance_status(remaining)
    return {
        "order_before": order,
        "total_amount": total_amt,
        "cost_general": cost_general,
        "cost_display": cost_display,
        "paid": paid,
        "fees": fees,
        "basic_margin": basic_m,
        "actual_margin": actual_margin,
        "remaining": remaining,
        "balance_status": balance,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="실제 Supabase 에 변경을 적용한다. 없으면 dry-run.")
    args = ap.parse_args()

    client = _load_client()

    print("=" * 100)
    print("[BEFORE] 결제 행")
    _print_payments(client)
    print()

    recalc_before = _recalc_margin(client)
    print(
        f"[BEFORE] total={recalc_before['total_amount']:,.0f} "
        f"paid={recalc_before['paid']:,.0f} remaining={recalc_before['remaining']:,.0f} "
        f"actual_margin(current)={recalc_before['order_before'].get('actual_margin')} "
        f"balance_status(current)={recalc_before['order_before'].get('balance_status')}"
    )
    print()

    if not args.apply:
        print("DRY-RUN: --apply 를 붙여야 실제 변경이 적용됩니다.")
        # 추정 결과만 보여준다
        projected_paid = recalc_before["paid"] + ORIG_AMOUNT - (ORIG_AMOUNT - KEPT_AMOUNT)
        # 삭제: +(-8,124,000) 제거 → +8,124,000, 감액: -(8,124,000 - 5,624,000) = -2,500,000
        delta = ORIG_AMOUNT - (ORIG_AMOUNT - KEPT_AMOUNT)  # = KEPT_AMOUNT
        projected_paid = recalc_before["paid"] + ORIG_AMOUNT + (KEPT_AMOUNT - ORIG_AMOUNT)
        projected_paid = recalc_before["paid"] + 8_124_000 - 2_500_000  # offset 제거(+8.124M) 감액(-2.5M)
        projected_remaining = recalc_before["total_amount"] - projected_paid
        print(
            f"[DRY-RUN 예상] paid_after={projected_paid:,.0f} "
            f"remaining_after={projected_remaining:,.0f} "
            f"balance_status_after={_balance_status(projected_remaining)}"
        )
        return 0

    print("=" * 100)
    print("[APPLY] Supabase 변경 적용 시작")
    print()

    now_iso = datetime.now(tz=KST).isoformat()

    # 1) 상계 전표 삭제
    print(f"  1) DELETE app_payments WHERE id={PAYMENT_ID_OFFSET}")
    del_res = client.table("app_payments").delete().eq("id", PAYMENT_ID_OFFSET).execute()
    print(f"     -> deleted rows: {len(del_res.data or [])}")

    # 2) 원 결제 감액
    print(f"  2) UPDATE app_payments SET amount={KEPT_AMOUNT} WHERE id={PAYMENT_ID_ORIGINAL}")
    upd_res = (
        client.table("app_payments")
        .update({"amount": KEPT_AMOUNT})
        .eq("id", PAYMENT_ID_ORIGINAL)
        .execute()
    )
    print(f"     -> updated rows: {len(upd_res.data or [])}")

    # 3-a) payment_change_cancel_undo audit
    print("  3-a) INSERT app_payment_history (payment_change_cancel_undo)")
    client.table("app_payment_history").insert({
        "db_filename": DB_FILENAME,
        "sale_id": ORDER_ID,
        "customer_name": CUSTOMER_NAME,
        "action_type": "payment_change_cancel_undo",
        "old_payment_data": {
            "payment_id": PAYMENT_ID_OFFSET,
            "amount": -ORIG_AMOUNT,
            "payment_method": "계좌이체",
            "note": "결제변경 요청에 의한 취소 등록 (삭제됨)",
        },
        "new_payment_data": {
            "payment_id": None,
            "amount": 0,
            "note": "전액 상계 전표 삭제 (수동 정정 — 부분 결제변경으로 전환)",
        },
        "reason": MANUAL_FIX_REASON,
        "changed_by": CHANGED_BY,
        "changed_at": now_iso,
    }).execute()
    print("     -> inserted")

    # 3-b) payment_change_partial audit
    print("  3-b) INSERT app_payment_history (payment_change_partial)")
    client.table("app_payment_history").insert({
        "db_filename": DB_FILENAME,
        "sale_id": ORDER_ID,
        "customer_name": CUSTOMER_NAME,
        "action_type": "payment_change_partial",
        "old_payment_data": {
            "payment_id": PAYMENT_ID_ORIGINAL,
            "amount": ORIG_AMOUNT,
            "payment_method": "계좌이체",
        },
        "new_payment_data": {
            "payment_id": PAYMENT_ID_ORIGINAL,
            "amount": KEPT_AMOUNT,
            "note": (
                f"결제변경 요청에 의한 감액 ({ORIG_AMOUNT:,}원 → {KEPT_AMOUNT:,}원, "
                f"차액 {NEW_PARTIAL_AMOUNT:,}원 신규 결제로 이동) · 수동 정정"
            ),
        },
        "reason": MANUAL_FIX_REASON,
        "changed_by": CHANGED_BY,
        "changed_at": now_iso,
    }).execute()
    print("     -> inserted")

    # 4) actual_margin / balance_status 재계산
    recalc_after = _recalc_margin(client)
    print(
        f"  4) UPDATE app_orders SET actual_margin={recalc_after['actual_margin']:.0f}, "
        f"balance_status='{recalc_after['balance_status']}'"
    )
    (
        client.table("app_orders")
        .update({
            "actual_margin": recalc_after["actual_margin"],
            "balance_status": recalc_after["balance_status"],
        })
        .eq("db_filename", DB_FILENAME)
        .eq("id", ORDER_ID)
        .execute()
    )
    print("     -> updated")

    print()
    print("=" * 100)
    print("[AFTER] 결제 행")
    _print_payments(client)
    print()
    print(
        f"[AFTER] total={recalc_after['total_amount']:,.0f} "
        f"paid={recalc_after['paid']:,.0f} remaining={recalc_after['remaining']:,.0f} "
        f"actual_margin={recalc_after['actual_margin']:.0f} "
        f"balance_status={recalc_after['balance_status']}"
    )
    print()
    print("✅ 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
