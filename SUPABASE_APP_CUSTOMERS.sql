-- Supabase 고객 테이블 (app_customers)
-- 매장 구분: store_name NOT NULL — app_stores.store_name과 일치. 매장별 고객 데이터 분리.
-- Supabase 대시보드 → SQL Editor에서 이 파일 내용을 실행하세요.

CREATE TABLE IF NOT EXISTS app_customers (
  id BIGSERIAL PRIMARY KEY,
  store_name TEXT NOT NULL,
  name TEXT NOT NULL DEFAULT '미입력',
  phone1 TEXT,
  phone2 TEXT,
  address TEXT,
  source TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_app_customers_store_name ON app_customers(store_name);
CREATE INDEX IF NOT EXISTS idx_app_customers_phone1 ON app_customers(store_name, phone1);

COMMENT ON COLUMN app_customers.store_name IS '매장 구분자. app_stores.store_name과 동일. 모든 조회/입력 시 필터 필수.';

-- RLS (다른 app_* 테이블과 동일하게 전체 허용)
ALTER TABLE app_customers ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow all app_customers" ON app_customers;
CREATE POLICY "Allow all app_customers" ON app_customers FOR ALL USING (true) WITH CHECK (true);
