-- =============================================================
-- momo SaaS — Phase 8: 배송팀 포털 (Pro 전용)
-- Supabase 대시보드 → SQL Editor → 이 파일 전체 실행
-- =============================================================

-- -----------------------------------------------------------
-- 1. app_delivery_regions — 지역 정의
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_delivery_regions (
  id          BIGSERIAL PRIMARY KEY,
  org_id      BIGINT NOT NULL REFERENCES app_orgs(id) ON DELETE CASCADE,
  region_name TEXT NOT NULL,
  region_code TEXT NOT NULL,
  is_active   BOOLEAN NOT NULL DEFAULT true,
  created_at  TIMESTAMPTZ DEFAULT now(),
  UNIQUE(org_id, region_code)
);
CREATE INDEX IF NOT EXISTS idx_delivery_regions_org ON app_delivery_regions(org_id);
ALTER TABLE app_delivery_regions ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "regions_all" ON app_delivery_regions;
CREATE POLICY "regions_all" ON app_delivery_regions FOR ALL USING (true) WITH CHECK (true);

-- -----------------------------------------------------------
-- 2. app_delivery_slot_templates — 요일별 기본 슬롯 용량
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_delivery_slot_templates (
  id           BIGSERIAL PRIMARY KEY,
  org_id       BIGINT NOT NULL REFERENCES app_orgs(id) ON DELETE CASCADE,
  region_id    BIGINT NOT NULL REFERENCES app_delivery_regions(id) ON DELETE CASCADE,
  day_of_week  INTEGER NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
               -- 0=월 1=화 2=수 3=목 4=금 5=토 6=일
  time_slot    TEXT NOT NULL CHECK (time_slot IN ('morning', 'afternoon')),
  max_count    INTEGER NOT NULL DEFAULT 0 CHECK (max_count >= 0),
  UNIQUE(region_id, day_of_week, time_slot)
);
ALTER TABLE app_delivery_slot_templates ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "slot_templates_all" ON app_delivery_slot_templates;
CREATE POLICY "slot_templates_all" ON app_delivery_slot_templates FOR ALL USING (true) WITH CHECK (true);

-- -----------------------------------------------------------
-- 3. app_delivery_slot_overrides — 특정 날짜 예외 설정
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_delivery_slot_overrides (
  id            BIGSERIAL PRIMARY KEY,
  org_id        BIGINT NOT NULL REFERENCES app_orgs(id) ON DELETE CASCADE,
  region_id     BIGINT NOT NULL REFERENCES app_delivery_regions(id) ON DELETE CASCADE,
  override_date DATE NOT NULL,
  time_slot     TEXT NOT NULL CHECK (time_slot IN ('morning', 'afternoon')),
  max_count     INTEGER NOT NULL DEFAULT 0 CHECK (max_count >= 0),
  note          TEXT,
  UNIQUE(region_id, override_date, time_slot)
);
CREATE INDEX IF NOT EXISTS idx_slot_overrides_region_date
  ON app_delivery_slot_overrides(region_id, override_date);
ALTER TABLE app_delivery_slot_overrides ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "slot_overrides_all" ON app_delivery_slot_overrides;
CREATE POLICY "slot_overrides_all" ON app_delivery_slot_overrides FOR ALL USING (true) WITH CHECK (true);

-- -----------------------------------------------------------
-- 4. app_delivery_drivers — 배송 기사 계정
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_delivery_drivers (
  id           BIGSERIAL PRIMARY KEY,
  org_id       BIGINT NOT NULL REFERENCES app_orgs(id) ON DELETE CASCADE,
  region_id    BIGINT NOT NULL REFERENCES app_delivery_regions(id) ON DELETE RESTRICT,
  driver_name  TEXT NOT NULL,
  login_id     TEXT NOT NULL,
  password_hash TEXT NOT NULL,
  is_active    BOOLEAN NOT NULL DEFAULT true,
  created_at   TIMESTAMPTZ DEFAULT now(),
  UNIQUE(org_id, login_id)
);
CREATE INDEX IF NOT EXISTS idx_delivery_drivers_region ON app_delivery_drivers(region_id);
ALTER TABLE app_delivery_drivers ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "drivers_all" ON app_delivery_drivers;
CREATE POLICY "drivers_all" ON app_delivery_drivers FOR ALL USING (true) WITH CHECK (true);

-- -----------------------------------------------------------
-- 5. app_deliveries — 배송 스케줄
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_deliveries (
  id             BIGSERIAL PRIMARY KEY,
  order_id       BIGINT REFERENCES app_orders(id) ON DELETE SET NULL,
  org_id         BIGINT NOT NULL REFERENCES app_orgs(id) ON DELETE CASCADE,
  region_id      BIGINT REFERENCES app_delivery_regions(id) ON DELETE SET NULL,
  driver_id      BIGINT REFERENCES app_delivery_drivers(id) ON DELETE SET NULL,
  scheduled_date DATE,
  time_slot      TEXT DEFAULT 'any' CHECK (time_slot IN ('morning', 'afternoon', 'any')),
  status         TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','assigned','loaded','arrived','completed','cancelled')),
  delivery_note  TEXT,      -- 관리자 메모
  driver_memo    TEXT,      -- 기사 현장 메모
  loaded_at      TIMESTAMPTZ,
  arrived_at     TIMESTAMPTZ,
  completed_at   TIMESTAMPTZ,
  cancelled_at   TIMESTAMPTZ,
  created_at     TIMESTAMPTZ DEFAULT now(),
  UNIQUE(order_id)
);
CREATE INDEX IF NOT EXISTS idx_deliveries_org         ON app_deliveries(org_id, status);
CREATE INDEX IF NOT EXISTS idx_deliveries_region_date ON app_deliveries(region_id, scheduled_date);
CREATE INDEX IF NOT EXISTS idx_deliveries_driver       ON app_deliveries(driver_id, scheduled_date);

ALTER TABLE app_deliveries ENABLE ROW LEVEL SECURITY;
-- 관리자 전체 접근 (service_role key 사용 시 RLS 우회)
DROP POLICY IF EXISTS "deliveries_admin_all" ON app_deliveries;
CREATE POLICY "deliveries_admin_all" ON app_deliveries FOR ALL USING (true) WITH CHECK (true);

-- -----------------------------------------------------------
-- 6. 슬롯 잔여량 계산 함수
-- -----------------------------------------------------------
CREATE OR REPLACE FUNCTION get_delivery_slot_remaining(
  p_region_id BIGINT,
  p_date      DATE,
  p_time_slot TEXT
) RETURNS INT
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
  v_max  INT;
  v_used INT;
  v_dow  INT;
BEGIN
  -- PostgreSQL: 0=일, 1=월 ... 6=토 → 우리 규칙: 0=월...6=일로 변환
  v_dow := ((EXTRACT(DOW FROM p_date)::INT) + 6) % 7;

  -- override 우선 확인
  SELECT max_count INTO v_max
  FROM app_delivery_slot_overrides
  WHERE region_id = p_region_id
    AND override_date = p_date
    AND time_slot = p_time_slot;

  -- 없으면 template 사용
  IF NOT FOUND OR v_max IS NULL THEN
    SELECT max_count INTO v_max
    FROM app_delivery_slot_templates
    WHERE region_id = p_region_id
      AND day_of_week = v_dow
      AND time_slot = p_time_slot;
  END IF;

  IF v_max IS NULL OR v_max = 0 THEN RETURN 0; END IF;

  SELECT COUNT(*) INTO v_used
  FROM app_deliveries
  WHERE region_id = p_region_id
    AND scheduled_date = p_date
    AND time_slot = p_time_slot
    AND status NOT IN ('cancelled');

  RETURN GREATEST(0, v_max - v_used);
END;
$$;

-- -----------------------------------------------------------
-- 7. app_customers 배송 정보 컬럼 추가
-- -----------------------------------------------------------
ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS delivery_address_detail TEXT;
ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS elevator_yn              BOOLEAN DEFAULT true;
ALTER TABLE app_customers ADD COLUMN IF NOT EXISTS preferred_delivery_time TEXT;

-- =============================================================
-- 검증:
--   SELECT tablename FROM pg_tables WHERE tablename LIKE 'app_delivery%';
--   SELECT * FROM get_delivery_slot_remaining(1, CURRENT_DATE, 'morning');
-- =============================================================
