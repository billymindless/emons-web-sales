# -*- coding: utf-8 -*-
"""
프랜차이즈 가구 매장용 세일즈 및 경영 대시보드
Database-per-Tenant 아키텍처: Master DB 1개 + 매장별 독립 SQLite 파일
"""
import base64
import io
import hmac
import html
import json
import os
import re
import sqlite3
import threading
import streamlit as st
import pandas as pd
from datetime import datetime, date, timedelta
import requests
import hashlib
import time
import plotly.express as px
import plotly.graph_objects as go
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

# 브라우저 탭 타이틀 및 레이아웃 (반드시 최상단에서 호출)
# 모바일: 넓은 화면 사용 + 사이드바 접힌 상태로 시작
# [아이콘] assets/apple-touch-icon.png 가 있으면 탭·홈화면 추가 아이콘으로 사용.
#         파일 위치: app.py와 같은 디렉터리 기준 assets/apple-touch-icon.png (예: 프로젝트루트/assets/apple-touch-icon.png)
#         권장: 180x180 또는 192x192 PNG, 에몬스 'e' 로고 또는 가구 아이콘.
_ICON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "apple-touch-icon.png")
st.set_page_config(
    page_title="에몬스판매관리 프로그램",
    layout="wide",
    initial_sidebar_state="collapsed",
    page_icon=_ICON_PATH if os.path.exists(_ICON_PATH) else "🪑",
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
    """st.secrets에서 URL/Key를 읽어 Supabase 클라이언트 반환. (client, None) 또는 (None, error_message)."""
    if _create_supabase_client is None:
        return None, "Supabase 라이브러리가 설치되지 않았습니다. pip install supabase 를 실행해 주세요."
    try:
        secrets = st.secrets.get("supabase") or {}
        url = (secrets.get("url") or "").strip()
        key = (secrets.get("key") or secrets.get("anon_key") or "").strip()
        if not url or not key:
            return None, "Supabase URL 또는 Key가 설정되지 않았습니다. .streamlit/secrets.toml에 [supabase] url, key를 추가해 주세요."
        client = _create_supabase_client(url, key)
        return client, None
    except Exception as e:
        err_msg = str(e)
        if not err_msg or err_msg.strip() == "":
            err_msg = "연결 오류가 발생했습니다."
        return None, f"Supabase 연결에 실패했습니다: {err_msg}"


def get_supabase_client_or_warn():
    """Supabase 클라이언트 반환. 실패 시 화면에 경고를 띄우고 None 반환."""
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
    Supabase Admin API용 클라이언트 (service_role_key 사용).
    직원 계정 생성 등 관리자 전용 작업 시 사용. 현재 로그인 세션에 영향 없음.
    반환: (client, None) 또는 (None, error_message).
    """
    if _create_supabase_client is None:
        return None, "Supabase 라이브러리가 설치되지 않았습니다."
    try:
        secrets = st.secrets.get("supabase") or {}
        url = (secrets.get("url") or "").strip()
        if not url:
            return None, "Supabase URL이 설정되지 않았습니다."
        # service_role_key: secrets.toml의 service_role_key 또는 환경변수 SUPABASE_SERVICE_ROLE_KEY
        key = (secrets.get("service_role_key") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
        if not key:
            return None, "직원 계정 생성을 위해 Supabase service_role_key가 필요합니다. .streamlit/secrets.toml에 [supabase] service_role_key를 추가하거나, 환경변수 SUPABASE_SERVICE_ROLE_KEY를 설정해 주세요."
        client = _create_supabase_client(url, key)
        return client, None
    except Exception as e:
        err_msg = str(e).strip() or "연결 오류가 발생했습니다."
        return None, f"Supabase Admin 연결 실패: {err_msg}"


# ---------- Supabase 직원/매장 테이블 (app_users, app_user_stores, app_stores) ----------
# 테이블이 없으면 ensure_supabase_app_tables()로 자동 생성 시도(database_url 있을 때) 또는 SQL Editor에서 SUPABASE_APP_TABLES.sql 실행.

def _supabase_app_tables_available():
    """Supabase에 app_users 테이블이 있는지 확인. (있으면 True, 없으면 False)"""
    client, err = get_supabase_client()
    if err or not client:
        return False
    try:
        client.table("app_users").select("id").limit(1).execute()
        return True
    except Exception:
        return False


def _supabase_run_app_tables_sql():
    """
    Supabase DB에 app_stores, app_users, app_user_stores 테이블이 없으면 생성.
    st.secrets의 supabase.database_url (Postgres 연결 문자열)이 있으면 psycopg2로 DDL 실행.
    성공 시 True, 실패 또는 URL 없음 시 False.
    """
    try:
        import psycopg2
    except ImportError:
        return False
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
    sql_path = os.path.join(BASE_DIR, "SUPABASE_APP_TABLES.sql")
    if not os.path.isfile(sql_path):
        return False
    try:
        with open(sql_path, "r", encoding="utf-8") as f:
            raw = f.read()
    except Exception:
        return False
    # 줄 단위 주석 제거 후, 문장 단위로 분리
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
                if "already exists" not in str(e).lower() and "duplicate" not in str(e).lower():
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


def _get_supabase_stores_list():
    """Supabase app_stores 목록. 반환: [{"id", "store_name", "db_filename"}, ...]"""
    client, err = get_supabase_client()
    if err or not client:
        return []
    try:
        r = client.table("app_stores").select("id, store_name, db_filename").order("id").execute()
        return (r.data or []) if hasattr(r, "data") else []
    except Exception:
        return []


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


def _get_supabase_user_allowed_stores(user_id: int):
    """Supabase app_user_stores + app_stores에서 접근 가능 매장 목록. [(store_id, db_filename, store_name), ...]"""
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


def _get_supabase_store_by_db_filename(db_filename: str):
    """db_filename으로 app_stores에서 store id 조회. 반환: id 또는 None."""
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


def _get_supabase_store_assigned_employee_names(db_filename: str) -> list:
    """해당 매장에 배정된 직원 표시명 목록 (Supabase). [name 또는 username, ...]"""
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
        out = []
        for uid in user_ids:
            r = client.table("app_users").select("name, username").eq("id", uid).maybe_single().execute()
            if r.data:
                display = (str(r.data.get("name") or "").strip() or str(r.data.get("username") or "").strip()) or None
                if display and display not in out:
                    out.append(display)
        return out
    except Exception:
        return []


def _get_supabase_users_list():
    """Supabase app_users 전체 목록. 반환: [{"id", "username", "email", "role", "name", "store_id"}, ...]"""
    client, err = get_supabase_client()
    if err or not client:
        return []
    try:
        r = client.table("app_users").select("id, username, email, role, name, store_id").order("username").execute()
        return (r.data or []) if hasattr(r, "data") else []
    except Exception:
        return []


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


def _get_supabase_employee_list_with_stores():
    """직원 명부용: app_users + 배정매장 이름 문자열. 반환: list of dict id, email, username, name, role, 배정매장."""
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
        out.append({
            "id": uid,
            "email": u.get("email"),
            "username": u.get("username"),
            "name": u.get("name"),
            "role": u.get("role"),
            "배정매장": 배정매장,
        })
    return out


def _supabase_insert_app_user(username: str, email: str, role: str, store_id, name: str):
    """app_users에 한 행 삽입 (비밀번호는 Supabase Auth에서 관리하므로 placeholder 저장). 반환: (user_id, None) 또는 (None, error_msg)."""
    client, err = get_supabase_client()
    if err or not client:
        return None, (err or "Supabase 연결 불가")
    try:
        pw_placeholder = hashlib.sha256("supabase_managed".encode()).hexdigest()
        row = {
            "username": username,
            "password": pw_placeholder,
            "email": email or None,
            "role": role,
            "store_id": store_id,
            "name": name or None,
        }
        r = client.table("app_users").insert(row).execute()
        data = (r.data or []) if hasattr(r, "data") else []
        if data and len(data) > 0 and data[0].get("id") is not None:
            return data[0]["id"], None
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
    """Supabase customers 테이블에서 id(기본키) 기준으로 고객명 조회. 중복 없이 단일 행만 반환."""
    client, err = get_supabase_client()
    if err or not customer_id:
        return ""
    try:
        q = client.table("customers").select("name").eq("id", int(customer_id))
        tc = _customers_tenant_column()
        if tc:
            q = q.eq(tc, db_filename)
        r = q.maybe_single().execute()
        row = r.data[0] if isinstance(r.data, list) and r.data else (r.data if isinstance(r.data, dict) else None)
        if row and row.get("name"):
            return (row["name"] or "").strip()
    except Exception:
        pass
    return ""


def _get_customers_by_ids_supabase(db_filename: str, customer_ids: list) -> dict:
    """Supabase에서 id 목록으로 고객 조회. 반환: { id: { name, phone1, phone2, address }, ... }"""
    if not customer_ids:
        return {}
    client, err = get_supabase_client()
    if err:
        return {}
    try:
        q = client.table("customers").select("id, name, phone1, phone2, address").in_("id", customer_ids)
        tc = _customers_tenant_column()
        if tc:
            q = q.eq(tc, db_filename)
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
) -> bool:
    """
    오프라인 결제 고객 정보를 채널톡에 PUSH (PUSH 전용).
    전화번호로 유저 조회 후 기존 태그 유지 + 새 태그 추가, 프로필 업데이트.
    태그 형식: store_tag_key가 있으면 '{매장키}구매/{품목}' (예: 삼산구매/옷장), 없으면 '품목_구매'.
    실패 시 False, 성공 시 True. 예외는 호출부에서 처리.
    """
    headers = _channel_talk_headers()
    if not headers or not phone_number or not str(phone_number).strip():
        return False
    member_id = re.sub(r"\D", "", str(phone_number).strip())
    if not member_id:
        return False
    category_clean = (re.sub(r"\s+", "", (item_category or "").strip()) or "기타")
    if store_tag_key and str(store_tag_key).strip():
        tag_new = f"{str(store_tag_key).strip()}구매/{category_clean}"
    else:
        tag_new = category_clean + "_구매"
    purchase_date_str = purchase_date.isoformat() if hasattr(purchase_date, "isoformat") else str(purchase_date)

    # 1) GET 기존 유저 (태그 병합용)
    existing_tags = []
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
    except Exception:
        pass

    # 2) 새 태그 중복 없이 추가
    if tag_new not in existing_tags:
        existing_tags.append(tag_new)
    tags_final = existing_tags

    # 3) 프로필 + 태그로 PATCH/PUT
    profile = {
        "name": (customer_name or "").strip() or "고객",
        "mobileNumber": (phone_number or "").strip(),
        "오프라인_최근구매액": int(purchase_amount),
        "오프라인_최근구매일": purchase_date_str[:10],
        "오프라인_구매품목": (item_category or "").strip() or "-",
    }
    body = {"profile": profile, "tags": tags_final}
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


def fetch_channel_talk_customers() -> list:
    """
    채널톡에서 유저 리스트를 GET으로 가져와 반환.
    각 항목은 mobileNumber, name 등 프로필 정보 포함.
    API 미지원 시 빈 리스트 반환.
    """
    """
    [현재 미사용] 채널톡 Open API에서는 /users 목록 GET이 405를 반환하므로
    대량 PULL 용도로는 사용할 수 없습니다. 단건 조회용 별도 헬퍼를 사용하세요.
    """
    st.info("채널톡 Open API에서 전체 고객 목록(/users) 조회는 지원되지 않아, 대량 PULL은 사용할 수 없습니다.")
    return []


def fetch_channel_talk_customer_by_phone(phone_number: str) -> dict | None:
    """
    전화번호(=memberId) 기준으로 채널톡 사용자 1명을 조회하는 현실적인 PULL 방식.
    - GET /open/v5/users/@{memberId} 사용
    - 성공 시 user dict, 실패/404 시 None 반환
    """
    headers = _channel_talk_headers()
    if not headers:
        st.error("채널톡 API 키가 설정되지 않았습니다. st.secrets 설정을 확인하세요.")
        return None
    if not phone_number or not str(phone_number).strip():
        st.error("조회할 전화번호를 입력하세요.")
        return None
    member_id = re.sub(r"\\D", "", str(phone_number).strip())
    if not member_id:
        st.error("전화번호 형식이 올바르지 않습니다.")
        return None
    try:
        r = requests.get(
            f"{CHANNEL_TALK_BASE_URL}/users/@{member_id}",
            headers=headers,
            timeout=10,
        )
        try:
            st.toast(f"채널톡 단건 조회 응답 코드: {r.status_code}")
        except Exception:
            pass
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
PAYMENT_METHOD_OPTIONS = ["신용카드", "체크카드", "지역화폐", "이체", "온누리"]
CARD_COMPANY_OPTIONS = ["신한카드", "삼성카드", "현대카드", "KB국민카드", "롯데카드", "하나카드", "NH농협카드", "우리카드", "IBK기업은행(BC)", "기타 BC카드"]


def _payment_fee_amount(payment_method: str, amount: int) -> float:
    """결제 수단별 수수료: 신용카드 2%, 체크카드 1.15%, 그 외 0%."""
    if not payment_method or amount <= 0:
        return 0.0
    if payment_method == "신용카드":
        return round(amount * 0.02, 0)
    if payment_method == "체크카드":
        return round(amount * 0.0115, 0)
    return 0.0

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

LOGO_FALLBACK_MSG = "에몬스 로고를 불러올 수 없습니다. (경로 확인 필요)"

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
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), store_name, alert_type, message)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def _get_store_name_by_db(db_filename: str) -> str:
    """current_db(db_filename)로 Stores에서 store_name 조회."""
    if not db_filename:
        return "알 수 없음"
    try:
        conn = get_master_conn()
        row = conn.execute("SELECT store_name FROM Stores WHERE db_filename = ?", (db_filename,)).fetchone()
        conn.close()
        return row[0] if row else db_filename
    except Exception:
        return db_filename or "알 수 없음"


def get_store_assigned_employee_names(db_filename: str) -> list[str]:
    """
    해당 매장(db_filename)에 배정된 직원(로그인 계정)의 표시명 목록.
    Supabase app_users/app_user_stores가 있으면 그쪽 우선, 없으면 Master DB.
    반환: [표시명, ...] (name 있으면 name, 없으면 username)
    """
    if not db_filename:
        return []
    if _supabase_app_tables_available():
        return _get_supabase_store_assigned_employee_names(db_filename)
    conn = get_master_conn()
    try:
        row = conn.execute("SELECT id FROM Stores WHERE db_filename = ?", (db_filename,)).fetchone()
        if not row:
            return []
        store_id = row[0]
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='UserStores'")
        if cur.fetchone() is not None:
            rows = conn.execute(
                """
                SELECT u.name, u.username
                FROM Users u
                JOIN UserStores us ON u.id = us.user_id
                WHERE us.store_id = ?
                ORDER BY u.name, u.username
                """,
                (store_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT name, username FROM Users WHERE store_id = ? ORDER BY name, username",
                (store_id,),
            ).fetchall()
        result = []
        for r in rows:
            display = (str(r[0] or "").strip() or str(r[1] or "").strip()) or None
            if display and display not in result:
                result.append(display)
        return result
    except Exception:
        return []
    finally:
        conn.close()


def _get_store_tag_key(store_name: str) -> str:
    """
    채널톡 태그용 매장 키 추출. 예: '울산삼산점' -> '삼산', '학성점' -> '학성'.
    st.secrets에 CHANNEL_TALK_STORE_TAG_KEYS = "삼산,학성,동구" 형태로 매장명 포함 시 사용할 키 목록 지정 가능.
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
    db_filename은 예: store_1.db (Master DB의 Stores.db_filename 값).
    로그인 성공 시 st.session_state['current_db']에 저장되며, 이후 모든 탭(매출 등록, 대시보드 등)의
    DB 쿼리는 master_system.db가 아닌 이 tenant DB 파일에만 연결되도록 매개변수화되어 있음.
    기존 DB 파일은 _ensure_tenant_schema로 card_company, fee_amount, actual_margin 컬럼을 안전하게 추가.
    """
    if not db_filename:
        return None
    path = os.path.join(DB_DIR, db_filename)
    if not os.path.exists(path):
        return None
    conn = sqlite3.connect(path)
    try:
        _ensure_tenant_schema(conn)
    except Exception:
        pass
    return conn


@st.cache_data(ttl=600)
def load_customers_cached(db_filename: str, limit: int | None = 50) -> pd.DataFrame:
    """고객 목록 캐시 로딩 (ttl=10분). Supabase customers 테이블, id 기준 조회. limit=None이면 전체, 50이면 최근 50건."""
    client, err = get_supabase_client()
    if err:
        if "supabase_error" not in st.session_state:
            st.session_state["supabase_error"] = err
        return pd.DataFrame()
    try:
        q = client.table("customers").select("id, name, phone1, phone2, address")
        tc = _customers_tenant_column()
        if tc:
            q = q.eq(tc, db_filename)
        q = q.order("id", desc=True)
        if limit:
            q = q.limit(limit)
        r = q.execute()
        if r.data and len(r.data) > 0:
            st.session_state.pop("supabase_error", None)
            return pd.DataFrame(r.data)
        return pd.DataFrame()
    except Exception as e:
        if "supabase_error" not in st.session_state:
            st.session_state["supabase_error"] = str(e)
        return pd.DataFrame()


@st.cache_data(ttl=600)
def load_sales_cached(db_filename: str, limit: int | None = None) -> pd.DataFrame:
    """Sales 테이블 캐시 로딩 (ttl=10분). Supabase sales 테이블, id 기준. limit=None이면 전체(대시보드 집계용)."""
    client, err = get_supabase_client()
    if err:
        if "supabase_error" not in st.session_state:
            st.session_state["supabase_error"] = err
        return pd.DataFrame(columns=["transaction_date", "amount"])
    try:
        q = client.table("sales").select("transaction_date, amount")
        tenant_col = _sales_tenant_column()
        if tenant_col:
            q = q.eq(tenant_col, db_filename)
        q = q.order("id", desc=True)
        if limit:
            q = q.limit(limit)
        r = q.execute()
        if r.data and len(r.data) > 0:
            st.session_state.pop("supabase_error", None)
            return pd.DataFrame(r.data)
        return pd.DataFrame(columns=["transaction_date", "amount"])
    except Exception as e:
        if "supabase_error" not in st.session_state:
            st.session_state["supabase_error"] = str(e)
        return pd.DataFrame(columns=["transaction_date", "amount"])


@st.cache_data(ttl=600)
def load_orders_cached(db_filename: str, order_col_list: str, limit: int | None = 50) -> pd.DataFrame:
    """Orders 목록 캐시 로딩 (ttl=10분). limit=None이면 전체(대시보드 등)."""
    conn = get_tenant_conn(db_filename)
    if not conn:
        return pd.DataFrame()
    try:
        if limit:
            return pd.read_sql(
                f"SELECT {order_col_list} FROM Orders ORDER BY id DESC LIMIT ?",
                conn, params=(limit,)
            )
        return pd.read_sql(f"SELECT {order_col_list} FROM Orders", conn)
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


@st.cache_data(ttl=600)
def load_payments_cached(db_filename: str) -> pd.DataFrame:
    """Payments 캐시 로딩 (ttl=10분)."""
    conn = get_tenant_conn(db_filename)
    if not conn:
        return pd.DataFrame()
    try:
        return pd.read_sql("SELECT order_id, amount, fee_amount FROM Payments", conn)
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


@st.cache_data(ttl=600)
def load_todos_cached(db_filename: str, limit: int = 100) -> pd.DataFrame:
    """Todos 캐시 로딩 (ttl=10분)."""
    conn = get_tenant_conn(db_filename)
    if not conn:
        return pd.DataFrame()
    try:
        return pd.read_sql(
            "SELECT id, created_date, author, content, is_completed FROM Todos ORDER BY id DESC LIMIT ?",
            conn, params=(limit,)
        )
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def clear_data_cache():
    """저장 후 캐시 무효화로 다음 로딩 시 최신 데이터 반영."""
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
    if "onnuri_approval_code" not in cols:
        conn.execute("ALTER TABLE Payments ADD COLUMN onnuri_approval_code TEXT")
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


def _insert_sales_transaction(db_filename: str, order_id: int, transaction_date: str, amount: float, note: str = "", unpaid_balance: float | None = None):
    """Sales 테이블에 매출 트랜잭션 1건 INSERT (Supabase). order_id, amount, transaction_date, note, created_at 저장. unpaid_balance(미수금)는 Supabase sales.unpaid_balance 컬럼에 저장(해당 컬럼 없으면 제외 후 재시도)."""
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
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        tenant_col = _sales_tenant_column()
        if tenant_col:
            payload[tenant_col] = db_filename
        # 미수금: Supabase sales.unpaid_balance 컬럼에 저장 (판매가 - 수납액, 0이면 완납)
        if unpaid_balance is not None:
            payload["unpaid_balance"] = round(float(unpaid_balance), 2)
        try:
            client.table("sales").insert(payload).execute()
        except Exception as e1:
            # unpaid_balance 컬럼이 없는 구 Supabase 스키마면 해당 필드 제외 후 재시도
            err_str = str(e1).lower()
            if unpaid_balance is not None and ("unpaid_balance" in err_str or "42703" in err_str or "does not exist" in err_str):
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
AUTH_SECRET = os.environ.get("EMONS_AUTH_SECRET", "emons-default-secret-change-in-production")
AUTH_EXPIRY_DAYS = 7
AUTH_SESSION_SECONDS = 3600  # 로그인 후 1시간 동안만 세션 유지, 연속 새로고침 시에도 복구


def _current_username() -> str:
    """세션에서 현재 사용자 ID(username) 가져오기 (없으면 'unknown')."""
    user = st.session_state.get("current_user") or {}
    return user.get("username") or "unknown"


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
            datetime.now().isoformat(),
            actor,
            entity_type,
            int(entity_id),
            field_name,
            "" if old_value is None else str(old_value),
            "" if new_value is None else str(new_value),
            reason.strip(),
        ),
    )


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
            datetime.now().isoformat(),
        ),
    )


def _insert_payment_history(
    conn: sqlite3.Connection,
    sale_id: int,
    customer_name: str,
    action_type: str,
    old_payment_data,
    new_payment_data,
    reason: str,
    receipt_image_path: str | None = None,
) -> None:
    """결제 변경 이력(PaymentHistory) 1건 기록. 영수증 이미지 경로는 선택 저장."""
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
            json.dumps(old_payment_data or {}, ensure_ascii=False),
            json.dumps(new_payment_data or {}, ensure_ascii=False),
            reason.strip(),
            _current_username(),
            datetime.now().isoformat(),
            receipt_image_path or None,
        ),
    )


def _render_order_audit_trail(db_filename: str, order_id: int):
    """주문(Order) 기준 변경 이력(AuditLogs) + 관련 결제 영수증 표시 공통 UI."""
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
    # 관련 결제 영수증 조회
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
    """매장 관리자/최고 관리자용 결제 변경/취소 모니터링 화면."""
    db_filename = st.session_state.get("current_db")
    if not db_filename:
        st.warning("매장에 로그인한 후 이용하세요.")
        return
    conn = get_tenant_conn(db_filename)
    if not conn:
        st.error("매장 DB를 찾을 수 없습니다.")
        return

    st.header("🚨 결제 변경/취소 모니터링")

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        search_name = st.text_input("고객명 검색")
    with col2:
        start_date = st.date_input("시작일", value=date.today() - timedelta(days=7), key="ph_start")
    with col3:
        end_date = st.date_input("종료일", value=date.today(), key="ph_end")
    with col4:
        action_filter = st.multiselect("작업 유형", ["잔금결제", "결제취소", "재결제", "금액변경"], default=[])
    user_filter = st.text_input("작업자(직원 ID) 필터", key="ph_user_filter")

    try:
        # sale_id(주문 id)로 Orders만 조인해 customer_id 확보. 고객명은 ph.customer_name 또는 Supabase에서 id 기준 조회
        df = pd.read_sql(
            """
            SELECT ph.log_id, ph.sale_id, ph.customer_name,
                   o.customer_id,
                   ph.action_type, ph.old_payment_data, ph.new_payment_data,
                   ph.reason, ph.changed_by, ph.changed_at, ph.receipt_image_path
            FROM PaymentHistory ph
            LEFT JOIN Orders o ON o.id = ph.sale_id
            ORDER BY ph.changed_at DESC
            """,
            conn,
        )
    except Exception:
        df = pd.DataFrame()
    finally:
        conn.close()

    if not df.empty and "customer_id" in df.columns:
        # 비어 있는 customer_name은 Supabase에서 id 기준으로 채움
        need_name = df["customer_name"].fillna("").str.strip() == ""
        for idx in df.index[need_name]:
            cid = df.at[idx, "customer_id"]
            if pd.notna(cid) and int(cid):
                df.at[idx, "customer_name"] = _get_customer_name_supabase(db_filename, int(cid))
        df["customer_name"] = df["customer_name"].fillna("")

    if df.empty:
        st.info("결제 변경/취소 이력이 없습니다.")
        return

    df["changed_at_dt"] = pd.to_datetime(df["changed_at"], errors="coerce")
    mask = pd.Series(True, index=df.index)

    if search_name:
        mask &= df["customer_name"].fillna("").str.contains(search_name.strip(), case=False)
    if start_date:
        mask &= df["changed_at_dt"].dt.date >= start_date
    if end_date:
        mask &= df["changed_at_dt"].dt.date <= end_date
    if action_filter:
        mask &= df["action_type"].isin(action_filter)
    if user_filter:
        mask &= df["changed_by"].fillna("").str.contains(user_filter.strip(), case=False)

    df_f = df.loc[mask].copy()
    df_f = df_f.drop(columns=["changed_at_dt"])

    def _highlight_cancel(row):
        if row["action_type"] == "결제취소":
            return ["background-color: #ffe6e6; color: #b30000;"] * len(row)
        return ["" for _ in row]

    # 표시용 DataFrame에서는 receipt_image_path 제외 (테이블에는 경로만 보이면 됨)
    display_cols = [c for c in df_f.columns if c != "receipt_image_path"]
    st.dataframe(df_f[display_cols].style.apply(_highlight_cancel, axis=1), use_container_width=True)

    # 영수증 이미지가 있는 이력만 아코디언으로 표시
    has_receipts = "receipt_image_path" in df_f.columns and (df_f["receipt_image_path"].notna() & (df_f["receipt_image_path"].astype(str).str.strip() != "")).any()
    if has_receipts:
        with st.expander("📷 결제 변경 이력 상세 (영수증 이미지)"):
            for _, row in df_f.iterrows():
                receipt_path = row.get("receipt_image_path") if isinstance(row.get("receipt_image_path"), str) and (row.get("receipt_image_path") or "").strip() else None
                if not receipt_path:
                    continue
                if os.path.exists(receipt_path):
                    with st.expander(f"{row.get('customer_name', '-')} | {row.get('changed_at', '')} | {row.get('action_type', '')}"):
                        st.caption(row.get("reason", ""))
                        st.image(receipt_path, caption="첨부 영수증", use_column_width=True)
                else:
                    with st.expander(f"{row.get('customer_name', '-')} | {row.get('changed_at', '')} | {row.get('action_type', '')}"):
                        st.caption(row.get("reason", ""))
                        st.warning("영수증 파일을 찾을 수 없습니다.")


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
    sig = hmac.new(AUTH_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def _verify_auth_token(token: str) -> dict | None:
    """토큰 검증 + 로그인 후 1시간 이내인지 확인. 통과 시 유저 정보 dict, 아니면 None."""
    if not token or "." not in token:
        return None
    try:
        payload_b64, sig = token.rsplit(".", 1)
        expected = hmac.new(AUTH_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
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
# ADDRESS_API_KEY(공공) 또는 KAKAO_REST_KEY(카카오) 환경변수로 지정. 없으면 수동 입력만 가능.

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
    # 환경변수 미설정 시 아래 REST 키 사용 (카카오 개발자 콘솔 REST API 키)
    KAKAO_REST_KEY_DEFAULT = "19911112315a182013d5ac8592852019"
    key = api_key or os.environ.get("KAKAO_REST_KEY", KAKAO_REST_KEY_DEFAULT)
    if not key:
        return [], "KAKAO_REST_KEY를 설정해 주세요."
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
    KAKAO_REST_KEY_DEFAULT = "19911112315a182013d5ac8592852019"
    key = api_key or os.environ.get("KAKAO_REST_KEY", KAKAO_REST_KEY_DEFAULT)
    if not key:
        return [], "KAKAO_REST_KEY를 설정해 주세요."
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
    KAKAO_REST_KEY_DEFAULT = "19911112315a182013d5ac8592852019"
    key = os.environ.get("KAKAO_REST_KEY", KAKAO_REST_KEY_DEFAULT)
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
    st.title("에몬스판매관리 프로그램")
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
                                    store_id, db_filename = allowed_stores[0][0], allowed_stores[0][1]
                                st.session_state.logged_in = True
                                st.session_state.current_user = {
                                    "id": user_id, "username": uname, "role": role,
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
    """로그인 사용자에 따른 사이드바용 매장명. superadmin이면 본사, 아니면 Stores에서 조회."""
    if user.get("role") == "superadmin":
        return "🏢 에몬스울산본점"
    store_id = user.get("store_id")
    if not store_id:
        return "🏢 매장"
    conn = get_master_conn()
    try:
        row = conn.execute("SELECT store_name FROM Stores WHERE id = ?", (store_id,)).fetchone()
        return f"🏢 {row[0]}" if row else "🏢 매장"
    finally:
        conn.close()


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
            st.plotly_chart(fig1, use_container_width=True)

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
            st.plotly_chart(fig2, use_container_width=True)

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
            st.plotly_chart(fig3, use_container_width=True)

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
            st.plotly_chart(fig4, use_container_width=True)


# 지도 기본 중심/줌 (한국)
_MAP_CENTER = (36.5, 127.5)
_MAP_ZOOM = 7


def _build_map_data_with_geocoding(merged: pd.DataFrame) -> pd.DataFrame:
    """merged(orders+customers)에서 주소 지오코딩 후 latitude, longitude, address, building_name, 고객명, 품목, 금액, 배송일자 포함 DataFrame 반환."""
    if "address" not in merged.columns or "name" not in merged.columns:
        return pd.DataFrame()
    if "geo_cache" not in st.session_state:
        st.session_state.geo_cache = {}
    cache = st.session_state.geo_cache
    rows = []
    for _, row in merged.iterrows():
        addr = (row.get("address") or "").strip()
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
        addr = (r.get("address") or "").strip()
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
                md1 = _build_map_data_with_geocoding(df1).dropna(subset=["latitude", "longitude"])
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
                md2 = _build_map_data_with_geocoding(df2).dropna(subset=["latitude", "longitude"])
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
            st.plotly_chart(fig_a1, use_container_width=True, key=f"{key_prefix}_visit_route_a")
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
            st.plotly_chart(fig_b1, use_container_width=True, key=f"{key_prefix}_visit_route_b")

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
            st.plotly_chart(fig_a2, use_container_width=True, key=f"{key_prefix}_purchase_reason_a")
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
            st.plotly_chart(fig_b2, use_container_width=True, key=f"{key_prefix}_purchase_reason_b")

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
            st.plotly_chart(fig_a3, use_container_width=True, key=f"{key_prefix}_category_top10_a")
            st.dataframe(cat_a[["순위", "품목", "판매건수"]], use_container_width=True, key=f"{key_prefix}_category_df_a", height=min(280, 50 + len(cat_a) * 32))
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
            st.plotly_chart(fig_b3, use_container_width=True, key=f"{key_prefix}_category_top10_b")
            st.dataframe(cat_b[["순위", "품목", "판매건수"]], use_container_width=True, key=f"{key_prefix}_category_df_b", height=min(280, 50 + len(cat_b) * 32))

    # ---------- ④ 지역별 매출 분포 지도 (Folium 좌우 비교) ----------
    st.subheader("④ 지역별 매출 분포 지도")
    c1, c2 = st.columns(2)
    with c1:
        st.caption(f"기간 A: {label_a}")
        _render_single_period_folium_map(df_period_a, label_a, f"{key_prefix}_map_a")
    with c2:
        st.caption(f"기간 B: {label_b}")
        _render_single_period_folium_map(df_period_b, label_b, f"{key_prefix}_map_b")


def render_marketing_insights_tenant():
    """매장(Tenant): 해당 매장 데이터로 다중 기간 교차 분석 (기간 A vs 기간 B)."""
    db_filename = st.session_state.get("current_db")
    if not db_filename:
        st.warning("매장에 로그인한 후 이용하세요.")
        return
    if not get_tenant_conn(db_filename):
        st.error("매장 DB를 찾을 수 없습니다.")
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

    today = date.today()
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
    map_data = map_data.dropna(subset=["latitude", "longitude"])
    if map_data.empty:
        st.info("지오코딩 가능한 주소가 없습니다.")
        return
    m = _create_folium_map(map_data, _MAP_CENTER, _MAP_ZOOM, key_prefix)
    if m:
        st_folium(m, returned_objects=[], use_container_width=True, key=f"{key_prefix}_single_map")


def render_marketing_insights_superadmin():
    """최고 관리자: 다중 기간 비교 대시보드 (Comparative Analytics Dashboard)."""
    conn_m = get_master_conn()
    try:
        stores = pd.read_sql("SELECT id, store_name, db_filename FROM Stores ORDER BY id", conn_m)
    finally:
        conn_m.close()
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

    today = date.today()
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
    conn_m = get_master_conn()
    try:
        stores = pd.read_sql("SELECT id, store_name, db_filename FROM Stores ORDER BY id", conn_m)
    finally:
        conn_m.close()
    if len(stores) == 0:
        st.info("등록된 매장이 없습니다.")
        return
    today = date.today()
    month_start = today.replace(day=1)
    from calendar import monthrange
    month_end = date(today.year, today.month, monthrange(today.year, today.month)[1])
    all_orders = []
    all_payments = []
    store_orders = {}
    store_payments = {}
    for _, s in stores.iterrows():
        db_fn = s["db_filename"]
        conn = get_tenant_conn(db_fn)
        if not conn:
            continue
        try:
            try:
                orders = pd.read_sql(
                    "SELECT id, order_date, total_amount, actual_margin FROM Orders",
                    conn
                )
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
        pay_sum = payments.groupby("order_id")["amount"].sum() if len(payments) > 0 else pd.Series(dtype=float)
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
    st.dataframe(rank_display, use_container_width=True)


def _superadmin_tab2_hr_store_employees():
    """② 매장별 직원 평가 현황 (HR): 매장·연월 선택 후 100점 만점 KPI 표."""
    conn_m = get_master_conn()
    try:
        stores = pd.read_sql("SELECT id, store_name, db_filename FROM Stores ORDER BY id", conn_m)
    finally:
        conn_m.close()
    if len(stores) == 0:
        st.info("등록된 매장이 없습니다.")
        return
    store_options = stores["store_name"].tolist()
    selected_store = st.selectbox("매장 선택", store_options, key="sa_hr_store")
    if not selected_store:
        return
    db_fn = stores[stores["store_name"] == selected_store].iloc[0]["db_filename"]
    conn = get_tenant_conn(db_fn)
    if not conn:
        st.error("해당 매장 DB를 열 수 없습니다.")
        return
    try:
        cur = conn.execute("PRAGMA table_info(Orders)")
        cols = [r[1] for r in cur.fetchall()]
        order_list = "id, order_date, total_amount, actual_margin, employee_names"
        if "display_sales_amount" in cols:
            order_list += ", display_sales_amount"
        orders = pd.read_sql(f"SELECT {order_list} FROM Orders", conn)
    finally:
        conn.close()
    if "display_sales_amount" not in orders.columns:
        orders["display_sales_amount"] = 0
    orders["display_sales_amount"] = orders["display_sales_amount"].fillna(0).astype(int)
    orders["actual_margin"] = orders["actual_margin"].fillna(0)
    orders["order_date"] = pd.to_datetime(orders["order_date"], errors="coerce")
    order_dates = orders["order_date"].dropna()
    if len(order_dates) == 0:
        st.info("해당 매장에 주문 데이터가 없습니다.")
        return
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
    months_options = months_options[::-1]
    month_labels = [f"{y}년 {m}월" for y, m in months_options]
    sel_idx = st.selectbox("연/월 선택", range(len(month_labels)), format_func=lambda i: month_labels[i], key="sa_hr_month")
    sel_y, sel_m = months_options[sel_idx]
    month_start = date(sel_y, sel_m, 1)
    from calendar import monthrange
    month_end = date(sel_y, sel_m, monthrange(sel_y, sel_m)[1])
    orders_m = orders[(orders["order_date"].dt.date >= month_start) & (orders["order_date"].dt.date <= month_end)].copy()
    rows = []
    for _, r in orders_m.iterrows():
        emps = [e.strip() for e in (r.get("employee_names") or "").split(",") if e.strip()]
        n = len(emps) if emps else 1
        if not emps:
            continue
        amt = float(r.get("total_amount") or 0)
        margin = float(r.get("actual_margin") or 0)
        display_amt = float(r.get("display_sales_amount") or 0)
        per_amt = amt / n
        per_margin = margin / n
        per_display = (display_amt / n) if n else 0
        for e in emps:
            rows.append({"employee": e, "sales": per_amt, "margin": per_margin, "display_sales": per_display})
    if not rows:
        st.info("선택한 월에 직원이 배정된 주문이 없습니다.")
        return
    emp_df = pd.DataFrame(rows).groupby("employee", as_index=False).agg({"sales": "sum", "margin": "sum", "display_sales": "sum"})
    total_sales = emp_df["sales"].sum() or 0
    total_margin = emp_df["margin"].sum() or 0
    total_display = emp_df["display_sales"].sum() or 0
    emp_df["매출 점수(80)"] = (emp_df["sales"] / total_sales * 80).round(1) if total_sales else 0.0
    emp_df["마진 점수(10)"] = (emp_df["margin"] / total_margin * 10).round(1) if total_margin else 0.0
    emp_df["전시품 점수(10)"] = (emp_df["display_sales"] / total_display * 10).round(1) if total_display else 0.0
    emp_df["종합 점수"] = (emp_df["매출 점수(80)"] + emp_df["마진 점수(10)"] + emp_df["전시품 점수(10)"]).round(1)
    emp_df = emp_df.sort_values("종합 점수", ascending=False).reset_index(drop=True)
    emp_df["총 판매액"] = emp_df["sales"].round(0).astype(int)
    emp_df["마진액"] = emp_df["margin"].round(0).astype(int)
    emp_df["전시품 판매액"] = emp_df["display_sales"].round(0).astype(int)
    display_df = emp_df[["employee", "총 판매액", "마진액", "전시품 판매액", "매출 점수(80)", "마진 점수(10)", "전시품 점수(10)", "종합 점수"]].rename(columns={"employee": "직원명"})
    display_fmt = _format_df_display(display_df, ["총 판매액", "마진액", "전시품 판매액"])
    st.dataframe(display_fmt, use_container_width=True)


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
                    (title.strip(), content.strip(), (external_link.strip() or None), content.strip(), datetime.now().isoformat())
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
    고객명, 연락처, 품목, 총판매금액, 결제금액, 미수금, 결제수단, 온누리승인번호, 판매일자, 배송일자, 매장명, 판매담당자, 특이사항 등 전체 컬럼 포함."""
    conn_m = get_master_conn()
    try:
        stores = pd.read_sql("SELECT id, store_name, db_filename FROM Stores ORDER BY id", conn_m)
    finally:
        conn_m.close()
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
        if not conn:
            continue
        try:
            # Orders만 조회. 고객 정보는 Supabase에서 id 기준으로 채움
            merged = pd.read_sql("""
                SELECT o.id, o.customer_id, o.order_date, o.delivery_date,
                       o.total_amount, o.cost_price, o.actual_margin, o.employee_names,
                       o.category, o.visit_reason, o.purchase_reason,
                       COALESCE(o.display_sales_amount, 0) as display_sales_amount,
                       COALESCE(o.display_cost_amount, 0) as display_cost_amount,
                       o.balance_status
                FROM Orders o
            """, conn)
            customer_ids = merged["customer_id"].dropna().astype(int).unique().tolist()
            cust_map = _get_customers_by_ids_supabase(s["db_filename"], customer_ids) if customer_ids else {}
            merged["customer_name"] = merged["customer_id"].map(lambda cid: (cust_map.get(int(cid)) or {}).get("name", "") if pd.notna(cid) else "")
            merged["phone1"] = merged["customer_id"].map(lambda cid: (cust_map.get(int(cid)) or {}).get("phone1", "") if pd.notna(cid) else "")
            merged["phone2"] = merged["customer_id"].map(lambda cid: (cust_map.get(int(cid)) or {}).get("phone2", "") if pd.notna(cid) else "")
            merged["address"] = merged["customer_id"].map(lambda cid: (cust_map.get(int(cid)) or {}).get("address", "") if pd.notna(cid) else "")
            payments = pd.read_sql(
                "SELECT order_id, amount, payment_method, onnuri_approval_code, card_company FROM Payments",
                conn,
            )
        except Exception:
            conn.close()
            continue
        conn.close()
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
    csv_str = out_df.to_csv(index=False)
    try:
        csv_content = csv_str.encode("cp949")
    except (UnicodeEncodeError, LookupError):
        csv_content = csv_str.encode("utf-8-sig")
    st.download_button(
        "CSV 다운로드",
        data=csv_content,
        file_name=f"매출백업_{backup_start}_{backup_end}.csv",
        mime="text/csv",
        key="backup_dl"
    )


def _superadmin_tab_unpaid_report():
    """미수금(잔금) 전용 레포트: 기간 필터, 잔금 > 0 필터, 다운로드."""
    conn_m = get_master_conn()
    try:
        stores = pd.read_sql("SELECT id, store_name, db_filename FROM Stores ORDER BY id", conn_m)
    finally:
        conn_m.close()
    if len(stores) == 0:
        st.info("등록된 매장이 없습니다.")
        return
    st.subheader("미수금(잔금) 레포트")
    col1, col2 = st.columns(2)
    with col1:
        report_start = st.date_input("조회 시작일", value=date.today() - timedelta(days=30), key="unpaid_report_start")
    with col2:
        report_end = st.date_input("조회 종료일", value=date.today(), key="unpaid_report_end")
    rows = []
    for _, s in stores.iterrows():
        conn = get_tenant_conn(s["db_filename"])
        if not conn:
            continue
        try:
            merged = pd.read_sql("""
                SELECT o.id, o.customer_id, o.order_date, o.delivery_date, o.total_amount, o.employee_names,
                       COALESCE(o.display_sales_amount, 0) as display_sales_amount
                FROM Orders o
            """, conn)
            customer_ids = merged["customer_id"].dropna().astype(int).unique().tolist()
            cust_map = _get_customers_by_ids_supabase(s["db_filename"], customer_ids) if customer_ids else {}
            merged["customer_name"] = merged["customer_id"].map(lambda cid: (cust_map.get(int(cid)) or {}).get("name", "") if pd.notna(cid) else "")
            merged["phone1"] = merged["customer_id"].map(lambda cid: (cust_map.get(int(cid)) or {}).get("phone1", "") if pd.notna(cid) else "")
            payments = pd.read_sql("SELECT order_id, SUM(amount) as paid FROM Payments GROUP BY order_id", conn)
        except Exception:
            conn.close()
            continue
        conn.close()
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
    st.dataframe(display_df, use_container_width=True)
    csv_str = out_df.to_csv(index=False)
    try:
        csv_content = csv_str.encode("cp949")
    except (UnicodeEncodeError, LookupError):
        csv_content = csv_str.encode("utf-8-sig")
    st.download_button(
        "미수금 레포트 CSV 다운로드",
        data=csv_content,
        file_name=f"미수금레포트_{report_start}_{report_end}.csv",
        mime="text/csv",
        key="unpaid_report_dl",
    )


def _superadmin_tab5_store_accounts():
    """⑤ 매장 계정 관리: 신규 매장/계정 발급, 비밀번호 변경, 매장 삭제(이중 확인)."""
    conn_m = get_master_conn()
    try:
        stores = pd.read_sql("SELECT id, store_name, db_filename FROM Stores ORDER BY id", conn_m)
        users = pd.read_sql("SELECT id, username, role, store_id FROM Users WHERE store_id IS NOT NULL", conn_m)
    finally:
        conn_m.close()
    st.subheader("신규 매장 생성")
    with st.form("new_store_form"):
        store_name = st.text_input("매장명")
        submitted = st.form_submit_button("매장 생성")
        if submitted and store_name and store_name.strip():
            try:
                max_id = stores["id"].max() if len(stores) else 0
                new_id = int(max_id) + 1 if pd.notna(max_id) else 1
                db_filename = f"store_{new_id}.db"
                conn_m = get_master_conn()
                conn_m.execute("INSERT INTO Stores (store_name, db_filename) VALUES (?, ?)", (store_name.strip(), db_filename))
                conn_m.commit()
                create_tenant_db(db_filename)
                conn_m.close()
                st.success(f"매장 '{store_name}'이(가) 생성되었습니다. DB: {db_filename}")
                st.rerun()
            except sqlite3.IntegrityError:
                st.error("이미 존재하는 매장명이거나 DB 파일명입니다.")
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
                        conn_m = get_master_conn()
                        conn_m.execute(
                            "INSERT INTO Users (username, password, role, store_id) VALUES (?, ?, ?, ?)",
                            (new_username.strip(), pw_hash, new_role, store_id)
                        )
                        conn_m.commit()
                        conn_m.close()
                        st.success("계정이 생성되었습니다.")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("이미 존재하는 사용자명입니다.")
                else:
                    st.warning("사용자명과 비밀번호를 입력하세요.")
    st.subheader("매장 조회/수정")
    if len(stores) == 0:
        st.info("매장이 없습니다.")
        return
    store_options = stores["store_name"].tolist()
    selected_store_name = st.selectbox("매장 선택 (조회·수정)", store_options, key="sa_edit_store_sel")
    if selected_store_name:
        s = stores[stores["store_name"] == selected_store_name].iloc[0]
        store_users = users[users["store_id"] == s["id"]] if len(users) > 0 else pd.DataFrame()
        with st.expander("📋 매장 정보 수정", expanded=True):
            with st.form("store_edit_form"):
                edit_name = st.text_input("매장명", value=s["store_name"], key="sa_edit_name")
                edit_db = st.text_input("DB 파일명", value=s["db_filename"], key="sa_edit_db")
                if st.form_submit_button("저장"):
                    if edit_name and edit_name.strip() and edit_db and edit_db.strip():
                        try:
                            conn_m = get_master_conn()
                            conn_m.execute("UPDATE Stores SET store_name = ?, db_filename = ? WHERE id = ?", (edit_name.strip(), edit_db.strip(), s["id"]))
                            conn_m.commit()
                            conn_m.close()
                            st.success("저장되었습니다.")
                            st.rerun()
                        except sqlite3.IntegrityError:
                            st.error("매장명 또는 DB 파일명이 이미 사용 중입니다.")
                    else:
                        st.warning("매장명과 DB 파일명을 입력하세요.")
        st.caption("계정(ID) 조회 및 비밀번호 변경")
        for _, u in store_users.iterrows():
            with st.form(f"pw_{u['id']}"):
                st.text_input("현재 ID (조회용)", value=u["username"], disabled=True, key=f"disp_id_{u['id']}")
                new_pw = st.text_input("새 비밀번호 (변경 시만 입력)", type="password", key=f"pw_input_{u['id']}")
                if st.form_submit_button("비밀번호 변경"):
                    if new_pw:
                        conn_m = get_master_conn()
                        conn_m.execute("UPDATE Users SET password = ? WHERE id = ?", (hashlib.sha256(new_pw.encode()).hexdigest(), u["id"]))
                        conn_m.commit()
                        conn_m.close()
                        st.success("변경되었습니다.")
                        st.rerun()
                    else:
                        st.warning("새 비밀번호를 입력하세요.")
    st.subheader("매장 삭제 (이중 확인)")
    if len(stores) > 0:
        del_store_name = st.selectbox("삭제할 매장 선택", store_options, key="sa_del_store_sel")
        if del_store_name:
            s = stores[stores["store_name"] == del_store_name].iloc[0]
            st.warning("매장 삭제 시 해당 매장 계정과 매장 데이터가 삭제됩니다. 복구할 수 없습니다.")
            confirm = st.checkbox(f"'{s['store_name']}' 매장 삭제에 동의합니다.", key="del_confirm_final")
            if st.button("매장 삭제", key="del_btn_final"):
                if not confirm:
                    st.error("위 체크박스를 선택한 후 삭제할 수 있습니다.")
                else:
                    conn_m = get_master_conn()
                    conn_m.execute("DELETE FROM Users WHERE store_id = ?", (s["id"],))
                    conn_m.execute("DELETE FROM Stores WHERE id = ?", (s["id"],))
                    conn_m.commit()
                    conn_m.close()
                    db_path = os.path.join(DB_DIR, s["db_filename"])
                    if os.path.exists(db_path):
                        try:
                            os.remove(db_path)
                        except Exception:
                            pass
                    st.success("매장이 삭제되었습니다.")
                    st.rerun()


def render_monthly_payment_report(is_superadmin: bool):
    """월별 결제수단 집계표. Superadmin: 매장 선택 가능 / Store_admin: 소속 매장 고정.
    조회 방식: 월별/연도별 | 직접 날짜 지정"""
    today = date.today()
    query_mode = st.radio(
        "조회 방식",
        ["월별/연도별 조회", "직접 날짜 지정"],
        horizontal=True,
        key="payment_report_mode",
    )
    if query_mode == "월별/연도별 조회":
        report_year = st.selectbox(
            "조회 연도",
            list(range(today.year, today.year - 6, -1)),
            key="payment_report_year",
        )
        date_range_start = date(report_year, 1, 1)
        date_range_end = date(report_year, 12, 31)
    else:
        col_s, col_e = st.columns(2)
        with col_s:
            date_range_start = st.date_input("시작일", value=today - timedelta(days=30), key="payment_report_start")
        with col_e:
            date_range_end = st.date_input("종료일", value=today, key="payment_report_end")
        if date_range_start and date_range_end and date_range_start > date_range_end:
            st.warning("시작일이 종료일보다 늦습니다. 기간을 확인해 주세요.")
            return

    if is_superadmin:
        conn_m = get_master_conn()
        try:
            stores = pd.read_sql("SELECT id, store_name, db_filename FROM Stores ORDER BY id", conn_m)
        finally:
            conn_m.close()
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
        conn_m = get_master_conn()
        try:
            row = conn_m.execute(
                "SELECT store_name FROM Stores WHERE db_filename = ?",
                (db_filename,),
            ).fetchone()
            selected_store = row[0] if row else "매장"
        finally:
            conn_m.close()

    all_payments = []
    if is_superadmin and selected_store == "전체 매장 통합":
        for _, s in stores.iterrows():
            conn = get_tenant_conn(s["db_filename"])
            if not conn:
                continue
            try:
                df = pd.read_sql(
                    "SELECT payment_date, payment_method, card_company, amount FROM Payments WHERE payment_date IS NOT NULL AND payment_date != ''",
                    conn,
                )
                df["_store"] = s["store_name"]
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
        conn = get_tenant_conn(db_fn)
        if not conn:
            st.error("매장 DB를 찾을 수 없습니다.")
            return
        try:
            pay_df = pd.read_sql(
                "SELECT payment_date, payment_method, card_company, amount FROM Payments WHERE payment_date IS NOT NULL AND payment_date != ''",
                conn,
            )
        except Exception:
            st.error("결제 데이터를 불러올 수 없습니다.")
            conn.close()
            return
        conn.close()

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

    # 상세 결제수단(파생 컬럼): 신용/체크카드 → 카드사별 분리 (신용_신한, 체크_국민 등)
    # Payments.card_company 컬럼 사용 (없으면 _ensure_tenant_schema에서 ALTER TABLE로 추가됨)
    _card_short = {"신한카드": "신한", "삼성카드": "삼성", "현대카드": "현대", "KB국민카드": "국민",
                   "롯데카드": "롯데", "하나카드": "하나", "NH농협카드": "농협", "우리카드": "우리",
                   "IBK기업은행(BC)": "기업", "기타 BC카드": "기타"}
    def _to_detailed(row):
        meth = row["payment_method"] or "미지정"
        if meth in ("신용카드", "체크카드"):
            cc = row.get("card_company") or ""
            short = _card_short.get(cc, cc or "미지정")
            prefix = "신용" if meth == "신용카드" else "체크"
            return f"{prefix}_{short}"
        return meth
    pay_df["detailed_payment"] = pay_df.apply(_to_detailed, axis=1)

    if len(pay_df) == 0:
        st.info("선택한 기간에 결제 데이터가 없습니다.")
        return

    index_col = "결제월" if query_mode == "월별/연도별 조회" else "결제일자"
    total_label = "월별 총 결제액(Total)" if query_mode == "월별/연도별 조회" else "일별 총 결제액(Total)"
    pivot = pay_df.pivot_table(
        index=index_col,
        columns="detailed_payment",
        values="amount",
        aggfunc="sum",
        fill_value=0,
        margins=False,
    )
    pivot = pivot.fillna(0)
    # 컬럼 정렬: 신용 그룹 → 체크 그룹 → 이체/계좌이체 → 지역화폐 → 온누리 → 현금 → 기타
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
    st.dataframe(display_df, use_container_width=True)

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        pivot.to_excel(writer, sheet_name="결제수단집계")
    buf.seek(0)
    store_label = "전체매장" if (is_superadmin and selected_store == "전체 매장 통합") else selected_store.replace(" ", "_")
    if query_mode == "월별/연도별 조회":
        file_name = f"결제수단집계_{store_label}_{date_range_start.year}년.xlsx"
    else:
        file_name = f"결제수단집계_{store_label}_{date_range_start.isoformat()}_{date_range_end.isoformat()}.xlsx"
    st.download_button(
        "엑셀 다운로드",
        data=buf.getvalue(),
        file_name=file_name,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="monthly_payment_report_dl",
    )


# ---------- 직원 계정 관리 및 발령 (superadmin 전용) ----------
EMPLOYEE_STORE_OPTIONS = ["삼산점", "학성점", "양산점", "본사"]
EMPLOYEE_ROLE_OPTIONS = [
    ("user", "일반 직원 (user)"),
    ("store_admin", "매장 관리자 (store admin)"),
    ("superadmin", "최고 관리자 (superadmin)"),
]


def _get_store_id_by_display_name(display_name: str):
    """배정 매장 표시명(삼산점, 학성점, 양산점, 본사) → Stores.id. 본사는 NULL. 정확 일치 후 LIKE(울산삼산점 등) 매칭."""
    if not display_name or str(display_name).strip() == "본사":
        return None
    n = str(display_name).strip()
    conn = get_master_conn()
    try:
        row = conn.execute("SELECT id FROM Stores WHERE TRIM(store_name) = ?", (n,)).fetchone()
        if row:
            return row[0]
        keyword = n.replace("점", "").strip()
        if keyword:
            row = conn.execute(
                "SELECT id FROM Stores WHERE store_name LIKE ? LIMIT 1",
                ("%" + keyword + "%",),
            ).fetchone()
            return row[0] if row else None
        return None
    finally:
        conn.close()


def _get_store_ids_by_display_names(display_names: list):
    """배정 매장 표시명 여러 개 → [(store_id, store_name), ...] (본사 제외). 정확 일치 후 LIKE로 매칭(울산삼산점 등)."""
    result = []
    seen_ids = set()
    for name in (display_names or []):
        n = str(name).strip()
        if not n or n == "본사":
            continue
        conn = get_master_conn()
        try:
            row = conn.execute(
                "SELECT id, store_name FROM Stores WHERE TRIM(store_name) = ?",
                (n,),
            ).fetchone()
            if not row:
                keyword = n.replace("점", "").strip()
                if keyword:
                    row = conn.execute(
                        "SELECT id, store_name FROM Stores WHERE store_name LIKE ? LIMIT 1",
                        ("%" + keyword + "%",),
                    ).fetchone()
            if row and row[0] not in seen_ids:
                seen_ids.add(row[0])
                result.append((row[0], row[1]))
        finally:
            conn.close()
    return result


def render_employee_management():
    """직원 계정 관리 및 발령: Supabase Auth Admin API로 계정 생성 + 직원/매장은 Supabase app_users·app_stores 우선. superadmin 전용."""
    st.header("👥 직원 계정 관리 및 발령")

    use_supabase = ensure_supabase_app_tables()
    if use_supabase:
        stores_list = _get_supabase_stores_list()
        all_stores_df = pd.DataFrame(stores_list).sort_values("store_name", ignore_index=True) if stores_list else pd.DataFrame(columns=["id", "store_name", "db_filename"])
        if "store_name" not in all_stores_df.columns and len(all_stores_df) == 0:
            all_stores_df = pd.DataFrame(columns=["id", "store_name", "db_filename"])
    else:
        conn = get_master_conn()
        try:
            all_stores_df = pd.read_sql("SELECT id, store_name FROM Stores ORDER BY store_name", conn)
        finally:
            conn.close()
        client, _ = get_supabase_client()
        if client:
            st.warning(
                "직원 명부를 Supabase에 두려면 **app_users** 테이블이 필요합니다. "
                "Supabase 대시보드 → SQL Editor에서 프로젝트 루트의 **SUPABASE_APP_TABLES.sql** 내용을 실행해 주세요. "
                "또는 .streamlit/secrets.toml에 `database_url`(Postgres 연결 문자열)을 넣으면 앱이 테이블을 자동 생성합니다."
            )
        else:
            st.info("💡 직원 명부를 Supabase에 저장하려면 Supabase URL/Key 설정 후, SQL Editor에서 **SUPABASE_APP_TABLES.sql**을 실행해 주세요.")
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

                    selected_store_ids = all_stores_df[all_stores_df["store_name"].isin(emp_stores or [])]["id"].tolist()
                    first_store_id = selected_store_ids[0] if selected_store_ids else None
                    username = str(emp_email).strip()
                    role = str(emp_role_choice).strip()
                    emp_name_val = str(emp_name).strip() if emp_name else ""

                    if use_supabase:
                        existing = _supabase_get_app_user_by_email(username)
                        try:
                            if existing:
                                user_id = existing["id"]
                                _supabase_update_app_user(user_id, emp_name_val, role, first_store_id, selected_store_ids)
                                st.success("이미 Supabase에 있는 이메일입니다. 직원 정보(이름, 권한, 배정 매장)만 반영했습니다. 기존 비밀번호로 로그인할 수 있습니다.")
                            else:
                                user_id, ins_err = _supabase_insert_app_user(username, str(emp_email).strip(), role, first_store_id, emp_name_val)
                                if ins_err:
                                    st.error(f"직원 명부 등록 실패: {ins_err}")
                                    st.stop()
                                for sid in selected_store_ids:
                                    try:
                                        get_supabase_client()[0].table("app_user_stores").insert({"user_id": user_id, "store_id": sid}).execute()
                                    except Exception:
                                        pass
                                if supabase_already_exists:
                                    st.success("이 이메일은 Supabase에 이미 있어 앱 권한만 부여했습니다. 기존 비밀번호로 로그인할 수 있습니다.")
                                else:
                                    st.success("직원 계정이 생성되었습니다. 해당 이메일과 초기 비밀번호로 로그인할 수 있습니다.")
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
                    with st.form("employee_update_form"):
                        st.text_input("이메일 (로그인 ID)", value=row[1] or "", disabled=True, help="이메일 변경은 불가합니다.")
                        edit_name = st.text_input("직원 이름", value=(row[3] or "").strip() or (row[0] or ""), key="emp_update_name")
                        edit_role = st.selectbox(
                            "권한",
                            options=[r[0] for r in EMPLOYEE_ROLE_OPTIONS],
                            format_func=lambda x: next((r[1] for r in EMPLOYEE_ROLE_OPTIONS if r[0] == x), x),
                            index=next((i for i, r in enumerate(EMPLOYEE_ROLE_OPTIONS) if r[0] == row[2]), 0),
                            key="emp_update_role",
                        )
                        current_names = all_stores_df[all_stores_df["id"].isin(current_store_ids)]["store_name"].tolist()
                        edit_stores = st.multiselect(
                            "배정 매장 (여러 개 선택 가능)",
                            all_stores_df["store_name"].tolist(),
                            default=current_names,
                            key="emp_update_stores",
                        )
                        if st.form_submit_button("저장"):
                            store_ids = all_stores_df[all_stores_df["store_name"].isin(edit_stores)]["id"].tolist()
                            first_sid = store_ids[0] if store_ids else None
                            try:
                                if use_supabase:
                                    _supabase_update_app_user(edit_user_id, (edit_name or "").strip() or None, edit_role, first_sid, store_ids)
                                    st.success("직원 정보가 저장되었습니다.")
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
                                        st.success("직원 정보가 저장되었습니다.")
                                    finally:
                                        conn.close()
                                st.rerun()
                            except Exception as e:
                                st.error(f"저장 실패: {str(e)}")

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
                    if st.button("배정 매장 저장", key="emp_edit_save_btn"):
                        store_ids = all_stores_df[all_stores_df["store_name"].isin(edited_stores)]["id"].tolist()
                        first_sid = store_ids[0] if store_ids else None
                        try:
                            if use_supabase:
                                u = next((x for x in _get_supabase_users_list() if x.get("id") == store_edit_user_id), None)
                                cur_name = (u.get("name") or "").strip() if u else None
                                cur_role = u.get("role") if u else "user"
                                _supabase_update_app_user(store_edit_user_id, cur_name, cur_role, first_sid, store_ids)
                                st.success("배정 매장이 저장되었습니다.")
                            else:
                                conn = get_master_conn()
                                try:
                                    conn.execute("DELETE FROM UserStores WHERE user_id = ?", (store_edit_user_id,))
                                    for sid in store_ids:
                                        conn.execute("INSERT OR IGNORE INTO UserStores (user_id, store_id) VALUES (?, ?)", (store_edit_user_id, sid))
                                    conn.execute("UPDATE Users SET store_id = ? WHERE id = ?", (first_sid, store_edit_user_id))
                                    conn.commit()
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
                                        r = admin_client.auth.admin.list_users()
                                        users = getattr(r, "users", None) or getattr(r, "data", None)
                                        if users is None and hasattr(r, "model_dump"):
                                            d = r.model_dump()
                                            users = d.get("users", d.get("data", []))
                                        for u in (users or []):
                                            em = getattr(u, "email", None) if hasattr(u, "email") else (u.get("email") if isinstance(u, dict) else None)
                                            if em == del_email:
                                                uid = getattr(u, "id", None) if hasattr(u, "id") else (u.get("id") if isinstance(u, dict) else None)
                                                if uid:
                                                    admin_client.auth.admin.delete_user(uid)
                                                break
                                    except Exception:
                                        pass
                                if use_supabase:
                                    _supabase_delete_app_user(del_user_id)
                                    st.success("직원이 삭제되었습니다.")
                                else:
                                    conn = get_master_conn()
                                    conn.execute("DELETE FROM UserStores WHERE user_id = ?", (del_user_id,))
                                    conn.execute("DELETE FROM Users WHERE id = ?", (del_user_id,))
                                    conn.commit()
                                    conn.close()
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
        df = pd.DataFrame(emp_list) if emp_list else pd.DataFrame(columns=["id", "email", "username", "name", "role", "배정매장"])
    else:
        conn = get_master_conn()
        try:
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='UserStores'")
            if cur.fetchone() is not None:
                df = pd.read_sql(
                    """
                    SELECT u.id, u.email, u.username, u.name, u.role,
                        COALESCE(
                            (SELECT GROUP_CONCAT(s.store_name, ', ') FROM UserStores us JOIN Stores s ON us.store_id = s.id WHERE us.user_id = u.id),
                            (SELECT s.store_name FROM Stores s WHERE s.id = u.store_id)
                        ) AS 배정매장
                    FROM Users u
                    ORDER BY u.id
                    """,
                    conn,
                )
            else:
                df = pd.read_sql(
                    """
                    SELECT u.id, u.email, u.username, u.name, u.role, s.store_name AS 배정매장
                    FROM Users u
                    LEFT JOIN Stores s ON u.store_id = s.id
                    ORDER BY u.id
                    """,
                    conn,
                )
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
    df_display = df_display[["id", "email", "사용자명", "권한", "배정매장"]]
    df_display.columns = ["ID", "이메일", "사용자명", "권한", "배정 매장"]
    st.dataframe(df_display, use_container_width=True)


def render_superadmin():
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
    ])
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


# ========== 탭 1: 매장 관리자 메뉴 (Store Admin 전용) — Employees ==========

def render_store_admin_employees():
    db_filename = st.session_state.get("current_db")
    if not db_filename:
        st.warning("매장에 로그인한 후 이용하세요.")
        return
    conn = get_tenant_conn(db_filename)
    if not conn:
        st.error("매장 DB를 찾을 수 없습니다.")
        return

    # ---------- 관리자 알림 (마진율 이상 등) — Superadmin/매장 관리자 ----------
    try:
        conn_m = get_master_conn()
        store_name = _get_store_name_by_db(db_filename)
        role = (st.session_state.get("current_user") or {}).get("role", "")
        if role == "superadmin":
            alerts = pd.read_sql(
                "SELECT id, created_at, store_name, alert_type, message, seen FROM AdminAlerts ORDER BY id DESC LIMIT 50",
                conn_m,
            )
        else:
            alerts = pd.read_sql(
                "SELECT id, created_at, store_name, alert_type, message, seen FROM AdminAlerts WHERE store_name = ? ORDER BY id DESC LIMIT 50",
                conn_m,
                params=(store_name,),
            )
        conn_m.close()
        if len(alerts) > 0:
            st.subheader("관리자 알림 (마진율 이상 등)")
            alerts_disp = alerts.rename(columns={"created_at": "발생 시각", "store_name": "매장", "alert_type": "유형", "message": "내용"})
            st.dataframe(alerts_disp[["발생 시각", "매장", "유형", "내용"]], use_container_width=True)
        else:
            st.caption("최근 관리자 알림 없음 (마진율 이상 등록 시 여기에 표시됩니다).")
    except Exception:
        pass

    st.header("직원 마스터 (Employees)")
    try:
        df = pd.read_sql("SELECT id, name, is_active FROM Employees ORDER BY id", conn)
    except Exception:
        df = pd.DataFrame(columns=["id", "name", "is_active"])
    finally:
        conn.close()

    with st.form("add_employee_form"):
        name = st.text_input("직원 이름")
        is_active = st.checkbox("활성", value=True)
        if st.form_submit_button("추가"):
            if name and name.strip():
                conn = get_tenant_conn(db_filename)
                conn.execute("INSERT INTO Employees (name, is_active) VALUES (?, ?)", (name.strip(), 1 if is_active else 0))
                conn.commit()
                conn.close()
                st.success("추가되었습니다.")
                st.rerun()
            else:
                st.warning("이름을 입력하세요.")

    if len(df) > 0:
        st.subheader("직원 목록 (수정/비활성화)")
        for _, row in df.iterrows():
            with st.expander(f"{row['name']} {'(활성)' if row['is_active'] else '(비활성)'}"):
                with st.form(f"emp_{row['id']}"):
                    new_name = st.text_input("이름", value=row["name"], key=f"name_{row['id']}")
                    new_active = st.checkbox("활성", value=bool(row["is_active"]), key=f"active_{row['id']}")
                    if st.form_submit_button("저장"):
                        conn = get_tenant_conn(db_filename)
                        conn.execute("UPDATE Employees SET name = ?, is_active = ? WHERE id = ?", (new_name, 1 if new_active else 0, row["id"]))
                        conn.commit()
                        conn.close()
                        st.rerun()
                    if st.form_submit_button("삭제(비활성 권장)"):
                        conn = get_tenant_conn(db_filename)
                        conn.execute("UPDATE Employees SET is_active = 0 WHERE id = ?", (row["id"],))
                        conn.commit()
                        conn.close()
                        st.rerun()

    # ===== 매출/결제 수정 요청 승인 워크플로우 =====
    st.header("매출·결제 수정 요청 승인")
    conn = get_tenant_conn(db_filename)
    if not conn:
        st.error("매장 DB를 찾을 수 없습니다.")
        return
    try:
        req_df = pd.read_sql(
            "SELECT id, created_at, requested_by, entity_type, entity_id, payload, reason, status FROM EditRequests WHERE status = 'pending' ORDER BY created_at ASC",
            conn,
        )
    except Exception:
        req_df = pd.DataFrame(columns=["id", "created_at", "requested_by", "entity_type", "entity_id", "payload", "reason", "status"])
    if len(req_df) == 0:
        st.info("대기 중인 수정 요청이 없습니다.")
    else:
        st.error(f"대기 중인 수정 요청 {len(req_df)}건이 있습니다.")
        for _, r in req_df.iterrows():
            with st.expander(f"요청 #{r['id']} — {r['requested_by']} / {r['entity_type']} #{r['entity_id']}"):
                st.caption(f"요청 시각: {r['created_at']}")
                st.write(f"사유: {r['reason']}")
                try:
                    payload = json.loads(r["payload"])
                except Exception:
                    payload = {}
                st.json(payload)
                c1, c2 = st.columns(2)
                with c1:
                    if st.button("승인 후 DB 반영", key=f"req_approve_{r['id']}"):
                        try:
                            # 현재 주문/고객 값 조회 (고객은 Supabase, 주문은 SQLite)
                            oid = int(payload.get("order_id") or r["entity_id"])
                            cid = int(payload.get("customer_id") or 0)
                            cur_c = None
                            if cid:
                                try:
                                    sc, _ = get_supabase_client()
                                    if sc:
                                        qc = sc.table("customers").select("id, name, phone1, phone2, address").eq("id", cid)
                                        if _customers_tenant_column():
                                            qc = qc.eq(_customers_tenant_column(), db_filename)
                                        rc = qc.maybe_single().execute()
                                        if rc.data:
                                            cur_c = rc.data
                                except Exception:
                                    pass
                            cur_o = conn.execute(
                                "SELECT id, order_date, delivery_date, category, cost_price, total_amount, visit_reason, purchase_reason FROM Orders WHERE id = ?",
                                (oid,),
                            ).fetchone()
                            new_c = payload.get("new_customer") or {}
                            new_o = payload.get("new_order") or {}
                            reason = r["reason"]
                            if cur_o:
                                # cur_o: id, order_date, delivery_date, category, cost_price, total_amount, visit_reason, purchase_reason
                                old_total = cur_o[5] or 0
                                new_total = int(new_o.get("total_amount") or old_total)
                                old_cost = cur_o[4] or 0
                                new_cost = int(new_o.get("cost_price") or old_cost)
                                if old_total != new_total:
                                    _insert_audit_log(conn, "Order", oid, "total_amount", old_total, new_total, reason)
                                    # 회계 원칙: 차액을 오늘 날짜로 Sales 신규 Row INSERT
                                    delta = new_total - old_total
                                    today_str = datetime.now().strftime("%Y-%m-%d")
                                    order_date_val = cur_o[1] or today_str
                                    if isinstance(order_date_val, str) and "-" in order_date_val:
                                        parts = order_date_val.split("-")
                                        order_date_label = f"{int(parts[1])}월 {int(parts[2])}일" if len(parts) >= 3 else order_date_val
                                    else:
                                        order_date_label = str(order_date_val)
                                    note = f"{order_date_label} 주문 건 금액 변경에 따른 {'차감' if delta < 0 else '추가'}"
                                    _insert_sales_transaction(db_filename, oid, today_str, float(delta), note)
                                if old_cost != new_cost:
                                    _insert_audit_log(conn, "Order", oid, "cost_price", old_cost, new_cost, reason)
                                old_visit = cur_o[6] or ""
                                new_visit = new_o.get("visit_reason")
                                if (old_visit or "") != (new_visit or ""):
                                    _insert_audit_log(conn, "Order", oid, "visit_reason", old_visit, new_visit, reason)
                                old_purchase = cur_o[7] or ""
                                new_purchase = new_o.get("purchase_reason")
                                if (old_purchase or "") != (new_purchase or ""):
                                    _insert_audit_log(conn, "Order", oid, "purchase_reason", old_purchase, new_purchase, reason)
                                # 주문 업데이트
                                conn.execute(
                                    "UPDATE Orders SET delivery_date=?, category=?, total_amount=?, cost_price=?, visit_reason=?, purchase_reason=? WHERE id=?",
                                    (
                                        new_o.get("delivery_date") or cur_o[2],
                                        new_o.get("category") or cur_o[3],
                                        new_total,
                                        new_cost,
                                        new_visit or cur_o[6],
                                        new_purchase or cur_o[7],
                                        oid,
                                    ),
                                )
                            if cur_c and new_c:
                                try:
                                    sc, _ = get_supabase_client()
                                    if sc:
                                        upd = {
                                            "name": new_c.get("name") or cur_c.get("name"),
                                            "phone1": new_c.get("phone1") or cur_c.get("phone1"),
                                            "phone2": new_c.get("phone2") or cur_c.get("phone2"),
                                            "address": new_c.get("address") or cur_c.get("address"),
                                        }
                                    uq = sc.table("customers").update(upd).eq("id", cur_c["id"])
                                    if _customers_tenant_column():
                                        uq = uq.eq(_customers_tenant_column(), db_filename)
                                    uq.execute()
                                except Exception:
                                    pass
                            conn.execute(
                                "UPDATE EditRequests SET status='approved', reviewed_by=?, reviewed_at=? WHERE id=?",
                                (_current_username(), datetime.now().isoformat(), int(r["id"])),
                            )
                            conn.commit()
                            clear_data_cache()
                            st.success("요청이 승인되고 DB에 반영되었습니다.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"승인 처리 중 오류: {e}")
                with c2:
                    if st.button("거절", key=f"req_reject_{r['id']}"):
                        conn.execute(
                            "UPDATE EditRequests SET status='rejected', reviewed_by=?, reviewed_at=? WHERE id=?",
                            (_current_username(), datetime.now().isoformat(), int(r["id"])),
                        )
                        conn.commit()
                        st.success("요청이 거절되었습니다.")
                        st.rerun()
    conn.close()


# ========== 탭 2: 새로운 매출 등록 ==========

def render_new_sales():
    db_filename = st.session_state.get("current_db")
    if not db_filename:
        st.warning("매장에 로그인한 후 이용하세요.")
        return
    conn = get_tenant_conn(db_filename)
    if not conn:
        st.error("매장 DB를 찾을 수 없습니다.")
        return
    st.header("새로운 매출 등록")
    try:
        employees = pd.read_sql("SELECT id, name FROM Employees WHERE is_active = 1", conn)
    except Exception as e:
        employees = pd.DataFrame(columns=["id", "name"])
        st.warning("직원 목록을 불러오지 못했습니다. 매장 관리자 메뉴에서 직원을 먼저 등록해 주세요.")
    finally:
        conn.close()
    customers = load_customers_cached(db_filename, limit=50)

    # 고객 선택 또는 신규 (Supabase 오류 시 빈 DataFrame 방어)
    customer_options = ["[신규 고객]"]
    if not customers.empty and "name" in customers.columns:
        customer_options += customers["name"].astype(str).tolist()
    selected_customer_label = st.selectbox("고객 선택 *", customer_options)
    is_new_customer = selected_customer_label == "[신규 고객]"

    # 기존 고객 선택 시 해당 고객 정보로 폼 기본값 채움 (Phone 1/2 분리 유지)
    if is_new_customer:
        default_name, default_phone1, default_phone2 = "", "", ""
        default_addr = st.session_state.get("address_manual", "")
    else:
        row = customers[customers["name"].astype(str) == selected_customer_label].iloc[0]
        default_name = row["name"] or ""
        default_phone1 = row["phone1"] or ""
        default_phone2 = row["phone2"] or ""
        default_addr = row["address"] or st.session_state.get("address_manual", "")
        st.session_state["address_manual"] = default_addr
    cust_name = st.text_input("고객명 *", value=default_name)
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

    # ----- 주소 검색: 카카오 로컬 API + 검색 버튼 -----
    if "address_search_results" not in st.session_state:
        st.session_state.address_search_results = []
    if "address_search_error" not in st.session_state:
        st.session_state.address_search_error = None
    with st.form("address_search_form"):
        addr_keyword = st.text_input("주소 검색어 (예: 역삼동 123, 테헤란로) *", key="addr_keyword", placeholder="검색어 입력 후 아래 검색 버튼 클릭")
        search_clicked = st.form_submit_button("검색")
    if search_clicked:
        keyword = (st.session_state.get("addr_keyword") or "").strip()
        st.session_state.address_search_error = None
        if keyword:
            # 주소 검색 + 장소(키워드) 검색 병행: 도로명 주소와 건물명/상호명 모두 검색
            results_addr, err_addr = search_address_kakao(keyword)
            results_kw, err_kw = search_keyword_kakao(keyword)
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
            st.session_state.address_search_results = combined
            if err_addr and err_kw:
                st.session_state.address_search_error = err_addr
            elif not combined:
                st.session_state.address_search_error = err_addr or err_kw or "검색 결과가 없습니다. 검색어를 바꿔 보세요."
        else:
            st.warning("검색어를 입력한 뒤 검색 버튼을 눌러 주세요.")
    if st.session_state.get("address_search_error"):
        st.error(st.session_state.address_search_error)
    # 검색 결과를 st.selectbox로 표시 (건물명/장소명 상단 표시), 선택 시 고객 주소로 반영
    if st.session_state.address_search_results:
        results = st.session_state.address_search_results
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
                if building_name:
                    disp = f"[{building_name}] {addr}"
                elif bname:
                    disp = f"[{bname}] {addr}"
                else:
                    disp = addr
            options.append(disp)
            display_to_address[disp] = addr
        st.session_state._address_display_to_value = display_to_address

        def _on_address_select():
            sel = st.session_state.get("address_selection")
            if sel and hasattr(st.session_state, "_address_display_to_value"):
                st.session_state["address_manual"] = st.session_state._address_display_to_value.get(sel, sel)

        chosen = st.selectbox(
            "검색 결과에서 주소 선택 *",
            options=options,
            key="address_selection",
            on_change=_on_address_select,
            format_func=lambda x: x,
        )
        if chosen:
            addr_val = getattr(st.session_state, "_address_display_to_value", {}).get(chosen, chosen)
            st.session_state["address_manual"] = addr_val
    # 주소: 기본 주소(검색/수동) + 상세 주소(동·호수) 분리 → DB에는 두 값 합쳐서 저장
    if "address_manual" not in st.session_state:
        st.session_state["address_manual"] = default_addr
    if "address_detail" not in st.session_state:
        st.session_state["address_detail"] = ""
    st.text_area("기본 주소 (위 검색 선택 또는 직접 입력) *", key="address_manual")
    st.text_input("상세 주소 (동/호수 등) *", key="address_detail")
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
    # key로 선택 값 유지(리런 시 초기화 방지), 복수 선택 가능
    selected_employees = st.multiselect(
        "담당 직원 (복수 선택, 1/n 실적 분배 대상) *",
        options=emp_names,
        default=[],
        key="new_sales_employee_multiselect",
    )
    employee_names_str = ",".join(selected_employees) if selected_employees else ""
    if "order_date" not in st.session_state:
        st.session_state["order_date"] = date.today()
    order_date = st.date_input("계약일 *", key="order_date")
    if "delivery_date" not in st.session_state:
        st.session_state["delivery_date"] = date.today()
    delivery_date = st.date_input("배송일 *", key="delivery_date")
    CATEGORY_OPTIONS = ["옷장", "식탁", "자녀방", "침대", "SSDS침대", "서재_학생", "소파", "소품", "전시품"]
    selected_categories = st.multiselect("품목/카테고리 (복수 선택) *", options=CATEGORY_OPTIONS, key="category_multiselect")
    category = ",".join(selected_categories) if selected_categories else None
    has_display = selected_categories and "전시품" in selected_categories
    # 금액: 세션 초기화 후 text_input + on_change로 천 단위 콤마 표시, DB 저장 시 _parse_comma_to_int 사용
    if "cost_price" not in st.session_state:
        st.session_state["cost_price"] = "0"
    if "total_amount" not in st.session_state:
        st.session_state["total_amount"] = "0"

    def _on_cost_price():
        st.session_state["cost_price"] = _format_number_comma(st.session_state.get("cost_price", ""))

    def _on_total_amount():
        st.session_state["total_amount"] = _format_number_comma(st.session_state.get("total_amount", ""))

    st.text_input("일반제품 판매가(Selling Price) *", key="total_amount", on_change=_on_total_amount)
    st.text_input("일반제품 원가(Cost) *", key="cost_price", on_change=_on_cost_price)
    if has_display:
        if "display_sales_amount" not in st.session_state:
            st.session_state["display_sales_amount"] = "0"
        if "display_cost_amount" not in st.session_state:
            st.session_state["display_cost_amount"] = "0"
        st.caption("전시품 선택 시 입력 (1000단위 콤마 적용)")
        def _on_display_sales():
            st.session_state["display_sales_amount"] = _format_number_comma(st.session_state.get("display_sales_amount", ""))
        def _on_display_cost():
            st.session_state["display_cost_amount"] = _format_number_comma(st.session_state.get("display_cost_amount", ""))
        st.text_input("전시품 판매가 *", key="display_sales_amount", on_change=_on_display_sales)
        st.text_input("전시품 원가 *", key="display_cost_amount", on_change=_on_display_cost)
    # 실시간 합산: 최종 총 판매금액, 최종 총 원가, 기본 총 마진
    general_sales = _parse_comma_to_int(st.session_state.get("total_amount", "0"))
    general_cost = _parse_comma_to_int(st.session_state.get("cost_price", "0"))
    display_sales_val = _parse_comma_to_int(st.session_state.get("display_sales_amount", "0")) if has_display else 0
    display_cost_val = _parse_comma_to_int(st.session_state.get("display_cost_amount", "0")) if has_display else 0
    final_sales = general_sales + display_sales_val
    final_cost = general_cost + display_cost_val
    basic_margin = final_sales - final_cost
    st.subheader("합산 금액 (실시간)")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("최종 총 판매금액", f"{final_sales:,}원", help="일반제품 판매가 + 전시품 판매가")
    with c2:
        st.metric("최종 총 원가", f"{final_cost:,}원", help="일반제품 원가 + 전시품 원가")
    with c3:
        st.metric("기본 총 마진", f"{basic_margin:,}원", help="최종 총 판매금액 - 최종 총 원가")

    VISIT_REASON_OPTIONS = ["매장외관", "재구매", "소개", "광고(SNS 외)"]
    PURCHASE_REASON_OPTIONS = ["교체(이사없이)", "신혼/혼수", "공동구매(입주, 가구쇼 등)", "이사", "현대임직원할인"]
    visit_reason = st.selectbox("방문 이유 *", options=VISIT_REASON_OPTIONS)
    purchase_reason = st.selectbox("구매 이유 *", options=PURCHASE_REASON_OPTIONS)

    # ----- 다중(복합) 결제 수단: 최대 4개 고정 슬롯 -----
    st.subheader("결제 내역 (복수 결제 가능)")
    if "payment_rows" not in st.session_state:
        st.session_state["payment_rows"] = [
            {"method": "", "card_company": "", "amount": "0"} for _ in range(4)
        ]
    total_payment_int = 0
    for i in range(4):
        row_key = f"pay_method_{i}"
        card_key = f"pay_card_{i}"
        amt_key = f"pay_amt_{i}"
        if amt_key not in st.session_state:
            st.session_state[amt_key] = "0"
        c1, c2, c3 = st.columns([2, 2, 2])
        with c1:
            method = st.selectbox(f"결제 수단 #{i+1} *", options=PAYMENT_METHOD_OPTIONS, key=row_key, index=0 if i == 0 else 0)
        with c2:
            if method in ("신용카드", "체크카드"):
                card_company = st.selectbox(f"카드사 #{i+1} *", options=CARD_COMPANY_OPTIONS, key=card_key)
            else:
                card_company = None
        with c3:
            def _on_amt(j):
                def _():
                    k = f"pay_amt_{j}"
                    st.session_state[k] = _format_number_comma(st.session_state.get(k, ""))
                return _
            st.text_input(f"금액 #{i+1} *", key=amt_key, on_change=_on_amt(i))
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
        total_payment_slots = sum(_parse_comma_to_int(st.session_state.get(f"pay_amt_{i}", "0")) for i in range(4))
        unpaid_balance = final_sales_save - total_payment_slots  # 판매가 - 수납액 = 미수금
        # 마진율 검증 (15%~25% 범위 이탈 시 경고, 저장은 가능)
        margin_pct = (final_sales_save - final_cost_save) / final_sales_save * 100 if final_sales_save else 0
        margin_out_of_range = margin_pct < 15 or margin_pct > 25
        if margin_out_of_range:
            st.warning(f"⚠️ 주의: 마진율이 {margin_pct:.1f}%입니다. 적정 범위(15%~25%)를 벗어났습니다.")
        # 온누리상품권 결제에 대한 부정 사용 방지 검증
        # 1차: 승인번호 뒤 4자리 + 결제일 기준 중복 여부 확인 (금액 제외)
        # 중복 발견 시 해당 슬롯은 전체 승인번호(8자리 이상) 입력 단계로 전환
        for i in range(4):
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
        conn = get_tenant_conn(db_filename)
        try:
            if is_new_customer:
                # Supabase Customers INSERT (id 자동증가, RETURNING으로 id 조회)
                client, err = get_supabase_client()
                if err:
                    st.error(f"⚠️ Supabase 연결 실패: {err}")
                    st.stop()
                payload = {
                    "name": cust_name.strip(),
                    "phone1": phone1.strip(),
                    "phone2": phone2 or None,
                    "address": address_full or None,
                }
                tc = _customers_tenant_column()
                if tc:
                    payload[tc] = db_filename
                r = client.table("customers").insert(payload).execute()
                if not r.data or len(r.data) == 0:
                    st.error("고객 등록에 실패했습니다. Supabase 응답을 확인해 주세요.")
                    st.stop()
                customer_id = int(r.data[0]["id"])
            else:
                customer_id = int(customers[customers["name"].astype(str) == selected_customer_label]["id"].iloc[0])
            # 신규 주문 생성 시 초기 잔금 상태(balance_status)도 함께 설정
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
                "미납",  # 결제 추가 후 아래에서 실질 잔금 기준으로 다시 보정
            ))
            # id는 INSERT에 포함하지 않음(자동증가). 새로 생성된 주문 id 조회.
            order_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            total_fees = 0.0
            total_paid_initial = 0
            for i in range(4):
                amt = _parse_comma_to_int(st.session_state.get(f"pay_amt_{i}", "0"))
                if amt <= 0:
                    continue
                method = st.session_state.get(f"pay_method_{i}", "")
                card_company = st.session_state.get(f"pay_card_{i}", None) if method in ("신용카드", "체크카드") else None
                fee = _payment_fee_amount(method, amt)
                total_fees += fee
                total_paid_initial += amt
                # 온누리상품권 승인번호(4자리 또는 8자리 이상 전체) 저장
                onnuri_code = None
                if method and "온누리" in str(method):
                    stage = st.session_state.get(f"pay_onnuri_stage_{i}", "last4")
                    if stage == "last4":
                        raw = (st.session_state.get(f"pay_onnuri_last4_{i}", "") or "").strip()
                    else:
                        raw = (st.session_state.get(f"pay_onnuri_full_{i}", "") or "").strip()
                    onnuri_code = re.sub(r"\\D", "", raw) or None
                conn.execute("""
                    INSERT INTO Payments (order_id, payment_date, amount, payment_method, card_company, fee_amount, onnuri_approval_code)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (order_id, order_date.isoformat(), amt, method or None, card_company, fee, onnuri_code))
            actual_margin = basic_margin_save - total_fees
            conn.execute("UPDATE Orders SET actual_margin = ? WHERE id = ?", (actual_margin, order_id))
            # 초기 결제금액 기준으로 잔금 상태 업데이트
            remaining = final_sales_save - total_paid_initial
            balance_status = "완납" if remaining == 0 else "미납"
            conn.execute("UPDATE Orders SET balance_status = ? WHERE id = ?", (balance_status, order_id))
            # Sales: 신규 주문 1건을 transaction_date=주문일, amount=최종판매액, 미수금(unpaid_balance) 포함 기록 (Supabase)
            _insert_sales_transaction(db_filename, order_id, order_date.isoformat(), float(final_sales_save), "신규 주문", unpaid_balance=unpaid_balance)
            conn.commit()
            clear_data_cache()
            st.success("입력이 완료되었습니다.")
            # 마진율 이상 시 Superadmin/매장관리자 알림
            if margin_out_of_r.
            35\098541dange:
                store_name = _get_store_name_by_db(db_filename)
                _insert_admin_alert(store_name, "margin", f"{store_name}에서 마진율 {margin_pct:.1f}% 건이 등록되었습니다.")
            # 채널톡 PUSH: 백그라운드 스레드로 전송해 UI 블로킹 방지
            def _channel_talk_sync():
                try:
                    cutoff = _get_channel_talk_sync_cutoff_date()
                    if _get_channel_talk_secrets() and cutoff is not None and date.today() >= cutoff:
                        store_name = _get_store_name_by_db(db_filename)
                        store_tag_key = _get_store_tag_key(store_name)
                        sync_channel_talk_customer(
                            customer_name=cust_name.strip(),
                            phone_number=phone1.strip(),
                            purchase_amount=final_sales_save,
                            item_category=category or "",
                            purchase_date=order_date,
                            store_tag_key=store_tag_key,
                        )
                except Exception:
                    pass
            threading.Thread(target=_channel_talk_sync, daemon=True).start()
            # 입력값 초기화 후 새 등록 모드로 전환 (중복 등록 방지) — toast로 표시해 레이아웃 깜빡임 완화
            st.toast("등록이 완료되었습니다. (채널톡 동기화는 백그라운드에서 진행됩니다.)", icon="✅")
            # 신규 매출 등록 관련 상태 초기화
            for key in list(st.session_state.keys()):
                if key in (
                    "phone1",
                    "phone2",
                    "address_manual",
                    "address_detail",
                    "address_search_results",
                    "address_search_error",
                    "addr_keyword",
                    "address_selection",
                    "order_date",
                    "delivery_date",
                    "cost_price",
                    "total_amount",
                    "display_sales_amount",
                    "display_cost_amount",
                    "category_multiselect",
                ) or key.startswith(("pay_", "pay_onnuri_", "gen_pay", "d10_", "over_")):
                    try:
                        del st.session_state[key]
                    except Exception:
                        pass
            st.rerun()
        finally:
            conn.close()


# ========== 탭 3: 고객 및 잔금 관리 (3개 하위 탭) ==========

def _recalc_order_actual_margin(conn, order_id: int):
    """해당 주문의 Payments 수수료 합계 및 잔금 상태(balance_status)를 Orders에 반영."""
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
    # 결제 합계 기준으로 balance_status도 보정
    paid = conn.execute(
        "SELECT COALESCE(SUM(amount),0) FROM Payments WHERE order_id = ?",
        (order_id,),
    ).fetchone()[0]
    remaining = (total_amt or 0) - (paid or 0)
    balance_status = "완납" if remaining == 0 else "미납"
    conn.execute(
        "UPDATE Orders SET balance_status = ? WHERE id = ?",
        (balance_status, order_id),
    )


def _customer_balance_payment_ui(db_filename: str, order_id: int, balance: float, key_prefix: str = "pay"):
    """잔금 완납 처리(결제 추가) 공통 UI. 직원도 사용 가능하되, 모든 변경은 PaymentHistory에 기록."""
    amt_key = f"{key_prefix}_amt"
    if amt_key not in st.session_state:
        st.session_state[amt_key] = _format_number_comma(str(int(balance))) if balance > 0 else "0"
    st.caption("잔금 완납 처리 (결제 추가)")
    add_method = st.selectbox("결제 수단", options=PAYMENT_METHOD_OPTIONS, key=f"{key_prefix}_method")
    if add_method in ("신용카드", "체크카드"):
        add_card = st.selectbox("카드사", options=CARD_COMPANY_OPTIONS, key=f"{key_prefix}_card")
    else:
        add_card = None
    st.text_input("결제 금액", key=amt_key, on_change=lambda: st.session_state.__setitem__(amt_key, _format_number_comma(st.session_state.get(amt_key, ""))))
    add_amt_int = _parse_comma_to_int(st.session_state.get(amt_key, "0"))
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
    edit_reason = st.text_area("수정/취소 사유(필수, 5자 이상)", key=reason_key)
    if st.button("결제 등록", key=f"{key_prefix}_btn"):
        if add_amt_int > 0:
            if not edit_reason or len(edit_reason.strip()) < 5:
                st.warning("수정/취소 사유를 5자 이상 입력하세요.")
                return
            # 온누리상품권 중복 검증: 오늘 날짜 + 승인번호 4자리 조합 (금액 제외)
            onnuri_code = None
            if is_onnuri:
                stage = st.session_state.get(stage_key, "last4")
                pay_date_str = date.today().isoformat()
                if stage == "last4":
                    last4_raw = (st.session_state.get(last4_key, "") or "").strip()
                    last4_digits = re.sub(r"\\D", "", last4_raw)
                    if len(last4_digits) != 4:
                        st.error("온누리상품권 결제의 승인번호 뒤 4자리를 정확히 입력하세요.")
                        return
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
            conn = get_tenant_conn(db_filename)
            # 기존 결제 합계/잔금 기준으로 감사 로그 남김 (결제 금액/잔금 상태)
            cur = conn.execute("SELECT COALESCE(SUM(amount),0) FROM Payments WHERE order_id = ?", (order_id,))
            old_paid_total = cur.fetchone()[0] or 0
            new_paid_total = old_paid_total + add_amt_int
            old_balance = balance
            new_balance = balance - add_amt_int
            conn.execute("""
                INSERT INTO Payments (order_id, payment_date, amount, payment_method, card_company, fee_amount, onnuri_approval_code)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (order_id, date.today().isoformat(), add_amt_int, add_method or None, add_card, fee, onnuri_code))
            _recalc_order_actual_margin(conn, order_id)
            _insert_audit_log(conn, "Order", order_id, "payment_total", old_paid_total, new_paid_total, edit_reason)
            _insert_audit_log(conn, "Order", order_id, "balance_amount", old_balance, new_balance, edit_reason)
            # PaymentHistory 기록 (고객명: Supabase에서 id 기준 조회)
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
            _insert_payment_history(conn, order_id, customer_name, "잔금결제", old_payment_data, new_payment_data, edit_reason)
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
    conn = get_tenant_conn(db_filename)
    if not conn:
        st.error("매장 DB를 찾을 수 없습니다.")
        return

    st.header("고객 및 잔금 관리")
    current_user = st.session_state.get("current_user") or {}
    role = current_user.get("role", "user")
    tab_gen, tab_d10, tab_overdue = st.tabs([
        "1. 일반 고객 및 데이터 수정 (General)",
        "2. 다가오는 미수금 (배송일 D-10 이내)",
        "3. 🚨 경고! 미결 금액 (배송일 지남 + 미수금)"
    ])
    today = date.today()

    # ---------- 탭 1: 일반 고객 및 데이터 수정 ----------
    with tab_gen:
        # 고객 엑셀 일괄 등록 (기준일 이전 고객용, 채널톡 동기화 없음)
        with st.expander("📤 고객 엑셀 일괄 등록 (기존 고객)"):
            st.caption("엑셀 파일로 고객을 일괄 등록합니다. 채널톡에는 등록되지 않습니다. 컬럼: 이름(또는 name), 전화번호1(또는 phone1), 전화번호2(또는 phone2), 주소(또는 address). UTF-8 인코딩 권장.")
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
                                    qp = client.table("customers").select("phone1")
                                    if _customers_tenant_column():
                                        qp = qp.eq(_customers_tenant_column(), db_filename)
                                    r = qp.execute()
                                    existing_phones = set()
                                    for row in (r.data or []):
                                        if row.get("phone1") and str(row["phone1"]).strip():
                                            existing_phones.add(re.sub(r"\D", "", str(row["phone1"])))
                                    inserted, skipped = 0, 0
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
                                        excel_payload = {
                                            "name": name_val or "미입력",
                                            "phone1": phone1_val,
                                            "phone2": phone2_val,
                                            "address": address_val,
                                            "source": "엑셀",
                                        }
                                        if _customers_tenant_column():
                                            excel_payload[_customers_tenant_column()] = db_filename
                                        client.table("customers").insert(excel_payload).execute()
                                        existing_phones.add(phone1_digits)
                                        inserted += 1
                                    clear_data_cache()
                                    st.toast(f"엑셀 고객 일괄 등록 완료: {inserted}건 등록, {skipped}건 중복/스킵.", icon="✅")
                                    st.rerun()
                                except Exception as e:
                                    st.error(f"엑셀 등록 중 오류: {e}")
                except Exception as e:
                    st.error(f"엑셀 파일을 읽을 수 없습니다: {e}")

        # 채널톡 연동: PUSH 전용 (신규 매출 등록 시에만 채널톡으로 자동 전송, 태그 형식: 매장키구매/품목)
        with st.expander("📤 채널톡 연동 안내 (PUSH 전용)"):
            st.caption("채널톡 연동은 **PUSH 방식만** 사용합니다. '새로운 매출 등록'에서 매출을 저장하면 해당 고객 정보가 채널톡에 자동으로 전송되며, 고객 태그는 '매장구매/품목' 형식(예: 삼산구매/옷장)으로 저장됩니다. 채널톡에서 고객을 불러오는 PULL 기능은 사용하지 않습니다.")

        # 채널톡 푸시 수신 현황: DB 전송 여부 확인 (우리 쪽에서만 확인)
        with st.expander("📋 채널톡 푸시 수신 현황 (DB 전송 확인)"):
            st.caption("채널톡에서 푸시(웹훅)가 들어왔을 때 우리 DB에 등록되었는지 확인합니다. 채널톡 쪽 확인 없이 앱에서만 확인 가능합니다.")
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
                    "status": "상태", "message": "메시지", "db_filename": "저장DB", "customer_id": "고객ID"
                })
                st.write("**최근 푸시 수신 로그**")
                st.dataframe(log_disp, use_container_width=True)
            else:
                st.info("아직 채널톡 푸시 수신 로그가 없습니다. 웹훅 수신 서버를 설정하면 여기에 표시됩니다.")
            st.write("**채널톡으로 등록된 고객 (본 매장)**")
            try:
                sc, _ = get_supabase_client()
                if sc:
                    qct = sc.table("customers").select("id, name, phone1, phone2, address, source").in_("source", ["채널톡", "채널톡_웹훅"])
                    if _customers_tenant_column():
                        qct = qct.eq(_customers_tenant_column(), db_filename)
                    r = qct.order("id", desc=True).limit(100).execute()
                    ct_customers = pd.DataFrame(r.data) if r.data else pd.DataFrame()
                else:
                    ct_customers = pd.DataFrame()
            except Exception:
                ct_customers = pd.DataFrame()
            if len(ct_customers) > 0:
                st.dataframe(ct_customers.rename(columns={"id": "ID", "name": "고객명", "phone1": "연락처1", "phone2": "연락처2", "address": "주소", "source": "가입경로"}), use_container_width=True)
            else:
                st.info("채널톡(또는 푸시)으로 등록된 고객이 없습니다.")

        st.subheader("고객 검색 (이름 또는 전화번호)")
        search_query = st.text_input("이름 또는 전화번호로 검색", key="gen_search")
        if search_query and search_query.strip():
            q = search_query.strip()
            try:
                sc, _ = get_supabase_client()
                if sc:
                    qcq = sc.table("customers").select("id, name, phone1, phone2, address").or_(f"name.ilike.%{q}%,phone1.ilike.%{q}%,phone2.ilike.%{q}%")
                    if _customers_tenant_column():
                        qcq = qcq.eq(_customers_tenant_column(), db_filename)
                    r = qcq.order("id", desc=True).limit(50).execute()
                    customers = pd.DataFrame(r.data) if r.data else pd.DataFrame()
                else:
                    customers = pd.DataFrame()
            except Exception:
                customers = pd.DataFrame()
        else:
            customers = load_customers_cached(db_filename, limit=50)

        if len(customers) == 0:
            st.info("검색 결과가 없습니다.")
        else:
            selected_cid = st.selectbox(
                "고객 선택",
                customers["id"].tolist(),
                format_func=lambda cid: f"{customers[customers['id']==cid].iloc[0]['name']} ({customers[customers['id']==cid].iloc[0]['phone1'] or '-'})",
                key="gen_customer_select"
            )
            if selected_cid:
                cid = selected_cid
                conn = get_tenant_conn(db_filename)
                try:
                    orders = pd.read_sql(
                        "SELECT id, order_date, delivery_date, category, cost_price, total_amount, visit_reason, purchase_reason, employee_names FROM Orders WHERE customer_id = ?",
                        conn, params=(cid,)
                    )
                    payments = pd.read_sql("SELECT order_id, amount, fee_amount FROM Payments", conn)
                finally:
                    conn.close()
                pay_sum = payments.groupby("order_id")["amount"].sum()
                orders = orders.copy()
                orders["paid"] = orders["id"].map(pay_sum).fillna(0)
                orders["balance"] = orders["total_amount"] - orders["paid"]
                orders["delivery_date"] = pd.to_datetime(orders["delivery_date"], errors="coerce")
                num_cols = [c for c in ["cost_price", "total_amount", "paid", "balance"] if c in orders.columns]
                st.dataframe(_format_df_display(orders, num_cols), use_container_width=True)
                # 선택된 주문의 변경 이력 보기
                with st.expander("선택 주문 변경 이력 보기"):
                    hist_oid = st.selectbox("주문 선택 (변경 이력 조회용)", orders["id"].tolist(), key="gen_order_history_sel")
                    if hist_oid:
                        _render_order_audit_trail(db_filename, int(hist_oid))

                with st.expander("📝 데이터 수정하기"):
                    cust_row = customers[customers["id"] == cid].iloc[0]
                    edit_prefix = f"edit_c{cid}"
                    if f"{edit_prefix}_loaded" not in st.session_state:
                        st.session_state[f"{edit_prefix}_loaded"] = True
                        st.session_state[f"{edit_prefix}_name"] = cust_row["name"] or ""
                        st.session_state[f"{edit_prefix}_phone1"] = cust_row["phone1"] or ""
                        st.session_state[f"{edit_prefix}_phone2"] = cust_row["phone2"] or ""
                        st.session_state[f"{edit_prefix}_address"] = cust_row["address"] or ""
                    order_options = orders["id"].tolist()
                    sel_oid = st.selectbox("수정할 주문 선택", order_options, key=f"{edit_prefix}_order_sel")
                    if sel_oid:
                        orow = orders[orders["id"] == sel_oid].iloc[0]
                        if f"{edit_prefix}_oid" not in st.session_state or st.session_state[f"{edit_prefix}_oid"] != sel_oid:
                            st.session_state[f"{edit_prefix}_oid"] = sel_oid
                            dval = orow["delivery_date"]
                            if pd.notna(dval) and hasattr(dval, "date"):
                                st.session_state[f"{edit_prefix}_delivery"] = dval.date()
                            else:
                                st.session_state[f"{edit_prefix}_delivery"] = today
                            # 품목: 콤마 구분 문자열 → 리스트 (multiselect용, session_state는 사용 안 함)
                            st.session_state[f"{edit_prefix}_total"] = _format_number_comma(str(int(orow["total_amount"]))) if orow["total_amount"] else "0"
                            st.session_state[f"{edit_prefix}_cost"] = _format_number_comma(str(int(orow["cost_price"]))) if pd.notna(orow.get("cost_price")) and orow.get("cost_price") else "0"
                            st.session_state[f"{edit_prefix}_visit"] = orow["visit_reason"] or ""
                            st.session_state[f"{edit_prefix}_purchase"] = orow["purchase_reason"] or ""
                        st.text_input("고객명", key=f"{edit_prefix}_name")
                        st.text_input("Phone 1", key=f"{edit_prefix}_phone1")
                        st.text_input("Phone 2", key=f"{edit_prefix}_phone2")
                        st.text_area("주소", key=f"{edit_prefix}_address")
                        st.date_input("배송일", key=f"{edit_prefix}_delivery")
                        CATEGORY_OPTIONS_EDIT = ["옷장", "식탁", "자녀방", "침대", "SSDS침대", "서재_학생", "소파", "소품", "전시품"]
                        existing_cats = [x.strip() for x in (orow["category"] or "").split(",") if x.strip()]
                        default_cats = [c for c in existing_cats if c in CATEGORY_OPTIONS_EDIT]
                        selected_categories_edit = st.multiselect(
                            "품목/카테고리 (복수 선택)",
                            options=CATEGORY_OPTIONS_EDIT,
                            default=default_cats,
                            key=f"{edit_prefix}_category_multiselect",
                        )
                        category_edit_val = ",".join(selected_categories_edit) if selected_categories_edit else None
                        def _fmt_total():
                            st.session_state[f"{edit_prefix}_total"] = _format_number_comma(st.session_state.get(f"{edit_prefix}_total", ""))
                        def _fmt_cost():
                            st.session_state[f"{edit_prefix}_cost"] = _format_number_comma(st.session_state.get(f"{edit_prefix}_cost", ""))
                        st.text_input("총 판매금액 (일반+전시)", key=f"{edit_prefix}_total", on_change=_fmt_total)
                        st.text_input("일반제품 원가", key=f"{edit_prefix}_cost", on_change=_fmt_cost)
                        st.text_input("방문 이유", key=f"{edit_prefix}_visit")
                        st.text_input("구매 이유", key=f"{edit_prefix}_purchase")
                        # 매출 수정: 매장 직원(user)은 직접 수정 불가 → 수정 요청만, 매장 관리자(store_admin)는 즉시 수정 가능 + 감사 로그 남김
                        if role == "user":
                            req_reason = st.text_area("수정 요청 사유(필수)", key=f"{edit_prefix}_req_reason")
                            if st.button("수정 요청", key=f"{edit_prefix}_request_btn"):
                                if not req_reason or not req_reason.strip():
                                    st.warning("수정 요청 사유를 입력하세요.")
                                else:
                                    payload = {
                                        "customer_id": int(cid),
                                        "order_id": int(sel_oid),
                                        "new_customer": {
                                            "name": st.session_state[f"{edit_prefix}_name"],
                                            "phone1": st.session_state[f"{edit_prefix}_phone1"],
                                            "phone2": st.session_state.get(f"{edit_prefix}_phone2"),
                                            "address": st.session_state.get(f"{edit_prefix}_address"),
                                        },
                                        "new_order": {
                                            "delivery_date": str(st.session_state.get(f"{edit_prefix}_delivery")),
                                            "category": category_edit_val,
                                            "total_amount": _parse_comma_to_int(st.session_state.get(f"{edit_prefix}_total", "0")),
                                            "cost_price": _parse_comma_to_int(st.session_state.get(f"{edit_prefix}_cost", "0")),
                                            "visit_reason": st.session_state.get(f"{edit_prefix}_visit"),
                                            "purchase_reason": st.session_state.get(f"{edit_prefix}_purchase"),
                                        },
                                    }
                                    conn = get_tenant_conn(db_filename)
                                    conn.execute(
                                        """
                                        INSERT INTO EditRequests (created_at, requested_by, entity_type, entity_id, payload, reason, status)
                                        VALUES (?, ?, ?, ?, ?, ?, 'pending')
                                        """,
                                        (
                                            datetime.now().isoformat(),
                                            _current_username(),
                                            "Order",
                                            int(sel_oid),
                                            json.dumps(payload, ensure_ascii=False),
                                            req_reason.strip(),
                                        ),
                                    )
                                    conn.commit()
                                    conn.close()
                                    st.success("수정 요청이 접수되었습니다. 매장 관리자 승인 후 반영됩니다.")
                                    st.rerun()
                        else:
                            edit_reason = st.text_area("변경 사유(필수)", key=f"{edit_prefix}_reason")
                            if st.button("수정 완료 (Update)", key=f"{edit_prefix}_update_btn"):
                                if not edit_reason or not edit_reason.strip():
                                    st.warning("변경 사유를 입력하세요.")
                                else:
                                    conn = get_tenant_conn(db_filename)
                                    # 기존 값
                                    old_total = orow["total_amount"] or 0
                                    old_cost = orow.get("cost_price") or 0
                                    old_visit = orow.get("visit_reason") or ""
                                    old_purchase = orow.get("purchase_reason") or ""
                                    d_new = st.session_state.get(f"{edit_prefix}_delivery")
                                    delivery_str = d_new.isoformat() if hasattr(d_new, "isoformat") else str(d_new)
                                    new_total = _parse_comma_to_int(st.session_state.get(f"{edit_prefix}_total", "0"))
                                    new_cost = _parse_comma_to_int(st.session_state.get(f"{edit_prefix}_cost", "0"))
                                    new_visit = st.session_state.get(f"{edit_prefix}_visit") or None
                                    new_purchase = st.session_state.get(f"{edit_prefix}_purchase") or None
                                    # 잔금 불일치 검증: 총 판매액 vs 결제 합계
                                    pay_sum_row = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM Payments WHERE order_id = ?", (sel_oid,)).fetchone()
                                    payment_total = float(pay_sum_row[0]) if pay_sum_row else 0
                                    balance_check = new_total - payment_total
                                    if balance_check != 0:
                                        st.error("⛔ 결제 금액 불일치: 총 판매액과 결제 내역의 합계가 다릅니다. 확인 후 저장하세요.")
                                        conn.close()
                                        st.stop()
                                    # 마진율 검증 (경고만, 저장 가능)
                                    margin_pct = (new_total - new_cost) / new_total * 100 if new_total else 0
                                    if margin_pct < 15 or margin_pct > 25:
                                        st.warning(f"⚠️ 주의: 마진율이 {margin_pct:.1f}%입니다. 적정 범위(15%~25%)를 벗어났습니다.")
                                    # 감사 로그: 판매 금액/원가/방문·구매 이유 변경
                                    if old_total != new_total:
                                        _insert_audit_log(conn, "Order", sel_oid, "total_amount", old_total, new_total, edit_reason)
                                        # 회계 원칙: 차액을 오늘 날짜로 Sales 신규 Row INSERT (과거 Row는 UPDATE 하지 않음)
                                        delta = new_total - old_total
                                        today_str = datetime.now().strftime("%Y-%m-%d")
                                        order_date_val = orow.get("order_date") or today_str
                                        if isinstance(order_date_val, str) and "-" in order_date_val:
                                            parts = order_date_val.split("-")
                                            if len(parts) >= 3:
                                                order_date_label = f"{int(parts[1])}월 {int(parts[2])}일"
                                            else:
                                                order_date_label = str(order_date_val)
                                        else:
                                            order_date_label = str(order_date_val)
                                        note = f"{order_date_label} 주문 건 금액 변경에 따른 {'차감' if delta < 0 else '추가'}"
                                        _insert_sales_transaction(db_filename, int(sel_oid), today_str, float(delta), note)
                                        if margin_pct < 15 or margin_pct > 25:
                                            store_name = _get_store_name_by_db(db_filename)
                                            _insert_admin_alert(store_name, "margin", f"{store_name}에서 마진율 {margin_pct:.1f}% 건이 수정되었습니다.")
                                    if old_cost != new_cost:
                                        _insert_audit_log(conn, "Order", sel_oid, "cost_price", old_cost, new_cost, edit_reason)
                                    if (old_visit or "") != (new_visit or ""):
                                        _insert_audit_log(conn, "Order", sel_oid, "visit_reason", old_visit, new_visit, edit_reason)
                                    if (old_purchase or "") != (new_purchase or ""):
                                        _insert_audit_log(conn, "Order", sel_oid, "purchase_reason", old_purchase, new_purchase, edit_reason)
                                    # 고객 정보 업데이트 (Supabase, id 기준)
                                    try:
                                        sc, _ = get_supabase_client()
                                        if sc:
                                            upd_cust = {
                                                "name": st.session_state[f"{edit_prefix}_name"].strip(),
                                                "phone1": st.session_state[f"{edit_prefix}_phone1"].strip(),
                                                "phone2": st.session_state.get(f"{edit_prefix}_phone2") or None,
                                                "address": st.session_state.get(f"{edit_prefix}_address") or None,
                                            }
                                            uq_cust = sc.table("customers").update(upd_cust).eq("id", cid)
                                            if _customers_tenant_column():
                                                uq_cust = uq_cust.eq(_customers_tenant_column(), db_filename)
                                            uq_cust.execute()
                                    except Exception:
                                        pass
                                    # 주문 정보 업데이트
                                    conn.execute(
                                        "UPDATE Orders SET delivery_date=?, category=?, total_amount=?, cost_price=?, visit_reason=?, purchase_reason=? WHERE id=?",
                                        (
                                            delivery_str,
                                            category_edit_val,
                                            new_total,
                                            new_cost,
                                            new_visit,
                                            new_purchase,
                                            sel_oid,
                                        ),
                                    )
                                    conn.commit()
                                    conn.close()
                                    clear_data_cache()
                                    st.toast("수정되었습니다.", icon="✅")
                                    st.rerun()

                st.subheader("결제 내역 조회 및 취소 (수단 변경 시)")
                st.caption("신용카드 → 현금 등으로 변경하려면: 아래에서 해당 결제를 취소한 뒤, 하단 '잔금 추가 결제'에서 같은 금액을 새 수단으로 등록하세요.")
                order_id_pay = st.selectbox("주문 선택", orders["id"].tolist(), key="gen_pay_list_order")
                if order_id_pay:
                    conn = get_tenant_conn(db_filename)
                    try:
                        pay_list = pd.read_sql(
                            "SELECT id, payment_date, amount, payment_method, card_company, fee_amount FROM Payments WHERE order_id = ? ORDER BY id",
                            conn, params=(order_id_pay,)
                        )
                    finally:
                        conn.close()
                    if len(pay_list) == 0:
                        st.info("해당 주문의 결제 내역이 없습니다.")
                    else:
                        pay_display = pay_list.copy()
                        pay_display["amount"] = pay_display["amount"].apply(lambda x: f"{x:,.0f}원")
                        pay_display["fee_amount"] = pay_display["fee_amount"].fillna(0).apply(lambda x: f"{x:,.0f}원")
                        pay_display = pay_display.rename(columns={"id": "결제ID", "payment_date": "결제일", "amount": "금액", "payment_method": "수단", "card_company": "카드사", "fee_amount": "수수료"})
                        st.dataframe(pay_display[["결제ID", "결제일", "금액", "수단", "카드사", "수수료"]], use_container_width=True)
                        order_row = orders[orders["id"] == order_id_pay].iloc[0]
                        total_sales = float(order_row["total_amount"] or 0)
                        current_balance = float(order_row["balance"] or 0)
                        customer_name_for_receipt = (customers[customers["id"] == cid].iloc[0]["name"] or "고객").strip()
                        for _, prow in pay_list.iterrows():
                            with st.expander(f"결제 ID {prow['id']} — {prow['payment_method'] or '-'} {prow['amount']:,.0f}원"):
                                col_left, col_right = st.columns(2)
                                with col_left:
                                    st.info("**기존 결제 내역 (비교용)**")
                                    st.write(f"**총판매금액:** {total_sales:,.0f}원")
                                    st.write(f"**기존 결제수단:** {prow['payment_method'] or '-'}")
                                    st.write(f"**결제금액:** {float(prow['amount'] or 0):,.0f}원")
                                    st.write(f"**미수금:** {current_balance:,.0f}원")
                                with col_right:
                                    new_amount = st.number_input(
                                        "변경할 새 금액 (0이면 결제 취소)", min_value=0.0,
                                        value=float(prow["amount"] or 0), step=1000.0,
                                        key=f"pay_edit_amt_{prow['id']}",
                                    )
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
                                                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                                                ext = (receipt_upload.name or "").split(".")[-1].lower() or "jpg"
                                                if ext not in ("png", "jpg", "jpeg"):
                                                    ext = "jpg"
                                                fname = f"receipt_{safe_name}_{ts}.{ext}"
                                                receipt_path_saved = os.path.join(RECEIPTS_UPLOAD_DIR, fname)
                                                with open(receipt_path_saved, "wb") as f:
                                                    f.write(receipt_upload.getvalue())
                                            conn = get_tenant_conn(db_filename)
                                            cur = conn.execute(
                                                "SELECT COALESCE(SUM(amount),0) FROM Payments WHERE order_id = ?",
                                                (order_id_pay,),
                                            )
                                            old_paid_total = cur.fetchone()[0] or 0
                                            old_balance = float(orders[orders["id"] == order_id_pay]["balance"].iloc[0])
                                            old_payment = {
                                                "payment_id": int(prow["id"]),
                                                "amount": float(prow["amount"] or 0),
                                                "method": prow["payment_method"],
                                                "card_company": prow["card_company"],
                                            }
                                            if new_amount == 0:
                                                conn.execute("DELETE FROM Payments WHERE id = ?", (prow["id"],))
                                                action = "결제취소"
                                                new_payment = {}
                                            else:
                                                conn.execute(
                                                    "UPDATE Payments SET amount = ? WHERE id = ?",
                                                    (new_amount, int(prow["id"])),
                                                )
                                                action = "금액변경"
                                                new_payment = {
                                                    "payment_id": int(prow["id"]),
                                                    "amount": float(new_amount),
                                                }
                                            _recalc_order_actual_margin(conn, order_id_pay)
                                            cur2 = conn.execute(
                                                "SELECT COALESCE(SUM(amount),0) FROM Payments WHERE order_id = ?",
                                                (order_id_pay,),
                                            )
                                            new_paid_total = cur2.fetchone()[0] or 0
                                            new_balance = (old_balance + float(prow["amount"]) - float(new_amount)) if new_amount > 0 else old_balance + float(prow["amount"])
                                            _insert_audit_log(conn, "Order", order_id_pay, "payment_total", old_paid_total, new_paid_total, del_reason)
                                            _insert_audit_log(conn, "Order", order_id_pay, "balance_amount", old_balance, new_balance, del_reason)
                                            cur_cid = conn.execute("SELECT customer_id FROM Orders WHERE id = ?", (order_id_pay,)).fetchone()
                                            cid_ph = cur_cid[0] if cur_cid else None
                                            customer_name_ph = _get_customer_name_supabase(db_filename, cid_ph) if cid_ph else ""
                                            old_data = {
                                                "order_id": int(order_id_pay),
                                                "paid_total_before": old_paid_total,
                                                "balance_before": old_balance,
                                                "payment": old_payment,
                                            }
                                            new_data = {
                                                "order_id": int(order_id_pay),
                                                "paid_total_after": new_paid_total,
                                                "balance_after": new_balance,
                                                "payment": new_payment,
                                            }
                                            _insert_payment_history(conn, order_id_pay, customer_name_ph, action, old_data, new_data, del_reason, receipt_image_path=receipt_path_saved)
                                            conn.commit()
                                            conn.close()
                                            st.toast("✅ 결제 내역과 영수증이 성공적으로 업데이트되었습니다!")
                                            st.balloons()
                                            clear_data_cache()
                                            st.toast("저장되었습니다. 같은 금액을 다른 수단으로 등록하려면 아래 '잔금 추가 결제'를 이용하세요.", icon="✅")
                                            st.rerun()

                st.subheader("잔금 추가 결제")
                order_id_sel = st.selectbox("주문 선택", orders["id"].tolist(), key="gen_pay_order")
                if order_id_sel:
                    bal = float(orders[orders["id"] == order_id_sel]["balance"].iloc[0])
                    _customer_balance_payment_ui(db_filename, order_id_sel, bal, key_prefix="gen_pay")

    # ---------- 탭 2: 다가오는 미수금 (D-10 이내) ----------
    order_cols_d10 = "id, customer_id, order_date, delivery_date, total_amount, cost_price, category, employee_names"
    with tab_d10:
        st.subheader("다가오는 미수금 (배송일 0~10일 이내)")
        orders = load_orders_cached(db_filename, order_cols_d10, limit=None)
        payments = load_payments_cached(db_filename)
        customers = load_customers_cached(db_filename, limit=None)
        if len(orders) == 0:
            st.info("주문 데이터가 없습니다.")
        else:
            pay_sum = payments.groupby("order_id")["amount"].sum()
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
                st.dataframe(_format_df_display(df_d10, ["잔금"]), use_container_width=True)
                for _, row in list_d10.iterrows():
                    with st.expander(f"💰 {row['name']} — 잔금 {row['balance']:,.0f}원"):
                        _customer_balance_payment_ui(db_filename, row["id"], row["balance"], key_prefix=f"d10_{row['id']}")
            else:
                st.info("해당 조건의 미수금 고객이 없습니다.")

    # ---------- 탭 3: 🚨 경고! 미결 금액 (배송일 지남 + 미수금) ----------
    with tab_overdue:
        st.error("🚨 배송일이 이미 지났는데 잔금이 남아 있는 고객 목록입니다. 우선 완납 유도가 필요합니다.")
        orders = load_orders_cached(db_filename, order_cols_d10, limit=None)
        payments = load_payments_cached(db_filename)
        customers = load_customers_cached(db_filename, limit=None)
        if len(orders) == 0:
            st.info("주문 데이터가 없습니다.")
        else:
            pay_sum = payments.groupby("order_id")["amount"].sum()
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
                st.dataframe(_format_df_display(df_over, ["잔금"]), use_container_width=True)
                for _, row in list_overdue.iterrows():
                    with st.expander(f"🚨 {row['name']} — 잔금 {row['balance']:,.0f}원 (배송일 지남)"):
                        _customer_balance_payment_ui(db_filename, row["id"], row["balance"], key_prefix=f"over_{row['id']}")
            else:
                st.success("✅ 배송일 지난 미수금 고객이 없습니다.")


# ========== 탭 0: 경영 대시보드 (로그인 후 첫 화면) ==========

def render_dashboard():
    db_filename = st.session_state.get("current_db")
    if not db_filename:
        st.warning("매장에 로그인한 후 이용하세요.")
        return
    _render_recent_notices_section()
    conn = get_tenant_conn(db_filename)
    if not conn:
        st.error("매장 DB를 찾을 수 없습니다.")
        return
    st.header("경영 대시보드 및 인수인계")
    try:
        cur = conn.execute("PRAGMA table_info(Orders)")
        order_cols = [row[1] for row in cur.fetchall()]
        order_col_list = "id, customer_id, order_date, delivery_date, total_amount, cost_price, actual_margin, employee_names, category"
        if "display_sales_amount" in order_cols:
            order_col_list += ", display_sales_amount"
        if "display_cost_amount" in order_cols:
            order_col_list += ", display_cost_amount"
        if "balance_status" in order_cols:
            order_col_list += ", balance_status"
    finally:
        conn.close()
    orders = load_orders_cached(db_filename, order_col_list, limit=None)
    customers = load_customers_cached(db_filename, limit=None)
    sales_df = load_sales_cached(db_filename, limit=None)
    payments = load_payments_cached(db_filename)
    todos_df = load_todos_cached(db_filename)
    if "display_sales_amount" not in orders.columns:
        orders["display_sales_amount"] = 0
    if "display_cost_amount" not in orders.columns:
        orders["display_cost_amount"] = 0
    orders["display_sales_amount"] = orders["display_sales_amount"].fillna(0).astype(int)
    orders["display_cost_amount"] = orders["display_cost_amount"].fillna(0).astype(int)

    today = date.today()
    # 잔금 불일치 경고: balance_status가 '완납'인데 실 계산상 잔금이 0이 아닌 건수
    if len(orders) > 0 and "balance_status" in orders.columns:
        pay_sum_for_warn = payments.groupby("order_id")["amount"].sum()
        warn_orders = orders.copy()
        warn_orders["paid"] = warn_orders["id"].map(pay_sum_for_warn).fillna(0)
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
                st.dataframe(show_df, use_container_width=True)
                st.caption("결제 금액을 수정하려면 **고객 및 잔금 관리** → 고객 선택 → **결제 내역 조회 및 취소** / **잔금 추가 결제**에서 해당 주문을 수정하세요.")
            if st.button("🔄 잔금 상태 자동 보정 (결제 합계 기준으로 완납/미납 다시 계산)", key="dashboard_balance_fix_btn"):
                conn = get_tenant_conn(db_filename)
                if conn:
                    try:
                        for oid in suspicious["id"].tolist():
                            _recalc_order_actual_margin(conn, int(oid))
                        conn.commit()
                        clear_data_cache()
                        st.toast(f"✅ {len(suspicious)}건 보정했습니다. 잔금 상태가 결제 합계에 맞게 갱신되었습니다.", icon="✅")
                        st.rerun()
                    except Exception as e:
                        st.error(f"보정 중 오류가 발생했습니다: {e}")
                    finally:
                        conn.close()
                else:
                    st.error("매장 DB를 찾을 수 없습니다.")

    # ---------- 1. 오늘의 핵심 지표 (일일매출 / 누적매출 / 당일 마진율 / 판매건수) — 맨 위 표 ----------
    with st.container():
        today_str = today.strftime("%Y-%m-%d")
        month_start = today.replace(day=1)
        if len(sales_df) > 0:
            sales_calc = sales_df.copy()
            sales_calc["transaction_date"] = pd.to_datetime(sales_calc["transaction_date"], errors="coerce")
            sales_calc = sales_calc.dropna(subset=["transaction_date"])
            today_mask = sales_calc["transaction_date"].dt.strftime("%Y-%m-%d") == today_str
            daily_total = float(sales_calc.loc[today_mask, "amount"].fillna(0).sum())
            month_mask = (sales_calc["transaction_date"].dt.date >= month_start) & (sales_calc["transaction_date"].dt.date <= today)
            cumulative = float(sales_calc.loc[month_mask, "amount"].fillna(0).sum())
        else:
            daily_total = 0.0
            cumulative = 0.0
        if len(orders) > 0 and "order_date" in orders.columns:
            orders_dt = orders.copy()
            orders_dt["order_date"] = pd.to_datetime(orders_dt["order_date"], errors="coerce")
            orders_today = orders_dt[orders_dt["order_date"].dt.strftime("%Y-%m-%d") == today_str]
            count_today = len(orders_today)
            if len(orders_today) > 0:
                tot_sales = (orders_today["total_amount"].fillna(0) + orders_today.get("display_sales_amount", 0).fillna(0)).sum()
                tot_cost = (orders_today["cost_price"].fillna(0) + orders_today.get("display_cost_amount", 0).fillna(0)).sum()
                margin_pct_today = (tot_sales - tot_cost) / tot_sales * 100 if tot_sales else 0.0
            else:
                margin_pct_today = 0.0
        else:
            count_today = 0
            margin_pct_today = 0.0
        st.subheader("1. 오늘의 핵심 지표")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("오늘 일일매출", f"{daily_total:,.0f}원")
        with c2:
            st.metric("누적매출", f"{cumulative:,.0f}원")
        with c3:
            st.metric("당일 마진율", f"{margin_pct_today:.1f}%")
        with c4:
            st.metric("판매건수", f"{count_today}건")

    # ---------- 2. 미수금 고객 현황: 배송일이 10일 이내로 남았거나 지났고, 잔금 > 0 ----------
    st.subheader("2. 미수금 고객 현황")
    if len(orders) > 0:
        pay_sum = payments.groupby("order_id")["amount"].sum()
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
            st.dataframe(unpaid_display, use_container_width=True)
        else:
            st.info("해당 조건의 미수금 고객이 없습니다. (배송일 10일 이내·잔금 있음)")
    else:
        st.info("아직 주문 데이터가 없습니다.")

    # ---------- 3. 이번 달 예상 매출 (Sales 테이블 transaction_date 기준 Net Sales) ----------
    st.subheader("3. 이번 달 예상 매출 (순매출)")
    month_start = today.replace(day=1)
    month_end_str = today.strftime("%Y-%m-%d")
    month_start_str = month_start.strftime("%Y-%m-%d")
    if len(sales_df) > 0:
        sales_df["transaction_date"] = pd.to_datetime(sales_df["transaction_date"], errors="coerce")
        sales_df = sales_df.dropna(subset=["transaction_date"])
        if len(sales_df) > 0:
            month_sales = sales_df[(sales_df["transaction_date"].dt.date >= month_start) & (sales_df["transaction_date"].dt.date <= today)]
            cumulative = float(month_sales["amount"].sum())
            days_elapsed = (today - month_start).days + 1
            avg_daily = cumulative / days_elapsed if days_elapsed else 0
            from calendar import monthrange
            days_in_month = monthrange(today.year, today.month)[1]
            days_left = days_in_month - days_elapsed
            projected = cumulative + avg_daily * days_left if days_left > 0 else cumulative
            st.metric("이번 달 누적 매출 (Net Sales)", f"{cumulative:,.0f}원")
            st.metric("이번 달 예상 매출 (일평균 기반)", f"{projected:,.0f}원")
        else:
            st.metric("이번 달 누적 매출 (Net Sales)", "0원")
    else:
        st.metric("이번 달 누적 매출 (Net Sales)", "0원")

    # ---------- 4. 월별 직원 판매 현황 및 평가 (1/n 실적 분배 + KPI) ----------
    st.subheader("4. 월별 직원 판매 현황 및 평가")
    if len(orders) > 0 and "order_date" in orders.columns:
        orders["order_date"] = pd.to_datetime(orders["order_date"], errors="coerce")
        order_dates = orders["order_date"].dropna()
        if len(order_dates) > 0:
            min_d = order_dates.min().to_pydatetime()
            max_d = order_dates.max().to_pydatetime()
            months_options = []
            y, m = min_d.year, min_d.month
            end_y, end_m = max_d.year, max_d.month
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
            month_start = date(sel_y, sel_m, 1)
            from calendar import monthrange
            month_end = date(sel_y, sel_m, monthrange(sel_y, sel_m)[1])
            orders_m = orders[(orders["order_date"].dt.date >= month_start) & (orders["order_date"].dt.date <= month_end)].copy()
            if "display_sales_amount" not in orders_m.columns:
                orders_m["display_sales_amount"] = 0
            orders_m["display_sales_amount"] = orders_m["display_sales_amount"].fillna(0).astype(int)
            orders_m["actual_margin"] = orders_m["actual_margin"].fillna(0)
            rows = []
            for _, r in orders_m.iterrows():
                emps = [e.strip() for e in (r.get("employee_names") or "").split(",") if e.strip()]
                n = len(emps) if emps else 1
                if not emps:
                    continue
                amt = float(r.get("total_amount") or 0)
                margin = float(r.get("actual_margin") or 0)
                display_amt = float(r.get("display_sales_amount") or 0)
                per_amt = amt / n
                per_margin = margin / n
                per_display = (display_amt / n) if n else 0
                for e in emps:
                    rows.append({"employee": e, "sales": per_amt, "margin": per_margin, "display_sales": per_display})
            if rows:
                emp_df = pd.DataFrame(rows).groupby("employee", as_index=False).agg({"sales": "sum", "margin": "sum", "display_sales": "sum"})
                total_sales = emp_df["sales"].sum() or 0
                total_margin = emp_df["margin"].sum() or 0
                total_display = emp_df["display_sales"].sum() or 0
                # ① 매출 80점, ② 마진 10점, ③ 전시품 10점, ④ 종합 (ZeroDivisionError 방지)
                emp_df["매출 점수(80)"] = (emp_df["sales"] / total_sales * 80).round(1) if total_sales else 0.0
                emp_df["마진 점수(10)"] = (emp_df["margin"] / total_margin * 10).round(1) if total_margin else 0.0
                emp_df["전시품 점수(10)"] = (emp_df["display_sales"] / total_display * 10).round(1) if total_display else 0.0
                emp_df["종합 점수"] = (emp_df["매출 점수(80)"] + emp_df["마진 점수(10)"] + emp_df["전시품 점수(10)"]).round(1)
                emp_df = emp_df.sort_values("종합 점수", ascending=False).reset_index(drop=True)
                emp_df["총 판매액"] = emp_df["sales"].round(0).astype(int)
                emp_df["마진액"] = emp_df["margin"].round(0).astype(int)
                emp_df["전시품 판매액"] = emp_df["display_sales"].round(0).astype(int)
                display_df = emp_df[["employee", "총 판매액", "마진액", "전시품 판매액", "매출 점수(80)", "마진 점수(10)", "전시품 점수(10)", "종합 점수"]].rename(columns={"employee": "직원명"})
                display_fmt = _format_df_display(display_df, ["총 판매액", "마진액", "전시품 판매액"])
                st.dataframe(display_fmt, use_container_width=True)
            else:
                st.info("선택한 월에 직원이 배정된 주문이 없습니다.")
        else:
            st.info("주문 일자가 없어 월별 집계를 할 수 없습니다.")
    else:
        st.info("주문 데이터가 없어 직원 평가를 할 수 없습니다.")

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
        orders["order_date"] = pd.to_datetime(orders["order_date"])
        period_orders = orders[(orders["order_date"].dt.date >= stats_start) & (orders["order_date"].dt.date <= stats_end)]
        total_contract = period_orders["total_amount"].sum()
        order_ids = period_orders["id"].tolist()
        if order_ids:
            conn2 = get_tenant_conn(db_filename)
            pay_per = pd.read_sql("SELECT order_id, amount FROM Payments WHERE order_id IN ({})".format(",".join("?" * len(order_ids))), conn2, params=order_ids)
            conn2.close()
            paid_per = pay_per.groupby("order_id")["amount"].sum()
            period_orders = period_orders.copy()
            period_orders["_paid"] = period_orders["id"].map(paid_per).fillna(0)
            period_orders["_bal"] = period_orders["total_amount"] - period_orders["_paid"]
            total_unpaid_period = period_orders["_bal"].clip(lower=0).sum()
        else:
            total_unpaid_period = 0
        st.metric("해당 기간 총 계약 금액", f"{total_contract:,.0f}원")
        st.metric("해당 기간 총 미수금", f"{total_unpaid_period:,.0f}원")
    else:
        st.metric("해당 기간 총 계약 금액", "0원")
        st.metric("해당 기간 총 미수금", "0원")

    # ---------- 6. To-Do 리스트 (직원 간 인수인계) ----------
    st.subheader("6. 직원 To-Do 리스트 (인수인계)")
    with st.form("todo_form"):
        author = st.text_input("작성자", value=st.session_state.get("current_user", {}).get("username", ""))
        content = st.text_area("내용")
        if st.form_submit_button("등록"):
            if content and content.strip():
                conn = get_tenant_conn(db_filename)
                conn.execute(
                    "INSERT INTO Todos (created_date, author, content, is_completed) VALUES (?, ?, ?, 0)",
                    (date.today().isoformat(), author or None, content.strip())
                )
                conn.commit()
                conn.close()
                st.rerun()
            else:
                st.warning("내용을 입력하세요.")
    if len(todos_df) > 0:
        for _, row in todos_df.iterrows():
            content_preview = (row["content"] or "")[:50]
            if len((row["content"] or "")) > 50:
                content_preview += "..."
            with st.expander(f"{'✅' if row['is_completed'] else '⬜'} {content_preview} (by {row['author']})"):
                st.caption(row["created_date"])
                st.write(row["content"] or "")
                if not row["is_completed"] and st.button("완료 처리", key=f"todo_done_{row['id']}"):
                    conn = get_tenant_conn(db_filename)
                    conn.execute("UPDATE Todos SET is_completed = 1 WHERE id = ?", (row["id"],))
                    conn.commit()
                    conn.close()
                    st.rerun()


# ========== 메인: 탭 구성 및 라우팅 ==========

def main():
    init_master_db()
    conn_m = get_master_conn()
    try:
        ensure_master_schema(conn_m)
    finally:
        conn_m.close()
    ensure_session()
    _inject_mobile_css()
    _inject_favicon()

    # 로그인 성공 직후: 브라우저 localStorage에 이메일 저장(한 번만 실행 후 플래그 제거)
    _pending = st.session_state.pop("_pending_save_login_email", None)
    if _pending:
        _val_js = json.dumps(str(_pending))
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
    if st.sidebar.button("🏠 첫 화면으로 (대시보드)", use_container_width=True, key="sidebar_home_btn"):
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
        if st.sidebar.button("🚨 결제 변경/취소 모니터링", use_container_width=True):
            st.session_state["active_admin_page"] = "payment_monitor"
    # 최고 관리자 전용: 직원 계정 관리 및 발령
    if role == "superadmin":
        if st.sidebar.button("👥 직원 관리", use_container_width=True):
            st.session_state["active_admin_page"] = "employee_management"
    if st.sidebar.button("🚪 로그아웃", use_container_width=True):
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

    # 최고 관리자 전용: 직원 계정 관리 및 발령
    if role == "superadmin" and st.session_state.get("active_admin_page") == "employee_management":
        render_employee_management()
        return

    # Superadmin: 5탭 최고 관리자 메뉴
    if role == "superadmin":
        render_superadmin()
        return

    # 일반/매장 관리자: 메뉴를 상단 셀렉트로 노출 (모바일에서도 잘 보이게)
    if role == "store_admin":
        tab_labels = ["경영 대시보드", "📊 마케팅 인사이트", "새로운 매출 등록", "고객 및 잔금 관리", "매장 관리자 메뉴", "월별 결제수단 집계표"]
    else:
        tab_labels = ["경영 대시보드", "📊 마케팅 인사이트", "새로운 매출 등록", "고객 및 잔금 관리"]
    if "main_tab_idx" not in st.session_state:
        st.session_state["main_tab_idx"] = 0
    if st.session_state["main_tab_idx"] >= len(tab_labels):
        st.session_state["main_tab_idx"] = 0
    # Supabase 연결 실패 시 친절한 경고
    if st.session_state.get("supabase_error"):
        st.error("⚠️ **Supabase 연결 실패**: " + st.session_state["supabase_error"] + " — .streamlit/secrets.toml의 [supabase] url, key를 확인해 주세요.")
    # 상단 메뉴 선택 (스마트폰에서 탭이 잘리지 않도록 셀렉트박스로 제공)
    st.markdown('<p style="margin:0 0 0.25rem 0; font-size:0.85rem; color:#666;">📱 메뉴 선택</p>', unsafe_allow_html=True)
    st.markdown('<p class="mobile-menu-hint" style="margin:0 0 0.35rem 0; font-size:0.8rem; color:#888;">로그아웃·비밀번호는 왼쪽 상단 ☰에서</p>', unsafe_allow_html=True)
    menu_sel = st.selectbox(
        "메뉴",
        tab_labels,
        index=st.session_state["main_tab_idx"],
        key="main_menu_select",
        label_visibility="collapsed",
    )
    idx = tab_labels.index(menu_sel)
    st.session_state["main_tab_idx"] = idx
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


if __name__ == "__main__":
    main()
