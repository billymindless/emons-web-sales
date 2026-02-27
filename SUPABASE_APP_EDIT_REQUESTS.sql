-- Supabase 수정 요청 테이블 (기존 SQLite EditRequests 마이그레이션용)
-- 매장별 수정 요청(주문/결제 변경) 승인 워크플로우
-- Supabase 대시보드 → SQL Editor에서 실행하세요.

CREATE TABLE IF NOT EXISTS app_edit_requests (
  id BIGSERIAL PRIMARY KEY,
  db_filename TEXT NOT NULL,
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

CREATE INDEX IF NOT EXISTS idx_app_edit_requests_status ON app_edit_requests(db_filename, status);
CREATE INDEX IF NOT EXISTS idx_app_edit_requests_created ON app_edit_requests(db_filename, created_at);

ALTER TABLE app_edit_requests ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_edit_requests" ON app_edit_requests;
CREATE POLICY "Allow all app_edit_requests" ON app_edit_requests FOR ALL USING (true) WITH CHECK (true);
