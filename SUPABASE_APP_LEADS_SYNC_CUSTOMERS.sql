-- ============================================================
-- 기존 고객(app_customers)과 리드(app_leads) 소급 동기화
-- app_customers에 등록된 전화번호를 가진 리드를 4_계약완료로 업데이트
-- ============================================================

-- 1) 현황 확인 (실행 전 반드시 확인)
SELECT
    l.id,
    l.name,
    l.phone,
    l.lead_stage,
    c.id AS customer_id
FROM app_leads l
JOIN app_customers c
    ON regexp_replace(l.phone, '[^0-9]', '', 'g')
     = regexp_replace(c.phone, '[^0-9]', '', 'g')
WHERE l.lead_stage NOT IN ('4_계약완료', '5_실패', '6_보류')
ORDER BY l.created_at DESC;

-- 2) 실제 업데이트 (위 결과 확인 후 아래 실행)
UPDATE app_leads l
SET
    lead_stage        = '4_계약완료',
    converted_at      = NOW(),
    updated_at        = NOW()
FROM app_customers c
WHERE
    regexp_replace(l.phone, '[^0-9]', '', 'g')
  = regexp_replace(c.phone, '[^0-9]', '', 'g')
  AND l.lead_stage NOT IN ('4_계약완료', '5_실패', '6_보류');

-- 3) 결과 확인
SELECT COUNT(*) AS synced_count
FROM app_leads
WHERE lead_stage = '4_계약완료';
