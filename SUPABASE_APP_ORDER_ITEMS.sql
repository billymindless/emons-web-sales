-- =====================================================================
-- app_order_items : 주문 라인 아이템 (매입 원장 상세)
-- =====================================================================
-- 목적: 매입 원장(엑셀)에서 임포트되는 품목별 세부 정보를 저장한다.
--   - 한 주문(app_orders 1건)에 여러 라인 아이템이 대응 (1:N).
--   - 매입 원장의 (출고번호) 는 그룹핑용, 여기서는 참조 정보로 보관.
--   - 매출 총액(app_orders.total_amount) 은 라인 합 × (1 / (1 - margin))
--     로 역산되어 이미 저장되어 있으며, 라인 아이템은 원가/부가세/합계 원본.
-- =====================================================================

CREATE TABLE IF NOT EXISTS app_order_items (
  id              BIGSERIAL PRIMARY KEY,
  order_id        BIGINT NOT NULL REFERENCES app_orders(id) ON DELETE CASCADE,
  db_filename     TEXT,
  product_code    TEXT,
  product_name    TEXT,
  quantity        INTEGER  DEFAULT 1,
  unit_cost       BIGINT   DEFAULT 0,   -- 출고가 (라인 단가, 원가)
  line_cost       BIGINT   DEFAULT 0,   -- 주문금액 (수량 × 단가 - 라인할인, 매입 원장 원본)
  vat             BIGINT   DEFAULT 0,   -- 부가세
  line_total      BIGINT   DEFAULT 0,   -- 합계 (line_cost + vat)
  item_note       TEXT,
  ship_number     TEXT,                 -- 출고번호 (그룹핑 원본, 매장별 순환 시퀀스)
  order_kind      TEXT,                 -- 주문/매장분/회수 등 원본 구분
  import_source   TEXT,
  created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_app_order_items_order_id ON app_order_items(order_id);
CREATE INDEX IF NOT EXISTS idx_app_order_items_product_code ON app_order_items(product_code);
CREATE INDEX IF NOT EXISTS idx_app_order_items_ship_number ON app_order_items(db_filename, ship_number);

COMMENT ON TABLE app_order_items IS '주문 라인 아이템 (매입 원장 임포트). app_orders 1건에 여러 행.';
COMMENT ON COLUMN app_order_items.order_id IS 'app_orders.id 외래키. 주문 삭제 시 함께 삭제.';
COMMENT ON COLUMN app_order_items.unit_cost IS '출고가 (라인 단가, 원가 기준). 매입 원장 원본.';
COMMENT ON COLUMN app_order_items.line_cost IS '주문금액 = 단가 × 수량 - 할인. 매입 원장 원본, 부가세 별도.';
COMMENT ON COLUMN app_order_items.ship_number IS '매입 원장의 출고번호. 매장별 순환 시퀀스이므로 유일 ID 아님.';

ALTER TABLE app_order_items ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow all app_order_items" ON app_order_items;
CREATE POLICY "Allow all app_order_items" ON app_order_items FOR ALL USING (true) WITH CHECK (true);
