-- 사내 업무 태그·상단고정(핀) 확장
-- 모든 DDL은 멱등 (ADD COLUMN IF NOT EXISTS)
--
-- ※ app_tasks 테이블이 이미 존재하는 운영 DB에서는 자동 DDL이 실행되지 않으므로
--   Supabase SQL Editor에서 본 파일을 1회 수동 실행해 주세요.

ALTER TABLE app_tasks ADD COLUMN IF NOT EXISTS tags TEXT;
ALTER TABLE app_tasks ADD COLUMN IF NOT EXISTS is_pinned BOOLEAN DEFAULT false;
CREATE INDEX IF NOT EXISTS idx_app_tasks_pinned ON app_tasks(is_pinned, created_at DESC);
