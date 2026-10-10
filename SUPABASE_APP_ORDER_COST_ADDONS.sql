-- 추가 사은품 단가와, 주문에 실제로 고른 방수커버·사은품 줄.
-- Supabase 대시보드 → SQL Editor에서 실행하세요.
-- 앱은 이 파일을 실행하지 않습니다. 다시 실행해도 기존 행은 지우지 않습니다.

CREATE TABLE IF NOT EXISTS app_extra_gift_items (
    id          BIGSERIAL PRIMARY KEY,
    label       TEXT NOT NULL,
    amount      INTEGER NOT NULL,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    sort_order  INTEGER NOT NULL DEFAULT 0,
    updated_by  TEXT,
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS app_order_cost_addons (
    id           BIGSERIAL PRIMARY KEY,
    db_filename  TEXT NOT NULL,
    order_id     BIGINT NOT NULL,
    kind         TEXT NOT NULL,
    item_code    TEXT NOT NULL,
    item_label   TEXT NOT NULL,
    unit_amount  INTEGER NOT NULL,
    qty          INTEGER NOT NULL DEFAULT 1,
    created_at   TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT app_order_cost_addons_kind_check CHECK (kind IN ('cover', 'gift'))
);

CREATE INDEX IF NOT EXISTS idx_app_order_cost_addons_order
    ON app_order_cost_addons (db_filename, order_id);

ALTER TABLE app_extra_gift_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_extra_gift_items" ON app_extra_gift_items;
CREATE POLICY "Allow all app_extra_gift_items" ON app_extra_gift_items
    FOR ALL USING (true) WITH CHECK (true);

ALTER TABLE app_order_cost_addons ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_order_cost_addons" ON app_order_cost_addons;
CREATE POLICY "Allow all app_order_cost_addons" ON app_order_cost_addons
    FOR ALL USING (true) WITH CHECK (true);
