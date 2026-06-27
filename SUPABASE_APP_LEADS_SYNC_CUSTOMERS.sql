-- ============================================================
-- 기존 고객(app_customers)과 리드(app_leads) 소급 동기화
-- app_customers.phone1/phone2 → 숫자 정규화 후 app_leads.phone과 매칭
-- ============================================================

-- 1) 현황 확인 (실행 전 반드시 확인)
SELECT
    l.id          AS lead_id,
    l.name        AS lead_name,
    l.phone       AS lead_phone,
    l.lead_stage,
    c.id          AS customer_id,
    c.name        AS customer_name,
    c.phone1      AS customer_phone1,
    o.employee_names
FROM app_leads l
JOIN app_customers c
    ON regexp_replace(l.phone, '[^0-9]', '', 'g')
     = regexp_replace(c.phone1, '[^0-9]', '', 'g')
LEFT JOIN LATERAL (
    SELECT employee_names
    FROM app_orders
    WHERE customer_id = c.id
    ORDER BY id DESC
    LIMIT 1
) o ON true
WHERE l.lead_stage NOT IN ('4_계약완료', '5_실패', '6_보류')
ORDER BY l.created_at DESC;


-- 2) phone2로도 추가 확인
SELECT
    l.id AS lead_id, l.name AS lead_name, l.phone AS lead_phone,
    l.lead_stage, c.id AS customer_id, c.phone2 AS customer_phone2
FROM app_leads l
JOIN app_customers c
    ON regexp_replace(l.phone, '[^0-9]', '', 'g')
     = regexp_replace(c.phone2, '[^0-9]', '', 'g')
WHERE l.lead_stage NOT IN ('4_계약완료', '5_실패', '6_보류')
  AND c.phone2 IS NOT NULL;


-- 3) 실제 업데이트: phone1 매칭 → 4_계약완료 + employee_names 첫 번째 담당자 반영
UPDATE app_leads l
SET
    lead_stage   = '4_계약완료',
    converted_at = NOW(),
    updated_at   = NOW(),
    -- 담당직원이 비어있는 경우에만 주문 employee_names 첫 번째 이름으로 채움
    assigned_employee_id = CASE
        WHEN l.assigned_employee_id IS NULL THEN (
            SELECT u.id
            FROM app_orders o
            JOIN app_users u
                ON u.name = split_part(o.employee_names, ',', 1)
            WHERE o.customer_id = c.id
            ORDER BY o.id DESC
            LIMIT 1
        )
        ELSE l.assigned_employee_id
    END
FROM app_customers c
WHERE
    regexp_replace(l.phone, '[^0-9]', '', 'g')
  = regexp_replace(c.phone1, '[^0-9]', '', 'g')
  AND l.lead_stage NOT IN ('4_계약완료', '5_실패', '6_보류');


-- 4) phone2 매칭도 동일하게
UPDATE app_leads l
SET
    lead_stage   = '4_계약완료',
    converted_at = NOW(),
    updated_at   = NOW(),
    assigned_employee_id = CASE
        WHEN l.assigned_employee_id IS NULL THEN (
            SELECT u.id
            FROM app_orders o
            JOIN app_users u
                ON u.name = split_part(o.employee_names, ',', 1)
            WHERE o.customer_id = c.id
            ORDER BY o.id DESC
            LIMIT 1
        )
        ELSE l.assigned_employee_id
    END
FROM app_customers c
WHERE
    regexp_replace(l.phone, '[^0-9]', '', 'g')
  = regexp_replace(c.phone2, '[^0-9]', '', 'g')
  AND c.phone2 IS NOT NULL
  AND l.lead_stage NOT IN ('4_계약완료', '5_실패', '6_보류');


-- 5) 결과 확인
SELECT COUNT(*) AS synced_to_contract FROM app_leads WHERE lead_stage = '4_계약완료';
