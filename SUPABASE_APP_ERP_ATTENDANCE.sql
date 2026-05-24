-- Supabase ERP 근태 관리 모듈 테이블 5종
-- Supabase 대시보드 → SQL Editor에서 이 파일 내용을 실행하세요.
-- 모든 DDL은 IF NOT EXISTS / IF NOT EXISTS 컬럼 가드를 사용해 멱등 실행 가능합니다.

-- ============================================================
-- 1) app_staffing_rules - 요일별 시간대 최소 근무 인원 규칙
-- ============================================================
CREATE TABLE IF NOT EXISTS app_staffing_rules (
  id BIGSERIAL PRIMARY KEY,
  db_filename TEXT NOT NULL,
  day_of_week INT NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
  slot_start TIME NOT NULL,
  slot_end TIME NOT NULL,
  min_staff INT NOT NULL DEFAULT 1,
  created_by TEXT,
  updated_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_app_staffing_rules_store_dow
  ON app_staffing_rules(db_filename, day_of_week);

ALTER TABLE app_staffing_rules ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_staffing_rules" ON app_staffing_rules;
CREATE POLICY "Allow all app_staffing_rules" ON app_staffing_rules FOR ALL USING (true) WITH CHECK (true);


-- ============================================================
-- 2) app_shift_schedules - 사전 근무 일정 계획
-- ============================================================
CREATE TABLE IF NOT EXISTS app_shift_schedules (
  id BIGSERIAL PRIMARY KEY,
  db_filename TEXT NOT NULL,
  employee_name TEXT NOT NULL,
  shift_date DATE NOT NULL,
  shift_start TIME NOT NULL,
  shift_end TIME NOT NULL,
  work_location_name TEXT,
  note TEXT,
  created_by TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_app_shift_schedules_store_date
  ON app_shift_schedules(db_filename, shift_date);
CREATE INDEX IF NOT EXISTS idx_app_shift_schedules_emp_date
  ON app_shift_schedules(employee_name, shift_date);

ALTER TABLE app_shift_schedules ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_shift_schedules" ON app_shift_schedules;
CREATE POLICY "Allow all app_shift_schedules" ON app_shift_schedules FOR ALL USING (true) WITH CHECK (true);


-- ============================================================
-- 3) app_leave_grants - 직원별 연차 부여 및 입사일
-- ============================================================
CREATE TABLE IF NOT EXISTS app_leave_grants (
  id BIGSERIAL PRIMARY KEY,
  home_db_filename TEXT NOT NULL,
  employee_name TEXT NOT NULL,
  hire_date DATE,
  year INT NOT NULL,
  annual_days NUMERIC DEFAULT 0,
  created_by TEXT,
  updated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (home_db_filename, employee_name, year)
);
CREATE INDEX IF NOT EXISTS idx_app_leave_grants_emp_year
  ON app_leave_grants(home_db_filename, employee_name, year);

ALTER TABLE app_leave_grants ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_leave_grants" ON app_leave_grants;
CREATE POLICY "Allow all app_leave_grants" ON app_leave_grants FOR ALL USING (true) WITH CHECK (true);


-- ============================================================
-- 4) app_attendance_logs - 실제 근태 기록 (다점포 지원)
-- ============================================================
CREATE TABLE IF NOT EXISTS app_attendance_logs (
  id BIGSERIAL PRIMARY KEY,
  home_db_filename TEXT NOT NULL,
  work_db_filename TEXT,
  work_location_name TEXT,
  employee_name TEXT NOT NULL,
  log_date DATE NOT NULL,
  work_type TEXT NOT NULL,
  leave_deduction NUMERIC DEFAULT 0,
  diff_minutes INT DEFAULT 0,
  start_time TIME,
  end_time TIME,
  standard_start TIME DEFAULT '09:00',
  standard_end TIME DEFAULT '18:00',
  status TEXT DEFAULT 'approved',
  note TEXT,
  created_by TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_app_attendance_logs_home_date
  ON app_attendance_logs(home_db_filename, log_date);
CREATE INDEX IF NOT EXISTS idx_app_attendance_logs_emp_date
  ON app_attendance_logs(employee_name, log_date);
CREATE INDEX IF NOT EXISTS idx_app_attendance_logs_work_db
  ON app_attendance_logs(work_db_filename, log_date);

ALTER TABLE app_attendance_logs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_attendance_logs" ON app_attendance_logs;
CREATE POLICY "Allow all app_attendance_logs" ON app_attendance_logs FOR ALL USING (true) WITH CHECK (true);


-- ============================================================
-- 5) app_store_hours - 매장별 기본 근무시간 (주중/주말·공휴일 별)
-- ============================================================
CREATE TABLE IF NOT EXISTS app_store_hours (
  id BIGSERIAL PRIMARY KEY,
  db_filename TEXT NOT NULL UNIQUE,
  weekday_start TIME DEFAULT '09:00',
  weekday_end TIME DEFAULT '18:00',
  weekend_start TIME DEFAULT '10:00',
  weekend_end TIME DEFAULT '19:00',
  updated_by TEXT,
  updated_at TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE app_store_hours ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_store_hours" ON app_store_hours;
CREATE POLICY "Allow all app_store_hours" ON app_store_hours FOR ALL USING (true) WITH CHECK (true);

-- 매장별 기본 근무시간 seed (이미 있으면 건너뜀)
-- 학성점: 주중 10:00-19:00, 주말/공휴일 10:00-20:00
INSERT INTO app_store_hours (db_filename, weekday_start, weekday_end, weekend_start, weekend_end)
SELECT db_filename, '10:00'::TIME, '19:00'::TIME, '10:00'::TIME, '20:00'::TIME
FROM app_stores WHERE store_name LIKE '%학성%'
ON CONFLICT (db_filename) DO NOTHING;

-- 삼산점: 주중 10:00-19:30, 주말/공휴일 10:00-20:00
INSERT INTO app_store_hours (db_filename, weekday_start, weekday_end, weekend_start, weekend_end)
SELECT db_filename, '10:00'::TIME, '19:30'::TIME, '10:00'::TIME, '20:00'::TIME
FROM app_stores WHERE store_name LIKE '%삼산%'
ON CONFLICT (db_filename) DO NOTHING;

-- 양산점(=평산점): 주중 10:30-19:00, 주말/공휴일 10:30-19:30
INSERT INTO app_store_hours (db_filename, weekday_start, weekday_end, weekend_start, weekend_end)
SELECT db_filename, '10:30'::TIME, '19:00'::TIME, '10:30'::TIME, '19:30'::TIME
FROM app_stores WHERE store_name LIKE '%양산%' OR store_name LIKE '%평산%'
ON CONFLICT (db_filename) DO NOTHING;


-- ============================================================
-- 6) app_overtime_requests - 추가근무 신청/승인
-- ============================================================
CREATE TABLE IF NOT EXISTS app_overtime_requests (
  id BIGSERIAL PRIMARY KEY,
  home_db_filename TEXT NOT NULL,
  employee_name TEXT NOT NULL,
  request_date DATE NOT NULL,
  work_location_name TEXT,
  extra_start TIME,
  extra_end TIME,
  extra_minutes INT,
  reason TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  reviewed_by TEXT,
  reviewed_at TIMESTAMPTZ,
  created_by TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_app_overtime_requests_store_status
  ON app_overtime_requests(home_db_filename, status);
CREATE INDEX IF NOT EXISTS idx_app_overtime_requests_emp_date
  ON app_overtime_requests(employee_name, request_date);

ALTER TABLE app_overtime_requests ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_overtime_requests" ON app_overtime_requests;
CREATE POLICY "Allow all app_overtime_requests" ON app_overtime_requests FOR ALL USING (true) WITH CHECK (true);
