-- 행정동 인구·세대 데이터 영구 캐시 테이블 (상권 퍼포먼스 맵 속도 개선)
-- 목적: 행정안전부 API(data.go.kr) 응답을 행정동코드+통계년월 단위로 영구 저장해,
--       Streamlit 인메모리 캐시(st.cache_data)가 서버 재배포로 소실되어도
--       재조회 없이 즉시 재사용할 수 있게 한다.
--
-- 실행: Supabase 대시보드 → SQL Editor 에서 이 파일 내용을 실행.

CREATE TABLE IF NOT EXISTS app_dong_population_cache (
    admin_dong_code       TEXT NOT NULL,
    yyyymm                TEXT NOT NULL,
    total_population      INTEGER DEFAULT 0,
    total_households       INTEGER DEFAULT 0,
    age_30_49_population   INTEGER DEFAULT 0,
    fetched_at             TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (admin_dong_code, yyyymm)
);

CREATE INDEX IF NOT EXISTS idx_app_dong_population_cache_yyyymm
    ON app_dong_population_cache(yyyymm);

COMMENT ON TABLE app_dong_population_cache IS
  '행정동별 인구·세대·3040인구 API 응답 영구 캐시. 동 단위 상권 퍼포먼스 맵 렌더링 속도 개선용.';
COMMENT ON COLUMN app_dong_population_cache.admin_dong_code IS '카카오 coord2regioncode 행정동코드(10자리). 행안부 API admmCd 와 동일값.';
COMMENT ON COLUMN app_dong_population_cache.yyyymm IS '통계년월 (예: 202606). 분석 기간의 종료월 기준.';

-- RLS (다른 app_* 테이블과 동일 정책)
ALTER TABLE app_dong_population_cache ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_dong_population_cache" ON app_dong_population_cache;
CREATE POLICY "Allow all app_dong_population_cache" ON app_dong_population_cache
    FOR ALL USING (true) WITH CHECK (true);
