-- =====================================================================
-- app_gmail_tokens — 사용자별 Gmail OAuth2 토큰 저장
-- =====================================================================
-- 개인 Gmail(@gmail.com) 및 Google Workspace 계정 모두 지원.
-- 앱 내 "Google 계정 연결" 버튼으로 인증하면 자동으로 저장됩니다.
-- =====================================================================

CREATE TABLE IF NOT EXISTS app_gmail_tokens (
  id            BIGSERIAL PRIMARY KEY,
  username      TEXT NOT NULL,             -- app_users.username (연결한 직원)
  gmail_address TEXT,                      -- 연결된 Gmail 주소
  refresh_token TEXT NOT NULL,             -- OAuth2 refresh token (장기 유효)
  access_token  TEXT,                      -- OAuth2 access token (단기, 캐시용)
  token_expiry  TIMESTAMPTZ,               -- access_token 만료 시각
  created_at    TIMESTAMPTZ DEFAULT now(),
  updated_at    TIMESTAMPTZ DEFAULT now(),
  UNIQUE (username)
);

ALTER TABLE app_gmail_tokens ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_gmail_tokens" ON app_gmail_tokens;
CREATE POLICY "Allow all app_gmail_tokens"
  ON app_gmail_tokens FOR ALL USING (true) WITH CHECK (true);
