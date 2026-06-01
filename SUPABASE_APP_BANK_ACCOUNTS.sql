-- 계좌-매장 매핑 테이블
-- 입금 SMS의 마스킹 계좌(예: 392***16401011)에서 끝 8자리(account_suffix)로 매장을 판별한다.
CREATE TABLE IF NOT EXISTS app_bank_accounts (
    id BIGSERIAL PRIMARY KEY,
    bank_name TEXT NOT NULL DEFAULT '기업은행',
    account_suffix TEXT NOT NULL,          -- 매칭 키 (문자에 보이는 끝 8자리, 예 16401011)
    account_masked TEXT,                   -- 표시용 (예 392***16401011)
    account_alias TEXT,                    -- 사용자 지정 별칭
    store_name TEXT NOT NULL,              -- app_stores.store_name과 일치
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_app_bank_accounts_suffix
    ON app_bank_accounts(account_suffix);
CREATE INDEX IF NOT EXISTS idx_app_bank_accounts_store
    ON app_bank_accounts(store_name);

ALTER TABLE app_bank_accounts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_bank_accounts" ON app_bank_accounts;
CREATE POLICY "Allow all app_bank_accounts" ON app_bank_accounts FOR ALL USING (true) WITH CHECK (true);
