-- 직원 급여 정보 (월급제/시급제)
-- 멱등 DDL: 이미 존재하면 무시
CREATE TABLE IF NOT EXISTS app_employee_salaries (
  id              BIGSERIAL PRIMARY KEY,
  db_filename     TEXT NOT NULL,
  employee_name   TEXT NOT NULL,
  salary_type     TEXT NOT NULL DEFAULT 'monthly'
                    CHECK (salary_type IN ('monthly', 'hourly')),
  monthly_salary  BIGINT,
  hourly_wage     BIGINT,
  effective_from  DATE NOT NULL DEFAULT CURRENT_DATE,
  updated_by      TEXT,
  updated_at      TIMESTAMPTZ DEFAULT now(),
  UNIQUE (db_filename, employee_name)
);

ALTER TABLE app_employee_salaries ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
    WHERE tablename = 'app_employee_salaries' AND policyname = 'Allow all'
  ) THEN
    EXECUTE 'CREATE POLICY "Allow all" ON app_employee_salaries FOR ALL USING (true) WITH CHECK (true)';
  END IF;
END $$;
