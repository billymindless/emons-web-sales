# -*- coding: utf-8 -*-
"""
channel_call_service.py — 채널톡 콜(Meet) 통화 → app_call_records 인제스트.

계획서: docs/plans/채널톡_콜_상담일지_c5e969b5.plan.md
스키마: SUPABASE_APP_CALL_RECORDS.sql

기능:
  - 채널톡 Open API 래퍼 (통화로그·미트 메시지·녹음 URL)
  - Gemini 통화 요약 (JSON 스키마 강제)
  - _ingest_meet: 매장 매핑 → 리드 매칭 → app_call_records upsert → 리드 노트 append
  - poll_recent_calls: 지난 N분간 신규 통화만 수집 (idempotent, UNIQUE channel_meet_message_id)

FastAPI 프로세스(api.py)에서 사용. Streamlit(app.py) 은 조회만 하고 이 모듈은 import 하지 않아도 됨.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

from lead_service import (
    LeadServiceError,
    _headers as _supa_headers,
    _url as _supa_url,
    append_chat_history,
    get_lead_by_phone,
    normalize_phone,
    upsert_lead,
)

logger = logging.getLogger(__name__)

_CHANNEL_API_BASE = "https://api.channel.io/open/v5"
_GEMINI_URL_TMPL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-flash-latest:generateContent?key={key}"
)


# ──────────────────────────────────────────────
# 설정 · 인증 헤더
# ──────────────────────────────────────────────

def _channel_headers() -> dict[str, str] | None:
    key = os.environ.get("CHANNEL_TALK_ACCESS_KEY", "").strip()
    secret = os.environ.get("CHANNEL_TALK_ACCESS_SECRET", "").strip()
    if not key or not secret:
        return None
    return {
        "x-access-key": key,
        "x-access-secret": secret,
        "Accept": "application/json",
    }


def is_configured() -> bool:
    """채널톡 콜 인제스트가 실제로 동작 가능한 상태인지."""
    return _channel_headers() is not None


# ──────────────────────────────────────────────
# 채널톡 Open API 래퍼
# ──────────────────────────────────────────────

def _get(path: str, params: dict | None = None, timeout: float = 10.0) -> dict | None:
    hdrs = _channel_headers()
    if not hdrs:
        return None
    url = f"{_CHANNEL_API_BASE}{path}"
    try:
        r = httpx.get(url, headers=hdrs, params=params or {}, timeout=timeout)
        if r.status_code >= 400:
            logger.warning("channel API %s -> %s: %s", path, r.status_code, r.text[:200])
            return None
        return r.json()
    except Exception as e:
        logger.warning("channel API %s failed: %s", path, e)
        return None


def list_recent_calls(since_iso: str, limit: int = 200) -> list[dict]:
    """지난 N분 통화 로그. `GET /open/meet/call/log?since=`.

    since_iso: ISO-8601 UTC (예: 2026-09-28T00:00:00Z)
    반환 원본 스키마는 채널톡 변경 가능성이 있으므로 최소 필드만 파싱해서 사용.
    """
    data = _get("/meet/call/log", {"since": since_iso, "limit": limit})
    if not data:
        return []
    # 응답 스키마: {logs: [...], next?: ...} 또는 flat list. 두 케이스 모두 처리.
    if isinstance(data, list):
        return data
    for k in ("logs", "callLogs", "items", "data"):
        v = data.get(k)
        if isinstance(v, list):
            return v
    return []


def fetch_meet_meta(user_chat_id: str, message_id: str) -> dict | None:
    """미트 메시지 하나의 메타(재생시간·발신자·매니저 등)."""
    return _get(f"/user-chats/{user_chat_id}/meets/{message_id}")


def fetch_meet_transcript(user_chat_id: str, message_id: str) -> str:
    """채널톡 STT 원문 전체. 발화자별로 정리해 하나의 텍스트로."""
    data = _get(f"/user-chats/{user_chat_id}/meets/{message_id}/messages") or {}
    msgs = []
    if isinstance(data, dict):
        for k in ("messages", "items", "data"):
            v = data.get(k)
            if isinstance(v, list):
                msgs = v
                break
    elif isinstance(data, list):
        msgs = data
    lines: list[str] = []
    for m in msgs:
        speaker = (
            (m.get("author") or {}).get("name")
            or (m.get("speaker") or {}).get("name")
            or m.get("personType")
            or "화자"
        )
        text = (m.get("plainText") or m.get("text") or "").strip()
        if text:
            lines.append(f"[{speaker}] {text}")
    return "\n".join(lines)


def fetch_meet_recording(user_chat_id: str, message_id: str) -> dict:
    """녹음 URL·만료시각. 반환: {url, expires_at} (없으면 빈 dict)."""
    data = _get(f"/user-chats/{user_chat_id}/meets/{message_id}/recording") or {}
    url = (
        data.get("url")
        or data.get("recordingUrl")
        or (data.get("recording") or {}).get("url")
        or ""
    )
    exp = (
        data.get("expiresAt")
        or data.get("expires_at")
        or (data.get("recording") or {}).get("expiresAt")
    )
    return {"url": url, "expires_at": _parse_ms_ts(exp)}


def _parse_ms_ts(v: Any) -> str | None:
    """채널톡이 ms epoch 또는 ISO 문자열로 시각을 주는 경우 모두 처리."""
    if v is None or v == "":
        return None
    try:
        if isinstance(v, (int, float)):
            # ms epoch
            dt = datetime.fromtimestamp(float(v) / 1000.0, tz=timezone.utc)
            return dt.isoformat()
        s = str(v).strip()
        if s.isdigit():
            dt = datetime.fromtimestamp(int(s) / 1000.0, tz=timezone.utc)
            return dt.isoformat()
        # ISO 문자열은 그대로 저장
        return s
    except Exception:
        return None


# ──────────────────────────────────────────────
# Gemini 요약
# ──────────────────────────────────────────────

def summarize_transcript(
    transcript: str,
    *,
    from_number: str | None = None,
    duration_seconds: int | None = None,
) -> dict:
    """
    통화 전사 → JSON 요약. 실패 시 {summary: ..., parse_error: ...} 반환.
    스키마: {summary, category, action_items[], estimated_amount, caller_name}
    """
    text = (transcript or "").strip()
    if not text:
        return {"summary": "", "category": None, "action_items": [], "estimated_amount": None, "caller_name": None}
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not key:
        return {"summary": text[:200], "category": None, "action_items": [], "estimated_amount": None, "caller_name": None, "parse_error": "no_gemini_key"}

    _cats = "제품문의/가격문의/배송문의/AS/견적요청/방문예약/기타"
    prompt = (
        "다음은 가구 매장(에몬스)의 고객 상담 통화 전사입니다.\n"
        "전사에는 오탈자가 있을 수 있으니 무리하게 추측하지 말고, 확실한 내용만 요약하세요.\n"
        "아래 JSON 스키마 그대로 응답하세요 (다른 텍스트 없이 JSON만):\n"
        "{\n"
        '  "summary": "3줄 이내 한국어 요약",\n'
        f'  "category": "{_cats} 중 하나 또는 null",\n'
        '  "action_items": ["담당자 조치 항목 배열, 없으면 빈 배열"],\n'
        '  "estimated_amount": 숫자(원 단위, 언급 없으면 null),\n'
        '  "caller_name": "고객이 밝힌 이름 또는 null"\n'
        "}\n\n"
        f"발신번호: {from_number or '알 수 없음'}\n"
        f"통화시간(초): {duration_seconds or 0}\n\n"
        f"전사:\n{text[:5000]}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0,
            "responseMimeType": "application/json",
        },
    }
    try:
        r = httpx.post(_GEMINI_URL_TMPL.format(key=key), json=body, timeout=20.0)
        r.raise_for_status()
        raw = r.json()
        ai_text = raw["candidates"][0]["content"]["parts"][0]["text"]
        parsed = json.loads(ai_text)
    except Exception as e:
        logger.warning("gemini summarize failed: %s", e)
        return {
            "summary": text[:200],
            "category": None,
            "action_items": [],
            "estimated_amount": None,
            "caller_name": None,
            "parse_error": str(e),
        }
    # 안전한 캐스팅
    action_items = parsed.get("action_items")
    if not isinstance(action_items, list):
        action_items = []
    est = parsed.get("estimated_amount")
    try:
        est_num = float(est) if est not in (None, "", "null") else None
    except Exception:
        est_num = None
    return {
        "summary": str(parsed.get("summary") or "").strip(),
        "category": (str(parsed.get("category")).strip() if parsed.get("category") else None),
        "action_items": action_items,
        "estimated_amount": est_num,
        "caller_name": (str(parsed.get("caller_name")).strip() if parsed.get("caller_name") else None),
    }


# ──────────────────────────────────────────────
# Supabase 헬퍼 (channel_team ↔ db_filename 매핑, upsert, log)
# ──────────────────────────────────────────────

def _lookup_db_filename(channel_team_id: str | None) -> str | None:
    if not channel_team_id:
        return None
    try:
        r = httpx.get(
            _supa_url("app_call_channel_teams"),
            headers=_supa_headers(),
            params={
                "channel_team_id": f"eq.{channel_team_id}",
                "select": "db_filename",
                "limit": "1",
            },
            timeout=5.0,
        )
        rows = r.json() if r.status_code == 200 else []
        return rows[0]["db_filename"] if rows else None
    except Exception as e:
        logger.warning("lookup_db_filename failed: %s", e)
        return None


def _lookup_store_name(db_filename: str | None) -> str | None:
    if not db_filename:
        return None
    try:
        r = httpx.get(
            _supa_url("app_stores"),
            headers=_supa_headers(),
            params={
                "db_filename": f"eq.{db_filename}",
                "select": "store_name",
                "limit": "1",
            },
            timeout=5.0,
        )
        rows = r.json() if r.status_code == 200 else []
        return rows[0]["store_name"] if rows else None
    except Exception:
        return None


def _existing_call_record(channel_meet_message_id: str) -> dict | None:
    try:
        r = httpx.get(
            _supa_url("app_call_records"),
            headers=_supa_headers(),
            params={
                "channel_meet_message_id": f"eq.{channel_meet_message_id}",
                "select": "id,channel_meet_message_id",
                "limit": "1",
            },
            timeout=5.0,
        )
        rows = r.json() if r.status_code == 200 else []
        return rows[0] if rows else None
    except Exception:
        return None


def _upsert_call_record(payload: dict) -> int | None:
    """UNIQUE channel_meet_message_id 기준 upsert. 반환: id."""
    try:
        r = httpx.post(
            _supa_url("app_call_records"),
            json=payload,
            headers={
                **_supa_headers("resolution=merge-duplicates,return=representation"),
                "Prefer": "resolution=merge-duplicates,return=representation",
            },
            params={"on_conflict": "channel_meet_message_id"},
            timeout=8.0,
        )
        if r.status_code >= 400:
            logger.warning("app_call_records upsert %s: %s", r.status_code, r.text[:200])
            return None
        rows = r.json() if r.content else []
        return int(rows[0]["id"]) if rows else None
    except Exception as e:
        logger.warning("app_call_records upsert failed: %s", e)
        return None


def _log_ingest(
    *,
    source: str,
    action: str,
    channel_user_chat_id: str | None = None,
    channel_meet_message_id: str | None = None,
    db_filename: str | None = None,
    from_number: str | None = None,
    ok: bool = True,
    detail: str | None = None,
) -> None:
    try:
        httpx.post(
            _supa_url("app_call_ingest_log"),
            json={
                "source": source,
                "action": action,
                "channel_user_chat_id": channel_user_chat_id,
                "channel_meet_message_id": channel_meet_message_id,
                "db_filename": db_filename,
                "from_number": from_number,
                "ok": ok,
                "detail": (detail or "")[:2000] or None,
            },
            headers=_supa_headers("return=minimal"),
            timeout=5.0,
        )
    except Exception as e:
        logger.warning("app_call_ingest_log insert failed: %s", e)


# ──────────────────────────────────────────────
# 메타 파서 (채널톡 응답 → 우리 컬럼)
# ──────────────────────────────────────────────

def _pick_meta(meta: dict) -> dict:
    """
    채널톡 응답에서 필요한 필드만 뽑는다. 스키마 변화에 관대하게.
    반환 필드: direction, from_number, to_number, manager_email, manager_name,
              channel_team_id, started_at, engaged_at, ended_at, duration_seconds
    """
    out: dict[str, Any] = {}
    m = meta or {}
    # direction
    d = (m.get("direction") or m.get("callDirection") or "").lower()
    if d in ("inbound", "outbound", "missed"):
        out["direction"] = d
    elif m.get("missed") is True or m.get("missedAt") is not None:
        out["direction"] = "missed"
    # from/to
    frm = m.get("from") or (m.get("caller") or {}).get("number") or m.get("fromNumber")
    to = m.get("to") or (m.get("callee") or {}).get("number") or m.get("toNumber")
    if frm:
        out["from_number"] = normalize_phone(str(frm))
    if to:
        out["to_number"] = normalize_phone(str(to))
    # manager
    mgr = m.get("manager") or (m.get("engagedManager") or {})
    out["manager_email"] = (mgr.get("email") or "").strip() or None
    out["manager_name"] = (mgr.get("name") or "").strip() or None
    # team
    out["channel_team_id"] = str(m.get("teamId") or m.get("channelTeamId") or "") or None
    # timestamps
    for src, dst in (
        ("startedAt", "started_at"),
        ("engagedAt", "engaged_at"),
        ("endedAt", "ended_at"),
    ):
        v = m.get(src)
        if v is None:
            continue
        parsed = _parse_ms_ts(v)
        if parsed:
            out[dst] = parsed
    # duration: endedAt - engagedAt (ms) 가 있으면 사용, 없으면 talkTime/duration 필드
    dur = m.get("duration") or m.get("talkTime") or m.get("callDuration")
    if isinstance(dur, (int, float)):
        # ms 로 오는 경우가 많음 — 100초 이상이면 ms 로 간주
        out["duration_seconds"] = int(dur / 1000) if dur > 1000 else int(dur)
    return out


# ──────────────────────────────────────────────
# 핵심 인제스트
# ──────────────────────────────────────────────

def _try_match_lead(
    *,
    from_number: str | None,
    store_name: str | None,
    caller_name: str | None,
    summary: str,
) -> int | None:
    """
    발신번호로 리드 검색 → 없으면 upsert. matched_lead_id 반환.
    upsert 는 lead_service.upsert_lead 재사용 (전화_문의 채널로 기록).
    """
    if not from_number:
        return None
    existing = get_lead_by_phone(from_number)
    if existing and existing.get("id"):
        # 상담 이력 append (기존 leads/{id}/notes 흐름과 동일)
        try:
            append_chat_history(
                phone=from_number,
                channel="채널톡_콜",
                summary=("통화 요약: " + summary)[:500],
                handled_by="channel_call_ingest",
            )
        except Exception as e:
            logger.warning("append_chat_history (existing lead) failed: %s", e)
        return int(existing["id"])

    # 신규 리드 생성
    try:
        result = upsert_lead(
            phone=from_number,
            name=caller_name or None,
            memo=None,
            lead_source="전화_문의",
            store_name=store_name,
            customer_type=None,
            source_system="channel_call_ingest",
            log_note=True,
        )
    except LeadServiceError as e:
        logger.warning("upsert_lead failed: %s", e)
        return None
    if not result.get("ok"):
        logger.warning("upsert_lead not ok: %s", result.get("error"))
        return None
    lead_id = result.get("lead_id")
    # 신규 생성 시에도 요약을 별도 이력으로 남긴다
    try:
        append_chat_history(
            phone=from_number,
            channel="채널톡_콜",
            summary=("통화 요약: " + summary)[:500],
            handled_by="channel_call_ingest",
        )
    except Exception:
        pass
    return int(lead_id) if lead_id else None


def ingest_meet(
    user_chat_id: str,
    message_id: str,
    *,
    source: str = "webhook",
) -> dict:
    """
    단일 미트(통화) 인제스트. idempotent (UNIQUE channel_meet_message_id).
    반환: {ok, call_id, lead_id, action, detail?}
    """
    if not user_chat_id or not message_id:
        return {"ok": False, "action": "skip_missing_ids"}

    # 이미 처리된 경우: 재요약·재매칭은 하지 않고 그대로 반환
    existing = _existing_call_record(message_id)
    if existing:
        return {"ok": True, "call_id": int(existing["id"]), "action": "skip_duplicate"}

    if not is_configured():
        _log_ingest(source=source, action="skip_no_config", ok=False,
                    channel_user_chat_id=user_chat_id, channel_meet_message_id=message_id,
                    detail="CHANNEL_TALK_ACCESS_KEY/SECRET not set")
        return {"ok": False, "action": "skip_no_config"}

    meta_raw = fetch_meet_meta(user_chat_id, message_id) or {}
    meta = _pick_meta(meta_raw)
    db_filename = _lookup_db_filename(meta.get("channel_team_id"))
    store_name = _lookup_store_name(db_filename) if db_filename else None

    transcript = fetch_meet_transcript(user_chat_id, message_id)
    rec = fetch_meet_recording(user_chat_id, message_id)

    summary_out = summarize_transcript(
        transcript,
        from_number=meta.get("from_number"),
        duration_seconds=meta.get("duration_seconds"),
    )

    matched_lead_id = _try_match_lead(
        from_number=meta.get("from_number"),
        store_name=store_name,
        caller_name=summary_out.get("caller_name"),
        summary=summary_out.get("summary") or "",
    )

    payload = {
        "db_filename": db_filename,
        "channel_user_chat_id": user_chat_id,
        "channel_meet_message_id": message_id,
        "direction": meta.get("direction"),
        "from_number": meta.get("from_number"),
        "to_number": meta.get("to_number"),
        "caller_name": summary_out.get("caller_name"),
        "matched_lead_id": matched_lead_id,
        "manager_email": meta.get("manager_email"),
        "manager_name": meta.get("manager_name"),
        "started_at": meta.get("started_at"),
        "engaged_at": meta.get("engaged_at"),
        "ended_at": meta.get("ended_at"),
        "duration_seconds": meta.get("duration_seconds"),
        "recording_url": rec.get("url") or None,
        "recording_expires_at": rec.get("expires_at"),
        "transcript": transcript or None,
        "summary": summary_out.get("summary") or None,
        "category": summary_out.get("category"),
        "action_items": summary_out.get("action_items"),
        "estimated_amount": summary_out.get("estimated_amount"),
        "raw": {"meta": meta_raw},
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    call_id = _upsert_call_record(payload)
    ok = call_id is not None
    _log_ingest(
        source=source,
        action="ingest_ok" if ok else "ingest_failed",
        ok=ok,
        channel_user_chat_id=user_chat_id,
        channel_meet_message_id=message_id,
        db_filename=db_filename,
        from_number=meta.get("from_number"),
        detail=None if ok else "upsert failed",
    )
    return {
        "ok": ok,
        "call_id": call_id,
        "lead_id": matched_lead_id,
        "action": "ingest_ok" if ok else "ingest_failed",
        "db_filename": db_filename,
        "matched_lead": bool(matched_lead_id),
    }


def poll_recent_calls(minutes: int = 15) -> dict:
    """
    지난 N분 통화 로그 조회 후, DB 에 없는 것만 ingest_meet 호출.
    외부 cron 이 10분마다 호출하는 백업 경로. idempotent.
    """
    total = {"scanned": 0, "ingested": 0, "skipped": 0, "failed": 0, "items": []}
    if not is_configured():
        total["error"] = "not_configured"
        return total
    since = (datetime.now(timezone.utc) - timedelta(minutes=int(minutes))).isoformat()
    logs = list_recent_calls(since)
    total["scanned"] = len(logs)
    for row in logs:
        uc_id = str(row.get("userChatId") or row.get("user_chat_id") or "") or None
        m_id = str(row.get("messageId") or row.get("meetMessageId") or row.get("id") or "") or None
        if not (uc_id and m_id):
            total["skipped"] += 1
            continue
        if _existing_call_record(m_id):
            total["skipped"] += 1
            continue
        res = ingest_meet(uc_id, m_id, source="poll")
        if res.get("ok"):
            total["ingested"] += 1
        else:
            total["failed"] += 1
        total["items"].append({
            "user_chat_id": uc_id,
            "message_id": m_id,
            "ok": res.get("ok"),
            "action": res.get("action"),
        })
    return total


# ──────────────────────────────────────────────
# 웹훅 감지: 메시지 payload 에서 Meet 종료 이벤트 여부
# ──────────────────────────────────────────────

_MEET_HINT_KEYS = ("meet", "call", "callLog", "voip")


def detect_meet_message(payload: dict) -> tuple[str, str] | None:
    """
    채널톡 메시지 웹훅 payload 에서 Meet 종료 관련 메시지 감지.
    감지되면 (user_chat_id, message_id) 반환, 아니면 None.

    채널톡 웹훅 스키마:
      payload["entity"] = Message
      payload["type"]   = "Message"
      payload["event"]  = "upsert"/"push"
    Meet 종료 시스템 메시지는 blocks/messageV2/type 등에 "meet"/"call" 힌트가 붙는다.
    """
    if not isinstance(payload, dict):
        return None
    typ = str(payload.get("type") or "").lower()
    if typ and typ not in ("message",):
        return None
    entity = payload.get("entity") or {}
    if not isinstance(entity, dict):
        return None
    msg_id = str(entity.get("id") or "")
    chat_id = str(entity.get("chatId") or entity.get("userChatId") or "")
    chat_type = str(entity.get("chatType") or "").lower()
    if not (msg_id and chat_id and chat_type == "userchat"):
        return None
    # 힌트 검사: blocks / files / messageV2 / plainText
    hint_text = json.dumps(entity, ensure_ascii=False).lower()
    if not any(k in hint_text for k in _MEET_HINT_KEYS):
        return None
    return chat_id, msg_id
