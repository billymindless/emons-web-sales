-- 행정동 인구 캐시에 연령대별(10세 단위) 버킷 컬럼 추가
-- 목적: 상권 퍼포먼스 맵의 '핵심 타겟 연령대' 멀티셀렉트(10대~70대 이상)에서
--       API를 재호출하지 않고 UI 선택만으로 target_density 를 재계산할 수 있도록,
--       행안부 성/연령 API 응답의 10세 단위 버킷을 캐시에 그대로 저장한다.
--
-- 실행 순서: 1) SUPABASE_APP_DONG_POPULATION_CACHE.sql (테이블 미생성 환경) 먼저
--             2) 이 파일(SUPABASE_APP_DONG_POPULATION_CACHE_AGE_BUCKETS.sql)
--
-- 주의: 기존 age_30_49_population 컬럼은 하위호환용으로 유지(신규 코드는 참조하지 않음).
--       배포 후 첫 조회 시 각 (admin_dong_code, yyyymm) 행의 신규 컬럼은 자동으로
--       upsert 되어 채워진다. 기존 캐시 행을 강제 재적재하려면 아래 주석의 DELETE 참고.
--       캐시 테이블이 아직 없어도 앱은 정상 동작하며(그저 매 조회마다 API 호출),
--       속도 향상을 원할 때 위 두 SQL 을 순서대로 실행하면 된다.

ALTER TABLE app_dong_population_cache ADD COLUMN IF NOT EXISTS age_10_population INTEGER DEFAULT 0;
ALTER TABLE app_dong_population_cache ADD COLUMN IF NOT EXISTS age_20_population INTEGER DEFAULT 0;
ALTER TABLE app_dong_population_cache ADD COLUMN IF NOT EXISTS age_30_population INTEGER DEFAULT 0;
ALTER TABLE app_dong_population_cache ADD COLUMN IF NOT EXISTS age_40_population INTEGER DEFAULT 0;
ALTER TABLE app_dong_population_cache ADD COLUMN IF NOT EXISTS age_50_population INTEGER DEFAULT 0;
ALTER TABLE app_dong_population_cache ADD COLUMN IF NOT EXISTS age_60_population INTEGER DEFAULT 0;
ALTER TABLE app_dong_population_cache ADD COLUMN IF NOT EXISTS age_70plus_population INTEGER DEFAULT 0;

COMMENT ON COLUMN app_dong_population_cache.age_10_population IS
  '만 10~19세 남녀 합산 인구 (male10AgeNmprCnt+feml10AgeNmprCnt).';
COMMENT ON COLUMN app_dong_population_cache.age_20_population IS
  '만 20~29세 남녀 합산 인구.';
COMMENT ON COLUMN app_dong_population_cache.age_30_population IS
  '만 30~39세 남녀 합산 인구.';
COMMENT ON COLUMN app_dong_population_cache.age_40_population IS
  '만 40~49세 남녀 합산 인구.';
COMMENT ON COLUMN app_dong_population_cache.age_50_population IS
  '만 50~59세 남녀 합산 인구.';
COMMENT ON COLUMN app_dong_population_cache.age_60_population IS
  '만 60~69세 남녀 합산 인구.';
COMMENT ON COLUMN app_dong_population_cache.age_70plus_population IS
  '만 70세 이상 남녀 합산 인구 (70+80+90+100 버킷 합).';

-- (선택) 기존 캐시 행을 새 스키마로 완전 재적재하고 싶다면 실행:
-- DELETE FROM app_dong_population_cache;
