-- =====================================================================
-- 외부 결제내역 대사 (온누리 디지털 / 울산페이 등)
-- - 관리자가 가맹점 포털에서 받은 파일을 업로드 → 파싱 → ERP 결제와 매칭
-- - 재업로드 시 fingerprint UNIQUE 로 중복 적재 방지
-- - 매칭 테이블은 row_id UNIQUE 로 재매칭 중복 방지
-- - 검증 시작일: app_external_pay_settings.verify_from_date (기본 2026-08-01)
-- - orders.entry_source: 신규고객 매출 여부 스탬프 (매칭 대상 필터에 사용)
-- 모든 DDL은 IF NOT EXISTS 가드로 멱등 실행 가능.
-- =====================================================================

-- 0) app_orders.entry_source 컬럼 추가 (없으면)
ALTER TABLE app_orders ADD COLUMN IF NOT EXISTS entry_source TEXT;
CREATE INDEX IF NOT EXISTS idx_app_orders_entry_source
    ON app_orders (entry_source);

-- 1) 설정 (매장별 검증 시작일)
CREATE TABLE IF NOT EXISTS app_external_pay_settings (
    id            BIGSERIAL PRIMARY KEY,
    db_filename   TEXT NOT NULL,
    verify_from_date DATE NOT NULL DEFAULT DATE '2026-08-01',
    updated_by    TEXT,
    updated_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE (db_filename)
);

ALTER TABLE app_external_pay_settings ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_external_pay_settings" ON app_external_pay_settings;
CREATE POLICY "Allow all app_external_pay_settings" ON app_external_pay_settings
    FOR ALL USING (true) WITH CHECK (true);

-- 2) 업로드 배치 (파일 1건 = 배치 1건)
CREATE TABLE IF NOT EXISTS app_external_pay_batches (
    id           BIGSERIAL PRIMARY KEY,
    db_filename  TEXT NOT NULL,
    source       TEXT NOT NULL,         -- 'onnuri' | 'ulsanpay'
    file_name    TEXT,
    parsed_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    skipped_count  INTEGER NOT NULL DEFAULT 0,
    uploaded_by  TEXT,
    uploaded_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ext_batches_db_src
    ON app_external_pay_batches (db_filename, source, uploaded_at DESC);

ALTER TABLE app_external_pay_batches ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_external_pay_batches" ON app_external_pay_batches;
CREATE POLICY "Allow all app_external_pay_batches" ON app_external_pay_batches
    FOR ALL USING (true) WITH CHECK (true);

-- 3) 공식 결제 1행
CREATE TABLE IF NOT EXISTS app_external_pay_rows (
    id            BIGSERIAL PRIMARY KEY,
    batch_id      BIGINT REFERENCES app_external_pay_batches (id) ON DELETE SET NULL,
    db_filename   TEXT NOT NULL,
    source        TEXT NOT NULL,        -- 'onnuri' | 'ulsanpay'
    tx_date       DATE NOT NULL,
    tx_time       TEXT,                 -- 'HH:MM:SS'
    phone_last4   TEXT,                 -- 전화번호 뒤 4자리 (숫자만)
    amount        BIGINT NOT NULL,
    tx_status     TEXT,                 -- 결제완료 / 취소 / ...
    settle_status TEXT,                 -- 정산예정 / 정산중 / 정산완료
    buyer_name_masked TEXT,             -- 조*임 등
    approval_code TEXT,                 -- 온누리 승인번호(있으면), 울산페이 6자리 등
    raw_json      JSONB,
    fingerprint   TEXT NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE (fingerprint)
);

CREATE INDEX IF NOT EXISTS idx_ext_rows_db_src_date
    ON app_external_pay_rows (db_filename, source, tx_date);
CREATE INDEX IF NOT EXISTS idx_ext_rows_match_key
    ON app_external_pay_rows (db_filename, source, tx_date, phone_last4, amount);

ALTER TABLE app_external_pay_rows ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_external_pay_rows" ON app_external_pay_rows;
CREATE POLICY "Allow all app_external_pay_rows" ON app_external_pay_rows
    FOR ALL USING (true) WITH CHECK (true);

-- 4) 공식 행 ↔ ERP 결제 매칭
CREATE TABLE IF NOT EXISTS app_external_pay_matches (
    id            BIGSERIAL PRIMARY KEY,
    db_filename   TEXT NOT NULL,
    source        TEXT NOT NULL,
    row_id        BIGINT NOT NULL REFERENCES app_external_pay_rows (id) ON DELETE CASCADE,
    payment_id    BIGINT,                -- app_payments.id (없으면 NULL: official_only)
    order_id      BIGINT,
    customer_id   BIGINT,
    result_code   TEXT NOT NULL,         -- matched_ok / official_canceled / erp_canceled_official_paid / erp_only / official_only / ambiguous
    note          TEXT,
    matched_by    TEXT,
    matched_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE (row_id)
);

CREATE INDEX IF NOT EXISTS idx_ext_matches_db_src
    ON app_external_pay_matches (db_filename, source, result_code);
CREATE INDEX IF NOT EXISTS idx_ext_matches_payment
    ON app_external_pay_matches (payment_id);

ALTER TABLE app_external_pay_matches ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_external_pay_matches" ON app_external_pay_matches;
CREATE POLICY "Allow all app_external_pay_matches" ON app_external_pay_matches
    FOR ALL USING (true) WITH CHECK (true);
