# -*- coding: utf-8 -*-
"""
기존 고객 주소 일괄 지오코딩 스크립트
========================================
app_customers 테이블에서 주소(address)는 있지만 위도/경도(latitude, longitude)가
없는 고객을 찾아 카카오 API로 지오코딩 후 Supabase에 업데이트합니다.

사전 작업 (Supabase SQL Editor에서 1회 실행):
    ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS latitude  DOUBLE PRECISION;
    ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION;

사용법 (Windows PowerShell):
    python scripts/geocode_existing_customers.py `
        --supabase-url "https://xxxx.supabase.co" `
        --supabase-key "anon-or-service-role-key" `
        --kakao-key "YOUR_KAKAO_REST_KEY" `
        --dry-run

환경변수로도 설정 가능:
    SUPABASE_URL, SUPABASE_KEY, KAKAO_REST_KEY
"""

import argparse
import os
import sys
import time
import requests

# Windows 터미널 UTF-8 설정
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from supabase import create_client
except ImportError:
    print("ERROR: supabase 패키지 없음. pip install supabase")
    sys.exit(1)


def log(msg: str):
    try:
        print(msg, flush=True)
    except Exception:
        print(msg.encode("ascii", errors="replace").decode("ascii"), flush=True)


# ─── 카카오 지오코딩 ───────────────────────────────────────────────────────────

def geocode_kakao(address: str, kakao_key: str) -> dict | None:
    if not address or not address.strip():
        return None
    url = "https://dapi.kakao.com/v2/local/search/address.json"
    headers = {"Authorization": f"KakaoAK {kakao_key}"}
    try:
        r = requests.get(url, headers=headers, params={"query": address.strip()}, timeout=5)
        if r.status_code != 200:
            return None
        docs = r.json().get("documents", [])
        if not docs:
            return None
        d = docs[0]
        x, y = d.get("x"), d.get("y")
        if x is None or y is None:
            return None
        return {
            "latitude":  float(y),
            "longitude": float(x),
            "address":   d.get("address_name") or address.strip(),
        }
    except Exception as e:
        log(f"  [지오코딩 오류] {address!r}: {e}")
        return None


# ─── Supabase 고객 조회 (페이지네이션) ────────────────────────────────────────

def fetch_customers(client, store_filter: str, has_lat_col: bool) -> list[dict]:
    """
    모든 고객을 페이지네이션으로 조회.
    has_lat_col=True 이면 latitude/longitude 포함해서 조회.
    Python에서 주소 있음 + 위도 없음 필터링.
    """
    if has_lat_col:
        cols = "id, store_name, name, address, latitude, longitude"
    else:
        cols = "id, store_name, name, address"

    page_size = 1000
    offset    = 0
    all_rows  = []

    while True:
        q = client.table("app_customers").select(cols)
        if store_filter:
            q = q.eq("store_name", store_filter)
        batch = q.range(offset, offset + page_size - 1).execute()
        rows = batch.data or []
        all_rows.extend(rows)
        if len(rows) < page_size:
            break
        offset += page_size

    # Python에서 필터: 주소 있음 + 위도 없음(또는 컬럼 자체가 없는 경우)
    result = []
    for r in all_rows:
        addr = (r.get("address") or "").strip()
        if not addr:
            continue
        if has_lat_col:
            lat = r.get("latitude")
            if lat is not None:   # 이미 좌표 있음 -> 스킵
                continue
        result.append(r)

    return result


# ─── 메인 ─────────────────────────────────────────────────────────────────────

def run(args):
    supabase_url = args.supabase_url or os.environ.get("SUPABASE_URL", "")
    supabase_key = (
        args.supabase_key
        or os.environ.get("SUPABASE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
        or ""
    )
    kakao_key    = args.kakao_key or os.environ.get("KAKAO_REST_KEY", "")
    dry_run      = args.dry_run
    delay        = max(0.0, args.delay)
    limit        = max(0, args.limit)
    store_filter = (args.store or "").strip()

    if not supabase_url or not supabase_key:
        log("ERROR: --supabase-url 과 --supabase-key 를 입력하세요.")
        sys.exit(1)
    if not kakao_key:
        log("ERROR: --kakao-key 를 입력하거나 KAKAO_REST_KEY 환경변수를 설정하세요.")
        sys.exit(1)

    log("=" * 60)
    log("기존 고객 주소 일괄 지오코딩")
    log("=" * 60)
    log(f"  Supabase URL : {supabase_url}")
    log(f"  매장 필터    : {store_filter or '전체'}")
    log(f"  API 딜레이   : {delay}초")
    log(f"  처리 한도    : {'전체' if limit == 0 else str(limit) + '건'}")
    log(f"  DRY RUN      : {'ON (실제 저장 안 함)' if dry_run else 'OFF'}")
    log("=" * 60)

    # Supabase 연결
    try:
        client = create_client(supabase_url, supabase_key)
    except Exception as e:
        log(f"ERROR: Supabase 연결 실패: {repr(e)}")
        sys.exit(1)

    # ── Step 1: latitude 컬럼 존재 여부 확인 ──────────────────────────────────
    log("\n[0/4] latitude 컬럼 존재 여부 확인...")
    has_lat_col = False
    try:
        test = client.table("app_customers").select("id, latitude").limit(1).execute()
        has_lat_col = True
        log("  -> latitude 컬럼 있음")
    except Exception:
        log("  -> latitude 컬럼 없음 (모든 고객 대상으로 지오코딩 후 업데이트 시도)")
        log("     ※ Supabase SQL Editor에서 아래 실행을 권장합니다:")
        log("        ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS latitude  DOUBLE PRECISION;")
        log("        ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION;")

    # ── Step 2: 고객 조회 ─────────────────────────────────────────────────────
    log("\n[1/4] 지오코딩이 필요한 고객 조회 중...")
    try:
        all_rows = fetch_customers(client, store_filter, has_lat_col)
    except Exception as e:
        log(f"ERROR: 고객 조회 실패: {repr(e)}")
        sys.exit(1)

    total = len(all_rows)
    log(f"  -> {total}건 발견")
    if total == 0:
        log("\n처리할 고객이 없습니다 (이미 모두 좌표가 있거나 주소가 없음).")
        return

    if limit > 0:
        all_rows = all_rows[:limit]
        log(f"  -> 처리 한도 적용: {len(all_rows)}건만 처리")

    # ── Step 3: 고유 주소 지오코딩 ───────────────────────────────────────────
    log("\n[2/4] 고유 주소 추출 중...")
    unique_addrs = list({(r.get("address") or "").strip() for r in all_rows})
    log(f"  -> 고유 주소 {len(unique_addrs)}개 (API {len(unique_addrs)}회 예정)")

    log(f"\n[3/4] 카카오 지오코딩 시작 (딜레이 {delay}초)...")
    addr_to_geo: dict[str, dict | None] = {}
    ok_cnt, fail_cnt = 0, 0

    for i, addr in enumerate(unique_addrs, 1):
        geo = geocode_kakao(addr, kakao_key)
        addr_to_geo[addr] = geo
        short = addr[:38] if len(addr) > 38 else addr
        if geo:
            log(f"  [{i:3d}/{len(unique_addrs)}] OK   {short:<38} ({geo['latitude']:.5f}, {geo['longitude']:.5f})")
            ok_cnt += 1
        else:
            log(f"  [{i:3d}/{len(unique_addrs)}] FAIL {short}")
            fail_cnt += 1
        if i < len(unique_addrs):
            time.sleep(delay)

    log(f"\n  지오코딩: 성공 {ok_cnt} / 실패 {fail_cnt}")

    # ── Step 4: Supabase 업데이트 ─────────────────────────────────────────────
    pfx = "(DRY RUN)" if dry_run else ""
    log(f"\n[4/4] Supabase 업데이트 {pfx}...")
    updated, skipped, errors = 0, 0, 0

    for row in all_rows:
        addr = (row.get("address") or "").strip()
        geo  = addr_to_geo.get(addr)
        cid  = row["id"]
        name = (row.get("name") or "-")[:18]

        if not geo:
            log(f"  SKIP  [id={cid}] {name}")
            skipped += 1
            continue

        coord = f"({geo['latitude']:.5f}, {geo['longitude']:.5f})"
        if dry_run:
            log(f"  DRY   [id={cid}] {name:<18} -> {coord}")
            updated += 1
            continue

        try:
            client.table("app_customers").update({
                "latitude":  geo["latitude"],
                "longitude": geo["longitude"],
            }).eq("id", cid).execute()
            log(f"  OK    [id={cid}] {name:<18} -> {coord}")
            updated += 1
        except Exception as e:
            log(f"  ERROR [id={cid}] {name}: {repr(e)}")
            errors += 1

    # ── 결과 요약 ─────────────────────────────────────────────────────────────
    log("\n" + "=" * 60)
    log("완료")
    log("=" * 60)
    log(f"  대상      : {len(all_rows)}건")
    log(f"  {'예정(DRY)' if dry_run else '업데이트'}: {updated}건")
    log(f"  스킵      : {skipped}건 (지오코딩 실패)")
    if not dry_run:
        log(f"  오류      : {errors}건")
    if dry_run:
        log("")
        log("  ※ --dry-run 제거 후 재실행하면 실제 저장됩니다.")
    log("=" * 60)


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="app_customers 주소 일괄 지오코딩")
    p.add_argument("--supabase-url", default="")
    p.add_argument("--supabase-key", default="")
    p.add_argument("--kakao-key",    default="")
    p.add_argument("--store",        default="", help="특정 매장명 (생략 시 전체)")
    p.add_argument("--dry-run",      action="store_true")
    p.add_argument("--delay",        type=float, default=0.15, help="API 호출 간격(초)")
    p.add_argument("--limit",        type=int,   default=0,    help="처리 최대 건수 (0=전체)")
    run(p.parse_args())
