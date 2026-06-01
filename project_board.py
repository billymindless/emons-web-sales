"""
프로젝트 관리 모듈 백엔드.

emons-web-sales `app.py`의 사이드바 `📊 프로젝트 관리`에서 사용.
Supabase 클라이언트는 app.py의 get_supabase_client()를 재사용.

스키마: SUPABASE_APP_PROJECTS.sql + SUPABASE_APP_PROJECTS_MIGRATION.sql
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import streamlit as st


# ─────────────────────────────────────────────────────────────────────
# Supabase 클라이언트 (app.py와 동일 인터페이스)
# ─────────────────────────────────────────────────────────────────────

def _client():
    """app.py의 get_supabase_client()를 lazy import해서 호출."""
    from app import get_supabase_client  # noqa: WPS433
    return get_supabase_client()


# ─────────────────────────────────────────────────────────────────────
# 상수
# ─────────────────────────────────────────────────────────────────────

PROJECT_STATUSES = ["진행예정", "진행중", "완료", "보류", "취소"]
PROJECT_STATUS_EMOJI = {
    "진행예정": "🟡",
    "진행중":   "🔵",
    "완료":     "🟢",
    "보류":     "⚪",
    "취소":     "🔴",
}

VISIBILITY_OPTIONS = ["public", "private"]
VISIBILITY_LABELS = {
    "public":  "🌐 전체 공개 (모든 직원 조회 가능)",
    "private": "🔒 비공개 (팀원만 조회 가능)",
}

MEMBER_ROLES = ["pm", "member"]
MEMBER_ROLE_LABELS = {"pm": "PM", "member": "팀원"}

LEGACY_PROJECT_CODE = "LEGACY"


# ─────────────────────────────────────────────────────────────────────
# 캐시된 로더
# ─────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def load_project_types_cached(include_inactive: bool = False) -> list[dict]:
    """프로젝트 유형(카테고리) 목록. display_order, name 순."""
    client, err = _client()
    if err or not client:
        return []
    try:
        q = client.table("app_project_types").select("*")
        if not include_inactive:
            q = q.eq("is_active", True)
        r = q.order("display_order").order("name").execute()
        return r.data or []
    except Exception:
        return []


@st.cache_data(ttl=60, show_spinner=False)
def load_projects_cached(include_archived: bool = False) -> list[dict]:
    """전체 프로젝트 목록 (가시성 필터는 호출 측에서 처리)."""
    client, err = _client()
    if err or not client:
        return []
    try:
        q = client.table("app_projects").select("*")
        if not include_archived:
            q = q.is_("archived_at", "null")
        r = q.order("created_at", desc=True).execute()
        return r.data or []
    except Exception:
        return []


@st.cache_data(ttl=60, show_spinner=False)
def load_project_members_cached(project_ids: tuple[int, ...]) -> dict[int, list[dict]]:
    """project_id → members 리스트."""
    if not project_ids:
        return {}
    client, err = _client()
    if err or not client:
        return {}
    try:
        r = client.table("app_project_members").select("*").in_(
            "project_id", list(project_ids)
        ).execute()
        out: dict[int, list[dict]] = {}
        for row in (r.data or []):
            out.setdefault(int(row["project_id"]), []).append(row)
        return out
    except Exception:
        return {}


@st.cache_data(ttl=60, show_spinner=False)
def load_my_project_ids_cached(me_username: str) -> set[int]:
    """내가 멤버로 참여한 프로젝트 id 집합."""
    if not me_username:
        return set()
    client, err = _client()
    if err or not client:
        return set()
    try:
        r = client.table("app_project_members").select("project_id").eq(
            "employee_username", me_username
        ).execute()
        return {int(row["project_id"]) for row in (r.data or []) if row.get("project_id") is not None}
    except Exception:
        return set()


def clear_project_caches():
    """프로젝트 관련 모든 캐시 무효화."""
    load_project_types_cached.clear()
    load_projects_cached.clear()
    load_project_members_cached.clear()
    load_my_project_ids_cached.clear()


# ─────────────────────────────────────────────────────────────────────
# 가시성 필터 (목록용)
# ─────────────────────────────────────────────────────────────────────

def filter_visible_projects(
    projects: list[dict],
    members_by_pid: dict[int, list[dict]],
    me_username: str,
    role: str,
) -> list[dict]:
    """프로젝트별 visibility + role 에 따라 사용자가 볼 수 있는 목록만 반환.
    - superadmin: 전부
    - public: 모두 조회 가능
    - private: 본인이 멤버인 경우만
    """
    if role == "superadmin":
        return projects
    out: list[dict] = []
    for p in projects:
        if (p.get("visibility") or "public") == "public":
            out.append(p)
            continue
        members = members_by_pid.get(int(p["id"]), [])
        if any((m.get("employee_username") or "") == me_username for m in members):
            out.append(p)
    return out


# ─────────────────────────────────────────────────────────────────────
# 권한 헬퍼
# ─────────────────────────────────────────────────────────────────────

def can_view(project: dict, members: list[dict], me_username: str, role: str) -> bool:
    """프로젝트 조회 가능 여부."""
    if role == "superadmin":
        return True
    if (project.get("visibility") or "public") == "public":
        return True
    return any((m.get("employee_username") or "") == me_username for m in members)


def can_edit(project: dict, members: list[dict], me_username: str, role: str) -> bool:
    """프로젝트 수정 가능 여부.
    누구나 수정 가능 (단, 조회 권한이 있는 경우)."""
    return can_view(project, members, me_username, role)


def can_delete(project: dict, members: list[dict], me_username: str, role: str) -> bool:
    """프로젝트 삭제 가능 여부 — superadmin + 해당 프로젝트 PM 만."""
    if role == "superadmin":
        return True
    for m in members:
        if (m.get("employee_username") or "") == me_username and (m.get("role") or "") == "pm":
            return True
    return False


def is_pm(members: list[dict], me_username: str) -> bool:
    return any(
        (m.get("employee_username") or "") == me_username and (m.get("role") or "") == "pm"
        for m in members
    )


# ─────────────────────────────────────────────────────────────────────
# 통계 (상단 카드)
# ─────────────────────────────────────────────────────────────────────

def project_stats(visible_projects: list[dict], members_by_pid: dict[int, list[dict]]) -> dict:
    """상단 통계 카드 4종 데이터.
    - 전체 / 진행중 / 담당자 미배정(=멤버 0명) / 완료
    """
    total = len(visible_projects)
    in_progress = sum(1 for p in visible_projects if (p.get("status") or "") == "진행중")
    done = sum(1 for p in visible_projects if (p.get("status") or "") == "완료")
    no_assignee = sum(
        1 for p in visible_projects
        if not (members_by_pid.get(int(p["id"])) or [])
    )
    return {
        "total": total,
        "in_progress": in_progress,
        "done": done,
        "no_assignee": no_assignee,
    }


# ─────────────────────────────────────────────────────────────────────
# 단건 조회
# ─────────────────────────────────────────────────────────────────────

def get_project(project_id: int) -> dict | None:
    """프로젝트 단건 + 유형명까지 join 한 dict 반환."""
    client, err = _client()
    if err or not client:
        return None
    try:
        r = client.table("app_projects").select("*").eq("id", int(project_id)).limit(1).execute()
        rows = r.data or []
        return rows[0] if rows else None
    except Exception:
        return None


def get_project_members(project_id: int) -> list[dict]:
    client, err = _client()
    if err or not client:
        return []
    try:
        r = client.table("app_project_members").select("*").eq(
            "project_id", int(project_id)
        ).order("role", desc=True).order("assigned_at").execute()
        return r.data or []
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────
# 프로젝트 코드 자동 채번 (YY-N 형식)
# ─────────────────────────────────────────────────────────────────────

def _next_project_code(year: int | None = None) -> str:
    """현재 연도의 최대 순번 + 1로 'YY-N' 형식 코드 생성."""
    yy = (year if year is not None else datetime.now().year) % 100
    prefix = f"{yy:02d}-"
    client, err = _client()
    if err or not client:
        return f"{prefix}1"
    try:
        r = client.table("app_projects").select("code").like("code", f"{prefix}%").execute()
        max_n = 0
        for row in (r.data or []):
            c = str(row.get("code") or "")
            if c.startswith(prefix):
                try:
                    n = int(c[len(prefix):])
                    if n > max_n:
                        max_n = n
                except Exception:
                    continue
        return f"{prefix}{max_n + 1}"
    except Exception:
        return f"{prefix}1"


# ─────────────────────────────────────────────────────────────────────
# CRUD: 프로젝트
# ─────────────────────────────────────────────────────────────────────

def create_project(
    name: str,
    created_by: str,
    type_id: int | None = None,
    status: str = "진행예정",
    visibility: str = "public",
    description: str | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    pm_usernames: list[str] | None = None,
    member_usernames: list[str] | None = None,
) -> tuple[int | None, str | None]:
    """프로젝트 생성 + PM/팀원 등록.
    pm_usernames: 필수 1명 이상.
    """
    if not (name or "").strip():
        return None, "프로젝트명을 입력하세요."
    if not (created_by or "").strip():
        return None, "작성자 정보가 비어있습니다."
    if not pm_usernames:
        return None, "PM을 1명 이상 지정해야 합니다."

    client, err = _client()
    if err or not client:
        return None, err or "Supabase 연결 불가"

    try:
        code = _next_project_code()
        row = {
            "code": code,
            "name": name.strip(),
            "type_id": int(type_id) if type_id else None,
            "status": status if status in PROJECT_STATUSES else "진행예정",
            "visibility": visibility if visibility in VISIBILITY_OPTIONS else "public",
            "description": (description or "").strip() or None,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
            "created_by": created_by,
        }
        r = client.table("app_projects").insert(row).execute()
        new_id = int(r.data[0]["id"]) if r.data else None
        if not new_id:
            return None, "insert 후 id를 가져오지 못했습니다."

        # PM/팀원 등록
        seen: set[str] = set()
        for uname in (pm_usernames or []):
            u = (uname or "").strip()
            if not u or u in seen:
                continue
            seen.add(u)
            try:
                client.table("app_project_members").insert({
                    "project_id": new_id,
                    "employee_username": u,
                    "role": "pm",
                    "assigned_by": created_by,
                }).execute()
            except Exception:
                pass
        for uname in (member_usernames or []):
            u = (uname or "").strip()
            if not u or u in seen:
                continue
            seen.add(u)
            try:
                client.table("app_project_members").insert({
                    "project_id": new_id,
                    "employee_username": u,
                    "role": "member",
                    "assigned_by": created_by,
                }).execute()
            except Exception:
                pass

        clear_project_caches()
        return new_id, None
    except Exception as e:
        return None, f"프로젝트 생성 실패: {e}"


def update_project(
    project_id: int,
    patch: dict,
) -> tuple[bool, str | None]:
    """프로젝트 수정. patch 에는 name/type_id/status/visibility/description/start_date/end_date 만 허용."""
    if not project_id:
        return False, "project_id가 비어있습니다."
    client, err = _client()
    if err or not client:
        return False, err or "Supabase 연결 불가"

    allowed = {"name", "type_id", "status", "visibility", "description", "start_date", "end_date"}
    safe_patch: dict[str, Any] = {}
    for k, v in (patch or {}).items():
        if k not in allowed:
            continue
        if k == "status" and v not in PROJECT_STATUSES:
            continue
        if k == "visibility" and v not in VISIBILITY_OPTIONS:
            continue
        if k in ("start_date", "end_date") and isinstance(v, date):
            safe_patch[k] = v.isoformat()
        else:
            safe_patch[k] = v
    if not safe_patch:
        return False, "변경할 필드가 없습니다."
    safe_patch["updated_at"] = datetime.now().isoformat()

    try:
        client.table("app_projects").update(safe_patch).eq("id", int(project_id)).execute()
        clear_project_caches()
        return True, None
    except Exception as e:
        return False, f"프로젝트 수정 실패: {e}"


def delete_project(project_id: int) -> tuple[bool, str | None]:
    """프로젝트 삭제 (CASCADE로 members 자동 삭제, tasks/posts 는 project_id=NULL 로 보존)."""
    if not project_id:
        return False, "project_id가 비어있습니다."
    client, err = _client()
    if err or not client:
        return False, err or "Supabase 연결 불가"
    try:
        client.table("app_projects").delete().eq("id", int(project_id)).execute()
        clear_project_caches()
        return True, None
    except Exception as e:
        return False, f"프로젝트 삭제 실패: {e}"


def archive_project(project_id: int) -> tuple[bool, str | None]:
    """프로젝트 보관(archived_at 설정). 삭제 권한 없는 경우 대안으로 사용."""
    if not project_id:
        return False, "project_id가 비어있습니다."
    client, err = _client()
    if err or not client:
        return False, err or "Supabase 연결 불가"
    try:
        client.table("app_projects").update({
            "archived_at": datetime.now().isoformat()
        }).eq("id", int(project_id)).execute()
        clear_project_caches()
        return True, None
    except Exception as e:
        return False, f"프로젝트 보관 실패: {e}"


# ─────────────────────────────────────────────────────────────────────
# CRUD: 팀 멤버
# ─────────────────────────────────────────────────────────────────────

def set_members(
    project_id: int,
    members: list[dict],
    assigned_by: str,
) -> tuple[bool, str | None]:
    """프로젝트 멤버 전체 교체. members = [{username, role}, ...].
    최소 PM 1명 이상 검증.
    """
    if not project_id:
        return False, "project_id가 비어있습니다."
    pm_count = sum(1 for m in (members or []) if (m.get("role") or "") == "pm")
    if pm_count < 1:
        return False, "PM을 1명 이상 지정해야 합니다."

    client, err = _client()
    if err or not client:
        return False, err or "Supabase 연결 불가"
    try:
        client.table("app_project_members").delete().eq("project_id", int(project_id)).execute()
        rows = []
        for m in members:
            u = (m.get("username") or "").strip()
            r = (m.get("role") or "member").strip()
            if not u or r not in MEMBER_ROLES:
                continue
            rows.append({
                "project_id": int(project_id),
                "employee_username": u,
                "role": r,
                "assigned_by": assigned_by,
            })
        if rows:
            client.table("app_project_members").insert(rows).execute()
        clear_project_caches()
        return True, None
    except Exception as e:
        return False, f"멤버 변경 실패: {e}"


def add_member(
    project_id: int,
    username: str,
    role: str = "member",
    assigned_by: str | None = None,
) -> tuple[bool, str | None]:
    """단일 멤버 추가 (이미 있으면 role 업데이트)."""
    if not project_id or not (username or "").strip():
        return False, "project_id 또는 username 누락"
    if role not in MEMBER_ROLES:
        role = "member"
    client, err = _client()
    if err or not client:
        return False, err or "Supabase 연결 불가"
    try:
        existing = client.table("app_project_members").select("id").eq(
            "project_id", int(project_id)
        ).eq("employee_username", username.strip()).execute()
        if existing.data:
            client.table("app_project_members").update({
                "role": role,
                "assigned_by": assigned_by,
            }).eq("id", int(existing.data[0]["id"])).execute()
        else:
            client.table("app_project_members").insert({
                "project_id": int(project_id),
                "employee_username": username.strip(),
                "role": role,
                "assigned_by": assigned_by,
            }).execute()
        clear_project_caches()
        return True, None
    except Exception as e:
        return False, f"멤버 추가 실패: {e}"


def remove_member(project_id: int, username: str) -> tuple[bool, str | None]:
    """단일 멤버 제거. PM 1명 이상은 유지되도록 검증."""
    if not project_id or not (username or "").strip():
        return False, "project_id 또는 username 누락"
    members = get_project_members(int(project_id))
    target = next(
        (m for m in members if (m.get("employee_username") or "") == username.strip()),
        None,
    )
    if not target:
        return False, "해당 멤버를 찾을 수 없습니다."
    if (target.get("role") or "") == "pm":
        pm_count = sum(1 for m in members if (m.get("role") or "") == "pm")
        if pm_count <= 1:
            return False, "최소 1명의 PM이 필요합니다. 다른 PM을 먼저 지정하세요."
    client, err = _client()
    if err or not client:
        return False, err or "Supabase 연결 불가"
    try:
        client.table("app_project_members").delete().eq("id", int(target["id"])).execute()
        clear_project_caches()
        return True, None
    except Exception as e:
        return False, f"멤버 제거 실패: {e}"


def change_member_role(
    project_id: int,
    username: str,
    new_role: str,
) -> tuple[bool, str | None]:
    """멤버 역할 변경 (PM ↔ member). PM 1명 이상은 유지되도록 검증."""
    if new_role not in MEMBER_ROLES:
        return False, "유효하지 않은 역할입니다."
    members = get_project_members(int(project_id))
    target = next(
        (m for m in members if (m.get("employee_username") or "") == username.strip()),
        None,
    )
    if not target:
        return False, "해당 멤버를 찾을 수 없습니다."
    if (target.get("role") or "") == "pm" and new_role == "member":
        pm_count = sum(1 for m in members if (m.get("role") or "") == "pm")
        if pm_count <= 1:
            return False, "최소 1명의 PM이 필요합니다. 다른 멤버를 먼저 PM으로 지정하세요."
    client, err = _client()
    if err or not client:
        return False, err or "Supabase 연결 불가"
    try:
        client.table("app_project_members").update({"role": new_role}).eq(
            "id", int(target["id"])
        ).execute()
        clear_project_caches()
        return True, None
    except Exception as e:
        return False, f"역할 변경 실패: {e}"


# ─────────────────────────────────────────────────────────────────────
# CRUD: 프로젝트 유형(카테고리)
# ─────────────────────────────────────────────────────────────────────

def upsert_project_type(
    name: str,
    display_order: int = 0,
    type_id: int | None = None,
) -> tuple[int | None, str | None]:
    """유형 추가/수정. type_id가 있으면 update, 없으면 insert."""
    if not (name or "").strip():
        return None, "유형명을 입력하세요."
    client, err = _client()
    if err or not client:
        return None, err or "Supabase 연결 불가"
    try:
        if type_id:
            client.table("app_project_types").update({
                "name": name.strip(),
                "display_order": int(display_order),
                "updated_at": datetime.now().isoformat(),
            }).eq("id", int(type_id)).execute()
            clear_project_caches()
            return int(type_id), None
        else:
            r = client.table("app_project_types").insert({
                "name": name.strip(),
                "display_order": int(display_order),
            }).execute()
            new_id = int(r.data[0]["id"]) if r.data else None
            clear_project_caches()
            return new_id, None
    except Exception as e:
        return None, f"유형 저장 실패: {e}"


def deactivate_project_type(type_id: int) -> tuple[bool, str | None]:
    """유형 비활성화 (soft delete). 기존 프로젝트의 type_id 는 그대로 유지."""
    if not type_id:
        return False, "type_id가 비어있습니다."
    client, err = _client()
    if err or not client:
        return False, err or "Supabase 연결 불가"
    try:
        client.table("app_project_types").update({
            "is_active": False,
            "updated_at": datetime.now().isoformat(),
        }).eq("id", int(type_id)).execute()
        clear_project_caches()
        return True, None
    except Exception as e:
        return False, f"유형 비활성화 실패: {e}"


def reactivate_project_type(type_id: int) -> tuple[bool, str | None]:
    """유형 재활성화."""
    if not type_id:
        return False, "type_id가 비어있습니다."
    client, err = _client()
    if err or not client:
        return False, err or "Supabase 연결 불가"
    try:
        client.table("app_project_types").update({
            "is_active": True,
            "updated_at": datetime.now().isoformat(),
        }).eq("id", int(type_id)).execute()
        clear_project_caches()
        return True, None
    except Exception as e:
        return False, f"유형 재활성화 실패: {e}"


def delete_project_type(type_id: int) -> tuple[bool, str | None]:
    """유형 완전 삭제. 사용 중인 프로젝트가 있으면 거부."""
    if not type_id:
        return False, "type_id가 비어있습니다."
    client, err = _client()
    if err or not client:
        return False, err or "Supabase 연결 불가"
    try:
        in_use = client.table("app_projects").select("id").eq(
            "type_id", int(type_id)
        ).limit(1).execute()
        if in_use.data:
            return False, "이 유형을 사용 중인 프로젝트가 있어 삭제할 수 없습니다. '비활성화'를 사용하세요."
        client.table("app_project_types").delete().eq("id", int(type_id)).execute()
        clear_project_caches()
        return True, None
    except Exception as e:
        return False, f"유형 삭제 실패: {e}"


# ─────────────────────────────────────────────────────────────────────
# 보조: 프로젝트 진행률 (업무 기반)
# ─────────────────────────────────────────────────────────────────────

@st.cache_data(ttl=60, show_spinner=False)
def project_task_progress(project_id: int) -> dict:
    """프로젝트의 업무 진행률.
    반환: {total, done, in_progress, percent}
    """
    client, err = _client()
    if err or not client:
        return {"total": 0, "done": 0, "in_progress": 0, "percent": 0.0}
    try:
        r = client.table("app_tasks").select("status").eq("project_id", int(project_id)).execute()
        rows = r.data or []
        total = len(rows)
        done = sum(1 for t in rows if (t.get("status") or "") == "done")
        in_progress = sum(1 for t in rows if (t.get("status") or "") == "in_progress")
        percent = round((done / total) * 100, 1) if total > 0 else 0.0
        return {"total": total, "done": done, "in_progress": in_progress, "percent": percent}
    except Exception:
        return {"total": 0, "done": 0, "in_progress": 0, "percent": 0.0}


def clear_task_progress_cache():
    project_task_progress.clear()
