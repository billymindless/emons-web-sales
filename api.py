# -*- coding: utf-8 -*-
"""
이몬스 웹훅 FastAPI 서버 (독립 실행 프로세스).

현재 제공 엔드포인트:
  POST /webhook/solapi/friend-added     — Solapi 카카오채널 친구추가 이벤트 수신
  POST /webhook/solapi/message-received — 고객이 카카오채널로 보낸 메시지 수신
  POST /webhook/sms/deposit             — 기업은행 입금 SMS 수신
  POST /webhook/imweb/member            — 아임웹 신규 회원가입 이벤트 수신
  POST /webhook/imweb/order             — 아임웹 주문/배송 이벤트 수신
  POST /channel-talk/custom-tab         — 채널톡 Custom Tab 고객 정보 조회
  GET  /health                          — 헬스체크

실행:
  uvicorn api:app --host 0.0.0.0 --port 8000 --reload

환경변수:
  SUPABASE_URL              Supabase 프로젝트 URL
  SUPABASE_SERVICE_KEY      service_role key (전체 쓰기 권한)
  SOLAPI_WEBHOOK_SECRET     Solapi 웹훅 Secret (X-Solapi-Secret 헤더 검증용)
  SMS_WEBHOOK_TOKEN         SMS 포워딩 앱 인증 토큰
  IMWEB_WEBHOOK_TOKEN       아임웹 웹훅 보안 토큰 (아임웹 관리자에서 설정한 값)
  IMWEB_API_KEY             아임웹 REST API 키 (폴링 배치용)
  IMWEB_API_SECRET          아임웹 REST API Secret (폴링 배치용)
  MOMO_APP_URL              momo Streamlit 앱 도메인 (예: https://emons.streamlit.app)
  CHANNEL_TALK_DEFAULT_STORE  채널톡 자동 가입 시 기본 store_name (기본값: '채널톡')
  GEMINI_API_KEY              Google Gemini API 키 (VOC 자동 분석용, aistudio.google.com 발급)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
IMWEB_WEBHOOK_TOKEN = os.environ.get("IMWEB_WEBHOOK_TOKEN", "")
MOMO_APP_URL = os.environ.get("MOMO_APP_URL", "https://emons.streamlit.app").rstrip("/")
CHANNEL_TALK_DEFAULT_STORE = os.environ.get("CHANNEL_TALK_DEFAULT_STORE", "채널톡")

# 채널톡 → momo 자동 로그인용 환경변수 (app.py와 동일한 EMONS_AUTH_SECRET 사용)
# Render 환경변수에 아래 값들을 설정:
#   EMONS_AUTH_SECRET     — app.py와 공유하는 서명 비밀키
#   CT_AGENT_USER_ID      — 채널톡 전용 app_users.id
#   CT_AGENT_USERNAME     — app_users.username
#   CT_AGENT_ROLE         — 역할 (superadmin / store_admin / user)
#   CT_AGENT_STORE_ID     — app_users.store_id (선택)
#   CT_AGENT_DB_FILENAME  — 매장 db_filename (선택)
_CT_AUTH_SECRET = os.environ.get("EMONS_AUTH_SECRET", "emons-default-secret-change-in-production")
_CT_AGENT_USER_ID = int(os.environ.get("CT_AGENT_USER_ID", "0") or 0)
_CT_AGENT_USERNAME = os.environ.get("CT_AGENT_USERNAME", "")
_CT_AGENT_ROLE = os.environ.get("CT_AGENT_ROLE", "user")
_CT_AGENT_STORE_ID = os.environ.get("CT_AGENT_STORE_ID")
_CT_AGENT_DB_FILENAME = os.environ.get("CT_AGENT_DB_FILENAME", "")


def _make_ct_auth_token() -> str | None:
    """채널톡 버튼용 자동 로그인 토큰 생성. 환경변수 미설정 시 None 반환."""
    if not _CT_AGENT_USER_ID or not _CT_AGENT_USERNAME:
        return None
    now = time.time()
    payload = {
        "user_id": _CT_AGENT_USER_ID,
        "username": _CT_AGENT_USERNAME,
        "role": _CT_AGENT_ROLE,
        "store_id": int(_CT_AGENT_STORE_ID) if _CT_AGENT_STORE_ID else None,
        "db_filename": _CT_AGENT_DB_FILENAME,
        "logged_at": now,
        "exp": now + 30 * 24 * 3600,  # 30일 유효
    }
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(payload, sort_keys=True).encode()
    ).decode()
    sig = hmac.new(
        _CT_AUTH_SECRET.encode(), payload_b64.encode(), hashlib.sha256
    ).hexdigest()
    return f"{payload_b64}.{sig}"

app = FastAPI(
    title="이몬스 웹훅 API",
    description="Solapi 친구추가·메시지 수신, 기업은행 입금 SMS 처리",
    version="4.0.0",
)

# CORS — 채널톡 데스크 브라우저에서 직접 fetch 요청 허용
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 채널톡 도메인이 다양하므로 전체 허용
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
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
                # phone1/phone2가 하이픈 포함/미포함 등 다양한 형식으로 저장될 수 있어
                # _phone_variants로 OR 매칭 수행
                variants = _phone_variants(digits) if digits else []

                # 1) 직원(app_users) kakao_friend_added 갱신 — phone 다중 형식 매칭
                if variants:
                    user_or_parts = [f"phone.eq.{v}" for v in variants]
                    user_or_filter = "(" + ",".join(user_or_parts) + ")"
                    resp = await client.patch(
                        _supa_url("app_users") + f"?or={user_or_filter}",
                        headers=headers,
                        json={"kakao_friend_added": True},
                    )
                    updated_users = resp.status_code < 300

                # 2) 고객(app_customers) 갱신 — phone1/phone2 다중 형식 매칭
                if variants:
                    cust_patch = {
                        "kakao_friend_added": True,
                        "kakao_friend_added_at": now_iso,
                    }
                    if user_key:
                        cust_patch["kakao_user_key"] = user_key

                    cust_or_parts: list[str] = []
                    for v in variants:
                        cust_or_parts.append(f"phone1.eq.{v}")
                        cust_or_parts.append(f"phone2.eq.{v}")
                    cust_or_filter = "(" + ",".join(cust_or_parts) + ")"

                    resp2 = await client.patch(
                        _supa_url("app_customers") + f"?or={cust_or_filter}",
                        headers=headers,
                        json=cust_patch,
                    )
                    updated_customers = resp2.status_code < 300

                    # 매핑된 customer_id 조회 (kakao_mapping INSERT에 필요)
                    if updated_customers and user_key:
                        cust_resp = await client.get(
                            _supa_url("app_customers")
                            + f"?or={cust_or_filter}&select=id,store_name&limit=1",
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


# ──────────────────────────────────────────────
# 채널톡 Snippet — Pydantic 모델
# 실제 채널톡 요청 구조: { "user": {...}, "channel": {...}, "manager": {...}, "params": {...} }
# ──────────────────────────────────────────────

class CTUser(BaseModel):
    """채널톡 Snippet 요청의 user 필드."""
    name: str | None = None
    mobileNumber: str | None = None

    class Config:
        extra = "allow"


class ChannelTalkPayload(BaseModel):
    """채널톡 Snippet initialize/submit 요청 payload."""
    user: CTUser | None = None

    class Config:
        extra = "allow"


# ──────────────────────────────────────────────
# 채널톡 Snippet — 응답 빌더 헬퍼 (공식 v0 스펙)
# https://developers.channel.io/en/articles/5ddc332c
# 스키마: {"version": "v0", "layout": [...], "params": {}}
# 컴포넌트: text, key-value, button (각 항목 id 필수)
# ──────────────────────────────────────────────

def _ct_text(component_id: str, text: str, style: str = "paragraph") -> dict:
    return {"id": component_id, "type": "text", "text": text, "style": style}


def _ct_keyvalue(component_id: str, items: list[dict]) -> dict:
    return {"id": component_id, "type": "key-value", "items": items}


def _ct_button(component_id: str, label: str, url: str) -> dict:
    return {
        "id": component_id,
        "type": "button",
        "label": label,
        "action": {"type": "url", "url": url},
        "style": "primary",
    }


def _ct_error_response(message: str, params: dict | None = None) -> dict:
    """모든 에러 상황에서도 채널톡 UI가 깨지지 않도록 정상 JSON 반환."""
    return {
        "snippet": {
            "version": "v0",
            "layout": [_ct_text("error-msg", message)],
            "params": params or {},
        }
    }


def _format_currency(value: int | float | None) -> str:
    if value is None:
        return "0원"
    try:
        return f"{int(value):,}원"
    except Exception:
        return "0원"


def _build_ct_response(
    customer_name: str,
    is_new: bool,
    order_info: dict | None,
    cleaned_phone: str,
    params: dict | None = None,
    store_name: str = "",
    chat_history: list[dict] | None = None,
    lead_info: dict | None = None,
) -> dict:
    """채널톡 Snippet JSON 응답 생성 (공식 v0 스펙).
    응답 최상위 키는 반드시 "snippet" 이어야 함.
    auth_token이 있으면 매직링크에 ?auth=토큰 포함 → 자동 로그인.
    chat_history가 있으면 과거 상담 이력을 Snippet 하단에 추가.
    """
    status_label = "가망고객" if (is_new and not order_info) else ("신규 자동가입" if is_new else "기존 고객")
    layout: list[dict] = [
        _ct_text("customer-title", f"{customer_name} ({status_label})", style="h2"),
    ]

    if order_info and order_info.get("total_amount") is not None:
        category = order_info.get("category") or "상품"
        total = int(order_info.get("total_amount") or 0)
        paid = int(order_info.get("paid_total") or 0)
        balance = total - paid
        items = [
            {"key": "구매 매장", "value": store_name or "-"},
            {"key": "담당자", "value": str(order_info.get("employee_names") or "-")},
            {"key": "최근 주문", "value": str(category)},
            {"key": "계약일", "value": str(order_info.get("order_date") or "-")},
            {"key": "배송일", "value": str(order_info.get("delivery_date") or "-")},
            {"key": "결제금액", "value": _format_currency(total)},
            {"key": "입금완료", "value": _format_currency(paid)},
            {"key": "잔금", "value": _format_currency(balance)},
        ]
        layout.append(_ct_keyvalue("order-info", items))
    else:
        no_order_items = [{"key": "구매 매장", "value": store_name or "-"}]
        # 리드 정보가 있으면 Snippet에 노출
        if lead_info:
            no_order_items.extend([
                {"key": "유입 경로", "value": lead_info.get("lead_source", "-")},
                {"key": "상담 메모", "value": (lead_info.get("memo") or "-")[:60]},
                {"key": "상담일", "value": str(lead_info.get("created_at", ""))[:10] or "-"},
                {"key": "다음 연락", "value": str(lead_info.get("next_contact_date") or "-")},
            ])
        else:
            no_order_items.append({"key": "구매 내역", "value": "없음"})
        layout.append(_ct_keyvalue("no-order", no_order_items))

    # 과거 상담 이력 (최신 3건)
    if chat_history:
        layout.append(_ct_text("history-title", "─── 과거 상담 이력 ───", style="paragraph"))
        for i, rec in enumerate(chat_history[:3]):
            date_str = str(rec.get("created_at", ""))[:10]
            channel = rec.get("channel", "")
            summary = (rec.get("summary") or "")[:60]
            layout.append(_ct_text(f"hist-{i}", f"[{date_str} / {channel}] {summary}"))

    magic_url = f"{MOMO_APP_URL}/?home=1&menu=new_sales&phone={cleaned_phone}"
    ct_token = _make_ct_auth_token()
    if ct_token:
        magic_url += f"&auth={ct_token}"
    layout.append(_ct_button("magic-link-btn", "momo 시스템에서 열기", magic_url))

    return {
        "snippet": {
            "version": "v0",
            "layout": layout,
            "params": params or {},
        }
    }


# ──────────────────────────────────────────────
# 채널톡 — Supabase 조회/삽입 (내부 함수)
# ──────────────────────────────────────────────

def _phone_variants(cleaned: str) -> list[str]:
    """01012345678 → [01012345678, 010-1234-5678, 010 1234 5678] 등 검색 변형."""
    variants = {cleaned}
    if len(cleaned) == 11 and cleaned.startswith("010"):
        variants.add(f"{cleaned[:3]}-{cleaned[3:7]}-{cleaned[7:]}")
        variants.add(f"{cleaned[:3]} {cleaned[3:7]} {cleaned[7:]}")
        variants.add(f"{cleaned[:3]}.{cleaned[3:7]}.{cleaned[7:]}")
    return list(variants)


async def _ct_get_or_create_customer(
    client: httpx.AsyncClient,
    headers: dict,
    cleaned_phone: str,
    name_from_ct: str,
) -> tuple[int | None, str, str, bool]:
    """
    전화번호로 app_customers 조회 → 없으면 자동 가입.
    phone1/phone2 + 정규화/하이픈/공백 형식 모두 검색.
    반환: (customer_id, name, store_name, is_new)
    """
    variants = _phone_variants(cleaned_phone)
    # Supabase or 필터: or=(phone1.eq.X,phone1.eq.Y,phone2.eq.X,phone2.eq.Y)
    or_parts: list[str] = []
    for v in variants:
        or_parts.append(f"phone1.eq.{v}")
        or_parts.append(f"phone2.eq.{v}")
    or_filter = "(" + ",".join(or_parts) + ")"

    resp = await client.get(
        _supa_url("app_customers"),
        headers=headers,
        params={"or": or_filter, "select": "id,name,phone1,phone2,store_name", "limit": "1"},
    )
    rows = resp.json() if resp.status_code == 200 else []
    logger.info(
        "channel-talk lookup: variants=%s, status=%s, found=%d",
        variants, resp.status_code, len(rows),
    )
    if rows:
        return (
            int(rows[0]["id"]),
            str(rows[0].get("name") or name_from_ct or ""),
            str(rows[0].get("store_name") or ""),
            False,
        )

    # 2) 신규 자동 가입
    insert_data = {
        "store_name": CHANNEL_TALK_DEFAULT_STORE,
        "name": name_from_ct or "채널톡고객",
        "phone1": cleaned_phone,
        "source": "채널톡_자동가입",
    }
    resp2 = await client.post(
        _supa_url("app_customers"),
        headers={**headers, "Prefer": "return=representation"},
        json=insert_data,
    )
    if resp2.status_code in (200, 201):
        created = resp2.json()
        if created:
            return (
                int(created[0]["id"]),
                str(created[0].get("name") or insert_data["name"]),
                str(created[0].get("store_name") or CHANNEL_TALK_DEFAULT_STORE),
                True,
            )
    logger.warning("channel-talk: 자동 가입 실패 %s %s", resp2.status_code, resp2.text[:200])
    return None, name_from_ct or "채널톡고객", "", False


async def _ct_fetch_latest_order_with_balance(
    client: httpx.AsyncClient,
    headers: dict,
    customer_id: int,
) -> dict | None:
    """
    customer_id의 최근 1건 주문 + 결제 합계 조회.
    반환: {category, total_amount, paid_total} 또는 None.
    """
    # 최근 주문 1건
    resp = await client.get(
        _supa_url("app_orders"),
        headers=headers,
        params={
            "customer_id": f"eq.{customer_id}",
            "select": "id,category,total_amount,db_filename,order_date,delivery_date,employee_names",
            "order": "created_at.desc",
            "limit": "1",
        },
    )
    orders = resp.json() if resp.status_code == 200 else []
    if not orders:
        return None

    order = orders[0]
    order_id = int(order["id"])
    db_filename = order.get("db_filename") or ""

    # 결제 합계 조회 (해당 order_id + db_filename)
    pay_params: dict = {
        "order_id": f"eq.{order_id}",
        "select": "amount",
    }
    if db_filename:
        pay_params["db_filename"] = f"eq.{db_filename}"
    resp2 = await client.get(
        _supa_url("app_payments"),
        headers=headers,
        params=pay_params,
    )
    payments = resp2.json() if resp2.status_code == 200 else []
    paid_total = sum(int(p.get("amount") or 0) for p in payments)

    return {
        "order_id": order_id,
        "category": order.get("category"),
        "total_amount": order.get("total_amount"),
        "paid_total": paid_total,
        "order_date": (str(order.get("order_date") or "")[:10] or None),
        "delivery_date": (str(order.get("delivery_date") or "")[:10] or None),
        "employee_names": (str(order.get("employee_names") or "") or None),
    }


# ──────────────────────────────────────────────
# 채널톡 보조 함수 — 상담 이력 / 리드 조회 / Open API
# ──────────────────────────────────────────────

async def _ct_fetch_chat_history(
    client: httpx.AsyncClient,
    headers: dict,
    cleaned_phone: str,
    limit: int = 3,
) -> list[dict]:
    """app_chat_history 최신순 조회."""
    try:
        resp = await client.get(
            _supa_url("app_chat_history"),
            headers=headers,
            params={
                "customer_phone": f"eq.{cleaned_phone}",
                "order": "created_at.desc",
                "limit": str(limit),
                "select": "channel,summary,created_at",
            },
        )
        return resp.json() if resp.status_code == 200 else []
    except Exception as e:
        logger.warning("chat_history 조회 실패: %s", e)
        return []


async def _ct_fetch_lead_info(
    client: httpx.AsyncClient,
    headers: dict,
    cleaned_phone: str,
) -> dict | None:
    """app_leads 최신 활성 리드 1건 조회."""
    try:
        resp = await client.get(
            _supa_url("app_leads"),
            headers=headers,
            params={
                "phone": f"eq.{cleaned_phone}",
                "lead_stage": "not.in.(4_계약완료,5_계약실패)",
                "order": "created_at.desc",
                "limit": "1",
                "select": "id,lead_source,lead_stage,memo,next_contact_date,assigned_store,created_at",
            },
        )
        data = resp.json() if resp.status_code == 200 else []
        return data[0] if data else None
    except Exception as e:
        logger.warning("lead 조회 실패: %s", e)
        return None


async def _ct_register_online_lead(
    client: httpx.AsyncClient,
    headers: dict,
    cleaned_phone: str,
    name: str,
) -> None:
    """채널톡 신규 고객 → app_leads에 온라인_채널톡으로 자동 등록 (중복 시 무시)."""
    try:
        # 이미 리드가 있으면 등록 생략
        existing = await _ct_fetch_lead_info(client, headers, cleaned_phone)
        if existing:
            return

        from datetime import timedelta
        now_utc = datetime.now(timezone.utc)
        next_nurture_at = (now_utc + timedelta(days=2)).isoformat()

        await client.post(
            _supa_url("app_leads"),
            headers={**headers, "Prefer": "return=minimal"},
            json={
                "store_name": CHANNEL_TALK_DEFAULT_STORE,
                "phone": cleaned_phone,
                "name": name or "",
                "lead_source": "온라인_채널톡",
                "lead_stage": "1_신규유입",
                "assigned_store": CHANNEL_TALK_DEFAULT_STORE,
                "nurturing_step": 0,
                "next_nurture_at": next_nurture_at,
            },
            timeout=5.0,
        )
        logger.info("온라인 채널톡 리드 자동 등록: phone=%s", cleaned_phone)
    except Exception as e:
        logger.warning("온라인 리드 등록 실패 (계속 진행): %s", e)


async def _ct_upsert_user(
    phone: str,
    name: str,
    tags: list[str],
) -> bool:
    """
    채널톡 Open API — 유저 프로필 upsert + 태그 주입.
    환경변수: CHANNEL_TALK_ACCESS_KEY, CHANNEL_TALK_ACCESS_SECRET
    """
    access_key = os.environ.get("CHANNEL_TALK_ACCESS_KEY", "")
    access_secret = os.environ.get("CHANNEL_TALK_ACCESS_SECRET", "")
    if not access_key or not access_secret:
        logger.warning("채널톡 Open API 키 미설정 — ct_upsert_user 건너뜀")
        return False

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                "https://api.channel.io/v5/users",
                auth=(access_key, access_secret),
                json={
                    "mobileNumber": phone,
                    "name": name or "",
                    "tags": tags,
                },
            )
            if resp.status_code < 400:
                logger.info("채널톡 유저 upsert 성공: phone=%s tags=%s", phone, tags)
                return True
            logger.warning("채널톡 유저 upsert 실패: %s %s", resp.status_code, resp.text[:200])
            return False
    except Exception as e:
        logger.warning("채널톡 upsert 예외: %s", e)
        return False


# ──────────────────────────────────────────────
# 채널톡 Custom Tab 엔드포인트
# ──────────────────────────────────────────────

@app.api_route(
    "/channel-talk/custom-tab",
    methods=["POST", "PUT"],
    summary="채널톡 Snippet 고객 정보 조회",
)
async def channel_talk_custom_tab(request: Request) -> JSONResponse:
    """
    채널톡 Snippet webhook (POST와 PUT 모두 지원).
    1) 요청 본문 RAW 로깅 (디버깅용)
    2) 전화번호 추출 → 정규화
    3) Supabase app_customers 조회 (없으면 자동 가입)
    4) 최근 주문 + 결제 합계 조회 → 잔금 계산
    5) 채널톡 Snippet v0 JSON 응답 + RAW 로깅
    """
    try:
        # ── 디버깅: 요청 본문 RAW 로깅 (전체) ──
        raw_body = await request.body()
        try:
            payload_dict = json.loads(raw_body) if raw_body else {}
        except Exception:
            payload_dict = {}
        body_str = raw_body.decode("utf-8", errors="replace")
        logger.info(
            "channel-talk REQUEST: method=%s, len=%d, keys=%s",
            request.method, len(body_str), list(payload_dict.keys()),
        )
        logger.info("channel-talk REQUEST BODY FULL: %s", body_str)

        # 요청 params 추출 (응답에 echo back)
        req_params = payload_dict.get("params") or {}

        # 전화번호 추출 (여러 위치에서 시도)
        # 채널톡은 payload 구조가 요청 유형에 따라 다름:
        #   Snippet 요청: payload["user"] 또는 payload["context"]["profile"]
        #   일반 웹훅 이벤트: payload["refers"]["user"]
        raw_phone = ""
        name_from_ct = "채널톡고객"

        # 1) Snippet 요청 — payload["user"]
        user_obj = payload_dict.get("user") or {}
        # 2) 일반 웹훅 이벤트 — payload["refers"]["user"]
        if not user_obj:
            refers = payload_dict.get("refers") or {}
            user_obj = refers.get("user") or {}
        # 3) context.profile (일부 Snippet 버전)
        if not user_obj:
            ctx = payload_dict.get("context") or {}
            user_obj = ctx.get("profile") or {}

        if isinstance(user_obj, dict):
            raw_phone = (
                user_obj.get("mobileNumber")
                or user_obj.get("mobile_number")
                or user_obj.get("phone")
                or (user_obj.get("profile") or {}).get("mobileNumber")
                or ""
            )
            name_from_ct = user_obj.get("name") or "채널톡고객"

        # 디버그: 전화번호 추출 경로 로깅
        logger.info(
            "channel-talk PHONE DEBUG: raw=%r user_obj_type=%s user_obj_keys=%s",
            raw_phone,
            type(user_obj).__name__,
            list(user_obj.keys()) if isinstance(user_obj, dict) else "N/A",
        )

        cleaned_phone = _normalize_phone(raw_phone)
        logger.info("channel-talk PHONE CLEANED: %r", cleaned_phone)

        if not cleaned_phone:
            # 익명 고객 안내 — 전화번호 확보 유도
            anon_response = {
                "snippet": {
                    "version": "v0",
                    "layout": [
                        _ct_text("anon-warn", "익명 고객", style="h2"),
                        _ct_text(
                            "anon-guide",
                            "우측 프로필에 연락처를 입력하거나\n"
                            "서포트봇 폼으로 전화번호를 받으면\n"
                            "momo DB와 자동 연동됩니다.",
                        ),
                    ],
                    "params": req_params,
                }
            }
            logger.info("channel-talk RESPONSE (anonymous): no phone")
            return JSONResponse(anon_response)

        headers = _supa_headers()
        if not headers:
            logger.error("channel-talk: Supabase 환경변수 미설정")
            return JSONResponse(_ct_error_response(
                "서버 에러: 데이터베이스 연결이 설정되지 않았습니다.",
                params=req_params,
            ))

        chat_history: list[dict] = []
        lead_info: dict | None = None

        async with httpx.AsyncClient(timeout=10.0) as client:
            customer_id, customer_name, customer_store, is_new = await _ct_get_or_create_customer(
                client, headers, cleaned_phone, name_from_ct,
            )

            order_info: dict | None = None
            if customer_id is not None:
                try:
                    order_info = await _ct_fetch_latest_order_with_balance(
                        client, headers, customer_id,
                    )
                except Exception as e:
                    logger.warning("channel-talk: 주문 조회 실패 (계속 진행): %s", e)

            # 과거 상담 이력 조회 (app_chat_history)
            try:
                chat_history = await _ct_fetch_chat_history(client, headers, cleaned_phone)
            except Exception as e:
                logger.warning("channel-talk: chat_history 조회 실패 (계속 진행): %s", e)

            # 리드 정보 조회 (구매 이력 없는 경우 노출용)
            if not order_info:
                try:
                    lead_info = await _ct_fetch_lead_info(client, headers, cleaned_phone)
                except Exception as e:
                    logger.warning("channel-talk: lead 조회 실패 (계속 진행): %s", e)

                # 신규 고객이고 리드도 없으면 온라인 채널톡 리드로 자동 등록
                if is_new and not lead_info:
                    await _ct_register_online_lead(client, headers, cleaned_phone, name_from_ct)

        # 채널톡 우측 패널에 momo DB의 이름/매장 태그 동기화 (Open API)
        # 익명("채널톡고객") 상태가 아닐 때만 호출하여 의미 있는 정보만 push
        try:
            if customer_name and customer_name not in ("", "채널톡고객"):
                _ct_tags: list[str] = []
                if customer_store:
                    _ct_tags.append(str(customer_store))
                if order_info:
                    _ct_tags.append("기존고객")
                elif lead_info:
                    _ct_tags.append("리드")
                else:
                    _ct_tags.append("신규")
                await _ct_upsert_user(cleaned_phone, customer_name, _ct_tags)
        except Exception as e:
            logger.warning("channel-talk: _ct_upsert_user 동기화 실패 (계속 진행): %s", e)

        response_body = _build_ct_response(
            customer_name=customer_name,
            is_new=is_new,
            order_info=order_info,
            cleaned_phone=cleaned_phone,
            params=req_params,
            store_name=customer_store,
            chat_history=chat_history if chat_history else None,
            lead_info=lead_info,
        )
        logger.info(
            "channel-talk RESPONSE (ok): phone=%s, is_new=%s, response=%s",
            cleaned_phone, is_new,
            json.dumps(response_body, ensure_ascii=False)[:1000],
        )
        return JSONResponse(response_body)

    except Exception as e:
        logger.error("channel-talk webhook error: %s", e, exc_info=True)
        return JSONResponse(_ct_error_response(
            f"서버 에러: 데이터를 불러오지 못했습니다. ({type(e).__name__})"
        ))


# ──────────────────────────────────────────────
# 채널톡 chat.closed 웹훅 — 대화 전문 자동 아카이빙
# ──────────────────────────────────────────────

@app.post("/channel-talk/webhook", summary="채널톡 이벤트 웹훅 (chat.closed 아카이빙)")
async def channel_talk_webhook(request: Request) -> JSONResponse:
    """
    채널톡 chat.closed 이벤트를 수신하여 대화 전문을 app_chat_history에 저장.
    채널톡 Developer Portal에서 chat.closed 이벤트를 이 URL로 등록 필요.
    """
    try:
        raw_body = await request.body()
        try:
            payload = json.loads(raw_body) if raw_body else {}
        except Exception:
            payload = {}

        event = payload.get("event") or payload.get("type") or ""
        logger.info("channel-talk webhook: event=%s", event)

        if event != "chat.closed":
            return JSONResponse({"ok": True, "skipped": True})

        chat_obj = payload.get("chat") or {}
        user_obj = payload.get("user") or {}
        manager_obj = payload.get("manager") or {}

        chat_id = str(chat_obj.get("id") or "")
        phone = _normalize_phone(str(user_obj.get("mobileNumber") or user_obj.get("mobile_number") or ""))
        handled_by = str(manager_obj.get("email") or manager_obj.get("name") or "")

        if not phone:
            logger.info("channel-talk chat.closed: 전화번호 없음, 저장 건너뜀")
            return JSONResponse({"ok": True, "skipped": True, "reason": "no_phone"})

        # 채널톡 Open API로 대화 전문 수집
        full_text = ""
        access_key = os.environ.get("CHANNEL_TALK_ACCESS_KEY", "")
        access_secret = os.environ.get("CHANNEL_TALK_ACCESS_SECRET", "")
        if chat_id and access_key and access_secret:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(
                        f"https://api.channel.io/v5/chats/{chat_id}/messages",
                        auth=(access_key, access_secret),
                        params={"limit": "100"},
                    )
                    if resp.status_code < 400:
                        messages = resp.json().get("messages") or []
                        lines = []
                        for m in messages:
                            author = m.get("author", {}).get("name") or "고객"
                            text = m.get("plainText") or m.get("text") or ""
                            if text:
                                lines.append(f"[{author}] {text}")
                        full_text = "\n".join(lines)
            except Exception as e:
                logger.warning("채널톡 대화 전문 수집 실패: %s", e)

        # app_chat_history 저장
        supa_hdrs = {**_supa_headers(), "Prefer": "return=minimal"}
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                _supa_url("app_chat_history"),
                headers=supa_hdrs,
                json={
                    "customer_phone": phone,
                    "channel": "채널톡_웹챗",
                    "chat_id": chat_id or None,
                    "summary": full_text[:200] if full_text else "채팅 종료",
                    "full_text": full_text or None,
                    "handled_by": handled_by,
                },
            )

        logger.info("chat.closed 아카이빙 완료: phone=%s chat_id=%s", phone, chat_id)

        # ── Gemini VOC 분석 (full_text가 있고 API 키가 설정된 경우만) ──
        gemini_key = os.environ.get("GEMINI_API_KEY", "")
        if full_text and gemini_key:
            try:
                import google.generativeai as _genai
                _genai.configure(api_key=gemini_key)
                _model = _genai.GenerativeModel(
                    model_name="gemini-1.5-flash",
                    generation_config={"temperature": 0, "response_mime_type": "application/json"},
                )
                _prompt = (
                    "다음은 가구 쇼핑몰 고객 상담 대화입니다.\n"
                    "아래 항목을 분석해 JSON으로만 응답하세요 (다른 텍스트 없이 JSON만):\n"
                    "- is_claim: bool (클레임·환불·AS 요청 여부)\n"
                    "- complaint_category: str (배송/제품불량/가격/응대/기타/없음 중 하나)\n"
                    "- product_idea: str (신제품·개선 아이디어가 있으면 1문장, 없으면 빈 문자열)\n"
                    "- summary: str (대화 내용 1문장 요약)\n"
                    "- sentiment: str (긍정/중립/부정 중 하나)\n\n"
                    f"대화:\n{full_text[:3000]}"
                )
                _ai_resp = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: _model.generate_content(_prompt)
                )
                _ai_data = json.loads(_ai_resp.text)

                # app_voc_insights upsert (chat_id UNIQUE → 중복 건너뜀)
                _voc_hdrs = {**_supa_headers(), "Prefer": "resolution=ignore-duplicates,return=minimal"}
                async with httpx.AsyncClient(timeout=5.0) as _vcl:
                    await _vcl.post(
                        _supa_url("app_voc_insights"),
                        headers=_voc_hdrs,
                        json={
                            "chat_id": chat_id or None,
                            "customer_phone": phone,
                            "handled_by": handled_by,
                            "is_claim": bool(_ai_data.get("is_claim", False)),
                            "complaint_category": str(_ai_data.get("complaint_category") or "").strip(),
                            "product_idea": str(_ai_data.get("product_idea") or "").strip(),
                            "summary": str(_ai_data.get("summary") or "").strip(),
                            "sentiment": str(_ai_data.get("sentiment") or "").strip(),
                            "raw_json": _ai_data,
                            "source": "webhook",
                        },
                    )
                logger.info(
                    "VOC 분석 완료: chat_id=%s sentiment=%s is_claim=%s",
                    chat_id, _ai_data.get("sentiment"), _ai_data.get("is_claim"),
                )
            except Exception as _ve:
                logger.warning("VOC Gemini 분석 실패 (비치명적): %s", _ve)

        return JSONResponse({"ok": True})

    except Exception as e:
        logger.error("channel-talk webhook error: %s", e, exc_info=True)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


# 채널톡 웹훅 별칭 — base URL 등록 시 채널톡이 /channel-talk/webhook 경로를 자동 추가하는 경우 대비
app.add_api_route(
    "/channel-talk/webhook/channel-talk/webhook",
    channel_talk_webhook,
    methods=["POST"],
    include_in_schema=False,
)


# ──────────────────────────────────────────────
# 리드 넛징 예약 발송 실행기
# ──────────────────────────────────────────────

@app.get("/run-lead-care", summary="리드 넛징 예약 발송 실행")
async def run_lead_care() -> JSONResponse:
    """
    app_leads에서 next_nurture_at <= now() 조건 대상을 조회하여
    유입 경로별 넛징 메시지를 발송하고 nurturing_step을 갱신한다.
    GitHub Actions 또는 Render Cron으로 매일 오전 10시 실행.
    """
    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
        return JSONResponse({"ok": False, "error": "supabase not configured"}, status_code=500)

    now_iso = datetime.now(timezone.utc).isoformat()
    headers = _supa_headers()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                _supa_url("app_leads"),
                headers=headers,
                params={
                    "nurturing_step": "lt.2",
                    "next_nurture_at": f"lte.{now_iso}",
                    "lead_stage": "not.in.(4_계약완료,5_계약실패)",
                    "select": "id,phone,name,lead_source,lead_stage",
                    "limit": "100",
                },
            )
            leads = resp.json() if resp.status_code == 200 else []
    except Exception as e:
        logger.error("run-lead-care 조회 실패: %s", e)
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    if not leads:
        return JSONResponse({"ok": True, "processed": 0})

    try:
        from lead_manager import send_nurturing_message
    except ImportError:
        return JSONResponse({"ok": False, "error": "lead_manager import 실패"}, status_code=500)

    sent, failed = 0, 0
    for lead in leads:
        try:
            result = send_nurturing_message(lead)
            if result.get("status") in ("sent", "lms_fallback"):
                sent += 1
            else:
                failed += 1
        except Exception as e:
            logger.warning("넛징 발송 실패 lead_id=%s: %s", lead.get("id"), e)
            failed += 1

    logger.info("run-lead-care 완료: sent=%d failed=%d", sent, failed)
    return JSONResponse({"ok": True, "processed": len(leads), "sent": sent, "failed": failed})


@app.get("/health", summary="헬스체크")
async def health() -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "supabase_configured": bool(SUPABASE_URL and SUPABASE_SERVICE_KEY),
        "solapi_secret_set": bool(os.environ.get("SOLAPI_WEBHOOK_SECRET")),
        "imweb_webhook_configured": bool(IMWEB_WEBHOOK_TOKEN),
        "momo_app_url_set": bool(MOMO_APP_URL and MOMO_APP_URL != "https://emons.streamlit.app"),
    })


# ──────────────────────────────────────────────
# 직접 실행 시 uvicorn 사용
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)
