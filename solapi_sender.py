"""
Solapi 카카오 친구톡/알림톡/SMS/MMS 발송 모듈 (사내 업무 알림용).

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
    """
    Solapi 인증 정보를 로드한다.

    우선순위:
      1. st.secrets["solapi"] 섹션 (로컬 secrets.toml)
      2. st.secrets 최상위 SOLAPI_* 키
      3. 환경변수 SOLAPI_*
    """
    cfg: dict[str, str] = {
        "api_key": "",
        "api_secret": "",
        "sender": "",
        "pf_id": "",
    }

    # ── 환경변수 ────────────────────────────────
    _env_map = {
        "api_key": "SOLAPI_API_KEY",
        "api_secret": "SOLAPI_API_SECRET",
        "sender": "SOLAPI_SENDER",
        "pf_id": "SOLAPI_PF_ID",
    }
    for _k, _ev in _env_map.items():
        _v = os.environ.get(_ev, "")
        if _v:
            cfg[_k] = _v

    # ── st.secrets ─────────────────────────────
    try:
        import streamlit as st  # noqa: WPS433
        _sec = getattr(st, "secrets", None)
        if _sec is None:
            return cfg

        # [solapi] 섹션 시도
        try:
            _section = _sec["solapi"]
            for _k in ("api_key", "api_secret", "sender", "pf_id"):
                try:
                    _v = str(_section[_k]).strip()
                    if _v:
                        cfg[_k] = _v
                except (KeyError, TypeError):
                    pass
        except (KeyError, AttributeError):
            pass

        # 섹션이 없으면 최상위 SOLAPI_* 키 시도
        if not cfg["api_key"]:
            for _k, _ev in _env_map.items():
                try:
                    _v = str(_sec[_ev]).strip()
                    if _v:
                        cfg[_k] = _v
                except (KeyError, AttributeError, TypeError):
                    pass
    except Exception:
        pass

    return cfg


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


def send_alimtalk(
    to_phone: str,
    template_code: str,
    variables: dict[str, str],
    *,
    pf_id: str | None = None,
    fallback_sms_text: str | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """
    카카오 알림톡(ATA) 1건 발송.

    알림톡은 채널 친구 여부와 무관하게 발송되며, 카카오에서 사전 승인된
    템플릿 코드(template_code)가 필요합니다.

    Args:
        to_phone: 수신자 전화번호
        template_code: Solapi에 등록·승인된 카카오 알림톡 템플릿 코드 (예: "KA01TP...")
        variables: 템플릿 변수 딕셔너리 (예: {"이름": "홍길동", "품목": "소파"})
        pf_id: 카카오 발신 프로필 ID (None이면 secrets에서 읽음)
        fallback_sms_text: 알림톡 실패 시 SMS 대체 발송 본문 (None이면 SMS 폴백 비활성)
        timeout: 요청 타임아웃(초)

    반환 형식:
        {
            "status": "sent" | "failed" | "skipped",
            "msg_id": str | None,
            "error": str | None,
            "raw": dict | None,
        }
    """
    phone = _normalize_phone(to_phone)
    if not phone:
        return {"status": "skipped", "msg_id": None, "error": "phone_empty", "raw": None}

    sec = _get_secrets()
    api_key = sec.get("api_key", "")
    api_secret = sec.get("api_secret", "")
    sender = sec.get("sender", "")
    pf_id_effective = (pf_id or sec.get("pf_id") or "").strip()

    if not api_key or not api_secret or not pf_id_effective:
        return {"status": "skipped", "msg_id": None, "error": "solapi_secrets_missing", "raw": None}

    if not template_code:
        return {"status": "skipped", "msg_id": None, "error": "template_code_missing", "raw": None}

    kakao_options: dict[str, Any] = {
        "pfId": pf_id_effective,
        "templateCode": template_code,
        "variables": variables or {},
        "disableSms": fallback_sms_text is None,
    }

    msg: dict[str, Any] = {
        "to": phone,
        "from": sender,
        "text": fallback_sms_text or " ",  # ATA는 text 필드 필수지만 실제 내용은 템플릿 사용
        "type": "ATA",
        "kakaoOptions": kakao_options,
    }

    if fallback_sms_text:
        msg["text"] = fallback_sms_text

    payload = {"messages": [msg]}
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
        return {"status": "failed", "msg_id": None, "error": str(raw)[:500], "raw": raw}

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


def send_sms(
    to_phone: str,
    text: str,
    *,
    timeout: float = 10.0,
) -> dict[str, Any]:
    """
    일반 SMS/LMS 단문 발송.

    카카오 채널 미친구 고객용 폴백. 90자 초과 시 LMS로 자동 전환.

    반환 형식:
        {
            "status": "sent" | "failed" | "skipped",
            "msg_id": str | None,
            "error": str | None,
            "raw": dict | None,
        }
    """
    phone = _normalize_phone(to_phone)
    if not phone:
        return {"status": "skipped", "msg_id": None, "error": "phone_empty", "raw": None}

    sec = _get_secrets()
    api_key = sec.get("api_key", "")
    api_secret = sec.get("api_secret", "")
    sender = sec.get("sender", "")

    if not api_key or not api_secret or not sender:
        return {"status": "skipped", "msg_id": None, "error": "solapi_secrets_missing", "raw": None}

    msg_type = "LMS" if len(text) > 90 else "SMS"
    payload = {
        "messages": [{"to": phone, "from": sender, "text": text, "type": msg_type}]
    }
    headers = {
        "Authorization": _build_auth_header(api_key, api_secret),
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            SOLAPI_BASE_URL + SOLAPI_SEND_PATH, json=payload, headers=headers, timeout=timeout
        )
    except requests.RequestException as e:
        return {"status": "failed", "msg_id": None, "error": f"network: {e}", "raw": None}

    raw: dict[str, Any]
    try:
        raw = resp.json()
    except Exception:
        raw = {"text": resp.text[:500]}

    if resp.status_code >= 400:
        return {"status": "failed", "msg_id": None, "error": str(raw)[:500], "raw": raw}

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


def send_mms(
    to_phone: str,
    text: str,
    image_url: str,
    *,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """
    MMS 이미지 첨부 발송 (오프라인 방문 명함+사진용).

    image_url을 Solapi 파일 업로드 API에 먼저 전송 후 imageId를 MMS에 첨부.
    업로드 실패 시 LMS(텍스트)로 폴백 발송하고 status="lms_fallback" 반환.

    반환 형식:
        {
            "status": "sent" | "failed" | "skipped" | "lms_fallback",
            "msg_id": str | None,
            "error": str | None,
            "raw": dict | None,
        }
    """
    phone = _normalize_phone(to_phone)
    if not phone:
        return {"status": "skipped", "msg_id": None, "error": "phone_empty", "raw": None}

    sec = _get_secrets()
    api_key = sec.get("api_key", "")
    api_secret = sec.get("api_secret", "")
    sender = sec.get("sender", "")

    if not api_key or not api_secret or not sender:
        return {"status": "skipped", "msg_id": None, "error": "solapi_secrets_missing", "raw": None}

    headers_auth = {
        "Authorization": _build_auth_header(api_key, api_secret),
        "Content-Type": "application/json",
    }

    image_id: str | None = None
    if image_url and image_url.startswith("http"):
        try:
            up_resp = requests.post(
                SOLAPI_BASE_URL + "/storage/v1/files",
                json={"url": image_url, "type": "MMS"},
                headers=headers_auth,
                timeout=10.0,
            )
            if up_resp.status_code < 400:
                image_id = up_resp.json().get("fileId")
        except Exception:
            pass

    if not image_id:
        result = send_sms(to_phone, text, timeout=timeout)
        result["status"] = "lms_fallback"
        return result

    payload = {
        "messages": [
            {"to": phone, "from": sender, "text": text, "type": "MMS", "imageId": image_id}
        ]
    }

    try:
        resp = requests.post(
            SOLAPI_BASE_URL + SOLAPI_SEND_PATH, json=payload, headers=headers_auth, timeout=timeout
        )
    except requests.RequestException as e:
        return {"status": "failed", "msg_id": None, "error": f"network: {e}", "raw": None}

    raw: dict[str, Any]
    try:
        raw = resp.json()
    except Exception:
        raw = {"text": resp.text[:500]}

    if resp.status_code >= 400:
        return {"status": "failed", "msg_id": None, "error": str(raw)[:500], "raw": raw}

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


def send_batch(
    messages: list[dict],
    *,
    chunk_size: int = 100,
    timeout: float = 30.0,
) -> dict[str, Any]:
    """
    다수 고객에게 메시지 일괄 발송 (CRM 캠페인용).

    messages: Solapi 규격의 메시지 dict 리스트.
              'from'이 비어있으면 secrets의 sender로 자동 주입.
    chunk_size: 1회 API 호출 당 최대 메시지 수 (Solapi 권장 ≤ 100).

    반환:
        {"total": int, "sent": int, "failed": int, "errors": list[str]}
    """
    sec = _get_secrets()
    api_key = sec.get("api_key", "")
    api_secret = sec.get("api_secret", "")
    sender_no = sec.get("sender", "")

    if not api_key or not api_secret:
        return {
            "total": len(messages), "sent": 0,
            "failed": len(messages), "errors": ["solapi_secrets_missing"],
        }

    for m in messages:
        if not m.get("from"):
            m["from"] = sender_no

    total = len(messages)
    sent_count = 0
    failed_count = 0
    errors: list[str] = []

    for i in range(0, total, chunk_size):
        chunk = messages[i: i + chunk_size]
        hdrs = {
            "Authorization": _build_auth_header(api_key, api_secret),
            "Content-Type": "application/json",
        }
        try:
            r = requests.post(
                SOLAPI_BASE_URL + SOLAPI_SEND_PATH,
                json={"messages": chunk},
                headers=hdrs,
                timeout=timeout,
            )
            try:
                rj: dict[str, Any] = r.json()
            except Exception:
                rj = {"text": r.text[:300]}

            if r.status_code >= 400:
                failed_count += len(chunk)
                errors.append(f"청크 {i // chunk_size + 1} 오류: {str(rj)[:200]}")
            else:
                sent_count += len(chunk)
        except requests.RequestException as e:
            failed_count += len(chunk)
            errors.append(f"청크 {i // chunk_size + 1} 네트워크 오류: {e}")

    return {"total": total, "sent": sent_count, "failed": failed_count, "errors": errors}
