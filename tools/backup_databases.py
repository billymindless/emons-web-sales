"""
Phase 0 백업 스크립트 — SQLite DB 전체 스냅샷
사용법: python tools/backup_databases.py
백업 위치: C:/backups/momo/<YYYYMMDD_HHMMSS>/
"""

import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

_SCRIPT_DIR = Path(__file__).parent.parent

# 로컬 databases/ 우선, 없으면 OneDrive 경로 폴백
_LOCAL_DB_DIR = _SCRIPT_DIR / "databases"
_ONEDRIVE_DB_DIR = Path(os.environ.get("USERPROFILE", "C:/Users/billy")) / "OneDrive" / "바탕 화면" / "emons-web-sales" / "databases"

if _LOCAL_DB_DIR.exists() and any(_LOCAL_DB_DIR.glob("*.db")):
    DB_SOURCE_DIR = _LOCAL_DB_DIR
elif _ONEDRIVE_DB_DIR.exists():
    DB_SOURCE_DIR = _ONEDRIVE_DB_DIR
else:
    DB_SOURCE_DIR = _LOCAL_DB_DIR  # 없어도 일단 로컬 사용

BACKUP_BASE_DIR = Path("C:/backups/momo")

DB_FILES = [
    "master_system.db",
    "store_1.db",
    "store_2.db",
    "store_4.db",
]


def verify_integrity(db_path: Path) -> bool:
    try:
        conn = sqlite3.connect(str(db_path))
        result = conn.execute("PRAGMA integrity_check").fetchone()
        conn.close()
        return result[0] == "ok"
    except Exception as e:
        print(f"  [ERROR] {db_path.name} 무결성 검사 실패: {e}")
        return False


def backup():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = BACKUP_BASE_DIR / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== momo SQLite 백업 시작: {timestamp} ===")
    print(f"백업 위치: {backup_dir}\n")

    success_count = 0
    for db_name in DB_FILES:
        src = DB_SOURCE_DIR / db_name
        if not src.exists():
            print(f"  [SKIP] {db_name} - 파일 없음")
            continue

        print(f"  [{db_name}]")
        print(f"    크기: {src.stat().st_size:,} bytes")

        if not verify_integrity(src):
            print(f"    [WARNING] 무결성 검사 실패 — 그래도 복사 진행")

        dst = backup_dir / db_name
        shutil.copy2(str(src), str(dst))

        # 복사 후 재검증
        if verify_integrity(dst):
            print(f"    [OK] 복사 완료 및 검증 통과 → {dst}")
            success_count += 1
        else:
            print(f"    [ERROR] 복사 후 검증 실패")

    # receipts 폴더 백업
    receipts_src = DB_SOURCE_DIR / "receipts"
    if receipts_src.exists():
        receipts_dst = backup_dir / "receipts"
        shutil.copytree(str(receipts_src), str(receipts_dst))
        print(f"\n  [receipts] 폴더 복사 완료 → {receipts_dst}")

    print(f"\n=== 완료: {success_count}/{len(DB_FILES)} DB 백업 성공 ===")
    print(f"백업 경로: {backup_dir}")

    # 복원 리허설 안내
    print("\n--- 복원 리허설 방법 ---")
    print(f"  python tools/restore_database.py {backup_dir} store_1.db")

    return backup_dir if success_count > 0 else None


if __name__ == "__main__":
    result = backup()
    sys.exit(0 if result else 1)
