-- Supabase 주문·결제 통합 테이블 (기존 store_*.db의 Orders/Payments를 한 DB로 이전)
-- 매장 구분: db_filename NOT NULL (예: store_1.db) — 매장별 데이터 완전 분리·조회용
-- Supabase 대시보드 → SQL Editor에서 실행 후, app.py는 Supabase 클라이언트로만 주문/결제 접근

-- 1) 주문 테이블 (app_orders)
CREATE TABLE IF NOT EXISTS app_orders (
  id BIGSERIAL PRIMARY KEY,
  db_filename TEXT NOT NULL,
  customer_id BIGINT,
  employee_names TEXT,
  order_date TEXT NOT NULL,
  delivery_date TEXT,
  category TEXT,
  cost_price REAL,
  total_amount REAL,
  visit_reason TEXT,
  purchase_reason TEXT,
  actual_margin REAL,
  display_sales_amount INTEGER DEFAULT 0,
  display_cost_amount INTEGER DEFAULT 0,
  balance_status TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_app_orders_db_filename ON app_orders(db_filename);
CREATE INDEX IF NOT EXISTS idx_app_orders_order_date ON app_orders(db_filename, order_date);
CREATE INDEX IF NOT EXISTS idx_app_orders_customer_id ON app_orders(db_filename, customer_id);

COMMENT ON COLUMN app_orders.db_filename IS '매장 구분자 (store_1.db 등). NOT NULL, 모든 조회/입력 시 필터 필수.';

-- 2) 결제 테이블 (app_payments)
CREATE TABLE IF NOT EXISTS app_payments (
  id BIGSERIAL PRIMARY KEY,
  db_filename TEXT NOT NULL,
  order_id BIGINT NOT NULL,
  payment_date TEXT NOT NULL,
  amount REAL NOT NULL,
  payment_method TEXT,
  card_company TEXT,
  fee_amount REAL,
  onnuri_approval_code TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_app_payments_db_filename ON app_payments(db_filename);
CREATE INDEX IF NOT EXISTS idx_app_payments_order_id ON app_payments(db_filename, order_id);
CREATE INDEX IF NOT EXISTS idx_app_payments_onnuri ON app_payments(db_filename, payment_date) WHERE onnuri_approval_code IS NOT NULL;

COMMENT ON COLUMN app_payments.db_filename IS '매장 구분자. app_orders와 동일 값으로 필터. NOT NULL.';

-- created_by 컬럼 추가 (입력자 추적용) — 이미 적용된 경우 무시됨
ALTER TABLE app_payments ADD COLUMN IF NOT EXISTS created_by TEXT;

-- RLS (앱에서 service_role/anon으로 접근 시 정책)
ALTER TABLE app_orders ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_payments ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow all app_orders" ON app_orders;
CREATE POLICY "Allow all app_orders" ON app_orders FOR ALL USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Allow all app_payments" ON app_payments;
CREATE POLICY "Allow all app_payments" ON app_payments FOR ALL USING (true) WITH CHECK (true);
