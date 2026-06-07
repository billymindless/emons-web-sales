# -*- coding: utf-8 -*-
"""
아임웹 회원 데이터 → Supabase 동기화 배치 스크립트.

웹훅 유실 대비 안전망. 매일 1회 실행 (Render Cron Job 또는 수동).

환경변수:
  IMWEB_API_KEY        아임웹 REST API Key
  IMWEB_API_SECRET     아임웹 REST API Secret
  SUPABASE_URL         Supabase 프로젝트 URL
  SUPABASE_SERVICE_KEY Supabase service_role 키

실행:
  python imweb_sync.py
  python imweb_sync.py --days 7   # 최근 7일치 동기화
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

IMWEB_API_KEY = os.environ.get("IMWEB_API_KEY", "")
IMWEB_API_SECRET = os.environ.get("IMWEB_API_SECRET", "")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

IMWEB_BASE = "https://api.imweb.me"


# ──────────────────────────────────────────────────────
# 전화번호 정규화
# ──────────────────────────────────────────────────────

def _normalize_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("82") and len(digits) >= 11:
        digits = "0" + digits[2:]
    return digits


# ──────────────────────────────────────────────────────
# 아임웹 API 인증 토큰 발급
# ──────────────────────────────────────────────────────

def _get_imweb_token(client: httpx.Client) -> str | None:
    if not IMWEB_API_KEY or not IMWEB_API_SECRET:
        logger.error("IMWEB_API_KEY / IMWEB_API_SECRET 환경변수가 설정되지 않았습니다.")
        return None
    resp = client.post(
        f"{IMWEB_BASE}/v2/auth",
        json={"key": IMWEB_API_KEY, "secret": IMWEB_API_SECRET},
        timeout=10.0,
    )
    if resp.status_code != 200:
        logger.error("아임웹 인증 실패: %s %s", resp.status_code, resp.text)
        return None
    data = resp.json()
    token = data.get("data", {}).get("access_token") or data.get("access_token")
    if not token:
        logger.error("아임웹 토큰 파싱 실패: %s", data)
    return token


# ──────────────────────────────────────────────────────
# Supabase 헬퍼
# ──────────────────────────────────────────────────────

def _supa_headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _supa_url(table: str) -> str:
    return f"{SUPABASE_URL.rstrip('/')}/rest/v1/{table}"


# ──────────────────────────────────────────────────────
# 아임웹 회원 페이지 조회
# ──────────────────────────────────────────────────────

def _fetch_imweb_members(
    client: httpx.Client,
    token: str,
    page: int = 1,
    limit: int = 100,
    since_date: str | None = None,
) -> list[dict]:
    """아임웹 회원 목록 조회 (페이지 단위)."""
    params: dict = {"page": page, "limit": limit}
    if since_date:
        params["search_start_date"] = since_date
    resp = client.get(
        f"{IMWEB_BASE}/v2/member",
        headers={"access-token": token},
        params=params,
        timeout=15.0,
    )
    if resp.status_code != 200:
        logger.error("아임웹 회원 조회 실패 page=%s: %s %s", page, resp.status_code, resp.text[:300])
        return []
    data = resp.json()
    return data.get("data", {}).get("list") or data.get("list") or []


# ──────────────────────────────────────────────────────
# Supabase Upsert (전화번호 기준)
# ──────────────────────────────────────────────────────

def _upsert_customer(client: httpx.Client, member: dict) -> bool:
    raw_phone = (
        member.get("phone")
        or member.get("mobile")
        or member.get("hp")
        or ""
    )
    phone = _normalize_phone(raw_phone)
    if not phone:
        return False

    name = member.get("name") or member.get("member_name") or ""
    email = member.get("email") or ""
    imweb_member_id = str(member.get("member_id") or member.get("id") or "")
    marketing_agreed = bool(
        member.get("marketing_agree")
        or member.get("sms_agree")
    )
    joined_at = member.get("created") or member.get("join_date") or ""

    supa_headers = _supa_headers()

    # 기존 고객 조회 (전화번호 기준)
    resp = client.get(
        _supa_url("app_customers"),
        headers=supa_headers,
        params={"phone1": f"eq.{phone}", "select": "id,customer_type", "limit": "1"},
        timeout=10.0,
    )
    existing = resp.json() if resp.status_code == 200 else []

    if existing:
        customer_id = existing[0]["id"]
        patch = {
            "imweb_member_id": imweb_member_id or None,
            "marketing_agreed": marketing_agreed,
        }
        if joined_at:
            patch["imweb_joined_at"] = joined_at
        client.patch(
            _supa_url("app_customers") + f"?id=eq.{customer_id}",
            headers=supa_headers,
            json=patch,
            timeout=10.0,
        )
        logger.debug("업데이트: phone=%s customer_id=%s", phone, customer_id)
    else:
        insert = {
            "name": name or "아임웹회원",
            "phone1": phone,
            "email": email or None,
            "imweb_member_id": imweb_member_id or None,
            "imweb_joined_at": joined_at or None,
            "marketing_agreed": marketing_agreed,
            "customer_type": "member_only",
            "store_name": "아임웹",
        }
        resp2 = client.post(
            _supa_url("app_customers"),
            headers=supa_headers,
            json=insert,
            timeout=10.0,
        )
        if resp2.status_code in (200, 201):
            logger.info("신규 생성: phone=%s name=%s", phone, name)
        else:
            logger.warning("생성 실패: %s %s", resp2.status_code, resp2.text[:200])
    return True


# ──────────────────────────────────────────────────────
# 메인 동기화 루프
# ──────────────────────────────────────────────────────

def run_sync(days_back: int = 1) -> None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        logger.error("Supabase 환경변수가 설정되지 않았습니다.")
        return

    since_date = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime("%Y-%m-%d")
    logger.info("아임웹 회원 동기화 시작 (since=%s)", since_date)

    with httpx.Client() as client:
        token = _get_imweb_token(client)
        if not token:
            return

        page = 1
        total = 0
        while True:
            members = _fetch_imweb_members(client, token, page=page, since_date=since_date)
            if not members:
                break
            for member in members:
                _upsert_customer(client, member)
                total += 1
            logger.info("페이지 %d 처리 완료 (%d명)", page, len(members))
            if len(members) < 100:
                break
            page += 1
            time.sleep(0.3)  # API rate limit 방지

    logger.info("동기화 완료. 총 처리: %d명", total)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="아임웹 회원 → Supabase 동기화")
    parser.add_argument("--days", type=int, default=1, help="최근 N일치 동기화 (기본: 1)")
    args = parser.parse_args()
    run_sync(days_back=args.days)
