# -*- coding: utf-8 -*-
"""
app_customers 지역·건물명 컬럼 일괄 백필 스크립트
====================================================
`sigungu`, `bname`, `road_name`, `building_name` 중 하나라도 비어 있는 고객의
`address` 를 카카오 로컬 API로 다시 지오코딩하여 4개 컬럼을 채웁니다.

사전 작업 (Supabase SQL Editor에서 1회 실행):
    -- SUPABASE_APP_CUSTOMERS_REGION.sql 파일 실행
    ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS sigungu       TEXT;
    ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS bname         TEXT;
    ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS road_name     TEXT;
    ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS building_name TEXT;

사용법 (Windows PowerShell):
    python scripts/backfill_customer_region.py `
        --supabase-url "https://xxxx.supabase.co" `
        --supabase-key "anon-or-service-role-key" `
        --kakao-key "YOUR_KAKAO_REST_KEY" `
        --dry-run

환경변수로도 설정 가능:
    SUPABASE_URL, SUPABASE_KEY(or SUPABASE_ANON_KEY), KAKAO_REST_KEY
"""

import argparse
import os
import sys
import time
import requests

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


REGION_COLS = ("sigungu", "bname", "road_name", "building_name")


def log(msg: str) -> None:
    try:
        print(msg, flush=True)
    except Exception:
        print(msg.encode("ascii", errors="replace").decode("ascii"), flush=True)


# ─── 카카오 지오코딩 (확장: 지역+건물명 반환) ────────────────────────────────

def geocode_kakao_extended(address: str, kakao_key: str) -> dict | None:
    """app.py 의 geocode_address_kakao_extended 와 동일 스펙."""
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
        road = d.get("road_address") or {}
        addr = d.get("address") or {}
        return {
            "latitude": float(y),
            "longitude": float(x),
            "sigungu": (road.get("region_2depth_name") or addr.get("region_2depth_name") or "").strip() or None,
            "bname": (addr.get("bname") or "").strip() or None,
            "road_name": (road.get("road_name") or "").strip() or None,
            "building_name": (road.get("building_name") or "").strip() or None,
        }
    except Exception as e:
        log(f"  [지오코딩 오류] {address!r}: {e}")
        return None


# ─── Supabase 고객 조회 (페이지네이션) ────────────────────────────────────────

def fetch_customers_missing_region(client, store_filter: str) -> list[dict]:
    """
    주소가 있고 지역 4개 컬럼 중 하나 이상이 비어 있는 고객을 페이지네이션으로 조회.
    """
    cols = "id, store_name, name, address, sigungu, bname, road_name, building_name"
    page_size = 1000
    offset = 0
    all_rows: list[dict] = []

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

    result: list[dict] = []
    for r in all_rows:
        addr = (r.get("address") or "").strip()
        if not addr:
            continue
        if all(r.get(k) for k in REGION_COLS):
            continue
        result.append(r)
    return result


# ─── 메인 ─────────────────────────────────────────────────────────────────────

def run(args) -> None:
    supabase_url = args.supabase_url or os.environ.get("SUPABASE_URL", "")
    supabase_key = (
        args.supabase_key
        or os.environ.get("SUPABASE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
        or ""
    )
    kakao_key = args.kakao_key or os.environ.get("KAKAO_REST_KEY", "")
    dry_run = args.dry_run
    delay = max(0.0, args.delay)
    limit = max(0, args.limit)
    store_filter = (args.store or "").strip()
    overwrite = args.overwrite

    if not supabase_url or not supabase_key:
        log("ERROR: --supabase-url 과 --supabase-key 를 입력하세요.")
        sys.exit(1)
    if not kakao_key:
        log("ERROR: --kakao-key 를 입력하거나 KAKAO_REST_KEY 환경변수를 설정하세요.")
        sys.exit(1)

    log("=" * 60)
    log("app_customers 지역·건물명 컬럼 백필")
    log("=" * 60)
    log(f"  Supabase URL : {supabase_url}")
    log(f"  매장 필터    : {store_filter or '전체'}")
    log(f"  API 딜레이   : {delay}초")
    log(f"  처리 한도    : {'전체' if limit == 0 else str(limit) + '건'}")
    log(f"  덮어쓰기     : {'ON (기존 값도 재갱신)' if overwrite else 'OFF (빈 필드만 채움)'}")
    log(f"  DRY RUN      : {'ON (실제 저장 안 함)' if dry_run else 'OFF'}")
    log("=" * 60)

    try:
        client = create_client(supabase_url, supabase_key)
    except Exception as e:
        log(f"ERROR: Supabase 연결 실패: {repr(e)}")
        sys.exit(1)

    # ── Step 1: 지역 컬럼 존재 여부 확인 ─────────────────────────────────────
    log("\n[0/3] 지역 컬럼 존재 여부 확인...")
    try:
        client.table("app_customers").select("id, sigungu, bname, road_name, building_name").limit(1).execute()
        log("  -> 지역 컬럼(sigungu/bname/road_name/building_name) 확인 완료")
    except Exception as e:
        log(f"ERROR: 지역 컬럼이 없습니다. 먼저 SUPABASE_APP_CUSTOMERS_REGION.sql 을 실행하세요.")
        log(f"       상세: {repr(e)}")
        sys.exit(1)

    # ── Step 2: 대상 고객 조회 ──────────────────────────────────────────────
    log("\n[1/3] 지역 정보가 비어 있는 고객 조회 중...")
    try:
        if overwrite:
            # 전체 고객(주소 있는) 조회
            all_rows: list[dict] = []
            page_size = 1000
            offset = 0
            while True:
                q = client.table("app_customers").select(
                    "id, store_name, name, address, sigungu, bname, road_name, building_name"
                )
                if store_filter:
                    q = q.eq("store_name", store_filter)
                batch = q.range(offset, offset + page_size - 1).execute()
                rows = batch.data or []
                all_rows.extend(rows)
                if len(rows) < page_size:
                    break
                offset += page_size
            targets = [r for r in all_rows if (r.get("address") or "").strip()]
        else:
            targets = fetch_customers_missing_region(client, store_filter)
    except Exception as e:
        log(f"ERROR: 고객 조회 실패: {repr(e)}")
        sys.exit(1)

    total = len(targets)
    log(f"  -> {total}건 발견")
    if total == 0:
        log("\n처리할 고객이 없습니다.")
        return
    if limit > 0:
        targets = targets[:limit]
        log(f"  -> 처리 한도 적용: {len(targets)}건만 처리")

    # ── Step 3: 지오코딩 + 업데이트 ──────────────────────────────────────────
    pfx = "(DRY RUN)" if dry_run else ""
    log(f"\n[2/3] 카카오 지오코딩 & Supabase 업데이트 {pfx} (딜레이 {delay}초)...")

    # 중복 주소 캐싱
    addr_cache: dict[str, dict | None] = {}
    updated, skipped, errors, geo_reused = 0, 0, 0, 0

    for i, row in enumerate(targets, 1):
        cid = row["id"]
        name = (row.get("name") or "-")[:18]
        addr = (row.get("address") or "").strip()

        if addr in addr_cache:
            geo = addr_cache[addr]
            geo_reused += 1
        else:
            geo = geocode_kakao_extended(addr, kakao_key)
            addr_cache[addr] = geo
            if i < len(targets) and addr not in addr_cache:
                time.sleep(delay)

        if not geo:
            log(f"  [{i:4d}/{len(targets)}] SKIP  [id={cid}] {name}  주소={addr[:30]}")
            skipped += 1
            continue

        # overwrite=False: 빈 필드만 채움
        payload: dict = {}
        for k in REGION_COLS:
            new_v = geo.get(k)
            if not new_v:
                continue
            if overwrite or not row.get(k):
                payload[k] = new_v
        if not payload:
            log(f"  [{i:4d}/{len(targets)}] KEEP  [id={cid}] {name}  (이미 모든 필드 채워짐)")
            continue

        if dry_run:
            log(f"  [{i:4d}/{len(targets)}] DRY   [id={cid}] {name:<18} -> {payload}")
            updated += 1
            continue

        try:
            client.table("app_customers").update(payload).eq("id", cid).execute()
            log(f"  [{i:4d}/{len(targets)}] OK    [id={cid}] {name:<18} -> {payload}")
            updated += 1
        except Exception as e:
            log(f"  [{i:4d}/{len(targets)}] ERROR [id={cid}] {name}: {repr(e)}")
            errors += 1

        # 마지막이 아니고 캐시 신규 조회였다면 딜레이는 위에서 처리했음

    # ── 결과 요약 ─────────────────────────────────────────────────────────────
    log("\n" + "=" * 60)
    log("완료")
    log("=" * 60)
    log(f"  대상             : {len(targets)}건")
    log(f"  {'예정(DRY)' if dry_run else '업데이트'} : {updated}건")
    log(f"  스킵             : {skipped}건 (지오코딩 실패)")
    log(f"  주소 캐시 재사용 : {geo_reused}건")
    if not dry_run:
        log(f"  오류             : {errors}건")
    if dry_run:
        log("")
        log("  ※ --dry-run 제거 후 재실행하면 실제 저장됩니다.")
    log("=" * 60)


# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="app_customers 지역·건물명 컬럼 백필")
    p.add_argument("--supabase-url", default="")
    p.add_argument("--supabase-key", default="")
    p.add_argument("--kakao-key",    default="")
    p.add_argument("--store",        default="", help="특정 매장명 (생략 시 전체)")
    p.add_argument("--dry-run",      action="store_true")
    p.add_argument("--overwrite",    action="store_true", help="기존 값도 재갱신")
    p.add_argument("--delay",        type=float, default=0.15, help="API 호출 간격(초)")
    p.add_argument("--limit",        type=int,   default=0,    help="처리 최대 건수 (0=전체)")
    run(p.parse_args())
