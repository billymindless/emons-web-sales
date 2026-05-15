"""
Supabase 스키마 검증 스크립트
사용법: python tools/supabase/02_verify_schema.py

새 Supabase 프로젝트에 01_init_schema.sql 실행 후 이 스크립트로 확인.
"""

import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).parent.parent.parent

REQUIRED_TABLES = [
    "app_stores",
    "app_users",
    "app_user_stores",
    "app_customers",
    "app_orders",
    "app_payments",
    "app_payment_history",
    "app_edit_requests",
    "app_notices",
    "app_todos",
]

OPTIONAL_TABLES = [
    "app_employees",
    "app_audit_logs",
    "app_orgs",
    "app_subscriptions",
    "app_invoices",
]


def load_secrets():
    secrets_path = ROOT / ".streamlit" / "secrets.toml"
    if not secrets_path.exists():
        print(f"[ERROR] secrets.toml 없음: {secrets_path}")
        sys.exit(1)
    with open(secrets_path, "rb") as f:
        return tomllib.load(f)


def main():
    secrets = load_secrets()
    sb = secrets.get("supabase", {})
    url = sb.get("url", "").strip()
    service_key = sb.get("service_role_key", "").strip()

    if not url or not service_key:
        print("[ERROR] secrets.toml에 [supabase] url, service_role_key 필요")
        sys.exit(1)

    try:
        from supabase import create_client
    except ImportError:
        print("[ERROR] supabase 패키지 없음. pip install supabase 실행 후 재시도")
        sys.exit(1)

    client = create_client(url, service_key)
    print(f"Supabase: {url}")
    print()

    ok_count = 0
    fail_count = 0

    print("=== 필수 테이블 ===")
    for table in REQUIRED_TABLES:
        try:
            res = client.table(table).select("*", count="exact").limit(0).execute()
            count = res.count if res.count is not None else 0
            print(f"  [OK] {table:<30} {count}행")
            ok_count += 1
        except Exception as e:
            msg = str(e)[:80]
            print(f"  [없음] {table:<28} {msg}")
            fail_count += 1

    print()
    print("=== 선택 테이블 (Phase 4~6에서 생성 예정) ===")
    for table in OPTIONAL_TABLES:
        try:
            res = client.table(table).select("*", count="exact").limit(0).execute()
            count = res.count if res.count is not None else 0
            print(f"  [OK] {table:<30} {count}행")
        except Exception:
            print(f"  [미생성] {table}")

    print()
    if fail_count == 0:
        print(f"결과: 필수 테이블 {ok_count}/{len(REQUIRED_TABLES)} 모두 OK")
        print("앱을 실행할 준비가 됐습니다.")
    else:
        print(f"결과: 필수 테이블 {ok_count}/{len(REQUIRED_TABLES)} OK, {fail_count}개 없음")
        print("tools/supabase/01_init_schema.sql 을 Supabase SQL Editor에서 실행하세요.")
        sys.exit(1)


if __name__ == "__main__":
    main()
