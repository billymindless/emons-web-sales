-- =============================================================
-- momo SaaS — Phase 7: 에디션 분리 + 제품 카탈로그
-- Supabase 대시보드 → SQL Editor → 이 파일 전체 실행
-- =============================================================

-- -----------------------------------------------------------
-- 0. app_orgs 에디션 컬럼 추가
-- -----------------------------------------------------------
ALTER TABLE app_orgs ADD COLUMN IF NOT EXISTS edition TEXT NOT NULL DEFAULT 'lite'
  CHECK (edition IN ('lite', 'pro'));

-- -----------------------------------------------------------
-- 1. app_product_categories — 제품 카테고리
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_product_categories (
  id         BIGSERIAL PRIMARY KEY,
  org_id     BIGINT NOT NULL REFERENCES app_orgs(id) ON DELETE CASCADE,
  name       TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_product_categories_org ON app_product_categories(org_id, sort_order);
ALTER TABLE app_product_categories ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "prod_cat_all" ON app_product_categories;
CREATE POLICY "prod_cat_all" ON app_product_categories FOR ALL USING (true) WITH CHECK (true);

-- -----------------------------------------------------------
-- 2. app_products — 제품 마스터
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_products (
  id           BIGSERIAL PRIMARY KEY,
  org_id       BIGINT NOT NULL REFERENCES app_orgs(id) ON DELETE CASCADE,
  category_id  BIGINT REFERENCES app_product_categories(id) ON DELETE SET NULL,
  name         TEXT NOT NULL,
  sku          TEXT,
  product_type TEXT NOT NULL DEFAULT 'standard'
               CHECK (product_type IN ('ready_made', 'custom', 'standard')),
  base_price   BIGINT NOT NULL DEFAULT 0,   -- 원 단위
  is_active    BOOLEAN NOT NULL DEFAULT true,
  created_at   TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_products_org       ON app_products(org_id, is_active);
CREATE INDEX IF NOT EXISTS idx_products_category  ON app_products(category_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_products_sku ON app_products(org_id, sku)
  WHERE sku IS NOT NULL AND sku <> '';

ALTER TABLE app_products ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "products_all" ON app_products;
CREATE POLICY "products_all" ON app_products FOR ALL USING (true) WITH CHECK (true);

-- -----------------------------------------------------------
-- 3. app_product_option_groups — 옵션 그룹
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_product_option_groups (
  id          BIGSERIAL PRIMARY KEY,
  product_id  BIGINT NOT NULL REFERENCES app_products(id) ON DELETE CASCADE,
  group_name  TEXT NOT NULL,
  price_mode  TEXT NOT NULL DEFAULT 'delta'
              CHECK (price_mode IN ('fixed', 'delta')),
              -- fixed: 값 자체가 가격 (기성품)
              -- delta: 기본가에 더함 (주문제작)
  sort_order  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_option_groups_product ON app_product_option_groups(product_id, sort_order);
ALTER TABLE app_product_option_groups ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "opt_groups_all" ON app_product_option_groups;
CREATE POLICY "opt_groups_all" ON app_product_option_groups FOR ALL USING (true) WITH CHECK (true);

-- -----------------------------------------------------------
-- 4. app_product_option_values — 옵션 값
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_product_option_values (
  id          BIGSERIAL PRIMARY KEY,
  group_id    BIGINT NOT NULL REFERENCES app_product_option_groups(id) ON DELETE CASCADE,
  value_label TEXT NOT NULL,
  price       BIGINT NOT NULL DEFAULT 0,
  sort_order  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_option_values_group ON app_product_option_values(group_id, sort_order);
ALTER TABLE app_product_option_values ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "opt_values_all" ON app_product_option_values;
CREATE POLICY "opt_values_all" ON app_product_option_values FOR ALL USING (true) WITH CHECK (true);

-- -----------------------------------------------------------
-- 5. app_order_items — 주문-제품 연결
-- -----------------------------------------------------------
CREATE TABLE IF NOT EXISTS app_order_items (
  id               BIGSERIAL PRIMARY KEY,
  order_id         BIGINT NOT NULL REFERENCES app_orders(id) ON DELETE CASCADE,
  product_id       BIGINT REFERENCES app_products(id) ON DELETE SET NULL,
  product_name     TEXT NOT NULL,             -- 스냅샷 (제품 삭제 대비)
  qty              INTEGER NOT NULL DEFAULT 1,
  unit_price       BIGINT NOT NULL DEFAULT 0,
  line_total       BIGINT GENERATED ALWAYS AS (qty * unit_price) STORED,
  selected_options JSONB DEFAULT '{}'::JSONB, -- {"사이즈":{"label":"4인용","price":450000}}
  custom_note      TEXT,
  created_at       TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_order_items_order   ON app_order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_order_items_product ON app_order_items(product_id);

ALTER TABLE app_order_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "order_items_all" ON app_order_items;
CREATE POLICY "order_items_all" ON app_order_items FOR ALL USING (true) WITH CHECK (true);

-- =============================================================
-- 검증:
--   SELECT column_name FROM information_schema.columns
--   WHERE table_name='app_orgs' AND column_name='edition';
--   SELECT tablename FROM pg_tables WHERE tablename LIKE 'app_product%';
-- =============================================================
