-- =============================================================
-- momo SaaS — Supabase 초기 스키마 (v1)
-- Supabase 대시보드 → SQL Editor → 이 파일 내용을 순서대로 실행
-- CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS 사용으로
-- 이미 테이블이 있어도 안전하게 실행 가능
-- =============================================================

-- -----------------------------------------------------------
-- 1. 매장 테이블 (app_stores)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_stores (
  id          BIGSERIAL PRIMARY KEY,
  store_name  TEXT NOT NULL UNIQUE,
  db_filename TEXT NOT NULL UNIQUE
);

ALTER TABLE app_stores ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_stores" ON app_stores;
CREATE POLICY "Allow all app_stores" ON app_stores FOR ALL USING (true) WITH CHECK (true);

-- -----------------------------------------------------------
-- 2. 사용자 테이블 (app_users)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_users (
  id       BIGSERIAL PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password TEXT NOT NULL,
  email    TEXT,
  role     TEXT NOT NULL CHECK (role IN ('superadmin', 'store_admin', 'user')),
  store_id BIGINT REFERENCES app_stores(id),
  name     TEXT
);

ALTER TABLE app_users ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_users" ON app_users;
CREATE POLICY "Allow all app_users" ON app_users FOR ALL USING (true) WITH CHECK (true);

-- superadmin 초기 계정 (비밀번호: 1234 → SHA256)
INSERT INTO app_users (username, password, email, role, store_id, name)
SELECT 'superadmin',
       '03ac674216f3e15c761ee1a5e255f067953623c8b388b4459e13f978d7c846f4a',
       '',
       'superadmin',
       NULL,
       '최고관리자'
WHERE NOT EXISTS (SELECT 1 FROM app_users WHERE username = 'superadmin');

-- -----------------------------------------------------------
-- 3. 사용자-매장 다대다 (app_user_stores)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_user_stores (
  user_id  BIGINT NOT NULL REFERENCES app_users(id)  ON DELETE CASCADE,
  store_id BIGINT NOT NULL REFERENCES app_stores(id) ON DELETE CASCADE,
  PRIMARY KEY (user_id, store_id)
);

ALTER TABLE app_user_stores ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_user_stores" ON app_user_stores;
CREATE POLICY "Allow all app_user_stores" ON app_user_stores FOR ALL USING (true) WITH CHECK (true);

-- -----------------------------------------------------------
-- 4. 고객 테이블 (app_customers)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_customers (
  id         BIGSERIAL PRIMARY KEY,
  store_name TEXT NOT NULL,
  name       TEXT NOT NULL DEFAULT '미입력',
  phone1     TEXT,
  phone2     TEXT,
  address    TEXT,
  latitude   DOUBLE PRECISION,
  longitude  DOUBLE PRECISION,
  source     TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS latitude  DOUBLE PRECISION;
ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS longitude DOUBLE PRECISION;

CREATE INDEX IF NOT EXISTS idx_app_customers_store_name ON app_customers(store_name);
CREATE INDEX IF NOT EXISTS idx_app_customers_phone1     ON app_customers(store_name, phone1);

ALTER TABLE app_customers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_customers" ON app_customers;
CREATE POLICY "Allow all app_customers" ON app_customers FOR ALL USING (true) WITH CHECK (true);

-- -----------------------------------------------------------
-- 5. 주문 테이블 (app_orders)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_orders (
  id                   BIGSERIAL PRIMARY KEY,
  db_filename          TEXT NOT NULL,
  customer_id          BIGINT,
  employee_names       TEXT,
  order_date           TEXT NOT NULL,
  delivery_date        TEXT,
  category             TEXT,
  cost_price           REAL,
  total_amount         REAL,
  visit_reason         TEXT,
  purchase_reason      TEXT,
  actual_margin        REAL,
  display_sales_amount INTEGER DEFAULT 0,
  display_cost_amount  INTEGER DEFAULT 0,
  balance_status       TEXT,
  created_at           TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_app_orders_db_filename  ON app_orders(db_filename);
CREATE INDEX IF NOT EXISTS idx_app_orders_order_date   ON app_orders(db_filename, order_date);
CREATE INDEX IF NOT EXISTS idx_app_orders_customer_id  ON app_orders(db_filename, customer_id);

ALTER TABLE app_orders ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_orders" ON app_orders;
CREATE POLICY "Allow all app_orders" ON app_orders FOR ALL USING (true) WITH CHECK (true);

-- -----------------------------------------------------------
-- 6. 결제 테이블 (app_payments)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_payments (
  id                   BIGSERIAL PRIMARY KEY,
  db_filename          TEXT NOT NULL,
  order_id             BIGINT NOT NULL,
  payment_date         TEXT NOT NULL,
  amount               BIGINT NOT NULL,
  payment_method       TEXT,
  card_company         TEXT,
  fee_amount           BIGINT,
  onnuri_approval_code TEXT,
  created_at           TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE app_payments ADD COLUMN IF NOT EXISTS created_by TEXT;

CREATE INDEX IF NOT EXISTS idx_app_payments_db_filename ON app_payments(db_filename);
CREATE INDEX IF NOT EXISTS idx_app_payments_order_id    ON app_payments(db_filename, order_id);
CREATE INDEX IF NOT EXISTS idx_app_payments_onnuri      ON app_payments(db_filename, payment_date)
  WHERE onnuri_approval_code IS NOT NULL;

ALTER TABLE app_payments ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_payments" ON app_payments;
CREATE POLICY "Allow all app_payments" ON app_payments FOR ALL USING (true) WITH CHECK (true);

-- -----------------------------------------------------------
-- 7. 결제 변경 이력 (app_payment_history)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_payment_history (
  id                 BIGSERIAL PRIMARY KEY,
  db_filename        TEXT NOT NULL,
  sale_id            BIGINT NOT NULL,
  customer_name      TEXT,
  action_type        TEXT NOT NULL,
  old_payment_data   JSONB,
  new_payment_data   JSONB,
  reason             TEXT NOT NULL,
  changed_by         TEXT NOT NULL,
  changed_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  receipt_image_path TEXT
);

CREATE INDEX IF NOT EXISTS idx_app_payment_history_db_filename ON app_payment_history(db_filename);
CREATE INDEX IF NOT EXISTS idx_app_payment_history_sale_id     ON app_payment_history(sale_id);

ALTER TABLE app_payment_history ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "allow_all_app_payment_history" ON app_payment_history;
CREATE POLICY "allow_all_app_payment_history" ON app_payment_history FOR ALL USING (true) WITH CHECK (true);

-- -----------------------------------------------------------
-- 8. 수정 요청 (app_edit_requests)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_edit_requests (
  id           BIGSERIAL PRIMARY KEY,
  db_filename  TEXT,
  created_at   TIMESTAMPTZ DEFAULT now(),
  requested_by TEXT NOT NULL,
  entity_type  TEXT NOT NULL,
  entity_id    BIGINT NOT NULL,
  payload      JSONB NOT NULL,
  reason       TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending',
  reviewed_by  TEXT,
  reviewed_at  TIMESTAMPTZ
);

ALTER TABLE app_edit_requests ADD COLUMN IF NOT EXISTS target_username TEXT;
ALTER TABLE app_edit_requests ADD COLUMN IF NOT EXISTS notif_type      TEXT;

CREATE INDEX IF NOT EXISTS idx_app_edit_requests_status ON app_edit_requests(db_filename, status);
CREATE INDEX IF NOT EXISTS idx_app_edit_requests_created ON app_edit_requests(db_filename, created_at);
CREATE INDEX IF NOT EXISTS idx_app_edit_requests_notif   ON app_edit_requests(target_username, status);

ALTER TABLE app_edit_requests ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_edit_requests" ON app_edit_requests;
CREATE POLICY "Allow all app_edit_requests" ON app_edit_requests FOR ALL USING (true) WITH CHECK (true);

-- -----------------------------------------------------------
-- 9. 공지사항 (app_notices)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_notices (
  id            BIGSERIAL PRIMARY KEY,
  title         TEXT,
  content       TEXT,
  external_link TEXT,
  message       TEXT,
  is_active     BOOLEAN NOT NULL DEFAULT true,
  created_at    TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE app_notices ADD COLUMN IF NOT EXISTS title         TEXT;
ALTER TABLE app_notices ADD COLUMN IF NOT EXISTS content       TEXT;
ALTER TABLE app_notices ADD COLUMN IF NOT EXISTS external_link TEXT;
ALTER TABLE app_notices ADD COLUMN IF NOT EXISTS message       TEXT;
ALTER TABLE app_notices ADD COLUMN IF NOT EXISTS is_active     BOOLEAN DEFAULT true;
ALTER TABLE app_notices ADD COLUMN IF NOT EXISTS created_at    TIMESTAMPTZ DEFAULT now();

CREATE INDEX IF NOT EXISTS idx_app_notices_active_created ON app_notices(is_active, created_at DESC);

ALTER TABLE app_notices ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_notices" ON app_notices;
CREATE POLICY "Allow all app_notices" ON app_notices FOR ALL USING (true) WITH CHECK (true);

-- -----------------------------------------------------------
-- 10. 할일 (app_todos)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.app_todos (
  id           BIGSERIAL PRIMARY KEY,
  tenant_name  TEXT NOT NULL,
  author       TEXT DEFAULT '',
  content      TEXT NOT NULL,
  is_completed BOOLEAN DEFAULT FALSE,
  created_at   TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE public.app_todos ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "app_todos_select" ON public.app_todos;
CREATE POLICY "app_todos_select" ON public.app_todos FOR SELECT USING (true);
DROP POLICY IF EXISTS "app_todos_insert" ON public.app_todos;
CREATE POLICY "app_todos_insert" ON public.app_todos FOR INSERT WITH CHECK (true);
DROP POLICY IF EXISTS "app_todos_update" ON public.app_todos;
CREATE POLICY "app_todos_update" ON public.app_todos FOR UPDATE USING (true);
DROP POLICY IF EXISTS "app_todos_delete" ON public.app_todos;
CREATE POLICY "app_todos_delete" ON public.app_todos FOR DELETE USING (true);

-- =============================================================
-- 완료! 총 10개 테이블 생성.
-- 다음 단계: tools/supabase/02_verify_schema.py 실행으로 검증
-- =============================================================
