"""
리드고객 관리 페이지 (메뉴 3번).

전체 매장 공유 + 상담 히스토리 통합 + 메시지 발송 + 채널톡 유입 자동 가져오기.

관련 테이블:
    - app_leads               (리드 정보)
    - app_chat_history        (채널톡/카카오톡/오프라인 상담 이력)
    - app_customer_messages   (우리가 보낸 메시지 이력)
    - app_customers           (등록 고객 마스터)
    - app_users               (직원 정보 - 담당자 이름 표시용)
"""

from __future__ import annotations

import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
import streamlit as st

# ── 리드 단계 정의 ───────────────────────────────
LEAD_STAGES: dict[str, str] = {
    "1_신규": "신규",
    "2_상담중": "상담중",
    "3_견적발송": "견적발송",
    "4_계약완료": "계약완료",
    "5_실패": "실패",
    "6_보류": "보류",
}
LEAD_STAGE_BADGE: dict[str, str] = {
    "1_신규": "#e2e8f0",      # 회색
    "2_상담중": "#fef3c7",    # 노란
    "3_견적발송": "#dbeafe",   # 파랑
    "4_계약완료": "#d1fae5",   # 초록
    "5_실패": "#fee2e2",      # 빨강
    "6_보류": "#f3f4f6",      # 연회색
}
LEAD_STAGE_TEXT: dict[str, str] = {
    "1_신규": "#475569",
    "2_상담중": "#92400e",
    "3_견적발송": "#1e40af",
    "4_계약완료": "#065f46",
    "5_실패": "#991b1b",
    "6_보류": "#374151",
}

LEAD_SOURCE_LABELS: dict[str, str] = {
    "전화_문의": "전화문의",
    "오프라인_방문": "오프라인 방문",
    "온라인_채널톡": "채널톡",
    "카카오톡": "카카오톡",
    "기타": "기타",
}


# ──────────────────────────────────────────────
# Supabase 헬퍼 (lead_manager와 동일한 방식)
# ──────────────────────────────────────────────

def _supa() -> Any:
    """Supabase 클라이언트 반환 (없으면 None)."""
    try:
        from supabase_client import get_supabase  # type: ignore
        return get_supabase()
    except Exception:
        pass
    try:
        from supabase import create_client  # type: ignore
        sec = st.secrets.get("supabase", {}) if hasattr(st, "secrets") else {}
        url = sec.get("url") or os.environ.get("SUPABASE_URL", "")
        key = (
            sec.get("service_role_key")
            or sec.get("key")
            or os.environ.get("SUPABASE_SERVICE_KEY", "")
        )
        if url and key:
            return create_client(url, key)
    except Exception:
        pass
    return None


def _normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("82") and len(digits) >= 11:
        digits = "0" + digits[2:]
    return digits


def _format_phone_display(phone: str) -> str:
    """01012345678 → 010-1234-5678"""
    d = re.sub(r"\D", "", phone or "")
    if len(d) == 11:
        return f"{d[:3]}-{d[3:7]}-{d[7:]}"
    if len(d) == 10:
        return f"{d[:3]}-{d[3:6]}-{d[6:]}"
    return phone or ""


# ──────────────────────────────────────────────
# 직원 매핑 (담당직원 표시용)
# ──────────────────────────────────────────────

@st.cache_data(ttl=300, show_spinner=False)
def _get_employee_map() -> dict[int, str]:
    """employee_id → 직원 이름."""
    supa = _supa()
    if not supa:
        return {}
    try:
        rows = supa.table("app_users").select("id,name,username,store_name").execute().data or []
        return {
            int(r["id"]): (r.get("name") or r.get("username") or f"#{r['id']}")
            for r in rows if r.get("id")
        }
    except Exception:
        return {}


# ──────────────────────────────────────────────
# 상담 히스토리 통합 조회
# ──────────────────────────────────────────────

def _get_unified_history(phone: str, lead_id: int | None = None) -> list[dict]:
    """
    app_chat_history (수신) + app_customer_messages (발신) 통합.
    각 항목: {at, direction, channel, body, by, source_table}
    """
    supa = _supa()
    if not supa:
        return []
    phone_n = _normalize_phone(phone)
    items: list[dict] = []

    try:
        rows = supa.table("app_chat_history") \
            .select("id,channel,summary,full_text,handled_by,created_at,chat_id") \
            .eq("customer_phone", phone_n).order("created_at", desc=True).limit(100).execute().data or []
        for r in rows:
            items.append({
                "at": r.get("created_at", ""),
                "direction": "수신",
                "channel": r.get("channel", ""),
                "body": (r.get("full_text") or r.get("summary") or "").strip(),
                "by": r.get("handled_by") or "",
                "source": "상담이력",
                "chat_id": r.get("chat_id") or "",
            })
    except Exception:
        pass

    try:
        rows = supa.table("app_customer_messages") \
            .select("id,channel,message_type,status,message_body,sent_by,error_detail,created_at") \
            .eq("phone", phone_n).order("created_at", desc=True).limit(100).execute().data or []
        for r in rows:
            _st_label = r.get("status") or ""
            _suffix = "" if _st_label in ("sent", "") else f" [{_st_label}]"
            items.append({
                "at": r.get("created_at", ""),
                "direction": "발신",
                "channel": r.get("channel", ""),
                "body": (r.get("message_body") or "").strip() + _suffix,
                "by": r.get("sent_by") or "",
                "source": "발송이력",
                "msg_type": r.get("message_type", ""),
                "error": r.get("error_detail", ""),
            })
    except Exception:
        pass

    items.sort(key=lambda x: x.get("at", ""), reverse=True)
    return items


def _log_customer_message(
    *,
    phone: str,
    channel: str,
    body: str,
    status: str,
    sent_by: str,
    solapi_msg_id: str = "",
    error: str = "",
    customer_id: int | None = None,
    store_name: str | None = None,
) -> None:
    """app_customer_messages에 발신 로그 기록."""
    supa = _supa()
    if not supa:
        return
    try:
        supa.table("app_customer_messages").insert({
            "customer_id": customer_id,
            "store_name": store_name,
            "phone": _normalize_phone(phone),
            "message_type": "manual",
            "channel": channel,
            "status": status,
            "solapi_msg_id": solapi_msg_id or None,
            "message_body": body[:2000] if body else "",
            "error_detail": error[:500] if error else None,
            "sent_by": sent_by or "",
        }).execute()
    except Exception:
        pass


# ──────────────────────────────────────────────
# 채널톡 → 리드 가져오기
# ──────────────────────────────────────────────

def import_leads_from_channeltalk(limit: int = 50) -> dict[str, Any]:
    """
    채널톡 Open API에서 최근 사용자 N명 조회 → 신규 phone만 리드로 등록.

    제외 조건:
        - phone 없음
        - 이미 app_customers에 등록된 phone
        - 이미 app_leads에 등록된 phone
    """
    import requests
    access_key = (
        os.environ.get("CHANNEL_TALK_ACCESS_KEY", "")
        or os.environ.get("CHANNEL_TALK_API_KEY", "")
    )
    access_secret = os.environ.get("CHANNEL_TALK_ACCESS_SECRET", "")
    if not access_key or not access_secret:
        try:
            access_key = access_key or str(st.secrets.get("CHANNEL_TALK_API_KEY", ""))
            access_secret = access_secret or str(st.secrets.get("CHANNEL_TALK_ACCESS_SECRET", ""))
        except Exception:
            pass

    if not access_key or not access_secret:
        return {"ok": False, "error": "채널톡 API 키 미설정", "imported": 0, "skipped": 0, "details": []}

    headers = {
        "x-access-key": access_key,
        "x-access-secret": access_secret,
        "Content-Type": "application/json",
    }
    try:
        resp = requests.get(
            "https://api.channel.io/open/v5/users",
            headers=headers,
            params={"limit": str(min(limit, 100)), "sortOrder": "desc"},
            timeout=15.0,
        )
    except Exception as e:
        return {"ok": False, "error": f"채널톡 API 호출 실패: {e}", "imported": 0, "skipped": 0, "details": []}

    if resp.status_code >= 400:
        return {"ok": False, "error": f"채널톡 응답 {resp.status_code}: {resp.text[:200]}", "imported": 0, "skipped": 0, "details": []}

    try:
        data = resp.json()
    except Exception:
        return {"ok": False, "error": "응답 파싱 실패", "imported": 0, "skipped": 0, "details": []}

    users = data.get("users") or data.get("items") or []
    if not isinstance(users, list):
        return {"ok": False, "error": "유효한 사용자 목록 없음", "imported": 0, "skipped": 0, "details": []}

    supa = _supa()
    if not supa:
        return {"ok": False, "error": "Supabase 연결 실패", "imported": 0, "skipped": 0, "details": []}

    # 직원 이름 → ID 역방향 맵 (판매담당자 매핑용)
    _emp_name_to_id: dict[str, int] = {}
    try:
        _emp_rows = supa.table("app_users").select("id,name,username").execute().data or []
        for _er in _emp_rows:
            _en = (_er.get("name") or _er.get("username") or "").strip()
            if _en and _er.get("id"):
                _emp_name_to_id[_en] = int(_er["id"])
    except Exception:
        pass

    imported, skipped = 0, 0
    details: list[dict] = []

    for u in users:
        raw_phone = (
            u.get("mobileNumber")
            or u.get("phoneNumber")
            or (u.get("profile") or {}).get("mobileNumber")
            or ""
        )
        phone = _normalize_phone(raw_phone)
        _profile = u.get("profile") or {}
        _udata = u.get("data") or {}
        name = u.get("name") or _profile.get("name") or ""

        # 채널톡 커스텀 필드 "판매담당자" 추출 (여러 위치 시도)
        _sales_rep_name = (
            _profile.get("판매담당자")
            or _udata.get("판매담당자")
            or _profile.get("salesManager")
            or _udata.get("salesManager")
            or ""
        )
        _sales_rep_id = _emp_name_to_id.get(_sales_rep_name.strip()) if _sales_rep_name.strip() else None

        if not phone or len(phone) < 10:
            skipped += 1
            continue

        try:
            cust_rows = supa.table("app_customers").select("id") \
                .eq("phone", phone).limit(1).execute().data or []
            if cust_rows:
                skipped += 1
                details.append({"phone": phone, "name": name, "reason": "이미 등록 고객"})
                continue
        except Exception:
            pass

        try:
            lead_rows = supa.table("app_leads").select("id,assigned_employee_id") \
                .eq("phone", phone).limit(1).execute().data or []
            if lead_rows:
                # 이미 리드 존재 → 판매담당자만 업데이트 (비어 있을 때)
                _existing = lead_rows[0]
                if _sales_rep_id and not _existing.get("assigned_employee_id"):
                    try:
                        supa.table("app_leads").update(
                            {"assigned_employee_id": _sales_rep_id}
                        ).eq("id", _existing["id"]).execute()
                        details.append({"phone": phone, "name": name, "reason": f"리드 담당자 업데이트 → {_sales_rep_name}"})
                    except Exception:
                        pass
                else:
                    skipped += 1
                    details.append({"phone": phone, "name": name, "reason": "이미 리드 등록"})
                continue
        except Exception:
            pass

        try:
            _insert_row: dict = {
                "store_name": "전체",
                "phone": phone,
                "name": name or "채널톡 고객",
                "memo": (u.get("memo") or "")[:500],
                "lead_source": "온라인_채널톡",
                "lead_stage": "1_신규",
                "customer_type": "신규잠재고객",
                "nurturing_step": 0,
            }
            if _sales_rep_id:
                _insert_row["assigned_employee_id"] = _sales_rep_id
            supa.table("app_leads").insert(_insert_row).execute()
            imported += 1
            _rep_label = f" (담당: {_sales_rep_name})" if _sales_rep_name else ""
            details.append({"phone": phone, "name": name, "reason": f"신규 등록{_rep_label}"})
        except Exception as e:
            skipped += 1
            details.append({"phone": phone, "name": name, "reason": f"등록 실패: {str(e)[:80]}"})

    return {"ok": True, "imported": imported, "skipped": skipped, "details": details, "total_seen": len(users)}


# ──────────────────────────────────────────────
# 메인 페이지
# ──────────────────────────────────────────────

def _build_purchase_map(supa: Any) -> tuple[dict[str, str], dict[str, int]]:
    """
    실제 매출(app_orders)이 있는 고객만 추출하여 phone → employee_names 맵 생성.

    Returns:
        (phone_to_emp_names, phone_to_emp_id)
            phone_to_emp_names: 정규화 phone → 가장 최근 주문의 employee_names 문자열
            phone_to_emp_id   : 정규화 phone → 해당 employee_names 첫 번째 이름의 app_users.id
    """
    phone_to_emp_names: dict[str, str] = {}
    phone_to_emp_id: dict[str, int] = {}

    # ① 직원 이름 → ID 맵
    emp_name_to_id: dict[str, int] = {}
    try:
        for er in (supa.table("app_users").select("id,name,username").execute().data or []):
            en = (er.get("name") or er.get("username") or "").strip()
            if en and er.get("id"):
                emp_name_to_id[en] = int(er["id"])
    except Exception:
        return phone_to_emp_names, phone_to_emp_id

    # ② app_orders 전체 조회 → customer_id별 최신 employee_names 수집
    customer_id_to_emp: dict[int, str] = {}
    offset = 0
    while True:
        try:
            batch = supa.table("app_orders") \
                .select("id,customer_id,employee_names") \
                .not_.is_("customer_id", "null") \
                .order("id", desc=True) \
                .range(offset, offset + 999).execute().data or []
        except Exception:
            break
        if not batch:
            break
        for o in batch:
            cid = o.get("customer_id")
            if cid is None:
                continue
            # 최신순으로 정렬되어 들어오므로, 처음 만난 cid가 가장 최근 주문
            if cid not in customer_id_to_emp:
                customer_id_to_emp[cid] = (o.get("employee_names") or "")
        if len(batch) < 1000:
            break
        offset += 1000

    if not customer_id_to_emp:
        return phone_to_emp_names, phone_to_emp_id

    # ③ 주문 있는 customer_id의 phone1/phone2 조회 → phone 맵 채우기
    cid_list = list(customer_id_to_emp.keys())
    for i in range(0, len(cid_list), 200):
        chunk = cid_list[i:i + 200]
        try:
            rows = supa.table("app_customers").select("id,phone1,phone2") \
                .in_("id", chunk).execute().data or []
        except Exception:
            continue
        for r in rows:
            cid = r.get("id")
            if cid is None:
                continue
            emp_names = customer_id_to_emp.get(cid, "")
            for col in ("phone1", "phone2"):
                np = _normalize_phone(r.get(col) or "")
                if not np:
                    continue
                if np not in phone_to_emp_names:
                    phone_to_emp_names[np] = emp_names
                    first = next((n.strip() for n in emp_names.split(",") if n.strip()), "")
                    if first in emp_name_to_id:
                        phone_to_emp_id[np] = emp_name_to_id[first]

    return phone_to_emp_names, phone_to_emp_id


def _sync_leads_to_customers(full: bool = False) -> dict:
    """
    app_orders(매출) 기반으로 app_leads 동기화.
    매출이 있는 고객의 phone과 일치하는 리드만 → 4_계약완료 + 담당직원 매핑.

    full=False (자동): 활성 리드만 4_계약완료로 업데이트
    full=True  (수동): 전체 리드 대상으로 담당직원 누락분도 보강

    반환: {stage_updated, emp_updated, total_matched, errors}
    """
    supa = _supa()
    result = {"stage_updated": 0, "emp_updated": 0, "total_matched": 0,
              "purchase_map_size": 0, "errors": []}
    if not supa:
        result["errors"].append("Supabase 연결 실패")
        return result

    # ① 매출 기반 phone 맵 구성
    phone_to_emp_names, phone_to_emp_id = _build_purchase_map(supa)
    result["purchase_map_size"] = len(phone_to_emp_names)

    if not phone_to_emp_names:
        result["errors"].append("매출(app_orders) 데이터에서 phone 매핑 실패")
        return result

    try:
        # ② 리드 조회
        if full:
            all_leads = supa.table("app_leads").select(
                "id,phone,lead_stage,assigned_employee_id"
            ).execute().data or []
        else:
            all_leads = supa.table("app_leads").select(
                "id,phone,lead_stage,assigned_employee_id"
            ).not_.in_("lead_stage", ["4_계약완료", "5_실패", "6_보류"]).execute().data or []

        if not all_leads:
            return result

        now_utc = datetime.now(timezone.utc).isoformat()

        for l in all_leads:
            np = _normalize_phone(l.get("phone") or "")
            if not np or np not in phone_to_emp_names:
                continue

            result["total_matched"] += 1
            lead_id = l["id"]
            stage = l.get("lead_stage") or "1_신규"
            has_emp = bool(l.get("assigned_employee_id"))

            upd: dict = {"updated_at": now_utc}
            did = False

            if stage not in ("4_계약완료", "5_실패", "6_보류"):
                upd["lead_stage"] = "4_계약완료"
                upd["converted_at"] = now_utc
                did = True
                result["stage_updated"] += 1

            if not has_emp and np in phone_to_emp_id:
                upd["assigned_employee_id"] = phone_to_emp_id[np]
                did = True
                result["emp_updated"] += 1

            if did:
                try:
                    supa.table("app_leads").update(upd).eq("id", lead_id).execute()
                except Exception as e:
                    result["errors"].append(f"lead_id={lead_id}: {str(e)[:60]}")

        return result
    except Exception as e:
        result["errors"].append(str(e)[:100])
        return result


def render_lead_management() -> None:
    """3. 리드고객 관리 메인 페이지."""

    # ── 백그라운드 자동 동기화 (세션당 1회) ──
    if not st.session_state.get("_lead_synced"):
        _sr = _sync_leads_to_customers(full=False)
        st.session_state["_lead_synced"] = True
        if _sr["stage_updated"] > 0 or _sr["emp_updated"] > 0:
            st.toast(
                f"🔄 자동 동기화: 계약완료 {_sr['stage_updated']}건 · 담당자 {_sr['emp_updated']}건 업데이트",
                icon="✅",
            )

    # ── 페이지 헤더 ────────────────────────────
    _hc1, _hc2, _hc3, _hc4 = st.columns([4, 1.4, 1.4, 1.4])
    with _hc1:
        st.title("📋 리드고객 관리")
        st.caption("빠른 응답은 생각보다 꽤 강력한 무기입니다. (전체 매장 공유)")
    with _hc2:
        if st.button("🔄 담당자 동기화", width="stretch", key="lead_btn_full_sync",
                     help="실제 매출(app_orders)이 있는 고객만 담당직원을 다시 매핑합니다"):
            with st.spinner("동기화 중..."):
                st.session_state.pop("_lead_synced", None)
                _fsr = _sync_leads_to_customers(full=True)
            st.session_state["_sync_last_result"] = _fsr
            st.session_state["_lead_synced"] = True
            st.rerun()

    # 동기화 결과 표시 (rerun 직후 1회만)
    if st.session_state.get("_sync_last_result"):
        _r = st.session_state.pop("_sync_last_result")
        if _r.get("errors"):
            st.warning(f"⚠️ 오류: {_r['errors'][0]}")
        st.success(
            f"✅ 매출 phone {_r['purchase_map_size']}건 로드 · "
            f"리드 매칭 {_r['total_matched']}건 · "
            f"단계 업데이트 {_r['stage_updated']}건 · "
            f"담당자 업데이트 {_r['emp_updated']}건"
        )
        if _r['purchase_map_size'] == 0:
            st.error(
                "💡 매출 phone이 0건입니다. `app_orders.customer_id`가 비어있을 가능성. "
                "Supabase에서 `SELECT COUNT(customer_id) FROM app_orders;`로 확인해 주세요."
            )
    with _hc3:
        if st.button("📥 채널톡 가져오기", width="stretch", key="lead_btn_import_ct"):
            st.session_state["lead_show_import"] = True
            st.session_state["lead_show_form"] = False
            st.session_state.pop("lead_selected_id", None)
    with _hc4:
        if st.button("＋ 리드 등록", type="primary", width="stretch", key="lead_btn_open_form"):
            st.session_state["lead_show_form"] = not st.session_state.get("lead_show_form", False)
            st.session_state["lead_show_import"] = False
            st.session_state.pop("lead_selected_id", None)

    # ── 채널톡 가져오기 패널 ─────────────────────
    if st.session_state.get("lead_show_import"):
        _render_import_panel()

    # ── 리드 등록 폼 ───────────────────────────
    if st.session_state.get("lead_show_form"):
        with st.container(border=True):
            _render_register_form()

    # ── Supabase 데이터 로드 ────────────────────
    supa = _supa()
    if not supa:
        st.error("Supabase 연결 실패")
        return

    _full_select = (
        "id,phone,name,lead_source,lead_stage,memo,contact_memo,"
        "next_contact_date,assigned_employee_id,store_name,"
        "customer_type,classification_memo,classified_by,classified_at,last_contact_at,"
        "created_at,converted_at,revenue_amount,converted_order_id"
    )
    _legacy_select = (
        "id,phone,name,lead_source,lead_stage,memo,contact_memo,"
        "next_contact_date,assigned_employee_id,store_name,"
        "created_at,converted_at,revenue_amount,converted_order_id"
    )
    _migration_pending = False
    try:
        leads_raw: list[dict] = supa.table("app_leads").select(_full_select) \
            .order("created_at", desc=True).limit(500).execute().data or []
    except Exception as e:
        _err_msg = str(e)
        # customer_type 컬럼이 없으면 마이그레이션 전 — 레거시 SELECT로 폴백
        if "customer_type" in _err_msg or "42703" in _err_msg:
            _migration_pending = True
            try:
                leads_raw = supa.table("app_leads").select(_legacy_select) \
                    .order("created_at", desc=True).limit(500).execute().data or []
            except Exception as e2:
                st.error(f"리드 조회 실패: {e2}")
                return
            # 신규 컬럼 기본값 주입 → 이후 UI가 .get()으로 안전 접근
            for _l in leads_raw:
                _l.setdefault("customer_type", "신규잠재고객")
                _l.setdefault("classification_memo", None)
                _l.setdefault("classified_by", None)
                _l.setdefault("classified_at", None)
                _l.setdefault("last_contact_at", None)
        else:
            st.error(f"리드 조회 실패: {e}")
            return

    if _migration_pending:
        st.warning(
            "⚠️ **SQL 마이그레이션 필요** — `SUPABASE_APP_LEADS_CUSTOMER_TYPE.sql` 파일을 "
            "Supabase Dashboard › SQL Editor에서 실행하세요. "
            "고객 유형 분류·재유입 자동 재활성화 기능은 마이그레이션 후 활성화됩니다."
        )

    emp_map = _get_employee_map()

    # ── 구매완료 판정: app_orders(실제 매출)이 있는 phone만 ──
    # 단순 app_customers 등록만으론 구매완료로 분류하지 않음
    _purchase_map, _ = _build_purchase_map(supa)
    customer_phones: set[str] = set(_purchase_map.keys())

    for _l in leads_raw:
        _l["_is_customer"] = _normalize_phone(_l.get("phone") or "") in customer_phones

    _customer_count = sum(1 for _l in leads_raw if _l["_is_customer"])

    # ── Stats 카드 ────────────────────────────
    _total = len(leads_raw)
    _new = sum(1 for l in leads_raw if l.get("lead_stage") == "1_신규")
    _consult = sum(1 for l in leads_raw if l.get("lead_stage") in ("2_상담중", "3_견적발송"))
    _conv = sum(1 for l in leads_raw if l.get("lead_stage") == "4_계약완료" or l.get("_is_customer"))

    sc1, sc2, sc3, sc4, sc5 = st.columns(5)
    sc1.metric("전체 리드", f"{_total}건", help="전체 등록된 잠재 고객 수")
    sc2.metric("신규", f"{_new}건", help="아직 첫 응대가 필요한 문의")
    sc3.metric("상담중", f"{_consult}건", help="현재 커뮤니케이션이 진행 중인 리드")
    sc4.metric(
        "전환 완료",
        f"{_conv}건",
        delta=f"전환율 {round(_conv / _total * 100, 1)}%" if _total else None,
        help="계약 완료 단계 또는 이미 우리 DB에 고객으로 등록된 리드",
    )
    sc5.metric(
        "🛒 구매 완료",
        f"{_customer_count}건",
        help="app_customers 마스터에 phone이 매칭된 리드 (이미 구매한 고객)",
    )

    st.divider()

    # ── Stage filter tabs (segmented) ──────────
    _stage_keys = ["전체"] + list(LEAD_STAGES.keys())
    _stage_labels = ["전체"] + [LEAD_STAGES[k] for k in LEAD_STAGES.keys()]
    if "lead_filter_stage" not in st.session_state:
        st.session_state["lead_filter_stage"] = "전체"
    _sel_label = st.radio(
        "단계",
        _stage_labels,
        index=_stage_labels.index(LEAD_STAGES.get(st.session_state["lead_filter_stage"], "전체"))
            if st.session_state["lead_filter_stage"] != "전체" else 0,
        horizontal=True,
        label_visibility="collapsed",
        key="lead_stage_radio",
    )
    _sel_stage = _stage_keys[_stage_labels.index(_sel_label)]
    st.session_state["lead_filter_stage"] = _sel_stage

    # ── 검색 + 토글 + 카운트 ─────────────────────
    _sc1, _sc2, _sc3 = st.columns([4, 1.7, 1])
    with _sc1:
        _q = st.text_input(
            "검색", placeholder="회사명, 담당자, 연락처를 검색하세요",
            label_visibility="collapsed", key="lead_search_q",
        )
    with _sc2:
        _hide_customers = st.toggle(
            "🛒 구매완료 숨기기",
            value=st.session_state.get("lead_hide_customers", True),
            key="lead_hide_customers",
            help="DB에 이미 등록된(구매 완료) 고객을 목록에서 숨깁니다.",
        )

    # ── 고객 유형 필터 (customer_type) ───────────
    _type_options = ["전체", "신규잠재고객", "기존구매고객_DB외", "AS요청", "재상담"]
    _type_labels = ["전체", "🆕 신규", "🔄 기존구매(DB외)", "🔧 AS요청", "💬 재상담"]
    _sel_type_label = st.radio(
        "고객 유형",
        _type_labels,
        index=0,
        horizontal=True,
        label_visibility="collapsed",
        key="lead_type_radio",
    )
    _sel_type = _type_options[_type_labels.index(_sel_type_label)]

    # ── 필터링 ────────────────────────────────
    leads = leads_raw
    if _hide_customers:
        leads = [l for l in leads if not l.get("_is_customer")]
    if _sel_stage != "전체":
        leads = [l for l in leads if l.get("lead_stage") == _sel_stage]
    if _sel_type != "전체":
        leads = [l for l in leads if (l.get("customer_type") or "신규잠재고객") == _sel_type]
    if _q:
        _ql = _q.lower()
        leads = [
            l for l in leads
            if _ql in (l.get("name") or "").lower()
            or _ql in (l.get("phone") or "")
            or _ql in (l.get("store_name") or "").lower()
        ]

    with _sc3:
        st.markdown(
            f"<div style='text-align:right;padding-top:8px;color:#666;'>{len(leads)}건 표시 중</div>",
            unsafe_allow_html=True,
        )

    if not leads:
        st.info("조건에 맞는 리드가 없습니다.")
        return

    # ── 담당직원 클릭 필터 표시 ────────────────
    _filter_emp_id = st.session_state.get("lead_filter_emp_id")
    if _filter_emp_id:
        _filter_emp_name = emp_map.get(int(_filter_emp_id), "")
        st.markdown(
            f"<div style='background:#eff6ff;border:1px solid #bfdbfe;border-radius:6px;"
            f"padding:6px 14px;margin-bottom:8px;font-size:0.85rem;color:#1e40af;display:inline-block;'>"
            f"👤 <b>{_filter_emp_name}</b> 담당 리드 표시 중 "
            f"<span style='cursor:pointer;margin-left:8px;'>(전체 보기 → 아래 필터 해제 클릭)</span></div>",
            unsafe_allow_html=True,
        )
        if st.button("✕ 담당자 필터 해제", key="clear_emp_filter"):
            st.session_state.pop("lead_filter_emp_id", None)
            st.rerun()
        leads = [l for l in leads if l.get("assigned_employee_id") == _filter_emp_id]

    # ── 테이블 헤더 ───────────────────────────
    _ths = ["고객명", "연락처", "유형", "유입경로", "상태", "담당직원 ↓클릭=필터", "마지막 연락", ""]
    _ws = [2, 1.6, 1.3, 1.2, 1.5, 1.6, 1.1, 0.8]
    _hc = st.columns(_ws)
    for _c, _t in zip(_hc, _ths):
        _c.markdown(f"<div style='color:#475569;font-weight:600;font-size:0.82rem;'>{_t}</div>", unsafe_allow_html=True)
    st.markdown(
        "<div style='border-bottom:1px solid #e5e7eb;margin:0 0 4px 0;'></div>",
        unsafe_allow_html=True,
    )

    # ── 리드 행 ───────────────────────────────
    for _i, _lead in enumerate(leads):
        _lid = _lead.get("id")
        _name = _lead.get("name") or "—"
        _phone = _format_phone_display(_lead.get("phone") or "")
        _source = LEAD_SOURCE_LABELS.get(_lead.get("lead_source") or "", _lead.get("lead_source") or "—")
        _stage = _lead.get("lead_stage") or "1_신규"
        _stage_label = LEAD_STAGES.get(_stage, _stage)
        _bg = LEAD_STAGE_BADGE.get(_stage, "#e5e7eb")
        _fg = LEAD_STAGE_TEXT.get(_stage, "#374151")
        _ctype = _lead.get("customer_type") or "신규잠재고객"

        # 담당직원: 단일
        _primary_emp_id = _lead.get("assigned_employee_id")
        _emp_name_single = emp_map.get(int(_primary_emp_id), "") if _primary_emp_id else ""

        _store = _lead.get("store_name") or ""
        _last_contact = (
            str(_lead.get("last_contact_at") or "")[:10]
            or str(_lead.get("created_at") or "")[:10]
        )

        _rc = st.columns(_ws)
        _rc[0].write(_name)
        _rc[1].write(_phone)
        # 고객 유형 뱃지
        _type_badge_map = {
            "신규잠재고객":      ("🆕 신규",    "#e0e7ff", "#3730a3"),
            "기존구매고객_DB외": ("🔄 기존구매", "#fef3c7", "#92400e"),
            "AS요청":            ("🔧 AS",      "#fee2e2", "#991b1b"),
            "재상담":            ("💬 재상담",  "#d1fae5", "#065f46"),
        }
        _tlabel, _tbg, _tfg = _type_badge_map.get(_ctype, ("🆕 신규", "#e2e8f0", "#475569"))
        _rc[2].markdown(
            f"<span style='background:{_tbg};color:{_tfg};padding:3px 8px;"
            f"border-radius:10px;font-size:0.72rem;font-weight:600;'>{_tlabel}</span>",
            unsafe_allow_html=True,
        )
        _rc[3].write(_source)
        _badge_html = (
            f"<span style='background:{_bg};color:{_fg};"
            f"padding:3px 10px;border-radius:12px;font-size:0.78rem;font-weight:600;'>"
            f"{_stage_label}</span>"
        )
        if _lead.get("_is_customer"):
            _badge_html += (
                " <span style='background:#fef3c7;color:#92400e;padding:3px 8px;"
                "border-radius:12px;font-size:0.72rem;font-weight:600;margin-left:4px;'>"
                "🛒 구매완료</span>"
            )
        _rc[4].markdown(_badge_html, unsafe_allow_html=True)

        # 담당직원 — 클릭 시 해당 직원 필터
        with _rc[5]:
            if _emp_name_single:
                if st.button(
                    f"👤 {_emp_name_single}", key=f"emp_filter_{_lid}_{_primary_emp_id}_{_i}",
                    help="클릭하면 이 담당자의 리드만 표시",
                ):
                    st.session_state["lead_filter_emp_id"] = _primary_emp_id
                    st.rerun()
            else:
                st.markdown(
                    f"<span style='color:#94a3b8;font-size:0.82rem;'>—</span>"
                    f"<br><span style='color:#cbd5e1;font-size:0.72rem;'>{_store}</span>",
                    unsafe_allow_html=True,
                )

        _rc[6].write(_last_contact)
        with _rc[7]:
            _is_sel = st.session_state.get("lead_selected_id") == _lid
            if st.button("닫기" if _is_sel else "관리", key=f"lead_act_{_lid}_{_i}", width="stretch"):
                if _is_sel:
                    st.session_state.pop("lead_selected_id", None)
                else:
                    st.session_state["lead_selected_id"] = _lid
                    st.session_state["lead_show_form"] = False
                    st.session_state["lead_show_import"] = False
                    st.rerun()

    # ── 선택된 리드 상세 패널 ───────────────────
    _sel_id = st.session_state.get("lead_selected_id")
    if _sel_id:
        _sel = next((l for l in leads_raw if l.get("id") == _sel_id), None)
        if _sel:
            st.divider()
            _render_lead_detail_panel(_sel, emp_map)


# ──────────────────────────────────────────────
# 채널톡 가져오기 패널
# ──────────────────────────────────────────────

def _render_import_panel() -> None:
    with st.container(border=True):
        st.markdown("#### 📥 채널톡 유입 고객 가져오기")
        st.caption("채널톡 최근 사용자 중, 기존 고객/리드에 없는 사람만 신규 리드로 등록합니다.")
        _c1, _c2, _c3 = st.columns([2, 2, 1])
        with _c1:
            _limit = st.number_input("최근 N명 조회", min_value=10, max_value=100, value=50, step=10, key="ct_import_limit")
        with _c2:
            st.write("")
            st.write("")
            _go = st.button("🔄 가져오기 실행", type="primary", key="ct_import_run", width="stretch")
        with _c3:
            st.write("")
            st.write("")
            if st.button("닫기", key="ct_import_close", width="stretch"):
                st.session_state["lead_show_import"] = False
                st.rerun()

        if _go:
            with st.spinner("채널톡 사용자 조회 중..."):
                _res = import_leads_from_channeltalk(limit=int(_limit))
            if not _res.get("ok"):
                st.error(f"❌ {_res.get('error')}")
            else:
                st.success(
                    f"✅ 채널톡에서 {_res.get('total_seen')}명 조회 → "
                    f"**신규 등록 {_res.get('imported')}명**, 제외 {_res.get('skipped')}명"
                )
                _dl = _res.get("details", [])
                if _dl:
                    with st.expander("상세 결과", expanded=False):
                        st.dataframe(pd.DataFrame(_dl), width="stretch", hide_index=True)


# ──────────────────────────────────────────────
# 리드 등록 폼
# ──────────────────────────────────────────────

def _render_register_form() -> None:
    user = st.session_state.get("current_user") or {}
    default_store = user.get("store_name") or st.session_state.get("current_store_name") or ""

    # 담당 직원 드롭다운 옵션 (app_users 전체)
    emp_map = _get_employee_map()
    emp_options_sorted = sorted(emp_map.items(), key=lambda x: x[1])
    emp_id_list = [None] + [eid for eid, _ in emp_options_sorted]
    emp_labels = ["(미배정)"] + [name for _, name in emp_options_sorted]

    # 현재 로그인 직원이 직원 목록에 있으면 기본 선택
    _default_emp_idx = 0
    _cur_uid = user.get("id")
    if _cur_uid is not None:
        try:
            _default_emp_idx = emp_id_list.index(int(_cur_uid))
        except (ValueError, TypeError):
            _default_emp_idx = 0

    st.markdown("#### ＋ 새 리드 등록")
    with st.form("lead_register_form_v2", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            lead_source = st.radio(
                "유입 경로",
                ["전화_문의", "오프라인_방문", "온라인_채널톡", "기타"],
                horizontal=True,
            )
        with c2:
            send_now = st.toggle("즉시 첫 메시지 발송", value=True)

        cc1, cc2 = st.columns(2)
        with cc1:
            phone_in = st.text_input("전화번호 *", placeholder="010-0000-0000")
        with cc2:
            name_in = st.text_input("고객 이름")
        cc1b, cc2b = st.columns(2)
        with cc1b:
            emp_label_sel = st.selectbox(
                "담당 직원",
                emp_labels,
                index=_default_emp_idx,
                help="이 리드를 책임지는 직원을 선택합니다.",
            )
        with cc2b:
            store_in = st.text_input("담당 매장", value=default_store, placeholder="예: 울산삼산점")
        memo_in = st.text_area("상담 메모", height=80, placeholder="예: 토레도 소파 4인용 가격 문의")
        cc3, cc4 = st.columns(2)
        with cc3:
            next_in = st.date_input("다음 연락 예정일", value=date.today() + timedelta(days=3))
        with cc4:
            image_in = st.text_input("MMS 첨부 이미지 URL (선택)", placeholder="https://...")

        c5, c6 = st.columns([3, 1])
        with c5:
            submitted = st.form_submit_button("등록", type="primary", width="stretch")
        with c6:
            cancel = st.form_submit_button("취소", width="stretch")

    if cancel:
        st.session_state["lead_show_form"] = False
        st.rerun()
    if submitted:
        phone_clean = _normalize_phone(phone_in)
        if not phone_clean or len(phone_clean) < 10:
            st.error("전화번호를 올바르게 입력해 주세요.")
            return
        selected_employee_id = emp_id_list[emp_labels.index(emp_label_sel)]
        try:
            from lead_manager import register_lead
            result = register_lead(
                phone=phone_clean,
                name=name_in or "",
                memo=memo_in or "",
                lead_source=lead_source,
                store_name=store_in or "전체",
                employee_id=selected_employee_id,
                next_contact_date=str(next_in),
                send_now=send_now,
                image_url=image_in or "",
            )
        except Exception as e:
            st.error(f"오류: {e}")
            return

        if result.get("ok"):
            _sr = result.get("send_result", {}) or {}
            _label = {
                "sent": "✅ 발송 완료", "lms_fallback": "LMS 발송 완료",
                "skipped": f"발송 보류 ({_sr.get('error')})",
                "failed": f"발송 실패 ({_sr.get('error')})",
                "not_friend": "미친구(SMS 폴백)", "out_of_hours": "야간 발송 거부",
            }.get(_sr.get("status", ""), "즉시발송 OFF")
            st.success(f"✅ 등록 완료 (ID: {result['lead_id']}) | {_label}")

            # 발신 로그 기록
            if _sr.get("status") in ("sent", "lms_fallback"):
                _log_customer_message(
                    phone=phone_clean,
                    channel="friendtalk" if _sr.get("status") == "sent" else "sms",
                    body=memo_in or "(자동 첫 메시지)",
                    status=_sr.get("status", ""),
                    sent_by=str(user.get("username") or ""),
                    solapi_msg_id=str(_sr.get("msg_id") or ""),
                    store_name=store_in,
                )
            st.session_state["lead_show_form"] = False
            st.rerun()
        elif result.get("error") == "duplicate_phone":
            ex = result.get("existing", {})
            st.warning(
                f"⚠️ 이미 등록된 번호입니다 — ID: {result.get('lead_id')} | "
                f"성함: {ex.get('name', '—')} | 단계: {LEAD_STAGES.get(ex.get('lead_stage',''), '—')}"
            )
        else:
            st.error(f"등록 실패: {result.get('error')}")


# ──────────────────────────────────────────────
# 선택된 리드 상세 패널
# ──────────────────────────────────────────────

CUSTOMER_TYPE_LABELS: dict[str, str] = {
    "신규잠재고객": "🆕 신규 잠재고객 (구매 이력 없음)",
    "기존구매고객_DB외": "🔄 기존 구매 고객 (DB 외, 2026-04 이전)",
    "AS요청": "🔧 AS 요청 고객",
    "재상담": "💬 재상담 (기존 구매 후 신규 상담)",
}


def _render_lead_detail_panel(lead: dict, emp_map: dict[int, str]) -> None:
    _lid = lead.get("id")
    _name = lead.get("name") or "리드"
    _phone = lead.get("phone") or ""
    _stage = lead.get("lead_stage") or "1_신규"
    _bg = LEAD_STAGE_BADGE.get(_stage, "#e5e7eb")
    _fg = LEAD_STAGE_TEXT.get(_stage, "#374151")
    _store = lead.get("store_name") or "—"
    _emp = emp_map.get(int(lead.get("assigned_employee_id") or 0), "—")
    _created = str(lead.get("created_at") or "")[:10]
    _ctype = lead.get("customer_type") or "신규잠재고객"
    _last_contact = str(lead.get("last_contact_at") or "")[:16].replace("T", " ") or "—"

    _header = (
        f"### 📋 {_name} "
        f"<span style='background:{_bg};color:{_fg};padding:4px 12px;border-radius:14px;"
        f"font-size:0.82rem;font-weight:600;vertical-align:middle;'>{LEAD_STAGES.get(_stage, _stage)}</span>"
        f" <span style='background:#eef2ff;color:#3730a3;padding:4px 10px;border-radius:14px;"
        f"font-size:0.75rem;font-weight:600;vertical-align:middle;margin-left:6px;'>"
        f"{CUSTOMER_TYPE_LABELS.get(_ctype, _ctype)}</span>"
    )
    if lead.get("_is_customer"):
        _header += (
            " <span style='background:#fef3c7;color:#92400e;padding:4px 10px;border-radius:14px;"
            "font-size:0.78rem;font-weight:600;vertical-align:middle;margin-left:6px;'>"
            "🛒 우리 DB에 등록된 구매 고객</span>"
        )
    st.markdown(_header, unsafe_allow_html=True)
    _info_c1, _info_c2, _info_c3, _info_c4 = st.columns(4)
    _info_c1.markdown(f"**📞 연락처**  \n{_format_phone_display(_phone)}")
    _info_c2.markdown(f"**🏬 유입 매장**  \n{_store}")
    _info_c3.markdown(f"**👤 담당직원**  \n{_emp}")
    _info_c4.markdown(f"**🕒 마지막 연락**  \n{_last_contact}")

    if lead.get("memo"):
        st.markdown(f"📝 **상담 메모:** {lead.get('memo')}")
    if lead.get("classification_memo"):
        _by = lead.get("classified_by") or "—"
        _at = str(lead.get("classified_at") or "")[:10]
        st.markdown(
            f"🏷️ **분류 메모** ({_by} / {_at}): {lead.get('classification_memo')}"
        )
    if lead.get("contact_memo"):
        st.markdown(f"💬 **최근 사후 메모:** {lead.get('contact_memo')}")
    if lead.get("converted_at"):
        st.success(
            f"🎉 계약 전환 완료 — 매출 ID {lead.get('converted_order_id') or '—'} | "
            f"금액 {int(lead.get('revenue_amount') or 0):,}원 | "
            f"전환일 {str(lead.get('converted_at'))[:10]}"
        )

    _tab_classify, _tab_hist, _tab_msg, _tab_stage = st.tabs(
        ["🏷️ 고객 유형 분류", "📜 상담 히스토리", "💬 메시지 발송", "🔄 단계 변경"]
    )

    with _tab_classify:
        _render_classify_tab(lead)

    with _tab_hist:
        _render_history_tab(_phone, _lid)

    with _tab_msg:
        _render_message_tab(lead)

    with _tab_stage:
        _render_stage_change_tab(lead, emp_map)


# ──────────────────────────────────────────────
# 고객 유형 분류 탭
# ──────────────────────────────────────────────

def _render_classify_tab(lead: dict) -> None:
    """app_customers에 없는(혹은 분류 미정인) 고객의 유형을 수동 분류."""
    _lid = lead.get("id")
    _cur_type = lead.get("customer_type") or "신규잠재고객"
    _cur_memo = lead.get("classification_memo") or ""
    user = st.session_state.get("current_user") or {}
    _user_name = str(user.get("name") or user.get("username") or "")

    st.caption(
        "고객이 momo DB에 없거나 분류가 필요한 경우, 유형을 수동 지정합니다. "
        "분류 시 자동으로 리드 단계가 `2_상담중`으로 전환됩니다."
    )

    _type_keys = list(CUSTOMER_TYPE_LABELS.keys())
    _type_options = [CUSTOMER_TYPE_LABELS[k] for k in _type_keys]
    _idx = _type_keys.index(_cur_type) if _cur_type in _type_keys else 0
    _sel_label = st.radio(
        "고객 유형",
        _type_options,
        index=_idx,
        key=f"classify_type_{_lid}",
    )
    _new_type = _type_keys[_type_options.index(_sel_label)]

    _memo = st.text_area(
        "분류 메모",
        value=_cur_memo,
        height=80,
        placeholder="예: 2024년 킹덤 소파 구매, 배송 AS 문의",
        key=f"classify_memo_{_lid}",
    )

    _auto_consult = st.toggle(
        "분류 시 리드 단계를 '2_상담중'으로 자동 변경",
        value=(_new_type in ("기존구매고객_DB외", "AS요청", "재상담")),
        key=f"classify_auto_stage_{_lid}",
        help="기존구매·AS·재상담 유형은 보통 상담중 상태로 시작합니다.",
    )

    if st.button("🏷️ 분류 저장", type="primary", key=f"classify_save_{_lid}", width="stretch"):
        supa = _supa()
        if not supa:
            st.error("Supabase 연결 실패")
            return
        _upd: dict = {
            "customer_type": _new_type,
            "classification_memo": _memo.strip() or None,
            "classified_by": _user_name or None,
            "classified_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if _auto_consult:
            _upd["lead_stage"] = "2_상담중"
        try:
            supa.table("app_leads").update(_upd).eq("id", _lid).execute()
            st.success(f"✅ 유형 분류 완료: {CUSTOMER_TYPE_LABELS[_new_type]}")
            st.session_state.pop("lead_selected_id", None)
            st.rerun()
        except Exception as e:
            _emsg = str(e)
            if "customer_type" in _emsg or "42703" in _emsg:
                st.error(
                    "❌ DB 마이그레이션 미실행 — Supabase Dashboard › SQL Editor에서 "
                    "`SUPABASE_APP_LEADS_CUSTOMER_TYPE.sql` 파일을 실행하세요."
                )
            else:
                st.error(f"분류 저장 실패: {e}")


# ──────────────────────────────────────────────
# 상담 히스토리 탭
# ──────────────────────────────────────────────

def _render_history_tab(phone: str, lead_id: int) -> None:
    items = _get_unified_history(phone, lead_id)
    if not items:
        st.info("아직 상담/메시지 기록이 없습니다.")
        return
    st.caption(f"전화번호 `{_format_phone_display(phone)}` 의 모든 채널 이력 (최신순)")
    for it in items:
        _dir = it.get("direction", "")
        _ch = it.get("channel", "")
        _by = it.get("by", "")
        _at = str(it.get("at", ""))[:16].replace("T", " ")
        _body = it.get("body", "") or "(내용 없음)"
        _src = it.get("source", "")

        if _dir == "수신":
            _icon = "📥"
            _color = "#dbeafe"
            _border = "#3b82f6"
        else:
            _icon = "📤"
            _color = "#f0fdf4"
            _border = "#10b981"

        st.markdown(
            f"<div style='background:{_color};border-left:3px solid {_border};"
            f"padding:8px 12px;margin:6px 0;border-radius:4px;'>"
            f"<div style='font-size:0.78rem;color:#64748b;margin-bottom:4px;'>"
            f"{_icon} <b>{_dir} · {_ch}</b> · {_at} · {_by} · <i>{_src}</i></div>"
            f"<div style='white-space:pre-wrap;'>{_body}</div></div>",
            unsafe_allow_html=True,
        )


# ──────────────────────────────────────────────
# 메시지 발송 탭
# ──────────────────────────────────────────────

def _render_message_tab(lead: dict) -> None:
    user = st.session_state.get("current_user") or {}
    _phone = lead.get("phone") or ""
    _name = lead.get("name") or "고객"
    _store = lead.get("store_name") or "에몬스"
    _lid = lead.get("id")

    try:
        from solapi_sender import check_solapi_config, send_friendtalk, send_sms
    except ImportError:
        st.error("solapi_sender 모듈 로드 실패")
        return

    _cfg = check_solapi_config()
    if not _cfg.get("all_ok"):
        st.error(
            f"⚠️ Solapi 키 미설정 — api_key:{'✅' if _cfg['api_key'] else '❌'} / "
            f"api_secret:{'✅' if _cfg['api_secret'] else '❌'} / "
            f"pf_id:{'✅' if _cfg['pf_id'] else '❌'}"
        )
    else:
        st.caption(f"✅ Solapi 연결됨 ({_cfg['source']})")

    _ch = st.radio("발송 채널", ["친구톡 (카카오)", "SMS"], horizontal=True, key=f"msg_ch_{_lid}")
    _default = (
        f"안녕하세요 {_name}님, 에몬스 {_store}입니다.\n"
        "문의하신 내용 관련하여 연락드립니다.\n"
        "편하신 시간에 답변 주시면 감사하겠습니다."
    )
    _body = st.text_area("메시지 내용", value=_default, height=140, key=f"msg_body_{_lid}")

    if st.button("📤 발송", type="primary", key=f"msg_send_{_lid}", width="stretch"):
        if not _phone:
            st.error("전화번호 없음")
            return
        with st.spinner("발송 중..."):
            if _ch.startswith("친구톡"):
                _res = send_friendtalk(_phone, _body)
                _channel_label = "friendtalk"
            else:
                _res = send_sms(_phone, _body)
                _channel_label = "sms"
        _st = _res.get("status", "")
        _err = _res.get("error", "")

        _log_customer_message(
            phone=_phone,
            channel=_channel_label,
            body=_body,
            status=_st,
            sent_by=str(user.get("username") or ""),
            solapi_msg_id=str(_res.get("msg_id") or ""),
            error=_err,
            store_name=_store,
        )

        if _st == "sent":
            st.success("✅ 발송 완료!")
        elif _st == "lms_fallback":
            st.info("📩 LMS로 대체 발송 완료")
        elif _st == "not_friend":
            st.warning("👥 친구가 아닌 고객입니다. SMS로 재시도해 주세요.")
        elif _st == "skipped":
            st.warning(f"발송 보류: {_err}")
        else:
            st.error(f"발송 실패: {_err}")

        with st.expander("응답 상세"):
            st.json(_res.get("raw") or {"status": _st, "error": _err})


# ──────────────────────────────────────────────
# 단계 변경 탭
# ──────────────────────────────────────────────

def _render_stage_change_tab(lead: dict, emp_map: dict[int, str]) -> None:
    _lid = lead.get("id")
    _cur = lead.get("lead_stage") or "1_신규"
    _idx = list(LEAD_STAGES.keys()).index(_cur) if _cur in LEAD_STAGES else 0

    _new = st.selectbox(
        "새 단계",
        list(LEAD_STAGES.keys()),
        format_func=lambda x: LEAD_STAGES[x],
        index=_idx,
        key=f"stage_sel_{_lid}",
    )
    _nc = st.date_input("다음 연락 예정일", value=None, key=f"stage_nc_{_lid}")
    _memo = st.text_area("사후 메모", height=70, key=f"stage_memo_{_lid}")

    st.divider()
    st.markdown("**👤 담당 직원 편집**")

    # 담당 직원 단일 드롭다운 (app_users 기반)
    emp_options_sorted = sorted(emp_map.items(), key=lambda x: x[1])
    _emp_id_list = [None] + [eid for eid, _ in emp_options_sorted]
    _emp_labels = ["(미배정)"] + [name for _, name in emp_options_sorted]

    _cur_emp_id = lead.get("assigned_employee_id")
    try:
        _default_idx = _emp_id_list.index(int(_cur_emp_id)) if _cur_emp_id else 0
    except (ValueError, TypeError):
        _default_idx = 0

    _sel_label = st.selectbox(
        "담당 직원",
        _emp_labels,
        index=_default_idx,
        key=f"stage_emp_{_lid}",
        help="이 리드를 책임지는 직원 1명을 선택합니다.",
    )
    _new_emp_id = _emp_id_list[_emp_labels.index(_sel_label)]

    if st.button("업데이트 저장", type="primary", key=f"stage_btn_{_lid}", width="stretch"):
        supa = _supa()
        if not supa:
            st.error("Supabase 연결 실패")
            return

        _upd: dict = {
            "lead_stage": _new,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "assigned_employee_id": _new_emp_id,
        }
        if _memo:
            _upd["contact_memo"] = _memo
            _upd["followup_done"] = True
        if _nc:
            _upd["next_contact_date"] = str(_nc)
        try:
            supa.table("app_leads").update(_upd).eq("id", _lid).execute()
            st.success(f"✅ {LEAD_STAGES[_new]} 단계 + 담당직원 업데이트 완료")
            st.session_state.pop("lead_selected_id", None)
            st.rerun()
        except Exception as e:
            st.error(f"업데이트 실패: {e}")
