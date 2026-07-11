-- ────────────────────────────────────────────────────────────
-- AI 주간/월간 세일즈 리포트 저장 테이블
-- Supabase SQL Editor에서 실행하세요.
--
-- 관련 계획서: docs/plans/AI_WEEKLY_SALES_REPORT_PLAN.md
-- ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS app_sales_reports (
  id              BIGSERIAL PRIMARY KEY,
  period_type     TEXT NOT NULL,            -- 'weekly' | 'monthly'
  start_date      DATE NOT NULL,            -- 리포트 대상 기간 시작 (포함)
  end_date        DATE NOT NULL,            -- 리포트 대상 기간 종료 (포함)
  store_key       TEXT NOT NULL,            -- db_filename 또는 'all' (전 매장 통합)
  store_name      TEXT,                     -- 표시용 매장명 (all 이면 '전 매장 통합')
  title           TEXT NOT NULL,            -- 사용자 표시용 제목

  -- 리포트 본문
  markdown_body   TEXT,                     -- 최종 Markdown 문서 전체
  excel_url       TEXT,                     -- Supabase Storage URL (Phase 2)

  -- 분석 결과
  metrics         JSONB,                    -- 핵심 KPI 원본 값 (kpi, by_employee, by_region 등)
  ai_summary      JSONB,                    -- Gemini 응답 (executive/highlights/risks/actions)

  -- 실행 상태
  status          TEXT NOT NULL DEFAULT 'success', -- 'success' | 'failed' | 'running'
  error_message   TEXT,                     -- 실패 시 원인
  generated_by    TEXT,                     -- 'cron' | 사용자명 (수동 생성 시)

  generated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- 동일 기간·매장은 하나의 최신 리포트만 유지 (재생성 시 upsert)
  CONSTRAINT app_sales_reports_period_store_uniq
    UNIQUE (period_type, start_date, end_date, store_key)
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_app_sales_reports_generated_at
  ON app_sales_reports (generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_app_sales_reports_store
  ON app_sales_reports (store_key, period_type, start_date DESC);
CREATE INDEX IF NOT EXISTS idx_app_sales_reports_status
  ON app_sales_reports (status);

-- updated_at 자동 갱신 트리거
CREATE OR REPLACE FUNCTION app_sales_reports_touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_app_sales_reports_updated_at ON app_sales_reports;
CREATE TRIGGER trg_app_sales_reports_updated_at
  BEFORE UPDATE ON app_sales_reports
  FOR EACH ROW EXECUTE FUNCTION app_sales_reports_touch_updated_at();

-- RLS 비활성화 (내부 ERP, service_role_key 사용)
ALTER TABLE app_sales_reports DISABLE ROW LEVEL SECURITY;
