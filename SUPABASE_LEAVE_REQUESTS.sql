-- 연차/월차 관리 테이블
-- Supabase SQL Editor에서 아래 내용을 실행하세요.

CREATE TABLE IF NOT EXISTS app_leave_requests (
  id            bigserial   PRIMARY KEY,
  db_filename   text        NOT NULL,
  user_id       bigint      NOT NULL,
  employee_name text        NOT NULL,
  leave_type    text        NOT NULL,        -- 연차 / 월차 / 반차 / 병가
  start_date    date        NOT NULL,
  end_date      date        NOT NULL,
  days_count    numeric     NOT NULL,        -- 사용 일수
  reason        text,
  has_weekend   boolean     NOT NULL DEFAULT false,  -- 주말/공휴일 포함 여부
  status        text        NOT NULL DEFAULT '승인됨',  -- 승인됨 / 승인대기 / 거절됨
  reject_reason text,
  reviewed_by   text,
  reviewed_at   timestamptz,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_leave_requests_db
  ON app_leave_requests (db_filename, start_date);

CREATE INDEX IF NOT EXISTS idx_leave_requests_user
  ON app_leave_requests (user_id);

CREATE INDEX IF NOT EXISTS idx_leave_requests_status
  ON app_leave_requests (db_filename, status);

ALTER TABLE app_leave_requests ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service role full access leave_requests" ON app_leave_requests;
CREATE POLICY "service role full access leave_requests"
  ON app_leave_requests
  FOR ALL
  USING (true)
  WITH CHECK (true);
