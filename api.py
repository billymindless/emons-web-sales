# -*- coding: utf-8 -*-
"""
momo SaaS — 토스페이먼츠 빌링 FastAPI 서버.

엔드포인트:
  POST /billing/issue-key   빌링키 발급 (프론트 → 토스 인증 후 콜백)
  POST /billing/charge      정기결제 실행 (스케줄러 호출)
  POST /billing/webhook     토스 → 우리 서버 웹훅
  POST /billing/schedule    만료 예정 조직 일괄 결제 (Render Cron Job)
  GET  /billing/status/{org_id}   구독 상태 조회

실행:
  uvicorn api:app --host 0.0.0.0 --port 8000

환경변수:
  SUPABASE_URL            Supabase 프로젝트 URL
  SUPABASE_SERVICE_KEY    service_role key
  TOSS_SECRET_KEY         토스페이먼츠 시크릿 키 (test_sk_... 또는 live_sk_...)
  BILLING_WEBHOOK_SECRET  웹훅 서명 검증용 시크릿 (선택)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from supabase import create_client, Client

# ── 로깅
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("momo.billing")

# ── FastAPI
app = FastAPI(title="momo Billing API", version="1.0.0")

# ── Supabase admin client
def _supabase() -> Client:
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return create_client(url, key)

# ── 토스페이먼츠 API 기본 URL
TOSS_API_BASE = "https://api.tosspayments.com/v1"

def _toss_auth_header() -> str:
    secret = os.environ["TOSS_SECRET_KEY"]
    encoded = base64.b64encode(f"{secret}:".encode()).decode()
    return f"Basic {encoded}"


# ───────────────────────────────────────────────────────────
# Pydantic 모델
# ───────────────────────────────────────────────────────────

class IssueKeyRequest(BaseModel):
    auth_key: str
    customer_key: str
    org_id: int
    plan: str                         # starter | growth | pro
    billing_cycle: str = "monthly"    # monthly | annual

class ChargeRequest(BaseModel):
    org_id: int
    amount: int                       # 원 단위
    order_name: str = "momo SaaS 구독"

class ScheduleRequest(BaseModel):
    dry_run: bool = False


# ───────────────────────────────────────────────────────────
# 헬퍼
# ───────────────────────────────────────────────────────────

_PLAN_AMOUNTS = {
    "starter": {"monthly": 39_000,  "annual": 390_000},
    "growth":  {"monthly": 99_000,  "annual": 990_000},
    "pro":     {"monthly": 199_000, "annual": 1_990_000},
}

def _period_end(cycle: str) -> datetime:
    now = datetime.now(timezone.utc)
    return now + timedelta(days=365 if cycle == "annual" else 30)


def _log_billing_event(db: Client, org_id: int | None, event_type: str, payload: dict) -> None:
    try:
        db.table("app_billing_events").insert({
            "org_id": org_id,
            "event_type": event_type,
            "payload": payload,
        }).execute()
    except Exception as exc:
        logger.warning("billing event log failed: %s", exc)


def _upsert_subscription(db: Client, org_id: int, plan: str, cycle: str, billing_key: str) -> dict:
    """기존 구독을 업서트하고, app_orgs.plan 도 함께 업데이트."""
    period_end = _period_end(cycle)
    now = datetime.now(timezone.utc)

    # 기존 active/past_due 구독 조회
    existing = (
        db.table("app_subscriptions")
        .select("id")
        .eq("org_id", org_id)
        .in_("status", ["active", "past_due"])
        .limit(1)
        .execute()
    )
    sub_data = {
        "org_id": org_id,
        "plan": plan,
        "billing_cycle": cycle,
        "status": "active",
        "current_period_start": now.isoformat(),
        "current_period_end": period_end.isoformat(),
        "cancel_at_period_end": False,
        "toss_billing_key": billing_key,
        "updated_at": now.isoformat(),
    }
    if existing.data:
        sub_id = existing.data[0]["id"]
        db.table("app_subscriptions").update(sub_data).eq("id", sub_id).execute()
    else:
        result = db.table("app_subscriptions").insert(sub_data).execute()
        sub_id = result.data[0]["id"] if result.data else None

    # org plan 동기화
    db.table("app_orgs").update({"plan": plan}).eq("id", org_id).execute()
    return {"sub_id": sub_id, "period_end": period_end.isoformat()}


# ───────────────────────────────────────────────────────────
# POST /billing/issue-key
# ───────────────────────────────────────────────────────────

@app.post("/billing/issue-key")
async def issue_billing_key(req: IssueKeyRequest) -> JSONResponse:
    """
    토스페이먼츠 빌링키 발급.
    프론트가 requestBillingAuth() 완료 후 authKey·customerKey 를 서버로 전달.
    """
    db = _supabase()
    logger.info("issue-key org=%s plan=%s cycle=%s", req.org_id, req.plan, req.billing_cycle)

    # 1) 토스 빌링키 발급 API 호출
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{TOSS_API_BASE}/billing/authorizations/{req.auth_key}",
            headers={"Authorization": _toss_auth_header(), "Content-Type": "application/json"},
            json={"customerKey": req.customer_key},
            timeout=15,
        )

    if resp.status_code != 200:
        body = resp.json()
        logger.error("toss issue-key failed: %s", body)
        _log_billing_event(db, req.org_id, "issue_key_failed", {"error": body})
        raise HTTPException(status_code=502, detail=body.get("message", "빌링키 발급 실패"))

    toss_data = resp.json()
    billing_key = toss_data.get("billingKey") or toss_data.get("billing_key")
    if not billing_key:
        raise HTTPException(status_code=502, detail="billingKey 없음")

    # 2) Supabase 구독 업서트
    sub_result = _upsert_subscription(db, req.org_id, req.plan, req.billing_cycle, billing_key)

    # 3) 첫 회차 즉시 결제
    amount = _PLAN_AMOUNTS.get(req.plan, {}).get(req.billing_cycle, 0)
    if amount > 0:
        order_id = f"momo_{req.org_id}_{uuid.uuid4().hex[:8]}"
        async with httpx.AsyncClient() as client:
            charge_resp = await client.post(
                f"{TOSS_API_BASE}/billing/{billing_key}",
                headers={"Authorization": _toss_auth_header(), "Content-Type": "application/json"},
                json={
                    "customerKey": req.customer_key,
                    "amount": amount,
                    "orderId": order_id,
                    "orderName": f"momo SaaS {req.plan} ({req.billing_cycle})",
                    "customerEmail": "",
                },
                timeout=15,
            )
        charge_data = charge_resp.json()
        invoice_status = "paid" if charge_resp.status_code == 200 else "failed"
        db.table("app_invoices").insert({
            "org_id": req.org_id,
            "subscription_id": sub_result.get("sub_id"),
            "amount": amount,
            "status": invoice_status,
            "toss_payment_key": charge_data.get("paymentKey"),
            "toss_order_id": order_id,
            "paid_at": datetime.now(timezone.utc).isoformat() if invoice_status == "paid" else None,
            "receipt_url": charge_data.get("receipt", {}).get("url") if isinstance(charge_data.get("receipt"), dict) else None,
        }).execute()

    _log_billing_event(db, req.org_id, "issued_key", {"plan": req.plan, "cycle": req.billing_cycle})
    return JSONResponse({"status": "ok", "billing_key": billing_key[:6] + "***", **sub_result})


# ───────────────────────────────────────────────────────────
# POST /billing/charge
# ───────────────────────────────────────────────────────────

@app.post("/billing/charge")
async def charge_billing(req: ChargeRequest) -> JSONResponse:
    """정기결제 실행. 스케줄러(Render Cron) 또는 내부 호출."""
    db = _supabase()

    sub = (
        db.table("app_subscriptions")
        .select("*")
        .eq("org_id", req.org_id)
        .in_("status", ["active", "past_due"])
        .limit(1)
        .execute()
    )
    if not sub.data:
        raise HTTPException(status_code=404, detail="활성 구독 없음")

    sub_row = sub.data[0]
    billing_key = sub_row.get("toss_billing_key")
    if not billing_key:
        raise HTTPException(status_code=400, detail="빌링키 없음")

    order_id = f"momo_{req.org_id}_{uuid.uuid4().hex[:8]}"
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{TOSS_API_BASE}/billing/{billing_key}",
            headers={"Authorization": _toss_auth_header(), "Content-Type": "application/json"},
            json={
                "customerKey": f"org_{req.org_id}",
                "amount": req.amount,
                "orderId": order_id,
                "orderName": req.order_name,
            },
            timeout=15,
        )

    charge_data = resp.json()
    now = datetime.now(timezone.utc)

    if resp.status_code == 200:
        # 다음 결제 기간 갱신
        cycle = sub_row.get("billing_cycle", "monthly")
        new_end = (now + timedelta(days=365 if cycle == "annual" else 30)).isoformat()
        db.table("app_subscriptions").update({
            "status": "active",
            "current_period_start": now.isoformat(),
            "current_period_end": new_end,
            "updated_at": now.isoformat(),
        }).eq("id", sub_row["id"]).execute()

        db.table("app_invoices").insert({
            "org_id": req.org_id,
            "subscription_id": sub_row["id"],
            "amount": req.amount,
            "status": "paid",
            "toss_payment_key": charge_data.get("paymentKey"),
            "toss_order_id": order_id,
            "paid_at": now.isoformat(),
            "receipt_url": (charge_data.get("receipt") or {}).get("url"),
        }).execute()

        _log_billing_event(db, req.org_id, "charged", {"amount": req.amount, "order_id": order_id})
        return JSONResponse({"status": "paid", "order_id": order_id})

    # 결제 실패: 재시도 스케줄 설정
    fail_code = charge_data.get("code", "UNKNOWN")
    fail_msg = charge_data.get("message", "")
    retry_count_row = (
        db.table("app_invoices")
        .select("retry_count")
        .eq("toss_order_id", order_id)
        .maybe_single()
        .execute()
    )
    # 재시도 간격: D+1, D+3, D+7
    retry_offsets = [1, 3, 7]
    existing_inv = (
        db.table("app_invoices")
        .select("id,retry_count")
        .eq("org_id", req.org_id)
        .eq("status", "failed")
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    cur_retry = existing_inv.data[0]["retry_count"] if existing_inv.data else 0
    next_retry = None
    if cur_retry < len(retry_offsets):
        next_retry = (now + timedelta(days=retry_offsets[cur_retry])).isoformat()
    else:
        # 3회 실패 → read-only 모드
        db.table("app_subscriptions").update({"status": "past_due"}).eq("id", sub_row["id"]).execute()
        db.table("app_orgs").update({"plan": "locked"}).eq("id", req.org_id).execute()

    db.table("app_invoices").insert({
        "org_id": req.org_id,
        "subscription_id": sub_row["id"],
        "amount": req.amount,
        "status": "failed",
        "toss_order_id": order_id,
        "failure_reason": f"{fail_code}: {fail_msg}",
        "retry_count": cur_retry + 1,
        "next_retry_at": next_retry,
    }).execute()

    _log_billing_event(db, req.org_id, "failed", {"code": fail_code, "msg": fail_msg, "retry": cur_retry + 1})
    logger.error("charge failed org=%s code=%s retry=%s", req.org_id, fail_code, cur_retry + 1)
    return JSONResponse({"status": "failed", "code": fail_code, "next_retry": next_retry}, status_code=402)


# ───────────────────────────────────────────────────────────
# POST /billing/webhook  (토스 → 서버)
# ───────────────────────────────────────────────────────────

@app.post("/billing/webhook")
async def toss_webhook(request: Request) -> JSONResponse:
    """토스페이먼츠 웹훅 수신 (결제 완료/실패/취소)."""
    body_bytes = await request.body()

    # 서명 검증 (BILLING_WEBHOOK_SECRET 설정 시)
    webhook_secret = os.environ.get("BILLING_WEBHOOK_SECRET", "")
    if webhook_secret:
        sig = request.headers.get("TossPayments-Signature", "")
        expected = hmac.new(webhook_secret.encode(), body_bytes, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            logger.warning("webhook signature mismatch")
            raise HTTPException(status_code=401, detail="서명 불일치")

    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="JSON 파싱 실패")

    event_type = payload.get("eventType", "")
    data = payload.get("data", {})
    payment_key = data.get("paymentKey", "")
    order_id = data.get("orderId", "")

    logger.info("webhook event=%s order=%s", event_type, order_id)
    db = _supabase()

    # 주문 ID로 org 찾기
    inv = db.table("app_invoices").select("org_id,id").eq("toss_order_id", order_id).maybe_single().execute()
    inv_data = inv.data if isinstance(inv.data, dict) else (inv.data[0] if inv.data else None)
    org_id = inv_data["org_id"] if inv_data else None

    if event_type == "PAYMENT_STATUS_CHANGED":
        pay_status = data.get("status", "")
        if pay_status == "DONE" and inv_data:
            db.table("app_invoices").update({
                "status": "paid",
                "toss_payment_key": payment_key,
                "paid_at": datetime.now(timezone.utc).isoformat(),
                "receipt_url": (data.get("receipt") or {}).get("url"),
            }).eq("id", inv_data["id"]).execute()
            # 구독 active 복구
            if org_id:
                db.table("app_subscriptions").update({"status": "active"}).eq("org_id", org_id).in_("status", ["past_due"]).execute()

        elif pay_status == "CANCELED" and inv_data:
            db.table("app_invoices").update({"status": "refunded"}).eq("id", inv_data["id"]).execute()

    _log_billing_event(db, org_id, f"webhook_{event_type.lower()}", {"payment_key": payment_key, "status": data.get("status")})
    return JSONResponse({"status": "ok"})


# ───────────────────────────────────────────────────────────
# POST /billing/schedule  (Render Cron Job: 매일 새벽 실행)
# ───────────────────────────────────────────────────────────

@app.post("/billing/schedule")
async def run_billing_schedule(req: ScheduleRequest) -> JSONResponse:
    """
    결제일 도래 구독 일괄 처리.
    Render Cron Job 설정: `curl -X POST https://your-api/billing/schedule`
    """
    db = _supabase()
    now = datetime.now(timezone.utc)
    window_end = now + timedelta(hours=1)

    # current_period_end가 지금~1시간 이내인 active 구독
    due_subs = (
        db.table("app_subscriptions")
        .select("*")
        .lte("current_period_end", window_end.isoformat())
        .in_("status", ["active"])
        .eq("cancel_at_period_end", False)
        .execute()
    )

    results = []
    for sub in (due_subs.data or []):
        org_id = sub["org_id"]
        plan = sub.get("plan", "starter")
        cycle = sub.get("billing_cycle", "monthly")
        amount = _PLAN_AMOUNTS.get(plan, {}).get(cycle, 0)
        if amount == 0 or req.dry_run:
            results.append({"org_id": org_id, "action": "skipped", "dry_run": req.dry_run})
            continue

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    "http://localhost:8000/billing/charge",
                    json={"org_id": org_id, "amount": amount, "order_name": f"momo {plan} 정기결제"},
                    timeout=20,
                )
            results.append({"org_id": org_id, "status": resp.json().get("status"), "amount": amount})
        except Exception as exc:
            logger.error("schedule charge failed org=%s: %s", org_id, exc)
            results.append({"org_id": org_id, "error": str(exc)})

    # 만료 30/7/1일 전 알림 (app_billing_events 기록)
    for days_before in [30, 7, 1]:
        notice_window = now + timedelta(days=days_before)
        notice_subs = (
            db.table("app_subscriptions")
            .select("org_id,plan,current_period_end")
            .lte("current_period_end", (notice_window + timedelta(hours=12)).isoformat())
            .gte("current_period_end", (notice_window - timedelta(hours=12)).isoformat())
            .in_("status", ["active"])
            .execute()
        )
        for s in (notice_subs.data or []):
            _log_billing_event(db, s["org_id"], "expiry_notice", {"days_before": days_before, "plan": s["plan"]})

    logger.info("schedule run: %d subscriptions processed", len(results))
    return JSONResponse({"processed": len(results), "results": results})


# ───────────────────────────────────────────────────────────
# GET /billing/status/{org_id}
# ───────────────────────────────────────────────────────────

@app.get("/billing/status/{org_id}")
async def billing_status(org_id: int) -> JSONResponse:
    db = _supabase()

    org = db.table("app_orgs").select("plan,trial_ends_at").eq("id", org_id).maybe_single().execute()
    org_data = org.data if isinstance(org.data, dict) else (org.data[0] if org.data else {})

    sub = (
        db.table("app_subscriptions")
        .select("plan,status,current_period_end,billing_cycle,cancel_at_period_end")
        .eq("org_id", org_id)
        .in_("status", ["active", "past_due"])
        .limit(1)
        .execute()
    )
    sub_data = sub.data[0] if sub.data else None

    invoices = (
        db.table("app_invoices")
        .select("amount,status,paid_at,receipt_url")
        .eq("org_id", org_id)
        .order("created_at", desc=True)
        .limit(5)
        .execute()
    )

    return JSONResponse({
        "org": org_data,
        "subscription": sub_data,
        "recent_invoices": invoices.data or [],
    })


# ───────────────────────────────────────────────────────────
# Health check
# ───────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "service": "momo-billing"})
