# -*- coding: utf-8 -*-
"""
이몬스 웹훅 FastAPI 서버 (독립 실행 프로세스).

현재 제공 엔드포인트:
  POST /webhook/solapi/friend-added     — Solapi 카카오채널 친구추가 이벤트 수신
  POST /webhook/solapi/message-received — 고객이 카카오채널로 보낸 메시지 수신
  POST /webhook/sms/deposit             — 기업은행 입금 SMS 수신
  POST /webhook/imweb/member            — 아임웹 신규 회원가입 이벤트 수신
  POST /webhook/imweb/order             — 아임웹 주문/배송 이벤트 수신
  GET  /health                          — 헬스체크

실행:
  uvicorn api:app --host 0.0.0.0 --port 8000 --reload

환경변수:
  SUPABASE_URL           Supabase 프로젝트 URL
  SUPABASE_SERVICE_KEY   service_role key (전체 쓰기 권한)
  SOLAPI_WEBHOOK_SECRET  Solapi 웹훅 Secret (X-Solapi-Secret 헤더 검증용)
  SMS_WEBHOOK_TOKEN      SMS 포워딩 앱 인증 토큰
  IMWEB_WEBHOOK_TOKEN    아임웹 웹훅 보안 토큰 (아임웹 관리자에서 설정한 값)
  IMWEB_API_KEY          아임웹 REST API 키 (폴링 배치용)
  IMWEB_API_SECRET       아임웹 REST API Secret (폴링 배치용)
"""

from __future__ import annotations

import hmac
import logging
import os
import re
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
IMWEB_WEBHOOK_TOKEN = os.environ.get("IMWEB_WEBHOOK_TOKEN", "")

app = FastAPI(
    title="이몬스 웹훅 API",
    description="Solapi 친구추가·메시지 수신, 기업은행 입금 SMS 처리",
    version="4.0.0",
)


# ──────────────────────────────────────────────
# Supabase REST 헬퍼
# ──────────────────────────────────────────────

def _supa_headers() -> dict | None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _supa_url(table: str) -> str:
    return f"{SUPABASE_URL.rstrip('/')}/rest/v1/{table}"


# ──────────────────────────────────────────────
# Solapi 웹훅 보안 검증
# ──────────────────────────────────────────────

def _verify_solapi_secret(request: Request) -> bool:
    """
    Solapi 웹훅 위조 방지 검증.
    환경변수 SOLAPI_WEBHOOK_SECRET 설정 시 X-Solapi-Secret 헤더와 비교.
    미설정 시 검증 통과 (개발·테스트 환경 허용).
    """
    expected = os.environ.get("SOLAPI_WEBHOOK_SECRET", "")
    if not expected:
        return True
    received = request.headers.get("x-solapi-secret", "")
    return hmac.compare_digest(expected, received)


# ──────────────────────────────────────────────
# 공통 헬퍼: 전화번호 정규화
# ──────────────────────────────────────────────

def _digits_only(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _normalize_phone(phone: str) -> str:
    """
    010-1234-5678, +82-10-1234-5678 등 모든 형식을 01012345678로 통일.
    아임웹과 내부 DB 간 전화번호 형식 불일치 해결용.
    """
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("82") and len(digits) >= 11:
        digits = "0" + digits[2:]
    return digits


# ──────────────────────────────────────────────
# FastAPI 라우터
# ──────────────────────────────────────────────

@app.post("/webhook/solapi/friend-added", summary="Solapi 친구추가 이벤트 수신")
@app.post("/webhook/solapi/friend_added", include_in_schema=False)
async def solapi_friend_added_webhook(request: Request) -> JSONResponse:
    """
    Solapi 카카오채널 친구추가 이벤트 수신.
    - app_users.kakao_friend_added = true (직원)
    - app_customers.kakao_friend_added = true, kakao_user_key 저장 (고객)
    - kakao_mapping 테이블에 user_key ↔ customer_id 매핑 저장
    """
    if not _verify_solapi_secret(request):
        logger.warning("friend-added: Solapi Secret 검증 실패")
        return JSONResponse({"status": "error", "reason": "unauthorized"}, status_code=401)

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    # Solapi 페이로드 형식이 변경될 수 있어 다양한 키 시도
    phone = (
        payload.get("phone")
        or payload.get("phoneNumber")
        or payload.get("to")
        or (payload.get("data", {}) or {}).get("phone")
        or ""
    )
    sender_key = (
        payload.get("senderKey")
        or payload.get("sender_key")
        or (payload.get("data", {}) or {}).get("senderKey")
        or ""
    )
    user_key = (
        payload.get("userKey")
        or payload.get("user_key")
        or (payload.get("data", {}) or {}).get("userKey")
        or ""
    )

    digits = _digits_only(phone)
    now_iso = datetime.now(timezone.utc).isoformat()
    updated_users = False
    updated_customers = False
    mapped_customer_id = None
    headers = _supa_headers()

    if headers:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # 1) 직원(app_users) kakao_friend_added 갱신
                if digits:
                    resp = await client.patch(
                        _supa_url("app_users") + f"?phone=eq.{digits}",
                        headers=headers,
                        json={"kakao_friend_added": True},
                    )
                    updated_users = resp.status_code < 300

                # 2) 고객(app_customers) 갱신 — phone1 매칭
                if digits:
                    cust_patch = {
                        "kakao_friend_added": True,
                        "kakao_friend_added_at": now_iso,
                    }
                    if user_key:
                        cust_patch["kakao_user_key"] = user_key
                    resp2 = await client.patch(
                        _supa_url("app_customers") + f"?phone1=eq.{digits}",
                        headers=headers,
                        json=cust_patch,
                    )
                    updated_customers = resp2.status_code < 300

                    # 매핑된 customer_id 조회 (kakao_mapping INSERT에 필요)
                    if updated_customers and user_key:
                        cust_resp = await client.get(
                            _supa_url("app_customers") + f"?phone1=eq.{digits}&select=id,store_name",
                            headers=headers,
                        )
                        if cust_resp.status_code < 300:
                            cust_data = cust_resp.json()
                            if cust_data:
                                mapped_customer_id = cust_data[0].get("id")
                                cust_store = cust_data[0].get("store_name")

                # 3) kakao_mapping INSERT (user_key ↔ customer_id, 중복 시 무시)
                if user_key and mapped_customer_id:
                    await client.post(
                        _supa_url("kakao_mapping"),
                        headers={**headers, "Prefer": "resolution=ignore-duplicates,return=minimal"},
                        json={
                            "kakao_user_key": user_key,
                            "customer_id": mapped_customer_id,
                            "store_name": cust_store if "cust_store" in dir() else None,
                        },
                    )

        except Exception as e:
            logger.warning("solapi_friend_added_webhook 처리 실패: %s", e)

    return JSONResponse({
        "status": "ok",
        "matched_users": updated_users,
        "matched_customers": updated_customers,
        "mapped_customer_id": mapped_customer_id,
        "phone_digits": digits or None,
        "user_key": user_key or None,
        "sender_key": sender_key or None,
    }, status_code=200)


@app.post("/webhook/solapi/message-received", summary="카카오채널 고객 수신 메시지")
async def solapi_message_received_webhook(request: Request) -> JSONResponse:
    """
    고객이 카카오 비즈니스 채널로 보낸 메시지 수신.
    - Solapi Signature 검증
    - user_key → kakao_mapping → customer_id 조회
    - app_customer_messages에 direction='inbound'로 저장
    - 200 OK 즉시 반환 (타임아웃 방지)
    """
    if not _verify_solapi_secret(request):
        logger.warning("message-received: Solapi Secret 검증 실패")
        return JSONResponse({"status": "error", "reason": "unauthorized"}, status_code=401)

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    # Solapi 인바운드 메시지 페이로드에서 필드 추출
    user_key = (
        payload.get("userKey")
        or payload.get("user_key")
        or (payload.get("data", {}) or {}).get("userKey")
        or ""
    )
    message_text = (
        payload.get("text")
        or payload.get("content")
        or payload.get("message")
        or (payload.get("data", {}) or {}).get("text")
        or ""
    )
    message_type = payload.get("messageType") or payload.get("type") or "text"
    now_iso = datetime.now(timezone.utc).isoformat()

    headers = _supa_headers()
    customer_id = None
    store_name = None
    saved = False

    if not user_key:
        logger.info("message-received: user_key 없음 — 무시")
        return JSONResponse({"status": "ignored", "reason": "no_user_key"}, status_code=200)

    if headers:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                # 1) kakao_mapping에서 customer_id 조회
                mapping_resp = await client.get(
                    _supa_url("kakao_mapping")
                    + f"?kakao_user_key=eq.{user_key}&select=customer_id,store_name",
                    headers=headers,
                )
                if mapping_resp.status_code < 300:
                    mapping_data = mapping_resp.json()
                    if mapping_data:
                        customer_id = mapping_data[0].get("customer_id")
                        store_name = mapping_data[0].get("store_name")

                # 2) app_customer_messages에 인바운드 메시지 저장
                msg_row = {
                    "customer_id": customer_id,
                    "store_name": store_name,
                    "phone": None,
                    "message_type": message_type,
                    "channel": "friendtalk",
                    "status": "received",
                    "message_body": message_text,
                    "direction": "inbound",
                    "kakao_user_key": user_key,
                    "sent_by": "customer",
                    "created_at": now_iso,
                }
                msg_resp = await client.post(
                    _supa_url("app_customer_messages"),
                    headers={**headers, "Prefer": "return=minimal"},
                    json=msg_row,
                )
                saved = msg_resp.status_code < 300
                if not saved:
                    logger.warning(
                        "message-received: app_customer_messages 저장 실패 %s: %s",
                        msg_resp.status_code, msg_resp.text[:200],
                    )

        except Exception as e:
            logger.warning("solapi_message_received_webhook 처리 실패: %s", e)

    return JSONResponse({
        "status": "ok",
        "user_key": user_key,
        "customer_id": customer_id,
        "saved": saved,
    }, status_code=200)


@app.post("/webhook/sms/deposit", summary="기업은행 입금 SMS 수신")
async def sms_deposit_webhook(request: Request) -> JSONResponse:
    """사업장 휴대폰의 SMS 포워딩 앱이 전달한 기업은행 입금 문자를 파싱·적재.

    인증: 헤더 X-Webhook-Token 이 환경변수 SMS_WEBHOOK_TOKEN 과 일치해야 함.
    본문: JSON {"message": "<문자원문>"} 또는 폼/텍스트 본문(message/text/body 키 허용).
    입금 문자만 처리하고 출금은 무시한다. 계좌 끝자리로 매장을 자동 판별한다.
    """
    expected_token = os.environ.get("SMS_WEBHOOK_TOKEN", "")
    sent_token = request.headers.get("x-webhook-token", "") or request.query_params.get("token", "")
    if expected_token and sent_token != expected_token:
        return JSONResponse({"status": "error", "reason": "unauthorized"}, status_code=401)

    # 본문에서 문자 원문 추출 (JSON / form / raw 모두 시도)
    message = ""
    try:
        payload = await request.json()
        if isinstance(payload, dict):
            message = payload.get("message") or payload.get("text") or payload.get("body") or ""
        elif isinstance(payload, str):
            message = payload
    except Exception:
        try:
            form = await request.form()
            message = form.get("message") or form.get("text") or form.get("body") or ""
        except Exception:
            try:
                message = (await request.body()).decode("utf-8", errors="ignore")
            except Exception:
                message = ""

    if not message:
        return JSONResponse({"status": "error", "reason": "empty_message"}, status_code=400)

    try:
        import deposit_sms
    except Exception as e:
        logger.error("deposit_sms import 실패: %s", e)
        return JSONResponse({"status": "error", "reason": "parser_unavailable"}, status_code=500)

    parsed = deposit_sms.parse_ibk_sms(message)
    if not parsed:
        # 입금 문자가 아니거나(출금 등) 형식 불일치 → 무시(정상 200)
        return JSONResponse({"status": "ignored", "reason": "not_a_deposit"}, status_code=200)

    headers = _supa_headers()
    if not headers:
        return JSONResponse({"status": "error", "reason": "supabase_not_configured"}, status_code=500)

    # 계좌-매장 매핑 조회 후 매장 판별
    store_name = None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            acc_resp = await client.get(
                _supa_url("app_bank_accounts") + "?select=account_suffix,store_name,is_active",
                headers=headers,
            )
            accounts = acc_resp.json() if acc_resp.status_code < 300 else []
        store_name = deposit_sms.match_store(parsed.get("account_suffix"), accounts)
    except Exception as e:
        logger.warning("계좌-매장 매핑 조회 실패: %s", e)

    row = {
        "txn_at": parsed["txn_at"],
        "counterparty": parsed.get("counterparty"),
        "amount": parsed["amount"],
        "balance": parsed.get("balance"),
        "bank_name": parsed.get("bank_name") or "기업은행",
        "account_suffix": parsed.get("account_suffix"),
        "account_masked": parsed.get("account_masked"),
        "store_name": store_name,
        "source": "auto_sms",
        "raw_message": message,
        "dedup_hash": deposit_sms.make_dedup_hash(parsed),
        "created_by": "sms_webhook",
    }

    inserted = False
    duplicated = False
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                _supa_url("app_deposits"),
                headers={**headers, "Prefer": "return=representation,resolution=ignore-duplicates"},
                json=row,
            )
            if resp.status_code < 300:
                inserted = True
            elif resp.status_code == 409:
                duplicated = True
            else:
                logger.warning("app_deposits insert 실패 %s: %s", resp.status_code, resp.text)
    except Exception as e:
        logger.error("app_deposits insert 예외: %s", e)
        return JSONResponse({"status": "error", "reason": "insert_failed"}, status_code=500)

    return JSONResponse({
        "status": "ok",
        "inserted": inserted,
        "duplicated": duplicated,
        "matched_store": store_name,
        "amount": parsed["amount"],
    }, status_code=200)


# ──────────────────────────────────────────────
# 아임웹 웹훅 — 신규 회원가입
# ──────────────────────────────────────────────

@app.post("/webhook/imweb/member", summary="아임웹 신규 회원가입 이벤트")
async def imweb_member_webhook(request: Request) -> JSONResponse:
    """
    아임웹 Pro 웹훅: 신규 회원가입 이벤트 수신.
    - 전화번호로 기존 app_customers 조회
    - 있으면: imweb_member_id, marketing_agreed 업데이트, customer_type='purchaser' 유지
    - 없으면: 신규 레코드 생성 (customer_type='member_only')
    - 웰컴 알림톡 발송 트리거
    """
    # 아임웹 웹훅 토큰 검증
    received_token = (
        request.headers.get("x-imweb-token")
        or request.headers.get("authorization", "").replace("Bearer ", "")
    )
    if IMWEB_WEBHOOK_TOKEN and not hmac.compare_digest(IMWEB_WEBHOOK_TOKEN, received_token):
        logger.warning("imweb member webhook: 토큰 검증 실패")
        return JSONResponse({"status": "error", "reason": "unauthorized"}, status_code=401)

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    logger.info("imweb member webhook 수신: %s", str(payload)[:300])

    # 아임웹 페이로드 파싱 (필드명은 아임웹 문서 기준)
    raw_phone = (
        payload.get("phone")
        or payload.get("mobile")
        or (payload.get("member") or {}).get("phone")
        or (payload.get("member") or {}).get("mobile")
        or ""
    )
    phone = _normalize_phone(raw_phone)
    name = (
        payload.get("name")
        or (payload.get("member") or {}).get("name")
        or ""
    )
    email = (
        payload.get("email")
        or (payload.get("member") or {}).get("email")
        or ""
    )
    imweb_member_id = str(
        payload.get("member_id")
        or payload.get("id")
        or (payload.get("member") or {}).get("member_id")
        or (payload.get("member") or {}).get("id")
        or ""
    )
    marketing_agreed = bool(
        payload.get("marketing_agree")
        or payload.get("marketing_agreed")
        or (payload.get("member") or {}).get("marketing_agree")
    )
    joined_at = (
        payload.get("created")
        or payload.get("join_date")
        or (payload.get("member") or {}).get("created")
        or datetime.now(timezone.utc).isoformat()
    )

    if not phone:
        logger.warning("imweb member webhook: 전화번호 없음 — 무시")
        return JSONResponse({"status": "skipped", "reason": "no_phone"})

    headers = _supa_headers()
    if not headers:
        logger.error("imweb member webhook: Supabase 미설정")
        return JSONResponse({"status": "error", "reason": "supabase_not_configured"}, status_code=500)

    customer_id: int | None = None
    is_new = False

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            # 1) 전화번호로 기존 고객 조회
            resp = await client.get(
                _supa_url("app_customers"),
                headers=headers,
                params={"phone1": f"eq.{phone}", "select": "id,customer_type", "limit": "1"},
            )
            existing = resp.json() if resp.status_code == 200 else []

            if existing:
                customer_id = int(existing[0]["id"])
                # 기존 고객 업데이트 (imweb 정보 보강)
                patch_data: dict = {
                    "imweb_member_id": imweb_member_id or None,
                    "imweb_joined_at": joined_at,
                    "marketing_agreed": marketing_agreed,
                }
                await client.patch(
                    _supa_url("app_customers") + f"?id=eq.{customer_id}",
                    headers=headers,
                    json=patch_data,
                )
                logger.info("imweb member: 기존 고객 업데이트 customer_id=%s", customer_id)
            else:
                # 신규 고객 생성
                insert_data = {
                    "name": name or "아임웹회원",
                    "phone1": phone,
                    "email": email or None,
                    "imweb_member_id": imweb_member_id or None,
                    "imweb_joined_at": joined_at,
                    "marketing_agreed": marketing_agreed,
                    "customer_type": "member_only",
                    "store_name": "아임웹",
                }
                resp2 = await client.post(
                    _supa_url("app_customers"),
                    headers={**headers, "Prefer": "return=representation"},
                    json=insert_data,
                )
                if resp2.status_code in (200, 201):
                    created = resp2.json()
                    customer_id = int(created[0]["id"]) if created else None
                    is_new = True
                    logger.info("imweb member: 신규 고객 생성 customer_id=%s phone=%s", customer_id, phone)
                else:
                    logger.error("imweb member: 신규 고객 생성 실패 %s %s", resp2.status_code, resp2.text)

        except Exception as e:
            logger.error("imweb member webhook 처리 오류: %s", e)
            return JSONResponse({"status": "error", "reason": str(e)}, status_code=500)

    # 웰컴 알림톡 발송 (신규 가입자 + 전화번호 있을 때)
    if is_new and customer_id and phone:
        try:
            from customer_channel import send_welcome_message
            send_welcome_message(
                customer_id=customer_id,
                phone=phone,
                customer_name=name or "고객",
                store_name="이몬스",
                sent_by="imweb_webhook",
            )
        except Exception as e:
            logger.warning("웰컴 메시지 발송 실패 (주문 등록에는 영향 없음): %s", e)

    return JSONResponse({
        "status": "ok",
        "customer_id": customer_id,
        "is_new": is_new,
        "phone": phone,
    })


# ──────────────────────────────────────────────
# 아임웹 웹훅 — 주문/배송 이벤트
# ──────────────────────────────────────────────

@app.post("/webhook/imweb/order", summary="아임웹 주문/배송 이벤트")
async def imweb_order_webhook(request: Request) -> JSONResponse:
    """
    아임웹 Pro 웹훅: 주문완료 / 배송완료 이벤트 수신.
    - 전화번호로 customer_id 조회 및 customer_type='purchaser' 업데이트
    - imweb_order_events 테이블에 이벤트 저장
    - 배송완료(delivered) 시 7일 후 케어 메시지 예약 시각 기록
    """
    received_token = (
        request.headers.get("x-imweb-token")
        or request.headers.get("authorization", "").replace("Bearer ", "")
    )
    if IMWEB_WEBHOOK_TOKEN and not hmac.compare_digest(IMWEB_WEBHOOK_TOKEN, received_token):
        logger.warning("imweb order webhook: 토큰 검증 실패")
        return JSONResponse({"status": "error", "reason": "unauthorized"}, status_code=401)

    try:
        payload = await request.json()
    except Exception:
        payload = {}

    logger.info("imweb order webhook 수신: %s", str(payload)[:300])

    raw_phone = (
        payload.get("phone")
        or payload.get("receiver_phone")
        or (payload.get("order") or {}).get("phone")
        or (payload.get("orderer") or {}).get("phone")
        or ""
    )
    phone = _normalize_phone(raw_phone)
    imweb_order_id = str(
        payload.get("order_id")
        or payload.get("id")
        or (payload.get("order") or {}).get("order_code")
        or ""
    )
    order_status = (
        payload.get("status")
        or payload.get("order_status")
        or (payload.get("order") or {}).get("status")
        or ""
    )
    product_name = (
        payload.get("product_name")
        or (payload.get("items") or [{}])[0].get("name", "")
        or ""
    )

    if not phone or not imweb_order_id:
        return JSONResponse({"status": "skipped", "reason": "missing_fields"})

    headers = _supa_headers()
    if not headers:
        return JSONResponse({"status": "error", "reason": "supabase_not_configured"}, status_code=500)

    customer_id: int | None = None

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            # 전화번호로 customer_id 조회
            resp = await client.get(
                _supa_url("app_customers"),
                headers=headers,
                params={"phone1": f"eq.{phone}", "select": "id", "limit": "1"},
            )
            rows = resp.json() if resp.status_code == 200 else []
            if rows:
                customer_id = int(rows[0]["id"])
                # 구매 이력 있는 고객으로 업데이트
                await client.patch(
                    _supa_url("app_customers") + f"?id=eq.{customer_id}",
                    headers=headers,
                    json={
                        "customer_type": "purchaser",
                        "last_order_at": datetime.now(timezone.utc).isoformat(),
                    },
                )

            # 배송완료 시 케어 메시지 발송 예정 시각 계산 (7일 후)
            from datetime import timedelta
            care_send_at = None
            if order_status in ("delivered", "배송완료", "DELIVERED"):
                care_send_at = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()

            # imweb_order_events 저장 (중복 시 업데이트)
            event_data = {
                "imweb_order_id": imweb_order_id,
                "customer_id": customer_id,
                "phone": phone,
                "product_name": product_name,
                "order_status": order_status,
                "raw_payload": payload,
                "care_send_at": care_send_at,
            }
            await client.post(
                _supa_url("imweb_order_events"),
                headers={**headers, "Prefer": "resolution=merge-duplicates,return=representation"},
                json=event_data,
            )
            logger.info(
                "imweb order: order_id=%s status=%s customer_id=%s care_at=%s",
                imweb_order_id, order_status, customer_id, care_send_at,
            )

        except Exception as e:
            logger.error("imweb order webhook 처리 오류: %s", e)
            return JSONResponse({"status": "error", "reason": str(e)}, status_code=500)

    return JSONResponse({
        "status": "ok",
        "order_id": imweb_order_id,
        "order_status": order_status,
        "customer_id": customer_id,
    })


@app.get("/health", summary="헬스체크")
async def health() -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "supabase_configured": bool(SUPABASE_URL and SUPABASE_SERVICE_KEY),
        "solapi_secret_set": bool(os.environ.get("SOLAPI_WEBHOOK_SECRET")),
        "imweb_webhook_configured": bool(IMWEB_WEBHOOK_TOKEN),
    })


# ──────────────────────────────────────────────
# 직접 실행 시 uvicorn 사용
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)
