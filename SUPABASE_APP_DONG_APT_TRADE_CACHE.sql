-- 행정동 아파트 매매 실거래가 캐시 테이블 (상권 매력도 가중 평가용)
-- 목적: 국토교통부 아파트 매매 실거래가 상세 자료 API(data.go.kr 15126468) 응답을
--       (행정동코드, 기준 종료월, 조회 창) 단위로 영구 저장해, 가중치·행정동 선택 변경에도
--       API 재호출 없이 즉시 재사용할 수 있게 한다.
--
-- 실행: Supabase 대시보드 → SQL Editor 에서 이 파일 내용을 실행.
--
-- 관련 소스: dong_commercial_map.py 의 fetch_apt_price_bulk / _load_apt_cache_from_db /
--          _save_apt_cache_to_db 함수가 이 테이블을 사용합니다.

CREATE TABLE IF NOT EXISTS app_dong_apt_trade_cache (
    admin_dong_code        TEXT NOT NULL,
    yyyymm_end             TEXT NOT NULL,   -- 조회 창의 종료월 (YYYYMM, 예: 202606)
    window_months          INTEGER NOT NULL DEFAULT 3,
    median_price_per_m2    DOUBLE PRECISION DEFAULT 0,  -- 원/㎡ (dealAmount*10000 / excluUseAr)
    deal_count             INTEGER DEFAULT 0,           -- 창 기간 내 매칭된 거래 건수
    match_level            TEXT DEFAULT '',             -- 'umd' | 'sgg_fallback'
    fetched_at             TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (admin_dong_code, yyyymm_end, window_months)
);

CREATE INDEX IF NOT EXISTS idx_app_dong_apt_trade_cache_yyyymm_end
    ON app_dong_apt_trade_cache(yyyymm_end);
CREATE INDEX IF NOT EXISTS idx_app_dong_apt_trade_cache_match_level
    ON app_dong_apt_trade_cache(match_level);

COMMENT ON TABLE app_dong_apt_trade_cache IS
  '행정동별 아파트 매매 실거래 ㎡단가 캐시. 시군구(LAWD_CD=admin_dong_code[:5]) 단위로 API 를 조회한 뒤 법정동(umdNm)↔행정동명 매칭 결과를 행정동 단위로 저장한다.';
COMMENT ON COLUMN app_dong_apt_trade_cache.admin_dong_code IS
  '카카오 coord2regioncode 행정동코드(10자리).';
COMMENT ON COLUMN app_dong_apt_trade_cache.yyyymm_end IS
  '조회 창의 종료월(포함). 실제 조회 창은 (yyyymm_end 포함 직전 window_months 개월).';
COMMENT ON COLUMN app_dong_apt_trade_cache.match_level IS
  '"umd" = 법정동명 매칭 성공, "sgg_fallback" = 시군구 전체 중앙값으로 폴백.';

-- RLS (다른 app_* 캐시 테이블과 동일 정책)
ALTER TABLE app_dong_apt_trade_cache ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_dong_apt_trade_cache" ON app_dong_apt_trade_cache;
CREATE POLICY "Allow all app_dong_apt_trade_cache" ON app_dong_apt_trade_cache
    FOR ALL USING (true) WITH CHECK (true);
