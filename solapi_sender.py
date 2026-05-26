"""
Solapi 카카오 친구톡 실시간 발송 모듈 (사내 업무 알림용).

`crm_automation.py`의 ATA(알림톡) payload 빌더와 분리되어 있고,
이쪽은 사내 업무판에서 직원에게 즉시 친구톡을 발송하기 위해 사용한다.

Solapi v4 API:
    POST https://api.solapi.com/messages/v4/send-many
    HMAC-SHA256 인증 헤더 필요.

st.secrets 또는 환경변수에서 다음 값을 읽는다:
    [solapi]
    api_key = "..."
    api_secret = "..."
    sender = "01012345678"        # 발신자 휴대폰
    pf_id = "KAKAO_PFID"          # 카카오 발신프로필 ID
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

SOLAPI_BASE_URL = "https://api.solapi.com"
SOLAPI_SEND_PATH = "/messages/v4/send-many"

NIGHT_START_HOUR = 21  # 21:00 ~ 08:00 야간 거부 가능
NIGHT_END_HOUR = 8


def _get_secrets() -> dict[str, str]:
    """st.secrets에서 solapi 설정 로드. 없으면 환경변수 폴백."""
    try:
        import streamlit as st  # noqa: WPS433
        sec = st.secrets.get("solapi", {}) if hasattr(st, "secrets") else {}
        if sec:
            return {
                "api_key": sec.get("api_key", ""),
                "api_secret": sec.get("api_secret", ""),
                "sender": sec.get("sender", ""),
                "pf_id": sec.get("pf_id", ""),
            }
    except Exception:
        pass
    return {
        "api_key": os.environ.get("SOLAPI_API_KEY", ""),
        "api_secret": os.environ.get("SOLAPI_API_SECRET", ""),
        "sender": os.environ.get("SOLAPI_SENDER", ""),
        "pf_id": os.environ.get("SOLAPI_PF_ID", ""),
    }


def _build_auth_header(api_key: str, api_secret: str) -> str:
    """Solapi HMAC-SHA256 인증 헤더."""
    salt = secrets.token_hex(16)
    date_str = datetime.now(timezone.utc).isoformat()
    msg = (date_str + salt).encode()
    signature = hmac.new(api_secret.encode(), msg, hashlib.sha256).hexdigest()
    return f"HMAC-SHA256 apiKey={api_key}, date={date_str}, salt={salt}, signature={signature}"


def _normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")


def _is_night_kst() -> bool:
    """현재 KST 기준 야간(21~08)이면 True."""
    kst = datetime.now(timezone.utc) + timedelta(hours=9)
    hour = kst.hour
    return hour >= NIGHT_START_HOUR or hour < NIGHT_END_HOUR


def send_friendtalk(
    to_phone: str,
    body: str,
    *,
    pf_id: str | None = None,
    disable_sms_fallback: bool = True,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """
    카카오 친구톡 1건 발송.

    반환 형식:
        {
            "status": "sent" | "failed" | "out_of_hours" | "not_friend" | "skipped",
            "msg_id": str | None,
            "error": str | None,
            "raw": dict | None,
        }

    status 의미:
        - sent: Solapi가 받아들임 (실제 도달은 별도 추적)
        - failed: 호출 실패 또는 거부
        - out_of_hours: 야간 정책상 발송 거부(우리 측 선판단 or Solapi 응답)
        - not_friend: 채널 친구가 아니어서 실패
        - skipped: 키 누락 등으로 발송 자체를 보류
    """
    phone = _normalize_phone(to_phone)
    if not phone:
        return {"status": "skipped", "msg_id": None, "error": "phone_empty", "raw": None}

    sec = _get_secrets()
    api_key = sec.get("api_key", "")
    api_secret = sec.get("api_secret", "")
    sender = sec.get("sender", "")
    pf_id_effective = (pf_id or sec.get("pf_id") or "").strip()

    # 키 누락 → 발송 보류 (DB는 skipped로 기록되어 운영자가 후속 처리)
    if not api_key or not api_secret or not pf_id_effective:
        return {"status": "skipped", "msg_id": None, "error": "solapi_secrets_missing", "raw": None}

    # 야간 사전 차단을 정책으로 두지 않음. Solapi에 보내고 거부 응답이 오면 out_of_hours 처리.
    # 단, Solapi가 거부할 가능성이 높은 시간대를 표시만 하여 응답 분석에 사용.
    is_night_local = _is_night_kst()

    payload = {
        "messages": [
            {
                "to": phone,
                "from": sender,
                "text": body,
                "type": "CTA",  # 친구톡 텍스트
                "kakaoOptions": {
                    "pfId": pf_id_effective,
                    "disableSms": disable_sms_fallback,
                },
            }
        ]
    }

    headers = {
        "Authorization": _build_auth_header(api_key, api_secret),
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            SOLAPI_BASE_URL + SOLAPI_SEND_PATH,
            json=payload,
            headers=headers,
            timeout=timeout,
        )
    except requests.RequestException as e:
        return {"status": "failed", "msg_id": None, "error": f"network: {e}", "raw": None}

    raw: dict[str, Any]
    try:
        raw = resp.json()
    except Exception:
        raw = {"text": resp.text[:500]}

    if resp.status_code >= 400:
        err_text = str(raw)
        lower = err_text.lower()
        if is_night_local or "time" in lower or "발송 시간" in err_text or "야간" in err_text:
            return {"status": "out_of_hours", "msg_id": None, "error": err_text[:500], "raw": raw}
        if "friend" in lower or "친구" in err_text or "not_friend" in lower:
            return {"status": "not_friend", "msg_id": None, "error": err_text[:500], "raw": raw}
        return {"status": "failed", "msg_id": None, "error": err_text[:500], "raw": raw}

    # 성공 응답에서 message id 추출
    msg_id = None
    try:
        if isinstance(raw, dict):
            if "messageList" in raw and isinstance(raw["messageList"], list) and raw["messageList"]:
                msg_id = raw["messageList"][0].get("messageId")
            elif "groupInfo" in raw and isinstance(raw["groupInfo"], dict):
                msg_id = raw["groupInfo"].get("groupId")
    except Exception:
        pass

    return {"status": "sent", "msg_id": msg_id, "error": None, "raw": raw}
