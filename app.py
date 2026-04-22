# -*- coding: utf-8 -*-
from __future__ import annotations
"""
momo - 가구 매장 세일즈 및 경영 대시보드
"""
import base64
import calendar
import io
import hmac
import html
import json
import os
import re
import sqlite3
import threading
import textwrap
import traceback
import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta, time as dt_time, timezone
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def _today_kst() -> date:
    """앱 전역 '오늘' 기준. 서버가 UTC 등이어도 한국 날짜와 일치."""
    return datetime.now(tz=KST).date()


import requests
import hashlib
import time
import plotly.express as px
import plotly.graph_objects as go
try:
    from crm_automation import render_crm_menu  # type: ignore
    CRM_MODULE_AVAILABLE = True
except Exception:
    CRM_MODULE_AVAILABLE = False
try:
    import folium
    from folium.plugins import MarkerCluster
    from streamlit_folium import st_folium
    FOLIUM_AVAILABLE = True
except ImportError:
    FOLIUM_AVAILABLE = False

try:
    from supabase import create_client as _create_supabase_client
except ImportError:
    _create_supabase_client = None

# Supabase 클라이언트 Singleton 캐시 (연결 재사용으로 호출 횟수 47회+ 시 부하 감소)
# 형식: (client, url, key) — URL/Key가 바뀌면 새로 생성
_supabase_client_cache = None
_supabase_admin_client_cache = None

# 브라우저 탭 타이틀 및 레이아웃 (반드시 최상단에서 호출)
# 모바일: 넓은 화면 사용 + 사이드바 접힌 상태로 시작
# [아이콘] assets/apple-touch-icon.png 가 있으면 탭·홈화면 추가 아이콘으로 사용.
#         파일 위치: app.py와 같은 디렉터리 기준 assets/apple-touch-icon.png (예: 프로젝트루트/assets/apple-touch-icon.png)
#         권장: 180x180 또는 192x192 PNG, 에몬스 'e' 로고 또는 가구 아이콘.
_ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "apple-touch-icon.png")
st.set_page_config(
    page_title="momo",
    layout="wide",
    initial_sidebar_state="collapsed",
    page_icon=_ICON_PATH if os.path.exists(_ICON_PATH) else "🪑",
    menu_items={
        "Get Help": None,
        "Report a bug": None,
        "About": "**momo** — 에몬스 울산전시장\n\nⓒ 2025 에몬스 울산전시장. All rights reserved.",
    },
)


def _inject_favicon():
    """웹 탭 아이콘(favicon) 및 iOS 홈화면 추가용 apple-touch-icon을 <head>에 주입.
    assets/apple-touch-icon.png 가 있으면 data URL로 넣고, 없으면 무시."""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "apple-touch-icon.png")
    if not os.path.exists(path):
        return
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except Exception:
        return
    b64 = base64.b64encode(raw).decode("utf-8")
    data_url = f"data:image/png;base64,{b64}"
    st.markdown(
        f'<link rel="icon" href="{html.escape(data_url)}" type="image/png">'
        f'<link rel="apple-touch-icon" href="{html.escape(data_url)}">',
        unsafe_allow_html=True,
    )


def _inject_mobile_css():
    """모바일/스마트폰 환경용 CSS: 헤더·로고·여백·간격 전반 개선, 반응형 열·표·터치 친화."""
    st.markdown(
        """
        <style>
        /* ----- 로고/이미지 최적화: 화면 밖으로 잘리지 않게 (전체 화면) ----- */
        .block-container img, [data-testid="stSidebar"] img, .stMarkdown img, main img {
            max-width: 100% !important;
            height: auto !important;
            object-fit: contain !important;
        }
        .mobile-menu-hint { display: none; }
        /* ----- 모바일 구간 (768px 이하) ----- */
        @media (max-width: 768px) {
            /* 반응형 글꼴 크기: 헤더 한 줄에 들어오게 */
            h1, [data-testid="stMarkdown"] h1 { font-size: 1.5rem !important; line-height: 1.3 !important; font-weight: 600 !important; }
            h2, [data-testid="stMarkdown"] h2 { font-size: 1.2rem !important; line-height: 1.3 !important; font-weight: 600 !important; }
            h3, [data-testid="stMarkdown"] h3 { font-size: 1rem !important; line-height: 1.3 !important; font-weight: 600 !important; }
            /* 여백 최소화: 화면 최대한 넓게 */
            .block-container {
                padding-top: 1rem !important;
                padding-left: 1rem !important;
                padding-right: 1rem !important;
                padding-bottom: 1rem !important;
                max-width: 100% !important;
            }
            /* 요소 간격 축소: 입력창·버튼 사이 상하 간격 줄여 스크롤 감소 */
            [data-testid="stVerticalBlock"] > div { margin-bottom: 0.35rem !important; }
            .stTextInput, .stNumberInput, [data-testid="stSelectbox"], .stMultiSelect, .stTextArea { margin-bottom: 0.25rem !important; }
            .stButton > button { margin-bottom: 0.25rem !important; }
            /* 열(columns) 세로 배치 */
            [data-testid="column"] { min-width: 100% !important; }
            [data-testid="stHorizontalBlock"] > div { flex: 1 1 100% !important; min-width: 0 !important; }
            /* 표 영역 패딩 최소화, 가로 스크롤 */
            [data-testid="stDataFrame"] { padding: 0 2px !important; overflow-x: auto !important; }
            [data-testid="stDataFrame"] > div { margin: 0 !important; max-width: 100vw !important; }
            /* 탭 가로 스크롤 */
            [data-testid="stTabs"] > div > div { overflow-x: auto !important; -webkit-overflow-scrolling: touch !important; }
            [data-testid="stTabs"] [role="tablist"] { flex-wrap: nowrap !important; }
            .mobile-menu-hint { display: block !important; }
            /* 터치 친화: 버튼·입력창 최소 높이 */
            button[kind="primary"], button[kind="secondary"], .stButton > button { min-height: 44px !important; padding: 0.5rem 0.75rem !important; font-size: 1rem !important; }
            .stTextInput > div > div > input, .stTextArea > div > div { min-height: 44px !important; font-size: 16px !important; }
            [data-testid="stSelectbox"] > div { min-height: 44px !important; }
            /* 지도 말풍선: 모바일 가독성 */
            .leaflet-popup-content .map-popup { font-size: 14px !important; line-height: 1.4 !important; max-width: min(280px, 85vw) !important; padding: 6px 8px !important; }
        }
        /* 지도 말풍선 공통 */
        .leaflet-popup-content .map-popup { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _inject_branding_css():
    """Streamlit 기본 로고·footer·툴바·햄버거 메뉴 숨김 + 네이비 블루 브랜딩 CSS 주입.
    버튼·링크·강조색은 네이비 블루(#1B3A6B)로 통일.
    배경색 등 기존 레이아웃은 유지."""
    st.markdown(
        """
        <style>
        /* ── Streamlit 기본 UI 요소 숨김 ── */
        /* 우상단 햄버거(⋮) 메뉴 */
        #MainMenu { visibility: hidden !important; }
        /* 하단 "Made with Streamlit" 푸터 */
        footer { visibility: hidden !important; }
        /* 상단 Streamlit 로고/워터마크 영역 */
        header[data-testid="stHeader"] { background: transparent !important; }
        [data-testid="stDecoration"] { display: none !important; }
        /* 배포 툴바(Share/Edit/Running...) 숨김 */
        [data-testid="stToolbar"] { display: none !important; }
        /* 앱 상단 좁은 Streamlit 컬러 바(빨강·주황·초록) */
        .stApp > header::before { display: none !important; }

        /* ── 네이비 블루 브랜딩 ── */
        /* 기본 버튼 */
        .stButton > button[kind="primary"],
        .stButton > button[data-testid="baseButton-primary"] {
            background-color: #1B3A6B !important;
            border-color: #1B3A6B !important;
            color: #ffffff !important;
        }
        .stButton > button[kind="primary"]:hover,
        .stButton > button[data-testid="baseButton-primary"]:hover {
            background-color: #142d55 !important;
            border-color: #142d55 !important;
        }
        /* 링크 컬러 */
        a { color: #1B3A6B !important; }
        a:hover { color: #142d55 !important; }
        /* 탭 선택 언더라인 */
        [data-testid="stTabs"] [role="tab"][aria-selected="true"] {
            border-bottom-color: #1B3A6B !important;
            color: #1B3A6B !important;
        }
        /* 진행 표시줄 */
        .stProgress > div > div > div { background-color: #1B3A6B !important; }
        /* 슬라이더 */
        [data-testid="stSlider"] [role="slider"] { background-color: #1B3A6B !important; }
        /* 체크박스·라디오 강조 */
        [data-testid="stCheckbox"] svg, [data-testid="stRadio"] svg {
            fill: #1B3A6B !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


# ========== Supabase 연결 (st.secrets 기반) ==========
# Customers·Sales는 Supabase 테이블 사용. id(기본키) 기준으로만 단일 행 조회/수정하여 중복·오조회 방지.
def _sales_tenant_column() -> str | None:
    """Sales 테이블 테넌트 구분 컬럼명. secrets에 sales_tenant_column이 있고 비어있지 않을 때만 사용. 없거나 비면 None(테넌트 컬럼 미사용)."""
    try:
        val = (st.secrets.get("supabase") or {}).get("sales_tenant_column")
        if val is None or str(val).strip() == "":
            return None
        return str(val).strip()
    except Exception:
        return None


def _customers_tenant_column() -> str | None:
    """Customers 테이블 테넌트 구분 컬럼명. secrets에 customers_tenant_column이 있고 비어있지 않을 때만 사용. 없거나 비면 None(컬럼 미사용)."""
    try:
        val = (st.secrets.get("supabase") or {}).get("customers_tenant_column")
        if val is None or str(val).strip() == "":
            return None
        return str(val).strip()
    except Exception:
        return None


def get_supabase_client():
    """
    Supabase 클라이언트 반환 (Singleton). 동일 URL/Key면 한 번만 생성 후 재사용.
    반환: (client, None) 또는 (None, error_message).
    """
    global _supabase_client_cache
    if _create_supabase_client is None:
        return None, "Supabase 라이브러리가 설치되지 않았습니다. pip install supabase 를 실행해 주세요."
    try:
        secrets = st.secrets.get("supabase") or {}
        url = (secrets.get("url") or "").strip()
        key = (secrets.get("key") or secrets.get("anon_key") or "").strip()
        if not url or not key:
            return None, "Supabase URL 또는 Key가 설정되지 않았습니다. .streamlit/secrets.toml에 [supabase] url, key를 추가해 주세요."
        # 캐시된 클라이언트가 있고 URL/Key가 동일하면 재사용 (연결 중복 생성 방지)
        if _supabase_client_cache is not None:
            _client, _url, _key = _supabase_client_cache
            if _url == url and _key == key:
                return _client, None
        client = _create_supabase_client(url, key)
        _supabase_client_cache = (client, url, key)
        return client, None
    except Exception as e:
        err_msg = str(e)
        if not err_msg or err_msg.strip() == "":
            err_msg = "연결 오류가 발생했습니다."
        return None, f"Supabase 연결에 실패했습니다: {err_msg}"


def get_supabase_client_or_warn():
    """Supabase 클라이언트 반환 (내부적으로 Singleton 재사용). 실패 시 화면에 경고를 띄우고 None 반환."""
    client, err = get_supabase_client()
    if err:
        st.error(f"⚠️ {err}")
        return None
    return client


def get_supabase_client_with_auth_session():
    """
    로그인된 사용자의 Supabase 세션을 복원한 클라이언트 반환.
    비밀번호 변경(update_user) 등 인증이 필요한 API 호출 시 사용.
    반환: (client, None) 또는 (None, error_message).
    """
    client, err = get_supabase_client()
    if err or client is None:
        return None, err or "Supabase 클라이언트를 사용할 수 없습니다."
    sess = st.session_state.get("supabase_session")
    if isinstance(sess, dict) and sess.get("access_token") and sess.get("refresh_token"):
        try:
            client.auth.set_session(sess["access_token"], sess["refresh_token"])
        except Exception as e:
            return None, f"세션 복원 실패: {str(e)}"
    return client, None


def get_supabase_admin_client():
    """
    Supabase Admin API용 클라이언트 (service_role_key) — Singleton.
    직원 계정 생성 등 관리자 전용 작업 시 사용. 동일 설정이면 한 번만 생성 후 재사용.
    반환: (client, None) 또는 (None, error_message).
    """
    global _supabase_admin_client_cache
    if _create_supabase_client is None:
        return None, "Supabase 라이브러리가 설치되지 않았습니다."
    try:
        secrets = st.secrets.get("supabase") or {}
        url = (secrets.get("url") or "").strip()
        if not url:
            return None, "Supabase URL이 설정되지 않았습니다."
        key = (secrets.get("service_role_key") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        if not key:
            return None, "직원 계정 생성을 위해 Supabase service_role_key가 필요합니다. .streamlit/secrets.toml에 [supabase] service_role_key를 추가하거나, 환경변수 SUPABASE_SERVICE_ROLE_KEY를 설정해 주세요."
        if _supabase_admin_client_cache is not None:
            _client, _url, _key = _supabase_admin_client_cache
            if _url == url and _key == key:
                return _client, None
        client = _create_supabase_client(url, key)
        _supabase_admin_client_cache = (client, url, key)
        return client, None
    except Exception as e:
        err_msg = str(e).strip() or "연결 오류가 발생했습니다."
        return None, f"Supabase Admin 연결 실패: {err_msg}"


def _supabase_auth_uid_by_email(admin_client, email: str):
    """
    Supabase Auth에서 이메일로 사용자 UUID 조회. list_users는 기본 50명만 반환하므로 페이지네이션으로 전체 검색.
    반환: auth.users.id (UUID 문자열) 또는 None.
    """
    if not admin_client or not email or not str(email).strip():
        return None
    target = str(email).strip().lower()
    page = 1
    # Supabase Auth list_users 기본/안정 페이지 크기(환경별 인자 무시 대비)
    per_page = 50
    while True:
        try:
            r = admin_client.auth.admin.list_users(per_page=per_page, page=page)
        except TypeError:
            try:
                r = admin_client.auth.admin.list_users({"per_page": per_page, "page": page})
            except Exception:
                r = admin_client.auth.admin.list_users()
        # 일부 SDK 환경에서는 list_users가 list를 직접 반환함
        if isinstance(r, list):
            users = r
        else:
            users_raw = getattr(r, "users", None)
            if users_raw is None:
                users_raw = getattr(r, "data", None)
            # SDK 버전에 따라 data가 모델 객체일 수 있으므로 model_dump를 폭넓게 시도
            if users_raw is None and hasattr(r, "model_dump"):
                try:
                    users_raw = r.model_dump()
                except Exception:
                    users_raw = None
            if users_raw is not None and hasattr(users_raw, "model_dump"):
                try:
                    users_raw = users_raw.model_dump()
                except Exception:
                    pass
            # SDK/버전별 응답 형태를 모두 흡수해 users(list)로 정규화
            users = []
            if isinstance(users_raw, list):
                users = users_raw
            elif isinstance(users_raw, dict):
                if isinstance(users_raw.get("users"), list):
                    users = users_raw.get("users") or []
                elif isinstance(users_raw.get("data"), list):
                    users = users_raw.get("data") or []
                elif isinstance(users_raw.get("data"), dict) and isinstance((users_raw.get("data") or {}).get("users"), list):
                    users = (users_raw.get("data") or {}).get("users") or []
            elif hasattr(users_raw, "users"):
                maybe_users = getattr(users_raw, "users", None)
                if isinstance(maybe_users, list):
                    users = maybe_users
            elif hasattr(users_raw, "data"):
                maybe_data = getattr(users_raw, "data", None)
                if isinstance(maybe_data, list):
                    users = maybe_data
                elif hasattr(maybe_data, "users") and isinstance(getattr(maybe_data, "users", None), list):
                    users = getattr(maybe_data, "users")
        for u in (users or []):
            em = getattr(u, "email", None) if hasattr(u, "email") else (u.get("email") if isinstance(u, dict) else None)
            if em and str(em).strip().lower() == target:
                uid = getattr(u, "id", None) if hasattr(u, "id") else (u.get("id") if isinstance(u, dict) else None)
                return uid
        if not users or len(users) < per_page:
            break
        page += 1
        if page > 100:
            break
    return None


# ---------- Supabase 직원/매장 테이블 (app_users, app_user_stores, app_stores) ----------
# 테이블이 없으면 ensure_supabase_app_tables()로 자동 생성 시도(database_url 있을 때) 또는 SQL Editor에서 SUPABASE_APP_TABLES.sql 실행.
# 아래 매장/직원 목록 등은 @st.cache_data(ttl=600)로 10분 캐시. 매장·직원 추가/수정/삭제 후 반드시 clear_data_cache() 호출하면 즉시 갱신됨.

def _supabase_app_tables_available():
    """Supabase에 app_users 테이블이 있는지 확인. 세션당 1회만 HTTP 쿼리 후 결과를 세션 캐시."""
    _cache_key = "_supa_app_tables_avail"
    if _cache_key in st.session_state:
        return st.session_state[_cache_key]
    client, err = get_supabase_client()
    if err or not client:
        return False
    try:
        client.table("app_users").select("id").limit(1).execute()
        st.session_state[_cache_key] = True
        return True
    except Exception:
        st.session_state[_cache_key] = False
        return False


def _supabase_run_sql_file(db_url: str, sql_path: str) -> bool:
    """psycopg2로 SQL 파일을 Supabase에 실행. 성공 시 True."""
    try:
        import psycopg2
    except ImportError:
        return False
    if not os.path.isfile(sql_path):
        return False
    try:
        with open(sql_path, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        return False
    lines = []
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("--") or not s:
            continue
        lines.append(line)
    raw_clean = "\n".join(lines)
    statements = []
    for part in raw_clean.split(";"):
        s = part.strip()
        if s and not s.upper().startswith("--"):
            statements.append(s + ";")
    if not statements:
        return False
    conn = None
    try:
        conn = psycopg2.connect(db_url)
        conn.autocommit = True
        cur = conn.cursor()
        for stmt in statements:
            stmt = stmt.strip()
            if not stmt or stmt == ";":
                continue
            try:
                cur.execute(stmt)
            except Exception as e:
                err_msg = str(e).lower()
                if "already exists" not in err_msg and "duplicate" not in err_msg:
                    try:
                        cur.close()
                        conn.close()
                    except Exception:
                        pass
                    return False
        cur.close()
        return True
    except Exception:
        return False
    finally:
        if conn and not conn.closed:
            try:
                conn.close()
            except Exception:
                pass


def _supabase_run_app_tables_sql():
    """
    Supabase DB에 app_stores, app_users, app_user_stores, app_edit_requests 테이블이 없으면 생성.
    st.secrets의 supabase.database_url (Postgres 연결 문자열)이 있으면 psycopg2로 DDL 실행.
    성공 시 True, 실패 또는 URL 없음 시 False.
    """
    secrets = (st.secrets.get("supabase") or {}) if "secrets" in dir(st) else {}
    if not secrets:
        try:
            import streamlit as _st
            secrets = _st.secrets.get("supabase") or {}
        except Exception:
            pass
    db_url = (secrets.get("database_url") or secrets.get("db_url") or "").strip()
    if not db_url:
        return False
    sql_files = [
        "SUPABASE_APP_TABLES.sql",
        "SUPABASE_APP_EDIT_REQUESTS.sql",
    ]
    ok = False
    for fname in sql_files:
        fpath = os.path.join(BASE_DIR, fname)
        if os.path.isfile(fpath):
            _supabase_run_sql_file(db_url, fpath)
            ok = True
    return ok


def ensure_supabase_app_tables():
    """
    Supabase에 직원/매장용 테이블(app_users, app_user_stores, app_stores)이 있는지 확인.
    없으면 database_url로 자동 생성 시도. 성공 시 True, 아니면 False.
    """
    if _supabase_app_tables_available():
        return True
    if _supabase_run_app_tables_sql():
        return _supabase_app_tables_available()
    return False


@st.cache_data(ttl=3600)
def _get_supabase_stores_list():
    """Supabase app_stores 목록 캐시 (10분). 매장 추가/수정 후 clear_data_cache() 호출 시 갱신."""
    client, err = get_supabase_client()
    if err or not client:
        return []
    try:
        r = client.table("app_stores").select("id, store_name, db_filename").order("id").execute()
        return (r.data or []) if hasattr(r, "data") else []
    except Exception:
        return []


@st.cache_data(ttl=3600)
def get_supabase_stores_dataframe_cached():
    """
    Supabase app_stores 매장 목록을 DataFrame으로 반환 — 10분 캐시.
    단일 데이터 소스(Supabase) 전용. clear_data_cache() 호출 시 갱신.
    반환: DataFrame 컬럼 id, store_name, db_filename (빈 DataFrame일 수 있음).
    """
    data = _get_supabase_stores_list()
    if not data:
        return pd.DataFrame(columns=["id", "store_name", "db_filename"])
    df = pd.DataFrame(data)
    if "id" in df.columns:
        df = df.sort_values("id", ignore_index=True)
    return df


def _get_supabase_app_user_by_email(email: str):
    """
    Supabase app_users에서 이메일로 사용자 조회.
    반환: (id, username, role, store_id, db_filename) 또는 None.
    """
    if not email or not str(email).strip():
        return None
    client, err = get_supabase_client()
    if err or not client:
        return None
    try:
        email_clean = str(email).strip().lower()
        r = client.table("app_users").select("id, username, role, store_id").eq("email", email_clean).execute()
        rows = (r.data or []) if hasattr(r, "data") else []
        if not rows:
            return None
        row = rows[0]
        uid, username, role, store_id = row.get("id"), row.get("username"), row.get("role"), row.get("store_id")
        db_filename = ""
        first_sid = store_id
        if not first_sid:
            us = client.table("app_user_stores").select("store_id").eq("user_id", uid).limit(1).execute()
            if us.data and us.data[0].get("store_id"):
                first_sid = us.data[0]["store_id"]
        if first_sid:
            s = client.table("app_stores").select("db_filename").eq("id", first_sid).maybe_single().execute()
            if s.data and s.data.get("db_filename"):
                db_filename = s.data["db_filename"]
        return (uid, username, role, store_id, db_filename)
    except Exception:
        return None


@st.cache_data(ttl=1800)
def _get_supabase_user_allowed_stores(user_id: int):
    """접근 가능 매장 목록 캐시 (30분). 로그인 사용자별로 캐시되며, 배정 변경 시 clear_data_cache()로 갱신."""
    if not user_id:
        return []
    client, err = get_supabase_client()
    if err or not client:
        return []
    try:
        us = client.table("app_user_stores").select("store_id").eq("user_id", user_id).execute()
        store_ids = [x["store_id"] for x in (us.data or [])]
        if not store_ids:
            row = client.table("app_users").select("store_id").eq("id", user_id).maybe_single().execute()
            if row.data and row.data.get("store_id"):
                store_ids = [row.data["store_id"]]
        if not store_ids:
            return []
        out = []
        for sid in store_ids:
            s = client.table("app_stores").select("id, db_filename, store_name").eq("id", sid).maybe_single().execute()
            if s.data:
                out.append((s.data["id"], s.data["db_filename"], s.data["store_name"]))
        return sorted(out, key=lambda x: (x[2] or "", x[0]))
    except Exception:
        return []


@st.cache_data(ttl=3600)
def _get_supabase_store_by_db_filename(db_filename: str):
    """db_filename → store id 조회. @st.cache_data 1시간 캐시. 매장 변경 시 clear_data_cache()로 갱신."""
    if not db_filename:
        return None
    client, err = get_supabase_client()
    if err or not client:
        return None
    try:
        r = client.table("app_stores").select("id").eq("db_filename", db_filename).maybe_single().execute()
        return r.data.get("id") if r.data else None
    except Exception:
        return None


def _ensure_supabase_superadmin_email(email_clean: str):
    """app_users에서 username=superadmin인 행에 email 설정 (billymind@gmail.com 복구용)."""
    client, err = get_supabase_client()
    if err or not client:
        return
    try:
        client.table("app_users").update({"email": email_clean}).eq("username", "superadmin").execute()
    except Exception:
        pass


@st.cache_data(ttl=600)
def _get_supabase_store_assigned_employee_names(db_filename: str) -> list:
    store_id = _get_supabase_store_by_db_filename(db_filename)
    if not store_id:
        return []
    client, err = get_supabase_client()
    if err or not client:
        return []
    try:
        us = client.table("app_user_stores").select("user_id").eq("store_id", store_id).execute()
        user_ids = [x["user_id"] for x in (us.data or [])]
        if not user_ids:
            u = client.table("app_users").select("id").eq("store_id", store_id).execute()
            user_ids = [x["id"] for x in (u.data or [])]
        if not user_ids:
            return []

        r = client.table("app_users").select("name, username").in_("id", user_ids).execute()
        out = []
        for user_data in (r.data or []):
            display = (str(user_data.get("name") or "").strip() or str(user_data.get("username") or "").strip()) or None
            if display and display not in out:
                out.append(display)
        return out
    except Exception:
        return []


@st.cache_data(ttl=3600)
def _get_supabase_users_list():
    """Supabase app_users 전체 목록 캐시 (10분). 직원 추가/수정 후 clear_data_cache() 호출 시 갱신."""
    client, err = get_supabase_client()
    if err or not client:
        return []
    try:
        r = client.table("app_users").select("id, username, email, role, name, store_id").order("username").execute()
        return (r.data or []) if hasattr(r, "data") else []
    except Exception:
        return []


def _get_app_user_display_name_map():
    """
    이메일/username → 표시명(name 우선, 없으면 username) 매핑. To-Do 작성자 등 표기용.
    캐시된 _get_supabase_users_list() 활용으로 매번 쿼리하지 않음.
    """
    users = _get_supabase_users_list()
    out = {}
    for u in users:
        name = (str(u.get("name") or "").strip() or str(u.get("username") or "").strip()) or ""
        if name:
            if u.get("email"):
                out[str(u["email"]).strip().lower()] = name
            if u.get("username"):
                out[str(u["username"]).strip()] = name
                out[str(u["username"]).strip().lower()] = name
    return out


def _get_supabase_user_store_ids(user_id: int):
    """한 직원의 배정 매장 id 목록 (Supabase app_user_stores)."""
    if not user_id:
        return []
    client, err = get_supabase_client()
    if err or not client:
        return []
    try:
        r = client.table("app_user_stores").select("store_id").eq("user_id", user_id).execute()
        return [x["store_id"] for x in (r.data or [])]
    except Exception:
        return []


@st.cache_data(ttl=3600)
def _get_supabase_employee_list_with_stores():
    """직원 명부 + 배정매장 문자열 캐시 (10분). 직원/매장 변경 시 clear_data_cache()로 갱신."""
    users = _get_supabase_users_list()
    if not users:
        return []
    client, err = get_supabase_client()
    if err or not client:
        return [{"id": u["id"], "email": u.get("email"), "username": u.get("username"), "name": u.get("name"), "role": u.get("role"), "배정매장": ""} for u in users]
    store_names_by_id = {}
    try:
        stores_r = client.table("app_stores").select("id, store_name").execute()
        for s in (stores_r.data or []):
            store_names_by_id[s["id"]] = s.get("store_name") or ""
    except Exception:
        pass
    out = []
    for u in users:
        uid = u.get("id")
        store_ids = _get_supabase_user_store_ids(uid)
        names = [store_names_by_id.get(sid, "") for sid in store_ids if sid]
        배정매장 = ", ".join(n for n in names if n)
        if not 배정매장 and u.get("store_id"):
            배정매장 = store_names_by_id.get(u["store_id"], "") or ""
        # 기본 매장: app_users.store_id 기준
        primary_sid = u.get("store_id")
        기본매장 = store_names_by_id.get(primary_sid, "") if primary_sid else ""
        out.append({
            "id": uid,
            "email": u.get("email"),
            "username": u.get("username"),
            "name": u.get("name"),
            "role": u.get("role"),
            "배정매장": 배정매장,
            "기본매장": 기본매장,
        })
    return out


def _supabase_insert_app_user(username: str, email: str, role: str, store_id, name: str):
    """app_users에 한 행 삽입 (비밀번호는 Supabase Auth에서 관리하므로 placeholder 저장). 반환: (user_id, None) 또는 (None, error_msg)."""
    client, err = get_supabase_client()
    if err or not client:
        return None, (err or "Supabase 연결 불가")
    try:
        pw_placeholder = hashlib.sha256("supabase_managed".encode()).hexdigest()
        email_clean = (email or "").strip().lower() or None
        row = {
            "username": (username or "").strip(),
            "password": pw_placeholder,
            "email": email_clean,
            "role": (role or "user").strip(),
            "store_id": int(store_id) if store_id is not None else None,
            "name": (name or "").strip() or None,
        }
        r = client.table("app_users").insert(row).execute()
        data = (r.data if hasattr(r, "data") else None)
        if data is not None and len(data) > 0:
            row_data = data[0] if isinstance(data, list) else data
            uid = row_data.get("id") if isinstance(row_data, dict) else None
            if uid is not None:
                return int(uid), None
        if email_clean:
            existing = client.table("app_users").select("id").eq("email", email_clean).maybe_single().execute()
            if existing.data and existing.data.get("id") is not None:
                return int(existing.data["id"]), None
        return None, "insert 후 id를 가져오지 못했습니다."
    except Exception as e:
        return None, str(e)


def _supabase_update_app_user(user_id: int, name: str, role: str, store_id, store_ids: list):
    """app_users 한 행 수정 + app_user_stores 교체. 에러 시 예외."""
    client, err = get_supabase_client()
    if err or not client:
        raise RuntimeError(err or "Supabase 연결 불가")
    client.table("app_users").update({
        "name": (name or "").strip() or None,
        "role": role,
        "store_id": store_id,
    }).eq("id", user_id).execute()
    client.table("app_user_stores").delete().eq("user_id", user_id).execute()
    for sid in (store_ids or []):
        client.table("app_user_stores").insert({"user_id": user_id, "store_id": sid}).execute()


def _supabase_delete_app_user(user_id: int):
    """app_user_stores 삭제 후 app_users 삭제. 에러 시 예외."""
    client, err = get_supabase_client()
    if err or not client:
        raise RuntimeError(err or "Supabase 연결 불가")
    client.table("app_user_stores").delete().eq("user_id", user_id).execute()
    client.table("app_users").delete().eq("id", user_id).execute()


def _supabase_get_app_user_by_email(email: str):
    """이메일로 app_users 한 행 조회. 없으면 None."""
    if not email or not str(email).strip():
        return None
    client, err = get_supabase_client()
    if err or not client:
        return None
    try:
        r = client.table("app_users").select("id, username, email, role, store_id, name").eq("email", str(email).strip().lower()).maybe_single().execute()
        return r.data if r.data else None
    except Exception:
        return None


def _get_customer_name_supabase(db_filename: str, customer_id: int) -> str:
    """Supabase app_customers 테이블에서 id(기본키)·store_name 기준으로 고객명 조회."""
    client, err = get_supabase_client()
    if err or not customer_id:
        return ""
    store_name = _get_current_store_name_for_customers(db_filename)
    if not store_name:
        return ""
    try:
        q = client.table("app_customers").select("name").eq("id", int(customer_id)).eq("store_name", store_name)
        r = q.maybe_single().execute()
        row = r.data[0] if isinstance(r.data, list) and r.data else (r.data if isinstance(r.data, dict) else None)
        if row and row.get("name"):
            return (row["name"] or "").strip()
    except Exception:
        pass
    return ""


def _get_customers_by_ids_supabase(db_filename: str, customer_ids: list) -> dict:
    """Supabase app_customers에서 id 목록·store_name으로 고객 조회. 반환: { id: { name, phone1, phone2, address }, ... }"""
    if not customer_ids:
        return {}
    client, err = get_supabase_client()
    if err:
        return {}
    store_name = _get_current_store_name_for_customers(db_filename)
    if not store_name:
        return {}
    try:
        q = client.table("app_customers").select("id, name, phone1, phone2, address").eq("store_name", store_name).in_("id", customer_ids)
        r = q.execute()
        return {row["id"]: row for row in (r.data or [])}
    except Exception:
        return {}

# ========== 채널톡(Channel Talk) Open API 헬퍼 ==========
CHANNEL_TALK_BASE_URL = "https://api.channel.io/open/v5"


def _get_channel_talk_secrets():
    """st.secrets에서 채널톡 API 키 로드. 없으면 None 반환."""
    try:
        api_key = st.secrets.get("CHANNEL_TALK_API_KEY") or st.secrets.get("channel_talk", {}).get("CHANNEL_TALK_API_KEY")
        access_secret = st.secrets.get("CHANNEL_TALK_ACCESS_SECRET") or st.secrets.get("channel_talk", {}).get("CHANNEL_TALK_ACCESS_SECRET")
        if api_key and access_secret:
            return {"api_key": api_key, "access_secret": access_secret}
    except Exception:
        pass
    return None


def _channel_talk_headers():
    """채널톡 API 요청용 헤더 (x-access-key, x-access-secret)."""
    secrets = _get_channel_talk_secrets()
    if not secrets:
        return None
    return {
        "Content-Type": "application/json",
        "x-access-key": secrets["api_key"],
        "x-access-secret": secrets["access_secret"],
    }


def _get_channel_talk_sync_cutoff_date():
    """
    st.secrets의 CHANNEL_TALK_SYNC_CUTOFF_DATE(YYYY-MM-DD)를 date 객체로 반환.
    미설정 또는 파싱 실패 시 None. 기준일 이후 등록 고객만 채널톡 동기화 시 사용.
    """
    try:
        raw = st.secrets.get("CHANNEL_TALK_SYNC_CUTOFF_DATE") or st.secrets.get("channel_talk", {}).get("CHANNEL_TALK_SYNC_CUTOFF_DATE")
        if not raw or not str(raw).strip():
            return None
        return datetime.strptime(str(raw).strip()[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def sync_channel_talk_customer(
    customer_name: str,
    phone_number: str,
    purchase_amount: int | float,
    item_category: str,
    purchase_date,
    store_tag_key: str | None = None,
    is_returning: bool = False,
    unpaid_balance: float = 0.0,
) -> bool:
    """
    오프라인 결제 고객 정보를 채널톡에 PUSH.
    - 기존 태그 유지 + 새 구매 태그 추가
    - 재구매 시 '재구매_{매장키}' 태그 추가
    - 미수금 있으면 '미수금_{매장키}' 태그 추가, 완납이면 제거
    - 태그 형식: '{매장키}구매/{품목}' (예: 삼산구매/옷장)
    실패 시 False, 성공 시 True. 예외는 호출부에서 처리.
    """
    headers = _channel_talk_headers()
    if not headers or not phone_number or not str(phone_number).strip():
        return False
    member_id = re.sub(r"\D", "", str(phone_number).strip())
    if not member_id:
        return False
    category_clean = (re.sub(r"\s+", "", (item_category or "").strip()) or "기타")
    sk = str(store_tag_key).strip() if store_tag_key and str(store_tag_key).strip() else ""
    tag_purchase = f"{sk}구매/{category_clean}" if sk else f"{category_clean}_구매"
    tag_returning = f"재구매_{sk}" if sk else "재구매"
    tag_unpaid = f"미수금_{sk}" if sk else "미수금"
    purchase_date_str = purchase_date.isoformat() if hasattr(purchase_date, "isoformat") else str(purchase_date)

    # 1) GET 기존 유저 (태그 병합 + 재구매 여부 판단)
    existing_tags = []
    is_existing_user = False
    try:
        r_get = requests.get(
            f"{CHANNEL_TALK_BASE_URL}/users/@{member_id}",
            headers=headers,
            timeout=10,
        )
        if r_get.status_code == 200:
            data = r_get.json()
            if isinstance(data, dict):
                existing_tags = list(data.get("tags") or [])
                is_existing_user = True
    except Exception:
        pass

    # 2) 태그 로직
    # 구매 태그 추가
    if tag_purchase not in existing_tags:
        existing_tags.append(tag_purchase)
    # 재구매 태그: 기존 채널톡 사용자이거나 is_returning 플래그가 True인 경우
    if (is_returning or is_existing_user) and tag_returning not in existing_tags:
        existing_tags.append(tag_returning)
    # 미수금 태그: 잔금 있으면 추가, 완납이면 제거
    if unpaid_balance > 0:
        if tag_unpaid not in existing_tags:
            existing_tags.append(tag_unpaid)
    else:
        existing_tags = [t for t in existing_tags if t != tag_unpaid]

    # 3) 프로필 + 태그로 PUT
    profile = {
        "name": (customer_name or "").strip() or "고객",
        "mobileNumber": (phone_number or "").strip(),
        "오프라인_최근구매액": int(purchase_amount),
        "오프라인_최근구매일": purchase_date_str[:10],
        "오프라인_구매품목": (item_category or "").strip() or "-",
        "오프라인_누적구매횟수": len([t for t in existing_tags if "구매/" in t or t.endswith("_구매")]),
    }
    body = {"profile": profile, "tags": existing_tags}
    try:
        r_put = requests.put(
            f"{CHANNEL_TALK_BASE_URL}/users/@{member_id}",
            headers=headers,
            json=body,
            timeout=10,
        )
        return 200 <= r_put.status_code < 300
    except Exception:
        return False


def fetch_channel_talk_customer_by_phone_raw(phone_number: str) -> dict | None:
    """
    전화번호(memberId) 기준으로 채널톡 사용자 1명을 조회 (내부 헬퍼, UI 출력 없음).
    성공 시 user dict, 실패/404 시 None.
    """
    headers = _channel_talk_headers()
    if not headers or not phone_number:
        return None
    member_id = re.sub(r"\D", "", str(phone_number).strip())
    if not member_id:
        return None
    try:
        r = requests.get(
            f"{CHANNEL_TALK_BASE_URL}/users/@{member_id}",
            headers=headers,
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            return data if isinstance(data, dict) else None
        return None
    except Exception:
        return None


def fetch_channel_talk_customer_by_phone(phone_number: str) -> dict | None:
    """
    전화번호(=memberId) 기준으로 채널톡 사용자 1명을 조회 (UI 메시지 포함).
    성공 시 user dict, 실패/404 시 None 반환.
    """
    headers = _channel_talk_headers()
    if not headers:
        st.error("채널톡 API 키가 설정되지 않았습니다. st.secrets 설정을 확인하세요.")
        return None
    if not phone_number or not str(phone_number).strip():
        st.error("조회할 전화번호를 입력하세요.")
        return None
    member_id = re.sub(r"\D", "", str(phone_number).strip())
    if not member_id:
        st.error("전화번호 형식이 올바르지 않습니다.")
        return None
    try:
        r = requests.get(
            f"{CHANNEL_TALK_BASE_URL}/users/@{member_id}",
            headers=headers,
            timeout=10,
        )
        if r.status_code == 404:
            st.info("채널톡에 해당 전화번호로 등록된 고객이 없습니다.")
            return None
        if r.status_code != 200:
            st.error(f"채널톡에서 고객 정보를 가져오지 못했습니다. 상태 코드: {r.status_code}")
            return None
        data = r.json()
        if not isinstance(data, dict):
            st.error("채널톡 응답 형식이 예상과 다릅니다.")
            return None
        return data
    except Exception as e:
        st.error(f"채널톡 고객 단건 조회 중 에러 발생: {e}")
        return None


def _format_number_comma(s):
    """숫자만 추출 후 천 단위 콤마 포맷 (화면 표시용)."""
    if s is None or s == "":
        return ""
    digits = re.sub(r"\D", "", str(s))
    if not digits:
        return ""
    return f"{int(digits):,}"


def _format_signed_number_comma(s):
    """선행 + / - 는 유지하고 숫자 부분만 천 단위 콤마 (증감액 입력용)."""
    if s is None:
        return ""
    raw = str(s).strip()
    if raw == "":
        return ""
    sign = ""
    body = raw
    if body[0] in "+-":
        sign = body[0]
        body = body[1:].strip()
    digits = re.sub(r"\D", "", body)
    if not digits:
        return sign
    return sign + f"{int(digits):,}"


def _parse_comma_to_int(s):
    """콤마 포함 문자열을 정수로 변환 (DB 저장·잔금 계산용)."""
    if s is None or s == "":
        return 0
    return int(re.sub(r"\D", "", str(s)) or 0)


def _fmt_num(x):
    """글로벌 숫자 포맷: 금액/숫자에 천 단위 콤마 적용 (화면 출력용)."""
    if x is None or (isinstance(x, float) and pd.isna(x)):
        return ""
    try:
        v = float(x)
        if v != int(v):
            return f"{v:,.1f}"
        return f"{int(v):,}"
    except (TypeError, ValueError):
        return str(x)


def _format_df_display(df, num_columns):
    """DataFrame의 지정 컬럼을 천 단위 콤마 문자열로 변환한 복사본 반환 (st.dataframe 표시용)."""
    out = df.copy()
    for col in num_columns:
        if col not in out.columns:
            continue
        out[col] = out[col].apply(_fmt_num)
    return out


def _format_phone_hyphen(s):
    """숫자만 입력된 전화번호를 010-1234-5678 형식으로 포맷."""
    if s is None or s == "":
        return ""
    digits = re.sub(r"\D", "", str(s))
    if not digits:
        return ""
    n = len(digits)
    if n <= 3:
        return digits
    if n <= 7:
        return f"{digits[:3]}-{digits[3:]}"
    if digits.startswith("02") and n == 9:
        return f"{digits[:2]}-{digits[2:6]}-{digits[6:]}"
    return f"{digits[:3]}-{digits[3:7]}-{digits[7:11]}"


# 결제 수단·카드사·수수료율 (가구 매장 결제 로직)
PAYMENT_METHOD_OPTIONS = ["신용카드", "메인페이", "체크카드", "지역화폐", "계좌이체", "온누리", "현금(수금)", "온누리지류"]
# 신용카드·체크카드 공용 카드사 목록
CARD_COMPANY_OPTIONS = [
    "신한카드", "KB국민카드", "우리카드", "NH농협카드", "하나카드",
    "카카오뱅크", "토스뱅크", "케이뱅크",
    "삼성카드", "현대카드", "롯데카드", "BC카드", "기타",
]
# 체크카드 선택 시 카드사 표시 대상 수단 (신용카드·체크카드 동일 목록 사용)
_CARD_WITH_COMPANY = ("신용카드", "체크카드")


def _payment_fee_amount(payment_method: str, amount: int) -> float:
    """결제 수단별 수수료: 신용카드·메인페이 2.5%, 체크카드 1.5%, 그 외(현금·이체 등) 0%."""
    if not payment_method or amount <= 0:
        return 0.0
    if payment_method in ("신용카드", "메인페이"):
        return round(float(amount) * 0.025, 0)
    if payment_method == "체크카드":
        return round(float(amount) * 0.015, 0)
    return 0.0


def _compute_net_margin_rate(selling_price: float, cost: float, total_fee: float) -> float:
    """실질 마진율(%) = (판매가 - 원가 - 수수료) / 판매가 * 100. 판매가 0이면 0 반환."""
    if not selling_price or selling_price <= 0:
        return 0.0
    net_profit = selling_price - cost - total_fee
    return round((net_profit / selling_price) * 100.0, 1)


def _kpi_employee_names_cell_is_blank(val: object) -> bool:
    """sales/주문의 employee_names 셀이 비었거나 NaN·문자열 'nan' 등인지 (KPI 집계 시 결측으로 취급)."""
    if val is None:
        return True
    if isinstance(val, float) and pd.isna(val):
        return True
    if pd.isna(val):
        return True
    s = str(val).strip()
    if not s:
        return True
    if s.lower() in ("nan", "none", "null", "<na>", "nat"):
        return True
    return False


def _kpi_parse_employee_list(raw: object) -> list[str]:
    """employee_names 필드를 콤마 분리 직원 목록으로 파싱. NaN·'nan' 문자열 등은 빈 목록."""
    if _kpi_employee_names_cell_is_blank(raw):
        return []
    parts = [p.strip() for p in str(raw).split(",") if p.strip()]
    return [p for p in parts if p.lower() not in ("nan", "none", "null", "<na>")]


def _kpi_sanitize_employee_label(raw: object) -> str:
    """주문 employee_names에서 KPI용 단일 문자열(보완용). 비었으면 ''."""
    emps = _kpi_parse_employee_list(raw)
    return ",".join(emps) if emps else ""


def _fetch_order_employee_names_map_by_ids(db_filename: str, order_ids: list) -> dict[int, str]:
    """order_id → 주문 테이블의 최신 employee_names (Supabase app_orders 또는 SQLite Orders)."""
    out: dict[int, str] = {}
    if not db_filename or not order_ids:
        return out
    try:
        oids = sorted({int(x) for x in order_ids})
    except (TypeError, ValueError):
        return out
    if not oids:
        return out
    if _supabase_orders_payments_available():
        sc, err = get_supabase_client()
        if err or not sc:
            return out
        try:
            for chunk in (oids[i : i + 100] for i in range(0, len(oids), 100)):
                resp = (
                    sc.table("app_orders")
                    .select("id, employee_names")
                    .eq(ORDERS_PAYMENTS_TENANT_COL, db_filename)
                    .in_("id", chunk)
                    .execute()
                )
                for row in resp.data or []:
                    out[int(row["id"])] = str(row.get("employee_names") or "")
        except Exception:
            return out
        return out
    conn = get_tenant_conn(db_filename)
    if not conn:
        return out
    try:
        for chunk in (oids[i : i + 200] for i in range(0, len(oids), 200)):
            ph = ",".join("?" * len(chunk))
            cur = conn.execute(f"SELECT id, employee_names FROM Orders WHERE id IN ({ph})", chunk)
            for row in cur.fetchall():
                out[int(row[0])] = str(row[1] or "")
    except Exception:
        pass
    finally:
        conn.close()
    return out


def _overlay_sales_df_employee_names_from_live_orders(sal_df: "pd.DataFrame") -> None:
    """in-place: `_db_fn`·`order_id`가 있으면 주문 테이블 최신 담당 직원으로 `employee_names`를 덮어써 1/n·표시가 주문 수정과 일치."""
    if sal_df is None or sal_df.empty:
        return
    if "order_id" not in sal_df.columns or "employee_names" not in sal_df.columns or "_db_fn" not in sal_df.columns:
        return
    sal_df["employee_names"] = sal_df["employee_names"].astype(object)
    for db_fn in sal_df["_db_fn"].dropna().unique():
        db_s = str(db_fn)
        m = sal_df["_db_fn"] == db_fn
        oids = sal_df.loc[m, "order_id"].dropna().astype(int).unique().tolist()
        if not oids:
            continue
        emp_map = _fetch_order_employee_names_map_by_ids(db_s, oids)
        if not emp_map:
            continue

        def _resolve(oid: object, cur: object) -> object:
            if pd.isna(oid):
                return cur
            try:
                oi = int(oid)
            except (TypeError, ValueError):
                return cur
            ov = emp_map.get(oi)
            if ov is not None and str(ov).strip():
                return str(ov).strip()
            return cur

        sub = sal_df.loc[m, ["order_id", "employee_names"]]
        sal_df.loc[m, "employee_names"] = [_resolve(a, b) for a, b in zip(sub["order_id"], sub["employee_names"])]


# ========== 경로 설정 ==========
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE_DIR, "databases")
EMONS_LOG_SVG_PATH = os.path.normpath(os.path.join(BASE_DIR, "emons-log.svg"))
EMONS_LOGO_SVG_PATH = os.path.normpath(os.path.join(BASE_DIR, "emons-logo.svg"))
LOGO_SVG_PATH = os.path.normpath(os.path.join(BASE_DIR, "logo.svg"))
LOGO_PATH = os.path.normpath(os.path.join(BASE_DIR, "logo.png"))
os.makedirs(DB_DIR, exist_ok=True)
RECEIPT_DIR = os.path.join(DB_DIR, "receipts")
os.makedirs(RECEIPT_DIR, exist_ok=True)
# 결제 변경 시 업로드한 영수증 이미지 저장 (프로젝트 최상위 uploads/receipts)
RECEIPTS_UPLOAD_DIR = os.path.join(BASE_DIR, "uploads", "receipts")
os.makedirs(RECEIPTS_UPLOAD_DIR, exist_ok=True)

LOGO_FALLBACK_MSG = "momo 로고를 불러올 수 없습니다. (경로 확인 필요)"

# [DB 규칙] Customers, Sales, Orders 등 PK가 id(자동증가)인 테이블:
# - INSERT 시 id 컬럼은 절대 지정하지 않음 (DB 자동 생성).
# - 단일 행 조회/수정 시 항상 id 기준: WHERE id = ? / UPDATE ... WHERE id = ?


def _resolve_logo_path():
    """로고 경로 반환. emons-log.svg → emons-logo.svg → logo.svg → logo.png. app 기준·실행 폴더 순."""
    for _name, path in [
        ("emons-log.svg", EMONS_LOG_SVG_PATH),
        ("emons-log.svg", os.path.normpath(os.path.join(os.getcwd(), "emons-log.svg"))),
        ("emons-logo.svg", EMONS_LOGO_SVG_PATH),
        ("emons-logo.svg", os.path.normpath(os.path.join(os.getcwd(), "emons-logo.svg"))),
        ("logo.svg", LOGO_SVG_PATH),
        ("logo.svg", os.path.normpath(os.path.join(os.getcwd(), "logo.svg"))),
        ("logo.png", LOGO_PATH),
        ("logo.png", os.path.normpath(os.path.join(os.getcwd(), "logo.png"))),
    ]:
        if os.path.exists(path):
            return path
    return None


def _common_logo_html(
    logo_path: str | None,
    fallback_id: str = "emons-logo-fallback",
    href: str | None = None,
) -> str:
    """
    로고 공통 블록: 좌측 상단 고정, onError 시 빨간 에러 메시지 표시.
    login / sidebar 동일하게 사용. logo_path가 None이면 이미지 없이 에러 메시지만 노출.
    """
    container_style = (
        "display:flex; justify-content:flex-start; align-items:center; "
        "padding:0.75rem 0.75rem 0.5rem 0.75rem; box-sizing:border-box; min-height:2.5rem;"
    )
    fallback_span = (
        f'<span id="{html.escape(fallback_id)}" style="display:none; color:#c00; font-size:0.8rem; font-weight:600;">'
        f"{html.escape(LOGO_FALLBACK_MSG)}</span>"
    )
    if not logo_path:
        # 로고 파일이 없을 때도 홈으로 이동시키고 싶다면 span을 링크로 감쌀 수 있지만,
        # 여기서는 단순 에러 메시지만 노출한다.
        return (
            f'<div style="{container_style}">'
            f'<span style="color:#c00; font-size:0.8rem; font-weight:600;">{html.escape(LOGO_FALLBACK_MSG)}</span>'
            "</div>"
        )
    try:
        with open(logo_path, "rb") as f:
            raw = f.read()
    except Exception:
        return (
            f'<div style="{container_style}">'
            f'<span style="color:#c00; font-size:0.8rem; font-weight:600;">{html.escape(LOGO_FALLBACK_MSG)}</span>'
            "</div>"
        )
    ext = os.path.splitext(logo_path)[1].lower()
    mime = "image/svg+xml" if ext == ".svg" else "image/png"
    b64 = base64.b64encode(raw).decode("utf-8")
    src = f"data:{mime};base64,{b64}"
    onerror_js = (
        f"this.onerror=null; this.style.display='none';"
        f"var e=document.getElementById('{html.escape(fallback_id)}');"
        "if(e) e.style.display='inline';"
    )
    img_html = (
        f'<img src="{html.escape(src)}" alt="Logo" '
        'style="width:100%; max-width:140px; height:auto; object-fit:contain; display:block;" '
        f'onerror="{html.escape(onerror_js)}" />'
        f"{fallback_span}"
    )
    if href:
        inner = (
            f'<a href="{html.escape(href)}" '
            'style="display:inline-block; text-decoration:none;">'
            f"{img_html}"
            "</a>"
        )
    else:
        inner = img_html
    return f'<div style="{container_style}">{inner}</div>'


MASTER_DB_PATH = os.path.join(DB_DIR, "master_system.db")


# ========== Master DB 초기화 및 Connection Management ==========

def get_master_conn():
    """Master DB 연결. 인증·매장 목록 등 시스템 정보용."""
    return sqlite3.connect(MASTER_DB_PATH)


def migrate_stores_to_supabase():
    """
    [일회성] 로컬 Master DB의 Stores 테이블 데이터를 Supabase app_stores로 복사합니다.
    수동으로 한 번만 실행하면 됩니다. 동일 store_name이 있으면 db_filename만 갱신(upsert).
    반환: (성공 개수, None) 또는 (0, "에러 메시지")
    """
    conn = get_master_conn()
    try:
        rows = conn.execute("SELECT id, store_name, db_filename FROM Stores ORDER BY id").fetchall()
    finally:
        conn.close()
    if not rows:
        return 0, None
    client, err = get_supabase_client()
    if err or not client:
        return 0, (err or "Supabase 연결 실패")
    count = 0
    for row in rows:
        _id, store_name, db_filename = row[0], row[1], row[2]
        if not store_name or not db_filename:
            continue
        try:
            client.table("app_stores").upsert(
                [{"store_name": store_name.strip(), "db_filename": db_filename.strip()}],
                on_conflict="store_name",
            ).execute()
            count += 1
        except Exception as e:
            try:
                client.table("app_stores").insert({
                    "store_name": store_name.strip(),
                    "db_filename": db_filename.strip(),
                }).execute()
                count += 1
            except Exception as e2:
                return count, f"매장 '{store_name}' 반영 실패: {str(e2)}"
    return count, None


def init_master_db():
    """
    앱 실행 시 master_system.db가 없으면 생성하고,
    Stores, Users 테이블을 만들고 최고 관리자(superadmin / 1234)를 삽입.
    """
    if os.path.exists(MASTER_DB_PATH):
        return
    conn = get_master_conn()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS Stores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                store_name TEXT NOT NULL UNIQUE,
                db_filename TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS Users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('superadmin', 'store_admin', 'user')),
                store_id INTEGER REFERENCES Stores(id),
                UNIQUE(username)
            );
            CREATE TABLE IF NOT EXISTS Notices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                content TEXT,
                external_link TEXT,
                message TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS AdminAlerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                store_name TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                message TEXT NOT NULL,
                seen INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS ChannelTalkWebhookLog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                store_key TEXT,
                phone TEXT,
                name TEXT,
                status TEXT NOT NULL,
                message TEXT,
                db_filename TEXT,
                customer_id INTEGER
            );
        """)
        # 비밀번호 1234를 해시하여 저장 (단방향)
        pw_hash = hashlib.sha256("1234".encode()).hexdigest()
        conn.execute(
            "INSERT INTO Users (username, password, role, store_id) VALUES (?, ?, 'superadmin', NULL)",
            ("superadmin", pw_hash)
        )
        conn.commit()
    finally:
        conn.close()


def ensure_master_schema(conn: sqlite3.Connection):
    """기존 master_system.db에 Notices 등 누락된 테이블이 있으면 추가.
    Notices: id, title, content, external_link, message(legacy), is_active, created_at"""
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='Notices'")
    if cur.fetchone() is None:
        conn.execute("""
            CREATE TABLE Notices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                content TEXT,
                external_link TEXT,
                message TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            )
        """)
        conn.commit()
    else:
        cur2 = conn.execute("PRAGMA table_info(Notices)")
        cols = [row[1] for row in cur2.fetchall()]
        if "title" not in cols:
            conn.execute("ALTER TABLE Notices ADD COLUMN title TEXT")
        if "content" not in cols:
            conn.execute("ALTER TABLE Notices ADD COLUMN content TEXT")
        if "external_link" not in cols:
            conn.execute("ALTER TABLE Notices ADD COLUMN external_link TEXT")
        conn.commit()
    # Superadmin/매장관리자 알림 로그 (마진율 이상 등)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='AdminAlerts'")
    if cur.fetchone() is None:
        conn.execute("""
            CREATE TABLE AdminAlerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                store_name TEXT NOT NULL,
                alert_type TEXT NOT NULL,
                message TEXT NOT NULL,
                seen INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.commit()
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='ChannelTalkWebhookLog'")
    if cur.fetchone() is None:
        conn.execute("""
            CREATE TABLE ChannelTalkWebhookLog (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                store_key TEXT,
                phone TEXT,
                name TEXT,
                status TEXT NOT NULL,
                message TEXT,
                db_filename TEXT,
                customer_id INTEGER
            )
        """)
        conn.commit()
    # Supabase Auth 연동: Users 테이블에 email, name 컬럼 추가
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='Users'")
    if cur.fetchone() is not None:
        cur2 = conn.execute("PRAGMA table_info(Users)")
        cols = [row[1] for row in cur2.fetchall()]
        if "email" not in cols:
            conn.execute("ALTER TABLE Users ADD COLUMN email TEXT")
            conn.commit()
        if "name" not in cols:
            conn.execute("ALTER TABLE Users ADD COLUMN name TEXT")
            conn.commit()
    # 한 직원이 여러 매장 접근: UserStores (user_id, store_id) 다대다
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='UserStores'")
    if cur.fetchone() is None:
        conn.execute("""
            CREATE TABLE UserStores (
                user_id INTEGER NOT NULL REFERENCES Users(id) ON DELETE CASCADE,
                store_id INTEGER NOT NULL REFERENCES Stores(id) ON DELETE CASCADE,
                PRIMARY KEY (user_id, store_id)
            )
        """)
        conn.commit()
        # 기존 Users.store_id를 UserStores로 이전
        conn.execute("""
            INSERT OR IGNORE INTO UserStores (user_id, store_id)
            SELECT id, store_id FROM Users WHERE store_id IS NOT NULL
        """)
        conn.commit()


def _insert_admin_alert(store_name: str, alert_type: str, message: str):
    """Superadmin/매장관리자 알림: Master DB의 AdminAlerts에 기록."""
    try:
        conn = get_master_conn()
        conn.execute(
            "INSERT INTO AdminAlerts (created_at, store_name, alert_type, message, seen) VALUES (?, ?, ?, ?, 0)",
            (datetime.now(tz=KST).strftime("%Y-%m-%d %H:%M:%S"), store_name, alert_type, message)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


@st.cache_data(ttl=3600)
def _get_store_name_by_db(db_filename: str) -> str:
    """db_filename으로 Supabase app_stores에서 store_name 조회. @st.cache_data 1시간 캐시."""
    if not db_filename:
        return "알 수 없음"
    client, err = get_supabase_client()
    if err or not client:
        return db_filename or "알 수 없음"
    try:
        r = client.table("app_stores").select("store_name").eq("db_filename", db_filename).maybe_single().execute()
        data = r.data if isinstance(r.data, dict) else (r.data[0] if r.data and len(r.data) else None)
        return (data.get("store_name") or db_filename) if data else (db_filename or "알 수 없음")
    except Exception:
        return db_filename or "알 수 없음"


def get_store_assigned_employee_names(db_filename: str) -> list[str]:
    """
    해당 매장(db_filename)에 배정된 직원(로그인 계정)의 표시명 목록.
    Supabase app_stores 단일 데이터 소스 — app_users/app_user_stores만 조회.
    반환: [표시명, ...] (name 있으면 name, 없으면 username)
    """
    if not db_filename:
        return []
    if not _supabase_app_tables_available():
        return []
    return _get_supabase_store_assigned_employee_names(db_filename)


def _get_store_tag_key(store_name: str) -> str:
    """
    채널톡 태그용 매장 키 추출. 예: '울산삼산점' -> '삼산', '학성점' -> '학성'.
    st.secrets에 CHANNEL_TALK_STORE_TAG_KEYS = "삼산,학성,평산" 형태로 매장명 포함 시 사용할 키 목록 지정 가능.
    """
    if not store_name or not str(store_name).strip():
        return "기타"
    name = str(store_name).strip()
    try:
        raw = st.secrets.get("CHANNEL_TALK_STORE_TAG_KEYS") or st.secrets.get("channel_talk", {}).get("CHANNEL_TALK_STORE_TAG_KEYS")
        if raw:
            for key in [k.strip() for k in str(raw).split(",") if k.strip()]:
                if key in name:
                    return key
    except Exception:
        pass
    if "삼산" in name:
        return "삼산"
    if "학성" in name:
        return "학성"
    return name.replace("점", "").strip() or "기타"


def get_tenant_conn(db_filename: str):
    """
    [Connection Management] 매장(테넌트) 전용 DB 파일에 연결.
    디스크 I/O 병목을 막기 위해 _ensure_tenant_schema는 세션당 최초 1회만 실행하도록 캐싱 처리함.
    """
    if not db_filename:
        return None
    path = os.path.join(DB_DIR, db_filename)
    if not os.path.exists(path):
        return None
    conn = sqlite3.connect(path)

    # 세션 상태를 이용한 스키마 검사 캐싱 (반복 DDL 실행 원천 차단)
    cache_key = f"_schema_checked_{db_filename}"
    if cache_key not in st.session_state:
        try:
            _ensure_tenant_schema(conn)
            st.session_state[cache_key] = True
        except Exception:
            pass
    return conn


@st.cache_data(ttl=3600)
def load_customers_cached(db_filename: str, limit: int | None = 50, col_list: str | None = None) -> pd.DataFrame:
    """고객 목록 캐시 로딩 (1시간 TTL). Supabase app_customers 테이블, store_name(매장) 기준 조회.
    col_list: 쉼표 구분 컬럼명 문자열. None이면 기본 전체 컬럼(latitude/longitude 포함 시도).
    최소 컬럼만 필요한 경우 col_list='id, name, phone1' 등으로 전달해 네트워크 비용 절감."""
    client, err = get_supabase_client()
    if err:
        if "supabase_error" not in st.session_state:
            st.session_state["supabase_error"] = err
        return pd.DataFrame()
    store_name = _get_current_store_name_for_customers(db_filename)
    if not store_name:
        return pd.DataFrame()
    try:
        r = None
        if col_list:
            # 지정 컬럼만 조회 (네트워크 절감)
            q = client.table("app_customers").select(col_list).eq("store_name", store_name).order("id", desc=True)
            if limit:
                q = q.limit(limit)
            r = q.execute()
        else:
            # latitude, longitude가 app_customers에 있으면 지도 렌더링 시 활용. 없으면 기본 컬럼만 사용.
            base_cols = "id, name, phone1, phone2, address"
            for select_cols in (f"{base_cols}, latitude, longitude", base_cols):
                try:
                    q = client.table("app_customers").select(select_cols).eq("store_name", store_name).order("id", desc=True)
                    if limit:
                        q = q.limit(limit)
                    r = q.execute()
                    break
                except Exception:
                    continue
        if r and r.data and len(r.data) > 0:
            st.session_state.pop("supabase_error", None)
            return pd.DataFrame(r.data)
        return pd.DataFrame()
    except Exception as e:
        detail = getattr(e, "details", None) or getattr(e, "message", None) or str(e)
        if "supabase_error" not in st.session_state:
            st.session_state["supabase_error"] = detail
        return pd.DataFrame()


@st.cache_data(ttl=1800)
def load_sales_cached(db_filename: str, limit: int | None = None) -> pd.DataFrame:
    """Sales 테이블 캐시 로딩 (ttl=30분). Supabase sales 테이블, id 기준. limit=None이면 전체(대시보드 집계용).
    employee_names, order_id 포함 조회 - 대시보드 직원별 집계에 활용."""
    client, err = get_supabase_client()
    if err:
        if "supabase_error" not in st.session_state:
            st.session_state["supabase_error"] = err
        return pd.DataFrame(columns=["transaction_date", "amount", "order_id", "employee_names", "note"])
    try:
        q = client.table("sales").select("transaction_date, amount, order_id, employee_names, note")
        tenant_col = _sales_tenant_column()
        if tenant_col:
            q = q.eq(tenant_col, db_filename)
        q = q.order("id", desc=True)
        if limit:
            q = q.limit(limit)
        r = q.execute()
        if r.data and len(r.data) > 0:
            st.session_state.pop("supabase_error", None)
            return _filter_sales_to_store_orders(db_filename, pd.DataFrame(r.data))
        return pd.DataFrame(columns=["transaction_date", "amount", "order_id", "employee_names", "note"])
    except Exception as e:
        # employee_names 컬럼이 없는 구 스키마면 기본 컬럼만 조회
        try:
            q2 = client.table("sales").select("transaction_date, amount, order_id")
            tenant_col = _sales_tenant_column()
            if tenant_col:
                q2 = q2.eq(tenant_col, db_filename)
            q2 = q2.order("id", desc=True)
            if limit:
                q2 = q2.limit(limit)
            r2 = q2.execute()
            if r2.data and len(r2.data) > 0:
                df2 = pd.DataFrame(r2.data)
                df2["employee_names"] = None
                if "note" not in df2.columns:
                    df2["note"] = None
                return _filter_sales_to_store_orders(db_filename, df2)
        except Exception:
            pass
        if "supabase_error" not in st.session_state:
            st.session_state["supabase_error"] = str(e)
        return pd.DataFrame(columns=["transaction_date", "amount", "order_id", "employee_names", "note"])


@st.cache_data(ttl=600)
def _get_store_order_ids_cached(db_filename: str) -> list[int]:
    """매장별 app_orders id 목록 캐시. sales 테넌트 컬럼 미설정 시 2차 격리 필터에 사용."""
    if not db_filename or not _supabase_orders_payments_available():
        return []
    client, err = get_supabase_client()
    if err or not client:
        return []
    try:
        r = client.table("app_orders").select("id").eq(ORDERS_PAYMENTS_TENANT_COL, db_filename).execute()
        return [int(x["id"]) for x in (r.data or []) if x.get("id") is not None]
    except Exception:
        return []


def _filter_sales_to_store_orders(db_filename: str, sales_df: pd.DataFrame) -> pd.DataFrame:
    """
    sales 테이블 매장 격리 보정.
    - sales_tenant_column이 있으면 테넌트 필터가 이미 적용되므로 그대로 반환.
    - 없으면 app_orders(db_filename) 기준 order_id 교집합만 유지.
    """
    if sales_df.empty:
        return sales_df
    if _sales_tenant_column():
        return sales_df
    if "order_id" not in sales_df.columns:
        return sales_df.iloc[0:0].copy()

    valid_order_ids = _get_store_order_ids_cached(db_filename)
    if not valid_order_ids:
        return sales_df.iloc[0:0].copy()

    _oid = pd.to_numeric(sales_df["order_id"], errors="coerce")
    filtered = sales_df[_oid.isin(valid_order_ids)].copy()
    return filtered


@st.cache_data(ttl=600)
def load_sales_with_employees_cached(db_filename: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
    """sales 테이블에서 employee_names 포함 조회 (직원별 판매 실적 보고서용). transaction_date 기준 필터."""
    client, err = get_supabase_client()
    if err:
        return pd.DataFrame(columns=["transaction_date", "amount", "order_id", "note", "employee_names"])
    try:
        q = client.table("sales").select("transaction_date, amount, order_id, note, employee_names")
        tenant_col = _sales_tenant_column()
        if tenant_col:
            q = q.eq(tenant_col, db_filename)
        if start_date:
            q = q.gte("transaction_date", start_date)
        if end_date:
            q = q.lte("transaction_date", end_date)
        q = q.order("transaction_date", desc=False)
        r = q.execute()
        if r.data:
            return _filter_sales_to_store_orders(db_filename, pd.DataFrame(r.data))
        return pd.DataFrame(columns=["transaction_date", "amount", "order_id", "note", "employee_names"])
    except Exception as e:
        if "supabase_error" not in st.session_state:
            st.session_state["supabase_error"] = str(e)
        return pd.DataFrame(columns=["transaction_date", "amount", "order_id", "note", "employee_names"])


@st.cache_data(ttl=1800)
def load_orders_cached(db_filename: str, order_col_list: str, limit: int | None = 50, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
    if _supabase_orders_payments_available():
        return _load_orders_supabase(db_filename, columns=order_col_list, limit=limit, start_date=start_date, end_date=end_date)
    conn = get_tenant_conn(db_filename)
    if not conn:
        return pd.DataFrame()
    try:
        query = f"SELECT {order_col_list} FROM Orders WHERE 1=1"
        params = []
        if start_date:
            query += " AND order_date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND order_date <= ?"
            params.append(end_date)
        query += " ORDER BY id DESC"
        if limit:
            query += " LIMIT ?"
            params.append(limit)
        return pd.read_sql(query, conn, params=params)
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


@st.cache_data(ttl=1800)
def load_payments_cached(db_filename: str) -> pd.DataFrame:
    """결제(Payments) 목록 캐시 로딩 (30분). Supabase app_payments 우선. 저장 후 clear_data_cache() 호출 시 갱신."""
    if _supabase_orders_payments_available():
        return _load_payments_supabase(db_filename)
    conn = get_tenant_conn(db_filename)
    if not conn:
        return pd.DataFrame()
    try:
        return pd.read_sql(
            "SELECT order_id, amount, fee_amount, payment_date, payment_method, onnuri_approval_code FROM Payments",
            conn,
        )
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def _kpi_receipt_method_label_series(df: "pd.DataFrame") -> "pd.Series":
    """대시보드 수납액 수단별 집계용 라벨. 온누리·온누리지류 등은 '온누리'로 합치고, 수단 공란+온누리 승인번호가 있으면 온누리로 분류."""
    idx = df.index
    n = len(df)
    if "payment_method" in df.columns:
        pm = df["payment_method"].fillna("").astype(str).str.strip()
        pm = pm.replace({"nan": "", "None": "", "<NA>": "", "NaT": ""})
    else:
        pm = pd.Series([""] * n, index=idx, dtype=str)

    def _fold_onnuri_label(m: object) -> str:
        s = str(m).strip() if m is not None else ""
        if not s or s.lower() in ("nan", "none", "<na>"):
            return ""
        if "온누리" in s:
            return "온누리"
        return s

    out = pm.map(_fold_onnuri_label)
    has_onn = pd.Series(False, index=idx)
    if "onnuri_approval_code" in df.columns:
        oc = df["onnuri_approval_code"].fillna("").astype(str).str.strip()
        has_onn = oc.ne("") & ~oc.str.lower().isin(("nan", "none", "nat"))
    out = out.mask((out == "") & has_onn, "온누리")
    out = out.mask(out == "", "미지정")
    return out


def _payment_method_in_kpi_receipt_bucket(meth: object) -> bool:
    """KPI '현금수금집계' 점수용: _payment_fee_amount 기준 수수료 0%인 수단만 포함. 신용·메인페이·체크 제외, 그 외 미등록 수단도 제외."""
    s = str(meth or "").strip()
    if not s:
        return False
    if "신용카드" in s:
        return False
    if "메인페이" in s:
        return False
    if s == "체크카드" or ("체크" in s and "카드" in s):
        return False
    if "지역화폐" in s:
        return True
    if "온누리" in s:
        return True
    if "이체" in s:
        return True
    if "현금" in s:
        return True
    return False


def _aggregate_cash_collected_by_employee(
    db_filename: str,
    range_start: date,
    range_end: date,
    orders_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    직원 평가용: payment_date가 [range_start, range_end]이고 결제수단이 KPI 현금수금집계 구간
    (_payment_method_in_kpi_receipt_bucket: 수수료 0%만·신용·메인페이·체크 제외)인 결제액만 합산.
    order_id → 해당 주문 employee_names 1/n 배분 후 직원별 합계. (저장/스키마/결제 코어 미변경, 조회·집계만)
    """
    if not db_filename or orders_df is None or orders_df.empty or "id" not in orders_df.columns:
        return pd.DataFrame(columns=["employee", "cash_sales"])
    oid_emp: dict[int, str] = {}
    for _, orow in orders_df.iterrows():
        try:
            oid_emp[int(orow["id"])] = _kpi_sanitize_employee_label(orow.get("employee_names"))
        except (TypeError, ValueError, KeyError):
            continue
    if _supabase_orders_payments_available():
        pay_df = _load_payments_supabase(db_filename)
    else:
        conn = get_tenant_conn(db_filename)
        if not conn:
            return pd.DataFrame(columns=["employee", "cash_sales"])
        try:
            pay_df = pd.read_sql(
                "SELECT order_id, payment_date, payment_method, amount FROM Payments "
                "WHERE payment_date IS NOT NULL AND payment_date != ''",
                conn,
            )
        except Exception:
            pay_df = pd.DataFrame()
        finally:
            conn.close()
    if pay_df is None or pay_df.empty:
        return pd.DataFrame(columns=["employee", "cash_sales"])
    if "payment_method" not in pay_df.columns:
        pay_df = pay_df.copy()
        pay_df["payment_method"] = ""
    pay_df = pay_df.copy()
    pay_df["payment_date"] = pd.to_datetime(pay_df["payment_date"], errors="coerce")
    pay_df = pay_df[pay_df["payment_date"].notna()]
    pay_df["_pd"] = pay_df["payment_date"].dt.date
    pay_df = pay_df[(pay_df["_pd"] >= range_start) & (pay_df["_pd"] <= range_end)]
    pay_df["amount"] = pd.to_numeric(pay_df["amount"], errors="coerce").fillna(0)
    pay_df = pay_df[pay_df["amount"] != 0]
    pay_df = pay_df[pay_df["payment_method"].apply(_payment_method_in_kpi_receipt_bucket)]
    rows: list[dict] = []
    for _, pr in pay_df.iterrows():
        oid = pr.get("order_id")
        if pd.isna(oid):
            continue
        try:
            oid_i = int(oid)
        except (TypeError, ValueError):
            continue
        en = oid_emp.get(oid_i, "")
        emps = _kpi_parse_employee_list(en)
        if not emps:
            continue
        amt = float(pr["amount"])
        n = len(emps)
        per = amt / n
        for e in emps:
            rows.append({"employee": e, "cash_sales": per})
    if not rows:
        return pd.DataFrame(columns=["employee", "cash_sales"])
    return pd.DataFrame(rows).groupby("employee", as_index=False)["cash_sales"].sum()


def _utc_z_bounds_for_kst_range(month_start: date, today: date) -> tuple[str, str]:
    """Supabase timestamptz 비교용 UTC 'Z' 문자열 (KST 자정~말)."""
    start_kst = datetime.combine(month_start, dt_time.min, tzinfo=KST)
    end_kst = datetime.combine(today, dt_time.max, tzinfo=KST)
    su = start_kst.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    eu = end_kst.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    return su, eu


@st.cache_data(ttl=300)
def load_payment_history_dashboard_cached(
    db_filename: str, month_start: date, today: date
) -> pd.DataFrame:
    """대시보드 집계용 app_payment_history(또는 SQLite PaymentHistory). changed_at은 KST 당월~오늘."""
    if not db_filename:
        return pd.DataFrame()
    range_start_iso = datetime.combine(month_start, dt_time.min, tzinfo=KST).isoformat()
    range_end_iso = datetime.combine(today, dt_time.max, tzinfo=KST).isoformat()
    if _supabase_orders_payments_available():
        try:
            sc, err = get_supabase_client()
            if err or not sc:
                return pd.DataFrame()
            zu, zv = _utc_z_bounds_for_kst_range(month_start, today)
            r = (
                sc.table("app_payment_history")
                .select("action_type, old_payment_data, new_payment_data, changed_at")
                .eq("db_filename", db_filename)
                .gte("changed_at", zu)
                .lte("changed_at", zv)
                .order("changed_at", desc=True)
                .limit(5000)
                .execute()
            )
            rows = r.data or []
            df = pd.DataFrame(rows) if rows else pd.DataFrame()
            if df.empty:
                # 1차(UTC 경계) 무결과 시: 최근 행만 불러와 KST 날짜로 당월 필터 (API/타임존 이슈 완화)
                r2 = (
                    sc.table("app_payment_history")
                    .select("action_type, old_payment_data, new_payment_data, changed_at")
                    .eq("db_filename", db_filename)
                    .order("changed_at", desc=True)
                    .limit(4000)
                    .execute()
                )
                rows2 = r2.data or []
                df = pd.DataFrame(rows2) if rows2 else pd.DataFrame()
            if df.empty:
                return df
            _ts = pd.to_datetime(df["changed_at"], errors="coerce", utc=True)
            _d = _ts.dt.tz_convert(KST).dt.date
            return df.loc[(_d >= month_start) & (_d <= today)].copy()
        except Exception:
            return pd.DataFrame()
    conn = get_tenant_conn(db_filename)
    if not conn:
        return pd.DataFrame()
    try:
        return pd.read_sql(
            """
            SELECT action_type, old_payment_data, new_payment_data, changed_at
            FROM PaymentHistory
            WHERE changed_at >= ? AND changed_at <= ?
            """,
            conn,
            params=(range_start_iso, range_end_iso),
        )
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def _dashboard_parse_ph_payload(val) -> dict:
    if val is None:
        return {}
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            inner = json.loads(val)
            if isinstance(inner, str):
                inner = json.loads(inner)
            return inner if isinstance(inner, dict) else {}
        except Exception:
            return {}
    return {}


def _ph_blob_amount(blob: dict) -> float:
    """이력 JSON에서 결제 금액 추출 (payment.amount 또는 최상위 amount)."""
    if not isinstance(blob, dict):
        return 0.0
    pay = blob.get("payment")
    if isinstance(pay, dict) and pay.get("amount") is not None:
        try:
            return float(pay["amount"])
        except (TypeError, ValueError):
            pass
    if blob.get("amount") is not None:
        try:
            return float(blob["amount"])
        except (TypeError, ValueError):
            pass
    return 0.0


def _dashboard_cancel_reduce_totals_from_ph(
    ph_df: pd.DataFrame, today: date, month_start: date, month_end: date
) -> dict[str, float]:
    """결제 이력 기준 금일/당월 취소·삭제·감액(결제변경 순감) 합계. 금액은 양수로 반환."""
    out = {
        "today_cancel": 0.0,
        "today_reduce": 0.0,
        "month_cancel": 0.0,
        "month_reduce": 0.0,
    }
    if ph_df is None or ph_df.empty:
        return out
    if "changed_at" not in ph_df.columns or "action_type" not in ph_df.columns:
        return out
    _work = ph_df.copy()
    _evt = pd.to_datetime(_work["changed_at"], errors="coerce", utc=True)
    _work["_evt_date"] = _evt.dt.tz_convert(KST).dt.date
    for _, row in _work.iterrows():
        d = row["_evt_date"]
        if pd.isna(d):
            continue
        at = str(row.get("action_type") or "").strip()
        old = _dashboard_parse_ph_payload(row.get("old_payment_data"))
        new = _dashboard_parse_ph_payload(row.get("new_payment_data"))
        if at in ("결제취소", "결제직접삭제"):
            amt = _ph_blob_amount(old)
            if amt > 0:
                if d == today:
                    out["today_cancel"] += amt
                if month_start <= d <= month_end:
                    out["month_cancel"] += amt
        elif at == "결제변경":
            oa = _ph_blob_amount(old)
            na = _ph_blob_amount(new)
            red = max(0.0, oa - na)
            if red > 0:
                if d == today:
                    out["today_reduce"] += red
                if month_start <= d <= month_end:
                    out["month_reduce"] += red
    return out


@st.cache_data(ttl=600)
def load_todos_cached(db_filename: str, limit: int = 100) -> pd.DataFrame:
    """
    To-Do 목록 캐시 로딩 (ttl=10분).
    Supabase app_todos 테이블에서 tenant_name(매장명) 기준으로 조회한다.
    반환 컬럼: id, created_date(문자열), author, content, is_completed.
    """
    if not db_filename:
        return pd.DataFrame(columns=["id", "created_date", "author", "content", "is_completed"])
    tenant_name = _get_store_name_by_db(db_filename) or db_filename
    client, err = get_supabase_client()
    if err or not client:
        return pd.DataFrame(columns=["id", "created_date", "author", "content", "is_completed"])
    try:
        q = client.table("app_todos").select(
            "id, tenant_name, author, content, is_completed, created_at"
        ).eq("tenant_name", tenant_name).order("created_at", desc=True)
        if limit:
            q = q.limit(limit)
        r = q.execute()
        rows = (r.data or []) if hasattr(r, "data") else []
    except Exception:
        rows = []
    if not rows:
        return pd.DataFrame(columns=["id", "created_date", "author", "content", "is_completed"])
    df = pd.DataFrame(rows)
    # created_at → created_date(문자열) 변환
    if "created_at" in df.columns:
        try:
            created_dt = pd.to_datetime(df["created_at"], errors="coerce")
            df["created_date"] = created_dt.dt.strftime("%Y-%m-%d %H:%M")
        except Exception:
            df["created_date"] = ""
    else:
        df["created_date"] = ""
    # 필요한 컬럼만 반환 (부족한 컬럼은 기본값으로 채움)
    for col in ("author", "content", "is_completed"):
        if col not in df.columns:
            df[col] = "" if col in ("author", "content") else False
    out = df[["id", "created_date", "author", "content", "is_completed"]].copy()
    return out


def _get_todos_for_display(db_filename: str) -> pd.DataFrame:
    """
    To-Do 목록 반환. session_state에 최근 mutate로 갱신된 데이터가 있으면 즉시 반환(DB 재조회 없음).
    없으면 load_todos_cached에서 로드 후 session_state에 저장하여 이후 mutate 시 즉시 반영 가능하게 함.
    """
    if not db_filename:
        return pd.DataFrame(columns=["id", "created_date", "author", "content", "is_completed"])
    if "_todos_local" not in st.session_state:
        st.session_state["_todos_local"] = {}
    local_key = db_filename
    if local_key in st.session_state["_todos_local"]:
        return st.session_state["_todos_local"][local_key].copy()
    df = load_todos_cached(db_filename)
    st.session_state["_todos_local"][local_key] = df.copy()
    return df


def _invalidate_todos_local(db_filename: str):
    """To-Do 로컬 캐시 무효화. 새로고침 시 DB에서 재조회."""
    if "_todos_local" in st.session_state and db_filename in st.session_state["_todos_local"]:
        del st.session_state["_todos_local"][db_filename]


def clear_data_cache():
    """
    전체 데이터 캐시 무효화. 주문/결제/매장/직원 등 저장·수정·삭제 직후 호출하면
    load_orders_cached, get_supabase_stores_dataframe_cached, _get_supabase_stores_list 등이 다음 조회 시 최신값을 가져옴.
    세션 내 가용성 캐시(_supa_orders_avail, _supa_app_tables_avail)도 함께 초기화하여 최신 상태 반영.
    """
    if "_todos_local" in st.session_state:
        st.session_state["_todos_local"] = {}
    # 세션 캐시 가용성 플래그 초기화 (테이블 구조 변경 시 재확인)
    for _k in ("_supa_orders_avail", "_supa_app_tables_avail"):
        st.session_state.pop(_k, None)
    try:
        st.cache_data.clear()
    except Exception:
        pass


def _ensure_tenant_schema(conn: sqlite3.Connection):
    """
    기존 DB와 호환: Payments에 card_company, fee_amount, Orders에 actual_margin 등 누락 컬럼을 ALTER TABLE로 추가하고
    신규 보안/로그 테이블(AuditLogs, EditRequests)을 생성.
    """
    cur = conn.execute("PRAGMA table_info(Payments)")
    cols = [row[1] for row in cur.fetchall()]
    if "card_company" not in cols:
        conn.execute("ALTER TABLE Payments ADD COLUMN card_company TEXT")
    if "fee_amount" not in cols:
        conn.execute("ALTER TABLE Payments ADD COLUMN fee_amount REAL")
    if "onnuri_approval_code" not in cols:
        conn.execute("ALTER TABLE Payments ADD COLUMN onnuri_approval_code TEXT")
    if "created_at" not in cols:
        conn.execute("ALTER TABLE Payments ADD COLUMN created_at TEXT DEFAULT (datetime('now', '+9 hours'))")
    if "created_by" not in cols:
        conn.execute("ALTER TABLE Payments ADD COLUMN created_by TEXT")
    cur = conn.execute("PRAGMA table_info(Orders)")
    cols = [row[1] for row in cur.fetchall()]
    if "actual_margin" not in cols:
        conn.execute("ALTER TABLE Orders ADD COLUMN actual_margin REAL")
    if "delivery_date" not in cols:
        conn.execute("ALTER TABLE Orders ADD COLUMN delivery_date TEXT")
    if "display_sales_amount" not in cols:
        conn.execute("ALTER TABLE Orders ADD COLUMN display_sales_amount INTEGER DEFAULT 0")
    if "display_cost_amount" not in cols:
        conn.execute("ALTER TABLE Orders ADD COLUMN display_cost_amount INTEGER DEFAULT 0")
    # 잔금 상태(완납/미납 등) 표시용
    if "balance_status" not in cols:
        conn.execute("ALTER TABLE Orders ADD COLUMN balance_status TEXT")
    # Customers 가입경로(채널톡 등)용 source 컬럼
    cur = conn.execute("PRAGMA table_info(Customers)")
    cust_cols = [row[1] for row in cur.fetchall()]
    if "source" not in cust_cols:
        conn.execute("ALTER TABLE Customers ADD COLUMN source TEXT")
    # 감사 로그 테이블 (매출·결제·잔금 변경 이력)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='AuditLogs'")
    if cur.fetchone() is None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS AuditLogs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                actor_username TEXT NOT NULL,
                entity_type TEXT NOT NULL,   -- 'Order', 'Payment' 등
                entity_id INTEGER NOT NULL,
                field_name TEXT NOT NULL,    -- 'total_amount', 'payment_total', 'balance_status' 등
                old_value TEXT,
                new_value TEXT,
                reason TEXT NOT NULL
            )
            """
        )
    # 수정 승인 워크플로우용 요청 테이블
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='EditRequests'")
    if cur.fetchone() is None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS EditRequests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                entity_type TEXT NOT NULL,   -- 'Order', 'Payment' 등
                entity_id INTEGER NOT NULL,
                payload TEXT NOT NULL,       -- JSON: 제안된 변경 값들
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', -- 'pending', 'approved', 'rejected'
                reviewed_by TEXT,
                reviewed_at TEXT
            )
            """
        )
    # 결제 영수증 테이블 (파일 경로 관리)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='PaymentReceipts'")
    if cur.fetchone() is None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS PaymentReceipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_id INTEGER NOT NULL REFERENCES Payments(id),
                file_path TEXT NOT NULL,
                original_name TEXT,
                uploaded_by TEXT,
                uploaded_at TEXT NOT NULL
            )
            """
        )
    # 결제 변경 이력 테이블 (PaymentHistory)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='PaymentHistory'")
    if cur.fetchone() is None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS PaymentHistory (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                sale_id INTEGER NOT NULL,
                customer_name TEXT,
                action_type TEXT NOT NULL,
                old_payment_data TEXT,
                new_payment_data TEXT,
                reason TEXT NOT NULL,
                changed_by TEXT NOT NULL,
                changed_at TEXT NOT NULL,
                receipt_image_path TEXT
            )
            """
        )
    # PaymentHistory에 영수증 이미지 경로 컬럼 추가 (기존 DB 호환)
    cur = conn.execute("PRAGMA table_info(PaymentHistory)")
    ph_cols = [row[1] for row in cur.fetchall()]
    if "receipt_image_path" not in ph_cols:
        conn.execute("ALTER TABLE PaymentHistory ADD COLUMN receipt_image_path TEXT")
    # 회계용 매출 원장 (차액 기반 트랜잭션, transaction_date 기준 일/월 매출 집계)
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='Sales'")
    if cur.fetchone() is None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS Sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL REFERENCES Orders(id),
                transaction_date TEXT NOT NULL,
                amount REAL NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL
            )
        """)
    conn.commit()


# ========== Supabase 주문/결제 (app_orders, app_payments) — 매장별 db_filename 필수 ==========
ORDERS_PAYMENTS_TENANT_COL = "db_filename"

# 잔금 상태(balance_status): 완납 / 미납 / 이상결제(결제 합계 > 계약금액, 초과결제 탭과 동일 기준)
BALANCE_STATUS_COMPLETE = "완납"
BALANCE_STATUS_UNPAID = "미납"
BALANCE_STATUS_OVERPAID = "이상결제"


def _balance_status_from_remaining(remaining: float) -> str:
    """remaining = 계약금액 - 결제합계. 0이면 완납, 음수면 초과(이상결제)."""
    if remaining == 0:
        return BALANCE_STATUS_COMPLETE
    if remaining < 0:
        return BALANCE_STATUS_OVERPAID
    return BALANCE_STATUS_UNPAID


def _supabase_orders_payments_available() -> bool:
    """Supabase에 app_orders 테이블이 있는지 확인. 세션당 1회만 HTTP 쿼리 후 결과를 세션 캐시."""
    _cache_key = "_supa_orders_avail"
    if _cache_key in st.session_state:
        return st.session_state[_cache_key]
    client, err = get_supabase_client()
    if err or not client:
        return False
    try:
        client.table("app_orders").select("id").limit(1).execute()
        st.session_state[_cache_key] = True
        return True
    except Exception:
        st.session_state[_cache_key] = False
        return False


def _load_orders_supabase(db_filename: str, columns: str = "*", limit: int | None = None, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
    if not db_filename:
        return pd.DataFrame()
    client, err = get_supabase_client()
    if err or not client:
        return pd.DataFrame()
    try:
        sel = client.table("app_orders").select(columns).eq(ORDERS_PAYMENTS_TENANT_COL, db_filename).order("id", desc=True)
        if start_date:
            sel = sel.gte("order_date", start_date)
        if end_date:
            sel = sel.lte("order_date", end_date)
        if limit:
            sel = sel.limit(limit)
        r = sel.execute()
        rows = (r.data or []) if hasattr(r, "data") else []
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _load_payments_supabase(db_filename: str, order_id: int | None = None) -> pd.DataFrame:
    """app_payments 조회. order_id 지정 시 해당 주문만."""
    if not db_filename:
        return pd.DataFrame()
    client, err = get_supabase_client()
    if err or not client:
        return pd.DataFrame()
    try:
        q = client.table("app_payments").select(
            "id, order_id, payment_date, amount, payment_method, card_company, fee_amount, onnuri_approval_code, created_by"
        ).eq(ORDERS_PAYMENTS_TENANT_COL, db_filename)
        if order_id is not None:
            q = q.eq("order_id", order_id)
        q = q.order("id")
        r = q.execute()
        rows = (r.data or []) if hasattr(r, "data") else []
        return pd.DataFrame(rows) if rows else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _get_current_store_name_for_customers(db_filename: str) -> str:
    """고객 테이블 tenant용 매장명. 세션 current_db → app_stores 조회. 없으면 db_filename 반환."""
    if not db_filename:
        return ""
    return _get_store_name_by_db(db_filename) or db_filename


def _supabase_insert_customer(db_filename: str, name: str, phone1: str, phone2: str | None, address: str | None) -> tuple[int | None, str | None]:
    """
    Supabase app_customers 테이블에 고객 1건 INSERT. store_name(현재 매장) 필수 주입.
    주소가 있으면 카카오 지오코딩으로 latitude/longitude 자동 저장 (KAKAO_REST_KEY 필요).
    반환: (새 customer id, None) 성공 시, (None, 에러_상세_메시지) 실패 시.
    """
    client, err = get_supabase_client()
    if err or not client:
        return None, (err or "Supabase 연결 불가")
    store_name = _get_current_store_name_for_customers(db_filename)
    addr_clean = (address or "").strip() or None
    payload = {
        "store_name": store_name,
        "name": (name or "").strip() or "미입력",
        "phone1": (phone1 or "").strip() or "",
        "phone2": (phone2 or "").strip() or None,
        "address": addr_clean,
    }
    # 주소가 있으면 카카오 지오코딩으로 위도/경도 자동 저장
    if addr_clean:
        try:
            _geo = geocode_address_kakao_extended(addr_clean)
            if _geo:
                payload["latitude"] = _geo["latitude"]
                payload["longitude"] = _geo["longitude"]
        except Exception:
            pass
    try:
        r = client.table("app_customers").insert(payload).execute()
        if r.data and len(r.data) > 0 and r.data[0].get("id") is not None:
            return int(r.data[0]["id"]), None
        return None, "insert 후 id를 받지 못함"
    except Exception as e:
        detail = getattr(e, "details", None) or getattr(e, "message", None) or str(e)
        try:
            if hasattr(e, "body") and isinstance(e.body, dict):
                detail = e.body.get("message") or e.body.get("details") or detail
        except Exception:
            pass
        return None, (detail or str(e))


def _count_orders_on_date(db_filename: str, order_date_str: str) -> int:
    """해당 매장·날짜의 주문 건수. 신규 등록 전 '오늘의 첫 매출' 판별용. Supabase/로컬 모두 지원."""
    if not db_filename or not order_date_str:
        return 0
    if _supabase_orders_payments_available():
        client, err = get_supabase_client()
        if err or not client:
            return 0
        try:
            r = client.table("app_orders").select("id").eq(ORDERS_PAYMENTS_TENANT_COL, db_filename).eq("order_date", order_date_str).execute()
            return len(r.data or [])
        except Exception:
            return 0
    conn = get_tenant_conn(db_filename)
    if not conn:
        return 0
    try:
        row = conn.execute("SELECT COUNT(*) FROM Orders WHERE order_date = ?", (order_date_str,)).fetchone()
        return row[0] or 0
    finally:
        conn.close()


@st.cache_data(ttl=3600)
def _cached_store_aov_30d(db_filename: str) -> float:
    """해당 매장 직전 30일 평균 객단가(주문당 금액). 성과 축하 AOV 비교용. ttl=1시간."""
    if not db_filename:
        return 0.0
    today = _today_kst()
    start = today - timedelta(days=30)
    start_str = start.isoformat()
    end_str = today.isoformat()
    if _supabase_orders_payments_available():
        df = _load_orders_supabase(db_filename, "id, order_date, total_amount", limit=500)
        if df.empty or "order_date" not in df.columns:
            return 0.0
        df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
        df = df.dropna(subset=["order_date"])
        df["_dt"] = df["order_date"].dt.date
        mask = (df["_dt"] >= start) & (df["_dt"] <= today)
        subset = df.loc[mask]
        if len(subset) == 0:
            return 0.0
        return float(subset["total_amount"].fillna(0).mean())
    conn = get_tenant_conn(db_filename)
    if not conn:
        return 0.0
    try:
        df = pd.read_sql(
            "SELECT order_date, total_amount FROM Orders WHERE order_date >= ? AND order_date <= ?",
            conn,
            params=(start_str, end_str),
        )
        if df.empty:
            return 0.0
        return float(df["total_amount"].fillna(0).mean())
    finally:
        conn.close()


@st.cache_data(ttl=3600)
def _cached_employee_monthly_max(db_filename: str, employee_names: str, year: int, month: int) -> float:
    """해당 매장·직원(들)·연월의 직원별 최고 주문 금액(당월). 성과 축하 개인 최고 기록 비교용. ttl=1시간."""
    try:
        if not db_filename or not employee_names or not str(employee_names).strip():
            return 0.0
        month_start = date(year, month, 1)
        from calendar import monthrange
        month_end = date(year, month, monthrange(year, month)[1])
        start_str = month_start.isoformat()
        end_str = month_end.isoformat()
        if _supabase_orders_payments_available():
            df = _load_orders_supabase(db_filename, "id, order_date, total_amount, employee_names", limit=500)
            if df.empty or "employee_names" not in df.columns:
                return 0.0
            df["order_date"] = pd.to_datetime(df["order_date"], errors="coerce")
            df = df.dropna(subset=["order_date"])
            df["_dt"] = df["order_date"].dt.date
            mask = (df["_dt"] >= month_start) & (df["_dt"] <= month_end)
            subset = df.loc[mask]
            names_set = set(n.strip() for n in str(employee_names).split(",") if n.strip())
            max_val = 0.0
            for _, row in subset.iterrows():
                row_names = str(row.get("employee_names") or "").split(",")
                if any(n.strip() in names_set for n in row_names):
                    try:
                        max_val = max(max_val, float(row.get("total_amount") or 0))
                    except (TypeError, ValueError):
                        pass
            return max_val
        conn = get_tenant_conn(db_filename)
        if not conn:
            return 0.0
        try:
            df = pd.read_sql(
                "SELECT order_date, total_amount, employee_names FROM Orders WHERE order_date >= ? AND order_date <= ?",
                conn,
                params=(start_str, end_str),
            )
            if df.empty or "employee_names" not in df.columns:
                return 0.0
            names_set = set(n.strip() for n in str(employee_names).split(",") if n.strip())
            max_val = 0.0
            for _, row in df.iterrows():
                row_names = str(row.get("employee_names") or "").split(",")
                if any(n.strip() in names_set for n in row_names):
                    try:
                        max_val = max(max_val, float(row.get("total_amount") or 0))
                    except (TypeError, ValueError):
                        pass
            return max_val
        finally:
            conn.close()
    except Exception:
        return 0.0


def _insert_order_supabase(db_filename: str, payload: dict) -> int | None:
    """app_orders에 1건 INSERT. payload에 db_filename 없으면 자동 설정. 반환: 새 id 또는 None."""
    if not db_filename:
        return None
    client, err = get_supabase_client()
    if err or not client:
        return None
    payload = dict(payload)
    payload[ORDERS_PAYMENTS_TENANT_COL] = db_filename
    try:
        r = client.table("app_orders").insert(payload).execute()
        if r.data and len(r.data) > 0 and "id" in r.data[0]:
            return int(r.data[0]["id"])
        return None
    except Exception:
        return None


def _update_order_supabase(db_filename: str, order_id: int, updates: dict) -> bool:
    """app_orders 1건 업데이트. db_filename + id로 필터."""
    if not db_filename:
        return False
    client, err = get_supabase_client()
    if err or not client:
        return False
    try:
        client.table("app_orders").update(updates).eq(ORDERS_PAYMENTS_TENANT_COL, db_filename).eq("id", order_id).execute()
        return True
    except Exception:
        return False


def _clean_value_for_supabase_json(val):
    """pandas NaN / float nan 등 JSON으로 보낼 수 없는 값은 None으로 정규화."""
    if val is None:
        return None
    if isinstance(val, float) and val != val:  # NaN
        return None
    try:
        if pd.isna(val):
            return None
    except (ValueError, TypeError):
        pass
    return val


def _insert_payment_supabase(
    db_filename: str,
    payload: dict,
    *,
    _error_detail: list | None = None,
) -> int | None:
    """app_payments에 1건 INSERT. payload에 db_filename 없으면 자동 설정. 반환: 새 id 또는 None."""
    if not db_filename:
        if _error_detail is not None:
            _error_detail.append("db_filename 없음")
        return None
    client, err = get_supabase_client()
    if err or not client:
        if _error_detail is not None:
            _error_detail.append(err or "Supabase 클라이언트를 만들 수 없습니다.")
        return None
    payload = dict(payload)
    for _k, _v in list(payload.items()):
        payload[_k] = _clean_value_for_supabase_json(_v)
    for _tk in ("card_company", "payment_method", "onnuri_approval_code", "created_by"):
        if _tk in payload and payload[_tk] == "":
            payload[_tk] = None
    if payload.get("order_id") is not None:
        try:
            payload["order_id"] = int(payload["order_id"])
        except (TypeError, ValueError):
            if _error_detail is not None:
                _error_detail.append(f"order_id 변환 실패: {payload.get('order_id')!r}")
            return None
    payload[ORDERS_PAYMENTS_TENANT_COL] = db_filename
    try:
        r = client.table("app_payments").insert(payload).execute()
        if r.data and len(r.data) > 0 and "id" in r.data[0]:
            return int(r.data[0]["id"])
        if _error_detail is not None:
            _error_detail.append("INSERT 응답에 id가 없습니다. RLS·스키마·트리거를 확인하세요.")
        return None
    except Exception as e:
        if _error_detail is not None:
            _error_detail.append(str(e) or repr(e))
        return None


def _update_payment_supabase(db_filename: str, payment_id: int, updates: dict) -> bool:
    """app_payments 1건 업데이트."""
    if not db_filename:
        return False
    client, err = get_supabase_client()
    if err or not client:
        return False
    try:
        client.table("app_payments").update(updates).eq(ORDERS_PAYMENTS_TENANT_COL, db_filename).eq("id", payment_id).execute()
        return True
    except Exception:
        return False


def _delete_payment_supabase(db_filename: str, payment_id: int) -> bool:
    """app_payments 1건 삭제."""
    if not db_filename:
        return False
    client, err = get_supabase_client()
    if err or not client:
        return False
    try:
        client.table("app_payments").delete().eq(ORDERS_PAYMENTS_TENANT_COL, db_filename).eq("id", payment_id).execute()
        return True
    except Exception:
        return False


def _get_order_supabase(db_filename: str, order_id: int) -> dict | None:
    """app_orders 단일 행 조회. 없으면 None."""
    if not db_filename:
        return None
    client, err = get_supabase_client()
    if err or not client:
        return None
    try:
        r = client.table("app_orders").select("*").eq(ORDERS_PAYMENTS_TENANT_COL, db_filename).eq("id", order_id).maybe_single().execute()
        return r.data if r.data else None
    except Exception:
        return None


def _get_order_customer_id_supabase(db_filename: str, order_id: int) -> int | None:
    """주문의 customer_id만 조회."""
    row = _get_order_supabase(db_filename, order_id)
    return int(row["customer_id"]) if row and row.get("customer_id") is not None else None


def _sum_payments_by_order_supabase(db_filename: str, order_id: int) -> tuple[float, float]:
    """해당 주문의 결제 합계(amount), 수수료 합계(fee_amount). (paid_total, total_fees)."""
    df = _load_payments_supabase(db_filename, order_id=order_id)
    if df.empty:
        return 0.0, 0.0
    paid = float(df["amount"].sum()) if "amount" in df.columns else 0.0
    fees = float(df["fee_amount"].fillna(0).sum()) if "fee_amount" in df.columns else 0.0
    return paid, fees


def _recalc_order_actual_margin_supabase(db_filename: str, order_id: int) -> bool:
    """주문의 actual_margin, balance_status를 결제 기준으로 재계산하여 app_orders에 반영."""
    order = _get_order_supabase(db_filename, order_id)
    if not order:
        return False
    total_amt = float(order.get("total_amount") or 0)
    cost_general = float(order.get("cost_price") or 0)
    cost_display = float(order.get("display_cost_amount") or 0)
    paid, total_fees = _sum_payments_by_order_supabase(db_filename, order_id)
    basic_m = total_amt - (cost_general + cost_display)
    actual_margin = basic_m - total_fees
    remaining = total_amt - paid
    balance_status = _balance_status_from_remaining(remaining)
    return _update_order_supabase(db_filename, order_id, {"actual_margin": actual_margin, "balance_status": balance_status})


def _count_payments_onnuri_dup_supabase(db_filename: str, payment_date: str, onnuri_last4: str) -> int:
    """동일 결제일 + 온누리 승인번호 뒤 4자리 조합 개수 (중복 검증용)."""
    if not db_filename or len(onnuri_last4) != 4:
        return 0
    client, err = get_supabase_client()
    if err or not client:
        return 0
    try:
        r = client.table("app_payments").select("onnuri_approval_code, payment_method").eq(ORDERS_PAYMENTS_TENANT_COL, db_filename).eq("payment_date", payment_date).execute()
        rows = (r.data or []) if hasattr(r, "data") else []
        return sum(1 for row in rows if "온누리" in str(row.get("payment_method") or "") and (str(row.get("onnuri_approval_code") or "")[-4:]) == onnuri_last4)
    except Exception:
        return 0


def _insert_sales_transaction(db_filename: str, order_id: int, transaction_date: str, amount: float, note: str = "", unpaid_balance: float | None = None, employee_names: str | None = None):
    """Sales 테이블에 매출 트랜잭션 1건 INSERT (Supabase). order_id, amount, transaction_date, note, employee_names, created_at 저장.
    employee_names: 쉼표 구분 직원명 (1/n 분배 기준). unpaid_balance(미수금)는 sales.unpaid_balance 컬럼에 저장(없으면 제외 후 재시도).
    employee_names는 sales.employee_names 컬럼에 저장(없으면 제외 후 재시도)."""
    client, err = get_supabase_client()
    if err:
        if "supabase_error" not in st.session_state:
            st.session_state["supabase_error"] = err
        return
    try:
        payload = {
            "order_id": order_id,
            "transaction_date": transaction_date,
            "amount": amount,
            "note": note or "",
            "created_at": datetime.now(tz=KST).strftime("%Y-%m-%d %H:%M:%S"),
        }
        tenant_col = _sales_tenant_column()
        if tenant_col:
            payload[tenant_col] = db_filename
        if unpaid_balance is not None:
            payload["unpaid_balance"] = round(float(unpaid_balance), 2)
        if employee_names is not None:
            payload["employee_names"] = employee_names or ""
        try:
            client.table("sales").insert(payload).execute()
        except Exception as e1:
            err_str = str(e1).lower()
            # employee_names 컬럼이 없는 구 스키마면 해당 필드 제외 후 재시도
            if employee_names is not None and ("employee_names" in err_str or "42703" in err_str or "does not exist" in err_str):
                payload.pop("employee_names", None)
                try:
                    client.table("sales").insert(payload).execute()
                except Exception as e2:
                    err_str2 = str(e2).lower()
                    if unpaid_balance is not None and ("unpaid_balance" in err_str2 or "42703" in err_str2 or "does not exist" in err_str2):
                        payload.pop("unpaid_balance", None)
                        client.table("sales").insert(payload).execute()
                    else:
                        raise
            # unpaid_balance 컬럼이 없는 구 Supabase 스키마면 해당 필드 제외 후 재시도
            elif unpaid_balance is not None and ("unpaid_balance" in err_str or "42703" in err_str or "does not exist" in err_str):
                payload.pop("unpaid_balance", None)
                client.table("sales").insert(payload).execute()
            else:
                raise
    except Exception as e:
        if "supabase_error" not in st.session_state:
            st.session_state["supabase_error"] = str(e)


def create_tenant_db(db_filename: str):
    """
    신규 매장 생성 시 해당 매장 전용 SQLite 파일에 5개 테이블을 자동 생성.
    Master의 Stores에 매장 등록 후 이 함수를 호출하여 물리적 DB 파일을 만든다.
    """
    path = os.path.join(DB_DIR, db_filename)
    if os.path.exists(path):
        return
    conn = sqlite3.connect(path)
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS Employees (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS Customers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                phone1 TEXT,
                phone2 TEXT,
                address TEXT,
                source TEXT
            );
            CREATE TABLE IF NOT EXISTS Orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                customer_id INTEGER NOT NULL REFERENCES Customers(id),
                employee_names TEXT,
                order_date TEXT NOT NULL,
                delivery_date TEXT,
                category TEXT,
                cost_price REAL,
                total_amount REAL,
                visit_reason TEXT,
                purchase_reason TEXT,
                actual_margin REAL,
                display_sales_amount INTEGER DEFAULT 0,
                display_cost_amount INTEGER DEFAULT 0,
                balance_status TEXT
            );
            CREATE TABLE IF NOT EXISTS Payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL REFERENCES Orders(id),
                payment_date TEXT NOT NULL,
                amount REAL NOT NULL,
                payment_method TEXT,
                card_company TEXT,
                fee_amount REAL,
                onnuri_approval_code TEXT
            );
            CREATE TABLE IF NOT EXISTS Todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_date TEXT NOT NULL,
                author TEXT,
                content TEXT,
                is_completed INTEGER NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS AuditLogs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                actor_username TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                field_name TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                reason TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS EditRequests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                requested_by TEXT NOT NULL,
                entity_type TEXT NOT NULL,
                entity_id INTEGER NOT NULL,
                payload TEXT NOT NULL,
                reason TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                reviewed_by TEXT,
                reviewed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS PaymentReceipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                payment_id INTEGER NOT NULL REFERENCES Payments(id),
                file_path TEXT NOT NULL,
                original_name TEXT,
                uploaded_by TEXT,
                uploaded_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS PaymentHistory (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                sale_id INTEGER NOT NULL,
                customer_name TEXT,
                action_type TEXT NOT NULL,
                old_payment_data TEXT,
                new_payment_data TEXT,
                reason TEXT NOT NULL,
                changed_by TEXT NOT NULL,
                changed_at TEXT NOT NULL,
                receipt_image_path TEXT
            );
            CREATE TABLE IF NOT EXISTS Sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL REFERENCES Orders(id),
                transaction_date TEXT NOT NULL,
                amount REAL NOT NULL,
                note TEXT,
                created_at TEXT NOT NULL
            );
        """)
        conn.commit()
    finally:
        conn.close()


# ========== 인증 및 세션 ==========

def verify_user(username: str, password: str):
    """
    Master DB에서 사용자 검증.
    성공 시 (user_id, username, role, store_id, db_filename) 튜플 반환.
    실패 시 None.
    """
    pw_hash = hashlib.sha256(password.encode()).hexdigest()
    conn = get_master_conn()
    try:
        row = conn.execute("""
            SELECT u.id, u.username, u.role, u.store_id, s.db_filename
            FROM Users u
            LEFT JOIN Stores s ON u.store_id = s.id
            WHERE u.username = ? AND u.password = ?
        """, (username, pw_hash)).fetchone()
        return row
    finally:
        conn.close()


def get_app_user_by_email(email: str):
    """
    Supabase Auth 로그인 후: 이메일로 사용자 정보 조회.
    Supabase app_users 테이블이 있으면 그쪽을 우선 사용, 없으면 Master DB 사용.
    성공 시 (user_id, username, role, store_id, db_filename) 튜플 반환. 실패 시 None.
    """
    if not email or not str(email).strip():
        return None
    email_clean = str(email).strip().lower()
    if ensure_supabase_app_tables():
        row = _get_supabase_app_user_by_email(email_clean)
        if row is not None:
            return row
        if email_clean == "billymind@gmail.com":
            _ensure_supabase_superadmin_email(email_clean)
            return _get_supabase_app_user_by_email(email_clean)
        return None
    conn = get_master_conn()
    try:
        cur = conn.execute("PRAGMA table_info(Users)")
        cols = [r[1] for r in cur.fetchall()]
        if "email" not in cols:
            conn.execute("ALTER TABLE Users ADD COLUMN email TEXT")
            conn.commit()
        row = conn.execute("""
            SELECT u.id, u.username, u.role, u.store_id, s.db_filename
            FROM Users u
            LEFT JOIN Stores s ON u.store_id = s.id
            WHERE u.email IS NOT NULL AND TRIM(u.email) != '' AND LOWER(TRIM(u.email)) = ?
        """, (email_clean,)).fetchone()
        if row is not None:
            return row
        if email_clean == "billymind@gmail.com":
            conn.execute("UPDATE Users SET email = ? WHERE username = 'superadmin'", (email_clean,))
            conn.commit()
            row = conn.execute("""
                SELECT u.id, u.username, u.role, u.store_id, s.db_filename
                FROM Users u
                LEFT JOIN Stores s ON u.store_id = s.id
                WHERE u.email IS NOT NULL AND TRIM(u.email) != '' AND LOWER(TRIM(u.email)) = ?
            """, (email_clean,)).fetchone()
            return row
        return None
    except Exception:
        return None
    finally:
        conn.close()


def get_user_allowed_stores(user_id: int):
    """
    한 직원이 접근 가능한 매장 목록 (여러 매장 지원).
    Supabase app_users/app_user_stores가 있으면 그쪽 우선, 없으면 Master DB.
    반환: [(store_id, db_filename, store_name), ...]
    """
    if not user_id:
        return []
    if _supabase_app_tables_available():
        return _get_supabase_user_allowed_stores(user_id)
    conn = get_master_conn()
    try:
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='UserStores'")
        if cur.fetchone() is None:
            row = conn.execute("""
                SELECT u.store_id, s.db_filename, s.store_name
                FROM Users u
                LEFT JOIN Stores s ON u.store_id = s.id
                WHERE u.id = ? AND u.store_id IS NOT NULL
            """, (user_id,)).fetchone()
            return [row] if row and row[0] else []
        rows = conn.execute("""
            SELECT s.id, s.db_filename, s.store_name
            FROM UserStores us
            JOIN Stores s ON us.store_id = s.id
            WHERE us.user_id = ?
            ORDER BY s.store_name
        """, (user_id,)).fetchall()
        if rows:
            return [tuple(r) for r in rows]
        row = conn.execute("""
            SELECT u.store_id, s.db_filename, s.store_name
            FROM Users u
            LEFT JOIN Stores s ON u.store_id = s.id
            WHERE u.id = ? AND u.store_id IS NOT NULL
        """, (user_id,)).fetchone()
        return [row] if row and row[0] else []
    except Exception:
        return []
    finally:
        conn.close()


def ensure_session():
    """세션 초기화: logged_in, current_user, current_db 등."""
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
    if "current_user" not in st.session_state:
        st.session_state.current_user = None
    if "current_db" not in st.session_state:
        st.session_state.current_db = None


# ========== 로그인 상태 유지 (localStorage + query_params) ==========
# [토큰 삭제] 다음 두 경우에만 수행. 그 외에는 삭제하지 않음.
#  1) 로그아웃 버튼 클릭 (logout=1) → _inject_js_clear_auth_on_logout
#  2) URL에 auth가 있고 검증 실패(1시간 만료/서명 무효)이며 auth 길이 >= 80 → _inject_js_clear_auth_and_remove_auth_param
#
# [다중 새로고침 시 로그아웃 디버깅 체크리스트]
#  - F12 → Console: "토큰 삭제됨" 로그가 뜨면 → 원인(로그아웃 클릭 / 1시간 만료 또는 무효) 확인
#  - Application → Local Storage / Session Storage: emons_auth 키가 새로고침 후에도 있는지 확인
#  - Network: 새로고침 시 요청 URL에 ?auth= 가 붙는 요청이 한 번이라도 가는지 확인
#  - URL이 2083자 제한으로 잘리면 검증 실패 → auth 길이 < 80이면 삭제 스크립트를 주입하지 않도록 함
AUTH_STORAGE_KEY = "emons_auth"
_AUTH_SECRET_FALLBACK = "emons-default-secret-change-in-production"


def _get_auth_secret() -> str:
    """st.secrets → os.environ → fallback 순으로 HMAC 서명 키를 반환."""
    try:
        val = st.secrets.get("EMONS_AUTH_SECRET") or ""
        if val and str(val).strip():
            return str(val).strip()
    except Exception:
        pass
    return os.environ.get("EMONS_AUTH_SECRET", _AUTH_SECRET_FALLBACK)
AUTH_EXPIRY_DAYS = 30
AUTH_SESSION_SECONDS = AUTH_EXPIRY_DAYS * 24 * 3600  # 토큰 만료일과 동일 (30일)


def _current_username() -> str:
    """세션에서 현재 사용자 ID(username) 가져오기 (없으면 'unknown')."""
    user = st.session_state.get("current_user") or {}
    return user.get("username") or "unknown"


def _get_current_user_display_name() -> str:
    """현재 로그인한 직원의 표시명(실명). app_users.name 우선, 없으면 username. To-Do 작성자 등에 사용."""
    user = st.session_state.get("current_user") or {}
    name = user.get("name")
    if name and str(name).strip():
        return str(name).strip()
    username = user.get("username") or ""
    display_map = _get_app_user_display_name_map()
    return display_map.get(str(username).strip()) or display_map.get(str(username).strip().lower()) or username or ""


def _current_display_name_for_todo() -> str:
    """
    To-Do 작성자 비교용 현재 로그인 직원 표시명.
    To-Do 저장 시 사용한 _get_current_user_display_name() 과 동일한 값을 돌려준다.
    """
    return _get_current_user_display_name()


def _insert_audit_log(
    conn: sqlite3.Connection,
    entity_type: str,
    entity_id: int,
    field_name: str,
    old_value,
    new_value,
    reason: str,
) -> None:
    """AuditLogs 테이블에 변경 이력 1건 삽입."""
    actor = _current_username()
    conn.execute(
        """
        INSERT INTO AuditLogs (created_at, actor_username, entity_type, entity_id, field_name, old_value, new_value, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.now(tz=KST).isoformat(),
            actor,
            entity_type,
            int(entity_id),
            field_name,
            "" if old_value is None else str(old_value),
            "" if new_value is None else str(new_value),
            reason.strip(),
        ),
    )


def _get_username_by_display_name(display_name: str) -> str | None:
    """표시명(실명)으로 app_users.username 역방향 조회. 없으면 None."""
    if not display_name or not display_name.strip():
        return None
    display_map = _get_app_user_display_name_map()
    for uname, dname in display_map.items():
        if (dname or "").strip() == display_name.strip():
            return uname
    return None


def _insert_order_notification(
    db_filename: str,
    order_id: int,
    employee_names_str: str,
    notif_type: str,
    payload_msg: str,
    triggered_by_username: str,
    reason: str,
):
    """담당 직원들에게 알림 레코드 삽입 (app_edit_requests.target_username 활용).
    notif_type: 'order_modified' | 'sales_assigned'
    employee_names_str: 쉼표 구분 표시명(예: '홍길동,김철수')
    triggered_by_username: 변경자 username (자기 자신은 알림 제외)
    """
    if not _supabase_orders_payments_available():
        return
    if not employee_names_str or not employee_names_str.strip():
        return
    target_names = [n.strip() for n in employee_names_str.split(",") if n.strip()]
    if not target_names:
        return
    try:
        sc, err = get_supabase_client()
        if err or not sc:
            return
        _tenant_name = _get_store_name_by_db(db_filename) or db_filename
        for display_name in target_names:
            target_uname = _get_username_by_display_name(display_name)
            if not target_uname:
                continue
            if target_uname == triggered_by_username:
                continue
            try:
                sc.table("app_edit_requests").insert({
                    "db_filename": db_filename,
                    "tenant_name": _tenant_name,
                    "entity_type": "Order",
                    "entity_id": int(order_id),
                    "requested_by": triggered_by_username or "",
                    "target_username": target_uname,
                    "notif_type": notif_type,
                    "payload": payload_msg,
                    "reason": reason,
                    "status": "pending_notif",
                }).execute()
            except Exception:
                pass
    except Exception:
        pass


def _get_admin_usernames_for_store(db_filename: str) -> list[str]:
    """해당 매장의 store_admin + superadmin username 목록 반환.
    app_users.store_id (단일 매장) 와 app_user_stores (다중 매장 배정) 모두 체크.
    """
    if not _supabase_orders_payments_available() or not db_filename:
        return []
    try:
        users = _get_supabase_users_list()
        stores = _get_supabase_stores_list()
        # db_filename → store id 변환
        store_id = None
        for s in stores:
            if s.get("db_filename") == db_filename:
                store_id = s.get("id")
                break

        # app_user_stores 에서 해당 매장에 배정된 user_id 집합 조회 (다중 매장 배정 지원)
        user_ids_in_store: set = set()
        if store_id is not None:
            try:
                sc, _err = get_supabase_client()
                if sc and not _err:
                    _r = sc.table("app_user_stores").select("user_id").eq("store_id", store_id).execute()
                    user_ids_in_store = {x["user_id"] for x in (_r.data or [])}
            except Exception:
                pass

        result = []
        for u in users:
            uname = (u.get("username") or "").strip()
            role = u.get("role", "")
            if not uname:
                continue
            if role == "superadmin":
                result.append(uname)
            elif role == "store_admin" and store_id is not None:
                # app_users.store_id 또는 app_user_stores 둘 중 하나라도 매칭되면 포함
                if u.get("store_id") == store_id or u.get("id") in user_ids_in_store:
                    result.append(uname)
        return list(dict.fromkeys(result))  # 중복 제거 (순서 유지)
    except Exception:
        return []


def _insert_fraud_alert_to_admins(
    db_filename: str,
    order_id: int,
    actor_username: str,
    alert_level: str,
    alert_type: str,
    payload: str,
    reason: str = "",
):
    """부정행위 의심 신호를 app_edit_requests에 삽입 — 수신자: 매장관리자·통합관리자 전원.
    alert_level: 'info' | 'warning' | 'critical'
    alert_type : 'high_amount_change' | 'negative_margin' | 'payment_cancel' |
                 'off_hours_edit' | 'self_sales_assign'
    """
    if not _supabase_orders_payments_available() or not db_filename:
        return
    admin_usernames = _get_admin_usernames_for_store(db_filename)
    if not admin_usernames:
        return
    try:
        sc, err = get_supabase_client()
        if err or not sc:
            return
        _fraud_tenant = _get_store_name_by_db(db_filename) or db_filename
        for admin_uname in admin_usernames:
            try:
                sc.table("app_edit_requests").insert({
                    "db_filename": db_filename,
                    "tenant_name": _fraud_tenant,
                    "entity_type": "Order",
                    "entity_id": int(order_id) if order_id else 0,
                    "requested_by": actor_username or "unknown",
                    "target_username": admin_uname,
                    "notif_type": "admin_alert",
                    "payload": f"[{alert_level.upper()}][{alert_type}] {payload}",
                    "reason": reason or "",
                    "status": "pending_notif",
                }).execute()
            except Exception:
                pass
    except Exception:
        pass


def _check_and_send_fraud_signals(
    db_filename: str,
    order_id: int,
    actor_username: str,
    old_total: float = 0.0,
    new_total: float = 0.0,
    old_cost: float = 0.0,
    new_cost: float = 0.0,
    new_display_cost: float = 0.0,
    old_employee_names: str = "",
    new_employee_names: str = "",
    reason: str = "",
    action_type: str = "order_modified",
):
    """주문 수정·결제 변경 완료 직후 호출 — 부정행위 의심 패턴 탐지 후 관리자 알림 삽입.
    코어 로직과 완전히 분리된 읽기전용 탐지 레이어.
    """
    if not db_filename:
        return
    try:
        current_hour = datetime.now(tz=KST).hour

        # ── 규칙 1: 결제 취소 ──
        if action_type == "payment_cancel":
            _insert_fraud_alert_to_admins(
                db_filename, order_id, actor_username,
                "warning", "payment_cancel",
                f"주문 #{order_id} 결제 취소 처리 — 담당: {actor_username}",
                reason,
            )
            return  # 결제 취소는 단일 규칙만 적용

        # ── 규칙 2: 금액 변경률 ≥ 15% ──
        if old_total > 0 and action_type in ("order_modified", "amount_change"):
            change_pct = abs(new_total - old_total) / old_total * 100
            if change_pct >= 30:
                _insert_fraud_alert_to_admins(
                    db_filename, order_id, actor_username,
                    "critical", "high_amount_change",
                    f"판매금액 {change_pct:.1f}% 변경: {int(old_total):,}원 → {int(new_total):,}원 (담당: {actor_username})",
                    reason,
                )
            elif change_pct >= 15:
                _insert_fraud_alert_to_admins(
                    db_filename, order_id, actor_username,
                    "warning", "high_amount_change",
                    f"판매금액 {change_pct:.1f}% 변경: {int(old_total):,}원 → {int(new_total):,}원 (담당: {actor_username})",
                    reason,
                )

        # ── 규칙 3: 마진율 음수 (원가 ≥ 판매가) ──
        if new_total > 0:
            total_cost = new_cost + new_display_cost
            if total_cost >= new_total:
                _insert_fraud_alert_to_admins(
                    db_filename, order_id, actor_username,
                    "critical", "negative_margin",
                    f"원가({int(total_cost):,}원) ≥ 판매가({int(new_total):,}원) — 마진율 0% 이하 감지 (담당: {actor_username})",
                    reason,
                )

        # ── 규칙 4: 영업 외 시간 수정 (22시~07시) ──
        if current_hour >= 22 or current_hour < 7:
            _insert_fraud_alert_to_admins(
                db_filename, order_id, actor_username,
                "info", "off_hours_edit",
                f"주문 #{order_id} 영업 외 시간({current_hour}시) 수정 — 담당: {actor_username}",
                reason,
            )

        # ── 규칙 5: 본인이 자신을 담당 직원에 추가 ──
        if old_employee_names != new_employee_names and actor_username and actor_username != "unknown":
            actor_display = (_get_app_user_display_name_map().get(actor_username) or actor_username)
            _old_set = {e.strip() for e in old_employee_names.split(",") if e.strip()}
            _new_set = {e.strip() for e in new_employee_names.split(",") if e.strip()}
            _newly_added = _new_set - _old_set
            if actor_display in _newly_added:
                _insert_fraud_alert_to_admins(
                    db_filename, order_id, actor_username,
                    "warning", "self_sales_assign",
                    f"{actor_username}이(가) 타인 주문({order_id})에 본인을 담당자로 추가: {old_employee_names or '(없음)'} → {new_employee_names}",
                    reason,
                )

    except Exception:
        pass  # 탐지 오류가 코어 로직에 영향을 주지 않도록 silently 무시


def _fetch_my_notifications(db_filename: str, username: str) -> list:
    """현재 직원(username)에게 발송된 미확인 알림 목록 조회."""
    if not _supabase_orders_payments_available() or not username or username == "unknown":
        return []
    try:
        sc, err = get_supabase_client()
        if err or not sc:
            return []
        r = (
            sc.table("app_edit_requests")
            .select("id, created_at, db_filename, entity_id, notif_type, payload, reason, requested_by")
            .eq("target_username", username)
            .eq("status", "pending_notif")
            .eq("db_filename", db_filename)
            .order("created_at", desc=True)
            .limit(50)
            .execute()
        )
        return r.data or []
    except Exception:
        return []


def _mark_notifications_read(notification_ids: list):
    """알림 ID 목록을 읽음(notif_seen) 처리."""
    if not notification_ids:
        return
    try:
        sc, err = get_supabase_client()
        if err or not sc:
            return
        sc.table("app_edit_requests").update({"status": "notif_seen"}).in_("id", notification_ids).execute()
    except Exception:
        pass


def _render_login_notifications(db_filename: str):
    """로그인 직후 1회만 호출: 미확인 알림을 배너로 표시 후 읽음 처리."""
    if st.session_state.get("_login_notif_shown"):
        return
    st.session_state["_login_notif_shown"] = True

    username = _current_username()
    if not username or username == "unknown":
        return

    notifs = _fetch_my_notifications(db_filename, username)
    if not notifs:
        return

    notif_ids = [n["id"] for n in notifs if "id" in n]
    _mark_notifications_read(notif_ids)

    notif_type_label = {
        "order_modified": ("📝", "주문 정보가 수정되었습니다"),
        "sales_assigned": ("💰", "매출이 새로 배분되었습니다"),
    }

    with st.container(border=True):
        st.markdown(f"### 🔔 새 알림 {len(notifs)}건")
        st.caption("아래 알림은 최근 주문 수정 또는 매출 배분 내역입니다. 페이지를 이동하면 사라집니다.")
        for n in notifs:
            created = str(n.get("created_at", ""))[:16].replace("T", " ")
            triggered = n.get("requested_by", "누군가")
            payload = n.get("payload", "")
            ntype = n.get("notif_type", "")
            order_id = n.get("entity_id", "")
            icon, label = notif_type_label.get(ntype, ("ℹ️", "알림"))
            reason_text = n.get("reason", "")
            st.info(
                f"{icon} **{label}** — 주문 #{order_id}  \n"
                f"변경자: `{triggered}` | 일시: {created}  \n"
                f"사유: {reason_text or '(없음)'}  \n"
                f"내용: {payload}"
            )
        st.divider()


def _fetch_admin_fraud_alerts(db_filename: str, username: str) -> list:
    """관리자(username)에게 발송된 미확인 부정행위 경보 목록 조회."""
    if not _supabase_orders_payments_available() or not username or username == "unknown":
        return []
    try:
        sc, err = get_supabase_client()
        if err or not sc:
            return []
        r = (
            sc.table("app_edit_requests")
            .select("id, created_at, entity_id, payload, reason, requested_by")
            .eq("target_username", username)
            .eq("notif_type", "admin_alert")
            .eq("status", "pending_notif")
            .eq("db_filename", db_filename)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
        return r.data or []
    except Exception:
        return []


def _render_admin_fraud_alerts(db_filename: str):
    """로그인 직후 1회만 호출: 관리자(store_admin·superadmin)에게 부정행위 의심 경보 표시.
    일반 직원 알림(_render_login_notifications)과 완전히 분리된 독립 레이어.
    """
    if st.session_state.get("_admin_fraud_alert_shown"):
        return
    st.session_state["_admin_fraud_alert_shown"] = True

    username = _current_username()
    if not username or username == "unknown":
        return

    alerts = _fetch_admin_fraud_alerts(db_filename, username)
    if not alerts:
        return

    alert_ids = [a["id"] for a in alerts if "id" in a]
    _mark_notifications_read(alert_ids)

    level_icon = {
        "CRITICAL": "🚨",
        "WARNING":  "⚠️",
        "INFO":     "ℹ️",
    }
    type_label = {
        "high_amount_change": "판매금액 대폭 변경",
        "negative_margin":    "마진율 음수 감지",
        "payment_cancel":     "결제 취소",
        "off_hours_edit":     "영업 외 시간 수정",
        "self_sales_assign":  "본인 직접 배분 의심",
    }

    with st.container(border=True):
        st.markdown(f"### 🔍 관리자 경보 {len(alerts)}건")
        st.caption("아래 항목은 부정행위 의심 패턴이 감지된 주문입니다. 페이지를 이동하면 사라집니다.")
        for a in alerts:
            created = str(a.get("created_at", ""))[:16].replace("T", " ")
            actor = a.get("requested_by", "알 수 없음")
            payload_raw = a.get("payload", "")
            order_id = a.get("entity_id", "-")
            reason_text = a.get("reason", "")
            # payload 파싱: "[CRITICAL][high_amount_change] 내용"
            level_str = "WARNING"
            type_str = ""
            content_str = payload_raw
            try:
                if payload_raw.startswith("[") and "][" in payload_raw:
                    end1 = payload_raw.index("]")
                    level_str = payload_raw[1:end1]
                    rest = payload_raw[end1 + 1:]
                    if rest.startswith("["):
                        end2 = rest.index("]")
                        type_str = rest[1:end2]
                        content_str = rest[end2 + 2:]
            except Exception:
                pass
            icon = level_icon.get(level_str, "⚠️")
            type_display = type_label.get(type_str, type_str)
            if level_str == "CRITICAL":
                st.error(
                    f"{icon} **[{type_display}]** — 주문 #{order_id}  \n"
                    f"담당자: `{actor}` | 일시: {created}  \n"
                    f"사유: {reason_text or '(없음)'}  \n"
                    f"{content_str}"
                )
            elif level_str == "WARNING":
                st.warning(
                    f"{icon} **[{type_display}]** — 주문 #{order_id}  \n"
                    f"담당자: `{actor}` | 일시: {created}  \n"
                    f"사유: {reason_text or '(없음)'}  \n"
                    f"{content_str}"
                )
            else:
                st.info(
                    f"{icon} **[{type_display}]** — 주문 #{order_id}  \n"
                    f"담당자: `{actor}` | 일시: {created}  \n"
                    f"사유: {reason_text or '(없음)'}  \n"
                    f"{content_str}"
                )
        st.divider()


# ────────────────────────────────────────────────────────────────────────────
# 주문 삭제 요청 시스템 (직원 → 관리자 2단계 승인)
# ────────────────────────────────────────────────────────────────────────────

def _insert_delete_request(db_filename: str, order_id: int, reason: str, requested_by_username: str):
    """직원이 요청한 주문 삭제를 app_edit_requests에 기록 (notif_type='delete_request').
    해당 매장의 store_admin + superadmin 모두에게 알림을 보낸다.
    """
    if not _supabase_orders_payments_available() or not db_filename:
        return False, "Supabase 연결 오류"
    admin_usernames = _get_admin_usernames_for_store(db_filename)
    if not admin_usernames:
        return False, "관리자를 찾을 수 없습니다"
    try:
        sc, err = get_supabase_client()
        if err or not sc:
            return False, str(err)
        tenant_name = _get_store_name_by_db(db_filename) or db_filename
        for admin_uname in admin_usernames:
            sc.table("app_edit_requests").insert({
                "db_filename": db_filename,
                "tenant_name": tenant_name,
                "entity_type": "Order",
                "entity_id": int(order_id),
                "requested_by": requested_by_username or "",
                "target_username": admin_uname,
                "notif_type": "delete_request",
                "payload": f"주문 #{order_id} 삭제 요청",
                "reason": reason.strip(),
                "status": "pending",
            }).execute()
        return True, None
    except Exception as e:
        return False, str(e)


def _fetch_pending_delete_requests(db_filename: str) -> list:
    """해당 매장의 미처리 삭제 요청 목록 조회 (관리자용)."""
    if not _supabase_orders_payments_available() or not db_filename:
        return []
    try:
        sc, err = get_supabase_client()
        if err or not sc:
            return []
        username = _current_username()
        r = (
            sc.table("app_edit_requests")
            .select("id, created_at, entity_id, requested_by, reason, status")
            .eq("db_filename", db_filename)
            .eq("notif_type", "delete_request")
            .eq("target_username", username)
            .eq("status", "pending")
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
        return r.data or []
    except Exception:
        return []


def _fetch_recent_resolved_delete_requests(db_filename: str) -> list:
    """최근 처리 완료된 삭제 요청 목록 조회 (승인·반려 포함, 최근 20건).
    reviewed_at 컬럼이 없는 환경에서는 created_at 기준으로 정렬 fallback.
    """
    if not _supabase_orders_payments_available() or not db_filename:
        return []
    try:
        sc, err = get_supabase_client()
        if err or not sc:
            return []
        username = _current_username()
        # reviewed_at 포함 시도
        try:
            r = (
                sc.table("app_edit_requests")
                .select("id, created_at, reviewed_at, entity_id, requested_by, reason, status, reviewed_by, payload")
                .eq("db_filename", db_filename)
                .eq("notif_type", "delete_request")
                .eq("target_username", username)
                .in_("status", ["approved", "rejected"])
                .order("reviewed_at", desc=True)
                .limit(20)
                .execute()
            )
        except Exception:
            # reviewed_at / reviewed_by 컬럼 없는 경우 created_at 기준 fallback
            r = (
                sc.table("app_edit_requests")
                .select("id, created_at, entity_id, requested_by, reason, status, payload")
                .eq("db_filename", db_filename)
                .eq("notif_type", "delete_request")
                .eq("target_username", username)
                .in_("status", ["approved", "rejected"])
                .order("created_at", desc=True)
                .limit(20)
                .execute()
            )
        return r.data or []
    except Exception:
        return []


def _resolve_delete_request(request_id: int, action: str, reviewed_by: str, reject_reason: str = "", order_id: int | None = None, db_filename: str | None = None):
    """삭제 요청을 승인(approved) 또는 반려(rejected) 처리.
    action: 'approved' | 'rejected'
    order_id + db_filename 을 함께 넘기면 동일 주문의 다른 관리자 레코드도 일괄 처리.
    테이블 스키마에 없는 컬럼은 자동으로 제외하며 최소한 status만 업데이트.
    """
    def _try_update_with_fallback(sc, base_filter_fn, reject_reason: str):
        """reviewed_at → reviewed_by → status+reason → status 순으로 단계별 fallback."""
        base = {"status": action}
        if reject_reason:
            base["reason"] = reject_reason

        candidates = [
            {**base, "reviewed_by": reviewed_by, "reviewed_at": datetime.now(tz=KST).isoformat()},
            {**base, "reviewed_by": reviewed_by},
            base,
            {"status": action},
        ]
        last_err = None
        for payload in candidates:
            try:
                base_filter_fn(sc.table("app_edit_requests").update(payload)).execute()
                return True, None
            except Exception as e:
                last_err = e
                continue
        return False, str(last_err)

    try:
        sc, err = get_supabase_client()
        if err or not sc:
            return False, str(err)

        # 1) 특정 레코드 업데이트
        ok, upd_err = _try_update_with_fallback(
            sc,
            lambda q: q.eq("id", request_id),
            reject_reason,
        )
        if not ok:
            return False, upd_err

        # 2) 같은 주문의 다른 관리자 레코드도 일괄 처리 (중복 표시 방지)
        if order_id is not None and db_filename:
            try:
                _try_update_with_fallback(
                    sc,
                    lambda q: q.eq("db_filename", db_filename).eq("entity_id", int(order_id)).eq("notif_type", "delete_request").eq("status", "pending"),
                    reject_reason,
                )
            except Exception:
                pass
        return True, None
    except Exception as e:
        return False, str(e)


def _snapshot_order_for_delete(db_filename: str, order_id: int) -> dict:
    """삭제 전 주문 상세 정보를 dict로 수집하여 반환. 삭제 후 payload에 보관용."""
    snap = {"order_id": order_id}
    try:
        if not _supabase_orders_payments_available() or not order_id:
            return snap
        order_detail = _get_order_supabase(db_filename, int(order_id))
        if not order_detail:
            return snap
        cid = order_detail.get("customer_id")
        cust_name = _get_customer_name_supabase(db_filename, int(cid)) if cid else "-"
        paid, _ = _sum_payments_by_order_supabase(db_filename, int(order_id))
        total_amount = float(order_detail.get("total_amount") or 0)
        snap.update({
            "customer_name": cust_name or "-",
            "order_date": str(order_detail.get("order_date") or "-")[:10],
            "delivery_date": str(order_detail.get("delivery_date") or "-")[:10],
            "category": str(order_detail.get("category") or "-"),
            "total_amount": int(total_amount),
            "paid_amount": int(paid),
            "balance": int(total_amount - paid),
            "employee_names": str(order_detail.get("employee_names") or "-"),
        })
    except Exception:
        pass
    return snap


def _save_order_snapshot_to_payload(db_filename: str, order_id: int, snapshot: dict):
    """app_edit_requests의 payload 컬럼에 스냅샷 JSON 저장 (삭제 승인 이후 상세 조회용)."""
    try:
        sc, err = get_supabase_client()
        if err or not sc:
            return
        import json as _json
        payload_json = _json.dumps(snapshot, ensure_ascii=False)
        (
            sc.table("app_edit_requests")
            .update({"payload": payload_json})
            .eq("db_filename", db_filename)
            .eq("entity_id", int(order_id))
            .eq("notif_type", "delete_request")
            .execute()
        )
    except Exception:
        pass


def _approve_delete_order(db_filename: str, order_id: int) -> tuple:
    """주문 및 연관 결제 데이터를 실제로 삭제한다 (관리자 승인 후 호출).
    반환: (성공여부: bool, 오류메시지: str | None)
    """
    try:
        if _supabase_orders_payments_available():
            sc, err = get_supabase_client()
            if err or not sc:
                return False, str(err)
            sc.table("app_payments").delete().eq("order_id", int(order_id)).eq("db_filename", db_filename).execute()
            try:
                sc.table("sales").delete().eq("order_id", int(order_id)).execute()
            except Exception:
                pass
            sc.table("app_orders").delete().eq("id", int(order_id)).eq("db_filename", db_filename).execute()
        else:
            conn = get_tenant_conn(db_filename)
            try:
                conn.execute("DELETE FROM Payments WHERE order_id = ?", (int(order_id),))
                conn.execute("DELETE FROM Orders WHERE id = ?", (int(order_id),))
                conn.commit()
            finally:
                conn.close()
        clear_data_cache()
        return True, None
    except Exception as e:
        return False, str(e)


def _render_admin_delete_requests(db_filename: str):
    """관리자 전용: 직원이 보낸 주문 삭제 요청 목록을 표시하고 승인/반려 처리."""
    st.header("🗑️ 주문 삭제 요청 관리")
    st.caption("직원이 보낸 주문 삭제 요청을 검토하고 승인 또는 반려합니다. 승인 시 주문과 결제 데이터가 영구 삭제됩니다.")

    reviewed_by = _current_username()

    # ── 대기 중인 요청 ──
    requests_list = _fetch_pending_delete_requests(db_filename)

    if not requests_list:
        st.info("현재 처리 대기 중인 삭제 요청이 없습니다.")
    else:
        st.markdown(f"**⏳ 대기 중인 삭제 요청: {len(requests_list)}건**")
        for req in requests_list:
            req_id = req.get("id")
            order_id = req.get("entity_id")
            requester = req.get("requested_by", "알 수 없음")
            reason = req.get("reason", "")
            created = str(req.get("created_at", ""))[:16].replace("T", " ")

            # ── 주문 상세 정보 조회 ──
            order_detail = None
            cust_name = "-"
            order_date_str = "-"
            delivery_date_str = "-"
            category_str = "-"
            total_amount_str = "-"
            paid_str = "-"
            balance_str = "-"
            employee_str = "-"
            try:
                if _supabase_orders_payments_available() and order_id:
                    order_detail = _get_order_supabase(db_filename, int(order_id))
                    if order_detail:
                        cid = order_detail.get("customer_id")
                        if cid:
                            cust_name = _get_customer_name_supabase(db_filename, int(cid)) or "-"
                        order_date_str = str(order_detail.get("order_date") or "-")[:10]
                        delivery_date_str = str(order_detail.get("delivery_date") or "-")[:10]
                        category_str = str(order_detail.get("category") or "-")
                        total_amount = float(order_detail.get("total_amount") or 0)
                        total_amount_str = f"{int(total_amount):,}원"
                        employee_str = str(order_detail.get("employee_names") or "-")
                        paid, _ = _sum_payments_by_order_supabase(db_filename, int(order_id))
                        balance = total_amount - paid
                        paid_str = f"{int(paid):,}원"
                        balance_str = f"{int(balance):,}원"
            except Exception:
                pass

            with st.container(border=True):
                col_info, col_btns = st.columns([3, 2])
                with col_info:
                    st.markdown(f"**주문 #{order_id}** 삭제 요청")
                    st.caption(f"요청자: `{requester}` | 요청일시: {created}")
                    # 주문 상세 정보
                    info_col1, info_col2 = st.columns(2)
                    with info_col1:
                        st.write(f"👤 **고객명:** {cust_name}")
                        st.write(f"📅 **계약일:** {order_date_str}")
                        st.write(f"🚚 **배송일:** {delivery_date_str}")
                    with info_col2:
                        st.write(f"🛋️ **품목:** {category_str}")
                        st.write(f"💰 **계약금액:** {total_amount_str}")
                        st.write(f"💳 **결제액:** {paid_str} | 잔금: {balance_str}")
                    st.write(f"👥 **담당직원:** {employee_str}")
                    st.error(f"🗑️ **삭제 사유:** {reason or '(사유 없음)'}")
                with col_btns:
                    approve_key = f"del_approve_{req_id}"
                    reject_key = f"del_reject_{req_id}"
                    reject_reason_key = f"del_reject_reason_{req_id}"

                    if st.button("✅ 승인 (삭제 실행)", key=approve_key, type="primary"):
                        # 삭제 전 주문 스냅샷 저장 (삭제 후 내역 조회용)
                        _snap = _snapshot_order_for_delete(db_filename, order_id)
                        _save_order_snapshot_to_payload(db_filename, order_id, _snap)
                        ok, del_err = _approve_delete_order(db_filename, order_id)
                        if ok:
                            res_ok, res_err = _resolve_delete_request(req_id, "approved", reviewed_by, order_id=order_id, db_filename=db_filename)
                            if not res_ok:
                                st.warning(f"주문은 삭제되었으나 요청 상태 갱신 실패: {res_err}")
                            st.session_state[f"_del_done_{req_id}"] = order_id
                            st.toast(f"✅ 주문 #{order_id} 삭제 완료", icon="✅")
                            st.rerun()
                        else:
                            st.error(f"삭제 실패: {del_err}")
                            st.stop()

                    reject_reason_val = st.text_input("반려 사유 (선택)", key=reject_reason_key, placeholder="반려 이유를 입력하세요")
                    if st.button("❌ 반려", key=reject_key):
                        ok, rej_err = _resolve_delete_request(req_id, "rejected", reviewed_by, reject_reason_val, order_id=order_id, db_filename=db_filename)
                        if ok:
                            st.toast(f"주문 #{order_id} 삭제 요청이 반려되었습니다.", icon="❌")
                            st.session_state[f"_del_rejected_{req_id}"] = order_id
                            st.rerun()
                        else:
                            st.error(f"반려 처리 실패: {rej_err}")

    st.divider()

    # ── 최근 처리 완료 내역 ──
    st.markdown("#### 📋 최근 처리 완료 내역")
    resolved_list = _fetch_recent_resolved_delete_requests(db_filename)
    if not resolved_list:
        st.caption("최근 처리된 삭제 요청이 없습니다.")
    else:
        import json as _json

        # Excel 다운로드용 데이터 수집
        _dl_rows = []

        for res in resolved_list:
            r_order_id = res.get("entity_id")
            r_requester = res.get("requested_by", "알 수 없음")
            r_reason = res.get("reason", "")
            r_status = res.get("status", "")
            r_reviewed_by = res.get("reviewed_by", "")
            r_reviewed_at = str(res.get("reviewed_at") or "")[:16].replace("T", " ")
            r_created = str(res.get("created_at", ""))[:16].replace("T", " ")

            # payload에서 스냅샷 파싱 (jsonb→dict 또는 text→str 모두 처리)
            snap = {}
            try:
                raw_payload = res.get("payload")
                if isinstance(raw_payload, dict):
                    snap = raw_payload  # Supabase jsonb 타입: 이미 dict로 반환됨
                elif isinstance(raw_payload, str) and raw_payload.strip().startswith("{"):
                    snap = _json.loads(raw_payload)
            except Exception:
                snap = {}

            r_cust    = snap.get("customer_name", "-")
            r_date    = snap.get("order_date", "-")
            r_del     = snap.get("delivery_date", "-")
            r_cat     = snap.get("category", "-")
            r_total   = snap.get("total_amount")
            r_paid    = snap.get("paid_amount")
            r_bal     = snap.get("balance")
            r_emp     = snap.get("employee_names", "-")

            r_total_str = f"{int(r_total):,}원" if r_total is not None else "-"
            r_paid_str  = f"{int(r_paid):,}원"  if r_paid  is not None else "-"
            r_bal_str   = f"{int(r_bal):,}원"   if r_bal   is not None else "-"

            badge_icon = "✅" if r_status == "approved" else "❌"
            badge_label = "삭제 완료" if r_status == "approved" else "반려됨"

            with st.expander(
                f"{badge_icon} **주문 #{r_order_id}** [{badge_label}]  "
                f"ㅤ고객: {r_cust}ㅤ|ㅤ계약금액: {r_total_str}ㅤ|ㅤ처리일: {r_reviewed_at}",
                expanded=False,
            ):
                d_col1, d_col2 = st.columns(2)
                with d_col1:
                    st.write(f"👤 **고객명:** {r_cust}")
                    st.write(f"📅 **계약일:** {r_date}")
                    st.write(f"🚚 **배송일:** {r_del}")
                    st.write(f"🛋️ **품목:** {r_cat}")
                with d_col2:
                    st.write(f"💰 **계약금액:** {r_total_str}")
                    st.write(f"💳 **결제액:** {r_paid_str}  /  잔금: {r_bal_str}")
                    st.write(f"👥 **담당직원:** {r_emp}")
                st.divider()
                st.write(f"🗑️ **삭제 사유:** {r_reason or '(없음)'}")
                st.write(f"📋 **요청자:** `{r_requester}` | 요청일시: {r_created}")
                st.write(f"✍️ **처리자:** `{r_reviewed_by}` | 처리일시: {r_reviewed_at}")

            _dl_rows.append({
                "주문번호":    r_order_id,
                "처리결과":    badge_label,
                "고객명":      r_cust,
                "계약일":      r_date,
                "배송일":      r_del,
                "품목":        r_cat,
                "계약금액(원)": r_total if r_total is not None else "",
                "결제액(원)":   r_paid  if r_paid  is not None else "",
                "잔금(원)":     r_bal   if r_bal   is not None else "",
                "담당직원":    r_emp,
                "삭제사유":    r_reason or "",
                "요청자":      r_requester,
                "요청일시":    r_created,
                "처리자":      r_reviewed_by,
                "처리일시":    r_reviewed_at,
            })

        # ── Excel 다운로드 버튼 ──
        if _dl_rows:
            try:
                import io as _io
                _df_dl = pd.DataFrame(_dl_rows)
                _buf = _io.BytesIO()
                with pd.ExcelWriter(_buf, engine="openpyxl") as _ew:
                    _df_dl.to_excel(_ew, index=False, sheet_name="삭제처리내역")
                st.download_button(
                    label="📥 삭제 처리 내역 Excel 다운로드",
                    data=_buf.getvalue(),
                    file_name=f"삭제처리내역_{datetime.now(tz=KST).strftime('%Y%m%d_%H%M')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="del_history_download_btn",
                )
            except Exception as _ex:
                st.caption(f"다운로드 준비 실패: {_ex}")

    if st.button("🔄 새로고침", key="del_req_refresh_btn"):
        st.rerun()


def _save_payment_receipt(conn: sqlite3.Connection, payment_id: int, uploaded_file):
    """온누리 등 결제 영수증 파일을 RECEIPT_DIR에 저장하고 PaymentReceipts에 경로 기록."""
    if uploaded_file is None:
        return
    try:
        raw_name = uploaded_file.name
    except Exception:
        raw_name = "receipt"
    name_root, ext = os.path.splitext(raw_name)
    if not ext:
        ext = ".bin"
    safe_ext = ext.lower()
    filename = f"{int(payment_id)}_{int(time.time())}{safe_ext}"
    file_path = os.path.join(RECEIPT_DIR, filename)
    try:
        with open(file_path, "wb") as f:
            f.write(uploaded_file.getbuffer())
    except Exception:
        return
    conn.execute(
        """
        INSERT INTO PaymentReceipts (payment_id, file_path, original_name, uploaded_by, uploaded_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            int(payment_id),
            file_path,
            raw_name,
            _current_username(),
            datetime.now(tz=KST).isoformat(),
        ),
    )


def _insert_payment_history(
    conn,
    sale_id: int,
    customer_name: str,
    action_type: str,
    old_payment_data,
    new_payment_data,
    reason: str,
    receipt_image_path: str | None = None,
    db_filename: str | None = None,
) -> None:
    """결제 변경 이력 1건 기록. Supabase(app_payment_history) 우선, SQLite conn 있으면 병행 저장.
    반환값: 오류 문자열(실패) 또는 None(성공)"""
    import math

    def _safe_json(obj):
        """NaN/Inf/pd.NA 등 JSON 직렬화 불가 값을 None으로 치환하는 재귀 변환."""
        if obj is None:
            return None
        if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
            return None
        try:
            import pandas as _pd
            if obj is _pd.NA or obj is _pd.NaT:
                return None
        except Exception:
            pass
        if isinstance(obj, dict):
            return {k: _safe_json(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_safe_json(v) for v in obj]
        return obj

    now_iso = datetime.now(tz=KST).isoformat()
    changed_by = _current_username() or "unknown"
    supa_error: str | None = None
    safe_old = _safe_json(old_payment_data or {})
    safe_new = _safe_json(new_payment_data or {})
    # ── Supabase 저장 ──
    if _supabase_orders_payments_available() and db_filename:
        try:
            sc, err = get_supabase_client()
            if err or not sc:
                supa_error = f"Supabase 클라이언트 오류: {err}"
            else:
                result = sc.table("app_payment_history").insert({
                    "db_filename": db_filename,
                    "sale_id": int(sale_id),
                    "customer_name": customer_name or "",
                    "action_type": action_type,
                    "old_payment_data": safe_old,
                    "new_payment_data": safe_new,
                    "reason": reason.strip(),
                    "changed_by": changed_by,
                    "changed_at": now_iso,
                    "receipt_image_path": receipt_image_path or None,
                }).execute()
                if hasattr(result, "error") and result.error:
                    supa_error = str(result.error)
        except Exception as _e:
            supa_error = str(_e)
    # ── 오류를 session_state에 누적 저장 (모니터링 페이지에서 표시) ──
    if supa_error:
        _errs = st.session_state.get("_ph_insert_errors", [])
        _errs.append(f"[{action_type}] {supa_error}")
        st.session_state["_ph_insert_errors"] = _errs[-5:]  # 최대 5건 보관
    # ── SQLite 저장 (로컬 환경 또는 병행) ──
    if conn:
        try:
            conn.execute(
                """
                INSERT INTO PaymentHistory (
                    sale_id, customer_name, action_type,
                    old_payment_data, new_payment_data,
                    reason, changed_by, changed_at, receipt_image_path
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(sale_id),
                    customer_name or "",
                    action_type,
                    json.dumps(safe_old, ensure_ascii=False),
                    json.dumps(safe_new, ensure_ascii=False),
                    reason.strip(),
                    changed_by,
                    now_iso,
                    receipt_image_path or None,
                ),
            )
        except Exception as _sqlite_e:
            if supa_error is None and not _supabase_orders_payments_available():
                supa_error = f"SQLite 저장 오류: {_sqlite_e}"
    return supa_error


def _render_order_audit_trail(db_filename: str, order_id: int):
    """주문(Order) 기준 변경 이력(AuditLogs) + 관련 결제 영수증 표시 공통 UI.
    Supabase 환경: app_edit_requests 테이블에서 이력 조회. SQLite 환경: AuditLogs 테이블 사용."""
    logs = pd.DataFrame()
    # ── Supabase 경로 ──
    if _supabase_orders_payments_available():
        try:
            sc, err = get_supabase_client()
            if sc and not err:
                r = sc.table("app_edit_requests").select(
                    "created_at, requested_by, payload, reason, status, reviewed_by, reviewed_at"
                ).eq("db_filename", db_filename).eq("entity_id", int(order_id)).eq("entity_type", "Order").order("created_at", desc=True).execute()
                rows = r.data or []
                if rows:
                    logs = pd.DataFrame([{
                        "created_at": x.get("created_at", "")[:16],
                        "actor_username": x.get("requested_by", "-"),
                        "field_name": "수정 요청",
                        "old_value": "-",
                        "new_value": str(x.get("payload") or ""),
                        "reason": x.get("reason", "-"),
                        "status": x.get("status", "-"),
                        "reviewed_by": x.get("reviewed_by") or "-",
                    } for x in rows])
        except Exception:
            logs = pd.DataFrame()
        if logs.empty:
            st.info("변경 이력이 없습니다. (수정 요청 이력만 표시됩니다)")
        else:
            for _, row in logs.iterrows():
                _status_icon = "✅" if row.get("status") == "approved" else ("❌" if row.get("status") == "rejected" else "⏳")
                with st.expander(f"{row['created_at']} — {row['actor_username']} [{_status_icon} {row.get('status', '-')}]"):
                    st.write(f"**사유:** {row['reason']}")
                    st.write(f"**검토자:** {row.get('reviewed_by', '-')}")
                    st.caption(f"변경 내용(payload): {row['new_value']}")
        return
    # ── SQLite 경로 ──
    conn = get_tenant_conn(db_filename)
    if not conn:
        st.info("이력 정보를 불러올 수 없습니다.")
        return
    try:
        logs = pd.read_sql(
            """
            SELECT created_at, actor_username, field_name, old_value, new_value, reason
            FROM AuditLogs
            WHERE entity_type = 'Order' AND entity_id = ?
            ORDER BY created_at DESC
            """,
            conn,
            params=(int(order_id),),
        )
    except Exception:
        logs = pd.DataFrame()
    if len(logs) == 0:
        st.info("변경 이력이 없습니다.")
    else:
        for _, row in logs.iterrows():
            with st.expander(f"{row['created_at']} — {row['actor_username']} / {row['field_name']}"):
                st.write(f"필드: **{row['field_name']}**")
                st.write(f"변경 전: `{row['old_value']}`")
                st.write(f"변경 후: `{row['new_value']}`")
                st.write(f"사유: {row['reason']}")
    # 관련 결제 영수증 조회 (SQLite 전용)
    try:
        receipts = pd.read_sql(
            """
            SELECT pr.id, pr.file_path, pr.original_name, pr.uploaded_by, pr.uploaded_at, p.id AS payment_id, p.amount, p.payment_date
            FROM PaymentReceipts pr
            JOIN Payments p ON p.id = pr.payment_id
            WHERE p.order_id = ?
            ORDER BY pr.uploaded_at DESC
            """,
            conn,
            params=(int(order_id),),
        )
    except Exception:
        receipts = pd.DataFrame()
    finally:
        conn.close()
    if len(receipts) > 0:
        st.subheader("관련 결제 영수증")
        for _, r in receipts.iterrows():
            with st.expander(f"영수증 #{r['id']} — 결제ID {r['payment_id']} / {r['amount']:,.0f}원 / {r['uploaded_at']}"):
                st.write(f"업로더: {r['uploaded_by']}")
                st.write(f"원본 파일명: {r['original_name']}")
                if os.path.exists(r["file_path"]):
                    st.image(r["file_path"], caption=r["original_name"], use_column_width=True)


def render_payment_history_monitor():
    """매장 관리자/최고 관리자용 결제 변경/취소 모니터링 화면. Supabase app_payment_history 우선."""
    db_filename = st.session_state.get("current_db")
    if not db_filename:
        st.warning("매장에 로그인한 후 이용하세요.")
        return

    st.header("🚨 결제 변경/취소 모니터링")

    # ── 진단 패널 ──
    _has_ph_errors = bool(st.session_state.get("_ph_insert_errors"))
    with st.expander("🔧 Supabase 테이블 진단 (문제 발생 시 확인)", expanded=_has_ph_errors):
        _diag_supa = _supabase_orders_payments_available()
        st.write(f"**Supabase 연결 상태:** {'✅ 정상' if _diag_supa else '❌ 연결 실패'}")
        st.write(f"**현재 매장 DB:** `{db_filename}`")
        if _diag_supa:
            try:
                _sc, _ = get_supabase_client()
                _test_r = _sc.table("app_payment_history").select("id").eq("db_filename", db_filename).limit(1).execute()
                st.success(f"✅ app_payment_history 테이블 정상 접근 가능 (전체 행 확인 중...)")
                _cnt_r = _sc.table("app_payment_history").select("id", count="exact").eq("db_filename", db_filename).execute()
                _total = _cnt_r.count if hasattr(_cnt_r, "count") else len(_cnt_r.data or [])
                st.info(f"현재 매장 이력 총 {_total}건 저장됨")
            except Exception as _diag_e:
                st.error(f"❌ app_payment_history 접근 오류: {_diag_e}")
                st.warning("Supabase 대시보드 → SQL Editor에서 아래 SQL을 실행해 테이블을 재생성하세요.")
                st.code("""DROP TABLE IF EXISTS app_payment_history;
CREATE TABLE app_payment_history (
    id                  BIGSERIAL PRIMARY KEY,
    db_filename         TEXT NOT NULL,
    sale_id             BIGINT NOT NULL,
    customer_name       TEXT,
    action_type         TEXT NOT NULL,
    old_payment_data    JSONB,
    new_payment_data    JSONB,
    reason              TEXT NOT NULL,
    changed_by          TEXT NOT NULL,
    changed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    receipt_image_path  TEXT
);
CREATE INDEX idx_aph_db ON app_payment_history (db_filename);
CREATE INDEX idx_aph_sale ON app_payment_history (sale_id);
ALTER TABLE app_payment_history ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "allow_all_app_payment_history" ON app_payment_history;
CREATE POLICY "allow_all_app_payment_history" ON app_payment_history FOR ALL USING (true) WITH CHECK (true);""", language="sql")
        # 저장된 오류 메시지 표시
        _ph_errors_diag = st.session_state.get("_ph_insert_errors", [])
        if _ph_errors_diag:
            st.error(f"마지막 저장 오류: {_ph_errors_diag}")
            if st.button("오류 기록 초기화"):
                st.session_state["_ph_insert_errors"] = []
                st.rerun()

    # 저장 오류 팝업 (최근 저장 시 오류가 있었으면 상단에 표시 — pop하지 않고 유지하여 재진입 시에도 보임)
    _ph_errors = st.session_state.get("_ph_insert_errors", [])
    if _ph_errors:
        st.error(f"🚨 결제 이력 Supabase 저장 오류 {len(_ph_errors)}건 발생! 아래 '진단' 패널을 확인하세요.")
        for _err_msg in _ph_errors:
            st.warning(f"저장 실패: {_err_msg}")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        search_name = st.text_input("고객명 검색", key="ph_search_name")
    with col2:
        start_date = st.date_input("시작일", value=_today_kst() - timedelta(days=7), key="ph_start")
    with col3:
        end_date = st.date_input("종료일", value=_today_kst(), key="ph_end")
    with col4:
        action_filter = st.multiselect(
            "작업 유형",
            [
                "잔금결제",
                "결제취소",
                "재결제",
                "결제변경",
                "판매금액변경",  # 계약(판매) 금액 변경
                "계약변경",      # 기존 기록 호환용
                "금액변경",      # 구 버전 호환용
            ],
            default=[],
        )
    user_filter = st.text_input("작업자(직원 ID) 필터", key="ph_user_filter")

    df = pd.DataFrame()

    # ── Supabase 조회 (DB 레벨에서 날짜 범위 필터링) ──
    if _supabase_orders_payments_available():
        try:
            sc, err = get_supabase_client()
            if sc and not err:
                # Supabase TIMESTAMPTZ는 UTC 기준 — KST(UTC+9) 기준으로 하루 여유를 둬서 조회
                _start_iso = start_date.isoformat() + "T00:00:00"
                _end_iso   = end_date.isoformat()   + "T23:59:59"
                q = (
                    sc.table("app_payment_history")
                    .select("id, sale_id, customer_name, action_type, old_payment_data, new_payment_data, reason, changed_by, changed_at")
                    .eq("db_filename", db_filename)
                    .gte("changed_at", _start_iso)
                    .lte("changed_at", _end_iso)
                    .order("changed_at", desc=True)
                    .limit(2000)
                )
                r = q.execute()
                rows = r.data or []
                if rows:
                    df = pd.DataFrame(rows)
                    df = df.rename(columns={"id": "log_id"})
                    st.caption(f"📋 Supabase에서 {len(df):,}건 조회됨")
                else:
                    # 날짜 범위 내 데이터 없음 — 전체 건수도 확인
                    _total_r = sc.table("app_payment_history").select("id", count="exact").eq("db_filename", db_filename).execute()
                    _total = _total_r.count if hasattr(_total_r, "count") else len(_total_r.data or [])
                    if _total == 0:
                        st.warning("⚠️ app_payment_history 테이블에 데이터가 없습니다. 결제 변경/취소 작업이 발생하면 자동으로 기록됩니다.")
                    else:
                        st.info(f"선택한 날짜 범위({start_date} ~ {end_date})에 해당하는 이력이 없습니다. (전체 {_total:,}건 존재)")
        except Exception as e:
            st.error(f"Supabase 조회 오류: {e}")
            st.info("Supabase 연결에 문제가 있습니다. 아래 진단 패널을 확인하세요.")
    else:
        # ── SQLite 조회 (로컬) ──
        conn = get_tenant_conn(db_filename)
        if not conn:
            st.error("매장 DB를 찾을 수 없습니다.")
            return
        try:
            df = pd.read_sql(
                """
                SELECT ph.log_id, ph.sale_id, ph.customer_name,
                       ph.action_type, ph.old_payment_data, ph.new_payment_data,
                       ph.reason, ph.changed_by, ph.changed_at
                FROM PaymentHistory ph
                ORDER BY ph.changed_at DESC
                """,
                conn,
            )
        except Exception:
            df = pd.DataFrame()
        finally:
            conn.close()

    if df.empty:
        return

    df["changed_at_dt"] = pd.to_datetime(df["changed_at"], errors="coerce", utc=True)
    mask = pd.Series(True, index=df.index)

    if search_name:
        mask &= df["customer_name"].fillna("").str.contains(search_name.strip(), case=False)
    if action_filter:
        mask &= df["action_type"].isin(action_filter)
    if user_filter:
        mask &= df["changed_by"].fillna("").str.contains(user_filter.strip(), case=False)

    df_f = df.loc[mask].copy()
    # UTC → KST(+9) 변환 후 표시
    df_f["changed_at"] = (
        df_f["changed_at_dt"]
        .dt.tz_convert("Asia/Seoul")
        .dt.strftime("%Y-%m-%d %H:%M")
        .fillna(df_f["changed_at"].astype(str))
    )
    df_f = df_f.drop(columns=["changed_at_dt"])

    # username → 실명 변환
    _umap = {}
    try:
        _umap = _get_app_user_display_name_map()
    except Exception:
        pass
    df_f["입력자"] = df_f["changed_by"].apply(lambda v: _umap.get(str(v or "").strip()) or _umap.get(str(v or "").strip().lower()) or str(v or "-"))

    st.caption(f"총 {len(df_f):,}건 조회됨 (날짜 범위: {start_date} ~ {end_date})")

    def _highlight_cancel(row):
        if row.get("action_type") == "결제취소":
            return ["background-color: #ffe6e6; color: #b30000;"] * len(row)
        return ["" for _ in row]

    display_cols = [c for c in ["log_id", "changed_at", "customer_name", "action_type", "reason", "입력자", "sale_id"] if c in df_f.columns]
    st.dataframe(df_f[display_cols].style.apply(_highlight_cancel, axis=1), width='stretch')

    # 상세 내역 (old/new payment data) expander
    with st.expander("📋 결제 변경 상세 내역"):
        for _, row in df_f.head(50).iterrows():
            label = f"{row.get('changed_at', '')} | {row.get('customer_name', '-')} | {row.get('action_type', '')}"
            with st.expander(label):
                c_l, c_r = st.columns(2)
                with c_l:
                    st.markdown("**변경 전**")
                    old_data = row.get("old_payment_data") or {}
                    if isinstance(old_data, str):
                        try:
                            old_data = json.loads(old_data)
                        except Exception:
                            pass
                    st.json(old_data)
                with c_r:
                    st.markdown("**변경 후**")
                    new_data = row.get("new_payment_data") or {}
                    if isinstance(new_data, str):
                        try:
                            new_data = json.loads(new_data)
                        except Exception:
                            pass
                    st.json(new_data)
                st.caption(f"사유: {row.get('reason', '-')} | 작업자: {row.get('changed_by', '-')}")


def _create_auth_token(user_info: dict) -> str:
    """유저 정보 + 로그인 시간을 서명된 토큰으로. 브라우저 localStorage/URL 전달용."""
    now = time.time()
    payload = {
        "user_id": user_info.get("id"),
        "username": user_info.get("username"),
        "role": user_info.get("role"),
        "store_id": user_info.get("store_id"),
        "db_filename": user_info.get("db_filename"),
        "logged_at": now,
        "exp": now + AUTH_EXPIRY_DAYS * 24 * 3600,
    }
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
    sig = hmac.new(_get_auth_secret().encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def _verify_auth_token(token: str) -> dict | None:
    """토큰 검증 + 로그인 후 1시간 이내인지 확인. 통과 시 유저 정보 dict, 아니면 None."""
    if not token or "." not in token:
        return None
    try:
        payload_b64, sig = token.rsplit(".", 1)
        expected = hmac.new(_get_auth_secret().encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            return None
        pad = 4 - len(payload_b64) % 4
        if pad != 4:
            payload_b64 += "=" * pad
        raw = base64.urlsafe_b64decode(payload_b64)
        payload = json.loads(raw.decode())
        if payload.get("exp", 0) < time.time():
            return None
        # 로그인한 지 1시간 초과 시 세션 만료 (다중 새로고침 시에도 동일 기준)
        logged_at = payload.get("logged_at", 0)
        if (time.time() - logged_at) > AUTH_SESSION_SECONDS:
            return None
        return {
            "id": payload["user_id"],
            "username": payload["username"],
            "role": payload["role"],
            "store_id": payload.get("store_id"),
            "db_filename": payload.get("db_filename"),
        }
    except Exception:
        return None


def _try_restore_from_query_params():
    """URL의 auth가 있으면 검증(1시간 이내) 후 세션 복구. 복구 시 True."""
    try:
        q = st.query_params
    except Exception:
        return False
    auth = q.get("auth")
    if not auth:
        return False
    user = _verify_auth_token(auth)
    if not user:
        return False
    st.session_state.logged_in = True
    st.session_state.current_user = user
    st.session_state.current_db = user.get("db_filename") if user.get("role") != "superadmin" else None
    return True


def _inject_js_url_auth_save_and_replace_state():
    """
    최상단 스크립트: URL에 ?auth= 가 있으면 가장 먼저 실행.
    1) auth 값을 localStorage + sessionStorage에 즉시 저장
    2) history.replaceState로만 URL에서 ?auth= 제거 (리다이렉트/ pushState 사용 안 함 → Session History Skippable 경고 방지)
    """
    st.markdown(
        """
        <script>
        (function(){
            var params = new URLSearchParams(window.location.search);
            var auth = params.get("auth");
            if (auth) {
                var key = "emons_auth";
                localStorage.setItem(key, auth);
                sessionStorage.setItem(key, auth);
                console.log("--- 토큰 저장됨 ---");
                params.delete("auth");
                var cleanUrl = window.location.pathname + (params.toString() ? "?" + params.toString() : "");
                window.history.replaceState({}, "", cleanUrl);
            }
        })();
        </script>
        """,
        unsafe_allow_html=True,
    )


def _inject_js_localStorage_redirect_with_auth():
    """로그인 페이지: URL에 토큰이 없을 때만. localStorage(또는 sessionStorage)에 토큰이 있으면 ?auth= 붙여 한 번만 이동."""
    st.markdown(
        """
        <script>
        (function(){
            if (window.location.search.includes("auth=")) return;
            var key = "emons_auth";
            var val = localStorage.getItem(key);
            if (!val) { val = sessionStorage.getItem(key); if (val) { localStorage.setItem(key, val); } }
            if (val) {
                var u = new URL(window.location.href);
                u.searchParams.set("auth", val);
                window.location.replace(u.toString());
            }
        })();
        </script>
        """,
        unsafe_allow_html=True,
    )


def _inject_js_clear_auth_on_logout():
    """유일한 삭제 경로 1: 유저가 로그아웃 버튼을 클릭했을 때만 호출됨."""
    st.markdown(
        """
        <script>
        (function(){
            var key = "emons_auth";
            localStorage.removeItem(key);
            sessionStorage.removeItem(key);
            console.log("--- 토큰 삭제됨: 원인=로그아웃 버튼 클릭 ---");
            var u = new URL(window.location.href);
            u.searchParams.delete("logout");
            window.history.replaceState({}, "", u.toString());
        })();
        </script>
        """,
        unsafe_allow_html=True,
    )


def _inject_js_clear_auth_and_remove_auth_param():
    """유일한 삭제 경로 2: 1시간 만료 또는 서명 무효일 때만 호출됨. (URL 잘림 시에는 호출 안 함)"""
    st.markdown(
        """
        <script>
        (function(){
            var key = "emons_auth";
            localStorage.removeItem(key);
            sessionStorage.removeItem(key);
            console.log("--- 토큰 삭제됨: 원인=1시간 만료 또는 토큰 무효 ---");
            var u = new URL(window.location.href);
            u.searchParams.delete("auth");
            window.history.replaceState({}, "", u.toString());
        })();
        </script>
        """,
        unsafe_allow_html=True,
    )


def _maybe_clear_localStorage_on_logout():
    """URL에 logout=1이 있으면 localStorage 정리 스크립트 주입."""
    try:
        if st.query_params.get("logout") == "1":
            _inject_js_clear_auth_on_logout()
    except Exception:
        pass


# ========== 주소 검색 API 연동 (한국 주소) ==========
# 공공데이터 도로명주소 API / Vworld / 카카오 중 API 키 설정 시 사용 가능.
# KAKAO_REST_KEY: st.secrets["KAKAO_REST_KEY"] 또는 환경변수로 설정. 코드 내 하드코딩 금지.


def _get_kakao_rest_key() -> str:
    """카카오 REST API 키를 st.secrets → os.environ 순으로 조회. 없으면 빈 문자열 반환."""
    try:
        val = st.secrets.get("KAKAO_REST_KEY") or ""
        if val and str(val).strip():
            return str(val).strip()
    except Exception:
        pass
    return os.environ.get("KAKAO_REST_KEY", "")

def search_address_public(keyword: str, api_key: str = ""):
    """
    공공데이터포털 도로명주소 API (건축물대장 주소 조회 또는 도로명주소 API).
    API 키는 환경변수 ADDRESS_API_KEY 또는 인자로 전달.
    반환: [{"road_addr", "jibun_addr", "zip_code"}, ...]
    """
    if not keyword or not keyword.strip():
        return []
    key = api_key or os.environ.get("ADDRESS_API_KEY", "")
    if not key:
        return []
    url = "https://www.juso.go.kr/addrlink/addrLinkApi.do"
    params = {
        "confmKey": key,
        "currentPage": "1",
        "countPerPage": "10",
        "keyword": keyword,
        "resultType": "json",
    }
    try:
        r = requests.get(url, params=params, timeout=5)
        data = r.json()
        if data.get("results") and data["results"].get("juso"):
            juso_list = data["results"]["juso"]
            return [
                {
                    "road_addr": j.get("roadAddr", ""),
                    "jibun_addr": j.get("jibunAddr", ""),
                    "zip_code": j.get("zipNo", ""),
                }
                for j in juso_list
            ]
    except Exception:
        pass
    return []


def _kakao_local_error_hint(raw_msg: str) -> str:
    """로컬 API 비활성화 등 카카오 콘솔 설정 오류 시 안내 문구 추가."""
    if not raw_msg:
        return raw_msg
    raw_lower = raw_msg.lower()
    if "open_map_and_local" in raw_lower or ("disabled" in raw_lower and "service" in raw_lower):
        return (
            "카카오 앱에서 [지도/로컬] 서비스가 꺼져 있습니다. "
            "developers.kakao.com → 내 애플리케이션 → 해당 앱 → [앱 설정] → [앱 키] 아래 또는 [제품 설정]에서 "
            "[카카오맵] 또는 [로컬 API] 사용 설정을 [ON]으로 바꿔 주세요."
        )
    return raw_msg


def search_address_kakao(keyword: str, api_key: str = ""):
    """
    카카오 로컬 API - 주소 검색.
    GET https://dapi.kakao.com/v2/local/search/address.json
    헤더: Authorization: KakaoAK {KAKAO_REST_KEY}
    반환: ( [{"address_name": "서울 강남구 ..."}, ...], None ) 또는 ( [], "오류메시지" )
    """
    if not keyword or not keyword.strip():
        return [], None
    key = api_key or _get_kakao_rest_key()
    if not key:
        return [], "KAKAO_REST_KEY가 설정되지 않았습니다. st.secrets 또는 환경변수에 KAKAO_REST_KEY를 추가해 주세요."
    url = "https://dapi.kakao.com/v2/local/search/address.json"
    headers = {"Authorization": f"KakaoAK {key}"}
    params = {"query": keyword.strip()}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        try:
            data = r.json() if r.content else {}
        except Exception:
            data = {}
        # API 오류 응답 (401, 403 등): 카카오는 code, msg 등 반환
        if r.status_code != 200:
            err_msg = data.get("msg") or data.get("message") or f"API 오류 (HTTP {r.status_code})"
            return [], _kakao_local_error_hint(err_msg)
        if data.get("errorType") or data.get("error"):
            raw = data.get("message") or data.get("msg") or "API 오류"
            return [], _kakao_local_error_hint(raw)
        docs = data.get("documents", [])
        # address_name(전체 주소), road_address.building_name(건물명), address.bname(법정동명) 수집
        result = []
        for d in docs[:15]:
            addr = d.get("address_name")
            if not addr or not isinstance(addr, str):
                continue
            road = d.get("road_address") or {}
            addr_obj = d.get("address") or {}
            building_name = (road.get("building_name") or "").strip() or None
            bname = (addr_obj.get("bname") or "").strip() or None
            result.append({
                "address_name": addr,
                "building_name": building_name,
                "bname": bname,
            })
        return result, None
    except requests.exceptions.RequestException as e:
        return [], f"연결 오류: {str(e)[:80]}"
    except Exception as e:
        return [], f"오류: {str(e)[:80]}"


def search_keyword_kakao(keyword: str, api_key: str = ""):
    """
    카카오 로컬 API - 키워드 검색(장소/건물명).
    GET https://dapi.kakao.com/v2/local/search/keyword.json
    반환: ( [{"place_name", "address_name", "road_address_name", "x", "y", "source": "keyword"}, ...], None ) 또는 ( [], "오류메시지" )
    """
    if not keyword or not str(keyword).strip():
        return [], None
    key = api_key or _get_kakao_rest_key()
    if not key:
        return [], "KAKAO_REST_KEY가 설정되지 않았습니다. st.secrets 또는 환경변수에 KAKAO_REST_KEY를 추가해 주세요."
    url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    headers = {"Authorization": f"KakaoAK {key}"}
    params = {"query": keyword.strip(), "size": 15}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=10)
        try:
            data = r.json() if r.content else {}
        except Exception:
            data = {}
        if r.status_code != 200:
            err_msg = data.get("msg") or data.get("message") or f"API 오류 (HTTP {r.status_code})"
            return [], _kakao_local_error_hint(err_msg)
        if data.get("errorType") or data.get("error"):
            raw = data.get("message") or data.get("msg") or "API 오류"
            return [], _kakao_local_error_hint(raw)
        docs = data.get("documents", [])
        result = []
        for d in docs:
            addr = (d.get("address_name") or d.get("road_address_name") or "").strip()
            place = (d.get("place_name") or "").strip()
            if not addr and not place:
                continue
            x, y = d.get("x"), d.get("y")
            result.append({
                "place_name": place or None,
                "address_name": addr or place,
                "road_address_name": (d.get("road_address_name") or "").strip() or None,
                "x": x,
                "y": y,
                "source": "keyword",
            })
        return result, None
    except requests.exceptions.RequestException as e:
        return [], f"연결 오류: {str(e)[:80]}"
    except Exception as e:
        return [], f"오류: {str(e)[:80]}"


def geocode_address_kakao(address: str):
    """주소 문자열을 카카오 API로 지오코딩하여 (lat, lon) 반환. 실패 시 None."""
    ext = geocode_address_kakao_extended(address)
    return (ext["latitude"], ext["longitude"]) if ext else None


def geocode_address_kakao_extended(address: str) -> dict | None:
    """
    주소 문자열을 카카오 API로 지오코딩하여 좌표 + 건물명/법정동명 반환.
    반환: {"latitude", "longitude", "address", "building_name", "bname"} 또는 None.
    """
    if not address or not str(address).strip():
        return None
    q = str(address).strip()
    key = _get_kakao_rest_key()
    if not key:
        return None
    url = "https://dapi.kakao.com/v2/local/search/address.json"
    headers = {"Authorization": f"KakaoAK {key}"}
    params = {"query": q}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=5)
        if r.status_code != 200:
            return None
        data = r.json() if r.content else {}
        docs = data.get("documents", [])
        if not docs:
            return None
        d = docs[0]
        x, y = d.get("x"), d.get("y")
        if x is None or y is None:
            return None
        road = d.get("road_address") or {}
        addr_obj = d.get("address") or {}
        building_name = (road.get("building_name") or "").strip() or None
        bname = (addr_obj.get("bname") or "").strip() or None
        addr_name = (d.get("address_name") or q) or ""
        return {
            "latitude": float(y),
            "longitude": float(x),
            "address": addr_name,
            "building_name": building_name,
            "bname": bname,
        }
    except Exception:
        return None


def address_search(keyword: str):
    """
    주소 검색 통합: 환경변수에 따라 공공데이터 또는 카카오 시도.
    API 키가 없으면 빈 리스트 반환 후 수동 입력 유도.
    """
    results = search_address_public(keyword)
    if results:
        return results
    results, _ = search_address_kakao(keyword)
    return results


def _address_search_dialog_impl():
    """주소 검색 모달 본문. 검색 후 선택 시 address_manual에 반영하고 닫힘. st.form으로 엔터키 검색 지원."""
    with st.form("address_search_form", clear_on_submit=False):
        kw = st.text_input("주소 검색어", placeholder="예: 역삼동 123, 테헤란로")
        submitted = st.form_submit_button("검색")
    if submitted:
        if kw and kw.strip():
            results_addr, err_addr = search_address_kakao(kw.strip())
            results_kw, err_kw = search_keyword_kakao(kw.strip())
            combined = []
            seen = set()
            for r in (results_addr or []):
                addr = r.get("address_name") or ""
                if addr and addr not in seen:
                    seen.add(addr)
                    r["source"] = "address"
                    combined.append(r)
            for r in (results_kw or []):
                addr = r.get("address_name") or ""
                if addr and addr not in seen:
                    seen.add(addr)
                    combined.append(r)
            st.session_state._dialog_addr_results = combined
            st.session_state._dialog_addr_error = err_addr if not combined and err_addr else None
        else:
            st.warning("검색어를 입력하세요.")
    if st.session_state.get("_dialog_addr_error"):
        st.error(st.session_state._dialog_addr_error)
    results = st.session_state.get("_dialog_addr_results") or []
    if results:
        options = []
        display_to_address = {}
        for r in results:
            addr = r.get("address_name") or ""
            if r.get("source") == "keyword":
                place = (r.get("place_name") or "").strip()
                disp = f"장소: {place} — {addr}" if place else addr
            else:
                building_name = (r.get("building_name") or "").strip()
                bname = (r.get("bname") or "").strip()
                disp = f"[{building_name}] {addr}" if building_name else (f"[{bname}] {addr}" if bname else addr)
            options.append(disp)
            display_to_address[disp] = addr
        chosen = st.selectbox("검색 결과에서 주소 선택", options, key="dialog_addr_select")
        if st.button("선택 완료", key="dialog_addr_confirm"):
            if chosen:
                st.session_state["address_manual"] = display_to_address.get(chosen, chosen)
            for k in ("_dialog_addr_results", "_dialog_addr_error", "_show_address_dialog"):
                st.session_state.pop(k, None)
            st.rerun()


# st.dialog 데코레이터 (Streamlit 1.33+). 없으면 expander 폴백
if hasattr(st, "dialog"):

    @st.dialog("주소 검색", width="medium")
    def _address_search_dialog():
        _address_search_dialog_impl()
else:

    def _address_search_dialog():
        with st.expander("📍 주소 검색", expanded=True):
            _address_search_dialog_impl()


# ========== 로그인 페이지 ==========

def _inject_js_login_form_attributes():
    """로그인 폼 input에 id, name, autocomplete 부여. 빈 autocomplete는 'off'로 수정 (유효하지 않은 빈 값 경고 해소)."""
    st.markdown(
        """
        <script>
        (function(){
            function fixAutocomplete() {
                var form = document.querySelector('form[data-testid="stForm"]') || document.querySelector('form');
                if (form) {
                    var inputs = form.querySelectorAll('input:not([type="hidden"])');
                    if (inputs.length >= 2 && !inputs[0].getAttribute('name')) {
                        inputs[0].setAttribute('id', 'emons-username');
                        inputs[0].setAttribute('name', 'username');
                        inputs[0].setAttribute('autocomplete', 'username');
                        inputs[1].setAttribute('id', 'emons-password');
                        inputs[1].setAttribute('name', 'password');
                        inputs[1].setAttribute('autocomplete', 'current-password');
                    }
                }
                document.querySelectorAll('input:not([type="hidden"])').forEach(function(inp) {
                    var ac = inp.getAttribute('autocomplete');
                    if (ac === '' || ac === null) inp.setAttribute('autocomplete', 'off');
                });
            }
            function run() { fixAutocomplete(); setTimeout(fixAutocomplete, 150); }
            if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run);
            else run();
        })();
        </script>
        """,
        unsafe_allow_html=True,
    )


def render_login():
    ensure_session()
    # 이메일 자동 입력: URL의 email 파라미터 또는 localStorage(아래 스크립트에서 리다이렉트)로 복원
    try:
        default_email = (st.query_params.get("email") or "").strip()
    except Exception:
        default_email = ""
    # 로그인 화면 로고 전용 CSS: @media로 모바일/PC 구분
    st.markdown(
        """
        <style>
        /* 로그인 화면 emons 로고: PC — 적당한 크기로 제한 */
        .emons-login-logo img {
            max-width: 350px !important;
            width: auto !important;
            height: auto !important;
            object-fit: contain;
        }
        @media (max-width: 768px) {
            /* 모바일: 상단 여백으로 잘림 방지, 화면 너비의 60% 크기 */
            .emons-login-logo {
                margin-top: 1.5rem;
                padding-top: 1rem;
                box-sizing: border-box;
            }
            .emons-login-logo img {
                width: 60% !important;
                max-width: 60% !important;
                height: auto !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    # 로고: 로그인 화면에서도 좌측 상단 고정 (공통 레이아웃), 에러 시 빨간 메시지
    logo_html = (
        '<div class="emons-login-logo">'
        + _common_logo_html(_resolve_logo_path(), fallback_id="emons-logo-fallback-login")
        + "</div>"
    )
    st.markdown(logo_html, unsafe_allow_html=True)
    st.title("momo")
    st.subheader("로그인")
    with st.form("login_form"):
        email = st.text_input("이메일", value=default_email, key="login_email", type="default", placeholder="예: you@example.com")
        password = st.text_input("비밀번호", type="password", key="login_password")
        submitted = st.form_submit_button("로그인")
        if submitted:
            if not (email and str(email).strip()):
                st.error("이메일을 입력해 주세요.")
            elif not password:
                st.error("비밀번호를 입력해 주세요.")
            elif len(password) < 6:
                st.error("비밀번호는 6자 이상이어야 합니다.")
            else:
                client, err = get_supabase_client()
                if err:
                    st.error(f"⚠️ {err}")
                else:
                    try:
                        response = client.auth.sign_in_with_password({
                            "email": str(email).strip(),
                            "password": password,
                        })
                        session = response.session
                        user = response.user
                        if not session or not user:
                            st.error("이메일 또는 비밀번호가 올바르지 않습니다.")
                        else:
                            app_user = get_app_user_by_email(user.email)
                            if not app_user:
                                st.error("이 이메일은 등록된 사용자가 아닙니다. 관리자에게 문의하세요.")
                            else:
                                user_id, uname, role, store_id, db_filename = app_user
                                allowed_stores = get_user_allowed_stores(user_id) if role != "superadmin" else []
                                if allowed_stores:
                                    # 기본 매장(app_users.store_id)이 allowed_stores에 있으면 우선 적용
                                    _matched_store = next((s for s in allowed_stores if s[0] == store_id), None)
                                    if _matched_store:
                                        store_id, db_filename = _matched_store[0], _matched_store[1]
                                    else:
                                        # 기본 매장이 없거나 배정 목록과 불일치하면 첫 매장으로 fallback
                                        store_id, db_filename = allowed_stores[0][0], allowed_stores[0][1]
                                display_map = _get_app_user_display_name_map()
                                display_name = (display_map.get(str(uname).strip()) or display_map.get(str(uname).strip().lower()) or uname or "").strip()
                                st.session_state.logged_in = True
                                st.session_state.current_user = {
                                    "id": user_id, "username": uname, "name": display_name or None, "role": role,
                                    "store_id": store_id, "db_filename": db_filename,
                                    "allowed_stores": allowed_stores,
                                }
                                st.session_state.current_db = db_filename if role != "superadmin" else None
                                st.session_state["supabase_session"] = {
                                    "access_token": session.access_token,
                                    "refresh_token": session.refresh_token,
                                }
                                # 성공 시 다음 접속을 위해 이메일을 브라우저에 저장할 예정(메인 로드 시 스크립트로 저장)
                                st.session_state["_pending_save_login_email"] = str(email).strip()
                                st.rerun()
                    except Exception as e:
                        # 디버깅용: 어디에서 오류가 나는지 터미널에 전체 스택을 출력
                        traceback.print_exc()
                        err_msg = str(e).strip() or "로그인에 실패했습니다."
                        if "Invalid login" in err_msg or "invalid" in err_msg.lower():
                            st.error("이메일 또는 비밀번호가 올바르지 않습니다.")
                        else:
                            st.error(f"로그인 중 오류가 발생했습니다: {err_msg}")

    st.caption("💡 로그인할 때 사용한 이메일은 이 기기(브라우저)에만 저장되며, 다음 로그인 시 자동으로 채워집니다.")

    # 이메일 자동 저장/자동 입력: localStorage 사용 (같은 브라우저에서 다음 로그인 시 이메일 유지)
    # 1) URL에 email이 없고 localStorage에 저장된 이메일이 있으면 ?email= 붙여서 이동 → 서버에서 default value로 채움
    # 2) 로그인 성공 시에는 메인 화면 로드 시 한 번만 localStorage에 저장(위에서 _pending_save_login_email 설정)
    st.markdown(
        """
        <script>
        (function(){
            var KEY = 'emons_login_email';
            try {
                var u = new URL(window.location.href);
                if (!u.searchParams.get('email') && localStorage.getItem(KEY)) {
                    u.searchParams.set('email', localStorage.getItem(KEY));
                    window.location.replace(u.toString());
                    return;
                }
            } catch(e) {}
            function attachSaveOnLoginClick() {
                var forms = document.querySelectorAll('form');
                for (var i = 0; i < forms.length; i++) {
                    var form = forms[i];
                    var btn = form.querySelector('button[kind="primary"], button');
                    if (!btn || btn.textContent.trim() !== '로그인') continue;
                    var inputs = form.querySelectorAll('input:not([type="password"])');
                    var emailInput = inputs[0];
                    if (!emailInput) continue;
                    btn.removeEventListener('click', _saveEmail);
                    btn.addEventListener('click', _saveEmail);
                    function _saveEmail() {
                        var val = (emailInput.value || '').trim();
                        if (val) try { localStorage.setItem(KEY, val); } catch(e) {}
                    }
                    break;
                }
            }
            if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', attachSaveOnLoginClick);
            else setTimeout(attachSaveOnLoginClick, 300);
        })();
        </script>
        """,
        unsafe_allow_html=True,
    )
    # 로그인 폼 input에 autocomplete 등 속성 최적화 (브라우저 자동 완성 동작)
    _inject_js_login_form_attributes()


# ========== 최고 관리자 (Superadmin) 전용: 공지 조회 ==========

def get_store_display_name(user):
    """로그인 사용자에 따른 사이드바용 매장명. superadmin이면 본사, 아니면 Supabase app_stores에서 조회."""
    if user.get("role") == "superadmin":
        return "🏢 에몬스울산본점"
    store_id = user.get("store_id")
    if not store_id:
        return "🏢 매장"
    client, err = get_supabase_client()
    if err or not client:
        return "🏢 매장"
    try:
        r = client.table("app_stores").select("store_name").eq("id", int(store_id)).maybe_single().execute()
        data = r.data if isinstance(r.data, dict) else (r.data[0] if r.data and len(r.data) else None)
        return f"🏢 {data['store_name']}" if data and data.get("store_name") else "🏢 매장"
    except Exception:
        return "🏢 매장"


def get_latest_active_notice():
    """Notices 테이블에서 is_active=1인 최신 공지 1건 메시지 반환. 없으면 None. (레거시 호환)"""
    conn = get_master_conn()
    try:
        row = conn.execute(
            "SELECT content, message FROM Notices WHERE is_active = 1 ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        return (row[0] or row[1] or "").strip() or None
    finally:
        conn.close()


def get_recent_notices(limit: int = 5):
    """최근 공지사항 limit건 반환. (id, title, content, external_link, created_at)"""
    conn = get_master_conn()
    try:
        df = pd.read_sql(
            """
            SELECT id, title, content, external_link, message, created_at
            FROM Notices
            WHERE is_active = 1
            ORDER BY created_at DESC
            LIMIT ?
            """,
            conn,
            params=(limit,),
        )
        return df
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def _render_recent_notices_section():
    """전사 공지사항 섹션: 최근 3~5건을 expander로 표시, 외부 링크는 새 창에서 열기."""
    notices = get_recent_notices(5)
    if len(notices) == 0:
        return
    st.subheader("📌 최근 공지사항")
    for _, row in notices.iterrows():
        title = (row.get("title") or "").strip() or "(제목 없음)"
        content = (row.get("content") or row.get("message") or "").strip()
        external_link = (row.get("external_link") or "").strip()
        created = (row.get("created_at") or "")[:10]
        label = f"{title} ({created})"
        with st.expander(label):
            if content:
                st.write(content)
            if external_link:
                st.markdown(
                    f'<a href="{html.escape(external_link)}" target="_blank" rel="noopener noreferrer" '
                    'style="display:inline-block;padding:0.4rem 0.8rem;margin-top:0.5rem;'
                    'background:#1f77b4;color:white;border-radius:0.4rem;text-decoration:none;'
                    'font-weight:500;">🔗 링크 열기 (새 창)</a>',
                    unsafe_allow_html=True,
                )


# ========== 마케팅 인사이트 (Plotly 4종 차트) ==========

def extract_region(address):
    """
    도로명 주소 환경에 맞춘 지역명 추출 (3단계 Fallback).
    1순위: 주소 끝 괄호 안의 '동/읍/면' (예: "... (삼산동)" -> "삼산동")
    2순위: 주요 도로명 '로/길' (예: "... 삼산로 123" -> "삼산로")
    3순위: '구/군' (예: "... 남구 ..." -> "남구")
    """
    if not isinstance(address, str):
        return "기타"
    s = address.strip()
    if not s:
        return "기타"
    # 1순위: 괄호 안의 동/읍/면
    match1 = re.search(r"\(([가-힣]+[동읍면])\)", s)
    if match1:
        return match1.group(1)
    # 2순위: 로/길
    match2 = re.search(r"([가-힣]+[로길])\b", s)
    if match2:
        return match2.group(1)
    # 3순위: 구/군
    match3 = re.search(r"([가-힣]+[구군])\b", s)
    if match3:
        return match3.group(1)
    return "기타 지역"


def _render_marketing_insights_charts(orders: pd.DataFrame, customers: pd.DataFrame, period_label: str = None):
    """orders + customers 로 4종 Plotly 차트 2x2 배치. period_label 있으면 차트 상단에 표시."""
    if orders is None or len(orders) == 0:
        st.info("데이터가 충분하지 않습니다.")
        return
    merged = orders.merge(customers[["id", "address"]], left_on="customer_id", right_on="id", how="left")
    merged["region"] = merged["address"].map(extract_region)
    merged["total_amount"] = merged["total_amount"].fillna(0).astype(float)

    if period_label:
        st.caption(f"📅 {period_label}")
    st.subheader("🎯 데이터 기반 마케팅 분석")
    c1, c2 = st.columns(2)

    with c1:
        region_sales = merged.groupby("region", as_index=False)["total_amount"].sum()
        region_sales = region_sales[region_sales["total_amount"] > 0]
        if len(region_sales) == 0:
            st.info("데이터가 충분하지 않습니다.")
        else:
            region_sales["text"] = region_sales["total_amount"].apply(lambda x: f"{x:,.0f}원")
            fig1 = px.pie(region_sales, values="total_amount", names="region", title="① 지역별 매출 분포 (Pie Chart)")
            fig1.update_traces(textposition="inside", textinfo="percent+label", hovertemplate="%{label}<br>매출: %{value:,.0f}원<br>비중: %{percent}<extra></extra>")
            fig1.update_layout(margin=dict(t=40, b=20, l=20, r=20), height=320)
            st.plotly_chart(fig1, width='stretch')

    with c2:
        visit_counts = merged["visit_reason"].fillna("미기입").value_counts().reset_index()
        visit_counts.columns = ["visit_reason", "count"]
        if len(visit_counts) == 0 or visit_counts["count"].sum() == 0:
            st.info("데이터가 충분하지 않습니다.")
        else:
            visit_counts["text"] = visit_counts["count"].apply(lambda x: f"{x:,}건")
            fig2 = go.Figure(data=[go.Pie(labels=visit_counts["visit_reason"], values=visit_counts["count"], hole=0.5, textinfo="percent+label")])
            fig2.update_traces(hovertemplate="%{label}<br>건수: %{value:,}건<extra></extra>")
            fig2.update_layout(title="② 방문 경로 분석", margin=dict(t=40, b=20, l=20, r=20), height=320, showlegend=True)
            st.plotly_chart(fig2, width='stretch')

    with c1:
        purchase_sales = merged.groupby("purchase_reason", as_index=False)["total_amount"].sum()
        purchase_sales = purchase_sales[purchase_sales["purchase_reason"].notna() & (purchase_sales["purchase_reason"] != "")]
        if len(purchase_sales) == 0:
            st.info("데이터가 충분하지 않습니다.")
        else:
            purchase_sales = purchase_sales.sort_values("total_amount", ascending=True)
            purchase_sales["amt_str"] = purchase_sales["total_amount"].apply(lambda x: f"{x:,.0f}원")
            fig3 = px.bar(purchase_sales, x="total_amount", y="purchase_reason", orientation="h", title="③ 구매 이유별 총 판매 금액")
            fig3.update_traces(hovertemplate="%{y}<br>총액: %{x:,.0f}원<extra></extra>")
            fig3.update_layout(margin=dict(t=40, b=20, l=20, r=20), height=320, xaxis_title="총 판매 금액(원)", yaxis_title="")
            st.plotly_chart(fig3, width='stretch')

    with c2:
        cats = merged["category"].fillna("").str.split(",").explode()
        cats = cats.str.strip()
        cats = cats[cats != ""]
        cat_counts = cats.value_counts().reset_index()
        cat_counts.columns = ["category", "count"]
        if len(cat_counts) == 0:
            st.info("데이터가 충분하지 않습니다.")
        else:
            cat_counts = cat_counts.sort_values("count", ascending=True)
            fig4 = px.bar(cat_counts, x="count", y="category", orientation="h", title="④ 카테고리별 인기 품목(판매 횟수)")
            fig4.update_traces(hovertemplate="%{y}<br>판매 횟수: %{x:,}건<extra></extra>")
            fig4.update_layout(margin=dict(t=40, b=20, l=20, r=20), height=320, xaxis_title="판매 횟수", yaxis_title="")
            st.plotly_chart(fig4, width='stretch')


# 지도 기본 중심/줌 (한국)
_MAP_CENTER = (36.5, 127.5)
_MAP_ZOOM = 7


def _build_map_data_with_geocoding(merged: pd.DataFrame) -> pd.DataFrame:
    """merged(orders+customers)에서 주소 지오코딩 후 latitude, longitude, address, building_name, 고객명, 품목, 금액, 배송일자 포함 DataFrame 반환.
    app_customers에 latitude, longitude가 있으면 우선 사용(지오코딩 스킵)."""
    def _safe_strip_text(v) -> str:
        try:
            if v is None or pd.isna(v):
                return ""
        except Exception:
            if v is None:
                return ""
        return str(v).strip()

    if "address" not in merged.columns or "name" not in merged.columns:
        return pd.DataFrame()
    if "geo_cache" not in st.session_state:
        st.session_state.geo_cache = {}
    cache = st.session_state.geo_cache
    rows = []
    for _, row in merged.iterrows():
        # app_customers에 latitude, longitude가 있으면 활용 (유효한 숫자일 때만)
        lat_val, lon_val = row.get("latitude"), row.get("longitude")
        try:
            if pd.notna(lat_val) and pd.notna(lon_val) and -90 <= float(lat_val) <= 90 and -180 <= float(lon_val) <= 180:
                lat, lon = float(lat_val), float(lon_val)
                addr_display = _safe_strip_text(row.get("address")) or "-"
                rows.append({
                    "latitude": lat, "longitude": lon, "address": addr_display,
                    "building_name": None, "bname": None,
                    "customer_name": row.get("name") or "-", "category": row.get("category") or "-",
                    "total_amount": int(row.get("total_amount") or 0),
                    "delivery_date": str(row.get("delivery_date") or row.get("order_date") or "-")[:10],
                })
                continue
        except (ValueError, TypeError):
            pass
        addr = _safe_strip_text(row.get("address"))
        if not addr:
            continue
        if addr in cache:
            ent = cache[addr]
            lat, lon = ent["latitude"], ent["longitude"]
            addr_display = ent.get("address") or addr
            building_name = ent.get("building_name")
            bname = ent.get("bname")
        else:
            ext = geocode_address_kakao_extended(addr)
            if ext is None:
                continue
            lat, lon = ext["latitude"], ext["longitude"]
            addr_display = ext.get("address") or addr
            building_name = ext.get("building_name")
            bname = ext.get("bname")
            cache[addr] = {"latitude": lat, "longitude": lon, "address": addr_display, "building_name": building_name, "bname": bname}
        rows.append({
            "latitude": lat,
            "longitude": lon,
            "address": addr_display,
            "building_name": building_name,
            "bname": bname,
            "customer_name": row.get("name") or "-",
            "category": row.get("category") or "-",
            "total_amount": int(row.get("total_amount") or 0),
            "delivery_date": str(row.get("delivery_date") or row.get("order_date") or "-")[:10],
        })
    if not rows:
        return pd.DataFrame(columns=["latitude", "longitude", "address", "building_name", "bname", "customer_name", "category", "total_amount", "delivery_date"])
    return pd.DataFrame(rows)


# 말풍선(팝업) 가독성: 모바일에서 글자 크기·너비 조정 (Folium Popup 내부 스타일)
_MAP_POPUP_STYLE = (
    "font-size:14px;line-height:1.4;max-width:min(280px,85vw);padding:6px 8px;"
    "font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;"
)


def _create_folium_map(df: pd.DataFrame, center: tuple, zoom_start: int, key: str = "") -> "folium.Map | None":
    """DataFrame(latitude, longitude, address, building_name, customer_name, category, total_amount, delivery_date)로 Folium 지도 생성. 말풍선 상단에 [건물명] 굵게 표시."""
    if not FOLIUM_AVAILABLE or df.empty:
        return None
    m = folium.Map(location=center, zoom_start=zoom_start, tiles="OpenStreetMap")
    mc = MarkerCluster(name="판매 현황")
    for _, r in df.iterrows():
        lat, lon = r["latitude"], r["longitude"]
        if pd.isna(lat) or pd.isna(lon):
            continue
        building_name = r.get("building_name") or r.get("bname")
        try:
            addr = "" if (r.get("address") is None or pd.isna(r.get("address"))) else str(r.get("address")).strip()
        except Exception:
            addr = str(r.get("address") or "").strip()
        line1 = ""
        if building_name:
            line1 = f"<strong>[{html.escape(str(building_name))}]</strong><br>"
        line_addr = f"<b>주소:</b> {html.escape(addr or '-')}<br>" if addr else ""
        popup_html = (
            f'<div class="map-popup" style="{_MAP_POPUP_STYLE}">'
            f"{line1}"
            f"{line_addr}"
            f"<b>고객명:</b> {html.escape(str(r.get('customer_name', '-')))}<br>"
            f"<b>품목:</b> {html.escape(str(r.get('category', '-')))}<br>"
            f"<b>금액:</b> {int(r.get('total_amount',0)):,.0f}원<br>"
            f"<b>배송일:</b> {html.escape(str(r.get('delivery_date', '-')))}"
            f"</div>"
        )
        folium.Marker([lat, lon], popup=folium.Popup(popup_html, max_width=320)).add_to(mc)
    mc.add_to(m)
    return m


def _render_regional_sales_map_section(merged: pd.DataFrame, key_prefix: str = "map"):
    """지역별 판매 현황 지도: 단일 연도 / 연도별 비교 모드. merged는 orders+customers 조인된 DataFrame (name, address 포함)."""
    if not FOLIUM_AVAILABLE:
        st.info("지도 기능을 사용하려면 `folium`과 `streamlit-folium` 패키지를 설치해 주세요.")
        return
    merged["order_date"] = pd.to_datetime(merged["order_date"], errors="coerce")
    merged = merged[merged["order_date"].notna()]
    merged["order_year"] = merged["order_date"].dt.year.astype(int)
    years = sorted(merged["order_year"].unique().tolist(), reverse=True)
    if not years:
        st.info("지도에 표시할 판매 데이터가 없습니다.")
        return
    year_options = ["전체"] + [str(y) for y in years]
    mode = st.radio(
        "지도 조회 모드",
        ["단일 연도 보기", "연도별 비교 보기"],
        horizontal=True,
        key=f"{key_prefix}_mode",
    )
    if mode == "단일 연도 보기":
        sel_year = st.selectbox("조회 연도", year_options, key=f"{key_prefix}_single_year")
        if sel_year == "전체":
            df_filtered = merged
        else:
            df_filtered = merged[merged["order_year"] == int(sel_year)]
        if len(df_filtered) == 0:
            st.info(f"{sel_year}년 데이터가 없습니다.")
            return
        map_data = _build_map_data_with_geocoding(df_filtered)
        if map_data.empty or "latitude" not in map_data.columns or "longitude" not in map_data.columns:
            st.info("지도에 표시할 위치 데이터(위도/경도)가 존재하지 않습니다.")
            return
        map_data = map_data.dropna(subset=["latitude", "longitude"])
        if map_data.empty:
            st.info("지오코딩 가능한 주소 데이터가 없습니다. (KAKAO_REST_KEY 확인)")
            return
        m = _create_folium_map(map_data, _MAP_CENTER, _MAP_ZOOM, f"{key_prefix}_single")
        if m:
            st_folium(m, returned_objects=[], use_container_width=True, key=f"{key_prefix}_map_single")
    else:
        col1, col2 = st.columns(2)
        year1_opts = [str(y) for y in years]
        year2_opts = [str(y) for y in years]
        with col1:
            y1 = st.selectbox("비교 연도 1", year1_opts, key=f"{key_prefix}_y1")
        with col2:
            y2 = st.selectbox("비교 연도 2", year2_opts, key=f"{key_prefix}_y2")
        df1 = merged[merged["order_year"] == int(y1)]
        df2 = merged[merged["order_year"] == int(y2)]
        with col1:
            st.caption(f"📅 {y1}년")
            if len(df1) == 0:
                st.info(f"{y1}년 데이터 없음")
            else:
                md1 = _build_map_data_with_geocoding(df1)
                if md1.empty or "latitude" not in md1.columns or "longitude" not in md1.columns:
                    st.info("지도에 표시할 위치 데이터(위도/경도)가 존재하지 않습니다.")
                else:
                    md1 = md1.dropna(subset=["latitude", "longitude"])
                    if md1.empty:
                        st.info("지오코딩 가능한 주소 없음")
                    else:
                        m1 = _create_folium_map(md1, _MAP_CENTER, _MAP_ZOOM, f"{key_prefix}_m1")
                        if m1:
                            st_folium(m1, returned_objects=[], use_container_width=True, key=f"{key_prefix}_map_left")
        with col2:
            st.caption(f"📅 {y2}년")
            if len(df2) == 0:
                st.info(f"{y2}년 데이터 없음")
            else:
                md2 = _build_map_data_with_geocoding(df2)
                if md2.empty or "latitude" not in md2.columns or "longitude" not in md2.columns:
                    st.info("지도에 표시할 위치 데이터(위도/경도)가 존재하지 않습니다.")
                else:
                    md2 = md2.dropna(subset=["latitude", "longitude"])
                    if md2.empty:
                        st.info("지오코딩 가능한 주소 없음")
                    else:
                        m2 = _create_folium_map(md2, _MAP_CENTER, _MAP_ZOOM, f"{key_prefix}_m2")
                        if m2:
                            st_folium(m2, returned_objects=[], use_container_width=True, key=f"{key_prefix}_map_right")


def _render_marketing_multi_period_comparison(
    merged_all: pd.DataFrame,
    range_start_a: date,
    range_end_a: date,
    range_start_b: date,
    range_end_b: date,
    key_prefix: str,
):
    """
    다중 기간 교차 분석: 기간 A vs 기간 B로 4대 마케팅 지표를 좌우 비교.
    merged_all에는 order_date, total_amount, visit_reason, purchase_reason, category, name, address 필요.
    모든 st.plotly_chart / st.dataframe / st_folium에 key_prefix 기반 고유 key 부여.
    """
    merged_all = merged_all.copy()
    merged_all["order_date"] = pd.to_datetime(merged_all["order_date"], errors="coerce")
    merged_all = merged_all[merged_all["order_date"].notna()]
    merged_all["_dt"] = merged_all["order_date"].dt.date
    merged_all["total_amount"] = merged_all["total_amount"].fillna(0).astype(float)

    df_period_a = merged_all[(merged_all["_dt"] >= range_start_a) & (merged_all["_dt"] <= range_end_a)].copy()
    df_period_b = merged_all[(merged_all["_dt"] >= range_start_b) & (merged_all["_dt"] <= range_end_b)].copy()

    label_a = f"{range_start_a} ~ {range_end_a}"
    label_b = f"{range_start_b} ~ {range_end_b}"

    # ---------- ① 방문 경로 분석 (유입 경로별 고객 수): Pie 또는 가로형 막대 ----------
    st.subheader("① 방문 경로 분석 (유입 경로별 고객 수)")
    c1, c2 = st.columns(2)
    with c1:
        st.caption(f"기간 A: {label_a}")
        vc_a = df_period_a["visit_reason"].fillna("미기입").value_counts().reset_index()
        vc_a.columns = ["visit_reason", "count"]
        if len(vc_a) == 0 or vc_a["count"].sum() == 0:
            st.info("데이터 없음")
        else:
            fig_a1 = px.bar(vc_a, x="count", y="visit_reason", orientation="h", title="방문 경로별 고객 수")
            fig_a1.update_traces(hovertemplate="%{y}<br>건수: %{x:,}건<extra></extra>")
            fig_a1.update_layout(margin=dict(t=30, b=20, l=20, r=20), height=320, yaxis_title="", xaxis_title="건수")
            st.plotly_chart(fig_a1, width='stretch', key=f"{key_prefix}_visit_route_a")
    with c2:
        st.caption(f"기간 B: {label_b}")
        vc_b = df_period_b["visit_reason"].fillna("미기입").value_counts().reset_index()
        vc_b.columns = ["visit_reason", "count"]
        if len(vc_b) == 0 or vc_b["count"].sum() == 0:
            st.info("데이터 없음")
        else:
            fig_b1 = px.bar(vc_b, x="count", y="visit_reason", orientation="h", title="방문 경로별 고객 수")
            fig_b1.update_traces(hovertemplate="%{y}<br>건수: %{x:,}건<extra></extra>")
            fig_b1.update_layout(margin=dict(t=30, b=20, l=20, r=20), height=320, yaxis_title="", xaxis_title="건수")
            st.plotly_chart(fig_b1, width='stretch', key=f"{key_prefix}_visit_route_b")

    # ---------- ② 구매 이유별 총판매금액: 세로형 막대, y축 콤마 포맷 ----------
    st.subheader("② 구매 이유별 총판매금액")
    c1, c2 = st.columns(2)
    with c1:
        st.caption(f"기간 A: {label_a}")
        pr_a = df_period_a.groupby("purchase_reason", as_index=False)["total_amount"].sum()
        pr_a = pr_a[pr_a["purchase_reason"].notna() & (pr_a["purchase_reason"] != "")]
        if len(pr_a) == 0:
            st.info("데이터 없음")
        else:
            pr_a = pr_a.sort_values("total_amount", ascending=False)
            fig_a2 = px.bar(pr_a, x="purchase_reason", y="total_amount", title="구매 이유별 총판매금액")
            fig_a2.update_traces(hovertemplate="%{x}<br>총액: %{y:,.0f}원<extra></extra>")
            fig_a2.update_layout(margin=dict(t=30, b=80, l=20, r=20), height=320, xaxis_title="", yaxis_title="총 판매금액(원)", xaxis_tickangle=-45)
            fig_a2.update_yaxes(tickformat=",", title="총 판매금액(원)")
            st.plotly_chart(fig_a2, width='stretch', key=f"{key_prefix}_purchase_reason_a")
    with c2:
        st.caption(f"기간 B: {label_b}")
        pr_b = df_period_b.groupby("purchase_reason", as_index=False)["total_amount"].sum()
        pr_b = pr_b[pr_b["purchase_reason"].notna() & (pr_b["purchase_reason"] != "")]
        if len(pr_b) == 0:
            st.info("데이터 없음")
        else:
            pr_b = pr_b.sort_values("total_amount", ascending=False)
            fig_b2 = px.bar(pr_b, x="purchase_reason", y="total_amount", title="구매 이유별 총판매금액")
            fig_b2.update_traces(hovertemplate="%{x}<br>총액: %{y:,.0f}원<extra></extra>")
            fig_b2.update_layout(margin=dict(t=30, b=80, l=20, r=20), height=320, xaxis_title="", yaxis_title="총 판매금액(원)", xaxis_tickangle=-45)
            fig_b2.update_yaxes(tickformat=",", title="총 판매금액(원)")
            st.plotly_chart(fig_b2, width='stretch', key=f"{key_prefix}_purchase_reason_b")

    # ---------- ③ 카테고리별 인기 품목 Top 10: 가로형 막대 또는 DataFrame ----------
    st.subheader("③ 카테고리별 인기 품목 (Top 10)")
    c1, c2 = st.columns(2)
    with c1:
        st.caption(f"기간 A: {label_a}")
        cats_a = df_period_a["category"].fillna("").str.split(",").explode().str.strip()
        cats_a = cats_a[cats_a != ""]
        cat_a = cats_a.value_counts().reset_index().head(10)
        cat_a.columns = ["품목", "판매건수"]
        cat_a["순위"] = range(1, len(cat_a) + 1)
        if len(cat_a) == 0:
            st.info("데이터 없음")
        else:
            fig_a3 = px.bar(cat_a, x="판매건수", y="품목", orientation="h", title="품목별 판매 횟수 Top 10")
            fig_a3.update_traces(hovertemplate="%{y}<br>판매 횟수: %{x:,}건<extra></extra>")
            fig_a3.update_layout(margin=dict(t=30, b=20, l=20, r=20), height=320, xaxis_title="판매 횟수", yaxis_title="")
            st.plotly_chart(fig_a3, width='stretch', key=f"{key_prefix}_category_top10_a")
            st.dataframe(cat_a[["순위", "품목", "판매건수"]], width='stretch', key=f"{key_prefix}_category_df_a", height=min(280, 50 + len(cat_a) * 32))
    with c2:
        st.caption(f"기간 B: {label_b}")
        cats_b = df_period_b["category"].fillna("").str.split(",").explode().str.strip()
        cats_b = cats_b[cats_b != ""]
        cat_b = cats_b.value_counts().reset_index().head(10)
        cat_b.columns = ["품목", "판매건수"]
        cat_b["순위"] = range(1, len(cat_b) + 1)
        if len(cat_b) == 0:
            st.info("데이터 없음")
        else:
            fig_b3 = px.bar(cat_b, x="판매건수", y="품목", orientation="h", title="품목별 판매 횟수 Top 10")
            fig_b3.update_traces(hovertemplate="%{y}<br>판매 횟수: %{x:,}건<extra></extra>")
            fig_b3.update_layout(margin=dict(t=30, b=20, l=20, r=20), height=320, xaxis_title="판매 횟수", yaxis_title="")
            st.plotly_chart(fig_b3, width='stretch', key=f"{key_prefix}_category_top10_b")
            st.dataframe(cat_b[["순위", "품목", "판매건수"]], width='stretch', key=f"{key_prefix}_category_df_b", height=min(280, 50 + len(cat_b) * 32))

    # ---------- ④ 지역별 매출 분포 지도 (Folium 좌우 비교) ----------
    st.subheader("④ 지역별 매출 분포 지도")
    c1, c2 = st.columns(2)
    with c1:
        st.caption(f"기간 A: {label_a}")
        _render_single_period_folium_map(df_period_a, label_a, f"{key_prefix}_map_a")
    with c2:
        st.caption(f"기간 B: {label_b}")
        _render_single_period_folium_map(df_period_b, label_b, f"{key_prefix}_map_b")


@st.fragment
def render_marketing_insights_tenant():
    """매장(Tenant): 해당 매장 데이터로 다중 기간 교차 분석 (기간 A vs 기간 B).
    @st.fragment: 기간 날짜 선택 시 이 섹션만 재실행 — 전체 페이지 Full Rerun 방지."""
    db_filename = st.session_state.get("current_db")
    if not db_filename:
        st.warning("매장에 로그인한 후 이용하세요.")
        return
    order_cols_mi = "id, customer_id, order_date, delivery_date, total_amount, visit_reason, purchase_reason, category"
    orders = load_orders_cached(db_filename, order_cols_mi, limit=None)
    customers = load_customers_cached(db_filename, limit=None)

    # 데이터 빈 값 체크
    if orders.empty or customers.empty:
        st.info("아직 분석할 데이터가 없습니다.")
        return

    # 컬럼 존재 여부 체크: 있는 컬럼만 사용
    wanted_cols = ["id", "name", "address"]
    available_cols = [c for c in wanted_cols if c in customers.columns]
    if "id" not in available_cols:
        st.info("고객 데이터에 id 컬럼이 없어 병합할 수 없습니다.")
        return
    customers_sub = customers[available_cols].copy()
    for c in wanted_cols:
        if c not in customers_sub.columns:
            customers_sub[c] = None

    today = _today_kst()
    month_start = today.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    st.subheader("📅 비교 기간 선택")
    col_a, col_b = st.columns(2)
    with col_a:
        st.caption("비교 기간 A (예: 작년 동기, 광고 전)")
        range_start_a = st.date_input("시작일", value=last_month_start, key="mi_tenant_period_a_start")
        range_end_a = st.date_input("종료일", value=last_month_end, key="mi_tenant_period_a_end")
    with col_b:
        st.caption("비교 기간 B (예: 올해, 광고 후)")
        range_start_b = st.date_input("시작일", value=month_start, key="mi_tenant_period_b_start")
        range_end_b = st.date_input("종료일", value=today, key="mi_tenant_period_b_end")
    if range_start_a > range_end_a:
        range_end_a = range_start_a
    if range_start_b > range_end_b:
        range_end_b = range_start_b

    merged = orders.merge(customers_sub, left_on="customer_id", right_on="id", how="left")
    if len(merged) == 0:
        st.info("등록된 매출 데이터가 없습니다. 기간을 선택해도 비교할 데이터가 없습니다.")
        return
    _render_marketing_multi_period_comparison(
        merged,
        range_start_a, range_end_a,
        range_start_b, range_end_b,
        key_prefix="mi_tenant",
    )


def _render_single_period_folium_map(merged_df: pd.DataFrame, period_label: str, key_prefix: str):
    """기간별 필터된 merged로 Folium 지도 1개 렌더링 (좌우 비교용)."""
    if not FOLIUM_AVAILABLE:
        st.info("지도 기능을 사용하려면 `folium`과 `streamlit-folium` 패키지를 설치해 주세요.")
        return
    if merged_df.empty:
        st.info(f"기간 {period_label}에 데이터가 없습니다.")
        return
    map_data = _build_map_data_with_geocoding(merged_df)
    if map_data.empty or "latitude" not in map_data.columns or "longitude" not in map_data.columns:
        st.info("지도에 표시할 위치 데이터(위도/경도)가 존재하지 않습니다.")
        return
    map_data = map_data.dropna(subset=["latitude", "longitude"])
    if map_data.empty:
        st.info("지오코딩 가능한 주소가 없습니다.")
        return
    m = _create_folium_map(map_data, _MAP_CENTER, _MAP_ZOOM, key_prefix)
    if m:
        st_folium(m, returned_objects=[], use_container_width=True, key=f"{key_prefix}_single_map")


def render_marketing_insights_superadmin():
    """최고 관리자: 다중 기간 비교 대시보드 (Comparative Analytics Dashboard)."""
    stores = get_supabase_stores_dataframe_cached()
    if len(stores) == 0:
        st.info("등록된 매장이 없습니다.")
        return
    merged_list = []
    order_cols_sa = "id, customer_id, order_date, delivery_date, total_amount, visit_reason, purchase_reason, category"
    for _, s in stores.iterrows():
        db_fn = s["db_filename"]
        orders = load_orders_cached(db_fn, order_cols_sa, limit=None)
        customers = load_customers_cached(db_fn, limit=None)
        if len(orders) == 0:
            continue
        if not customers.empty and "address" in customers.columns:
            cust_sub = customers[["id", "name", "address"]]
        else:
            cust_sub = customers[["id", "name"]].copy() if not customers.empty else pd.DataFrame(columns=["id", "name", "address"])
            if not cust_sub.empty:
                cust_sub["address"] = None
        if cust_sub.empty:
            m = orders.copy()
            m["name"] = m["address"] = None
        else:
            m = orders.merge(cust_sub, left_on="customer_id", right_on="id", how="left")
        m["_store"] = s["store_name"]
        merged_list.append(m)
    if not merged_list:
        st.info("데이터가 충분하지 않습니다.")
        return
    merged_list_nonempty = [df for df in merged_list if df is not None and len(df) > 0]
    if not merged_list_nonempty:
        st.info("데이터가 충분하지 않습니다.")
        return
    merged_all = pd.concat(merged_list_nonempty, ignore_index=True)
    merged_all["order_date"] = pd.to_datetime(merged_all["order_date"], errors="coerce")
    merged_all["region"] = merged_all["address"].map(extract_region)
    merged_all["total_amount"] = merged_all["total_amount"].fillna(0).astype(float)
    merged_all = merged_all[merged_all["order_date"].notna()]
    merged_all["_dt"] = merged_all["order_date"].dt.date

    today = _today_kst()
    month_start = today.replace(day=1)
    last_month_end = month_start - timedelta(days=1)
    last_month_start = last_month_end.replace(day=1)

    st.subheader("📅 비교 기간 선택")
    col_a, col_b = st.columns(2)
    with col_a:
        st.caption("비교 기간 A (예: 작년 동기, 광고 전)")
        range_start_a = st.date_input("시작일", value=last_month_start, key="mi_superadmin_period_a_start")
        range_end_a = st.date_input("종료일", value=last_month_end, key="mi_superadmin_period_a_end")
    with col_b:
        st.caption("비교 기간 B (예: 올해, 광고 후)")
        range_start_b = st.date_input("시작일", value=month_start, key="mi_superadmin_period_b_start")
        range_end_b = st.date_input("종료일", value=today, key="mi_superadmin_period_b_end")
    if range_start_a > range_end_a:
        range_end_a = range_start_a
    if range_start_b > range_end_b:
        range_end_b = range_start_b

    _render_marketing_multi_period_comparison(
        merged_all,
        range_start_a, range_end_a,
        range_start_b, range_end_b,
        key_prefix="mi_superadmin",
    )


# ========== 탭 0: 최고 관리자 메뉴 (Superadmin) — 6탭 구성 ==========

def _superadmin_tab1_integrated_dashboard():
    """① 전 지점 통합 대시보드: 이번 달 총매출/총마진/전체 미수금 + 매장별 랭킹."""
    _render_recent_notices_section()
    stores = get_supabase_stores_dataframe_cached()
    if len(stores) == 0:
        st.info("등록된 매장이 없습니다.")
        return
    today = _today_kst()
    month_start = today.replace(day=1)
    from calendar import monthrange
    month_end = date(today.year, today.month, monthrange(today.year, today.month)[1])
    start_str = month_start.isoformat()
    end_str = month_end.isoformat()
    all_orders = []
    all_payments = []
    store_orders = {}
    store_payments = {}
    for _, s in stores.iterrows():
        db_fn = s["db_filename"]
        if _supabase_orders_payments_available():
            orders = _load_orders_supabase(db_fn, "id, order_date, total_amount, actual_margin", limit=None, start_date=start_str, end_date=end_str)
            payments = _load_payments_supabase(db_fn)
        else:
            conn = get_tenant_conn(db_fn)
            if not conn:
                continue
            try:
                try:
                    orders = pd.read_sql("SELECT id, order_date, total_amount, actual_margin FROM Orders WHERE order_date >= ? AND order_date <= ?", conn, params=(start_str, end_str))
                except Exception:
                    orders = pd.DataFrame()
                try:
                    payments = pd.read_sql("SELECT order_id, amount FROM Payments", conn)
                except Exception:
                    payments = pd.DataFrame()
            finally:
                conn.close()

        if len(orders) == 0:
            store_orders[s["store_name"]] = pd.DataFrame()
            store_payments[s["store_name"]] = pd.DataFrame()
            continue

        orders["order_date"] = pd.to_datetime(orders["order_date"], errors="coerce")
        orders["_store"] = s["store_name"]
        pay_sum = payments.groupby("order_id")["amount"].sum() if not payments.empty and "order_id" in payments.columns else pd.Series(dtype=float)
        orders["_paid"] = orders["id"].map(pay_sum).fillna(0)
        orders["_balance"] = orders["total_amount"] - orders["_paid"]
        month_ord = orders[(orders["order_date"].dt.date >= month_start) & (orders["order_date"].dt.date <= month_end)]
        store_orders[s["store_name"]] = month_ord
        store_payments[s["store_name"]] = payments
        all_orders.append(orders)
    if not all_orders:
        st.metric("이번 달 전 지점 총매출", "0원")
        st.metric("이번 달 전 지점 총마진", "0원")
        st.metric("전체 누적 미수금", "0원")
        return
    all_orders_nonempty = [df for df in all_orders if df is not None and len(df) > 0]
    if not all_orders_nonempty:
        st.metric("이번 달 전 지점 총매출", "0원")
        st.metric("이번 달 전 지점 총마진", "0원")
        st.metric("전체 누적 미수금", "0원")
        return
    combined = pd.concat(all_orders_nonempty, ignore_index=True)
    month_combined = combined[(combined["order_date"].dt.date >= month_start) & (combined["order_date"].dt.date <= month_end)]
    total_sales_month = month_combined["total_amount"].sum()
    total_margin_month = month_combined["actual_margin"].fillna(0).sum()
    total_unpaid_all = combined["_balance"].clip(lower=0).sum()
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("이번 달 전 지점 총매출", f"{total_sales_month:,.0f}원")
    with c2:
        st.metric("이번 달 전 지점 총마진", f"{total_margin_month:,.0f}원")
    with c3:
        st.metric("전체 누적 미수금", f"{total_unpaid_all:,.0f}원")
    st.subheader("매장별 실적 랭킹 (이번 달 최종 판매금액)")
    rows = []
    for store_name, ord_df in store_orders.items():
        if len(ord_df) == 0:
            rows.append({"매장명": store_name, "이번 달 판매금액": 0, "미수금 합계": 0})
            continue
        sales = ord_df["total_amount"].sum()
        full_for_store = next((o for o in all_orders if (o["_store"] == store_name).all()), None)
        unpaid = full_for_store["_balance"].clip(lower=0).sum() if full_for_store is not None and "_balance" in full_for_store.columns else 0
        rows.append({"매장명": store_name, "이번 달 판매금액": int(sales), "미수금 합계": int(unpaid)})
    rank_df = pd.DataFrame(rows).sort_values("이번 달 판매금액", ascending=False).reset_index(drop=True)
    rank_df["순위"] = range(1, len(rank_df) + 1)
    rank_display = _format_df_display(rank_df[["순위", "매장명", "이번 달 판매금액", "미수금 합계"]], ["이번 달 판매금액", "미수금 합계"])
    st.dataframe(rank_display, width='stretch')


def _superadmin_tab2_hr_store_employees():
    """② 매장별 직원 평가 현황 (HR): 매장/전체 + 단일월/연월범위 집계."""
    stores = get_supabase_stores_dataframe_cached()
    if len(stores) == 0:
        st.info("등록된 매장이 없습니다.")
        return

    store_options = ["전체 매장 통합"] + stores["store_name"].tolist()
    selected_store = st.selectbox("매장 선택", store_options, key="sa_hr_store")
    period_mode = st.radio(
        "조회 방식",
        ["단일 월", "연월 범위"],
        horizontal=True,
        key="sa_hr_period_mode",
    )

    # 1) 선택 범위의 주문 데이터 로드 (DB 스키마/코어 로직 변경 없이 조회만 수행)
    orders_all = []
    if selected_store == "전체 매장 통합":
        target_rows = stores[["store_name", "db_filename"]].to_dict("records")
    else:
        target_rows = stores[stores["store_name"] == selected_store][["store_name", "db_filename"]].to_dict("records")
    for s in target_rows:
        db_fn = s["db_filename"]
        store_nm = s["store_name"]
        if _supabase_orders_payments_available():
            order_list = "id, order_date, total_amount, actual_margin, employee_names, display_sales_amount"
            odf = _load_orders_supabase(db_fn, order_list, limit=None)
        else:
            conn = get_tenant_conn(db_fn)
            if not conn:
                continue
            try:
                cur = conn.execute("PRAGMA table_info(Orders)")
                cols = [r[1] for r in cur.fetchall()]
                order_list = "id, order_date, total_amount, actual_margin, employee_names"
                if "display_sales_amount" in cols:
                    order_list += ", display_sales_amount"
                odf = pd.read_sql(f"SELECT {order_list} FROM Orders", conn)
            except Exception:
                odf = pd.DataFrame()
            finally:
                conn.close()
        if odf is None or odf.empty:
            continue
        odf = odf.copy()
        odf["_store"] = store_nm
        orders_all.append(odf)

    if not orders_all:
        st.info("선택한 매장 범위에 주문 데이터가 없습니다.")
        return
    orders = pd.concat(orders_all, ignore_index=True)

    if "display_sales_amount" not in orders.columns:
        orders["display_sales_amount"] = 0
    orders["display_sales_amount"] = orders["display_sales_amount"].fillna(0).astype(int)
    orders["actual_margin"] = orders["actual_margin"].fillna(0)
    orders["order_date"] = pd.to_datetime(orders["order_date"], errors="coerce")
    order_dates = orders["order_date"].dropna()
    if len(order_dates) == 0:
        st.info("주문 날짜 데이터가 없습니다.")
        return

    # 2) 월 옵션 생성
    min_d = order_dates.min().to_pydatetime()
    max_d = order_dates.max().to_pydatetime()
    months_options = []
    y, m = min_d.year, min_d.month
    end_y, end_m = max_d.year, max_d.month
    while (y, m) <= (end_y, end_m):
        months_options.append((y, m))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    months_options = months_options[::-1]  # 최신월 우선
    month_labels = [f"{yy}년 {mm}월" for yy, mm in months_options]

    from calendar import monthrange
    if period_mode == "단일 월":
        sel_idx = st.selectbox(
            "연/월 선택",
            range(len(month_labels)),
            format_func=lambda i: month_labels[i],
            key="sa_hr_month_single",
        )
        sel_y, sel_m = months_options[sel_idx]
        range_start = date(sel_y, sel_m, 1)
        range_end = date(sel_y, sel_m, monthrange(sel_y, sel_m)[1])
    else:
        c1, c2 = st.columns(2)
        with c1:
            s_idx = st.selectbox(
                "시작 연/월",
                range(len(month_labels)),
                format_func=lambda i: month_labels[i],
                index=min(len(month_labels) - 1, 3),
                key="sa_hr_month_start",
            )
        with c2:
            e_idx = st.selectbox(
                "종료 연/월",
                range(len(month_labels)),
                format_func=lambda i: month_labels[i],
                index=0,
                key="sa_hr_month_end",
            )
        # 최신순 배열이므로 index가 작을수록 최신월
        _start_idx = max(s_idx, e_idx)
        _end_idx = min(s_idx, e_idx)
        sy, sm = months_options[_start_idx]
        ey, em = months_options[_end_idx]
        range_start = date(sy, sm, 1)
        range_end = date(ey, em, monthrange(ey, em)[1])

    st.caption(f"조회 기간: {range_start.isoformat()} ~ {range_end.isoformat()}")
    st.caption(
        "※ **매출 점수(70)·매출집계(순액)**: 기간 내 **판매일(transaction_date)** sales 순액(음수 포함) 1/n. "
        "**현금수금 점수(10)·현금수금집계**: **결제일(payment_date)** 기준, **수수료 없는 수납**만(이체·온누리·지역화폐·현금 등). 신용·체크·**메인페이** 제외 1/n. "
        "**마진 점수(15)·전시품 점수(5)**: 동 기간 sales를 주문 비율로 배분(경영 대시보드 월별 KPI와 동일)."
    )

    # 3) 매출·마진·전시: sales transaction_date 구간 | 현금수금: payment_date·KPI 수납 버킷 (집계만)
    s_parts: list = []
    for s in target_rows:
        _dbf = s["db_filename"]
        _snm = s["store_name"]
        o_sub = orders[orders["_store"] == _snm] if "_store" in orders.columns else orders
        sdf = load_sales_with_employees_cached(_dbf, range_start.isoformat(), range_end.isoformat())
        if sdf.empty or "transaction_date" not in sdf.columns:
            continue
        sdf = sdf.copy()
        sdf["transaction_date"] = pd.to_datetime(sdf["transaction_date"], errors="coerce")
        sdf = sdf.dropna(subset=["transaction_date"])
        sdf = sdf[
            (sdf["transaction_date"].dt.date >= range_start) & (sdf["transaction_date"].dt.date <= range_end)
        ]
        if sdf.empty:
            continue
        td = _kpi_employee_totals_from_sales_slice(sdf, o_sub)
        if td.empty:
            continue
        td = td.copy()
        td["store"] = _snm
        s_parts.append(td)

    if s_parts:
        df_s = pd.concat(s_parts, ignore_index=True)
        df_s = df_s.groupby(["store", "employee"], as_index=False).agg(
            {"revenue": "sum", "margin": "sum", "display_sales": "sum"}
        )
    else:
        df_s = pd.DataFrame(columns=["store", "employee", "revenue", "margin", "display_sales"])

    cs_parts: list = []
    for s in target_rows:
        _dbf = s["db_filename"]
        _snm = s["store_name"]
        o_sub = orders[orders["_store"] == _snm] if "_store" in orders.columns else orders
        cdf = _aggregate_cash_collected_by_employee(_dbf, range_start, range_end, o_sub)
        if not cdf.empty:
            cdf = cdf.copy()
            cdf["store"] = _snm
            cs_parts.append(cdf.rename(columns={"cash_sales": "kpi_receipt"}))
    if cs_parts:
        df_cs = pd.concat(cs_parts, ignore_index=True)
    else:
        df_cs = pd.DataFrame(columns=["store", "employee", "kpi_receipt"])

    if df_s.empty:
        df_s = pd.DataFrame(columns=["store", "employee", "revenue", "margin", "display_sales"])
    if df_cs.empty:
        df_cs = pd.DataFrame(columns=["store", "employee", "kpi_receipt"])

    row_df = df_s.merge(df_cs, on=["store", "employee"], how="outer").fillna(
        {"revenue": 0.0, "margin": 0.0, "display_sales": 0.0, "kpi_receipt": 0.0}
    )
    row_df = row_df[~row_df["employee"].map(_kpi_employee_names_cell_is_blank)]
    if row_df.empty:
        st.info("선택한 기간에 직원 배정 매출·현금수금·마진·전시 데이터가 없습니다.")
        return

    emp_df = row_df.groupby("employee", as_index=False).agg({
        "revenue": "sum",
        "kpi_receipt": "sum",
        "margin": "sum",
        "display_sales": "sum",
        "store": "nunique",
    }).rename(columns={"store": "참여 매장 수"})

    total_revenue = emp_df["revenue"].sum() or 0
    total_margin = emp_df["margin"].sum() or 0
    total_display = emp_df["display_sales"].sum() or 0
    total_kpi_receipt = emp_df["kpi_receipt"].sum() or 0
    emp_df["매출 점수(70)"] = (emp_df["revenue"] / total_revenue * 70).round(1) if total_revenue else 0.0
    emp_df["마진 점수(15)"] = (emp_df["margin"] / total_margin * 15).round(1) if total_margin else 0.0
    emp_df["전시품 점수(5)"] = (emp_df["display_sales"] / total_display * 5).round(1) if total_display else 0.0
    emp_df["현금수금 점수(10)"] = (emp_df["kpi_receipt"] / total_kpi_receipt * 10).round(1) if total_kpi_receipt else 0.0
    emp_df["종합 점수"] = (
        emp_df["매출 점수(70)"] + emp_df["마진 점수(15)"] + emp_df["전시품 점수(5)"] + emp_df["현금수금 점수(10)"]
    ).round(1)
    emp_df = emp_df.sort_values("종합 점수", ascending=False).reset_index(drop=True)
    emp_df["매출집계(순액)"] = emp_df["revenue"].round(0).astype(int)
    emp_df["현금수금집계"] = emp_df["kpi_receipt"].round(0).astype(int)
    emp_df["마진액"] = emp_df["margin"].round(0).astype(int)
    emp_df["전시품 판매액"] = emp_df["display_sales"].round(0).astype(int)

    base_cols = [
        "employee",
        "매출집계(순액)",
        "현금수금집계",
        "마진액",
        "전시품 판매액",
        "매출 점수(70)",
        "마진 점수(15)",
        "전시품 점수(5)",
        "현금수금 점수(10)",
        "종합 점수",
    ]
    if selected_store == "전체 매장 통합":
        base_cols.insert(5, "참여 매장 수")
    display_df = emp_df[base_cols].rename(columns={"employee": "직원명"})
    display_fmt = _format_df_display(
        display_df, ["매출집계(순액)", "현금수금집계", "마진액", "전시품 판매액"]
    )
    st.dataframe(
        display_fmt,
        width='stretch',
        column_config={
            "직원명": st.column_config.TextColumn("직원명", width="small"),
            "매출집계(순액)": st.column_config.TextColumn("매출집계(순액)", width="medium"),
            "현금수금집계": st.column_config.TextColumn("현금수금집계", width="medium"),
            "마진액": st.column_config.TextColumn("마진액", width="medium"),
            "전시품 판매액": st.column_config.TextColumn("전시품 판매액", width="small"),
            "매출 점수(70)": st.column_config.NumberColumn("매출(70)", format="%.1f", width="small"),
            "마진 점수(15)": st.column_config.NumberColumn("마진(15)", format="%.1f", width="small"),
            "전시품 점수(5)": st.column_config.NumberColumn("전시품(5)", format="%.1f", width="small"),
            "현금수금 점수(10)": st.column_config.NumberColumn("현금수금(10)", format="%.1f", width="small"),
            "종합 점수": st.column_config.NumberColumn("종합 점수", format="%.1f", width="small"),
        },
    )

    store_emp = pd.DataFrame()
    # 4) 전체 통합: 매장별·직원별 현금수금집계 합 보조표
    if selected_store == "전체 매장 통합":
        st.markdown("##### 전지점 통합 - 매장별 직원 현금수금집계 합")
        store_emp = (
            row_df.groupby(["store", "employee"], as_index=False)["kpi_receipt"]
            .sum()
            .sort_values(["store", "kpi_receipt"], ascending=[True, False])
            .rename(columns={"store": "매장명", "employee": "직원명", "kpi_receipt": "현금수금집계합"})
        )
        store_emp["현금수금집계합"] = store_emp["현금수금집계합"].round(0).astype(int)
        st.dataframe(_format_df_display(store_emp, ["현금수금집계합"]), width='stretch')

    # 5) 엑셀 다운로드 (직원 통합표 + 통합모드 시 매장별 보조표)
    dl_buf = io.BytesIO()
    with pd.ExcelWriter(dl_buf, engine="openpyxl") as writer:
        dl_main = display_df.copy()
        dl_main.to_excel(writer, sheet_name="직원평가요약", index=False)
        if selected_store == "전체 매장 통합" and not store_emp.empty:
            store_emp.to_excel(writer, sheet_name="매장별직원매출합", index=False)
    dl_buf.seek(0)
    _store_label = "전체매장통합" if selected_store == "전체 매장 통합" else selected_store.replace(" ", "_")
    _period_label = f"{range_start.isoformat()}_{range_end.isoformat()}"
    st.download_button(
        "📥 직원 평가 엑셀 다운로드",
        data=dl_buf.getvalue(),
        file_name=f"직원평가_{_store_label}_{_period_label}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="sa_hr_excel_download",
    )


def _superadmin_tab3_notices():
    """③ 전체 매장 공지사항 관리: 제목/내용/외부링크 등록·목록·삭제."""
    st.subheader("📢 공지사항 등록")
    with st.form("notice_add_form"):
        title = st.text_input("제목 *", placeholder="공지 제목을 입력하세요")
        content = st.text_area("내용 *", placeholder="공지 내용을 입력하세요")
        external_link = st.text_input("외부 링크(URL)", placeholder="유튜브, 회의록 등 URL (선택)")
        if st.form_submit_button("공지 등록"):
            if title and title.strip() and content and content.strip():
                conn = get_master_conn()
                conn.execute(
                    "INSERT INTO Notices (title, content, external_link, message, is_active, created_at) VALUES (?, ?, ?, ?, 1, ?)",
                    (title.strip(), content.strip(), (external_link.strip() or None), content.strip(), datetime.now(tz=KST).isoformat())
                )
                conn.commit()
                conn.close()
                st.success("등록되었습니다.")
                st.rerun()
            else:
                st.warning("제목과 내용을 입력하세요.")
    st.subheader("공지사항 목록 (삭제)")
    conn = get_master_conn()
    try:
        notices = pd.read_sql(
            "SELECT id, title, content, external_link, created_at FROM Notices ORDER BY created_at DESC",
            conn,
        )
    finally:
        conn.close()
    if len(notices) == 0:
        st.info("등록된 공지가 없습니다.")
        return
    for _, row in notices.iterrows():
        t = (row.get("title") or "").strip() or "(제목 없음)"
        dt = (row.get("created_at") or "")[:10]
        with st.expander(f"📌 {dt} — {t}"):
            body = (row.get("content") or row.get("message") or "").strip()
            if body:
                st.write(body)
            if row.get("external_link") and str(row["external_link"]).strip():
                url = str(row["external_link"]).strip()
                st.markdown(
                    f'<a href="{html.escape(url)}" target="_blank" rel="noopener noreferrer" '
                    'style="display:inline-block;padding:0.4rem 0.8rem;margin-top:0.5rem;'
                    'background:#1f77b4;color:white;border-radius:0.4rem;text-decoration:none;'
                    'font-weight:500;">🔗 링크 열기</a>',
                    unsafe_allow_html=True,
                )
            if st.button("삭제", key=f"notice_del_{row['id']}"):
                conn = get_master_conn()
                conn.execute("DELETE FROM Notices WHERE id = ?", (row["id"],))
                conn.commit()
                conn.close()
                st.rerun()


def _superadmin_tab4_backup_csv():
    """④ 원클릭 데이터 백업: 기간 지정 후 전 매장 매출/결제 내역 CSV 다운로드 (한글 깨짐 방지).
    고객명, 연락처, 품목, 총판매금액, 결제금액, 미수금, 결제수단, 온누리승인번호, 판매일자, 배송일자, 매장명, 판매담당자, 특이사항 등 전체 컬럼 포함. 매장 목록은 Supabase app_stores 전용."""
    stores = get_supabase_stores_dataframe_cached()
    if len(stores) == 0:
        st.info("등록된 매장이 없습니다.")
        return
    st.subheader("백업 기간 지정")
    col1, col2 = st.columns(2)
    with col1:
        backup_start = st.date_input("시작일", key="backup_start")
    with col2:
        backup_end = st.date_input("종료일", key="backup_end")
    rows = []
    for _, s in stores.iterrows():
        conn = get_tenant_conn(s["db_filename"])
        db_fn = s["db_filename"]
        if _supabase_orders_payments_available():
            merged = _load_orders_supabase(db_fn, "id, customer_id, order_date, delivery_date, total_amount, cost_price, actual_margin, employee_names, category, visit_reason, purchase_reason, display_sales_amount, display_cost_amount, balance_status", limit=None)
            payments = _load_payments_supabase(db_fn)
            if merged.empty:
                continue
            for col in ("display_sales_amount", "display_cost_amount"):
                if col in merged.columns:
                    merged[col] = merged[col].fillna(0)
                else:
                    merged[col] = 0
        else:
            conn = get_tenant_conn(db_fn)
            if not conn:
                continue
            try:
                merged = pd.read_sql("""
                    SELECT o.id, o.customer_id, o.order_date, o.delivery_date,
                           o.total_amount, o.cost_price, o.actual_margin, o.employee_names,
                           o.category, o.visit_reason, o.purchase_reason,
                           COALESCE(o.display_sales_amount, 0) as display_sales_amount,
                           COALESCE(o.display_cost_amount, 0) as display_cost_amount,
                           o.balance_status
                    FROM Orders o
                """, conn)
                payments = pd.read_sql(
                    "SELECT order_id, amount, payment_method, onnuri_approval_code, card_company FROM Payments",
                    conn,
                )
            except Exception:
                conn.close()
                continue
            conn.close()
        customer_ids = merged["customer_id"].dropna().astype(int).unique().tolist()
        cust_map = _get_customers_by_ids_supabase(s["db_filename"], customer_ids) if customer_ids else {}
        merged["customer_name"] = merged["customer_id"].map(lambda cid: (cust_map.get(int(cid)) or {}).get("name", "") if pd.notna(cid) else "")
        merged["phone1"] = merged["customer_id"].map(lambda cid: (cust_map.get(int(cid)) or {}).get("phone1", "") if pd.notna(cid) else "")
        merged["phone2"] = merged["customer_id"].map(lambda cid: (cust_map.get(int(cid)) or {}).get("phone2", "") if pd.notna(cid) else "")
        merged["address"] = merged["customer_id"].map(lambda cid: (cust_map.get(int(cid)) or {}).get("address", "") if pd.notna(cid) else "")
        merged["order_date"] = pd.to_datetime(merged["order_date"], errors="coerce")
        merged = merged[merged["order_date"].notna()]
        merged = merged[(merged["order_date"].dt.date >= backup_start) & (merged["order_date"].dt.date <= backup_end)]
        pay_agg = payments.groupby("order_id").agg({
            "amount": "sum",
            "payment_method": lambda x: ", ".join(str(m) for m in x.dropna().unique() if m),
            "onnuri_approval_code": lambda x: ", ".join(str(a) for a in x.dropna().unique() if a),
        }).rename(columns={"amount": "paid", "payment_method": "methods", "onnuri_approval_code": "onnuri_codes"})
        merged = merged.merge(pay_agg, left_on="id", right_index=True, how="left")
        merged["paid"] = merged["paid"].fillna(0)
        merged["total_sales"] = merged["total_amount"] + merged["display_sales_amount"]
        merged["balance"] = merged["total_sales"] - merged["paid"]
        merged["methods"] = merged["methods"].fillna("")
        merged["onnuri_codes"] = merged["onnuri_codes"].fillna("")
        merged["특이사항"] = (merged["visit_reason"].fillna("") + " / " + merged["purchase_reason"].fillna("")).str.strip(" /")
        for _, o in merged.iterrows():
            rows.append({
                "매장명": s["store_name"],
                "주문ID": int(o["id"]),
                "고객명": o.get("customer_name") or "",
                "연락처": o.get("phone1") or "",
                "연락처2": o.get("phone2") or "",
                "주소": o.get("address") or "",
                "품목": o.get("category") or "",
                "총판매금액": int(o["total_sales"]),
                "결제금액": int(o["paid"]),
                "미수금(잔금)": int(o["balance"]),
                "결제수단": o.get("methods") or "",
                "온누리승인번호": o.get("onnuri_codes") or "",
                "판매일자": o["order_date"].strftime("%Y-%m-%d") if pd.notna(o.get("order_date")) else "",
                "배송일자": str(o.get("delivery_date") or ""),
                "판매담당자": o.get("employee_names") or "",
                "특이사항": o.get("특이사항") or "",
                "방문이유": o.get("visit_reason") or "",
                "구매이유": o.get("purchase_reason") or "",
                "원가": int(o.get("cost_price") or 0),
                "실제마진": int(o.get("actual_margin") or 0),
                "전시판매액": int(o.get("display_sales_amount") or 0),
                "전시원가": int(o.get("display_cost_amount") or 0),
                "잔금상태": o.get("balance_status") or "",
            })
    if not rows:
        st.warning("선택 기간에 해당하는 데이터가 없습니다.")
        return
    out_df = pd.DataFrame(rows)
    csv_content = out_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        "CSV 다운로드",
        data=csv_content,
        file_name=f"매출백업_{backup_start}_{backup_end}.csv",
        mime="text/csv; charset=utf-8",
        key="backup_dl"
    )


def _superadmin_tab_unpaid_report():
    """미수금(잔금) 전용 레포트: 기간 필터, 잔금 > 0 필터, 다운로드."""
    stores = get_supabase_stores_dataframe_cached()
    if len(stores) == 0:
        st.info("등록된 매장이 없습니다.")
        return
    st.subheader("미수금(잔금) 레포트")
    col1, col2 = st.columns(2)
    with col1:
        report_start = st.date_input("조회 시작일", value=_today_kst() - timedelta(days=30), key="unpaid_report_start")
    with col2:
        report_end = st.date_input("조회 종료일", value=_today_kst(), key="unpaid_report_end")
    rows = []
    for _, s in stores.iterrows():
        db_fn = s["db_filename"]
        if _supabase_orders_payments_available():
            merged = _load_orders_supabase(db_fn, "id, customer_id, order_date, delivery_date, total_amount, employee_names, display_sales_amount", limit=None)
            if merged.empty:
                continue
            if "display_sales_amount" not in merged.columns:
                merged["display_sales_amount"] = 0
            else:
                merged["display_sales_amount"] = merged["display_sales_amount"].fillna(0)
            pay_df = _load_payments_supabase(db_fn)
            if not pay_df.empty and "order_id" in pay_df.columns and "amount" in pay_df.columns:
                payments = pay_df.groupby("order_id")["amount"].sum().reset_index()
                payments.columns = ["order_id", "paid"]
            else:
                payments = pd.DataFrame(columns=["order_id", "paid"])
        else:
            conn = get_tenant_conn(db_fn)
            if not conn:
                continue
            try:
                merged = pd.read_sql("""
                    SELECT o.id, o.customer_id, o.order_date, o.delivery_date, o.total_amount, o.employee_names,
                           COALESCE(o.display_sales_amount, 0) as display_sales_amount
                    FROM Orders o
                """, conn)
                payments = pd.read_sql("SELECT order_id, SUM(amount) as paid FROM Payments GROUP BY order_id", conn)
            except Exception:
                conn.close()
                continue
            conn.close()
        customer_ids = merged["customer_id"].dropna().astype(int).unique().tolist()
        cust_map = _get_customers_by_ids_supabase(s["db_filename"], customer_ids) if customer_ids else {}
        merged["customer_name"] = merged["customer_id"].map(lambda cid: (cust_map.get(int(cid)) or {}).get("name", "") if pd.notna(cid) else "")
        merged["phone1"] = merged["customer_id"].map(lambda cid: (cust_map.get(int(cid)) or {}).get("phone1", "") if pd.notna(cid) else "")
        merged = merged.merge(payments, left_on="id", right_on="order_id", how="left")
        merged["paid"] = merged["paid"].fillna(0)
        merged["total_sales"] = merged["total_amount"] + merged["display_sales_amount"]
        merged["balance"] = merged["total_sales"] - merged["paid"]
        merged["order_date"] = pd.to_datetime(merged["order_date"], errors="coerce")
        merged = merged[merged["order_date"].notna()]
        merged = merged[(merged["order_date"].dt.date >= report_start) & (merged["order_date"].dt.date <= report_end)]
        merged = merged[merged["balance"] > 0]
        for _, o in merged.iterrows():
            rows.append({
                "매장명": s["store_name"],
                "판매담당자": o.get("employee_names") or "",
                "고객명": o.get("customer_name") or "",
                "연락처": o.get("phone1") or "",
                "판매일자": o["order_date"].strftime("%Y-%m-%d") if pd.notna(o.get("order_date")) else "",
                "배송일자": str(o.get("delivery_date") or ""),
                "총판매금액": int(o["total_sales"]),
                "미수금액(잔금)": int(o["balance"]),
            })
    if not rows:
        st.info("선택 기간 내 잔금이 있는 건이 없습니다.")
        return
    out_df = pd.DataFrame(rows)
    display_df = _format_df_display(out_df, ["총판매금액", "미수금액(잔금)"])
    st.dataframe(display_df, width='stretch')
    csv_content = out_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
    st.download_button(
        "미수금 레포트 CSV 다운로드",
        data=csv_content,
        file_name=f"미수금레포트_{report_start}_{report_end}.csv",
        mime="text/csv; charset=utf-8",
        key="unpaid_report_dl",
    )


def _superadmin_tab5_store_accounts():
    """⑤ 매장 계정 관리: Supabase app_stores / app_users만 사용. 매장 생성·수정·삭제 및 계정 발급·비밀번호 변경."""
    client, err = get_supabase_client()
    if err or not client:
        st.error(f"Supabase 연결이 필요합니다: {err or '연결 실패'}")
        return
    stores_list = _get_supabase_stores_list()
    stores = pd.DataFrame(stores_list).sort_values("store_name", ignore_index=True) if stores_list else pd.DataFrame(columns=["id", "store_name", "db_filename"])
    if stores.empty or "store_name" not in stores.columns:
        stores = pd.DataFrame(columns=["id", "store_name", "db_filename"])
    users_list = _get_supabase_users_list()
    us_pairs = set()
    try:
        r = client.table("app_user_stores").select("user_id, store_id").execute()
        for row in (r.data or []):
            us_pairs.add((row["user_id"], row["store_id"]))
    except Exception:
        pass

    st.subheader("신규 매장 생성")
    with st.form("new_store_form"):
        store_name = st.text_input("매장명")
        submitted = st.form_submit_button("매장 생성")
        if submitted and store_name and store_name.strip():
            stores_list = _get_supabase_stores_list()
            max_id = max((s["id"] for s in stores_list), default=0)
            db_filename = f"store_{max_id + 1}.db"
            try:
                client.table("app_stores").insert({
                    "store_name": store_name.strip(),
                    "db_filename": db_filename,
                }).execute()
                create_tenant_db(db_filename)
                clear_data_cache()
                st.success(f"매장 '{store_name}'이(가) 생성되었습니다. DB: {db_filename}")
                st.rerun()
            except Exception as e:
                err_str = str(e).lower()
                if "unique" in err_str or "duplicate" in err_str or "already exists" in err_str:
                    st.error("이미 존재하는 매장명이거나 DB 파일명입니다.")
                else:
                    st.error(f"등록 실패: {e}")
        elif submitted:
            st.warning("매장명을 입력하세요.")

    st.subheader("매장별 계정 발급")
    if len(stores) > 0:
        with st.form("new_user_form"):
            store_id = st.selectbox("매장 선택", stores["id"].tolist(), format_func=lambda x: stores[stores["id"] == x]["store_name"].iloc[0])
            new_username = st.text_input("사용자명")
            new_password = st.text_input("비밀번호", type="password")
            new_role = st.selectbox("역할", ["store_admin", "user"])
            if st.form_submit_button("계정 생성"):
                if new_username and new_username.strip() and new_password:
                    pw_hash = hashlib.sha256(new_password.encode()).hexdigest()
                    try:
                        client.table("app_users").insert({
                            "username": new_username.strip(),
                            "password": pw_hash,
                            "role": new_role,
                            "store_id": int(store_id),
                            "name": None,
                            "email": None,
                        }).execute()
                        clear_data_cache()
                        st.success("계정이 생성되었습니다.")
                        st.rerun()
                    except Exception as e:
                        err_str = str(e).lower()
                        if "unique" in err_str or "duplicate" in err_str:
                            st.error("이미 존재하는 사용자명입니다.")
                        else:
                            st.error(f"계정 생성 실패: {e}")
                else:
                    st.warning("사용자명과 비밀번호를 입력하세요.")

    st.subheader("매장 조회/수정")
    if len(stores) == 0:
        st.info("매장이 없습니다. 위에서 매장을 추가하거나, migrate_stores_to_supabase()를 실행해 Master DB 매장을 이전하세요.")
        return
    store_options = stores["store_name"].tolist()
    selected_store_name = st.selectbox("매장 선택 (조회·수정)", store_options, key="sa_edit_store_sel")
    if selected_store_name:
        s = stores[stores["store_name"] == selected_store_name].iloc[0]
        sid = s["id"]
        store_users = [u for u in users_list if u.get("store_id") == sid or (u["id"], sid) in us_pairs]
        store_users_df = pd.DataFrame(store_users) if store_users else pd.DataFrame(columns=["id", "username", "role", "store_id"])
        with st.expander("📋 매장 정보 수정", expanded=True):
            with st.form("store_edit_form"):
                edit_name = st.text_input("매장명", value=s["store_name"], key="sa_edit_name")
                edit_db = st.text_input("DB 파일명", value=s["db_filename"], key="sa_edit_db")
                if st.form_submit_button("저장"):
                    if edit_name and edit_name.strip() and edit_db and edit_db.strip():
                        try:
                            client.table("app_stores").update({
                                "store_name": edit_name.strip(),
                                "db_filename": edit_db.strip(),
                            }).eq("id", int(sid)).execute()
                            clear_data_cache()
                            st.success("저장되었습니다.")
                            st.rerun()
                        except Exception as e:
                            err_str = str(e).lower()
                            if "unique" in err_str or "duplicate" in err_str:
                                st.error("매장명 또는 DB 파일명이 이미 사용 중입니다.")
                            else:
                                st.error(f"수정 실패: {e}")
                    else:
                        st.warning("매장명과 DB 파일명을 입력하세요.")
        st.caption("계정(ID) 조회 및 비밀번호 변경")
        for _, u in store_users_df.iterrows():
            with st.form(f"pw_{u['id']}"):
                st.text_input("현재 ID (조회용)", value=u.get("username", ""), disabled=True, key=f"disp_id_{u['id']}")
                new_pw = st.text_input("새 비밀번호 (변경 시만 입력)", type="password", key=f"pw_input_{u['id']}")
                if st.form_submit_button("비밀번호 변경"):
                    if new_pw:
                        try:
                            client.table("app_users").update({
                                "password": hashlib.sha256(new_pw.encode()).hexdigest(),
                            }).eq("id", int(u["id"])).execute()
                            clear_data_cache()
                            st.success("변경되었습니다.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"변경 실패: {e}")
                    else:
                        st.warning("새 비밀번호를 입력하세요.")

    st.subheader("매장 삭제 (이중 확인)")
    if len(stores) > 0:
        del_store_name = st.selectbox("삭제할 매장 선택", store_options, key="sa_del_store_sel")
        if del_store_name:
            s = stores[stores["store_name"] == del_store_name].iloc[0]
            st.warning("매장 삭제 시 해당 매장 배정이 해제되고 매장 행이 삭제됩니다. 복구할 수 없습니다.")
            confirm = st.checkbox(f"'{s['store_name']}' 매장 삭제에 동의합니다.", key="del_confirm_final")
            if st.button("매장 삭제", key="del_btn_final"):
                if not confirm:
                    st.error("위 체크박스를 선택한 후 삭제할 수 있습니다.")
                else:
                    try:
                        sid = int(s["id"])
                        client.table("app_user_stores").delete().eq("store_id", sid).execute()
                        client.table("app_users").update({"store_id": None}).eq("store_id", sid).execute()
                        client.table("app_stores").delete().eq("id", sid).execute()
                        clear_data_cache()
                        db_path = os.path.join(DB_DIR, s["db_filename"])
                        if os.path.exists(db_path):
                            try:
                                os.remove(db_path)
                            except Exception:
                                pass
                        st.success("매장이 삭제되었습니다.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"삭제 실패: {e}")


def _fetch_order_totals_and_margin_for_report(db_fn: str, order_ids: list[int]) -> dict[int, tuple[float, float, int | None]]:
    """order_id -> (total_amount, actual_margin, customer_id). 월별 결제수단 집계표의 매출 원장·마진 기여용."""
    if not order_ids:
        return {}
    out: dict[int, tuple[float, float, int | None]] = {}
    chunks = [order_ids[i : i + 100] for i in range(0, len(order_ids), 100)]
    if _supabase_orders_payments_available():
        client, err = get_supabase_client()
        if err or not client:
            return {}
        try:
            for ch in chunks:
                r = (
                    client.table("app_orders")
                    .select("id, total_amount, actual_margin, customer_id")
                    .eq(ORDERS_PAYMENTS_TENANT_COL, db_fn)
                    .in_("id", ch)
                    .execute()
                )
                for row in (r.data or []):
                    oid = int(row["id"])
                    tot = float(row.get("total_amount") or 0)
                    am = float(row.get("actual_margin") or 0) if row.get("actual_margin") is not None else 0.0
                    _cid = row.get("customer_id")
                    try:
                        cid_i = int(_cid) if _cid is not None and str(_cid).strip() != "" else None
                    except (TypeError, ValueError):
                        cid_i = None
                    out[oid] = (tot, am, cid_i)
        except Exception:
            return out
    else:
        conn = get_tenant_conn(db_fn)
        if not conn:
            return {}
        try:
            for ch in chunks:
                placeholders = ",".join(["?"] * len(ch))
                cur = conn.execute(
                    f"SELECT id, total_amount, COALESCE(actual_margin, 0), customer_id FROM Orders WHERE id IN ({placeholders})",
                    ch,
                )
                for row in cur.fetchall():
                    oid, tot, am, cid = int(row[0]), row[1], row[2], row[3] if len(row) > 3 else None
                    try:
                        cid_i = int(cid) if cid is not None else None
                    except (TypeError, ValueError):
                        cid_i = None
                    out[oid] = (float(tot or 0), float(am or 0), cid_i)
        except Exception:
            pass
        finally:
            conn.close()
    return out


def render_monthly_payment_report(is_superadmin: bool):
    """월별 결제수단 집계표 및 직원별 판매 실적 조회 모듈."""
    today = _today_kst()
    
    st.subheader("📊 통계 및 집계 리포트")
    report_type = st.radio("보고서 종류 선택", ["💳 결제수단 집계표", "👤 직원별 판매 실적"], horizontal=True, key="report_type_radio")
    st.divider()

    query_mode = st.radio(
        "조회 방식",
        ["월별/연도별 조회", "직접 날짜 지정"],
        horizontal=True,
        key="payment_report_mode",
    )
    if query_mode == "월별/연도별 조회":
        report_year = st.selectbox("조회 연도", list(range(today.year, today.year - 6, -1)), key="payment_report_year")
        date_range_start = date(report_year, 1, 1)
        date_range_end = date(report_year, 12, 31)
    else:
        col_s, col_e = st.columns(2)
        with col_s:
            date_range_start = st.date_input("시작일", value=today - timedelta(days=7), key="payment_report_start")
        with col_e:
            date_range_end = st.date_input("종료일", value=today, key="payment_report_end")
        if date_range_start and date_range_end and date_range_start > date_range_end:
            st.warning("시작일이 종료일보다 늦습니다. 기간을 확인해 주세요.")
            return

    if is_superadmin:
        stores = get_supabase_stores_dataframe_cached()
        if len(stores) == 0:
            st.info("등록된 매장이 없습니다.")
            return
        store_options = ["전체 매장 통합"] + stores["store_name"].tolist()
        selected_store = st.selectbox("매장 선택", store_options, key="payment_report_store")
    else:
        db_filename = st.session_state.get("current_db")
        if not db_filename:
            st.warning("매장에 로그인한 후 이용하세요.")
            return
        stores = get_supabase_stores_dataframe_cached()
        match = stores[stores["db_filename"] == db_filename]
        selected_store = match["store_name"].iloc[0] if len(match) > 0 else "매장"

    # ==========================================
    # 모드 1: 결제수단 집계표
    # ==========================================
    if report_type == "💳 결제수단 집계표":
        all_payments = []
        if is_superadmin and selected_store == "전체 매장 통합":
            for _, s in stores.iterrows():
                db_fn = s["db_filename"]
                if _supabase_orders_payments_available():
                    df = _load_payments_supabase(db_fn)
                    if not df.empty and "payment_date" in df.columns:
                        df = df[df["payment_date"].notna() & (df["payment_date"] != "")]
                        _keep = [c for c in ["order_id", "payment_date", "payment_method", "card_company", "onnuri_approval_code", "amount", "created_at", "created_by"] if c in df.columns]
                        df = df[_keep]
                        df["_store"] = s["store_name"]
                        df["_db_fn"] = db_fn
                        all_payments.append(df)
                else:
                    conn = get_tenant_conn(db_fn)
                    if not conn:
                        continue
                    try:
                        _pcols = [r[1] for r in conn.execute("PRAGMA table_info(Payments)").fetchall()]
                        _extra = ", ".join(c for c in ["onnuri_approval_code", "created_at", "created_by"] if c in _pcols)
                        _sel = f"order_id, payment_date, payment_method, card_company, amount{', ' + _extra if _extra else ''}"
                        df = pd.read_sql(f"SELECT {_sel} FROM Payments WHERE payment_date IS NOT NULL AND payment_date != ''", conn)
                        df["_store"] = s["store_name"]
                        df["_db_fn"] = db_fn
                        all_payments.append(df)
                    except Exception:
                        pass
                    finally:
                        conn.close()
            if not all_payments:
                st.info("선택 기간/매장에 결제 데이터가 없습니다.")
                return
            all_payments_nonempty = [df for df in all_payments if df is not None and len(df) > 0]
            if not all_payments_nonempty:
                st.info("선택 기간/매장에 결제 데이터가 없습니다.")
                return
            pay_df = pd.concat(all_payments_nonempty, ignore_index=True)
        else:
            db_fn = db_filename if not is_superadmin else stores[stores["store_name"] == selected_store].iloc[0]["db_filename"]
            if _supabase_orders_payments_available():
                pay_df = _load_payments_supabase(db_fn)
                if pay_df.empty or "payment_date" not in pay_df.columns:
                    pay_df = pd.DataFrame(columns=["order_id", "payment_date", "payment_method", "card_company", "amount"])
                else:
                    pay_df = pay_df[pay_df["payment_date"].notna() & (pay_df["payment_date"] != "")]
                    _keep = [c for c in ["order_id", "payment_date", "payment_method", "card_company", "onnuri_approval_code", "amount", "created_at", "created_by"] if c in pay_df.columns]
                    pay_df = pay_df[_keep]
                pay_df["_db_fn"] = db_fn
            else:
                conn = get_tenant_conn(db_fn)
                if not conn:
                    st.error("매장 DB를 찾을 수 없습니다.")
                    return
                try:
                    _pcols = [r[1] for r in conn.execute("PRAGMA table_info(Payments)").fetchall()]
                    _extra = ", ".join(c for c in ["onnuri_approval_code", "created_at", "created_by"] if c in _pcols)
                    _sel = f"order_id, payment_date, payment_method, card_company, amount{', ' + _extra if _extra else ''}"
                    pay_df = pd.read_sql(f"SELECT {_sel} FROM Payments WHERE payment_date IS NOT NULL AND payment_date != ''", conn)
                except Exception:
                    st.error("결제 데이터를 불러올 수 없습니다.")
                    conn.close()
                    return
                conn.close()
                pay_df["_db_fn"] = db_fn

        pay_df["payment_date"] = pd.to_datetime(pay_df["payment_date"], errors="coerce")
        pay_df = pay_df[pay_df["payment_date"].notna()]
        pay_df["결제일자"] = pay_df["payment_date"].dt.strftime("%Y-%m-%d")
        pay_df["결제월"] = pay_df["payment_date"].dt.strftime("%Y-%m")
        pay_df["_pd"] = pay_df["payment_date"].dt.date
        pay_df = pay_df[(pay_df["_pd"] >= date_range_start) & (pay_df["_pd"] <= date_range_end)]
        pay_df["payment_method"] = pay_df["payment_method"].fillna("미지정")
        if "card_company" not in pay_df.columns:
            pay_df["card_company"] = None
        pay_df["amount"] = pd.to_numeric(pay_df["amount"], errors="coerce").fillna(0)

        _card_short = {"신한카드": "신한", "삼성카드": "삼성", "KB국민카드": "국민", "현대카드": "현대", "롯데카드": "롯데", "우리카드": "우리", "하나카드": "하나", "BC카드": "BC", "NH농협카드": "농협", "기타": "기타"}
        def _to_detailed(row):
            meth = row["payment_method"] or "미지정"
            if meth == "메인페이":
                return "메인페이"
            if meth in ("신용카드", "체크카드"):
                cc = row.get("card_company") or ""
                short = _card_short.get(cc, cc or "미지정")
                prefix = "신용" if meth == "신용카드" else "체크"
                return f"{prefix}_{short}" if cc else meth
            return meth
        pay_df["detailed_payment"] = pay_df.apply(_to_detailed, axis=1)

        if len(pay_df) == 0:
            st.info("선택한 기간에 결제 데이터가 없습니다.")
            return

        # 매출 원장(sales) 행별 마진 기여액 — 결제 집계와 동일 기간·매장, 엑셀 다중 시트·화면 expander 공용
        sales_margin_excel_df = pd.DataFrame()
        sales_margin_monthly_df = pd.DataFrame()
        try:
            _sm_parts: list = []
            if is_superadmin and selected_store == "전체 매장 통합":
                for _, s in stores.iterrows():
                    _sfn = s["db_filename"]
                    _sx = load_sales_with_employees_cached(
                        _sfn, start_date=date_range_start.isoformat(), end_date=date_range_end.isoformat()
                    )
                    if not _sx.empty:
                        _sx = _sx.copy()
                        _sx["_store"] = s["store_name"]
                        _sx["_db_fn"] = _sfn
                        _sm_parts.append(_sx)
            else:
                _sfn = db_filename if not is_superadmin else stores[stores["store_name"] == selected_store].iloc[0]["db_filename"]
                _sx = load_sales_with_employees_cached(
                    _sfn, start_date=date_range_start.isoformat(), end_date=date_range_end.isoformat()
                )
                if not _sx.empty:
                    _sx = _sx.copy()
                    _sx["_store"] = selected_store
                    _sx["_db_fn"] = _sfn
                    _sm_parts.append(_sx)
            if _sm_parts:
                _sm_all = pd.concat(_sm_parts, ignore_index=True)
                _sm_all["transaction_date"] = pd.to_datetime(_sm_all["transaction_date"], errors="coerce")
                _sm_all = _sm_all[_sm_all["transaction_date"].notna()]
                _sm_all["_pd"] = _sm_all["transaction_date"].dt.date
                _sm_all = _sm_all[
                    (_sm_all["_pd"] >= date_range_start) & (_sm_all["_pd"] <= date_range_end)
                ]
                try:
                    _overlay_sales_df_employee_names_from_live_orders(_sm_all)
                except Exception:
                    pass
                _sm_rows: list = []
                for _fn in _sm_all["_db_fn"].dropna().unique():
                    _mask_fn = _sm_all["_db_fn"] == _fn
                    _oids = _sm_all.loc[_mask_fn, "order_id"].dropna().astype(int).unique().tolist()
                    _omap = _fetch_order_totals_and_margin_for_report(str(_fn), _oids)
                    for _, _sr in _sm_all.loc[_mask_fn].iterrows():
                        _oid = _sr.get("order_id")
                        if pd.isna(_oid):
                            continue
                        _oid_i = int(_oid)
                        _amt = float(_sr.get("amount") or 0)
                        _tot, _mrg, _cid = _omap.get(_oid_i, (0.0, 0.0, None))
                        _ftot = float(_tot)
                        if _ftot != 0:
                            _contrib = round(float(_mrg) * (_amt / _ftot), 0)
                        else:
                            _contrib = 0.0
                        _td = _sr["transaction_date"]
                        _dstr = _td.strftime("%Y-%m-%d") if hasattr(_td, "strftime") else str(_td)[:10]
                        _mstr = _td.strftime("%Y-%m") if hasattr(_td, "strftime") else ""
                        _sm_rows.append({
                            "매장명": _sr.get("_store", ""),
                            "거래일자": _dstr,
                            "거래월": _mstr,
                            "원본주문ID": _oid_i,
                            "매출변동액": int(round(_amt, 0)),
                            "주문총액": int(round(_ftot, 0)),
                            "주문실마진": int(round(float(_mrg), 0)),
                            "마진기여액": int(_contrib),
                            "비고": str(_sr.get("note") or "").strip() or "-",
                            "담당직원": str(_sr.get("employee_names") or "").strip() or "-",
                            "_db_fn": str(_fn),
                            "_customer_id": _cid,
                        })
                if _sm_rows:
                    sales_margin_excel_df = pd.DataFrame(_sm_rows)
                    sales_margin_excel_df["고객명"] = ""
                    for _fn_u in sales_margin_excel_df["_db_fn"].dropna().unique():
                        _mu = sales_margin_excel_df["_db_fn"] == _fn_u
                        _cids_u: list[int] = []
                        for _x in sales_margin_excel_df.loc[_mu, "_customer_id"].dropna().unique():
                            try:
                                _cids_u.append(int(_x))
                            except (TypeError, ValueError):
                                pass
                        _cids_u = list(set(_cids_u))
                        if not _cids_u:
                            continue
                        try:
                            _cmap = _get_customers_by_ids_supabase(str(_fn_u), _cids_u)
                            sales_margin_excel_df.loc[_mu, "고객명"] = sales_margin_excel_df.loc[_mu, "_customer_id"].apply(
                                lambda c, m=_cmap: (m.get(int(c)) or {}).get("name", "") or "" if pd.notna(c) else ""
                            )
                        except Exception:
                            pass
                    sales_margin_excel_df = sales_margin_excel_df.drop(columns=["_db_fn", "_customer_id"], errors="ignore")
                    sales_margin_excel_df = sales_margin_excel_df.sort_values("거래일자", ascending=False).reset_index(drop=True)
                    sales_margin_monthly_df = (
                        sales_margin_excel_df.groupby("거래월", as_index=False)[["매출변동액", "마진기여액"]]
                        .sum()
                        .sort_values("거래월", ascending=False)
                        .reset_index(drop=True)
                    )
        except Exception:
            sales_margin_excel_df = pd.DataFrame()
            sales_margin_monthly_df = pd.DataFrame()

        index_col = "결제월" if query_mode == "월별/연도별 조회" else "결제일자"
        total_label = "월별 총 결제액(Total)" if query_mode == "월별/연도별 조회" else "일별 총 결제액(Total)"
        pivot = pay_df.pivot_table(index=index_col, columns="detailed_payment", values="amount", aggfunc="sum", fill_value=0, margins=False)
        pivot = pivot.fillna(0)
        
        def _col_sort_key(c):
            if str(c).startswith("신용_"): return (0, str(c))
            if str(c).startswith("체크_"): return (1, str(c))
            order = {"이체": 2, "계좌이체": 2, "지역화폐": 3, "온누리": 4, "현금": 5}
            return (order.get(c, 6), str(c))
        pivot = pivot.reindex(columns=sorted(pivot.columns, key=_col_sort_key))
        pivot[total_label] = pivot.sum(axis=1)
        total_row = pd.DataFrame(pivot.sum(axis=0)).T
        total_row.index = ["총합계"]
        if pivot.empty:
            pivot = total_row.copy()
        else:
            pivot = pd.concat([pivot, total_row], ignore_index=False)
        pivot = pivot.astype(int)

        display_df = pivot.map(lambda x: f"{x:,}" if isinstance(x, (int, float)) else str(x))
        st.dataframe(display_df, width='stretch')

        with st.expander("📒 매출 원장·마진 기여 내역 (감액·증액 대비)", expanded=False):
            st.caption(
                "매출 원장(sales) 각 행 금액이 주문 총액에서 차지하는 비율만큼, 해당 주문의 **현재** 실마진(actual_margin)을 배분한 값입니다. "
                "감액(음수) 행은 마진기여액도 음수로 표시됩니다. (경영 대시보드 월별 직원 판매 평가의 마진 배분과 동일.) "
                "주문총액·주문실마진은 조회 시점 스냅샷이며, 과거 특정일의 마진 ‘변경분’이 아니라 본 행이 현재 잔고 기준으로 마진 합계에 더하거나 빼는 몫입니다."
            )
            if sales_margin_excel_df.empty:
                st.info("선택 기간에 매출 원장 데이터가 없거나, 주문 정보를 불러오지 못했습니다.")
            else:
                st.write("**월별 요약 (매출 변동·마진 기여 합계)**")
                _sm_m_disp = sales_margin_monthly_df.copy()
                for _c in ("매출변동액", "마진기여액"):
                    if _c in _sm_m_disp.columns:
                        _sm_m_disp[_c] = _sm_m_disp[_c].apply(lambda x: f"{int(x):,}원")
                st.dataframe(_sm_m_disp, width='stretch', hide_index=True)
                st.write("**행별 상세**")
                _sm_d_disp = sales_margin_excel_df.copy()
                for _c in ("매출변동액", "주문총액", "주문실마진", "마진기여액"):
                    if _c in _sm_d_disp.columns:
                        _sm_d_disp[_c] = _sm_d_disp[_c].apply(lambda x: f"{int(x):,}원")
                if is_superadmin and selected_store == "전체 매장 통합" and "매장명" in _sm_d_disp.columns:
                    _sm_cols = ["매장명", "거래일자", "거래월", "원본주문ID", "고객명", "매출변동액", "주문총액", "주문실마진", "마진기여액", "비고", "담당직원"]
                else:
                    _sm_cols = ["거래일자", "거래월", "원본주문ID", "고객명", "매출변동액", "주문총액", "주문실마진", "마진기여액", "비고", "담당직원"]
                _sm_d_disp = _sm_d_disp[[c for c in _sm_cols if c in _sm_d_disp.columns]]
                st.dataframe(_sm_d_disp, width='stretch', hide_index=True)

        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            pivot.to_excel(writer, sheet_name="결제수단집계")
            if not sales_margin_monthly_df.empty:
                sales_margin_monthly_df.to_excel(writer, sheet_name="월별매출마진요약", index=False)
            if not sales_margin_excel_df.empty:
                sales_margin_excel_df.to_excel(writer, sheet_name="매출및마진기여", index=False)
        buf.seek(0)
        store_label = "전체매장" if (is_superadmin and selected_store == "전체 매장 통합") else selected_store.replace(" ", "_")
        if query_mode == "월별/연도별 조회":
            file_name = f"결제수단집계_{store_label}_{date_range_start.year}년.xlsx"
        else:
            file_name = f"결제수단집계_{store_label}_{date_range_start.isoformat()}_{date_range_end.isoformat()}.xlsx"
        st.download_button("엑셀 다운로드", data=buf.getvalue(), file_name=file_name, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="monthly_payment_report_dl")

        # ── 결제 입력 감사 내역 ──────────────────────────────
        if True:
            with st.expander("🔍 결제 입력 감사 내역", expanded=False):
                st.caption("결제일자 · 입력일자 · 입력자 정보를 확인할 수 있습니다. 입력 오류 발생 시 책임 소재를 특정하는 데 활용하세요.")
                _audit_df = pay_df.copy()

                # ── username → 실명 매핑 ──
                _uname_map = {}
                try:
                    _uname_map = _get_app_user_display_name_map()
                except Exception:
                    pass

                # ── created_by가 없는 행에 대해 app_payment_history에서 역조회 ──
                if "created_by" not in _audit_df.columns:
                    _audit_df["created_by"] = None
                if "order_id" in _audit_df.columns:
                    _null_mask = _audit_df["created_by"].isna() | (_audit_df["created_by"] == "")
                    if _null_mask.any() and _supabase_orders_payments_available():
                        try:
                            _null_oids = _audit_df.loc[_null_mask, "order_id"].dropna().astype(int).unique().tolist()
                            if _null_oids:
                                _ph_db_fn = db_fn if not is_superadmin else (stores[stores["store_name"] == selected_store].iloc[0]["db_filename"] if selected_store != "전체 매장 통합" else None)
                                _ph_q_base = get_supabase_client()[0].table("app_payment_history").select("sale_id, changed_by").in_("sale_id", _null_oids).order("changed_at", desc=False)
                                if _ph_db_fn:
                                    _ph_q_base = _ph_q_base.eq("db_filename", _ph_db_fn)
                                _ph_rows = (_ph_q_base.execute().data or [])
                                # sale_id별 마지막 changed_by (최초 입력자 = 첫 번째)
                                _oid_to_changer: dict = {}
                                for _ph in _ph_rows:
                                    _sid = str(_ph.get("sale_id", ""))
                                    if _sid not in _oid_to_changer:
                                        _oid_to_changer[_sid] = _ph.get("changed_by") or ""
                                def _fill_creator(row):
                                    if row.get("created_by"):
                                        return row["created_by"]
                                    return _oid_to_changer.get(str(int(row["order_id"])) if pd.notna(row.get("order_id")) else "", "") or None
                                _audit_df["created_by"] = _audit_df.apply(_fill_creator, axis=1)
                        except Exception:
                            pass

                # ── username → 실명 변환 ──
                def _resolve_name(val):
                    if not val or str(val).strip() in ("", "None", "nan"):
                        return "미상"
                    resolved = _uname_map.get(str(val).strip()) or _uname_map.get(str(val).strip().lower())
                    return resolved if resolved else str(val)
                _audit_df["입력자"] = _audit_df["created_by"].apply(_resolve_name)

                # ── 입력일자 ──
                if "created_at" in _audit_df.columns:
                    _audit_df["입력일자"] = pd.to_datetime(_audit_df["created_at"], errors="coerce").dt.strftime("%Y-%m-%d %H:%M")
                    _audit_df["입력일자"] = _audit_df["입력일자"].fillna("미상")
                else:
                    _audit_df["입력일자"] = "미상"

                # ── 결제일자 ──
                if "결제일자" not in _audit_df.columns:
                    _audit_df["결제일자"] = pd.to_datetime(_audit_df.get("payment_date"), errors="coerce").dt.strftime("%Y-%m-%d")

                # ── 결제수단 ──
                _audit_df["결제수단"] = _audit_df["detailed_payment"] if "detailed_payment" in _audit_df.columns else _audit_df.apply(_to_detailed, axis=1)

                # ── 승인번호 (메인페이·온누리·지역화폐 전용, 감사 내역에만 표시) ──
                def _get_approval(r):
                    meth = str(r.get("payment_method") or "")
                    if meth == "메인페이":
                        return str(r.get("card_company") or "")
                    if "온누리" in meth:
                        return str(r.get("onnuri_approval_code") or "")
                    if meth == "지역화폐":
                        return str(r.get("card_company") or "")
                    return ""
                _audit_df["승인번호"] = _audit_df.apply(_get_approval, axis=1)

                # ── 금액 ──
                _audit_df["금액"] = pd.to_numeric(_audit_df.get("amount", 0), errors="coerce").fillna(0).astype(int)

                # ── 표시 컬럼 선택 ──
                _show_cols = ["결제일자", "입력일자", "입력자", "결제수단", "승인번호", "금액"]
                if "_store" in _audit_df.columns:
                    _show_cols = ["매장"] + _show_cols
                    _audit_df["매장"] = _audit_df["_store"]
                _audit_show = _audit_df[[c for c in _show_cols if c in _audit_df.columns]].copy()

                # 결제일자 내림차순 정렬
                if "결제일자" in _audit_show.columns:
                    _audit_show = _audit_show.sort_values("결제일자", ascending=False)

                # 금액 포맷
                _audit_show["금액"] = _audit_show["금액"].map(lambda x: f"{x:,}" if isinstance(x, (int, float)) else str(x))

                st.dataframe(_audit_show, width='stretch', hide_index=True)

                # 엑셀 다운로드 (감사 내역) — 고객명 포함
                _abuf = io.BytesIO()
                _audit_excel = _audit_show.copy()
                _audit_excel["금액"] = _audit_excel["금액"].str.replace(",", "", regex=False)

                # order_id → 고객명 매핑 (엑셀 전용, 화면 표시 로직 무관)
                # _audit_df에 order_id와 _db_fn이 있으므로, 인덱스로 정렬해 고객명 조회
                _oid_to_cname: dict = {}
                try:
                    _sc_a, _ = get_supabase_client()
                    if _sc_a and "order_id" in _audit_df.columns:
                        _db_fn_col = "_db_fn" if "_db_fn" in _audit_df.columns else None
                        _db_fn_groups = _audit_df[_db_fn_col].dropna().unique().tolist() if _db_fn_col else [db_fn]
                        for _a_fn in _db_fn_groups:
                            if _db_fn_col:
                                _a_oids = _audit_df.loc[_audit_df[_db_fn_col] == _a_fn, "order_id"].dropna().astype(int).unique().tolist()
                            else:
                                _a_oids = _audit_df["order_id"].dropna().astype(int).unique().tolist()
                            if not _a_oids:
                                continue
                            _a_cid_map: dict = {}
                            for _a_chunk in [_a_oids[i:i+100] for i in range(0, len(_a_oids), 100)]:
                                _a_r = _sc_a.table("app_orders").select("id, customer_id").eq("db_filename", _a_fn).in_("id", _a_chunk).execute()
                                for _a_row in (_a_r.data or []):
                                    _a_cid_map[_a_row["id"]] = _a_row.get("customer_id")
                            _a_cids = [v for v in _a_cid_map.values() if v is not None]
                            if not _a_cids:
                                continue
                            _a_cust_map = _get_customers_by_ids_supabase(str(_a_fn), list(set(int(c) for c in _a_cids)))
                            for _a_oid in _a_oids:
                                _a_cid = _a_cid_map.get(_a_oid)
                                if _a_cid is not None:
                                    _oid_to_cname[_a_oid] = (_a_cust_map.get(int(_a_cid)) or {}).get("name", "")
                except Exception:
                    pass  # 고객명 조회 실패 시 빈 칸 유지 (다운로드는 정상 동작)

                # _audit_df 인덱스 기준으로 고객명 컬럼 생성 후 _audit_excel에 병합
                if "order_id" in _audit_df.columns and _oid_to_cname:
                    _cname_series = _audit_df["order_id"].apply(
                        lambda oid: _oid_to_cname.get(int(oid), "") if pd.notna(oid) else ""
                    ).reindex(_audit_excel.index, fill_value="")
                    _audit_excel.insert(0, "고객명", _cname_series.values)

                with pd.ExcelWriter(_abuf, engine="openpyxl") as _wr:
                    _audit_excel.to_excel(_wr, sheet_name="결제감사내역", index=False)
                _abuf.seek(0)
                st.download_button(
                    "📥 감사 내역 엑셀 다운로드",
                    data=_abuf.getvalue(),
                    file_name=f"결제감사내역_{store_label}_{date_range_start.isoformat()}_{date_range_end.isoformat()}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key="audit_report_dl",
                )

    # ==========================================
    # 모드 2: 직원별 판매 실적 (sales 테이블 기반 - transaction_date 기준)
    # ==========================================
    else:
        st.info("ℹ️ 직원별 판매 실적은 **sales 테이블(transaction_date 기준)**으로 집계됩니다. 주문 변경(증액/감액)도 변경 시점 월로 반영됩니다.")

        all_sales = []
        if is_superadmin and selected_store == "전체 매장 통합":
            for _, s in stores.iterrows():
                db_fn = s["db_filename"]
                df = load_sales_with_employees_cached(db_fn, start_date=date_range_start.isoformat(), end_date=date_range_end.isoformat())
                if not df.empty:
                    df["_store"] = s["store_name"]
                    df["_db_fn"] = db_fn
                    all_sales.append(df)
        else:
            db_fn = db_filename if not is_superadmin else stores[stores["store_name"] == selected_store].iloc[0]["db_filename"]
            df = load_sales_with_employees_cached(db_fn, start_date=date_range_start.isoformat(), end_date=date_range_end.isoformat())
            if not df.empty:
                df["_store"] = selected_store
                df["_db_fn"] = db_fn
                all_sales.append(df)

        if not all_sales:
            st.info("선택 기간/매장에 판매 데이터가 없습니다.")
            return

        sal_df = pd.concat(all_sales, ignore_index=True)
        sal_df["transaction_date"] = pd.to_datetime(sal_df["transaction_date"], errors="coerce")
        sal_df = sal_df[sal_df["transaction_date"].notna()]
        sal_df["_pd"] = sal_df["transaction_date"].dt.date
        sal_df = sal_df[(sal_df["_pd"] >= date_range_start) & (sal_df["_pd"] <= date_range_end)]

        try:
            _overlay_sales_df_employee_names_from_live_orders(sal_df)
        except Exception as _e:
            st.caption(f"⚠️ 직원명(주문 기준) 보완 오류: {_e}")

        if sal_df.empty:
            st.info("해당 기간에 판매 데이터가 없습니다.")
            return

        # 1. 고유 직원 목록 추출
        unique_emps = set()
        for emps in sal_df["employee_names"].dropna():
            for e in str(emps).split(","):
                if e.strip():
                    unique_emps.add(e.strip())

        emp_opts = ["전체 직원"] + sorted(list(unique_emps))
        selected_emp = st.selectbox("직원 선택 (실적 조회)", emp_opts, key="emp_perf_sel")

        # 2. 1/n 분배 로직 적용 및 상세 데이터 조립
        rows = []
        for _, r in sal_df.iterrows():
            emps = [e.strip() for e in str(r.get("employee_names") or "").split(",") if e.strip()]
            n = len(emps) if emps else 1
            if not emps:
                continue

            amt = float(r.get("amount") or 0)
            per_amt = amt / n
            per_cnt = 1.0 / n

            d_val = r["transaction_date"]
            d_str = d_val.strftime("%Y-%m-%d")
            m_str = d_val.strftime("%Y-%m")
            store_nm = r.get("_store", "")
            note_str = r.get("note") or "-"
            db_fn_val = r.get("_db_fn", "")
            oid = r.get("order_id")

            for e in emps:
                if selected_emp == "전체 직원" or selected_emp == e:
                    rows.append({
                        "매장명": store_nm,
                        "일자": d_str,
                        "월": m_str,
                        "직원명": e,
                        "비고": note_str,
                        "판매금액": per_amt,
                        "판매건수": per_cnt,
                        "원본주문ID": oid,
                        "_db_fn": db_fn_val,
                    })

        if not rows:
            st.info("선택한 직원의 판매 데이터가 없습니다.")
            return

        df_emp = pd.DataFrame(rows)

        # order_id로 고객정보 조회 (db_filename별 배치, app_orders + tenant 필터 적용)
        df_emp["고객명"] = ""
        df_emp["전화번호"] = ""
        try:
            _sc, _ = get_supabase_client()
            if _sc:
                for _fn in df_emp["_db_fn"].dropna().unique():
                    _mask = df_emp["_db_fn"] == _fn
                    _oids = df_emp.loc[_mask, "원본주문ID"].dropna().astype(int).unique().tolist()
                    if not _oids:
                        continue
                    _chunks = [_oids[i:i+100] for i in range(0, len(_oids), 100)]
                    _cid_map = {}
                    for _chunk in _chunks:
                        _r = _sc.table("app_orders").select("id, customer_id").eq("db_filename", _fn).in_("id", _chunk).execute()
                        for _row in (_r.data or []):
                            _cid_map[_row["id"]] = _row.get("customer_id")
                    _cids = [v for v in _cid_map.values() if v is not None]
                    if _cids:
                        _cust_map = _get_customers_by_ids_supabase(str(_fn), list(set(int(c) for c in _cids)))
                        df_emp.loc[_mask, "고객명"] = df_emp.loc[_mask, "원본주문ID"].apply(
                            lambda oid, cm=_cid_map, cu=_cust_map: (cu.get(int(cm.get(int(oid), -1) or -1)) or {}).get("name", "") if pd.notna(oid) and int(oid) in cm else ""
                        )
                        df_emp.loc[_mask, "전화번호"] = df_emp.loc[_mask, "원본주문ID"].apply(
                            lambda oid, cm=_cid_map, cu=_cust_map: (cu.get(int(cm.get(int(oid), -1) or -1)) or {}).get("phone1", "") if pd.notna(oid) and int(oid) in cm else ""
                        )
        except Exception as _e:
            st.caption(f"⚠️ 고객정보 조회 오류: {_e}")

        group_col = "월" if query_mode == "월별/연도별 조회" else "일자"

        # 날짜 범위 계산 (직접 날짜 지정 모드에서만 주간 상세 표시 적용)
        _date_diff = (date_range_end - date_range_start).days
        _show_inline_detail = (query_mode == "직접 날짜 지정") and (_date_diff <= 7)

        # 결제수단 조회 (1주일 이하 직접 날짜 지정 모드에서만)
        df_emp["결제수단"] = ""
        if _show_inline_detail:
            try:
                _sc, _ = get_supabase_client()
                if _sc:
                    for _fn in df_emp["_db_fn"].dropna().unique():
                        _mask = df_emp["_db_fn"] == _fn
                        _oids = df_emp.loc[_mask, "원본주문ID"].dropna().astype(int).unique().tolist()
                        if not _oids:
                            continue
                        _pay_method_map: dict = {}
                        _chunks = [_oids[i:i+100] for i in range(0, len(_oids), 100)]
                        for _chunk in _chunks:
                            _pr = _sc.table("app_payments").select(
                                "order_id, payment_method, card_company"
                            ).eq(ORDERS_PAYMENTS_TENANT_COL, _fn).in_("order_id", _chunk).execute()
                            for _prow in (_pr.data or []):
                                _oid_key = _prow.get("order_id")
                                if _oid_key is None:
                                    continue
                                _pm = str(_prow.get("payment_method") or "").strip()
                                _cc = str(_prow.get("card_company") or "").strip()
                                if _cc and _cc not in ("-", ""):
                                    _pm = f"{_pm}({_cc})" if _pm else _cc
                                if _pm:
                                    existing = _pay_method_map.get(int(_oid_key), [])
                                    if _pm not in existing:
                                        existing.append(_pm)
                                    _pay_method_map[int(_oid_key)] = existing
                        df_emp.loc[_mask, "결제수단"] = df_emp.loc[_mask, "원본주문ID"].apply(
                            lambda oid, pm=_pay_method_map: ", ".join(pm.get(int(oid), [])) if pd.notna(oid) else ""
                        )
            except Exception as _pe:
                st.caption(f"⚠️ 결제수단 조회 오류: {_pe}")

        # 3. 요약 집계 (Excel 및 >7일 화면용으로 항상 계산)
        if selected_emp == "전체 직원":
            summary = df_emp.groupby(["매장명", group_col, "직원명"], as_index=False)[["판매금액", "판매건수"]].sum()
        else:
            summary = df_emp.groupby(["매장명", group_col], as_index=False)[["판매금액", "판매건수"]].sum()
            summary.insert(2, "직원명", selected_emp)

        summary = summary.sort_values(by=["매장명", group_col, "판매금액"], ascending=[True, True, False]).reset_index(drop=True)
        summary["판매금액"] = summary["판매금액"].round(0).astype(int)
        summary["판매건수"] = summary["판매건수"].round(2)

        # 3-a. 7일 이하 직접 날짜 지정: 상세내역 단일 테이블만 표시 (고객명·전화번호·결제수단·원본주문ID 포함)
        if _show_inline_detail:
            st.write("📋 **판매 상세 내역 (결제수단 포함)**")
            detail_inline = df_emp.copy()
            detail_inline["판매금액"] = detail_inline["판매금액"].round(0).astype(int)
            detail_inline["판매건수"] = detail_inline["판매건수"].round(2)
            detail_inline = detail_inline.sort_values(by=["일자", "매장명", "직원명"], ascending=[True, True, True])
            detail_inline = detail_inline.drop(columns=[c for c in ["_db_fn", "월"] if c in detail_inline.columns])
            inline_cols = ["매장명", "일자", "직원명", "고객명", "전화번호", "결제수단", "비고", "판매금액", "판매건수", "원본주문ID"]
            detail_inline = detail_inline[[c for c in inline_cols if c in detail_inline.columns]]
            disp_inline = detail_inline.copy()
            disp_inline["판매금액"] = disp_inline["판매금액"].apply(lambda x: f"{x:,}원")
            disp_inline["판매건수"] = disp_inline["판매건수"].apply(lambda x: f"{x:g}건")
            st.dataframe(disp_inline, width='stretch')
            st.caption("※ transaction_date(판매/변경 시점) 기준. 증액/감액 delta도 포함됩니다.")
        else:
            # 3-b. 8일 이상: 요약 테이블 표시 + 상세는 다운로드 안내
            disp_df = summary.copy()
            disp_df["판매금액"] = disp_df["판매금액"].apply(lambda x: f"{x:,}원")
            disp_df["판매건수"] = disp_df["판매건수"].apply(lambda x: f"{x:g}건")
            st.write("📌 **실적 요약**")
            st.dataframe(disp_df, width='stretch')
            st.caption("※ transaction_date(판매/변경 시점) 기준. 증액/감액 delta도 포함됩니다.")
            st.info("📥 이 이상의 데이터는 다운로드 시 상세내역에서 확인가능 (고객명·전화번호·결제수단 포함)")

        # 4. 엑셀 다운로드 (다중 시트: 요약 + 상세 분리, 결제수단 포함)
        buf = io.BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            summary.to_excel(writer, sheet_name="집계요약", index=False)

            detail_df = df_emp.copy()
            detail_df["판매금액"] = detail_df["판매금액"].round(0).astype(int)
            detail_df["판매건수"] = detail_df["판매건수"].round(2)
            detail_df = detail_df.sort_values(by=["일자", "매장명", "직원명"], ascending=[False, True, True])
            detail_df = detail_df.drop(columns=[c for c in ["_db_fn"] if c in detail_df.columns])
            ordered_cols = ["매장명", "일자", "월", "직원명", "고객명", "전화번호", "결제수단", "비고", "판매금액", "판매건수", "원본주문ID"]
            detail_df = detail_df[[c for c in ordered_cols if c in detail_df.columns]]
            detail_df.to_excel(writer, sheet_name="상세내역", index=False)

        buf.seek(0)
        store_label = "전체매장" if (is_superadmin and selected_store == "전체 매장 통합") else selected_store.replace(" ", "_")
        dl_name = f"직원판매실적_상세포함_{store_label}_{date_range_start.isoformat()}_{date_range_end.isoformat()}.xlsx"
        st.download_button("📊 요약 및 상세내역 엑셀 다운로드", data=buf.getvalue(), file_name=dl_name, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", key="emp_perf_detail_dl")


# ---------- 직원 계정 관리 및 발령 (superadmin 전용) ----------
EMPLOYEE_STORE_OPTIONS = ["삼산점", "학성점", "양산점", "본사"]
EMPLOYEE_ROLE_OPTIONS = [
    ("user", "일반 직원 (user)"),
    ("store_admin", "매장 관리자 (store admin)"),
    ("superadmin", "최고 관리자 (superadmin)"),
]


def _get_store_id_by_display_name(display_name: str):
    """배정 매장 표시명(삼산점, 학성점, 양산점, 본사) → app_stores.id. 본사는 NULL. Supabase app_stores만 조회."""
    if not display_name or str(display_name).strip() == "본사":
        return None
    n = str(display_name).strip()
    stores = _get_supabase_stores_list()
    for s in stores:
        if (s.get("store_name") or "").strip() == n:
            return s.get("id")
    keyword = n.replace("점", "").strip()
    if keyword:
        for s in stores:
            if keyword in (s.get("store_name") or ""):
                return s.get("id")
    return None


def _get_store_ids_by_display_names(display_names: list):
    """배정 매장 표시명 여러 개 → [(store_id, store_name), ...] (본사 제외). Supabase app_stores만 조회."""
    result = []
    seen_ids = set()
    stores = _get_supabase_stores_list()
    for name in (display_names or []):
        n = str(name).strip()
        if not n or n == "본사":
            continue
        for s in stores:
            sn = (s.get("store_name") or "").strip()
            if sn == n or (n.replace("점", "").strip() in sn):
                sid = s.get("id")
                if sid is not None and sid not in seen_ids:
                    seen_ids.add(sid)
                    result.append((sid, sn))
                break
    return result


def render_employee_management():
    """직원 계정 관리 및 발령: Supabase Auth Admin API로 계정 생성 + 직원/매장은 Supabase app_users·app_stores 우선. superadmin 전용."""
    st.header("👥 직원 계정 관리 및 발령")

    use_supabase = ensure_supabase_app_tables()
    stores_list = _get_supabase_stores_list()
    all_stores_df = pd.DataFrame(stores_list).sort_values("store_name", ignore_index=True) if stores_list else pd.DataFrame(columns=["id", "store_name", "db_filename"])
    if "store_name" not in all_stores_df.columns and len(all_stores_df) == 0:
        all_stores_df = pd.DataFrame(columns=["id", "store_name", "db_filename"])
    if len(all_stores_df) == 0:
        client, _ = get_supabase_client()
        if client:
            st.caption("등록된 매장이 없거나 Supabase app_stores를 조회할 수 없습니다. Superadmin 매장 계정 탭에서 매장을 추가하거나, migrate_stores_to_supabase()를 실행하세요.")
    store_options = all_stores_df["store_name"].tolist() if len(all_stores_df) > 0 else []

    admin_client, admin_err = get_supabase_admin_client()
    if admin_err:
        st.error(f"⚠️ {admin_err}")
        st.caption("Supabase Dashboard → Project Settings → API에서 service_role key를 복사해 .streamlit/secrets.toml의 [supabase] service_role_key 로 추가해 주세요.")

    with st.form("employee_register_form", clear_on_submit=True):
        st.subheader("신규 직원 등록")
        emp_email = st.text_input("이메일 (로그인 ID)", placeholder="예: employee@example.com", key="emp_email")
        emp_password = st.text_input("초기 비밀번호", type="password", key="emp_password")
        emp_name = st.text_input("직원 이름", placeholder="홍길동", key="emp_name")
        emp_stores = st.multiselect(
            "배정 매장 (여러 개 선택 가능)",
            store_options,
            key="emp_stores",
            help="등록된 매장 목록에서 선택합니다. 매장이 없으면 최고관리자 메뉴 → ⑤ 매장 계정 관리에서 먼저 매장을 추가하세요.",
        )
        emp_role_choice = st.selectbox(
            "부여 권한",
            options=[r[0] for r in EMPLOYEE_ROLE_OPTIONS],
            format_func=lambda x: next((r[1] for r in EMPLOYEE_ROLE_OPTIONS if r[0] == x), x),
            key="emp_role",
        )
        submitted = st.form_submit_button("계정 생성")

        if submitted:
            if not (emp_email and str(emp_email).strip()):
                st.error("이메일을 입력해 주세요.")
            elif not emp_password:
                st.error("초기 비밀번호를 입력해 주세요.")
            elif len(emp_password) < 6:
                st.error("초기 비밀번호는 6자 이상이어야 합니다.")
            elif not (emp_name and str(emp_name).strip()):
                st.error("직원 이름을 입력해 주세요.")
            elif admin_err or admin_client is None:
                st.error("관리자 API를 사용할 수 없어 계정을 생성할 수 없습니다.")
            else:
                with st.spinner("계정 생성 중입니다..."):
                    supabase_already_exists = False
                    try:
                        admin_client.auth.admin.create_user({
                            "email": str(emp_email).strip(),
                            "password": emp_password,
                            "email_confirm": True,
                        })
                    except Exception as e:
                        err_msg = str(e).strip() or "알 수 없는 오류"
                        if "already been registered" in err_msg or "already exists" in err_msg.lower():
                            supabase_already_exists = True
                        else:
                            st.error(f"Supabase 계정 생성에 실패했습니다: {err_msg}")
                            st.stop()

                    selected_store_ids = [int(x) for x in all_stores_df[all_stores_df["store_name"].isin(emp_stores or [])]["id"].tolist()]
                    first_store_id = selected_store_ids[0] if selected_store_ids else None
                    username = str(emp_email).strip()
                    role = str(emp_role_choice).strip()
                    emp_name_val = str(emp_name).strip() if emp_name else ""

                    if use_supabase:
                        existing = _supabase_get_app_user_by_email(username)
                        try:
                            if existing:
                                user_id = int(existing["id"])
                                _supabase_update_app_user(user_id, emp_name_val, role, first_store_id, selected_store_ids)
                                st.success("이미 Supabase에 있는 이메일입니다. 직원 정보(이름, 권한, 배정 매장)만 반영했습니다. 기존 비밀번호로 로그인할 수 있습니다.")
                                clear_data_cache()
                            else:
                                user_id, ins_err = _supabase_insert_app_user(username, str(emp_email).strip(), role, first_store_id, emp_name_val)
                                if ins_err:
                                    st.error(f"직원 명부 등록 실패: {ins_err}")
                                    st.stop()
                                for sid in selected_store_ids:
                                    try:
                                        get_supabase_client()[0].table("app_user_stores").insert({"user_id": user_id, "store_id": int(sid)}).execute()
                                    except Exception:
                                        pass
                                if supabase_already_exists:
                                    st.success("이 이메일은 Supabase에 이미 있어 앱 권한만 부여했습니다. 기존 비밀번호로 로그인할 수 있습니다.")
                                else:
                                    st.success("직원 계정이 생성되었습니다. 해당 이메일과 초기 비밀번호로 로그인할 수 있습니다.")
                                clear_data_cache()
                        except Exception as e:
                            st.error(f"Supabase 직원 정보 반영 실패: {str(e)}")
                            st.stop()
                    else:
                        conn = get_master_conn()
                        try:
                            existing = conn.execute(
                                "SELECT id FROM Users WHERE email IS NOT NULL AND TRIM(LOWER(email)) = ?",
                                (username.lower(),),
                            ).fetchone()
                            if existing:
                                user_id = existing[0]
                                conn.execute(
                                    "UPDATE Users SET name = ?, role = ?, store_id = ? WHERE id = ?",
                                    (emp_name_val or None, role, first_store_id, user_id),
                                )
                                cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='UserStores'")
                                if cur.fetchone() is not None and selected_store_ids:
                                    conn.execute("DELETE FROM UserStores WHERE user_id = ?", (user_id,))
                                    for sid in selected_store_ids:
                                        conn.execute("INSERT OR IGNORE INTO UserStores (user_id, store_id) VALUES (?, ?)", (user_id, sid))
                                conn.commit()
                                st.success("이미 Supabase에 있는 이메일입니다. 직원 정보(이름, 권한, 배정 매장)만 반영했습니다. 기존 비밀번호로 로그인할 수 있습니다.")
                            else:
                                conn.execute(
                                    """
                                    INSERT INTO Users (username, password, email, role, store_id, name)
                                    VALUES (?, ?, ?, ?, ?, ?)
                                    """,
                                    (username, hashlib.sha256("supabase_managed".encode()).hexdigest(), str(emp_email).strip(), role, first_store_id, emp_name_val or None),
                                )
                                conn.commit()
                                user_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                                cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='UserStores'")
                                if cur.fetchone() is not None and selected_store_ids:
                                    for sid in selected_store_ids:
                                        conn.execute("INSERT OR IGNORE INTO UserStores (user_id, store_id) VALUES (?, ?)", (user_id, sid))
                                    conn.commit()
                                if supabase_already_exists:
                                    st.success("이 이메일은 Supabase에 이미 있어 앱 권한만 부여했습니다. 기존 비밀번호로 로그인할 수 있습니다.")
                                else:
                                    st.success("직원 계정이 생성되었습니다. 해당 이메일과 초기 비밀번호로 로그인할 수 있습니다.")
                        except sqlite3.IntegrityError:
                            conn.rollback()
                            st.error("Master DB에 이미 같은 사용자명/이메일이 등록되어 있습니다. 직원 수정 메뉴에서 기존 직원을 수정해 주세요.")
                            conn.close()
                            st.stop()
                        except Exception as e:
                            conn.rollback()
                            st.error(f"Master DB 등록에 실패했습니다: {str(e)}")
                            conn.close()
                            st.stop()
                        finally:
                            conn.close()

                st.rerun()

    st.subheader("직원 수정 · 매장 변경 · 삭제")
    if use_supabase:
        users_data = _get_supabase_users_list()
        users_list = pd.DataFrame(users_data).sort_values("username", ignore_index=True) if users_data else pd.DataFrame(columns=["id", "username", "email", "role", "name"])
        has_user_stores = True
    else:
        conn = get_master_conn()
        try:
            users_list = pd.read_sql(
                "SELECT id, username, email, role, name FROM Users ORDER BY username",
                conn,
            )
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='UserStores'")
            has_user_stores = cur.fetchone() is not None
        finally:
            conn.close()

    if len(users_list) == 0:
        st.info("등록된 직원이 없습니다. 신규 등록 후 수정/삭제할 수 있습니다.")
    else:
        def _user_label(uid):
            row = users_list[users_list["id"] == uid].iloc[0]
            name = (row.get("name") or "").strip() or row.get("username") or ""
            email = row.get("email") or ""
            return f"{name} ({email})" if email else name

        # ----- 직원 정보 수정 (이름, 권한, 배정 매장) -----
        with st.expander("✏️ 직원 정보 수정", expanded=False):
            edit_user_id = st.selectbox(
                "수정할 직원 선택",
                users_list["id"].tolist(),
                format_func=_user_label,
                key="emp_edit_user_id",
            )
            if edit_user_id:
                # 선택 직원이 바뀌면 기본 정보 수정 폼 위젯 상태를 초기화해
                # 해당 직원의 현재 DB 값이 자동으로 다시 채워지도록 한다.
                if st.session_state.get("_emp_update_loaded_user_id") != edit_user_id:
                    for _k in ("emp_update_name", "emp_update_role", "emp_update_stores", "emp_update_primary_store"):
                        st.session_state.pop(_k, None)
                    st.session_state["_emp_update_loaded_user_id"] = edit_user_id

                if use_supabase:
                    urow = next((u for u in _get_supabase_users_list() if u.get("id") == edit_user_id), None)
                    row = (urow.get("username"), urow.get("email"), urow.get("role"), urow.get("name")) if urow else None
                    current_store_ids = _get_supabase_user_store_ids(edit_user_id)
                else:
                    conn = get_master_conn()
                    try:
                        row = conn.execute(
                            "SELECT username, email, role, name FROM Users WHERE id = ?",
                            (edit_user_id,),
                        ).fetchone()
                        current_stores = conn.execute(
                            "SELECT store_id FROM UserStores WHERE user_id = ? ORDER BY store_id",
                            (edit_user_id,),
                        ).fetchall()
                        current_store_ids = [r[0] for r in current_stores]
                    finally:
                        conn.close()
                if row and len(all_stores_df) > 0:
                    with st.form(f"employee_update_form_{edit_user_id}"):
                        st.markdown("**기본 정보 수정**")
                        edit_name = st.text_input(
                            "직원 이름",
                            value=(row[3] or "").strip() or (row[0] or ""),
                            key=f"emp_update_name_{edit_user_id}",
                        )
                        edit_role = st.selectbox(
                            "권한",
                            options=[r[0] for r in EMPLOYEE_ROLE_OPTIONS],
                            format_func=lambda x: next((r[1] for r in EMPLOYEE_ROLE_OPTIONS if r[0] == x), x),
                            index=next((i for i, r in enumerate(EMPLOYEE_ROLE_OPTIONS) if r[0] == row[2]), 0),
                            key=f"emp_update_role_{edit_user_id}",
                        )
                        current_names = all_stores_df[all_stores_df["id"].isin(current_store_ids)]["store_name"].tolist()
                        edit_stores = st.multiselect(
                            "배정 매장 (여러 개 선택 가능)",
                            all_stores_df["store_name"].tolist(),
                            default=current_names,
                            key=f"emp_update_stores_{edit_user_id}",
                        )
                        # 기본 매장 지정 (로그인 시 자동 선택될 매장)
                        _cur_primary_sid = urow.get("store_id") if urow else None
                        _cur_primary_name = all_stores_df[all_stores_df["id"] == _cur_primary_sid]["store_name"].iloc[0] if (_cur_primary_sid is not None and not all_stores_df[all_stores_df["id"] == _cur_primary_sid].empty) else (current_names[0] if current_names else None)
                        _all_store_names = all_stores_df["store_name"].tolist()
                        _primary_idx = _all_store_names.index(_cur_primary_name) if _cur_primary_name in _all_store_names else 0
                        edit_primary_store = st.selectbox(
                            "🏠 기본 매장 (로그인 시 자동 선택)",
                            _all_store_names,
                            index=_primary_idx,
                            key=f"emp_update_primary_store_{edit_user_id}",
                            help="여러 매장에 배정된 경우 로그인할 때 기본으로 선택될 매장을 지정합니다.",
                        )
                        if st.form_submit_button("저장"):
                            store_ids = all_stores_df[all_stores_df["store_name"].isin(edit_stores)]["id"].tolist()
                            # 기본 매장 store_id를 first_sid로 사용
                            _primary_rows = all_stores_df[all_stores_df["store_name"] == edit_primary_store]
                            first_sid = int(_primary_rows.iloc[0]["id"]) if not _primary_rows.empty else (store_ids[0] if store_ids else None)
                            # 기본 매장이 배정 매장에 없으면 자동으로 포함
                            if first_sid and first_sid not in store_ids:
                                store_ids = [first_sid] + store_ids
                            try:
                                if use_supabase:
                                    _supabase_update_app_user(edit_user_id, (edit_name or "").strip() or None, edit_role, first_sid, store_ids)
                                    clear_data_cache()
                                    st.success(f"직원 정보가 저장되었습니다. 기본 매장: {edit_primary_store}")
                                else:
                                    conn = get_master_conn()
                                    try:
                                        conn.execute(
                                            "UPDATE Users SET name = ?, role = ?, store_id = ? WHERE id = ?",
                                            ((edit_name or "").strip() or None, edit_role, first_sid, edit_user_id),
                                        )
                                        conn.execute("DELETE FROM UserStores WHERE user_id = ?", (edit_user_id,))
                                        for sid in store_ids:
                                            conn.execute("INSERT OR IGNORE INTO UserStores (user_id, store_id) VALUES (?, ?)", (edit_user_id, sid))
                                        conn.commit()
                                        clear_data_cache()
                                        st.success(f"직원 정보가 저장되었습니다. 기본 매장: {edit_primary_store}")
                                    finally:
                                        conn.close()
                                st.rerun()
                            except Exception as e:
                                st.error(f"저장 실패: {str(e)}")

                    # ----- 이메일(로그인 ID) 변경 -----
                    with st.form(f"emp_email_update_form_{edit_user_id}"):
                        st.markdown("**이메일(로그인 ID) 변경**")
                        st.caption("현재 이메일 → 새 이메일로 변경합니다. Supabase Auth + app_users 양쪽 모두 반영됩니다.")
                        st.text_input("현재 이메일", value=(row[1] or row[0] or "").strip(), disabled=True, key=f"emp_cur_email_display_{edit_user_id}")
                        new_email_input = st.text_input("새 이메일", placeholder="새 이메일 주소를 입력하세요", key=f"emp_new_email_input_{edit_user_id}")
                        if st.form_submit_button("이메일 변경"):
                            new_email_clean = (new_email_input or "").strip()
                            if not new_email_clean or "@" not in new_email_clean:
                                st.error("올바른 이메일 주소를 입력해 주세요.")
                            else:
                                cur_email = (row[1] or row[0] or "").strip()
                                email_errs = []
                                # Supabase Auth 이메일 변경
                                if use_supabase and admin_client and not admin_err and cur_email:
                                    try:
                                        auth_uid = _supabase_auth_uid_by_email(admin_client, cur_email)
                                        if auth_uid:
                                            admin_client.auth.admin.update_user_by_id(auth_uid, {"email": new_email_clean})
                                        else:
                                            email_errs.append("Supabase Auth 계정 없음(app_users만 변경)")
                                    except Exception as e:
                                        email_errs.append(f"Auth 변경 실패: {e}")
                                # app_users 이메일·username 변경
                                if use_supabase:
                                    try:
                                        client, _ce = get_supabase_client()
                                        if client:
                                            client.table("app_users").update({
                                                "email": new_email_clean,
                                                "username": new_email_clean,
                                            }).eq("id", edit_user_id).execute()
                                    except Exception as e:
                                        email_errs.append(f"app_users 변경 실패: {e}")
                                if email_errs:
                                    st.warning("일부 변경 실패: " + " / ".join(email_errs))
                                else:
                                    clear_data_cache()
                                    st.success(f"이메일이 {new_email_clean} 으로 변경되었습니다.")
                                    st.rerun()

                    # ----- 비밀번호 리셋 (동일 직원 대상) -----
                    with st.form(f"emp_password_reset_form_{edit_user_id}"):
                        st.caption("🔐 비밀번호 리셋 · 위에서 선택한 직원의 로그인 비밀번호를 변경합니다.")
                        st.text_input("대상 (이메일)", value=(row[1] or row[0] or "").strip(), disabled=True, key=f"emp_pw_target_display_{edit_user_id}")
                        emp_reset_pw = st.text_input("새 비밀번호", type="password", key=f"emp_reset_pw_{edit_user_id}")
                        emp_reset_pw_confirm = st.text_input("새 비밀번호 확인", type="password", key=f"emp_reset_pw_confirm_{edit_user_id}")
                        if st.form_submit_button("비밀번호 변경"):
                            if not emp_reset_pw or len(emp_reset_pw) < 6:
                                st.error("비밀번호는 6자 이상 입력해 주세요.")
                            elif emp_reset_pw != emp_reset_pw_confirm:
                                st.error("새 비밀번호가 일치하지 않습니다.")
                            else:
                                target_email = (row[1] or row[0] or "").strip()
                                if not target_email:
                                    st.error("대상 직원의 이메일을 찾을 수 없습니다.")
                                elif use_supabase and admin_client and not admin_err:
                                    try:
                                        auth_uid = _supabase_auth_uid_by_email(admin_client, target_email)
                                        if auth_uid:
                                            admin_client.auth.admin.update_user_by_id(auth_uid, {"password": emp_reset_pw})
                                            st.success("비밀번호가 변경되었습니다. 해당 직원은 다음 로그인부터 새 비밀번호를 사용합니다.")
                                            clear_data_cache()
                                            st.rerun()
                                        else:
                                            # app_users에는 있으나 Supabase Auth에 없는 레거시 계정 복구:
                                            # 관리자 권한으로 Auth 계정을 생성하고 입력한 비밀번호를 즉시 적용한다.
                                            try:
                                                created = admin_client.auth.admin.create_user({
                                                    "email": target_email,
                                                    "password": emp_reset_pw,
                                                    "email_confirm": True,
                                                })
                                                created_user = getattr(created, "user", None) or (created.get("user") if isinstance(created, dict) else None)
                                                created_uid = getattr(created_user, "id", None) if created_user is not None and hasattr(created_user, "id") else (created_user.get("id") if isinstance(created_user, dict) else None)
                                                if created_uid:
                                                    st.success("Supabase Auth 계정이 없어 새로 생성한 뒤 비밀번호를 설정했습니다. 해당 직원은 즉시 로그인할 수 있습니다.")
                                                    clear_data_cache()
                                                    st.rerun()
                                                else:
                                                    st.warning("Supabase Auth 계정 조회/생성에 실패했습니다. 관리자에게 문의해 주세요.")
                                            except Exception as ce:
                                                ce_msg = str(ce)
                                                # "이미 등록된 이메일"이면 조회 누락 가능성이 높으므로 재조회 후 비밀번호 변경 재시도
                                                if "already been registered" in ce_msg.lower() or "already registered" in ce_msg.lower():
                                                    try:
                                                        auth_uid_retry = _supabase_auth_uid_by_email(admin_client, target_email)
                                                        if auth_uid_retry:
                                                            admin_client.auth.admin.update_user_by_id(auth_uid_retry, {"password": emp_reset_pw})
                                                            st.success("기존 Supabase Auth 계정을 찾아 비밀번호를 변경했습니다.")
                                                            clear_data_cache()
                                                            st.rerun()
                                                        else:
                                                            st.error("Supabase Auth에는 이메일이 이미 등록되어 있지만 사용자 조회에 실패했습니다. 잠시 후 다시 시도해 주세요.")
                                                    except Exception as re:
                                                        st.error(f"Supabase Auth 재조회/비밀번호 변경 실패: {str(re)}")
                                                else:
                                                    st.error(f"Supabase Auth 계정 생성 실패: {ce_msg}")
                                    except Exception as e:
                                        st.error(f"비밀번호 변경 실패: {str(e)}")
                                elif not use_supabase:
                                    try:
                                        conn = get_master_conn()
                                        pw_hash = hashlib.sha256(emp_reset_pw.encode()).hexdigest()
                                        conn.execute("UPDATE Users SET password = ? WHERE id = ?", (pw_hash, edit_user_id))
                                        conn.commit()
                                        conn.close()
                                        st.success("비밀번호가 변경되었습니다.")
                                        st.rerun()
                                    except Exception as e:
                                        st.error(f"비밀번호 변경 실패: {str(e)}")
                                else:
                                    st.error("비밀번호 변경을 위해 Supabase service_role_key(관리자 API)가 필요합니다.")

        # ----- 직원 매장 변경 (배정 매장만 빠르게 변경) -----
        if has_user_stores and len(all_stores_df) > 0:
            with st.expander("🏪 직원 매장 변경 (배정 매장만)", expanded=False):
                store_edit_user_id = st.selectbox(
                    "매장을 변경할 직원 선택",
                    users_list["id"].tolist(),
                    format_func=_user_label,
                    key="emp_store_change_user_id",
                )
                if store_edit_user_id:
                    if use_supabase:
                        current_ids = _get_supabase_user_store_ids(store_edit_user_id)
                    else:
                        conn = get_master_conn()
                        try:
                            current = conn.execute(
                                "SELECT store_id FROM UserStores WHERE user_id = ? ORDER BY store_id",
                                (store_edit_user_id,),
                            ).fetchall()
                            current_ids = [r[0] for r in current]
                        finally:
                            conn.close()
                    current_names = all_stores_df[all_stores_df["id"].isin(current_ids)]["store_name"].tolist()
                    edited_stores = st.multiselect(
                        "배정 매장 (여러 개 선택 가능)",
                        all_stores_df["store_name"].tolist(),
                        default=current_names,
                        key="emp_edit_stores",
                    )
                    # 기본 매장 지정
                    _su = next((x for x in _get_supabase_users_list() if x.get("id") == store_edit_user_id), None) if use_supabase else None
                    _cur_primary_sid2 = _su.get("store_id") if _su else (current_ids[0] if current_ids else None)
                    _cur_primary_name2 = all_stores_df[all_stores_df["id"] == _cur_primary_sid2]["store_name"].iloc[0] if (_cur_primary_sid2 is not None and not all_stores_df[all_stores_df["id"] == _cur_primary_sid2].empty) else (current_names[0] if current_names else None)
                    _all_store_names2 = all_stores_df["store_name"].tolist()
                    _primary_idx2 = _all_store_names2.index(_cur_primary_name2) if _cur_primary_name2 in _all_store_names2 else 0
                    edit_primary_store2 = st.selectbox(
                        "🏠 기본 매장 (로그인 시 자동 선택)",
                        _all_store_names2,
                        index=_primary_idx2,
                        key="emp_edit_primary_store",
                        help="로그인할 때 기본으로 선택될 매장입니다.",
                    )
                    if st.button("배정 매장 저장", key="emp_edit_save_btn"):
                        store_ids = all_stores_df[all_stores_df["store_name"].isin(edited_stores)]["id"].tolist()
                        _prows2 = all_stores_df[all_stores_df["store_name"] == edit_primary_store2]
                        first_sid = int(_prows2.iloc[0]["id"]) if not _prows2.empty else (store_ids[0] if store_ids else None)
                        if first_sid and first_sid not in store_ids:
                            store_ids = [first_sid] + store_ids
                        try:
                            if use_supabase:
                                u = next((x for x in _get_supabase_users_list() if x.get("id") == store_edit_user_id), None)
                                cur_name = (u.get("name") or "").strip() if u else None
                                cur_role = u.get("role") if u else "user"
                                _supabase_update_app_user(store_edit_user_id, cur_name, cur_role, first_sid, store_ids)
                                clear_data_cache()
                                st.success(f"배정 매장이 저장되었습니다. 기본 매장: {edit_primary_store2}")
                            else:
                                conn = get_master_conn()
                                try:
                                    conn.execute("DELETE FROM UserStores WHERE user_id = ?", (store_edit_user_id,))
                                    for sid in store_ids:
                                        conn.execute("INSERT OR IGNORE INTO UserStores (user_id, store_id) VALUES (?, ?)", (store_edit_user_id, sid))
                                    conn.execute("UPDATE Users SET store_id = ? WHERE id = ?", (first_sid, store_edit_user_id))
                                    conn.commit()
                                    clear_data_cache()
                                    st.success("배정 매장이 저장되었습니다.")
                                finally:
                                    conn.close()
                            st.rerun()
                        except Exception as e:
                            if not use_supabase:
                                try:
                                    conn.rollback()
                                    conn.close()
                                except Exception:
                                    pass
                            st.error(f"저장 실패: {str(e)}")

        # ----- 직원 삭제 -----
        with st.expander("🗑️ 직원 삭제", expanded=False):
            del_user_id = st.selectbox(
                "삭제할 직원 선택",
                users_list["id"].tolist(),
                format_func=_user_label,
                key="emp_del_user_id",
            )
            if del_user_id:
                del_email = users_list[users_list["id"] == del_user_id].iloc[0].get("email") or ""
                st.warning(f"**{_user_label(del_user_id)}** 직원을 삭제하면 로그인할 수 없습니다. Supabase 계정도 삭제됩니다.")
                del_confirm = st.checkbox("위 직원을 삭제하는 것에 동의합니다.", key="emp_del_confirm")
                if st.button("직원 삭제", key="emp_del_btn", type="primary"):
                    if not del_confirm:
                        st.error("삭제하려면 동의 체크박스를 선택해 주세요.")
                    else:
                        with st.spinner("삭제 중..."):
                            try:
                                if admin_client and not admin_err and del_email:
                                    try:
                                        uid = _supabase_auth_uid_by_email(admin_client, del_email)
                                        if uid:
                                            admin_client.auth.admin.delete_user(uid)
                                    except Exception:
                                        pass
                                if use_supabase:
                                    _supabase_delete_app_user(del_user_id)
                                    clear_data_cache()
                                    st.success("직원이 삭제되었습니다.")
                                else:
                                    conn = get_master_conn()
                                    conn.execute("DELETE FROM UserStores WHERE user_id = ?", (del_user_id,))
                                    conn.execute("DELETE FROM Users WHERE id = ?", (del_user_id,))
                                    conn.commit()
                                    conn.close()
                                    clear_data_cache()
                                    st.success("직원이 삭제되었습니다.")
                                st.rerun()
                            except Exception as e:
                                try:
                                    conn.rollback()
                                    conn.close()
                                except Exception:
                                    pass
                                st.error(f"삭제 실패: {str(e)}")

    st.subheader("직원 명부")
    if use_supabase:
        emp_list = _get_supabase_employee_list_with_stores()
        df = pd.DataFrame(emp_list) if emp_list else pd.DataFrame(columns=["id", "email", "username", "name", "role", "배정매장", "기본매장"])
    else:
        conn = get_master_conn()
        try:
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='UserStores'")
            has_user_stores = cur.fetchone() is not None
            if has_user_stores:
                users_df = pd.read_sql("SELECT u.id, u.email, u.username, u.name, u.role, u.store_id FROM Users u ORDER BY u.id", conn)
                us_rows = conn.execute("SELECT user_id, store_id FROM UserStores").fetchall()
                store_ids_by_user = {}
                for ur, sr in us_rows:
                    store_ids_by_user.setdefault(ur, []).append(sr)
                stores_map = {s["id"]: s.get("store_name") for s in _get_supabase_stores_list()}
                def _fmt_stores(uid, single_sid):
                    sids = store_ids_by_user.get(uid) or ([single_sid] if single_sid else [])
                    names = [stores_map.get(sid) or str(sid) for sid in sids if sid]
                    return ", ".join(names) if names else "-"
                users_df["배정매장"] = users_df.apply(lambda r: _fmt_stores(r["id"], r.get("store_id")), axis=1)
                df = users_df.drop(columns=["store_id"], errors="ignore")
            else:
                users_df = pd.read_sql("SELECT id, email, username, name, role, store_id FROM Users ORDER BY id", conn)
                stores_map = {s["id"]: s.get("store_name") for s in _get_supabase_stores_list()}
                users_df["배정매장"] = users_df["store_id"].map(lambda sid: stores_map.get(sid) or "-" if pd.notna(sid) and sid else "-")
                df = users_df.drop(columns=["store_id"], errors="ignore")
        finally:
            conn.close()

    if len(df) == 0:
        st.info("등록된 직원이 없습니다.")
        return

    role_display = df["role"].map(lambda r: next((x[1] for x in EMPLOYEE_ROLE_OPTIONS if x[0] == r), r))
    df_display = df.copy()
    df_display["권한"] = role_display
    if "name" in df_display.columns:
        df_display["사용자명"] = df_display.apply(
            lambda r: (str(r.get("name") or "").strip() or str(r.get("username") or "")),
            axis=1,
        )
    else:
        df_display["사용자명"] = df_display["username"]
    df_display["배정매장"] = df_display["배정매장"].fillna("")
    if "기본매장" in df_display.columns:
        df_display["기본매장"] = df_display["기본매장"].fillna("")
        df_display = df_display[["id", "email", "사용자명", "권한", "기본매장", "배정매장"]]
        df_display.columns = ["ID", "이메일", "사용자명", "권한", "기본 매장 (로그인 시)", "배정 매장 (전체)"]
    else:
        df_display = df_display[["id", "email", "사용자명", "권한", "배정매장"]]
        df_display.columns = ["ID", "이메일", "사용자명", "권한", "배정 매장"]
    st.dataframe(df_display, width='stretch')


CONFIRM_DATA_RESET_PHRASE = "데이터를 모두 초기화합니다"
CONFIRM_DATA_RESET_STORE_PREFIX = "매장 초기화:"


def _danger_zone_delete_store_data(client, db_fn: str, store_name: str, scope: str) -> list[str]:
    """단일 매장의 데이터를 scope에 따라 삭제. 반환: 오류 메시지 목록."""
    errors = []
    sales_col = _sales_tenant_column()

    # 1) 결제 내역 (app_payments) — 반드시 orders 전에 삭제
    try:
        client.table("app_payments").delete().eq(ORDERS_PAYMENTS_TENANT_COL, db_fn).execute()
    except Exception as e:
        errors.append(f"app_payments({db_fn}): {e}")

    # 2) 매출 통계 (sales) — order_id 기반 또는 tenant 컬럼 기반
    try:
        if sales_col:
            client.table("sales").delete().eq(sales_col, db_fn).execute()
        else:
            # tenant 컬럼 없으면 order_id 목록을 먼저 조회 후 삭제
            try:
                oids_r = client.table("app_orders").select("id").eq(ORDERS_PAYMENTS_TENANT_COL, db_fn).execute()
                oids = [x["id"] for x in (oids_r.data or [])]
                if oids:
                    client.table("sales").delete().in_("order_id", oids).execute()
            except Exception as e2:
                errors.append(f"sales(order_id 방식)({db_fn}): {e2}")
    except Exception as e:
        errors.append(f"sales({db_fn}): {e}")

    # 3) 주문 (app_orders)
    try:
        client.table("app_orders").delete().eq(ORDERS_PAYMENTS_TENANT_COL, db_fn).execute()
    except Exception as e:
        errors.append(f"app_orders({db_fn}): {e}")

    # 4) 고객 정보 (app_customers) — scope에 포함된 경우만
    if scope in ("customers", "full"):
        try:
            client.table("app_customers").delete().eq("store_name", store_name).execute()
        except Exception as e:
            errors.append(f"app_customers({store_name}): {e}")

    # 5) 알림·삭제요청 기록 (app_edit_requests) — full scope만
    if scope == "full":
        try:
            client.table("app_edit_requests").delete().eq("db_filename", db_fn).execute()
        except Exception as e:
            errors.append(f"app_edit_requests({db_fn}): {e}")

    return errors


def _superadmin_tab_danger_zone_data_reset():
    """
    Superadmin 전용 Danger Zone: 매장별 또는 전체 영업 데이터 초기화.
    삭제 대상: app_payments → sales → app_orders → (app_customers) → (app_edit_requests)
    app_users, app_stores 등 마스터 데이터는 절대 삭제하지 않음.
    """
    # superadmin 이중 확인
    if st.session_state.get("role") != "superadmin":
        st.error("이 기능은 최고 관리자(superadmin)만 사용할 수 있습니다.")
        return

    st.warning(
        "**⚠️ Danger Zone** — 이 화면의 모든 작업은 **되돌릴 수 없습니다.**  \n"
        "직원·매장 계정 정보는 삭제되지 않습니다."
    )

    stores_list = _get_supabase_stores_list()
    if not stores_list:
        st.info("등록된 매장이 없습니다.")
        return

    # ── 모드 선택 ──
    reset_mode = st.radio(
        "초기화 범위",
        ["전체 매장 초기화", "특정 매장만 초기화"],
        key="dz_mode_radio",
        horizontal=True,
    )

    # ── 매장 선택 (매장별 모드) ──
    selected_store = None
    if reset_mode == "특정 매장만 초기화":
        store_options = {s["store_name"]: s for s in stores_list if s.get("store_name")}
        selected_store_name = st.selectbox(
            "초기화할 매장 선택",
            list(store_options.keys()),
            key="dz_store_select",
        )
        selected_store = store_options.get(selected_store_name)

    # ── 삭제 범위 선택 ──
    st.markdown("**삭제 범위 선택**")
    scope_label = st.radio(
        "삭제 범위",
        [
            "주문·결제·통계만 삭제 (고객 정보 유지)",
            "고객 정보도 함께 삭제",
            "알림·요청 기록까지 완전 초기화",
        ],
        key="dz_scope_radio",
        label_visibility="collapsed",
    )
    scope_map = {
        "주문·결제·통계만 삭제 (고객 정보 유지)": "orders",
        "고객 정보도 함께 삭제": "customers",
        "알림·요청 기록까지 완전 초기화": "full",
    }
    scope = scope_map[scope_label]

    # ── 삭제 대상 요약 ──
    deleted_tables = ["app_payments", "sales", "app_orders"]
    if scope in ("customers", "full"):
        deleted_tables.append("app_customers")
    if scope == "full":
        deleted_tables.append("app_edit_requests")

    target_store_label = "전체 매장" if reset_mode == "전체 매장 초기화" else f"**{selected_store['store_name'] if selected_store else '-'}**"
    st.info(
        f"삭제 대상: {target_store_label}  \n"
        f"삭제 테이블: `{'`, `'.join(deleted_tables)}`  \n"
        f"유지 테이블: `app_users`, `app_stores` (직원·매장 계정)"
    )

    # ── 확인 문구 입력 ──
    with st.expander("🔐 초기화 실행 (확인 문구 입력 필요)", expanded=False):
        st.caption(f'아래 문구를 **정확히** 입력해야만 실행 버튼이 활성화됩니다.')

        if reset_mode == "전체 매장 초기화":
            required_phrase = CONFIRM_DATA_RESET_PHRASE
            st.code(required_phrase)
        else:
            sname = selected_store["store_name"] if selected_store else ""
            required_phrase = f"{CONFIRM_DATA_RESET_STORE_PREFIX} {sname}"
            st.code(required_phrase)

        confirm_input = st.text_input(
            "확인 문구 입력",
            placeholder="위 문구를 그대로 입력하세요",
            key="danger_zone_confirm_input",
        )
        phrase_ok = (confirm_input or "").strip() == required_phrase

        if st.button(
            "🗑️ 초기화 실행",
            key="danger_zone_execute_btn",
            disabled=not phrase_ok,
            type="primary",
        ):
            client, err = get_supabase_client()
            if err or not client:
                st.error("Supabase 연결에 실패했습니다.")
                return

            all_errors = []
            deleted_stores = []

            if reset_mode == "전체 매장 초기화":
                target_stores = stores_list
            else:
                target_stores = [selected_store] if selected_store else []

            progress = st.progress(0, text="초기화 중...")
            for i, s in enumerate(target_stores):
                db_fn = s.get("db_filename")
                sname = s.get("store_name") or db_fn
                if not db_fn:
                    continue
                store_name_val = _get_store_name_by_db(db_fn) or sname
                errs = _danger_zone_delete_store_data(client, db_fn, store_name_val, scope)
                all_errors.extend(errs)
                deleted_stores.append(sname)
                progress.progress((i + 1) / len(target_stores), text=f"{sname} 처리 완료")

            clear_data_cache()
            progress.empty()

            if all_errors:
                st.warning("일부 삭제 실패:\n" + "\n".join(all_errors[:10]))
            else:
                st.success(
                    f"✅ 초기화 완료!  \n"
                    f"처리 매장: {', '.join(deleted_stores)}  \n"
                    f"삭제 테이블: {', '.join(deleted_tables)}"
                )
            st.rerun()


# 앱 내 FAQ (검색 대상: 제목·본문·keywords). 항목 추가 시 이 리스트만 수정하면 됩니다.
APP_FAQ_ITEMS: list[dict[str, str]] = [
    {
        "title": "오늘 입력해도 어제 매출로 잡히나요?",
        "keywords": "계약일 어제 매출 집계 오늘 입력 날짜",
        "body": (
            "계약일을 **어제**로 선택했을 때 그렇게 잡힙니다. "
            "계약일을 **오늘**로 두면 오늘 매출로 집계됩니다."
        ),
    },
    {
        "title": "브라우저를 닫았다가 다시 열면 계약일이 또 오늘로 돌아가나요?",
        "keywords": "브라우저 기본값 계약일 세션 초기화",
        "body": (
            "처음 들어올 때 기본값이 오늘일 수 있습니다. "
            "**등록할 때마다** 실제 계약일인지 **계약일** 필드를 확인하는 습관을 두면 좋습니다."
        ),
    },
    {
        "title": "계약은 어제인데, 실제 입금은 오늘만 가능한 경우는요?",
        "keywords": "입금 결제일 계약일 다름 신규 매출 결제변경",
        "body": (
            "현재 **신규 매출 등록** 화면에서는 첫 결제의 **결제일**이 **계약일**과 같게 들어갑니다.\n\n"
            "계약일과 입금일을 다르게 남겨야 하는 경우는, 일단 **1차 계약금 입금 기준**으로 매출 날짜를 작성하고, "
            "추후 **결제 변경** 메뉴에서 수정해 주세요."
        ),
    },
    {
        "title": "어제의 매출을 오늘 날짜로 등록하려면요? (예: 4/30 매출을 5/1로 등록)",
        "keywords": "4/30 5/1 계약일 배송일 새로운 매출 등록 체크리스트",
        "body": (
            "집계·통계에 반영되게 하려면 **실제 계약이 이루어진 날**을 **계약일**에 넣는 것이 원칙입니다. "
            "아래는 **새로운 매출 등록** 시 확인할 절차입니다.\n\n"
            "1. **새로운 매출 등록** 화면에 들어갑니다.\n"
            "2. **계약일** = 실제 계약이 있었던 날(예: 어제, 또는 해당 영업일).\n"
            "3. **배송일** = 실제 또는 예정에 맞게 수정합니다.\n"
            "4. 고객·금액·결제 정보를 확인합니다.\n"
            "5. **매출 등록**을 누릅니다.\n\n"
            "**참고:** 특정 일자로 ‘보이게’만 바꾸고 싶다면 계약일·매출 원장·초기 결제일이 함께 따라가므로, "
            "운영 규정에 맞는 날짜인지 꼭 확인하세요."
        ),
    },
    {
        "title": "월별 직원 평가(KPI) 종합 점수는 어떻게 나뉘나요?",
        "keywords": (
            "KPI 직원 평가 매출 현금수금 마진 전시품 종합점수 배점 70 15 5 10 HR 대시보드 "
            "월별 판매 현황"
        ),
        "body": (
            "종합 점수는 **네 가지 합**으로 **100점 만점**입니다.\n\n"
            "- **매출 점수 70점**\n"
            "- **마진 점수 15점**\n"
            "- **전시품 판매 점수 5점**\n"
            "- **현금수금 점수 10점** (결제일 기준, 수수료 없는 수납: 이체·온누리·지역화폐·현금)\n\n"
            "**경영 대시보드**의 「3. 월별 직원 판매 현황 및 평가」와 "
            "**최고 관리자 → 매장별 직원 평가(HR)** 에서 같은 기준으로 집계합니다. "
            "(HR은 선택한 **단일 월** 또는 **연월 범위** 기준입니다.)"
        ),
    },
    {
        "title": "매출 점수(70점)와 마진·전시 점수는 무엇을 기준으로 하나요?",
        "keywords": (
            "매출 순액 감액 음수 sales 판매일 transaction_date 마진 전시품 주문 비율 "
            "매출집계 순액"
        ),
        "body": (
            "**매출 점수(70)**\n\n"
            "- **매출 원장(sales)**의 **판매일(`transaction_date`)**이 집계 월(또는 HR에서 선택한 기간)에 들어가는 행만 사용합니다.\n"
            "- 각 행 **금액(`amount`)**은 **감액 등 음수**도 그대로 반영된 **순액**입니다.\n"
            "- 해당 행에 적힌 담당 직원이 여 명이면 금액을 **직원 수로 나눈 몫(1/n)** 이 각 직원 매출로 잡힙니다.\n"
            "- 기간(또는 월) 전체에서 직원별로 합친 뒤, 그 비율로 **70점**을 나눕니다.\n\n"
            "**마진(20)·전시품(10)**\n\n"
            "- 같은 **sales** 구간의 행을, 연결된 **주문**의 `total_amount` 등에 대해 **비율로 나눠** 직원에게 배분합니다. "
            "(대시보드 월별 KPI와 동일한 방식입니다.)\n"
            "- 주문 합계가 0인 특수한 경우에는 **메모(note)에 있는 KPI용 마진 차액 표식**이 반영될 수 있습니다."
        ),
    },
    {
        "title": "현금수금집계에는 어떤 결제가 들어가나요? (KPI 종합 점수와의 관계)",
        "keywords": (
            "현금수금 결제일 payment_date 온누리 지역화폐 이체 현금 신용카드 체크카드 메인페이 "
            "현금수금집계 수수료"
        ),
        "body": (
            "- **결제일(`payment_date`)**이 집계 월(또는 HR 기간)에 들어가는 결제만 합산합니다.\n"
            "- **수수료가 붙는 결제는 제외**합니다. 전산 기준으로 **신용카드·메인페이(2.5%)·체크카드(1.5%)** 는 "
            "**현금수금집계에 포함되지 않습니다.**\n"
            "- **수수료 0%** 인 **계좌이체(이체), 온누리·온누리지류, 지역화폐, 현금(수금)** 만 포함됩니다.\n"
            "- 결제 금액도 주문 담당 직원이 여러 명이면 **1/n**으로 나눈 뒤 직원별로 합산합니다.\n"
            "- KPI 표의 **현금수금집계** 열은 위 기준의 **금액 참고**이며, **종합 점수(매출70+마진20+전시10)에는 넣지 않습니다.**"
        ),
    },
    {
        "title": "대시보드 ‘해당 기간 총 계약 금액’과 KPI 매출이 다른 이유가 있나요?",
        "keywords": "총 계약 금액 통계 기간별 KPI 차이 sales 순액",
        "body": (
            "- 「4. 기간별 통계」의 **해당 기간 총 계약 금액**은 선택 기간의 **sales 순액 합(직원 나누기 전)** 입니다.\n"
            "- KPI **매출 점수(70)** 는 같은 **sales·판매일** 기준이지만 **직원별 1/n 배분 후 비율**로 점수를 매깁니다.\n"
            "- **현금수금집계** 는 **결제일·결제수단**으로 따로 집계한 **참고 금액**이므로, 위 숫자들과 **항상 같지는 않습니다.**"
        ),
    },
    {
        "title": "‘주문 취소’(매출·마진 0원)와 주문 삭제는 무엇이 다른가요?",
        "keywords": (
            "주문 취소 삭제 0원 매출 마진 주문 정보 수정 결제 변경 감액 삭제요청 관리자 승인 "
            "영구 삭제 오등록 테스트"
        ),
        "body": (
            "**1) 매출·마진 0원으로 정리하는 경우 (주문은 남김)**\n\n"
            "「**주문 정보 수정**」에서 일반·전시 판매가 등을 조정해 **총 계약금액이 0원**이 되게 하면, "
            "화면에 계산되는 **기본 마진도 0원**에 맞춰집니다. 이 경우 **주문 번호와 주문 레코드는 그대로** 남습니다. "
            "수정 사유·변경 이력·결제 변경 기록 등 **추적**이 가능합니다.\n\n"
            "이미 결제가 잡혀 있다면 잔액이 맞도록 **결제 변경** 메뉴에서 금액을 줄이거나, "
            "안내에 따라 **0원으로 두어 결제 취소** 처리하는 식으로 맞춰야 할 수 있습니다. "
            "(계약금을 줄여 결제 합계보다 작아지면, 화면에서 **감액할 결제**를 지정하는 절차가 나올 수 있습니다.)\n\n"
            "**2) 주문 삭제**\n\n"
            "직원이 **삭제 요청**을 보내고, **매장 관리자 또는 최고 관리자가 승인**하면 해당 주문이 **시스템에서 영구 삭제**됩니다. "
            "연결된 **결제 데이터도 함께 삭제**됩니다. **Supabase(클라우드) 저장**을 쓰는 경우, **매출 원장(sales)** 에서 "
            "해당 주문에 묶인 데이터 삭제도 함께 시도합니다. 목록·통계에서는 주문이 **없던 것처럼** 보입니다.\n\n"
            "**어떤 때 무엇을 쓰나요?**\n\n"
            "- 계약이 무효·취소되었지만 **남겨 둘 이력**이 필요하면 → **0원 정리(주문 수정)** 을 검토하세요.\n"
            "- **완전 오등록·테스트·중복**처럼 처음부터 없어야 할 건이면 → **삭제 요청**을 검토하세요."
        ),
    },
    {
        "title": "특수 등록의 위약금·직원 구매는 무엇인가요?",
        "keywords": (
            "특수등록 특수 등록 위약금 직원구매 직원 구매 KPI 매출 수금집계 수금 계약취소 취소 "
            "신규 매출"
        ),
        "body": (
            "「**신규 매출 등록**」화면의 **⚡ 특수 등록 (위약금 / 직원 구매)** 에서 등록합니다.\n\n"
            "**공통**\n\n"
            "- 둘 다 **통상적인 매장 영업 매출**이나 **직원 평가(KPI)** 에 넣지 않는 성격의 건으로 쓰입니다.\n\n"
            "**위약금**\n\n"
            "- **고객이 계약을 취소한 뒤** 실제로 **위약금이 발생·수령된 경우**에만 등록합니다.\n"
            "- **실제 수금 금액**에 맞춰 입력해 장부·입금 흐름을 맞추는 용도입니다.\n"
            "- **결제일·결제수단 기준 수금 집계**(예: KPI 표의 **현금수금집계** 참고 열)에는 **포함**될 수 있습니다. "
            "(KPI **매출 점수(70)** 등 sales·판매일 기준 영업 매출과는 별개입니다.)\n"
            "- 일반 상품 판매로 보지 않으므로, **취소 후 위약금이 있을 때만** 특수 등록의 위약금으로 넣어 주세요.\n\n"
            "**직원 구매**\n\n"
            "- 직원이 매장 상품을 살 때 **판매가 = 원가(구매가)** 로 등록되어 **마진 0원**으로 남습니다.\n"
            "- 화면 안내와 같이 **통상 매출로 계상하지 않는** 유형입니다.\n\n"
            "**정리**\n\n"
            "- **위약금** = 취소 **후** 생긴 위약금만, 수금 맞춤 + 수금 쪽 집계 반영.\n"
            "- **직원 구매** = 내부 구매, 마진 0·영업 매출·KPI와 분리."
        ),
    },
]


def _faq_filter_items(query: str) -> list[dict[str, str]]:
    q = (query or "").strip().lower()
    if not q:
        return list(APP_FAQ_ITEMS)
    out = []
    for e in APP_FAQ_ITEMS:
        blob = " ".join([e.get("title", ""), e.get("keywords", ""), e.get("body", "")]).lower()
        if q in blob:
            out.append(e)
    return out


def render_faq_page():
    """전역 메뉴 FAQ: 검색 + 목록. 항목은 APP_FAQ_ITEMS에서 유지보수."""
    st.header("❓ FAQ (도움말)")
    st.caption("키워드로 검색하거나, 아래 질문을 펼쳐 확인하세요. 새 질문은 앱 업데이트 시 목록에 추가됩니다.")
    _q = st.text_input(
        "검색",
        placeholder="예: 계약일, 매출, 입금, 브라우저",
        key="faq_keyword_search",
        label_visibility="collapsed",
    )
    st.caption("🔎 위 칸에 검색어를 입력하면 제목·내용·연관 키워드에서 찾습니다.")
    matched = _faq_filter_items(_q)
    if not matched:
        st.info("검색 결과가 없습니다. 다른 키워드를 입력해 보세요.")
        return
    _expand = bool((_q or "").strip())
    for i, item in enumerate(matched):
        with st.expander(item["title"], expanded=_expand and i < 12):
            st.markdown(item["body"])


def render_superadmin():
    # 최고 관리자 화면: 헤더 + 탭을 Sticky Header 컨테이너로 감싸 상단 고정
    st.markdown('<div class="sticky-header">', unsafe_allow_html=True)
    st.header("최고 관리자 메뉴")
    t = st.tabs([
        "① 전 지점 통합 대시보드",
        "② 매장별 직원 평가 현황 (HR)",
        "③ 📢 공지사항 관리",
        "④ 원클릭 데이터 백업 (CSV)",
        "⑤ 매장 계정 관리",
        "⑥ 전 지점 마케팅 분석",
        "⑦ 미수금(잔금) 레포트",
        "⑧ 월별 결제수단 집계표",
        "⑨ ⚠️ 데이터 초기화 (Danger Zone)",
        "⑩ FAQ (도움말)",
    ])
    st.markdown("</div>", unsafe_allow_html=True)
    with t[0]:
        _superadmin_tab1_integrated_dashboard()
    with t[1]:
        _superadmin_tab2_hr_store_employees()
    with t[2]:
        _superadmin_tab3_notices()
    with t[3]:
        _superadmin_tab4_backup_csv()
    with t[4]:
        _superadmin_tab5_store_accounts()
    with t[5]:
        render_marketing_insights_superadmin()
    with t[6]:
        _superadmin_tab_unpaid_report()
    with t[7]:
        render_monthly_payment_report(is_superadmin=True)
    with t[8]:
        _superadmin_tab_danger_zone_data_reset()
    with t[9]:
        render_faq_page()


# ========== 탭 1: 매장 관리자 메뉴 (Store Admin 전용) — Employees ==========

def render_store_admin_employees():
    """직원 마스터 및 수정 요청 UI. 100% Supabase app_users/app_user_stores 기반."""
    db_filename = st.session_state.get("current_db")
    if not db_filename:
        st.warning("매장에 로그인한 후 이용하세요.")
        return

    if not _supabase_app_tables_available():
        st.error("Supabase app_users/app_stores 테이블이 필요합니다. 클라우드 환경을 설정해 주세요.")
        return

    client, err = get_supabase_client()
    if err or not client:
        st.error(f"Supabase 연결이 필요합니다: {err or '연결 실패'}")
        return

    store_id = _get_supabase_store_by_db_filename(db_filename)
    if not store_id:
        st.error("현재 매장 정보를 Supabase에서 찾을 수 없습니다.")
        return

    st.header("직원 마스터 (Employees)")
    users = _get_supabase_users_list()
    emp_rows = []
    for u in users:
        if u.get("role") not in ("store_admin", "user"):
            continue
        store_ids = _get_supabase_user_store_ids(u.get("id"))
        if store_id not in store_ids and u.get("store_id") != store_id:
            continue
        name = (u.get("name") or u.get("username") or "").strip()
        emp_rows.append({"id": u["id"], "username": u.get("username", ""), "name": name or "-", "role": u.get("role", "user")})
    df = pd.DataFrame(emp_rows)

    # ---------- 신규 직원 등록 ----------
    with st.form("add_employee_form"):
        st.subheader("신규 직원 등록")
        new_username = st.text_input("사용자명(ID)", key="emp_new_username")
        new_password = st.text_input("비밀번호", type="password", key="emp_new_password")
        new_name = st.text_input("이름(표시명)", key="emp_new_name")
        new_role = st.selectbox("역할", ["store_admin", "user"], key="emp_new_role")
        if st.form_submit_button("추가"):
            if new_username and new_username.strip() and new_password:
                pw_hash = hashlib.sha256(new_password.encode()).hexdigest()
                try:
                    ins = client.table("app_users").insert({
                        "username": new_username.strip(),
                        "password": pw_hash,
                        "role": new_role,
                        "store_id": int(store_id),
                        "name": (new_name or "").strip() or None,
                        "email": None,
                    }).execute()
                    new_id = ins.data[0]["id"] if ins.data else None
                    if new_id:
                        try:
                            client.table("app_user_stores").insert({
                                "user_id": new_id,
                                "store_id": int(store_id),
                            }).execute()
                        except Exception:
                            pass
                    clear_data_cache()
                    st.success("직원이 등록되었습니다.")
                    st.rerun()
                except Exception as e:
                    err_str = str(e).lower()
                    if "unique" in err_str or "duplicate" in err_str:
                        st.error("이미 존재하는 사용자명입니다.")
                    else:
                        st.error(f"등록 실패: {e}")
            else:
                st.warning("사용자명과 비밀번호를 입력하세요.")

    # ---------- 직원 목록 (수정/삭제) ----------
    if len(df) > 0:
        st.subheader("직원 목록 (수정/삭제)")
        for _, row in df.iterrows():
            with st.expander(f"{row['name']} ({row.get('username', '')})"):
                with st.form(f"emp_{row['id']}"):
                    new_name = st.text_input("이름", value=row["name"], key=f"name_{row['id']}")
                    new_role = st.selectbox("역할", ["store_admin", "user"], index=0 if row.get("role") == "store_admin" else 1, key=f"role_{row['id']}")
                    submitted_save = st.form_submit_button("저장")
                    submitted_del = st.form_submit_button("매장에서 제거")
                    if submitted_save:
                        try:
                            client.table("app_users").update({
                                "name": (new_name or "").strip() or None,
                                "role": new_role,
                            }).eq("id", int(row["id"])).execute()
                            clear_data_cache()
                            st.success("수정되었습니다.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"수정 실패: {e}")
                    elif submitted_del:
                        try:
                            client.table("app_user_stores").delete().eq("user_id", int(row["id"])).eq("store_id", int(store_id)).execute()
                            client.table("app_users").update({"store_id": None}).eq("id", int(row["id"])).eq("store_id", int(store_id)).execute()
                            clear_data_cache()
                            st.success("매장에서 제거되었습니다.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"제거 실패: {e}")

    # (결제 수정 승인 워크플로우 제거됨 - 직접 수정 방식으로 전환)


# ========== 성과 축하 (Gamification) — 신규 주문 INSERT 시에만 트리거 ==========

def _render_gamification_feedback(ctx: dict):
    """
    신규 매출 등록 성공 시 4가지 지표(매출규모, 마진율, AOV, 추가성과)를 평가해 카드/메시지로 표시.
    ctx: amount, cost, margin_pct, employee_names, db_filename, order_date, is_today_first
    """
    amount = int(ctx.get("amount") or 0)
    cost = int(ctx.get("cost") or 0)
    margin_pct = float(ctx.get("margin_pct") or 0)
    employee_names = (ctx.get("employee_names") or "").strip()
    db_filename = ctx.get("db_filename") or ""
    order_date = ctx.get("order_date")
    is_today_first = bool(ctx.get("is_today_first"))
    year = order_date.year if hasattr(order_date, "year") else _today_kst().year
    month = order_date.month if hasattr(order_date, "month") else _today_kst().month

    st.subheader("🎉 성과 축하")
    cards_html = []

    # A. 매출 규모 (Sales Volume)
    if amount >= 10_000_000:
        st.balloons()
        cards_html.append(
            '<div style="background:linear-gradient(135deg,#ffd700 0%,#ffb347 100%);color:#1a1a1a;padding:1rem 1.25rem;border-radius:12px;margin-bottom:0.75rem;box-shadow:0 2px 8px rgba(0,0,0,0.15);">'
            '<strong>🏆 최고의 판매자입니다!</strong> 압도적인 실적을 달성했습니다.</div>'
        )
    elif amount >= 7_000_000:
        cards_html.append(
            '<div style="background:linear-gradient(135deg,#4a90d9 0%,#357abd 100%);color:#fff;padding:1rem 1.25rem;border-radius:12px;margin-bottom:0.75rem;box-shadow:0 2px 8px rgba(0,0,0,0.12);">'
            '<strong>🚀 정말 대단해요!</strong> 탁월한 성과입니다.</div>'
        )
    elif amount >= 5_000_000:
        cards_html.append(
            '<div style="background:linear-gradient(135deg,#ff8c42 0%,#e67e22 100%);color:#fff;padding:1rem 1.25rem;border-radius:12px;margin-bottom:0.75rem;box-shadow:0 2px 8px rgba(0,0,0,0.12);">'
            '<strong>🎉 축하합니다!</strong> 훌륭한 성과를 기록했습니다.</div>'
        )

    # B. 마진율 (Margin Rate)
    if margin_pct >= 20:
        cards_html.append(
            '<div style="color:#0d8050;padding:0.5rem 0;margin-bottom:0.5rem;">'
            '🎯 대단합니다. 목표된 마진율을 달성하였습니다.</div>'
        )
    elif margin_pct < 15 and amount > 0:
        cards_html.append(
            '<div style="color:#666;padding:0.5rem 0;margin-bottom:0.5rem;">'
            '💡 우리 다음에는 개인 마진을 조금 더 관리해 보아요.</div>'
        )

    # C. 평균 객단가 (AOV)
    try:
        aov_30 = _cached_store_aov_30d(db_filename)
        if aov_30 > 0 and amount >= aov_30:
            cards_html.append(
                '<div style="color:#1a73e8;padding:0.5rem 0;margin-bottom:0.5rem;">'
                '📈 와우! 당월 평균 객단가 이상의 매출을 기록했습니다.</div>'
            )
    except Exception:
        pass

    # D. 추가 성과 (개인 최고: INSERT 전 당월 최대값 대비 경신 여부)
    try:
        prev_max = float(ctx.get("monthly_max_before") or 0)
        if amount > prev_max and amount > 0:
            cards_html.append(
                '<div style="color:#c5221f;padding:0.5rem 0;margin-bottom:0.5rem;">'
                '🔥 이번 달 개인 최고 매출액을 갱신했습니다!</div>'
            )
    except Exception:
        pass
    if is_today_first:
        cards_html.append(
            '<div style="color:#137333;padding:0.5rem 0;margin-bottom:0.5rem;">'
            '🌅 오늘 매장의 첫 매출을 개시했습니다. 좋은 출발입니다!</div>'
        )

    if cards_html:
        st.markdown("\n".join(cards_html), unsafe_allow_html=True)
    st.divider()


# ========== 탭 2: 새로운 매출 등록 ==========


def _customer_search_fragment_impl(db_filename: str):
    """기존 고객 검색 UI. fragment로 감싸 타이핑/검색 시 전체 스크립트 대신 이 부분만 렌더링.
    Supabase: or_ ilike 검색 (PostgREST는 *를 % 와일드카드로 사용). SQLite: LIKE 검색."""
    with st.form("cust_search_form"):
        q = st.text_input("이름 또는 전화번호로 검색 *", key="new_sales_cust_search", placeholder="예: 홍길동, 010-1234")
        search_clicked = st.form_submit_button("검색")
    if search_clicked and q and q.strip():
        q_clean = q.strip()
        results_list = []
        try:
            if _supabase_orders_payments_available():
                sc, _ = get_supabase_client()
                store_name = _get_current_store_name_for_customers(db_filename)
                if sc and store_name:
                    # 속도·안정성: 최근 200건 로드 후 클라이언트 필터링 (or_/ilike 지연·오류 회피)
                    r = sc.table("app_customers").select("id, name, phone1, phone2, address").eq("store_name", store_name).order("id", desc=True).limit(200).execute()
                    rows = r.data or []
                    q_lower = q_clean.lower()
                    results_list = [row for row in rows if q_lower in (str(row.get("name") or "").lower()) or q_lower in (str(row.get("phone1") or "").lower()) or q_lower in (str(row.get("phone2") or "").lower())]
            else:
                conn = get_tenant_conn(db_filename)
                if conn:
                    try:
                        pattern = f"%{q_clean}%"
                        cur = conn.execute(
                            "SELECT id, name, phone1, phone2, address FROM Customers WHERE name LIKE ? OR phone1 LIKE ? OR phone2 LIKE ? ORDER BY id DESC LIMIT 50",
                            (pattern, pattern, pattern),
                        )
                        results_list = [dict(zip(("id", "name", "phone1", "phone2", "address"), row)) for row in cur.fetchall()]
                    finally:
                        conn.close()
            st.session_state["_cust_search_results"] = results_list
        except Exception:
            st.session_state["_cust_search_results"] = []
    results = st.session_state.get("_cust_search_results") or []
    if results:
        customers = pd.DataFrame(results)
        cust_options = list(customers["id"].tolist())

        # 동명이인 판별: 이름이 중복되는 경우 주소 앞 10자도 표시
        _name_counts = customers["name"].value_counts()

        def _fmt(cid):
            row = customers[customers["id"] == cid]
            if len(row) == 0:
                return str(cid)
            r0 = row.iloc[0]
            name = r0.get("name") or "-"
            phone = r0.get("phone1") or "-"
            if _name_counts.get(name, 1) > 1:
                addr = str(r0.get("address") or "")
                addr_hint = ("·" + addr[:10]) if addr else ""
                return f"{name} ({phone}{addr_hint})"
            return f"{name} ({phone})"

        def _on_customer_select():
            """selectbox 값이 바뀌거나 이미 선택된 상태에서도 세션 상태를 항상 갱신."""
            sel_id = st.session_state.get("new_sales_cust_select")
            if sel_id is None:
                return
            _results = st.session_state.get("_cust_search_results") or []
            if not _results:
                return
            _customers = pd.DataFrame(_results)
            matched = _customers[_customers["id"] == sel_id]
            if matched.empty:
                return
            r0 = matched.iloc[0]
            st.session_state["_new_sales_selected_customer"] = dict(r0)
            st.session_state["new_sales_cust_name"] = r0.get("name") or ""
            st.session_state["phone1"] = _format_phone_hyphen(r0.get("phone1") or "") or ""
            st.session_state["phone2"] = _format_phone_hyphen(r0.get("phone2") or "") or ""
            st.session_state["address_manual"] = r0.get("address") or st.session_state.get("address_manual", "")

        st.caption("고객을 선택하면 아래 입력란에 이름·전화번호·주소가 자동 입력됩니다.")
        st.selectbox(
            "검색 결과에서 고객 선택 *",
            cust_options,
            format_func=_fmt,
            key="new_sales_cust_select",
            on_change=_on_customer_select,
        )
        # selectbox가 처음 렌더링될 때(검색 직후)도 첫 번째 항목을 자동 반영
        # on_change는 값이 변경될 때만 실행되므로, 세션에 선택 결과가 없는 경우 직접 세팅
        if not st.session_state.get("_new_sales_selected_customer"):
            _on_customer_select()
    elif search_clicked and q and q.strip():
        st.info("검색 결과가 없습니다. **신규 고객 등록** 탭에서 새로 등록하세요.")


def _customer_search_fragment(db_filename: str):
    """기존 고객 검색 UI. st.fragment 제거함 — fragment 시 세션/위젯 상호작용으로 입력창 사라짐 이슈 회피."""
    _customer_search_fragment_impl(db_filename)


def _address_section_impl():
    if st.button("주소 검색", key="addr_search_btn", type="primary"):
        st.session_state["_show_address_dialog"] = True
    if st.session_state.get("_show_address_dialog"):
        _address_search_dialog()
    st.text_area("기본 주소 (위 버튼으로 검색하거나 직접 입력) *", key="address_manual")
    st.text_input("상세 주소 (동/호수 등) *", key="address_detail", placeholder="예: 101동 202호")


def _render_address_section_fragment():
    _address_section_impl()


def _render_special_order_form(db_filename: str, employees: pd.DataFrame):
    """위약금 / 직원구매 전용 간편 등록 폼."""
    tab_penalty, tab_emp = st.tabs(["💸 위약금 등록", "🛒 직원 구매 등록"])

    # ── 위약금 등록 ──
    with tab_penalty:
        st.caption("계약 취소 시 수령한 위약금을 별도 주문으로 등록합니다. 원가는 0원으로 처리됩니다.")
        p_col1, p_col2 = st.columns(2)
        with p_col1:
            p_cust_name = st.text_input("고객 이름 *", key="sp_penalty_name")
            p_cust_phone = st.text_input("연락처", key="sp_penalty_phone", placeholder="010-0000-0000")
        with p_col2:
            p_date = st.date_input("계약일 *", value=_today_kst(), key="sp_penalty_date")
            p_amount = st.text_input(
                "위약금 금액 *", key="sp_penalty_amount",
                on_change=lambda: st.session_state.__setitem__(
                    "sp_penalty_amount",
                    _format_number_comma(st.session_state.get("sp_penalty_amount", ""))
                )
            )
        p_method = st.selectbox("결제 수단", options=PAYMENT_METHOD_OPTIONS, key="sp_penalty_method")
        if p_method in _CARD_WITH_COMPANY:
            p_card = st.selectbox("카드사", options=CARD_COMPANY_OPTIONS, key="sp_penalty_card")
        elif p_method == "메인페이":
            p_card = st.text_input("메인페이 승인번호 4자리", key="sp_penalty_card", max_chars=4)
        elif p_method == "지역화폐":
            p_card = st.text_input("지역화폐 승인번호", key="sp_penalty_card")
        else:
            p_card = None
        p_reason = st.text_area("위약금 사유 *", key="sp_penalty_reason", placeholder="예) 계약 취소 위약금, 고객 변심으로 인한 계약 철회")
        p_emp_names = []
        if not employees.empty:
            p_emp_sel = st.multiselect("담당 직원", options=employees["name"].tolist(), key="sp_penalty_emp")
            p_emp_names = p_emp_sel
        if st.button("💸 위약금 등록", key="sp_penalty_btn", type="primary"):
            p_amt_int = _parse_comma_to_int(st.session_state.get("sp_penalty_amount", "0"))
            if not p_cust_name.strip():
                st.error("고객 이름을 입력해 주세요.")
            elif p_amt_int <= 0:
                st.error("위약금 금액을 입력해 주세요.")
            elif not p_reason.strip():
                st.error("위약금 사유를 입력해 주세요.")
            else:
                try:
                    emp_str = ",".join(p_emp_names) if p_emp_names else None
                    fee = _payment_fee_amount(p_method, p_amt_int)
                    p_date_str = p_date.isoformat()
                    if _supabase_orders_payments_available():
                        cid, cid_err = _supabase_insert_customer(db_filename, p_cust_name.strip(), p_cust_phone.strip() or "", None, None)
                        if cid is None:
                            st.error(f"고객 등록 실패: {cid_err}")
                        else:
                            oid = _insert_order_supabase(db_filename, {
                                "customer_id": cid,
                                "employee_names": emp_str,
                                "order_date": p_date_str,
                                "delivery_date": p_date_str,
                                "category": "위약금",
                                "cost_price": 0,
                                "total_amount": p_amt_int,
                                "balance_status": "미납",
                                "visit_reason": p_reason.strip(),
                                "purchase_reason": "위약금",
                            })
                            if oid:
                                _insert_payment_supabase(db_filename, {
                                    "order_id": oid,
                                    "payment_date": p_date_str,
                                    "amount": p_amt_int,
                                    "payment_method": p_method or None,
                                    "card_company": p_card,
                                    "fee_amount": fee,
                                    "created_by": _current_username(),
                                })
                                _recalc_order_actual_margin_supabase(db_filename, oid)
                                clear_data_cache()
                                st.success(f"✅ 위약금 {p_amt_int:,}원 등록 완료! (주문 #{oid})")
                                for k in ["sp_penalty_name", "sp_penalty_phone", "sp_penalty_amount", "sp_penalty_reason", "sp_penalty_card"]:
                                    st.session_state.pop(k, None)
                                st.rerun()
                            else:
                                st.error("주문 등록에 실패했습니다.")
                    else:
                        conn = get_tenant_conn(db_filename)
                        try:
                            conn.execute(
                                "INSERT INTO Customers (name, phone1) VALUES (?, ?)",
                                (p_cust_name.strip(), p_cust_phone.strip() or ""),
                            )
                            cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                            conn.execute(
                                "INSERT INTO Orders (customer_id, employee_names, order_date, delivery_date, category, cost_price, total_amount, visit_reason, purchase_reason, balance_status) VALUES (?,?,?,?,?,0,?,?,?,?)",
                                (cid, emp_str, p_date_str, p_date_str, "위약금", p_amt_int, p_reason.strip(), "위약금", "미납"),
                            )
                            oid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                            conn.execute(
                                "INSERT INTO Payments (order_id, payment_date, amount, payment_method, card_company, fee_amount, created_by, created_at) VALUES (?,?,?,?,?,?,?,datetime('now', '+9 hours'))",
                                (oid, p_date_str, p_amt_int, p_method or None, p_card, fee, _current_username()),
                            )
                            _recalc_order_actual_margin(conn, oid, db_filename)
                            conn.commit()
                            clear_data_cache()
                            st.success(f"✅ 위약금 {p_amt_int:,}원 등록 완료!")
                            for k in ["sp_penalty_name", "sp_penalty_phone", "sp_penalty_amount", "sp_penalty_reason", "sp_penalty_card"]:
                                st.session_state.pop(k, None)
                            st.rerun()
                        finally:
                            conn.close()
                except Exception as e:
                    st.error(f"등록 오류: {e}")

    # ── 직원 구매 등록 ──
    with tab_emp:
        st.caption("직원 구매는 매출로 계상되지 않습니다. 판매가 = 원가로 등록되어 마진 0원으로 기록됩니다.")
        e_col1, e_col2 = st.columns(2)
        with e_col1:
            e_emp_sel = None
            if not employees.empty:
                e_emp_sel = st.selectbox("구매 직원 *", options=employees["name"].tolist(), key="sp_emp_buyer")
            e_date = st.date_input("구매일 *", value=_today_kst(), key="sp_emp_date")
        with e_col2:
            e_category = st.selectbox("품목", options=["옷장", "식탁", "자녀방", "침대", "SSDS침대", "서재_학생", "소파", "소품", "전시품", "기타"], key="sp_emp_category")
            e_cost = st.text_input(
                "원가(구매가) *", key="sp_emp_cost",
                on_change=lambda: st.session_state.__setitem__(
                    "sp_emp_cost",
                    _format_number_comma(st.session_state.get("sp_emp_cost", ""))
                )
            )
        e_method = st.selectbox("결제 수단", options=PAYMENT_METHOD_OPTIONS, key="sp_emp_method")
        if e_method in _CARD_WITH_COMPANY:
            e_card = st.selectbox("카드사", options=CARD_COMPANY_OPTIONS, key="sp_emp_card")
        elif e_method == "메인페이":
            e_card = st.text_input("메인페이 승인번호 4자리", key="sp_emp_card", max_chars=4)
        elif e_method == "지역화폐":
            e_card = st.text_input("지역화폐 승인번호", key="sp_emp_card")
        else:
            e_card = None
        if st.button("🛒 직원 구매 등록", key="sp_emp_btn", type="primary"):
            e_cost_int = _parse_comma_to_int(st.session_state.get("sp_emp_cost", "0"))
            if not e_emp_sel:
                st.error("구매 직원을 선택해 주세요.")
            elif e_cost_int <= 0:
                st.error("원가(구매가)를 입력해 주세요.")
            else:
                try:
                    e_date_str = e_date.isoformat()
                    fee = _payment_fee_amount(e_method, e_cost_int)
                    if _supabase_orders_payments_available():
                        cid, cid_err = _supabase_insert_customer(db_filename, f"[직원]{e_emp_sel}", "", None, None)
                        if cid is None:
                            st.error(f"등록 실패: {cid_err}")
                        else:
                            oid = _insert_order_supabase(db_filename, {
                                "customer_id": cid,
                                "employee_names": e_emp_sel,
                                "order_date": e_date_str,
                                "delivery_date": e_date_str,
                                "category": f"직원구매_{e_category}",
                                "cost_price": e_cost_int,
                                "total_amount": e_cost_int,
                                "balance_status": "미납",
                                "visit_reason": "직원구매",
                                "purchase_reason": "직원구매",
                            })
                            if oid:
                                _insert_payment_supabase(db_filename, {
                                    "order_id": oid,
                                    "payment_date": e_date_str,
                                    "amount": e_cost_int,
                                    "payment_method": e_method or None,
                                    "card_company": e_card,
                                    "fee_amount": fee,
                                    "created_by": _current_username(),
                                })
                                _recalc_order_actual_margin_supabase(db_filename, oid)
                                clear_data_cache()
                                st.success(f"✅ 직원 구매 {e_cost_int:,}원 등록 완료! (주문 #{oid})")
                                for k in ["sp_emp_cost", "sp_emp_card"]:
                                    st.session_state.pop(k, None)
                                st.rerun()
                            else:
                                st.error("주문 등록에 실패했습니다.")
                    else:
                        conn = get_tenant_conn(db_filename)
                        try:
                            conn.execute(
                                "INSERT INTO Customers (name, phone1) VALUES (?, ?)",
                                (f"[직원]{e_emp_sel}", ""),
                            )
                            cid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                            conn.execute(
                                "INSERT INTO Orders (customer_id, employee_names, order_date, delivery_date, category, cost_price, total_amount, visit_reason, purchase_reason, balance_status) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (cid, e_emp_sel, e_date_str, e_date_str, f"직원구매_{e_category}", e_cost_int, e_cost_int, "직원구매", "직원구매", "미납"),
                            )
                            oid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                            conn.execute(
                                "INSERT INTO Payments (order_id, payment_date, amount, payment_method, card_company, fee_amount, created_by, created_at) VALUES (?,?,?,?,?,?,?,datetime('now', '+9 hours'))",
                                (oid, e_date_str, e_cost_int, e_method or None, e_card, fee, _current_username()),
                            )
                            _recalc_order_actual_margin(conn, oid, db_filename)
                            conn.commit()
                            clear_data_cache()
                            st.success(f"✅ 직원 구매 {e_cost_int:,}원 등록 완료!")
                            for k in ["sp_emp_cost", "sp_emp_card"]:
                                st.session_state.pop(k, None)
                            st.rerun()
                        finally:
                            conn.close()
                except Exception as e:
                    st.error(f"등록 오류: {e}")


@st.fragment
def render_new_sales():
    db_filename = st.session_state.get("current_db")
    if not db_filename:
        st.warning("매장에 로그인한 후 이용하세요.")
        return
    # 신규 주문 INSERT 직후에만 설정된 성과 축하 컨텍스트 표시 (UPDATE/수정 시에는 절대 설정되지 않음)
    if "_gamification_ctx" in st.session_state:
        _render_gamification_feedback(st.session_state["_gamification_ctx"])
        del st.session_state["_gamification_ctx"]
    st.header("새로운 매출 등록")
    # 직원 목록: Supabase app_users/app_user_stores 우선 사용, 없으면 레거시 SQLite Employees 사용
    employees = pd.DataFrame(columns=["id", "name"])
    if _supabase_app_tables_available():
        store_id = _get_supabase_store_by_db_filename(db_filename)
        users = _get_supabase_users_list()
        rows = []
        for u in users:
            uid = u.get("id")
            role = u.get("role")
            if role not in ("store_admin", "user"):
                continue
            store_ids = _get_supabase_user_store_ids(uid)
            if store_id and store_id not in store_ids and u.get("store_id") != store_id:
                continue
            name = (u.get("name") or u.get("username") or "").strip()
            if not name:
                continue
            rows.append({"id": uid, "name": name})
        if rows:
            employees = pd.DataFrame(rows)
    else:
        conn = get_tenant_conn(db_filename)
        if conn:
            try:
                employees = pd.read_sql("SELECT id, name FROM Employees WHERE is_active = 1", conn)
            except Exception:
                employees = pd.DataFrame(columns=["id", "name"])
                st.warning("직원 목록을 불러오지 못했습니다. 매장 관리자 메뉴에서 직원을 먼저 등록해 주세요.")
            finally:
                conn.close()
    # ── 특수 등록 (위약금 / 직원구매) ──
    with st.expander("⚡ 특수 등록 (위약금 / 직원 구매)", expanded=False):
        _render_special_order_form(db_filename, employees)

    with st.expander("💳 기존 주문 잔금 빠른 등록", expanded=False):
        st.caption("기존 주문의 잔금을 이 화면에서 바로 등록합니다. 결제 날짜 기본값은 계약일이며, 필요 시 변경할 수 있습니다.")
        quick_q = st.text_input(
            "고객 검색 (이름/전화번호)",
            key="new_sales_quick_balance_query",
            placeholder="예: 홍길동 또는 01012345678",
        )
        quick_customers = pd.DataFrame()
        if quick_q and quick_q.strip():
            try:
                if _supabase_orders_payments_available():
                    sc_q, _ = get_supabase_client()
                    store_name_q = _get_current_store_name_for_customers(db_filename)
                    if sc_q and store_name_q:
                        q_safe = re.sub(r"[*,]", "", quick_q.strip())
                        or_filter = f"name.ilike.*{q_safe}*,phone1.ilike.*{q_safe}*,phone2.ilike.*{q_safe}*"
                        r_q = sc_q.table("app_customers").select("id, name, phone1, phone2").eq("store_name", store_name_q).or_(or_filter).order("id", desc=True).limit(50).execute()
                        quick_customers = pd.DataFrame(r_q.data) if r_q.data else pd.DataFrame()
                else:
                    conn_q = get_tenant_conn(db_filename)
                    if conn_q:
                        try:
                            pattern = f"%{quick_q.strip()}%"
                            quick_customers = pd.read_sql(
                                "SELECT id, name, phone1, phone2 FROM Customers WHERE name LIKE ? OR phone1 LIKE ? OR phone2 LIKE ? ORDER BY id DESC LIMIT 50",
                                conn_q,
                                params=(pattern, pattern, pattern),
                            )
                        finally:
                            conn_q.close()
            except Exception:
                quick_customers = pd.DataFrame()

        if quick_q and quick_q.strip() and quick_customers.empty:
            st.info("검색된 고객이 없습니다.")

        if not quick_customers.empty:
            quick_customers = quick_customers.drop_duplicates(subset=["name", "phone1"], keep="first").reset_index(drop=True)
            quick_cids = quick_customers["id"].tolist()

            def _fmt_quick_cust(cid):
                row = quick_customers[quick_customers["id"] == cid]
                if row.empty:
                    return str(cid)
                r0 = row.iloc[0]
                return f"{r0.get('name') or '-'} ({r0.get('phone1') or '-'})"

            quick_cid = st.selectbox("고객 선택", quick_cids, format_func=_fmt_quick_cust, key="new_sales_quick_balance_customer")
            if quick_cid:
                qrow = quick_customers[quick_customers["id"] == quick_cid].iloc[0]
                q_name = str(qrow.get("name") or "")
                q_phone = str(qrow.get("phone1") or "")
                all_cids = quick_customers[
                    (quick_customers["name"].fillna("") == q_name) &
                    (quick_customers["phone1"].fillna("") == q_phone)
                ]["id"].tolist() or [quick_cid]

                if _supabase_orders_payments_available():
                    all_orders_q = _load_orders_supabase(db_filename, "id, customer_id, order_date, total_amount", limit=None)
                    q_orders = all_orders_q[all_orders_q["customer_id"].isin(all_cids)].copy() if (not all_orders_q.empty and "customer_id" in all_orders_q.columns) else pd.DataFrame()
                    q_payments = _load_payments_supabase(db_filename)
                else:
                    conn_qo = get_tenant_conn(db_filename)
                    if conn_qo:
                        try:
                            placeholders = ",".join("?" * len(all_cids))
                            q_orders = pd.read_sql(
                                f"SELECT id, customer_id, order_date, total_amount FROM Orders WHERE customer_id IN ({placeholders})",
                                conn_qo,
                                params=tuple(all_cids),
                            )
                            q_payments = pd.read_sql("SELECT order_id, amount FROM Payments", conn_qo)
                        finally:
                            conn_qo.close()
                    else:
                        q_orders = pd.DataFrame()
                        q_payments = pd.DataFrame()

                if not q_orders.empty and "id" in q_orders.columns:
                    pay_sum_q = q_payments.groupby("order_id")["amount"].sum() if (not q_payments.empty and "order_id" in q_payments.columns and "amount" in q_payments.columns) else pd.Series(dtype=float)
                    q_orders["paid"] = q_orders["id"].map(pay_sum_q).fillna(0)
                    q_orders["balance"] = q_orders["total_amount"].fillna(0) - q_orders["paid"]
                    q_orders = q_orders[q_orders["balance"] > 0].copy()
                    q_orders["order_date"] = pd.to_datetime(q_orders["order_date"], errors="coerce")
                    q_orders = q_orders.sort_values(["order_date", "id"], ascending=[False, False]).reset_index(drop=True)

                    if q_orders.empty:
                        st.info("선택한 고객의 미수 주문이 없습니다.")
                    else:
                        q_oids = q_orders["id"].tolist()

                        def _fmt_quick_order(oid):
                            orow = q_orders[q_orders["id"] == oid].iloc[0]
                            od = orow.get("order_date")
                            od_str = od.strftime("%Y-%m-%d") if hasattr(od, "strftime") else str(od or "-")[:10]
                            bal = float(orow.get("balance") or 0)
                            return f"주문 #{oid} | 계약일 {od_str} | 잔금 {bal:,.0f}원"

                        quick_oid = st.selectbox("잔금 등록 주문 선택", q_oids, format_func=_fmt_quick_order, key="new_sales_quick_balance_order")
                        if quick_oid:
                            sel = q_orders[q_orders["id"] == quick_oid].iloc[0]
                            sel_balance = float(sel.get("balance") or 0)
                            sel_order_date = sel.get("order_date")
                            default_pay_date = sel_order_date.date() if hasattr(sel_order_date, "date") else _today_kst()
                            _customer_balance_payment_ui(
                                db_filename,
                                int(quick_oid),
                                sel_balance,
                                key_prefix=f"quick_bal_{int(quick_oid)}",
                                default_payment_date=default_pay_date,
                            )
                else:
                    st.info("선택한 고객의 주문 데이터가 없습니다.")

    st.divider()

    # 고객 선택: 기본 모드 = 신규 고객 등록, [기존 고객 검색] 버튼으로 검색 패널 열기
    if "_cust_search_panel_open" not in st.session_state:
        st.session_state["_cust_search_panel_open"] = False

    _panel_open = st.session_state["_cust_search_panel_open"]
    _col_new, _col_search = st.columns(2)
    with _col_new:
        if st.button(
            "✏️ 신규 고객으로 등록",
            key="btn_new_cust_mode",
            type="secondary" if _panel_open else "primary",
            width='stretch',
        ):
            st.session_state["_cust_search_panel_open"] = False
            for _k in ["_new_sales_selected_customer", "_cust_search_results",
                       "new_sales_cust_name", "new_sales_cust_search", "new_sales_cust_select"]:
                st.session_state.pop(_k, None)
            st.rerun()
    with _col_search:
        if st.button(
            "🔍 기존 고객 검색",
            key="btn_open_cust_search",
            type="primary" if _panel_open else "secondary",
            width='stretch',
        ):
            st.session_state["_cust_search_panel_open"] = True
            st.rerun()

    if st.session_state.get("_cust_search_panel_open"):
        _customer_search_fragment(db_filename)

    selected_customer_row = st.session_state.get("_new_sales_selected_customer")
    is_new_customer = selected_customer_row is None

    if selected_customer_row and st.session_state.get("_cust_search_panel_open"):
        _sel_name = selected_customer_row.get("name") or ""
        _sel_phone = selected_customer_row.get("phone1") or "-"
        st.success(f"✅ 선택된 고객: **{_sel_name}** ({_sel_phone})")
        if st.button("❌ 선택 해제 (신규 고객으로 전환)", key="btn_clear_cust_sel"):
            for _k in ["_new_sales_selected_customer", "_cust_search_results",
                       "new_sales_cust_name", "new_sales_cust_search", "new_sales_cust_select"]:
                st.session_state.pop(_k, None)
            st.session_state["_cust_search_panel_open"] = False
            st.rerun()

    default_name = (selected_customer_row.get("name") or "") if selected_customer_row else ""
    default_phone1 = (selected_customer_row.get("phone1") or "") if selected_customer_row else ""
    default_phone2 = (selected_customer_row.get("phone2") or "") if selected_customer_row else ""
    default_addr = (selected_customer_row.get("address") or st.session_state.get("address_manual", "")) if selected_customer_row else st.session_state.get("address_manual", "")
    cust_name_key = "new_sales_cust_name"
    if cust_name_key not in st.session_state:
        st.session_state[cust_name_key] = default_name
    if selected_customer_row is not None and default_name:
        st.session_state[cust_name_key] = default_name
    cust_name = st.text_input("고객명 *", key=cust_name_key)
    # 전화번호: 세션 초기화 후 위젯에서 on_change로 010-1234-5678 형식 하이픈 포맷
    if "phone1" not in st.session_state:
        st.session_state["phone1"] = _format_phone_hyphen(default_phone1) if default_phone1 else ""
    if "phone2" not in st.session_state:
        st.session_state["phone2"] = _format_phone_hyphen(default_phone2) if default_phone2 else ""
    if not is_new_customer:
        st.session_state["phone1"] = _format_phone_hyphen(default_phone1) if default_phone1 else ""
        st.session_state["phone2"] = _format_phone_hyphen(default_phone2) if default_phone2 else ""

    def _on_phone1():
        st.session_state["phone1"] = _format_phone_hyphen(st.session_state.get("phone1", ""))

    def _on_phone2():
        st.session_state["phone2"] = _format_phone_hyphen(st.session_state.get("phone2", ""))

    st.text_input("Phone 1 *", key="phone1", on_change=_on_phone1)
    st.text_input("Phone 2", key="phone2", on_change=_on_phone2)
    phone1 = st.session_state.get("phone1", "")
    phone2 = st.session_state.get("phone2", "")

    # ----- 주소: [주소 검색] 버튼 → 다이얼로그 팝업, @st.fragment로 입력 시 부분만 렌더링 -----
    if "address_manual" not in st.session_state:
        st.session_state["address_manual"] = default_addr
    if "address_detail" not in st.session_state:
        st.session_state["address_detail"] = ""
    _render_address_section_fragment()
    address_base = st.session_state.get("address_manual", "")
    address_detail = st.session_state.get("address_detail", "")
    address_full = " ".join(filter(None, [address_base.strip(), address_detail.strip()])) or None

    # 담당 직원: 직원 명부(현재 매장 배정 사용자) 우선 사용, 없으면 매장별 직원 마스터(Employees) 사용
    store_emp_names = get_store_assigned_employee_names(db_filename)
    if store_emp_names:
        emp_names = store_emp_names
    else:
        emp_names = employees["name"].tolist() if not employees.empty and "name" in employees.columns else []
    if not emp_names:
        st.warning("이 매장에 배정된 직원이 없습니다. **직원 계정 관리**에서 해당 매장을 배정해 주세요. (또는 매장 관리자 메뉴 → 직원 마스터에서 등록)")
    # key에 폼 리셋 카운터를 붙여 등록 완료 시 새 위젯으로 초기화
    _form_reset = st.session_state.get("_new_sales_form_reset", 0)
    selected_employees = st.multiselect(
        "담당 직원 (복수 선택, 1/n 실적 분배 대상) *",
        options=emp_names,
        default=[],
        key=f"new_sales_employee_multiselect_{_form_reset}",
    )
    employee_names_str = ",".join(selected_employees) if selected_employees else ""
    if "order_date" not in st.session_state:
        st.session_state["order_date"] = _today_kst()
    order_date = st.date_input("계약일 *", key="order_date")
    if "delivery_date" not in st.session_state:
        st.session_state["delivery_date"] = _today_kst()
    delivery_date = st.date_input("배송일 *", key="delivery_date")
    CATEGORY_OPTIONS = ["옷장", "식탁", "자녀방", "침대", "SSDS침대", "서재_학생", "소파", "소품", "전시품"]
    selected_categories = st.multiselect("품목/카테고리 (복수 선택) *", options=CATEGORY_OPTIONS, key=f"category_multiselect_{_form_reset}")
    category = ",".join(selected_categories) if selected_categories else None
    has_display = selected_categories and "전시품" in selected_categories
    # 금액: text_input + on_change 콤마 포맷 (포커스 아웃 시에만 rerun → number_input 대비 가볍거나 동일)
    if "cost_price" not in st.session_state:
        st.session_state["cost_price"] = "0"
    if "total_amount" not in st.session_state:
        st.session_state["total_amount"] = "0"

    st.text_input(
        "일반제품 판매가(Selling Price) *", key="total_amount",
        on_change=lambda: st.session_state.__setitem__(
            "total_amount", _format_number_comma(st.session_state.get("total_amount", ""))
        ),
    )
    st.text_input(
        "일반제품 원가(Cost) *", key="cost_price",
        on_change=lambda: st.session_state.__setitem__(
            "cost_price", _format_number_comma(st.session_state.get("cost_price", ""))
        ),
    )
    if has_display:
        if "display_sales_amount" not in st.session_state:
            st.session_state["display_sales_amount"] = "0"
        if "display_cost_amount" not in st.session_state:
            st.session_state["display_cost_amount"] = "0"
        st.text_input(
            "전시품 판매가 *", key="display_sales_amount",
            on_change=lambda: st.session_state.__setitem__(
                "display_sales_amount", _format_number_comma(st.session_state.get("display_sales_amount", ""))
            ),
        )
        st.text_input(
            "전시품 원가 *", key="display_cost_amount",
            on_change=lambda: st.session_state.__setitem__(
                "display_cost_amount", _format_number_comma(st.session_state.get("display_cost_amount", ""))
            ),
        )
    # 실시간 합산: 최종 총 판매금액, 최종 총 원가, 기본 총 마진
    general_sales = _parse_comma_to_int(st.session_state.get("total_amount", "0"))
    general_cost = _parse_comma_to_int(st.session_state.get("cost_price", "0"))
    display_sales_val = _parse_comma_to_int(st.session_state.get("display_sales_amount", "0")) if has_display else 0
    display_cost_val = _parse_comma_to_int(st.session_state.get("display_cost_amount", "0")) if has_display else 0
    final_sales = general_sales + display_sales_val
    final_cost = general_cost + display_cost_val
    basic_margin = final_sales - final_cost
    basic_margin_rate = (basic_margin / final_sales * 100) if final_sales else 0.0
    st.subheader("합산 금액 (실시간)")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("최종 총 판매금액", f"{final_sales:,}원", help="일반제품 판매가 + 전시품 판매가")
    with c2:
        st.metric("최종 총 원가", f"{final_cost:,}원", help="일반제품 원가 + 전시품 원가")
    with c3:
        st.metric("기본 총 마진", f"{basic_margin:,}원", help="최종 총 판매금액 - 최종 총 원가")
    with c4:
        _rate_icon = "🟢" if basic_margin_rate >= 20 else ("🟡" if basic_margin_rate >= 15 else "🔴")
        st.metric(
            f"1차 마진율 {_rate_icon}",
            f"{basic_margin_rate:.1f}%",
            help="(판매가 − 원가) / 판매가 × 100 (카드 수수료 미반영 기본 마진율)",
        )

    VISIT_REASON_OPTIONS = ["매장외관", "재구매", "소개", "광고(SNS 외)"]
    PURCHASE_REASON_OPTIONS = ["교체(이사없이)", "신혼/혼수", "공동구매(입주, 가구쇼 등)", "이사", "현대임직원할인"]
    visit_reason = st.selectbox("방문 이유 *", options=VISIT_REASON_OPTIONS, key="visit_reason")
    purchase_reason = st.selectbox("구매 이유 *", options=PURCHASE_REASON_OPTIONS, key="purchase_reason")

    # ----- 다중(복합) 결제 수단: 기본 4개, 최대 20개 (플러스 버튼으로 추가) -----
    MAX_PAYMENT_SLOTS = 20
    DEFAULT_PAYMENT_SLOTS = 4
    st.subheader("결제 내역 (복수 결제 가능)")
    # 현재 사용 중인 슬롯 개수 (세션에 저장)
    if "payment_slot_count" not in st.session_state:
        st.session_state["payment_slot_count"] = DEFAULT_PAYMENT_SLOTS
    slot_count = st.session_state["payment_slot_count"]
    # 내부적으로는 최대 슬롯 수만큼 키를 준비해 둠
    if "payment_rows" not in st.session_state:
        st.session_state["payment_rows"] = [
            {"method": "", "card_company": "", "amount": "0"} for _ in range(MAX_PAYMENT_SLOTS)
        ]
    # 슬롯 추가 버튼
    add_col1, add_col2 = st.columns([3, 1])
    with add_col2:
        disabled_add = slot_count >= MAX_PAYMENT_SLOTS
        if st.button("➕ 결제 수단 추가", disabled=disabled_add, key="add_payment_slot"):
            # 버튼 클릭 시 상태만 변경하면, Streamlit이 자체적으로 전체 앱을 1회 rerun합니다.
            if slot_count < MAX_PAYMENT_SLOTS:
                st.session_state["payment_slot_count"] = slot_count + 1
    total_payment_int = 0
    for i in range(slot_count):
        row_key = f"pay_method_{i}"
        card_key = f"pay_card_{i}"
        amt_key = f"pay_amt_{i}"
        if amt_key not in st.session_state:
            st.session_state[amt_key] = "0"
        c1, c2, c3 = st.columns([2, 2, 2])
        with c1:
            method = st.selectbox(f"결제 수단 #{i+1} *", options=PAYMENT_METHOD_OPTIONS, key=row_key, index=0 if i == 0 else 0)
        with c2:
            if method in _CARD_WITH_COMPANY:
                card_company = st.selectbox(f"카드사 #{i+1} *", options=CARD_COMPANY_OPTIONS, key=card_key)
            elif method == "메인페이":
                st.text_input(f"메인페이 승인번호 4자리 #{i+1}", key=card_key, max_chars=4)
                card_company = st.session_state.get(card_key)
            elif method == "지역화폐":
                st.text_input(f"지역화폐 승인번호 #{i+1}", key=card_key)
                card_company = st.session_state.get(card_key)
            else:
                card_company = None
        with c3:
            _ak = amt_key
            st.text_input(f"금액 #{i+1} *", key=_ak, on_change=lambda k=_ak: st.session_state.__setitem__(k, _format_number_comma(st.session_state.get(k, ""))))
        total_payment_int += _parse_comma_to_int(st.session_state.get(amt_key, "0"))
        # 온누리상품권 전용 승인번호/영수증 입력 UI (결제 수단에 '온누리'가 포함될 때만)
        is_onnuri = method and ("온누리" in str(method))
        onnuri_stage_key = f"pay_onnuri_stage_{i}"
        last4_key = f"pay_onnuri_last4_{i}"
        full_key = f"pay_onnuri_full_{i}"
        receipt_key = f"pay_onnuri_receipt_{i}"
        if is_onnuri:
            if onnuri_stage_key not in st.session_state:
                st.session_state[onnuri_stage_key] = "last4"
            stage = st.session_state.get(onnuri_stage_key, "last4")
            if stage == "last4":
                st.text_input(f"온누리 승인번호 뒤 4자리 #{i+1}", key=last4_key, max_chars=4)
            else:
                st.text_input(f"온누리 승인번호 전체 (8자리 이상) #{i+1}", key=full_key)
            st.file_uploader(
                "온누리상품권 영수증 사진(선택)",
                type=["png", "jpg", "jpeg", "webp"],
                key=receipt_key,
            )
        else:
            # 다른 결제 수단으로 변경되면 단계 상태는 초기화
            st.session_state.pop(onnuri_stage_key, None)
    balance = final_sales - total_payment_int
    st.metric("잔금 (미수금)", f"{balance:,}원", help="최종 총 판매금액 - 누적 결제금액")

    # 예상 수수료·최종 실질 마진율 (결제 수단별 수수료 반영, 실시간)
    total_fee_est = sum(
        _payment_fee_amount(st.session_state.get(f"pay_method_{i}", ""), _parse_comma_to_int(st.session_state.get(f"pay_amt_{i}", "0")))
        for i in range(slot_count)
    )
    net_margin_rate_est = _compute_net_margin_rate(float(final_sales), float(final_cost), total_fee_est)
    fee_delta = net_margin_rate_est - basic_margin_rate
    fee_col, margin_col = st.columns(2)
    with fee_col:
        st.metric("예상 수수료", f"{int(total_fee_est):,}원", help="신용카드·메인페이 2.5%, 체크카드 1.5%, 그 외 0%")
    with margin_col:
        _net_icon = "🟢" if net_margin_rate_est >= 20 else ("🟡" if net_margin_rate_est >= 15 else "🔴")
        st.metric(
            f"최종 실질 마진율 {_net_icon}",
            f"{net_margin_rate_est:.1f}%",
            delta=f"{fee_delta:+.1f}%",
            delta_color="normal",
            help="(판매가 − 원가 − 수수료) / 판매가 × 100 (카드·메인페이 등 수수료 반영 후 최종 마진율)",
        )

    if st.button("매출 등록"):
        cust_name_ok = cust_name and cust_name.strip()
        phone1_ok = phone1 and phone1.strip()
        delivery_ok = delivery_date is not None
        if not cust_name_ok:
            st.error("고객명(필수)을 입력하세요.")
            st.stop()
        if not phone1_ok:
            st.error("Phone 1(필수)을 입력하세요.")
            st.stop()
        if not delivery_ok:
            st.error("배송일(필수)을 선택하세요.")
            st.stop()
        cost_price_int = _parse_comma_to_int(st.session_state.get("cost_price", "0"))
        general_sales_int = _parse_comma_to_int(st.session_state.get("total_amount", "0"))
        display_sales_int = _parse_comma_to_int(st.session_state.get("display_sales_amount", "0")) if has_display else 0
        display_cost_int = _parse_comma_to_int(st.session_state.get("display_cost_amount", "0")) if has_display else 0
        final_sales_save = general_sales_int + display_sales_int
        final_cost_save = cost_price_int + display_cost_int
        basic_margin_save = final_sales_save - final_cost_save
        # 결제 합계 및 미수금(잔금) 계산 — 완불이 아니어도 저장 가능(계약금만 받고 저장 가능)
        total_payment_slots = sum(_parse_comma_to_int(st.session_state.get(f"pay_amt_{i}", "0")) for i in range(slot_count))
        unpaid_balance = final_sales_save - total_payment_slots  # 판매가 - 수납액 = 미수금
        # 수수료 합계 및 실질 마진율 (신용카드·메인페이 2.5% 반영)
        total_fees_save = 0.0
        for i in range(slot_count):
            amt = _parse_comma_to_int(st.session_state.get(f"pay_amt_{i}", "0"))
            method = st.session_state.get(f"pay_method_{i}", "")
            total_fees_save += _payment_fee_amount(method, amt)
        net_margin_rate_save = _compute_net_margin_rate(float(final_sales_save), float(final_cost_save), total_fees_save)
        margin_out_of_range = net_margin_rate_save < 15 or net_margin_rate_save > 25
        if margin_out_of_range:
            st.warning(f"⚠️ 주의: 실질 마진율이 {net_margin_rate_save:.1f}%입니다. 적정 범위(15%~25%)를 벗어났습니다.")
        # 온누리상품권 결제에 대한 부정 사용 방지 검증
        # 1차: 승인번호 뒤 4자리 + 결제일 기준 중복 여부 확인 (금액 제외)
        # 중복 발견 시 해당 슬롯은 전체 승인번호(8자리 이상) 입력 단계로 전환
        for i in range(slot_count):
            method = st.session_state.get(f"pay_method_{i}", "")
            amt = _parse_comma_to_int(st.session_state.get(f"pay_amt_{i}", "0"))
            if amt <= 0:
                continue
            if not method or "온누리" not in str(method):
                continue
            stage_key = f"pay_onnuri_stage_{i}"
            last4_key = f"pay_onnuri_last4_{i}"
            full_key = f"pay_onnuri_full_{i}"
            stage = st.session_state.get(stage_key, "last4")
            pay_date_str = order_date.isoformat()
            if stage == "last4":
                last4_raw = (st.session_state.get(last4_key, "") or "").strip()
                last4_digits = re.sub(r"\\D", "", last4_raw)
                if len(last4_digits) != 4:
                    st.error("온누리상품권 결제의 승인번호 뒤 4자리를 정확히 입력하세요.")
                    st.stop()
                # 동일 결제일+승인번호 4자리 조합이 이미 존재하는지 교차 검증 (금액 제외)
                if _supabase_orders_payments_available():
                    dup_cnt = _count_payments_onnuri_dup_supabase(db_filename, pay_date_str, last4_digits)
                else:
                    conn_chk = get_tenant_conn(db_filename)
                    try:
                        dup_cnt = conn_chk.execute(
                            """
                            SELECT COUNT(*) FROM Payments
                            WHERE payment_method LIKE '%온누리%'
                              AND payment_date = ?
                              AND onnuri_approval_code IS NOT NULL
                              AND substr(onnuri_approval_code, -4) = ?
                            """,
                            (pay_date_str, last4_digits),
                        ).fetchone()[0]
                    finally:
                        conn_chk.close()
                if dup_cnt > 0:
                    # 전체 승인번호(8자리 이상) 입력 단계로 전환
                    st.session_state[stage_key] = "full"
                    st.error("⚠️ 동일한 결제일, 금액, 승인번호 4자리를 가진 기록이 이미 존재합니다. 정상 중복 건일 경우 승인번호 '전체 8자리 이상'을 입력해 주세요.")
                    st.stop()
            else:
                full_code_raw = (st.session_state.get(full_key, "") or "").strip()
                full_digits = re.sub(r"\\D", "", full_code_raw)
                if len(full_digits) < 8:
                    st.error("온누리상품권 승인번호 전체(8자리 이상)를 정확히 입력하세요.")
                    st.stop()
        use_supabase_op = _supabase_orders_payments_available()
        # 성과 축하는 신규 INSERT 시에만 1회 트리거 (UPDATE/수정 시 미설정). INSERT 전 메타데이터 수집.
        today_iso = order_date.isoformat() if hasattr(order_date, "isoformat") else str(_today_kst())
        _count_today_before = _count_orders_on_date(db_filename, today_iso)
        is_today_first = _count_today_before == 0
        _year = order_date.year if hasattr(order_date, "year") else _today_kst().year
        _month = order_date.month if hasattr(order_date, "month") else _today_kst().month
        monthly_max_before = _cached_employee_monthly_max(db_filename, employee_names_str, _year, _month)

        if use_supabase_op:
            if is_new_customer:
                customer_id, ins_err = _supabase_insert_customer(db_filename, cust_name, phone1, phone2, address_full)
                if customer_id is None:
                    st.error("고객 등록에 실패했습니다. " + (ins_err or "Supabase app_customers 테이블·스키마(store_name, name, phone1 등)를 확인해 주세요."))
                    st.stop()
            else:
                customer_id = int(selected_customer_row["id"])
            order_payload = {
                "customer_id": customer_id,
                "employee_names": employee_names_str or None,
                "order_date": order_date.isoformat(),
                "delivery_date": delivery_date.isoformat() if delivery_date else None,
                "category": category,
                "cost_price": cost_price_int,
                "total_amount": final_sales_save,
                "visit_reason": visit_reason or None,
                "purchase_reason": purchase_reason or None,
                "display_sales_amount": display_sales_int,
                "display_cost_amount": display_cost_int,
                "balance_status": "미납",
            }
            order_id = _insert_order_supabase(db_filename, order_payload)
            if order_id is None:
                st.error("주문 등록에 실패했습니다. Supabase를 확인해 주세요.")
                st.stop()
            total_fees = 0.0
            total_paid_initial = 0
            for i in range(slot_count):
                amt = _parse_comma_to_int(st.session_state.get(f"pay_amt_{i}", "0"))
                if amt <= 0:
                    continue
                method = st.session_state.get(f"pay_method_{i}", "")
                card_company = st.session_state.get(f"pay_card_{i}", None) if method in ("신용카드", "메인페이") else None
                fee = _payment_fee_amount(method, amt)
                total_fees += fee
                total_paid_initial += amt
                onnuri_code = None
                if method and "온누리" in str(method):
                    stage = st.session_state.get(f"pay_onnuri_stage_{i}", "last4")
                    if stage == "last4":
                        raw = (st.session_state.get(f"pay_onnuri_last4_{i}", "") or "").strip()
                    else:
                        raw = (st.session_state.get(f"pay_onnuri_full_{i}", "") or "").strip()
                    onnuri_code = re.sub(r"\\D", "", raw) or None
                _insert_payment_supabase(db_filename, {
                    "order_id": order_id,
                    "payment_date": order_date.isoformat(),
                    "amount": amt,
                    "payment_method": method or None,
                    "card_company": card_company,
                    "fee_amount": fee,
                    "onnuri_approval_code": onnuri_code,
                    "created_by": _current_username(),
                })
            actual_margin = basic_margin_save - total_fees  # 실질 마진 = 판매가 - 원가 - 수수료
            remaining = final_sales_save - total_paid_initial
            balance_status = _balance_status_from_remaining(remaining)
            _update_order_supabase(db_filename, order_id, {"actual_margin": actual_margin, "balance_status": balance_status})
            _insert_sales_transaction(db_filename, order_id, order_date.isoformat(), float(final_sales_save), "신규 주문", unpaid_balance=unpaid_balance, employee_names=employee_names_str or None)
            clear_data_cache()
            st.success("매출등록이 완료되었습니다.")
            net_margin_rate_ctx = _compute_net_margin_rate(float(final_sales_save), float(final_cost_save), total_fees)
            st.session_state["_gamification_ctx"] = {
                "amount": final_sales_save,
                "cost": final_cost_save,
                "margin_pct": net_margin_rate_ctx,
                "employee_names": employee_names_str,
                "db_filename": db_filename,
                "order_date": order_date,
                "is_today_first": is_today_first,
                "monthly_max_before": monthly_max_before,
            }
            # 채널톡 PUSH: 백그라운드 스레드로 전송 (UI 블로킹 방지)
            def _channel_talk_sync_supa():
                try:
                    if not _get_channel_talk_secrets():
                        return
                    store_name_ct = _get_store_name_by_db(db_filename)
                    store_tag_key_ct = _get_store_tag_key(store_name_ct)
                    unpaid_ct = float(remaining) if remaining > 0 else 0.0
                    sync_channel_talk_customer(
                        customer_name=cust_name.strip(),
                        phone_number=phone1.strip(),
                        purchase_amount=final_sales_save,
                        item_category=category or "",
                        purchase_date=order_date,
                        store_tag_key=store_tag_key_ct,
                        is_returning=(not is_new_customer),
                        unpaid_balance=unpaid_ct,
                    )
                except Exception:
                    pass
            threading.Thread(target=_channel_talk_sync_supa, daemon=True).start()
            st.toast("등록이 완료되었습니다. (채널톡 동기화는 백그라운드에서 진행됩니다.)", icon="✅")
            st.session_state["_new_sales_form_reset"] = st.session_state.get("_new_sales_form_reset", 0) + 1
            st.session_state["_cust_search_panel_open"] = False
            # 반복 등록 시 금액이 이전 건과 동일하게 남는 현상 방지: 비위젯 상태 초기화 후 위젯 키는 삭제
            st.session_state["payment_slot_count"] = DEFAULT_PAYMENT_SLOTS
            st.session_state["payment_rows"] = [{"method": "", "card_company": "", "amount": "0"} for _ in range(MAX_PAYMENT_SLOTS)]
            for key in list(st.session_state.keys()):
                if key in (
                    "phone1", "phone2", "address_manual", "address_detail",
                    "address_search_results", "address_search_error", "addr_keyword", "address_selection",
                    "order_date", "delivery_date",
                    "_show_address_dialog", "_dialog_addr_results", "_dialog_addr_error",
                    "_new_sales_selected_customer", "_cust_search_results",
                    "new_sales_cust_name", "new_sales_cust_search", "new_sales_cust_select",
                    "cost_price", "total_amount", "display_sales_amount", "display_cost_amount",
                    "visit_reason", "purchase_reason",
                    "payment_rows",
                ) or key.startswith(("pay_", "pay_onnuri_", "gen_pay", "d10_", "over_", "new_sales_employee_multiselect", "category_multiselect")):
                    try:
                        del st.session_state[key]
                    except Exception:
                        pass
            st.rerun()
        else:
            conn = get_tenant_conn(db_filename)
            try:
                if is_new_customer:
                    customer_id, ins_err = _supabase_insert_customer(db_filename, cust_name, phone1, phone2, address_full)
                    if customer_id is None:
                        st.error("고객 등록에 실패했습니다. " + (ins_err or "Supabase app_customers 테이블·스키마(store_name, name, phone1 등)를 확인해 주세요."))
                        st.stop()
                else:
                    customer_id = int(selected_customer_row["id"])
                conn.execute("""
                    INSERT INTO Orders (customer_id, employee_names, order_date, delivery_date, category, cost_price, total_amount, visit_reason, purchase_reason, display_sales_amount, display_cost_amount, balance_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    customer_id,
                    employee_names_str or None,
                    order_date.isoformat(),
                    delivery_date.isoformat(),
                    category,
                    cost_price_int,
                    final_sales_save,
                    visit_reason or None,
                    purchase_reason or None,
                    display_sales_int,
                    display_cost_int,
                    "미납",
                ))
                order_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
                total_fees = 0.0
                total_paid_initial = 0
                for i in range(slot_count):
                    amt = _parse_comma_to_int(st.session_state.get(f"pay_amt_{i}", "0"))
                    if amt <= 0:
                        continue
                    method = st.session_state.get(f"pay_method_{i}", "")
                    card_company = st.session_state.get(f"pay_card_{i}", None) if method in ("신용카드", "메인페이") else None
                    fee = _payment_fee_amount(method, amt)
                    total_fees += fee
                    total_paid_initial += amt
                    onnuri_code = None
                    if method and "온누리" in str(method):
                        stage = st.session_state.get(f"pay_onnuri_stage_{i}", "last4")
                        if stage == "last4":
                            raw = (st.session_state.get(f"pay_onnuri_last4_{i}", "") or "").strip()
                        else:
                            raw = (st.session_state.get(f"pay_onnuri_full_{i}", "") or "").strip()
                        onnuri_code = re.sub(r"\\D", "", raw) or None
                    conn.execute("""
                        INSERT INTO Payments (order_id, payment_date, amount, payment_method, card_company, fee_amount, onnuri_approval_code, created_by, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
                    """, (order_id, order_date.isoformat(), amt, method or None, card_company, fee, onnuri_code, _current_username()))
                actual_margin = basic_margin_save - total_fees
                conn.execute("UPDATE Orders SET actual_margin = ? WHERE id = ?", (actual_margin, order_id))
                remaining = final_sales_save - total_paid_initial
                balance_status = _balance_status_from_remaining(remaining)
                conn.execute("UPDATE Orders SET balance_status = ? WHERE id = ?", (balance_status, order_id))
                _insert_sales_transaction(db_filename, order_id, order_date.isoformat(), float(final_sales_save), "신규 주문", unpaid_balance=unpaid_balance, employee_names=employee_names_str or None)
                conn.commit()
                clear_data_cache()
                net_margin_rate_ctx = _compute_net_margin_rate(float(final_sales_save), float(final_cost_save), total_fees)
            finally:
                conn.close()
            st.session_state["_gamification_ctx"] = {
                "amount": final_sales_save,
                "cost": final_cost_save,
                "margin_pct": net_margin_rate_ctx,
                "employee_names": employee_names_str,
                "db_filename": db_filename,
                "order_date": order_date,
                "is_today_first": is_today_first,
                "monthly_max_before": monthly_max_before,
            }
            st.success("매출등록이 완료되었습니다.")
            # 마진율 이상 시 Superadmin/매장관리자 알림
            if margin_out_of_range:
                store_name = _get_store_name_by_db(db_filename)
                _insert_admin_alert(store_name, "margin", f"{store_name}에서 실질 마진율 {net_margin_rate_save:.1f}% 건이 등록되었습니다.")
            # 채널톡 PUSH: 백그라운드 스레드로 전송해 UI 블로킹 방지
            _sqlite_remaining = remaining  # 클로저에서 사용할 변수 캡처
            def _channel_talk_sync():
                try:
                    if not _get_channel_talk_secrets():
                        return
                    store_name_ct = _get_store_name_by_db(db_filename)
                    store_tag_key_ct = _get_store_tag_key(store_name_ct)
                    unpaid_ct = float(_sqlite_remaining) if _sqlite_remaining > 0 else 0.0
                    sync_channel_talk_customer(
                        customer_name=cust_name.strip(),
                        phone_number=phone1.strip(),
                        purchase_amount=final_sales_save,
                        item_category=category or "",
                        purchase_date=order_date,
                        store_tag_key=store_tag_key_ct,
                        is_returning=(not is_new_customer),
                        unpaid_balance=unpaid_ct,
                    )
                except Exception:
                    pass
            threading.Thread(target=_channel_talk_sync, daemon=True).start()
            st.toast("등록이 완료되었습니다. (채널톡 동기화는 백그라운드에서 진행됩니다.)", icon="✅")
            st.session_state["_new_sales_form_reset"] = st.session_state.get("_new_sales_form_reset", 0) + 1
            st.session_state["_cust_search_panel_open"] = False
            # 반복 등록 시 금액이 이전 건과 동일하게 남는 현상 방지: 비위젯 상태 초기화 후 위젯 키는 삭제
            st.session_state["payment_slot_count"] = DEFAULT_PAYMENT_SLOTS
            st.session_state["payment_rows"] = [{"method": "", "card_company": "", "amount": "0"} for _ in range(MAX_PAYMENT_SLOTS)]
            # 신규 매출 등록 관련 상태 초기화
            for key in list(st.session_state.keys()):
                if key in (
                    "phone1", "phone2", "address_manual", "address_detail",
                    "address_search_results", "address_search_error", "addr_keyword", "address_selection",
                    "order_date", "delivery_date",
                    "_show_address_dialog", "_dialog_addr_results", "_dialog_addr_error",
                    "_new_sales_selected_customer", "_cust_search_results",
                    "new_sales_cust_name", "new_sales_cust_search", "new_sales_cust_select",
                    "cost_price", "total_amount", "display_sales_amount", "display_cost_amount",
                    "visit_reason", "purchase_reason",
                    "payment_rows",
                ) or key.startswith(("pay_", "pay_onnuri_", "gen_pay", "d10_", "over_", "new_sales_employee_multiselect", "category_multiselect")):
                    try:
                        del st.session_state[key]
                    except Exception:
                        pass
            st.rerun()


# ========== 탭 3: 고객 및 잔금 관리 (3개 하위 탭) ==========

def _recalc_order_actual_margin(conn, order_id: int, db_filename: str | None = None):
    """해당 주문의 Payments 수수료 합계 및 잔금 상태(balance_status)를 Orders에 반영. Supabase 사용 시 db_filename 필요."""
    if _supabase_orders_payments_available() and db_filename:
        _recalc_order_actual_margin_supabase(db_filename, order_id)
        return
    row = conn.execute(
        "SELECT total_amount, cost_price, COALESCE(display_cost_amount,0) FROM Orders WHERE id = ?",
        (order_id,),
    ).fetchone()
    if not row:
        return
    total_amt, cost_general, cost_display = row[0] or 0, row[1] or 0, row[2] or 0
    total_fees = conn.execute(
        "SELECT COALESCE(SUM(fee_amount),0) FROM Payments WHERE order_id = ?",
        (order_id,),
    ).fetchone()[0]
    basic_m = total_amt - (cost_general + cost_display)
    conn.execute(
        "UPDATE Orders SET actual_margin = ? WHERE id = ?",
        (basic_m - total_fees, order_id),
    )
    paid = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM Payments WHERE order_id = ?",
        (order_id,),
    ).fetchone()[0]
    remaining = (total_amt or 0) - (paid or 0)
    balance_status = _balance_status_from_remaining(remaining)
    conn.execute(
        "UPDATE Orders SET balance_status = ? WHERE id = ?",
        (balance_status, order_id),
    )


def _multi_order_split_payment_ui(db_filename: str, orders_df: pd.DataFrame, key_prefix: str = "split"):
    """동일 고객의 복수 주문에 단일 결제 금액을 분배 등록하는 UI.
    orders_df: 잔금이 있는 주문들의 DataFrame (columns: id, balance, order_date, category, total_amount).
    한 번의 결제 수단/금액 입력으로 여러 주문에 자동 분배 저장."""
    if orders_df is None or len(orders_df) < 2:
        return

    st.markdown("#### 💳 복수 주문 분배 결제")
    st.caption("한 번의 결제(카드·온누리 등)로 여러 주문에 금액을 나누어 등록합니다.")

    # 총 잔금 표시
    total_balance = float(orders_df["balance"].sum())
    st.metric("전체 주문 합산 잔금", f"{total_balance:,.0f}원")

    # 결제 수단 / 날짜
    _split_date_key = f"{key_prefix}_date"
    if _split_date_key not in st.session_state:
        st.session_state[_split_date_key] = _today_kst()
    col_m, col_d = st.columns(2)
    with col_m:
        split_method = st.selectbox("결제 수단", options=PAYMENT_METHOD_OPTIONS, key=f"{key_prefix}_method")
    with col_d:
        split_date = st.date_input("결제 날짜 *", key=_split_date_key)

    # 카드사 / 메인페이
    _CARD_WITH_COMPANY_SPLIT = ("신용카드", "체크카드")
    if split_method in _CARD_WITH_COMPANY_SPLIT:
        split_card = st.selectbox("카드사", options=CARD_COMPANY_OPTIONS, key=f"{key_prefix}_card")
    elif split_method == "메인페이":
        split_card = st.text_input("메인페이 승인번호 4자리", key=f"{key_prefix}_card", max_chars=4)
    elif split_method == "지역화폐":
        split_card = st.text_input("지역화폐 승인번호", key=f"{key_prefix}_card")
    else:
        split_card = None
        st.session_state.pop(f"{key_prefix}_card", None)

    # 온누리상품권 승인번호
    is_onnuri_split = split_method and "온누리" in str(split_method)
    if is_onnuri_split:
        split_onnuri = st.text_input("온누리 승인번호 뒤 4자리 (대표 1건)", key=f"{key_prefix}_onnuri", max_chars=4)
    else:
        split_onnuri = None
        st.session_state.pop(f"{key_prefix}_onnuri", None)

    st.markdown("**주문별 배분 금액 입력**")
    st.caption("각 주문에 배분할 금액을 입력하세요. 합계가 실제 수령 금액과 일치해야 합니다.")

    alloc_keys = {}
    for _, orow in orders_df.iterrows():
        oid = int(orow["id"])
        bal = float(orow.get("balance") or 0)
        cat = str(orow.get("category") or "-")
        od = orow.get("order_date", "")
        od_str = str(od)[:10] if od else "-"
        ak = f"{key_prefix}_alloc_{oid}"
        if ak not in st.session_state:
            st.session_state[ak] = _format_number_comma(str(int(bal)))
        alloc_keys[oid] = ak
        col_info, col_input = st.columns([2, 1])
        with col_info:
            st.write(f"주문 #{oid} | {cat} | {od_str} | 잔금 **{bal:,.0f}원**")
        with col_input:
            st.text_input(
                f"배분 금액",
                key=ak,
                label_visibility="collapsed",
                on_change=lambda k=ak: st.session_state.__setitem__(k, _format_number_comma(st.session_state.get(k, ""))),
            )

    # 합계 검증
    alloc_total = sum(_parse_comma_to_int(st.session_state.get(ak, "0")) for ak in alloc_keys.values())
    if alloc_total > 0:
        st.info(f"배분 합계: **{alloc_total:,.0f}원**")

    split_reason = st.text_area("처리 사유 (필수, 5자 이상)", key=f"{key_prefix}_reason", placeholder="예: 온누리상품권 100만원 단일 결제 — 70만/30만 분배")

    if st.button("분배 결제 일괄 등록", key=f"{key_prefix}_btn", type="primary"):
        if not split_reason or len(split_reason.strip()) < 5:
            st.warning("사유를 5자 이상 입력하세요.")
            return
        if alloc_total <= 0:
            st.warning("배분 금액을 1원 이상 입력하세요.")
            return

        errors = []
        success_count = 0
        pay_date_str = split_date.isoformat() if hasattr(split_date, "isoformat") else _today_kst().isoformat()

        for oid, ak in alloc_keys.items():
            alloc_amt = _parse_comma_to_int(st.session_state.get(ak, "0"))
            if alloc_amt <= 0:
                continue
            orow_match = orders_df[orders_df["id"] == oid]
            if orow_match.empty:
                continue
            bal = float(orow_match.iloc[0].get("balance") or 0)
            fee = _payment_fee_amount(split_method, alloc_amt)
            onnuri_code = split_onnuri if is_onnuri_split else None
            try:
                if _supabase_orders_payments_available():
                    old_paid, _ = _sum_payments_by_order_supabase(db_filename, oid)
                    _insert_payment_supabase(db_filename, {
                        "order_id": oid,
                        "payment_date": pay_date_str,
                        "amount": alloc_amt,
                        "payment_method": split_method or None,
                        "card_company": split_card,
                        "fee_amount": fee,
                        "onnuri_approval_code": onnuri_code,
                        "created_by": _current_username(),
                    })
                    _recalc_order_actual_margin_supabase(db_filename, oid)
                    new_paid = old_paid + alloc_amt
                    cid_ph = _get_order_customer_id_supabase(db_filename, oid)
                    cname_ph = _get_customer_name_supabase(db_filename, cid_ph) if cid_ph else ""
                    _insert_payment_history(
                        None, oid, cname_ph, "분배결제(복수주문)",
                        {"order_id": oid, "balance_before": bal, "paid_total_before": old_paid},
                        {"order_id": oid, "added_amount": alloc_amt, "method": split_method, "balance_after": bal - alloc_amt, "paid_total_after": new_paid},
                        split_reason, db_filename=db_filename,
                    )
                else:
                    conn = get_tenant_conn(db_filename)
                    old_paid = conn.execute("SELECT COALESCE(SUM(amount),0) FROM Payments WHERE order_id=?", (oid,)).fetchone()[0] or 0
                    conn.execute(
                        "INSERT INTO Payments (order_id, payment_date, amount, payment_method, card_company, fee_amount, onnuri_approval_code, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,datetime('now', '+9 hours'))",
                        (oid, pay_date_str, alloc_amt, split_method or None, split_card, fee, onnuri_code, _current_username()),
                    )
                    _recalc_order_actual_margin(conn, oid, db_filename)
                    conn.commit()
                    conn.close()
                success_count += 1
            except Exception as e:
                errors.append(f"주문 #{oid}: {e}")

        clear_data_cache()
        if errors:
            for err in errors:
                st.error(err)
        if success_count > 0:
            st.toast(f"✅ {success_count}건 분배 결제 등록 완료!", icon="✅")
            st.rerun()


@st.fragment
def _customer_balance_payment_ui(
    db_filename: str,
    order_id: int,
    balance: float,
    key_prefix: str = "pay",
    default_payment_date: date | None = None,
):
    """잔금 완납 처리(결제 추가) 공통 UI. 직원도 사용 가능하되, 모든 변경은 PaymentHistory에 기록."""
    amt_key = f"{key_prefix}_amt"
    if amt_key not in st.session_state:
        st.session_state[amt_key] = _format_number_comma(str(int(balance))) if balance > 0 else "0"
    _pay_date_key = f"{key_prefix}_pay_date"
    if _pay_date_key not in st.session_state:
        st.session_state[_pay_date_key] = default_payment_date or _today_kst()
    st.caption("잔금 완납 처리 (결제 추가)")
    add_pay_date = st.date_input("결제 날짜 *", key=_pay_date_key)
    add_method = st.selectbox("결제 수단", options=PAYMENT_METHOD_OPTIONS, key=f"{key_prefix}_method")
    if add_method in _CARD_WITH_COMPANY:
        add_card = st.selectbox("카드사", options=CARD_COMPANY_OPTIONS, key=f"{key_prefix}_card")
    elif add_method == "메인페이":
        add_card = st.text_input("메인페이 승인번호 4자리", key=f"{key_prefix}_card", max_chars=4)
    elif add_method == "지역화폐":
        add_card = st.text_input("지역화폐 승인번호", key=f"{key_prefix}_card")
    else:
        add_card = None
    st.text_input("결제 금액", key=amt_key, on_change=lambda: st.session_state.__setitem__(amt_key, _format_number_comma(st.session_state.get(amt_key, ""))))
    add_amt_int = _parse_comma_to_int(st.session_state.get(amt_key, "0"))
    # 초과 결제 경고 (입력 차단 없음 — 결변·카드취소 처리 지원)
    if add_amt_int > 0 and add_amt_int > balance:
        _over = add_amt_int - max(balance, 0)
        st.warning(f"⚠️ 입력 금액({add_amt_int:,}원)이 잔금({max(balance,0):,.0f}원)보다 **{_over:,}원 초과**합니다. 결변·카드취소 처리 목적이면 그대로 등록하세요. 초과 금액은 '초과결제 항목' 탭에 표시됩니다.")
    # 온누리상품권일 때 승인번호/영수증 입력
    is_onnuri = add_method and ("온누리" in str(add_method))
    stage_key = f"{key_prefix}_onnuri_stage"
    last4_key = f"{key_prefix}_onnuri_last4"
    full_key = f"{key_prefix}_onnuri_full"
    receipt_key = f"{key_prefix}_onnuri_receipt"
    if is_onnuri:
        if stage_key not in st.session_state:
            st.session_state[stage_key] = "last4"
        stage = st.session_state.get(stage_key, "last4")
        if stage == "last4":
            st.text_input("온누리 승인번호 뒤 4자리", key=last4_key, max_chars=4)
        else:
            st.text_input("온누리 승인번호 전체 (8자리 이상)", key=full_key)
        st.file_uploader(
            "온누리상품권 영수증 사진(선택)",
            type=["png", "jpg", "jpeg", "webp"],
            key=receipt_key,
        )
    else:
        st.session_state.pop(stage_key, None)
    reason_key = f"{key_prefix}_reason"
    edit_reason = st.text_area("결제 메모(선택)", key=reason_key)
    if st.button("결제 등록", key=f"{key_prefix}_btn"):
        if add_amt_int > 0:
            # 온누리상품권 중복 검증: 오늘 날짜 + 승인번호 4자리 조합 (금액 제외)
            onnuri_code = None
            pay_date_str = add_pay_date.isoformat() if hasattr(add_pay_date, "isoformat") else _today_kst().isoformat()
            if is_onnuri:
                stage = st.session_state.get(stage_key, "last4")
                if stage == "last4":
                    last4_raw = (st.session_state.get(last4_key, "") or "").strip()
                    last4_digits = re.sub(r"\\D", "", last4_raw)
                    if len(last4_digits) != 4:
                        st.error("온누리상품권 결제의 승인번호 뒤 4자리를 정확히 입력하세요.")
                        return
                    if _supabase_orders_payments_available():
                        dup_cnt = _count_payments_onnuri_dup_supabase(db_filename, pay_date_str, last4_digits)
                    else:
                        conn_chk = get_tenant_conn(db_filename)
                        try:
                            dup_cnt = conn_chk.execute(
                                """
                                SELECT COUNT(*) FROM Payments
                                WHERE payment_method LIKE '%온누리%'
                                  AND payment_date = ?
                                  AND onnuri_approval_code IS NOT NULL
                                  AND substr(onnuri_approval_code, -4) = ?
                                """,
                                (pay_date_str, last4_digits),
                            ).fetchone()[0]
                        finally:
                            conn_chk.close()
                    if dup_cnt > 0:
                        st.session_state[stage_key] = "full"
                        st.error("⚠️ 동일한 결제일, 금액, 승인번호 4자리를 가진 기록이 이미 존재합니다. 정상 중복 건일 경우 승인번호 '전체 8자리 이상'을 입력해 주세요.")
                        return
                    onnuri_code = last4_digits
                else:
                    full_raw = (st.session_state.get(full_key, "") or "").strip()
                    full_digits = re.sub(r"\\D", "", full_raw)
                    if len(full_digits) < 8:
                        st.error("온누리상품권 승인번호 전체(8자리 이상)를 정확히 입력하세요.")
                        return
                    onnuri_code = full_digits
            fee = _payment_fee_amount(add_method, add_amt_int)
            use_supabase_op = _supabase_orders_payments_available()
            if use_supabase_op:
                paid_total, _ = _sum_payments_by_order_supabase(db_filename, order_id)
                old_paid_total = paid_total
                new_paid_total = old_paid_total + add_amt_int
                old_balance = balance
                new_balance = balance - add_amt_int
                _insert_payment_supabase(db_filename, {
                    "order_id": order_id,
                    "payment_date": pay_date_str,
                    "amount": add_amt_int,
                    "payment_method": add_method or None,
                    "card_company": add_card,
                    "fee_amount": fee,
                    "onnuri_approval_code": onnuri_code,
                    "created_by": _current_username(),
                })
                _recalc_order_actual_margin_supabase(db_filename, order_id)
                customer_id_for_ph = _get_order_customer_id_supabase(db_filename, order_id)
                customer_name = _get_customer_name_supabase(db_filename, customer_id_for_ph) if customer_id_for_ph else ""
                # Supabase 이력 저장 (conn 없이도 동작)
                _ph_err = _insert_payment_history(
                    None, order_id, customer_name, "잔금결제",
                    {"order_id": order_id, "balance_before": old_balance, "paid_total_before": old_paid_total},
                    {"order_id": order_id, "added_amount": add_amt_int, "method": add_method, "card_company": add_card, "balance_after": new_balance, "paid_total_after": new_paid_total},
                    edit_reason, db_filename=db_filename,
                )
                if _ph_err:
                    st.warning(f"⚠️ 이력 저장 오류 (기능은 정상): {_ph_err}")
                # SQLite 감사 로그 (파일이 있을 때만)
                conn = get_tenant_conn(db_filename)
                if conn:
                    try:
                        _insert_audit_log(conn, "Order", order_id, "payment_total", old_paid_total, new_paid_total, edit_reason)
                        _insert_audit_log(conn, "Order", order_id, "balance_amount", old_balance, new_balance, edit_reason)
                        conn.commit()
                    finally:
                        conn.close()
            else:
                conn = get_tenant_conn(db_filename)
                cur = conn.execute("SELECT COALESCE(SUM(amount),0) FROM Payments WHERE order_id = ?", (order_id,))
                old_paid_total = cur.fetchone()[0] or 0
                new_paid_total = old_paid_total + add_amt_int
                old_balance = balance
                new_balance = balance - add_amt_int
                conn.execute("""
                    INSERT INTO Payments (order_id, payment_date, amount, payment_method, card_company, fee_amount, onnuri_approval_code, created_by, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
                """, (order_id, pay_date_str, add_amt_int, add_method or None, add_card, fee, onnuri_code, _current_username()))
                _recalc_order_actual_margin(conn, order_id, db_filename)
                _insert_audit_log(conn, "Order", order_id, "payment_total", old_paid_total, new_paid_total, edit_reason)
                _insert_audit_log(conn, "Order", order_id, "balance_amount", old_balance, new_balance, edit_reason)
                cur_cid = conn.execute("SELECT customer_id FROM Orders WHERE id = ?", (order_id,)).fetchone()
                customer_id_for_ph = cur_cid[0] if cur_cid else None
                customer_name = _get_customer_name_supabase(db_filename, customer_id_for_ph) if customer_id_for_ph else ""
                old_payment_data = {"order_id": order_id, "balance_before": old_balance, "paid_total_before": old_paid_total}
                new_payment_data = {
                    "order_id": order_id,
                    "added_amount": add_amt_int,
                    "method": add_method,
                    "card_company": add_card,
                    "balance_after": new_balance,
                    "paid_total_after": new_paid_total,
                }
                _insert_payment_history(conn, order_id, customer_name, "잔금결제", old_payment_data, new_payment_data, edit_reason, db_filename=db_filename)
                conn.commit()
                conn.close()
            clear_data_cache()
            st.toast("등록되었습니다. 잔금이 0원이면 리스트에서 사라집니다.", icon="✅")
            st.rerun()
        else:
            st.warning("금액을 입력하세요.")


def render_customer_balance():
    db_filename = st.session_state.get("current_db")
    if not db_filename:
        st.warning("매장에 로그인한 후 이용하세요.")
        return
    st.header("고객 및 잔금 관리")
    current_user = st.session_state.get("current_user") or {}
    role = current_user.get("role", "user")
    tab_gen, tab_d10, tab_overdue, tab_anomaly = st.tabs([
        "1. 일반 고객 및 데이터 수정 (General)",
        "2. 미수금 (배송일 D-10 이내)",
        "3. 🚨 배송일 후 미결금액",
        "4. 🔴 초과결제 항목"
    ])
    today = _today_kst()

    # ---------- 탭 1: 일반 고객 및 데이터 수정 ----------
    with tab_gen:
        # 고객 엑셀 일괄 등록 (기준일 이전 고객용, 채널톡 동기화 없음)
        with st.expander("📤 고객 엑셀 일괄 등록 (기존 고객)"):
            st.caption("엑셀 파일로 고객을 일괄 등록합니다. 채널톡에는 등록되지 않습니다. 컬럼: 이름(또는 name), 전화번호1(또는 phone1), 전화번호2(또는 phone2), 주소(또는 address). UTF-8 인코딩 권장.")
            # 현재 app_customers 스키마에 맞는 샘플 CSV 다운로드
            sample_rows = [
                {
                    "이름": "홍길동",
                    "전화번호1": "010-1234-5678",
                    "전화번호2": "010-9876-5432",
                    "주소": "서울특별시 강남구 테헤란로 123",
                },
                {
                    "이름": "김이모",
                    "전화번호1": "010-0000-0000",
                    "전화번호2": "",
                    "주소": "부산광역시 해운대구 센텀서로 45",
                },
            ]
            sample_df = pd.DataFrame(sample_rows, columns=["이름", "전화번호1", "전화번호2", "주소"])
            _sample_csv_bytes = sample_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
            st.download_button(
                "📥 고객 일괄등록 샘플 CSV 다운로드",
                data=_sample_csv_bytes,
                file_name="고객_일괄등록_샘플.csv",
                mime="text/csv; charset=utf-8",
                key="customer_bulk_sample_csv",
            )
            excel_upload = st.file_uploader("엑셀 파일 (.xlsx)", type=["xlsx"], key="customer_excel_upload")
            if excel_upload is not None:
                try:
                    df = pd.read_excel(excel_upload, engine="openpyxl")
                    df = df.astype(str).fillna("")
                    col_map = {}
                    for c in df.columns:
                        c_str = (c or "").strip()
                        c_low = c_str.lower() if c_str else ""
                        if c_str == "이름" or c_low == "name":
                            col_map["name"] = c
                        elif c_str in ("전화번호1", "전화번호") or c_low == "phone1":
                            col_map["phone1"] = c
                        elif c_str == "전화번호2" or c_low == "phone2":
                            col_map["phone2"] = c
                        elif c_str == "주소" or c_low == "address":
                            col_map["address"] = c
                    if "name" not in col_map or "phone1" not in col_map:
                        st.error("엑셀에 '이름'(또는 name)과 '전화번호1'(또는 phone1) 컬럼이 필요합니다.")
                    else:
                        if st.button("엑셀 고객 일괄 등록 실행", key="customer_excel_import_btn"):
                            client, err = get_supabase_client()
                            if err:
                                st.error(f"⚠️ Supabase 연결 실패: {err}")
                            else:
                                try:
                                    store_name = _get_current_store_name_for_customers(db_filename)
                                    if not store_name:
                                        st.error("현재 매장 정보를 확인할 수 없습니다. 로그인 상태와 app_stores를 확인해 주세요.")
                                    else:
                                        qp = client.table("app_customers").select("phone1").eq("store_name", store_name)
                                        r = qp.execute()
                                        existing_phones = set()
                                        for row in (r.data or []):
                                            if row.get("phone1") and str(row["phone1"]).strip():
                                                existing_phones.add(re.sub(r"\D", "", str(row["phone1"])))
                                        inserted, skipped = 0, 0
                                        _batch_rows = []
                                        for _, row in df.iterrows():
                                            name_val = (row.get(col_map["name"]) or "").strip() if "name" in col_map else ""
                                            phone1_val = (row.get(col_map["phone1"]) or "").strip() if "phone1" in col_map else ""
                                            phone2_val = (row.get(col_map["phone2"]) or "").strip() if "phone2" in col_map else None
                                            address_val = (row.get(col_map["address"]) or "").strip() if "address" in col_map else None
                                            if not name_val and not phone1_val:
                                                continue
                                            if not phone1_val:
                                                skipped += 1
                                                continue
                                            phone1_digits = re.sub(r"\D", "", phone1_val)
                                            if phone1_digits in existing_phones:
                                                skipped += 1
                                                continue
                                            _batch_rows.append({
                                                "store_name": store_name,
                                                "name": name_val or "미입력",
                                                "phone1": phone1_val,
                                                "phone2": phone2_val,
                                                "address": address_val,
                                                "source": "엑셀",
                                            })
                                            existing_phones.add(phone1_digits)
                                        # 100건 단위 배치 INSERT (1건씩 → HTTP 요청 수 대폭 감소)
                                        _batch_size = 100
                                        for _bi in range(0, len(_batch_rows), _batch_size):
                                            _chunk = _batch_rows[_bi:_bi + _batch_size]
                                            client.table("app_customers").insert(_chunk).execute()
                                            inserted += len(_chunk)
                                        clear_data_cache()
                                        st.toast(f"엑셀 고객 일괄 등록 완료: {inserted}건 등록, {skipped}건 중복/스킵.", icon="✅")
                                        st.rerun()
                                except Exception as e:
                                    detail = getattr(e, "details", None) or getattr(e, "message", None) or str(e)
                                    st.error(f"엑셀 등록 중 오류: {detail}")
                except Exception as e:
                    st.error(f"엑셀 파일을 읽을 수 없습니다: {e}")

        # 채널톡 PUSH + PULL 연동
        with st.expander("📤 채널톡 연동 (PUSH 자동 / PULL 단건 조회)"):
            st.caption(
                "**PUSH**: '새로운 매출 등록' 저장 시 해당 고객 정보가 채널톡에 자동 전송됩니다. "
                "태그 형식: `매장키구매/품목` (예: 삼산구매/옷장). "
                "재구매 시 `재구매_매장키`, 미수금 있을 시 `미수금_매장키` 태그도 자동 추가됩니다.\n\n"
                "**PULL**: 아래에서 전화번호로 채널톡 고객 정보를 단건 조회할 수 있습니다."
            )
            ct_secrets_ok = bool(_get_channel_talk_secrets())
            if not ct_secrets_ok:
                st.warning("채널톡 API 키가 설정되지 않았습니다. `.streamlit/secrets.toml`의 `[channel_talk]` 항목을 설정해 주세요.")
            else:
                st.success("채널톡 API 키가 설정되어 있습니다.")

            st.markdown("---")
            st.markdown("**📥 채널톡 고객 단건 조회 (PULL)**")
            col_ph, col_btn = st.columns([3, 1])
            with col_ph:
                ct_pull_phone = st.text_input(
                    "조회할 전화번호 입력",
                    placeholder="01012345678",
                    key="ct_pull_phone_input",
                    label_visibility="collapsed",
                )
            with col_btn:
                ct_pull_btn = st.button("채널톡 조회", key="ct_pull_btn", width='stretch')
            if ct_pull_btn and ct_pull_phone and ct_pull_phone.strip():
                if not ct_secrets_ok:
                    st.error("채널톡 API 키를 먼저 설정해 주세요.")
                else:
                    with st.spinner("채널톡에서 고객 정보를 조회 중..."):
                        ct_user = fetch_channel_talk_customer_by_phone(ct_pull_phone.strip())
                    if ct_user:
                        profile = ct_user.get("profile") or {}
                        tags_ct = ct_user.get("tags") or []
                        st.success("채널톡에서 고객 정보를 찾았습니다.")
                        info_cols = st.columns(3)
                        info_cols[0].metric("이름", profile.get("name") or ct_user.get("name") or "-")
                        info_cols[1].metric("연락처", profile.get("mobileNumber") or ct_pull_phone.strip())
                        info_cols[2].metric("최근 구매액", f"{int(profile.get('오프라인_최근구매액') or 0):,}원")
                        st.write("**태그:**", ", ".join(tags_ct) if tags_ct else "없음")
                        st.write("**최근 구매일:**", profile.get("오프라인_최근구매일") or "-")
                        st.write("**최근 구매 품목:**", profile.get("오프라인_구매품목") or "-")
                        st.write("**누적 구매 횟수:**", profile.get("오프라인_누적구매횟수") or "-")

        # 채널톡 웹훅 수신 로그
        with st.expander("📋 채널톡 웹훅 수신 로그"):
            st.caption("채널톡에서 웹훅이 들어왔을 때 우리 DB에 등록된 기록입니다.")
            log_df = pd.DataFrame()
            try:
                sc_log, _ = get_supabase_client()
                if sc_log:
                    role = (st.session_state.get("current_user") or {}).get("role", "")
                    store_name_log = _get_current_store_name_for_customers(db_filename)
                    q_log = sc_log.table("channel_talk_webhook_log").select(
                        "id, created_at, store_key, phone, name, status, message, store_name, customer_id"
                    ).order("id", desc=True).limit(100)
                    if role != "superadmin" and store_name_log:
                        q_log = q_log.eq("store_name", store_name_log)
                    r_log = q_log.execute()
                    log_df = pd.DataFrame(r_log.data) if r_log.data else pd.DataFrame()
            except Exception:
                pass
            if log_df.empty:
                conn_m = get_master_conn()
                try:
                    role = (st.session_state.get("current_user") or {}).get("role", "")
                    if role == "superadmin":
                        log_df = pd.read_sql(
                            "SELECT id, created_at, store_key, phone, name, status, message, db_filename, customer_id FROM ChannelTalkWebhookLog ORDER BY id DESC LIMIT 100",
                            conn_m,
                        )
                    else:
                        log_df = pd.read_sql(
                            "SELECT id, created_at, store_key, phone, name, status, message, db_filename, customer_id FROM ChannelTalkWebhookLog WHERE db_filename = ? ORDER BY id DESC LIMIT 100",
                            conn_m,
                            params=(db_filename,),
                        )
                except Exception:
                    log_df = pd.DataFrame()
                finally:
                    conn_m.close()
            if len(log_df) > 0:
                log_disp = log_df.rename(columns={
                    "created_at": "수신 시각", "store_key": "매장키", "phone": "연락처", "name": "고객명",
                    "status": "상태", "message": "메시지", "store_name": "매장명",
                    "db_filename": "저장DB", "customer_id": "고객ID"
                })
                st.dataframe(log_disp, width='stretch')
            else:
                st.info("아직 채널톡 웹훅 수신 로그가 없습니다. 웹훅 수신 서버를 설정하면 여기에 표시됩니다.")
            st.write("**채널톡으로 등록된 고객 (본 매장)**")
            try:
                sc, _ = get_supabase_client()
                if sc:
                    store_name = _get_current_store_name_for_customers(db_filename)
                    if store_name:
                        qct = sc.table("app_customers").select("id, name, phone1, phone2, address, source").eq("store_name", store_name).in_("source", ["채널톡", "채널톡_웹훅"])
                        r = qct.order("id", desc=True).limit(100).execute()
                    else:
                        r = type("R", (), {"data": None})()
                        r.data = None
                    ct_customers = pd.DataFrame(r.data) if r.data else pd.DataFrame()
                else:
                    ct_customers = pd.DataFrame()
            except Exception:
                ct_customers = pd.DataFrame()
            if len(ct_customers) > 0:
                st.dataframe(ct_customers.rename(columns={"id": "ID", "name": "고객명", "phone1": "연락처1", "phone2": "연락처2", "address": "주소", "source": "가입경로"}), width='stretch')
            else:
                st.info("채널톡(또는 푸시)으로 등록된 고객이 없습니다.")

        st.subheader("고객 검색 (이름 또는 전화번호)")
        search_query = st.text_input("이름 또는 전화번호로 검색", key="gen_search")
        if not search_query or not search_query.strip():
            st.info("고객 이름 또는 전화번호를 입력하여 검색하세요.")
            customers = pd.DataFrame()
        else:
            q = search_query.strip()
            try:
                if _supabase_orders_payments_available():
                    sc, _ = get_supabase_client()
                    if sc:
                        store_name = _get_current_store_name_for_customers(db_filename)
                        if store_name:
                            q_safe = re.sub(r"[*,]", "", q.strip())
                            or_filter = f"name.ilike.*{q_safe}*,phone1.ilike.*{q_safe}*,phone2.ilike.*{q_safe}*" if q_safe else "id.eq.-1"
                            qcq = sc.table("app_customers").select("id, name, phone1, phone2, address").eq("store_name", store_name).or_(or_filter)
                            r = qcq.order("id", desc=True).limit(50).execute()
                            customers = pd.DataFrame(r.data) if r.data else pd.DataFrame()
                        else:
                            customers = pd.DataFrame()
                    else:
                        customers = pd.DataFrame()
                else:
                    conn = get_tenant_conn(db_filename)
                    if conn:
                        try:
                            pattern = f"%{q}%"
                            cur = conn.execute(
                                "SELECT id, name, phone1, phone2, address FROM Customers WHERE name LIKE ? OR phone1 LIKE ? OR phone2 LIKE ? ORDER BY id DESC LIMIT 50",
                                (pattern, pattern, pattern),
                            )
                            customers = pd.DataFrame(cur.fetchall(), columns=["id", "name", "phone1", "phone2", "address"])
                        finally:
                            conn.close()
                    else:
                        customers = pd.DataFrame()
            except Exception:
                customers = pd.DataFrame()

        if len(customers) == 0:
            if search_query and search_query.strip():
                st.info("검색 결과가 없습니다.")
        else:
            # 동일 고객(이름+전화번호) 중복 제거 → 한 명만 표시
            customers_unique = customers.drop_duplicates(subset=["name", "phone1"], keep="first").reset_index(drop=True)
            cust_options = customers_unique["id"].tolist()

            def _fmt_cust(cid):
                row = customers_unique[customers_unique["id"] == cid]
                if len(row) == 0:
                    return str(cid)
                r0 = row.iloc[0]
                return f"{r0.get('name') or '-'} ({r0.get('phone1') or '-'})"

            selected_cid = st.selectbox(
                "고객 선택",
                cust_options,
                format_func=_fmt_cust,
                key="gen_customer_select"
            )
            if selected_cid:
                cid = selected_cid
                # 선택된 고객과 동일한 이름+전화번호를 가진 모든 customer_id 수집 (중복 등록 시 모든 주문 조회)
                sel_row = customers_unique[customers_unique["id"] == cid].iloc[0]
                _sel_name = str(sel_row.get("name") or "")
                _sel_phone = str(sel_row.get("phone1") or "")
                all_cids = customers[
                    (customers["name"].fillna("") == _sel_name) &
                    (customers["phone1"].fillna("") == _sel_phone)
                ]["id"].tolist()
                if not all_cids:
                    all_cids = [cid]

                if _supabase_orders_payments_available():
                    all_orders = _load_orders_supabase(
                        db_filename,
                        "id, customer_id, order_date, delivery_date, category, cost_price, total_amount, actual_margin, display_sales_amount, display_cost_amount, visit_reason, purchase_reason, employee_names",
                        limit=None,
                    )
                    orders = all_orders[all_orders["customer_id"].isin(all_cids)].copy() if not all_orders.empty and "customer_id" in all_orders.columns else pd.DataFrame()
                    payments = _load_payments_supabase(db_filename)
                else:
                    conn = get_tenant_conn(db_filename)
                    try:
                        placeholders = ",".join("?" * len(all_cids))
                        orders = pd.read_sql(
                            f"SELECT id, order_date, delivery_date, category, cost_price, total_amount, COALESCE(actual_margin,0) AS actual_margin, display_sales_amount, display_cost_amount, visit_reason, purchase_reason, employee_names FROM Orders WHERE customer_id IN ({placeholders})",
                            conn, params=tuple(all_cids)
                        )
                        payments = pd.read_sql("SELECT order_id, amount, fee_amount FROM Payments", conn)
                    finally:
                        conn.close()
                pay_sum = payments.groupby("order_id")["amount"].sum() if not payments.empty and "order_id" in payments.columns else pd.Series(dtype=float)
                if orders.empty or "id" not in orders.columns:
                    st.info("아직 등록된 주문 데이터가 없습니다.")
                else:
                    orders = orders.copy()
                    orders["paid"] = orders["id"].map(pay_sum).fillna(0)
                    orders["balance"] = orders["total_amount"] - orders["paid"]
                    orders["delivery_date"] = pd.to_datetime(orders["delivery_date"], errors="coerce")
                    # 일반판매가 = total_amount - display_sales_amount
                    if "display_sales_amount" in orders.columns:
                        orders["일반판매가"] = (orders["total_amount"].fillna(0) - orders["display_sales_amount"].fillna(0)).clip(lower=0)
                        orders["전시판매가"] = orders["display_sales_amount"].fillna(0)
                        disp_cols = ["id", "order_date", "delivery_date", "category", "일반판매가", "전시판매가", "total_amount", "cost_price", "paid", "balance"]
                        show_cols = [c for c in disp_cols if c in orders.columns]
                        num_cols = [c for c in ["일반판매가", "전시판매가", "total_amount", "cost_price", "paid", "balance"] if c in orders.columns]
                        disp_df = orders[show_cols].copy()
                        disp_df = disp_df.rename(columns={"id": "주문ID", "order_date": "계약일", "delivery_date": "배송일"})
                        st.dataframe(_format_df_display(disp_df, num_cols), width='stretch')
                    else:
                        num_cols = [c for c in ["cost_price", "total_amount", "paid", "balance"] if c in orders.columns]
                        disp_df = orders.copy().rename(columns={"id": "주문ID", "order_date": "계약일", "delivery_date": "배송일"})
                        st.dataframe(_format_df_display(disp_df, num_cols), width='stretch')
                    # 선택된 주문의 변경 이력 보기
                    with st.expander("선택 주문 변경 이력 보기"):
                        def _fmt_order_hist(oid):
                            r = orders[orders["id"] == oid].iloc[0]
                            od = r.get("order_date") or ""
                            dlv = r.get("delivery_date") or ""
                            od_str = od.strftime("%Y-%m-%d") if hasattr(od, "strftime") else str(od)[:10]
                            dlv_str = dlv.strftime("%Y-%m-%d") if hasattr(dlv, "strftime") else str(dlv)[:10]
                            return f"주문 #{oid} | 계약일 {od_str} | 배송일 {dlv_str}"
                        hist_oid = st.selectbox("주문 선택 (변경 이력 조회용)", orders["id"].tolist(), format_func=_fmt_order_hist, key="gen_order_history_sel")
                        if hist_oid:
                            _render_order_audit_trail(db_filename, int(hist_oid))

                    with st.expander("📝 주문 수정 & 결제 관리", expanded=False):
                        cust_row = customers_unique[customers_unique["id"] == cid].iloc[0]
                        edit_prefix = f"edit_c{cid}"
                        _db_name = str(cust_row.get("name") or "")
                        _db_phone1 = str(cust_row.get("phone1") or "")
                        _db_phone2 = str(cust_row.get("phone2") or "")
                        _db_addr = str(cust_row.get("address") or "")
                        if not st.session_state.get(f"{edit_prefix}_name"):
                            st.session_state[f"{edit_prefix}_name"] = _db_name
                        if not st.session_state.get(f"{edit_prefix}_phone1"):
                            st.session_state[f"{edit_prefix}_phone1"] = _db_phone1
                        if f"{edit_prefix}_phone2" not in st.session_state:
                            st.session_state[f"{edit_prefix}_phone2"] = _db_phone2
                        if f"{edit_prefix}_address" not in st.session_state:
                            st.session_state[f"{edit_prefix}_address"] = _db_addr
                        def _fmt_order_sel(oid):
                            r = orders[orders["id"] == oid]
                            if r.empty:
                                return f"주문 #{oid}"
                            row = r.iloc[0]
                            od = row.get("order_date", "")
                            dlv = row.get("delivery_date", "")
                            od_str = od.strftime("%Y-%m-%d") if hasattr(od, "strftime") else str(od or "-")[:10]
                            dlv_str = dlv.strftime("%Y-%m-%d") if hasattr(dlv, "strftime") else str(dlv or "-")[:10]
                            return f"주문 #{oid} | 계약일 {od_str} | 배송일 {dlv_str}"
                        order_options = orders["id"].tolist()
                        sel_oid = st.selectbox("수정할 주문 선택", order_options, format_func=_fmt_order_sel, key=f"{edit_prefix}_order_sel")
                        if sel_oid:
                            orow = orders[orders["id"] == sel_oid].iloc[0]
                            # 재초기화 조건: OID 변경, 키 미존재, 또는 DB에 금액이 있는데 세션 상태가 0/빈값인 경우
                            # 0원 입력(주문 취소 정리)을 허용하기 위해 값이 0인지로 초기화 여부를 판단하지 않는다.
                            _need_order_init = (
                                st.session_state.get(f"{edit_prefix}_oid") != sel_oid
                                or f"{edit_prefix}_general_sales" not in st.session_state
                                or f"{edit_prefix}_cost" not in st.session_state
                                or f"{edit_prefix}_display_sales" not in st.session_state
                                or f"{edit_prefix}_display_cost" not in st.session_state
                            )
                            if _need_order_init:
                                # NaN/None/pd.NA 안전 변환 헬퍼
                                def _safe_float(v):
                                    try:
                                        f = float(v)
                                        return f if (f == f) else 0.0  # NaN 체크 (NaN != NaN)
                                    except Exception:
                                        return 0.0

                                dval = orow["delivery_date"]
                                if pd.notna(dval) and hasattr(dval, "date"):
                                    st.session_state[f"{edit_prefix}_delivery"] = dval.date()
                                else:
                                    st.session_state[f"{edit_prefix}_delivery"] = today
                                _disp_sales_val = _safe_float(orow.get("display_sales_amount"))
                                _disp_cost_val = _safe_float(orow.get("display_cost_amount"))
                                _total_val = _safe_float(orow.get("total_amount"))
                                _cost_val = _safe_float(orow.get("cost_price"))
                                _general_sales_val = max(0.0, _total_val - _disp_sales_val)
                                st.session_state[f"{edit_prefix}_general_sales"] = _format_number_comma(str(int(_general_sales_val)))
                                st.session_state[f"{edit_prefix}_cost"] = _format_number_comma(str(int(_cost_val)))
                                st.session_state[f"{edit_prefix}_display_sales"] = _format_number_comma(str(int(_disp_sales_val)))
                                st.session_state[f"{edit_prefix}_display_cost"] = _format_number_comma(str(int(_disp_cost_val)))
                                st.session_state[f"{edit_prefix}_has_display"] = _disp_sales_val > 0
                                st.session_state[f"{edit_prefix}_visit"] = str(orow.get("visit_reason") or "")
                                st.session_state[f"{edit_prefix}_purchase"] = str(orow.get("purchase_reason") or "")
                                _existing_emps = [e.strip() for e in str(orow.get("employee_names") or "").split(",") if e.strip()]
                                st.session_state[f"{edit_prefix}_employees"] = _existing_emps
                                # 카테고리 multiselect도 선택 주문 기준으로 초기화
                                CATEGORY_OPTIONS_EDIT_INIT = ["옷장", "식탁", "자녀방", "침대", "SSDS침대", "서재_학생", "소파", "소품", "전시품"]
                                _init_cats = [x.strip() for x in str(orow.get("category") or "").split(",") if x.strip() and x.strip() in CATEGORY_OPTIONS_EDIT_INIT]
                                st.session_state[f"{edit_prefix}_category_multiselect"] = _init_cats
                                # 초기화 완료 후 _oid 설정 (중간 실패 시 다음 렌더에서 재시도되도록)
                                st.session_state[f"{edit_prefix}_oid"] = sel_oid

                            # ── 섹션 1: 주문 정보 수정 ──
                            st.markdown("#### 📋 주문 정보 수정")
                            st.text_input("고객명", key=f"{edit_prefix}_name")
                            st.text_input("Phone 1", key=f"{edit_prefix}_phone1")
                            st.text_input("Phone 2", key=f"{edit_prefix}_phone2")
                            st.text_area("주소", key=f"{edit_prefix}_address")
                            st.date_input("배송일 *", key=f"{edit_prefix}_delivery")
                            CATEGORY_OPTIONS_EDIT = ["옷장", "식탁", "자녀방", "침대", "SSDS침대", "서재_학생", "소파", "소품", "전시품"]
                            # 세션 상태(_need_order_init 블록)에서 이미 초기화되므로 default= 사용 안 함
                            selected_categories_edit = st.multiselect(
                                "품목/카테고리 (복수 선택)",
                                options=CATEGORY_OPTIONS_EDIT,
                                key=f"{edit_prefix}_category_multiselect",
                            )
                            category_edit_val = ",".join(selected_categories_edit) if selected_categories_edit else None

                            # ── 금액 정보 (새 판매 등록폼과 동일 구조) ──
                            st.markdown("#### 💰 금액 정보")
                            def _fmt_general_sales():
                                st.session_state[f"{edit_prefix}_general_sales"] = _format_number_comma(st.session_state.get(f"{edit_prefix}_general_sales", ""))
                            def _fmt_edit_cost():
                                st.session_state[f"{edit_prefix}_cost"] = _format_number_comma(st.session_state.get(f"{edit_prefix}_cost", ""))
                            def _fmt_display_sales():
                                st.session_state[f"{edit_prefix}_display_sales"] = _format_number_comma(st.session_state.get(f"{edit_prefix}_display_sales", ""))
                            def _fmt_display_cost():
                                st.session_state[f"{edit_prefix}_display_cost"] = _format_number_comma(st.session_state.get(f"{edit_prefix}_display_cost", ""))
                            st.text_input("일반제품 판매가(Selling Price)", key=f"{edit_prefix}_general_sales", on_change=_fmt_general_sales)
                            st.text_input("일반제품 원가(Cost)", key=f"{edit_prefix}_cost", on_change=_fmt_edit_cost)
                            has_display_edit = st.checkbox(
                                "전시품 포함",
                                value=st.session_state.get(f"{edit_prefix}_has_display", False),
                                key=f"{edit_prefix}_has_display_chk",
                            )
                            if has_display_edit:
                                st.text_input("전시품 판매가", key=f"{edit_prefix}_display_sales", on_change=_fmt_display_sales)
                                st.text_input("전시품 원가", key=f"{edit_prefix}_display_cost", on_change=_fmt_display_cost)

                            # 실시간 합산 표시
                            _edit_general_sales = _parse_comma_to_int(st.session_state.get(f"{edit_prefix}_general_sales", "0"))
                            _edit_cost = _parse_comma_to_int(st.session_state.get(f"{edit_prefix}_cost", "0"))
                            _edit_display_sales = _parse_comma_to_int(st.session_state.get(f"{edit_prefix}_display_sales", "0")) if has_display_edit else 0
                            _edit_display_cost = _parse_comma_to_int(st.session_state.get(f"{edit_prefix}_display_cost", "0")) if has_display_edit else 0
                            _edit_total = _edit_general_sales + _edit_display_sales
                            _edit_total_cost = _edit_cost + _edit_display_cost
                            _edit_basic_margin = _edit_total - _edit_total_cost
                            _edit_basic_margin_rate = (_edit_basic_margin / _edit_total * 100) if _edit_total else 0.0
                            st.markdown("**합산 금액 (실시간)**")
                            _ec1, _ec2, _ec3, _ec4 = st.columns(4)
                            with _ec1:
                                st.metric("최종 총 판매금액", f"{_edit_total:,}원")
                            with _ec2:
                                st.metric("최종 총 원가", f"{_edit_total_cost:,}원")
                            with _ec3:
                                st.metric("기본 총 마진", f"{_edit_basic_margin:,}원")
                            with _ec4:
                                _ri = "🟢" if _edit_basic_margin_rate >= 20 else ("🟡" if _edit_basic_margin_rate >= 15 else "🔴")
                                st.metric(f"1차 마진율 {_ri}", f"{_edit_basic_margin_rate:.1f}%")

                            st.text_input("방문 이유", key=f"{edit_prefix}_visit")
                            st.text_input("구매 이유", key=f"{edit_prefix}_purchase")

                            # ── 담당 직원 변경 ──
                            st.markdown("#### 👤 담당 직원 변경")
                            _store_emp_names_edit = get_store_assigned_employee_names(db_filename)
                            if not _store_emp_names_edit:
                                try:
                                    _emp_conn = get_tenant_conn(db_filename)
                                    if _emp_conn:
                                        _emp_df = pd.read_sql("SELECT name FROM Employees WHERE is_active = 1", _emp_conn)
                                        _emp_conn.close()
                                        _store_emp_names_edit = _emp_df["name"].tolist() if not _emp_df.empty else []
                                except Exception:
                                    _store_emp_names_edit = []
                            _cur_emp_str = str(orow.get("employee_names") or "")
                            st.caption(f"현재 담당 직원: **{_cur_emp_str or '(없음)'}**")
                            if _store_emp_names_edit:
                                selected_employees_edit = st.multiselect(
                                    "담당 직원 선택 (변경 시 선택, 1/n 실적 분배 대상)",
                                    options=_store_emp_names_edit,
                                    key=f"{edit_prefix}_employees",
                                )
                            else:
                                st.warning("이 매장에 배정된 직원이 없어 직원 변경이 불가능합니다.")
                                selected_employees_edit = [e for e in (st.session_state.get(f"{edit_prefix}_employees") or []) if e]

                            # ── ⚖️ 실시간 잔금 검증 및 안내 로직 ──
                            _old_paid_total = float(orow["paid"])
                            _new_balance_preview = _edit_total - _old_paid_total
                            if _new_balance_preview >= 0:
                                st.session_state.pop(f"{edit_prefix}_allow_overpay", None)

                            st.markdown("#### ⚖️ 결제 및 잔금 검증")
                            st.metric("현재까지 결제된 총 금액", f"{int(_old_paid_total):,}원")

                            _block_update = False
                            # ── 감액 동시 처리 UI ──
                            _overdue_reduction_plan: dict = {}  # {payment_id: new_amount}
                            if _new_balance_preview < 0:
                                # 감액 필요 금액
                                _need_reduce = int(-_new_balance_preview)
                                st.warning(
                                    f"⚠️ **계약 감액 처리 필요**: 새 계약금액({_edit_total:,}원)이 결제 합계({int(_old_paid_total):,}원)보다 "
                                    f"**{_need_reduce:,}원** 적습니다.\n\n"
                                    f"아래에서 감액할 결제 건을 선택하면 **계약금액 + 결제 감액이 동시에 저장**됩니다."
                                )
                                _allow_overpay_edit = st.checkbox(
                                    "✅ 초과 허용: 결제 합계가 새 계약금액보다 커도 저장합니다. (잔금 상태는 이상결제로 저장되며, 초과결제 항목 탭에서도 동일 기준으로 표시됩니다.)",
                                    key=f"{edit_prefix}_allow_overpay",
                                )
                                # 이 주문의 결제 목록 로드
                                if _supabase_orders_payments_available():
                                    _reduce_pays = _load_payments_supabase(db_filename, sel_oid)
                                else:
                                    _rp_conn = get_tenant_conn(db_filename)
                                    try:
                                        _reduce_pays = pd.read_sql(
                                            "SELECT id, payment_date, amount, payment_method, card_company FROM Payments WHERE order_id=? ORDER BY id",
                                            _rp_conn, params=(sel_oid,)
                                        )
                                    except Exception:
                                        _reduce_pays = pd.DataFrame()
                                    finally:
                                        _rp_conn.close()

                                # 양수 결제 건만 표시 (마이너스 상계 전표 제외)
                                if not _reduce_pays.empty and "amount" in _reduce_pays.columns:
                                    _reduce_pays_pos = _reduce_pays[_reduce_pays["amount"].astype(float) > 0].copy()
                                else:
                                    _reduce_pays_pos = pd.DataFrame()

                                if _reduce_pays_pos.empty:
                                    if not _allow_overpay_edit:
                                        _block_update = True
                                        st.error("⛔ 조정 가능한 결제 내역이 없습니다. 결제 섹션에서 직접 처리하거나, 위 「초과 허용」을 선택해 주세요.")
                                else:
                                    st.markdown("**감액할 결제 건 선택 (새 금액 입력)**")
                                    _running_need = _need_reduce
                                    for _, _pr in _reduce_pays_pos.iterrows():
                                        _pr_id = int(_pr["id"])
                                        _pr_amt = float(_pr["amount"] or 0)
                                        _pr_method = str(_pr.get("payment_method") or "-")
                                        _pr_date = str(_pr.get("payment_date") or "-")[:10]
                                        _pr_key = f"reduce_amt_{_pr_id}_{sel_oid}"
                                        # 기본값: 남은 조정필요액만큼 차감
                                        _default_new = max(0, int(_pr_amt) - _running_need)
                                        if _pr_key not in st.session_state:
                                            st.session_state[_pr_key] = _format_number_comma(str(_default_new))
                                        _rc1, _rc2, _rc3 = st.columns([3, 2, 2])
                                        with _rc1:
                                            st.write(f"결제 #{_pr_id} | {_pr_method} | {_pr_date} | **{_pr_amt:,.0f}원**")
                                        with _rc2:
                                            st.text_input(
                                                "새 금액",
                                                key=_pr_key,
                                                label_visibility="collapsed",
                                                on_change=lambda k=_pr_key: st.session_state.__setitem__(k, _format_number_comma(st.session_state.get(k, ""))),
                                            )
                                        with _rc3:
                                            _new_pr_val = _parse_comma_to_int(st.session_state.get(_pr_key, "0"))
                                            _delta_pr = int(_pr_amt) - _new_pr_val
                                            if _delta_pr > 0:
                                                st.caption(f"▼ -{_delta_pr:,}원 감액")
                                            elif _delta_pr < 0:
                                                st.caption(f"▲ +{-_delta_pr:,}원 증액")
                                            else:
                                                st.caption("변경 없음")
                                        _overdue_reduction_plan[_pr_id] = _new_pr_val
                                        _running_need -= max(0, int(_pr_amt) - _new_pr_val)

                                    # 조정 후 잔금 재계산
                                    _planned_new_paid = sum(
                                        _parse_comma_to_int(st.session_state.get(f"reduce_amt_{_pr_id}_{sel_oid}", "0"))
                                        for _pr_id in _overdue_reduction_plan
                                    )
                                    _planned_balance = _edit_total - _planned_new_paid
                                    if _planned_balance < 0:
                                        if not _allow_overpay_edit:
                                            _block_update = True
                                            st.error(f"⛔ 조정 후에도 결제 합계({_planned_new_paid:,}원)가 새 계약금액({_edit_total:,}원)을 초과합니다. 감액을 더 늘리거나 「초과 허용」을 선택해 주세요.")
                                    elif _planned_balance == 0:
                                        st.success(f"✅ 조정 후 잔금: {_planned_balance:,}원 (완납)")
                                    else:
                                        st.info(f"ℹ️ 조정 후 예상 잔금: {_planned_balance:,}원")
                            elif _edit_total != float(orow["total_amount"] or 0) and _new_balance_preview > 0:
                                st.warning(f"⚠️ 증액 안내: 총 계약 금액이 늘어나서 **{int(_new_balance_preview):,}원**의 추가 잔금이 발생합니다.\\n\\n"
                                           f"👉 **다음 단계:** 아래 [수정 완료] 버튼을 누른 후, 화면 하단의 **[잔금 추가 결제]** 탭을 열어 증액된 금액만큼 추가 결제를 진행해 주세요.")
                            else:
                                st.info(f"✅ 변경 후 예상 잔금: {int(_new_balance_preview):,}원")

                            # 승인 절차 없이 전 직원 직접 수정 (결제 초과 시 기본은 버튼 잠금, 「초과 허용」 시 저장 가능)
                            edit_reason = st.text_area("변경 사유(필수, 예: 단가 할인, 옵션 추가 등)", key=f"{edit_prefix}_reason")
                            
                            if st.button("수정 완료 (Update)", key=f"{edit_prefix}_update_btn", disabled=_block_update, type="primary"):
                                if not edit_reason or not edit_reason.strip():
                                    st.warning("변경 사유를 반드시 입력하세요.")
                                else:
                                    _use_supa = _supabase_orders_payments_available()
                                    conn = None if _use_supa else get_tenant_conn(db_filename)
                                    old_total = float(orow["total_amount"] or 0)
                                    _oam = orow.get("actual_margin")
                                    try:
                                        old_actual_margin = 0.0 if pd.isna(_oam) else float(_oam)
                                    except (TypeError, ValueError):
                                        old_actual_margin = 0.0
                                    if old_actual_margin != old_actual_margin:  # NaN 방지
                                        old_actual_margin = 0.0
                                    old_cost = float(orow.get("cost_price") or 0)
                                    old_visit = orow.get("visit_reason") or ""
                                    old_purchase = orow.get("purchase_reason") or ""
                                    d_new = st.session_state.get(f"{edit_prefix}_delivery")
                                    delivery_str = d_new.isoformat() if hasattr(d_new, "isoformat") else str(d_new)
                                    new_total = _edit_total
                                    new_cost = _edit_cost
                                    new_display_sales = _edit_display_sales
                                    new_display_cost = _edit_display_cost
                                    new_visit = st.session_state.get(f"{edit_prefix}_visit") or None
                                    new_purchase = st.session_state.get(f"{edit_prefix}_purchase") or None
                                    old_employee_names = str(orow.get("employee_names") or "")
                                    _new_emp_list = st.session_state.get(f"{edit_prefix}_employees") or []
                                    new_employee_names = ",".join(e.strip() for e in _new_emp_list if e.strip())

                                    # 2차 서버단 방어 로직
                                    if _use_supa:
                                        payment_total, _ = _sum_payments_by_order_supabase(db_filename, sel_oid)
                                    else:
                                        pay_sum_row = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM Payments WHERE order_id = ?", (sel_oid,)).fetchone()
                                        payment_total = float(pay_sum_row[0]) if pay_sum_row else 0
                                        
                                    balance_check = new_total - payment_total
                                    _allow_overpay_save = bool(st.session_state.get(f"{edit_prefix}_allow_overpay"))
                                    if balance_check < 0:
                                        # 초과 허용: 결제는 그대로 두고 계약만 반영 → 재계산 시 balance_status=이상결제, 초과결제 탭은 balance<0 동일
                                        if _allow_overpay_save:
                                            pass
                                        elif _overdue_reduction_plan:
                                            today_str_reduce = datetime.now(tz=KST).strftime("%Y-%m-%d")
                                            _reduce_err = None
                                            for _rpid, _new_ramt in _overdue_reduction_plan.items():
                                                try:
                                                    if _use_supa:
                                                        _old_pay_rows = _load_payments_supabase(db_filename, sel_oid)
                                                        if not _old_pay_rows.empty and "id" in _old_pay_rows.columns:
                                                            _match = _old_pay_rows[_old_pay_rows["id"] == _rpid]
                                                            if not _match.empty:
                                                                _old_amt = float(_match.iloc[0]["amount"] or 0)
                                                                _old_method = str(_match.iloc[0].get("payment_method") or "")
                                                                if int(_old_amt) != _new_ramt:
                                                                    # 역분개(상계) 엔트리: 구 금액 음수
                                                                    _diff_amt = _new_ramt - int(_old_amt)
                                                                    sc_r, _ = get_supabase_client()
                                                                    if sc_r:
                                                                        sc_r.table("app_payments").insert({
                                                                            ORDERS_PAYMENTS_TENANT_COL: db_filename,
                                                                            "order_id": sel_oid,
                                                                            "payment_date": today_str_reduce,
                                                                            "amount": _diff_amt,
                                                                            "payment_method": _old_method or None,
                                                                            "fee_amount": 0,
                                                                            "created_by": _current_username(),
                                                                        }).execute()
                                                    else:
                                                        if conn:
                                                            _old_ramt_row = conn.execute("SELECT amount, payment_method FROM Payments WHERE id=?", (_rpid,)).fetchone()
                                                            if _old_ramt_row:
                                                                _old_amt_sql = float(_old_ramt_row[0] or 0)
                                                                _old_method_sql = _old_ramt_row[1] or ""
                                                                if int(_old_amt_sql) != _new_ramt:
                                                                    _diff_sql = _new_ramt - int(_old_amt_sql)
                                                                    conn.execute(
                                                                        "INSERT INTO Payments (order_id, payment_date, amount, payment_method, fee_amount, created_by, created_at) VALUES (?,?,?,?,0,?,datetime('now', '+9 hours'))",
                                                                        (sel_oid, today_str_reduce, _diff_sql, _old_method_sql or None, _current_username()),
                                                                    )
                                                except Exception as _re:
                                                    _reduce_err = str(_re)
                                            if _reduce_err:
                                                st.warning(f"⚠️ 결제 감액 처리 중 일부 오류: {_reduce_err}")
                                            # 감액 적용 후 payment_total 재계산
                                            if _use_supa:
                                                payment_total, _ = _sum_payments_by_order_supabase(db_filename, sel_oid)
                                            else:
                                                if conn:
                                                    payment_total = float(conn.execute("SELECT COALESCE(SUM(amount),0) FROM Payments WHERE order_id=?", (sel_oid,)).fetchone()[0] or 0)
                                            balance_check = new_total - payment_total
                                            if balance_check < 0:
                                                st.error(f"⛔ 감액 처리 후에도 결제 합계({payment_total:,.0f}원)가 새 계약금액({new_total:,.0f}원)을 초과합니다. 「초과 허용」으로 저장하려면 해당 옵션을 켠 뒤 다시 시도해 주세요.")
                                                if conn: conn.close()
                                                st.stop()
                                        else:
                                            st.error(f"⛔ 초과결제 감지: 결제 금액이 구매 금액보다 큽니다. 결제 내역을 조정하거나 「초과 허용」을 선택해 주세요.")
                                            if conn: conn.close()
                                            st.stop()

                                    margin_pct = (new_total - new_cost - new_display_cost) / new_total * 100 if new_total else 0
                                    if margin_pct < 15 or margin_pct > 25:
                                        st.warning(f"⚠️ 주의: 마진율이 {margin_pct:.1f}%입니다. 적정 범위(15%~25%)를 벗어났습니다.")
                                        
                                    # 감사 로그 및 매출 차액 반영
                                    if old_total != new_total:
                                        if conn: _insert_audit_log(conn, "Order", sel_oid, "total_amount", old_total, new_total, edit_reason)
                                        delta = new_total - old_total
                                        today_str = datetime.now(tz=KST).strftime("%Y-%m-%d")
                                        order_date_val = orow.get("order_date") or today_str
                                        if isinstance(order_date_val, str) and "-" in order_date_val:
                                            parts = order_date_val.split("-")
                                            order_date_label = f"{int(parts[1])}월 {int(parts[2])}일" if len(parts) >= 3 else str(order_date_val)
                                        else:
                                            order_date_label = str(order_date_val)
                                        _dm_kpi = (
                                            (old_actual_margin * float(delta) / old_total) if abs(old_total) > 1e-9 else 0.0
                                        )
                                        note = (
                                            f"{order_date_label} 주문 건 금액 변경에 따른 {'차감' if delta < 0 else '추가'}"
                                            f"|__dm:{int(round(_dm_kpi))}"
                                        )
                                        # 담당 직원: 수정 후 직원명 우선, 없으면 기존 직원명 사용 (delta도 같은 직원에게 귀속)
                                        _delta_emp = new_employee_names if new_employee_names else old_employee_names
                                        _insert_sales_transaction(db_filename, int(sel_oid), today_str, float(delta), note, employee_names=_delta_emp or None)
                                        if margin_pct < 15 or margin_pct > 25:
                                            store_name = _get_store_name_by_db(db_filename)
                                            _insert_admin_alert(store_name, "margin", f"[{store_name}] 마진율 {margin_pct:.1f}% 건이 수정되었습니다.")
                                            
                                    if old_cost != new_cost and conn:
                                        _insert_audit_log(conn, "Order", sel_oid, "cost_price", old_cost, new_cost, edit_reason)
                                    if (old_visit or "") != (new_visit or "") and conn:
                                        _insert_audit_log(conn, "Order", sel_oid, "visit_reason", old_visit, new_visit, edit_reason)
                                    if (old_purchase or "") != (new_purchase or "") and conn:
                                        _insert_audit_log(conn, "Order", sel_oid, "purchase_reason", old_purchase, new_purchase, edit_reason)
                                    if old_employee_names != new_employee_names and conn:
                                        _insert_audit_log(conn, "Order", sel_oid, "employee_names", old_employee_names, new_employee_names, edit_reason)

                                    try:
                                        sc, _ = get_supabase_client()
                                        if sc:
                                            store_name = _get_current_store_name_for_customers(db_filename)
                                            if store_name:
                                                upd_cust = {
                                                    "name": st.session_state[f"{edit_prefix}_name"].strip(),
                                                    "phone1": st.session_state[f"{edit_prefix}_phone1"].strip(),
                                                    "phone2": st.session_state.get(f"{edit_prefix}_phone2") or None,
                                                    "address": st.session_state.get(f"{edit_prefix}_address") or None,
                                                }
                                                # 주소가 있으면 카카오 지오코딩으로 위도/경도 자동 업데이트
                                                _upd_addr = upd_cust.get("address")
                                                if _upd_addr:
                                                    try:
                                                        _geo = geocode_address_kakao_extended(_upd_addr)
                                                        if _geo:
                                                            upd_cust["latitude"] = _geo["latitude"]
                                                            upd_cust["longitude"] = _geo["longitude"]
                                                    except Exception:
                                                        pass
                                                sc.table("app_customers").update(upd_cust).eq("store_name", store_name).eq("id", cid).execute()
                                            # Supabase 직원명 변경 이력 기록 (app_edit_requests)
                                            if old_employee_names != new_employee_names:
                                                try:
                                                    _actor = _current_username()
                                                    sc.table("app_edit_requests").insert({
                                                        "db_filename": db_filename,
                                                        "tenant_name": _get_store_name_by_db(db_filename) or db_filename,
                                                        "entity_type": "Order",
                                                        "entity_id": int(sel_oid),
                                                        "requested_by": _actor or "",
                                                        "payload": f"employee_names: {old_employee_names!r} → {new_employee_names!r}",
                                                        "reason": edit_reason,
                                                        "status": "approved",
                                                    }).execute()
                                                except Exception:
                                                    pass
                                    except Exception:
                                        pass
                                        
                                    # DB 업데이트 실행
                                    if _use_supa:
                                        _update_order_supabase(db_filename, sel_oid, {
                                            "delivery_date": delivery_str,
                                            "category": category_edit_val,
                                            "total_amount": new_total,
                                            "cost_price": new_cost,
                                            "display_sales_amount": new_display_sales,
                                            "display_cost_amount": new_display_cost,
                                            "visit_reason": new_visit,
                                            "purchase_reason": new_purchase,
                                            "employee_names": new_employee_names or None,
                                        })
                                        _recalc_order_actual_margin_supabase(db_filename, sel_oid)
                                    else:
                                        conn.execute(
                                            "UPDATE Orders SET delivery_date=?, category=?, total_amount=?, cost_price=?, display_sales_amount=?, display_cost_amount=?, visit_reason=?, purchase_reason=?, employee_names=? WHERE id=?",
                                            (delivery_str, category_edit_val, new_total, new_cost, new_display_sales, new_display_cost, new_visit, new_purchase, new_employee_names or None, sel_oid),
                                        )
                                        # 계약금액/원가 변경 시 SQLite PaymentHistory에도 이력 기록
                                        if old_total != new_total or old_cost != new_cost:
                                            _old_disp_sales = float(orow.get("display_sales_amount") or 0)
                                            _old_disp_cost = float(orow.get("display_cost_amount") or 0)
                                            _cust_name_ph = (st.session_state.get(f"{edit_prefix}_name") or "").strip()
                                            _old_data_sql = {
                                                "order_id": int(sel_oid),
                                                "old_total": old_total,
                                                "old_cost": old_cost,
                                                "old_display_sales": _old_disp_sales,
                                                "old_display_cost": _old_disp_cost,
                                            }
                                            _new_data_sql = {
                                                "order_id": int(sel_oid),
                                                "new_total": new_total,
                                                "new_cost": new_cost,
                                                "new_display_sales": new_display_sales,
                                                "new_display_cost": new_display_cost,
                                            }
                                            _ph_err_sql = _insert_payment_history(
                                                conn,
                                                sel_oid,
                                                _cust_name_ph,
                                                "판매금액변경",
                                                _old_data_sql,
                                                _new_data_sql,
                                                edit_reason,
                                                db_filename=db_filename,
                                            )
                                            if _ph_err_sql:
                                                st.warning(f"⚠️ 계약 변경 이력(SQLite) 저장 오류: {_ph_err_sql}")
                                        _recalc_order_actual_margin(conn, sel_oid, db_filename)
                                        conn.commit()
                                        
                                    if conn:
                                        conn.close()
                                    clear_data_cache()
                                    # 수정 후 재초기화 강제: 다음 렌더에서 DB 최신값으로 재로드
                                    st.session_state.pop(f"{edit_prefix}_oid", None)

                                    # ── 알림 발송 ──
                                    _actor_uname = _current_username()
                                    if old_employee_names != new_employee_names:
                                        # 새로 배분된 직원에게만 'sales_assigned' 알림
                                        _old_emp_set = set(e.strip() for e in old_employee_names.split(",") if e.strip())
                                        _new_emp_set = set(e.strip() for e in new_employee_names.split(",") if e.strip())
                                        _newly_added = ",".join(_new_emp_set - _old_emp_set)
                                        if _newly_added:
                                            _insert_order_notification(
                                                db_filename, int(sel_oid), _newly_added,
                                                "sales_assigned",
                                                f"담당 직원 변경: {old_employee_names or '(없음)'} → {new_employee_names}",
                                                _actor_uname, edit_reason,
                                            )
                                        # 기존 담당자에게도 '수정' 알림
                                        _notif_target = ",".join(_old_emp_set | _new_emp_set)
                                        _insert_order_notification(
                                            db_filename, int(sel_oid), _notif_target,
                                            "order_modified",
                                            f"담당 직원이 변경되었습니다: {old_employee_names or '(없음)'} → {new_employee_names}",
                                            _actor_uname, edit_reason,
                                        )
                                    else:
                                        # 금액/날짜 등 수정: 현재 담당 직원 전원에게 알림
                                        _insert_order_notification(
                                            db_filename, int(sel_oid), new_employee_names,
                                            "order_modified",
                                            f"주문 정보가 수정되었습니다 (판매가: {int(old_total):,}→{int(new_total):,}원)",
                                            _actor_uname, edit_reason,
                                        )

                                    # ── 부정행위 탐지 (코어 로직 무관 — 항상 try/except 격리) ──
                                    try:
                                        _check_and_send_fraud_signals(
                                            db_filename=db_filename,
                                            order_id=int(sel_oid),
                                            actor_username=_actor_uname or "unknown",
                                            old_total=old_total,
                                            new_total=new_total,
                                            old_cost=old_cost,
                                            new_cost=new_cost,
                                            new_display_cost=new_display_cost,
                                            old_employee_names=old_employee_names,
                                            new_employee_names=new_employee_names,
                                            reason=edit_reason,
                                            action_type="order_modified",
                                        )
                                    except Exception:
                                        pass

                                    _toast_parts = ["수정 내용이 즉시 반영되었습니다."]
                                    if old_employee_names != new_employee_names:
                                        _toast_parts.append(f"담당 직원: {old_employee_names or '(없음)'} → {new_employee_names or '(없음)'}")
                                    st.toast("✅ " + " | ".join(_toast_parts), icon="✅")
                                    st.rerun()

                            # ── 섹션 2: 결제 내역 조회 및 수정 ──
                            st.divider()
                            st.markdown("#### 💳 결제 내역 조회 및 수정")
                            st.caption("신용카드 → 현금 등 수단 변경 시: 해당 결제를 취소(금액 0 입력)한 뒤 아래 '잔금 추가 결제'에서 새 수단으로 등록하세요.")
                            customer_name_for_receipt = (customers[customers["id"] == cid].iloc[0]["name"] or "고객").strip()
                            for _order_id_pay in orders["id"].tolist():
                                if _supabase_orders_payments_available():
                                    pay_list = _load_payments_supabase(db_filename, _order_id_pay)
                                    if not pay_list.empty:
                                        pay_list = pay_list[["id", "payment_date", "amount", "payment_method", "card_company", "fee_amount"]]
                                    else:
                                        pay_list = pd.DataFrame()
                                else:
                                    _conn_pay = get_tenant_conn(db_filename)
                                    try:
                                        pay_list = pd.read_sql(
                                            "SELECT id, payment_date, amount, payment_method, card_company, fee_amount FROM Payments WHERE order_id = ? ORDER BY id",
                                            _conn_pay, params=(_order_id_pay,)
                                        )
                                    except Exception:
                                        pay_list = pd.DataFrame()
                                    finally:
                                        _conn_pay.close()
                                order_row = orders[orders["id"] == _order_id_pay].iloc[0]
                                total_sales = float(order_row["total_amount"] or 0)
                                current_balance = float(order_row["balance"] or 0)
                                ord_date = order_row.get("order_date", "")
                                ord_str = ord_date.strftime("%Y-%m-%d") if hasattr(ord_date, "strftime") else str(ord_date)[:10] if ord_date else ""
                                dlv_str = ""
                                if pd.notna(order_row.get("delivery_date")):
                                    d = order_row["delivery_date"]
                                    dlv_str = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)[:10] if d else ""
                                exp_label = f"주문 #{_order_id_pay} | 계약일 {ord_str} | 배송일 {dlv_str} | 총액 {total_sales:,.0f}원 | 잔금 {current_balance:,.0f}원"
                                with st.expander(exp_label, expanded=(_order_id_pay == sel_oid)):
                                    if pay_list.empty if hasattr(pay_list, 'empty') else len(pay_list) == 0:
                                        st.info("해당 주문의 결제 내역이 없습니다.")
                                    else:
                                        pay_display = pay_list.copy()
                                        pay_display["amount"] = pay_display["amount"].apply(lambda x: f"{x:,.0f}원")
                                        pay_display["fee_amount"] = pay_display["fee_amount"].fillna(0).apply(lambda x: f"{x:,.0f}원")
                                        pay_display = pay_display.rename(columns={"id": "결제ID", "payment_date": "결제일", "amount": "금액", "payment_method": "수단", "card_company": "카드사", "fee_amount": "수수료"})
                                        st.dataframe(pay_display[["결제ID", "결제일", "금액", "수단", "카드사", "수수료"]], width='stretch')
                                        for _, prow in pay_list.iterrows():
                                            with st.expander(f"결제 ID {prow['id']} — {prow['payment_method'] or '-'} {float(prow['amount'] or 0):,.0f}원"):
                                                col_left, col_right = st.columns(2)
                                                with col_left:
                                                    st.info("**기존 결제 내역 (비교용)**")
                                                    st.write(f"**총판매금액:** {total_sales:,.0f}원")
                                                    st.write(f"**기존 결제수단:** {prow['payment_method'] or '-'}")
                                                    st.write(f"**결제금액:** {float(prow['amount'] or 0):,.0f}원")
                                                    st.write(f"**미수금:** {current_balance:,.0f}원")
                                                with col_right:
                                                    if float(prow["amount"] or 0) < 0:
                                                        st.warning(
                                                            "⚠️ 회계 상계(취소) 처리를 위해 자동 생성된 마이너스 전표는 수정할 수 없습니다. "
                                                            "필요 시 아래에서 삭제할 수 있습니다."
                                                        )
                                                        st.divider()
                                                        st.markdown("##### 🗑️ 마이너스(상계) 전표 삭제")
                                                        st.caption(
                                                            "상계 전표를 삭제하면 결제 합계에서 해당 상계분이 빠져 잔금·마진에 반영됩니다. "
                                                            "원 양수 결제와의 짝이 맞는지 확인한 뒤 삭제하세요."
                                                        )
                                                        _neg_dd_confirm_key = f"pay_neg_del_confirm_{prow['id']}"
                                                        st.checkbox(
                                                            f"결제 ID {int(prow['id'])} (상계 {float(prow.get('amount') or 0):,.0f}원) 삭제 동의",
                                                            key=_neg_dd_confirm_key,
                                                        )
                                                        if st.button(
                                                            "🗑️ 상계 전표 삭제 실행",
                                                            key=f"pay_neg_del_btn_{prow['id']}",
                                                            type="secondary",
                                                        ):
                                                            if not st.session_state.get(_neg_dd_confirm_key):
                                                                st.warning("삭제하려면 확인 체크박스를 먼저 선택해 주세요.")
                                                            else:
                                                                try:
                                                                    _neg_amt = float(prow.get("amount") or 0)
                                                                    _neg_pid = int(prow["id"])
                                                                    _neg_paid_before, _neg_paid_after = 0.0, 0.0
                                                                    _neg_cname = ""
                                                                    if _supabase_orders_payments_available():
                                                                        _neg_paid_before, _ = _sum_payments_by_order_supabase(db_filename, _order_id_pay)
                                                                        _neg_cid = _get_order_customer_id_supabase(db_filename, _order_id_pay)
                                                                        _neg_cname = _get_customer_name_supabase(db_filename, _neg_cid) if _neg_cid else ""
                                                                        _neg_ok = _delete_payment_supabase(db_filename, _neg_pid)
                                                                    else:
                                                                        _neg_conn = get_tenant_conn(db_filename)
                                                                        try:
                                                                            _neg_conn.execute("DELETE FROM Payments WHERE id = ?", (_neg_pid,))
                                                                            _neg_conn.commit()
                                                                            _neg_ok = True
                                                                        except Exception:
                                                                            _neg_ok = False
                                                                        finally:
                                                                            _neg_conn.close()
                                                                    if _neg_ok:
                                                                        if _supabase_orders_payments_available():
                                                                            _neg_paid_after, _ = _sum_payments_by_order_supabase(db_filename, _order_id_pay)
                                                                            _recalc_order_actual_margin_supabase(db_filename, _order_id_pay)
                                                                        _neg_old_bal = float(orders[orders["id"] == _order_id_pay]["balance"].iloc[0]) if not orders[orders["id"] == _order_id_pay].empty else 0.0
                                                                        _neg_new_bal = _neg_old_bal + _neg_amt
                                                                        _insert_payment_history(
                                                                            None,
                                                                            _order_id_pay,
                                                                            _neg_cname,
                                                                            "결제직접삭제",
                                                                            {
                                                                                "order_id": int(_order_id_pay),
                                                                                "paid_total_before": _neg_paid_before,
                                                                                "payment": {
                                                                                    "payment_id": _neg_pid,
                                                                                    "amount": _neg_amt,
                                                                                    "method": prow.get("payment_method"),
                                                                                },
                                                                            },
                                                                            {
                                                                                "order_id": int(_order_id_pay),
                                                                                "paid_total_after": _neg_paid_after,
                                                                                "balance_after": _neg_new_bal,
                                                                            },
                                                                            "마이너스(상계) 전표 삭제",
                                                                            db_filename=db_filename,
                                                                        )
                                                                        clear_data_cache()
                                                                        st.success(f"✅ 상계 전표(결제 ID {_neg_pid}) 삭제 완료")
                                                                        st.rerun()
                                                                    else:
                                                                        st.error("삭제 실패. 잠시 후 다시 시도해 주세요.")
                                                                except Exception as _neg_e:
                                                                    st.error(f"삭제 오류: {_neg_e}")
                                                    else:
                                                        # 결제 수단 변경
                                                        _cur_method = prow.get("payment_method") or PAYMENT_METHOD_OPTIONS[0]
                                                        _method_idx = PAYMENT_METHOD_OPTIONS.index(_cur_method) if _cur_method in PAYMENT_METHOD_OPTIONS else 0
                                                        new_method = st.selectbox(
                                                            "결제 수단 변경",
                                                            options=PAYMENT_METHOD_OPTIONS,
                                                            index=_method_idx,
                                                            key=f"pay_edit_method_{prow['id']}",
                                                        )
                                                        # 카드사/승인번호 (수단에 따라)
                                                        if new_method in _CARD_WITH_COMPANY:
                                                            _cur_card = prow.get("card_company") or CARD_COMPANY_OPTIONS[0]
                                                            _card_idx = CARD_COMPANY_OPTIONS.index(_cur_card) if _cur_card in CARD_COMPANY_OPTIONS else 0
                                                            new_card_company = st.selectbox(
                                                                "카드사 변경",
                                                                options=CARD_COMPANY_OPTIONS,
                                                                index=_card_idx,
                                                                key=f"pay_edit_card_{prow['id']}",
                                                            )
                                                        elif new_method == "메인페이":
                                                            _cur_appr = prow.get("card_company") or ""
                                                            new_card_company = st.text_input(
                                                                "메인페이 승인번호 4자리",
                                                                value=_cur_appr,
                                                                max_chars=4,
                                                                key=f"pay_edit_card_{prow['id']}",
                                                            )
                                                        elif new_method == "지역화폐":
                                                            _cur_appr = prow.get("card_company") or ""
                                                            new_card_company = st.text_input(
                                                                "지역화폐 승인번호",
                                                                value=_cur_appr,
                                                                key=f"pay_edit_card_{prow['id']}",
                                                            )
                                                        else:
                                                            new_card_company = None
                                                            # 위젯 키 충돌 방지용 빈 placeholder
                                                            st.empty()
                                                        # 결제 날짜 변경 (기존 날짜 기본값)
                                                        _cur_pay_date = prow.get("payment_date")
                                                        try:
                                                            _cur_pay_date_val = pd.to_datetime(_cur_pay_date).date() if _cur_pay_date else _today_kst()
                                                        except Exception:
                                                            _cur_pay_date_val = _today_kst()
                                                        _edit_date_key = f"pay_edit_date_{prow['id']}"
                                                        if _edit_date_key not in st.session_state:
                                                            st.session_state[_edit_date_key] = _cur_pay_date_val
                                                        new_pay_date = st.date_input(
                                                            "결제 날짜 *",
                                                            key=_edit_date_key,
                                                        )
                                                        _old_amt_for_mode = int(float(prow["amount"] or 0))
                                                        _pay_mode_key = f"pay_edit_mode_{prow['id']}"
                                                        _pay_mode_prev_key = f"pay_edit_mode_prev_{prow['id']}"
                                                        _pay_mode_cur = st.radio(
                                                            "입력 방식",
                                                            ["새 금액 입력", "증감액 입력 (+증액, -감액)"],
                                                            key=_pay_mode_key,
                                                            horizontal=True,
                                                            help="새 금액: 변경 후 최종 금액 전체 입력. 증감액: 변경분만 입력 (예: -1,000,000)",
                                                        )
                                                        _is_delta_mode = (_pay_mode_cur == "증감액 입력 (+증액, -감액)")
                                                        _pay_amt_key = f"pay_edit_amt_{prow['id']}"
                                                        if st.session_state.get(_pay_mode_prev_key) != _pay_mode_cur:
                                                            st.session_state[_pay_amt_key] = "0" if _is_delta_mode else _format_number_comma(str(_old_amt_for_mode))
                                                            st.session_state[_pay_mode_prev_key] = _pay_mode_cur
                                                        elif _pay_amt_key not in st.session_state:
                                                            st.session_state[_pay_amt_key] = _format_number_comma(str(_old_amt_for_mode))
                                                        if _is_delta_mode:
                                                            st.text_input(
                                                                "증감액 (예: -1,000,000 감액 / +500,000 증액 / 0 전액취소)",
                                                                key=_pay_amt_key,
                                                            )
                                                            _delta_raw = str(st.session_state.get(_pay_amt_key, "0")).strip()
                                                            _delta_sign = -1 if _delta_raw.startswith("-") else 1
                                                            _delta_abs = int(re.sub(r"\D", "", _delta_raw) or "0")
                                                            _delta_val = _delta_sign * _delta_abs
                                                            new_amount = max(0, _old_amt_for_mode + _delta_val)
                                                            st.caption(f"현재: **{_old_amt_for_mode:,}원** → 변경 후: **{new_amount:,}원**")
                                                        else:
                                                            def _fmt_pay_edit_amt(_k=_pay_amt_key):
                                                                st.session_state[_k] = _format_number_comma(st.session_state.get(_k, ""))
                                                            st.text_input(
                                                                "변경할 새 금액 (0이면 결제 취소)",
                                                                key=_pay_amt_key,
                                                                on_change=_fmt_pay_edit_amt,
                                                            )
                                                            new_amount = _parse_comma_to_int(st.session_state.get(_pay_amt_key, "0"))
                                                            st.caption(f"현재: **{_old_amt_for_mode:,}원** → 변경 후: **{new_amount:,}원**")
                                                        del_reason = st.text_input("결제 변경 사유 (필수, 5자 이상)", key=f"pay_del_reason_{prow['id']}", placeholder="예: 카드 취소 후 현금 결제")
                                                        receipt_upload = st.file_uploader(
                                                            "📷 취소/재결제 영수증 사진 업로드",
                                                            type=["png", "jpg", "jpeg"],
                                                            key=f"pay_receipt_{prow['id']}",
                                                        )
                                                        if st.button("수정 완료", key=f"pay_edit_{prow['id']}"):
                                                            if not del_reason or len(del_reason.strip()) < 5:
                                                                st.warning("사유를 5자 이상 입력하세요.")
                                                            else:
                                                                receipt_path_saved = None
                                                                if receipt_upload:
                                                                    safe_name = re.sub(r"[^\w\u3130-\u318f\uac00-\ud7af]", "_", customer_name_for_receipt)[:30]
                                                                    ts = datetime.now(tz=KST).strftime("%Y%m%d_%H%M%S")
                                                                    ext = (receipt_upload.name or "").split(".")[-1].lower() or "jpg"
                                                                    if ext not in ("png", "jpg", "jpeg"):
                                                                        ext = "jpg"
                                                                    fname = f"receipt_{safe_name}_{ts}.{ext}"
                                                                    receipt_path_saved = os.path.join(RECEIPTS_UPLOAD_DIR, fname)
                                                                    with open(receipt_path_saved, "wb") as f:
                                                                        f.write(receipt_upload.getvalue())
                                                                old_balance = float(orders[orders["id"] == _order_id_pay]["balance"].iloc[0])
                                                                old_payment = {
                                                                    "payment_id": int(prow["id"]),
                                                                    "amount": float(prow["amount"] or 0),
                                                                    "method": prow["payment_method"],
                                                                    "card_company": prow["card_company"],
                                                                }
                                                                # 새 수수료 계산 (수단 변경 반영)
                                                                new_fee = _payment_fee_amount(new_method, float(new_amount)) if new_amount > 0 else 0.0
                                                                # 상계(-) / 재결제(+) 모두 화면의「결제 날짜」반영 (오늘 고정 사용 안 함)
                                                                _pay_edit_date_str = (
                                                                    new_pay_date.isoformat()
                                                                    if isinstance(new_pay_date, date)
                                                                    else _today_kst().isoformat()
                                                                )
                                                                old_amt_val = float(prow["amount"] or 0)
                                                                old_fee_val = float(prow["fee_amount"] or 0)
                                                                _pay_edit_committed = False

                                                                if _supabase_orders_payments_available():
                                                                    old_paid_total, _ = _sum_payments_by_order_supabase(db_filename, _order_id_pay)

                                                                    # 1. 마이너스(-) 상계 전표 INSERT (계좌이체 등 card_company가 pandas NaN이면 JSON 오류 → _insert_payment_supabase에서 None 처리)
                                                                    _rev_err: list[str] = []
                                                                    _rev_pay_id = _insert_payment_supabase(
                                                                        db_filename,
                                                                        {
                                                                            "order_id": _order_id_pay,
                                                                            "payment_date": _pay_edit_date_str,
                                                                            "amount": -old_amt_val,
                                                                            "payment_method": prow["payment_method"],
                                                                            "card_company": prow["card_company"],
                                                                            "fee_amount": -old_fee_val,
                                                                            "created_by": _current_username(),
                                                                        },
                                                                        _error_detail=_rev_err,
                                                                    )
                                                                    if _rev_pay_id is None:
                                                                        if _rev_err:
                                                                            st.error(
                                                                                "상계(마이너스) 전표를 Supabase에 저장하지 못했습니다. 상세: "
                                                                                + _rev_err[0]
                                                                            )
                                                                        else:
                                                                            st.error(
                                                                                "상계(마이너스) 전표를 Supabase에 저장하지 못했습니다. "
                                                                                "네트워크·DB 권한을 확인한 뒤 다시 시도해 주세요."
                                                                            )
                                                                    elif new_amount == 0:
                                                                        action = "결제취소"
                                                                        new_payment = {}
                                                                        _pay_edit_committed = True
                                                                    else:
                                                                        # 2. 새로운 결제(+) 전표 INSERT
                                                                        _new_err: list[str] = []
                                                                        _new_pay_id = _insert_payment_supabase(
                                                                            db_filename,
                                                                            {
                                                                                "order_id": _order_id_pay,
                                                                                "payment_date": _pay_edit_date_str,
                                                                                "amount": float(new_amount),
                                                                                "payment_method": new_method,
                                                                                "card_company": new_card_company,
                                                                                "fee_amount": float(new_fee),
                                                                                "created_by": _current_username(),
                                                                            },
                                                                            _error_detail=_new_err,
                                                                        )
                                                                        if _new_pay_id is None:
                                                                            if _new_err:
                                                                                st.error(
                                                                                    "새 결제(+) 전표 저장에 실패했습니다. 상세: "
                                                                                    + _new_err[0]
                                                                                    + " (상계 전표는 이미 반영되었을 수 있습니다.)"
                                                                                )
                                                                            else:
                                                                                st.error(
                                                                                    "새 결제(+) 전표 저장에 실패했습니다. "
                                                                                    "상계 전표는 이미 반영되었을 수 있으니 관리자에게 문의해 주세요."
                                                                                )
                                                                        else:
                                                                            action = "결제변경"
                                                                            new_payment = {
                                                                                "payment_id": "신규생성(상계처리)",
                                                                                "amount": float(new_amount),
                                                                                "method": new_method,
                                                                                "card_company": new_card_company,
                                                                            }
                                                                            _pay_edit_committed = True

                                                                    if _pay_edit_committed:
                                                                        _recalc_order_actual_margin_supabase(db_filename, _order_id_pay)
                                                                        new_paid_total, _ = _sum_payments_by_order_supabase(db_filename, _order_id_pay)
                                                                        new_balance = (old_balance + float(prow["amount"]) - float(new_amount)) if new_amount > 0 else old_balance + float(prow["amount"])
                                                                        cid_ph = _get_order_customer_id_supabase(db_filename, _order_id_pay)
                                                                        customer_name_ph = _get_customer_name_supabase(db_filename, cid_ph) if cid_ph else ""
                                                                        old_data = {"order_id": int(_order_id_pay), "paid_total_before": old_paid_total, "balance_before": old_balance, "payment": old_payment}
                                                                        new_data = {"order_id": int(_order_id_pay), "paid_total_after": new_paid_total, "balance_after": new_balance, "payment": new_payment}
                                                                        _ph_err = _insert_payment_history(
                                                                            None, _order_id_pay, customer_name_ph, action,
                                                                            old_data, new_data, del_reason,
                                                                            receipt_image_path=receipt_path_saved,
                                                                            db_filename=db_filename,
                                                                        )
                                                                        if _ph_err:
                                                                            st.warning(f"⚠️ 이력 저장 오류: {_ph_err}")
                                                                        conn = get_tenant_conn(db_filename)
                                                                        if conn:
                                                                            try:
                                                                                _insert_audit_log(conn, "Order", _order_id_pay, "payment_total", old_paid_total, new_paid_total, del_reason)
                                                                                _insert_audit_log(conn, "Order", _order_id_pay, "balance_amount", old_balance, new_balance, del_reason)
                                                                                conn.commit()
                                                                            finally:
                                                                                conn.close()
                                                                else:
                                                                    conn = get_tenant_conn(db_filename)
                                                                    cur = conn.execute("SELECT COALESCE(SUM(amount),0) FROM Payments WHERE order_id = ?", (_order_id_pay,))
                                                                    old_paid_total = cur.fetchone()[0] or 0
    
                                                                    # 1. 마이너스(-) 상계 전표 INSERT
                                                                    conn.execute(
                                                                        "INSERT INTO Payments (order_id, payment_date, amount, payment_method, card_company, fee_amount) VALUES (?, ?, ?, ?, ?, ?)",
                                                                        (_order_id_pay, _pay_edit_date_str, -old_amt_val, prow["payment_method"], prow["card_company"], -old_fee_val)
                                                                    )
    
                                                                    if new_amount == 0:
                                                                        action = "결제취소"
                                                                        new_payment = {}
                                                                    else:
                                                                        # 2. 새로운 결제(+) 전표 INSERT
                                                                        conn.execute(
                                                                            "INSERT INTO Payments (order_id, payment_date, amount, payment_method, card_company, fee_amount) VALUES (?, ?, ?, ?, ?, ?)",
                                                                            (_order_id_pay, _pay_edit_date_str, float(new_amount), new_method, new_card_company, float(new_fee))
                                                                        )
                                                                        action = "결제변경"
                                                                        new_payment = {
                                                                            "payment_id": "신규생성(상계처리)",
                                                                            "amount": float(new_amount),
                                                                            "method": new_method,
                                                                            "card_company": new_card_company,
                                                                        }
                                                                    _recalc_order_actual_margin(conn, _order_id_pay, db_filename)
                                                                    cur2 = conn.execute("SELECT COALESCE(SUM(amount),0) FROM Payments WHERE order_id = ?", (_order_id_pay,))
                                                                    new_paid_total = cur2.fetchone()[0] or 0
                                                                    new_balance = (old_balance + float(prow["amount"]) - float(new_amount)) if new_amount > 0 else old_balance + float(prow["amount"])
                                                                    _insert_audit_log(conn, "Order", _order_id_pay, "payment_total", old_paid_total, new_paid_total, del_reason)
                                                                    _insert_audit_log(conn, "Order", _order_id_pay, "balance_amount", old_balance, new_balance, del_reason)
                                                                    cur_cid = conn.execute("SELECT customer_id FROM Orders WHERE id = ?", (_order_id_pay,)).fetchone()
                                                                    cid_ph = cur_cid[0] if cur_cid else None
                                                                    customer_name_ph = _get_customer_name_supabase(db_filename, cid_ph) if cid_ph else ""
                                                                    old_data = {"order_id": int(_order_id_pay), "paid_total_before": old_paid_total, "balance_before": old_balance, "payment": old_payment}
                                                                    new_data = {"order_id": int(_order_id_pay), "paid_total_after": new_paid_total, "balance_after": new_balance, "payment": new_payment}
                                                                    _insert_payment_history(conn, _order_id_pay, customer_name_ph, action, old_data, new_data, del_reason, receipt_image_path=receipt_path_saved, db_filename=db_filename)
                                                                    conn.commit()
                                                                    conn.close()
                                                                    _pay_edit_committed = True
                                                                if _pay_edit_committed:
                                                                    st.success("변경이 완료되었습니다.")
                                                                    # ── 부정행위 탐지: 결제 취소 시 관리자 경보 (코어 로직 무관) ──
                                                                    try:
                                                                        if action == "결제취소":
                                                                            _check_and_send_fraud_signals(
                                                                                db_filename=db_filename,
                                                                                order_id=int(_order_id_pay),
                                                                                actor_username=_current_username(),
                                                                                reason=del_reason,
                                                                                action_type="payment_cancel",
                                                                            )
                                                                    except Exception:
                                                                        pass
                                                                    clear_data_cache()
                                                                    st.rerun()

                                                # ── 잘못 입력 직접 삭제 (2열 밖 전체 너비 — 오른쪽 컬럼에 가려지지 않도록) ──
                                                if float(prow.get("amount") or 0) >= 0:
                                                    st.divider()
                                                    st.markdown("##### 🗑️ 잘못 입력한 결제 직접 삭제")
                                                    st.caption("중복 입력 등 실수로 잘못 입력된 결제를 상계 전표 없이 완전 삭제합니다.")
                                                    _dd_confirm_key = f"pay_direct_del_confirm_{prow['id']}"
                                                    st.checkbox(
                                                        f"결제 ID {int(prow['id'])} ({prow.get('payment_method', '-')} / {float(prow.get('amount') or 0):,.0f}원) 완전 삭제 동의",
                                                        key=_dd_confirm_key,
                                                    )
                                                    if st.button("🗑️ 삭제 실행", key=f"pay_direct_del_btn_{prow['id']}", type="secondary"):
                                                        if not st.session_state.get(_dd_confirm_key):
                                                            st.warning("삭제하려면 확인 체크박스를 먼저 선택해 주세요.")
                                                        else:
                                                            try:
                                                                _dd_amt = float(prow.get("amount") or 0)
                                                                _dd_pid = int(prow["id"])
                                                                _dd_paid_before, _dd_paid_after = 0.0, 0.0
                                                                _dd_cname = ""
                                                                if _supabase_orders_payments_available():
                                                                    _dd_paid_before, _ = _sum_payments_by_order_supabase(db_filename, _order_id_pay)
                                                                    _dd_cid = _get_order_customer_id_supabase(db_filename, _order_id_pay)
                                                                    _dd_cname = _get_customer_name_supabase(db_filename, _dd_cid) if _dd_cid else ""
                                                                    _dd_ok = _delete_payment_supabase(db_filename, _dd_pid)
                                                                else:
                                                                    _dd_conn = get_tenant_conn(db_filename)
                                                                    try:
                                                                        _dd_conn.execute("DELETE FROM Payments WHERE id = ?", (_dd_pid,))
                                                                        _dd_conn.commit()
                                                                        _dd_ok = True
                                                                    except Exception:
                                                                        _dd_ok = False
                                                                    finally:
                                                                        _dd_conn.close()
                                                                if _dd_ok:
                                                                    if _supabase_orders_payments_available():
                                                                        _dd_paid_after, _ = _sum_payments_by_order_supabase(db_filename, _order_id_pay)
                                                                        _recalc_order_actual_margin_supabase(db_filename, _order_id_pay)
                                                                    _dd_old_bal = float(orders[orders["id"] == _order_id_pay]["balance"].iloc[0]) if not orders[orders["id"] == _order_id_pay].empty else 0.0
                                                                    _dd_new_bal = _dd_old_bal + _dd_amt
                                                                    _insert_payment_history(
                                                                        None, _order_id_pay, _dd_cname, "결제직접삭제",
                                                                        {"order_id": int(_order_id_pay), "paid_total_before": _dd_paid_before, "payment": {"payment_id": _dd_pid, "amount": _dd_amt, "method": prow.get("payment_method")}},
                                                                        {"order_id": int(_order_id_pay), "paid_total_after": _dd_paid_after, "balance_after": _dd_new_bal},
                                                                        "잘못 입력 직접 삭제",
                                                                        db_filename=db_filename,
                                                                    )
                                                                    clear_data_cache()
                                                                    st.success(f"✅ 결제 ID {_dd_pid} 삭제 완료")
                                                                    st.rerun()
                                                                else:
                                                                    st.error("삭제 실패. 잠시 후 다시 시도해 주세요.")
                                                            except Exception as _dd_e:
                                                                st.error(f"삭제 오류: {_dd_e}")

                    st.subheader("잔금 추가 결제")
                    st.caption("⚠️ 초과 결제(결제액 > 구매액)도 입력 가능합니다. 초과 건은 '초과결제 항목' 탭에 자동 표시됩니다.")
                    orders_with_balance = orders[orders["balance"] > 0]
                    # 초과 결제 입력용: balance ≤ 0인 주문도 선택적으로 결제 추가 가능
                    orders_overpaid = orders[orders["balance"] <= 0]
                    if len(orders_with_balance) == 0 and len(orders_overpaid) == 0:
                        st.info("등록된 주문이 없습니다.")
                    else:
                        # 복수 주문 분배 결제 UI (잔금 있는 주문 2건 이상일 때)
                        if len(orders_with_balance) >= 2:
                            with st.expander("💳 복수 주문 분배 결제 (한 번의 결제로 여러 주문에 배분)", expanded=False):
                                _multi_order_split_payment_ui(db_filename, orders_with_balance.reset_index(drop=True), key_prefix=f"split_{selected_cid}")

                        st.markdown("---")
                        st.caption("개별 주문 단건 결제")
                        for _, orow in orders_with_balance.iterrows():
                            oid = orow["id"]
                            bal = float(orow["balance"] or 0)
                            dlv = orow.get("delivery_date", "")
                            dlv_str = dlv.strftime("%Y-%m-%d") if hasattr(dlv, "strftime") else str(dlv) if dlv else "-"
                            with st.expander(f"주문 #{oid} | 배송일 {dlv_str} | 잔금 {bal:,.0f}원", expanded=False):
                                _customer_balance_payment_ui(db_filename, oid, bal, key_prefix=f"gen_pay_{oid}")

                        # 완납/초과 주문에 추가 결제 입력 허용 (결변·취소·위약금 처리용)
                        if len(orders_overpaid) > 0:
                            with st.expander(f"🔄 완납·초과 주문 추가 결제 입력 ({len(orders_overpaid)}건) — 결변·취소 처리용", expanded=False):
                                st.warning("아래 주문은 이미 완납 또는 초과 결제된 상태입니다. 결제 수단 변경(결변)이나 카드 취소 처리 목적으로만 사용하세요. 초과 금액은 '초과결제 항목' 탭에 자동 표시됩니다.")
                                for _, orow in orders_overpaid.iterrows():
                                    oid = orow["id"]
                                    bal = float(orow["balance"] or 0)
                                    dlv = orow.get("delivery_date", "")
                                    dlv_str = dlv.strftime("%Y-%m-%d") if hasattr(dlv, "strftime") else str(dlv) if dlv else "-"
                                    bal_label = f"초과 {abs(bal):,.0f}원" if bal < 0 else "완납"
                                    with st.expander(f"주문 #{oid} | 배송일 {dlv_str} | {bal_label}", expanded=False):
                                        _customer_balance_payment_ui(db_filename, oid, 0, key_prefix=f"gen_pay_over_{oid}")

                    # ── 주문 삭제 요청 (직원 → 관리자 승인) ──
                    with st.expander("🗑️ 주문 삭제 요청 (관리자 승인 필요)", expanded=False):
                        st.caption("삭제가 필요한 주문을 선택하고 사유를 입력하면, 매장 관리자에게 승인 요청이 전송됩니다. 관리자 승인 후 실제 삭제가 이루어집니다.")
                        def _fmt_del_order(oid):
                            r = orders[orders["id"] == oid]
                            if r.empty:
                                return f"주문 #{oid}"
                            row = r.iloc[0]
                            od = row.get("order_date", "")
                            od_str = od.strftime("%Y-%m-%d") if hasattr(od, "strftime") else str(od or "-")[:10]
                            amt = int(row.get("total_amount") or 0)
                            return f"주문 #{oid} | 계약일 {od_str} | 금액 {amt:,}원"
                        del_oid = st.selectbox(
                            "삭제 요청할 주문",
                            orders["id"].tolist(),
                            format_func=_fmt_del_order,
                            key=f"del_req_oid_{cid}",
                        )
                        del_reason = st.text_area(
                            "삭제 사유 *",
                            key=f"del_req_reason_{cid}",
                            placeholder="예) 중복 입력, 고객 취소, 계약 철회 등 구체적인 사유를 입력해 주세요.",
                            max_chars=300,
                        )
                        if st.button("📨 삭제 요청 전송", key=f"del_req_btn_{cid}", type="primary"):
                            if not del_reason or not del_reason.strip():
                                st.error("삭제 사유를 입력해 주세요.")
                            elif not _supabase_orders_payments_available():
                                st.error("Supabase 환경에서만 삭제 요청을 사용할 수 있습니다.")
                            else:
                                _req_by = _current_username()
                                ok, req_err = _insert_delete_request(db_filename, int(del_oid), del_reason.strip(), _req_by)
                                if ok:
                                    st.success(f"주문 #{del_oid} 삭제 요청이 관리자에게 전송되었습니다. 승인 후 삭제됩니다.")
                                else:
                                    st.error(f"요청 전송 실패: {req_err}")

    # ---------- 탭 2·3·4 공통 데이터: 1회만 로드 후 각 탭에서 재사용 ----------
    order_cols_d10 = "id, customer_id, order_date, delivery_date, total_amount, cost_price, category, employee_names"
    _shared_orders_d10 = load_orders_cached(db_filename, order_cols_d10, limit=None)
    _shared_payments_d10 = load_payments_cached(db_filename)
    # 탭2·3·4는 id, name, phone1만 사용 → 불필요 컬럼 제외
    _shared_customers_d10 = load_customers_cached(db_filename, limit=None, col_list="id, name, phone1")
    # pay_sum: 탭2·3·4 공통으로 1회만 계산
    _shared_pay_sum_d10 = (
        _shared_payments_d10.groupby("order_id")["amount"].sum()
        if not _shared_payments_d10.empty and "order_id" in _shared_payments_d10.columns
        else pd.Series(dtype=float)
    )

    # ---------- 탭 2: 다가오는 미수금 (D-10 이내) ----------
    with tab_d10:
        st.subheader("다가오는 미수금 (배송일 0~10일 이내)")
        orders = _shared_orders_d10
        payments = _shared_payments_d10
        customers = _shared_customers_d10
        if orders.empty or "id" not in orders.columns:
            st.info("아직 등록된 주문 데이터가 없습니다.")
        else:
            pay_sum = _shared_pay_sum_d10
            orders["paid"] = orders["id"].map(pay_sum).fillna(0)
            orders["balance"] = orders["total_amount"] - orders["paid"]
            orders["delivery_date"] = pd.to_datetime(orders["delivery_date"], errors="coerce")
            orders = orders.merge(customers, left_on="customer_id", right_on="id", suffixes=("", "_c"))
            d10_end = today + timedelta(days=10)
            include_over_10 = st.checkbox("10일보다 더 많이 남은 고객도 검색에 포함", key="d10_include_over")
            if include_over_10:
                mask_date = orders["delivery_date"].notna()
            else:
                mask_date = orders["delivery_date"].notna() & (orders["delivery_date"].dt.date >= today) & (orders["delivery_date"].dt.date <= d10_end)
            mask_balance = orders["balance"] > 0
            list_d10 = orders[mask_balance & mask_date].copy()
            if len(list_d10) > 0:
                list_d10["배송일"] = list_d10["delivery_date"].dt.strftime("%Y-%m-%d") if pd.api.types.is_datetime64_any_dtype(list_d10["delivery_date"]) else list_d10["delivery_date"].astype(str)
                df_d10 = list_d10[["name", "phone1", "배송일", "category", "employee_names", "balance"]].rename(columns={"name": "고객명", "phone1": "전화번호", "category": "품목", "employee_names": "담당자", "balance": "잔금"})
                st.dataframe(_format_df_display(df_d10, ["잔금"]), width='stretch')
                for _, row in list_d10.iterrows():
                    with st.expander(f"💰 {row['name']} — 잔금 {row['balance']:,.0f}원"):
                        _customer_balance_payment_ui(db_filename, row["id"], row["balance"], key_prefix=f"d10_{row['id']}")
            else:
                st.info("해당 조건의 미수금 고객이 없습니다.")

    # ---------- 탭 3: 🚨 경고! 미결 금액 (배송일 지남 + 미수금) ----------
    with tab_overdue:
        st.error("🚨 배송일이 이미 지났는데 잔금이 남아 있는 고객 목록입니다. 우선 완납 유도가 필요합니다.")
        orders = _shared_orders_d10
        payments = _shared_payments_d10
        customers = _shared_customers_d10
        if orders.empty or "id" not in orders.columns:
            st.info("아직 등록된 주문 데이터가 없습니다.")
        else:
            pay_sum = _shared_pay_sum_d10  # 탭2에서 이미 계산된 pay_sum 재사용
            orders["paid"] = orders["id"].map(pay_sum).fillna(0)
            orders["balance"] = orders["total_amount"] - orders["paid"]
            orders["delivery_date"] = pd.to_datetime(orders["delivery_date"], errors="coerce")
            orders = orders.merge(customers, left_on="customer_id", right_on="id", suffixes=("", "_c"))
            mask_past = orders["delivery_date"].notna() & (orders["delivery_date"].dt.date < today)
            mask_balance = orders["balance"] > 0
            list_overdue = orders[mask_balance & mask_past].copy()
            if len(list_overdue) > 0:
                list_overdue["배송일"] = list_overdue["delivery_date"].dt.strftime("%Y-%m-%d")
                df_over = list_overdue[["name", "phone1", "배송일", "category", "employee_names", "balance"]].rename(columns={"name": "고객명", "phone1": "전화번호", "category": "품목", "employee_names": "담당자", "balance": "잔금"})
                st.dataframe(_format_df_display(df_over, ["잔금"]), width='stretch')
                for _, row in list_overdue.iterrows():
                    with st.expander(f"🚨 {row['name']} — 잔금 {row['balance']:,.0f}원 (배송일 지남)"):
                        _customer_balance_payment_ui(db_filename, row["id"], row["balance"], key_prefix=f"over_{row['id']}")
            else:
                st.success("✅ 배송일 지난 미수금 고객이 없습니다.")

    # ---------- 탭 4: 🔴 초과결제 항목 ----------
    with tab_anomaly:
        st.subheader("🔴 초과결제 항목 (결제금액 > 구매금액)")
        st.caption("결제 금액이 구매 금액을 초과한 건입니다. 결제 수단 변경(결변) 또는 카드 취소 처리로 정리해 주세요.")
        if role not in ("store_admin", "superadmin"):
            st.info("매장 관리자 또는 최고 관리자만 조회할 수 있습니다.")
        else:
            _a_orders = _shared_orders_d10
            _a_payments = _shared_payments_d10
            _a_customers = _shared_customers_d10
            if _a_orders.empty or "id" not in _a_orders.columns:
                st.info("주문 데이터가 없습니다.")
            else:
                _a_pay_sum = _a_payments.groupby("order_id")["amount"].sum() if not _a_payments.empty and "order_id" in _a_payments.columns else pd.Series(dtype=float)
                _a_orders = _a_orders.copy()
                _a_orders["paid"] = _a_orders["id"].map(_a_pay_sum).fillna(0)
                _a_orders["balance"] = _a_orders["total_amount"] - _a_orders["paid"]
                _a_orders["delivery_date"] = pd.to_datetime(_a_orders["delivery_date"], errors="coerce")
                _a_orders = _a_orders.merge(_a_customers[["id", "name", "phone1"]], left_on="customer_id", right_on="id", suffixes=("", "_c"), how="left")

                _overpaid = _a_orders[_a_orders["balance"] < 0].copy()
                if _overpaid.empty:
                    st.success("✅ 초과결제 항목이 없습니다.")
                else:
                    _overpaid["초과금액"] = (_overpaid["balance"] * -1)
                    _overpaid_disp = _overpaid.copy()
                    _overpaid_disp["배송일"] = _overpaid_disp["delivery_date"].dt.strftime("%Y-%m-%d").where(_overpaid_disp["delivery_date"].notna(), "-")
                    _overpaid_disp = _overpaid_disp[["name", "phone1", "배송일", "category", "employee_names", "total_amount", "paid", "초과금액"]].rename(columns={
                        "name": "고객명", "phone1": "전화번호", "category": "품목",
                        "employee_names": "담당자", "total_amount": "구매금액", "paid": "결제금액"
                    })
                    st.error(f"⛔ 총 {len(_overpaid_disp)}건, 초과결제 합계 {int(_overpaid_disp['초과금액'].sum()):,}원 — 즉시 확인 필요!")
                    st.dataframe(_format_df_display(_overpaid_disp, ["구매금액", "결제금액", "초과금액"]), width='stretch')
                    st.markdown("---")
                    for _, _op_row in _overpaid.iterrows():
                        _op_oid = _op_row["id"]
                        _op_name = str(_op_row.get("name") or "-")
                        _op_phone = str(_op_row.get("phone1") or "-")
                        _op_excess = float(_op_row.get("초과금액", 0))
                        _op_total = float(_op_row.get("total_amount") or 0)
                        _op_balance = float(_op_row.get("balance") or 0)
                        _op_dlv = _op_row.get("delivery_date", "")
                        _op_dlv_str = _op_dlv.strftime("%Y-%m-%d") if hasattr(_op_dlv, "strftime") else str(_op_dlv or "-")[:10]
                        with st.expander(f"🔴 {_op_name} ({_op_phone}) — 주문 #{_op_oid} | 배송일 {_op_dlv_str} | 초과 {_op_excess:,.0f}원", expanded=False):
                            if not _a_payments.empty and "order_id" in _a_payments.columns:
                                _op_pays = _a_payments[_a_payments["order_id"] == _op_oid].copy()
                            else:
                                _op_pays = pd.DataFrame()
                            if _op_pays.empty:
                                st.info("이 주문의 결제 내역이 없습니다.")
                            else:
                                # ── 결제 내역 요약 테이블 ──
                                _pay_detail_cols = []
                                _pay_col_rename = {}
                                for _c, _label in [
                                    ("id", "결제ID"),
                                    ("payment_date", "결제일"),
                                    ("payment_method", "결제수단"),
                                    ("card_company", "카드사/승인번호"),
                                    ("onnuri_approval_code", "온누리승인번호"),
                                    ("amount", "금액"),
                                    ("fee_amount", "수수료"),
                                    ("created_by", "등록자"),
                                ]:
                                    if _c in _op_pays.columns:
                                        _pay_detail_cols.append(_c)
                                        _pay_col_rename[_c] = _label
                                _op_pays_disp = _op_pays[_pay_detail_cols].rename(columns=_pay_col_rename).copy()
                                _fmt_money_cols = [_pay_col_rename.get(c) for c in ("amount", "fee_amount") if _pay_col_rename.get(c) in _op_pays_disp.columns]
                                st.caption("📋 현재 결제 내역 — 취소/감액할 건을 아래 수정 패널에서 선택하세요")
                                st.dataframe(_format_df_display(_op_pays_disp, _fmt_money_cols), width='stretch')
                                st.markdown("---")
                                # ── 각 결제건 수정 패널 (탭1과 동일 방식) ──
                                for _, prow in _op_pays.iterrows():
                                    _prow_amt = float(prow["amount"] or 0)
                                    _prow_method = prow.get("payment_method") or "-"
                                    with st.expander(f"✏️ 결제 ID {prow['id']} — {_prow_method} {_prow_amt:,.0f}원 수정/취소", expanded=False):
                                        col_left, col_right = st.columns(2)
                                        with col_left:
                                            st.info("**기존 결제 내역 (비교용)**")
                                            st.write(f"**총 구매금액:** {_op_total:,.0f}원")
                                            st.write(f"**결제수단:** {_prow_method}")
                                            st.write(f"**결제금액:** {_prow_amt:,.0f}원")
                                            st.write(f"**초과금액:** {_op_excess:,.0f}원")
                                            if "card_company" in prow and prow["card_company"] and str(prow["card_company"]) not in ("None", "nan", ""):
                                                st.write(f"**카드사/승인번호:** {prow['card_company']}")
                                            if "onnuri_approval_code" in prow and prow["onnuri_approval_code"] and str(prow["onnuri_approval_code"]) not in ("None", "nan", ""):
                                                st.write(f"**온누리승인번호:** {prow['onnuri_approval_code']}")
                                        with col_right:
                                            if _prow_amt < 0:
                                                st.warning(
                                                    "⚠️ 자동 생성된 마이너스 상계 전표는 수정할 수 없습니다. 필요 시 아래에서 삭제할 수 있습니다."
                                                )
                                                st.divider()
                                                st.markdown("##### 🗑️ 마이너스(상계) 전표 삭제")
                                                st.caption(
                                                    "상계 전표를 삭제하면 결제 합계에서 해당 상계분이 빠져 잔금·마진에 반영됩니다. "
                                                    "원 양수 결제와의 짝이 맞는지 확인한 뒤 삭제하세요."
                                                )
                                                _op_neg_confirm = f"op_neg_del_confirm_{prow['id']}"
                                                st.checkbox(
                                                    f"결제 ID {int(prow['id'])} (상계 {_prow_amt:,.0f}원) 삭제 동의",
                                                    key=_op_neg_confirm,
                                                )
                                                if st.button(
                                                    "🗑️ 상계 전표 삭제 실행",
                                                    key=f"op_neg_del_btn_{prow['id']}",
                                                    type="secondary",
                                                ):
                                                    if not st.session_state.get(_op_neg_confirm):
                                                        st.warning("삭제하려면 확인 체크박스를 먼저 선택해 주세요.")
                                                    else:
                                                        try:
                                                            _op_neg_pid = int(prow["id"])
                                                            _op_neg_paid_before, _op_neg_paid_after = 0.0, 0.0
                                                            _op_neg_cname = ""
                                                            if _supabase_orders_payments_available():
                                                                _op_neg_paid_before, _ = _sum_payments_by_order_supabase(db_filename, _op_oid)
                                                                _op_neg_cid = _get_order_customer_id_supabase(db_filename, _op_oid)
                                                                _op_neg_cname = _get_customer_name_supabase(db_filename, _op_neg_cid) if _op_neg_cid else ""
                                                                _op_neg_ok = _delete_payment_supabase(db_filename, _op_neg_pid)
                                                            else:
                                                                _op_neg_conn = get_tenant_conn(db_filename)
                                                                try:
                                                                    _op_neg_conn.execute("DELETE FROM Payments WHERE id = ?", (_op_neg_pid,))
                                                                    _op_neg_conn.commit()
                                                                    _op_neg_ok = True
                                                                except Exception:
                                                                    _op_neg_ok = False
                                                                finally:
                                                                    _op_neg_conn.close()
                                                            if _op_neg_ok:
                                                                if _supabase_orders_payments_available():
                                                                    _op_neg_paid_after, _ = _sum_payments_by_order_supabase(db_filename, _op_oid)
                                                                    _recalc_order_actual_margin_supabase(db_filename, _op_oid)
                                                                _op_neg_new_bal = _op_balance + _prow_amt
                                                                _insert_payment_history(
                                                                    None,
                                                                    _op_oid,
                                                                    _op_neg_cname,
                                                                    "결제직접삭제",
                                                                    {
                                                                        "order_id": int(_op_oid),
                                                                        "paid_total_before": _op_neg_paid_before,
                                                                        "payment": {
                                                                            "payment_id": _op_neg_pid,
                                                                            "amount": _prow_amt,
                                                                            "method": _prow_method,
                                                                        },
                                                                    },
                                                                    {
                                                                        "order_id": int(_op_oid),
                                                                        "paid_total_after": _op_neg_paid_after,
                                                                        "balance_after": _op_neg_new_bal,
                                                                    },
                                                                    "마이너스(상계) 전표 삭제",
                                                                    db_filename=db_filename,
                                                                )
                                                                clear_data_cache()
                                                                st.success(f"✅ 상계 전표(결제 ID {_op_neg_pid}) 삭제 완료")
                                                                st.rerun()
                                                            else:
                                                                st.error("삭제 실패. 잠시 후 다시 시도해 주세요.")
                                                        except Exception as _op_neg_e:
                                                            st.error(f"삭제 오류: {_op_neg_e}")
                                            else:
                                                _cur_method_op = prow.get("payment_method") or PAYMENT_METHOD_OPTIONS[0]
                                                _method_idx_op = PAYMENT_METHOD_OPTIONS.index(_cur_method_op) if _cur_method_op in PAYMENT_METHOD_OPTIONS else 0
                                                new_method_op = st.selectbox(
                                                    "결제 수단 변경",
                                                    options=PAYMENT_METHOD_OPTIONS,
                                                    index=_method_idx_op,
                                                    key=f"op_edit_method_{prow['id']}",
                                                )
                                                if new_method_op in _CARD_WITH_COMPANY:
                                                    _cur_card_op = prow.get("card_company") or CARD_COMPANY_OPTIONS[0]
                                                    _card_idx_op = CARD_COMPANY_OPTIONS.index(_cur_card_op) if _cur_card_op in CARD_COMPANY_OPTIONS else 0
                                                    new_card_op = st.selectbox("카드사", options=CARD_COMPANY_OPTIONS, index=_card_idx_op, key=f"op_edit_card_{prow['id']}")
                                                elif new_method_op == "메인페이":
                                                    new_card_op = st.text_input("메인페이 승인번호 4자리", value=prow.get("card_company") or "", max_chars=4, key=f"op_edit_card_{prow['id']}")
                                                elif new_method_op == "지역화폐":
                                                    new_card_op = st.text_input("지역화폐 승인번호", value=prow.get("card_company") or "", key=f"op_edit_card_{prow['id']}")
                                                else:
                                                    new_card_op = None
                                                    st.empty()
                                                _op_edit_date_key = f"op_edit_date_{prow['id']}"
                                                try:
                                                    _op_cur_date_val = pd.to_datetime(prow.get("payment_date")).date() if prow.get("payment_date") else _today_kst()
                                                except Exception:
                                                    _op_cur_date_val = _today_kst()
                                                if _op_edit_date_key not in st.session_state:
                                                    st.session_state[_op_edit_date_key] = _op_cur_date_val
                                                new_pay_date_op = st.date_input("결제 날짜 *", key=_op_edit_date_key)
                                                _old_amt_op_for_mode = int(_prow_amt)
                                                _op_mode_key = f"op_edit_mode_{prow['id']}"
                                                _op_mode_prev_key = f"op_edit_mode_prev_{prow['id']}"
                                                _op_mode_cur = st.radio(
                                                    "입력 방식",
                                                    ["새 금액 입력", "증감액 입력 (+증액, -감액)"],
                                                    key=_op_mode_key,
                                                    horizontal=True,
                                                    help="새 금액: 변경 후 최종 금액 전체 입력. 증감액: 변경분만 입력 (예: -1,000,000)",
                                                )
                                                _is_op_delta_mode = (_op_mode_cur == "증감액 입력 (+증액, -감액)")
                                                _op_amt_key = f"op_edit_amt_{prow['id']}"
                                                if st.session_state.get(_op_mode_prev_key) != _op_mode_cur:
                                                    st.session_state[_op_amt_key] = "0" if _is_op_delta_mode else _format_number_comma(str(_old_amt_op_for_mode))
                                                    st.session_state[_op_mode_prev_key] = _op_mode_cur
                                                elif _op_amt_key not in st.session_state:
                                                    st.session_state[_op_amt_key] = _format_number_comma(str(_old_amt_op_for_mode))
                                                if _is_op_delta_mode:
                                                    def _fmt_op_delta(_k=_op_amt_key):
                                                        st.session_state[_k] = _format_signed_number_comma(st.session_state.get(_k, ""))
                                                    st.text_input(
                                                        "증감액 (예: -1,000,000 감액 / +500,000 증액 / 0 전액취소)",
                                                        key=_op_amt_key,
                                                        on_change=_fmt_op_delta,
                                                    )
                                                    _op_delta_raw = str(st.session_state.get(_op_amt_key, "0")).strip()
                                                    _op_delta_sign = -1 if _op_delta_raw.startswith("-") else 1
                                                    _op_delta_abs = int(re.sub(r"\D", "", _op_delta_raw) or "0")
                                                    _op_delta_val = _op_delta_sign * _op_delta_abs
                                                    new_amount_op = max(0, _old_amt_op_for_mode + _op_delta_val)
                                                    st.caption(f"현재: **{_old_amt_op_for_mode:,}원** → 변경 후: **{new_amount_op:,}원**")
                                                else:
                                                    def _fmt_op_amt(_k=_op_amt_key):
                                                        st.session_state[_k] = _format_number_comma(st.session_state.get(_k, ""))
                                                    st.text_input("변경할 새 금액 (0이면 결제 취소)", key=_op_amt_key, on_change=_fmt_op_amt)
                                                    new_amount_op = _parse_comma_to_int(st.session_state.get(_op_amt_key, "0"))
                                                    st.caption(f"현재: **{_old_amt_op_for_mode:,}원** → 변경 후: **{new_amount_op:,}원**")
                                                del_reason_op = st.text_input("결제 변경 사유 (필수, 5자 이상)", key=f"op_del_reason_{prow['id']}", placeholder="예: 온누리 취소 후 카드 결제")
                                                if st.button("수정 완료", key=f"op_pay_edit_{prow['id']}", type="primary"):
                                                    if not del_reason_op or len(del_reason_op.strip()) < 5:
                                                        st.warning("사유를 5자 이상 입력하세요.")
                                                    else:
                                                        _old_amt_op = _prow_amt
                                                        _old_fee_op = float(prow.get("fee_amount") or 0)
                                                        _new_fee_op = _payment_fee_amount(new_method_op, new_amount_op) if new_amount_op > 0 else 0.0
                                                        _pay_op_date_str = (
                                                            new_pay_date_op.isoformat()
                                                            if isinstance(new_pay_date_op, date)
                                                            else _today_kst().isoformat()
                                                        )
                                                        _old_payment_op = {"payment_id": int(prow["id"]), "amount": _old_amt_op, "method": _prow_method, "card_company": prow.get("card_company")}
                                                        if _supabase_orders_payments_available():
                                                            _old_paid_op, _ = _sum_payments_by_order_supabase(db_filename, _op_oid)
                                                            _insert_payment_supabase(db_filename, {"order_id": _op_oid, "payment_date": _pay_op_date_str, "amount": -_old_amt_op, "payment_method": _prow_method, "card_company": prow.get("card_company"), "fee_amount": -_old_fee_op})
                                                            if new_amount_op == 0:
                                                                _action_op = "결제취소"
                                                                _new_payment_op = {}
                                                            else:
                                                                _insert_payment_supabase(db_filename, {"order_id": _op_oid, "payment_date": _pay_op_date_str, "amount": float(new_amount_op), "payment_method": new_method_op, "card_company": new_card_op, "fee_amount": float(_new_fee_op)})
                                                                _action_op = "결제변경"
                                                                _new_payment_op = {"payment_id": "신규생성(상계처리)", "amount": float(new_amount_op), "method": new_method_op, "card_company": new_card_op}
                                                            _recalc_order_actual_margin_supabase(db_filename, _op_oid)
                                                            _new_paid_op, _ = _sum_payments_by_order_supabase(db_filename, _op_oid)
                                                            _new_bal_op = (_op_balance + _old_amt_op - float(new_amount_op)) if new_amount_op > 0 else _op_balance + _old_amt_op
                                                            _cid_op = _get_order_customer_id_supabase(db_filename, _op_oid)
                                                            _cname_op = _get_customer_name_supabase(db_filename, _cid_op) if _cid_op else ""
                                                            _insert_payment_history(None, _op_oid, _cname_op, _action_op, {"order_id": int(_op_oid), "paid_total_before": _old_paid_op, "balance_before": _op_balance, "payment": _old_payment_op}, {"order_id": int(_op_oid), "paid_total_after": _new_paid_op, "balance_after": _new_bal_op, "payment": _new_payment_op}, del_reason_op, db_filename=db_filename)
                                                        else:
                                                            _conn_op = get_tenant_conn(db_filename)
                                                            if _conn_op:
                                                                try:
                                                                    _old_paid_op = _conn_op.execute("SELECT COALESCE(SUM(amount),0) FROM Payments WHERE order_id = ?", (_op_oid,)).fetchone()[0] or 0
                                                                    _conn_op.execute("INSERT INTO Payments (order_id, payment_date, amount, payment_method, card_company, fee_amount) VALUES (?, ?, ?, ?, ?, ?)", (_op_oid, _pay_op_date_str, -_old_amt_op, _prow_method, prow.get("card_company"), -_old_fee_op))
                                                                    if new_amount_op == 0:
                                                                        _action_op = "결제취소"
                                                                        _new_payment_op = {}
                                                                    else:
                                                                        _conn_op.execute("INSERT INTO Payments (order_id, payment_date, amount, payment_method, card_company, fee_amount) VALUES (?, ?, ?, ?, ?, ?)", (_op_oid, _pay_op_date_str, float(new_amount_op), new_method_op, new_card_op, float(_new_fee_op)))
                                                                        _action_op = "결제변경"
                                                                        _new_payment_op = {}
                                                                    _conn_op.commit()
                                                                finally:
                                                                    _conn_op.close()
                                                        try:
                                                            if _action_op == "결제취소":
                                                                _check_and_send_fraud_signals(db_filename=db_filename, order_id=int(_op_oid), actor_username=_current_username(), reason=del_reason_op, action_type="payment_cancel")
                                                        except Exception:
                                                            pass
                                                        st.toast(f"✅ 결제 ID {prow['id']} {_action_op} 완료", icon="✅")
                                                        clear_data_cache()
                                                        st.rerun()
                    # 알림 자동 기록 (세션당 1회)
                    _alert_key2 = f"_anomaly_alert_overpaid_{db_filename}"
                    if _alert_key2 not in st.session_state:
                        st.session_state[_alert_key2] = True
                        _sn_b = _get_store_name_by_db(db_filename)
                        _insert_admin_alert(_sn_b, "overpaid_summary", f"[{_sn_b}] 초과결제 이상 항목 {len(_overpaid_disp)}건 / 합계 {int(_overpaid_disp['초과금액'].sum()):,}원 즉시 확인 필요")


# ========== 탭 0: 경영 대시보드 (로그인 후 첫 화면) ==========

def _kpi_parse_delta_margin_from_sales_note(note: object) -> float | None:
    """sales.note에 '|__dm:<정수>'(계약 변경 시 KPI용 비례 마진 차액)가 있으면 반환. 없거나 파싱 실패 시 None."""
    if note is None:
        return None
    s = str(note).strip()
    if "|__dm:" not in s:
        return None
    try:
        tail = s.split("|__dm:", 1)[1].split("|", 1)[0].strip()
        return float(tail)
    except (ValueError, IndexError):
        return None


def _kpi_employee_totals_from_sales_slice(kpi_m: "pd.DataFrame", orders: "pd.DataFrame") -> "pd.DataFrame":
    """sales 원장 구간에서 직원별 순매출·마진·전시 배분. 1/n 분모는 주문의 최신 employee_names를 우선(sales 스냅샷보다 앞섬). amount 음수·note|__dm 동일."""
    if kpi_m.empty:
        return pd.DataFrame(columns=["employee", "revenue", "margin", "display_sales"])
    km = kpi_m.copy()
    if "employee_names" not in km.columns:
        km["employee_names"] = None
    # Arrow-backed StringDtype는 loc에 Series 대입 시 TypeError → object로 풀어서 보완 (pandas 2.2+/3.x, Streamlit Cloud)
    km["employee_names"] = km["employee_names"].astype(object)
    if not orders.empty and "id" in orders.columns and "employee_names" in orders.columns and "order_id" in km.columns:
        _oid_emp_map = orders.set_index("id")["employee_names"].to_dict()

        def _emp_from_oid(oid: object) -> str:
            if oid is None or (isinstance(oid, float) and pd.isna(oid)) or pd.isna(oid):
                return ""
            try:
                return _kpi_sanitize_employee_label(_oid_emp_map.get(int(oid)))
            except (TypeError, ValueError):
                return ""

        _from_order = km["order_id"].map(_emp_from_oid)
        _use_order = ~_from_order.map(_kpi_employee_names_cell_is_blank)
        if _use_order.any():
            km.loc[_use_order, "employee_names"] = _from_order.loc[_use_order]
    _total_map: dict = {}
    _margin_map = {}
    _display_map = {}
    if not orders.empty and "id" in orders.columns:
        if "total_amount" in orders.columns:
            _total_map = orders.set_index("id")["total_amount"].fillna(0).astype(float).to_dict()
        if "actual_margin" in orders.columns:
            _margin_map = orders.set_index("id")["actual_margin"].fillna(0).astype(float).to_dict()
        if "display_sales_amount" in orders.columns:
            _display_map = orders.set_index("id")["display_sales_amount"].fillna(0).astype(float).to_dict()
    rows_md: list = []
    for _, r in km.iterrows():
        emps = _kpi_parse_employee_list(r.get("employee_names"))
        n = len(emps) if emps else 1
        if not emps:
            continue
        amt = float(r.get("amount") or 0)
        oid = r.get("order_id")
        oid_int = int(oid) if oid is not None and pd.notna(oid) else None
        if oid_int is None:
            margin = 0.0
            display_amt = 0.0
            per_amt = amt / n
        else:
            tot = float(_total_map.get(oid_int, 0) or 0)
            base_m = float(_margin_map.get(oid_int, 0) or 0)
            base_d = float(_display_map.get(oid_int, 0) or 0)
            _dm_note = _kpi_parse_delta_margin_from_sales_note(r.get("note"))
            if tot == 0:
                margin = (_dm_note / n) if _dm_note is not None else 0.0
                display_amt = 0.0
                per_amt = amt / n
            else:
                _ratio = amt / tot
                margin = (base_m * _ratio) / n
                display_amt = (base_d * _ratio) / n
                per_amt = amt / n
        if per_amt == 0 and margin == 0 and display_amt == 0:
            continue
        for e in emps:
            rows_md.append({"employee": e, "revenue": per_amt, "margin": margin, "display_sales": display_amt})
    if not rows_md:
        return pd.DataFrame(columns=["employee", "revenue", "margin", "display_sales"])
    df_md = pd.DataFrame(rows_md)
    return df_md.groupby("employee", as_index=False).agg({"revenue": "sum", "margin": "sum", "display_sales": "sum"})


@st.fragment
def _render_kpi_section(sales_df: "pd.DataFrame", orders: "pd.DataFrame", db_filename: str):
    """월별 직원 판매 현황: 종합=매출70+마진20+전시10. 매출=sales 해당월 순액 1/n. 현금수금집계는 참고 열. 마진·전시=sales·주문 비율."""
    st.subheader("4. 월별 직원 판매 현황 및 평가")
    if not sales_df.empty and "transaction_date" in sales_df.columns:
        _kpi_sales = sales_df.copy()
        _kpi_sales["transaction_date"] = pd.to_datetime(_kpi_sales["transaction_date"], errors="coerce")
        _kpi_sales = _kpi_sales.dropna(subset=["transaction_date"])
        _kpi_dates = _kpi_sales["transaction_date"].dropna()
        if len(_kpi_dates) > 0:
            _kpi_min = _kpi_dates.min().to_pydatetime()
            _kpi_max = _kpi_dates.max().to_pydatetime()
            months_options = []
            y, m = _kpi_min.year, _kpi_min.month
            end_y, end_m = _kpi_max.year, _kpi_max.month
            while (y, m) <= (end_y, end_m):
                months_options.append((y, m))
                m += 1
                if m > 12:
                    m = 1
                    y += 1
            months_options = months_options[::-1]
            month_labels = [f"{y}년 {m}월" for y, m in months_options]
            if "kpi_month_idx" not in st.session_state:
                st.session_state["kpi_month_idx"] = 0
            sel_idx = st.selectbox("연/월 선택", range(len(month_labels)), format_func=lambda i: month_labels[i], key="kpi_month_sel")
            sel_y, sel_m = months_options[sel_idx]
            from calendar import monthrange as _mrange
            _kpi_start = date(sel_y, sel_m, 1)
            _kpi_end = date(sel_y, sel_m, _mrange(sel_y, sel_m)[1])
            _kpi_m = _kpi_sales[
                (_kpi_sales["transaction_date"].dt.date >= _kpi_start) &
                (_kpi_sales["transaction_date"].dt.date <= _kpi_end)
            ].copy()

            df_rev = _kpi_employee_totals_from_sales_slice(_kpi_m, orders)
            if df_rev.empty:
                df_rev = pd.DataFrame(columns=["employee", "revenue", "margin", "display_sales"])

            cash_df = (
                _aggregate_cash_collected_by_employee(db_filename, _kpi_start, _kpi_end, orders)
                if db_filename and not orders.empty
                else pd.DataFrame(columns=["employee", "cash_sales"])
            )
            if not cash_df.empty:
                cash_df = cash_df.rename(columns={"cash_sales": "kpi_receipt"})
            else:
                cash_df = pd.DataFrame(columns=["employee", "kpi_receipt"])

            emp_merged = df_rev.merge(cash_df, on="employee", how="outer").fillna(
                {"revenue": 0.0, "margin": 0.0, "display_sales": 0.0, "kpi_receipt": 0.0}
            )
            emp_merged = emp_merged[~emp_merged["employee"].map(_kpi_employee_names_cell_is_blank)]

            if not emp_merged.empty:
                emp_df = emp_merged.copy()
                total_revenue = emp_df["revenue"].sum() or 0
                total_margin = emp_df["margin"].sum() or 0
                total_display = emp_df["display_sales"].sum() or 0
                total_kpi_receipt = emp_df["kpi_receipt"].sum() or 0
                emp_df["매출 점수(70)"] = (emp_df["revenue"] / total_revenue * 70).round(1) if total_revenue else 0.0
                emp_df["마진 점수(15)"] = (emp_df["margin"] / total_margin * 15).round(1) if total_margin else 0.0
                emp_df["전시품 점수(5)"] = (emp_df["display_sales"] / total_display * 5).round(1) if total_display else 0.0
                emp_df["현금수금 점수(10)"] = (emp_df["kpi_receipt"] / total_kpi_receipt * 10).round(1) if total_kpi_receipt else 0.0
                emp_df["종합 점수"] = (
                    emp_df["매출 점수(70)"] + emp_df["마진 점수(15)"] + emp_df["전시품 점수(5)"] + emp_df["현금수금 점수(10)"]
                ).round(1)
                emp_df = emp_df.sort_values("종합 점수", ascending=False).reset_index(drop=True)
                emp_df["매출집계(순액)"] = emp_df["revenue"].round(0).astype(int)
                emp_df["현금수금집계"] = emp_df["kpi_receipt"].round(0).astype(int)
                emp_df["마진액"] = emp_df["margin"].round(0).astype(int)
                emp_df["전시품 판매액"] = emp_df["display_sales"].round(0).astype(int)
                display_df = emp_df[
                    [
                        "employee",
                        "매출집계(순액)",
                        "현금수금집계",
                        "마진액",
                        "전시품 판매액",
                        "매출 점수(70)",
                        "마진 점수(15)",
                        "전시품 점수(5)",
                        "현금수금 점수(10)",
                        "종합 점수",
                    ]
                ].rename(columns={"employee": "직원명"})
                display_fmt = _format_df_display(
                    display_df, ["매출집계(순액)", "현금수금집계", "마진액", "전시품 판매액"]
                )
                st.dataframe(
                    display_fmt,
                    width='stretch',
                    column_config={
                        "직원명": st.column_config.TextColumn("직원명", width="small"),
                        "매출집계(순액)": st.column_config.TextColumn("매출집계(순액)", width="medium"),
                        "현금수금집계": st.column_config.TextColumn("현금수금집계", width="medium"),
                        "마진액": st.column_config.TextColumn("마진액", width="medium"),
                        "전시품 판매액": st.column_config.TextColumn("전시품 판매액", width="small"),
                        "매출 점수(70)": st.column_config.NumberColumn("매출(70)", format="%.1f", width="small"),
                        "마진 점수(15)": st.column_config.NumberColumn("마진(15)", format="%.1f", width="small"),
                        "전시품 점수(5)": st.column_config.NumberColumn("전시품(5)", format="%.1f", width="small"),
                        "현금수금 점수(10)": st.column_config.NumberColumn("현금수금(10)", format="%.1f", width="small"),
                        "종합 점수": st.column_config.NumberColumn("종합 점수", format="%.1f", width="small"),
                    },
                )
                st.caption(
                    "※ **종합 점수** = 매출 70 + 마진 15 + 전시품 5 + 현금수금 10. **매출 점수(70)·매출집계(순액)**: 해당 월 **판매일(transaction_date)** 기준 sales 금액(감액 등 음수 포함) 1/n. "
                    "**현금수금 점수(10)·현금수금집계**: 해당 월 **결제일(payment_date)** 기준, **수수료 없는 수납**만(이체·온누리·지역화폐·현금 등). 신용·체크·**메인페이** 제외 1/n. "
                    "**마진·전시 점수**: sales 해당 월 행을 주문 total 대비 비율로 배분(음수 매출 반영). total_amount=0이면 note|__dm 마진 차액 반영."
                )
            else:
                st.info("선택한 월에 직원이 배정된 평가 데이터(매출·현금수금·마진·전시)가 없습니다.")
        else:
            st.info("판매 데이터가 없어 월별 집계를 할 수 없습니다.")
    else:
        st.info("판매 데이터가 없어 직원 평가를 할 수 없습니다.")


@st.fragment
def _render_dashboard_todos_only(db_filename: str):
    """To-Do 섹션 fragment: 등록·완료·삭제 시 이 섹션만 rerun (전체 대시보드 재로딩 없음)."""
    todos_df = _get_todos_for_display(db_filename)
    st.subheader("6. 직원 To-Do 리스트 (인수인계)")
    if st.button("🔄 서버에서 새로고침", key="todo_refresh_btn_simple"):
        _invalidate_todos_local(db_filename)
        st.rerun()
    with st.form("todo_form_simple"):
        st.text_input("작성자", value=_get_current_user_display_name(), disabled=True, key="todo_author_display_simple")
        content = st.text_area("내용", key="todo_content_simple")
        if st.form_submit_button("등록"):
            if content and content.strip():
                author = _get_current_user_display_name()
                tenant_name = _get_store_name_by_db(db_filename) or db_filename
                client, err = get_supabase_client()
                if err or not client:
                    st.error(f"⚠️ To-Do 저장 중 Supabase 연결 실패: {err}")
                else:
                    try:
                        if "supabase" not in st.session_state:
                            st.session_state["supabase"] = client
                        r = st.session_state["supabase"].table("app_todos").insert({
                            "tenant_name": tenant_name, "author": author or "", "content": content.strip(), "is_completed": False,
                        }).execute()
                        if r.data and len(r.data) > 0:
                            new_row = r.data[0]
                            created_at = new_row.get("created_at", "")
                            created_date = pd.to_datetime(created_at).strftime("%Y-%m-%d %H:%M") if created_at else str(created_at)[:16]
                            new_df_row = pd.DataFrame([{"id": new_row.get("id"), "created_date": created_date, "author": author or "", "content": content.strip(), "is_completed": False}])
                            if "_todos_local" not in st.session_state:
                                st.session_state["_todos_local"] = {}
                            if db_filename not in st.session_state["_todos_local"]:
                                st.session_state["_todos_local"][db_filename] = load_todos_cached(db_filename)
                            st.session_state["_todos_local"][db_filename] = pd.concat([new_df_row, st.session_state["_todos_local"][db_filename]], ignore_index=True)
                        st.rerun()
                    except Exception as e:
                        st.error(f"To-Do 저장 중 오류가 발생했습니다: {e}")
            else:
                st.warning("내용을 입력하세요.")
    author_display_map = _get_app_user_display_name_map()
    current_todo_author = _current_display_name_for_todo()
    if len(todos_df) > 0:
        for _, row in todos_df.iterrows():
            is_done = bool(row.get("is_completed"))
            content_preview = (row["content"] or "")[:50] + ("..." if len((row["content"] or "")) > 50 else "")
            raw_author = row.get("author") or ""
            author_display = author_display_map.get(str(raw_author).strip()) or author_display_map.get(str(raw_author).strip().lower()) or (raw_author or "—")
            with st.expander(f"{'✅ 완료' if is_done else '⬜'} {content_preview} (by {author_display})", expanded=not is_done):
                st.caption(row["created_date"])
                if is_done:
                    st.success("✅ **완료된 업무입니다.**")
                st.write(row["content"] or "")
                if not is_done and st.button("완료 처리", key=f"todo_done_s_{row['id']}"):
                    client, err = get_supabase_client()
                    if err or not client:
                        st.error("⚠️ To-Do 완료 처리 중 Supabase 연결 실패")
                    else:
                        try:
                            if "supabase" not in st.session_state:
                                st.session_state["supabase"] = client
                            st.session_state["supabase"].table("app_todos").update({"is_completed": True}).eq("id", row["id"]).execute()
                            if "_todos_local" in st.session_state and db_filename in st.session_state["_todos_local"]:
                                tdf = st.session_state["_todos_local"][db_filename]
                                mask = tdf["id"] == row["id"]
                                if mask.any():
                                    st.session_state["_todos_local"][db_filename] = tdf.copy()
                                    st.session_state["_todos_local"][db_filename].loc[mask, "is_completed"] = True
                            st.rerun()
                        except Exception as e:
                            st.error(f"To-Do 완료 처리 중 오류: {e}")
                can_delete = is_done or (raw_author or "").strip() == current_todo_author
                if can_delete and st.button("삭제", key=f"todo_delete_s_{row['id']}"):
                    client, err = get_supabase_client()
                    if err or not client:
                        st.error("⚠️ To-Do 삭제 중 Supabase 연결 실패")
                    else:
                        try:
                            if "supabase" not in st.session_state:
                                st.session_state["supabase"] = client
                            st.session_state["supabase"].table("app_todos").delete().eq("id", row["id"]).execute()
                            if "_todos_local" in st.session_state and db_filename in st.session_state["_todos_local"]:
                                tdf = st.session_state["_todos_local"][db_filename]
                                st.session_state["_todos_local"][db_filename] = tdf[tdf["id"] != row["id"]].reset_index(drop=True)
                            st.rerun()
                        except Exception as e:
                            st.error(f"To-Do 삭제 중 오류: {e}")
    else:
        st.info("등록된 To-Do가 없습니다. 위에서 새로 등록해 보세요.")


def render_dashboard():
    db_filename = st.session_state.get("current_db")
    if not db_filename:
        st.warning("매장에 로그인한 후 이용하세요.")
        return
    _render_recent_notices_section()
    st.header("경영 대시보드 및 인수인계")

    # Supabase 연결 확인
    client, err = get_supabase_client()
    if err or not client:
        st.error(f"⚠️ Supabase 연결 실패: {err}")
        return

    # 1) 주문·결제 — @st.cache_data 캐시 활용 (10분 TTL), 로딩 시 스피너로 체감 속도 개선
    order_cols_str = "id, customer_id, order_date, delivery_date, total_amount, cost_price, actual_margin, employee_names, category, display_sales_amount, display_cost_amount, balance_status"
    with st.spinner("데이터 불러오는 중..."):
        orders = load_orders_cached(db_filename, order_cols_str, limit=None)
        payments = load_payments_cached(db_filename)
    order_columns = ["id", "customer_id", "order_date", "delivery_date", "total_amount", "cost_price", "actual_margin", "employee_names", "category", "display_sales_amount", "display_cost_amount", "balance_status"]
    for c in order_columns:
        if c not in orders.columns:
            orders[c] = None

    # 2) 고객(app_customers) / 매출(sales) / To-Do
    # 대시보드는 id, name, phone1만 사용 → col_list로 불필요 컬럼 제외 (네트워크 절감)
    customers = load_customers_cached(db_filename, limit=None, col_list="id, name, phone1")
    sales_df = load_sales_cached(db_filename, limit=None)
    # ── 매장 격리 방어 필터: sales_tenant_column 미설정 시 order_id로 2차 필터 ──
    # (sales 테이블에 db_filename 컬럼이 없거나 secrets 미설정일 때 전 매장 데이터가 섞이는 것을 방지)
    if not sales_df.empty and not orders.empty and "order_id" in sales_df.columns and _sales_tenant_column() is None:
        _valid_oids = set(orders["id"].dropna().astype(int).tolist())
        sales_df = sales_df[sales_df["order_id"].isin(_valid_oids)].copy()
    if payments.empty:
        payments = pd.DataFrame(columns=["order_id", "amount", "payment_method", "onnuri_approval_code"])
    else:
        for col in ("order_id", "amount", "payment_method", "onnuri_approval_code"):
            if col not in payments.columns:
                payments[col] = "" if col in ("payment_method", "onnuri_approval_code") else 0
    todos_df = _get_todos_for_display(db_filename)
    if "display_sales_amount" not in orders.columns:
        orders["display_sales_amount"] = 0
    if "display_cost_amount" not in orders.columns:
        orders["display_cost_amount"] = 0
    orders["display_sales_amount"] = orders["display_sales_amount"].fillna(0).astype(int)
    orders["display_cost_amount"] = orders["display_cost_amount"].fillna(0).astype(int)

    today = _today_kst()
    today_str = today.strftime("%Y-%m-%d")
    month_start = today.replace(day=1)
    st.divider()

    # ---------- 1. 주요 매출 항목 (5대 핵심 KPI) ----------
    st.subheader("1. 주요 매출 항목")
    daily_receipt_total = 0.0  # 당일 payment_date 기준 수납 순액(상계·취소 반영)
    daily_receipt_methods_html = ""  # 결제수단별 HTML (이스케이프 포함)
    month_cumulative_methods_html = ""  # 당월 누적 수납 — 결제수단별 HTML
    cumulative_sales = 0.0
    month_pay_neg_sum = 0.0
    expected_total_sales = 0.0
    margin_pct = 0.0
    order_count = 0
    # 당일 sales 테이블 기반 계약 신규/조정 분리
    today_sales_new = 0.0    # 오늘 신규 계약 (양수)
    today_sales_adj = 0.0    # 오늘 금액 수정 조정 (음수 or 양수)
    daily_contract_txn_count = 0  # 당일 sales 원장 기준 주문 건수(고유 order_id)
    daily_contract_margin_pct = 0.0  # 당일 원장에 등장한 주문들의 계약금·원가 기준 마진율(표시용)
    try:
        _ph_df_dash = load_payment_history_dashboard_cached(db_filename, month_start, today)
        ph_totals = _dashboard_cancel_reduce_totals_from_ph(_ph_df_dash, today, month_start, today)
    except Exception:
        ph_totals = {
            "today_cancel": 0.0,
            "today_reduce": 0.0,
            "month_cancel": 0.0,
            "month_reduce": 0.0,
        }
    try:
        if not payments.empty and "payment_date" in payments.columns and "amount" in payments.columns:
            pmt = payments.copy()
            pmt["payment_date"] = pd.to_datetime(pmt["payment_date"], errors="coerce")
            pmt = pmt.dropna(subset=["payment_date"])
            if not pmt.empty:
                pmt["_date"] = pmt["payment_date"].dt.date
                month_pmt = pmt[(pmt["_date"] >= month_start) & (pmt["_date"] <= today)]
                today_pmt = pmt[pmt["_date"] == today]
                daily_receipt_total = float(today_pmt["amount"].fillna(0).sum()) if len(today_pmt) else 0.0
                if len(today_pmt) > 0:
                    if "payment_method" in today_pmt.columns or "onnuri_approval_code" in today_pmt.columns:
                        _g_recv = (
                            today_pmt.groupby(_kpi_receipt_method_label_series(today_pmt), sort=False)["amount"]
                            .sum()
                            .sort_values(ascending=False)
                        )
                        daily_receipt_methods_html = "".join(
                            f'<div class="kpi-daily-recv-row"><span class="kpi-daily-recv-m">{html.escape(str(_mk))}</span>'
                            f'<span class="kpi-daily-recv-amt">{float(_mv):,.0f}원</span></div>'
                            for _mk, _mv in _g_recv.items()
                        )
                    else:
                        daily_receipt_methods_html = (
                            f'<div class="kpi-daily-recv-row"><span class="kpi-daily-recv-m">수단 미분류</span>'
                            f'<span class="kpi-daily-recv-amt">{daily_receipt_total:,.0f}원</span></div>'
                        )
                cumulative_sales = float(month_pmt["amount"].fillna(0).sum()) if len(month_pmt) > 0 else 0.0
                if len(month_pmt) > 0:
                    if "payment_method" in month_pmt.columns or "onnuri_approval_code" in month_pmt.columns:
                        _g_month_recv = (
                            month_pmt.groupby(_kpi_receipt_method_label_series(month_pmt), sort=False)["amount"]
                            .sum()
                            .sort_values(ascending=False)
                        )
                        month_cumulative_methods_html = "".join(
                            f'<div class="kpi-daily-recv-row"><span class="kpi-daily-recv-m">{html.escape(str(_mk))}</span>'
                            f'<span class="kpi-daily-recv-amt">{float(_mv):,.0f}원</span></div>'
                            for _mk, _mv in _g_month_recv.items()
                        )
                    else:
                        month_cumulative_methods_html = (
                            f'<div class="kpi-daily-recv-row"><span class="kpi-daily-recv-m">수단 미분류</span>'
                            f'<span class="kpi-daily-recv-amt">{cumulative_sales:,.0f}원</span></div>'
                        )
                _neg_month = month_pmt[month_pmt["amount"].astype(float) < 0]
                month_pay_neg_sum = float(_neg_month["amount"].fillna(0).sum()) if len(_neg_month) else 0.0

        if not orders.empty and "order_date" in orders.columns:
            ord_df = orders.copy()
            ord_df["order_date"] = pd.to_datetime(ord_df["order_date"], errors="coerce")
            ord_df = ord_df.dropna(subset=["order_date"])
            if not ord_df.empty:
                ord_df["_date"] = ord_df["order_date"].dt.date
                month_ord = ord_df[(ord_df["_date"] >= month_start) & (ord_df["_date"] <= today)]
                if len(month_ord) > 0:
                    # 마진율은 주문 기준으로 계산 (누적매출 지표 기준과 분리)
                    tot_amt = month_ord["total_amount"].fillna(0)
                    tot_cost = month_ord["cost_price"].fillna(0)
                    if "display_cost_amount" in month_ord.columns:
                        tot_cost = tot_cost + month_ord["display_cost_amount"].fillna(0)
                    sum_sales = float(tot_amt.sum())
                    sum_cost = float(tot_cost.sum())
                    margin_pct = (sum_sales - sum_cost) / sum_sales * 100 if sum_sales else 0.0
                    order_count = len(month_ord)

        # sales 테이블에서 오늘 신규 계약 vs 금액 수정 조정 분리
        if not sales_df.empty and "transaction_date" in sales_df.columns:
            _sd = sales_df.copy()
            _sd["transaction_date"] = pd.to_datetime(_sd["transaction_date"], errors="coerce")
            _sd = _sd.dropna(subset=["transaction_date"])
            _sd["_date"] = _sd["transaction_date"].dt.date
            _month_sd = _sd[(_sd["_date"] >= month_start) & (_sd["_date"] <= today)]
            if not _month_sd.empty:
                # 상단 KPI "누적매출(월)"과 하단 Net Sales를 동일 기준(sales.amount 순액)으로 통일
                expected_total_sales = float(_month_sd["amount"].sum())
            _today_sd = _sd[_sd["_date"] == today]
            if not _today_sd.empty:
                today_sales_new = float(_today_sd[_today_sd["amount"] > 0]["amount"].sum())
                today_sales_adj = float(_today_sd[_today_sd["amount"] < 0]["amount"].sum())
                try:
                    if "order_id" in _today_sd.columns:
                        _oid_ct = (
                            pd.to_numeric(_today_sd["order_id"], errors="coerce").dropna().astype(int).unique().tolist()
                        )
                        daily_contract_txn_count = len(_oid_ct) if _oid_ct else int(len(_today_sd))
                        if _oid_ct and not orders.empty:
                            _co_day = orders[orders["id"].isin(_oid_ct)]
                            if not _co_day.empty and "total_amount" in _co_day.columns:
                                _ct_tot = float(_co_day["total_amount"].fillna(0).astype(float).sum())
                                _ct_cst = (
                                    float(_co_day["cost_price"].fillna(0).astype(float).sum())
                                    if "cost_price" in _co_day.columns
                                    else 0.0
                                )
                                if "display_cost_amount" in _co_day.columns:
                                    _ct_cst += float(_co_day["display_cost_amount"].fillna(0).astype(float).sum())
                                if _ct_tot > 0:
                                    daily_contract_margin_pct = (_ct_tot - _ct_cst) / _ct_tot * 100.0
                    else:
                        daily_contract_txn_count = int(len(_today_sd))
                except Exception:
                    daily_contract_txn_count = int(len(_today_sd))
    except Exception:
        pass

    today_sales_net = today_sales_new + today_sales_adj

    # 당일 계약 마이너스 조정 표시 (조정분이 없으면 숨김)
    _contract_adj_display = "" if today_sales_adj < 0 else "display:none;"
    _contract_adj_new_fmt = f"{today_sales_new:,.0f}"
    _contract_adj_neg_fmt = f"{abs(today_sales_adj):,.0f}"

    # 일평균 기반 예상 월매출 계산
    # (이번 달 누적 계약매출 ÷ 경과일수) × 이번 달 총일수
    _days_elapsed = (today - month_start).days + 1  # 1일 ~ 오늘 포함
    _days_in_month = calendar.monthrange(today.year, today.month)[1]
    projected_monthly_sales = (
        (expected_total_sales / _days_elapsed * _days_in_month)
        if _days_elapsed > 0 and expected_total_sales > 0
        else 0.0
    )
    _proj_sub = f"일평균 {expected_total_sales/_days_elapsed:,.0f}원 × {_days_in_month}일" if _days_elapsed > 0 and expected_total_sales > 0 else ""

    if payments.empty or "payment_date" not in payments.columns:
        daily_receipt_total = 0.0
        daily_receipt_methods_html = '<div class="kpi-daily-recv-empty">결제일(payment_date) 없음</div>'
        month_cumulative_methods_html = '<div class="kpi-daily-recv-empty">결제일(payment_date) 없음</div>'
    else:
        if not daily_receipt_methods_html:
            daily_receipt_methods_html = '<div class="kpi-daily-recv-empty">당일 수납 내역 없음</div>'
        if not month_cumulative_methods_html:
            month_cumulative_methods_html = '<div class="kpi-daily-recv-empty">당월 수납 내역 없음</div>'

    if payments.empty or "payment_date" not in payments.columns:
        _recv_month_line1 = "⚠️ 결제일(payment_date) 없음 — 수납·상계 표시 불가"
        _recv_month_line2 = ""
        _recv_month_line3 = ""
        _recv_month_hint = ""
    else:
        _recv_month_line1 = "당월 결제 순액에 상계(음수) 반영됨"
        _recv_month_line2 = f"당월 상계(음수 결제) 합: {month_pay_neg_sum:,.0f}원"
        _recv_month_line3 = (
            f"이력·취소/삭제 {ph_totals['month_cancel']:,.0f}원 · "
            f"이력·감액(결제변경) {ph_totals['month_reduce']:,.0f}원"
        )
        _recv_month_hint = (
            "당월 음수 결제·이력이 없으면 0입니다."
            if (month_pay_neg_sum == 0 and ph_totals["month_cancel"] == 0 and ph_totals["month_reduce"] == 0)
            else ""
        )

    _wd_kr_dash = ("월", "화", "수", "목", "금", "토", "일")
    _dash_daily_date_title = f"{today.year}.{today.month:02d}.{today.day:02d} ({_wd_kr_dash[today.weekday()]})"

    st.markdown(
        textwrap.dedent(f"""
        <style>
        .kpi-table {{
            width: 100%;
            table-layout: fixed;
            border-collapse: separate;
            border-spacing: 8px 0;
            margin-bottom: 0.5rem;
        }}
        .kpi-table td {{
            background: #f8f9fa;
            border-radius: 10px;
            padding: 12px 10px;
            text-align: center;
            vertical-align: top;
            width: 14.28%;
        }}
        .kpi-td-daily-contract {{
            width: 15%;
            min-width: 138px;
        }}
        .kpi-contract-adj-row {{
            margin-top: 6px;
            font-size: 0.72rem;
            color: #546e7a;
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 2px;
            flex-wrap: wrap;
        }}
        .kpi-contract-adj-positive {{
            color: #1565c0;
            font-weight: 600;
        }}
        .kpi-contract-adj-op {{
            color: #78909c;
            font-weight: 700;
        }}
        .kpi-contract-adj-negative {{
            color: #c62828;
            font-weight: 600;
        }}
        .kpi-td-daily-receipt {{
            width: 18%;
            min-width: 168px;
        }}
        .kpi-daily-recv-total {{
            font-size: 1.28rem;
            font-weight: 800;
            color: #0d47a1;
            line-height: 1.2;
            margin-top: 5px;
        }}
        .kpi-daily-recv-caption {{
            font-size: 0.58rem;
            color: #90a4ae;
            margin-top: 4px;
            font-weight: 600;
        }}
        .kpi-daily-recv-breakdown {{
            margin-top: 8px;
            text-align: left;
            padding: 0 2px 0 0;
            max-height: 110px;
            overflow-y: auto;
            width: 100%;
        }}
        .kpi-month-recv-breakdown {{
            margin-top: 6px;
            text-align: left;
            padding: 0 2px 0 0;
            max-height: 130px;
            overflow-y: auto;
            width: 100%;
        }}
        .kpi-td-month-cumulative-receipt {{
            min-width: 168px;
            width: 18%;
        }}
        .kpi-daily-recv-row {{
            font-size: 0.62rem;
            color: #546e7a;
            line-height: 1.5;
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            gap: 6px;
            border-bottom: 1px solid #eceff1;
            padding: 3px 0;
        }}
        .kpi-daily-recv-row:last-child {{
            border-bottom: none;
        }}
        .kpi-daily-recv-m {{
            flex: 1;
            min-width: 0;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }}
        .kpi-daily-recv-amt {{
            font-size: 0.64rem;
            font-weight: 700;
            color: #37474f;
            white-space: nowrap;
        }}
        .kpi-daily-recv-empty {{
            font-size: 0.62rem;
            color: #90a4ae;
            text-align: center;
            padding: 6px 0;
        }}
        .kpi-value-daily-contract {{
            font-size: 1.22rem;
            font-weight: 800;
            color: #e65100;
            line-height: 1.15;
            margin-top: 4px;
        }}
        .kpi-date-pill {{
            display: inline-block;
            font-size: 0.6rem;
            font-weight: 700;
            color: #37474f;
            background: linear-gradient(180deg, #eceff1, #dfe3e6);
            padding: 2px 8px;
            border-radius: 999px;
            margin-left: 5px;
            vertical-align: middle;
            letter-spacing: 0.02em;
        }}
        .kpi-contract-date-below {{
            margin: 2px 0 6px 0;
        }}
        .kpi-contract-date-below .kpi-date-pill {{
            margin-left: 0;
        }}
        .kpi-daily-meta {{
            font-size: 0.74rem;
            color: #263238;
            font-weight: 600;
            margin-top: 6px;
            line-height: 1.4;
        }}
        .kpi-table td.highlight {{
            background: linear-gradient(135deg, #fff3e0, #ffe0b2);
            border: 2px solid #ff6f00;
        }}
        .kpi-label {{
            font-size: 0.78rem;
            color: #666;
            font-weight: 600;
            letter-spacing: 0.03em;
            margin-bottom: 6px;
        }}
        .kpi-value {{
            font-size: 1.45rem;
            font-weight: 800;
            color: #1a1a2e;
            line-height: 1.2;
        }}
        .kpi-value.contract {{
            color: #e65100;
            font-size: 1.6rem;
        }}
        .kpi-sub {{
            font-size: 0.7rem;
            color: #888;
            margin-top: 4px;
        }}
        .kpi-sub2 {{
            font-size: 0.62rem;
            color: #777;
            margin-top: 3px;
            line-height: 1.35;
        }}
        .kpi-sub-muted {{
            color: #999;
            font-size: 0.58rem;
        }}
        .kpi-detail-stack {{
            display: flex;
            flex-direction: column;
            align-items: center;
            gap: 6px;
            margin-top: 8px;
            width: 100%;
        }}
        .kpi-detail-stack .kpi-sub,
        .kpi-detail-stack .kpi-sub2 {{
            display: block;
            margin-top: 0;
        }}
        </style>
        <table class="kpi-table">
          <tr>
            <td class="highlight kpi-td-daily-contract">
              <div class="kpi-label">📋 당일 계약</div>
              <div class="kpi-contract-date-below">
                <span class="kpi-date-pill">{html.escape(_dash_daily_date_title)}</span>
              </div>
              <div class="kpi-value-daily-contract">{today_sales_net:,.0f}원</div>
              <div class="kpi-daily-meta">({daily_contract_txn_count}건) · 마진율 {daily_contract_margin_pct:.1f}%</div>
              <div class="kpi-contract-adj-row" style="{_contract_adj_display}"><span class="kpi-contract-adj-positive">{_contract_adj_new_fmt}원</span><span class="kpi-contract-adj-op"> &minus; </span><span class="kpi-contract-adj-negative">{_contract_adj_neg_fmt}원</span></div>
            </td>
            <td>
              <div class="kpi-label">📈 누적매출 (월)</div>
              <div class="kpi-value">{expected_total_sales:,.0f}원</div>
              <div class="kpi-sub">{month_start.strftime("%m/%d")} ~ 오늘</div>
            </td>
            <td class="kpi-td-daily-receipt">
              <div class="kpi-label">💳 일일 수납액<span class="kpi-date-pill">{html.escape(_dash_daily_date_title)}</span></div>
              <div class="kpi-daily-recv-total">{daily_receipt_total:,.0f}원</div>
              <div class="kpi-daily-recv-breakdown">
                {daily_receipt_methods_html}
              </div>
            </td>
            <td class="kpi-td-month-cumulative-receipt">
              <div class="kpi-label">📊 누적 수납액 (월)</div>
              <div class="kpi-value">{cumulative_sales:,.0f}원</div>
              <div class="kpi-month-recv-breakdown">
                {month_cumulative_methods_html}
              </div>
            </td>
            <td>
              <div class="kpi-label">🎯 예상 월매출</div>
              <div class="kpi-value">{projected_monthly_sales:,.0f}원</div>
              <div class="kpi-sub">{_proj_sub}</div>
            </td>
            <td>
              <div class="kpi-label">💹 누적 마진율 (월)</div>
              <div class="kpi-value">{margin_pct:.1f}%</div>
            </td>
            <td>
              <div class="kpi-label">🛒 판매건수</div>
              <div class="kpi-value">{order_count}건</div>
            </td>
          </tr>
        </table>
        """),
        unsafe_allow_html=True,
    )

    st.divider()

    # 주문별 결제합계 - 이하 "잔금 불일치 경고" + "미수금 현황" 두 곳에서 공유
    _dash_pay_sum = (
        payments.groupby("order_id")["amount"].sum()
        if not payments.empty and "order_id" in payments.columns
        else pd.Series(dtype=float)
    )
    # 잔금 불일치 경고: balance_status가 '완납'인데 실 계산상 잔금이 0이 아닌 건수
    if len(orders) > 0 and "balance_status" in orders.columns:
        warn_orders = orders.copy()
        warn_orders["paid"] = warn_orders["id"].map(_dash_pay_sum).fillna(0)
        warn_orders["real_balance"] = warn_orders["total_amount"] - warn_orders["paid"]
        suspicious = warn_orders[(warn_orders["balance_status"] == "완납") & (warn_orders["real_balance"] != 0)]
        if len(suspicious) > 0:
            st.error(f"⚠️ 잔금 불일치 의심 건 {len(suspicious)}건 발생 (완납 표시이나 실 잔금이 0이 아님)")
            with st.expander("📋 잔금 불일치 건 상세"):
                disp = suspicious.merge(customers[["id", "name"]], left_on="customer_id", right_on="id", how="left", suffixes=("_order", "_cust"))
                disp = disp.rename(columns={"id_order": "주문ID", "name": "고객명", "total_amount": "총액", "paid": "결제합계", "real_balance": "실잔금", "balance_status": "표시상태"})
                show_df = disp[["주문ID", "고객명", "총액", "결제합계", "실잔금", "표시상태"]].copy()
                for col in ("총액", "결제합계", "실잔금"):
                    show_df[col] = show_df[col].apply(_fmt_num)
                st.dataframe(show_df, width='stretch')
                st.caption("결제 금액을 수정하려면 **고객 및 잔금 관리** → 고객 선택 → **결제 내역 조회 및 취소** / **잔금 추가 결제**에서 해당 주문을 수정하세요.")
            if st.button("🔄 잔금 상태 자동 보정 (결제 합계 기준으로 완납/미납 다시 계산)", key="dashboard_balance_fix_btn"):
                try:
                    for oid in suspicious["id"].tolist():
                        _recalc_order_actual_margin_supabase(db_filename, int(oid))
                    clear_data_cache()
                    st.toast(f"✅ {len(suspicious)}건 보정했습니다. 잔금 상태가 결제 합계에 맞게 갱신되었습니다.", icon="✅")
                    st.rerun()
                except Exception as e:
                    st.error(f"보정 중 오류가 발생했습니다: {e}")

    # ---------- 2. 미수금 고객 현황: 배송일이 10일 이내로 남았거나 지났고, 잔금 > 0 ----------
    st.subheader("2. 미수금 고객 현황")
    if len(orders) > 0:
        pay_sum = _dash_pay_sum  # 위에서 이미 계산된 pay_sum 재사용
        orders = orders.copy()
        orders["paid"] = orders["id"].map(pay_sum).fillna(0)
        orders["balance"] = orders["total_amount"] - orders["paid"]
        orders["delivery_date"] = pd.to_datetime(orders["delivery_date"], errors="coerce")
        orders_with_cust = orders.merge(customers, left_on="customer_id", right_on="id", suffixes=("", "_c"))
        orders_with_cust = orders_with_cust.rename(columns={"name": "고객명", "phone1": "전화번호", "delivery_date": "배송일", "category": "품목", "employee_names": "담당자", "balance": "잔금"})
        # 배송일이 오늘 기준 10일 이내로 남았거나 이미 지난 경우 (delivery_date <= today+10)
        cutoff = today + timedelta(days=10)
        mask_date = orders_with_cust["배송일"].dt.date <= cutoff if pd.api.types.is_datetime64_any_dtype(orders_with_cust["배송일"]) else False
        mask_balance = orders_with_cust["잔금"] > 0
        unpaid_list = orders_with_cust.loc[mask_balance & (orders_with_cust["배송일"].notna())]
        if pd.api.types.is_datetime64_any_dtype(unpaid_list["배송일"]):
            unpaid_list = unpaid_list[unpaid_list["배송일"].dt.date <= cutoff]
        display_cols = ["고객명", "전화번호", "배송일", "품목", "담당자", "잔금"]
        unpaid_list = unpaid_list[["고객명", "전화번호", "배송일", "품목", "담당자", "잔금"]].copy()
        if len(unpaid_list) > 0 and pd.api.types.is_datetime64_any_dtype(unpaid_list["배송일"]):
            unpaid_list["배송일"] = unpaid_list["배송일"].dt.strftime("%Y-%m-%d")
        if len(unpaid_list) > 0:
            unpaid_display = _format_df_display(unpaid_list, ["잔금"])
            st.dataframe(unpaid_display, width='stretch')
        else:
            st.info("해당 조건의 미수금 고객이 없습니다. (배송일 10일 이내·잔금 있음)")
    else:
        st.info("아직 주문 데이터가 없습니다.")

    st.divider()

    # ---------- 3. 직원별 일일 판매 금액 및 마진율 ----------
    st.subheader("3. 직원별 일일 판매 금액 및 마진율")
    if not sales_df.empty and "transaction_date" in sales_df.columns:
        _daily_emp_sd = sales_df.copy()
        _daily_emp_sd["transaction_date"] = pd.to_datetime(_daily_emp_sd["transaction_date"], errors="coerce")
        _daily_emp_sd = _daily_emp_sd.dropna(subset=["transaction_date"])
        _daily_emp_today = _daily_emp_sd[_daily_emp_sd["transaction_date"].dt.date == today]
        if not _daily_emp_today.empty:
            # 양수(판매) / 음수(상계) 분리
            _daily_amt_num = pd.to_numeric(_daily_emp_today["amount"], errors="coerce").fillna(0)
            _daily_pos = _daily_emp_today[_daily_amt_num > 0]
            _daily_neg = _daily_emp_today[_daily_amt_num < 0]

            # 직원별 순액·마진 집계 (마진율 계산 기준)
            _df_net = _kpi_employee_totals_from_sales_slice(_daily_emp_today, orders)
            # 직원별 양수(판매금액) 합계
            _df_pos = (
                _kpi_employee_totals_from_sales_slice(_daily_pos, orders)
                if not _daily_pos.empty
                else pd.DataFrame(columns=["employee", "revenue", "margin", "display_sales"])
            )
            # 직원별 음수(상계금액) 합계
            _df_neg_agg = (
                _kpi_employee_totals_from_sales_slice(_daily_neg, orders)
                if not _daily_neg.empty
                else pd.DataFrame(columns=["employee", "revenue", "margin", "display_sales"])
            )

            # 이름 없는 행 필터
            def _daily2_filter_blank(df):
                if df.empty or "employee" not in df.columns:
                    return df
                return df[~df["employee"].map(_kpi_employee_names_cell_is_blank)].copy()

            _df_net = _daily2_filter_blank(_df_net)
            _df_pos = _daily2_filter_blank(_df_pos)
            _df_neg_agg = _daily2_filter_blank(_df_neg_agg)

            if not _df_net.empty:
                # 순액 기준 테이블
                _merged = _df_net[["employee", "revenue", "margin"]].rename(
                    columns={"revenue": "순액", "margin": "당일 마진액"}
                ).copy()
                # 양수(판매금액) 병합
                if not _df_pos.empty:
                    _merged = _merged.merge(
                        _df_pos[["employee", "revenue"]].rename(columns={"revenue": "당일 판매금액"}),
                        on="employee", how="left",
                    )
                else:
                    _merged["당일 판매금액"] = 0.0
                # 음수(상계금액) 병합
                if not _df_neg_agg.empty:
                    _merged = _merged.merge(
                        _df_neg_agg[["employee", "revenue"]].rename(columns={"revenue": "상계금액"}),
                        on="employee", how="left",
                    )
                else:
                    _merged["상계금액"] = 0.0

                if "당일 판매금액" not in _merged.columns:
                    _merged["당일 판매금액"] = 0.0
                if "상계금액" not in _merged.columns:
                    _merged["상계금액"] = 0.0
                _merged["당일 판매금액"] = _merged["당일 판매금액"].fillna(0.0)
                _merged["상계금액"] = _merged["상계금액"].fillna(0.0)

                _merged["마진율(%)"] = _merged.apply(
                    lambda _r: round(_r["당일 마진액"] / _r["순액"] * 100, 1) if _r["순액"] != 0 else 0.0,
                    axis=1,
                )
                _merged = _merged.sort_values("순액", ascending=False).reset_index(drop=True)

                # 상계금액 열 유무 판단
                _has_neg = (_merged["상계금액"] < 0).any()

                if _has_neg:
                    _disp = _merged[["employee", "당일 판매금액", "상계금액", "순액", "당일 마진액", "마진율(%)"]].rename(
                        columns={"employee": "직원명"}
                    )
                    _money_cols = ["당일 판매금액", "상계금액", "순액", "당일 마진액"]
                else:
                    _disp = _merged[["employee", "순액", "당일 마진액", "마진율(%)"]].rename(
                        columns={"employee": "직원명", "순액": "당일 판매금액"}
                    )
                    _money_cols = ["당일 판매금액", "당일 마진액"]

                # 포맷 함수
                def _daily2_fmt_krw(x):
                    try:
                        v = float(x)
                        return f"{v:,.0f}원" if v != 0 else "-"
                    except (TypeError, ValueError):
                        return str(x)

                def _daily2_fmt_pct(x):
                    try:
                        return f"{float(x):.1f}%"
                    except (TypeError, ValueError):
                        return str(x)

                # 음수 빨간색 스타일 적용 (column 단위)
                def _daily2_color_neg(col):
                    return [
                        "color: #d32f2f; font-weight: 600" if (isinstance(v, (int, float)) and v < 0) else ""
                        for v in col
                    ]

                _fmt_dict = {c: _daily2_fmt_krw for c in _money_cols}
                _fmt_dict["마진율(%)"] = _daily2_fmt_pct

                _styler = (
                    _disp.style
                    .format(_fmt_dict)
                    .apply(_daily2_color_neg, subset=_money_cols)
                )
                st.dataframe(_styler, width='stretch')
                st.caption(
                    f"※ 기준일: {today.strftime('%Y-%m-%d')} · 판매일(transaction_date) 기준 · 복수 담당자 시 1/n 배분"
                    + (" · 빨간색 = 마이너스(상계) 금액" if _has_neg else "")
                )
            else:
                st.info(f"오늘({today.strftime('%Y-%m-%d')}) 직원이 배정된 판매 데이터가 없습니다.")
        else:
            st.info(f"오늘({today.strftime('%Y-%m-%d')}) 판매 데이터가 없습니다.")
    else:
        st.info("판매 데이터가 없습니다.")

    # ---------- 4. 월별 직원 판매 현황 및 평가 (종합: 매출70+마진20+전시10, 현금수금집계는 참고 열) ----------
    # @st.fragment로 분리: 연/월 selectbox 변경 시 이 섹션만 rerun
    _render_kpi_section(sales_df, orders, db_filename)

    # ---------- 5. 관리자 통계: 기간별 총 계약 금액 / 총 미수금 ----------
    st.subheader("5. 기간별 통계 (총 계약 금액 / 총 미수금)")
    if "stats_start" not in st.session_state:
        st.session_state["stats_start"] = today.replace(day=1)
    if "stats_end" not in st.session_state:
        st.session_state["stats_end"] = today
    col1, col2 = st.columns(2)
    with col1:
        stats_start = st.date_input("시작일", key="stats_start")
    with col2:
        stats_end = st.date_input("종료일", key="stats_end")
    if len(orders) > 0:
        orders["order_date"] = pd.to_datetime(orders["order_date"], errors="coerce")
        period_orders = orders[(orders["order_date"].dt.date >= stats_start) & (orders["order_date"].dt.date <= stats_end)]
        # sales(transaction_date) 구간의 amount 순합(감액 음수 포함) — 3번 매출 점수(70) 직원 배분 전 총액과 동일 기준, 현금수금집계(참고)와는 별개
        period_sales_net = 0.0
        if not sales_df.empty and "transaction_date" in sales_df.columns and "amount" in sales_df.columns:
            _sd_stat = sales_df.copy()
            _sd_stat["transaction_date"] = pd.to_datetime(_sd_stat["transaction_date"], errors="coerce")
            _sd_stat = _sd_stat.dropna(subset=["transaction_date"])
            _m_stat = (_sd_stat["transaction_date"].dt.date >= stats_start) & (
                _sd_stat["transaction_date"].dt.date <= stats_end
            )
            period_sales_net = float(_sd_stat.loc[_m_stat, "amount"].fillna(0).astype(float).sum())
        if len(period_orders) > 0 and not payments.empty:
            order_ids = period_orders["id"].tolist()
            pay_df = payments[payments["order_id"].isin(order_ids)][["order_id", "amount"]].copy()
            paid_per = pay_df.groupby("order_id")["amount"].sum() if len(pay_df) > 0 else pd.Series(dtype=float)
            period_orders = period_orders.copy()
            period_orders["_paid"] = period_orders["id"].map(paid_per).fillna(0)
            period_orders["_bal"] = period_orders["total_amount"] - period_orders["_paid"]
            total_unpaid_period = float(period_orders["_bal"].clip(lower=0).sum())
        else:
            total_unpaid_period = 0.0
        st.metric(
            "해당 기간 총 계약 금액",
            f"{period_sales_net:,.0f}원",
            help="sales 테이블 transaction_date·amount 합계(증액·감액 음수 반영). '3. 월별 직원 평가' 매출 점수(70)는 동일 월 sales 순액 1/n 배분이며, 현금수금집계는 payment_date·수납 수단 버킷으로 별도 집계된 참고 금액입니다.",
        )
        st.metric("해당 기간 총 미수금", f"{total_unpaid_period:,.0f}원")
    else:
        st.metric("해당 기간 총 계약 금액", "0원")
        st.metric("해당 기간 총 미수금", "0원")

    # ---------- 5. To-Do 리스트 (직원 간 인수인계) ----------
    # @st.fragment로 분리: To-Do 등록·완료·삭제 시 이 섹션만 rerun (전체 대시보드 재로딩 없음)
    _render_dashboard_todos_only(db_filename)


# ========== 메인: 탭 구성 및 라우팅 ==========

@st.cache_resource
def _init_system_once():
    """서버 기동 시 최초 1회만 마스터 DB 스키마 검사를 수행하여 Rerun 병목 제거"""
    init_master_db()
    conn_m = get_master_conn()
    try:
        ensure_master_schema(conn_m)
    finally:
        conn_m.close()
    return True


def main():
    _init_system_once()
    ensure_session()
    _inject_mobile_css()
    _inject_favicon()
    _inject_branding_css()

    # 전역 Sticky Header 스타일 주입: 상단 메뉴/탭 고정 및 본문 패딩 조정
    st.markdown(
        """
        <style>
        .sticky-header {
            position: fixed;
            top: 0;
            left: 0;
            right: 0;
            z-index: 9999;
            background-color: white;
            padding: 1rem 1rem 0 1rem;
            box-shadow: 0px 2px 5px rgba(0, 0, 0, 0.1);
        }
        /* 사이드바(PC)가 열려 있을 때 본문과 정렬 */
        @media (min-width: 768px) {
            .sticky-header {
                left: 21rem; /* 사이드바 너비만큼 밀어줌 */
            }
        }
        /* 본문이 헤더에 가려지지 않도록 상단 패딩 추가 */
        .block-container {
            padding-top: 6rem !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # 로그인 성공 직후: 브라우저 localStorage에 이메일 저장(한 번만 실행 후 플래그 제거)
    _pending = st.session_state.pop("_pending_save_login_email", None)
    if _pending:
        # json.dumps로 JS 문자열 이스케이프 후 </script> 시퀀스 추가 방어 (XSS 차단)
        _val_js = json.dumps(str(_pending)).replace("</", r"<\/").replace("<!--", r"<\!--")
        st.markdown(
            f'<script>(function(){{ try {{ localStorage.setItem("emons_login_email", {_val_js}); }} catch(e) {{}} }})();</script>',
            unsafe_allow_html=True,
        )

    # Supabase Auth: 로그인하지 않았으면 로그인 화면만 표시
    if not st.session_state.logged_in:
        render_login()
        return

    user = st.session_state.current_user
    role = user["role"]

    # 쿼리 파라미터를 이용한 홈 이동(?home=1) 처리:
    # 로고 클릭 시 언제든지 메인 대시보드/홈으로 돌아갈 수 있도록,
    # payment_monitor 등 별도 관리자 화면 진입 상태를 초기화한다.
    try:
        home_flag = st.query_params.get("home")
    except Exception:
        home_flag = None
    if home_flag:
        # 관리자 전용 모니터링 등 별도 라우팅 상태를 초기화
        if "active_admin_page" in st.session_state:
            del st.session_state["active_admin_page"]
        # URL에서 home 파라미터 제거
        try:
            q = dict(st.query_params)
            q.pop("home", None)
            st.query_params.from_dict(q)
        except Exception:
            pass
        st.rerun()
        return

    # 로그인 후 사이드바: 좌측 상단 공통 로고(emons-log.svg / emons-logo.svg 우선, 에러 시 빨간 메시지)
    # 로고를 클릭하면 항상 현재 토큰을 포함한 URL(?home=1&auth=...)로 이동하여
    # 새 세션이 열리더라도 URL 토큰으로 즉시 로그인 복구 후 대시보드(홈)로 돌아오도록 처리.
    raw_logo_html = _common_logo_html(
        _resolve_logo_path(),
        fallback_id="emons-logo-fallback-sidebar",
    )
    clickable_logo_html = f"""
    <div style="cursor:pointer;"
         onclick="(function(){{ 
             try {{
                 var u = new URL(window.location.href);
                 u.searchParams.set('home', '1');
                 window.location.href = u.toString();
             }} catch(e2) {{
                 window.location.href = window.location.pathname + '?home=1';
             }}
         }})();">
    {raw_logo_html}
    </div>
    """
    st.sidebar.markdown(clickable_logo_html, unsafe_allow_html=True)
    # 홈 버튼: 세션 상태만 초기화하고 rerun하여 로그아웃 없이 대시보드로 안전하게 복귀
    if st.sidebar.button("🏠 첫 화면으로 (대시보드)", width='stretch', key="sidebar_home_btn"):
        if "active_admin_page" in st.session_state:
            del st.session_state["active_admin_page"]
        st.rerun()
    # 한 직원이 여러 매장: 매장 선택 드롭다운 (superadmin 제외)
    allowed_stores = user.get("allowed_stores") or []
    if role != "superadmin" and len(allowed_stores) > 1:
        options = [s[2] for s in allowed_stores]
        current_sid = user.get("store_id")
        current_idx = next((i for i, s in enumerate(allowed_stores) if s[0] == current_sid), 0)
        sel_idx = st.sidebar.selectbox(
            "매장 선택",
            range(len(options)),
            format_func=lambda i: options[i],
            index=current_idx,
            key="sidebar_store_sel",
        )
        if sel_idx != current_idx:
            st.session_state.current_user["store_id"] = allowed_stores[sel_idx][0]
            st.session_state.current_user["db_filename"] = allowed_stores[sel_idx][1]
            st.session_state.current_db = allowed_stores[sel_idx][1]
            st.rerun()
    store_display = get_store_display_name(user)
    st.sidebar.markdown(
        f"<div style='padding:0.4rem 0; border-radius:0.4rem;'>"
        f"<p style='margin:0; font-size:1.1rem; font-weight:600;'>{html.escape(store_display)}</p>"
        f"<p style='margin:0.2rem 0 0 0; font-size:0.85rem; color:#666;'>👤 ID: {html.escape(user['username'])}</p>"
        f"</div>",
        unsafe_allow_html=True
    )
    st.sidebar.divider()
    # 비밀번호 변경 (Supabase Auth)
    with st.sidebar.expander("🔐 비밀번호 변경"):
        new_pw = st.text_input("새 비밀번호", type="password", key="new_password_input")
        new_pw_confirm = st.text_input("새 비밀번호 확인", type="password", key="new_password_confirm")
        if st.button("비밀번호 변경", key="sidebar_change_pw_btn"):
            if not new_pw:
                st.error("새 비밀번호를 입력해 주세요.")
            elif len(new_pw) < 6:
                st.error("비밀번호는 6자 이상이어야 합니다.")
            elif new_pw != new_pw_confirm:
                st.error("새 비밀번호가 일치하지 않습니다.")
            else:
                auth_client, auth_err = get_supabase_client_with_auth_session()
                if auth_err:
                    st.error(f"⚠️ {auth_err}")
                else:
                    try:
                        auth_client.auth.update_user({"password": new_pw})
                        st.success("비밀번호가 변경되었습니다. 다음 로그인부터 새 비밀번호를 사용하세요.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"비밀번호 변경에 실패했습니다: {str(e)}")
    st.sidebar.divider()
    # 관리자 전용: 결제 변경/취소 모니터링 화면 진입 버튼
    if role in ("store_admin", "superadmin"):
        if st.sidebar.button("🚨 결제 변경/취소 모니터링", width='stretch'):
            st.session_state["active_admin_page"] = "payment_monitor"
        _del_db = st.session_state.get("current_db")
        _pending_del_count = len(_fetch_pending_delete_requests(_del_db)) if _del_db else 0
        _del_btn_label = f"🗑️ 주문 삭제 요청 관리 ({_pending_del_count}건)" if _pending_del_count > 0 else "🗑️ 주문 삭제 요청 관리"
        if st.sidebar.button(_del_btn_label, width='stretch'):
            st.session_state["active_admin_page"] = "delete_requests"
    # 최고 관리자 전용: 직원 계정 관리 및 발령
    if role == "superadmin":
        if st.sidebar.button("👥 직원 관리", width='stretch'):
            st.session_state["active_admin_page"] = "employee_management"
    st.sidebar.markdown("---")
    if st.sidebar.button("🚪 로그아웃", width='stretch'):
        try:
            client, _ = get_supabase_client()
            if client:
                client.auth.sign_out()
        except Exception:
            pass
        for key in list(st.session_state.keys()):
            del st.session_state[key]
        st.rerun()

    # 관리자 전용 모니터링 화면 라우팅
    if role in ("store_admin", "superadmin") and st.session_state.get("active_admin_page") == "payment_monitor":
        render_payment_history_monitor()
        return

    # 관리자 전용: 주문 삭제 요청 관리 화면 라우팅
    if role in ("store_admin", "superadmin") and st.session_state.get("active_admin_page") == "delete_requests":
        _del_db = st.session_state.get("current_db")
        if _del_db:
            _render_admin_delete_requests(_del_db)
        else:
            st.warning("매장 DB 정보를 찾을 수 없습니다.")
        return

    # 최고 관리자 전용: 직원 계정 관리 및 발령
    if role == "superadmin" and st.session_state.get("active_admin_page") == "employee_management":
        render_employee_management()
        return

    # Superadmin: 5탭 최고 관리자 메뉴
    if role == "superadmin":
        render_superadmin()
        return

    # 일반/매장 관리자: 메뉴를 상단 셀렉트로 노출 (모바일에서도 잘 보이게), 넘버링 및 그룹 구분
    if role == "store_admin":
        tab_labels = [
            "1. 대시보드",
            "2. 마케팅 인사이트",
            "3. 새로운 매출 등록",
            "4. 고객 및 잔금 관리",
            "5. 매장 관리자 메뉴",
            "6. 월별 결제수단 집계표",
            "7. 고객 CRM 자동화",
            "8. FAQ (도움말)",
        ]
    else:
        tab_labels = [
            "1. 대시보드",
            "2. 마케팅 인사이트",
            "3. 새로운 매출 등록",
            "4. 고객 및 잔금 관리",
            "5. 월별 결제수단 집계표",
            "6. FAQ (도움말)",
        ]
    if "main_tab_idx" not in st.session_state:
        st.session_state["main_tab_idx"] = 0
    if st.session_state["main_tab_idx"] >= len(tab_labels):
        st.session_state["main_tab_idx"] = 0
    # Supabase 오류 시 안내 (테이블 없음 / RLS / 연결 실패 구분)
    if st.session_state.get("supabase_error"):
        err_text = str(st.session_state["supabase_error"] or "")
        if "schema cache" in err_text or "Could not find the table" in err_text or "app_customers" in err_text:
            st.error("⚠️ **Supabase에 app_customers 테이블이 없습니다.** Supabase 대시보드 → SQL Editor에서 프로젝트의 **SUPABASE_APP_CUSTOMERS.sql** 파일 내용을 실행해 주세요.")
        elif "42501" in err_text or "row-level security" in err_text or "violates row-level security" in err_text:
            if "sales" in err_text.lower():
                st.error("⚠️ **sales 테이블 RLS 정책 오류**: Supabase 대시보드 → SQL Editor에서 **SUPABASE_SALES.sql** 파일의 RLS 정책 부분(또는 전체)을 실행해 주세요.")
            else:
                st.error("⚠️ **Supabase RLS 정책 오류**: " + err_text + " — 해당 테이블에 INSERT를 허용하는 RLS 정책을 추가해 주세요.")
        else:
            st.error("⚠️ **Supabase 연결 실패**: " + err_text + " — .streamlit/secrets.toml의 [supabase] url, key를 확인해 주세요.")
    # 상단 메뉴 선택(Sticky Header 내에 렌더링: 일반 유저/매장관리자 공통)
    st.markdown('<div class="sticky-header">', unsafe_allow_html=True)
    st.markdown("---")
    st.markdown('<p style="margin:0 0 0.25rem 0; font-size:0.85rem; color:#666;">📱 메뉴 선택</p>', unsafe_allow_html=True)
    st.markdown('<p class="mobile-menu-hint" style="margin:0 0 0.35rem 0; font-size:0.8rem; color:#888;">로그아웃·비밀번호는 왼쪽 상단 ☰에서</p>', unsafe_allow_html=True)
    menu_sel = st.selectbox(
        "메뉴",
        tab_labels,
        index=st.session_state["main_tab_idx"],
        key="main_menu_select",
        label_visibility="collapsed",
    )
    st.markdown("</div>", unsafe_allow_html=True)
    idx = tab_labels.index(menu_sel)
    st.session_state["main_tab_idx"] = idx
    st.session_state["current_menu"] = idx
    # 로그인 직후 1회: 내 알림 배너 (주문 수정 / 매출 배분 알림) — 일반 직원용
    _db_fn_for_notif = st.session_state.get("current_db") or (user.get("db_filename") if role != "superadmin" else None)
    if _db_fn_for_notif and role != "superadmin":
        _render_login_notifications(_db_fn_for_notif)
    # 로그인 직후 1회: 부정행위 의심 경보 배너 — 매장관리자·통합관리자 전용
    if _db_fn_for_notif and role in ("store_admin", "superadmin"):
        _render_admin_fraud_alerts(_db_fn_for_notif)

    # 대시보드 탭 선택 시 캐시 재사용으로 즉시 표시
    st.divider()
    if idx == 0:
        render_dashboard()
    elif idx == 1:
        render_marketing_insights_tenant()
    elif idx == 2:
        render_new_sales()
    elif idx == 3:
        render_customer_balance()
    elif role == "store_admin" and idx == 4:
        render_store_admin_employees()
    elif role == "store_admin" and idx == 5:
        render_monthly_payment_report(is_superadmin=False)
    elif role == "user" and idx == 4:
        render_monthly_payment_report(is_superadmin=False)
    elif role == "store_admin" and idx == 6:
        if CRM_MODULE_AVAILABLE:
            render_crm_menu()
        else:
            st.error("CRM 모듈(crm_automation.py)을 불러올 수 없습니다. 파일이 존재하는지 확인해 주세요.")
    elif role == "store_admin" and idx == 7:
        render_faq_page()
    elif role == "user" and idx == 5:
        render_faq_page()


if __name__ == "__main__":
    main()
