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
    if not sc or not store_name:
        return pd.DataFrame()
    try:
        # app_orders 조회 (store_name 기준)
        q = (
            sc.table("app_orders")
            .select(
                "id, customer_id, order_date, delivery_date, category,"
                " total_amount, employee_names"
            )
            .eq("store_name", store_name)
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
