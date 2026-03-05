-- app_payment_history: 결제 변경/취소 이력 테이블 (Supabase)
-- Streamlit Cloud 배포 후 Supabase SQL Editor에서 실행

-- 기존 테이블이 잘못 생성된 경우 완전히 삭제 후 재생성
DROP TABLE IF EXISTS app_payment_history;

CREATE TABLE app_payment_history (
    id                  BIGSERIAL PRIMARY KEY,
    db_filename         TEXT NOT NULL,
    sale_id             BIGINT NOT NULL,
    customer_name       TEXT,
    action_type         TEXT NOT NULL,
    old_payment_data    JSONB,
    new_payment_data    JSONB,
    reason              TEXT NOT NULL,
    changed_by          TEXT NOT NULL,
    changed_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    receipt_image_path  TEXT
);

-- 인덱스
CREATE INDEX idx_app_payment_history_db_filename ON app_payment_history (db_filename);
CREATE INDEX idx_app_payment_history_sale_id     ON app_payment_history (sale_id);

-- RLS 활성화
ALTER TABLE app_payment_history ENABLE ROW LEVEL SECURITY;

-- 기존 정책 제거 후 재생성
DROP POLICY IF EXISTS "allow_all_app_payment_history" ON app_payment_history;

CREATE POLICY "allow_all_app_payment_history"
    ON app_payment_history
    FOR ALL
    USING (true)
    WITH CHECK (true);
