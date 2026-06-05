-- =====================================================================
-- 근태 관리 v2 ─ 월·연 필수 근무시간 기반 + 신청·승인 워크플로우
-- =====================================================================
-- 동작 모델:
--   월 기준 = (해당 ym의 월 입력) OR (연 입력 / 12 fallback)
--   월 잔여 = 월 기준 − Σ(승인된 신청 분, 해당 ym)
--   연 잔여 = (연 입력) − Σ(승인된 신청 분, 해당 year)
--
-- 모든 DDL 은 IF NOT EXISTS 가드를 사용해 멱등 실행 가능합니다.
-- 마지막의 1회성 마이그레이션도 ON CONFLICT DO NOTHING + 중복 방지 WHERE 절로
-- 여러 번 실행해도 안전합니다.
-- =====================================================================


-- ---------------------------------------------------------------------
-- 1) app_monthly_work_targets ─ 직원별 월 필수 근무시간 (매장관리자 입력)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_monthly_work_targets (
    id BIGSERIAL PRIMARY KEY,
    db_filename TEXT NOT NULL,
    employee_name TEXT NOT NULL,
    ym TEXT NOT NULL,                      -- 'YYYY-MM'
    required_minutes INTEGER NOT NULL,     -- 월 필수 (시간 × 60)
    note TEXT,
    created_by TEXT,
    updated_by TEXT,
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (db_filename, employee_name, ym)
);

CREATE INDEX IF NOT EXISTS idx_monthly_targets_db_ym
    ON app_monthly_work_targets (db_filename, ym);
CREATE INDEX IF NOT EXISTS idx_monthly_targets_emp_ym
    ON app_monthly_work_targets (db_filename, employee_name, ym);

ALTER TABLE app_monthly_work_targets ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_monthly_work_targets" ON app_monthly_work_targets;
CREATE POLICY "Allow all app_monthly_work_targets" ON app_monthly_work_targets
    FOR ALL USING (true) WITH CHECK (true);


-- ---------------------------------------------------------------------
-- 2) app_yearly_work_targets ─ 직원별 연 필수 근무시간 (매장관리자 입력)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_yearly_work_targets (
    id BIGSERIAL PRIMARY KEY,
    db_filename TEXT NOT NULL,
    employee_name TEXT NOT NULL,
    year INTEGER NOT NULL,
    required_minutes INTEGER NOT NULL,
    note TEXT,
    created_by TEXT,
    updated_by TEXT,
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (db_filename, employee_name, year)
);

CREATE INDEX IF NOT EXISTS idx_yearly_targets_db_year
    ON app_yearly_work_targets (db_filename, year);
CREATE INDEX IF NOT EXISTS idx_yearly_targets_emp_year
    ON app_yearly_work_targets (db_filename, employee_name, year);

ALTER TABLE app_yearly_work_targets ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_yearly_work_targets" ON app_yearly_work_targets;
CREATE POLICY "Allow all app_yearly_work_targets" ON app_yearly_work_targets
    FOR ALL USING (true) WITH CHECK (true);


-- ---------------------------------------------------------------------
-- 3) app_work_adjustments ─ 신청·승인 통합 테이블
--    kind:   'reward' | 'meeting' | 'summer_vacation' | 'overtime' | 'etc'
--    sign:   '+' (실근무 인정 가산) | '-' (휴무 차감)
--    status: 'pending' | 'approved' | 'rejected'
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_work_adjustments (
    id BIGSERIAL PRIMARY KEY,
    db_filename TEXT NOT NULL,
    employee_name TEXT NOT NULL,
    target_date DATE NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('reward','meeting','summer_vacation','overtime','etc')),
    sign TEXT NOT NULL CHECK (sign IN ('+','-')),
    minutes INTEGER NOT NULL CHECK (minutes > 0),
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
    created_by TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    approved_by TEXT,
    approved_at TIMESTAMPTZ,
    reject_reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_work_adj_db_status
    ON app_work_adjustments (db_filename, status);
CREATE INDEX IF NOT EXISTS idx_work_adj_emp_date
    ON app_work_adjustments (db_filename, employee_name, target_date);
CREATE INDEX IF NOT EXISTS idx_work_adj_db_date
    ON app_work_adjustments (db_filename, target_date);

ALTER TABLE app_work_adjustments ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_work_adjustments" ON app_work_adjustments;
CREATE POLICY "Allow all app_work_adjustments" ON app_work_adjustments
    FOR ALL USING (true) WITH CHECK (true);


-- ---------------------------------------------------------------------
-- 3-1) 추가근무·회의 시간대 + 근무지 컬럼 추가 (캘린더 자동 반영용)
-- ---------------------------------------------------------------------
ALTER TABLE app_work_adjustments
    ADD COLUMN IF NOT EXISTS shift_start TIME,           -- 시작 시간 (추가근무·회의용)
    ADD COLUMN IF NOT EXISTS shift_end   TIME,           -- 종료 시간
    ADD COLUMN IF NOT EXISTS work_db_filename TEXT,      -- 근무 매장 db_filename
    ADD COLUMN IF NOT EXISTS work_location_name TEXT;    -- 외부 근무지 명칭


-- ---------------------------------------------------------------------
-- 4) 1회성 마이그레이션 ─ app_overtime_claims → app_work_adjustments
--    기존 모든 추가근무 보상 신청을 새 통합 테이블로 복사합니다.
--    매핑:
--      kind   = 'overtime'
--      sign   = '+'                 (추가근무는 실근무 인정)
--      reason = COALESCE(reason, '') || ' [compensation_type=' || compensation_type || ']'
--    중복 방지: 동일 (db_filename, employee_name, target_date, kind, minutes, created_at) 가
--             이미 app_work_adjustments 에 있으면 건너뜁니다.
--    app_overtime_claims 테이블이 없는 환경에서도 에러가 나지 않도록 DO 블록으로 감쌌습니다.
-- ---------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'app_overtime_claims'
    ) THEN
        INSERT INTO app_work_adjustments (
            db_filename, employee_name, target_date,
            kind, sign, minutes, reason, status,
            created_by, created_at, approved_by, approved_at
        )
        SELECT
            oc.db_filename,
            oc.employee_name,
            oc.claim_date,
            'overtime'                                                       AS kind,
            '+'                                                              AS sign,
            oc.minutes,
            COALESCE(oc.reason, '')
                || ' [compensation_type=' || oc.compensation_type || ']'    AS reason,
            oc.status,
            oc.created_by,
            oc.created_at,
            oc.approved_by,
            oc.approved_at
        FROM app_overtime_claims oc
        WHERE NOT EXISTS (
            SELECT 1 FROM app_work_adjustments wa
            WHERE wa.db_filename   = oc.db_filename
              AND wa.employee_name = oc.employee_name
              AND wa.target_date   = oc.claim_date
              AND wa.kind          = 'overtime'
              AND wa.minutes       = oc.minutes
              AND wa.created_at    = oc.created_at
        );
        RAISE NOTICE '[v2 migration] app_overtime_claims → app_work_adjustments 복사 완료';
    ELSE
        RAISE NOTICE '[v2 migration] app_overtime_claims 테이블이 없어 마이그레이션을 건너뜁니다.';
    END IF;
END
$$;
