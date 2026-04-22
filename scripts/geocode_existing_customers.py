# -*- coding: utf-8 -*-
"""
기존 고객 주소 일괄 지오코딩 스크립트
========================================
app_customers 테이블에서 주소(address)는 있지만 위도/경도(latitude, longitude)가
없는 고객을 찾아 카카오 API로 지오코딩 후 Supabase에 업데이트합니다.

사용법:
    python scripts/geocode_existing_customers.py \
        --supabase-url https://xxxx.supabase.co \
        --supabase-key anon-or-service-role-key \
        --kakao-key YOUR_KAKAO_REST_KEY \
        [--store 매장명]        # 특정 매장만 처리 (생략 시 전체)
        [--dry-run]             # 실제 업데이트 없이 결과만 미리 보기
        [--delay 0.15]          # API 호출 간격 (초, 기본 0.15초)
        [--limit 0]             # 처리할 최대 건수 (0=전체)

환경변수로도 설정 가능:
    SUPABASE_URL, SUPABASE_KEY (또는 SUPABASE_ANON_KEY), KAKAO_REST_KEY
"""

import argparse
import os
import sys
import time
import requests

# supabase 패키지 import
try:
    from supabase import create_client
except ImportError:
    print("ERROR: supabase 패키지가 없습니다. `pip install supabase` 실행 후 재시도하세요.")
    sys.exit(1)


# ─── 카카오 지오코딩 ───────────────────────────────────────────────────────────

def geocode_kakao(address: str, kakao_key: str) -> dict | None:
    """
    카카오 로컬 API로 주소 → 위도/경도 변환.
    반환: {"latitude", "longitude", "address"} 또는 None.
    """
    if not address or not address.strip():
        return None
    url = "https://dapi.kakao.com/v2/local/search/address.json"
    headers = {"Authorization": f"KakaoAK {kakao_key}"}
    params = {"query": address.strip()}
    try:
        r = requests.get(url, headers=headers, params=params, timeout=5)
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
            "latitude": float(y),
            "longitude": float(x),
            "address": d.get("address_name") or address.strip(),
        }
    except Exception as e:
        print(f"  [지오코딩 오류] {address!r}: {e}")
        return None


# ─── 메인 로직 ────────────────────────────────────────────────────────────────

def run(args):
    # 1. 인자 / 환경변수에서 키 읽기
    supabase_url = args.supabase_url or os.environ.get("SUPABASE_URL", "")
    supabase_key = (
        args.supabase_key
        or os.environ.get("SUPABASE_KEY")
        or os.environ.get("SUPABASE_ANON_KEY")
        or ""
    )
    kakao_key = args.kakao_key or os.environ.get("KAKAO_REST_KEY", "")

    if not supabase_url or not supabase_key:
        print("ERROR: --supabase-url 과 --supabase-key 를 입력하거나 환경변수를 설정해 주세요.")
        sys.exit(1)
    if not kakao_key:
        print("ERROR: --kakao-key 를 입력하거나 KAKAO_REST_KEY 환경변수를 설정해 주세요.")
        sys.exit(1)

    dry_run = args.dry_run
    delay   = max(0.0, args.delay)
    limit   = max(0, args.limit)
    store_filter = (args.store or "").strip()

    print("=" * 60)
    print("기존 고객 주소 일괄 지오코딩")
    print("=" * 60)
    print(f"  Supabase URL : {supabase_url}")
    print(f"  매장 필터    : {store_filter or '전체'}")
    print(f"  API 딜레이   : {delay}초")
    print(f"  처리 한도    : {'전체' if limit == 0 else limit}건")
    print(f"  DRY RUN      : {'ON (실제 업데이트 안 함)' if dry_run else 'OFF'}")
    print("=" * 60)

    # 2. Supabase 연결
    client = create_client(supabase_url, supabase_key)

    # 3. 주소 있고 위도/경도 없는 고객 조회
    print("\n[1/4] 지오코딩이 필요한 고객 조회 중...")
    try:
        q = (
            client.table("app_customers")
            .select("id, store_name, name, address, latitude, longitude")
            .not_.is_("address", "null")      # 주소 있음
            .is_("latitude", "null")           # 위도 없음
        )
        if store_filter:
            q = q.eq("store_name", store_filter)

        # 페이지네이션: 최대 1000건씩 (Supabase 기본 1000 limit)
        all_rows = []
        page_size = 1000
        offset = 0
        while True:
            batch = q.range(offset, offset + page_size - 1).execute()
            rows = batch.data or []
            all_rows.extend(rows)
            if len(rows) < page_size:
                break
            offset += page_size

    except Exception as e:
        print(f"ERROR: Supabase 조회 실패: {e}")
        sys.exit(1)

    # 빈 주소 제거
    all_rows = [r for r in all_rows if (r.get("address") or "").strip()]

    total = len(all_rows)
    print(f"  → {total}건 발견")

    if total == 0:
        print("\n모든 고객의 위도/경도가 이미 입력되어 있습니다.")
        return

    if limit > 0:
        all_rows = all_rows[:limit]
        print(f"  → 처리 한도 적용: {len(all_rows)}건 처리")

    # 4. 주소 중복 제거 (같은 주소는 API 1번만 호출)
    addr_to_geo: dict[str, dict | None] = {}

    print("\n[2/4] 고유 주소 목록 추출 중...")
    unique_addresses = list({(r["address"] or "").strip() for r in all_rows if (r.get("address") or "").strip()})
    print(f"  → 고유 주소 {len(unique_addresses)}개")

    # 5. 지오코딩 실행
    print(f"\n[3/4] 카카오 API 지오코딩 시작 (딜레이 {delay}초)...")
    success_addr, fail_addr = 0, 0
    for i, addr in enumerate(unique_addresses, 1):
        geo = geocode_kakao(addr, kakao_key)
        addr_to_geo[addr] = geo
        status = f"✓ ({geo['latitude']:.5f}, {geo['longitude']:.5f})" if geo else "✗ 실패"
        print(f"  [{i}/{len(unique_addresses)}] {addr[:40]!r:<42} {status}")
        if geo:
            success_addr += 1
        else:
            fail_addr += 1
        if i < len(unique_addresses):
            time.sleep(delay)

    print(f"\n  지오코딩 결과: 성공 {success_addr}개 / 실패 {fail_addr}개")

    # 6. Supabase 업데이트
    print(f"\n[4/4] Supabase 업데이트 {'(DRY RUN - 실제 저장 안 함)' if dry_run else '중'}...")
    updated, skipped, errors = 0, 0, 0

    for row in all_rows:
        addr = (row.get("address") or "").strip()
        geo  = addr_to_geo.get(addr)
        cid  = row["id"]
        name = row.get("name") or "-"

        if not geo:
            print(f"  SKIP [{cid}] {name!r} - 지오코딩 실패 ({addr[:30]!r})")
            skipped += 1
            continue

        if dry_run:
            print(f"  DRY  [{cid}] {name!r} → ({geo['latitude']:.5f}, {geo['longitude']:.5f})")
            updated += 1
            continue

        try:
            client.table("app_customers").update({
                "latitude":  geo["latitude"],
                "longitude": geo["longitude"],
            }).eq("id", cid).execute()
            print(f"  OK   [{cid}] {name!r} → ({geo['latitude']:.5f}, {geo['longitude']:.5f})")
            updated += 1
        except Exception as e:
            print(f"  ERR  [{cid}] {name!r}: {e}")
            errors += 1

    # 7. 결과 요약
    print("\n" + "=" * 60)
    print("완료 요약")
    print("=" * 60)
    print(f"  처리 대상   : {len(all_rows)}건")
    print(f"  {'업데이트(예정)' if dry_run else '업데이트 완료'}: {updated}건")
    print(f"  지오코딩 실패 (스킵): {skipped}건")
    if not dry_run:
        print(f"  Supabase 오류: {errors}건")
    if dry_run:
        print("\n  ※ DRY RUN 모드: 실제로 저장되지 않았습니다.")
        print("     --dry-run 옵션 제거 후 재실행하면 실제 저장됩니다.")
    print("=" * 60)


# ─── CLI 진입점 ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="app_customers 기존 주소 일괄 지오코딩 → Supabase 업데이트"
    )
    parser.add_argument("--supabase-url",  default="", help="Supabase 프로젝트 URL")
    parser.add_argument("--supabase-key",  default="", help="Supabase anon/service_role 키")
    parser.add_argument("--kakao-key",     default="", help="카카오 REST API 키")
    parser.add_argument("--store",         default="", help="특정 매장명만 처리 (생략 시 전체)")
    parser.add_argument("--dry-run",       action="store_true", help="미리 보기 (실제 저장 안 함)")
    parser.add_argument("--delay",         type=float, default=0.15, help="API 호출 간격 초 (기본 0.15)")
    parser.add_argument("--limit",         type=int,   default=0,    help="처리 최대 건수 (0=전체)")
    args = parser.parse_args()
    run(args)
