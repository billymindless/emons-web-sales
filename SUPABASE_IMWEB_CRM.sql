-- ============================================================
-- 아임웹 CRM 연동 마이그레이션
-- 실행 위치: Supabase 대시보드 → SQL Editor
-- 순서: SUPABASE_KAKAO_CHANNEL.sql → SUPABASE_KAKAO_MAPPING.sql → 이 파일
-- ============================================================

-- 1) app_customers에 아임웹/CRM 관련 컬럼 추가
ALTER TABLE app_customers
  ADD COLUMN IF NOT EXISTS imweb_member_id   TEXT,          -- 아임웹 고유 회원 ID
  ADD COLUMN IF NOT EXISTS imweb_joined_at   TIMESTAMPTZ,  -- 아임웹 가입 일시
  ADD COLUMN IF NOT EXISTS marketing_agreed  BOOLEAN DEFAULT FALSE,  -- 마케팅 수신 동의
  ADD COLUMN IF NOT EXISTS customer_type     TEXT DEFAULT 'purchaser',
  -- 'purchaser': 구매 이력 있는 고객
  -- 'member_only': 아임웹 가입만 한 잠재 고객
  ADD COLUMN IF NOT EXISTS welcome_sent      BOOLEAN DEFAULT FALSE,  -- 웰컴 메시지 발송 여부
  ADD COLUMN IF NOT EXISTS last_order_at     TIMESTAMPTZ;  -- 최근 구매 일시 (CRM 필터용)

-- 아임웹 회원 ID 유니크 인덱스 (중복 방지)
CREATE UNIQUE INDEX IF NOT EXISTS idx_app_customers_imweb_id
  ON app_customers(imweb_member_id)
  WHERE imweb_member_id IS NOT NULL;

-- CRM 필터링용 인덱스
CREATE INDEX IF NOT EXISTS idx_app_customers_type
  ON app_customers(customer_type, marketing_agreed);

CREATE INDEX IF NOT EXISTS idx_app_customers_last_order
  ON app_customers(last_order_at DESC NULLS LAST);


-- 2) 아임웹 주문 이벤트 수신 로그 테이블 (배송 후 케어 메시지 예약용)
CREATE TABLE IF NOT EXISTS imweb_order_events (
  id             BIGSERIAL PRIMARY KEY,
  imweb_order_id TEXT        NOT NULL,
  customer_id    BIGINT      REFERENCES app_customers(id) ON DELETE SET NULL,
  phone          TEXT,
  product_name   TEXT,
  order_status   TEXT,        -- 'order_complete' | 'delivered' 등
  raw_payload    JSONB,
  care_sent      BOOLEAN DEFAULT FALSE,   -- 배송 후 케어 메시지 발송 여부
  care_send_at   TIMESTAMPTZ,            -- 케어 메시지 발송 예정 일시
  created_at     TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_imweb_order_events_order_id
  ON imweb_order_events(imweb_order_id);

CREATE INDEX IF NOT EXISTS idx_imweb_order_events_care
  ON imweb_order_events(care_sent, care_send_at)
  WHERE care_sent = FALSE;

ALTER TABLE imweb_order_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all imweb_order_events" ON imweb_order_events;
CREATE POLICY "Allow all imweb_order_events"
  ON imweb_order_events FOR ALL USING (true) WITH CHECK (true);


-- ============================================================
-- 실행 후 확인 쿼리
-- ============================================================
-- SELECT column_name FROM information_schema.columns
--   WHERE table_name = 'app_customers'
--   AND column_name IN ('imweb_member_id','marketing_agreed','customer_type','welcome_sent');
--
-- SELECT COUNT(*) FROM imweb_order_events;
