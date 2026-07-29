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

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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

def _kakao_address_to_coord(address: str) -> tuple[float, float] | None:
    """주소 → (lat, lon). app.py 의 geocode_address_kakao 와 동일 로직 (독립 구현)."""
    key = _get_kakao_rest_key_local()
    if not key or not address:
        return None
    try:
        r = requests.get(
            "https://dapi.kakao.com/v2/local/search/address.json",
            params={"query": address.strip()},
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


def geocode_to_admin_dong(
    address: str | None,
    lat: float | None = None,
    lon: float | None = None,
) -> dict | None:
    """
    주소 또는 좌표로 행정동명·행정동코드 조회.

    로직:
      1. lat/lon 이 없으면 카카오 주소검색으로 좌표 확보 (1회 호출)
      2. 좌표로 카카오 coord2regioncode.json 호출 → region_type='H' (행정동) 문서 추출

    반환:
      {
        "admin_dong_name": str,   # region_3depth_name
        "admin_dong_code": str,   # code (10자리)
        "sigungu": str,           # region_2depth_name
        "sidonm": str,            # region_1depth_name
      } 또는 None (실패 시).
    """
    key = _get_kakao_rest_key_local()
    if not key:
        return None

    if lat is None or lon is None:
        if not address:
            return None
        coord = _kakao_address_to_coord(address)
        if not coord:
            return None
        lat, lon = coord

    try:
        r = requests.get(
            KAKAO_COORD2REGIONCODE_URL,
            params={"x": lon, "y": lat},
            headers={"Authorization": f"KakaoAK {key}"},
            timeout=5.0,
        )
        if r.status_code != 200:
            return None
        docs = (r.json() or {}).get("documents") or []
        h = next((d for d in docs if str(d.get("region_type")) == "H"), None)
        if h is None:
            return None
        return {
            "admin_dong_name": (h.get("region_3depth_name") or "").strip(),
            "admin_dong_code": (h.get("code") or "").strip(),
            "sigungu": (h.get("region_2depth_name") or "").strip(),
            "sidonm": (h.get("region_1depth_name") or "").strip(),
        }
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════
# 행안부 공공데이터 API: 인구 · 세대현황 / 성/연령별 인구
# ══════════════════════════════════════════════════════════════════

_THIRTY_DAYS_SEC = 60 * 60 * 24 * 30


def _to_int_safe(v: Any) -> int:
    try:
        return int(str(v).replace(",", "").strip())
    except Exception:
        return 0


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


@st.cache_data(ttl=_THIRTY_DAYS_SEC, show_spinner=False)
def fetch_admin_dong_age_population(admin_dong_code: str, yyyymm: str) -> dict:
    """
    행정동코드+통계년월로 30~49세 인구수 조회 (타겟 밀집도 계산용).

    반환: {"ok", "admin_dong_code", "yyyymm",
           "age_30_49_population", "total_population", "error", "raw_url"}
    """
    key = _get_population_service_key()
    if not key or not admin_dong_code or not yyyymm:
        return {
            "ok": False, "admin_dong_code": admin_dong_code, "yyyymm": yyyymm,
            "age_30_49_population": 0, "total_population": 0,
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
                "age_30_49_population": 0, "total_population": 0,
                "error": f"HTTP {r.status_code}: {r.text[:120]}", "raw_url": raw_url,
            }
        js = r.json() if r.content else {}
        items = _extract_items(js)
        if not items:
            return {
                "ok": False, "admin_dong_code": admin_dong_code, "yyyymm": yyyymm,
                "age_30_49_population": 0, "total_population": 0,
                "error": "응답 items 비어 있음", "raw_url": raw_url,
            }
        # 실 응답: 행별 컬럼형 age 필드.
        #   male{X}AgeNmprCnt: 만 X~X+9세 남자 (X ∈ {0,10,20,...,100})
        #   feml{X}AgeNmprCnt: 만 X~X+9세 여자
        # 30~49세 대상: X ∈ {30, 40} 의 남녀 합산.
        # 총인구는 totNmprCnt 필드에서 취득.
        target_buckets = ("30", "40")
        age_bucket = 0
        total = 0
        for it in items:
            total += _to_int_safe(_pick(it, ["totNmprCnt", "totPopltCnt"]))
            for _bkt in target_buckets:
                age_bucket += _to_int_safe(it.get(f"male{_bkt}AgeNmprCnt"))
                age_bucket += _to_int_safe(it.get(f"feml{_bkt}AgeNmprCnt"))
        return {
            "ok": True, "admin_dong_code": admin_dong_code, "yyyymm": yyyymm,
            "age_30_49_population": age_bucket, "total_population": total,
            "error": "", "raw_url": raw_url,
        }
    except Exception as e:
        return {
            "ok": False, "admin_dong_code": admin_dong_code, "yyyymm": yyyymm,
            "age_30_49_population": 0, "total_population": 0,
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


# ══════════════════════════════════════════════════════════════════
# KPI 산출 · 2x2 클러스터링
# ══════════════════════════════════════════════════════════════════

def compute_dong_kpi(crm_counts: pd.DataFrame, population: pd.DataFrame) -> pd.DataFrame:
    """
    crm_counts:  [admin_dong_code, admin_dong_name, purchase_count]
    population:  [admin_dong_code, total_households, total_population, age_30_49_population]
    반환: 위 컬럼 + penetration_rate(%), target_density(%)
    """
    if crm_counts is None or crm_counts.empty:
        return crm_counts.copy() if crm_counts is not None else pd.DataFrame()
    df = crm_counts.merge(population, on="admin_dong_code", how="left")
    # 0 을 NaN 으로 바꿔 분모 오류(ZeroDivisionError 대신 무한대) 를 방지.
    # pd.NA 는 object dtype 승격을 유발해 이후 astype(float) 에서
    # "float() argument ... not 'NAType'" 오류가 나므로 float 호환 NaN 사용.
    df["total_households"] = pd.to_numeric(df.get("total_households"), errors="coerce").replace(0, float("nan"))
    df["total_population"] = pd.to_numeric(df.get("total_population"), errors="coerce").replace(0, float("nan"))
    df["age_30_49_population"] = pd.to_numeric(df.get("age_30_49_population"), errors="coerce")
    df["purchase_count"] = pd.to_numeric(df.get("purchase_count"), errors="coerce").fillna(0)
    df["penetration_rate"] = (df["purchase_count"] / df["total_households"] * 100).astype(float).fillna(0.0)
    df["target_density"] = (df["age_30_49_population"] / df["total_population"] * 100).astype(float).fillna(0.0)
    df["penetration_rate"] = df["penetration_rate"].round(3)
    df["target_density"] = df["target_density"].round(2)
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
    선택한 매장(들)의 app_orders + app_customers 조인 → admin_dong_code 별 구매건수 집계.
    order_date 가 [start_date, end_date] (YYYY-MM-DD, 포함) 구간인 주문만 집계한다.
    반환: [admin_dong_code, admin_dong_name, purchase_count]

    10분 캐싱(ttl=600) — 매장/기간이 동일하면 Supabase 재조회 없이 즉시 반환.
    `_client` 는 언더스코어 접두사로 Streamlit 캐시 해싱에서 제외됨(공식 규칙).
    최신 데이터 강제 조회가 필요하면 `aggregate_purchase_count_by_dong.clear()` 호출.
    """
    client = _client
    if not store_keys:
        return pd.DataFrame(columns=["admin_dong_code", "admin_dong_name", "purchase_count"])
    orders = _paginated_select(
        client, "app_orders", "id, customer_id, db_filename, order_date",
        filters=[
            ("in_", "db_filename", store_keys),
            ("gte", "order_date", start_date),
            ("lte", "order_date", end_date),
        ],
    )
    if not orders:
        return pd.DataFrame(columns=["admin_dong_code", "admin_dong_name", "purchase_count"])
    cust_ids = sorted({int(o["customer_id"]) for o in orders if o.get("customer_id") is not None})
    if not cust_ids:
        return pd.DataFrame(columns=["admin_dong_code", "admin_dong_name", "purchase_count"])
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
        return pd.DataFrame(columns=["admin_dong_code", "admin_dong_name", "purchase_count"])
    cdf = pd.DataFrame(custs).rename(columns={"id": "customer_id"})
    odf = pd.DataFrame(orders)
    merged = odf.merge(cdf[["customer_id", "admin_dong_code", "admin_dong_name"]], on="customer_id", how="left")
    merged = merged[merged["admin_dong_code"].notna() & (merged["admin_dong_code"] != "")]
    if merged.empty:
        return pd.DataFrame(columns=["admin_dong_code", "admin_dong_name", "purchase_count"])
    grp = merged.groupby(["admin_dong_code", "admin_dong_name"], as_index=False)["id"].count()
    grp = grp.rename(columns={"id": "purchase_count"})
    return grp.sort_values("purchase_count", ascending=False)


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

def count_customers_needing_admin_dong(client, store_names: list[str] | None = None) -> int:
    """admin_dong_code 가 없고 address 는 있는 고객 수."""
    try:
        q = client.table("app_customers").select("id", count="exact").is_("admin_dong_code", "null").not_.is_("address", "null")
        if store_names:
            q = q.in_("store_name", store_names)
        r = q.execute()
        return int(getattr(r, "count", 0) or 0)
    except Exception as e:
        logger.warning("count_customers_needing_admin_dong 실패: %s", e)
        return 0


def _fetch_customers_needing_admin_dong(client, store_names: list[str] | None, limit: int) -> list[dict]:
    """address 는 있고 admin_dong_code 는 비어있는 고객 최대 `limit` 명 조회.
    latitude/longitude 가 이미 있으면 재활용해 카카오 주소검색 호출 절감."""
    try:
        q = client.table("app_customers").select(
            "id, address, latitude, longitude, store_name"
        ).is_("admin_dong_code", "null").not_.is_("address", "null").limit(limit)
        if store_names:
            q = q.in_("store_name", store_names)
        r = q.execute()
        return (r.data or []) if hasattr(r, "data") else []
    except Exception as e:
        logger.warning("고객 조회 실패: %s", e)
        return []


def backfill_admin_dong_batch(
    client,
    store_names: list[str] | None = None,
    max_records: int = 100,
    sleep_between_calls_sec: float = 0.05,
    progress_callback=None,
) -> dict:
    """
    행정동 미매핑 고객을 최대 `max_records` 건 처리.
    반환: {"processed": int, "updated": int, "failed": int, "errors": list[str]}
    """
    result: dict = {"processed": 0, "updated": 0, "failed": 0, "errors": []}
    rows = _fetch_customers_needing_admin_dong(client, store_names, max_records)
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
            info = None
            result["errors"].append(f"id={cid}: geocode 예외 {e}")

        if not info or not info.get("admin_dong_code"):
            result["failed"] += 1
        else:
            try:
                client.table("app_customers").update({
                    "admin_dong_name": info.get("admin_dong_name"),
                    "admin_dong_code": info.get("admin_dong_code"),
                }).eq("id", cid).execute()
                result["updated"] += 1
            except Exception as e:
                result["failed"] += 1
                result["errors"].append(f"id={cid}: update 실패 {e}")

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
_POP_CACHE_COLUMNS = [
    "admin_dong_code", "total_households", "total_population", "age_30_49_population",
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
    payload = [
        {
            "admin_dong_code": r["admin_dong_code"],
            "yyyymm": yyyymm,
            "total_households": r["total_households"],
            "total_population": r["total_population"],
            "age_30_49_population": r["age_30_49_population"],
        }
        for r in rows
    ]
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
    return {
        "admin_dong_code": str(code),
        "total_households": int(pop.get("total_households") or 0),
        "total_population": int(pop.get("total_population") or 0),
        "age_30_49_population": int(age.get("age_30_49_population") or 0),
        "error": (pop.get("error") or "") + (" | " + age.get("error") if age.get("error") else ""),
    }


def fetch_population_bulk(
    client, admin_dong_codes: list[str], yyyymm: str, max_workers: int = 8,
) -> pd.DataFrame:
    """
    행정동코드 리스트 → 인구·세대·3040 데이터 DataFrame.

    1) app_dong_population_cache 에서 (code, yyyymm) 벌크 조회로 캐시 적중분 확보.
    2) 캐시 미스난 코드만 ThreadPoolExecutor 로 병렬 호출 (인구 API + 연령 API).
    3) 신규 조회분은 캐시 테이블에 upsert 해 다음 조회부터 즉시 재사용.

    반환: [admin_dong_code, total_households, total_population, age_30_49_population, error]
    """
    codes = [str(c) for c in admin_dong_codes if c]
    if not codes:
        return pd.DataFrame(
            columns=["admin_dong_code", "total_households", "total_population", "age_30_49_population", "error"]
        )

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
                    fetched_rows.append({
                        "admin_dong_code": code, "total_households": 0,
                        "total_population": 0, "age_30_49_population": 0,
                        "error": f"병렬 조회 예외: {e}",
                    })
        # 정상 조회된(에러 없는) 행만 영구 캐시에 저장 — 오류 응답을 캐시해 재시도를 막지 않도록.
        _ok_rows = [r for r in fetched_rows if not (r.get("error") or "").strip()]
        _save_population_cache_to_db(client, _ok_rows, yyyymm)

    rows: list[dict] = []
    for code in codes:
        if code in cache_hits:
            hit = cache_hits[code]
            rows.append({
                "admin_dong_code": code,
                "total_households": int(hit.get("total_households") or 0),
                "total_population": int(hit.get("total_population") or 0),
                "age_30_49_population": int(hit.get("age_30_49_population") or 0),
                "error": "",
            })
    rows.extend(fetched_rows)

    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["admin_dong_code", "total_households", "total_population", "age_30_49_population", "error"]
    )


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
                    fetched_rows.append({
                        "admin_dong_code": futures[fut], "total_households": 0,
                        "total_population": 0, "age_30_49_population": 0,
                        "error": f"병렬 조회 예외: {e}",
                    })
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

    role = st.session_state.get("user", {}).get("role", "user")
    current_db = st.session_state.get("current_db")
    if role == "superadmin":
        store_options: list[tuple[str, str]] = [
            (s["db_filename"], s["store_name"]) for s in stores if s.get("db_filename")
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
        period_range = st.date_input(
            "분석 기간 (구매건수 집계 구간)",
            value=(_default_start, _today),
            help="이 기간의 구매건수를 행정동별로 합산해 침투율을 계산합니다. "
                 "인구·세대 데이터는 기간 종료월 기준으로 1회만 조회합니다.",
            key="dcm_period_range",
        )

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
    yyyymm = end_date.strftime("%Y%m")  # 인구·세대 데이터는 기간 종료월 스냅샷만 사용

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

    # ── 데이터 조회 · 렌더링 ─────────────────────────────
    _c_btn1, _c_btn2 = st.columns([2, 1])
    with _c_btn1:
        do_render = st.button("🗺️ 상권 맵 렌더링", type="primary", key="dcm_render_btn")
    with _c_btn2:
        force_refresh = st.checkbox(
            "최신 데이터로 새로고침", key="dcm_force_refresh",
            help="캐시를 무시하고 Supabase 에서 구매건수를 다시 집계합니다 (당일 신규 주문 반영 등).",
        )

    # 매장·기간이 바뀌면 이전 결과는 무효화 (시군구 필터만 바꿀 때는 재클릭 없이도
    # 아래 캐시된 원본으로 즉시 재필터링되도록 session_state 에 원본을 보관).
    _cache_key = (tuple(sorted(sel_dbfns)), start_date_str, end_date_str)

    if do_render:
        if force_refresh:
            aggregate_purchase_count_by_dong.clear()
        with st.spinner("행정동별 구매건수 집계 중…"):
            _raw_df = aggregate_purchase_count_by_dong(client, sel_dbfns, start_date_str, end_date_str)
        st.session_state["dcm_raw_crm_df"] = _attach_sigungu(_raw_df)
        st.session_state["dcm_raw_cache_key"] = _cache_key
        if st.session_state.get("dcm_sigungu_cache_key") != _cache_key:
            st.session_state.pop("dcm_sigungu_filter", None)
            st.session_state["dcm_sigungu_cache_key"] = _cache_key

    if st.session_state.get("dcm_raw_cache_key") != _cache_key:
        st.info("매장·분석기간 설정 후 '상권 맵 렌더링'을 눌러 주세요.")
        return

    crm_df_all = st.session_state.get("dcm_raw_crm_df")
    if crm_df_all is None or crm_df_all.empty:
        st.warning(
            "선택한 매장·기간에 행정동 매핑이 된 구매 건이 없습니다. "
            "위 백필 패널로 행정동 변환을 먼저 실행하거나 분석 기간을 넓혀 보세요."
        )
        return

    # ── 시군구 필터 (변경 시 재클릭 없이 즉시 적용) ──────────
    _sgg_options = sorted(x for x in crm_df_all["sigungu_label"].unique().tolist() if x)
    sel_sgg = st.multiselect(
        "시군구 필터 (미선택 시 전체)",
        options=_sgg_options,
        default=_sgg_options,
        key="dcm_sigungu_filter",
        help="구매건수가 집계된 행정동을 시군구 단위로 좁혀 봅니다. GeoJSON 범위 밖 행정동은 '기타(미분류)'로 표시됩니다.",
    )
    crm_df = crm_df_all[crm_df_all["sigungu_label"].isin(sel_sgg)] if sel_sgg else crm_df_all.iloc[0:0]
    if crm_df.empty:
        st.warning("선택한 시군구에 해당하는 행정동이 없습니다.")
        return

    st.caption(f"📅 분석 기간: {start_date} ~ {end_date} · 인구 기준월: {yyyymm}")
    st.success(
        f"행정동 {len(crm_df)}개, 기간 내 총 구매건수 {int(crm_df['purchase_count'].sum())} 건"
    )

    with st.spinner("행정안전부 인구·세대 데이터 조회 중… (영구 캐시 + 병렬 조회)"):
        pop_df = fetch_population_bulk(client, crm_df["admin_dong_code"].astype(str).tolist(), yyyymm)

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

    kpi_df = compute_dong_kpi(crm_df, pop_df)
    kpi_df = assign_quadrant(kpi_df)

    # ── KPI 요약 ─────────────────────────────────────
    _render_summary_metrics(kpi_df)

    # ── 지도 렌더 ────────────────────────────────────
    _render_map(kpi_df)

    # ── 4분면 산점도 · 표 ────────────────────────────
    _render_quadrant_scatter(kpi_df)
    _render_table(kpi_df)


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


def _render_backfill_panel(client, store_names: list[str]) -> None:
    """미변환 고객 백필 배치 실행 UI."""
    pending = count_customers_needing_admin_dong(client, store_names)
    st.metric("미변환 고객 수", f"{pending:,} 명",
              help="admin_dong_code 가 비어 있고 address 는 채워진 고객")

    if pending == 0:
        st.success("모든 고객이 이미 행정동으로 매핑되어 있습니다.")
        return

    c1, c2 = st.columns([1, 3])
    with c1:
        batch_size = st.number_input(
            "이번 배치 처리 건수", min_value=10, max_value=1000, value=100, step=10,
            key="dcm_backfill_batch_size",
        )
    with c2:
        st.caption(
            "카카오 API Rate Limit 을 고려해 소량씩 반복 실행하는 걸 권장합니다. "
            "위경도가 이미 있는 고객은 좌표를 재사용해 주소검색 호출을 절감합니다."
        )
    if st.button("▶️ 배치 실행", key="dcm_backfill_run"):
        _pbar = st.progress(0.0, text="지오코딩 중…")

        def _cb(done: int, total: int) -> None:
            frac = min(1.0, done / max(1, total))
            _pbar.progress(frac, text=f"지오코딩 진행 {done}/{total}")

        with st.spinner("행정동 매핑 배치 실행 중…"):
            res = backfill_admin_dong_batch(
                client, store_names or None, max_records=int(batch_size),
                progress_callback=_cb,
            )
        _pbar.progress(1.0, text="완료")
        st.success(
            f"처리 {res['processed']} · 성공 {res['updated']} · 실패 {res['failed']}"
        )
        if res["errors"]:
            with st.expander("오류 상세", expanded=False):
                for e in res["errors"][:20]:
                    st.text(e)


def _render_summary_metrics(df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    _total_dong = len(df)
    _total_orders = int(df["purchase_count"].sum())
    _valid = df[(df["penetration_rate"] > 0) | (df["target_density"] > 0)]
    _med_p = float(df.attrs.get("median_penetration", _valid["penetration_rate"].median() if not _valid.empty else 0.0))
    _med_d = float(df.attrs.get("median_target_density", _valid["target_density"].median() if not _valid.empty else 0.0))
    m1, m2, m3, m4 = st.columns(4)
    with m1:
        st.metric("대상 행정동 수", f"{_total_dong:,}")
    with m2:
        st.metric("총 구매건수", f"{_total_orders:,}")
    with m3:
        st.metric("침투율 중앙값", f"{_med_p:.3f}%")
    with m4:
        st.metric("밀집도 중앙값", f"{_med_d:.2f}%")

    quad_counts = df["quadrant"].value_counts().to_dict() if "quadrant" in df.columns else {}
    st.caption(
        "그룹 분포 — "
        f"A(핵심): {quad_counts.get('A', 0)} · "
        f"B(개척): {quad_counts.get('B', 0)} · "
        f"C(포화): {quad_counts.get('C', 0)} · "
        f"D(저우선): {quad_counts.get('D', 0)} · "
        f"미분류: {quad_counts.get('-', 0)}"
    )


def _render_map(df: pd.DataFrame) -> None:
    """choropleth (침투율) + Scattermapbox (밀집도 버블) 오버레이."""
    import plotly.express as px
    import plotly.graph_objects as go

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

    # centroid 조인 (버블 오버레이용)
    _cent_key = "adm_cd2" if featureidkey.endswith("adm_cd2") else "adm_cd"
    primary = primary.merge(
        idx[[_cent_key, "adm_nm", "centroid_lat", "centroid_lon"]],
        left_on="_join", right_on=_cent_key, how="left",
    )

    # 지도 중심: centroid 평균
    _lat_c = float(primary["centroid_lat"].dropna().mean() or 35.5)
    _lon_c = float(primary["centroid_lon"].dropna().mean() or 129.3)

    fig = px.choropleth_mapbox(
        primary,
        geojson=geojson,
        locations="_join",
        featureidkey=featureidkey,
        color="penetration_rate",
        color_continuous_scale="YlOrRd",
        range_color=(0, max(0.1, float(primary["penetration_rate"].quantile(0.95) or 1))),
        mapbox_style="carto-positron",
        zoom=10,
        center={"lat": _lat_c, "lon": _lon_c},
        opacity=0.55,
        hover_name="admin_dong_name",
        hover_data={
            "_join": False,
            "penetration_rate": ":.3f",
            "target_density": ":.2f",
            "purchase_count": True,
            "total_households": True,
            "quadrant": True,
        },
        labels={
            "penetration_rate": "침투율 %", "target_density": "밀집도 %",
            "purchase_count": "구매건수(기간내)", "total_households": "세대수",
            "quadrant": "그룹",
        },
    )

    # 버블 오버레이: quadrant 별 색상
    _quad_color = {"A": "#d62728", "B": "#1f77b4", "C": "#2ca02c", "D": "#7f7f7f", "-": "#cccccc"}
    _size_col = primary["target_density"].fillna(0).astype(float)
    _size_max = float(_size_col.max() or 1)
    _sizes = 6 + (_size_col / _size_max) * 24  # 6 ~ 30
    _colors = primary["quadrant"].map(_quad_color).fillna("#cccccc")

    fig.add_trace(go.Scattermapbox(
        lat=primary["centroid_lat"],
        lon=primary["centroid_lon"],
        mode="markers",
        marker=dict(size=_sizes, color=_colors, opacity=0.85),
        text=primary["admin_dong_name"],
        hovertemplate=(
            "<b>%{text}</b><br>"
            "침투율: %{customdata[0]:.3f}%<br>"
            "밀집도: %{customdata[1]:.2f}%<br>"
            "그룹: %{customdata[2]}<extra></extra>"
        ),
        customdata=primary[["penetration_rate", "target_density", "quadrant"]].values,
        name="타겟 밀집도",
        showlegend=False,
    ))

    fig.update_layout(
        margin={"r": 0, "t": 0, "l": 0, "b": 0},
        height=560,
        coloraxis_colorbar=dict(title="침투율 %"),
    )
    st.plotly_chart(fig, use_container_width=True)


def _render_quadrant_scatter(df: pd.DataFrame) -> None:
    """중앙값 기준 4분면 산점도 (지도 판독 보조)."""
    import plotly.express as px
    if df is None or df.empty:
        return
    med_p = float(df.attrs.get("median_penetration", 0.0))
    med_d = float(df.attrs.get("median_target_density", 0.0))
    _quad_color = {"A": "#d62728", "B": "#1f77b4", "C": "#2ca02c", "D": "#7f7f7f", "-": "#cccccc"}

    fig = px.scatter(
        df,
        x="penetration_rate",
        y="target_density",
        color="quadrant",
        color_discrete_map=_quad_color,
        hover_name="admin_dong_name",
        hover_data={
            "purchase_count": True, "total_households": True, "total_population": True,
            "age_30_49_population": True,
            "penetration_rate": ":.3f", "target_density": ":.2f",
        },
        labels={
            "penetration_rate": "시장 침투율 %",
            "target_density": "타겟 밀집도 % (30~49세)",
            "quadrant": "그룹",
        },
        title="2x2 매트릭스 (중앙값 기준 4분면)",
    )
    fig.add_vline(x=med_p, line_dash="dash", line_color="grey",
                  annotation_text=f"침투율 중앙값 {med_p:.3f}%", annotation_position="top")
    fig.add_hline(y=med_d, line_dash="dash", line_color="grey",
                  annotation_text=f"밀집도 중앙값 {med_d:.2f}%", annotation_position="right")
    fig.update_layout(height=420, margin={"r": 20, "t": 60, "l": 40, "b": 40})
    st.plotly_chart(fig, use_container_width=True)


def _render_table(df: pd.DataFrame) -> None:
    """상세 표 (그룹별 정렬)."""
    if df is None or df.empty:
        return
    _cols = [
        "quadrant", "admin_dong_name", "admin_dong_code", "purchase_count",
        "total_households", "total_population", "age_30_49_population",
        "penetration_rate", "target_density",
    ]
    if "sigungu_label" in df.columns:
        _cols.insert(2, "sigungu_label")
    show = df[_cols].copy()
    show = show.sort_values(
        by=["quadrant", "penetration_rate", "target_density"],
        ascending=[True, False, False],
    )
    show = show.rename(columns={
        "quadrant": "그룹",
        "admin_dong_name": "행정동",
        "sigungu_label": "시군구",
        "admin_dong_code": "행정동코드",
        "purchase_count": "구매건수(기간내)",
        "total_households": "세대수",
        "total_population": "총인구",
        "age_30_49_population": "3040 인구",
        "penetration_rate": "침투율(%)",
        "target_density": "밀집도(%)",
    })
    st.dataframe(show, use_container_width=True, hide_index=True)
