"""
복원 리허설 스크립트 — 백업 DB를 임시 경로에 복원해서 무결성 확인
사용법: python tools/restore_database.py <backup_dir> <db_name>
예시:   python tools/restore_database.py C:/backups/momo/20260515_184000 store_1.db
"""

import gc
import sqlite3
import shutil
import sys
from pathlib import Path


def restore_rehearsal(backup_dir: Path, db_name: str):
    src = backup_dir / db_name
    if not src.exists():
        print(f"[ERROR] 백업 파일 없음: {src}")
        sys.exit(1)

    tmp_path = backup_dir / f"_restore_test_{db_name}"
    shutil.copy2(str(src), str(tmp_path))

    conn = None
    try:
        conn = sqlite3.connect(str(tmp_path))
        check = conn.execute("PRAGMA integrity_check").fetchone()
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        table_counts = {t[0]: conn.execute(f"SELECT COUNT(*) FROM [{t[0]}]").fetchone()[0] for t in tables}
        conn.close()
        conn = None

        print(f"[{db_name}] 복원 리허설 결과")
        print(f"  무결성: {check[0]}")
        print(f"  테이블 수: {len(tables)}")
        for name, count in table_counts.items():
            print(f"    - {name}: {count}행")

        if check[0] == "ok":
            print(f"\n[OK] 복원 리허설 통과")
        else:
            print(f"\n[FAIL] 무결성 검사 실패")
    finally:
        if conn:
            conn.close()
        gc.collect()
        try:
            tmp_path.unlink()
        except PermissionError:
            pass  # Windows 잠금 해제 전 삭제 실패 무시 (temp 파일이므로 무해)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("사용법: python tools/restore_database.py <backup_dir> <db_name>")
        sys.exit(1)

    backup_dir = Path(sys.argv[1])
    db_name = sys.argv[2]
    restore_rehearsal(backup_dir, db_name)
