-- 사내 업무 공개 범위(scope) 컬럼 추가
-- scope: 'store' = 내 매장 전용, 'company' = 전체 공개
ALTER TABLE app_tasks ADD COLUMN IF NOT EXISTS scope TEXT DEFAULT 'store';
CREATE INDEX IF NOT EXISTS idx_app_tasks_scope ON app_tasks(scope);
