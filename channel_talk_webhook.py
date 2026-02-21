# -*- coding: utf-8 -*-
"""
채널톡 웹훅 수신 서버 (Channel Talk → 우리 DB PUSH 반영).
Channel Talk에서 이 URL로 POST 시, payload를 파싱해 Master DB 로그 기록 및 해당 매장 DB에 고객 등록.
실행: python channel_talk_webhook.py  (기본 포트 5050, 환경변수 PORT로 변경 가능)
"""
import os
import re
import sqlite3
from datetime import datetime

from flask import Flask, request, jsonify

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE_DIR, "databases")
MASTER_DB_PATH = os.path.join(DB_DIR, "master_system.db")
os.makedirs(DB_DIR, exist_ok=True)

app = Flask(__name__)


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
    """매장명에서 채널톡 태그용 매장 키 추출. env CHANNEL_TALK_STORE_TAG_KEYS 사용."""
    if not store_name or not str(store_name).strip():
        return "기타"
    name = str(store_name).strip()
    raw = os.environ.get("CHANNEL_TALK_STORE_TAG_KEYS", "삼산,학성,동구")
    for key in [k.strip() for k in raw.split(",") if k.strip()]:
        if key in name:
            return key
    if "삼산" in name:
        return "삼산"
    if "학성" in name:
        return "학성"
    return name.replace("점", "").strip() or "기타"


def get_db_filename_by_store_key(store_key: str):
    """매장키(예: 삼산)로 Stores 테이블에서 db_filename 조회. 없으면 None."""
    if not store_key or not str(store_key).strip():
        return None
    key = str(store_key).strip()
    conn = get_master_conn()
    try:
        rows = conn.execute("SELECT store_name, db_filename FROM Stores").fetchall()
        for store_name, db_filename in rows:
            if _store_tag_key_from_name(store_name) == key:
                return db_filename
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


@app.route("/webhook/channel-talk", methods=["POST"])
@app.route("/webhook/channel_talk", methods=["POST"])
def channel_talk_webhook():
    # 채널톡은 200 응답을 받으면 성공으로 간주. 우리 내부 실패는 로그에만 기록하고 200 반환.
    try:
        body = request.get_json(force=True, silent=True) or {}
    except Exception:
        body = {}
    data = body.get("data") or body
    user = data.get("user") or body.get("user") or data
    name = (user.get("name") or user.get("user", {}).get("name") or data.get("name") or body.get("name") or "").strip() or "고객"
    phone = user.get("mobileNumber") or user.get("mobile_number") or user.get("phone") or data.get("phone") or body.get("phone") or body.get("mobileNumber") or ""
    phone_clean = _normalize_phone(phone)
    tags = user.get("tags") or data.get("tags") or body.get("tags") or []
    store_key = _extract_store_key_from_tags(tags)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_id = None
    conn_m = get_master_conn()
    try:
        ensure_webhook_log_table(conn_m)
        conn_m.execute(
            """INSERT INTO ChannelTalkWebhookLog (created_at, store_key, phone, name, status, message, db_filename, customer_id)
               VALUES (?, ?, ?, ?, 'processing', NULL, NULL, NULL)""",
            (now, store_key, phone_clean or phone or None, name),
        )
        log_id = conn_m.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn_m.commit()
    except Exception as e:
        conn_m.rollback()
        return jsonify({"ok": False, "error": str(e)}), 200
    finally:
        conn_m.close()

    if not phone_clean:
        _update_log(log_id, "fail", "연락처 없음", None, None)
        return jsonify({"ok": True}), 200

    db_filename = get_db_filename_by_store_key(store_key) if store_key else None
    if not db_filename:
        _update_log(log_id, "fail", "매장키 미매칭(태그 형식: 매장키구매/품목)", None, None)
        return jsonify({"ok": True}), 200

    tenant = get_tenant_conn(db_filename)
    if not tenant:
        _update_log(log_id, "fail", "tenant DB 없음", db_filename, None)
        return jsonify({"ok": True}), 200

    try:
        ensure_customer_source(tenant)
        cur = tenant.execute("SELECT id FROM Customers WHERE REPLACE(REPLACE(REPLACE(phone1,' ',''),'-',''),'.','') = ?", (phone_clean,))
        existing = cur.fetchone()
        if existing:
            _update_log(log_id, "skipped", "이미 등록된 연락처", db_filename, existing[0])
            return jsonify({"ok": True}), 200
        tenant.execute(
            "INSERT INTO Customers (name, phone1, phone2, address, source) VALUES (?, ?, NULL, NULL, '채널톡_웹훅')",
            (name, phone.strip() if phone else phone_clean),
        )
        customer_id = tenant.execute("SELECT last_insert_rowid()").fetchone()[0]
        tenant.commit()
        _update_log(log_id, "success", None, db_filename, customer_id)
        return jsonify({"ok": True}), 200
    except Exception as e:
        tenant.rollback()
        _update_log(log_id, "fail", str(e), db_filename, None)
        return jsonify({"ok": True}), 200
    finally:
        tenant.close()


def _update_log(log_id: int, status: str, message: str | None, db_filename: str | None, customer_id: int | None):
    conn = get_master_conn()
    try:
        conn.execute(
            "UPDATE ChannelTalkWebhookLog SET status=?, message=?, db_filename=?, customer_id=? WHERE id=?",
            (status, message, db_filename, customer_id, log_id),
        )
        conn.commit()
    finally:
        conn.close()


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="0.0.0.0", port=port, debug=False)
