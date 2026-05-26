-- 사내결제시스템(사내 업무) 카카오 친구톡 발송용 직원 정보 컬럼
-- 멱등 실행 가능 (IF NOT EXISTS)

ALTER TABLE app_users ADD COLUMN IF NOT EXISTS phone TEXT;
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS kakao_friend_added BOOLEAN DEFAULT false;
ALTER TABLE app_users ADD COLUMN IF NOT EXISTS kakao_notify_enabled BOOLEAN DEFAULT true;

CREATE INDEX IF NOT EXISTS idx_app_users_phone ON app_users(phone);
CREATE INDEX IF NOT EXISTS idx_app_users_kakao_friend_added ON app_users(kakao_friend_added);
