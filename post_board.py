"""
사내 게시판(게시물) 백엔드 모듈.

emons-web-sales `app.py`의 ERP 메뉴 하위 "📝 게시물"에서 사용.
사내 업무판(task_board.py)의 축소판 — 담당자/상태/알림 없는 단순 게시판.
Supabase 클라이언트와 첨부 Storage 버킷(task-attachments)은 app.py / task_board.py와 공유.
"""

from __future__ import annotations

import mimetypes
import uuid
from datetime import datetime, timezone
from typing import Any

import streamlit as st


# ─────────────────────────────────────────────────────────────────────
# Supabase 클라이언트 (app.py와 동일 인터페이스)
# ─────────────────────────────────────────────────────────────────────

def _client():
    from app import get_supabase_client  # noqa: WPS433
    return get_supabase_client()


def _admin_client():
    from app import get_supabase_admin_client  # noqa: WPS433
    return get_supabase_admin_client()


# ─────────────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────────────

ATTACHMENT_BUCKET = "task-attachments"  # task_board.py와 공유
POST_SCOPES = ["store", "company"]
POST_SCOPE_LABELS = {"store": "매장별 (우리 매장만)", "company": "전체공용 (전 직원)"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_tags(raw: str | None) -> str | None:
    """쉼표/공백 구분 입력을 정규화: 공백 제거·중복 제거, '#a, b' → 'a,b'."""
    if not raw:
        return None
    parts = []
    for chunk in str(raw).replace("#", " ").replace("\n", ",").split(","):
        for t in chunk.split():
            tag = t.strip()
            if tag and tag not in parts:
                parts.append(tag)
    return ",".join(parts) if parts else None


def split_tags(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [t.strip() for t in str(raw).replace("#", " ").replace("\n", ",").split(",") if t.strip()]


# ─────────────────────────────────────────────────────────────────────
# 캐시된 로더
# ─────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def load_posts_cached(store_name: str | None, is_superadmin: bool = False) -> list[dict]:
    """열람 가능한 게시물 목록.
    - 전체공용(scope='company')은 모두 노출
    - 매장별(scope='store')은 자기 매장(store_name)만, superadmin은 전체
    고정(is_pinned) 우선, 그다음 최신순 정렬."""
    client, err = _client()
    if err or not client:
        return []
    try:
        r = (
            client.table("app_posts")
            .select("id, title, content, author, store_name, scope, tags, is_pinned, created_at, updated_at")
            .order("is_pinned", desc=True)
            .order("created_at", desc=True)
            .execute()
        )
        rows = r.data or []
    except Exception:
        return []
    out = []
    for p in rows:
        scope = p.get("scope") or "store"
        if scope == "company":
            out.append(p)
        elif is_superadmin:
            out.append(p)
        elif store_name and p.get("store_name") == store_name:
            out.append(p)
    return out


@st.cache_data(ttl=30, show_spinner=False)
def load_post_comments_cached(post_id: int) -> list[dict]:
    client, err = _client()
    if err or not client:
        return []
    try:
        r = client.table("app_post_comments").select("*").eq("post_id", post_id).order("created_at").execute()
        return r.data or []
    except Exception:
        return []


@st.cache_data(ttl=30, show_spinner=False)
def load_post_attachments_cached(post_id: int) -> list[dict]:
    client, err = _client()
    if err or not client:
        return []
    try:
        r = client.table("app_post_attachments").select("*").eq("post_id", post_id).order("uploaded_at").execute()
        return r.data or []
    except Exception:
        return []


def clear_post_caches():
    """게시판 관련 모든 캐시 무효화."""
    load_posts_cached.clear()
    load_post_comments_cached.clear()
    load_post_attachments_cached.clear()


# ─────────────────────────────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────────────────────────────

def create_post(
    title: str,
    content: str,
    author: str,
    store_name: str | None,
    scope: str = "store",
    tags: str | None = None,
    is_pinned: bool = False,
) -> tuple[int | None, str | None]:
    """게시물 생성."""
    title = (title or "").strip()
    if not title:
        return None, "제목을 입력해 주세요."
    client, err = _client()
    if err or not client:
        return None, err or "Supabase 연결 불가"
    try:
        row = {
            "title": title,
            "content": (content or "").strip() or None,
            "author": author,
            "store_name": None if scope == "company" else store_name,
            "scope": scope if scope in POST_SCOPES else "store",
            "tags": normalize_tags(tags),
            "is_pinned": bool(is_pinned),
        }
        r = client.table("app_posts").insert(row).execute()
        new_id = int(r.data[0]["id"]) if r.data else None
        if not new_id:
            return None, "insert 후 id를 가져오지 못했습니다."
        clear_post_caches()
        return new_id, None
    except Exception as e:
        return None, str(e)


def update_post(post_id: int, **fields) -> tuple[bool, str | None]:
    """게시물 필드 수정. title/content/tags/scope/store_name/is_pinned 허용."""
    client, err = _client()
    if err or not client:
        return False, err
    allowed = {"title", "content", "tags", "scope", "store_name", "is_pinned"}
    patch = {k: v for k, v in fields.items() if k in allowed}
    if "tags" in patch:
        patch["tags"] = normalize_tags(patch["tags"])
    if not patch:
        return True, None
    patch["updated_at"] = _now_iso()
    try:
        client.table("app_posts").update(patch).eq("id", post_id).execute()
        clear_post_caches()
        return True, None
    except Exception as e:
        return False, str(e)


def delete_post(post_id: int) -> tuple[bool, str | None]:
    client, err = _client()
    if err or not client:
        return False, err
    try:
        client.table("app_posts").delete().eq("id", post_id).execute()
        clear_post_caches()
        return True, None
    except Exception as e:
        return False, str(e)


def toggle_post_pin(post_id: int, value: bool) -> tuple[bool, str | None]:
    return update_post(post_id, is_pinned=bool(value))


def post_comment(post_id: int, author: str, body: str,
                 parent_comment_id: int | None = None) -> tuple[int | None, str | None]:
    body = (body or "").strip()
    if not body:
        return None, "내용이 비어 있습니다."
    client, err = _client()
    if err or not client:
        return None, err
    try:
        r = client.table("app_post_comments").insert({
            "post_id": post_id,
            "author": author,
            "body": body,
            "parent_comment_id": int(parent_comment_id) if parent_comment_id else None,
        }).execute()
        new_id = int(r.data[0]["id"]) if r.data else None
        clear_post_caches()
        return new_id, None
    except Exception as e:
        return None, str(e)


def attach_file(
    post_id: int | None,
    comment_id: int | None,
    uploaded_file,
    uploaded_by: str,
) -> tuple[dict | None, str | None]:
    """Streamlit UploadedFile을 Supabase Storage에 업로드 + app_post_attachments INSERT.
    task_board.attach_file과 동일 로직, 테이블만 app_post_attachments."""
    if uploaded_file is None:
        return None, "파일이 없습니다."
    admin, err = _admin_client()
    if err or not admin:
        client, err2 = _client()
        if err2 or not client:
            return None, err or err2 or "Supabase 연결 불가"
        admin = client

    try:
        original_name = uploaded_file.name
        ext = original_name.split(".")[-1].lower() if "." in original_name else "bin"
        mime, _ = mimetypes.guess_type(original_name)
        mime = mime or "application/octet-stream"
        data = uploaded_file.getvalue() if hasattr(uploaded_file, "getvalue") else uploaded_file.read()
        byte_size = len(data) if data else 0
        if byte_size <= 0:
            return None, "빈 파일"
        if byte_size > 20 * 1024 * 1024:  # 20MB
            return None, "파일이 너무 큽니다 (최대 20MB)"

        storage_path = f"post_{post_id or 'orphan'}/{uuid.uuid4().hex}.{ext}"
        up_res = admin.storage.from_(ATTACHMENT_BUCKET).upload(
            path=storage_path,
            file=data,
            file_options={"content-type": mime, "upsert": "false"},
        )
        _up_err = getattr(up_res, "error", None)
        if _up_err:
            return None, (
                f"Storage 업로드 실패: {_up_err}. "
                "Supabase 대시보드 → Storage → 'task-attachments' 버킷이 존재하는지 확인하세요."
            )

        client, _ = _client()
        if client:
            r = client.table("app_post_attachments").insert({
                "post_id": post_id,
                "comment_id": comment_id,
                "storage_path": storage_path,
                "mime_type": mime,
                "original_name": original_name,
                "byte_size": byte_size,
                "uploaded_by": uploaded_by,
            }).execute()
            new_row = (r.data or [{}])[0] if r.data else {}
        else:
            new_row = {"storage_path": storage_path, "mime_type": mime, "original_name": original_name}
        clear_post_caches()
        return new_row, None
    except Exception as e:
        return None, str(e)
