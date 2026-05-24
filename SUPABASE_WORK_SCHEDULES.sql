-- 근무 일정 계획 테이블
-- Supabase SQL Editor에서 아래 내용을 실행하세요.

CREATE TABLE IF NOT EXISTS app_work_schedules (
  id            bigserial   PRIMARY KEY,
  db_filename   text        NOT NULL,
  user_id       bigint      NOT NULL,
  employee_name text        NOT NULL,
  work_date     date        NOT NULL,
  start_time    time,
  end_time      time,
  work_type     text        NOT NULL DEFAULT '정상',  -- 정상 / 반차 / 야근
  memo          text,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_work_schedules_db_date
  ON app_work_schedules (db_filename, work_date);

CREATE INDEX IF NOT EXISTS idx_work_schedules_user
  ON app_work_schedules (user_id);

ALTER TABLE app_work_schedules ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "service role full access work_schedules" ON app_work_schedules;
CREATE POLICY "service role full access work_schedules"
  ON app_work_schedules
  FOR ALL
  USING (true)
  WITH CHECK (true);
