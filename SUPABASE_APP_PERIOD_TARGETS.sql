-- =====================================================================
-- 직원별 "기간 목표" 테이블 ─ 매장관리자가 임의 기간 + 총 필수시간 지정
-- 예: 김승찬 2026-06 ~ 2026-07, 200시간
-- 한 직원에게 여러 기간 등록 가능. 등록/수정/삭제 지원.
-- 모든 DDL은 IF NOT EXISTS 가드로 멱등 실행 가능.
-- =====================================================================
CREATE TABLE IF NOT EXISTS app_period_work_targets (
    id BIGSERIAL PRIMARY KEY,
    db_filename TEXT NOT NULL,
    employee_name TEXT NOT NULL,
    start_ym TEXT NOT NULL,              -- 'YYYY-MM' (기간 시작월)
    end_ym TEXT NOT NULL,                -- 'YYYY-MM' (기간 종료월)
    required_minutes INTEGER NOT NULL,   -- 기간 총 필수시간 (시간 × 60)
    note TEXT,
    created_by TEXT,
    updated_by TEXT,
    updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_period_targets_db_emp
    ON app_period_work_targets (db_filename, employee_name);

ALTER TABLE app_period_work_targets ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_period_work_targets" ON app_period_work_targets;
CREATE POLICY "Allow all app_period_work_targets" ON app_period_work_targets
    FOR ALL USING (true) WITH CHECK (true);
