-- =====================================================================
-- app_product_taxonomy : 품목명 → 대분류 카테고리 매핑
-- =====================================================================
-- 목적: app_order_items.product_name 을 대분류 카테고리로 자동 분류
--       (Gemini 배치) + 수동 override 관리.
--   - 동일 product_name 은 전 매장에서 하나의 분류를 공유한다.
--   - source 는 'gemini' (자동) / 'manual' (관리자 최초 입력) /
--     'override' (관리자 재분류) 를 구분한다.
-- =====================================================================

CREATE TABLE IF NOT EXISTS app_product_taxonomy (
  product_name TEXT PRIMARY KEY,
  category     TEXT NOT NULL,
  source       TEXT NOT NULL DEFAULT 'gemini',
  confidence   REAL,
  updated_by   TEXT,
  updated_at   TIMESTAMPTZ DEFAULT now(),
  CHECK (category IN (
    '옷장','식탁','자녀방','침대','SSDS침대','서재_학생','소파','소품','전시품','기타'
  )),
  CHECK (source IN ('gemini','manual','override'))
);

CREATE INDEX IF NOT EXISTS idx_app_product_taxonomy_category ON app_product_taxonomy(category);
CREATE INDEX IF NOT EXISTS idx_app_product_taxonomy_source   ON app_product_taxonomy(source);

COMMENT ON TABLE  app_product_taxonomy         IS '품목명(app_order_items.product_name) → 대분류 매핑. 전 매장 공유.';
COMMENT ON COLUMN app_product_taxonomy.source  IS 'gemini | manual | override';
COMMENT ON COLUMN app_product_taxonomy.confidence IS 'Gemini 자동 분류 시 0.0~1.0 확신도 (수동일 경우 NULL).';

ALTER TABLE app_product_taxonomy ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow all app_product_taxonomy" ON app_product_taxonomy;
CREATE POLICY "Allow all app_product_taxonomy" ON app_product_taxonomy FOR ALL USING (true) WITH CHECK (true);
