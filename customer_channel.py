# -*- coding: utf-8 -*-
"""
고객 카카오 채널 연동 모듈 (customer_channel.py)

구매 완료 직후 카카오 알림톡/친구톡/SMS 발송 및 발송 이력 관리.

발송 흐름:
  1. kakao_friend_added == True  → CTA 친구톡(send_friendtalk) 발송
  2. kakao_friend_added == False → ATA 알림톡(send_alimtalk) 시도
       알림톡 템플릿 미설정 시 채널 초대 SMS 발송으로 폴백

발송 이력은 Supabase app_customer_messages 테이블에 기록.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

# ──────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────

DEFAULT_INVITE_SMS_TEMPLATE = (
    "[이몬스] {이름}님, 구매해 주셔서 감사합니다.\n"
    "배송 안내 및 AS 문의는 카카오채널을 이용해 주세요.\n"
    "채널 추가: {채널URL}"
)

DEFAULT_PURCHASE_FRIENDTALK_TEMPLATE = (
    "{이름}님, 주문이 확인되었습니다.\n"
    "품목: {품목}\n"
    "배송 예정일: {배송일}\n"
    "문의사항은 이 채널로 연락해 주세요."
)


# ──────────────────────────────────────────────
# Supabase 헬퍼
# ──────────────────────────────────────────────

def _get_supabase():
    try:
        from app import get_supabase_client  # type: ignore
        client, err = get_supabase_client()
        return client if not err else None
    except Exception:
        return None


def _get_kakao_channel_url() -> str:
    """secrets.toml 또는 환경변수에서 카카오 채널 URL 반환."""
    try:
        import streamlit as st
        sec = st.secrets.get("solapi", {}) if hasattr(st, "secrets") else {}
        url = sec.get("kakao_channel_url", "")
        if url:
            return url
    except Exception:
        pass
    return os.environ.get("KAKAO_CHANNEL_URL", "https://pf.kakao.com/_XXXXX")


def _get_purchase_template_code() -> str:
    """secrets.toml 또는 환경변수에서 구매 완료 알림톡 템플릿 코드 반환."""
    try:
        import streamlit as st
        sec = st.secrets.get("solapi", {}) if hasattr(st, "secrets") else {}
        return sec.get("purchase_template_code", "")
    except Exception:
        pass
    return os.environ.get("SOLAPI_PURCHASE_TEMPLATE_CODE", "")


def _normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


# ──────────────────────────────────────────────
# 발송 이력 기록
# ──────────────────────────────────────────────

def _log_message(
    customer_id: int | None,
    store_name: str,
    order_id: int | None,
    phone: str,
    message_type: str,
    channel: str,
    status: str,
    msg_id: str | None,
    message_body: str,
    error_detail: str | None,
    sent_by: str,
) -> None:
    """발송 결과를 app_customer_messages 테이블에 기록."""
    sc = _get_supabase()
    if not sc:
        return
    try:
        sc.table("app_customer_messages").insert({
            "customer_id": customer_id,
            "store_name": store_name,
            "order_id": order_id,
            "phone": phone,
            "message_type": message_type,
            "channel": channel,
            "status": status,
            "solapi_msg_id": msg_id,
            "message_body": message_body,
            "error_detail": error_detail,
            "sent_by": sent_by,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }).execute()
    except Exception:
        pass  # 발송 이력 기록 실패는 발송 자체에 영향을 주지 않음


# ──────────────────────────────────────────────
# 고객 카카오 친구 여부 조회
# ──────────────────────────────────────────────

def get_customer_kakao_status(customer_id: int) -> dict[str, Any]:
    """
    app_customers에서 카카오 채널 친구 여부 조회.

    반환:
        {
            "kakao_friend_added": bool,
            "kakao_friend_added_at": str | None,
            "phone": str | None,
            "name": str | None,
        }
    """
    sc = _get_supabase()
    if not sc or not customer_id:
        return {"kakao_friend_added": False, "kakao_friend_added_at": None, "phone": None, "name": None}
    try:
        r = sc.table("app_customers").select(
            "name, phone1, kakao_friend_added, kakao_friend_added_at"
        ).eq("id", int(customer_id)).limit(1).execute()
        if r.data:
            row = r.data[0]
            return {
                "kakao_friend_added": bool(row.get("kakao_friend_added")),
                "kakao_friend_added_at": row.get("kakao_friend_added_at"),
                "phone": row.get("phone1"),
                "name": row.get("name"),
            }
    except Exception:
        pass
    return {"kakao_friend_added": False, "kakao_friend_added_at": None, "phone": None, "name": None}


# ──────────────────────────────────────────────
# 핵심 발송 함수
# ──────────────────────────────────────────────

def send_purchase_notification(
    customer_id: int,
    phone: str,
    customer_name: str,
    order_info: dict[str, Any],
    store_name: str,
    order_id: int | None = None,
    sent_by: str = "system",
) -> dict[str, Any]:
    """
    구매 완료 직후 카카오 알림톡 또는 친구추가 유도 SMS 발송.

    Args:
        customer_id: app_customers.id
        phone: 고객 전화번호
        customer_name: 고객 이름
        order_info: {"category": str, "total_amount": int, "delivery_date": str}
        store_name: 매장명
        order_id: app_orders.id (발송 이력 연결용)
        sent_by: 발송 담당자 username

    반환:
        {"channel": str, "status": str, "msg_id": str | None, "error": str | None}
    """
    from solapi_sender import send_friendtalk, send_alimtalk  # type: ignore

    phone_digits = _normalize_phone(phone)
    if not phone_digits:
        return {"channel": "none", "status": "skipped", "msg_id": None, "error": "phone_empty"}

    kakao_status = get_customer_kakao_status(customer_id)
    is_friend = kakao_status.get("kakao_friend_added", False)

    category = str(order_info.get("category") or "")
    total_amount = order_info.get("total_amount") or 0
    delivery_date = str(order_info.get("delivery_date") or "미정")

    # 친구톡 본문 (채널 친구인 경우)
    friendtalk_body = (
        DEFAULT_PURCHASE_FRIENDTALK_TEMPLATE
        .replace("{이름}", customer_name)
        .replace("{품목}", category)
        .replace("{배송일}", delivery_date)
    )

    if is_friend:
        # 채널 친구 → CTA 친구톡 발송
        result = send_friendtalk(phone_digits, friendtalk_body, disable_sms_fallback=False)
        channel = "friendtalk"
        message_body = friendtalk_body
    else:
        # 채널 미친구 → ATA 알림톡 시도
        template_code = _get_purchase_template_code()
        if template_code:
            variables = {
                "이름": customer_name,
                "품목": category,
                "배송일": delivery_date,
                "매출금액": f"{int(total_amount):,}",
            }
            fallback_sms = (
                f"[이몬스] {customer_name}님, 구매 감사합니다.\n"
                f"품목: {category} / 배송예정: {delivery_date}\n"
                f"채널 추가: {_get_kakao_channel_url()}"
            )
            result = send_alimtalk(
                phone_digits,
                template_code,
                variables,
                fallback_sms_text=fallback_sms,
            )
            channel = "alimtalk"
            message_body = fallback_sms
        else:
            # 템플릿 미설정 → 채널 초대 SMS
            channel_url = _get_kakao_channel_url()
            sms_body = (
                DEFAULT_INVITE_SMS_TEMPLATE
                .replace("{이름}", customer_name)
                .replace("{채널URL}", channel_url)
            )
            result = send_friendtalk(phone_digits, sms_body, disable_sms_fallback=False)
            channel = "sms"
            message_body = sms_body

    _log_message(
        customer_id=customer_id,
        store_name=store_name,
        order_id=order_id,
        phone=phone_digits,
        message_type="purchase_confirm",
        channel=channel,
        status=result.get("status", "failed"),
        msg_id=result.get("msg_id"),
        message_body=message_body,
        error_detail=result.get("error"),
        sent_by=sent_by,
    )

    return {
        "channel": channel,
        "status": result.get("status"),
        "msg_id": result.get("msg_id"),
        "error": result.get("error"),
    }


def send_channel_invite_sms(
    customer_id: int,
    phone: str,
    customer_name: str,
    store_name: str,
    order_id: int | None = None,
    sent_by: str = "system",
) -> dict[str, Any]:
    """
    채널 친구추가 유도 SMS 수동 발송 (관리자 버튼용).
    카카오 채널 URL을 포함한 안내 문자를 발송하고 이력 기록.
    """
    from solapi_sender import send_friendtalk  # type: ignore

    phone_digits = _normalize_phone(phone)
    if not phone_digits:
        return {"status": "skipped", "error": "phone_empty"}

    channel_url = _get_kakao_channel_url()
    sms_body = (
        DEFAULT_INVITE_SMS_TEMPLATE
        .replace("{이름}", customer_name)
        .replace("{채널URL}", channel_url)
    )

    result = send_friendtalk(phone_digits, sms_body, disable_sms_fallback=False)

    _log_message(
        customer_id=customer_id,
        store_name=store_name,
        order_id=order_id,
        phone=phone_digits,
        message_type="channel_invite",
        channel="sms",
        status=result.get("status", "failed"),
        msg_id=result.get("msg_id"),
        message_body=sms_body,
        error_detail=result.get("error"),
        sent_by=sent_by,
    )

    return {"status": result.get("status"), "error": result.get("error")}


def send_manual_friendtalk(
    customer_id: int,
    phone: str,
    customer_name: str,
    message_body: str,
    store_name: str,
    order_id: int | None = None,
    sent_by: str = "system",
) -> dict[str, Any]:
    """관리자가 고객 상세 화면에서 직접 친구톡 발송."""
    from solapi_sender import send_friendtalk  # type: ignore

    phone_digits = _normalize_phone(phone)
    if not phone_digits:
        return {"status": "skipped", "error": "phone_empty"}

    result = send_friendtalk(phone_digits, message_body, disable_sms_fallback=False)

    _log_message(
        customer_id=customer_id,
        store_name=store_name,
        order_id=order_id,
        phone=phone_digits,
        message_type="manual",
        channel="friendtalk",
        status=result.get("status", "failed"),
        msg_id=result.get("msg_id"),
        message_body=message_body,
        error_detail=result.get("error"),
        sent_by=sent_by,
    )

    return {"status": result.get("status"), "error": result.get("error")}
