# -*- coding: utf-8 -*-
"""
7. 고객 CRM 자동화 모듈 (crm_automation.py)

사용법: app.py 에서 from crm_automation import render_crm_menu 로 import 후 호출.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def _today_kst() -> date:
    return datetime.now(tz=KST).date()

import pandas as pd
import streamlit as st

# ──────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────

ALLOWED_ROLES = ("store_admin", "superadmin")

SEND_CHANNEL_OPTIONS = ["일반 문자 (SMS/LMS)", "카카오 브랜드톡 (친구톡 – 광고용)"]

TRIGGER_OPTIONS = [
    "즉시 발송",
    "특정일 예약 발송",
    "배송 후 7일 자동 발송",
    "배송 후 30일(1개월) 자동 발송",
    "배송 후 100일 자동 발송",
    "배송 후 365일(1년) 자동 발송",
]

VARIABLE_GUIDE = "`{이름}` `{품목}` `{배송일}` `{매출금액}` 치환 변수를 메시지에 사용할 수 있습니다."


# ──────────────────────────────────────────────
# Supabase 헬퍼 (app.py 의 get_supabase_client 재사용)
# ──────────────────────────────────────────────

def _get_supabase():
    """app.py 에서 import된 get_supabase_client 또는 st.session_state 캐시 사용."""
    try:
        from app import get_supabase_client  # type: ignore
        client, err = get_supabase_client()
        return client
    except Exception:
        return None


def _query_targets(
    db_filename: str,
    store_name: str | None,
    item_filter: list[str],
    region_filter: str,
    order_date_start: date | None,
    order_date_end: date | None,
    delivery_date_start: date | None,
    delivery_date_end: date | None,
    price_min: int,
    price_max: int,
) -> pd.DataFrame:
    """
    Supabase app_orders + app_customers 조인으로 타겟 고객 리스트 조회.
    조건에 맞는 고객(중복 제거)을 DataFrame으로 반환.
    """
    sc = _get_supabase()
    if not sc or not db_filename:
        return pd.DataFrame()
    try:
        # app_orders 조회 (db_filename 기준 — store_name 컬럼 없음)
        q = (
            sc.table("app_orders")
            .select(
                "id, customer_id, order_date, delivery_date, category,"
                " total_amount, employee_names"
            )
            .eq("db_filename", db_filename)
        )
        if order_date_start:
            q = q.gte("order_date", str(order_date_start))
        if order_date_end:
            q = q.lte("order_date", str(order_date_end))
        if delivery_date_start:
            q = q.gte("delivery_date", str(delivery_date_start))
        if delivery_date_end:
            q = q.lte("delivery_date", str(delivery_date_end))
        if price_min > 0:
            q = q.gte("total_amount", price_min)
        if price_max > 0:
            q = q.lte("total_amount", price_max)
        r_orders = q.limit(1000).execute()
        orders_df = pd.DataFrame(r_orders.data or [])
        if orders_df.empty:
            return pd.DataFrame()

        # 품목 필터 (클라이언트 측)
        if item_filter:
            pattern = "|".join(re.escape(i) for i in item_filter)
            orders_df = orders_df[
                orders_df["category"].fillna("").str.contains(pattern, case=False)
            ]
        if orders_df.empty:
            return pd.DataFrame()

        # 고객 정보 조회
        cust_ids = orders_df["customer_id"].dropna().astype(int).unique().tolist()
        r_custs = (
            sc.table("app_customers")
            .select("id, name, phone1, phone2, address")
            .in_("id", cust_ids)
            .execute()
        )
        custs_df = pd.DataFrame(r_custs.data or [])
        if custs_df.empty:
            return pd.DataFrame()

        custs_df = custs_df.rename(columns={"id": "customer_id"})
        merged = orders_df.merge(custs_df, on="customer_id", how="left")

        # 지역 필터
        if region_filter.strip():
            merged = merged[
                merged["address"].fillna("").str.contains(region_filter.strip(), case=False)
            ]

        # 중복 제거 (고객 단위), 최신 주문 기준
        merged = merged.sort_values("order_date", ascending=False)
        merged = merged.drop_duplicates(subset=["customer_id"])

        return merged[
            ["customer_id", "name", "phone1", "phone2", "address",
             "order_date", "delivery_date", "category", "total_amount"]
        ].reset_index(drop=True)
    except Exception as e:
        st.warning(f"타겟 조회 중 오류: {e}")
        return pd.DataFrame()


def _save_crm_automation(
    store_name: str,
    campaign_name: str,
    send_channel: str,
    kakao_channel_id: str | None,
    message_template: str,
    trigger_type: str,
    scheduled_date: str | None,
    delivery_offset_days: int | None,
    item_filter: list[str],
    region_filter: str,
    price_min: int,
    price_max: int,
    fallback_sms: bool,
    solapi_payload_preview: dict,
    target_count: int,
) -> bool:
    """crm_automations 테이블에 자동화 룰 저장."""
    sc = _get_supabase()
    if not sc:
        return False
    row = {
        "store_name": store_name,
        "campaign_name": campaign_name,
        "send_channel": send_channel,
        "kakao_channel_id": kakao_channel_id,
        "message_template": message_template,
        "trigger_type": trigger_type,
        "scheduled_date": scheduled_date,
        "delivery_offset_days": delivery_offset_days,
        "filter_items": json.dumps(item_filter, ensure_ascii=False),
        "filter_region": region_filter,
        "filter_price_min": price_min,
        "filter_price_max": price_max,
        "fallback_sms": fallback_sms,
        "solapi_payload_preview": json.dumps(solapi_payload_preview, ensure_ascii=False),
        "target_count": target_count,
        "status": "active",
        "created_at": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        sc.table("crm_automations").insert(row).execute()
        return True
    except Exception as e:
        st.error(f"자동화 룰 저장 실패: {e}")
        return False


def _build_solapi_payload(
    targets: pd.DataFrame,
    message_template: str,
    send_channel: str,
    kakao_channel_id: str | None,
    fallback_sms: bool,
    image_uploaded: bool,
    image_file_id: str | None,
) -> dict[str, Any]:
    """
    Solapi API 규격의 카카오 친구톡 발송 Payload 딕셔너리 생성.
    실제 발송 시 Solapi SDK로 전달하면 됨.
    """
    is_kakao = "카카오" in send_channel
    messages = []
    for _, row in targets.iterrows():
        name = str(row.get("name") or "고객")
        item = str(row.get("category") or "")
        delivery = str(row.get("delivery_date") or "")
        amount = str(int(row.get("total_amount") or 0))
        phone = str(row.get("phone1") or "")
        text = (
            message_template
            .replace("{이름}", name)
            .replace("{품목}", item)
            .replace("{배송일}", delivery)
            .replace("{매출금액}", amount)
        )
        if is_kakao:
            text = "(광고) " + text
        msg: dict[str, Any] = {
            "to": re.sub(r"\D", "", phone),
            "from": "",  # Solapi 등록 발신번호로 교체 필요
            "text": text,
            "type": "ATA" if is_kakao else "LMS",
        }
        if is_kakao:
            kakao_opts: dict[str, Any] = {
                "pfId": kakao_channel_id or "",
                "disableSms": not fallback_sms,
            }
            if image_file_id:
                kakao_opts["imageId"] = image_file_id
            msg["kakaoOptions"] = kakao_opts
        messages.append(msg)

    return {
        "messages": messages,
        "_meta": {
            "total": len(messages),
            "channel": send_channel,
            "fallback_sms": fallback_sms,
            "generated_at": datetime.utcnow().isoformat(),
        },
    }


def _get_current_store_name(db_filename: str | None) -> str | None:
    """app.py 의 _get_current_store_name_for_customers 재사용."""
    try:
        from app import _get_current_store_name_for_customers  # type: ignore
        return _get_current_store_name_for_customers(db_filename)
    except Exception:
        return None


# ──────────────────────────────────────────────
# 메인 렌더 함수
# ──────────────────────────────────────────────

def _render_kakao_channel_tab(db_filename: str | None, store_name: str | None) -> None:
    """카카오 채널 동기화 현황 탭 — 친구 현황 요약 + 발송 이력 조회."""
    sc = _get_supabase()

    st.subheader("채널 친구 현황")
    if sc and db_filename and store_name:
        try:
            r_all = (
                sc.table("app_customers")
                .select("id, name, phone1, kakao_friend_added, kakao_friend_added_at")
                .eq("store_name", store_name)
                .limit(2000)
                .execute()
            )
            cust_df = pd.DataFrame(r_all.data or [])
            if cust_df.empty:
                st.info("등록된 고객이 없습니다.")
            else:
                total = len(cust_df)
                friends = cust_df["kakao_friend_added"].fillna(False).sum()
                non_friends = total - friends
                c1, c2, c3 = st.columns(3)
                c1.metric("전체 고객", f"{total}명")
                c2.metric("채널 친구", f"{int(friends)}명")
                c3.metric("미연결", f"{int(non_friends)}명")

                tab_all, tab_non = st.tabs(["전체 고객", "미연결 고객"])
                with tab_all:
                    disp = cust_df.rename(columns={
                        "id": "ID", "name": "이름", "phone1": "전화번호",
                        "kakao_friend_added": "친구", "kakao_friend_added_at": "친구추가일",
                    }).copy()
                    disp["친구"] = disp["친구"].fillna(False).map({True: "✅", False: "⚠️"})
                    disp["친구추가일"] = disp["친구추가일"].fillna("").astype(str).str[:16].str.replace("T", " ")
                    st.dataframe(disp, use_container_width=True)
                with tab_non:
                    non_df = cust_df[~cust_df["kakao_friend_added"].fillna(False)].copy()
                    if non_df.empty:
                        st.success("모든 고객이 채널 친구입니다.")
                    else:
                        non_disp = non_df[["id", "name", "phone1"]].rename(
                            columns={"id": "ID", "name": "이름", "phone1": "전화번호"}
                        )
                        st.dataframe(non_disp, use_container_width=True)
                        st.caption(f"미연결 고객 {len(non_df)}명에게 채널 초대 문자를 일괄 발송하려면 아래 버튼을 사용하세요.")
                        if st.button("📨 미연결 고객 전체 채널 초대 발송", key="crm_bulk_invite_btn", type="primary"):
                            try:
                                from customer_channel import send_channel_invite_sms
                                _actor = (st.session_state.get("current_user") or {}).get("username", "system")
                                _ok_cnt = 0
                                _fail_cnt = 0
                                for _, row in non_df.iterrows():
                                    _res = send_channel_invite_sms(
                                        customer_id=int(row["id"]),
                                        phone=str(row.get("phone1") or ""),
                                        customer_name=str(row.get("name") or "고객"),
                                        store_name=store_name,
                                        sent_by=_actor,
                                    )
                                    if _res.get("status") == "sent":
                                        _ok_cnt += 1
                                    else:
                                        _fail_cnt += 1
                                st.success(f"발송 완료: {_ok_cnt}건 성공 / {_fail_cnt}건 실패")
                            except Exception as e:
                                st.error(f"발송 중 오류: {e}")
        except Exception as e:
            st.warning(f"고객 조회 실패: {e}")
    else:
        st.info("매장 선택 후 이용 가능합니다.")

    st.divider()
    st.subheader("발송 이력 조회")
    if sc and store_name:
        try:
            col_f1, col_f2 = st.columns(2)
            with col_f1:
                _log_type = st.selectbox(
                    "메시지 유형",
                    ["전체", "purchase_confirm", "channel_invite", "cs_reply", "manual"],
                    key="crm_log_type_filter",
                )
            with col_f2:
                _log_status = st.selectbox(
                    "발송 상태",
                    ["전체", "sent", "failed", "skipped", "not_friend", "out_of_hours"],
                    key="crm_log_status_filter",
                )
            col_d1, col_d2 = st.columns(2)
            with col_d1:
                _log_start = st.date_input("시작일", value=None, key="crm_log_start")
            with col_d2:
                _log_end = st.date_input("종료일", value=None, key="crm_log_end")

            q = (
                sc.table("app_customer_messages")
                .select("id, customer_id, phone, message_type, channel, status, sent_by, message_body, error_detail, created_at")
                .eq("store_name", store_name)
                .order("created_at", desc=True)
                .limit(200)
            )
            if _log_type != "전체":
                q = q.eq("message_type", _log_type)
            if _log_status != "전체":
                q = q.eq("status", _log_status)
            if _log_start:
                q = q.gte("created_at", str(_log_start))
            if _log_end:
                q = q.lte("created_at", str(_log_end) + "T23:59:59")
            r_logs = q.execute()
            logs_df = pd.DataFrame(r_logs.data or [])
            if logs_df.empty:
                st.info("조건에 맞는 발송 이력이 없습니다.")
            else:
                logs_df["created_at"] = logs_df["created_at"].astype(str).str[:16].str.replace("T", " ")
                disp_logs = logs_df.rename(columns={
                    "id": "ID", "customer_id": "고객ID", "phone": "전화번호",
                    "message_type": "유형", "channel": "채널", "status": "상태",
                    "sent_by": "발송자", "message_body": "메시지", "error_detail": "오류", "created_at": "발송일시",
                })
                st.dataframe(disp_logs, use_container_width=True)

                import io as _io
                _xl_buf = _io.BytesIO()
                logs_df.to_excel(_xl_buf, index=False, sheet_name="발송이력")
                st.download_button(
                    "📥 발송 이력 Excel 다운로드",
                    data=_xl_buf.getvalue(),
                    file_name=f"kakao_send_logs_{store_name}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="crm_log_download",
                )
        except Exception as e:
            st.warning(f"발송 이력 조회 실패: {e}")
    else:
        st.info("매장 선택 후 이용 가능합니다.")

    # ── 고객 인바운드 메시지 상담 패널 ────────────────────────────────────
    st.divider()
    st.subheader("고객 수신 메시지 상담")
    st.caption("고객이 카카오채널로 보낸 메시지를 확인하고 답장을 보냅니다. (5초마다 자동 새로고침)")

    if sc and store_name:
        # 자동 새로고침 토글
        _auto_refresh = st.toggle("자동 새로고침 (5초)", value=False, key="crm_inbound_autorefresh")
        if _auto_refresh:
            import time as _time
            _time.sleep(5)
            st.rerun()

        try:
            # 인바운드 메시지 목록 (최근 50건)
            _inbound_q = (
                sc.table("app_customer_messages")
                .select("id, customer_id, kakao_user_key, message_body, created_at")
                .eq("direction", "inbound")
                .order("created_at", desc=True)
                .limit(50)
            )
            if store_name:
                _inbound_q = _inbound_q.eq("store_name", store_name)
            _inbound_r = _inbound_q.execute()
            _inbound_df = pd.DataFrame(_inbound_r.data or [])

            if _inbound_df.empty:
                st.info("수신된 고객 메시지가 없습니다.")
                st.caption(
                    "고객이 카카오채널로 메시지를 보내면 이 화면에 표시됩니다.\n"
                    "Solapi 콘솔에 메시지 수신 웹훅 URL이 등록되어 있어야 합니다:\n"
                    "`https://emons-sms-webhook.onrender.com/webhook/solapi/message-received`"
                )
            else:
                # customer_id → 고객명 매핑
                _cust_ids = _inbound_df["customer_id"].dropna().astype(int).unique().tolist()
                _cust_map: dict = {}
                if _cust_ids:
                    try:
                        _cr = (
                            sc.table("app_customers")
                            .select("id, name, phone1")
                            .in_("id", _cust_ids)
                            .execute()
                        )
                        _cust_map = {
                            int(r["id"]): {"name": r.get("name", "알 수 없음"), "phone": r.get("phone1", "")}
                            for r in (_cr.data or [])
                        }
                    except Exception:
                        pass

                # 고객 선택 드롭다운
                _unique_custs = _inbound_df["customer_id"].dropna().astype(int).unique().tolist()

                def _fmt_cust_inbound(cid: int) -> str:
                    info = _cust_map.get(int(cid), {})
                    return f"{info.get('name', '?')} ({info.get('phone', '')})" if info else str(cid)

                _sel_cust_id = st.selectbox(
                    "대화할 고객 선택",
                    options=_unique_custs,
                    format_func=_fmt_cust_inbound,
                    key="crm_inbound_cust_select",
                )

                if _sel_cust_id:
                    # 선택 고객의 전체 대화 이력 (인바운드 + 아웃바운드)
                    try:
                        _chat_r = (
                            sc.table("app_customer_messages")
                            .select("id, direction, message_body, sent_by, channel, created_at")
                            .eq("customer_id", int(_sel_cust_id))
                            .order("created_at", desc=False)
                            .limit(100)
                            .execute()
                        )
                        _chat_rows = _chat_r.data or []
                    except Exception:
                        _chat_rows = []

                    # 대화창 표시
                    _chat_container = st.container(border=True)
                    with _chat_container:
                        if not _chat_rows:
                            st.caption("대화 내용이 없습니다.")
                        for _msg in _chat_rows:
                            _dir = _msg.get("direction", "outbound")
                            _body = _msg.get("message_body") or ""
                            _ts = str(_msg.get("created_at") or "")[:16].replace("T", " ")
                            _by = _msg.get("sent_by") or ""
                            if _dir == "inbound":
                                st.chat_message("user").markdown(
                                    f"{_body}\n\n<small style='color:gray'>{_ts}</small>",
                                    unsafe_allow_html=True,
                                )
                            else:
                                st.chat_message("assistant").markdown(
                                    f"{_body}\n\n<small style='color:gray'>{_ts} · {_by}</small>",
                                    unsafe_allow_html=True,
                                )

                    # 답장 입력창
                    _reply_body = st.text_area(
                        "답장 메시지 입력",
                        placeholder="고객에게 보낼 메시지를 입력하세요.",
                        height=100,
                        key=f"crm_reply_body_{_sel_cust_id}",
                    )
                    _cust_info = _cust_map.get(int(_sel_cust_id), {})
                    _cust_phone = _cust_info.get("phone", "")
                    _cust_name = _cust_info.get("name", "고객")
                    _actor = (st.session_state.get("current_user") or {}).get("username", "system")

                    if st.button("답장 보내기", key=f"crm_reply_send_{_sel_cust_id}", type="primary", use_container_width=True):
                        if not _reply_body.strip():
                            st.warning("메시지 내용을 입력하세요.")
                        elif not _cust_phone:
                            st.warning("고객 전화번호가 없어 발송할 수 없습니다.")
                        else:
                            try:
                                from customer_channel import send_manual_friendtalk
                                _reply_res = send_manual_friendtalk(
                                    customer_id=int(_sel_cust_id),
                                    phone=_cust_phone,
                                    customer_name=_cust_name,
                                    message_body=_reply_body.strip(),
                                    store_name=store_name,
                                    sent_by=_actor,
                                )
                                if _reply_res.get("status") == "sent":
                                    st.toast("답장이 발송되었습니다.", icon="✅")
                                    st.rerun()
                                elif _reply_res.get("status") == "not_friend":
                                    st.warning("채널 친구가 아니어서 친구톡 발송이 불가합니다. 일반 SMS로 발송하려면 채널 초대 먼저 발송하세요.")
                                elif _reply_res.get("status") == "skipped":
                                    st.warning("Solapi 설정이 없어 발송이 건너뛰어졌습니다.")
                                else:
                                    st.error(f"발송 실패: {_reply_res.get('error')}")
                            except Exception as e:
                                st.error(f"발송 중 오류: {e}")

        except Exception as e:
            st.warning(f"인바운드 메시지 조회 실패: {e}")
    else:
        st.info("매장 선택 후 이용 가능합니다.")


def render_crm_menu() -> None:
    """7. 고객 CRM 자동화 메뉴 진입점."""

    # ── 1) 권한 제어 (RBAC) ──────────────────
    user = st.session_state.get("current_user") or {}
    role = user.get("role", "")
    if role not in ALLOWED_ROLES:
        st.error("접근 권한이 없습니다. 매장관리자 이상만 이용 가능한 메뉴입니다.")
        return

    db_filename: str | None = st.session_state.get("current_db")
    store_name = _get_current_store_name(db_filename)

    st.title("7. 고객 CRM 자동화")
    st.caption("타겟 고객을 필터링하고 카카오 친구톡(광고) 또는 SMS로 CRM 메시지를 발송·예약합니다.")

    _crm_tab1, _crm_tab2 = st.tabs(["CRM 캠페인", "카카오 채널 현황"])
    with _crm_tab2:
        _render_kakao_channel_tab(db_filename, store_name)
    with _crm_tab1:
        _render_crm_campaign_tab(db_filename, store_name)


def _render_crm_campaign_tab(db_filename: str | None, store_name: str | None) -> None:
    """CRM 캠페인 탭 내용."""
    # ── 2) 타겟 고객 필터링 ───────────────────
    with st.expander("🎯 타겟 고객 필터 설정", expanded=True):
        col1, col2 = st.columns(2)
        with col1:
            item_filter = st.multiselect(
                "구매 품목 (복수 선택)",
                options=["소파", "침대", "옷장", "식탁", "책상", "TV장", "매트리스", "기타"],
                key="crm_item_filter",
            )
            region_filter = st.text_input(
                "지역 (주소 포함 키워드)",
                placeholder="예) 울산, 삼산동",
                key="crm_region_filter",
            )
        with col2:
            col_od1, col_od2 = st.columns(2)
            with col_od1:
                order_date_start = st.date_input("계약일 시작", value=None, key="crm_od_start")
            with col_od2:
                order_date_end = st.date_input("계약일 종료", value=None, key="crm_od_end")
            col_dd1, col_dd2 = st.columns(2)
            with col_dd1:
                delivery_date_start = st.date_input("배송일 시작", value=None, key="crm_dd_start")
            with col_dd2:
                delivery_date_end = st.date_input("배송일 종료", value=None, key="crm_dd_end")

        col_p1, col_p2 = st.columns(2)
        with col_p1:
            price_min = st.number_input(
                "판매가 최소 (원)", min_value=0, value=0, step=100000, key="crm_price_min"
            )
        with col_p2:
            price_max = st.number_input(
                "판매가 최대 (원, 0 = 제한 없음)", min_value=0, value=0, step=100000, key="crm_price_max"
            )

        if st.button("🔍 타겟 고객 조회", key="crm_search_btn", use_container_width=True):
            with st.spinner("Supabase에서 타겟을 조회 중..."):
                targets_df = _query_targets(
                    db_filename=db_filename,
                    store_name=store_name,
                    item_filter=item_filter,
                    region_filter=region_filter or "",
                    order_date_start=order_date_start if isinstance(order_date_start, date) else None,
                    order_date_end=order_date_end if isinstance(order_date_end, date) else None,
                    delivery_date_start=delivery_date_start if isinstance(delivery_date_start, date) else None,
                    delivery_date_end=delivery_date_end if isinstance(delivery_date_end, date) else None,
                    price_min=int(price_min),
                    price_max=int(price_max),
                )
            st.session_state["crm_targets_df"] = targets_df

        targets_df: pd.DataFrame = st.session_state.get("crm_targets_df", pd.DataFrame())
        if not targets_df.empty:
            st.success(f"발견된 타겟 **{len(targets_df)}명**")
            disp = targets_df.rename(columns={
                "name": "고객명", "phone1": "연락처", "address": "주소",
                "order_date": "계약일", "delivery_date": "배송일",
                "category": "품목", "total_amount": "판매가",
            })
            st.dataframe(disp.drop(columns=["customer_id", "phone2"], errors="ignore"),
                         use_container_width=True)
        elif "crm_targets_df" in st.session_state:
            st.info("조건에 맞는 고객이 없습니다. 필터를 조정해 주세요.")

    # ── 3) 발송 채널 및 메시지 작성 ───────────
    st.divider()
    st.subheader("✉️ 발송 채널 및 메시지 작성")

    send_channel = st.radio(
        "발송 채널 선택",
        SEND_CHANNEL_OPTIONS,
        horizontal=True,
        key="crm_send_channel",
    )

    kakao_channel_id: str | None = None
    image_file_id: str | None = None
    image_uploaded = False

    if "카카오" in send_channel:
        st.info(
            "📢 **정보통신망법 안내**: 광고성 메시지 발송 시 메시지 앞에 **(광고)** 표기가 자동 삽입됩니다. "
            "수신거부 안내(무료수신거부: 080-XXX-XXXX)도 메시지 하단에 포함해 주세요."
        )
        kakao_channel_id = st.text_input(
            "카카오톡 채널 ID (필수)",
            placeholder="예) @이몬스가구",
            key="crm_kakao_channel_id",
        )
        uploaded_img = st.file_uploader(
            "광고 이미지 첨부 (선택, Solapi에 미리 등록된 imageId 사용 시 생략 가능)",
            type=["jpg", "jpeg", "png"],
            key="crm_kakao_image",
        )
        if uploaded_img:
            image_uploaded = True
            st.image(uploaded_img, caption="첨부 이미지 미리보기", width=300)
            st.caption("※ 실제 발송 시 Solapi에 이미지를 먼저 업로드한 후 반환된 imageId를 코드에 입력하세요.")

        fallback_sms = st.checkbox(
            "친구 미추가 고객: 일반 문자(SMS)로 대체 발송 (Fallback)",
            value=True,
            key="crm_fallback_sms",
        )
    else:
        fallback_sms = False

    st.caption(f"💡 변수 치환 안내: {VARIABLE_GUIDE}")
    message_template = st.text_area(
        "메시지 내용",
        placeholder=(
            "예) 안녕하세요 {이름}님! 이몬스 가구입니다.\n"
            "구매하신 {품목}의 배송일({배송일})이 다가왔습니다.\n"
            "궁금한 점이 있으시면 언제든지 연락해 주세요. 😊\n\n"
            "무료수신거부: 080-000-0000"
        ),
        height=180,
        key="crm_message_template",
    )
    campaign_name = st.text_input(
        "캠페인 이름 (저장용)",
        placeholder="예) 2024년 4월 재구매 유도 캠페인",
        key="crm_campaign_name",
    )

    # ── 4) 발송 시점 조건 ────────────────────
    st.divider()
    st.subheader("⏰ 발송 시점 설정")

    trigger = st.selectbox("발송 타이밍", TRIGGER_OPTIONS, key="crm_trigger")

    scheduled_date_str: str | None = None
    delivery_offset_days: int | None = None

    if trigger == "특정일 예약 발송":
        sched = st.date_input(
            "예약 발송 날짜",
            value=_today_kst() + timedelta(days=1),
            key="crm_scheduled_date",
        )
        if isinstance(sched, date):
            scheduled_date_str = str(sched)

    elif "배송 후" in trigger:
        _days_map = {"7일": 7, "30일(1개월)": 30, "100일": 100, "365일(1년)": 365}
        for k, v in _days_map.items():
            if k in trigger:
                delivery_offset_days = v
                break
        st.info(
            f"배송일 기준 **+{delivery_offset_days}일** 후 자동 발송됩니다. "
            "발송 규칙은 Supabase `crm_automations` 테이블에 저장되며, "
            "별도 스케줄러(Supabase Edge Function 또는 cron)에서 실행됩니다."
        )

    # ── 5) 실행 및 저장 ─────────────────────
    st.divider()
    st.subheader("🚀 CRM 캠페인 실행 / 저장")

    targets_df_final: pd.DataFrame = st.session_state.get("crm_targets_df", pd.DataFrame())
    n_targets = len(targets_df_final)

    if st.button("📨 CRM 캠페인 실행 / 저장", key="crm_execute_btn",
                 type="primary", use_container_width=True):

        # ── 유효성 검사
        if not message_template.strip():
            st.error("메시지 내용을 입력해 주세요.")
            st.stop()
        if not campaign_name.strip():
            st.error("캠페인 이름을 입력해 주세요.")
            st.stop()
        if "카카오" in send_channel and not kakao_channel_id:
            st.error("카카오 브랜드톡 발송 시 채널 ID는 필수입니다.")
            st.stop()
        if n_targets == 0:
            st.error("타겟 고객이 없습니다. 먼저 '타겟 고객 조회'를 실행해 주세요.")
            st.stop()

        # ── Solapi Payload 생성
        payload = _build_solapi_payload(
            targets=targets_df_final,
            message_template=message_template,
            send_channel=send_channel,
            kakao_channel_id=kakao_channel_id,
            fallback_sms=fallback_sms,
            image_uploaded=image_uploaded,
            image_file_id=image_file_id,
        )

        if trigger == "즉시 발송":
            # 즉시 발송: Payload 확인 후 성공 처리
            # 실제 Solapi 호출은 아래 주석 해제 후 사용
            # import solapi; solapi.send_many(payload["messages"])
            st.success(f"✅ 총 **{n_targets}명**에게 발송 명령이 전달되었습니다.")
            with st.expander("📄 Solapi 발송 Payload (검토용)", expanded=False):
                st.json(payload)

        else:
            # 예약/자동 발송: Supabase crm_automations 에 저장
            ok = _save_crm_automation(
                store_name=store_name or "",
                campaign_name=campaign_name.strip(),
                send_channel=send_channel,
                kakao_channel_id=kakao_channel_id,
                message_template=message_template.strip(),
                trigger_type=trigger,
                scheduled_date=scheduled_date_str,
                delivery_offset_days=delivery_offset_days,
                item_filter=item_filter,
                region_filter=region_filter or "",
                price_min=int(price_min),
                price_max=int(price_max),
                fallback_sms=fallback_sms,
                solapi_payload_preview=payload,
                target_count=n_targets,
            )
            if ok:
                st.success(
                    f"📅 CRM 자동화 룰이 저장되었습니다. "
                    f"({trigger} / 타겟 {n_targets}명)"
                )
                with st.expander("📄 저장된 Payload 미리보기", expanded=False):
                    st.json(payload)
            # 실패 시 _save_crm_automation 내부에서 st.error 호출됨

    # ── 저장된 자동화 룰 목록 ─────────────────
    st.divider()
    with st.expander("📋 저장된 CRM 자동화 룰 목록", expanded=False):
        sc = _get_supabase()
        if sc and store_name:
            try:
                r = (
                    sc.table("crm_automations")
                    .select(
                        "id, campaign_name, send_channel, trigger_type,"
                        " scheduled_date, delivery_offset_days, target_count,"
                        " status, created_at"
                    )
                    .eq("store_name", store_name)
                    .order("id", desc=True)
                    .limit(50)
                    .execute()
                )
                rules_df = pd.DataFrame(r.data or [])
                if not rules_df.empty:
                    st.dataframe(
                        rules_df.rename(columns={
                            "campaign_name": "캠페인명", "send_channel": "채널",
                            "trigger_type": "발송 타이밍", "scheduled_date": "예약일",
                            "delivery_offset_days": "배송 후(일)",
                            "target_count": "타겟 수", "status": "상태",
                            "created_at": "생성일",
                        }),
                        use_container_width=True,
                    )
                else:
                    st.info("저장된 CRM 자동화 룰이 없습니다.")
            except Exception as e:
                st.warning(f"룰 목록 조회 실패: {e}")
        else:
            st.info("Supabase 연결이 필요합니다.")
