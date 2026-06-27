"""
리드 고객 관리 모듈.

옴니채널(오프라인 방문, 전화 문의, 온라인 채널톡) 유입 리드를 Supabase app_leads에
등록하고, 유입 경로별 넛징 메시지를 발송하며, 매출 등록 시 자동으로 4_계약완료로
클로즈하는 로직을 담당한다.

Supabase 접근: api.py와 동일하게 httpx REST 방식 사용 (환경변수 SUPABASE_URL, SUPABASE_SERVICE_KEY).
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MOMO_APP_URL = os.environ.get("MOMO_APP_URL", "")


def _get_supa_config() -> tuple[str, str]:
    """Supabase URL·Service Key를 st.secrets 우선, 환경변수 폴백으로 반환."""
    try:
        import streamlit as st  # noqa: WPS433
        sec = st.secrets.get("supabase", {}) if hasattr(st, "secrets") else {}
        url = sec.get("url", "") or os.environ.get("SUPABASE_URL", "")
        key = (
            sec.get("service_role_key", "")
            or sec.get("key", "")
            or os.environ.get("SUPABASE_SERVICE_KEY", "")
        )
        return url, key
    except Exception:
        return os.environ.get("SUPABASE_URL", ""), os.environ.get("SUPABASE_SERVICE_KEY", "")


# ──────────────────────────────────────────
# Supabase REST 헬퍼
# ──────────────────────────────────────────

def _supa_headers() -> dict[str, str]:
    _, key = _get_supa_config()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _supa_url(table: str) -> str:
    url, _ = _get_supa_config()
    return f"{url}/rest/v1/{table}"


def _normalize_phone(phone: str) -> str:
    """010-1234-5678 등 모든 형식을 01012345678로 통일."""
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("82") and len(digits) >= 11:
        digits = "0" + digits[2:]
    return digits


# ──────────────────────────────────────────
# 리드 등록
# ──────────────────────────────────────────

def register_lead(
    phone: str,
    name: str,
    memo: str,
    lead_source: str,
    store_name: str,
    employee_id: int | None = None,
    next_contact_date: str | None = None,
    send_now: bool = True,
    image_url: str = "",
) -> dict[str, Any]:
    """
    리드 고객을 Supabase app_leads에 등록하고, 유입 경로별 T+0 즉시 메시지를 발송한다.

    Args:
        phone: 고객 전화번호 (형식 무관)
        name: 고객 성함
        memo: 상담 메모
        lead_source: '온라인_채널톡' | '전화_문의' | '오프라인_방문'
        store_name: 매장명
        employee_id: 담당 직원 app_users.id (None이면 미배정)
        next_contact_date: 다음 연락 예정일 (YYYY-MM-DD)
        send_now: True면 T+0 즉시 메시지 발송
        image_url: 오프라인 방문 시 MMS에 첨부할 사진 URL

    Returns:
        {"ok": bool, "lead_id": int|None, "send_result": dict|None, "error": str|None}
    """
    normalized = _normalize_phone(phone)
    if not normalized:
        return {"ok": False, "lead_id": None, "send_result": None, "error": "phone_empty"}

    now_utc = datetime.now(timezone.utc)

    # T+N일 예약 시각 계산
    if lead_source == "오프라인_방문":
        next_nurture_days = 3
    else:
        next_nurture_days = 2

    next_nurture_at = (now_utc + timedelta(days=next_nurture_days)).isoformat()

    row: dict[str, Any] = {
        "store_name": store_name,
        "phone": normalized,
        "name": name or "",
        "memo": memo or "",
        "lead_source": lead_source,
        "lead_stage": "1_신규",
        "assigned_employee_id": employee_id,
        "assigned_store": store_name,
        "next_contact_date": next_contact_date,
        "nurturing_step": 0,
        "next_nurture_at": next_nurture_at,
    }

    # ── 중복 전화번호 체크 ──────────────────────
    try:
        chk = httpx.get(
            _supa_url("app_leads") + f"?phone=eq.{normalized}&store_name=eq.{store_name}"
            "&select=id,name,lead_stage,created_at&limit=1",
            headers=_supa_headers(),
            timeout=5.0,
        )
        if chk.status_code < 300:
            existing = chk.json()
            if existing:
                ex = existing[0]
                return {
                    "ok": False,
                    "lead_id": ex.get("id"),
                    "send_result": None,
                    "error": "duplicate_phone",
                    "existing": ex,
                }
    except Exception:
        pass  # 체크 실패 시 등록 계속 진행

    lead_id: int | None = None
    try:
        resp = httpx.post(
            _supa_url("app_leads"),
            json=row,
            headers=_supa_headers(),
            timeout=10.0,
        )
        if resp.status_code in (200, 201):
            data = resp.json()
            if isinstance(data, list) and data:
                lead_id = data[0].get("id")
        else:
            logger.error("app_leads INSERT 실패: %s %s", resp.status_code, resp.text[:300])
            return {"ok": False, "lead_id": None, "send_result": None, "error": resp.text[:200]}
    except Exception as e:
        logger.exception("app_leads INSERT 예외")
        return {"ok": False, "lead_id": None, "send_result": None, "error": str(e)}

    # 즉시 발송 업데이트 (nurturing_step = 1)
    if lead_id:
        try:
            httpx.patch(
                _supa_url("app_leads") + f"?id=eq.{lead_id}",
                json={"lead_stage": "2_상담중", "nurturing_step": 1},
                headers=_supa_headers(),
                timeout=5.0,
            )
        except Exception:
            pass

    send_result: dict | None = None
    if send_now and normalized:
        send_result = _send_t0_message(
            phone=normalized,
            name=name or "고객",
            lead_source=lead_source,
            store_name=store_name,
            image_url=image_url,
        )

    return {"ok": True, "lead_id": lead_id, "send_result": send_result, "error": None}


def _send_t0_message(
    phone: str,
    name: str,
    lead_source: str,
    store_name: str,
    image_url: str = "",
) -> dict[str, Any]:
    """T+0 즉시 발송. 유입 경로별로 다른 메시지/수단 사용."""
    try:
        from solapi_sender import send_friendtalk, send_mms, send_sms
    except ImportError:
        return {"status": "skipped", "error": "solapi_sender import 실패"}

    if lead_source == "오프라인_방문":
        text = (
            f"안녕하세요 {name}님, 에몬스 {store_name}입니다.\n"
            "오늘 방문해 주셔서 감사합니다.\n"
            "보셨던 제품 사진과 담당자 연락처를 보내드립니다.\n"
            "궁금하신 점은 아래 채널톡으로 문의 주세요."
        )
        if image_url:
            return send_mms(phone, text, image_url)
        return send_sms(phone, text)

    # 전화_문의 / 온라인_채널톡
    text = (
        f"안녕하세요 {name}님, 에몬스 {store_name}입니다.\n"
        "문의하신 제품 카탈로그 및 가격 안내를 보내드립니다.\n"
        "추가 문의는 채널톡으로 편하게 연락 주세요."
    )
    result = send_friendtalk(phone, text)
    if result.get("status") in ("not_friend", "failed"):
        result = send_sms(phone, text)
    return result


# ──────────────────────────────────────────
# 예약 넛징 발송 (T+N일 실행기에서 호출)
# ──────────────────────────────────────────

def send_nurturing_message(lead: dict[str, Any]) -> dict[str, Any]:
    """
    app_leads 레코드 하나를 받아 T+N 넛징 메시지를 발송하고
    nurturing_step과 next_nurture_at을 갱신한다.
    """
    try:
        from solapi_sender import send_friendtalk, send_sms
    except ImportError:
        return {"status": "skipped", "error": "solapi_sender import 실패"}

    phone = lead.get("phone", "")
    name = lead.get("name") or "고객"
    lead_source = lead.get("lead_source", "")
    lead_id = lead.get("id")

    if not phone or not lead_id:
        return {"status": "skipped", "error": "phone or lead_id 없음"}

    if lead_source == "오프라인_방문":
        text = (
            f"안녕하세요 {name}님, 에몬스입니다.\n"
            "사이즈나 색상 결정에 어려움이 있으신가요?\n"
            "채널톡으로 문의 주시면 바로 답변드리겠습니다."
        )
    else:
        text = (
            f"안녕하세요 {name}님, 에몬스입니다.\n"
            "이번 주말 매장 방문 예약 시 VVIP 사은품 혜택을 드립니다.\n"
            "궁금하신 점은 채널톡으로 편하게 문의해 주세요."
        )

    result = send_friendtalk(phone, text)
    if result.get("status") in ("not_friend", "failed"):
        result = send_sms(phone, text)

    # nurturing_step 갱신 (2 = 완료)
    try:
        httpx.patch(
            _supa_url("app_leads") + f"?id=eq.{lead_id}",
            json={"nurturing_step": 2, "next_nurture_at": None},
            headers=_supa_headers(),
            timeout=5.0,
        )
    except Exception:
        pass

    return result


# ──────────────────────────────────────────
# 매출 등록 시 리드 자동 클로즈
# ──────────────────────────────────────────

def _resolve_employee_id(employee_names: str) -> int | None:
    """employee_names 문자열(콤마 구분)의 첫 번째 이름을 app_users.id로 변환."""
    first_name = next(
        (n.strip() for n in (employee_names or "").split(",") if n.strip()), None
    )
    if not first_name:
        return None
    try:
        resp = httpx.get(
            _supa_url("app_users"),
            params={"name": f"eq.{first_name}", "select": "id", "limit": "1"},
            headers=_supa_headers(),
            timeout=5.0,
        )
        rows = resp.json() if resp.status_code < 400 else []
        if rows and isinstance(rows, list):
            return int(rows[0]["id"])
    except Exception:
        pass
    return None


def auto_close_lead(
    phone: str,
    order_id: int,
    revenue: float,
    employee_names: str = "",
) -> bool:
    """
    매출 등록 시 호출. app_leads에서 동일 전화번호의 활성 리드를 찾아 자동 클로즈.
    employee_names(콤마 구분 직원 이름)를 받아 assigned_employee_id도 자동 업데이트.

    Returns:
        True if a lead was closed, False otherwise.
    """
    normalized = _normalize_phone(phone)
    if not normalized:
        return False

    try:
        resp = httpx.get(
            _supa_url("app_leads"),
            params={
                "phone": f"eq.{normalized}",
                "lead_stage": "not.in.(4_계약완료,5_실패,6_보류)",
                "order": "lead_stage.asc,created_at.asc",
                "limit": "1",
                "select": "id,assigned_employee_id",
            },
            headers=_supa_headers(),
            timeout=5.0,
        )
        data = resp.json() if resp.status_code < 400 else []
    except Exception:
        return False

    if not data:
        return False

    lead_id = data[0]["id"]
    current_emp_id = data[0].get("assigned_employee_id")
    now_utc = datetime.now(timezone.utc).isoformat()

    patch_body: dict = {
        "lead_stage": "4_계약완료",
        "converted_at": now_utc,
        "converted_order_id": order_id,
        "revenue_amount": revenue,
        "updated_at": now_utc,
    }

    # 담당자가 아직 미설정이고 employee_names가 주어진 경우 → ID 조회 후 반영
    if not current_emp_id and employee_names:
        emp_id = _resolve_employee_id(employee_names)
        if emp_id:
            patch_body["assigned_employee_id"] = emp_id
            logger.info("리드 담당자 자동 매핑: lead_id=%s employee_names=%s → id=%s", lead_id, employee_names, emp_id)

    try:
        httpx.patch(
            _supa_url("app_leads") + f"?id=eq.{lead_id}",
            json=patch_body,
            headers=_supa_headers(),
            timeout=5.0,
        )
        logger.info("리드 자동 클로즈: lead_id=%s order_id=%s revenue=%s", lead_id, order_id, revenue)
        return True
    except Exception:
        logger.exception("리드 자동 클로즈 실패")
        return False


# ──────────────────────────────────────────
# 상담 이력 저장
# ──────────────────────────────────────────

def save_chat_history(
    phone: str,
    channel: str,
    summary: str,
    handled_by: str = "",
    full_text: str = "",
    chat_id: str = "",
) -> bool:
    """
    app_chat_history에 상담 이력을 저장한다.

    Args:
        phone: 고객 전화번호
        channel: '채널톡_웹챗' | '카카오톡' | '오프라인_메모' | '전화_통화'
        summary: 상담 요약
        handled_by: 담당 상담원 이름 또는 이메일
        full_text: 대화 전문 (선택)
        chat_id: 채널톡 chatId (선택)
    """
    normalized = _normalize_phone(phone)
    if not normalized:
        return False

    try:
        resp = httpx.post(
            _supa_url("app_chat_history"),
            json={
                "customer_phone": normalized,
                "channel": channel,
                "chat_id": chat_id or None,
                "summary": summary[:500] if summary else "",
                "full_text": full_text or None,
                "handled_by": handled_by or "",
            },
            headers=_supa_headers(),
            timeout=5.0,
        )
        return resp.status_code in (200, 201)
    except Exception:
        logger.exception("app_chat_history INSERT 실패")
        return False


def get_chat_history(phone: str, limit: int = 20) -> list[dict]:
    """
    전화번호로 app_chat_history를 조회하여 최신순으로 반환.
    """
    normalized = _normalize_phone(phone)
    if not normalized:
        return []

    try:
        resp = httpx.get(
            _supa_url("app_chat_history"),
            params={
                "customer_phone": f"eq.{normalized}",
                "order": "created_at.desc",
                "limit": str(limit),
                "select": "id,channel,summary,full_text,handled_by,created_at",
            },
            headers=_supa_headers(),
            timeout=5.0,
        )
        return resp.json() if resp.status_code < 400 else []
    except Exception:
        return []


def get_leads_by_phone(phone: str) -> list[dict]:
    """전화번호로 app_leads를 조회하여 반환."""
    normalized = _normalize_phone(phone)
    if not normalized:
        return []

    try:
        resp = httpx.get(
            _supa_url("app_leads"),
            params={
                "phone": f"eq.{normalized}",
                "order": "created_at.desc",
                "select": "id,lead_source,lead_stage,memo,next_contact_date,assigned_store,created_at",
            },
            headers=_supa_headers(),
            timeout=5.0,
        )
        return resp.json() if resp.status_code < 400 else []
    except Exception:
        return []
