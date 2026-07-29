-- Supabase 고객 테이블에 행정동 매핑 실패 사유 추적 컬럼 추가
-- 목적: '동 단위 상권 퍼포먼스 맵' 백필 배치가 실패 원인(단계별)을 기록해
--       (1) 사람이 원인을 파악하고 (2) 동일하게 실패할 주소를 무한 재시도하지 않게 함.
--
-- 실행: Supabase 대시보드 → SQL Editor 에서 이 파일 내용을 실행.

ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS admin_dong_fail_reason TEXT;
ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS admin_dong_fail_at TIMESTAMPTZ;
ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS admin_dong_fail_count INTEGER DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_app_customers_admin_dong_fail_reason ON app_customers(admin_dong_fail_reason);

COMMENT ON COLUMN app_customers.admin_dong_fail_reason IS
  '행정동 지오코딩 실패 단계. no_kakao_key | not_found(주소검색/정규화재시도/키워드검색 모두 0건) | no_h_region(좌표는 있으나 행정동 매칭 실패) | rate_limited(HTTP 429/5xx, 일시오류) | update_failed. NULL 이면 성공했거나 아직 시도 안 함.';
COMMENT ON COLUMN app_customers.admin_dong_fail_at IS
  '마지막 실패 시각. not_found 는 이 값 기준 N일 내 배치 대상에서 자동 제외(무한 재시도 방지), rate_limited 는 항상 재시도 대상 유지.';
COMMENT ON COLUMN app_customers.admin_dong_fail_count IS
  '누적 실패 횟수 (참고용, 반복 실패 주소 식별).';
