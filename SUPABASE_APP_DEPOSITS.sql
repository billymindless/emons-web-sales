-- 입금 원장 테이블
-- 기업은행 입금 SMS([Web발신] 형식)를 webhook으로 수신해 적재한다. 입금만 저장(출금 제외).
CREATE TABLE IF NOT EXISTS app_deposits (
    id BIGSERIAL PRIMARY KEY,
    txn_at TIMESTAMPTZ NOT NULL,           -- 문자의 YYYY/MM/DD HH:MM
    counterparty TEXT,                     -- 거래처/입금자명 (예 디지털온누리)
    amount NUMERIC NOT NULL,               -- 입금액
    balance NUMERIC,                       -- 잔액
    bank_name TEXT DEFAULT '기업은행',
    account_suffix TEXT,                   -- 계좌 끝 8자리 (매장 매칭 키)
    account_masked TEXT,                   -- 표시용 (예 392***16401011)
    store_name TEXT,                       -- 매칭된 매장 (실패 시 NULL = 미분류)
    source TEXT NOT NULL DEFAULT 'auto_sms', -- 'auto_sms' | 'manual'
    linked_sale_id BIGINT,                 -- 매출 연결 (sales.id)
    raw_message TEXT,                      -- 원문 보관
    dedup_hash TEXT,                       -- 중복 방지 키
    memo TEXT,
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_app_deposits_dedup
    ON app_deposits(dedup_hash) WHERE dedup_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_app_deposits_store_date
    ON app_deposits(store_name, txn_at DESC);
CREATE INDEX IF NOT EXISTS idx_app_deposits_txn_at
    ON app_deposits(txn_at DESC);

ALTER TABLE app_deposits ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_deposits" ON app_deposits;
CREATE POLICY "Allow all app_deposits" ON app_deposits FOR ALL USING (true) WITH CHECK (true);
