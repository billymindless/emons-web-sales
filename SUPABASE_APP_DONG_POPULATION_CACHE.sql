-- 행정동 인구·세대 데이터 영구 캐시 테이블 (상권 퍼포먼스 맵 속도 개선)
-- 목적: 행정안전부 API(data.go.kr) 응답을 행정동코드+통계년월 단위로 영구 저장해,
--       Streamlit 인메모리 캐시(st.cache_data)가 서버 재배포로 소실되어도
--       재조회 없이 즉시 재사용할 수 있게 한다.
--
-- 실행: Supabase 대시보드 → SQL Editor 에서 이 파일 내용을 실행.
--
-- ※ 테이블이 아직 없는 환경(최초 설치): 이 파일만 실행하면 됩니다.
-- ※ 이미 구버전 테이블(age_30_49_population 만 있는 경우)이 있으면
--    SUPABASE_APP_DONG_POPULATION_CACHE_AGE_BUCKETS.sql 을 추가 실행하세요.

CREATE TABLE IF NOT EXISTS app_dong_population_cache (
    admin_dong_code        TEXT NOT NULL,
    yyyymm                 TEXT NOT NULL,
    total_population       INTEGER DEFAULT 0,
    total_households       INTEGER DEFAULT 0,
    age_10_population      INTEGER DEFAULT 0,
    age_20_population      INTEGER DEFAULT 0,
    age_30_population      INTEGER DEFAULT 0,
    age_40_population      INTEGER DEFAULT 0,
    age_50_population      INTEGER DEFAULT 0,
    age_60_population      INTEGER DEFAULT 0,
    age_70plus_population  INTEGER DEFAULT 0,
    age_30_49_population   INTEGER DEFAULT 0,  -- 하위호환(구버전), 신규 코드는 미사용
    fetched_at             TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (admin_dong_code, yyyymm)
);

CREATE INDEX IF NOT EXISTS idx_app_dong_population_cache_yyyymm
    ON app_dong_population_cache(yyyymm);

COMMENT ON TABLE app_dong_population_cache IS
  '행정동별 인구·세대·연령대별 인구 API 응답 영구 캐시. 동 단위 상권 퍼포먼스 맵 렌더링 속도 개선용.';
COMMENT ON COLUMN app_dong_population_cache.admin_dong_code IS '카카오 coord2regioncode 행정동코드(10자리). 행안부 API admmCd 와 동일값.';
COMMENT ON COLUMN app_dong_population_cache.yyyymm IS '통계년월 (예: 202606). 분석 기간의 종료월 기준.';
COMMENT ON COLUMN app_dong_population_cache.age_70plus_population IS
  '만 70세 이상 남녀 합산 인구 (70+80+90+100 버킷 합).';

-- RLS (다른 app_* 테이블과 동일 정책)
ALTER TABLE app_dong_population_cache ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_dong_population_cache" ON app_dong_population_cache;
CREATE POLICY "Allow all app_dong_population_cache" ON app_dong_population_cache
    FOR ALL USING (true) WITH CHECK (true);
