-- =============================================================
-- momo SaaS — Phase 4: 본점-지점 조직 모델 (v1)
-- Supabase 대시보드 → SQL Editor → 이 파일 전체를 실행
-- 기존 데이터 보존: ADD COLUMN IF NOT EXISTS 사용
-- =============================================================

-- -----------------------------------------------------------
-- 1. app_orgs (조직/본사 단위)
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_orgs (
  id                   BIGSERIAL PRIMARY KEY,
  name                 TEXT NOT NULL UNIQUE,
  plan                 TEXT NOT NULL DEFAULT 'solo',   -- solo | business | enterprise
  status               TEXT NOT NULL DEFAULT 'active', -- active | suspended | cancelled
  billing_customer_key TEXT,
  created_at           TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE app_orgs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "orgs_service_all" ON app_orgs;
CREATE POLICY "orgs_service_all" ON app_orgs FOR ALL USING (true) WITH CHECK (true);

-- -----------------------------------------------------------
-- 2. app_stores 보강
-- -----------------------------------------------------------
ALTER TABLE app_stores ADD COLUMN IF NOT EXISTS org_id           BIGINT REFERENCES app_orgs(id) ON DELETE SET NULL;
ALTER TABLE app_stores ADD COLUMN IF NOT EXISTS is_headquarters  BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE app_stores ADD COLUMN IF NOT EXISTS parent_store_id  BIGINT REFERENCES app_stores(id) ON DELETE SET NULL;
ALTER TABLE app_stores ADD COLUMN IF NOT EXISTS address          TEXT;
ALTER TABLE app_stores ADD COLUMN IF NOT EXISTS phone            TEXT;

CREATE INDEX IF NOT EXISTS idx_app_stores_org_id ON app_stores(org_id);

-- -----------------------------------------------------------
-- 3. app_users 보강
-- -----------------------------------------------------------
-- role CHECK 확장 (기존 값 유지 + 새 역할 추가)
ALTER TABLE app_users DROP CONSTRAINT IF EXISTS app_users_role_check;
ALTER TABLE app_users ADD CONSTRAINT app_users_role_check
  CHECK (role IN (
    'superadmin',    -- momo 운영자 전용 (격리)
    'org_owner',     -- 조직 오너: 전 지점 + 결제·구독
    'org_admin',     -- 조직 어드민: 전 지점, 결제 불가
    'store_manager', -- 지점 매니저: 자기 지점만 (구 store_admin과 동일)
    'staff',         -- 직원: 자기 지점, 매출 등록/조회 (구 user와 동일)
    -- 구 역할 (하위 호환)
    'store_admin',
    'user'
  ));

ALTER TABLE app_users ADD COLUMN IF NOT EXISTS org_id BIGINT REFERENCES app_orgs(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_app_users_org_id ON app_users(org_id);

-- -----------------------------------------------------------
-- 4. app_user_stores: role 컬럼 추가 (지점 내 역할)
-- -----------------------------------------------------------
ALTER TABLE app_user_stores ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'staff'
  CHECK (role IN ('org_owner', 'org_admin', 'store_manager', 'staff', 'store_admin', 'user'));

-- -----------------------------------------------------------
-- 5. 기존 매장을 조직으로 자동 마이그레이션
--    app_stores 각각 → 독립 app_orgs 1개 생성 후 연결
-- -----------------------------------------------------------
DO $$
DECLARE
  s RECORD;
  new_org_id BIGINT;
  owner_user_id BIGINT;
  owner_plan TEXT;
BEGIN
  FOR s IN SELECT * FROM app_stores WHERE org_id IS NULL LOOP
    -- 매장의 오너 찾기 (app_users.store_id 기준)
    SELECT id, plan INTO owner_user_id, owner_plan
      FROM app_users
     WHERE store_id = s.id AND role IN ('store_admin', 'org_owner', 'store_manager')
     ORDER BY id LIMIT 1;

    -- app_orgs 생성
    INSERT INTO app_orgs (name, plan, status)
    VALUES (
      s.store_name,
      COALESCE(owner_plan, 'solo'),
      'active'
    )
    RETURNING id INTO new_org_id;

    -- app_stores.org_id 연결 + 본점으로 표시
    UPDATE app_stores
       SET org_id = new_org_id,
           is_headquarters = true
     WHERE id = s.id;

    -- 해당 org의 사용자들에게 org_id 부여
    UPDATE app_users
       SET org_id = new_org_id
     WHERE store_id = s.id AND org_id IS NULL;
  END LOOP;
END $$;

-- superadmin 은 org 없음 (momo 운영자)
-- (superadmin의 org_id는 NULL 유지)

-- -----------------------------------------------------------
-- 6. RLS 정책 갱신
-- -----------------------------------------------------------

-- app_stores: service_role 전체 허용 (RLS는 앱 레벨에서 org_id로 필터)
DROP POLICY IF EXISTS "Allow all app_stores" ON app_stores;
CREATE POLICY "Allow all app_stores" ON app_stores FOR ALL USING (true) WITH CHECK (true);

-- app_users: service_role 전체 허용
DROP POLICY IF EXISTS "Allow all app_users" ON app_users;
CREATE POLICY "Allow all app_users" ON app_users FOR ALL USING (true) WITH CHECK (true);

-- app_orders: db_filename 기준 (기존 유지 — 향후 org_id 기반으로 전환 예정)
-- app_payments, app_customers: 동일 기존 정책 유지

-- =============================================================
-- 완료! 검증:
--   SELECT id, name, plan, status FROM app_orgs;
--   SELECT id, store_name, org_id, is_headquarters FROM app_stores;
--   SELECT id, username, role, org_id FROM app_users;
-- =============================================================
