"""
사내 업무판 (사내결제시스템) 백엔드 모듈.

emons-web-sales `app.py`의 ERP 메뉴 하위 "📋 사내 업무"에서 사용.
Supabase 클라이언트는 app.py의 get_supabase_client() / get_supabase_admin_client()를 재사용.
"""

from __future__ import annotations

import io
import logging
import mimetypes
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import streamlit as st

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Supabase 클라이언트 (app.py와 동일 인터페이스)
# ─────────────────────────────────────────────────────────────────────

def _client():
    """app.py의 get_supabase_client()를 lazy import해서 호출."""
    from app import get_supabase_client  # noqa: WPS433
    return get_supabase_client()


def _admin_client():
    """app.py의 get_supabase_admin_client()를 lazy import해서 호출. Storage 업로드에 service_role 필요."""
    from app import get_supabase_admin_client  # noqa: WPS433
    return get_supabase_admin_client()


# ─────────────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────────────

TASK_STATUSES = ["requested", "in_progress", "feedback", "done", "on_hold"]
TASK_STATUS_LABELS = {
    "requested": "요청",
    "in_progress": "진행",
    "feedback": "피드백",
    "done": "완료",
    "on_hold": "보류",
}
TASK_STATUS_EMOJI = {
    "requested": "🟡",
    "in_progress": "🔵",
    "feedback": "🟣",
    "done": "🟢",
    "on_hold": "⚪",
}

TASK_PRIORITIES = ["low", "normal", "high", "urgent"]
TASK_PRIORITY_LABELS = {"low": "낮음", "normal": "보통", "high": "높음", "urgent": "긴급"}

ASSIGNEE_ROLES = ["owner", "assignee", "watcher"]

# 보안(회사 경영) 카테고리: 작성자 + 지정 담당자에게만 보이는 업무
CONFIDENTIAL_CATEGORY = "company_mgmt"
CATEGORY_LABELS = {"company_mgmt": "회사 경영(보안)"}


def normalize_tags(raw: str | None) -> str | None:
    """쉼표/공백 구분 입력을 정규화: 공백·중복 제거. '#a, b' → 'a,b'."""
    if not raw:
        return None
    parts: list[str] = []
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

ATTACHMENT_BUCKET = "task-attachments"
ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/jpg", "image/webp", "image/gif"}

# 결제변경 검증(사후 검증) 연동
PAYMENT_CHANGE_TASK_TYPE = "payment_change_request"
PAYMENT_CHANGE_TYPES = ["refund", "cancel_card", "cancel_transfer", "method_change", "onnuri_change"]
PAYMENT_CHANGE_TYPE_LABELS = {
    "refund": "환불/반품",
    "cancel_card": "신용카드 취소",
    "cancel_transfer": "계좌이체 취소/반환",
    "method_change": "결제수단 변경",
    "onnuri_change": "온누리 결제 변경",
}


# ─────────────────────────────────────────────────────────────────────
# 캐시된 로더
# ─────────────────────────────────────────────────────────────────────

TASK_SCOPES = ["store", "company"]
TASK_SCOPE_LABELS = {"store": "🏪 내 매장 전용", "company": "🌐 전체 공개 (교차 매장 가능)"}


@st.cache_data(ttl=60, show_spinner=False)
def load_tasks_cached(
    store_name: str | None = None,
    include_done: bool = False,
    db_filename: str | None = None,
) -> list[dict]:
    """매장별 일반 업무 목록. None이면 전 매장 (superadmin용).
    - scope='store': store_name 또는 db_filename 일치하는 매장만
    - scope='company': 전체 공개 → 모든 매장에서 노출
    보안(회사 경영) 업무는 여기서 제외.

    db_filename 폴백: 결제변경 검증 등 store_name 표기가 달라도
    같은 매장 DB 파일이면 목록에 포함되도록 한다.
    """
    client, err = _client()
    if err or not client:
        return []

    def _run(select_cols: str):
        q = client.table("app_tasks").select(select_cols)
        if not include_done:
            q = q.not_.in_("status", ["done", "on_hold"])
        return (q.order("created_at", desc=True).execute().data or [])

    _cols_full = (
        "id, parent_task_id, title, description, status, priority, "
        "start_date, due_date, created_by, store_name, db_filename, "
        "scope, category, tags, is_pinned, created_at, updated_at, closed_at"
    )
    _cols_no_scope = (
        "id, parent_task_id, title, description, status, priority, "
        "start_date, due_date, created_by, store_name, db_filename, "
        "category, tags, is_pinned, created_at, updated_at, closed_at"
    )
    _cols_legacy = (
        "id, parent_task_id, title, description, status, priority, "
        "start_date, due_date, created_by, store_name, db_filename, "
        "created_at, updated_at, closed_at"
    )
    rows = []
    for cols in (_cols_full, _cols_no_scope, _cols_legacy):
        try:
            rows = _run(cols)
            break
        except Exception as e:
            if any(c in str(e) for c in ("scope", "category", "tags", "is_pinned")):
                continue
            return []

    # 보안 업무 제외
    rows = [t for t in rows if t.get("category") != CONFIDENTIAL_CATEGORY]

    # scope 필터링 (superadmin / scope 미설정 시 전체 허용)
    if store_name or db_filename:
        def _store_match(t: dict) -> bool:
            if t.get("scope", "store") == "company":
                return True
            if store_name and t.get("store_name") == store_name:
                return True
            if db_filename and t.get("db_filename") == db_filename:
                return True
            return False
        rows = [t for t in rows if _store_match(t)]
    return rows


@st.cache_data(ttl=60, show_spinner=False)
def load_my_confidential_tasks_cached(me_username: str, include_done: bool = False) -> list[dict]:
    """내가 작성했거나 담당자로 지정된 보안(회사 경영) 업무만 반환. 매장 무관."""
    if not me_username:
        return []
    client, err = _client()
    if err or not client:
        return []
    try:
        # 내가 담당자로 지정된 task_id 수집
        ar = client.table("app_task_assignees").select("task_id").eq(
            "employee_username", me_username
        ).execute()
        my_assigned_ids = {int(row["task_id"]) for row in (ar.data or []) if row.get("task_id") is not None}

        _cols = (
            "id, parent_task_id, title, description, status, priority, "
            "start_date, due_date, created_by, store_name, db_filename, "
            "category, tags, is_pinned, created_at, updated_at, closed_at"
        )
        try:
            q = client.table("app_tasks").select(_cols).eq("category", CONFIDENTIAL_CATEGORY)
            if not include_done:
                q = q.not_.in_("status", ["done", "on_hold"])
            r = q.order("created_at", desc=True).execute()
        except Exception as _e:
            if any(c in str(_e) for c in ("tags", "is_pinned")):
                _cols2 = (
                    "id, parent_task_id, title, description, status, priority, "
                    "start_date, due_date, created_by, store_name, db_filename, "
                    "category, created_at, updated_at, closed_at"
                )
                q = client.table("app_tasks").select(_cols2).eq("category", CONFIDENTIAL_CATEGORY)
                if not include_done:
                    q = q.not_.in_("status", ["done", "on_hold"])
                r = q.order("created_at", desc=True).execute()
            else:
                raise
        rows = r.data or []
        # 가시성: 작성자 본인이거나 담당자인 경우만
        return [
            t for t in rows
            if t.get("created_by") == me_username or int(t["id"]) in my_assigned_ids
        ]
    except Exception:
        return []


def load_task_by_id(task_id: int) -> dict | None:
    """단일 업무 조회 (알림 딥링크·포커스용). 캐시 없음 — 최신 상태 필요.

    maybe_single() 은 0건일 때 예외를 던지는 클라이언트가 있어 limit(1) 로 조회한다.
    """
    client, err = _client()
    if err or not client or not task_id:
        return None
    _cols_candidates = (
        (
            "id, parent_task_id, title, description, status, priority, "
            "start_date, due_date, created_by, store_name, db_filename, "
            "scope, category, tags, is_pinned, task_type, verify_status, "
            "created_at, updated_at, closed_at"
        ),
        (
            "id, parent_task_id, title, description, status, priority, "
            "start_date, due_date, created_by, store_name, db_filename, "
            "scope, category, tags, is_pinned, created_at, updated_at, closed_at"
        ),
        (
            "id, parent_task_id, title, description, status, priority, "
            "start_date, due_date, created_by, store_name, db_filename, "
            "created_at, updated_at, closed_at"
        ),
    )
    for cols in _cols_candidates:
        try:
            r = client.table("app_tasks").select(cols).eq("id", int(task_id)).limit(1).execute()
            rows = r.data or []
            return rows[0] if rows else None
        except Exception as e:
            if any(c in str(e) for c in ("task_type", "verify_status", "scope", "category", "tags", "is_pinned")):
                continue
            logger.warning("load_task_by_id(%s) 실패: %s", task_id, e)
            return None
    return None


def load_my_assigned_tasks(me_username: str, include_done: bool = False) -> list[dict]:
    """내가 담당자로 지정된 업무 (결제변경 검증 등). 매장명 불일치여도 목록에 보이게 한다."""
    if not me_username:
        return []
    client, err = _client()
    if err or not client:
        return []
    try:
        ar = client.table("app_task_assignees").select("task_id").eq(
            "employee_username", me_username
        ).execute()
        ids = sorted({
            int(row["task_id"]) for row in (ar.data or []) if row.get("task_id") is not None
        })
        if not ids:
            return []
        _cols = (
            "id, parent_task_id, title, description, status, priority, "
            "start_date, due_date, created_by, store_name, db_filename, "
            "scope, category, tags, is_pinned, task_type, verify_status, "
            "created_at, updated_at, closed_at"
        )
        _cols_basic = (
            "id, parent_task_id, title, description, status, priority, "
            "start_date, due_date, created_by, store_name, db_filename, "
            "created_at, updated_at, closed_at"
        )
        rows: list[dict] = []
        for cols in (_cols, _cols_basic):
            try:
                # PostgREST in_ 는 한 번에 많이내면 실패할 수 있어 청크
                chunk: list[dict] = []
                for i in range(0, len(ids), 200):
                    batch = ids[i:i + 200]
                    q = client.table("app_tasks").select(cols).in_("id", batch)
                    if not include_done:
                        q = q.not_.in_("status", ["done", "on_hold"])
                    chunk.extend(q.execute().data or [])
                rows = chunk
                break
            except Exception as e:
                if any(c in str(e) for c in ("task_type", "verify_status", "scope", "category", "tags", "is_pinned")):
                    continue
                logger.warning("load_my_assigned_tasks 실패: %s", e)
                return []
        # 보안 업무는 기존 confidential 로더가 담당 — 여기서는 제외해 중복·권한 혼선 방지
        return [t for t in rows if t.get("category") != CONFIDENTIAL_CATEGORY]
    except Exception as e:
        logger.warning("load_my_assigned_tasks 예외: %s", e)
        return []


@st.cache_data(ttl=60, show_spinner=False)
def load_task_assignees_cached(task_ids: tuple[int, ...]) -> dict[int, list[dict]]:
    """task_id → assignees 리스트."""
    if not task_ids:
        return {}
    client, err = _client()
    if err or not client:
        return {}
    try:
        r = client.table("app_task_assignees").select("*").in_("task_id", list(task_ids)).execute()
        out: dict[int, list[dict]] = {}
        for row in (r.data or []):
            out.setdefault(int(row["task_id"]), []).append(row)
        return out
    except Exception:
        return {}


@st.cache_data(ttl=30, show_spinner=False)
def load_task_comments_cached(task_id: int) -> list[dict]:
    client, err = _client()
    if err or not client:
        return []
    try:
        r = client.table("app_task_comments").select("*").eq("task_id", task_id).order("created_at").execute()
        return r.data or []
    except Exception:
        return []


@st.cache_data(ttl=30, show_spinner=False)
def load_task_attachments_cached(task_id: int) -> list[dict]:
    client, err = _client()
    if err or not client:
        return []
    try:
        r = client.table("app_task_attachments").select("*").eq("task_id", task_id).order("uploaded_at").execute()
        return r.data or []
    except Exception:
        return []


@st.cache_data(ttl=30, show_spinner=False)
def load_task_activity_cached(task_id: int) -> list[dict]:
    client, err = _client()
    if err or not client:
        return []
    try:
        r = client.table("app_task_activity").select("*").eq("task_id", task_id).order("created_at", desc=True).limit(50).execute()
        return r.data or []
    except Exception:
        return []


@st.cache_data(ttl=15, show_spinner=False)
def load_my_notifications_cached(username: str, unread_only: bool = False, limit: int = 30) -> list[dict]:
    client, err = _client()
    if err or not client or not username:
        return []
    try:
        q = client.table("app_notifications").select("*").eq("recipient_username", username)
        if unread_only:
            q = q.eq("is_read", False)
        r = q.order("sent_at", desc=True).limit(limit).execute()
        return r.data or []
    except Exception:
        return []


@st.cache_data(ttl=15, show_spinner=False)
def count_unread_notifications(username: str) -> int:
    client, err = _client()
    if err or not client or not username:
        return 0
    try:
        r = client.table("app_notifications").select("id", count="exact").eq("recipient_username", username).eq("is_read", False).execute()
        return int(getattr(r, "count", 0) or 0)
    except Exception:
        return 0


@st.cache_data(ttl=300, show_spinner=False)
def load_templates_cached() -> dict[str, str]:
    """template_key → body_template."""
    client, err = _client()
    if err or not client:
        return {}
    try:
        r = client.table("app_notification_templates").select("template_key, body_template").execute()
        return {row["template_key"]: row["body_template"] for row in (r.data or [])}
    except Exception:
        return {}


def clear_task_caches():
    """업무·알림·템플릿 관련 모든 캐시를 무효화."""
    load_tasks_cached.clear()
    load_my_confidential_tasks_cached.clear()
    load_task_assignees_cached.clear()
    load_task_comments_cached.clear()
    load_task_attachments_cached.clear()
    load_task_activity_cached.clear()
    load_my_notifications_cached.clear()
    count_unread_notifications.clear()


def clear_template_cache():
    load_templates_cached.clear()


# ─────────────────────────────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────────────────────────────

def create_task(
    title: str,
    description: str,
    created_by: str,
    store_name: str | None,
    db_filename: str | None,
    parent_task_id: int | None = None,
    start_date: date | None = None,
    due_date: date | None = None,
    priority: str = "normal",
    assignees: list[str] | None = None,
    category: str | None = None,
    tags: str | None = None,
    is_pinned: bool = False,
    scope: str = "store",
) -> tuple[int | None, str | None]:
    """업무 생성. assignees는 owner+assignees를 묶은 username 리스트.
    category가 CONFIDENTIAL_CATEGORY면 보안(회사 경영) 업무로 작성자·담당자에게만 노출."""
    client, err = _client()
    if err or not client:
        return None, err or "Supabase 연결 불가"
    try:
        row = {
            "title": (title or "").strip(),
            "description": (description or "").strip() or None,
            "status": "requested",
            "priority": priority if priority in TASK_PRIORITIES else "normal",
            "start_date": start_date.isoformat() if start_date else None,
            "due_date": due_date.isoformat() if due_date else None,
            "created_by": created_by,
            "store_name": store_name,
            "db_filename": db_filename,
            "parent_task_id": int(parent_task_id) if parent_task_id else None,
            "category": (category or None),
            "tags": normalize_tags(tags),
            "is_pinned": bool(is_pinned),
            "scope": scope if scope in TASK_SCOPES else "store",
        }
        # 구 스키마 호환: 신규 컬럼 미존재 시 제외 후 재시도
        _optional_cols = ["scope", "category", "tags", "is_pinned"]
        for _attempt in range(len(_optional_cols) + 1):
            try:
                r = client.table("app_tasks").insert(row).execute()
                break
            except Exception as _ins_e:
                _msg = str(_ins_e)
                _dropped = False
                for _c in _optional_cols:
                    if _c in row and _c in _msg and ("PGRST204" in _msg or "schema cache" in _msg or "column" in _msg):
                        row.pop(_c, None)
                        _dropped = True
                        break
                if not _dropped:
                    raise
        new_id = int(r.data[0]["id"]) if r.data else None
        if not new_id:
            return None, "insert 후 id를 가져오지 못했습니다."

        # 담당자 등록
        for idx, uname in enumerate(assignees or []):
            uname_clean = (uname or "").strip()
            if not uname_clean:
                continue
            try:
                client.table("app_task_assignees").insert({
                    "task_id": new_id,
                    "employee_username": uname_clean,
                    "role": "owner" if idx == 0 else "assignee",
                    "assigned_by": created_by,
                }).execute()
            except Exception:
                pass

        log_activity(new_id, created_by, "created", {"title": row["title"]})
        notify_recipients(
            task_id=new_id,
            recipients=[u for u in (assignees or []) if u and u != created_by],
            event_type="task_assigned",
            template_vars={
                "name": "",   # 발송 시 username→name 매핑
                "title": row["title"],
                "due_date": row["due_date"] or "-",
                "requester": created_by,
                "link": _task_link(new_id),
            },
            in_app_message=f"신규 업무 배정: {row['title']}",
        )
        clear_task_caches()
        return new_id, None
    except Exception as e:
        return None, str(e)


def update_status(task_id: int, new_status: str, actor: str) -> tuple[bool, str | None]:
    if new_status not in TASK_STATUSES:
        return False, "허용되지 않는 상태값"
    client, err = _client()
    if err or not client:
        return False, err
    try:
        # 기존 상태 조회
        cur = client.table("app_tasks").select("status, title").eq("id", task_id).single().execute()
        cur_status = (cur.data or {}).get("status")
        title = (cur.data or {}).get("title", "")
        patch: dict = {"status": new_status, "updated_at": _now_iso()}
        if new_status == "done":
            patch["closed_at"] = _now_iso()
        client.table("app_tasks").update(patch).eq("id", task_id).execute()
        log_activity(task_id, actor, "status_changed", {"from": cur_status, "to": new_status})
        # 알림: 전 담당자 + 작성자
        recipients = _all_stakeholders(task_id, exclude=actor)
        notify_recipients(
            task_id=task_id,
            recipients=recipients,
            event_type="status_changed",
            template_vars={
                "title": title,
                "from_status": TASK_STATUS_LABELS.get(cur_status, cur_status or "-"),
                "to_status": TASK_STATUS_LABELS.get(new_status, new_status),
                "actor": actor,
                "link": _task_link(task_id),
            },
            in_app_message=f"상태 변경: {title} → {TASK_STATUS_LABELS.get(new_status, new_status)}",
        )
        clear_task_caches()
        return True, None
    except Exception as e:
        return False, str(e)


def update_task_fields(task_id: int, actor: str, **fields) -> tuple[bool, str | None]:
    """제목·설명·일정·우선순위·태그·상단고정 변경."""
    allowed = {"title", "description", "start_date", "due_date", "priority", "tags", "is_pinned", "scope"}
    patch = {k: v for k, v in fields.items() if k in allowed}
    if "tags" in patch:
        patch["tags"] = normalize_tags(patch["tags"])
    if not patch:
        return True, None
    for k in ("start_date", "due_date"):
        v = patch.get(k)
        if isinstance(v, date):
            patch[k] = v.isoformat()
    patch["updated_at"] = _now_iso()
    client, err = _client()
    if err or not client:
        return False, err
    try:
        client.table("app_tasks").update(patch).eq("id", task_id).execute()
        log_activity(task_id, actor, "updated", patch)
        clear_task_caches()
        return True, None
    except Exception as e:
        return False, str(e)


def delete_task(task_id: int) -> tuple[bool, str | None]:
    """업무 삭제. 하위업무가 있으면 parent_task_id를 NULL로 만들고 삭제."""
    client, err = _client()
    if err or not client:
        return False, err or "Supabase 연결 불가"
    try:
        # 하위업무의 parent 참조 해제 (CASCADE 미설정 환경 대비)
        client.table("app_tasks").update({"parent_task_id": None}).eq("parent_task_id", task_id).execute()
        client.table("app_tasks").delete().eq("id", task_id).execute()
        clear_task_caches()
        return True, None
    except Exception as e:
        return False, str(e)


def assign_users(task_id: int, usernames: list[str], actor: str) -> tuple[bool, str | None]:
    """담당자 교체: 기존 owner는 유지, assignee/watcher만 교체."""
    client, err = _client()
    if err or not client:
        return False, err
    try:
        existing = client.table("app_task_assignees").select("*").eq("task_id", task_id).execute()
        existing_users = {row["employee_username"] for row in (existing.data or []) if row.get("role") != "owner"}
        owners = {row["employee_username"] for row in (existing.data or []) if row.get("role") == "owner"}
        new_users = {u.strip() for u in usernames if u and u.strip()} - owners

        to_add = new_users - existing_users
        to_remove = existing_users - new_users

        title_row = client.table("app_tasks").select("title").eq("id", task_id).single().execute()
        title = (title_row.data or {}).get("title", "")

        for u in to_remove:
            client.table("app_task_assignees").delete().eq("task_id", task_id).eq("employee_username", u).execute()
            log_activity(task_id, actor, "unassigned", {"user": u})
            notify_recipients(
                task_id=task_id,
                recipients=[u],
                event_type="task_assigned",
                template_vars={"name": u, "title": title, "due_date": "-", "requester": actor, "link": _task_link(task_id)},
                in_app_message=f"업무 제외: {title}",
            )
        for u in to_add:
            try:
                client.table("app_task_assignees").insert({
                    "task_id": task_id,
                    "employee_username": u,
                    "role": "assignee",
                    "assigned_by": actor,
                }).execute()
                log_activity(task_id, actor, "assigned", {"user": u})
                notify_recipients(
                    task_id=task_id,
                    recipients=[u],
                    event_type="task_assigned",
                    template_vars={"name": u, "title": title, "due_date": "-", "requester": actor, "link": _task_link(task_id)},
                    in_app_message=f"신규 업무 배정: {title}",
                )
            except Exception:
                pass
        clear_task_caches()
        return True, None
    except Exception as e:
        return False, str(e)


def post_comment(task_id: int, author: str, body: str,
                 parent_comment_id: int | None = None) -> tuple[int | None, str | None]:
    body = (body or "").strip()
    if not body:
        return None, "내용이 비어 있습니다."
    client, err = _client()
    if err or not client:
        return None, err
    try:
        r = client.table("app_task_comments").insert({
            "task_id": task_id,
            "author": author,
            "body": body,
            "parent_comment_id": int(parent_comment_id) if parent_comment_id else None,
        }).execute()
        new_id = int(r.data[0]["id"]) if r.data else None
        task_row = client.table("app_tasks").select("title").eq("id", task_id).single().execute()
        title = (task_row.data or {}).get("title", "")
        preview = body if len(body) <= 60 else body[:57] + "..."
        log_activity(task_id, author, "commented", {"comment_id": new_id, "preview": preview})
        recipients = _all_stakeholders(task_id, exclude=author)
        notify_recipients(
            task_id=task_id,
            recipients=recipients,
            event_type="comment_added",
            template_vars={"title": title, "author": author, "preview": preview, "link": _task_link(task_id)},
            in_app_message=f"새 댓글: {title}",
        )
        clear_task_caches()
        return new_id, None
    except Exception as e:
        return None, str(e)


def attach_file(
    task_id: int | None,
    comment_id: int | None,
    uploaded_file,
    uploaded_by: str,
) -> tuple[dict | None, str | None]:
    """Streamlit UploadedFile을 Supabase Storage에 업로드 + DB 레코드 INSERT."""
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

        storage_path = f"{task_id or 'orphan'}/{uuid.uuid4().hex}.{ext}"
        up_res = admin.storage.from_(ATTACHMENT_BUCKET).upload(
            path=storage_path,
            file=data,
            file_options={"content-type": mime, "upsert": "false"},
        )
        # SDK 버전에 따라 예외 대신 .error 속성으로 오류를 반환하는 경우 방어
        _up_err = getattr(up_res, "error", None)
        if _up_err:
            return None, (
                f"Storage 업로드 실패: {_up_err}. "
                "Supabase 대시보드 → Storage → 'task-attachments' 버킷이 존재하는지 확인하세요."
            )

        client, _ = _client()
        if client:
            r = client.table("app_task_attachments").insert({
                "task_id": task_id,
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

        if task_id:
            log_activity(task_id, uploaded_by, "attached", {"name": original_name, "size": byte_size})
        clear_task_caches()
        return new_row, None
    except Exception as e:
        return None, str(e)


def signed_url_for(storage_path: str, expires_in: int = 3600) -> str | None:
    admin, err = _admin_client()
    if err or not admin:
        admin, err2 = _client()
        if err2 or not admin:
            return None
    try:
        r = admin.storage.from_(ATTACHMENT_BUCKET).create_signed_url(storage_path, expires_in)
        if isinstance(r, dict):
            return r.get("signedURL") or r.get("signed_url")
        return getattr(r, "signedURL", None) or getattr(r, "signed_url", None)
    except Exception:
        return None


def download_attachment_bytes(storage_path: str) -> bytes | None:
    admin, err = _admin_client()
    if err or not admin:
        admin, err2 = _client()
        if err2 or not admin:
            return None
    try:
        return admin.storage.from_(ATTACHMENT_BUCKET).download(storage_path)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────
# 활동 로그
# ─────────────────────────────────────────────────────────────────────

def log_activity(task_id: int, actor: str, action: str, payload: dict | None = None):
    client, err = _client()
    if err or not client:
        return
    try:
        client.table("app_task_activity").insert({
            "task_id": task_id,
            "actor": actor,
            "action": action,
            "payload": payload or {},
        }).execute()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────
# 알림 (in-app + 친구톡)
# ─────────────────────────────────────────────────────────────────────

def notify_recipients(
    task_id: int | None,
    recipients: list[str],
    event_type: str,
    template_vars: dict[str, Any],
    in_app_message: str,
):
    """recipient별 in-app + 친구톡 발송."""
    if not recipients:
        return
    recipients = list({(r or "").strip() for r in recipients if r and (r or "").strip()})
    if not recipients:
        return

    client, err = _client()
    if err or not client:
        return

    # 친구톡 본문은 send_friendtalk 안에서 템플릿 로드
    link_path = template_vars.get("link") or _task_link(task_id) if task_id else None

    # 수신자 정보 일괄 조회 (phone, kakao_notify_enabled, kakao_friend_added, name)
    try:
        users_r = client.table("app_users").select(
            "username, name, phone, kakao_notify_enabled, kakao_friend_added"
        ).in_("username", recipients).execute()
        user_map = {row["username"]: row for row in (users_r.data or [])}
    except Exception:
        user_map = {}

    for uname in recipients:
        user = user_map.get(uname) or {}
        # 친구톡 가능 여부
        phone = (user.get("phone") or "").strip()
        notify_enabled = bool(user.get("kakao_notify_enabled", True))
        friend_added = bool(user.get("kakao_friend_added", False))
        display_name = (user.get("name") or uname or "").strip()

        # 변수 보강
        vars_per_user = dict(template_vars)
        if "name" in vars_per_user and not vars_per_user["name"]:
            vars_per_user["name"] = display_name

        # 1) in_app row INSERT (kakao_status는 발송 시도 후 갱신)
        kakao_status_initial: str
        if not phone:
            kakao_status_initial = "no_phone"
        elif not notify_enabled:
            kakao_status_initial = "disabled"
        elif not friend_added:
            kakao_status_initial = "not_friend"
        else:
            kakao_status_initial = "pending"

        try:
            ins = client.table("app_notifications").insert({
                "recipient_username": uname,
                "task_id": task_id,
                "type": event_type,
                "message": in_app_message,
                "link_path": link_path,
                "channel": "both" if kakao_status_initial == "pending" else "in_app",
                "kakao_status": kakao_status_initial,
            }).execute()
            notif_id = int(ins.data[0]["id"]) if ins.data else None
        except Exception:
            notif_id = None

        # 2) 친구톡 발송 (가능한 경우)
        if kakao_status_initial == "pending" and notif_id:
            try:
                from solapi_sender import send_friendtalk  # noqa: WPS433
                body = render_template(event_type, vars_per_user)
                if body:
                    result = send_friendtalk(to_phone=phone, body=body)
                    if isinstance(result, dict):
                        client.table("app_notifications").update({
                            "kakao_status": result.get("status") or "failed",
                            "kakao_msg_id": result.get("msg_id"),
                            "kakao_error": result.get("error"),
                        }).eq("id", notif_id).execute()
            except Exception as e:
                try:
                    client.table("app_notifications").update({
                        "kakao_status": "failed",
                        "kakao_error": str(e)[:200],
                    }).eq("id", notif_id).execute()
                except Exception:
                    pass

    # 캐시 무효화 (수신자별 카운트)
    load_my_notifications_cached.clear()
    count_unread_notifications.clear()


def mark_notification_read(notif_id: int) -> bool:
    client, err = _client()
    if err or not client:
        return False
    try:
        client.table("app_notifications").update({
            "is_read": True,
            "read_at": _now_iso(),
        }).eq("id", notif_id).execute()
        load_my_notifications_cached.clear()
        count_unread_notifications.clear()
        return True
    except Exception:
        return False


def mark_all_read(username: str) -> int:
    client, err = _client()
    if err or not client:
        return 0
    try:
        r = client.table("app_notifications").update({
            "is_read": True,
            "read_at": _now_iso(),
        }).eq("recipient_username", username).eq("is_read", False).execute()
        load_my_notifications_cached.clear()
        count_unread_notifications.clear()
        return len(r.data or [])
    except Exception:
        return 0


def render_template(event_type: str, vars_dict: dict[str, Any]) -> str:
    templates = load_templates_cached()
    body = templates.get(event_type) or ""
    try:
        return body.format(**{k: ("-" if v is None else str(v)) for k, v in vars_dict.items()})
    except KeyError:
        # 변수가 누락되어도 원본 반환
        return body
    except Exception:
        return body


def update_template(template_key: str, new_body: str, actor: str) -> tuple[bool, str | None]:
    client, err = _client()
    if err or not client:
        return False, err
    try:
        client.table("app_notification_templates").update({
            "body_template": new_body,
            "updated_by": actor,
            "updated_at": _now_iso(),
        }).eq("template_key", template_key).execute()
        clear_template_cache()
        return True, None
    except Exception as e:
        return False, str(e)


# ─────────────────────────────────────────────────────────────────────
# 친구추가 미완료 직원 조회 (관리자 카드용)
# ─────────────────────────────────────────────────────────────────────

def load_friend_pending_users(store_id: int | None = None) -> list[dict]:
    """kakao_friend_added=false 인 직원 목록 (해당 매장 또는 전체)."""
    client, err = _client()
    if err or not client:
        return []
    try:
        q = client.table("app_users").select(
            "id, username, name, phone, store_id, role"
        ).eq("kakao_friend_added", False).neq("role", "superadmin")
        if store_id is not None:
            q = q.eq("store_id", int(store_id))
        r = q.execute()
        return r.data or []
    except Exception:
        return []


def mark_friend_added_by_phone(phone: str) -> bool:
    """webhook 또는 수동 동기화: phone으로 직원 찾아 kakao_friend_added=true."""
    client, err = _client()
    if err or not client:
        return False
    phone_clean = "".join(c for c in (phone or "") if c.isdigit())
    if not phone_clean:
        return False
    try:
        client.table("app_users").update({
            "kakao_friend_added": True,
        }).eq("phone", phone_clean).execute()
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────
# 마감 임박 lazy 배치
# ─────────────────────────────────────────────────────────────────────

BATCH_KEY_DUE_SOON = "due_soon_dminus1"


def maybe_run_due_soon_batch() -> bool:
    """오늘 D-1 마감 임박 배치 1회 실행. 이미 실행됐으면 skip."""
    client, err = _client()
    if err or not client:
        return False
    today = _today_kst()
    try:
        # 멱등 검사
        chk = client.table("app_batch_runs").select("id").eq("batch_key", BATCH_KEY_DUE_SOON).eq("run_date", today.isoformat()).execute()
        if chk.data:
            return False

        # running 레코드 선점 (UNIQUE 제약으로 race condition 방어)
        try:
            client.table("app_batch_runs").insert({
                "batch_key": BATCH_KEY_DUE_SOON,
                "run_date": today.isoformat(),
                "status": "running",
            }).execute()
        except Exception:
            return False  # 다른 세션이 먼저 선점

        # 내일 마감인 업무 조회
        target_due = (today + timedelta(days=1)).isoformat()
        tasks_r = client.table("app_tasks").select(
            "id, title, due_date"
        ).eq("due_date", target_due).not_.in_("status", ["done", "on_hold"]).execute()
        tasks = tasks_r.data or []
        processed = 0

        for t in tasks:
            assignees_r = client.table("app_task_assignees").select("employee_username").eq("task_id", t["id"]).execute()
            recipients = [r["employee_username"] for r in (assignees_r.data or [])]
            if not recipients:
                continue
            notify_recipients(
                task_id=int(t["id"]),
                recipients=recipients,
                event_type="due_soon",
                template_vars={
                    "title": t["title"],
                    "due_date": t["due_date"],
                    "link": _task_link(int(t["id"])),
                },
                in_app_message=f"내일 마감: {t['title']}",
            )
            processed += 1

        client.table("app_batch_runs").update({
            "status": "success",
            "finished_at": _now_iso(),
            "processed_count": processed,
        }).eq("batch_key", BATCH_KEY_DUE_SOON).eq("run_date", today.isoformat()).execute()
        return True
    except Exception as e:
        try:
            client.table("app_batch_runs").update({
                "status": "failed",
                "finished_at": _now_iso(),
                "error": str(e)[:500],
            }).eq("batch_key", BATCH_KEY_DUE_SOON).eq("run_date", today.isoformat()).execute()
        except Exception:
            pass
        return False


def retry_out_of_hours_notifications() -> int:
    """야간 거부된 친구톡을 재시도 (08시 이후 호출 권장). 처리 건수 반환."""
    client, err = _client()
    if err or not client:
        return 0
    try:
        r = client.table("app_notifications").select(
            "id, recipient_username, type, link_path, task_id"
        ).eq("kakao_status", "out_of_hours").limit(100).execute()
        rows = r.data or []
        count = 0
        for n in rows:
            uname = n["recipient_username"]
            u_r = client.table("app_users").select(
                "phone, name, kakao_friend_added, kakao_notify_enabled"
            ).eq("username", uname).maybe_single().execute()
            user = u_r.data or {}
            phone = (user.get("phone") or "").strip()
            if not phone or not user.get("kakao_friend_added") or not user.get("kakao_notify_enabled", True):
                client.table("app_notifications").update({"kakao_status": "skipped"}).eq("id", n["id"]).execute()
                continue
            try:
                from solapi_sender import send_friendtalk  # noqa: WPS433
                # 원본 본문 복원이 어려우므로 in_app message 재사용
                msg_r = client.table("app_notifications").select("message").eq("id", n["id"]).maybe_single().execute()
                body = (msg_r.data or {}).get("message", "")
                result = send_friendtalk(to_phone=phone, body=body)
                if isinstance(result, dict):
                    client.table("app_notifications").update({
                        "kakao_status": result.get("status") or "failed",
                        "kakao_msg_id": result.get("msg_id"),
                        "kakao_error": result.get("error"),
                    }).eq("id", n["id"]).execute()
                    if result.get("status") == "sent":
                        count += 1
            except Exception:
                pass
        return count
    except Exception:
        return 0


# ─────────────────────────────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────────────────────────────

def _all_stakeholders(task_id: int, exclude: str | None = None) -> list[str]:
    client, err = _client()
    if err or not client:
        return []
    try:
        r = client.table("app_task_assignees").select("employee_username").eq("task_id", task_id).execute()
        users = [row["employee_username"] for row in (r.data or [])]
        # 작성자도 포함
        t_r = client.table("app_tasks").select("created_by").eq("id", task_id).maybe_single().execute()
        if t_r.data and t_r.data.get("created_by"):
            users.append(t_r.data["created_by"])
        uniq = list({u for u in users if u and u != exclude})
        return uniq
    except Exception:
        return []


def _task_link(task_id: int | None) -> str:
    if not task_id:
        return ""
    return f"?task={task_id}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_kst() -> date:
    return (datetime.now(timezone.utc) + timedelta(hours=9)).date()


# ─────────────────────────────────────────────────────────────────────
# 결제변경 사내 검증(사후 검증) 연동
#   결제 자체는 매출관리 기존 로직이 즉시 반영. 여기서는 검증 태스크만 다룬다.
# ─────────────────────────────────────────────────────────────────────

def create_payment_change_task(
    sale_id: int,
    payment_id: int | None,
    customer_name: str | None,
    change_type: str,
    original_payment: dict | None,
    new_payment: dict | None,
    reason: str,
    created_by: str,
    store_name: str | None,
    db_filename: str | None,
    assignees: list[str] | None = None,
) -> tuple[int | None, str | None]:
    """결제변경 사후 검증 태스크 생성 + 메타 저장 + 검증자 알림.
    결제 반영은 호출 측(매출관리)에서 이미 완료된 상태로 들어온다."""
    client, err = _client()
    if err or not client:
        return None, err or "Supabase 연결 불가"

    change_label = PAYMENT_CHANGE_TYPE_LABELS.get(change_type, change_type)
    title = f"[결제변경 검증] {customer_name or '-'} · {change_label}"
    op = original_payment or {}
    npd = new_payment or {}

    def _fmt(p: dict) -> str:
        amt = p.get("amount")
        amt_s = f"{int(amt):,}원" if amt not in (None, "") else "-"
        parts = [amt_s, p.get("method") or "-"]
        if p.get("onnuri"):
            parts.append(f"온누리:{p.get('onnuri')}")
        return " / ".join(parts)

    description = (
        f"결제변경 유형: {change_label}\n"
        f"고객: {customer_name or '-'}\n"
        f"원본 결제: {_fmt(op)}\n"
        f"변경 결제: {_fmt(npd)}\n"
        f"사유: {reason or '-'}\n"
        f"(결제는 매출관리에서 즉시 반영됨 — 본 건은 증빙 확인 후 완료 처리)"
    )

    try:
        row = {
            "title": title,
            "description": description,
            "status": "requested",
            "priority": "high",
            "created_by": created_by,
            "store_name": store_name,
            "db_filename": db_filename,
            "parent_task_id": None,
            "task_type": PAYMENT_CHANGE_TASK_TYPE,
            "verify_status": "pending",
        }
        r = client.table("app_tasks").insert(row).execute()
        task_id = int(r.data[0]["id"]) if r.data else None
        if not task_id:
            return None, "검증 태스크 생성 실패 (id 없음)"

        # 메타 저장
        try:
            client.table("app_payment_change_requests").insert({
                "task_id": task_id,
                "db_filename": db_filename,
                "sale_id": int(sale_id),
                "payment_id": int(payment_id) if payment_id else None,
                "customer_name": customer_name,
                "change_type": change_type,
                "original_amount": _to_int_or_none(op.get("amount")),
                "original_method": op.get("method"),
                "original_onnuri": op.get("onnuri"),
                "new_amount": _to_int_or_none(npd.get("amount")),
                "new_method": npd.get("method"),
                "new_onnuri": npd.get("onnuri"),
                "reason": reason,
                "created_by": created_by,
            }).execute()
        except Exception as e:
            # 메타 실패해도 태스크는 유지 — 활동 로그에 남김
            log_activity(task_id, created_by, "pcr_meta_failed", {"error": str(e)[:200]})

        # 담당자(검증자) 등록 + 알림
        recipients = [u for u in (assignees or []) if u and u != created_by]
        for idx, uname in enumerate(recipients):
            try:
                client.table("app_task_assignees").insert({
                    "task_id": task_id,
                    "employee_username": uname,
                    "role": "assignee",
                    "assigned_by": created_by,
                }).execute()
            except Exception:
                pass

        log_activity(task_id, created_by, "payment_change_created", {
            "change_type": change_type, "sale_id": sale_id, "payment_id": payment_id,
        })
        notify_recipients(
            task_id=task_id,
            recipients=recipients,
            event_type="task_assigned",
            template_vars={
                "name": "", "title": title, "due_date": "-",
                "requester": created_by, "link": _task_link(task_id),
            },
            in_app_message=f"결제변경 검증 요청: {title}",
        )
        clear_task_caches()
        return task_id, None
    except Exception as e:
        return None, str(e)


def resolve_payment_change(task_id: int, verifier: str, note: str | None = None) -> tuple[bool, str | None]:
    """결제변경 검증 완료 처리. verify_status='resolved' + 검증자/시각/비고 기록."""
    client, err = _client()
    if err or not client:
        return False, err
    try:
        client.table("app_tasks").update({
            "verify_status": "resolved",
            "verified_by": verifier,
            "verified_at": _now_iso(),
            "verify_note": (note or "").strip() or None,
            "status": "done",
            "closed_at": _now_iso(),
            "updated_at": _now_iso(),
        }).eq("id", task_id).execute()
        log_activity(task_id, verifier, "payment_change_resolved", {"note": (note or "")[:200]})

        # 요청자에게 완료 알림
        try:
            t_r = client.table("app_tasks").select("created_by, title").eq("id", task_id).maybe_single().execute()
            creator = (t_r.data or {}).get("created_by")
            title = (t_r.data or {}).get("title", "")
            if creator and creator != verifier:
                notify_recipients(
                    task_id=task_id,
                    recipients=[creator],
                    event_type="status_changed",
                    template_vars={
                        "title": title, "from_status": "미결", "to_status": "검증 완료",
                        "actor": verifier, "link": _task_link(task_id),
                    },
                    in_app_message=f"결제변경 검증 완료: {title}",
                )
        except Exception:
            pass
        clear_task_caches()
        return True, None
    except Exception as e:
        return False, str(e)


def load_payment_change_meta(task_id: int) -> dict | None:
    """task_id로 결제변경 메타 1건 조회."""
    client, err = _client()
    if err or not client:
        return None
    try:
        r = client.table("app_payment_change_requests").select("*").eq("task_id", task_id).maybe_single().execute()
        return r.data if isinstance(r.data, dict) else None
    except Exception:
        return None


def load_payment_verify_state(task_id: int) -> dict:
    """app_tasks의 검증 상태 컬럼만 조회 (컬럼 미존재 환경에서도 안전)."""
    client, err = _client()
    if err or not client:
        return {}
    try:
        r = client.table("app_tasks").select(
            "verify_status, verified_by, verified_at, verify_note"
        ).eq("id", task_id).maybe_single().execute()
        return r.data if isinstance(r.data, dict) else {}
    except Exception:
        return {}


def load_pending_payment_verifications(store_name: str | None, role: str) -> list[dict]:
    """미결(pending) 결제변경 검증 태스크 목록. superadmin이면 전 매장."""
    client, err = _client()
    if err or not client:
        return []
    try:
        q = client.table("app_tasks").select(
            "id, title, store_name, created_by, created_at, verify_status"
        ).eq("task_type", PAYMENT_CHANGE_TASK_TYPE).eq("verify_status", "pending")
        if role != "superadmin" and store_name:
            q = q.eq("store_name", store_name)
        r = q.order("created_at", desc=True).execute()
        return r.data or []
    except Exception:
        return []


def _to_int_or_none(v):
    try:
        if v in (None, ""):
            return None
        return int(round(float(v)))
    except Exception:
        return None
