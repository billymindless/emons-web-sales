-- Supabase 수정 요청 테이블 (기존 SQLite EditRequests 마이그레이션용)
-- 매장별 수정 요청(주문/결제 변경) 승인 워크플로우
-- Supabase 대시보드 → SQL Editor에서 실행하세요.
-- ※ 이미 테이블이 존재하는 경우에도 안전하게 실행됩니다 (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).

-- 1. 테이블 신규 생성 (없는 경우에만)
CREATE TABLE IF NOT EXISTS app_edit_requests (
  id BIGSERIAL PRIMARY KEY,
  db_filename TEXT,
  created_at TIMESTAMPTZ DEFAULT now(),
  requested_by TEXT NOT NULL,
  entity_type TEXT NOT NULL,
  entity_id BIGINT NOT NULL,
  payload JSONB NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  reviewed_by TEXT,
  reviewed_at TIMESTAMPTZ
);

-- 2. 기존 테이블에 db_filename 컬럼이 없는 경우 추가 (마이그레이션)
ALTER TABLE app_edit_requests ADD COLUMN IF NOT EXISTS db_filename TEXT;

-- 3. 인덱스 생성
CREATE INDEX IF NOT EXISTS idx_app_edit_requests_status ON app_edit_requests(db_filename, status);
CREATE INDEX IF NOT EXISTS idx_app_edit_requests_created ON app_edit_requests(db_filename, created_at);

-- 4. RLS 정책
ALTER TABLE app_edit_requests ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_edit_requests" ON app_edit_requests;
CREATE POLICY "Allow all app_edit_requests" ON app_edit_requests FOR ALL USING (true) WITH CHECK (true);
