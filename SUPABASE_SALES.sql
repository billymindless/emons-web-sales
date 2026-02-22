-- Supabase sales 테이블 (매출 트랜잭션 로그)
-- 테넌트 구분: db_filename 또는 secrets의 sales_tenant_column 값과 동일한 컬럼 사용.
-- RLS 정책이 없거나 제한적이면 INSERT 시 "new row violates row-level security policy" 발생 → 아래 정책 추가 필요.
-- Supabase 대시보드 → SQL Editor에서 실행하세요.

-- 1) 테이블이 없을 경우 생성 (이미 있으면 스킵)
CREATE TABLE IF NOT EXISTS sales (
  id BIGSERIAL PRIMARY KEY,
  order_id BIGINT NOT NULL,
  transaction_date TEXT NOT NULL,
  amount REAL NOT NULL,
  note TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  db_filename TEXT,
  unpaid_balance REAL
);

CREATE INDEX IF NOT EXISTS idx_sales_db_filename ON sales(db_filename);
CREATE INDEX IF NOT EXISTS idx_sales_transaction_date ON sales(transaction_date);

-- 2) RLS 허용 정책 (INSERT/UPDATE/DELETE/SELECT 모두 허용)
ALTER TABLE sales ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow all sales" ON sales;
CREATE POLICY "Allow all sales" ON sales FOR ALL USING (true) WITH CHECK (true);
