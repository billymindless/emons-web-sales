-- 고객 계약서 보관 스키마
-- 종이 계약서를 휴대폰으로 찍어 그 고객에 붙이고, PC에서 세로 A4 PDF로 다시 봅니다.
-- 저장 규격: EXIF 적용 후 세로 A4, 긴 변 2339px, JPEG q=80.
-- 모든 DDL은 멱등 (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).
--
-- 사용 방법:
--   1) Supabase 대시보드 > Storage 에서 비공개 버킷 'customer-contracts' 를 만든다.
--      (Public = off, File size limit = 10MB 이상 권장)
--   2) 이 파일을 SQL Editor 에서 실행한다.

-- ─────────────────────────────────────────────────────────────────────
-- app_customer_contracts : 고객 1인 당 N 장 (page_no 순서)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_customer_contracts (
    id BIGSERIAL PRIMARY KEY,
    db_filename TEXT NOT NULL,
    customer_id BIGINT NOT NULL,
    order_id BIGINT NULL,
    page_no INTEGER NOT NULL,
    storage_path TEXT NOT NULL,          -- {db_filename}/{customer_id}/{uuid}.jpg
    thumb_path TEXT NULL,                -- 긴 변 480px JPEG
    mime_type TEXT NOT NULL DEFAULT 'image/jpeg',
    byte_size BIGINT NOT NULL DEFAULT 0,
    uploaded_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ocr_status TEXT NOT NULL DEFAULT 'skipped'
        CHECK (ocr_status IN ('skipped','pending','done','failed')),
    ocr_text TEXT NULL
);
CREATE INDEX IF NOT EXISTS idx_cust_contracts_cust
    ON app_customer_contracts(db_filename, customer_id, page_no);
CREATE INDEX IF NOT EXISTS idx_cust_contracts_created
    ON app_customer_contracts(db_filename, created_at DESC);

-- ─────────────────────────────────────────────────────────────────────
-- RLS (앱 레벨에서 가시성 필터링 — 기존 테이블들과 동일 정책)
-- ─────────────────────────────────────────────────────────────────────
ALTER TABLE app_customer_contracts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_customer_contracts" ON app_customer_contracts;
CREATE POLICY "Allow all app_customer_contracts"
    ON app_customer_contracts FOR ALL USING (true) WITH CHECK (true);

COMMENT ON TABLE  app_customer_contracts IS '고객별 종이 계약서 페이지. storage_path 는 customer-contracts 버킷 경로.';
COMMENT ON COLUMN app_customer_contracts.page_no   IS '같은 고객 내에서 1부터 증가하는 페이지 번호. 다시 찍기는 같은 번호로 교체.';
COMMENT ON COLUMN app_customer_contracts.ocr_status IS '1차는 skipped. 2차에서 OCR 큐에 넣고 결과를 ocr_text 에 저장.';
