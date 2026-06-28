"""
엘리베이터 사이즈 점검 모듈.

기능:
1. 주소로 승강기 스펙 조회 — 공공데이터포털(국가승강기정보센터) API 연동
2. 매트리스 진입 시뮬레이션 — 엘리베이터 내부 치수 + 매트리스 사이즈로 진입 가능 여부 판정

데이터 소스: 행정안전부_승강기 정보 (한국승강기안전공단, data.go.kr)
"""

from __future__ import annotations

import math
import os
from typing import Any

import pandas as pd
import streamlit as st


# ────────────────────────────────────────────────────────────────
# 공공데이터포털 API 클라이언트
# ────────────────────────────────────────────────────────────────

ELEVATOR_API_BASE = "http://openapi.elevator.go.kr/openapi/service/ElevatorInfoService"


def _get_service_key() -> str:
    """승강기 API 서비스 키 로드. 환경변수 우선, secrets 폴백."""
    key = os.environ.get("ELEVATOR_API_KEY", "")
    if key:
        return key.strip()
    try:
        sec = st.secrets.get("elevator_api", {}) if hasattr(st, "secrets") else {}
        return str(sec.get("service_key", "") or "").strip()
    except Exception:
        return ""


@st.cache_data(ttl=3600, show_spinner=False)
def fetch_elevators_by_address(
    road_addr: str = "",
    building_name: str = "",
    sido: str = "",
    sigungu: str = "",
    num_rows: int = 30,
) -> dict:
    """
    공공데이터포털 승강기 정보 API 호출.

    Returns:
        {"ok": bool, "items": list[dict], "error": str}
    """
    import requests
    import xmltodict

    key = _get_service_key()
    if not key:
        return {"ok": False, "items": [], "error": "API 서비스키가 설정되지 않았습니다."}

    params: dict[str, Any] = {
        "serviceKey": key,
        "numOfRows": str(num_rows),
        "pageNo": "1",
        "_type": "xml",
    }
    if sido:
        params["Sido"] = sido
    if sigungu:
        params["Sigungu"] = sigungu
    if road_addr:
        params["address1"] = road_addr
        params["roadAddr"] = road_addr
    if building_name:
        params["buldNm"] = building_name

    try:
        resp = requests.get(
            f"{ELEVATOR_API_BASE}/getList",
            params=params,
            timeout=10.0,
        )
    except Exception as e:
        return {"ok": False, "items": [], "error": f"API 호출 실패: {e}"}

    if resp.status_code >= 400:
        return {"ok": False, "items": [],
                "error": f"API 응답 오류 {resp.status_code}: {resp.text[:200]}"}

    text = resp.text or ""
    # 인증키 오류 케이스 (공공데이터포털은 200 OK로 반환)
    if "SERVICE_KEY_IS_NOT_REGISTERED" in text or "SERVICE KEY IS NOT REGISTERED" in text.upper():
        return {"ok": False, "items": [],
                "error": "서비스키가 등록되지 않았습니다. 공공데이터포털에서 활용신청 후 1~2시간 기다리거나 키를 재확인해 주세요."}

    try:
        parsed = xmltodict.parse(text)
    except Exception as e:
        return {"ok": False, "items": [], "error": f"응답 파싱 실패: {e}"}

    # response/body/items/item 구조
    body = (parsed.get("response") or {}).get("body") or {}
    items_wrap = body.get("items") or {}
    raw_items = items_wrap.get("item") if isinstance(items_wrap, dict) else None
    if raw_items is None:
        return {"ok": True, "items": [], "error": ""}
    if isinstance(raw_items, dict):
        raw_items = [raw_items]

    return {"ok": True, "items": raw_items, "error": ""}


def _to_int_mm(v: Any) -> int:
    """API 응답값을 mm 정수로 변환 (None/문자열/숫자 모두 처리)."""
    if v is None or v == "":
        return 0
    try:
        return int(float(str(v)))
    except (TypeError, ValueError):
        return 0


def _items_to_dataframe(items: list[dict]) -> pd.DataFrame:
    """API 응답을 보기 좋은 DataFrame으로 변환."""
    if not items:
        return pd.DataFrame()
    rows = []
    for it in items:
        rows.append({
            "승강기번호": it.get("elvtrAsignNo") or it.get("elevatorNo") or "",
            "건물명": it.get("buldNm") or "",
            "주소": it.get("address1") or it.get("address") or "",
            "종류": it.get("elvtrKindNm") or it.get("elvtrDiv") or "",
            "정원(명)": _to_int_mm(it.get("ratedCap") or it.get("rrUsr")),
            "적재하중(kg)": _to_int_mm(it.get("liveLoad")),
            "내부폭W(mm)": _to_int_mm(it.get("bdyWdthSz") or it.get("cgeWidth")),
            "내부깊이D(mm)": _to_int_mm(it.get("bdyLnghSz") or it.get("cgeLength")),
            "내부높이H(mm)": _to_int_mm(it.get("bdyHgtSz") or it.get("cgeHeight")),
            "출입구폭(mm)": _to_int_mm(it.get("doorWdthSz") or it.get("entWidth")),
            "출입구높이(mm)": _to_int_mm(it.get("doorHgtSz") or it.get("entHeight")),
            "검사유효기간": it.get("nextInspctPlanDt") or it.get("nextInspectionDate") or "",
        })
    return pd.DataFrame(rows)


# ────────────────────────────────────────────────────────────────
# 매트리스 진입 시뮬레이션 알고리즘
# ────────────────────────────────────────────────────────────────

def _can_rotate_rectangle_in_box(rect_w: int, rect_l: int, box_w: int, box_d: int) -> bool:
    """
    직사각형(rect_w x rect_l)이 직사각형 박스(box_w x box_d) 안에서 회전 가능한지.
    1도 단위로 스캔하여 모든 각도에서 박스 안에 들어가는 각도가 있는지 검사.
    """
    if rect_w <= 0 or rect_l <= 0 or box_w <= 0 or box_d <= 0:
        return False
    for deg in range(0, 91):
        rad = math.radians(deg)
        c, s = abs(math.cos(rad)), abs(math.sin(rad))
        rotated_w = rect_w * c + rect_l * s
        rotated_d = rect_w * s + rect_l * c
        if rotated_w <= box_w and rotated_d <= box_d:
            return True
    return False


def simulate_mattress_entry(
    inner_w: int, inner_d: int, inner_h: int,
    door_w: int, door_h: int,
    mat_w: int, mat_l: int, mat_t: int,
    safety_mm: int = 50,
) -> dict:
    """
    매트리스 진입 가능 여부 판정.

    두 가지 진입 자세 검사:
      A) 눕힘 진입: 매트리스가 바닥과 평행, 두께가 수직. 출입구를 통과할 때 두께(mt)가
         수직, 짧은변이 수평이어야 함.
      B) 세움 진입: 매트리스를 세워서 두께(mt)가 진행방향 폭. 가장 일반적인 운반 방식.
         출입구 단면 = mt × (mw 또는 ml).

    Args:
        inner_w/d/h: 엘리베이터 내부 폭/깊이/높이 (mm)
        door_w/h:    출입구 폭/높이 (mm)
        mat_w/l/t:   매트리스 가로/세로/두께 (mm)
        safety_mm:   안전 여유 (각 변에 적용)
    """
    iw, id_, ih = inner_w - safety_mm, inner_d - safety_mm, inner_h - safety_mm
    dw, dh = door_w - safety_mm, door_h - safety_mm
    details: list[str] = []
    methods: list[str] = []

    short_side = min(mat_w, mat_l)
    long_side = max(mat_w, mat_l)

    # ── A) 눕힘 진입 ──────────────────────────────
    # 출입구: 두께(mt)는 출입구 높이, 짧은변은 출입구 폭
    lay_pass_door = (mat_t <= dh and short_side <= dw)
    lay_fit_inside_direct = (
        mat_t <= ih
        and ((mat_w <= iw and mat_l <= id_) or (mat_l <= iw and mat_w <= id_))
    )
    lay_fit_inside_rotated = (
        mat_t <= ih
        and _can_rotate_rectangle_in_box(mat_w, mat_l, iw, id_)
    )

    if lay_pass_door and lay_fit_inside_direct:
        methods.append("눕혀서 진입 (회전 없이)")
        details.append(f"매트리스를 바닥에 눕혀 직진 진입 가능 (두께 {mat_t}mm가 출입구 높이 {door_h}mm 안에 들어감)")
    elif lay_pass_door and lay_fit_inside_rotated:
        methods.append("눕혀서 진입 (내부 회전 필요)")
        details.append("출입구 통과 후 엘리베이터 내부에서 회전이 필요합니다.")
    else:
        if mat_t > dh:
            details.append(f"눕힘 불가: 매트리스 두께 {mat_t}mm > 출입구 높이 여유 {dh}mm")
        elif short_side > dw:
            details.append(f"눕힘 불가: 매트리스 짧은변 {short_side}mm > 출입구 폭 여유 {dw}mm")
        elif not lay_fit_inside_rotated:
            details.append(f"눕힘 불가: 매트리스가 내부 바닥({inner_w}×{inner_d}mm)에 회전해도 들어가지 않음")

    # ── B) 세움 진입 (가장 일반적) ─────────────────
    # 매트리스를 세워 두께(mt)가 출입구 폭, 한 변(mw 또는 ml)이 출입구 높이
    # 단면이 도어를 통과: mt가 도어의 짧은 쪽 ≤, 한 변이 도어의 긴 쪽 ≤
    stand_pass_door_long = (mat_t <= dw and long_side <= dh)   # 긴변 수직, 두께 수평
    stand_pass_door_short = (mat_t <= dw and short_side <= dh)  # 짧은변 수직, 두께 수평
    stand_pass_door_rot = (mat_t <= dh and short_side <= dw)    # 도어가 좁고 높을 때
    stand_pass_door = stand_pass_door_long or stand_pass_door_short or stand_pass_door_rot

    # 내부 안착: 세운 자세에서 footprint = (어느 한 변) × 두께
    # 그리고 수직 방향 = 다른 한 변이 천장 높이 안에 들어감
    stand_fit_inside_a = (
        long_side <= ih   # 긴변 수직
        and ((short_side <= iw and mat_t <= id_) or (short_side <= id_ and mat_t <= iw))
    )
    stand_fit_inside_b = (
        short_side <= ih  # 짧은변 수직 (천장 낮을 때)
        and ((long_side <= iw and mat_t <= id_) or (long_side <= id_ and mat_t <= iw))
    )
    stand_fit_inside = stand_fit_inside_a or stand_fit_inside_b

    if stand_pass_door and stand_fit_inside:
        methods.append("세워서 진입 (두께 방향으로 밀어 넣기)")
        details.append(
            f"매트리스를 세워서 두께({mat_t}mm)를 진행방향으로 밀면 진입 가능. "
            f"내부에서 한 면 = {long_side}mm가 수직."
        )
    else:
        if not stand_pass_door:
            details.append(
                f"세움 불가: 출입구({door_w}×{door_h}mm-여유)에 단면(두께 {mat_t}mm × 변 {long_side}/{short_side}mm)을 통과시킬 수 없음"
            )
        elif not stand_fit_inside:
            details.append(
                f"세움 불가: 내부({inner_w}×{inner_d}×{inner_h}mm-여유)에 세운 상태로 안착 불가. "
                f"수직 길이 최소 {short_side}mm 또는 {long_side}mm가 내부 높이 {inner_h}mm를 초과하거나 바닥 footprint가 들어가지 않음"
            )

    if methods:
        verdict = "✅ 진입 가능"
        best = methods[0]
    else:
        verdict = "❌ 진입 불가"
        best = ""

    return {
        "verdict": verdict,
        "best_method": best,
        "methods": methods,
        "details": details,
        "safety_mm": safety_mm,
    }


# ────────────────────────────────────────────────────────────────
# 시각화
# ────────────────────────────────────────────────────────────────

def _render_floor_plan(inner_w: int, inner_d: int, mat_w: int, mat_l: int) -> None:
    """엘리베이터 평면도와 매트리스 배치를 matplotlib로 시각화."""
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches
    from matplotlib.transforms import Affine2D

    fig, ax = plt.subplots(figsize=(6, 5))

    elev = patches.Rectangle(
        (0, 0), inner_w / 1000, inner_d / 1000,
        linewidth=2, edgecolor="#0ea5e9", facecolor="#f0f9ff",
    )
    ax.add_patch(elev)

    fits = False
    fit_deg = 0
    for deg in range(0, 91, 1):
        rad = math.radians(deg)
        c, s = abs(math.cos(rad)), abs(math.sin(rad))
        rw = mat_w * c + mat_l * s
        rd = mat_w * s + mat_l * c
        if rw <= inner_w and rd <= inner_d:
            fits = True
            fit_deg = deg
            break

    if fits:
        cx, cy = (inner_w / 2) / 1000, (inner_d / 2) / 1000
        mat = patches.Rectangle(
            (-mat_w / 2 / 1000, -mat_l / 2 / 1000),
            mat_w / 1000, mat_l / 1000,
            linewidth=2, edgecolor="#16a34a", facecolor="#dcfce7", alpha=0.6,
        )
        t = Affine2D().rotate_deg(fit_deg).translate(cx, cy) + ax.transData
        mat.set_transform(t)
        ax.add_patch(mat)
        ax.set_title(f"매트리스 회전 {fit_deg}°에서 평면 안착 가능", fontsize=11)
    else:
        ax.set_title(f"매트리스 {mat_w}×{mat_l}mm는 평면으로 안착 불가 — 세움 진입 검토", fontsize=10)

    bound = max(inner_w, mat_w, mat_l) / 1000
    ax.set_xlim(-0.2, bound + 0.2)
    ax.set_ylim(-0.2, bound + 0.2)
    ax.set_aspect("equal")
    ax.set_xlabel("폭 W (m)")
    ax.set_ylabel("깊이 D (m)")
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    st.pyplot(fig)
    plt.close(fig)


# ────────────────────────────────────────────────────────────────
# UI 탭
# ────────────────────────────────────────────────────────────────

def _render_search_tab() -> None:
    st.markdown("#### 주소로 승강기 스펙 조회")
    st.caption("공공데이터포털(국가승강기정보센터)에서 도로명주소·건물명으로 등록된 승강기 정보를 조회합니다.")

    if not _get_service_key():
        st.warning(
            "**API 서비스키가 설정되지 않았습니다.**\n\n"
            "1) [공공데이터포털](https://www.data.go.kr) 회원가입 → '승강기 정보' 검색 → 활용신청\n"
            "2) 발급된 **일반 인증키(Decoding)** 를 복사\n"
            "3) `.streamlit/secrets.toml`에 다음 추가:\n"
            "```toml\n[elevator_api]\nservice_key = \"발급받은_디코딩_키\"\n```\n"
            "또는 Render 환경변수 `ELEVATOR_API_KEY` 로 등록.\n\n"
            "자세한 절차는 [ELEVATOR_API_SETUP.md](ELEVATOR_API_SETUP.md) 참고."
        )

    with st.form("elev_search_form"):
        c1, c2 = st.columns(2)
        with c1:
            sido = st.text_input("시/도", placeholder="예: 울산광역시")
            road_addr = st.text_input("도로명주소", placeholder="예: 삼산로 200")
        with c2:
            sigungu = st.text_input("시/군/구", placeholder="예: 남구")
            building_name = st.text_input("건물명 (선택)", placeholder="예: 에몬스아파트")
        submitted = st.form_submit_button("🔍 검색", type="primary", width="stretch")

    if submitted:
        if not (road_addr or building_name):
            st.error("도로명주소 또는 건물명 중 하나는 입력해 주세요.")
            return
        with st.spinner("승강기 정보 조회 중..."):
            result = fetch_elevators_by_address(
                road_addr=road_addr, building_name=building_name,
                sido=sido, sigungu=sigungu, num_rows=30,
            )
        if not result["ok"]:
            st.error(f"❌ {result['error']}")
            return

        items = result["items"]
        if not items:
            st.info("조회된 승강기가 없습니다. 주소를 다시 확인해 주세요.")
            return

        df = _items_to_dataframe(items)
        st.success(f"✅ {len(df)}건 조회됨")
        st.dataframe(df, width="stretch", hide_index=True)

        # 시뮬레이션으로 보내기 버튼 (행 선택)
        st.markdown("##### 시뮬레이션에 사용할 승강기 선택")
        opts = [
            f"{i+1}. {row['승강기번호'] or '미상'} — 내부 {row['내부폭W(mm)']}×{row['내부깊이D(mm)']}×{row['내부높이H(mm)']}mm"
            for i, row in df.iterrows()
        ]
        sel = st.selectbox("승강기 선택", opts, key="elev_pick")
        if st.button("📐 시뮬레이션으로 보내기", type="primary"):
            idx = opts.index(sel)
            row = df.iloc[idx]
            st.session_state["elev_inner_w"] = int(row["내부폭W(mm)"] or 0)
            st.session_state["elev_inner_d"] = int(row["내부깊이D(mm)"] or 0)
            st.session_state["elev_inner_h"] = int(row["내부높이H(mm)"] or 0)
            st.session_state["elev_door_w"] = int(row["출입구폭(mm)"] or 0)
            st.session_state["elev_door_h"] = int(row["출입구높이(mm)"] or 0)
            st.success("✅ 시뮬레이션 탭으로 이동해서 매트리스 사이즈를 입력해 주세요.")


def _render_simulation_tab() -> None:
    st.markdown("#### 매트리스 진입 시뮬레이션")
    st.caption("엘리베이터 내부 치수와 매트리스 사이즈를 입력하면 진입 가능 여부를 판정합니다.")

    st.markdown("##### 🛗 엘리베이터 치수 (mm)")
    e1, e2, e3 = st.columns(3)
    with e1:
        inner_w = st.number_input("내부 폭 W", min_value=0, max_value=5000,
                                  value=int(st.session_state.get("elev_inner_w", 1600)), step=10)
    with e2:
        inner_d = st.number_input("내부 깊이 D", min_value=0, max_value=5000,
                                  value=int(st.session_state.get("elev_inner_d", 1500)), step=10)
    with e3:
        inner_h = st.number_input("내부 높이 H", min_value=0, max_value=5000,
                                  value=int(st.session_state.get("elev_inner_h", 2300)), step=10)
    e4, e5 = st.columns(2)
    with e4:
        door_w = st.number_input("출입구 폭", min_value=0, max_value=3000,
                                 value=int(st.session_state.get("elev_door_w", 900)), step=10)
    with e5:
        door_h = st.number_input("출입구 높이", min_value=0, max_value=3000,
                                 value=int(st.session_state.get("elev_door_h", 2100)), step=10)

    st.markdown("##### 🛏️ 매트리스 사이즈 (mm)")
    PRESETS = {
        "직접 입력": (0, 0, 0),
        "싱글 S (1000×2000×250)": (1000, 2000, 250),
        "슈퍼싱글 SS (1100×2000×250)": (1100, 2000, 250),
        "더블 D (1350×2000×250)": (1350, 2000, 250),
        "퀸 Q (1500×2000×250)": (1500, 2000, 250),
        "킹 K (1600×2000×250)": (1600, 2000, 250),
        "라지킹 LK (1800×2000×250)": (1800, 2000, 250),
    }
    preset = st.selectbox("프리셋", list(PRESETS.keys()), index=4)
    if preset != "직접 입력":
        pw, pl, pt = PRESETS[preset]
    else:
        pw, pl, pt = 1500, 2000, 250

    m1, m2, m3 = st.columns(3)
    with m1:
        mat_w = st.number_input("매트리스 가로", min_value=0, max_value=3000,
                                value=pw, step=10, key=f"mat_w_{preset}")
    with m2:
        mat_l = st.number_input("매트리스 세로", min_value=0, max_value=3000,
                                value=pl, step=10, key=f"mat_l_{preset}")
    with m3:
        mat_t = st.number_input("매트리스 두께", min_value=0, max_value=1000,
                                value=pt, step=10, key=f"mat_t_{preset}")

    safety = st.slider("안전 여유 (mm) — 각 변에 적용", 0, 200, 50, step=10,
                       help="실제 운반 시 손잡이·벽 손상 방지를 위한 여유")

    if st.button("🚪 시뮬레이션 실행", type="primary", width="stretch"):
        if min(inner_w, inner_d, inner_h, door_w, door_h, mat_w, mat_l, mat_t) <= 0:
            st.error("모든 치수에 0보다 큰 값을 입력해 주세요.")
            return

        result = simulate_mattress_entry(
            inner_w, inner_d, inner_h,
            door_w, door_h,
            mat_w, mat_l, mat_t,
            safety_mm=safety,
        )

        if "가능" in result["verdict"]:
            st.success(f"## {result['verdict']}")
            st.markdown(f"**추천 방법:** {result['best_method']}")
        else:
            st.error(f"## {result['verdict']}")

        if result["methods"]:
            st.markdown("**가능한 진입 방법:**")
            for m in result["methods"]:
                st.markdown(f"- {m}")

        with st.expander("📋 상세 판정 내역", expanded=True):
            for d in result["details"]:
                st.write(f"• {d}")

        st.markdown("##### 📐 평면도 시각화 (눕혀서 진입 시 회전 시뮬레이션)")
        try:
            _render_floor_plan(inner_w, inner_d, mat_w, mat_l)
        except Exception as e:
            st.warning(f"시각화 생성 실패: {e}")


def render_elevator_inspection() -> None:
    """엘리베이터 사이즈 점검 메인 페이지."""
    st.title("🛗 엘리베이터 사이즈 점검")
    st.caption("주소로 승강기 스펙을 조회하고, 매트리스 진입 가능 여부를 시뮬레이션합니다.")

    tab1, tab2 = st.tabs(["1️⃣ 주소로 스펙 조회", "2️⃣ 매트리스 진입 시뮬레이션"])
    with tab1:
        _render_search_tab()
    with tab2:
        _render_simulation_tab()
