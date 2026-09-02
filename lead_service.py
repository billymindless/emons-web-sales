"""
리드 upsert·조회 공통 서비스.

api.py(FastAPI 웹훅·/v1 라우트), lead_manager.py(모모 UI), lead_management.py(백필)
세 갈래가 각자의 INSERT/UPDATE 를 갖고 있던 것을 하나로 통합한다.

핵심 원칙:
    - 전화번호 1개 = app_leads 레코드 1건. 중복 INSERT 금지.
    - upsert_lead 는 있으면 UPDATE, 없으면 INSERT — 재유입 분기(A/B/C/D)를 유지한다.
    - 상담 히스토리(app_chat_history)는 별도 append. 기존 memo 를 덮어쓰지 않는다.
    - 이 모듈은 Streamlit 에 의존하지 않는다. Supabase 환경변수만 있으면 FastAPI 프로세스에서도
      바로 사용할 수 있다.

이 모듈은 동기(httpx) 헬퍼로 통일한다. FastAPI 라우트에서도 그대로 호출 가능
(짧은 REST 왕복이라 이벤트 루프 블로킹 영향이 미미하며, register_lead 도 이미 동기 httpx 를 쓴다).
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────
# Supabase REST 헬퍼
# ──────────────────────────────────────────

_ALLOWED_STAGES = {"1_신규", "2_상담중", "3_견적발송", "4_계약완료", "5_실패", "6_보류"}
_TERMINAL_STAGES = {"4_계약완료", "5_실패", "6_보류"}
_ALLOWED_CUSTOMER_TYPES = {"신규잠재고객", "기존구매고객_DB외", "AS요청", "재상담"}
_ALLOWED_LEAD_SOURCES = {"온라인_채널톡", "전화_문의", "오프라인_방문", "카카오톡", "기타"}
_ALLOWED_SOURCE_SYSTEMS = {
    "channel_talk", "grok_bot", "momo_ui",
    "channel_talk_import", "emons_seller", "webhook",
}
_CLASSIFIED_TYPES = {"기존구매고객_DB외", "AS요청", "재상담"}

_DEFAULT_STORE = os.environ.get("CHANNEL_TALK_DEFAULT_STORE", "채널톡")


class LeadServiceError(RuntimeError):
    """upsert_lead 등에서 Supabase 오류를 감쌀 때 사용."""


def _supa_config() -> tuple[str, str]:
    url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    return url, key


def _headers(prefer: str = "return=representation") -> dict[str, str]:
    _, key = _supa_config()
    return {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }


def _url(table: str) -> str:
    url, _ = _supa_config()
    if not url:
        raise LeadServiceError("SUPABASE_URL not configured")
    return f"{url}/rest/v1/{table}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_phone(phone: str | None) -> str:
    """010-1234-5678, +82-10-1234-5678 등 모든 형식을 01012345678로 통일."""
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("82") and len(digits) >= 11:
        digits = "0" + digits[2:]
    return digits


# ──────────────────────────────────────────
# 조회
# ──────────────────────────────────────────

_LEAD_SELECT = (
    "id,store_name,phone,name,memo,lead_source,lead_stage,customer_type,"
    "employee_names,assigned_employee_id,next_contact_date,last_contact_at,"
    "nurturing_step,next_nurture_at,classification_memo,classified_by,classified_at,"
    "converted_at,converted_order_id,revenue_amount,created_at,updated_at"
)


def get_lead_by_phone(phone: str) -> dict | None:
    """전화번호로 최근 app_leads 1건 조회."""
    normalized = normalize_phone(phone)
    if not normalized:
        return None
    try:
        resp = httpx.get(
            _url("app_leads"),
            headers=_headers(),
            params={
                "phone": f"eq.{normalized}",
                "order": "created_at.desc",
                "limit": "1",
                "select": _LEAD_SELECT,
            },
            timeout=8.0,
        )
        data = resp.json() if resp.status_code == 200 else []
        return data[0] if data else None
    except Exception as e:
        logger.warning("get_lead_by_phone 실패: %s", e)
        return None


def get_lead_by_id(lead_id: int) -> dict | None:
    try:
        resp = httpx.get(
            _url("app_leads"),
            headers=_headers(),
            params={
                "id": f"eq.{int(lead_id)}",
                "limit": "1",
                "select": _LEAD_SELECT,
            },
            timeout=8.0,
        )
        data = resp.json() if resp.status_code == 200 else []
        return data[0] if data else None
    except Exception as e:
        logger.warning("get_lead_by_id 실패: %s", e)
        return None


def search_leads(
    *,
    phone: str | None = None,
    stage: str | None = None,
    store: str | None = None,
    customer_type: str | None = None,
    q: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict]:
    """리스트 조회. 필터를 조합해 최근순으로 반환."""
    params: dict[str, str] = {
        "order": "created_at.desc",
        "limit": str(max(1, min(limit, 100))),
        "offset": str(max(0, int(offset))),
        "select": _LEAD_SELECT,
    }
    if phone:
        params["phone"] = f"eq.{normalize_phone(phone)}"
    if stage:
        params["lead_stage"] = f"eq.{stage}"
    if store:
        params["store_name"] = f"eq.{store}"
    if customer_type:
        params["customer_type"] = f"eq.{customer_type}"
    if q:
        # name/memo 부분 일치 (PostgREST ilike 는 % 를 * 로 표기)
        pattern = f"*{q}*"
        params["or"] = f"(name.ilike.{pattern},memo.ilike.{pattern})"
    try:
        resp = httpx.get(
            _url("app_leads"),
            headers=_headers(),
            params=params,
            timeout=10.0,
        )
        return resp.json() if resp.status_code == 200 else []
    except Exception as e:
        logger.warning("search_leads 실패: %s", e)
        return []


# ──────────────────────────────────────────
# app_chat_history append
# ──────────────────────────────────────────

def append_chat_history(
    phone: str,
    *,
    channel: str,
    summary: str = "",
    full_text: str = "",
    handled_by: str = "system",
    chat_id: str | None = None,
) -> bool:
    """리드 활동/메모를 app_chat_history 에 한 줄 추가.

    upsert_lead 가 memo 를 덮어쓰지 않기 위한 안전한 로그 경로.
    """
    normalized = normalize_phone(phone)
    if not normalized:
        return False
    payload: dict[str, Any] = {
        "customer_phone": normalized,
        "channel": channel,
        "summary": (summary or "")[:500],
        "full_text": (full_text or "") or None,
        "handled_by": handled_by or "system",
    }
    if chat_id:
        payload["chat_id"] = chat_id
    try:
        resp = httpx.post(
            _url("app_chat_history"),
            json=payload,
            headers=_headers("return=minimal"),
            timeout=5.0,
        )
        return resp.status_code in (200, 201, 204)
    except Exception:
        logger.exception("app_chat_history INSERT 실패")
        return False


# ──────────────────────────────────────────
# 핵심: upsert_lead
# ──────────────────────────────────────────

def upsert_lead(
    *,
    phone: str,
    name: str | None = None,
    memo: str | None = None,
    lead_source: str | None = None,
    store_name: str | None = None,
    employee_names: str | None = None,
    customer_type: str | None = None,
    next_contact_date: str | None = None,
    classification_memo: str | None = None,
    source_system: str = "webhook",
    log_note: bool = True,
) -> dict[str, Any]:
    """전화번호 단일 키로 app_leads 를 upsert 한다.

    Returns:
        {
            "ok": bool,
            "lead_id": int | None,
            "created": bool,
            "branch": "A" | "B" | "C" | "D" | None,
            "lead": dict | None,
            "error": str | None,
        }

    분기 (채널톡 재유입 정책과 동일):
        - lead 있음 + customer_type in (기존구매/AS/재상담)  → A: last_contact_at 갱신,
          lead_stage=2_상담중 (단, 4_계약완료/5_실패/6_보류 는 유지)
        - lead 있음 + customer_type in (신규잠재고객 | NULL)  → B: last_contact_at 만 갱신
        - lead 없음 + app_customers 매칭                     → C: 신규 INSERT, customer_type=재상담
        - lead 없음 + app_customers 없음                     → D: 신규 INSERT, customer_type=신규잠재고객
        (호출자가 customer_type 을 명시적으로 넘기면 그 값을 우선.)

    기존 memo 는 절대 덮어쓰지 않는다. 새 memo 는 app_chat_history 에 append.
    """
    normalized = normalize_phone(phone)
    if not normalized:
        return {
            "ok": False, "lead_id": None, "created": False,
            "branch": None, "lead": None, "error": "phone_empty",
        }

    if lead_source and lead_source not in _ALLOWED_LEAD_SOURCES:
        return {
            "ok": False, "lead_id": None, "created": False,
            "branch": None, "lead": None,
            "error": f"invalid_lead_source:{lead_source}",
        }
    if customer_type and customer_type not in _ALLOWED_CUSTOMER_TYPES:
        return {
            "ok": False, "lead_id": None, "created": False,
            "branch": None, "lead": None,
            "error": f"invalid_customer_type:{customer_type}",
        }
    if source_system and source_system not in _ALLOWED_SOURCE_SYSTEMS:
        # 알 수 없는 source_system 은 통과시키되 로그만 남긴다 (외부 시스템 확장을 위해).
        logger.info("unknown source_system=%s (upserting anyway)", source_system)

    existing = get_lead_by_phone(normalized)
    now_iso = _now_iso()
    note_channel = f"upsert_{source_system}"

    if existing:
        return _update_existing_lead(
            existing=existing,
            normalized=normalized,
            name=name,
            memo=memo,
            employee_names=employee_names,
            customer_type=customer_type,
            next_contact_date=next_contact_date,
            classification_memo=classification_memo,
            now_iso=now_iso,
            note_channel=note_channel,
            log_note=log_note,
            source_system=source_system,
        )

    return _insert_new_lead(
        normalized=normalized,
        name=name,
        memo=memo,
        lead_source=lead_source,
        store_name=store_name,
        employee_names=employee_names,
        customer_type=customer_type,
        next_contact_date=next_contact_date,
        classification_memo=classification_memo,
        now_iso=now_iso,
        note_channel=note_channel,
        log_note=log_note,
        source_system=source_system,
    )


def _customer_matched(phone: str) -> bool:
    """app_customers 에 phone1/phone2 매칭 여부."""
    try:
        resp = httpx.get(
            _url("app_customers"),
            headers=_headers(),
            params={
                "or": f"(phone1.eq.{phone},phone2.eq.{phone})",
                "select": "id",
                "limit": "1",
            },
            timeout=5.0,
        )
        return resp.status_code == 200 and bool(resp.json() or [])
    except Exception:
        return False


def _insert_new_lead(
    *,
    normalized: str,
    name: str | None,
    memo: str | None,
    lead_source: str | None,
    store_name: str | None,
    employee_names: str | None,
    customer_type: str | None,
    next_contact_date: str | None,
    classification_memo: str | None,
    now_iso: str,
    note_channel: str,
    log_note: bool,
    source_system: str,
) -> dict[str, Any]:
    # customer_type 미지정 시 app_customers 매칭 기준 자동 분류
    if customer_type is None:
        customer_type = "재상담" if _customer_matched(normalized) else "신규잠재고객"
    branch = "C" if customer_type == "재상담" else "D"

    row: dict[str, Any] = {
        "store_name": store_name or _DEFAULT_STORE,
        "phone": normalized,
        "name": name or "",
        "memo": (memo or "")[:2000],
        "lead_source": lead_source or "온라인_채널톡",
        "lead_stage": "1_신규",
        "customer_type": customer_type,
        "nurturing_step": 0,
        "last_contact_at": now_iso,
    }
    if employee_names:
        row["employee_names"] = employee_names
    if next_contact_date:
        row["next_contact_date"] = next_contact_date
    if classification_memo:
        row["classification_memo"] = classification_memo

    try:
        resp = httpx.post(
            _url("app_leads"),
            json=row,
            headers=_headers(),
            timeout=10.0,
        )
        if resp.status_code not in (200, 201):
            logger.error(
                "app_leads INSERT 실패 %s: %s", resp.status_code, resp.text[:300],
            )
            return {
                "ok": False, "lead_id": None, "created": False,
                "branch": branch, "lead": None, "error": resp.text[:200],
            }
        data = resp.json()
        lead = data[0] if isinstance(data, list) and data else None
        lead_id = int(lead["id"]) if lead and "id" in lead else None
    except Exception as e:
        logger.exception("app_leads INSERT 예외")
        return {
            "ok": False, "lead_id": None, "created": False,
            "branch": branch, "lead": None, "error": str(e),
        }

    if log_note and (memo or name):
        append_chat_history(
            normalized,
            channel=note_channel,
            summary=(memo or f"신규 리드 등록: {name or ''}").strip()[:500],
            handled_by=source_system,
        )
    return {
        "ok": True, "lead_id": lead_id, "created": True,
        "branch": branch, "lead": lead, "error": None,
    }


def _update_existing_lead(
    *,
    existing: dict,
    normalized: str,
    name: str | None,
    memo: str | None,
    employee_names: str | None,
    customer_type: str | None,
    next_contact_date: str | None,
    classification_memo: str | None,
    now_iso: str,
    note_channel: str,
    log_note: bool,
    source_system: str,
) -> dict[str, Any]:
    lead_id = int(existing["id"])
    current_type = existing.get("customer_type")
    current_stage = existing.get("lead_stage")

    # 분기 결정
    effective_type = customer_type or current_type
    if effective_type in _CLASSIFIED_TYPES:
        branch = "A"
    else:
        branch = "B"

    patch: dict[str, Any] = {
        "last_contact_at": now_iso,
        "updated_at": now_iso,
    }
    # 이름은 비어 있을 때만 채움 (덮어쓰기 금지)
    if name and not (existing.get("name") or "").strip():
        patch["name"] = name
    # 담당자는 지정 시 덮어쓰기 (호출자가 명시적으로 넘긴 경우)
    if employee_names is not None:
        patch["employee_names"] = employee_names
    if customer_type and customer_type != current_type:
        patch["customer_type"] = customer_type
    if classification_memo:
        patch["classification_memo"] = classification_memo
        patch["classified_by"] = source_system
        patch["classified_at"] = now_iso
    if next_contact_date:
        patch["next_contact_date"] = next_contact_date

    # 분기 A: 4/5/6 이 아니면 2_상담중 승격
    if branch == "A" and current_stage not in _TERMINAL_STAGES:
        patch["lead_stage"] = "2_상담중"

    try:
        resp = httpx.patch(
            _url("app_leads"),
            headers=_headers(),
            params={"id": f"eq.{lead_id}"},
            json=patch,
            timeout=8.0,
        )
        if resp.status_code not in (200, 204):
            logger.warning(
                "app_leads UPDATE 실패 %s: %s", resp.status_code, resp.text[:200],
            )
            return {
                "ok": False, "lead_id": lead_id, "created": False,
                "branch": branch, "lead": existing, "error": resp.text[:200],
            }
        updated = resp.json() if resp.text else []
        lead = updated[0] if isinstance(updated, list) and updated else existing
    except Exception as e:
        logger.exception("app_leads UPDATE 예외")
        return {
            "ok": False, "lead_id": lead_id, "created": False,
            "branch": branch, "lead": existing, "error": str(e),
        }

    # memo 는 덮어쓰지 않고 app_chat_history 로 append (있을 때만)
    if log_note and memo:
        append_chat_history(
            normalized,
            channel=note_channel,
            summary=memo.strip()[:500],
            handled_by=source_system,
        )
    return {
        "ok": True, "lead_id": lead_id, "created": False,
        "branch": branch, "lead": lead, "error": None,
    }


# ──────────────────────────────────────────
# 부분 수정 (단계·담당·연락일 등)
# ──────────────────────────────────────────

def update_lead(
    lead_id: int,
    *,
    lead_stage: str | None = None,
    memo: str | None = None,
    employee_names: str | None = None,
    next_contact_date: str | None = None,
    customer_type: str | None = None,
    classification_memo: str | None = None,
    source_system: str = "grok_bot",
) -> dict[str, Any]:
    """리드 단건 부분 수정.

    memo 는 여기서도 덮어쓰지 않고 app_chat_history 로 이관한다.
    lead_stage 는 화이트리스트 검증.
    """
    if lead_stage and lead_stage not in _ALLOWED_STAGES:
        return {"ok": False, "lead": None, "error": f"invalid_lead_stage:{lead_stage}"}
    if customer_type and customer_type not in _ALLOWED_CUSTOMER_TYPES:
        return {"ok": False, "lead": None, "error": f"invalid_customer_type:{customer_type}"}

    now_iso = _now_iso()
    patch: dict[str, Any] = {"updated_at": now_iso}
    if lead_stage:
        patch["lead_stage"] = lead_stage
    if employee_names is not None:
        patch["employee_names"] = employee_names
    if next_contact_date is not None:
        patch["next_contact_date"] = next_contact_date
    if customer_type:
        patch["customer_type"] = customer_type
    if classification_memo is not None:
        patch["classification_memo"] = classification_memo
        patch["classified_by"] = source_system
        patch["classified_at"] = now_iso

    try:
        resp = httpx.patch(
            _url("app_leads"),
            headers=_headers(),
            params={"id": f"eq.{int(lead_id)}"},
            json=patch,
            timeout=8.0,
        )
        if resp.status_code not in (200, 204):
            return {"ok": False, "lead": None, "error": resp.text[:200]}
        rows = resp.json() if resp.text else []
        lead = rows[0] if isinstance(rows, list) and rows else None
    except Exception as e:
        return {"ok": False, "lead": None, "error": str(e)}

    if memo and lead and lead.get("phone"):
        append_chat_history(
            lead["phone"],
            channel=f"note_{source_system}",
            summary=memo.strip()[:500],
            handled_by=source_system,
        )
    return {"ok": True, "lead": lead, "error": None}
