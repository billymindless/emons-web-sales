-- app_users 테이블에 hire_date 컬럼 추가 (멱등)
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS hire_date DATE;

-- 조회 성능 인덱스
CREATE INDEX IF NOT EXISTS idx_app_users_hire_date ON app_users (hire_date);
