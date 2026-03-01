# -*- coding: utf-8 -*-
"""
채널톡 웹훅 수신 FastAPI 서버 (독립 실행 프로세스).

채널톡에서 이 서버로 POST 요청이 들어오면:
  1. Pydantic 모델로 페이로드 파싱
  2. Supabase app_customers 에 신규 고객 등록 (중복 제외)
  3. Supabase channel_talk_webhook_log 에 수신 이력 기록
  4. SQLite Customers 에도 동시 저장 (하위 호환)
  5. {"status": "success"} + HTTP 200 반환

실행:
  uvicorn api:app --host 0.0.0.0 --port 8000 --reload

환경변수 (또는 .env):
  SUPABASE_URL              Supabase 프로젝트 URL
  SUPABASE_SERVICE_KEY      service_role key (전체 쓰기 권한)
  CHANNEL_TALK_STORE_TAG_KEYS  매장키 목록 (쉼표 구분, 예: 삼산,학성,평산)
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE_DIR, "databases")
MASTER_DB_PATH = os.path.join(DB_DIR, "master_system.db")
os.makedirs(DB_DIR, exist_ok=True)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

app = FastAPI(
    title="이몬스 채널톡 웹훅 API",
    description="채널톡 → Supabase/SQLite 고객 데이터 수신 서버",
    version="2.0.0",
)


# ──────────────────────────────────────────────
# Pydantic 스키마 (채널톡 페이로드 파싱)
# ──────────────────────────────────────────────

class ChannelTalkUserProfile(BaseModel):
    """채널톡 user.profile 또는 data.user 필드 내 사용자 정보."""
    name: str | None = None
    mobileNumber: str | None = Field(None, alias="mobileNumber")
    mobile_number: str | None = None
    phone: str | None = None
    tags: list[str] | None = None

    model_config = {"populate_by_name": True, "extra": "allow"}


class ChannelTalkUserWrapper(BaseModel):
    """채널톡 data.user 또는 body.user 래퍼."""
    name: str | None = None
    mobileNumber: str | None = Field(None, alias="mobileNumber")
    mobile_number: str | None = None
    phone: str | None = None
    tags: list[str] | None = None
    profile: ChannelTalkUserProfile | None = None

    model_config = {"populate_by_name": True, "extra": "allow"}


class ChannelTalkData(BaseModel):
    """채널톡 body.data 필드."""
    user: ChannelTalkUserWrapper | None = None
    name: str | None = None
    phone: str | None = None
    mobileNumber: str | None = Field(None, alias="mobileNumber")
    tags: list[str] | None = None

    model_config = {"populate_by_name": True, "extra": "allow"}


class ChannelTalkPayload(BaseModel):
    """채널톡 웹훅 최상위 페이로드."""
    data: ChannelTalkData | None = None
    user: ChannelTalkUserWrapper | None = None
    name: str | None = None
    phone: str | None = None
    mobileNumber: str | None = Field(None, alias="mobileNumber")
    tags: list[str] | None = None

    model_config = {"populate_by_name": True, "extra": "allow"}


# ──────────────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────────────

def _normalize_phone(phone: str | None) -> str:
    """전화번호에서 숫자만 추출."""
    return re.sub(r"\D", "", str(phone or "").strip()) if phone else ""


def _extract_name_phone_tags(payload: ChannelTalkPayload) -> tuple[str, str, str, list[str]]:
    """
    채널톡 페이로드 중첩 구조에서 name / phone_raw / phone_clean / tags 를 추출.
    채널톡은 이벤트 종류마다 구조가 다르므로 다단계 폴백으로 처리.
    """
    data = payload.data or ChannelTalkData()
    user_top = payload.user or ChannelTalkUserWrapper()
    user_data = (data.user or ChannelTalkUserWrapper())

    # name 우선순위: data.user > body.user > data > body
    name = (
        user_data.name or user_top.name
        or data.name or payload.name or ""
    ).strip() or "고객"

    # phone 우선순위: data.user > body.user > data > body
    phone_raw = (
        user_data.mobileNumber or user_data.mobile_number or user_data.phone
        or user_top.mobileNumber or user_top.mobile_number or user_top.phone
        or data.mobileNumber or data.phone
        or payload.mobileNumber or payload.phone
        or ""
    )

    # tags 우선순위: data.user > body.user > data > body
    tags = (
        user_data.tags or user_top.tags
        or data.tags or payload.tags or []
    )

    phone_clean = _normalize_phone(phone_raw)
    return name, phone_raw, phone_clean, tags


def _extract_store_key_from_tags(tags: list[str]) -> str | None:
    """태그 목록에서 'XXX구매/YYY' 패턴이 있으면 XXX(매장키)를 반환."""
    for t in tags:
        m = re.match(r"^(.+?)구매/", (t or "").strip())
        if m:
            return m.group(1).strip()
    return None


def _store_tag_key_from_name(store_name: str) -> str:
    """매장명에서 환경변수 기준으로 태그 키를 추출."""
    if not store_name:
        return "기타"
    raw = os.environ.get("CHANNEL_TALK_STORE_TAG_KEYS", "삼산,학성,평산")
    for key in [k.strip() for k in raw.split(",") if k.strip()]:
        if key in store_name:
            return key
    return store_name.replace("점", "").strip() or "기타"


# ──────────────────────────────────────────────
# Supabase REST 헬퍼 (httpx 비동기)
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


def _supa_url(table: str) -> str:
    return f"{SUPABASE_URL.rstrip('/')}/rest/v1/{table}"


async def supa_find_store_name(store_key: str) -> str | None:
    """매장키로 app_stores 테이블에서 store_name 조회."""
    h = _supa_headers()
    if not h or not store_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.get(
                _supa_url("app_stores"),
                headers=h,
                params={"select": "store_name", "limit": 50},
            )
        if r.status_code == 200:
            for s in (r.json() or []):
                name = (s.get("store_name") or "").strip()
                if store_key in name:
                    return name
    except Exception as e:
        logger.warning("supa_find_store_name error: %s", e)
    return None


async def supa_find_customer_by_phone(store_name: str, phone_clean: str) -> int | None:
    """app_customers 에서 동일 연락처 고객 id 조회. 없으면 None."""
    h = _supa_headers()
    if not h:
        return None
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.get(
                _supa_url("app_customers"),
                headers=h,
                params={
                    "select": "id",
                    "store_name": f"eq.{store_name}",
                    "or": f"(phone1.eq.{phone_clean},phone2.eq.{phone_clean})",
                    "limit": 1,
                },
            )
        if r.status_code == 200:
            rows = r.json()
            if rows:
                return int(rows[0]["id"])
    except Exception as e:
        logger.warning("supa_find_customer error: %s", e)
    return None


async def supa_insert_customer(
    store_name: str, name: str, phone_raw: str, phone_clean: str
) -> int | None:
    """app_customers 에 신규 고객 등록. 성공 시 id 반환."""
    h = _supa_headers()
    if not h:
        return None
    payload = {
        "store_name": store_name,
        "name": name or "고객",
        "phone1": phone_raw.strip() if phone_raw else phone_clean,
        "phone2": None,
        "address": None,
        "source": "채널톡_웹훅",
    }
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.post(_supa_url("app_customers"), headers=h, json=payload)
        if r.status_code in (200, 201):
            rows = r.json()
            if isinstance(rows, list) and rows:
                return int(rows[0]["id"])
        logger.warning("supa_insert_customer failed: %s %s", r.status_code, r.text[:200])
    except Exception as e:
        logger.warning("supa_insert_customer error: %s", e)
    return None


async def supa_insert_log(
    created_at: str,
    store_key: str | None,
    phone: str | None,
    name: str,
    status: str,
    message: str | None,
    store_name: str | None,
    customer_id: int | None,
) -> int | None:
    """channel_talk_webhook_log 에 로그 행 삽입. 성공 시 id 반환."""
    h = _supa_headers()
    if not h:
        return None
    payload: dict[str, Any] = {
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
        async with httpx.AsyncClient(timeout=6) as client:
            r = await client.post(_supa_url("channel_talk_webhook_log"), headers=h, json=payload)
        if r.status_code in (200, 201):
            rows = r.json()
            if isinstance(rows, list) and rows:
                return int(rows[0]["id"])
    except Exception as e:
        logger.warning("supa_insert_log error: %s", e)
    return None


async def supa_update_log(
    log_id: int | None,
    status: str,
    message: str | None = None,
    customer_id: int | None = None,
) -> None:
    """channel_talk_webhook_log 의 기존 행을 업데이트."""
    if not log_id:
        return
    h = _supa_headers()
    if not h:
        return
    patch: dict[str, Any] = {"status": status}
    if message is not None:
        patch["message"] = message
    if customer_id is not None:
        patch["customer_id"] = customer_id
    try:
        async with httpx.AsyncClient(timeout=6) as client:
            await client.patch(
                _supa_url("channel_talk_webhook_log"),
                headers={**h, "Prefer": "return=minimal"},
                params={"id": f"eq.{log_id}"},
                json=patch,
            )
    except Exception as e:
        logger.warning("supa_update_log error: %s", e)


# ──────────────────────────────────────────────
# SQLite 헬퍼 (하위 호환, 동기)
# ──────────────────────────────────────────────

def _sqlite_master_conn() -> sqlite3.Connection:
    return sqlite3.connect(MASTER_DB_PATH)


def _sqlite_tenant_conn(db_filename: str) -> sqlite3.Connection | None:
    if not db_filename:
        return None
    path = os.path.join(DB_DIR, db_filename)
    return sqlite3.connect(path) if os.path.isfile(path) else None


def _sqlite_get_db_filename(store_key: str) -> str | None:
    """SQLite master DB Stores 테이블에서 매장키로 db_filename 조회."""
    if not store_key:
        return None
    conn = _sqlite_master_conn()
    try:
        rows = conn.execute("SELECT store_name, db_filename FROM Stores").fetchall()
        for store_name, db_filename in rows:
            if _store_tag_key_from_name(store_name) == store_key:
                return db_filename
    except Exception:
        pass
    finally:
        conn.close()
    return None


def _sqlite_ensure_log_table(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ChannelTalkWebhookLog (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at  TEXT NOT NULL,
            store_key   TEXT,
            phone       TEXT,
            name        TEXT,
            status      TEXT NOT NULL,
            message     TEXT,
            db_filename TEXT,
            customer_id INTEGER
        )
    """)
    conn.commit()


def _sqlite_ensure_source_col(conn: sqlite3.Connection) -> None:
    cols = [row[1] for row in conn.execute("PRAGMA table_info(Customers)").fetchall()]
    if "source" not in cols:
        conn.execute("ALTER TABLE Customers ADD COLUMN source TEXT")
        conn.commit()


def _sqlite_upsert_customer(
    db_filename: str | None, name: str, phone_raw: str, phone_clean: str
) -> int | None:
    """SQLite Customers 테이블에 고객 저장 (중복이면 기존 id 반환)."""
    if not db_filename:
        return None
    tenant = _sqlite_tenant_conn(db_filename)
    if not tenant:
        return None
    try:
        _sqlite_ensure_source_col(tenant)
        row = tenant.execute(
            "SELECT id FROM Customers WHERE REPLACE(REPLACE(REPLACE(phone1,' ',''),'-',''),'.','') = ?",
            (phone_clean,),
        ).fetchone()
        if row:
            return row[0]
        tenant.execute(
            "INSERT INTO Customers (name, phone1, phone2, address, source) VALUES (?, ?, NULL, NULL, '채널톡_웹훅')",
            (name, phone_raw.strip() if phone_raw else phone_clean),
        )
        cid = tenant.execute("SELECT last_insert_rowid()").fetchone()[0]
        tenant.commit()
        return cid
    except Exception as e:
        logger.warning("_sqlite_upsert_customer error: %s", e)
        return None
    finally:
        tenant.close()


def _sqlite_write_log(
    created_at: str,
    store_key: str | None,
    phone_clean: str,
    name: str,
    status: str,
    message: str | None,
    db_filename: str | None,
    customer_id: int | None,
) -> None:
    """SQLite master ChannelTalkWebhookLog 에 로그 기록."""
    conn = _sqlite_master_conn()
    try:
        _sqlite_ensure_log_table(conn)
        conn.execute(
            """INSERT INTO ChannelTalkWebhookLog
               (created_at, store_key, phone, name, status, message, db_filename, customer_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (created_at, store_key, phone_clean or None, name, status, message, db_filename, customer_id),
        )
        conn.commit()
    except Exception as e:
        logger.warning("_sqlite_write_log error: %s", e)
    finally:
        conn.close()


# ──────────────────────────────────────────────
# FastAPI 라우터
# ──────────────────────────────────────────────

@app.post("/webhook/channel-talk", summary="채널톡 웹훅 수신")
@app.post("/webhook/channel_talk", include_in_schema=False)
async def channel_talk_webhook(request: Request) -> JSONResponse:
    """
    채널톡에서 POST 요청이 오면 고객 데이터를 파싱 후 Supabase + SQLite 에 저장.
    채널톡은 200 응답을 받으면 성공으로 간주하므로 내부 오류도 200 반환.
    """
    # ── 페이로드 파싱 (Pydantic 실패 시 raw dict 폴백)
    try:
        raw = await request.json()
    except Exception:
        raw = {}

    try:
        payload = ChannelTalkPayload.model_validate(raw)
    except Exception:
        payload = ChannelTalkPayload()

    name, phone_raw, phone_clean, tags = _extract_name_phone_tags(payload)
    store_key = _extract_store_key_from_tags(tags)
    now = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    logger.info("웹훅 수신 | name=%s phone=%s store_key=%s", name, phone_clean, store_key)

    # ── 1) Supabase: 매장 조회 + 초기 로그
    store_name_supa = await supa_find_store_name(store_key) if store_key else None
    supa_log_id = await supa_insert_log(
        created_at=now,
        store_key=store_key,
        phone=phone_clean or phone_raw or None,
        name=name,
        status="processing",
        message=None,
        store_name=store_name_supa,
        customer_id=None,
    )

    # ── 2) 유효성 검사
    if not phone_clean:
        await supa_update_log(supa_log_id, "fail", "연락처 없음")
        _sqlite_write_log(now, store_key, "", name, "fail", "연락처 없음", None, None)
        return JSONResponse({"status": "success"}, status_code=200)

    if not store_key:
        await supa_update_log(supa_log_id, "fail", "매장키 미매칭 (태그 형식: 매장키구매/품목)")
        _sqlite_write_log(now, store_key, phone_clean, name, "fail", "매장키 미매칭", None, None)
        return JSONResponse({"status": "success"}, status_code=200)

    # ── 3) Supabase app_customers 저장
    supa_customer_id: int | None = None
    if store_name_supa:
        existing_id = await supa_find_customer_by_phone(store_name_supa, phone_clean)
        if existing_id:
            await supa_update_log(supa_log_id, "skipped", "이미 등록된 연락처", existing_id)
            supa_customer_id = existing_id
            logger.info("기존 고객 (Supabase id=%s)", existing_id)
        else:
            new_id = await supa_insert_customer(store_name_supa, name, phone_raw, phone_clean)
            if new_id:
                await supa_update_log(supa_log_id, "success", None, new_id)
                supa_customer_id = new_id
                logger.info("신규 고객 등록 (Supabase id=%s)", new_id)
            else:
                await supa_update_log(supa_log_id, "fail", "Supabase 고객 등록 실패")
                logger.warning("Supabase 고객 등록 실패 | phone=%s", phone_clean)
    else:
        msg = f"매장키 '{store_key}'에 해당하는 매장을 Supabase에서 찾지 못함"
        await supa_update_log(supa_log_id, "fail", msg)
        logger.warning(msg)

    # ── 4) SQLite 하위 호환 저장
    db_filename = _sqlite_get_db_filename(store_key)
    sqlite_cust_id = _sqlite_upsert_customer(db_filename, name, phone_raw, phone_clean)
    final_cust_id = supa_customer_id or sqlite_cust_id
    _sqlite_write_log(
        now, store_key, phone_clean, name,
        "success" if final_cust_id else "fail",
        None if final_cust_id else "모든 DB 저장 실패",
        db_filename,
        final_cust_id,
    )

    return JSONResponse({"status": "success"}, status_code=200)


@app.get("/health", summary="헬스체크")
async def health() -> JSONResponse:
    return JSONResponse({
        "status": "ok",
        "supabase_configured": bool(SUPABASE_URL and SUPABASE_SERVICE_KEY),
    })


# ──────────────────────────────────────────────
# 직접 실행 시 uvicorn 사용
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)
