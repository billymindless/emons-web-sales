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

ELEVATOR_API_BASE = "https://apis.data.go.kr/B553664/ElevatorInformationService"

# 정원(인승) → 표준 내부치수 추정 (KS B 6361 일반승객용 기준, 보편 사양)
# (W: 폭, D: 깊이, H: 높이, doorW: 출입구 폭, doorH: 출입구 높이)  단위: mm
CAPACITY_DIMENSIONS_MM: dict[int, tuple[int, int, int, int, int]] = {
    6:  (1100, 1000, 2300, 800, 2100),
    9:  (1400, 1100, 2300, 800, 2100),
    11: (1400, 1350, 2300, 900, 2100),
    13: (1600, 1350, 2300, 900, 2100),
    15: (1600, 1500, 2300, 900, 2100),
    17: (1800, 1500, 2300, 900, 2100),
    20: (1800, 1700, 2300, 1000, 2100),
    24: (2100, 1500, 2300, 1100, 2100),
    26: (2100, 1700, 2300, 1100, 2100),
}


def _estimate_dimensions(rated_capacity_persons: int) -> tuple[int, int, int, int, int]:
    """정원에 가장 가까운 표준 치수 반환 (없으면 가장 큰 값으로 외삽)."""
    if rated_capacity_persons <= 0:
        return (1600, 1500, 2300, 900, 2100)
    keys = sorted(CAPACITY_DIMENSIONS_MM.keys())
    for k in keys:
        if rated_capacity_persons <= k:
            return CAPACITY_DIMENSIONS_MM[k]
    return CAPACITY_DIMENSIONS_MM[keys[-1]]


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
    sido: str = "",
    sigungu: str = "",
    building_name: str = "",
    num_rows: int = 50,
) -> dict:
    """
    공공데이터포털 한국승강기안전공단 승강기목록 API 호출.

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
    }
    if sido:
        params["sido"] = sido
    if sigungu:
        params["sigungu"] = sigungu
    if building_name:
        params["buld_nm"] = building_name

    try:
        resp = requests.get(
            f"{ELEVATOR_API_BASE}/getElevatorListM",
            params=params,
            timeout=15.0,
        )
    except Exception as e:
        return {"ok": False, "items": [], "error": f"API 호출 실패: {e}"}

    if resp.status_code >= 400:
        return {"ok": False, "items": [],
                "error": f"API 응답 오류 {resp.status_code}: {resp.text[:200]}"}

    text = resp.text or ""
    if "SERVICE_KEY_IS_NOT_REGISTERED" in text.upper() or "SERVICE KEY IS NOT REGISTERED" in text.upper():
        return {"ok": False, "items": [],
                "error": "서비스키가 등록되지 않았습니다. 공공데이터포털에서 활용신청 후 1~2시간 대기."}

    try:
        parsed = xmltodict.parse(text)
    except Exception as e:
        return {"ok": False, "items": [], "error": f"응답 파싱 실패: {e}"}

    response = parsed.get("response") or {}
    header = response.get("header") or {}
    result_code = str(header.get("resultCode") or "")
    if result_code and result_code != "00":
        return {"ok": False, "items": [],
                "error": f"API 응답코드 {result_code}: {header.get('resultMsg', '')}"}

    body = response.get("body") or {}
    items_wrap = body.get("items") or {}
    raw_items = items_wrap.get("item") if isinstance(items_wrap, dict) else None
    if raw_items is None:
        return {"ok": True, "items": [], "error": ""}
    if isinstance(raw_items, dict):
        raw_items = [raw_items]

    return {"ok": True, "items": raw_items, "error": ""}


def _parse_int(v: Any) -> int:
    """'17 인승', '1150 KG' 등에서 숫자만 추출."""
    if v is None or v == "":
        return 0
    s = str(v)
    import re
    m = re.search(r"(\d+)", s)
    return int(m.group(1)) if m else 0


def _items_to_dataframe(items: list[dict]) -> pd.DataFrame:
    """API 응답을 보기 좋은 DataFrame으로 변환 + 정원 기반 추정 치수 추가."""
    if not items:
        return pd.DataFrame()
    rows = []
    for it in items:
        cap = _parse_int(it.get("ratedCap"))
        w, d, h, dw, dh = _estimate_dimensions(cap)
        rows.append({
            "호기": it.get("elvtrAsignNo") or "",
            "고유번호": it.get("elevatorNo") or "",
            "건물명": it.get("buldNm") or "",
            "주소": (it.get("address1") or "") + " " + (it.get("address2") or ""),
            "종류": it.get("elvtrKindNm") or "",
            "형식": it.get("elvtrFormNm") or "",
            "모델": it.get("elvtrModel") or "",
            "정원(명)": cap,
            "적재하중": it.get("liveLoad") or "",
            "추정 내부W(mm)": w,
            "추정 내부D(mm)": d,
            "추정 내부H(mm)": h,
            "추정 출입구W(mm)": dw,
            "추정 출입구H(mm)": dh,
            "운행상태": it.get("elvtrStts") or "",
            "최종검사": it.get("lastInspctDe") or "",
            "검사결과": it.get("lastResultNm") or "",
            "관리업체": it.get("mntCpnyNm") or "",
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

def _box_mesh(
    x0: float, y0: float, z0: float,
    dx: float, dy: float, dz: float,
    color: str, opacity: float = 0.5, name: str = "",
):
    """plotly Mesh3d 직육면체 생성. 좌표는 m 단위 권장."""
    import plotly.graph_objects as go
    x = [x0, x0 + dx, x0 + dx, x0, x0, x0 + dx, x0 + dx, x0]
    y = [y0, y0, y0 + dy, y0 + dy, y0, y0, y0 + dy, y0 + dy]
    z = [z0, z0, z0, z0, z0 + dz, z0 + dz, z0 + dz, z0 + dz]
    # 12개 삼각형 면 인덱스
    i = [0, 0, 0, 0, 4, 4, 1, 1, 2, 2, 3, 3]
    j = [1, 2, 4, 3, 5, 6, 2, 5, 6, 3, 7, 0]
    k = [2, 3, 7, 7, 6, 7, 6, 6, 7, 7, 4, 4]
    return go.Mesh3d(
        x=x, y=y, z=z, i=i, j=j, k=k,
        color=color, opacity=opacity, name=name,
        flatshading=True, showscale=False,
        hoverinfo="name",
    )


def _box_wireframe(
    x0: float, y0: float, z0: float,
    dx: float, dy: float, dz: float,
    color: str, name: str = "",
):
    """직육면체 12개 모서리 라인."""
    import plotly.graph_objects as go
    pts = [
        (x0, y0, z0), (x0+dx, y0, z0), (x0+dx, y0+dy, z0), (x0, y0+dy, z0),
        (x0, y0, z0+dz), (x0+dx, y0, z0+dz), (x0+dx, y0+dy, z0+dz), (x0, y0+dy, z0+dz),
    ]
    edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]
    xs, ys, zs = [], [], []
    for a, b in edges:
        xs += [pts[a][0], pts[b][0], None]
        ys += [pts[a][1], pts[b][1], None]
        zs += [pts[a][2], pts[b][2], None]
    return go.Scatter3d(
        x=xs, y=ys, z=zs, mode="lines",
        line=dict(color=color, width=4),
        name=name, hoverinfo="name", showlegend=True,
    )


def _decide_mattress_pose(
    inner_w: int, inner_d: int, inner_h: int,
    mat_w: int, mat_l: int, mat_t: int,
    safety_mm: int,
) -> dict | None:
    """
    가장 적합한 매트리스 자세 결정 후 박스 좌표 반환.

    Returns: {"dx","dy","dz","x0","y0","z0","pose","rot_deg"} (mm) 또는 None
    """
    iw, id_, ih = inner_w - safety_mm, inner_d - safety_mm, inner_h - safety_mm

    # 1) 눕힘 직진 (가로/세로 그대로)
    if mat_t <= ih and mat_w <= iw and mat_l <= id_:
        return {
            "dx": mat_w, "dy": mat_l, "dz": mat_t,
            "x0": safety_mm // 2, "y0": safety_mm // 2, "z0": 0,
            "pose": "눕힘 직진 (회전 없음)", "rot_deg": 0,
        }
    if mat_t <= ih and mat_l <= iw and mat_w <= id_:
        return {
            "dx": mat_l, "dy": mat_w, "dz": mat_t,
            "x0": safety_mm // 2, "y0": safety_mm // 2, "z0": 0,
            "pose": "눕힘 90° 회전", "rot_deg": 90,
        }
    # 2) 평면 회전 — 단순 표현 위해 박스를 대각선 위치에 표시
    for deg in range(1, 90):
        rad = math.radians(deg)
        c, s = abs(math.cos(rad)), abs(math.sin(rad))
        rw = mat_w * c + mat_l * s
        rd = mat_w * s + mat_l * c
        if mat_t <= ih and rw <= iw and rd <= id_:
            # 회전된 bounding box로 단순화
            return {
                "dx": rw, "dy": rd, "dz": mat_t,
                "x0": (inner_w - rw) / 2,
                "y0": (inner_d - rd) / 2,
                "z0": 0,
                "pose": f"눕힘 + 내부 회전 {deg}°",
                "rot_deg": deg,
            }
    # 3) 세움 — 긴변 수직, 짧은변 바닥, 두께 또 다른 바닥축
    long_side = max(mat_w, mat_l)
    short_side = min(mat_w, mat_l)
    if long_side <= ih and short_side <= iw and mat_t <= id_:
        return {
            "dx": short_side, "dy": mat_t, "dz": long_side,
            "x0": safety_mm // 2, "y0": safety_mm // 2, "z0": 0,
            "pose": f"세움 (짧은변 {short_side}mm 바닥, 긴변 {long_side}mm 수직)",
            "rot_deg": 0,
        }
    if long_side <= ih and short_side <= id_ and mat_t <= iw:
        return {
            "dx": mat_t, "dy": short_side, "dz": long_side,
            "x0": safety_mm // 2, "y0": safety_mm // 2, "z0": 0,
            "pose": f"세움 (벽면 부착, 긴변 {long_side}mm 수직)",
            "rot_deg": 0,
        }
    return None


def _render_3d_view(
    inner_w: int, inner_d: int, inner_h: int,
    mat_w: int, mat_l: int, mat_t: int,
    safety_mm: int,
) -> None:
    """엘리베이터와 매트리스를 3D 인터랙티브 뷰로 시각화."""
    import plotly.graph_objects as go

    # mm → m 변환
    iw, id_, ih = inner_w / 1000, inner_d / 1000, inner_h / 1000

    fig = go.Figure()

    # 엘리베이터 내부 박스 (와이어프레임 + 바닥)
    fig.add_trace(_box_wireframe(0, 0, 0, iw, id_, ih,
                                 color="#0284c7", name=f"엘리베이터 {inner_w}×{inner_d}×{inner_h}mm"))
    fig.add_trace(_box_mesh(0, 0, 0, iw, id_, 0.02,
                            color="#bae6fd", opacity=0.5, name="바닥"))

    # 매트리스 자세 결정
    pose = _decide_mattress_pose(inner_w, inner_d, inner_h, mat_w, mat_l, mat_t, safety_mm)

    if pose:
        dx, dy, dz = pose["dx"] / 1000, pose["dy"] / 1000, pose["dz"] / 1000
        x0, y0, z0 = pose["x0"] / 1000, pose["y0"] / 1000, pose["z0"] / 1000
        fig.add_trace(_box_mesh(x0, y0, z0, dx, dy, dz,
                                color="#22c55e", opacity=0.7,
                                name=f"매트리스 ({pose['pose']})"))
        fig.add_trace(_box_wireframe(x0, y0, z0, dx, dy, dz,
                                     color="#15803d", name="매트리스 외곽"))
        title = f"매트리스 자세: {pose['pose']}"
    else:
        title = "매트리스 진입 불가 — 빈 엘리베이터만 표시"

    fig.update_layout(
        scene=dict(
            xaxis=dict(title="폭 W (m)", range=[-0.1, iw + 0.3]),
            yaxis=dict(title="깊이 D (m)", range=[-0.1, id_ + 0.3]),
            zaxis=dict(title="높이 H (m)", range=[0, ih + 0.2]),
            aspectmode="data",
            camera=dict(eye=dict(x=1.6, y=1.6, z=1.1)),
        ),
        title=dict(text=title, x=0.5, font=dict(size=13)),
        height=520,
        margin=dict(l=0, r=0, t=40, b=0),
        legend=dict(orientation="h", y=-0.05),
    )
    st.plotly_chart(fig, width="stretch")
    st.caption("💡 마우스로 드래그하여 시점을 회전, 휠로 확대/축소할 수 있습니다.")


# ────────────────────────────────────────────────────────────────
# UI 탭
# ────────────────────────────────────────────────────────────────

def _render_search_tab() -> None:
    st.markdown("#### 주소로 승강기 스펙 조회")
    st.caption("한국승강기안전공단 공공데이터로 건물의 승강기 목록과 정원·모델을 조회합니다.")

    st.info(
        "ℹ️ **공공데이터는 정원(인승)·모델만 제공**하며 내부 치수는 포함되지 않습니다. "
        "정원 기준 **표준 KS 치수로 자동 추정**하여 표시하고, 시뮬레이션 탭에서 현장 실측치로 수정 가능합니다."
    )

    if not _get_service_key():
        st.warning(
            "**API 서비스키가 설정되지 않았습니다.**\n\n"
            "[공공데이터포털](https://www.data.go.kr) → '한국승강기안전공단_승강기 정보' 활용신청 → "
            "발급받은 **일반 인증키(Decoding)** 를 `.streamlit/secrets.toml` `[elevator_api] service_key`에 저장.\n\n"
            "자세한 절차는 [ELEVATOR_API_SETUP.md](ELEVATOR_API_SETUP.md) 참고."
        )

    with st.form("elev_search_form"):
        c1, c2, c3 = st.columns([1, 1, 2])
        with c1:
            sido = st.text_input("시/도 *", placeholder="예: 울산광역시")
        with c2:
            sigungu = st.text_input("시/군/구 *", placeholder="예: 남구")
        with c3:
            building_name = st.text_input("건물명", placeholder="예: 에몬스아파트 (부분 일치)")
        submitted = st.form_submit_button("🔍 검색", type="primary", width="stretch")

    if submitted:
        if not sido or not sigungu:
            st.error("시/도와 시/군/구는 필수입니다.")
            return
        with st.spinner("승강기 정보 조회 중..."):
            result = fetch_elevators_by_address(
                sido=sido, sigungu=sigungu, building_name=building_name, num_rows=50,
            )
        if not result["ok"]:
            st.error(f"❌ {result['error']}")
            return

        items = result["items"]
        if not items:
            st.info("조회된 승강기가 없습니다. 시/도·시/군/구를 정확히(예: '경남', '진주') 입력했는지 확인해 주세요.")
            return

        df = _items_to_dataframe(items)
        st.session_state["_elev_last_df"] = df
        st.success(f"✅ {len(df)}건 조회됨")

    # 이전 결과가 있으면 (form submit이 아닌 경우에도) 표시
    df = st.session_state.get("_elev_last_df")
    if df is None or df.empty:
        return

    st.dataframe(df, width="stretch", hide_index=True)

    st.markdown("##### 시뮬레이션에 사용할 승강기 선택")
    opts = [
        f"{i+1}. {row['건물명']} {row['호기']}호기 — {row['정원(명)']}인승, "
        f"추정 {row['추정 내부W(mm)']}×{row['추정 내부D(mm)']}×{row['추정 내부H(mm)']}mm"
        for i, row in df.iterrows()
    ]
    sel = st.selectbox("승강기 선택", opts, key="elev_pick")
    if st.button("📐 시뮬레이션으로 보내기", type="primary"):
        idx = opts.index(sel)
        row = df.iloc[idx]
        st.session_state["elev_inner_w"] = int(row["추정 내부W(mm)"] or 0)
        st.session_state["elev_inner_d"] = int(row["추정 내부D(mm)"] or 0)
        st.session_state["elev_inner_h"] = int(row["추정 내부H(mm)"] or 0)
        st.session_state["elev_door_w"] = int(row["추정 출입구W(mm)"] or 0)
        st.session_state["elev_door_h"] = int(row["추정 출입구H(mm)"] or 0)
        st.success(
            "✅ 추정 치수가 시뮬레이션 탭에 입력되었습니다. "
            "**현장 실측치가 있다면 시뮬레이션 탭에서 직접 수정해 주세요.**"
        )


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

        st.markdown("##### 🧊 3D 인터랙티브 시뮬레이션")
        try:
            _render_3d_view(inner_w, inner_d, inner_h, mat_w, mat_l, mat_t, safety)
        except Exception as e:
            st.warning(f"3D 시각화 생성 실패: {e}")


def render_elevator_inspection() -> None:
    """엘리베이터 사이즈 점검 메인 페이지."""
    st.title("🛗 엘리베이터 사이즈 점검")
    st.caption("주소로 승강기 스펙을 조회하고, 매트리스 진입 가능 여부를 시뮬레이션합니다.")

    tab1, tab2 = st.tabs(["1️⃣ 주소로 스펙 조회", "2️⃣ 매트리스 진입 시뮬레이션"])
    with tab1:
        _render_search_tab()
    with tab2:
        _render_simulation_tab()
