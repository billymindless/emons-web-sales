-- ============================================================
-- app_leads — customer_type 고객 유형 분류 + 최소 데이터 정책 마이그레이션
--
-- 변경 사항:
--   1. customer_type 컬럼 추가 (신규/기존구매/AS/재상담)
--   2. classification_memo, classified_by, classified_at 추가
--   3. last_contact_at 추가 (재유입 추적용)
--   4. lead_stage CHECK 제약에 '2_상담중' 포함 보장
--   5. contact_person 컬럼 제거 (고객사 담당자 → 단순화)
--   6. extra_assignee_ids 컬럼 제거 (복수 담당자 → 단일 담당자 1명)
--   7. assigned_store 컬럼 제거 (store_name 단일 컬럼으로 통일)
--
-- ⚠️ 반드시 STEP 순서대로 실행하세요. 컬럼 DROP은 데이터 손실이 발생합니다.
-- ============================================================

-- ── STEP 1: 신규 컬럼 추가 ────────────────────────────────────

ALTER TABLE app_leads
    ADD COLUMN IF NOT EXISTS customer_type TEXT DEFAULT '신규잠재고객';

-- ENUM 제약 (NULL 허용해야 기존 행과 호환)
ALTER TABLE app_leads
    DROP CONSTRAINT IF EXISTS app_leads_customer_type_check;
ALTER TABLE app_leads
    ADD CONSTRAINT app_leads_customer_type_check
    CHECK (customer_type IS NULL OR customer_type IN (
        '신규잠재고객',
        '기존구매고객_DB외',
        'AS요청',
        '재상담'
    ));

ALTER TABLE app_leads
    ADD COLUMN IF NOT EXISTS classification_memo TEXT;

ALTER TABLE app_leads
    ADD COLUMN IF NOT EXISTS classified_by TEXT;

ALTER TABLE app_leads
    ADD COLUMN IF NOT EXISTS classified_at TIMESTAMPTZ;

ALTER TABLE app_leads
    ADD COLUMN IF NOT EXISTS last_contact_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS app_leads_customer_type_idx
    ON app_leads (customer_type);

CREATE INDEX IF NOT EXISTS app_leads_last_contact_idx
    ON app_leads (last_contact_at DESC);


-- ── STEP 2: lead_stage ENUM에 '2_상담중' 포함 보장 ─────────────
-- (이미 STAGE_MIGRATION에서 추가됐을 수 있으나 멱등성 보장)

ALTER TABLE app_leads
    DROP CONSTRAINT IF EXISTS app_leads_lead_stage_check;
ALTER TABLE app_leads
    ADD CONSTRAINT app_leads_lead_stage_check
    CHECK (lead_stage IN (
        '1_신규',
        '2_상담중',
        '3_견적발송',
        '4_계약완료',
        '5_실패',
        '6_보류'
    ));


-- ── STEP 3: 불필요 컬럼 제거 (데이터 손실 발생 — 신중) ──────────
-- 기존 contact_person, extra_assignee_ids, assigned_store 데이터를 폐기.
-- 필요 시 STEP 3 실행 전 별도 백업 SELECT를 권장:
--   SELECT id, contact_person, extra_assignee_ids, assigned_store FROM app_leads
--    WHERE contact_person IS NOT NULL OR extra_assignee_ids != '[]'::jsonb;

ALTER TABLE app_leads DROP COLUMN IF EXISTS contact_person;
ALTER TABLE app_leads DROP COLUMN IF EXISTS extra_assignee_ids;
ALTER TABLE app_leads DROP COLUMN IF EXISTS assigned_store;


-- ── STEP 4: 마이그레이션 결과 확인 ─────────────────────────────

SELECT customer_type, COUNT(*) AS 건수
FROM app_leads
GROUP BY customer_type
ORDER BY customer_type NULLS FIRST;

-- 컬럼 구조 확인
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_name = 'app_leads'
ORDER BY ordinal_position;
