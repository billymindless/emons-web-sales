# -*- coding: utf-8 -*-
"""
call_records_ui.py — 리드 관리 > 통화 상담 서브탭.

계획서: docs/plans/채널톡_콜_상담일지_c5e969b5.plan.md
스키마: SUPABASE_APP_CALL_RECORDS.sql (app_call_records)

책임:
  - 매장·기간 필터 목록 (superadmin 은 전체, store_admin 은 자기 매장 고정)
  - 통화 상세 팝업: 녹음 재생, 전사 원문, Gemini 요약, 액션아이템, 매칭 리드 링크
  - superadmin: 매장 ↔ 채널톡 팀 매핑 편집 (app_call_channel_teams)
  - 관리자 softDelete (deleted_at 세팅)

인제스트·요약은 [channel_call_service.py](channel_call_service.py) 가 담당하고, 이 파일은 조회/표시 전용.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pandas as pd
import streamlit as st


# ──────────────────────────────────────────────
# Supabase (lead_management._supa 와 동일 규약)
# ──────────────────────────────────────────────

def _supa() -> Any:
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


def _fmt_phone(phone: str) -> str:
    d = re.sub(r"\D", "", phone or "")
    if len(d) == 11:
        return f"{d[:3]}-{d[3:7]}-{d[7:]}"
    if len(d) == 10:
        return f"{d[:3]}-{d[3:6]}-{d[6:]}"
    return phone or ""


def _fmt_duration(seconds: int | None) -> str:
    try:
        s = int(seconds or 0)
    except Exception:
        s = 0
    if s <= 0:
        return "-"
    m, sec = divmod(s, 60)
    return f"{m}분 {sec}초" if m else f"{sec}초"


# ──────────────────────────────────────────────
# 데이터 조회 (60초 캐시)
# ──────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def _fetch_call_records(
    *,
    db_filename: str | None,
    start: str,
    end: str,
    category: str | None = None,
    matched_only: bool = False,
) -> list[dict]:
    """
    기간·매장 필터로 app_call_records 조회 (deleted_at IS NULL).
    db_filename=None 이면 전체 매장 (superadmin 전용 호출).
    """
    sc = _supa()
    if not sc:
        return []
    try:
        q = sc.table("app_call_records").select(
            "id,db_filename,started_at,ended_at,duration_seconds,direction,"
            "from_number,to_number,caller_name,manager_name,manager_email,"
            "matched_lead_id,category,summary,recording_url,recording_expires_at,"
            "transcript,action_items,estimated_amount,deleted_at"
        )
        q = q.is_("deleted_at", "null")
        q = q.gte("started_at", f"{start}T00:00:00+09:00")
        q = q.lte("started_at", f"{end}T23:59:59+09:00")
        if db_filename:
            q = q.eq("db_filename", db_filename)
        if category:
            q = q.eq("category", category)
        if matched_only:
            q = q.not_.is_("matched_lead_id", "null")
        r = q.order("started_at", desc=True).limit(500).execute()
        return list(r.data or [])
    except Exception as e:
        st.caption(f"⚠️ 통화 목록 조회 실패: {e}")
        return []


@st.cache_data(ttl=300, show_spinner=False)
def _fetch_store_map() -> dict[str, str]:
    """db_filename → store_name 매핑."""
    sc = _supa()
    if not sc:
        return {}
    try:
        r = sc.table("app_stores").select("store_name,db_filename").execute()
        return {row["db_filename"]: row["store_name"] for row in (r.data or []) if row.get("db_filename")}
    except Exception:
        return {}


@st.cache_data(ttl=60, show_spinner=False)
def _fetch_lead_names(lead_ids: tuple[int, ...]) -> dict[int, dict]:
    """리드 id → {name, phone, lead_stage} 매핑."""
    if not lead_ids:
        return {}
    sc = _supa()
    if not sc:
        return {}
    try:
        r = sc.table("app_leads").select("id,name,phone,lead_stage,store_name") \
              .in_("id", list(lead_ids)).execute()
        return {int(x["id"]): x for x in (r.data or [])}
    except Exception:
        return {}


def _clear_caches() -> None:
    try:
        _fetch_call_records.clear()
    except Exception:
        pass
    try:
        _fetch_lead_names.clear()
    except Exception:
        pass


# ──────────────────────────────────────────────
# 세부 팝업
# ──────────────────────────────────────────────

def _render_call_detail(call: dict, lead: dict | None, role: str) -> None:
    _phone = _fmt_phone(call.get("from_number") or "")
    _dt = str(call.get("started_at") or "")[:19].replace("T", " ")
    _mgr = call.get("manager_name") or call.get("manager_email") or "-"
    _dir_label = {"inbound": "수신", "outbound": "발신", "missed": "부재중"}.get(
        (call.get("direction") or "").lower(), "-"
    )
    st.markdown(f"**{_dt}** · {_dir_label} · {_phone} · 상담원 **{_mgr}**")

    _dur = _fmt_duration(call.get("duration_seconds"))
    _cat = call.get("category") or "-"
    _est = call.get("estimated_amount")
    _est_s = f"{int(_est):,}원" if isinstance(_est, (int, float)) and _est else "-"
    _c1, _c2, _c3 = st.columns(3)
    with _c1:
        st.metric("통화 시간", _dur)
    with _c2:
        st.metric("문의 유형", _cat)
    with _c3:
        st.metric("추정 견적", _est_s)

    # 녹음
    rec = (call.get("recording_url") or "").strip()
    if rec:
        exp = call.get("recording_expires_at")
        if exp:
            try:
                exp_dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
                if exp_dt < datetime.now(timezone.utc):
                    st.warning("⚠️ 녹음 링크가 만료되었을 수 있습니다.")
                elif exp_dt - datetime.now(timezone.utc) < timedelta(days=3):
                    st.caption(f"⏰ 녹음 링크는 {str(exp)[:19]} 까지 유효")
            except Exception:
                pass
        try:
            st.audio(rec)
        except Exception:
            st.markdown(f"[🎧 녹음 파일 열기]({rec})")
    else:
        st.caption("녹음 파일 없음")

    # 요약
    st.markdown("##### 🧠 AI 요약")
    _summary = call.get("summary") or "(요약 없음)"
    st.write(_summary)

    _actions = call.get("action_items") or []
    if isinstance(_actions, str):
        try:
            _actions = json.loads(_actions)
        except Exception:
            _actions = []
    if _actions:
        st.markdown("##### ✅ 액션 아이템")
        for i, item in enumerate(_actions):
            st.checkbox(str(item), key=f"call_action_{call['id']}_{i}", disabled=True)

    # 관련 리드
    st.markdown("##### 👤 관련 리드")
    if lead:
        _lead_line = (
            f"**{lead.get('name') or '(이름 없음)'}** · "
            f"{_fmt_phone(lead.get('phone') or '')} · "
            f"단계 `{lead.get('lead_stage') or '-'}` · "
            f"매장 `{lead.get('store_name') or '-'}`"
        )
        st.markdown(_lead_line)
        if st.button("리드 상세로 이동", key=f"call_goto_lead_{call['id']}"):
            st.session_state["lead_selected_id"] = int(lead["id"])
            st.session_state["lead_records_view"] = "리드 목록"
            st.rerun()
    else:
        st.caption("매칭된 리드가 없습니다. (이 통화의 발신번호로 새 리드가 자동 생성됩니다)")

    # 전사 원문
    with st.expander("📝 전사 원문 보기", expanded=False):
        st.text(call.get("transcript") or "(전사 없음)")

    # 관리자 softDelete
    if role in ("superadmin", "store_admin"):
        st.divider()
        _confirm_key = f"call_del_confirm_{call['id']}"
        if st.session_state.get(_confirm_key):
            _cc1, _cc2 = st.columns(2)
            with _cc1:
                if st.button("정말 삭제", type="primary", key=f"call_del_yes_{call['id']}", width="stretch"):
                    if _soft_delete_call(int(call["id"])):
                        _clear_caches()
                        st.session_state.pop("call_selected_id", None)
                        st.session_state[_confirm_key] = False
                        st.success("삭제되었습니다.")
                        st.rerun()
                    else:
                        st.error("삭제 실패")
            with _cc2:
                if st.button("취소", key=f"call_del_no_{call['id']}", width="stretch"):
                    st.session_state[_confirm_key] = False
                    st.rerun()
        else:
            if st.button("🗑️ 이 통화 기록 삭제", key=f"call_del_{call['id']}"):
                st.session_state[_confirm_key] = True
                st.rerun()


def _soft_delete_call(call_id: int) -> bool:
    sc = _supa()
    if not sc:
        return False
    try:
        sc.table("app_call_records").update(
            {"deleted_at": datetime.now(timezone.utc).isoformat()}
        ).eq("id", call_id).execute()
        return True
    except Exception:
        return False


# ──────────────────────────────────────────────
# 메인 렌더
# ──────────────────────────────────────────────

def render_call_records_tab(current_db: str | None, role: str, me_name: str) -> None:
    """리드 관리 > 통화 상담 탭 진입점."""
    st.markdown("#### 📞 통화 상담")
    st.caption(
        "채널톡 콜로 걸려온 고객 통화의 녹음·전사·요약을 여기에서 확인합니다. "
        "발신번호는 자동으로 리드에 연결됩니다."
    )

    # superadmin: 매장 ↔ 채널톡 팀 매핑 편집 (같은 화면 안에 접힘)
    if role == "superadmin":
        with st.expander("🔗 매장 ↔ 채널톡 팀 매핑 관리 (superadmin)", expanded=False):
            render_channel_teams_admin()

    stores_map = _fetch_store_map()

    # 상단 필터
    _c1, _c2, _c3, _c4 = st.columns([1.4, 1.4, 1.4, 1.0])
    with _c1:
        default_start = date.today() - timedelta(days=6)
        _start = st.date_input("시작일", value=default_start, key="call_flt_start")
    with _c2:
        _end = st.date_input("종료일", value=date.today(), key="call_flt_end")
    with _c3:
        if role == "superadmin":
            _opts = ["(전체 매장)"] + sorted(stores_map.values())
            _pick = st.selectbox("매장", _opts, key="call_flt_store")
            _db_selected = None if _pick == "(전체 매장)" else next(
                (k for k, v in stores_map.items() if v == _pick), None
            )
        else:
            _db_selected = current_db
            _store_disp = stores_map.get(current_db or "", current_db or "-")
            st.text_input("매장", value=_store_disp, disabled=True, key="call_flt_store_ro")
    with _c4:
        _matched_only = st.toggle("리드 매칭만", value=False, key="call_flt_matched")

    _c5, _c6 = st.columns([1.4, 4])
    with _c5:
        _cat = st.selectbox(
            "문의 유형",
            ["전체", "제품문의", "가격문의", "배송문의", "AS", "견적요청", "방문예약", "기타"],
            key="call_flt_cat",
        )
    with _c6:
        st.caption(" ")  # 여백

    _cat_val = None if _cat == "전체" else _cat

    if _end < _start:
        st.error("종료일이 시작일보다 빠를 수 없습니다.")
        return

    rows = _fetch_call_records(
        db_filename=_db_selected,
        start=_start.isoformat(),
        end=_end.isoformat(),
        category=_cat_val,
        matched_only=_matched_only,
    )

    # 상단 요약 카드
    total = len(rows)
    inbound = sum(1 for r in rows if (r.get("direction") or "").lower() == "inbound")
    missed = sum(1 for r in rows if (r.get("direction") or "").lower() == "missed")
    matched = sum(1 for r in rows if r.get("matched_lead_id"))
    est_sum = 0.0
    for r in rows:
        v = r.get("estimated_amount")
        try:
            est_sum += float(v or 0)
        except Exception:
            pass
    _m1, _m2, _m3, _m4, _m5 = st.columns(5)
    with _m1:
        st.metric("총 통화", f"{total}건")
    with _m2:
        st.metric("수신", f"{inbound}건")
    with _m3:
        st.metric("부재중", f"{missed}건")
    with _m4:
        st.metric("리드 매칭", f"{matched}/{total}")
    with _m5:
        st.metric("추정 견적 합", f"{int(est_sum):,}원" if est_sum else "-")

    st.divider()

    if not rows:
        st.info("이 조건에 해당하는 통화 기록이 없습니다.")
    else:
        _render_call_list(rows, stores_map, role)


def _render_call_list(rows: list[dict], stores_map: dict[str, str], role: str) -> None:
    lead_ids = tuple(sorted({int(r["matched_lead_id"]) for r in rows if r.get("matched_lead_id")}))
    lead_map = _fetch_lead_names(lead_ids)

    # 선택된 통화 상세: 다이얼로그
    _sel_id = st.session_state.get("call_selected_id")
    if _sel_id:
        _sel = next((r for r in rows if int(r["id"]) == int(_sel_id)), None)
        if _sel:
            _lead = lead_map.get(int(_sel["matched_lead_id"])) if _sel.get("matched_lead_id") else None
            try:
                from ui_dialogs import open_dialog as _open_dialog  # noqa: WPS433
                _open_dialog(
                    f"통화 상세 · {_fmt_phone(_sel.get('from_number') or '')}",
                    lambda c=_sel, l=_lead, r=role: _render_call_detail(c, l, r),
                    width="large",
                    on_dismiss=lambda: st.session_state.pop("call_selected_id", None),
                )
            except Exception:
                # 폴백: 인라인 렌더
                with st.container(border=True):
                    _render_call_detail(_sel, _lead, role)

    # 목록 표 (버튼으로 상세)
    _hdr = st.columns([1.4, 1.2, 1.4, 0.9, 0.9, 1.4, 2.6, 0.7])
    for i, label in enumerate(["시간", "매장", "발신번호", "방향", "통화시간", "매칭 리드", "요약", ""]):
        with _hdr[i]:
            st.markdown(f"**{label}**")
    st.markdown("<hr style='margin:2px 0;'/>", unsafe_allow_html=True)

    for row in rows:
        _dt = str(row.get("started_at") or "")[:16].replace("T", " ")
        _store = stores_map.get(row.get("db_filename") or "", row.get("db_filename") or "미매핑")
        _from = _fmt_phone(row.get("from_number") or "-")
        _dir = {"inbound": "📥 수신", "outbound": "📤 발신", "missed": "❌ 부재"}.get(
            (row.get("direction") or "").lower(), "-"
        )
        _dur = _fmt_duration(row.get("duration_seconds"))
        _lead = lead_map.get(int(row["matched_lead_id"])) if row.get("matched_lead_id") else None
        _lead_lbl = (
            f"👤 {_lead.get('name') or _fmt_phone(_lead.get('phone') or '')}"
            if _lead else "-"
        )
        _sum = (row.get("summary") or "").split("\n")[0][:80]

        _c = st.columns([1.4, 1.2, 1.4, 0.9, 0.9, 1.4, 2.6, 0.7])
        with _c[0]:
            st.write(_dt)
        with _c[1]:
            st.write(_store)
        with _c[2]:
            st.write(_from)
        with _c[3]:
            st.write(_dir)
        with _c[4]:
            st.write(_dur)
        with _c[5]:
            st.write(_lead_lbl)
        with _c[6]:
            st.write(_sum or "(요약 없음)")
        with _c[7]:
            if st.button("상세", key=f"call_open_{row['id']}"):
                st.session_state["call_selected_id"] = int(row["id"])
                st.rerun()


# ──────────────────────────────────────────────
# superadmin: 매장 ↔ 채널톡 팀 매핑 편집
# ──────────────────────────────────────────────

def render_channel_teams_admin() -> None:
    """superadmin 관리자 메뉴에서 호출. 매장별 채널톡 팀 ID/번호를 편집."""
    st.markdown("#### 🔗 매장 ↔ 채널톡 팀 매핑")
    st.caption(
        "채널톡 콜을 받은 팀 ID 를 매장(db_filename) 에 연결합니다. "
        "매핑이 없으면 통화가 `db_filename=NULL` 로 저장되어 매장 필터에 잡히지 않습니다."
    )
    sc = _supa()
    if not sc:
        st.error("Supabase 클라이언트를 사용할 수 없습니다.")
        return

    stores_map = _fetch_store_map()
    stores_reverse = {v: k for k, v in stores_map.items()}
    store_names = sorted(stores_map.values())

    try:
        r = sc.table("app_call_channel_teams").select("*").order("db_filename").execute()
        rows = list(r.data or [])
    except Exception as e:
        st.error(f"매핑 조회 실패: {e}")
        return

    # 기존 행 편집
    for row in rows:
        _rid = int(row["id"])
        _c1, _c2, _c3, _c4, _c5 = st.columns([1.4, 1.4, 1.4, 1.4, 0.6])
        with _c1:
            _cur_store = stores_map.get(row.get("db_filename") or "", "")
            _default_idx = store_names.index(_cur_store) if _cur_store in store_names else 0
            _new_store = st.selectbox(
                "매장", store_names, index=_default_idx, key=f"ct_map_store_{_rid}"
            )
        with _c2:
            _new_team = st.text_input("채널톡 팀 ID", value=row.get("channel_team_id") or "", key=f"ct_map_team_{_rid}")
        with _c3:
            _new_num = st.text_input("채널톡 번호", value=row.get("channel_phone_number") or "", key=f"ct_map_num_{_rid}")
        with _c4:
            _new_note = st.text_input("메모", value=row.get("note") or "", key=f"ct_map_note_{_rid}")
        with _c5:
            if st.button("저장", key=f"ct_map_save_{_rid}"):
                try:
                    sc.table("app_call_channel_teams").update({
                        "db_filename": stores_reverse.get(_new_store),
                        "channel_team_id": _new_team.strip() or None,
                        "channel_phone_number": _new_num.strip() or None,
                        "note": _new_note.strip() or None,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }).eq("id", _rid).execute()
                    st.success("저장됨")
                    st.rerun()
                except Exception as e:
                    st.error(f"저장 실패: {e}")
            if st.button("🗑", key=f"ct_map_del_{_rid}", help="이 매핑 삭제"):
                try:
                    sc.table("app_call_channel_teams").delete().eq("id", _rid).execute()
                    st.rerun()
                except Exception as e:
                    st.error(f"삭제 실패: {e}")

    st.divider()

    # 신규 행 추가
    st.markdown("**신규 매핑 추가**")
    with st.form("ct_map_new", clear_on_submit=True):
        _nc1, _nc2, _nc3, _nc4 = st.columns([1.4, 1.4, 1.4, 1.4])
        with _nc1:
            _n_store = st.selectbox("매장", store_names, key="ct_map_new_store")
        with _nc2:
            _n_team = st.text_input("채널톡 팀 ID", key="ct_map_new_team")
        with _nc3:
            _n_num = st.text_input("채널톡 번호", key="ct_map_new_num")
        with _nc4:
            _n_note = st.text_input("메모(선택)", key="ct_map_new_note")
        if st.form_submit_button("추가", type="primary"):
            if not _n_team.strip():
                st.error("채널톡 팀 ID 는 필수입니다.")
            else:
                try:
                    sc.table("app_call_channel_teams").insert({
                        "db_filename": stores_reverse.get(_n_store),
                        "channel_team_id": _n_team.strip(),
                        "channel_phone_number": _n_num.strip() or None,
                        "note": _n_note.strip() or None,
                    }).execute()
                    st.success("추가되었습니다.")
                    st.rerun()
                except Exception as e:
                    st.error(f"추가 실패: {e}")
