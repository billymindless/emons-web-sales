-- ============================================================
-- 카카오 비즈니스 채널 고객 연동 마이그레이션
-- 실행 위치: Supabase 대시보드 → SQL Editor
-- ============================================================

-- 1) app_customers에 카카오 채널 친구 여부 컬럼 추가
ALTER TABLE app_customers
  ADD COLUMN IF NOT EXISTS kakao_friend_added    BOOLEAN    DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS kakao_friend_added_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS kakao_user_key        TEXT;

CREATE INDEX IF NOT EXISTS idx_app_customers_kakao
  ON app_customers(store_name, kakao_friend_added);

-- 2) 고객 메시지 발송 이력 테이블 생성
CREATE TABLE IF NOT EXISTS app_customer_messages (
  id              BIGSERIAL PRIMARY KEY,
  customer_id     BIGINT REFERENCES app_customers(id) ON DELETE SET NULL,
  store_name      TEXT,
  order_id        BIGINT,
  phone           TEXT,
  message_type    TEXT,        -- 'purchase_confirm' | 'shipping_notify' | 'channel_invite' | 'cs_reply' | 'manual'
  channel         TEXT,        -- 'alimtalk' | 'friendtalk' | 'sms'
  status          TEXT,        -- 'sent' | 'failed' | 'not_friend' | 'skipped' | 'out_of_hours'
  solapi_msg_id   TEXT,
  message_body    TEXT,
  error_detail    TEXT,
  sent_by         TEXT,        -- 담당자 username
  created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_app_cust_msg_customer  ON app_customer_messages(customer_id);
CREATE INDEX IF NOT EXISTS idx_app_cust_msg_store     ON app_customer_messages(store_name, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_app_cust_msg_status    ON app_customer_messages(status);

-- 3) RLS
ALTER TABLE app_customer_messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow all app_customer_messages" ON app_customer_messages;
CREATE POLICY "Allow all app_customer_messages"
  ON app_customer_messages FOR ALL USING (true) WITH CHECK (true);

-- ============================================================
-- 실행 후 확인 쿼리
-- ============================================================
-- SELECT column_name FROM information_schema.columns
--   WHERE table_name = 'app_customers'
--   AND column_name IN ('kakao_friend_added', 'kakao_friend_added_at', 'kakao_user_key');
--
-- SELECT COUNT(*) FROM app_customer_messages;
