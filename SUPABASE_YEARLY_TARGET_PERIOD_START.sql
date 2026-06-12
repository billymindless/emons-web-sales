-- app_yearly_work_targets 에 집계 시작일(period_start_date) 컬럼 추가
-- Supabase 대시보드 → SQL Editor 에서 실행하세요.

-- 1) 컬럼 추가 (이미 있으면 건너뜀)
ALTER TABLE app_yearly_work_targets
ADD COLUMN IF NOT EXISTS period_start_date DATE;

-- 2) 2026년 모든 직원 목표의 집계 시작일을 6월 1일로 설정
UPDATE app_yearly_work_targets
SET period_start_date = '2026-06-01'
WHERE year = 2026;

-- 3) 결과 확인
SELECT employee_name, year, required_minutes,
       round(required_minutes / 60.0, 1) AS required_hours,
       period_start_date
FROM app_yearly_work_targets
WHERE year = 2026
ORDER BY employee_name;
