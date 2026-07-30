"""
동 단위 상권 퍼포먼스 맵 (Trade Area Performance by Administrative Dong).

기능 개요:
  - CRM 매출 데이터 (app_customers/app_orders) 를 카카오 로컬 API 로 행정동 단위까지 지오코딩
  - 행정안전부 공공데이터 (행정동별 인구·세대현황) 와 조인
  - "시장 침투율 × 타겟 밀집도" 2x2 매트릭스로 A/B/C/D 4그룹 분류
  - Plotly Mapbox choropleth (배경 폴리곤) + Scattermapbox (버블 오버레이) 로 시각화

관련 마스터 플랜: `docs/plans/` 의 '동 단위 상권 퍼포먼스 맵' 계획서.

외부 의존:
  - Kakao Local API (기존 KAKAO_REST_API_KEY 재사용)
  - 행정안전부 인구·세대 API (data.go.kr 카탈로그 15108065 등) — [population_api] service_key 신규
  - GeoJSON: vuski/admdongkor 의 HangJeongDong_ver*.geojson (매장 소재 시도만 필터한 사본을 data/ 에 저장)
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests
import streamlit as st

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════
# 상수 · 설정
# ══════════════════════════════════════════════════════════════════

GEOJSON_PATH_DEFAULT = str(Path(__file__).parent / "data" / "admdong_boundary.geojson")

KAKAO_COORD2REGIONCODE_URL = "https://dapi.kakao.com/v2/local/geo/coord2regioncode.json"

# 행안부 주소기반산업지원서비스 도로명주소 검색 API (business.juso.go.kr).
# 카카오 주소검색 실패 시 2차 지오코더로 사용 — 노이즈 섞인 주소에 관대함.
JUSO_ADDR_SEARCH_URL = "https://business.juso.go.kr/addrlink/addrLinkApi.do"

# 행정안전부 행정동별 주민등록 인구 및 세대현황 API (data.go.kr 15108065).
# 서비스명(admmPpltnHhStus) 아래 operation(selectAdmmPpltnHhStus) 을 포함한 최종 경로.
# endpoint 는 secrets.toml [population_api] endpoint 로 재정의 가능.
POPULATION_API_BASE_DEFAULT = (
    "https://apis.data.go.kr/1741000/admmPpltnHhStus/selectAdmmPpltnHhStus"
)
# 행정동별 성/연령별 주민등록 인구수 API (data.go.kr 15108072).
# 동일 기관(1741000) 명명 규칙에 따라 operation 은 selectAdmmSexdAgePpltn 로 추정.
# endpoint 는 secrets.toml [population_api] age_endpoint 로 재정의 가능.
POPULATION_AGE_API_BASE_DEFAULT = (
    "https://apis.data.go.kr/1741000/admmSexdAgePpltn/selectAdmmSexdAgePpltn"
)

_POP_ENV_CANDIDATES: tuple[str, ...] = (
    "POPULATION_API_KEY",
    "DATA_GO_KR_SERVICE_KEY",
    "ADMIN_DONG_POPULATION_KEY",
    "ELEVATOR_API_KEY",
    "ELEVATOR_SERVICE_KEY",
)

_KAKAO_ENV_CANDIDATES: tuple[str, ...] = (
    "KAKAO_REST_API_KEY",
    "KAKAO_REST_KEY",
    "KAKAO_API_KEY",
)

# ── 핵심 타겟 연령대 (10세 단위 버킷) ─────────────────────────────
# 행안부 성/연령 API 응답의 컬럼 접두어(만 X~X+9세) → 우리 시스템의 버킷 키.
# 60대까지는 API 접두어와 동일하게 X(정수 문자열), 70대 이상은 "70plus" 로 축약해
# API 버킷 70·80·90·100 을 모두 합산해 하나의 UI 라벨로 노출한다.
AGE_BUCKET_KEYS: tuple[str, ...] = ("10", "20", "30", "40", "50", "60", "70plus")

# 버킷 키 → 화면 라벨 (Streamlit multiselect 및 표/차트 라벨용).
AGE_BUCKET_LABELS: dict[str, str] = {
    "10": "10대", "20": "20대", "30": "30대", "40": "40대",
    "50": "50대", "60": "60대", "70plus": "70대 이상",
}

# 버킷 키 → 실제 API 응답의 X 값 리스트 (male{X}AgeNmprCnt/feml{X}AgeNmprCnt 참조).
_AGE_KEY_TO_API_BUCKETS: dict[str, tuple[str, ...]] = {
    "10": ("10",), "20": ("20",), "30": ("30",), "40": ("40",),
    "50": ("50",), "60": ("60",), "70plus": ("70", "80", "90", "100"),
}

# 기본 선택(=기존 30~59세 정의와 동일한 동작 유지).
DEFAULT_TARGET_AGE_KEYS: tuple[str, ...] = ("30", "40", "50")


def age_bucket_column(key: str) -> str:
    """버킷 키 → app_dong_population_cache 컬럼명."""
    return f"age_{key}_population"


def format_selected_age_label(keys: list[str] | tuple[str, ...] | None) -> str:
    """선택 연령대를 사람이 읽기 쉬운 문자열로 (예: '30·40·50대' / '20·70대 이상')."""
    if not keys:
        return "선택 없음"
    return "·".join(AGE_BUCKET_LABELS.get(k, k) for k in keys)


# ══════════════════════════════════════════════════════════════════
# 인증키 로드 (elevator_inspection._get_service_key_diagnostic 패턴)
# ══════════════════════════════════════════════════════════════════

def _load_toml_dict() -> dict:
    """.streamlit/secrets.toml 을 직접 읽어 dict 반환 (없으면 빈 dict)."""
    try:
        import tomllib  # Python 3.11+
    except Exception:
        return {}
    for _p in [
        Path(__file__).parent / ".streamlit" / "secrets.toml",
        Path.cwd() / ".streamlit" / "secrets.toml",
    ]:
        try:
            if _p.exists():
                with open(_p, "rb") as _f:
                    return tomllib.load(_f) or {}
        except Exception:
            continue
    return {}


def _get_population_service_key_diagnostic() -> tuple[str, dict[str, str]]:
    """
    행정안전부 인구·세대 API 서비스 키를 다중 폴백으로 로드하고 진단 정보 반환.

    data.go.kr 계정 1개의 일반 인증키는 승인받은 모든 API 에 공용 사용 가능.
    [population_api] 가 없으면 [elevator_api] / ELEVATOR_API_KEY 로 폴백.
    """
    diag: dict[str, str] = {
        "secrets_toml_found": "no",
        "st_secrets_found": "no",
        "elevator_fallback": "no",
        "env_var_found": "no",
        "env_var_name": "",
        "final_source": "none",
        "key_len": "0",
    }
    key = ""

    def _set_key(value: str, source: str, *, elevator: bool = False) -> None:
        nonlocal key
        if not value:
            return
        key = value
        diag["final_source"] = source
        if elevator:
            diag["elevator_fallback"] = "yes"

    _toml = _load_toml_dict()
    _v = str(((_toml.get("population_api") or {}).get("service_key", "")) or "").strip()
    if _v:
        _set_key(_v, "secrets.toml[population_api]")
        diag["secrets_toml_found"] = "yes"
    else:
        _v = str(((_toml.get("elevator_api") or {}).get("service_key", "")) or "").strip()
        if _v:
            _set_key(_v, "secrets.toml[elevator_api]", elevator=True)
            diag["secrets_toml_found"] = "yes"

    try:
        if hasattr(st, "secrets"):
            _sec = st.secrets.get("population_api", {}) or {}
            _v = str(_sec.get("service_key", "") or "").strip()
            if _v:
                _set_key(_v, "st.secrets[population_api]")
                diag["st_secrets_found"] = "yes"
            else:
                _sec = st.secrets.get("elevator_api", {}) or {}
                _v = str(_sec.get("service_key", "") or "").strip()
                if _v:
                    _set_key(_v, "st.secrets[elevator_api]", elevator=True)
                    diag["st_secrets_found"] = "yes"
    except Exception:
        pass

    for _name in _POP_ENV_CANDIDATES:
        _v = (os.environ.get(_name, "") or "").strip()
        if _v:
            _set_key(
                _v,
                f"env:{_name}",
                elevator=_name in ("ELEVATOR_API_KEY", "ELEVATOR_SERVICE_KEY"),
            )
            diag["env_var_found"] = "yes"
            diag["env_var_name"] = _name
            break

    diag["key_len"] = str(len(key))
    return key, diag


def _get_population_service_key() -> str:
    return _get_population_service_key_diagnostic()[0]


def _get_population_api_endpoint(kind: str = "population") -> str:
    """[population_api] endpoint / age_endpoint 값 우선, 없으면 기본값.
    kind: 'population' (총인구/세대) 또는 'age' (성/연령별 인구).
    """
    _field = "age_endpoint" if kind == "age" else "endpoint"
    _default = POPULATION_AGE_API_BASE_DEFAULT if kind == "age" else POPULATION_API_BASE_DEFAULT

    _toml = _load_toml_dict()
    val = str(((_toml.get("population_api") or {}).get(_field, "")) or "").strip()
    if val:
        return val

    try:
        if hasattr(st, "secrets"):
            _sec = st.secrets.get("population_api", {}) or {}
            val = str(_sec.get(_field, "") or "").strip()
            if val:
                return val
    except Exception:
        pass

    return _default


def _get_kakao_rest_key_local() -> str:
    """카카오 REST API 키 로컬 로더 (app.py 의 _get_kakao_rest_key 와 동일 규칙).
    app 을 import 하면 순환참조 위험이 있어 여기서 별도 구현.
    """
    _toml = _load_toml_dict()
    _v = str(((_toml.get("kakao") or {}).get("rest_api_key", "")) or "").strip()
    if _v:
        return _v
    for _k in ("KAKAO_REST_KEY", "KAKAO_REST_API_KEY", "KAKAO_API_KEY"):
        _v = str(_toml.get(_k, "") or "").strip()
        if _v:
            return _v
    try:
        if hasattr(st, "secrets"):
            _v = str((st.secrets.get("kakao", {}) or {}).get("rest_api_key", "") or "").strip()
            if _v:
                return _v
            for _k in ("KAKAO_REST_KEY", "KAKAO_REST_API_KEY", "KAKAO_API_KEY"):
                try:
                    _v = str(st.secrets.get(_k, "") or "").strip()
                    if _v:
                        return _v
                except Exception:
                    continue
    except Exception:
        pass
    for _name in _KAKAO_ENV_CANDIDATES:
        _v = (os.environ.get(_name, "") or "").strip()
        if _v:
            return _v
    return ""


# ══════════════════════════════════════════════════════════════════
# 카카오 API: 좌표 → 행정동
# ══════════════════════════════════════════════════════════════════

def _kakao_address_to_coord_ex(address: str) -> tuple[tuple[float, float] | None, bool]:
    """주소 → (lat, lon). 반환의 2번째 값은 '일시 오류(HTTP 429/5xx 또는 요청 예외)' 여부.
    2번째 값이 True 면 주소가 잘못된 게 아니라 API 쪽 일시 오류이므로 상위에서 재시도 대상으로
    남겨야 한다 (0건 응답과 구분)."""
    key = _get_kakao_rest_key_local()
    if not key or not address:
        return None, False
    try:
        r = requests.get(
            "https://dapi.kakao.com/v2/local/search/address.json",
            params={"query": address.strip()},
            headers={"Authorization": f"KakaoAK {key}"},
            timeout=5.0,
        )
        if r.status_code == 429 or r.status_code >= 500:
            return None, True
        if r.status_code != 200:
            return None, False
        docs = (r.json() or {}).get("documents") or []
        if not docs:
            return None, False
        d = docs[0]
        x, y = d.get("x"), d.get("y")
        if x is None or y is None:
            return None, False
        return (float(y), float(x)), False
    except Exception:
        return None, True


def _kakao_address_to_coord(address: str) -> tuple[float, float] | None:
    """하위호환 래퍼 (일시오류 구분 없이 좌표만 필요한 호출부용)."""
    coord, _ = _kakao_address_to_coord_ex(address)
    return coord


def _kakao_keyword_search_to_coord(query: str) -> tuple[float, float] | None:
    """카카오 키워드검색(POI/건물명 검색) → (lat, lon). 도로명주소 검색이 실패한 노이즈 섞인
    주소(메모·태그·중복 텍스트 등)에 대한 최후 폴백. app.py 의 키워드검색 패턴과 동일한
    엔드포인트를 독립 구현 (순환 import 방지)."""
    key = _get_kakao_rest_key_local()
    if not key or not query or not query.strip():
        return None
    try:
        r = requests.get(
            "https://dapi.kakao.com/v2/local/search/keyword.json",
            params={"query": query.strip()},
            headers={"Authorization": f"KakaoAK {key}"},
            timeout=5.0,
        )
        if r.status_code != 200:
            return None
        docs = (r.json() or {}).get("documents") or []
        if not docs:
            return None
        d = docs[0]
        x, y = d.get("x"), d.get("y")
        if x is None or y is None:
            return None
        return float(y), float(x)
    except Exception:
        return None


def _get_juso_confm_key() -> str:
    """행안부 주소검색 API 승인키 로더. [juso_api] confm_key → 환경변수 순."""
    _toml = _load_toml_dict()
    _v = str(((_toml.get("juso_api") or {}).get("confm_key", "")) or "").strip()
    if _v:
        return _v
    try:
        if hasattr(st, "secrets"):
            _v = str((st.secrets.get("juso_api", {}) or {}).get("confm_key", "") or "").strip()
            if _v:
                return _v
    except Exception:
        pass
    for _name in ("JUSO_CONFM_KEY", "JUSO_API_KEY"):
        _v = (os.environ.get(_name, "") or "").strip()
        if _v:
            return _v
    return ""


def _juso_refine_road_address(address: str) -> str | None:
    """행안부 juso.go.kr 검색 API 로 지저분한 주소를 정제된 도로명주소로 변환.

    카카오 주소검색이 0건인 주소도 juso 는 부분 일치·옛 표기에 관대해 찾는 경우가 많다.
    반환된 도로명주소를 카카오에 재투입하면 좌표 → 행정동으로 이어진다.
    키 미설정·오류·0건이면 None (상위에서 다음 폴백으로 진행)."""
    key = _get_juso_confm_key()
    if not key or not address or not address.strip():
        return None
    # juso API 는 특수문자 포함 keyword 를 거부(E0012 등) → 사전 제거
    kw = re.sub(r"[%=><\[\]{}|\\^~`!@#$&*()'\"]", " ", address)
    kw = re.sub(r"\s+", " ", kw).strip()
    if len(kw) < 4:
        return None
    try:
        r = requests.get(
            JUSO_ADDR_SEARCH_URL,
            params={
                "confmKey": key, "currentPage": 1, "countPerPage": 3,
                "keyword": kw, "resultType": "json",
            },
            timeout=5.0,
        )
        if r.status_code != 200:
            return None
        results = (r.json() or {}).get("results") or {}
        common = results.get("common") or {}
        if str(common.get("errorCode", "")) != "0":
            logger.info("juso API 오류 (%s): %s", common.get("errorCode"), common.get("errorMessage"))
            return None
        jusos = results.get("juso") or []
        if not jusos:
            return None
        road = str(jusos[0].get("roadAddr") or jusos[0].get("roadAddrPart1") or "").strip()
        return road or None
    except Exception:
        return None


def _normalize_address_candidates(address: str) -> list[str]:
    """도로명주소 검색이 실패하는 주된 원인(실측 확인)인 동/호수 뒤 메모·태그·중복표기를
    제거한 재시도 후보 목록을 순서대로 반환 (원본과 동일하거나 너무 짧아진 후보는 제외).

    실측 실패 사례:
      "...래미안2차) 302-901  따님방"          → ')' 까지만 사용
      "...서사 우미린 105-901 [84a] 미고지..."  → '[..]' 제거 + 숫자-숫자 이후 제거
      "...양산삼성명가타운) 106-605"            → ')' 까지만 사용
    """
    addr = (address or "").strip()
    if not addr:
        return []
    candidates: list[str] = []

    # 1) 마지막 ')' 까지만 사용 — 법정동/건물명 이후의 동·호수·메모 제거
    idx = addr.rfind(")")
    if idx != -1 and idx < len(addr) - 1:
        candidates.append(addr[: idx + 1].strip())

    # 2) '[...]' 대괄호 메모 제거
    no_bracket = re.sub(r"\[[^\]]*\]", "", addr).strip()
    if no_bracket != addr:
        candidates.append(no_bracket)

    # 3) 끝부분 '숫자-숫자' (동-호 표기) 이후 전부 제거 (예: '106-605', '302-901  따님방')
    stripped = re.sub(r"\s+\d{1,4}-\d{1,4}\b.*$", "", no_bracket).strip()
    if stripped and stripped != no_bracket:
        candidates.append(stripped)

    # 4) 끝부분 '숫자동 숫자호' 표기 이후 전부 제거 (예: '102동 1504호 100A')
    stripped2 = re.sub(r"\s+\d{1,4}\s*동\s*\d{0,4}\s*호?\b.*$", "", addr).strip()
    if stripped2 and stripped2 != addr:
        candidates.append(stripped2)

    # 5) 전화번호 제거 (예: '... 010-1234-5678 배송전 연락')
    no_phone = re.sub(r"\b\d{2,4}[- .]?\d{3,4}[- .]?\d{4}\b", "", addr).strip()
    if no_phone != addr:
        candidates.append(no_phone)

    # 6) '도로명 + 건물번호' 까지만 절단 — 이후의 건물명·동호수·메모 전부 제거
    #    (예: '울산 남구 삼산로 35, 무슨아파트 102동 301호 오후배송' → '울산 남구 삼산로 35')
    m_road = re.match(r"^(.{4,}?(?:대로|로|길)\s*\d+(?:-\d+)?)", no_phone)
    if m_road:
        candidates.append(m_road.group(1).strip())

    # 7) 지번주소 '동/리/가 + 번지' 까지만 절단
    #    (예: '울산 중구 학성동 123-4 주택 파란대문' → '울산 중구 학성동 123-4')
    m_jibun = re.match(r"^(.{4,}?[동리가]\s*\d+(?:-\d+)?)(?=\s|$)", no_phone)
    if m_jibun:
        candidates.append(m_jibun.group(1).strip())

    seen: set[str] = {addr}
    out: list[str] = []
    for c in candidates:
        if c and len(c) >= 6 and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def geocode_to_admin_dong(
    address: str | None,
    lat: float | None = None,
    lon: float | None = None,
) -> dict:
    """
    주소 또는 좌표로 행정동명·행정동코드 조회. 주소검색 0건 시 정규화 재시도 →
    juso.go.kr 2차 지오코더 → 키워드검색 순으로 폴백 체인을 적용한다.

    반환 (항상 "ok" 키 포함):
      성공: {"ok": True, "admin_dong_name", "admin_dong_code", "sigungu", "sidonm",
             "lat", "lon", "matched_address"}
      실패: {"ok": False, "fail_stage": str, "fail_detail": str}
        fail_stage 종류:
          - "no_kakao_key"   : 카카오 REST 키 미설정
          - "rate_limited"   : HTTP 429/5xx 등 일시 오류 → 다음 배치에서 자동 재시도
          - "no_h_region"    : 좌표는 확보했으나 행정동(H) 문서 없음
          - "not_found"      : 주소검색·정규화·juso·키워드검색 모두 실패 (영구 실패 후보)
    """
    key = _get_kakao_rest_key_local()
    if not key:
        return {"ok": False, "fail_stage": "no_kakao_key", "fail_detail": "카카오 REST 키가 설정되지 않음"}

    _lat, _lon = lat, lon
    matched_address: str | None = None

    if _lat is None or _lon is None:
        if not address or not address.strip():
            return {"ok": False, "fail_stage": "not_found", "fail_detail": "주소 값이 비어 있음"}

        coord, rate_limited = _kakao_address_to_coord_ex(address)
        if coord:
            _lat, _lon = coord
            matched_address = address
        elif rate_limited:
            return {"ok": False, "fail_stage": "rate_limited", "fail_detail": "주소검색 HTTP 429/5xx 또는 요청 예외"}
        else:
            for cand in _normalize_address_candidates(address):
                coord, rate_limited = _kakao_address_to_coord_ex(cand)
                if coord:
                    _lat, _lon = coord
                    matched_address = cand
                    break
                if rate_limited:
                    return {"ok": False, "fail_stage": "rate_limited", "fail_detail": f"정규화 재시도 중 HTTP 오류: {cand}"}

            # 2차 지오코더: 행안부 juso 검색으로 주소 정제 → 카카오 재검색
            if _lat is None:
                _seen_road: set[str] = set()
                for cand in [address, *_normalize_address_candidates(address)]:
                    road = _juso_refine_road_address(cand)
                    if not road or road in _seen_road:
                        continue
                    _seen_road.add(road)
                    coord, rate_limited = _kakao_address_to_coord_ex(road)
                    if coord:
                        _lat, _lon = coord
                        matched_address = road
                        break
                    if rate_limited:
                        return {"ok": False, "fail_stage": "rate_limited",
                                "fail_detail": f"juso 정제주소 재검색 중 HTTP 오류: {road}"}

            if _lat is None:
                for cand in [address, *_normalize_address_candidates(address)]:
                    coord = _kakao_keyword_search_to_coord(cand)
                    if coord:
                        _lat, _lon = coord
                        matched_address = cand
                        break
                if _lat is None:
                    return {
                        "ok": False, "fail_stage": "not_found",
                        "fail_detail": "주소검색·정규화·juso·키워드검색 모두 0건",
                    }

    try:
        r = requests.get(
            KAKAO_COORD2REGIONCODE_URL,
            params={"x": _lon, "y": _lat},
            headers={"Authorization": f"KakaoAK {key}"},
            timeout=5.0,
        )
        if r.status_code == 429 or r.status_code >= 500:
            return {"ok": False, "fail_stage": "rate_limited", "fail_detail": f"coord2region HTTP {r.status_code}"}
        if r.status_code != 200:
            return {"ok": False, "fail_stage": "not_found", "fail_detail": f"coord2region HTTP {r.status_code}"}
        docs = (r.json() or {}).get("documents") or []
        h = next((d for d in docs if str(d.get("region_type")) == "H"), None)
        if h is None:
            return {"ok": False, "fail_stage": "no_h_region", "fail_detail": "좌표는 확보했으나 행정동(H) 문서 없음"}
        return {
            "ok": True,
            "admin_dong_name": (h.get("region_3depth_name") or "").strip(),
            "admin_dong_code": (h.get("code") or "").strip(),
            "sigungu": (h.get("region_2depth_name") or "").strip(),
            "sidonm": (h.get("region_1depth_name") or "").strip(),
            "lat": _lat, "lon": _lon,
            "matched_address": matched_address,
        }
    except Exception as e:
        return {"ok": False, "fail_stage": "rate_limited", "fail_detail": f"coord2region 요청 예외: {type(e).__name__}: {e}"}


# ══════════════════════════════════════════════════════════════════
# 행안부 공공데이터 API: 인구 · 세대현황 / 성/연령별 인구
# ══════════════════════════════════════════════════════════════════

_THIRTY_DAYS_SEC = 60 * 60 * 24 * 30


def _to_int_safe(v: Any) -> int:
    try:
        return int(str(v).replace(",", "").strip())
    except Exception:
        return 0


def _clamp_population_yyyymm(yyyymm: str) -> str:
    """행정안전부 인구·세대 API는 진행 중인 이번 달 통계를 아직 게시하지 않아,
    이번 달(또는 미래월)로 조회하면 모든 행정동에서 INVALID_REQUEST_PARAMETER_ERROR
    를 반환한다 (실측 확인됨). 분석 기간 종료월이 이번 달 이상이면 가장 최근
    확정월(전월)로 낮춰서 요청한다."""
    this_month = pd.Timestamp.now().strftime("%Y%m")
    if not yyyymm or yyyymm >= this_month:
        prev_month_last_day = pd.Timestamp.now().replace(day=1) - pd.Timedelta(days=1)
        return prev_month_last_day.strftime("%Y%m")
    return yyyymm


@st.cache_data(ttl=_THIRTY_DAYS_SEC, show_spinner=False)
def fetch_admin_dong_population(admin_dong_code: str, yyyymm: str) -> dict:
    """
    행정동코드+통계년월로 총인구수·세대수 조회.

    반환:
      {"ok": bool, "admin_dong_code": str, "yyyymm": str,
       "total_population": int, "total_households": int,
       "error": str, "raw_url": str}

    주의: data.go.kr Swagger 명세로 정확한 파라미터명·응답 필드명을 확정 후
    본 함수의 `params` 와 파싱 로직을 미세 조정할 수 있습니다.
    """
    key = _get_population_service_key()
    if not key or not admin_dong_code or not yyyymm:
        return {
            "ok": False, "admin_dong_code": admin_dong_code, "yyyymm": yyyymm,
            "total_population": 0, "total_households": 0,
            "error": "service_key 또는 파라미터 누락", "raw_url": "",
        }
    url = _get_population_api_endpoint("population")
    # data.go.kr 15108065 Swagger 필수 파라미터: serviceKey, admmCd, srchFrYm, srchToYm
    # lv=7 → 단일 읍면동 단위 결과 1건. regSeCd=1 → 등록구분 전체 (기본값).
    params = {
        "serviceKey": key,
        "type": "json",
        "admmCd": admin_dong_code,
        "srchFrYm": yyyymm,
        "srchToYm": yyyymm,
        "lv": "7",
        "regSeCd": "1",
        "numOfRows": 100,
        "pageNo": 1,
    }
    try:
        # 실측상 5~7초가 걸리는 무거운 응답이라 병렬 호출 시 여유있게 20초로 설정.
        r = requests.get(url, params=params, timeout=20.0)
        # 1741000 API 는 Content-Type 이 UTF-8 을 명시하지 않아 requests 가
        # ISO-8859-1 로 잘못 추측 → 한글 mojibake. 강제 UTF-8 로 재해석.
        r.encoding = "utf-8"
        raw_url = str(r.url)
        if r.status_code != 200:
            return {
                "ok": False, "admin_dong_code": admin_dong_code, "yyyymm": yyyymm,
                "total_population": 0, "total_households": 0,
                "error": f"HTTP {r.status_code}: {r.text[:120]}", "raw_url": raw_url,
            }
        js = r.json() if r.content else {}
        items = _extract_items(js)
        if not items:
            return {
                "ok": False, "admin_dong_code": admin_dong_code, "yyyymm": yyyymm,
                "total_population": 0, "total_households": 0,
                "error": "응답 items 비어 있음", "raw_url": raw_url,
            }
        # lv=7 이면 단건이 정상이나, 안전하게 합산 (통·반 fallback 대비).
        # 실 응답 필드: 총인구수=totNmprCnt, 세대수=hhCnt.
        total_pop = sum(
            _to_int_safe(
                _pick(it, [
                    "totNmprCnt", "totPopltCnt", "totPopltnCnt",
                    "tot_popltn_co", "totPopltnCo",
                ])
            )
            for it in items
        )
        total_hh = sum(
            _to_int_safe(
                _pick(it, ["hhCnt", "hshldCnt", "totHshldCnt", "hshld_co"])
            )
            for it in items
        )
        return {
            "ok": True, "admin_dong_code": admin_dong_code, "yyyymm": yyyymm,
            "total_population": total_pop, "total_households": total_hh,
            "error": "", "raw_url": raw_url,
        }
    except Exception as e:
        return {
            "ok": False, "admin_dong_code": admin_dong_code, "yyyymm": yyyymm,
            "total_population": 0, "total_households": 0,
            "error": f"요청 실패: {type(e).__name__}: {e}", "raw_url": "",
        }


def _empty_age_buckets() -> dict[str, int]:
    """모든 UI 연령 버킷을 0 으로 초기화한 dict (누락 방지용 초기값)."""
    return {k: 0 for k in AGE_BUCKET_KEYS}


@st.cache_data(ttl=_THIRTY_DAYS_SEC, show_spinner=False)
def fetch_admin_dong_age_population(admin_dong_code: str, yyyymm: str) -> dict:
    """
    행정동코드+통계년월로 10세 단위 연령 인구수 조회 (타겟 밀집도 계산용).

    반환: {
        "ok", "admin_dong_code", "yyyymm",
        "age_buckets": {"10","20","30","40","50","60","70plus" → 남녀 합산 인구},
        "total_population",
        "error", "raw_url",
    }
    """
    empty_buckets = _empty_age_buckets()
    key = _get_population_service_key()
    if not key or not admin_dong_code or not yyyymm:
        return {
            "ok": False, "admin_dong_code": admin_dong_code, "yyyymm": yyyymm,
            "age_buckets": empty_buckets, "total_population": 0,
            "error": "service_key 또는 파라미터 누락", "raw_url": "",
        }
    url = _get_population_api_endpoint("age")
    # 자매 API(15108072) 는 동일 기관(1741000) 명명 규칙 → 파라미터 세트 동일 추정.
    params = {
        "serviceKey": key,
        "type": "json",
        "admmCd": admin_dong_code,
        "srchFrYm": yyyymm,
        "srchToYm": yyyymm,
        "lv": "7",
        "regSeCd": "1",
        "numOfRows": 200,
        "pageNo": 1,
    }
    try:
        # 실측상 5~7초가 걸리는 무거운 응답이라 병렬 호출 시 여유있게 20초로 설정.
        r = requests.get(url, params=params, timeout=20.0)
        r.encoding = "utf-8"
        raw_url = str(r.url)
        if r.status_code != 200:
            return {
                "ok": False, "admin_dong_code": admin_dong_code, "yyyymm": yyyymm,
                "age_buckets": empty_buckets, "total_population": 0,
                "error": f"HTTP {r.status_code}: {r.text[:120]}", "raw_url": raw_url,
            }
        js = r.json() if r.content else {}
        items = _extract_items(js)
        if not items:
            return {
                "ok": False, "admin_dong_code": admin_dong_code, "yyyymm": yyyymm,
                "age_buckets": empty_buckets, "total_population": 0,
                "error": "응답 items 비어 있음", "raw_url": raw_url,
            }
        # 실 응답: 행별 컬럼형 age 필드.
        #   male{X}AgeNmprCnt: 만 X~X+9세 남자 (X ∈ {0,10,20,...,100})
        #   feml{X}AgeNmprCnt: 만 X~X+9세 여자
        # UI 버킷 키(10~60, 70plus)별로 대응하는 API 버킷을 남녀 합산해 저장.
        # 총인구는 totNmprCnt 필드에서 취득.
        buckets = _empty_age_buckets()
        total = 0
        for it in items:
            total += _to_int_safe(_pick(it, ["totNmprCnt", "totPopltCnt"]))
            for ui_key, api_bkts in _AGE_KEY_TO_API_BUCKETS.items():
                for api_bkt in api_bkts:
                    buckets[ui_key] += _to_int_safe(it.get(f"male{api_bkt}AgeNmprCnt"))
                    buckets[ui_key] += _to_int_safe(it.get(f"feml{api_bkt}AgeNmprCnt"))
        return {
            "ok": True, "admin_dong_code": admin_dong_code, "yyyymm": yyyymm,
            "age_buckets": buckets, "total_population": total,
            "error": "", "raw_url": raw_url,
        }
    except Exception as e:
        return {
            "ok": False, "admin_dong_code": admin_dong_code, "yyyymm": yyyymm,
            "age_buckets": empty_buckets, "total_population": 0,
            "error": f"요청 실패: {type(e).__name__}: {e}", "raw_url": "",
        }


def _extract_items(js: Any) -> list[dict]:
    """공공데이터포털 응답에서 items 리스트 추출.

    행안부 1741000 admmPpltnHhStus/admmSexdAgePpltn 실제 응답:
      {"Response": {"head": {...}, "items": {"item": {...} | [{...}, ...]}}}
    표준 data.go.kr 응답:
      {"response": {"header": {...}, "body": {"items": {"item": ...}}}}
    """
    if not isinstance(js, dict):
        return []
    # 후보 최상위 래퍼 (대소문자 혼재 대응)
    for _top in ("response", "Response"):
        _wrap = js.get(_top)
        if not isinstance(_wrap, dict):
            continue
        # body.items (표준) 또는 items 직접 (1741000)
        _items = None
        _body = _wrap.get("body")
        if isinstance(_body, dict):
            _items = _body.get("items")
        if _items is None:
            _items = _wrap.get("items")
        if isinstance(_items, dict):
            _item = _items.get("item")
            if isinstance(_item, list):
                return _item
            if isinstance(_item, dict):
                return [_item]
            return []
        if isinstance(_items, list):
            return _items
    if "items" in js and isinstance(js["items"], list):
        return js["items"]
    return []


def _pick(d: dict, keys: list[str]) -> Any:
    """dict 에서 여러 후보 키 중 존재하는 첫 값 반환 (case-insensitive)."""
    if not isinstance(d, dict):
        return None
    _lower = {str(k).lower(): v for k, v in d.items()}
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
        lk = k.lower()
        if lk in _lower and _lower[lk] not in (None, ""):
            return _lower[lk]
    return None


# ══════════════════════════════════════════════════════════════════
# GeoJSON 로더 + centroid 계산
# ══════════════════════════════════════════════════════════════════

@st.cache_resource(show_spinner=False)
def load_admdong_geojson(path: str | None = None) -> dict:
    """행정동 경계 GeoJSON 로드 (앱 세션 전역 캐시)."""
    p = path or GEOJSON_PATH_DEFAULT
    if not Path(p).exists():
        return {"type": "FeatureCollection", "features": []}
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _polygon_centroid(coords: list) -> tuple[float, float] | None:
    """단순 평균 방식의 centroid (정밀도 요구 낮음, 지도 마커 위치용).
    Polygon 및 MultiPolygon 모두 지원."""
    pts: list[tuple[float, float]] = []

    def _walk(x: Any) -> None:
        if isinstance(x, (list, tuple)) and len(x) == 2 and all(isinstance(v, (int, float)) for v in x):
            pts.append((float(x[0]), float(x[1])))
            return
        if isinstance(x, (list, tuple)):
            for v in x:
                _walk(v)

    _walk(coords)
    if not pts:
        return None
    lon = sum(p[0] for p in pts) / len(pts)
    lat = sum(p[1] for p in pts) / len(pts)
    return (lat, lon)


@st.cache_data(ttl=3600, show_spinner=False)
def build_geojson_index(geojson: dict) -> pd.DataFrame:
    """
    GeoJSON features 를 DataFrame 인덱스로 변환.
      컬럼: adm_cd (8자리), adm_cd2 (10자리), adm_nm, sidonm, sggnm,
            centroid_lat, centroid_lon.
    """
    rows: list[dict] = []
    for feat in (geojson or {}).get("features", []) or []:
        props = feat.get("properties") or {}
        geom = feat.get("geometry") or {}
        c = _polygon_centroid(geom.get("coordinates"))
        rows.append({
            "adm_cd": str(props.get("adm_cd") or "").strip(),
            "adm_cd2": str(props.get("adm_cd2") or "").strip(),
            "adm_nm": str(props.get("adm_nm") or "").strip(),
            "sidonm": str(props.get("sidonm") or "").strip(),
            "sggnm": str(props.get("sggnm") or "").strip(),
            "centroid_lat": c[0] if c else None,
            "centroid_lon": c[1] if c else None,
        })
    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["adm_cd", "adm_cd2", "adm_nm", "sidonm", "sggnm", "centroid_lat", "centroid_lon"]
    )


def suggest_dong_candidates(address: str, geo_idx: pd.DataFrame) -> list[dict]:
    """실패 주소의 '같은 시군구 + 도로명 어간 ↔ 행정동명 어간' 매칭 후보를 점수순 반환.

    예: '울산 남구 삼산로 12 ...' → 남구 내 '삼산동' (어간 '삼산' 완전일치, score 1.0).
    도로명이 동명과 무관한 경우(중앙로 등)가 있으므로 자동 적용은
    'score 1.0 후보가 시군구 내 정확히 1개'일 때만 권장 (그 외는 관리자 확인용 추천).

    반환: [{"adm_nm", "adm_cd2", "score"}, ...] score 내림차순, 최대 3개.
    """
    addr = (address or "").strip()
    if not addr or geo_idx is None or geo_idx.empty:
        return []
    # 1) 도로명 어간 추출: '삼산로'→'삼산', '화합로3길'→'화합', '테크노대로'→'테크노'
    m_road = re.search(r"([가-힣]{2,})(?:대로|로|길)", addr)
    if not m_road:
        return []
    road_stem = m_road.group(1)

    # 2) 주소에 등장하는 시군구로 후보 범위 한정 (시군구 불명이면 추천하지 않음 — 안전)
    sgg_rows = geo_idx[geo_idx["sggnm"].apply(lambda s: bool(s) and s in addr)]
    if sgg_rows.empty:
        return []

    cands: dict[str, dict] = {}
    for _, row in sgg_rows.iterrows():
        # 행정동명 어간: '삼산동'→'삼산', '무거1동'→'무거', '범서읍'→'범서'
        last = str(row["adm_nm"]).split()[-1]
        dong_stem = re.sub(r"제?\d*[동읍면]$", "", last)
        if not dong_stem:
            continue
        if dong_stem == road_stem:
            score = 1.0
        elif len(dong_stem) >= 2 and (road_stem.startswith(dong_stem) or dong_stem.startswith(road_stem)):
            score = 0.6
        else:
            continue
        code = str(row["adm_cd2"])
        if code not in cands or cands[code]["score"] < score:
            cands[code] = {"adm_nm": row["adm_nm"], "adm_cd2": code, "score": score}
    return sorted(cands.values(), key=lambda x: -x["score"])[:3]


# ══════════════════════════════════════════════════════════════════
# KPI 산출 · 2x2 클러스터링
# ══════════════════════════════════════════════════════════════════

# 매니저용 행동 전략 라벨 — 알파벳 quadrant 를 액션 의미로 매핑.
# (기존 A=고침투·고밀도(핵심 VIP), B=저침투·고밀도(개척) 정의는 유지)
STRATEGY_ATTACK   = "🚨 집중 공략 (마케팅 시급)"
STRATEGY_DEFEND   = "👑 핵심 방어 (VIP 관리)"
STRATEGY_LATENT   = "💡 잠재 상권 (특수 요인)"
STRATEGY_HOLD     = "👻 마케팅 보류 (예산 절감)"
STRATEGY_UNSET    = "(미분류)"

STRATEGY_MAP = {
    "A": STRATEGY_DEFEND,
    "B": STRATEGY_ATTACK,
    "C": STRATEGY_LATENT,
    "D": STRATEGY_HOLD,
}

STRATEGY_COLOR = {
    STRATEGY_ATTACK: "#E53935",
    STRATEGY_DEFEND: "#1E88E5",
    STRATEGY_LATENT: "#43A047",
    STRATEGY_HOLD:   "#BDBDBD",
    STRATEGY_UNSET:  "#EEEEEE",
}

STRATEGY_ORDER = [STRATEGY_ATTACK, STRATEGY_DEFEND, STRATEGY_LATENT, STRATEGY_HOLD, STRATEGY_UNSET]

# Plotly 범례·안내 패널용 — 코드(A/B/C/D) + 지침 문구
STRATEGY_CODE = {
    STRATEGY_ATTACK: "B",
    STRATEGY_DEFEND: "A",
    STRATEGY_LATENT: "C",
    STRATEGY_HOLD: "D",
    STRATEGY_UNSET: "-",
}

STRATEGY_LEGEND_LABEL = {
    STRATEGY_ATTACK: "B · 🚨 집중 공략 (마케팅 시급)",
    STRATEGY_DEFEND: "A · 👑 핵심 방어 (VIP 관리)",
    STRATEGY_LATENT: "C · 💡 잠재 상권 (특수 요인)",
    STRATEGY_HOLD: "D · 👻 마케팅 보류 (예산 절감)",
    STRATEGY_UNSET: "- · (미분류)",
}

# Phase 16: 핵심 타겟 연령대를 UI 에서 사용자가 지정하므로, 안내 문구는
# 고정 "30~59세" 대신 "타겟" 으로 두고 실제 선택 라벨은 caption 에서 별도 노출한다.
STRATEGY_GUIDE = {
    "B": "타겟 인구는 많지만 우리 매장 구매는 적음 → 전단·광고·체험 이벤트 등 마케팅을 **지금 우선** 투입",
    "A": "구매도 많고 타겟 인구도 많음 → 기존 고객 **VIP 관리·재구매·소개 유도**에 집중",
    "C": "구매는 많지만 타겟 인구 비중은 낮음 → 성숙·특수 요인 지역, **유지·케이스별** 대응",
    "D": "구매·타겟 인구 모두 낮음 → **예산 절감·보류**, 다른 지역(B·A) 우선",
    "-": "인구·구매 데이터 부족으로 분류 불가 — 백필·수동 교정 후 재분석",
}

STRATEGY_COLOR_BY_LEGEND = {
    STRATEGY_LEGEND_LABEL[k]: v for k, v in STRATEGY_COLOR.items()
}

STRATEGY_LEGEND_ORDER = [STRATEGY_LEGEND_LABEL[s] for s in STRATEGY_ORDER]


def _render_strategy_legend_guide(selected_age_label: str = "") -> None:
    """지도 하단·매니저용 상세 범례 — A/B/C/D 코드별 지침 문구.

    selected_age_label: 현재 선택된 핵심 타겟 연령대 라벨(예: '30대·40대·50대').
    비어 있으면 세로축 안내에서 연령대 언급을 생략한다.
    """
    _items = [
        ("B", STRATEGY_ATTACK),
        ("A", STRATEGY_DEFEND),
        ("C", STRATEGY_LATENT),
        ("D", STRATEGY_HOLD),
    ]
    st.markdown("**행동 전략 범례** · 지도 색 = 아래 4가지 중 하나")
    _cols = st.columns(4)
    for _col, (_code, _label) in zip(_cols, _items):
        _color = STRATEGY_COLOR[_label]
        _guide = STRATEGY_GUIDE[_code]
        _title = STRATEGY_LEGEND_LABEL[_label]
        with _col:
            st.markdown(
                f'<div style="border-left:5px solid {_color};padding:8px 12px;'
                f'background:#fafafa;border-radius:6px;font-size:0.82rem;line-height:1.45;">'
                f'<span style="font-weight:700;color:{_color};">{_title}</span><br>'
                f'<span style="color:#444;">{_guide.replace("**", "")}</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
    _age_suffix = f"(선택: {selected_age_label})" if selected_age_label else ""
    st.caption(
        f"분류 기준: 가로축 = 우리 매장 침투율(1,000가구당 구매), 세로축 = 타겟 인구 비중{_age_suffix} — "
        "각 축의 **중앙값**으로 2×2 매트릭스를 나눕니다. "
        f"집중 공략(B) = {STRATEGY_GUIDE['B'].split('→')[0].strip()}."
    )


def compute_dong_kpi(
    crm_counts: pd.DataFrame,
    population: pd.DataFrame,
    selected_age_keys: list[str] | tuple[str, ...] | None = None,
) -> pd.DataFrame:
    """
    crm_counts:  [admin_dong_code, admin_dong_name, purchase_count, purchase_amount?]
    population:  [admin_dong_code, total_households, total_population,
                  age_10_population ~ age_60_population, age_70plus_population]
    selected_age_keys: UI 에서 선택된 연령 버킷 키(예: ["30","40","50"]). None 이면 DEFAULT_TARGET_AGE_KEYS.

    반환: 위 컬럼 + target_population, penetration_rate(%), target_density(%).
    attrs["selected_age_keys"] 에 사용된 선택값을 담아 하위 UI 라벨에서 재사용한다.
    """
    if crm_counts is None or crm_counts.empty:
        return crm_counts.copy() if crm_counts is not None else pd.DataFrame()
    sel_keys = [k for k in (selected_age_keys or DEFAULT_TARGET_AGE_KEYS) if k in AGE_BUCKET_KEYS]
    if not sel_keys:
        sel_keys = list(DEFAULT_TARGET_AGE_KEYS)

    df = crm_counts.merge(population, on="admin_dong_code", how="left")
    # 0 을 NaN 으로 바꿔 분모 오류(ZeroDivisionError 대신 무한대) 를 방지.
    # pd.NA 는 object dtype 승격을 유발해 이후 astype(float) 에서
    # "float() argument ... not 'NAType'" 오류가 나므로 float 호환 NaN 사용.
    df["total_households"] = pd.to_numeric(df.get("total_households"), errors="coerce").replace(0, float("nan"))
    df["total_population"] = pd.to_numeric(df.get("total_population"), errors="coerce").replace(0, float("nan"))
    for k in AGE_BUCKET_KEYS:
        col = age_bucket_column(k)
        df[col] = pd.to_numeric(df.get(col), errors="coerce").fillna(0)
    df["target_population"] = sum(df[age_bucket_column(k)] for k in sel_keys).astype(float)
    df["purchase_count"] = pd.to_numeric(df.get("purchase_count"), errors="coerce").fillna(0)
    df["purchase_amount"] = (
        pd.to_numeric(df.get("purchase_amount"), errors="coerce").fillna(0).round(0).astype("int64")
    )
    df["penetration_rate"] = (df["purchase_count"] / df["total_households"] * 100).astype(float).fillna(0.0)
    df["target_density"] = (df["target_population"] / df["total_population"] * 100).astype(float).fillna(0.0)
    df["penetration_rate"] = df["penetration_rate"].round(3)
    df["target_density"] = df["target_density"].round(2)
    df.attrs["selected_age_keys"] = list(sel_keys)
    return df


def assign_quadrant(df: pd.DataFrame) -> pd.DataFrame:
    """
    침투율·밀집도 각각의 중앙값으로 4분면 분류.
      A: 고침투·고밀집  (핵심 상권, 방어)
      B: 저침투·고밀집  (잠재/개척 상권, 마케팅 우선 투입)
      C: 고침투·저밀집  (성숙/포화 상권, 유지 관리)
      D: 저침투·저밀집  (저우선 상권)

    유효 데이터가 없으면 quadrant='-' 반환.
    """
    if df is None or df.empty:
        return df
    out = df.copy()
    if "penetration_rate" not in out.columns or "target_density" not in out.columns:
        out["quadrant"] = "-"
        return out
    # 침투율/밀집도가 모두 0 인 행 (구매도 없고 인구 데이터도 없는 경우)은 median 계산에서 제외
    _valid = out[(out["penetration_rate"] > 0) | (out["target_density"] > 0)]
    if _valid.empty:
        out["quadrant"] = "-"
        return out
    med_pen = float(_valid["penetration_rate"].median())
    med_den = float(_valid["target_density"].median())

    def _label(row: pd.Series) -> str:
        p = float(row.get("penetration_rate") or 0)
        d = float(row.get("target_density") or 0)
        if p <= 0 and d <= 0:
            return "-"
        high_p = p >= med_pen
        high_d = d >= med_den
        if high_p and high_d:
            return "A"
        if (not high_p) and high_d:
            return "B"
        if high_p and (not high_d):
            return "C"
        return "D"

    out["quadrant"] = out.apply(_label, axis=1)
    out["행동_전략"] = out["quadrant"].map(STRATEGY_MAP).fillna(STRATEGY_UNSET)

    # 호버 문장용 파생 컬럼 (침투율 % → 1,000가구당 구매가구 환산)
    _pen = pd.to_numeric(out.get("penetration_rate"), errors="coerce").fillna(0.0)
    _tgt = pd.to_numeric(out.get("target_population"), errors="coerce").fillna(0)
    out["hover_target"] = _tgt.astype(int).map(lambda n: f"{n:,}")
    out["hover_perf"] = _pen.map(lambda p: f"1,000가구 중 약 {p * 10:.1f}가구 구매")

    out.attrs["median_penetration"] = med_pen
    out.attrs["median_target_density"] = med_den
    return out


# ══════════════════════════════════════════════════════════════════
# CRM 집계: 매장별 행정동 구매건수
# ══════════════════════════════════════════════════════════════════

def _paginated_select(client, table: str, columns: str, filters: list[tuple] | None = None, page: int = 1000) -> list[dict]:
    """PostgREST 1000행 상한을 우회하는 페이지네이션 SELECT."""
    all_rows: list[dict] = []
    offset = 0
    while True:
        q = client.table(table).select(columns)
        for f in (filters or []):
            q = getattr(q, f[0])(*f[1:])
        r = q.range(offset, offset + page - 1).execute()
        rows = (r.data or []) if hasattr(r, "data") else []
        all_rows.extend(rows)
        if len(rows) < page:
            break
        offset += page
    return all_rows


@st.cache_data(ttl=600, show_spinner=False)
def aggregate_purchase_count_by_dong(
    _client, store_keys: list[str], start_date: str, end_date: str,
) -> pd.DataFrame:
    """
    선택한 매장(들)의 app_orders + app_customers 조인 → admin_dong_code 별 구매건수·금액 집계.
    order_date 가 [start_date, end_date] (YYYY-MM-DD, 포함) 구간인 주문만 집계한다.
    반환: [admin_dong_code, admin_dong_name, purchase_count, purchase_amount]
    purchase_amount 는 app_orders.total_amount 합계.

    10분 캐싱(ttl=600) — 매장/기간이 동일하면 Supabase 재조회 없이 즉시 반환.
    `_client` 는 언더스코어 접두사로 Streamlit 캐시 해싱에서 제외됨(공식 규칙).
    최신 데이터 강제 조회가 필요하면 `aggregate_purchase_count_by_dong.clear()` 호출.
    """
    def _empty_with_coverage(total_orders: int, mapped_orders: int) -> pd.DataFrame:
        _out = pd.DataFrame(columns=[
            "admin_dong_code", "admin_dong_name", "purchase_count", "purchase_amount",
        ])
        _out.attrs["total_orders_in_period"] = total_orders
        _out.attrs["mapped_orders_in_period"] = mapped_orders
        return _out

    client = _client
    if not store_keys:
        return _empty_with_coverage(0, 0)
    orders = _paginated_select(
        client, "app_orders", "id, customer_id, db_filename, order_date, total_amount",
        filters=[
            ("in_", "db_filename", store_keys),
            ("gte", "order_date", start_date),
            ("lte", "order_date", end_date),
        ],
    )
    if not orders:
        return _empty_with_coverage(0, 0)
    cust_ids = sorted({int(o["customer_id"]) for o in orders if o.get("customer_id") is not None})
    if not cust_ids:
        return _empty_with_coverage(len(orders), 0)
    custs: list[dict] = []
    _CHUNK = 500
    for i in range(0, len(cust_ids), _CHUNK):
        _batch = cust_ids[i:i + _CHUNK]
        try:
            r = client.table("app_customers").select(
                "id, admin_dong_code, admin_dong_name"
            ).in_("id", _batch).execute()
            custs.extend((r.data or []) if hasattr(r, "data") else [])
        except Exception as e:
            logger.warning("app_customers 배치 조회 실패 (%d ids): %s", len(_batch), e)
    if not custs:
        return _empty_with_coverage(len(orders), 0)
    cdf = pd.DataFrame(custs).rename(columns={"id": "customer_id"})
    odf = pd.DataFrame(orders)
    odf["total_amount"] = pd.to_numeric(odf.get("total_amount"), errors="coerce").fillna(0)
    merged_all = odf.merge(cdf[["customer_id", "admin_dong_code", "admin_dong_name"]], on="customer_id", how="left")
    mapped_mask = merged_all["admin_dong_code"].notna() & (merged_all["admin_dong_code"] != "")
    merged = merged_all[mapped_mask]
    if merged.empty:
        return _empty_with_coverage(len(merged_all), 0)
    grp = merged.groupby(["admin_dong_code", "admin_dong_name"], as_index=False).agg(
        purchase_count=("id", "count"),
        purchase_amount=("total_amount", "sum"),
    )
    grp["purchase_amount"] = grp["purchase_amount"].fillna(0).round(0).astype("int64")
    grp = grp.sort_values("purchase_count", ascending=False)
    grp.attrs["total_orders_in_period"] = len(merged_all)
    grp.attrs["mapped_orders_in_period"] = int(mapped_mask.sum())
    return grp


def _attach_sigungu(df: pd.DataFrame) -> pd.DataFrame:
    """admin_dong_code 기준으로 GeoJSON 의 시도/시군구명을 조인해 `sigungu_label` 컬럼을 추가.

    카카오 code(10자리)를 GeoJSON adm_cd2 로 우선 매칭하고, 실패 시 8자리 adm_cd 로 매칭한다
    (_render_map 의 매칭 로직과 동일). GeoJSON 스코프 밖이라 매칭이 안 되는 행정동은
    '기타(미분류)' 로 묶어 필터에서도 확인할 수 있게 한다.
    """
    if df is None or df.empty:
        out = df.copy() if df is not None else pd.DataFrame()
        out["sigungu_label"] = pd.Series(dtype=str)
        return out
    idx = build_geojson_index(load_admdong_geojson())
    out = df.copy()
    key10 = out["admin_dong_code"].astype(str)
    key8 = key10.str[:8]
    _label_by10 = {
        c: f"{s} {g}".strip() for c, s, g in zip(idx["adm_cd2"], idx["sidonm"], idx["sggnm"])
    }
    _label_by8 = {
        c: f"{s} {g}".strip() for c, s, g in zip(idx["adm_cd"], idx["sidonm"], idx["sggnm"])
    }
    out["sigungu_label"] = key10.map(_label_by10)
    out["sigungu_label"] = out["sigungu_label"].fillna(key8.map(_label_by8))
    out["sigungu_label"] = out["sigungu_label"].replace("", pd.NA).fillna("기타(미분류)")
    return out


# ══════════════════════════════════════════════════════════════════
# 백필: 미변환 고객 → 행정동 매핑
# ══════════════════════════════════════════════════════════════════

_NOT_FOUND_RETRY_COOLDOWN_DAYS = 7
_ADMIN_DONG_FAIL_REASONS = ("not_found", "no_h_region", "rate_limited", "no_kakao_key")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def count_customers_needing_admin_dong(client, store_names: list[str] | None = None) -> int:
    """admin_dong_code 가 없고 address 는 있는 고객 수 (실패사유 무관, 전체 미매핑)."""
    try:
        q = client.table("app_customers").select("id", count="exact").is_("admin_dong_code", "null").not_.is_("address", "null")
        if store_names:
            q = q.in_("store_name", store_names)
        r = q.execute()
        return int(getattr(r, "count", 0) or 0)
    except Exception as e:
        logger.warning("count_customers_needing_admin_dong 실패: %s", e)
        return 0


def get_admin_dong_fail_breakdown(client, store_names: list[str] | None = None) -> dict[str, int]:
    """실패 사유별 건수 + '미시도'(한 번도 시도한 적 없는 건) 건수. UI 브레이크다운 패널용."""
    out: dict[str, int] = {}
    try:
        for reason in _ADMIN_DONG_FAIL_REASONS:
            q = (
                client.table("app_customers").select("id", count="exact")
                .is_("admin_dong_code", "null").not_.is_("address", "null")
                .eq("admin_dong_fail_reason", reason)
            )
            if store_names:
                q = q.in_("store_name", store_names)
            r = q.execute()
            out[reason] = int(getattr(r, "count", 0) or 0)
        total = count_customers_needing_admin_dong(client, store_names)
        out["미시도"] = max(0, total - sum(out.values()))
        out["_total"] = total
    except Exception as e:
        logger.warning("get_admin_dong_fail_breakdown 실패: %s", e)
    return out


def _fetch_customers_needing_admin_dong(
    client, store_names: list[str] | None, limit: int, *, force_retry: bool = False,
) -> list[dict]:
    """address 는 있고 admin_dong_code 는 비어있는 고객 최대 `limit` 명 조회.
    latitude/longitude 가 이미 있으면 재활용해 카카오 주소검색 호출 절감.

    최근 `_NOT_FOUND_RETRY_COOLDOWN_DAYS`일 내 fail_reason='not_found' 로 기록된 건은
    동일 주소 무한 재시도를 막기 위해 기본적으로 제외한다 (rate_limited 는 항상 재시도 대상).
    force_retry=True 로 스킵 없이 강제 포함 가능."""
    try:
        q = client.table("app_customers").select(
            "id, address, latitude, longitude, store_name, "
            "admin_dong_fail_reason, admin_dong_fail_at, admin_dong_fail_count"
        ).is_("admin_dong_code", "null").not_.is_("address", "null")
        if store_names:
            q = q.in_("store_name", store_names)
        # not_found 쿨다운 스킵은 파이썬 필터로 처리하므로 여유있게 더 가져온다.
        r = q.limit(limit if force_retry else limit * 3).execute()
        rows = (r.data or []) if hasattr(r, "data") else []
        if force_retry:
            return rows[:limit]

        cutoff = datetime.now(timezone.utc) - timedelta(days=_NOT_FOUND_RETRY_COOLDOWN_DAYS)
        eligible = []
        for row in rows:
            if row.get("admin_dong_fail_reason") == "not_found":
                failed_at = _parse_iso_dt(row.get("admin_dong_fail_at"))
                if failed_at is not None and failed_at >= cutoff:
                    continue  # 최근 not_found → 이번 배치에서 스킵
            eligible.append(row)
        return eligible[:limit]
    except Exception as e:
        logger.warning("고객 조회 실패: %s", e)
        return []


def backfill_admin_dong_batch(
    client,
    store_names: list[str] | None = None,
    max_records: int = 100,
    sleep_between_calls_sec: float = 0.05,
    progress_callback=None,
    force_retry: bool = False,
) -> dict:
    """
    행정동 미매핑 고객을 최대 `max_records` 건 처리.
    반환: {"processed", "updated", "failed", "fail_reasons": {stage: count}, "errors": [...]}
    """
    result: dict = {"processed": 0, "updated": 0, "failed": 0, "fail_reasons": {}, "errors": []}
    rows = _fetch_customers_needing_admin_dong(client, store_names, max_records, force_retry=force_retry)
    if not rows:
        return result

    for _i, row in enumerate(rows):
        result["processed"] += 1
        cid = row.get("id")
        addr = (row.get("address") or "").strip()
        lat = row.get("latitude")
        lon = row.get("longitude")
        try:
            lat_f = float(lat) if lat is not None else None
            lon_f = float(lon) if lon is not None else None
        except Exception:
            lat_f, lon_f = None, None

        try:
            info = geocode_to_admin_dong(addr, lat=lat_f, lon=lon_f)
        except Exception as e:
            info = {"ok": False, "fail_stage": "rate_limited", "fail_detail": f"예외: {e}"}

        if info.get("ok") and info.get("admin_dong_code"):
            try:
                client.table("app_customers").update({
                    "admin_dong_name": info.get("admin_dong_name"),
                    "admin_dong_code": info.get("admin_dong_code"),
                    "admin_dong_fail_reason": None,
                    "admin_dong_fail_at": None,
                }).eq("id", cid).execute()
                result["updated"] += 1
            except Exception as e:
                result["failed"] += 1
                result["errors"].append(f"id={cid}: update 실패 {e}")
        else:
            result["failed"] += 1
            stage = info.get("fail_stage") or "not_found"
            result["fail_reasons"][stage] = result["fail_reasons"].get(stage, 0) + 1
            try:
                client.table("app_customers").update({
                    "admin_dong_fail_reason": stage,
                    "admin_dong_fail_at": _now_iso(),
                    "admin_dong_fail_count": int(row.get("admin_dong_fail_count") or 0) + 1,
                }).eq("id", cid).execute()
            except Exception as e:
                result["errors"].append(f"id={cid}: 실패사유 저장 오류 {e}")
            if info.get("fail_detail"):
                result["errors"].append(f"id={cid}: [{stage}] {info['fail_detail']}")

        if progress_callback is not None:
            try:
                progress_callback(_i + 1, len(rows))
            except Exception:
                pass
        if sleep_between_calls_sec > 0:
            time.sleep(sleep_between_calls_sec)
    return result


# ══════════════════════════════════════════════════════════════════
# 인구 데이터 배치 조회 (행정동 코드 리스트) — 영구 캐시 + 병렬 조회
# ══════════════════════════════════════════════════════════════════

_POP_CACHE_TABLE = "app_dong_population_cache"
_AGE_BUCKET_COLUMNS: list[str] = [age_bucket_column(k) for k in AGE_BUCKET_KEYS]
_POP_CACHE_COLUMNS = [
    "admin_dong_code", "total_households", "total_population",
    *_AGE_BUCKET_COLUMNS,
]


def _load_population_cache_from_db(client, admin_dong_codes: list[str], yyyymm: str) -> dict[str, dict]:
    """app_dong_population_cache 에서 (code, yyyymm) 일치 행을 벌크 조회 → {code: row} dict."""
    if client is None or not admin_dong_codes:
        return {}
    hits: dict[str, dict] = {}
    _CHUNK = 500
    codes = [str(c) for c in admin_dong_codes if c]
    try:
        for i in range(0, len(codes), _CHUNK):
            _batch = codes[i:i + _CHUNK]
            r = (
                client.table(_POP_CACHE_TABLE)
                .select(",".join(_POP_CACHE_COLUMNS))
                .eq("yyyymm", yyyymm)
                .in_("admin_dong_code", _batch)
                .execute()
            )
            for row in (r.data or []) if hasattr(r, "data") else []:
                hits[str(row["admin_dong_code"])] = row
    except Exception as e:
        logger.warning("행정동 인구 캐시 조회 실패 (테이블 미생성 가능): %s", e)
    return hits


def _save_population_cache_to_db(client, rows: list[dict], yyyymm: str) -> None:
    """새로 조회한 인구 데이터를 app_dong_population_cache 에 upsert (실패해도 렌더링은 계속)."""
    if client is None or not rows:
        return
    payload = []
    for r in rows:
        entry = {
            "admin_dong_code": r["admin_dong_code"],
            "yyyymm": yyyymm,
            "total_households": r["total_households"],
            "total_population": r["total_population"],
        }
        for k in AGE_BUCKET_KEYS:
            entry[age_bucket_column(k)] = int(r.get(age_bucket_column(k)) or 0)
        payload.append(entry)
    try:
        client.table(_POP_CACHE_TABLE).upsert(
            payload, on_conflict="admin_dong_code,yyyymm"
        ).execute()
    except Exception as e:
        logger.warning("행정동 인구 캐시 저장 실패 (테이블 미생성 가능): %s", e)


def _fetch_one_dong_population(code: str, yyyymm: str) -> dict:
    """행정동 1건의 인구+연령 API 를 호출해 캐시 저장용 row 로 변환 (스레드풀 워커)."""
    pop = fetch_admin_dong_population(str(code), yyyymm)
    age = fetch_admin_dong_age_population(str(code), yyyymm)
    buckets: dict[str, int] = age.get("age_buckets") or _empty_age_buckets()
    row = {
        "admin_dong_code": str(code),
        "total_households": int(pop.get("total_households") or 0),
        "total_population": int(pop.get("total_population") or 0),
        "error": (pop.get("error") or "") + (" | " + age.get("error") if age.get("error") else ""),
    }
    for k in AGE_BUCKET_KEYS:
        row[age_bucket_column(k)] = int(buckets.get(k) or 0)
    return row


_POP_BULK_COLUMNS: list[str] = [
    "admin_dong_code", "total_households", "total_population",
    *_AGE_BUCKET_COLUMNS,
    "error",
]


def _empty_pop_row(code: str, error: str = "") -> dict:
    row = {
        "admin_dong_code": code, "total_households": 0, "total_population": 0,
        "error": error,
    }
    for k in AGE_BUCKET_KEYS:
        row[age_bucket_column(k)] = 0
    return row


def fetch_population_bulk(
    client, admin_dong_codes: list[str], yyyymm: str, max_workers: int = 8,
) -> pd.DataFrame:
    """
    행정동코드 리스트 → 인구·세대·연령대별 인구 DataFrame.

    1) app_dong_population_cache 에서 (code, yyyymm) 벌크 조회로 캐시 적중분 확보.
    2) 캐시 미스난 코드만 ThreadPoolExecutor 로 병렬 호출 (인구 API + 연령 API).
    3) 신규 조회분은 캐시 테이블에 upsert 해 다음 조회부터 즉시 재사용.

    반환 컬럼: admin_dong_code, total_households, total_population,
             age_10_population ~ age_60_population, age_70plus_population, error.
    """
    codes = [str(c) for c in admin_dong_codes if c]
    if not codes:
        return pd.DataFrame(columns=_POP_BULK_COLUMNS)

    cache_hits = _load_population_cache_from_db(client, codes, yyyymm)
    missing = [c for c in codes if c not in cache_hits]

    fetched_rows: list[dict] = []
    if missing:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_fetch_one_dong_population, c, yyyymm): c for c in missing}
            for fut in as_completed(futures):
                try:
                    fetched_rows.append(fut.result())
                except Exception as e:
                    code = futures[fut]
                    fetched_rows.append(_empty_pop_row(code, error=f"병렬 조회 예외: {e}"))
        # 정상 조회된(에러 없는) 행만 영구 캐시에 저장 — 오류 응답을 캐시해 재시도를 막지 않도록.
        _ok_rows = [r for r in fetched_rows if not (r.get("error") or "").strip()]
        _save_population_cache_to_db(client, _ok_rows, yyyymm)

    rows: list[dict] = []
    for code in codes:
        if code in cache_hits:
            hit = cache_hits[code]
            row = {
                "admin_dong_code": code,
                "total_households": int(hit.get("total_households") or 0),
                "total_population": int(hit.get("total_population") or 0),
                "error": "",
            }
            for k in AGE_BUCKET_KEYS:
                row[age_bucket_column(k)] = int(hit.get(age_bucket_column(k)) or 0)
            rows.append(row)
    rows.extend(fetched_rows)

    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=_POP_BULK_COLUMNS)


def prefetch_all_dong_population(client, yyyymm: str, max_workers: int = 8) -> dict:
    """
    관리자용: app_customers 전체의 distinct admin_dong_code 를 대상으로 인구 데이터를
    미리 캐시 테이블에 채워 넣는다 (어떤 매장을 조회하든 즉시 캐시 히트하도록).

    반환: {"total_codes": int, "cache_hits": int, "newly_fetched": int, "failed": int}
    """
    try:
        r = client.table("app_customers").select("admin_dong_code").not_.is_("admin_dong_code", "null").execute()
        codes = sorted({str(row["admin_dong_code"]) for row in (r.data or []) if row.get("admin_dong_code")})
    except Exception as e:
        logger.warning("prefetch_all_dong_population: 행정동코드 목록 조회 실패: %s", e)
        return {"total_codes": 0, "cache_hits": 0, "newly_fetched": 0, "failed": 0}

    if not codes:
        return {"total_codes": 0, "cache_hits": 0, "newly_fetched": 0, "failed": 0}

    cache_hits = _load_population_cache_from_db(client, codes, yyyymm)
    missing = [c for c in codes if c not in cache_hits]

    fetched_rows: list[dict] = []
    if missing:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_fetch_one_dong_population, c, yyyymm): c for c in missing}
            for fut in as_completed(futures):
                try:
                    fetched_rows.append(fut.result())
                except Exception as e:
                    fetched_rows.append(_empty_pop_row(futures[fut], error=f"병렬 조회 예외: {e}"))
        _ok_rows = [r for r in fetched_rows if not (r.get("error") or "").strip()]
        _save_population_cache_to_db(client, _ok_rows, yyyymm)
        _failed = len(fetched_rows) - len(_ok_rows)
    else:
        _failed = 0

    return {
        "total_codes": len(codes),
        "cache_hits": len(cache_hits),
        "newly_fetched": len(missing) - _failed,
        "failed": _failed,
    }


# ══════════════════════════════════════════════════════════════════
# Streamlit UI
# ══════════════════════════════════════════════════════════════════

def _get_supabase_client_via_app():
    """app.py 의 get_supabase_client 를 지연 import 하여 사용 (순환 방지)."""
    from app import get_supabase_client  # noqa: WPS433
    return get_supabase_client()


def _get_stores_via_app() -> list[dict]:
    """app.py 의 매장 목록 조회 재사용."""
    try:
        from app import _get_supabase_stores_list  # noqa: WPS433
        return _get_supabase_stores_list() or []
    except Exception:
        return []


def render_dong_commercial_map() -> None:
    """상권 퍼포먼스 맵 페이지 렌더링 (main entry)."""
    st.header("🗺️ 동 단위 상권 퍼포먼스 맵")
    st.caption(
        "고객 주소를 카카오 API 로 행정동까지 지오코딩하고, "
        "행정안전부 인구·세대 공공데이터와 결합해 시장 침투율 × 타겟 밀집도 "
        "매트릭스로 상권을 A/B/C/D 4그룹으로 분류합니다."
    )

    # ── 진단: 서비스 키 · GeoJSON 상태 ─────────────────────────
    with st.expander("🔧 연동 진단 (API 키 · GeoJSON)", expanded=False):
        _render_diagnostic_panel()

    client, err = _get_supabase_client_via_app()
    if err or not client:
        st.error(f"Supabase 연결 실패: {err}")
        return

    # ── 매장 · 분석기간 선택 ───────────────────────────────
    stores = _get_stores_via_app()
    if not stores:
        st.info("매장 정보를 불러올 수 없습니다. 매장 계정 관리 화면에서 확인해 주세요.")
        return

    current_user = st.session_state.get("current_user", {}) or {}
    role = current_user.get("role", "user")
    current_db = st.session_state.get("current_db")
    # 다매장 배정 직원(app_user_stores)은 본인이 접근 가능한 매장 범위 내에서
    # 여러 매장을 함께 선택해 집계할 수 있게 한다. (allowed_stores: (store_id, db_filename, store_name))
    allowed_dbfns = {
        s[1] for s in (current_user.get("allowed_stores") or []) if len(s) > 1 and s[1]
    }
    if role == "superadmin":
        store_options: list[tuple[str, str]] = [
            (s["db_filename"], s["store_name"]) for s in stores if s.get("db_filename")
        ]
        default_indices = list(range(len(store_options)))
    elif allowed_dbfns:
        store_options = [
            (s["db_filename"], s["store_name"])
            for s in stores if s.get("db_filename") in allowed_dbfns
        ]
        default_indices = list(range(len(store_options)))
    else:
        store_options = [
            (s["db_filename"], s["store_name"])
            for s in stores if s.get("db_filename") == current_db
        ]
        default_indices = [0] if store_options else []

    if not store_options:
        st.info("접근 가능한 매장이 없습니다.")
        return

    c1, c2 = st.columns([2, 1])
    with c1:
        _labels = [n for _, n in store_options]
        sel_labels = st.multiselect(
            "매장 선택 (다중 선택 가능)",
            options=_labels,
            default=[_labels[i] for i in default_indices],
            key="dcm_stores",
        )
    with c2:
        _today = pd.Timestamp.now().date()
        _default_start = (pd.Timestamp.now() - pd.DateOffset(years=1)).date()
        _preset = st.selectbox(
            "기간 프리셋",
            ["직접 입력", "연간", "상반기", "하반기"],
            key="dcm_period_preset",
            help="연간/상반기/하반기 선택 시 기준 연도로 분석 기간이 자동 설정됩니다. "
                 "'직접 입력'은 기존처럼 날짜 범위를 자유롭게 지정합니다.",
        )
        if _preset == "직접 입력":
            period_range = st.date_input(
                "분석 기간 (구매건수 집계 구간)",
                value=(_default_start, _today),
                help="이 기간의 구매건수를 행정동별로 합산해 침투율을 계산합니다. "
                     "인구·세대 데이터는 기간 종료월 기준으로 1회만 조회합니다.",
                key="dcm_period_range",
            )
        else:
            _preset_year = st.number_input(
                "기준 연도", min_value=2000, max_value=int(_today.year),
                value=int(_today.year), step=1, key="dcm_preset_year",
            )
            _py = int(_preset_year)
            if _preset == "연간":
                period_range = (date(_py, 1, 1), date(_py, 12, 31))
            elif _preset == "상반기":
                period_range = (date(_py, 1, 1), date(_py, 6, 30))
            else:  # 하반기
                period_range = (date(_py, 7, 1), date(_py, 12, 31))
            st.caption(f"📅 {period_range[0]} ~ {period_range[1]} (구매일 기준 집계)")

    if not sel_labels:
        st.info("최소 1개 이상의 매장을 선택해 주세요.")
        return
    if not (isinstance(period_range, (tuple, list)) and len(period_range) == 2):
        st.warning("분석 기간의 시작일과 종료일을 모두 선택해 주세요.")
        return
    start_date, end_date = period_range
    if start_date > end_date:
        st.warning("시작일은 종료일보다 이전이어야 합니다.")
        return
    start_date_str = start_date.isoformat()
    end_date_str = end_date.isoformat()
    # 인구·세대 데이터는 기간 종료월 스냅샷만 사용. 단, 이번 달 통계는 행안부가
    # 아직 게시하지 않아 API가 거부하므로 최근 확정월(전월)로 자동 보정한다.
    yyyymm = _clamp_population_yyyymm(end_date.strftime("%Y%m"))

    sel_dbfns = [dbf for dbf, name in store_options if name in sel_labels]
    sel_store_names = [name for _, name in store_options if name in sel_labels]

    # ── 백필 UI ─────────────────────────────────────────
    with st.expander("🔄 미변환 고객 → 행정동 매핑 백필", expanded=False):
        _render_backfill_panel(client, sel_store_names)

    # ── 관리자용 인구 데이터 사전 캐시 ─────────────────────
    if role == "superadmin":
        with st.expander("⚡ 인구 데이터 사전 캐시 (전체 매장 대상)", expanded=False):
            st.caption(
                f"인구 기준월({yyyymm}) 데이터를 전체 고객의 행정동 기준으로 미리 내려받아 "
                "`app_dong_population_cache` 테이블에 저장합니다. "
                "이후 어떤 매장·기간을 조회해도 (같은 종료월이면) 즉시 캐시 히트됩니다."
            )
            if st.button("🔄 전체 캐시 미리 채우기", key="dcm_prefetch_btn"):
                with st.spinner("전체 행정동 인구 데이터 사전 캐시 중… (병렬 조회)"):
                    _stat = prefetch_all_dong_population(client, yyyymm)
                st.success(
                    f"전체 {_stat['total_codes']}개 행정동 · 캐시 적중 {_stat['cache_hits']}건 · "
                    f"신규 조회 {_stat['newly_fetched']}건 · 실패 {_stat['failed']}건"
                )

    # ══════════════════════════════════════════════════════════════
    # 2단계 — 핵심상권(시·군·동) 계층 선택
    # ══════════════════════════════════════════════════════════════
    st.markdown("### 2. 핵심상권 선정 (시·군·동)")
    _geo_idx = build_geojson_index(load_admdong_geojson())
    if _geo_idx.empty:
        st.error("GeoJSON 이 로드되지 않아 행정동 선택을 표시할 수 없습니다. 위 '연동 진단' 패널을 확인해 주세요.")
        return

    _sido_options = sorted(x for x in _geo_idx["sidonm"].unique().tolist() if x)
    _prev_sido = [s for s in st.session_state.get("dcm_sido", []) if s in _sido_options]
    _sd_col, _sgg_col, _dong_col = st.columns([1, 1, 2])
    with _sd_col:
        sel_sido = st.multiselect(
            "시도",
            options=_sido_options,
            default=_prev_sido or _sido_options[:1],
            key="dcm_sido",
            help="분석 대상 시도. 선택한 시도에 속한 시군구만 아래에서 표시됩니다.",
        )
    _sgg_pool = _geo_idx[_geo_idx["sidonm"].isin(sel_sido)] if sel_sido else _geo_idx.iloc[0:0]
    _sgg_options = sorted(x for x in _sgg_pool["sggnm"].unique().tolist() if x)
    _prev_sgg = [s for s in st.session_state.get("dcm_sgg", []) if s in _sgg_options]
    with _sgg_col:
        sel_sgg = st.multiselect(
            "시군구",
            options=_sgg_options,
            default=_prev_sgg or _sgg_options,
            key="dcm_sgg",
            help="선택 시도 안에서 분석할 시군구. 선택 시군구에 속한 행정동만 아래에서 표시됩니다.",
        )
    _dong_pool = _sgg_pool[_sgg_pool["sggnm"].isin(sel_sgg)] if sel_sgg else _sgg_pool.iloc[0:0]
    # adm_nm 은 "시도 시군구 행정동" 전체 경로 (라벨) / adm_cd2 는 10자리 코드 (값).
    _dong_labels_all = sorted(x for x in _dong_pool["adm_nm"].unique().tolist() if x)
    _dong_code_by_label = dict(zip(_dong_pool["adm_nm"], _dong_pool["adm_cd2"]))
    _dong_name_by_code = {
        str(row["adm_cd2"]): str(row["adm_nm"]).split()[-1]
        for _, row in _dong_pool.iterrows() if row.get("adm_cd2")
    }
    # 시군구별 "구전체" 가상 옵션 (예: 남구전체, 동구전체) — 선택 시 해당 시군구 전 행정동으로 펼침.
    # 현재 선택된 시군구에 대해서만 표시한다.
    _sgg_for_all = sorted(sel_sgg) if sel_sgg else []
    _sgg_all_labels = [f"{sgg}전체" for sgg in _sgg_for_all]
    _sgg_all_to_codes: dict[str, list[str]] = {
        f"{sgg}전체": [
            str(c) for c in _dong_pool.loc[_dong_pool["sggnm"] == sgg, "adm_cd2"].tolist() if c
        ]
        for sgg in _sgg_for_all
    }
    _dong_options = _sgg_all_labels + _dong_labels_all
    _prev_dongs = [d for d in st.session_state.get("dcm_dongs", []) if d in _dong_options]
    with _dong_col:
        sel_dong_labels = st.multiselect(
            "행정동 (핵심상권)",
            options=_dong_options,
            default=_prev_dongs or _sgg_all_labels,
            key="dcm_dongs",
            help="시군구 '○○전체'(예: 남구전체)를 고르면 해당 구의 모든 행정동이 포함됩니다. "
                 "개별 동도 함께 선택 가능합니다. 렌더링 시 구매 없는 동은 침투율 0으로 포함됩니다.",
        )
    # "남구전체" 등은 해당 시군구 코드 목록으로 펼치고, 개별 동 라벨은 직접 매핑. 중복 제거.
    _sel_codes_ordered: list[str] = []
    _seen_codes: set[str] = set()
    for _lab in sel_dong_labels:
        if _lab in _sgg_all_to_codes:
            _codes_for = _sgg_all_to_codes[_lab]
        elif _lab in _dong_code_by_label:
            _codes_for = [str(_dong_code_by_label[_lab])]
        else:
            continue
        for _c in _codes_for:
            if _c and _c not in _seen_codes:
                _seen_codes.add(_c)
                _sel_codes_ordered.append(_c)
    sel_adm_codes = _sel_codes_ordered

    # ══════════════════════════════════════════════════════════════
    # 3단계 — 핵심 타겟 연령대
    # ══════════════════════════════════════════════════════════════
    st.markdown("### 3. 핵심 타겟 연령대")
    _default_age_labels = [AGE_BUCKET_LABELS[k] for k in DEFAULT_TARGET_AGE_KEYS]
    sel_age_labels = st.multiselect(
        "핵심 타겟 연령대 (밀집도 계산 대상)",
        options=[AGE_BUCKET_LABELS[k] for k in AGE_BUCKET_KEYS],
        default=_default_age_labels,
        key="dcm_target_ages",
        help="선택한 연령대의 남녀 인구 합을 총인구로 나눠 타겟 밀집도를 계산합니다. "
             "렌더링 후에도 변경 시 인구 API 재호출 없이 즉시 반영됩니다.",
    )
    _label_to_key = {v: k for k, v in AGE_BUCKET_LABELS.items()}
    sel_age_keys = [_label_to_key[l] for l in sel_age_labels if l in _label_to_key]

    # ══════════════════════════════════════════════════════════════
    # 4단계 — 상권 맵 렌더링
    # ══════════════════════════════════════════════════════════════
    st.markdown("### 4. 상권 맵 렌더링")
    _missing = []
    if not sel_adm_codes:
        _missing.append("2단계에서 행정동 1개 이상")
    if not sel_age_keys:
        _missing.append("3단계에서 연령대 1개 이상")
    if _missing:
        st.info("다음을 먼저 선택해 주세요 — " + " · ".join(_missing))

    _can_render = bool(sel_labels and sel_adm_codes and sel_age_keys)
    _c_btn1, _c_btn2 = st.columns([2, 1])
    with _c_btn1:
        do_render = st.button(
            "🗺️ 상권 맵 렌더링", type="primary",
            disabled=not _can_render, key="dcm_render_btn",
        )
    with _c_btn2:
        force_refresh = st.checkbox(
            "최신 데이터로 새로고침", key="dcm_force_refresh",
            help="캐시를 무시하고 Supabase 에서 구매건수를 다시 집계합니다 (당일 신규 주문 반영 등).",
        )

    if not _can_render:
        return

    # 캐시 키: 매장·기간·선택 행정동 집합. 연령은 사후 재계산 대상이라 키에서 제외.
    _sel_adm_key = tuple(sorted(sel_adm_codes))
    _cache_key = (tuple(sorted(sel_dbfns)), start_date_str, end_date_str, _sel_adm_key)
    _pop_cache_key = (_cache_key, yyyymm)

    if do_render:
        if force_refresh:
            aggregate_purchase_count_by_dong.clear()
        with st.spinner("행정동별 구매건수 집계 중…"):
            _raw_df = aggregate_purchase_count_by_dong(client, sel_dbfns, start_date_str, end_date_str)

        # 선택 행정동 스켈레톤 — GeoJSON 기준으로 만든 뒤 CRM 을 left-join 하여
        # 구매가 없는 동도 침투율 0 으로 포함(잠재 상권 분석).
        _skeleton = pd.DataFrame({
            "admin_dong_code": [str(c) for c in sel_adm_codes],
            "admin_dong_name": [_dong_name_by_code.get(str(c), str(c)) for c in sel_adm_codes],
        })
        if _raw_df is None or _raw_df.empty:
            _merged = _skeleton.copy()
            _merged["purchase_count"] = 0
            _merged["purchase_amount"] = 0
        else:
            _amt_cols = ["admin_dong_code", "purchase_count"]
            if "purchase_amount" in _raw_df.columns:
                _amt_cols.append("purchase_amount")
            _crm_slim = _raw_df[_amt_cols].copy()
            _crm_slim["admin_dong_code"] = _crm_slim["admin_dong_code"].astype(str)
            _merged = _skeleton.merge(_crm_slim, on="admin_dong_code", how="left")
            _merged["purchase_count"] = _merged["purchase_count"].fillna(0).astype(int)
            if "purchase_amount" not in _merged.columns:
                _merged["purchase_amount"] = 0
            _merged["purchase_amount"] = (
                pd.to_numeric(_merged["purchase_amount"], errors="coerce").fillna(0).round(0).astype("int64")
            )
        _merged = _attach_sigungu(_merged)

        # attrs 캡처 (요약 지표의 미매핑 커버리지에 사용). skeleton 은 attrs 를 가지지 않으므로
        # 원본 CRM 결과의 attrs 를 사용한다.
        _raw_attrs = _raw_df.attrs if (_raw_df is not None) else {}
        st.session_state["dcm_mapping_coverage"] = {
            "total_orders": int(_raw_attrs.get("total_orders_in_period", 0) or 0),
            "mapped_orders": int(_raw_attrs.get("mapped_orders_in_period", 0) or 0),
        }
        st.session_state["dcm_raw_crm_df"] = _merged
        st.session_state["dcm_raw_cache_key"] = _cache_key
        # 새 렌더 요청이 오면 인구 데이터 캐시도 초기화 (아래에서 선택 동에 맞게 재적재).
        st.session_state.pop("dcm_pop_df", None)
        st.session_state.pop("dcm_pop_cache_key", None)

    if st.session_state.get("dcm_raw_cache_key") != _cache_key:
        st.info("현재 선택으로 '🗺️ 상권 맵 렌더링'을 눌러 주세요. (매장·기간·행정동을 바꿨다면 다시 눌러야 반영됩니다.)")
        return

    crm_df = st.session_state.get("dcm_raw_crm_df")
    if crm_df is None or crm_df.empty:
        st.warning("선택한 행정동에 렌더링할 데이터가 없습니다.")
        return

    st.caption(f"📅 분석 기간: {start_date} ~ {end_date} · 인구 기준월: {yyyymm}")
    _n_purch = int((crm_df["purchase_count"] > 0).sum())
    _amt_sum = int(pd.to_numeric(crm_df.get("purchase_amount"), errors="coerce").fillna(0).sum())
    st.success(
        f"선택 행정동 {len(crm_df)}개 (구매 발생 {_n_purch}개 · 잠재 {len(crm_df) - _n_purch}개), "
        f"기간 내 총 구매건수 {int(crm_df['purchase_count'].sum())} 건 · "
        f"구매금액 합계 {_amt_sum:,} 원"
    )

    # 인구 데이터: 선택 행정동에 대해서만 조회 (불필요 API 호출 절감).
    pop_df = st.session_state.get("dcm_pop_df")
    if pop_df is None or st.session_state.get("dcm_pop_cache_key") != _pop_cache_key:
        with st.spinner("행정안전부 인구·세대 데이터 조회 중… (영구 캐시 + 병렬 조회)"):
            pop_df = fetch_population_bulk(
                client, crm_df["admin_dong_code"].astype(str).tolist(), yyyymm,
            )
        st.session_state["dcm_pop_df"] = pop_df
        st.session_state["dcm_pop_cache_key"] = _pop_cache_key

    _err_msgs = [x for x in pop_df["error"].astype(str).tolist() if x.strip()]
    if pop_df.empty or all(pop_df["total_population"].fillna(0) == 0):
        st.error(
            "행정안전부 API 응답에서 인구 데이터를 파싱하지 못했습니다. "
            "위 '연동 진단' 패널에서 API 키·엔드포인트를 확인해 주세요."
        )
        if _err_msgs:
            with st.expander("응답 오류 상세", expanded=False):
                for m in _err_msgs[:20]:
                    st.text(m)

    kpi_df = compute_dong_kpi(crm_df, pop_df, sel_age_keys)
    kpi_df = assign_quadrant(kpi_df)

    # ── KPI 요약 ─────────────────────────────────────
    _render_summary_metrics(kpi_df)

    # ── 좌: 지도 (범주형 행동_전략) / 우: 2x2 매트릭스 ──
    _left, _right = st.columns([7, 3])
    with _left:
        _render_map(kpi_df)
    with _right:
        st.markdown("**2x2 매트릭스**")
        st.caption("가로: 우리 매장 침투율 · 세로: 타겟 인구 비중")
        _render_quadrant_scatter(kpi_df)

    # ── 하단: 집중 공략 Top 5 (즉각 행동 지침) ──
    _render_action_top5(kpi_df)

    # ── 상세 표 ─────────────────────────────────────
    _render_table(kpi_df)

    # ── PDF 출력 ─────────────────────────────────────
    _render_pdf_export_panel(
        kpi_df,
        store_names=sel_store_names,
        start_date=str(start_date),
        end_date=str(end_date),
        yyyymm=yyyymm,
        age_keys=sel_age_keys,
        cache_key=_cache_key,
    )


# ══════════════════════════════════════════════════════════════════
# 하위 렌더 함수
# ══════════════════════════════════════════════════════════════════

def _render_diagnostic_panel() -> None:
    """API 키/엔드포인트/GeoJSON 상태 요약."""
    pop_key, pop_diag = _get_population_service_key_diagnostic()
    kakao_key = _get_kakao_rest_key_local()
    geojson_p = GEOJSON_PATH_DEFAULT
    _exists = Path(geojson_p).exists()

    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**행안부 인구 API**")
        st.text(
            f"키 길이       : {pop_diag.get('key_len')}\n"
            f"소스          : {pop_diag.get('final_source')}\n"
            f"elevator폴백  : {pop_diag.get('elevator_fallback')}\n"
            f"endpoint(pop) : {_get_population_api_endpoint('population')}\n"
            f"endpoint(age) : {_get_population_api_endpoint('age')}"
        )
        if not pop_key:
            st.warning(
                "행안부 인구 API 키가 없습니다. `.streamlit/secrets.toml` 의 "
                "[population_api] service_key 를 설정하세요. "
                "이미 [elevator_api] 가 있다면 동일한 data.go.kr 일반 인증키를 "
                "[population_api] service_key 에 복사해도 됩니다."
            )
    with c2:
        st.markdown("**카카오 로컬 API**")
        st.text(f"키 길이 : {len(kakao_key)}\n키 감지 : {'yes' if kakao_key else 'no'}")
        if not kakao_key:
            st.warning("카카오 REST 키가 없습니다. 백필/신규 고객 지오코딩이 불가합니다.")
    with c3:
        st.markdown("**행정동 GeoJSON**")
        st.text(f"경로     : {geojson_p}\n존재여부 : {'yes' if _exists else 'no'}")
        if _exists:
            _gj = load_admdong_geojson()
            st.text(f"features : {len(_gj.get('features', []))}")
        else:
            st.warning(
                "GeoJSON 이 없습니다. vuski/admdongkor 리포에서 HangJeongDong_ver*.geojson 을 "
                "다운로드해 시도만 필터해 `data/admdong_boundary.geojson` 으로 저장하세요."
            )


_FAIL_REASON_LABELS: dict[str, str] = {
    "not_found": "주소 못찾음 (정규화·키워드검색까지 실패)",
    "no_h_region": "행정동 매칭 실패 (좌표는 확보)",
    "rate_limited": "일시오류 (자동 재시도 대상)",
    "no_kakao_key": "카카오 키 미설정",
    "미시도": "미시도 (아직 배치 대상 안 됨)",
}


def _render_backfill_panel(client, store_names: list[str]) -> None:
    """미변환 고객 백필 배치 실행 UI + 실패 사유 브레이크다운.

    배치 실행 직후에는 st.rerun() 으로 스크립트를 다시 돌려 아래 `pending` 카운트를
    최신값으로 재조회한다. rerun 을 하지 않으면 이번 실행에서 이미 렌더링된 상단
    지표(pending)가 배치 실행 '이전' 값을 그대로 보여줘, 실제로는 반영됐는데도
    화면상 숫자가 안 바뀐 것처럼 보인다.
    """
    _last = st.session_state.pop("dcm_backfill_last_result", None)
    if _last:
        _reason_summary = " · ".join(
            f"{_FAIL_REASON_LABELS.get(k, k)} {v}" for k, v in _last.get("fail_reasons", {}).items()
        )
        _label = "전체 처리 완료" if _last.get("is_full") else "처리 완료"
        st.success(
            f"{_label} — 처리 {_last['processed']} · 성공 {_last['updated']} · 실패 {_last['failed']}"
            + (f" ({_reason_summary})" if _reason_summary else "")
        )
        if _last.get("errors"):
            with st.expander("오류 상세", expanded=False):
                for e in _last["errors"][:30]:
                    st.text(e)

    pending = count_customers_needing_admin_dong(client, store_names)
    st.metric("미변환 고객 수", f"{pending:,} 명",
              help="admin_dong_code 가 비어 있고 address 는 채워진 고객")

    if pending == 0:
        st.success("모든 고객이 이미 행정동으로 매핑되어 있습니다.")
        return

    breakdown = get_admin_dong_fail_breakdown(client, store_names)
    if breakdown:
        st.caption("실패 사유별 현황 (최근 배치 실행 결과 기준):")
        _bc = st.columns(len(_ADMIN_DONG_FAIL_REASONS) + 1)
        for _i, reason in enumerate((*_ADMIN_DONG_FAIL_REASONS, "미시도")):
            with _bc[_i]:
                st.metric(_FAIL_REASON_LABELS.get(reason, reason), f"{breakdown.get(reason, 0):,}")

    c1, c2 = st.columns([1, 3])
    with c1:
        batch_size = st.number_input(
            "이번 배치 처리 건수", min_value=10, max_value=1000, value=100, step=10,
            key="dcm_backfill_batch_size",
        )
    with c2:
        st.caption(
            "카카오 API Rate Limit 을 고려해 소량씩 반복 실행하는 걸 권장합니다. "
            "위경도가 이미 있는 고객은 좌표를 재사용해 주소검색 호출을 절감합니다. "
            f"최근 {_NOT_FOUND_RETRY_COOLDOWN_DAYS}일 내 '주소 못찾음'으로 확정된 건은 "
            "기본적으로 재시도 대상에서 제외됩니다(무한 재시도 방지)."
        )
    force_retry = st.checkbox(
        "최근 실패건도 강제로 다시 시도",
        key="dcm_backfill_force_retry",
        help="쿨다운 기간과 무관하게 '주소 못찾음' 건도 이번 배치에 포함합니다 (주소를 수정한 뒤 재검증할 때 유용).",
    )
    if st.button("▶️ 배치 실행", key="dcm_backfill_run"):
        _pbar = st.progress(0.0, text="지오코딩 중…")

        def _cb(done: int, total: int) -> None:
            frac = min(1.0, done / max(1, total))
            _pbar.progress(frac, text=f"지오코딩 진행 {done}/{total}")

        with st.spinner("행정동 매핑 배치 실행 중…"):
            res = backfill_admin_dong_batch(
                client, store_names or None, max_records=int(batch_size),
                progress_callback=_cb, force_retry=force_retry,
            )
        _pbar.progress(1.0, text="완료")
        st.session_state["dcm_backfill_last_result"] = {**res, "is_full": False}
        st.rerun()

    st.divider()
    if st.button(f"🚀 미변환 고객 전체 한번에 처리 ({pending:,}명)", key="dcm_backfill_run_all"):
        _run_full_backfill(client, store_names, force_retry=force_retry, total_target=pending)

    _render_manual_correction_panel(client, store_names)


_BACKFILL_ALL_CHUNK_SIZE = 200


def _run_full_backfill(client, store_names: list[str], *, force_retry: bool, total_target: int) -> None:
    """미변환 고객을 `_BACKFILL_ALL_CHUNK_SIZE` 단위로 자동 반복 호출해 배치 버튼을
    여러 번 누를 필요 없이 한 번의 클릭으로 전체를 처리한다. Streamlit 은 스크립트
    실행 중에도 progress bar 갱신을 즉시 화면에 반영하므로 실행 도중 진행 상황을
    계속 확인할 수 있다 (브라우저 탭을 닫으면 중단되며, 이미 처리된 건은 DB에
    저장돼 있어 다시 눌러 이어서 처리 가능)."""
    agg = {"processed": 0, "updated": 0, "failed": 0, "fail_reasons": {}, "errors": []}
    pbar = st.progress(0.0, text="전체 일괄 처리 준비 중…")
    status = st.empty()

    def _cb(done: int, _total_in_chunk: int) -> None:
        running = agg["processed"] + done
        frac = min(1.0, running / max(1, total_target))
        pbar.progress(frac, text=f"전체 진행 {running:,}/{total_target:,}명")

    max_iterations = max(5, (total_target // _BACKFILL_ALL_CHUNK_SIZE) + 5)
    stagnant_chunks = 0
    with st.spinner("전체 일괄 처리 중… 완료될 때까지 이 탭을 닫지 마세요."):
        for _ in range(max_iterations):
            chunk = backfill_admin_dong_batch(
                client, store_names or None, max_records=_BACKFILL_ALL_CHUNK_SIZE,
                progress_callback=_cb, force_retry=force_retry,
            )
            if chunk["processed"] == 0:
                break
            agg["processed"] += chunk["processed"]
            agg["updated"] += chunk["updated"]
            agg["failed"] += chunk["failed"]
            for k, v in chunk.get("fail_reasons", {}).items():
                agg["fail_reasons"][k] = agg["fail_reasons"].get(k, 0) + v
            agg["errors"].extend(chunk.get("errors", []))
            status.caption(f"누적 처리 {agg['processed']:,} · 성공 {agg['updated']:,} · 실패 {agg['failed']:,}")

            stagnant_chunks = stagnant_chunks + 1 if chunk["updated"] == 0 else 0
            if stagnant_chunks >= 2:
                status.caption("⚠️ 연속 2개 청크에서 성공이 없어 중단합니다 (API 일시 오류 가능성, 잠시 후 다시 시도해 주세요).")
                break
            if chunk["processed"] < _BACKFILL_ALL_CHUNK_SIZE:
                break  # 남은 대상이 소진됨

    pbar.progress(1.0, text="전체 일괄 처리 완료")
    st.session_state["dcm_backfill_last_result"] = {**agg, "is_full": True}
    st.rerun()


def _render_manual_correction_panel(client, store_names: list[str]) -> None:
    """'주소 못찾음(not_found)'으로 확정된 고객을 대상으로 주소 수정 후 재시도,
    또는 행정동을 수동으로 직접 지정하는 최후 수단 UI."""
    with st.expander("🛠️ 실패 고객 수동 교정 (not_found 대상)", expanded=False):
        try:
            q = (
                client.table("app_customers").select(
                    "id, address, store_name, admin_dong_fail_reason, admin_dong_fail_at, admin_dong_fail_count"
                )
                .is_("admin_dong_code", "null").not_.is_("address", "null")
                .eq("admin_dong_fail_reason", "not_found")
                .order("admin_dong_fail_count", desc=True)
                .limit(30)
            )
            if store_names:
                q = q.in_("store_name", store_names)
            rows = (q.execute().data or [])
        except Exception as e:
            st.warning(f"실패 고객 목록 조회 실패: {e}")
            return

        if not rows:
            st.caption("수동 교정이 필요한 not_found 건이 없습니다.")
            return

        idx = build_geojson_index(load_admdong_geojson())
        # adm_nm 은 이미 "시도 시군구 행정동" 전체 경로 (예: '울산광역시 중구 학성동').
        _dong_options = sorted(set(idx["adm_nm"].tolist())) if not idx.empty else []
        _dong_code_by_label = (
            dict(zip(idx["adm_nm"], idx["adm_cd2"])) if not idx.empty else {}
        )

        def _apply_dong(cid_: int, code_: str, name_: str) -> None:
            client.table("app_customers").update({
                "admin_dong_name": name_,
                "admin_dong_code": code_,
                "admin_dong_fail_reason": None,
                "admin_dong_fail_at": None,
            }).eq("id", cid_).execute()

        # ── ⚡ 확실한 건 일괄 자동 적용 (도로명 어간 완전일치 후보가 시군구 내 1개뿐) ──
        st.caption(
            "⚡ **자동 적용**: 주소의 도로명 어간(예: '삼산로'→'삼산')과 완전히 일치하는 "
            "행정동이 같은 시군구에 정확히 1개뿐인 건만 일괄 적용합니다. "
            "그 외에는 아래 목록의 추천 버튼으로 개별 확인 후 적용하세요."
        )
        if st.button("⚡ 확실한 건 자동 적용 (현재 목록 대상)", key="dcm_fix_auto_apply"):
            _applied = 0
            for _r in rows:
                _suggs_a = suggest_dong_candidates(_r.get("address") or "", idx)
                _exact = [s for s in _suggs_a if s["score"] >= 1.0]
                if len(_exact) == 1:
                    _apply_dong(_r["id"], _exact[0]["adm_cd2"], _exact[0]["adm_nm"].split()[-1])
                    _applied += 1
            if _applied:
                st.success(f"{_applied}건 자동 적용 완료.")
                st.rerun()
            else:
                st.info("자동 적용 조건(어간 완전일치 단일 후보)에 맞는 건이 없습니다.")

        for row in rows:
            cid = row.get("id")
            with st.container(border=True):
                st.caption(f"고객 #{cid} · {row.get('store_name') or ''} · 실패 {row.get('admin_dong_fail_count') or 0}회")
                _c1, _c2 = st.columns([3, 1])
                with _c1:
                    new_addr = st.text_input(
                        "주소 수정 후 재시도", value=row.get("address") or "",
                        key=f"dcm_fix_addr_{cid}", label_visibility="collapsed",
                    )
                with _c2:
                    if st.button("재시도", key=f"dcm_fix_retry_{cid}"):
                        info = geocode_to_admin_dong(new_addr)
                        if info.get("ok") and info.get("admin_dong_code"):
                            client.table("app_customers").update({
                                "address": new_addr,
                                "admin_dong_name": info.get("admin_dong_name"),
                                "admin_dong_code": info.get("admin_dong_code"),
                                "admin_dong_fail_reason": None,
                                "admin_dong_fail_at": None,
                            }).eq("id", cid).execute()
                            st.success(f"성공: {info.get('admin_dong_name')}")
                            st.rerun()
                        else:
                            st.error(f"여전히 실패: {info.get('fail_stage')} · {info.get('fail_detail')}")

                # ── 같은 시군구 유사명 추천 (⭐ 어간 완전일치 / ☆ 부분일치) ──
                _suggs = suggest_dong_candidates(row.get("address") or "", idx)
                if _suggs:
                    _sc = st.columns(len(_suggs))
                    for _si, _sg in enumerate(_suggs):
                        with _sc[_si]:
                            _star = "⭐" if _sg["score"] >= 1.0 else "☆"
                            if st.button(f"{_star} {_sg['adm_nm']}",
                                         key=f"dcm_fix_sugg_{cid}_{_sg['adm_cd2']}",
                                         help="클릭 시 이 행정동으로 즉시 지정합니다."):
                                _apply_dong(cid, _sg["adm_cd2"], _sg["adm_nm"].split()[-1])
                                st.success(f"'{_sg['adm_nm']}' 지정 완료")
                                st.rerun()

                if _dong_options:
                    _sel = st.selectbox(
                        "또는 행정동 직접 지정 (최후 수단)", options=["(선택 안 함)"] + _dong_options,
                        key=f"dcm_fix_manual_{cid}", label_visibility="collapsed",
                    )
                    if _sel != "(선택 안 함)" and st.button("이 행정동으로 지정", key=f"dcm_fix_manual_btn_{cid}"):
                        _code = _dong_code_by_label.get(_sel, "")
                        _name = _sel.split(" ")[-1]
                        client.table("app_customers").update({
                            "admin_dong_name": _name,
                            "admin_dong_code": _code,
                            "admin_dong_fail_reason": None,
                            "admin_dong_fail_at": None,
                        }).eq("id", cid).execute()
                        st.success(f"'{_sel}' 로 수동 지정 완료")
                        st.rerun()


def _render_summary_metrics(df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    _total_dong = len(df)
    _total_orders = int(df["purchase_count"].sum())
    _total_amount = int(pd.to_numeric(df.get("purchase_amount"), errors="coerce").fillna(0).sum())
    _valid = df[(df["penetration_rate"] > 0) | (df["target_density"] > 0)]
    _med_p = float(df.attrs.get("median_penetration", _valid["penetration_rate"].median() if not _valid.empty else 0.0))
    _med_d = float(df.attrs.get("median_target_density", _valid["target_density"].median() if not _valid.empty else 0.0))
    m1, m2, m3, m4, m5 = st.columns(5)
    with m1:
        st.metric("대상 행정동 수", f"{_total_dong:,}")
    with m2:
        st.metric("총 구매건수", f"{_total_orders:,}")
    with m3:
        st.metric("구매금액 합계", f"{_total_amount:,} 원")
    with m4:
        st.metric("침투율 중앙값", f"{_med_p:.3f}%")
    with m5:
        st.metric("밀집도 중앙값", f"{_med_d:.2f}%")

    strat_counts = df["행동_전략"].value_counts().to_dict() if "행동_전략" in df.columns else {}
    st.caption(
        "행동 전략 분포 — "
        f"{STRATEGY_ATTACK}: {strat_counts.get(STRATEGY_ATTACK, 0)} · "
        f"{STRATEGY_DEFEND}: {strat_counts.get(STRATEGY_DEFEND, 0)} · "
        f"{STRATEGY_LATENT}: {strat_counts.get(STRATEGY_LATENT, 0)} · "
        f"{STRATEGY_HOLD}: {strat_counts.get(STRATEGY_HOLD, 0)} · "
        f"{STRATEGY_UNSET}: {strat_counts.get(STRATEGY_UNSET, 0)}"
    )

    coverage = st.session_state.get("dcm_mapping_coverage") or {}
    _total_o = int(coverage.get("total_orders") or 0)
    _mapped_o = int(coverage.get("mapped_orders") or 0)
    if _total_o > 0:
        _unmapped_o = _total_o - _mapped_o
        _unmapped_pct = _unmapped_o / _total_o * 100
        _msg = (
            f"⚠️ 선택 기간 내 구매건 중 행정동 미매핑 {_unmapped_o:,}건 "
            f"(전체 {_total_o:,}건 대비 {_unmapped_pct:.1f}%) — 침투율 계산에서 제외됨. "
            "위 '미변환 고객 백필' 패널에서 보완할 수 있습니다."
        )
        if _unmapped_pct >= 10:
            st.warning(_msg)
        elif _unmapped_o > 0:
            st.caption(_msg)


def _render_map(df: pd.DataFrame) -> None:
    """행동_전략(A/B/C/D) 범주형 choropleth. 매니저가 색만 보고
    '어디를 공략할지'를 즉시 판단할 수 있도록 4색 폴리곤만 표시한다."""
    import plotly.express as px

    geojson = load_admdong_geojson()
    if not geojson.get("features"):
        st.warning("GeoJSON 이 로드되지 않아 지도를 그릴 수 없습니다.")
        return

    idx = build_geojson_index(geojson)
    # 카카오 code(10자리) ↔ GeoJSON adm_cd2(10자리) 매칭 우선, 실패 시 adm_cd(8자리) 매칭 시도.
    df_map = df.copy()
    df_map["_key10"] = df_map["admin_dong_code"].astype(str)
    df_map["_key8"] = df_map["_key10"].str[:8]

    _match10 = df_map[df_map["_key10"].isin(set(idx["adm_cd2"]))]
    _match8 = df_map[~df_map["_key10"].isin(set(idx["adm_cd2"])) & df_map["_key8"].isin(set(idx["adm_cd"]))]

    if not _match10.empty:
        featureidkey = "properties.adm_cd2"
        _match10 = _match10.assign(_join=_match10["_key10"])
        primary = _match10
    else:
        featureidkey = "properties.adm_cd"
        _match8 = _match8.assign(_join=_match8["_key8"])
        primary = _match8

    if primary.empty:
        st.warning(
            "행정동 코드가 GeoJSON 과 매칭되지 않습니다. "
            "카카오 coord2regioncode 반환 code 형식과 GeoJSON 스키마를 재확인하세요."
        )
        return

    _cent_key = "adm_cd2" if featureidkey.endswith("adm_cd2") else "adm_cd"
    primary = primary.merge(
        idx[[_cent_key, "adm_nm", "centroid_lat", "centroid_lon"]],
        left_on="_join", right_on=_cent_key, how="left",
    )

    _lat_c = float(primary["centroid_lat"].dropna().mean() or 35.5)
    _lon_c = float(primary["centroid_lon"].dropna().mean() or 129.3)

    # Plotly 범례에 A/B/C/D 코드 포함
    primary = primary.copy()
    primary["행동_전략_범례"] = (
        primary["행동_전략"].map(STRATEGY_LEGEND_LABEL).fillna(STRATEGY_LEGEND_LABEL[STRATEGY_UNSET])
    )

    fig = px.choropleth_mapbox(
        primary,
        geojson=geojson,
        locations="_join",
        featureidkey=featureidkey,
        color="행동_전략_범례",
        color_discrete_map=STRATEGY_COLOR_BY_LEGEND,
        category_orders={"행동_전략_범례": STRATEGY_LEGEND_ORDER},
        mapbox_style="carto-positron",
        zoom=10,
        center={"lat": _lat_c, "lon": _lon_c},
        opacity=0.72,
        custom_data=["admin_dong_name", "행동_전략", "hover_target", "hover_perf"],
    )

    # 지침 3: 문장형 hover — 동이름·전략·핵심타겟(선택 연령대) 인구·성과 요약
    _age_label = format_selected_age_label(df.attrs.get("selected_age_keys"))
    fig.update_traces(
        hovertemplate=(
            "<b>%{customdata[0]}</b><br>"
            "전략: %{customdata[1]}<br>"
            f"핵심타겟({_age_label}): %{{customdata[2]}}명<br>"
            "성과: %{customdata[3]}"
            "<extra></extra>"
        ),
        marker_line_width=0.3,
        marker_line_color="#ffffff",
    )

    fig.update_layout(
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
        height=620,
        legend=dict(
            title=dict(text="행동 전략 (코드 · 지침)", font=dict(size=12)),
            orientation="v",
            yanchor="top", y=0.98,
            xanchor="left", x=1.01,
            bgcolor="rgba(255,255,255,0.92)",
            bordercolor="#dddddd",
            borderwidth=1,
            font=dict(size=11),
        ),
    )
    st.plotly_chart(fig, use_container_width=True)
    _render_strategy_legend_guide(format_selected_age_label(df.attrs.get("selected_age_keys")))


def _render_quadrant_scatter(df: pd.DataFrame) -> None:
    """중앙값 기준 4분면 산점도 — 지도의 색상 범례와 동일한 행동_전략 색상을 사용."""
    import plotly.express as px
    if df is None or df.empty:
        return
    med_p = float(df.attrs.get("median_penetration", 0.0))
    med_d = float(df.attrs.get("median_target_density", 0.0))
    _age_label = format_selected_age_label(df.attrs.get("selected_age_keys"))

    fig = px.scatter(
        df,
        x="penetration_rate",
        y="target_density",
        color="행동_전략",
        color_discrete_map=STRATEGY_COLOR,
        category_orders={"행동_전략": STRATEGY_ORDER},
        hover_name="admin_dong_name",
        hover_data={
            "penetration_rate": ":.3f", "target_density": ":.2f",
            "purchase_count": True,
            "purchase_amount": True,
            "total_households": True, "total_population": True,
            "target_population": True,
            "행동_전략": False,
        },
        labels={
            "penetration_rate": "우리 매장 침투율 %",
            "target_density": f"타겟 인구 비중 % ({_age_label})",
            "행동_전략": "행동 전략",
            "purchase_count": "구매건수",
            "purchase_amount": "구매금액 합계",
            "target_population": f"핵심타겟({_age_label})",
        },
    )
    fig.add_vline(x=med_p, line_dash="dash", line_color="grey",
                  annotation_text=f"침투율 중앙값 {med_p:.3f}%", annotation_position="top")
    fig.add_hline(y=med_d, line_dash="dash", line_color="grey",
                  annotation_text=f"타겟 비중 중앙값 {med_d:.2f}%", annotation_position="right")
    fig.update_layout(
        height=360,
        margin={"r": 10, "t": 30, "l": 40, "b": 40},
        showlegend=False,
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_action_top5(df: pd.DataFrame) -> None:
    """🚨 집중 공략 그룹 중 핵심 타겟(사용자가 선택한 연령대) 인구가 가장 많은 Top 5 를
    가로 막대로 표시 — 매니저의 즉각적인 마케팅 실행 지침용."""
    import plotly.express as px

    if df is None or df.empty or "행동_전략" not in df.columns:
        return

    _age_label = format_selected_age_label(df.attrs.get("selected_age_keys"))
    _attack = df[df["행동_전략"] == STRATEGY_ATTACK].copy()
    _attack["target_population"] = pd.to_numeric(
        _attack.get("target_population"), errors="coerce"
    ).fillna(0).astype(int)
    _attack = _attack[_attack["target_population"] > 0]

    st.markdown(f"#### 당장 마케팅할 Top 5 행정동 · 집중 공략 · 핵심타겟({_age_label}) 인구 기준")

    if _attack.empty:
        st.caption("해당 기간에 '🚨 집중 공략' 지역이 없습니다. 다른 그룹은 매트릭스와 표를 참고하세요.")
        return

    top5 = _attack.nlargest(5, "target_population").iloc[::-1]  # 가로 막대는 아래→위로 커지도록 역순
    top5["_label"] = top5["admin_dong_name"].fillna("").astype(str)
    if "sigungu_label" in top5.columns:
        top5["_label"] = top5["sigungu_label"].fillna("") + " " + top5["_label"]

    top5["_perf"] = top5["hover_perf"] if "hover_perf" in top5.columns else ""

    fig = px.bar(
        top5,
        x="target_population",
        y="_label",
        orientation="h",
        text="target_population",
        custom_data=["hover_perf"],
        labels={"target_population": f"핵심타겟({_age_label}) 인구", "_label": ""},
    )
    fig.update_traces(
        marker_color=STRATEGY_COLOR[STRATEGY_ATTACK],
        texttemplate="%{x:,}명",
        textposition="outside",
        hovertemplate="<b>%{y}</b><br>핵심타겟: %{x:,}명<br>%{customdata[0]}<extra></extra>",
        cliponaxis=False,
    )
    fig.update_layout(
        height=max(220, 60 * len(top5) + 80),
        margin={"r": 40, "t": 10, "l": 10, "b": 30},
        xaxis=dict(showgrid=True, gridcolor="#eeeeee", zeroline=False),
        yaxis=dict(showgrid=False),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_table(df: pd.DataFrame) -> None:
    """상세 표 — 행동 전략 순 정렬 (집중 공략 → 핵심 방어 → 잠재 → 보류)."""
    if df is None or df.empty:
        return
    _age_label = format_selected_age_label(df.attrs.get("selected_age_keys"))
    _cols = [
        "행동_전략", "admin_dong_name", "admin_dong_code", "purchase_count", "purchase_amount",
        "total_households", "total_population", "target_population",
        "penetration_rate", "target_density",
    ]
    if "sigungu_label" in df.columns:
        _cols.insert(2, "sigungu_label")
    _cols = [c for c in _cols if c in df.columns]
    show = df[_cols].copy()
    if "purchase_amount" not in show.columns:
        show["purchase_amount"] = 0
    show["purchase_amount"] = (
        pd.to_numeric(show["purchase_amount"], errors="coerce").fillna(0).round(0).astype("int64")
    )
    _order_idx = {s: i for i, s in enumerate(STRATEGY_ORDER)}
    show["_strategy_ord"] = show["행동_전략"].map(_order_idx).fillna(len(STRATEGY_ORDER))
    show = show.sort_values(
        by=["_strategy_ord", "penetration_rate", "target_density"],
        ascending=[True, False, False],
    ).drop(columns=["_strategy_ord"])
    show = show.rename(columns={
        "행동_전략": "행동 전략",
        "admin_dong_name": "행정동",
        "sigungu_label": "시군구",
        "admin_dong_code": "행정동코드",
        "purchase_count": "구매건수(기간내)",
        "purchase_amount": "구매금액 합계",
        "total_households": "세대수",
        "total_population": "총인구",
        "target_population": f"핵심인구({_age_label})",
        "penetration_rate": "침투율(%)",
        "target_density": "밀집도(%)",
    })
    st.dataframe(show, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════
# PDF 보고서 출력
# ══════════════════════════════════════════════════════════════════

# PDF 본문용 — 이모지 제거(CID 폰트 미지원)한 짧은 전략 라벨
_STRATEGY_PDF_LABEL = {
    STRATEGY_ATTACK: "B · 집중 공략",
    STRATEGY_DEFEND: "A · 핵심 방어",
    STRATEGY_LATENT: "C · 잠재 상권",
    STRATEGY_HOLD: "D · 마케팅 보류",
    STRATEGY_UNSET: "- · 미분류",
}

_MPL_KR_FONT_CONFIGURED = False
_MPL_KR_FONT_PROP = None  # matplotlib.font_manager.FontProperties | None
_BUNDLED_KR_FONT_PATH = Path(__file__).parent / "data" / "fonts" / "NanumGothic.ttf"


def _get_matplotlib_korean_font():
    """PDF 차트용 한글 FontProperties. 번들 NanumGothic 을 최우선 사용."""
    global _MPL_KR_FONT_CONFIGURED, _MPL_KR_FONT_PROP
    if _MPL_KR_FONT_CONFIGURED:
        return _MPL_KR_FONT_PROP
    _MPL_KR_FONT_CONFIGURED = True
    try:
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib import font_manager, rcParams
    except Exception as exc:
        logger.warning("matplotlib 폰트 설정 실패: %s", exc)
        return None

    # 1) 리포지토리 번들 폰트 (Streamlit Cloud 등 OS 한글 폰트 없는 환경 대응)
    path_candidates = [
        _BUNDLED_KR_FONT_PATH,
        Path("C:/Windows/Fonts/malgun.ttf"),
        Path("/usr/share/fonts/truetype/nanum/NanumGothic.ttf"),
        Path("/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ]
    font_path: Path | None = None
    for fp in path_candidates:
        if fp.exists():
            font_path = fp
            break

    chosen_name = None
    if font_path is not None:
        try:
            font_manager.fontManager.addfont(str(font_path))
            _MPL_KR_FONT_PROP = font_manager.FontProperties(fname=str(font_path))
            chosen_name = _MPL_KR_FONT_PROP.get_name()
        except Exception as exc:
            logger.warning("폰트 파일 등록 실패 %s: %s", font_path, exc)
            _MPL_KR_FONT_PROP = None

    # 2) 시스템 설치 폰트 이름 폴백
    if _MPL_KR_FONT_PROP is None:
        name_candidates = [
            "Malgun Gothic", "AppleGothic", "NanumGothic", "NanumBarunGothic",
            "Noto Sans CJK KR", "Noto Sans KR", "UnDotum",
        ]
        available = {f.name for f in font_manager.fontManager.ttflist}
        chosen_name = next((n for n in name_candidates if n in available), None)
        if chosen_name:
            _MPL_KR_FONT_PROP = font_manager.FontProperties(family=chosen_name)

    if chosen_name:
        # sans-serif 최상단에 넣어 tick/legend 기본값에도 적용
        rcParams["font.family"] = "sans-serif"
        prev = list(rcParams.get("font.sans-serif") or [])
        rcParams["font.sans-serif"] = [chosen_name] + [x for x in prev if x != chosen_name]
        rcParams["axes.unicode_minus"] = False
        logger.info("matplotlib 한글 폰트: %s (path=%s)", chosen_name, font_path)
    else:
        logger.warning(
            "한글 matplotlib 폰트를 찾지 못했습니다. data/fonts/NanumGothic.ttf 번들을 확인하세요."
        )
    return _MPL_KR_FONT_PROP


def _configure_matplotlib_korean_font() -> None:
    """하위 호환 — PDF PNG 빌더에서 호출."""
    _get_matplotlib_korean_font()


def _apply_korean_font_to_axes(ax, fp=None) -> None:
    """축 제목·라벨·틱·범례에 한글 FontProperties 를 강제 적용."""
    if fp is None:
        fp = _get_matplotlib_korean_font()
    if fp is None:
        return
    try:
        ax.title.set_fontproperties(fp)
        ax.xaxis.label.set_fontproperties(fp)
        ax.yaxis.label.set_fontproperties(fp)
        for label in ax.get_xticklabels() + ax.get_yticklabels():
            label.set_fontproperties(fp)
        legend = ax.get_legend()
        if legend is not None:
            for text in legend.get_texts():
                text.set_fontproperties(fp)
            if legend.get_title() is not None:
                legend.get_title().set_fontproperties(fp)
    except Exception as exc:
        logger.warning("축 한글 폰트 적용 실패: %s", exc)


def _pdf_safe_text(s: Any) -> str:
    """reportlab CID 폰트에서 문제 되는 이모지·제어문자를 제거."""
    text = "" if s is None else str(s)
    text = re.sub(r"[\U00010000-\U0010ffff]", "", text)
    text = re.sub(r"[\u2600-\u27bf\u2300-\u23ff\u2b00-\u2bff\ufe0f]", "", text)
    return text.strip()


def _match_kpi_to_geojson(df: pd.DataFrame) -> tuple[pd.DataFrame, str]:
    """kpi_df 를 GeoJSON 키(adm_cd2 우선, adm_cd 폴백)로 조인할 준비.
    반환: (조인키 컬럼 `_join` 이 붙은 df, featureidkey 접미 'adm_cd2'|'adm_cd')."""
    geojson = load_admdong_geojson()
    idx = build_geojson_index(geojson)
    df_map = df.copy()
    df_map["_key10"] = df_map["admin_dong_code"].astype(str)
    df_map["_key8"] = df_map["_key10"].str[:8]
    if df_map["_key10"].isin(set(idx["adm_cd2"])).any():
        out = df_map[df_map["_key10"].isin(set(idx["adm_cd2"]))].copy()
        out["_join"] = out["_key10"]
        return out, "adm_cd2"
    out = df_map[df_map["_key8"].isin(set(idx["adm_cd"]))].copy()
    out["_join"] = out["_key8"]
    return out, "adm_cd"


def _build_strategy_map_png(df: pd.DataFrame) -> bytes | None:
    """행동 전략 색으로 행정동 폴리곤을 채운 정적 지도 PNG."""
    try:
        fp = _get_matplotlib_korean_font()
        import matplotlib.pyplot as plt
    except Exception as exc:
        logger.warning("matplotlib 로드 실패 — PDF 지도 생략: %s", exc)
        return None

    geojson = load_admdong_geojson()
    primary, join_key = _match_kpi_to_geojson(df)
    if primary.empty or not geojson.get("features"):
        return None

    color_by_join = {
        str(r["_join"]): STRATEGY_COLOR.get(r.get("행동_전략"), STRATEGY_COLOR[STRATEGY_UNSET])
        for _, r in primary.iterrows()
    }
    name_by_join = {
        str(r["_join"]): str(r.get("admin_dong_name") or "")
        for _, r in primary.iterrows()
    }

    fig, ax = plt.subplots(figsize=(10.5, 8.2), dpi=140)
    xs_all: list[float] = []
    ys_all: list[float] = []
    for feat in geojson.get("features") or []:
        props = feat.get("properties") or {}
        key = str(props.get(join_key) or "").strip()
        if key not in color_by_join:
            continue
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []
        polys = [coords] if gtype == "Polygon" else (coords if gtype == "MultiPolygon" else [])
        for poly in polys:
            if not poly:
                continue
            ring = poly[0]
            xs = [float(p[0]) for p in ring]
            ys = [float(p[1]) for p in ring]
            xs_all.extend(xs)
            ys_all.extend(ys)
            ax.fill(xs, ys, facecolor=color_by_join[key], edgecolor="#ffffff",
                    linewidth=0.35, alpha=0.82, zorder=2)
            # 동 이름은 선택 동이 적을 때만 라벨 (가독성)
            if len(color_by_join) <= 40 and key in name_by_join:
                ax.text(
                    sum(xs) / len(xs), sum(ys) / len(ys),
                    name_by_join[key], fontsize=5.5, ha="center", va="center",
                    color="#222222", zorder=3,
                    fontproperties=fp,
                )

    if not xs_all:
        plt.close(fig)
        return None

    pad_x = (max(xs_all) - min(xs_all)) * 0.05 + 0.002
    pad_y = (max(ys_all) - min(ys_all)) * 0.05 + 0.002
    ax.set_xlim(min(xs_all) - pad_x, max(xs_all) + pad_x)
    ax.set_ylim(min(ys_all) - pad_y, max(ys_all) + pad_y)
    ax.set_aspect("equal", adjustable="box")
    ax.axis("off")
    ax.set_title("상권 전략 지도", fontsize=12, pad=8, fontproperties=fp)

    from matplotlib.patches import Patch
    legend_items = [
        (STRATEGY_ATTACK, _STRATEGY_PDF_LABEL[STRATEGY_ATTACK]),
        (STRATEGY_DEFEND, _STRATEGY_PDF_LABEL[STRATEGY_DEFEND]),
        (STRATEGY_LATENT, _STRATEGY_PDF_LABEL[STRATEGY_LATENT]),
        (STRATEGY_HOLD, _STRATEGY_PDF_LABEL[STRATEGY_HOLD]),
    ]
    handles = [
        Patch(facecolor=STRATEGY_COLOR[s], edgecolor="#888", label=lab)
        for s, lab in legend_items
    ]
    ax.legend(handles=handles, loc="lower left", framealpha=0.92, fontsize=8, prop=fp)
    _apply_korean_font_to_axes(ax, fp)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _build_quadrant_scatter_png(df: pd.DataFrame) -> bytes | None:
    """2x2 매트릭스 산점도 PNG."""
    try:
        fp = _get_matplotlib_korean_font()
        import matplotlib.pyplot as plt
    except Exception as exc:
        logger.warning("matplotlib 로드 실패 — PDF 산점도 생략: %s", exc)
        return None
    if df is None or df.empty:
        return None

    med_p = float(df.attrs.get("median_penetration", 0.0) or 0.0)
    med_d = float(df.attrs.get("median_target_density", 0.0) or 0.0)

    fig, ax = plt.subplots(figsize=(6.2, 5.2), dpi=140)
    for strat in STRATEGY_ORDER:
        sub = df[df["행동_전략"] == strat]
        if sub.empty:
            continue
        ax.scatter(
            sub["penetration_rate"], sub["target_density"],
            c=STRATEGY_COLOR.get(strat, "#999"),
            s=42, alpha=0.85, edgecolors="#ffffff", linewidths=0.4,
            label=_STRATEGY_PDF_LABEL.get(strat, strat),
            zorder=3,
        )
    ax.axvline(med_p, color="#888888", linestyle="--", linewidth=1, zorder=2)
    ax.axhline(med_d, color="#888888", linestyle="--", linewidth=1, zorder=2)
    ax.set_xlabel("침투율 %", fontproperties=fp)
    ax.set_ylabel("타겟 밀집도 %", fontproperties=fp)
    ax.set_title("2×2 매트릭스", fontproperties=fp)
    ax.grid(True, color="#eeeeee", linewidth=0.6)
    ax.legend(fontsize=7, loc="best", framealpha=0.9, prop=fp)
    _apply_korean_font_to_axes(ax, fp)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _build_top5_bar_png(df: pd.DataFrame) -> bytes | None:
    """집중 공략 Top 5 가로 막대 PNG."""
    try:
        fp = _get_matplotlib_korean_font()
        import matplotlib.pyplot as plt
    except Exception as exc:
        logger.warning("matplotlib 로드 실패 — PDF Top5 생략: %s", exc)
        return None
    if df is None or df.empty or "행동_전략" not in df.columns:
        return None

    attack = df[df["행동_전략"] == STRATEGY_ATTACK].copy()
    attack["target_population"] = pd.to_numeric(
        attack.get("target_population"), errors="coerce"
    ).fillna(0)
    attack = attack[attack["target_population"] > 0]
    if attack.empty:
        return None
    top5 = attack.nlargest(5, "target_population").iloc[::-1]
    labels = top5["admin_dong_name"].fillna("").astype(str).tolist()
    values = top5["target_population"].astype(float).tolist()

    fig, ax = plt.subplots(figsize=(7.5, 3.6), dpi=140)
    ax.barh(labels, values, color=STRATEGY_COLOR[STRATEGY_ATTACK], height=0.6)
    for i, v in enumerate(values):
        ax.text(v, i, f"  {int(v):,}", va="center", fontsize=8, color="#333",
                fontproperties=fp)
    ax.set_xlabel("핵심타겟 인구", fontproperties=fp)
    ax.set_title("집중 공략 Top 5 (B)", fontproperties=fp)
    ax.grid(True, axis="x", color="#eeeeee", linewidth=0.6)
    ax.set_axisbelow(True)
    _apply_korean_font_to_axes(ax, fp)

    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def build_dong_commercial_pdf(
    kpi_df: pd.DataFrame,
    *,
    store_names: list[str],
    start_date: str,
    end_date: str,
    yyyymm: str,
    age_label: str,
) -> bytes:
    """상권 퍼포먼스 맵 렌더 결과 PDF 바이트를 생성한다."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.platypus import (
        Image,
        PageBreak,
        Paragraph,
        SimpleDocTemplate,
        Spacer,
        Table,
        TableStyle,
    )

    if "HYSMyeongJo-Medium" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont("HYSMyeongJo-Medium"))
    if "HYGothic-Medium" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont("HYGothic-Medium"))

    page = landscape(A4)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=page,
        leftMargin=12 * mm, rightMargin=12 * mm,
        topMargin=12 * mm, bottomMargin=12 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "DcmTitle", parent=styles["Title"],
        fontName="HYGothic-Medium", fontSize=18, leading=24,
        alignment=TA_CENTER, spaceAfter=6,
    )
    h_style = ParagraphStyle(
        "DcmH", parent=styles["Heading2"],
        fontName="HYGothic-Medium", fontSize=12, leading=16,
        spaceBefore=8, spaceAfter=4,
    )
    body_style = ParagraphStyle(
        "DcmBody", parent=styles["Normal"],
        fontName="HYSMyeongJo-Medium", fontSize=9, leading=13,
        alignment=TA_LEFT,
    )
    small_style = ParagraphStyle(
        "DcmSmall", parent=styles["Normal"],
        fontName="HYSMyeongJo-Medium", fontSize=8, leading=11,
        textColor=colors.HexColor("#555555"),
    )
    cell_style = ParagraphStyle(
        "DcmCell", parent=styles["Normal"],
        fontName="HYSMyeongJo-Medium", fontSize=7.5, leading=9.5,
    )

    story: list[Any] = []
    story.append(Paragraph(_pdf_safe_text("동 단위 상권 퍼포먼스 맵 보고서"), title_style))
    story.append(Paragraph(
        _pdf_safe_text(
            f"매장: {', '.join(store_names) or '-'}  |  "
            f"분석기간: {start_date} ~ {end_date}  |  "
            f"인구기준월: {yyyymm}  |  "
            f"핵심타겟: {age_label}"
        ),
        body_style,
    ))
    story.append(Spacer(1, 4 * mm))

    # 요약 KPI
    _total_dong = len(kpi_df) if kpi_df is not None else 0
    _total_orders = int(kpi_df["purchase_count"].sum()) if _total_dong else 0
    _total_amount = (
        int(pd.to_numeric(kpi_df.get("purchase_amount"), errors="coerce").fillna(0).sum())
        if _total_dong else 0
    )
    _med_p = float(kpi_df.attrs.get("median_penetration", 0.0) or 0.0) if _total_dong else 0.0
    _med_d = float(kpi_df.attrs.get("median_target_density", 0.0) or 0.0) if _total_dong else 0.0
    strat_counts = (
        kpi_df["행동_전략"].value_counts().to_dict()
        if _total_dong and "행동_전략" in kpi_df.columns else {}
    )
    summary_rows = [
        ["대상 행정동", f"{_total_dong:,}", "총 구매건수", f"{_total_orders:,}"],
        ["구매금액 합계", f"{_total_amount:,} 원", "인구기준월", str(yyyymm)],
        ["침투율 중앙값", f"{_med_p:.3f}%", "밀집도 중앙값", f"{_med_d:.2f}%"],
        [
            _pdf_safe_text(_STRATEGY_PDF_LABEL[STRATEGY_ATTACK]),
            str(strat_counts.get(STRATEGY_ATTACK, 0)),
            _pdf_safe_text(_STRATEGY_PDF_LABEL[STRATEGY_DEFEND]),
            str(strat_counts.get(STRATEGY_DEFEND, 0)),
        ],
        [
            _pdf_safe_text(_STRATEGY_PDF_LABEL[STRATEGY_LATENT]),
            str(strat_counts.get(STRATEGY_LATENT, 0)),
            _pdf_safe_text(_STRATEGY_PDF_LABEL[STRATEGY_HOLD]),
            str(strat_counts.get(STRATEGY_HOLD, 0)),
        ],
    ]
    summary_tbl = Table(summary_rows, colWidths=[45 * mm, 30 * mm, 45 * mm, 30 * mm])
    summary_tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "HYSMyeongJo-Medium"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F5F5F5")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#F5F5F5")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#CCCCCC")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(summary_tbl)
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(
        _pdf_safe_text(
            "행동 전략 — B 집중공략: 타겟 많음·구매 적음(마케팅 시급) / "
            "A 핵심방어: 구매·타겟 모두 많음(VIP) / "
            "C 잠재상권: 구매 많음·타겟 비중 낮음 / "
            "D 보류: 구매·타겟 모두 낮음"
        ),
        small_style,
    ))

    # 지도
    story.append(Paragraph(_pdf_safe_text("1. 상권 전략 지도"), h_style))
    map_png = _build_strategy_map_png(kpi_df)
    if map_png:
        story.append(Image(io.BytesIO(map_png), width=250 * mm, height=155 * mm, kind="proportional"))
    else:
        story.append(Paragraph(_pdf_safe_text("(지도 이미지를 생성하지 못했습니다)"), body_style))

    story.append(PageBreak())

    # 2x2 + Top5
    story.append(Paragraph(_pdf_safe_text("2. 2×2 매트릭스 · 집중 공략 Top 5"), h_style))
    q_png = _build_quadrant_scatter_png(kpi_df)
    t_png = _build_top5_bar_png(kpi_df)
    chart_row: list[Any] = []
    if q_png:
        chart_row.append(Image(io.BytesIO(q_png), width=120 * mm, height=100 * mm, kind="proportional"))
    if t_png:
        chart_row.append(Image(io.BytesIO(t_png), width=130 * mm, height=70 * mm, kind="proportional"))
    if chart_row:
        if len(chart_row) == 2:
            charts = Table([chart_row], colWidths=[125 * mm, 140 * mm])
            charts.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ]))
            story.append(charts)
        else:
            story.append(chart_row[0])
    else:
        story.append(Paragraph(_pdf_safe_text("(차트 이미지를 생성하지 못했습니다)"), body_style))

    story.append(PageBreak())

    # 상세 표
    story.append(Paragraph(_pdf_safe_text("3. 행정동별 상세 지표"), h_style))
    if kpi_df is None or kpi_df.empty:
        story.append(Paragraph(_pdf_safe_text("(데이터 없음)"), body_style))
    else:
        show = kpi_df.copy()
        _order_idx = {s: i for i, s in enumerate(STRATEGY_ORDER)}
        show["_ord"] = show["행동_전략"].map(_order_idx).fillna(len(STRATEGY_ORDER))
        show = show.sort_values(
            by=["_ord", "penetration_rate", "target_density"],
            ascending=[True, False, False],
        )
        header = [
            "전략", "시군구", "행정동", "구매건수", "구매금액", "세대", "총인구",
            f"핵심({_pdf_safe_text(age_label)})", "침투%", "밀집%",
        ]
        rows: list[list[Any]] = [[Paragraph(_pdf_safe_text(h), cell_style) for h in header]]
        for _, r in show.iterrows():
            rows.append([
                Paragraph(_pdf_safe_text(_STRATEGY_PDF_LABEL.get(r.get("행동_전략"), r.get("행동_전략"))), cell_style),
                Paragraph(_pdf_safe_text(r.get("sigungu_label", "")), cell_style),
                Paragraph(_pdf_safe_text(r.get("admin_dong_name", "")), cell_style),
                Paragraph(f"{int(pd.to_numeric(r.get('purchase_count'), errors='coerce') or 0):,}", cell_style),
                Paragraph(f"{int(pd.to_numeric(r.get('purchase_amount'), errors='coerce') or 0):,}", cell_style),
                Paragraph(f"{int(pd.to_numeric(r.get('total_households'), errors='coerce') or 0):,}", cell_style),
                Paragraph(f"{int(pd.to_numeric(r.get('total_population'), errors='coerce') or 0):,}", cell_style),
                Paragraph(f"{int(pd.to_numeric(r.get('target_population'), errors='coerce') or 0):,}", cell_style),
                Paragraph(f"{float(pd.to_numeric(r.get('penetration_rate'), errors='coerce') or 0):.3f}", cell_style),
                Paragraph(f"{float(pd.to_numeric(r.get('target_density'), errors='coerce') or 0):.2f}", cell_style),
            ])
        col_w = [24 * mm, 28 * mm, 24 * mm, 16 * mm, 24 * mm, 18 * mm, 18 * mm, 24 * mm, 16 * mm, 16 * mm]
        detail = Table(rows, colWidths=col_w, repeatRows=1)
        detail.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), "HYSMyeongJo-Medium"),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#37474F")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#B0BEC5")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#FAFAFA")]),
            ("LEFTPADDING", (0, 0), (-1, -1), 2),
            ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]))
        story.append(detail)

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        _pdf_safe_text(
            f"생성일시: {datetime.now().strftime('%Y-%m-%d %H:%M')} · "
            "본 보고서는 상권 퍼포먼스 맵 화면의 현재 선택(매장·기간·행정동·연령대) 기준입니다."
        ),
        small_style,
    ))

    doc.build(story)
    return buf.getvalue()


def _render_pdf_export_panel(
    kpi_df: pd.DataFrame,
    *,
    store_names: list[str],
    start_date: str,
    end_date: str,
    yyyymm: str,
    age_keys: list[str],
    cache_key: Any,
) -> None:
    """렌더 결과 하단 — PDF 생성·다운로드 패널."""
    st.divider()
    st.markdown("### 📄 PDF 보고서")
    st.caption(
        "현재 화면에 렌더된 상권 전략 지도 · 2×2 매트릭스 · 집중 공략 Top 5 · "
        "행정동별 상세 표를 PDF로 저장합니다."
    )
    _pdf_state_key = (cache_key, tuple(age_keys), yyyymm)
    if st.session_state.get("dcm_pdf_state_key") != _pdf_state_key:
        st.session_state.pop("dcm_pdf_bytes", None)
        st.session_state.pop("dcm_pdf_filename", None)
        st.session_state["dcm_pdf_state_key"] = _pdf_state_key

    _age_label = format_selected_age_label(age_keys)
    c1, c2 = st.columns([1, 2])
    with c1:
        if st.button("📄 PDF 보고서 생성", type="secondary", key="dcm_pdf_gen_btn"):
            try:
                with st.spinner("PDF 생성 중… (지도·차트 렌더링)"):
                    pdf_bytes = build_dong_commercial_pdf(
                        kpi_df,
                        store_names=list(store_names or []),
                        start_date=start_date,
                        end_date=end_date,
                        yyyymm=yyyymm,
                        age_label=_age_label,
                    )
                st.session_state["dcm_pdf_bytes"] = pdf_bytes
                st.session_state["dcm_pdf_filename"] = (
                    f"dong_commercial_map_{start_date}_{end_date}.pdf"
                )
                st.success(f"PDF 생성 완료 ({len(pdf_bytes):,} bytes)")
            except Exception as exc:
                logger.exception("상권 맵 PDF 생성 실패")
                st.error(f"PDF 생성 실패: {exc}")

    _pdf = st.session_state.get("dcm_pdf_bytes")
    if _pdf:
        with c2:
            st.download_button(
                "⬇️ PDF 다운로드",
                data=_pdf,
                file_name=st.session_state.get(
                    "dcm_pdf_filename", "dong_commercial_map.pdf"
                ),
                mime="application/pdf",
                key="dcm_pdf_download_btn",
            )
