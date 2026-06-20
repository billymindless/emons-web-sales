# -*- coding: utf-8 -*-
"""
gmail_manager.py — Gmail IMAP/SMTP 연동 모듈

설정 방법:
    1. Google 계정 → 보안 → 2단계 인증 활성화
    2. 앱 비밀번호 → 앱 이름 입력 → 16자리 코드 발급
    3. 앱 내 메일관리 화면에서 Gmail 주소 + 앱 비밀번호 입력

별도 패키지 불필요 — Python 표준 라이브러리(imaplib, smtplib) 사용
"""

from __future__ import annotations

import email as _email_lib
import imaplib
import re
import smtplib
import os
from email.header import decode_header as _decode_header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

IMAP_HOST = "imap.gmail.com"
IMAP_PORT = 993
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 587


# ──────────────────────────────────────────────
# 자격증명 저장/로드 (Supabase app_gmail_tokens)
# ──────────────────────────────────────────────

def _get_supabase():
    try:
        from app import get_supabase_client  # type: ignore
        client, err = get_supabase_client()
        return client if not err else None
    except Exception:
        return None


def save_user_credentials(username: str, gmail_address: str, app_password: str) -> bool:
    """Gmail 주소 + 앱 비밀번호를 app_gmail_tokens에 저장."""
    sc = _get_supabase()
    if not sc:
        return False
    try:
        from datetime import datetime, timezone
        sc.table("app_gmail_tokens").upsert({
            "username":      username,
            "gmail_address": gmail_address.strip().lower(),
            "refresh_token": app_password,   # 앱 비밀번호를 refresh_token 컬럼에 저장
            "updated_at":    datetime.now(timezone.utc).isoformat(),
        }, on_conflict="username").execute()
        return True
    except Exception:
        return False


def load_user_credentials(username: str) -> dict | None:
    """app_gmail_tokens에서 사용자 자격증명 로드. 없으면 None."""
    sc = _get_supabase()
    if not sc or not username:
        return None
    try:
        r = sc.table("app_gmail_tokens").select("gmail_address,refresh_token").eq("username", username).limit(1).execute()
        if not r.data:
            return None
        row = r.data[0]
        return {
            "gmail_address": row.get("gmail_address", ""),
            "app_password":  row.get("refresh_token", ""),  # refresh_token 컬럼에 앱 비밀번호 저장
        }
    except Exception:
        return None


def delete_user_credentials(username: str) -> bool:
    """사용자 Gmail 연결 해제."""
    sc = _get_supabase()
    if not sc:
        return False
    try:
        sc.table("app_gmail_tokens").delete().eq("username", username).execute()
        return True
    except Exception:
        return False


def is_user_connected(username: str) -> bool:
    creds = load_user_credentials(username)
    return bool(creds and creds.get("gmail_address") and creds.get("app_password"))


# ──────────────────────────────────────────────
# IMAP 연결 테스트
# ──────────────────────────────────────────────

def test_connection(gmail_address: str, app_password: str) -> tuple[bool, str]:
    """IMAP 연결 테스트. (성공여부, 메시지) 반환."""
    try:
        with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as imap:
            imap.login(gmail_address.strip(), app_password.strip())
        return True, "연결 성공"
    except imaplib.IMAP4.error as e:
        return False, f"인증 실패: {e}"
    except Exception as e:
        return False, f"연결 오류: {e}"


# ──────────────────────────────────────────────
# 헤더 디코딩 유틸
# ──────────────────────────────────────────────

def _decode_str(value: str | None) -> str:
    if not value:
        return ""
    parts = _decode_header(value)
    decoded = []
    for part, enc in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            decoded.append(str(part))
    return "".join(decoded)


def _get_body(msg) -> str:
    """메일 본문 추출 (plain text 우선)."""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            disp = str(part.get("Content-Disposition") or "")
            if ct == "text/plain" and "attachment" not in disp:
                charset = part.get_content_charset() or "utf-8"
                return part.get_payload(decode=True).decode(charset, errors="replace")
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                charset = part.get_content_charset() or "utf-8"
                html = part.get_payload(decode=True).decode(charset, errors="replace")
                return re.sub(r"<[^>]+>", "", html)
    else:
        charset = msg.get_content_charset() or "utf-8"
        payload = msg.get_payload(decode=True)
        return payload.decode(charset, errors="replace") if payload else ""
    return ""


# ──────────────────────────────────────────────
# 메일 목록 조회
# ──────────────────────────────────────────────

def list_messages(
    gmail_address: str,
    app_password: str,
    folder: str = "INBOX",
    search: str = "ALL",
    max_results: int = 30,
) -> list[dict[str, Any]]:
    """
    IMAP으로 메일 목록 조회.
    반환 항목: {uid, subject, from_, to, date, snippet, is_unread}
    """
    results = []
    try:
        with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as imap:
            imap.login(gmail_address.strip(), app_password.strip())
            imap.select(folder, readonly=True)

            criteria = search.encode() if search != "ALL" else b"ALL"
            _, data = imap.uid("search", None, criteria)
            uids = data[0].split() if data[0] else []
            uids = uids[-max_results:]  # 최신 순으로 max_results개

            for uid in reversed(uids):
                _, msg_data = imap.uid("fetch", uid, "(FLAGS BODY.PEEK[HEADER.FIELDS (SUBJECT FROM TO DATE)])")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                msg = _email_lib.message_from_bytes(raw)
                flags_data = msg_data[0][0].decode() if isinstance(msg_data[0][0], bytes) else str(msg_data[0][0])
                is_unread = "\\Seen" not in flags_data

                results.append({
                    "uid":      uid.decode(),
                    "subject":  _decode_str(msg.get("Subject", "(제목 없음)")),
                    "from_":    _decode_str(msg.get("From", "")),
                    "to":       _decode_str(msg.get("To", "")),
                    "date":     _decode_str(msg.get("Date", ""))[:25],
                    "snippet":  "",
                    "is_unread": is_unread,
                })
    except Exception as e:
        raise RuntimeError(str(e)) from e
    return results


def get_message_body(
    gmail_address: str,
    app_password: str,
    uid: str,
    folder: str = "INBOX",
) -> str:
    """메일 전체 본문 조회 + 읽음 처리."""
    try:
        with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as imap:
            imap.login(gmail_address.strip(), app_password.strip())
            imap.select(folder)
            _, msg_data = imap.uid("fetch", uid.encode(), "(RFC822)")
            if not msg_data or not msg_data[0]:
                return ""
            msg = _email_lib.message_from_bytes(msg_data[0][1])
            imap.uid("store", uid.encode(), "+FLAGS", "\\Seen")
            return _get_body(msg)
    except Exception as e:
        return f"[오류] {e}"


def delete_message(
    gmail_address: str,
    app_password: str,
    uid: str,
    folder: str = "INBOX",
) -> bool:
    """메일을 휴지통으로 이동 (Gmail IMAP 삭제)."""
    try:
        with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as imap:
            imap.login(gmail_address.strip(), app_password.strip())
            imap.select(folder)
            # Gmail은 [Gmail]/Trash로 이동해야 실제 삭제됨
            imap.uid("copy", uid.encode(), "[Gmail]/Trash")
            imap.uid("store", uid.encode(), "+FLAGS", "\\Deleted")
            imap.expunge()
        return True
    except Exception:
        return False


def mark_as_read(
    gmail_address: str,
    app_password: str,
    uid: str,
    folder: str = "INBOX",
) -> bool:
    try:
        with imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT) as imap:
            imap.login(gmail_address.strip(), app_password.strip())
            imap.select(folder)
            imap.uid("store", uid.encode(), "+FLAGS", "\\Seen")
        return True
    except Exception:
        return False


# ──────────────────────────────────────────────
# 메일 발송 (SMTP)
# ──────────────────────────────────────────────

def send_message(
    gmail_address: str,
    app_password: str,
    to: str,
    subject: str,
    body: str,
    body_html: str | None = None,
) -> dict[str, Any]:
    """SMTP TLS로 메일 발송."""
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = gmail_address
        msg["To"]      = to
        msg.attach(MIMEText(body, "plain", "utf-8"))
        if body_html:
            msg.attach(MIMEText(body_html, "html", "utf-8"))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(gmail_address.strip(), app_password.strip())
            smtp.sendmail(gmail_address, [to], msg.as_bytes())
        return {"status": "sent", "error": None}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


# ──────────────────────────────────────────────
# 고객 메일 자동 연결
# ──────────────────────────────────────────────

def match_sender_to_customer(from_email: str) -> dict | None:
    """발신자 이메일로 app_customers 조회."""
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
