-- 공지사항에 매장별 노출 범위 부여 (전체매장 / 매장별 다중 선택)
-- Supabase 대시보드 → SQL Editor에서 실행하세요.
-- ※ 이미 컬럼이 존재하는 경우에도 안전하게 실행됩니다 (ADD COLUMN IF NOT EXISTS).

-- 1. store_ids 컬럼 추가 (NULL 또는 빈 배열 = 전체매장 공지)
ALTER TABLE app_notices ADD COLUMN IF NOT EXISTS store_ids BIGINT[];

-- 2. 매장 필터링(contains 연산) 조회 성능을 위한 GIN 인덱스
CREATE INDEX IF NOT EXISTS idx_app_notices_store_ids
  ON app_notices USING GIN (store_ids);
