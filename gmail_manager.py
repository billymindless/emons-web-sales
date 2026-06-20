# -*- coding: utf-8 -*-
"""
gmail_manager.py — Gmail OAuth2 연동 모듈

개인 Gmail(@gmail.com) 및 Google Workspace 계정 모두 지원.

연동 방식 A — 앱 내 버튼 클릭 (권장):
    1. 메일관리 화면에서 "Google 계정 연결" 클릭
    2. Google 로그인 + 권한 허용
    3. 자동으로 토큰 저장 완료

연동 방식 B — secrets.toml 직접 설정:
    [gmail]
    client_id     = "...apps.googleusercontent.com"
    client_secret = "GOCSPX-..."
    refresh_token = "1//0g..."
    sender_email  = "you@gmail.com"
"""

from __future__ import annotations

import base64
import os
import re
import urllib.parse
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

# ──────────────────────────────────────────────
# Google OAuth2 상수
# ──────────────────────────────────────────────
GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/userinfo.email",
]
GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


# ──────────────────────────────────────────────
# 설정 로드 (secrets.toml 또는 환경변수)
# ──────────────────────────────────────────────

def _get_gmail_secrets() -> dict:
    try:
        import streamlit as st
        sec = dict(st.secrets.get("gmail") or {}) if hasattr(st, "secrets") else {}
        if sec.get("client_id"):
            return sec
    except Exception:
        pass
    return {
        "client_id":     os.environ.get("GMAIL_CLIENT_ID", ""),
        "client_secret": os.environ.get("GMAIL_CLIENT_SECRET", ""),
        "refresh_token": os.environ.get("GMAIL_REFRESH_TOKEN", ""),
        "sender_email":  os.environ.get("GMAIL_SENDER_EMAIL", ""),
    }


def is_gmail_configured() -> bool:
    """secrets.toml 또는 환경변수로 전역 설정이 있는지 확인."""
    s = _get_gmail_secrets()
    return bool(s.get("client_id") and s.get("client_secret") and s.get("refresh_token"))


def get_client_credentials() -> tuple[str, str]:
    """(client_id, client_secret) 반환."""
    s = _get_gmail_secrets()
    return s.get("client_id", ""), s.get("client_secret", "")


# ──────────────────────────────────────────────
# OAuth2 인증 URL 생성 (앱 내 연결 버튼용)
# ──────────────────────────────────────────────

def get_oauth_url(redirect_uri: str, state: str = "gmail_oauth") -> str:
    """
    Google OAuth2 인증 URL 생성.
    redirect_uri: 앱 URL (예: https://emons.streamlit.app)
    """
    client_id, _ = get_client_credentials()
    if not client_id:
        return ""
    params = {
        "client_id":     client_id,
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         " ".join(GMAIL_SCOPES),
        "access_type":   "offline",
        "prompt":        "consent",   # refresh_token 강제 발급
        "state":         state,
    }
    return f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"


def exchange_code_for_tokens(
    code: str,
    redirect_uri: str,
) -> dict[str, Any]:
    """
    인증 코드를 access_token + refresh_token으로 교환.
    반환: {"access_token", "refresh_token", "expires_in", "error"(있을 때)}
    """
    import requests
    client_id, client_secret = get_client_credentials()
    resp = requests.post(GOOGLE_TOKEN_URL, data={
        "code":          code,
        "client_id":     client_id,
        "client_secret": client_secret,
        "redirect_uri":  redirect_uri,
        "grant_type":    "authorization_code",
    }, timeout=10)
    return resp.json()


def fetch_gmail_address(access_token: str) -> str:
    """access_token으로 연결된 Gmail 주소 조회."""
    try:
        import requests
        r = requests.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=8,
        )
        return r.json().get("email", "")
    except Exception:
        return ""


# ──────────────────────────────────────────────
# 사용자별 토큰 저장 / 불러오기 (Supabase)
# ──────────────────────────────────────────────

def _get_supabase():
    try:
        from app import get_supabase_client  # type: ignore
        client, err = get_supabase_client()
        return client if not err else None
    except Exception:
        return None


def save_user_tokens(username: str, tokens: dict) -> bool:
    """사용자 Gmail 토큰을 app_gmail_tokens에 저장(upsert)."""
    sc = _get_supabase()
    if not sc:
        return False
    try:
        row = {
            "username":      username,
            "refresh_token": tokens.get("refresh_token", ""),
            "access_token":  tokens.get("access_token"),
            "gmail_address": tokens.get("gmail_address", ""),
            "updated_at":    datetime.now(timezone.utc).isoformat(),
        }
        sc.table("app_gmail_tokens").upsert(row, on_conflict="username").execute()
        return True
    except Exception:
        return False


def load_user_tokens(username: str) -> dict | None:
    """app_gmail_tokens에서 사용자 토큰 불러오기. 없으면 None."""
    sc = _get_supabase()
    if not sc or not username:
        return None
    try:
        r = sc.table("app_gmail_tokens").select("*").eq("username", username).limit(1).execute()
        return r.data[0] if r.data else None
    except Exception:
        return None


def delete_user_tokens(username: str) -> bool:
    """사용자 Gmail 연결 해제 (토큰 삭제)."""
    sc = _get_supabase()
    if not sc:
        return False
    try:
        sc.table("app_gmail_tokens").delete().eq("username", username).execute()
        return True
    except Exception:
        return False


# ──────────────────────────────────────────────
# Gmail API 서비스 객체 빌드 (전역 or 사용자별)
# ──────────────────────────────────────────────

def _build_service(username: str | None = None):
    """
    Gmail API service 객체 생성.
    username 지정 시 해당 사용자의 Supabase 저장 토큰 우선 사용,
    없으면 secrets.toml 전역 설정으로 fallback.
    """
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    client_id, client_secret = get_client_credentials()
    refresh_token = None

    if username:
        row = load_user_tokens(username)
        if row:
            refresh_token = row.get("refresh_token")

    if not refresh_token:
        s = _get_gmail_secrets()
        refresh_token = s.get("refresh_token", "")

    if not refresh_token:
        raise ValueError("Gmail refresh token이 없습니다. 먼저 Google 계정을 연결해 주세요.")

    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=client_id,
        client_secret=client_secret,
        token_uri=GOOGLE_TOKEN_URL,
        scopes=GMAIL_SCOPES[:1],  # gmail.modify
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def is_user_connected(username: str) -> bool:
    """해당 사용자가 Gmail을 연결했는지 확인."""
    return bool(load_user_tokens(username))


# ──────────────────────────────────────────────
# 메일 목록 조회
# ──────────────────────────────────────────────

def list_messages(
    query: str = "",
    max_results: int = 20,
    label_ids: list[str] | None = None,
    username: str | None = None,
) -> list[dict[str, Any]]:
    """
    Gmail 메시지 목록 반환.
    각 항목: {id, thread_id, subject, from_, to, date, snippet, is_unread, labels}
    """
    svc = _build_service(username)
    params: dict = {"userId": "me", "maxResults": max_results}
    if query:
        params["q"] = query
    if label_ids:
        params["labelIds"] = label_ids

    res = svc.users().messages().list(**params).execute()
    msgs = res.get("messages") or []
    results = []
    for m in msgs:
        detail = svc.users().messages().get(
            userId="me", id=m["id"],
            format="metadata",
            metadataHeaders=["Subject", "From", "To", "Date"],
        ).execute()
        headers = {h["name"]: h["value"] for h in detail.get("payload", {}).get("headers", [])}
        label_list = detail.get("labelIds") or []
        results.append({
            "id":        m["id"],
            "thread_id": m.get("threadId", ""),
            "subject":   headers.get("Subject", "(제목 없음)"),
            "from_":     headers.get("From", ""),
            "to":        headers.get("To", ""),
            "date":      headers.get("Date", ""),
            "snippet":   detail.get("snippet", ""),
            "is_unread": "UNREAD" in label_list,
            "labels":    label_list,
        })
    return results


def get_message_body(msg_id: str, username: str | None = None) -> str:
    """메시지 본문(plain text 우선, 없으면 html 스트립) 반환."""
    svc = _build_service(username)
    detail = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()

    def _extract(payload):
        mime = payload.get("mimeType", "")
        if mime == "text/plain":
            data = (payload.get("body") or {}).get("data", "")
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace") if data else ""
        if mime == "text/html":
            data = (payload.get("body") or {}).get("data", "")
            raw = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace") if data else ""
            return re.sub(r"<[^>]+>", "", raw)
        for part in payload.get("parts") or []:
            result = _extract(part)
            if result:
                return result
        return ""

    return _extract(detail.get("payload") or {})


def mark_as_read(msg_id: str, username: str | None = None) -> bool:
    try:
        svc = _build_service(username)
        svc.users().messages().modify(
            userId="me", id=msg_id,
            body={"removeLabelIds": ["UNREAD"]},
        ).execute()
        return True
    except Exception:
        return False


# ──────────────────────────────────────────────
# 메일 발송
# ──────────────────────────────────────────────

def send_message(
    to: str,
    subject: str,
    body: str,
    body_html: str | None = None,
    sender_email: str | None = None,
    username: str | None = None,
) -> dict[str, Any]:
    """
    Gmail API로 메일 발송.
    반환: {"status": "sent"/"failed", "id": str | None, "error": str | None}
    """
    s = _get_gmail_secrets()
    # 사용자별 연결된 Gmail 주소 우선 사용
    if not sender_email and username:
        row = load_user_tokens(username)
        sender_email = (row or {}).get("gmail_address") or s.get("sender_email") or "me"
    sender_email = sender_email or s.get("sender_email") or "me"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender_email
    msg["To"]      = to
    msg.attach(MIMEText(body, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        svc = _build_service(username)
        result = svc.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        return {"status": "sent", "id": result.get("id"), "error": None}
    except Exception as e:
        return {"status": "failed", "id": None, "error": str(e)}


# ──────────────────────────────────────────────
# 고객 메일 자동 연결
# ──────────────────────────────────────────────

def match_sender_to_customer(from_email: str) -> dict | None:
    """발신자 이메일로 app_customers 조회. 매칭 시 {id, name, phone1, store_name} 반환."""
    try:
        from app import get_supabase_client  # type: ignore
        client, err = get_supabase_client()
        if err or not client:
            return None
        match = re.search(r"<([^>]+)>", from_email)
        addr = match.group(1) if match else from_email.strip()
        r = client.table("app_customers").select(
            "id,name,phone1,store_name"
        ).eq("email", addr).limit(1).execute()
        return r.data[0] if r.data else None
    except Exception:
        return None
