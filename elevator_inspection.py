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

# 정원(인승) → 적재중량(kg)  KS B 6361 기준 (1인 = 75kg 환산)
CAPACITY_TO_KG: dict[int, int] = {
    6: 450, 9: 600, 11: 750, 13: 900, 15: 1000,
    17: 1150, 20: 1350, 24: 1600, 26: 1700,
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


def _estimate_load_kg(rated_capacity_persons: int) -> int:
    """정원에 해당하는 표준 적재중량(kg) 추정."""
    if rated_capacity_persons <= 0:
        return 0
    keys = sorted(CAPACITY_TO_KG.keys())
    for k in keys:
        if rated_capacity_persons <= k:
            return CAPACITY_TO_KG[k]
    return CAPACITY_TO_KG[keys[-1]]


# 행정구역 정적 데이터 (행정안전부 2024년 기준)
SIDO_LIST: list[str] = [
    "서울특별시", "부산광역시", "대구광역시", "인천광역시",
    "광주광역시", "대전광역시", "울산광역시", "세종특별자치시",
    "경기도", "강원특별자치도", "충청북도", "충청남도",
    "전북특별자치도", "전라남도", "경상북도", "경상남도",
    "제주특별자치도",
]

SIDO_TO_SIGUNGU: dict[str, list[str]] = {
    "서울특별시": [
        "종로구", "중구", "용산구", "성동구", "광진구", "동대문구",
        "중랑구", "성북구", "강북구", "도봉구", "노원구", "은평구",
        "서대문구", "마포구", "양천구", "강서구", "구로구", "금천구",
        "영등포구", "동작구", "관악구", "서초구", "강남구", "송파구", "강동구",
    ],
    "부산광역시": [
        "중구", "서구", "동구", "영도구", "부산진구", "동래구", "남구",
        "북구", "해운대구", "사하구", "금정구", "강서구", "연제구",
        "수영구", "사상구", "기장군",
    ],
    "대구광역시": [
        "중구", "동구", "서구", "남구", "북구", "수성구", "달서구",
        "달성군", "군위군",
    ],
    "인천광역시": [
        "중구", "동구", "미추홀구", "연수구", "남동구", "부평구",
        "계양구", "서구", "강화군", "옹진군",
    ],
    "광주광역시": ["동구", "서구", "남구", "북구", "광산구"],
    "대전광역시": ["동구", "중구", "서구", "유성구", "대덕구"],
    "울산광역시": ["중구", "남구", "동구", "북구", "울주군"],
    "세종특별자치시": ["세종특별자치시"],
    "경기도": [
        "수원시", "성남시", "고양시", "용인시", "부천시", "안산시", "안양시",
        "남양주시", "화성시", "평택시", "의정부시", "시흥시", "파주시", "광명시",
        "김포시", "광주시", "군포시", "오산시", "이천시", "양주시", "안성시",
        "구리시", "포천시", "의왕시", "하남시", "여주시", "동두천시", "과천시",
        "가평군", "양평군", "연천군",
    ],
    "강원특별자치도": [
        "춘천시", "원주시", "강릉시", "동해시", "태백시", "속초시", "삼척시",
        "홍천군", "횡성군", "영월군", "평창군", "정선군", "철원군", "화천군",
        "양구군", "인제군", "고성군", "양양군",
    ],
    "충청북도": [
        "청주시", "충주시", "제천시",
        "보은군", "옥천군", "영동군", "증평군", "진천군",
        "괴산군", "음성군", "단양군",
    ],
    "충청남도": [
        "천안시", "공주시", "보령시", "아산시", "서산시", "논산시", "계룡시", "당진시",
        "금산군", "부여군", "서천군", "청양군", "홍성군", "예산군", "태안군",
    ],
    "전북특별자치도": [
        "전주시", "군산시", "익산시", "정읍시", "남원시", "김제시",
        "완주군", "진안군", "무주군", "장수군", "임실군",
        "순창군", "고창군", "부안군",
    ],
    "전라남도": [
        "목포시", "여수시", "순천시", "나주시", "광양시",
        "담양군", "곡성군", "구례군", "고흥군", "보성군", "화순군",
        "장흥군", "강진군", "해남군", "영암군", "무안군",
        "함평군", "영광군", "장성군", "완도군", "진도군", "신안군",
    ],
    "경상북도": [
        "포항시", "경주시", "김천시", "안동시", "구미시", "영주시", "영천시",
        "상주시", "문경시", "경산시",
        "의성군", "청송군", "영양군", "영덕군",
        "청도군", "고령군", "성주군", "칠곡군",
        "예천군", "봉화군", "울진군", "울릉군",
    ],
    "경상남도": [
        "창원시", "진주시", "통영시", "사천시", "김해시", "밀양시", "거제시", "양산시",
        "의령군", "함안군", "창녕군", "고성군", "남해군", "하동군",
        "산청군", "함양군", "거창군", "합천군",
    ],
    "제주특별자치도": ["제주시", "서귀포시"],
}


_ELEVATOR_ENV_CANDIDATES: tuple[str, ...] = (
    "ELEVATOR_API_KEY",
    "ELEVATOR_SERVICE_KEY",
    "DATA_GO_KR_SERVICE_KEY",
)


def _get_service_key_diagnostic() -> tuple[str, dict[str, str]]:
    """
    승강기 API 서비스 키를 3중 폴백으로 로드하고 진단 정보 동시 반환.

    로드 순서 (뒤쪽이 높은 우선순위로 덮어씀):
      1. secrets.toml 직접 읽기 (Path 기반, 로컬 개발 환경 확인)
      2. st.secrets[elevator_api] (Streamlit Cloud/로컬 secrets)
      3. 환경변수 ELEVATOR_API_KEY 등 (Render·Docker 등 배포)

    Returns:
        (key, diag) — diag는 어느 소스에서 무엇이 발견됐는지의 상세 정보
    """
    diag: dict[str, str] = {
        "secrets_toml_path": "",
        "secrets_toml_found": "no",
        "st_secrets_found": "no",
        "env_var_found": "no",
        "env_var_name": "",
        "final_source": "none",
        "key_len": "0",
    }
    key = ""

    # 1) secrets.toml 직접 읽기
    try:
        import tomllib
        from pathlib import Path
        for _p in [
            Path(__file__).parent / ".streamlit" / "secrets.toml",
            Path.cwd() / ".streamlit" / "secrets.toml",
        ]:
            if _p.exists():
                diag["secrets_toml_path"] = str(_p)
                with open(_p, "rb") as _f:
                    _data = tomllib.load(_f)
                _v = str((_data.get("elevator_api") or {}).get("service_key", "") or "").strip()
                if _v:
                    key = _v
                    diag["secrets_toml_found"] = "yes"
                    diag["final_source"] = "secrets.toml"
                break
    except Exception as e:
        diag["secrets_toml_path"] = f"error: {type(e).__name__}"

    # 2) st.secrets
    try:
        if hasattr(st, "secrets"):
            _sec = st.secrets.get("elevator_api", {})
            _v = str(_sec.get("service_key", "") or "").strip()
            if _v:
                key = _v
                diag["st_secrets_found"] = "yes"
                diag["final_source"] = "st.secrets"
    except Exception:
        pass

    # 3) 환경변수 (최우선)
    for _name in _ELEVATOR_ENV_CANDIDATES:
        _v = (os.environ.get(_name, "") or "").strip()
        if _v:
            key = _v
            diag["env_var_found"] = "yes"
            diag["env_var_name"] = _name
            diag["final_source"] = f"env:{_name}"
            break

    diag["key_len"] = str(len(key))
    return key, diag


def _get_service_key() -> str:
    """승강기 API 서비스 키 (호환 유지용 단순 래퍼)."""
    key, _ = _get_service_key_diagnostic()
    return key


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
    """API 응답을 보기 좋은 DataFrame으로 변환. 정원/적재중량만 표시."""
    if not items:
        return pd.DataFrame()
    rows = []
    for it in items:
        cap = _parse_int(it.get("ratedCap"))
        load_raw = _parse_int(it.get("liveLoad"))
        load_kg = load_raw if load_raw > 0 else _estimate_load_kg(cap)
        rows.append({
            "호기": it.get("elvtrAsignNo") or "",
            "고유번호": it.get("elevatorNo") or "",
            "건물명": it.get("buldNm") or "",
            "주소": (it.get("address1") or "") + " " + (it.get("address2") or ""),
            "종류": it.get("elvtrKindNm") or "",
            "형식": it.get("elvtrFormNm") or "",
            "모델": it.get("elvtrModel") or "",
            "정원(명)": cap,
            "적재중량(kg)": load_kg,
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
    """직사각형(rect_w × rect_l)이 박스(box_w × box_d) 안에 들어가는 각도가 있는지."""
    if rect_w <= 0 or rect_l <= 0 or box_w <= 0 or box_d <= 0:
        return False
    for deg in range(0, 91):
        rad = math.radians(deg)
        c, s = abs(math.cos(rad)), abs(math.sin(rad))
        if rect_w * c + rect_l * s <= box_w and rect_w * s + rect_l * c <= box_d:
            return True
    return False


def _diagonal_entry_check(
    iw: int, id_: int, ih: int,
    dw: int, dh: int,
    mat_w: int, mat_l: int, mat_t: int,
) -> dict | None:
    """
    매트리스를 θ도 기울인 자세로 진입 가능한지 검사.

    매트리스의 세 변(mat_w, mat_l, mat_t) 중 어느 두 변이 X-Z 평면에서 회전하는지
    모든 순열(6가지)을 시도하여 가장 평평한(작은 θ) 자세를 찾음.

    축 정의 (수평 = Y, 깊이 = X, 수직 = Z):
        axis_w (Y축, 출입구 폭 방향)
        axis_d (X축, 기울이는 축의 한 변 — θ=0일 때 깊이 방향)
        axis_t (Z축, 기울이는 축의 다른 변 — θ=0일 때 수직 방향)

    제약:
        출입구 통과: axis_w ≤ dw, 단면 높이 ≤ dh
        내부 수직:   단면 높이 ≤ ih
        내부 footprint(axis_w × occupy_d)는 (iw × id_)에 회전 후 안착 가능
    """
    if min(iw, id_, ih, dw, dh, mat_w, mat_l, mat_t) <= 0:
        return None

    from itertools import permutations
    dims = (mat_w, mat_l, mat_t)
    best: dict | None = None

    for axis_w, axis_d, axis_t in permutations(dims):
        if axis_w > dw:
            continue
        for deg in range(0, 91):
            rad = math.radians(deg)
            s, c = math.sin(rad), math.cos(rad)
            cross_h = axis_d * s + axis_t * c
            occupy_d = axis_d * c + axis_t * s
            if cross_h > dh or cross_h > ih:
                continue
            # 내부 안착: (axis_w × occupy_d) footprint가 (iw × id_)에 회전(평면 대각선) 가능
            if not _can_rotate_rectangle_in_box(int(axis_w), int(occupy_d), iw, id_):
                continue
            # 내부에서 어떤 평면 회전(평면 대각선)이 필요한지 계산
            floor_rot = _find_min_rotation(int(axis_w), int(occupy_d), iw, id_)
            cand = {
                "angle": deg,
                "axis_w": axis_w,
                "axis_d": axis_d,
                "axis_t": axis_t,
                "cross_h": cross_h,
                "occupy_d": occupy_d,
                "floor_rot_deg": floor_rot,
            }
            if best is None or cand["angle"] < best["angle"]:
                best = cand
            break
    return best


def _find_min_rotation(rect_w: int, rect_l: int, box_w: int, box_d: int) -> int:
    """직사각형이 박스에 들어가는 최소 회전 각도(0~90°). 들어가지 않으면 -1."""
    for deg in range(0, 91):
        rad = math.radians(deg)
        c, s = abs(math.cos(rad)), abs(math.sin(rad))
        if rect_w * c + rect_l * s <= box_w and rect_w * s + rect_l * c <= box_d:
            return deg
    return -1


def _check_specific_angle(
    iw: int, id_: int, ih: int, dw: int, dh: int,
    mat_w: int, mat_l: int, mat_t: int, angle: int,
) -> dict | None:
    """특정 각도에서 진입 가능한 매트리스 자세가 있는지 검사."""
    if min(iw, id_, ih, dw, dh, mat_w, mat_l, mat_t) <= 0:
        return None
    from itertools import permutations
    rad = math.radians(angle)
    s, c = math.sin(rad), math.cos(rad)
    for axis_w, axis_d, axis_t in permutations((mat_w, mat_l, mat_t)):
        if axis_w > dw:
            continue
        cross_h = axis_d * s + axis_t * c
        occupy_d = axis_d * c + axis_t * s
        if cross_h > dh or cross_h > ih:
            continue
        if (axis_w <= iw and occupy_d <= id_) or (axis_w <= id_ and occupy_d <= iw):
            return {"axis_w": axis_w, "axis_d": axis_d, "axis_t": axis_t,
                    "cross_h": cross_h, "occupy_d": occupy_d}
    return None


def _classify_pose(angle: int, axis_w: int, axis_d: int, axis_t: int,
                   cross_h: float, occupy_d: float, mat_t: int,
                   floor_rot: int = 0) -> tuple[str, str]:
    """
    (angle, axis_*, mat_t, floor_rot) 조합으로 자세 종류와 사람이 읽을 라벨 반환.
    Returns: (kind, label)  kind ∈ {"flat", "tilted", "standing"}
    """
    if 0 < angle < 90:
        kind = "tilted"
        label = (
            f"대각선 기울임 진입 ({angle}°) — 폭 {axis_w}mm, "
            f"수직 단면 {cross_h:.0f}mm, 바닥 깊이 {occupy_d:.0f}mm"
        )
    elif angle == 0 and axis_t == mat_t:
        kind = "flat"
        label = f"눕혀서 진입 (평평) — 폭 {axis_w}mm, 깊이 {axis_d}mm, 수직 = 두께 {mat_t}mm"
    elif angle == 90 and axis_d == mat_t:
        kind = "flat"
        label = f"눕혀서 진입 (회전 후) — 폭 {axis_w}mm, 깊이 {axis_t}mm, 수직 = 두께 {mat_t}mm"
    else:
        kind = "standing"
        vert = axis_t if angle == 0 else axis_d
        label = f"세워서 진입 — 폭 {axis_w}mm, 수직 {vert}mm"
    if floor_rot > 0:
        label += f" → 내부에서 평면 대각선 {floor_rot}° 회전 안착"
    return kind, label


def simulate_mattress_entry(
    inner_w: int, inner_d: int, inner_h: int,
    door_w: int, door_h: int,
    mat_w: int, mat_l: int, mat_t: int,
    safety_mm: int = 50,
) -> dict:
    """
    매트리스 진입 가능 여부 판정.

    _diagonal_entry_check가 매트리스 6가지 자세 순열 × 0~90° 각도 스캔으로 통합 검사.
    가장 평평한(작은 θ) 자세가 가능하면 진입 가능으로 판정.

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

    diag = _diagonal_entry_check(iw, id_, ih, dw, dh, mat_w, mat_l, mat_t)

    if diag:
        kind, label = _classify_pose(
            diag["angle"], diag["axis_w"], diag["axis_d"], diag["axis_t"],
            diag["cross_h"], diag["occupy_d"], mat_t,
            floor_rot=diag.get("floor_rot_deg", 0),
        )
        methods.append(label)
        # 다른 자세도 가능한지 보조 검사 (사용자가 옵션 비교용)
        for try_angle, try_label in [(0, "눕힘"), (45, "대각선 45°"), (90, "완전 세움")]:
            if try_angle == diag["angle"]:
                continue
            _alt = _check_specific_angle(iw, id_, ih, dw, dh,
                                         mat_w, mat_l, mat_t, try_angle)
            if _alt:
                details.append(f"• {try_label} 자세도 가능 (대안)")
        details.append(
            f"가장 안전한 자세: 매트리스 폭 {diag['axis_w']}mm가 출입구 폭 방향, "
            f"기울임 각도 {diag['angle']}°."
        )
        verdict = "✅ 진입 가능"
        best = label
    else:
        verdict = "❌ 진입 불가"
        best = ""
        # 어떤 제약이 걸렸는지 진단
        details.append(f"엘리베이터 내부({inner_w}×{inner_d}×{inner_h}mm) - 여유 {safety_mm}mm")
        details.append(f"출입구({door_w}×{door_h}mm) - 여유 {safety_mm}mm")
        details.append(f"매트리스({mat_w}×{mat_l}×{mat_t}mm)")
        min_side = min(mat_w, mat_l, mat_t)
        if min_side > dw:
            details.append(
                f"⚠ 매트리스의 가장 짧은 변 {min_side}mm가 출입구 여유 폭 {dw}mm를 초과 → 통과 불가"
            )
        else:
            details.append(
                f"⚠ 매트리스를 어떤 각도로 기울여도 출입구 높이({dh}mm) 또는 "
                f"내부 깊이({id_}mm)에 들어가지 않음"
            )

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
    door_w: int, door_h: int,
    mat_w: int, mat_l: int, mat_t: int,
    safety_mm: int,
) -> dict | None:
    """
    가장 안전한 운반 자세 결정 후 3D 시각화용 박스 좌표 반환.
    _diagonal_entry_check가 통합적으로 모든 자세를 검사하여 가장 평평한(작은 θ) 자세를 반환.

    Returns dict (모두 mm 단위):
        type: 'flat' (θ=0) | 'tilted' (0<θ<90) | 'standing' (θ=90)
        x0,y0,z0: 회전 피벗(바닥 앞쪽 한 모서리) 시작 좌표
        axis_w/axis_d/axis_t: 회전 전 박스의 Y/X/Z 길이
        tilt_deg: 기울임 각도
        pose: 사람이 읽을 설명
    """
    iw = inner_w - safety_mm
    id_ = inner_d - safety_mm
    ih = inner_h - safety_mm
    dw = door_w - safety_mm
    dh = door_h - safety_mm

    diag = _diagonal_entry_check(iw, id_, ih, dw, dh, mat_w, mat_l, mat_t)
    if not diag:
        # 마지막 시도: 평면 회전(눕힘 자세에서 수평으로 회전)
        for deg in range(1, 90):
            rad = math.radians(deg)
            c, s = abs(math.cos(rad)), abs(math.sin(rad))
            rw = mat_w * c + mat_l * s
            rd = mat_w * s + mat_l * c
            if mat_t <= ih and rw <= iw and rd <= id_:
                return {
                    "type": "flat",
                    "axis_w": rw, "axis_d": rd, "axis_t": mat_t,
                    "x0": safety_mm // 2,
                    "y0": (inner_d - rw) / 2,
                    "z0": 0,
                    "tilt_deg": 0,
                    "pose": f"눕힘 + 평면 회전 {deg}° (수평 회전)",
                }
        return None

    angle = diag["angle"]
    axis_w = diag["axis_w"]
    axis_d = diag["axis_d"]
    axis_t = diag["axis_t"]
    occupy_d = diag["occupy_d"]
    cross_h = diag["cross_h"]
    floor_rot = diag.get("floor_rot_deg", 0)

    if angle == 0 and axis_t == mat_t:
        ptype = "flat"
        pose_label = f"눕힘 (평평) — 폭 {axis_w}mm, 깊이 {axis_d}mm, 두께 {mat_t}mm가 수직"
    elif angle == 90 and axis_d == mat_t:
        ptype = "flat"
        pose_label = f"눕힘 (회전 후) — 폭 {axis_w}mm, 두께 {mat_t}mm가 수직"
    elif 0 < angle < 90:
        ptype = "tilted"
        pose_label = (
            f"대각선 기울임 {angle}° — 폭 {axis_w}mm, "
            f"수직단면 {cross_h:.0f}mm, 바닥깊이 {occupy_d:.0f}mm"
        )
    else:
        ptype = "standing"
        vert = axis_t if angle == 0 else axis_d
        pose_label = f"세움 ({vert}mm 수직, 폭 {axis_w}mm, 깊이 {occupy_d:.0f}mm)"

    if floor_rot > 0:
        pose_label += f" + 내부 평면 대각선 {floor_rot}°"

    return {
        "type": ptype,
        "axis_w": axis_w,
        "axis_d": axis_d,
        "axis_t": axis_t,
        "tilt_deg": angle,
        "floor_rot_deg": floor_rot,
        "occupy_d": occupy_d,
        "cross_h": cross_h,
        "x0": safety_mm // 2,
        "y0": (inner_d - axis_w) / 2,
        "z0": 0,
        "pose": pose_label,
    }


def _tilted_box_traces(
    pivot_x: float, pivot_y: float, pivot_z: float,
    axis_w: float, axis_l: float, axis_t: float,
    tilt_deg: float, floor_rot_deg: float = 0.0,
    color_fill: str = "#22c55e", color_edge: str = "#15803d",
    name: str = "매트리스",
):
    """
    1) Y축 기준 tilt_deg 회전 (수직 기울임)
    2) Z축 기준 floor_rot_deg 회전 (평면 대각선)
    이후 회전 결과를 X≥0, Z≥0 으로 자동 정렬한 뒤 pivot 위치로 평행이동.
    """
    import numpy as np
    import plotly.graph_objects as go

    rad_t = math.radians(tilt_deg)
    rad_f = math.radians(floor_rot_deg)
    ct, st_ = math.cos(rad_t), math.sin(rad_t)
    cf, sf = math.cos(rad_f), math.sin(rad_f)

    local = np.array([
        [0, 0, 0], [axis_l, 0, 0], [axis_l, axis_w, 0], [0, axis_w, 0],
        [0, 0, axis_t], [axis_l, 0, axis_t], [axis_l, axis_w, axis_t], [0, axis_w, axis_t],
    ])
    # 1) Y축 회전
    after_tilt = np.column_stack([
        local[:, 0] * ct - local[:, 2] * st_,
        local[:, 1],
        local[:, 0] * st_ + local[:, 2] * ct,
    ])
    # 2) Z축 회전 (평면)
    after_floor = np.column_stack([
        after_tilt[:, 0] * cf - after_tilt[:, 1] * sf,
        after_tilt[:, 0] * sf + after_tilt[:, 1] * cf,
        after_tilt[:, 2],
    ])
    rotated = after_floor
    rotated[:, 0] -= rotated[:, 0].min()
    rotated[:, 1] -= rotated[:, 1].min()
    rotated[:, 2] -= rotated[:, 2].min()
    rotated[:, 0] += pivot_x
    rotated[:, 1] += pivot_y
    rotated[:, 2] += pivot_z

    x, y, z = rotated[:, 0], rotated[:, 1], rotated[:, 2]
    i = [0, 0, 0, 0, 4, 4, 1, 1, 2, 2, 3, 3]
    j = [1, 2, 4, 3, 5, 6, 2, 5, 6, 3, 7, 0]
    k = [2, 3, 7, 7, 6, 7, 6, 6, 7, 7, 4, 4]
    mesh = go.Mesh3d(
        x=x, y=y, z=z, i=i, j=j, k=k,
        color=color_fill, opacity=0.65, name=name,
        flatshading=True, showscale=False, hoverinfo="name",
    )
    # 와이어프레임
    edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]
    xs, ys, zs = [], [], []
    for a, b in edges:
        xs += [x[a], x[b], None]
        ys += [y[a], y[b], None]
        zs += [z[a], z[b], None]
    wire = go.Scatter3d(
        x=xs, y=ys, z=zs, mode="lines",
        line=dict(color=color_edge, width=5),
        name=f"{name} 외곽", hoverinfo="name", showlegend=False,
    )
    return mesh, wire


def _render_3d_view(
    inner_w: int, inner_d: int, inner_h: int,
    door_w: int, door_h: int,
    mat_w: int, mat_l: int, mat_t: int,
    safety_mm: int,
) -> None:
    """엘리베이터와 매트리스를 3D 인터랙티브 뷰로 시각화 (대각선 기울임 자세 지원)."""
    import plotly.graph_objects as go

    iw, id_, ih = inner_w / 1000, inner_d / 1000, inner_h / 1000
    dw_m, dh_m = door_w / 1000, door_h / 1000

    fig = go.Figure()
    # 엘리베이터 외곽
    fig.add_trace(_box_wireframe(0, 0, 0, iw, id_, ih,
                                 color="#0284c7",
                                 name=f"엘리베이터 {inner_w}×{inner_d}×{inner_h}mm"))
    fig.add_trace(_box_mesh(0, 0, 0, iw, id_, 0.02,
                            color="#bae6fd", opacity=0.5, name="바닥"))
    # 출입구 표시 (X=0 면, 중앙)
    door_y0 = max((id_ - dw_m) / 2, 0)
    door_corners_y = [door_y0, door_y0 + dw_m, door_y0 + dw_m, door_y0, door_y0]
    door_corners_z = [0, 0, dh_m, dh_m, 0]
    fig.add_trace(go.Scatter3d(
        x=[0] * 5, y=door_corners_y, z=door_corners_z,
        mode="lines", line=dict(color="#f97316", width=6),
        name=f"출입구 {door_w}×{door_h}mm",
    ))

    pose = _decide_mattress_pose(inner_w, inner_d, inner_h, door_w, door_h,
                                  mat_w, mat_l, mat_t, safety_mm)

    if pose:
        # 매트리스를 출입구 근처에 배치 (X=0 면 인접)
        mesh, wire = _tilted_box_traces(
            pivot_x=0.05,
            pivot_y=max((id_ - pose["axis_w"] / 1000) / 2, 0.05),
            pivot_z=0,
            axis_w=pose["axis_w"] / 1000,
            axis_l=pose["axis_d"] / 1000,
            axis_t=pose["axis_t"] / 1000,
            tilt_deg=pose["tilt_deg"],
            floor_rot_deg=pose.get("floor_rot_deg", 0),
            color_fill="#22c55e", color_edge="#15803d",
            name=f"매트리스 ({pose['pose']})",
        )
        fig.add_trace(mesh)
        fig.add_trace(wire)
        title = f"매트리스 자세: {pose['pose']}"
    else:
        title = "매트리스 진입 불가 — 빈 엘리베이터만 표시"

    fig.update_layout(
        scene=dict(
            xaxis=dict(title="깊이 D (m)", range=[-0.1, iw + 0.3]),
            yaxis=dict(title="폭 W (m)", range=[-0.1, id_ + 0.3]),
            zaxis=dict(title="높이 H (m)", range=[0, ih + 0.3]),
            aspectmode="data",
            camera=dict(eye=dict(x=1.8, y=1.8, z=1.2)),
        ),
        title=dict(text=title, x=0.5, font=dict(size=13)),
        height=540,
        margin=dict(l=0, r=0, t=40, b=0),
        legend=dict(orientation="h", y=-0.05),
    )
    st.plotly_chart(fig, width="stretch")
    st.caption("💡 마우스로 드래그하여 시점을 회전, 휠로 확대/축소할 수 있습니다. 주황색 선은 출입구입니다.")


# ────────────────────────────────────────────────────────────────
# UI 탭
# ────────────────────────────────────────────────────────────────

def _render_simulation_section(key_prefix: str) -> None:
    """매트리스 진입 시뮬레이션 UI (검색 탭 + 단독 탭에서 재사용).

    Widget key를 key_prefix로 분리하여 두 곳에서 호출해도 충돌하지 않도록 함.
    검색 결과로 자동 입력하려면 session_state["{key_prefix}_inner_w"] 등 widget key를 미리 채움.
    """
    # 초기 default 설정 (widget key가 아직 없을 때만)
    _defaults = {
        f"{key_prefix}_inner_w": 1600,
        f"{key_prefix}_inner_d": 1500,
        f"{key_prefix}_inner_h": 2300,
        f"{key_prefix}_door_w": 900,
        f"{key_prefix}_door_h": 2100,
    }
    for k, v in _defaults.items():
        st.session_state.setdefault(k, v)

    st.markdown("##### 🛗 엘리베이터 치수 (mm) — 직접 입력 또는 검색 결과에서 자동 채움")
    e1, e2, e3 = st.columns(3)
    with e1:
        inner_w = st.number_input(
            "내부 폭 W", min_value=0, max_value=5000, step=10,
            key=f"{key_prefix}_inner_w",
        )
    with e2:
        inner_d = st.number_input(
            "내부 깊이 D", min_value=0, max_value=5000, step=10,
            key=f"{key_prefix}_inner_d",
        )
    with e3:
        inner_h = st.number_input(
            "내부 높이 H", min_value=0, max_value=5000, step=10,
            key=f"{key_prefix}_inner_h",
        )
    e4, e5 = st.columns(2)
    with e4:
        door_w = st.number_input(
            "출입구 폭", min_value=0, max_value=3000, step=10,
            key=f"{key_prefix}_door_w",
        )
    with e5:
        door_h = st.number_input(
            "출입구 높이", min_value=0, max_value=3000, step=10,
            key=f"{key_prefix}_door_h",
        )

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
    preset = st.selectbox(
        "프리셋", list(PRESETS.keys()), index=4, key=f"{key_prefix}_preset",
    )
    if preset != "직접 입력":
        pw, pl, pt = PRESETS[preset]
    else:
        pw, pl, pt = 1500, 2000, 250

    m1, m2, m3 = st.columns(3)
    with m1:
        mat_w = st.number_input(
            "매트리스 가로", min_value=0, max_value=3000, step=10,
            value=pw, key=f"{key_prefix}_mat_w_{preset}",
        )
    with m2:
        mat_l = st.number_input(
            "매트리스 세로", min_value=0, max_value=3000, step=10,
            value=pl, key=f"{key_prefix}_mat_l_{preset}",
        )
    with m3:
        mat_t = st.number_input(
            "매트리스 두께", min_value=0, max_value=1000, step=10,
            value=pt, key=f"{key_prefix}_mat_t_{preset}",
        )

    safety = st.slider(
        "안전 여유 (mm) — 각 변에 적용", 0, 200, 50, step=10,
        help="실제 운반 시 손잡이·벽 손상 방지를 위한 여유",
        key=f"{key_prefix}_safety",
    )

    if st.button("🚪 시뮬레이션 실행", type="primary", width="stretch",
                 key=f"{key_prefix}_run"):
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
            _render_3d_view(inner_w, inner_d, inner_h, door_w, door_h,
                            mat_w, mat_l, mat_t, safety)
        except Exception as e:
            st.warning(f"3D 시각화 생성 실패: {e}")


def _render_search_tab() -> None:
    st.markdown("#### 주소로 승강기 스펙 조회")
    st.caption("한국승강기안전공단 공공데이터로 건물의 승강기 목록과 정원·적재중량을 조회합니다.")

    st.info(
        "ℹ️ **공공데이터는 정원(인승)·모델만 제공**하며 내부 치수는 포함되지 않습니다. "
        "정원 기준 적재중량(kg)을 함께 표시하며, 아래 **시뮬레이션 섹션에서 직접 수치를 입력**해 매트리스 진입 가능 여부를 확인할 수 있습니다."
    )

    _svc_key, _key_diag = _get_service_key_diagnostic()
    if not _svc_key:
        st.warning(
            "**API 서비스키가 설정되지 않았습니다.**\n\n"
            "**Streamlit Community Cloud (현재 배포 환경)**: "
            "[share.streamlit.io](https://share.streamlit.io) → 본인 앱 → ⋮ → **Settings** → **Secrets** 탭에 "
            "아래 TOML을 붙여넣고 Save:\n"
            "```toml\n"
            "[elevator_api]\n"
            "service_key = \"공공데이터포털_일반인증키_Decoding값\"\n"
            "```\n"
            "**로컬 개발**: `.streamlit/secrets.toml` 에 위와 동일한 내용 작성.\n\n"
            "※ Render에 환경변수를 추가해도 Streamlit 앱(여기)에는 적용되지 않습니다. "
            "Render는 `api.py` 웹훅 서버 전용입니다."
        )
        with st.expander("🔍 진단 정보 보기 (어디서 키를 찾으려 했는지)"):
            st.code(
                f"secrets.toml 경로     : {_key_diag.get('secrets_toml_path') or '(찾지 못함)'}\n"
                f"secrets.toml 키 발견  : {_key_diag.get('secrets_toml_found')}\n"
                f"st.secrets 키 발견    : {_key_diag.get('st_secrets_found')}\n"
                f"환경변수 키 발견      : {_key_diag.get('env_var_found')} "
                f"(이름: {_key_diag.get('env_var_name') or '-'})\n"
                f"시도한 환경변수 이름  : {', '.join(_ELEVATOR_ENV_CANDIDATES)}\n"
                f"최종 소스             : {_key_diag.get('final_source')}\n"
                f"키 길이               : {_key_diag.get('key_len')}",
                language="text",
            )
            st.markdown(
                "**Streamlit Cloud에서 Secrets 설정 방법**\n"
                "1. [share.streamlit.io](https://share.streamlit.io) 로그인 → 본인 앱 선택\n"
                "2. 우측 상단 또는 하단의 **⋮** 메뉴 → **Settings**\n"
                "3. **Secrets** 탭 선택 → 위 TOML 형식 그대로 붙여넣기\n"
                "4. **Save** → 앱이 자동 재시작 (수 초 ~ 1분)\n\n"
                "**※ 환경변수가 아닌 TOML 형식으로 입력**해야 `st.secrets[\"elevator_api\"][\"service_key\"]`로 읽힙니다."
            )
    else:
        st.success(
            f"✅ API 서비스키 로드 완료 (소스: `{_key_diag.get('final_source')}`, 길이: {_key_diag.get('key_len')}자)"
        )

    c1, c2 = st.columns(2)
    with c1:
        sido = st.selectbox("시/도 *", SIDO_LIST, index=0, key="elev_sido")
    with c2:
        sigungu = st.selectbox(
            "시/군/구 *", SIDO_TO_SIGUNGU.get(sido, []), index=0, key="elev_sigungu",
        )
    c3, c4 = st.columns(2)
    with c3:
        road_name = st.text_input("도로명", placeholder="예: 에듀시티로 (부분 일치 필터)",
                                  key="elev_road")
    with c4:
        building_name = st.text_input("건물명", placeholder="예: 에듀시티로102 (부분 일치)",
                                      key="elev_buld")

    if st.button("🔍 검색", type="primary", width="stretch", key="elev_search_btn"):
        with st.spinner("승강기 정보 조회 중..."):
            result = fetch_elevators_by_address(
                sido=sido, sigungu=sigungu, building_name=building_name, num_rows=100,
            )
        if not result["ok"]:
            st.error(f"❌ {result['error']}")
            return
        items = result["items"]
        # 도로명 부분일치 클라이언트 필터 (API는 도로명 파라미터 미지원)
        if road_name and items:
            rn = road_name.strip()
            items = [
                it for it in items
                if rn in (str(it.get("address1") or "") + str(it.get("address2") or ""))
            ]
        if not items:
            st.info("조회된 승강기가 없습니다. 시/도·시/군/구·도로명을 다시 확인해 주세요.")
            st.session_state["_elev_last_df"] = pd.DataFrame()
            return
        df = _items_to_dataframe(items)
        st.session_state["_elev_last_df"] = df
        st.session_state["_elev_last_items"] = items
        st.success(f"✅ {len(df)}건 조회됨")

    df = st.session_state.get("_elev_last_df")
    if df is not None and not df.empty:
        st.markdown("##### 📋 조회 결과")
        st.dataframe(df, width="stretch", hide_index=True)

        st.markdown("##### 🎯 시뮬레이션에 사용할 승강기 선택")
        opts = [
            f"{i+1}. {row['건물명']} {row['호기']}호기 — "
            f"{row['정원(명)']}인승 / {row['적재중량(kg)']}kg"
            for i, row in df.iterrows()
        ]
        sel = st.selectbox("승강기 선택", opts, key="elev_pick")
        if st.button("📐 이 승강기 치수를 시뮬레이션에 입력 (KS 표준 추정)",
                     type="primary", key="elev_send_to_sim"):
            idx = opts.index(sel)
            row = df.iloc[idx]
            w, d, h, dw, dh = _estimate_dimensions(int(row["정원(명)"] or 0))
            # widget key를 직접 설정해야 다음 렌더링에서 반영됨
            st.session_state["search_inner_w"] = w
            st.session_state["search_inner_d"] = d
            st.session_state["search_inner_h"] = h
            st.session_state["search_door_w"] = dw
            st.session_state["search_door_h"] = dh
            st.success(
                f"✅ {row['정원(명)']}인승 KS 표준 추정치 입력 완료 — "
                f"내부 {w}×{d}×{h}mm, 출입구 {dw}×{dh}mm. "
                "현장 실측치가 있다면 아래에서 직접 수정하세요."
            )
            st.rerun()

    st.divider()
    st.markdown("#### 🛏️ 매트리스 진입 시뮬레이션")
    st.caption(
        "검색 결과를 선택하거나 직접 치수를 입력해 매트리스 진입 가능 여부를 판정합니다. "
        "**현장 실측치가 가장 정확합니다.**"
    )
    _render_simulation_section(key_prefix="search")


def _render_simulation_tab() -> None:
    st.markdown("#### 매트리스 진입 시뮬레이션")
    st.caption("엘리베이터 내부 치수와 매트리스 사이즈를 직접 입력하면 진입 가능 여부를 판정합니다.")
    _render_simulation_section(key_prefix="standalone")


def render_elevator_inspection() -> None:
    """엘리베이터 사이즈 점검 메인 페이지."""
    st.title("🛗 엘리베이터 사이즈 점검")
    st.caption("주소로 승강기 스펙을 조회하고, 매트리스 진입 가능 여부를 시뮬레이션합니다.")

    tab1, tab2 = st.tabs(["1️⃣ 주소 검색 + 시뮬레이션", "2️⃣ 매트리스 시뮬레이션 단독"])
    with tab1:
        _render_search_tab()
    with tab2:
        _render_simulation_tab()
