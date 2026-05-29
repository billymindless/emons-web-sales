#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
근태 데이터 정리 사전 검증 (삭제는 하지 않음, 조회 전용).

목적: 6월부터 근무시간을 새로 입력하기 위해, 6월 이전(< 2026-06-01)에 쌓인
      날짜 기반 근태 데이터가 테이블별로 몇 건인지 집계한다.

대상(날짜 기반, 삭제 후보):
  - app_shift_schedules    (shift_date)    : 근무 일정 계획  ← 6~12월 입력 대상
  - app_attendance_logs    (log_date)      : 실제 근태 기록
  - app_overtime_requests  (request_date)  : 추가근무 신청
  - app_work_adjustments   (target_date)   : 신청·승인 통합
  - app_monthly_work_targets (ym)          : 월 필수 근무시간(YYYY-MM)

제외(마스터/설정 — 삭제하면 안 됨):
  - app_staffing_rules, app_store_hours, app_leave_grants,
    app_yearly_work_targets, app_store_events
"""
from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
SECRETS = ROOT / ".streamlit" / "secrets.toml"

with open(SECRETS, "rb") as f:
    data = tomllib.load(f)
sup = data["supabase"]
from supabase import create_client

client = create_client(
    sup["url"].strip(),
    (sup.get("service_role_key") or sup.get("key") or sup.get("anon_key", "")).strip(),
)

CUTOFF = "2026-06-01"  # 이 날짜 미만이 삭제 후보

# (테이블, 날짜컬럼, 컬럼유형)
TABLES = [
    ("app_shift_schedules", "shift_date", "date"),
    ("app_attendance_logs", "log_date", "date"),
    ("app_overtime_requests", "request_date", "date"),
    ("app_work_adjustments", "target_date", "date"),
    ("app_monthly_work_targets", "ym", "ym"),
]


def count_rows(table: str, date_col: str, col_type: str):
    """(전체, 6월이전, 6월이후) 건수 반환."""
    try:
        total = client.table(table).select("id", count="exact").execute().count or 0
    except Exception as e:
        return None, None, None, f"조회 실패: {e}"

    try:
        if col_type == "date":
            before = (
                client.table(table)
                .select("id", count="exact")
                .lt(date_col, CUTOFF)
                .execute()
                .count
                or 0
            )
        else:  # ym 'YYYY-MM' 문자열 비교 → '2026-06' 미만
            before = (
                client.table(table)
                .select("id", count="exact")
                .lt(date_col, "2026-06")
                .execute()
                .count
                or 0
            )
    except Exception as e:
        return total, None, None, f"기간 조회 실패: {e}"

    after = total - before
    return total, before, after, None


print("=" * 78)
print(f"근태 데이터 사전 검증 (기준일: {CUTOFF} 미만 = 삭제 후보)")
print("=" * 78)
print(f"{'테이블':<28}{'날짜컬럼':<16}{'전체':>8}{'6월이전':>10}{'6월이후':>10}")
print("-" * 78)

grand_before = 0
for table, date_col, col_type in TABLES:
    total, before, after, err = count_rows(table, date_col, col_type)
    if err:
        print(f"{table:<28}{date_col:<16}  {err}")
        continue
    grand_before += before or 0
    print(f"{table:<28}{date_col:<16}{total:>8}{before:>10}{after:>10}")

print("-" * 78)
print(f"6월 이전 삭제 후보 합계: {grand_before} 건")
print("=" * 78)

# 매장별 shift_schedules 분포(6월 이전)도 같이 본다
print("\n[참고] app_shift_schedules 6월 이전 매장별 분포")
try:
    rows = (
        client.table("app_shift_schedules")
        .select("db_filename, shift_date")
        .lt("shift_date", CUTOFF)
        .execute()
        .data
        or []
    )
    by_db: dict[str, int] = {}
    min_d, max_d = None, None
    for r in rows:
        dbf = r.get("db_filename") or "(없음)"
        by_db[dbf] = by_db.get(dbf, 0) + 1
        d = str(r.get("shift_date") or "")[:10]
        if d:
            min_d = d if (min_d is None or d < min_d) else min_d
            max_d = d if (max_d is None or d > max_d) else max_d
    for dbf, c in sorted(by_db.items()):
        print(f"  {dbf:<24}{c:>6} 건")
    print(f"  기간 범위: {min_d} ~ {max_d}")
except Exception as e:
    print(f"  분포 조회 실패: {e}")
