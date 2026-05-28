# -*- coding: utf-8 -*-
"""
이몬스 웹훅 FastAPI 서버 (독립 실행 프로세스).

현재 제공 엔드포인트:
  POST /webhook/solapi/friend-added  — Solapi 카카오채널 친구추가 이벤트 수신
  GET  /health                       — 헬스체크

실행:
  uvicorn api:app --host 0.0.0.0 --port 8000 --reload

환경변수:
  SUPABASE_URL          Supabase 프로젝트 URL
  SUPABASE_SERVICE_KEY  service_role key (전체 쓰기 권한)
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone  # noqa: F401

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

app = FastAPI(
    title="이몬스 웹훅 API",
    description="Solapi 친구추가 이벤트 등 외부 웹훅 수신 서버",
    version="3.0.0",
)


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


def _supa_url(table: str) -> str:
    return f"{SUPABASE_URL.rstrip('/')}/rest/v1/{table}"


# ──────────────────────────────────────────────
# FastAPI 라우터
# ──────────────────────────────────────────────

@app.post("/webhook/solapi/friend-added", summary="Solapi 친구추가 이벤트 수신")
@app.post("/webhook/solapi/friend_added", include_in_schema=False)
async def solapi_friend_added_webhook(request: Request) -> JSONResponse:
    """
    Solapi가 카카오채널 친구추가 이벤트를 전달하면 phone으로 app_users 매칭 후
    kakao_friend_added=true로 갱신.
    """
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    # Solapi 페이로드 형식이 변경될 수 있어 다양한 키 시도
    phone = (
        payload.get("phone")
        or payload.get("phoneNumber")
        or payload.get("to")
        or (payload.get("data", {}) or {}).get("phone")
        or ""
    )
    sender_key = (
        payload.get("senderKey")
        or payload.get("sender_key")
        or (payload.get("data", {}) or {}).get("senderKey")
        or ""
    )

    digits = "".join(c for c in str(phone) if c.isdigit())
    updated = False
    headers = _supa_headers()
    if digits and headers:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.patch(
                    _supa_url("app_users") + f"?phone=eq.{digits}",
                    headers=headers,
                    json={"kakao_friend_added": True},
                )
                updated = resp.status_code < 300
        except Exception as e:
            logger.warning("solapi_friend_added_webhook update failed: %s", e)

    return JSONResponse({
        "status": "ok",
        "matched_by_phone": updated,
        "phone_digits": digits or None,
        "sender_key": sender_key or None,
    }, status_code=200)


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
