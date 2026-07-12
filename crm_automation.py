# -*- coding: utf-8 -*-
"""
7. 고객 CRM 자동화 모듈 (crm_automation.py)

사용법: app.py 에서 from crm_automation import render_crm_menu 로 import 후 호출.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
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

# 리드 단계 정의 (DB 값 → 표시명)
LEAD_STAGES: dict[str, str] = {
    "1_신규": "신규",
    "2_상담중": "상담중",
    "3_견적발송": "견적발송",
    "4_계약완료": "계약완료",
    "5_실패": "실패",
    "6_보류": "보류",
}
LEAD_STAGE_EMOJI: dict[str, str] = {
    "1_신규": "⚪",
    "2_상담중": "🟡",
    "3_견적발송": "🔵",
    "4_계약완료": "🟢",
    "5_실패": "🔴",
    "6_보류": "⚫",
}

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
            "from": "",  # send_batch()에서 secrets의 sender로 자동 주입
            "text": text,
            "type": "CTA" if is_kakao else "LMS",  # CTA = 친구톡 텍스트
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
    st.info(
        "ℹ️ **친구 상태 자동 갱신 방식**: Solapi는 친구 목록 조회 API를 제공하지 않습니다. "
        "CRM 캠페인 또는 메시지 발송 시 Solapi의 응답 결과에 따라 친구 상태가 자동으로 갱신됩니다.\n\n"
        "- 친구톡 **발송 성공** → 채널 친구로 자동 기록 ✅\n"
        "- 친구톡 발송 시 **'미친구' 응답** → 미연결로 자동 기록 ❌\n\n"
        "캠페인을 운영할수록 실제 친구 현황이 점점 정확해집니다."
    )
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
                    st.dataframe(disp, width="stretch")
                with tab_non:
                    non_df = cust_df[~cust_df["kakao_friend_added"].fillna(False)].copy()
                    if non_df.empty:
                        st.success("모든 고객이 채널 친구입니다.")
                    else:
                        non_disp = non_df[["id", "name", "phone1"]].rename(
                            columns={"id": "ID", "name": "이름", "phone1": "전화번호"}
                        )
                        st.dataframe(non_disp, width="stretch")
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
                st.dataframe(disp_logs, width="stretch")

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

    # ── 고객 실시간 상담 채팅창 (Next.js 앱 iframe) ────────────────────
    st.divider()
    st.subheader("고객 실시간 상담")

    import streamlit.components.v1 as _components
    try:
        import streamlit as _st
        _chat_app_url = (
            _st.secrets.get("chat_app", {}).get("url", "")
            if hasattr(_st, "secrets") else ""
        )
    except Exception:
        _chat_app_url = ""

    if not _chat_app_url:
        _chat_app_url = "https://emons-chat.vercel.app"

    _store_param = f"?store={store_name}" if store_name else ""
    _iframe_url = f"{_chat_app_url}/chat{_store_param}"

    st.caption(
        f"아래 창은 실시간 상담 앱입니다. 고객이 메시지를 보내면 즉시 표시됩니다. "
        f"([별도 창으로 열기]({_iframe_url}))"
    )
    _components.iframe(_iframe_url, height=680, scrolling=True)


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

    st.divider()
    st.info("📋 **리드고객 관리**는 메뉴 **3번 [리드고객 관리]** 로 이동되었습니다. 메시지 발송·상담 히스토리·채널톡 가져오기 등 모든 리드 기능이 통합되었습니다.")


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

        if st.button("🔍 타겟 고객 조회", key="crm_search_btn", width="stretch"):
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
                         width="stretch")
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
                 type="primary", width="stretch"):

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
            try:
                from solapi_sender import send_batch  # noqa: WPS433
                with st.spinner(f"Solapi로 {n_targets}명에게 발송 중..."):
                    result = send_batch(payload["messages"])
                sent_n = result.get("sent", 0)
                failed_n = result.get("failed", 0)
                errs = result.get("errors", [])
                if failed_n == 0:
                    st.success(f"✅ 총 **{sent_n}명** 발송 완료!")
                elif sent_n > 0:
                    st.warning(f"⚠️ {sent_n}명 성공 / {failed_n}명 실패")
                    for e in errs:
                        st.error(e)
                else:
                    st.error(f"❌ 발송 실패 ({failed_n}명)")
                    for e in errs:
                        st.error(e)

                # 친구톡 발송 성공 시 → 타겟 고객 친구 상태 갱신
                if sent_n > 0 and "카카오" in send_channel:
                    try:
                        from customer_channel import _update_kakao_friend_status  # noqa: WPS433
                        for _, trow in targets_df_final.iterrows():
                            _update_kakao_friend_status(
                                customer_id=int(trow["id"]) if trow.get("id") else None,
                                phone=str(trow.get("phone1") or ""),
                                send_status="sent",
                            )
                    except Exception:
                        pass
            except ImportError:
                st.error("solapi_sender 모듈을 불러올 수 없습니다.")
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
                        width="stretch",
                    )
                else:
                    st.info("저장된 CRM 자동화 룰이 없습니다.")
            except Exception as e:
                st.warning(f"룰 목록 조회 실패: {e}")
        else:
            st.info("Supabase 연결이 필요합니다.")

    # ── 아임웹 잠재고객 마케팅 발송 ───────────────────────────────────────
    st.divider()
    st.subheader("아임웹 잠재고객 마케팅 발송")
    st.caption(
        "아임웹 가입 후 구매 이력이 없는 고객(잠재고객)에게 친구톡 광고 메시지를 발송합니다.\n"
        "⚠️ marketing_agreed=True(마케팅 수신 동의) 고객에게만 발송됩니다. 법적 의무 준수."
    )

    sc2 = _get_supabase()
    if sc2:
        with st.expander("잠재고객 필터 및 발송", expanded=False):
            _col1, _col2 = st.columns(2)
            with _col1:
                _days_since_join = st.number_input(
                    "가입 후 최소 경과일 (미구매 기간)",
                    min_value=1, value=7, step=1,
                    key="imweb_days_filter",
                    help="가입 후 이 기간 이상 구매가 없는 고객만 조회",
                )
            with _col2:
                _marketing_only = st.checkbox(
                    "마케팅 동의 고객만 (권장)",
                    value=True,
                    key="imweb_marketing_filter",
                )

            if st.button("잠재고객 조회", key="imweb_prospect_search", width="stretch"):
                try:
                    from datetime import timedelta as _td
                    _cutoff = (datetime.now() - _td(days=int(_days_since_join))).isoformat()
                    _q = (
                        sc2.table("app_customers")
                        .select("id,name,phone1,imweb_joined_at,marketing_agreed,kakao_friend_added")
                        .eq("customer_type", "member_only")
                        .lte("imweb_joined_at", _cutoff)
                    )
                    if _marketing_only:
                        _q = _q.eq("marketing_agreed", True)
                    _r = _q.limit(200).execute()
                    _prospect_df = pd.DataFrame(_r.data or [])
                    st.session_state["imweb_prospects"] = _prospect_df
                except Exception as e:
                    st.error(f"조회 실패: {e}")

            _prospect_df = st.session_state.get("imweb_prospects", pd.DataFrame())
            if not _prospect_df.empty:
                st.success(f"조회된 잠재고객: **{len(_prospect_df)}명**")
                st.dataframe(
                    _prospect_df.rename(columns={
                        "name": "이름", "phone1": "전화번호",
                        "imweb_joined_at": "가입일", "marketing_agreed": "마케팅동의",
                        "kakao_friend_added": "채널친구",
                    }),
                    width="stretch",
                    height=200,
                )

                _msg_body = st.text_area(
                    "발송 메시지 내용",
                    placeholder=(
                        "{이름}님, 이몬스입니다.\n"
                        "가입 감사 이벤트로 특별 할인 혜택을 준비했습니다.\n"
                        "(광고) 수신거부: 080-000-0000"
                    ),
                    height=130,
                    key="imweb_bulk_msg",
                    help="{이름} 으로 고객 이름 자동 삽입 가능. 광고성 메시지는 반드시 (광고) 표기 및 수신거부 번호 포함 필요",
                )

                _actor = (st.session_state.get("current_user") or {}).get("username", "system")
                if st.button(
                    f"친구톡 일괄 발송 ({len(_prospect_df)}명)",
                    key="imweb_bulk_send",
                    type="primary",
                    width="stretch",
                ):
                    if not _msg_body.strip():
                        st.error("메시지 내용을 입력하세요.")
                    elif "(광고)" not in _msg_body:
                        st.error("광고성 메시지에는 반드시 '(광고)' 표기가 있어야 합니다. (정보통신망법 준수)")
                    else:
                        _targets = [
                            {
                                "customer_id": int(row["id"]),
                                "phone": str(row.get("phone1") or ""),
                                "name": str(row.get("name") or "고객"),
                            }
                            for _, row in _prospect_df.iterrows()
                            if row.get("phone1")
                        ]
                        with st.spinner(f"{len(_targets)}명에게 발송 중..."):
                            try:
                                from customer_channel import send_bulk_marketing
                                _result = send_bulk_marketing(
                                    targets=_targets,
                                    message_body=_msg_body.strip(),
                                    store_name=store_name or db_filename or "",
                                    sent_by=_actor,
                                )
                                st.success(
                                    f"발송 완료 — 성공: {_result['sent']}건 / "
                                    f"실패: {_result['failed']}건 / 스킵: {_result['skipped']}건"
                                )
                                st.session_state.pop("imweb_prospects", None)
                            except Exception as e:
                                st.error(f"발송 오류: {e}")
    else:
        st.info("Supabase 연결 후 이용 가능합니다.")


# ──────────────────────────────────────────────
# 가망고객 등록 탭 (옴니채널 리드)
# ──────────────────────────────────────────────

def _register_lead_form(auto_store: str, employee_id: Any, user: dict) -> None:
    """리드 등록 폼 (컨테이너 내부에서 렌더링)."""
    with st.form("lead_register_form", clear_on_submit=True):
        fc1, fc2 = st.columns(2)
        with fc1:
            lead_source = st.radio("유입 경로", ["전화_문의", "오프라인_방문"], horizontal=True)
        with fc2:
            send_now = st.toggle("즉시 메시지 발송", value=True)
        phone_input = st.text_input("전화번호 (필수)", placeholder="010-0000-0000")
        name_input = st.text_input("고객 성함 (선택)")
        memo_input = st.text_area("상담 메모", placeholder="예: 토레도 소파 4인용 가격 문의", height=80)
        fd1, fd2 = st.columns(2)
        with fd1:
            next_contact = st.date_input("다음 연락 예정일", value=date.today() + timedelta(days=3))
        with fd2:
            image_url_input = ""
            if lead_source == "오프라인_방문":
                image_url_input = st.text_input("MMS 첨부 사진 URL (선택)", placeholder="https://...")
        submitted = st.form_submit_button("등록", type="primary", width="stretch")

    if submitted:
        phone_clean = re.sub(r"\D", "", phone_input or "")
        if not phone_clean or len(phone_clean) < 10:
            st.error("전화번호를 올바르게 입력해 주세요.")
        else:
            try:
                from lead_manager import register_lead, save_chat_history  # noqa: WPS433
                result = register_lead(
                    phone=phone_clean,
                    name=name_input or "",
                    memo=memo_input or "",
                    lead_source=lead_source,
                    store_name=auto_store,
                    employee_id=employee_id,
                    next_contact_date=str(next_contact),
                    send_now=send_now,
                    image_url=image_url_input or "",
                )
                if result.get("ok"):
                    _sr = result.get("send_result", {}) or {}
                    _st = _sr.get("status", "")
                    _err = _sr.get("error", "")
                    send_label = {
                        "sent": "발송 완료", "lms_fallback": "LMS 발송 완료",
                        "skipped": f"발송 보류 ({_err})", "failed": f"발송 실패 ({_err})",
                        "not_friend": "미친구(SMS 폴백)", "out_of_hours": "야간 발송 거부",
                    }.get(_st, f"미발송 ({_st})" if _st else "즉시발송 OFF")
                    st.success(f"✅ 등록 완료 (ID: {result['lead_id']}) | 메시지: {send_label}")
                    st.session_state["lead_show_form"] = False
                elif result.get("error") == "duplicate_phone":
                    ex = result.get("existing", {})
                    st.warning(
                        f"⚠️ 이미 등록된 번호입니다 — ID: {result.get('lead_id')} | "
                        f"성함: {ex.get('name', '—')} | "
                        f"단계: {LEAD_STAGES.get(ex.get('lead_stage',''), ex.get('lead_stage','—'))} | "
                        f"등록일: {str(ex.get('created_at', ''))[:10]}"
                    )
                    if memo_input:
                        save_chat_history(
                            phone=phone_clean,
                            channel="전화_통화" if lead_source == "전화_문의" else "오프라인_메모",
                            summary=memo_input,
                            handled_by=str(user.get("username") or ""),
                        )
                else:
                    st.error(f"등록 실패: {result.get('error')}")
            except ImportError:
                st.error("lead_manager 모듈을 불러오지 못했습니다.")
            except Exception as e:
                st.error(f"오류: {e}")


def _render_lead_registration_tab(db_filename: str | None, store_name: str | None) -> None:
    """리드 관리 전체 화면 — 통계 + 필터 + 목록 + 메시지 발송 + 단계 변경."""
    user = st.session_state.get("current_user") or {}
    employee_id = user.get("id")
    auto_store = store_name or user.get("store_name") or ""
    supa = _get_supabase()

    # ── 헤더 + 리드 등록 버튼 ─────────────────
    hc1, hc2 = st.columns([5, 1])
    with hc1:
        st.subheader("리드 관리")
        st.caption("첫인상은 3초, 후속 연락은 평판으로 남습니다.")
    with hc2:
        if st.button("＋ 리드 등록", type="primary", width="stretch", key="lead_open_form_btn"):
            st.session_state["lead_show_form"] = not st.session_state.get("lead_show_form", False)
            st.session_state.pop("selected_lead_id", None)

    # ── 등록 폼 ───────────────────────────────
    if st.session_state.get("lead_show_form", False):
        with st.container(border=True):
            _register_lead_form(auto_store, employee_id, user)

    # ── 통계 카드 ──────────────────────────────
    if supa and auto_store:
        try:
            _all = supa.table("app_leads").select("id,lead_stage,revenue_amount") \
                .eq("store_name", auto_store).execute().data or []
            _df_stat = pd.DataFrame(_all)
            _total = len(_df_stat)
            _new = int((_df_stat["lead_stage"] == "1_신규").sum()) if _total else 0
            _consult = int(_df_stat["lead_stage"].isin(["2_상담중", "3_견적발송"]).sum()) if _total else 0
            _conv = int((_df_stat["lead_stage"] == "4_계약완료").sum()) if _total else 0
            _conv_rate = f"{round(_conv / _total * 100, 1)}%" if _total else "0%"
            sc1, sc2, sc3, sc4 = st.columns(4)
            sc1.metric("전체 리드", f"{_total}건")
            sc2.metric("신규", f"{_new}건")
            sc3.metric("상담중", f"{_consult}건", help="상담중 + 견적발송")
            sc4.metric("전환 완료", f"{_conv}건", delta=f"전환율 {_conv_rate}")
        except Exception:
            pass

    st.divider()

    # ── 필터 + 검색 ───────────────────────────
    fc1, fc2, fc3 = st.columns([3, 2, 3])
    with fc1:
        stage_opts = ["전체"] + list(LEAD_STAGES.keys())
        stage_sel = st.selectbox(
            "단계",
            stage_opts,
            format_func=lambda x: "전체" if x == "전체" else f"{LEAD_STAGE_EMOJI.get(x,'')} {LEAD_STAGES[x]}",
            key="lead_filter_stage",
        )
    with fc2:
        source_sel = st.selectbox(
            "유입 경로",
            ["전체", "전화_문의", "오프라인_방문", "온라인_채널톡"],
            key="lead_filter_source",
        )
    with fc3:
        search_q = st.text_input("🔍 이름·전화번호 검색", placeholder="검색어 입력", label_visibility="collapsed", key="lead_search")

    # ── 리드 목록 조회 ─────────────────────────
    if not supa:
        st.info("Supabase 연결 후 이용 가능합니다.")
        return

    try:
        q = supa.table("app_leads").select(
            "id,phone,name,lead_source,lead_stage,memo,next_contact_date,"
            "assigned_store,created_at,revenue_amount,contact_memo"
        ).order("created_at", desc=True).limit(300)
        if auto_store:
            q = q.eq("store_name", auto_store)
        leads_raw: list[dict] = q.execute().data or []
    except Exception as e:
        st.error(f"리드 조회 실패: {e}")
        return

    # 클라이언트 필터링
    leads = leads_raw
    if stage_sel != "전체":
        leads = [l for l in leads if l.get("lead_stage") == stage_sel]
    if source_sel != "전체":
        leads = [l for l in leads if l.get("lead_source") == source_sel]
    if search_q:
        sq = search_q.lower()
        leads = [
            l for l in leads
            if sq in (l.get("name") or "").lower() or sq in (l.get("phone") or "")
        ]

    st.caption(f"{len(leads)}건 표시 중")

    if not leads:
        st.info("조건에 맞는 리드가 없습니다.")
        return

    # ── 테이블 헤더 ───────────────────────────
    _hcols = st.columns([2, 2, 1.5, 1.8, 1.5, 1.5, 0.8])
    for _hc, _hl in zip(_hcols, ["이름", "전화번호", "유입경로", "단계", "담당매장", "등록일", "관리"]):
        _hc.markdown(f"**{_hl}**")

    # ── 리드 행 렌더링 ─────────────────────────
    for _i, _lead in enumerate(leads):
        _lid = _lead.get("id")
        _lname = _lead.get("name") or "—"
        _lphone = _lead.get("phone") or ""
        _lstage = _lead.get("lead_stage") or ""
        _lsource = (_lead.get("lead_source") or "").replace("_", " ")
        _lstore = _lead.get("assigned_store") or "—"
        _lcreated = str(_lead.get("created_at") or "")[:10]
        _slabel = f"{LEAD_STAGE_EMOJI.get(_lstage, '⚪')} {LEAD_STAGES.get(_lstage, _lstage)}"

        _rc = st.columns([2, 2, 1.5, 1.8, 1.5, 1.5, 0.8])
        _rc[0].write(_lname)
        _rc[1].write(_lphone)
        _rc[2].write(_lsource)
        _rc[3].write(_slabel)
        _rc[4].write(_lstore)
        _rc[5].write(_lcreated)
        with _rc[6]:
            _is_sel = st.session_state.get("selected_lead_id") == _lid
            if st.button("닫기" if _is_sel else "관리", key=f"lead_act_{_lid}_{_i}", width="stretch"):
                if _is_sel:
                    st.session_state.pop("selected_lead_id", None)
                else:
                    st.session_state["selected_lead_id"] = _lid
                    st.session_state["lead_show_form"] = False

    # ── 선택된 리드 액션 패널 ───────────────────
    _sel_id = st.session_state.get("selected_lead_id")
    if _sel_id:
        _sel = next((l for l in leads_raw if l.get("id") == _sel_id), None)
        if _sel:
            st.divider()
            _sname = _sel.get("name") or _sel.get("phone") or "리드"
            _slabel_sel = f"{LEAD_STAGE_EMOJI.get(_sel.get('lead_stage',''), '⚪')} {LEAD_STAGES.get(_sel.get('lead_stage',''), _sel.get('lead_stage',''))}"
            st.markdown(f"#### 📋 {_sname} &nbsp; <span style='font-size:0.85rem;color:#666'>{_slabel_sel}</span>", unsafe_allow_html=True)

            _atab1, _atab2 = st.tabs(["💬 메시지 발송", "📊 단계 변경"])

            # ── 메시지 발송 탭 ──────────────────
            with _atab1:
                try:
                    from solapi_sender import send_friendtalk, send_sms, check_solapi_config  # noqa: WPS433
                except ImportError:
                    st.error("solapi_sender 모듈 없음")
                    send_friendtalk = None  # type: ignore
                    send_sms = None  # type: ignore
                    check_solapi_config = None  # type: ignore

                # ── Solapi 설정 진단 ───────────────
                if check_solapi_config:
                    _cfg = check_solapi_config()
                    if not _cfg["all_ok"]:
                        st.error(
                            f"⚠️ **Solapi 키 미로드** — 발송 불가  \n"
                            f"api_key: {'✅' if _cfg['api_key'] else '❌'}  "
                            f"api_secret: {'✅' if _cfg['api_secret'] else '❌'}  "
                            f"pf_id: {'✅' if _cfg['pf_id'] else '❌'}  "
                            f"sender: {'✅' if _cfg['sender'] else '❌'}  \n"
                            f"로드 경로: {_cfg['source']}  \n"
                            f"api_key 앞 4자리: `{_cfg['api_key_hint']}`"
                        )
                    else:
                        st.caption(f"✅ Solapi 연결됨 ({_cfg['source']}, key: `{_cfg['api_key_hint']}`)")

                _ch = st.radio(
                    "발송 채널",
                    ["친구톡 (카카오)", "SMS"],
                    horizontal=True,
                    key="act_channel",
                )
                _default_msg = (
                    f"안녕하세요 {_sel.get('name') or '고객'}님, 에몬스 {auto_store}입니다.\n"
                    "문의하신 내용 관련하여 연락드립니다.\n"
                    "편하신 시간에 답변 주시면 감사하겠습니다."
                )
                _msg_body = st.text_area(
                    "메시지 내용", value=_default_msg, height=130, key="act_msg_body"
                )
                if st.button("📤 발송", type="primary", key="act_send_btn", width="stretch"):
                    _phone_to = _sel.get("phone", "")
                    if not _phone_to:
                        st.error("전화번호가 없습니다.")
                    elif not send_friendtalk:
                        st.error("발송 모듈 없음")
                    else:
                        with st.spinner("발송 중..."):
                            if _ch.startswith("친구톡"):
                                _res = send_friendtalk(_phone_to, _msg_body)
                            else:
                                _res = send_sms(_phone_to, _msg_body)
                        _rs = _res.get("status", "")
                        _re = _res.get("error", "")
                        if _rs == "sent":
                            st.success("✅ 발송 완료!")
                            try:
                                from customer_channel import _update_kakao_friend_status  # noqa: WPS433
                                _update_kakao_friend_status(None, _phone_to, _rs)
                            except Exception:
                                pass
                        elif _rs == "skipped" and _re == "solapi_secrets_missing":
                            st.warning(
                                "⚙️ Solapi 키가 로드되지 않았습니다.  \n"
                                "`.streamlit/secrets.toml` 의 `[solapi]` 섹션 또는 "
                                "Render 환경변수 `SOLAPI_API_KEY / SOLAPI_PF_ID` 등을 확인해 주세요."
                            )
                        elif _rs == "skipped":
                            st.warning(f"발송 보류: {_re}")
                        elif _rs == "not_friend":
                            st.info("카카오 친구가 아닌 고객입니다. SMS로 재시도하려면 채널을 SMS로 변경해 주세요.")
                        else:
                            st.error(f"발송 실패: {_re}")

            # ── 단계 변경 탭 ────────────────────
            with _atab2:
                _cur_idx = list(LEAD_STAGES.keys()).index(_sel.get("lead_stage", "1_신규")) \
                    if _sel.get("lead_stage") in LEAD_STAGES else 0
                _new_stage = st.selectbox(
                    "새 단계",
                    list(LEAD_STAGES.keys()),
                    format_func=lambda x: f"{LEAD_STAGE_EMOJI.get(x,'')} {LEAD_STAGES[x]}",
                    index=_cur_idx,
                    key="act_stage_sel",
                )
                _nc_upd = st.date_input("다음 연락 예정일", value=None, key="act_next_contact")
                _memo_upd = st.text_area(
                    "사후 메모", placeholder="상담 결과, 다음 액션 등", height=80, key="act_memo"
                )
                if st.button("단계 업데이트", type="primary", key="act_stage_btn", width="stretch"):
                    try:
                        _upd: dict = {
                            "lead_stage": _new_stage,
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }
                        if _memo_upd:
                            _upd["contact_memo"] = _memo_upd
                            _upd["followup_done"] = True
                        if _nc_upd:
                            _upd["next_contact_date"] = str(_nc_upd)
                        supa.table("app_leads").update(_upd).eq("id", _sel_id).execute()
                        st.success(f"✅ {_sname} → {LEAD_STAGES[_new_stage]} 완료")
                        st.session_state.pop("selected_lead_id", None)
                        st.rerun()
                    except Exception as _ue:
                        st.error(f"업데이트 실패: {_ue}")
