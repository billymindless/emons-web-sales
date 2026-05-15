-- =============================================================
-- momo SaaS — 플랜(구독) 컬럼 추가 (v1)
-- Supabase 대시보드 → SQL Editor → 이 파일 내용을 실행
-- =============================================================

-- app_users 에 plan 컬럼 추가 (solo / business / enterprise)
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS plan TEXT DEFAULT 'solo';

-- app_stores 에 owner_user_id 컬럼 추가 (매장 소유자)
ALTER TABLE app_stores ADD COLUMN IF NOT EXISTS owner_user_id BIGINT REFERENCES app_users(id);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_app_users_plan ON app_users(plan);
CREATE INDEX IF NOT EXISTS idx_app_stores_owner ON app_stores(owner_user_id);
