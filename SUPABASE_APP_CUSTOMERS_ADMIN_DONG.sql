-- Supabase 고객 테이블에 행정동 컬럼 추가 (상권 퍼포먼스 맵용)
-- 목적: '동 단위 상권 퍼포먼스 맵' 기능에서 행정안전부 인구·세대 API 와
--       조인하기 위한 행정동명·행정동코드 필드를 신설.
--
-- 기존 bname 컬럼은 카카오 지번주소의 '법정동' 이므로 행안부 인구 API 의
-- '행정동' 키와 다를 수 있음. 별도로 카카오 coord2regioncode(region_type=H)
-- 응답 값을 저장한다.
--
-- 실행: Supabase 대시보드 → SQL Editor 에서 이 파일 내용을 실행.

-- ── 컬럼 추가 (무중단) ──
ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS admin_dong_name TEXT;
ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS admin_dong_code TEXT;

-- ── 조회 인덱스 (상권 맵 그룹핑 성능) ──
CREATE INDEX IF NOT EXISTS idx_app_customers_admin_dong_code ON app_customers(admin_dong_code);
CREATE INDEX IF NOT EXISTS idx_app_customers_admin_dong_name ON app_customers(admin_dong_name);

-- ── 컬럼 설명 ──
COMMENT ON COLUMN app_customers.admin_dong_name IS
  '카카오 coord2regioncode region_type=H 의 region_3depth_name (행정동명, 예: 삼산동). 상권 퍼포먼스 맵에서 행정동별 집계 축으로 사용.';
COMMENT ON COLUMN app_customers.admin_dong_code IS
  '카카오 coord2regioncode region_type=H 의 code (행정동코드 10자리, 예: 3111051000). 행정안전부 인구·세대 API 와 조인 키로 사용 (필요 시 8자리 접미사 매핑).';
