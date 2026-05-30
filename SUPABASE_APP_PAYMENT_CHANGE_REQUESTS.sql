-- 결제변경 사내 검증(사후 검증) 연동 스키마
-- 결제는 매출관리에서 즉시 반영되고, 본 테이블/컬럼은 사내 업무 검증(완료/미결) 추적용.
-- 모든 DDL은 멱등 (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)

-- ─────────────────────────────────────────────────────────────────────
-- 1) app_tasks 검증 컬럼 추가 (결제변경 검증 태스크용)
-- ─────────────────────────────────────────────────────────────────────
ALTER TABLE app_tasks ADD COLUMN IF NOT EXISTS task_type TEXT DEFAULT 'general';
ALTER TABLE app_tasks ADD COLUMN IF NOT EXISTS verify_status TEXT;
ALTER TABLE app_tasks ADD COLUMN IF NOT EXISTS verified_by TEXT;
ALTER TABLE app_tasks ADD COLUMN IF NOT EXISTS verified_at TIMESTAMPTZ;
ALTER TABLE app_tasks ADD COLUMN IF NOT EXISTS verify_note TEXT;

CREATE INDEX IF NOT EXISTS idx_app_tasks_verify
    ON app_tasks(task_type, verify_status);

-- ─────────────────────────────────────────────────────────────────────
-- 2) app_payment_change_requests : 결제변경 검증 메타
--    task_id로 app_tasks와 1:1 연결. 원본/변경 결제 정보 스냅샷 보관.
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_payment_change_requests (
    id               BIGSERIAL PRIMARY KEY,
    task_id          BIGINT NOT NULL REFERENCES app_tasks(id) ON DELETE CASCADE,
    db_filename      TEXT NOT NULL,
    sale_id          BIGINT NOT NULL,          -- app_orders.id (= app_payment_history.sale_id)
    payment_id       BIGINT,                   -- app_payments.id (NULL이면 신규 결제 건)
    customer_name    TEXT,
    change_type      TEXT NOT NULL,
    -- 'refund' | 'cancel_card' | 'cancel_transfer' | 'method_change' | 'onnuri_change'
    original_amount  BIGINT,
    original_method  TEXT,
    original_onnuri  TEXT,
    new_amount       BIGINT,
    new_method       TEXT,
    new_onnuri       TEXT,
    reason           TEXT NOT NULL,
    created_by       TEXT NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_app_pcr_task ON app_payment_change_requests(task_id);
CREATE INDEX IF NOT EXISTS idx_app_pcr_store ON app_payment_change_requests(db_filename);
CREATE INDEX IF NOT EXISTS idx_app_pcr_sale ON app_payment_change_requests(db_filename, sale_id);

-- ─────────────────────────────────────────────────────────────────────
-- RLS
-- ─────────────────────────────────────────────────────────────────────
ALTER TABLE app_payment_change_requests ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_payment_change_requests" ON app_payment_change_requests;
CREATE POLICY "Allow all app_payment_change_requests" ON app_payment_change_requests FOR ALL USING (true) WITH CHECK (true);
