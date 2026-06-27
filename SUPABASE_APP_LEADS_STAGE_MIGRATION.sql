-- ============================================================
-- app_leads 리드 단계(lead_stage) 재정의 마이그레이션
-- 실행 순서: STEP 1 확인 → STEP 2 기존 제약 DROP → STEP 3 데이터 UPDATE
--           → STEP 4 새 CHECK 제약 ADD → STEP 5 검증
-- ⚠️ 반드시 위 순서대로 실행하세요.
-- ============================================================

-- ── STEP 1: 마이그레이션 전 현황 확인 ──────────────────────────
SELECT lead_stage, COUNT(*) AS 건수
FROM app_leads
GROUP BY lead_stage
ORDER BY lead_stage;


-- ── STEP 2: 기존 CHECK 제약 제거 (UPDATE 전에 필수) ────────────
ALTER TABLE app_leads
    DROP CONSTRAINT IF EXISTS app_leads_lead_stage_check;


-- ── STEP 3: 기존 데이터 새 단계값으로 마이그레이션 ──────────────
UPDATE app_leads SET lead_stage = '1_신규'    WHERE lead_stage = '1_신규유입';
UPDATE app_leads SET lead_stage = '2_상담중'   WHERE lead_stage = '2_자료발송';
UPDATE app_leads SET lead_stage = '3_견적발송' WHERE lead_stage = '3_매장방문';
-- 4_계약완료는 값 유지 (변경 없음)
UPDATE app_leads SET lead_stage = '5_실패'    WHERE lead_stage = '5_계약실패';


-- ── STEP 4: 새 CHECK 제약 추가 ────────────────────────────────
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


-- ── STEP 5: 마이그레이션 결과 검증 ────────────────────────────
SELECT lead_stage, COUNT(*) AS 건수
FROM app_leads
GROUP BY lead_stage
ORDER BY lead_stage;

-- 결과에 '1_신규유입', '2_자료발송', '3_매장방문', '5_계약실패' 가 없으면 완료.
