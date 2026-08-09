-- =====================================================================
-- 직원 KPI 가중치 (매출/마진/전시/현금수금) — 매장별/전체통합 × 연월 단위
-- - 스코프: db_filename = 매장 db 또는 '__all__' (전체 매장 통합)
-- - 기간: year_month = 'YYYY-MM'
-- - 설정 없으면 앱에서 기본값(70/15/5/10)으로 폴백
-- - 모든 DDL은 IF NOT EXISTS 가드로 멱등 실행 가능.
-- =====================================================================

CREATE TABLE IF NOT EXISTS app_employee_kpi_weights (
    id BIGSERIAL PRIMARY KEY,
    db_filename TEXT NOT NULL,          -- 매장 db 또는 '__all__'
    year_month  TEXT NOT NULL,          -- 'YYYY-MM'
    w_revenue   NUMERIC NOT NULL DEFAULT 70,
    w_margin    NUMERIC NOT NULL DEFAULT 15,
    w_display   NUMERIC NOT NULL DEFAULT 5,
    w_cash      NUMERIC NOT NULL DEFAULT 10,
    updated_by  TEXT,
    updated_at  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (db_filename, year_month)
);

CREATE INDEX IF NOT EXISTS idx_kpi_weights_scope_ym
    ON app_employee_kpi_weights (db_filename, year_month);

ALTER TABLE app_employee_kpi_weights ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_employee_kpi_weights" ON app_employee_kpi_weights;
CREATE POLICY "Allow all app_employee_kpi_weights" ON app_employee_kpi_weights
    FOR ALL USING (true) WITH CHECK (true);
