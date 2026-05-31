-- 사내 업무판 보안 카테고리 확장
-- '회사 경영(company_mgmt)' 보안 업무용 category 컬럼 추가
-- 모든 DDL은 멱등 (ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS)
--
-- ※ app_tasks 테이블이 이미 존재하는 운영 DB에서는 자동 DDL이 실행되지 않으므로
--   Supabase SQL Editor에서 본 파일을 1회 수동 실행해 주세요.

ALTER TABLE app_tasks ADD COLUMN IF NOT EXISTS category TEXT;
CREATE INDEX IF NOT EXISTS idx_app_tasks_category ON app_tasks(category);
