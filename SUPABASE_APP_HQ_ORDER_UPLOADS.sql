-- =====================================================================
-- app_hq_order_uploads / app_hq_order_snapshots
-- 본사 ERP "주문조회(대)_emsf" 엑셀 주기 업로드용.
--   - uploads: 업로드 이력 (파일 단위)
--   - snapshots: 출고번호 단위 최신 스냅샷 + 이전값 (감/증액·취소 감지)
-- 규칙:
--   - 주문/결제/매출 원본은 절대 변경하지 않음. 이 두 테이블만 upsert.
--   - (db_filename, ship_number, order_date) UNIQUE — 출고번호가 매장별 순환
--     시퀀스이므로 시간이 지나면 재사용됨. 등록일까지 조합해야 실질적 유일성 확보.
-- =====================================================================

CREATE TABLE IF NOT EXISTS app_hq_order_uploads (
  id                BIGSERIAL PRIMARY KEY,
  db_filename       TEXT NOT NULL,
  filename          TEXT,
  uploaded_by       TEXT,
  row_count         INTEGER DEFAULT 0,
  new_count         INTEGER DEFAULT 0,
  dup_count         INTEGER DEFAULT 0,
  revised_count     INTEGER DEFAULT 0,
  missing_count     INTEGER DEFAULT 0,
  hq_amount_sum     BIGINT  DEFAULT 0,
  order_date_min    DATE,
  order_date_max    DATE,
  note              TEXT,
  uploaded_at       TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_app_hq_order_uploads_store
  ON app_hq_order_uploads(db_filename, uploaded_at DESC);


CREATE TABLE IF NOT EXISTS app_hq_order_snapshots (
  id                 BIGSERIAL PRIMARY KEY,
  db_filename        TEXT NOT NULL,
  ship_number        TEXT NOT NULL,          -- 본사 출고번호 (매장별 순환. UNIQUE 는 (db_filename, ship_number))
  contract_no        TEXT,                    -- 판매계약번호
  phone1_digits      TEXT,                    -- 전화1 숫자만
  customer_name      TEXT,
  order_date         DATE,                    -- 등록일 (계약일 fallback)
  contract_date      DATE,                    -- 계약일
  delivery_date      DATE,
  ship_date          DATE,                    -- 출고일
  order_amount       BIGINT DEFAULT 0,        -- 본사 주문금액 (VAT 별도)
  vat                BIGINT DEFAULT 0,
  total_amount_hq    BIGINT DEFAULT 0,        -- 합계 (주문금액+VAT). 매장 판매가와 다름
  order_status       TEXT,
  order_kind         TEXT,
  employee_names     TEXT,
  outlet             TEXT,                    -- 아울렛 여부
  is_display         BOOLEAN DEFAULT FALSE,   -- 리빙(법)·매장분 = 전시품(고객 판매)
  is_store_display   BOOLEAN DEFAULT FALSE,   -- 매장 자체 전시분 (판매 아님, 대사 대상 제외)
  merge_target_order_id BIGINT,               -- hq_only 스냅샷을 기존 앱 주문의 본사 원가에 합산 연결 (분할 출고 등)
  source_upload_id   BIGINT REFERENCES app_hq_order_uploads(id) ON DELETE SET NULL,
  prev_order_amount  BIGINT,                  -- 직전 값 (revised 감지용)
  prev_order_status  TEXT,
  prev_uploaded_at   TIMESTAMPTZ,
  first_seen_at      TIMESTAMPTZ DEFAULT now(),
  last_seen_at       TIMESTAMPTZ DEFAULT now()
);

-- 실질 유일성: (매장, 출고번호, 등록일). 출고번호가 매장별 순환이라 등록일 조합 필수.
CREATE UNIQUE INDEX IF NOT EXISTS uniq_hq_ship_date
  ON app_hq_order_snapshots(db_filename, ship_number, order_date);

CREATE INDEX IF NOT EXISTS idx_app_hq_snap_store_date
  ON app_hq_order_snapshots(db_filename, order_date);
CREATE INDEX IF NOT EXISTS idx_app_hq_snap_phone
  ON app_hq_order_snapshots(db_filename, phone1_digits);
CREATE INDEX IF NOT EXISTS idx_app_hq_snap_upload
  ON app_hq_order_snapshots(source_upload_id);

COMMENT ON TABLE app_hq_order_uploads     IS '본사 ERP 주문조회(대) 엑셀 업로드 이력 (파일 단위).';
COMMENT ON TABLE app_hq_order_snapshots   IS '본사 출고 단위 최신 스냅샷. (db_filename, ship_number) UNIQUE. 원본 주문 무변경.';
COMMENT ON COLUMN app_hq_order_snapshots.prev_order_amount IS '직전 업로드의 order_amount. NULL 이면 최초. 다르면 revised.';
COMMENT ON COLUMN app_hq_order_snapshots.total_amount_hq   IS '본사 합계 = 주문금액+VAT. 매장 판매가(app_orders.total_amount)와 정의 다름.';
COMMENT ON COLUMN app_hq_order_snapshots.is_store_display  IS '매장 자체 전시분(판매 아님). true 면 원가 대사·앱 주문 매칭에서 제외.';

-- 기존 배포 환경 마이그레이션 (컬럼 없는 경우에만 추가)
ALTER TABLE app_hq_order_snapshots ADD COLUMN IF NOT EXISTS is_store_display BOOLEAN DEFAULT FALSE;
ALTER TABLE app_hq_order_snapshots ADD COLUMN IF NOT EXISTS merge_target_order_id BIGINT;

CREATE INDEX IF NOT EXISTS idx_app_hq_snap_merge_target
  ON app_hq_order_snapshots(db_filename, merge_target_order_id)
  WHERE merge_target_order_id IS NOT NULL;

COMMENT ON COLUMN app_hq_order_snapshots.merge_target_order_id
  IS '분할 출고 등: hq_only 스냅샷을 기존 앱 주문(app_orders.id)의 본사 원가에 합산 연결. NULL 이면 미합산.';

ALTER TABLE app_hq_order_uploads   ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_hq_order_snapshots ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow all app_hq_order_uploads"   ON app_hq_order_uploads;
DROP POLICY IF EXISTS "Allow all app_hq_order_snapshots" ON app_hq_order_snapshots;
CREATE POLICY "Allow all app_hq_order_uploads"
  ON app_hq_order_uploads FOR ALL USING (true) WITH CHECK (true);
CREATE POLICY "Allow all app_hq_order_snapshots"
  ON app_hq_order_snapshots FOR ALL USING (true) WITH CHECK (true);
