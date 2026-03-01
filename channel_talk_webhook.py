# -*- coding: utf-8 -*-
"""
채널톡 웹훅 수신 서버 (Channel Talk → 우리 DB PULL 반영).

Channel Talk에서 이 URL로 POST 시, payload를 파싱해
- Supabase app_customers 테이블에 고객 등록 (신규인 경우)
- Supabase channel_talk_webhook_log 테이블에 수신 로그 기록
- SQLite Customers 테이블에 동시 저장 (하위 호환)

실행: python channel_talk_webhook.py  (기본 포트 5050, 환경변수 PORT로 변경 가능)

환경변수 (또는 .env):
  SUPABASE_URL           Supabase 프로젝트 URL
  SUPABASE_SERVICE_KEY   Supabase service_role key (전체 쓰기 권한)
  CHANNEL_TALK_SECRET    채널톡 웹훅 서명 검증용 Secret (선택)
  CHANNEL_TALK_STORE_TAG_KEYS  매장키 목록 (쉼표 구분, 예: 삼산,학성,동구)
"""
import os
import re
import sqlite3
from datetime import datetime

import requests
from flask import Flask, request, jsonify

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE_DIR, "databases")
MASTER_DB_PATH = os.path.join(DB_DIR, "master_system.db")
os.makedirs(DB_DIR, exist_ok=True)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

app = Flask(__name__)


# ──────────────────────────────────────────────
# Supabase REST 헬퍼
# ──────────────────────────────────────────────

def _supa_headers() -> dict | None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _supa_table_url(table: str) -> str:
    return f"{SUPABASE_URL.rstrip('/')}/rest/v1/{table}"


def supa_find_store_name_by_key(store_key: str) -> str | None:
    """매장키(예: 삼산)로 Supabase app_stores에서 store_name 조회."""
    h = _supa_headers()
    if not h or not store_key:
        return None
    try:
        r = requests.get(
            _supa_table_url("app_stores"),
            headers=h,
            params={"select": "store_name", "limit": 50},
            timeout=5,
        )
        if r.status_code != 200:
            return None
        stores = r.json()
        for s in (stores or []):
            name = (s.get("store_name") or "").strip()
            if store_key in name:
                return name
        return None
    except Exception:
        return None


def supa_find_customer_by_phone(store_name: str, phone_clean: str) -> int | None:
    """Supabase app_customers에서 동일 연락처 고객 id 조회. 없으면 None."""
    h = _supa_headers()
    if not h:
        return None
    try:
        r = requests.get(
            _supa_table_url("app_customers"),
            headers=h,
            params={
                "select": "id",
                "store_name": f"eq.{store_name}",
                "or": f"(phone1.eq.{phone_clean},phone2.eq.{phone_clean})",
                "limit": 1,
            },
            timeout=5,
        )
        if r.status_code == 200:
            rows = r.json()
            if rows:
                return int(rows[0]["id"])
        return None
    except Exception:
        return None


def supa_insert_customer(store_name: str, name: str, phone: str, phone_clean: str) -> int | None:
    """Supabase app_customers에 신규 고객 등록. 성공 시 id 반환, 실패 시 None."""
    h = _supa_headers()
    if not h:
        return None
    payload = {
        "store_name": store_name,
        "name": name or "고객",
        "phone1": phone.strip() if phone else phone_clean,
        "phone2": None,
        "address": None,
        "source": "채널톡_웹훅",
    }
    try:
        r = requests.post(
            _supa_table_url("app_customers"),
            headers=h,
            json=payload,
            timeout=5,
        )
        if r.status_code in (200, 201):
            rows = r.json()
            if isinstance(rows, list) and rows:
                return int(rows[0]["id"])
        return None
    except Exception:
        return None


def supa_insert_webhook_log(
    created_at: str,
    store_key: str | None,
    phone: str | None,
    name: str,
    status: str,
    message: str | None,
    store_name: str | None,
    customer_id: int | None,
) -> int | None:
    """Supabase channel_talk_webhook_log 테이블에 로그 삽입. 성공 시 id 반환."""
    h = _supa_headers()
    if not h:
        return None
    payload = {
        "created_at": created_at,
        "store_key": store_key,
        "phone": phone,
        "name": name,
        "status": status,
        "message": message,
        "store_name": store_name,
        "customer_id": customer_id,
    }
    try:
        r = requests.post(
            _supa_table_url("channel_talk_webhook_log"),
            headers=h,
            json=payload,
            timeout=5,
        )
        if r.status_code in (200, 201):
            rows = r.json()
            if isinstance(rows, list) and rows:
                return int(rows[0]["id"])
        return None
    except Exception:
        return None


def supa_update_webhook_log(log_id: int, status: str, message: str | None, customer_id: int | None):
    """Supabase channel_talk_webhook_log 로그 상태 업데이트."""
    h = _supa_headers()
    if not h or not log_id:
        return
    payload = {"status": status}
    if message is not None:
        payload["message"] = message
    if customer_id is not None:
        payload["customer_id"] = customer_id
    try:
        requests.patch(
            _supa_table_url("channel_talk_webhook_log"),
            headers={**h, "Prefer": "return=minimal"},
            params={"id": f"eq.{log_id}"},
            json=payload,
            timeout=5,
        )
    except Exception:
        pass


# ──────────────────────────────────────────────
# SQLite 헬퍼 (하위 호환)
# ──────────────────────────────────────────────

def get_master_conn():
    return sqlite3.connect(MASTER_DB_PATH)


def get_tenant_conn(db_filename: str):
    if not db_filename:
        return None
    path = os.path.join(DB_DIR, db_filename)
    if not os.path.isfile(path):
        return None
    return sqlite3.connect(path)


def _store_tag_key_from_name(store_name: str) -> str:
    """매장명에서 채널톡 태그용 매장 키 추출."""
    if not store_name or not str(store_name).strip():
        return "기타"
    name = str(store_name).strip()
    raw = os.environ.get("CHANNEL_TALK_STORE_TAG_KEYS", "삼산,학성,동구")
    for key in [k.strip() for k in raw.split(",") if k.strip()]:
        if key in name:
            return key
    return name.replace("점", "").strip() or "기타"


def get_db_filename_by_store_key(store_key: str):
    """매장키로 SQLite master DB에서 db_filename 조회."""
    if not store_key:
        return None
    conn = get_master_conn()
    try:
        rows = conn.execute("SELECT store_name, db_filename FROM Stores").fetchall()
        for store_name, db_filename in rows:
            if _store_tag_key_from_name(store_name) == str(store_key).strip():
                return db_filename
        return None
    except Exception:
        return None
    finally:
        conn.close()


def ensure_webhook_log_table(conn: sqlite3.Connection):
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


def ensure_customer_source(conn: sqlite3.Connection):
    cur = conn.execute("PRAGMA table_info(Customers)")
    cols = [row[1] for row in cur.fetchall()]
    if "source" not in cols:
        conn.execute("ALTER TABLE Customers ADD COLUMN source TEXT")
        conn.commit()


def _extract_store_key_from_tags(tags) -> str | None:
    """태그 목록에서 'XXX구매/YYY' 형식의 태그가 있으면 XXX(매장키) 반환."""
    if not tags:
        return None
    if isinstance(tags, str):
        tags = [tags]
    for t in tags:
        s = (t or "").strip()
        m = re.match(r"^(.+?)구매/", s)
        if m:
            return m.group(1).strip()
    return None


def _normalize_phone(phone: str) -> str:
    return re.sub(r"\D", "", str(phone or "").strip()) if phone else ""


def _sqlite_insert_customer_fallback(db_filename: str, name: str, phone: str, phone_clean: str):
    """SQLite Customers 테이블에도 동시 저장 (하위 호환). 실패해도 무시."""
    if not db_filename:
        return None
    tenant = get_tenant_conn(db_filename)
    if not tenant:
        return None
    try:
        ensure_customer_source(tenant)
        cur = tenant.execute(
            "SELECT id FROM Customers WHERE REPLACE(REPLACE(REPLACE(phone1,' ',''),'-',''),'.','') = ?",
            (phone_clean,),
        )
        existing = cur.fetchone()
        if existing:
            return existing[0]
        tenant.execute(
            "INSERT INTO Customers (name, phone1, phone2, address, source) VALUES (?, ?, NULL, NULL, '채널톡_웹훅')",
            (name, phone.strip() if phone else phone_clean),
        )
        customer_id = tenant.execute("SELECT last_insert_rowid()").fetchone()[0]
        tenant.commit()
        return customer_id
    except Exception:
        return None
    finally:
        tenant.close()


def _sqlite_log(log_id_supa: int | None, created_at: str, store_key: str | None,
                phone_clean: str, name: str, status: str, message: str | None,
                db_filename: str | None, customer_id: int | None):
    """SQLite master DB ChannelTalkWebhookLog에도 로그 저장."""
    conn = get_master_conn()
    try:
        ensure_webhook_log_table(conn)
        conn.execute(
            """INSERT INTO ChannelTalkWebhookLog
               (created_at, store_key, phone, name, status, message, db_filename, customer_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (created_at, store_key, phone_clean or None, name, status, message, db_filename, customer_id),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# ──────────────────────────────────────────────
# 웹훅 엔드포인트
# ──────────────────────────────────────────────

@app.route("/webhook/channel-talk", methods=["POST"])
@app.route("/webhook/channel_talk", methods=["POST"])
def channel_talk_webhook():
    """
    채널톡이 이 URL로 POST할 때 고객 데이터를 수신해 Supabase + SQLite에 저장.
    채널톡은 200 응답을 받으면 성공으로 간주하므로, 내부 오류도 200을 반환.
    """
    try:
        body = request.get_json(force=True, silent=True) or {}
    except Exception:
        body = {}

    data = body.get("data") or body
    user = data.get("user") or body.get("user") or data
    name = (
        user.get("name") or user.get("user", {}).get("name")
        or data.get("name") or body.get("name") or ""
    ).strip() or "고객"
    phone = (
        user.get("mobileNumber") or user.get("mobile_number") or user.get("phone")
        or data.get("phone") or body.get("phone") or body.get("mobileNumber") or ""
    )
    phone_clean = _normalize_phone(phone)
    tags = user.get("tags") or data.get("tags") or body.get("tags") or []
    store_key = _extract_store_key_from_tags(tags)

    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    supa_log_id = None

    # ── 1) 초기 로그 (Supabase)
    store_name_for_supa = supa_find_store_name_by_key(store_key) if store_key else None
    supa_log_id = supa_insert_webhook_log(
        created_at=now,
        store_key=store_key,
        phone=phone_clean or phone or None,
        name=name,
        status="processing",
        message=None,
        store_name=store_name_for_supa,
        customer_id=None,
    )

    if not phone_clean:
        supa_update_webhook_log(supa_log_id, "fail", "연락처 없음", None)
        _sqlite_log(supa_log_id, now, store_key, "", name, "fail", "연락처 없음", None, None)
        return jsonify({"ok": True}), 200

    if not store_key:
        supa_update_webhook_log(supa_log_id, "fail", "매장키 미매칭(태그 형식: 매장키구매/품목)", None)
        _sqlite_log(supa_log_id, now, store_key, phone_clean, name, "fail", "매장키 미매칭", None, None)
        return jsonify({"ok": True}), 200

    # ── 2) Supabase app_customers에 고객 저장
    supa_customer_id = None
    if store_name_for_supa:
        existing_id = supa_find_customer_by_phone(store_name_for_supa, phone_clean)
        if existing_id:
            supa_update_webhook_log(supa_log_id, "skipped", "이미 등록된 연락처", existing_id)
            supa_customer_id = existing_id
        else:
            new_id = supa_insert_customer(store_name_for_supa, name, phone, phone_clean)
            if new_id:
                supa_update_webhook_log(supa_log_id, "success", None, new_id)
                supa_customer_id = new_id
            else:
                supa_update_webhook_log(supa_log_id, "fail", "Supabase 고객 등록 실패", None)
    else:
        supa_update_webhook_log(supa_log_id, "fail", f"매장키 '{store_key}'에 해당하는 매장을 Supabase에서 찾지 못함", None)

    # ── 3) SQLite 하위 호환 저장
    db_filename = get_db_filename_by_store_key(store_key)
    sqlite_cust_id = _sqlite_insert_customer_fallback(db_filename, name, phone, phone_clean)
    _sqlite_log(
        supa_log_id, now, store_key, phone_clean, name,
        "success" if (supa_customer_id or sqlite_cust_id) else "fail",
        None if (supa_customer_id or sqlite_cust_id) else "모든 DB 저장 실패",
        db_filename,
        supa_customer_id or sqlite_cust_id,
    )

    return jsonify({"ok": True}), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "supabase": bool(SUPABASE_URL)}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)
