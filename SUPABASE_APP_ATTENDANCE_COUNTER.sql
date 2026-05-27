-- =====================================================================
-- 근태 카운터 시스템 확장 마이그레이션
--  - 매장 공용 일정 (app_store_events)
--  - 직원 월 목표 근무시간 (app_employee_settings.monthly_target_hours)
--  - 추가근무 보상 신청 (app_overtime_claims)
-- =====================================================================

-- 1) 매장 공용 일정 (메모/표시 전용. 개인 근무에 영향 없음)
--    event_date = 시작일(또는 단일일), end_date = 종료일(여러 날 일정만)
--    end_date NULL  → 하루짜리 일정 (event_date 1일만 표시)
--    end_date NOT NULL → event_date ~ end_date 까지 매일 캘린더에 표시
CREATE TABLE IF NOT EXISTS app_store_events (
    id BIGSERIAL PRIMARY KEY,
    db_filename TEXT NOT NULL,
    event_date DATE NOT NULL,
    end_date DATE,
    title TEXT NOT NULL,
    start_time TIME,
    end_time TIME,
    note TEXT,
    created_by TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- 기존 테이블이 있던 환경을 위해 end_date 컬럼 추가
ALTER TABLE app_store_events
    ADD COLUMN IF NOT EXISTS end_date DATE;

CREATE INDEX IF NOT EXISTS idx_store_events_db_date
    ON app_store_events (db_filename, event_date);


-- 2) 직원 월 목표 근무시간 컬럼 추가
ALTER TABLE app_employee_settings
    ADD COLUMN IF NOT EXISTS monthly_target_hours NUMERIC;


-- 3) 추가근무 보상 신청
--    compensation_type: 'comp_time'(시차 적립) | 'leave_swap'(연차 대체) | 'payment'(급여 청구)
--    status:           'pending' | 'approved' | 'rejected'
CREATE TABLE IF NOT EXISTS app_overtime_claims (
    id BIGSERIAL PRIMARY KEY,
    db_filename TEXT NOT NULL,
    employee_name TEXT NOT NULL,
    claim_date DATE NOT NULL,
    minutes INTEGER NOT NULL,
    compensation_type TEXT NOT NULL,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_by TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    approved_by TEXT,
    approved_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_overtime_claims_db_status
    ON app_overtime_claims (db_filename, status);
CREATE INDEX IF NOT EXISTS idx_overtime_claims_emp_date
    ON app_overtime_claims (db_filename, employee_name, claim_date);
