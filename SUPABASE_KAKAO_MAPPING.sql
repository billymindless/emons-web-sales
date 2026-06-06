-- ============================================================
-- 카카오 채팅 연동 확장 마이그레이션 (Phase 2)
-- SUPABASE_KAKAO_CHANNEL.sql 실행 후 이 파일을 실행하세요.
-- 실행 위치: Supabase 대시보드 → SQL Editor
-- ============================================================

-- 1) kakao_mapping 테이블 신규 생성
--    kakao_user_key(카카오 유저 식별자) ↔ customer_id 1:1 매핑
CREATE TABLE IF NOT EXISTS kakao_mapping (
  id             BIGSERIAL PRIMARY KEY,
  kakao_user_key TEXT        NOT NULL,
  customer_id    BIGINT      REFERENCES app_customers(id) ON DELETE CASCADE,
  store_name     TEXT,
  created_at     TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_kakao_mapping_user_key
  ON kakao_mapping(kakao_user_key);

CREATE INDEX IF NOT EXISTS idx_kakao_mapping_customer
  ON kakao_mapping(customer_id);

-- RLS
ALTER TABLE kakao_mapping ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all kakao_mapping" ON kakao_mapping;
CREATE POLICY "Allow all kakao_mapping"
  ON kakao_mapping FOR ALL USING (true) WITH CHECK (true);


-- 2) app_customer_messages에 direction 컬럼 추가
--    'outbound' = 우리가 발송한 메시지
--    'inbound'  = 고객이 카카오채널로 보낸 메시지
ALTER TABLE app_customer_messages
  ADD COLUMN IF NOT EXISTS direction      TEXT DEFAULT 'outbound',
  ADD COLUMN IF NOT EXISTS kakao_user_key TEXT;

CREATE INDEX IF NOT EXISTS idx_app_cust_msg_direction
  ON app_customer_messages(store_name, direction, created_at DESC);


-- ============================================================
-- 실행 후 확인 쿼리
-- ============================================================
-- SELECT COUNT(*) FROM kakao_mapping;
-- SELECT column_name FROM information_schema.columns
--   WHERE table_name = 'app_customer_messages'
--   AND column_name IN ('direction', 'kakao_user_key');
