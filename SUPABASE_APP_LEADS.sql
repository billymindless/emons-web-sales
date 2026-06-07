-- ============================================================
-- app_leads: 옴니채널 리드 고객 관리 테이블
-- 유입 경로(lead_source), 영업 단계(lead_stage), 담당자 KPI,
-- 넛징 자동화, 구매 전환 추적을 하나의 테이블로 통합.
-- ============================================================

CREATE TABLE IF NOT EXISTS app_leads (
  id                   BIGSERIAL PRIMARY KEY,
  store_name           TEXT NOT NULL,
  phone                TEXT NOT NULL,
  name                 TEXT,
  memo                 TEXT,

  -- 유입 경로 (3가지로 엄격히 통제)
  lead_source          TEXT NOT NULL CHECK (lead_source IN (
                         '온라인_채널톡', '전화_문의', '오프라인_방문'
                       )),

  -- 리드 온도 (영업 파이프라인 5단계)
  lead_stage           TEXT NOT NULL DEFAULT '1_신규유입' CHECK (lead_stage IN (
                         '1_신규유입', '2_자료발송', '3_매장방문', '4_계약완료', '5_계약실패'
                       )),

  -- 담당자 (KPI 핵심 기본키)
  assigned_employee_id BIGINT REFERENCES app_users(id),
  assigned_store       TEXT,

  -- 사후 관리
  next_contact_date    DATE,
  contact_memo         TEXT,
  followup_done        BOOLEAN DEFAULT FALSE,

  -- 자동화 상태
  nurturing_step       INT DEFAULT 0,
  next_nurture_at      TIMESTAMPTZ,
  ct_synced            BOOLEAN DEFAULT FALSE,

  -- 전환 추적 (구매 연결)
  converted_at         TIMESTAMPTZ,
  converted_order_id   BIGINT REFERENCES app_orders(id),
  revenue_amount       NUMERIC(12,2),

  created_at           TIMESTAMPTZ DEFAULT now(),
  updated_at           TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS app_leads_phone_idx
  ON app_leads (phone);

CREATE INDEX IF NOT EXISTS app_leads_store_idx
  ON app_leads (store_name);

CREATE INDEX IF NOT EXISTS app_leads_employee_idx
  ON app_leads (assigned_employee_id);

CREATE INDEX IF NOT EXISTS app_leads_stage_idx
  ON app_leads (lead_stage);

-- 넛징 발송 대상 조회용 부분 인덱스 (중복 발송 방지)
CREATE INDEX IF NOT EXISTS app_leads_nurture_idx
  ON app_leads (next_nurture_at)
  WHERE nurturing_step < 2
    AND lead_stage NOT IN ('4_계약완료', '5_계약실패');
