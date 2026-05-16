-- =============================================================
-- momo SaaS — Phase 5/6: 트라이얼 + 결제 스키마 (v1)
-- Supabase 대시보드 → SQL Editor → 이 파일 전체를 실행
-- =============================================================

-- -----------------------------------------------------------
-- 1. app_orgs 보강: 트라이얼 기간 + 상태
-- -----------------------------------------------------------
ALTER TABLE app_orgs ADD COLUMN IF NOT EXISTS trial_ends_at    TIMESTAMPTZ;
ALTER TABLE app_orgs ADD COLUMN IF NOT EXISTS onboarding_done  BOOLEAN NOT NULL DEFAULT false;

-- 기존 레코드에 trial_ends_at 채우기 (이미 가입된 org: 오늘부터 14일)
UPDATE app_orgs
   SET trial_ends_at = now() + interval '14 days'
 WHERE trial_ends_at IS NULL AND plan = 'trial';

-- -----------------------------------------------------------
-- 2. app_subscriptions (구독)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_subscriptions (
  id                    BIGSERIAL PRIMARY KEY,
  org_id                BIGINT NOT NULL REFERENCES app_orgs(id) ON DELETE CASCADE,
  plan                  TEXT NOT NULL,            -- starter | growth | pro
  billing_cycle         TEXT NOT NULL DEFAULT 'monthly', -- monthly | annual
  status                TEXT NOT NULL DEFAULT 'active',  -- active | past_due | cancelled | paused
  current_period_start  TIMESTAMPTZ NOT NULL DEFAULT now(),
  current_period_end    TIMESTAMPTZ NOT NULL,
  cancel_at_period_end  BOOLEAN NOT NULL DEFAULT false,
  toss_billing_key      TEXT,                     -- 토스페이먼츠 발급 빌링키
  created_at            TIMESTAMPTZ DEFAULT now(),
  updated_at            TIMESTAMPTZ DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_app_subscriptions_org_active
  ON app_subscriptions(org_id)
  WHERE status IN ('active', 'past_due');

CREATE INDEX IF NOT EXISTS idx_app_subscriptions_period_end
  ON app_subscriptions(current_period_end)
  WHERE status = 'active';

ALTER TABLE app_subscriptions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "subs_service_all" ON app_subscriptions;
CREATE POLICY "subs_service_all" ON app_subscriptions FOR ALL USING (true) WITH CHECK (true);

-- -----------------------------------------------------------
-- 3. app_invoices (청구서)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_invoices (
  id                BIGSERIAL PRIMARY KEY,
  org_id            BIGINT NOT NULL REFERENCES app_orgs(id) ON DELETE CASCADE,
  subscription_id   BIGINT REFERENCES app_subscriptions(id),
  amount            BIGINT NOT NULL,              -- 원 단위
  status            TEXT NOT NULL DEFAULT 'pending', -- pending | paid | failed | refunded
  toss_payment_key  TEXT,
  toss_order_id     TEXT UNIQUE,
  paid_at           TIMESTAMPTZ,
  receipt_url       TEXT,
  failure_reason    TEXT,
  retry_count       INTEGER NOT NULL DEFAULT 0,
  next_retry_at     TIMESTAMPTZ,
  created_at        TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_app_invoices_org_id ON app_invoices(org_id);
CREATE INDEX IF NOT EXISTS idx_app_invoices_status  ON app_invoices(status, next_retry_at)
  WHERE status IN ('pending', 'failed');

ALTER TABLE app_invoices ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "invoices_service_all" ON app_invoices;
CREATE POLICY "invoices_service_all" ON app_invoices FOR ALL USING (true) WITH CHECK (true);

-- -----------------------------------------------------------
-- 4. app_billing_events (감사 로그)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_billing_events (
  id          BIGSERIAL PRIMARY KEY,
  org_id      BIGINT REFERENCES app_orgs(id) ON DELETE SET NULL,
  event_type  TEXT NOT NULL,   -- issued_key | charged | failed | refunded | cancelled | retried
  payload     JSONB,
  created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_billing_events_org ON app_billing_events(org_id, created_at DESC);

ALTER TABLE app_billing_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "billing_events_service_all" ON app_billing_events;
CREATE POLICY "billing_events_service_all" ON app_billing_events FOR ALL USING (true) WITH CHECK (true);

-- =============================================================
-- 완료! 검증:
--   SELECT id, name, plan, trial_ends_at, status FROM app_orgs;
--   SELECT tablename FROM pg_tables WHERE tablename LIKE 'app_%' ORDER BY tablename;
-- =============================================================
