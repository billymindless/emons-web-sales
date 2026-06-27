-- ============================================================
-- app_leads 다중 담당직원 + 담당자(고객사 연락 담당자) 컬럼 추가
-- ============================================================

-- 1) 고객사의 담당 연락자 이름 (채널톡/오프라인에서 별도 입력)
ALTER TABLE app_leads
    ADD COLUMN IF NOT EXISTS contact_person TEXT;

-- 2) 추가 담당직원 IDs (기존 assigned_employee_id 외 추가)
--    JSON 배열로 저장: [2, 5, 7]
ALTER TABLE app_leads
    ADD COLUMN IF NOT EXISTS extra_assignee_ids JSONB DEFAULT '[]'::jsonb;

-- 실행 확인
SELECT id, name, contact_person, assigned_employee_id, extra_assignee_ids
FROM app_leads
LIMIT 5;
