# -*- coding: utf-8 -*-
"""
gmail_manager.py — Gmail OAuth2 연동 모듈

Google OAuth 2.0 (installed-app / service-account 양쪽 지원)을 사용해
Gmail 받은편지함 조회·검색·메일 발송 기능을 제공합니다.

설정 (secrets.toml):
    [gmail]
    client_id     = "..."          # OAuth2 클라이언트 ID
    client_secret = "..."          # OAuth2 클라이언트 Secret
    refresh_token = "..."          # 최초 인증 후 발급된 refresh_token
    sender_email  = "you@gmail.com"  # 발송자 이메일

필요 패키지: google-auth, google-auth-oauthlib, google-api-python-client
(requirements.txt 에 추가 필요)
"""

from __future__ import annotations

import base64
import email as _email_lib
import os
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

# ──────────────────────────────────────────────
# 설정 로드
# ──────────────────────────────────────────────

def _get_gmail_secrets() -> dict:
    """secrets.toml [gmail] 섹션 또는 환경변수에서 자격증명 로드."""
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
    s = _get_gmail_secrets()
    return bool(s.get("client_id") and s.get("client_secret") and s.get("refresh_token"))


# ──────────────────────────────────────────────
# Gmail API 클라이언트
# ──────────────────────────────────────────────

def _build_service():
    """google-api-python-client로 Gmail service 객체 생성."""
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    s = _get_gmail_secrets()
    creds = Credentials(
        token=None,
        refresh_token=s["refresh_token"],
        client_id=s["client_id"],
        client_secret=s["client_secret"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


# ──────────────────────────────────────────────
# 메일 목록 조회
# ──────────────────────────────────────────────

def list_messages(
    query: str = "",
    max_results: int = 20,
    label_ids: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Gmail 메시지 목록 반환.
    각 항목: {id, thread_id, subject, from_, to, date, snippet, is_unread, labels}
    """
    svc = _build_service()
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


def get_message_body(msg_id: str) -> str:
    """메시지 본문(plain text 우선, 없으면 html 스트립) 반환."""
    svc = _build_service()
    detail = svc.users().messages().get(userId="me", id=msg_id, format="full").execute()

    def _extract(payload):
        mime = payload.get("mimeType", "")
        if mime == "text/plain":
            data = (payload.get("body") or {}).get("data", "")
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace") if data else ""
        if mime == "text/html":
            data = (payload.get("body") or {}).get("data", "")
            raw = base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace") if data else ""
            import re
            return re.sub(r"<[^>]+>", "", raw)
        for part in payload.get("parts") or []:
            result = _extract(part)
            if result:
                return result
        return ""

    return _extract(detail.get("payload") or {})


def mark_as_read(msg_id: str) -> bool:
    """메시지를 읽음 처리."""
    try:
        svc = _build_service()
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
) -> dict[str, Any]:
    """
    Gmail API로 메일 발송.
    반환: {"status": "sent"/"failed", "id": str | None, "error": str | None}
    """
    s = _get_gmail_secrets()
    sender = sender_email or s.get("sender_email") or "me"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = sender
    msg["To"]      = to
    msg.attach(MIMEText(body, "plain", "utf-8"))
    if body_html:
        msg.attach(MIMEText(body_html, "html", "utf-8"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        svc = _build_service()
        result = svc.users().messages().send(
            userId="me", body={"raw": raw}
        ).execute()
        return {"status": "sent", "id": result.get("id"), "error": None}
    except Exception as e:
        return {"status": "failed", "id": None, "error": str(e)}


# ──────────────────────────────────────────────
# 고객 메일 자동 연결 (수신 메일 → 고객 DB 매칭)
# ──────────────────────────────────────────────

def match_sender_to_customer(from_email: str) -> dict | None:
    """
    발신자 이메일 주소로 app_customers 조회.
    매칭된 고객이 있으면 {id, name, phone1, store_name} 반환, 없으면 None.
    """
    try:
        from app import get_supabase_client  # type: ignore
        client, err = get_supabase_client()
        if err or not client:
            return None
        # 이메일 주소만 추출 (예: "홍길동 <hong@gmail.com>" → "hong@gmail.com")
        import re
        match = re.search(r"<([^>]+)>", from_email)
        addr = match.group(1) if match else from_email.strip()
        r = client.table("app_customers").select(
            "id,name,phone1,store_name"
        ).eq("email", addr).limit(1).execute()
        return r.data[0] if r.data else None
    except Exception:
        return None
