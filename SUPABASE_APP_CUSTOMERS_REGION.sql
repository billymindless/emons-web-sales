-- Supabase 고객 테이블에 지역·건물명 컬럼 추가 (다면 분석용)
-- 목적: 마케팅 인사이트 「다면 분석」 섹션에서 시군구/법정동/도로명/건물명 기준으로
--       매출을 다차원 피벗 분석하기 위해 정규화된 지역 컬럼을 신설.
-- 실행: Supabase 대시보드 → SQL Editor에서 이 파일 내용을 실행.
--       (기존 데이터는 별도 백필 스크립트 scripts/backfill_customer_region.py 로 보강)

-- ── 컬럼 추가 (무중단) ──
ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS sigungu       TEXT;
ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS bname         TEXT;
ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS road_name     TEXT;
ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS building_name TEXT;

-- ── 조회 인덱스 (다면 분석 필터/그룹 성능) ──
CREATE INDEX IF NOT EXISTS idx_app_customers_sigungu       ON app_customers(sigungu);
CREATE INDEX IF NOT EXISTS idx_app_customers_bname         ON app_customers(bname);
CREATE INDEX IF NOT EXISTS idx_app_customers_road_name     ON app_customers(road_name);
-- building_name 부분 일치 검색용 (ILIKE %..%). 데이터 소규모 시 seq scan 허용.
CREATE INDEX IF NOT EXISTS idx_app_customers_building_name ON app_customers(building_name);

-- ── 컬럼 설명 ──
COMMENT ON COLUMN app_customers.sigungu       IS '카카오 지오코딩 region_2depth_name (예: 남구). 다면 분석 지역 필터용.';
COMMENT ON COLUMN app_customers.bname         IS '카카오 지오코딩 region_3depth_name (법정동, 예: 삼산동). 다면 분석 지역 필터용.';
COMMENT ON COLUMN app_customers.road_name     IS '카카오 지오코딩 road_address.road_name (도로명, 예: 봉월로). 다면 분석 지역 필터용.';
COMMENT ON COLUMN app_customers.building_name IS '카카오 지오코딩 road_address.building_name (예: 태화강엑슬루타워). 아파트명 검색·건물유형 분류용.';
