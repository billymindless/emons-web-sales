-- =====================================================================
-- app_employee_salaries — 직원별 급여 정보
-- Supabase SQL Editor에서 실행하세요.
-- =====================================================================

CREATE TABLE IF NOT EXISTS app_employee_salaries (
  id              BIGSERIAL PRIMARY KEY,
  db_filename     TEXT NOT NULL,
  employee_name   TEXT NOT NULL,
  salary_type     TEXT NOT NULL DEFAULT 'monthly'  -- 'monthly' | 'hourly'
                  CHECK (salary_type IN ('monthly', 'hourly')),
  monthly_salary  BIGINT DEFAULT 0,   -- 월급제: 월 급여(원)
  hourly_wage     BIGINT DEFAULT 0,   -- 시급제: 시간당 급여(원)
  effective_from  DATE,               -- 적용 시작일
  updated_by      TEXT,
  updated_at      TIMESTAMPTZ DEFAULT now(),
  UNIQUE (db_filename, employee_name)
);

ALTER TABLE app_employee_salaries DISABLE ROW LEVEL SECURITY;
