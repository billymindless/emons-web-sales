-- ============================================================
-- app_leads — employee_names 컬럼 추가
--
-- 매출 등록(app_orders.employee_names)과 동일한 패턴으로,
-- 리드 1건에 여러 담당 직원을 쉼표 구분 텍스트로 저장한다.
-- 1/n 실적 분배 계산 시 app_orders와 동일한 로직을 재사용 가능.
--
-- 기존 assigned_employee_id(INT FK)는 그대로 유지하되,
-- 첫 번째 선택 직원의 ID를 백업/조인용으로 채워둔다.
-- ============================================================

ALTER TABLE app_leads
    ADD COLUMN IF NOT EXISTS employee_names TEXT;

-- 검색 인덱스 (LIKE 검색용 trigram 또는 단순 btree)
CREATE INDEX IF NOT EXISTS app_leads_employee_names_idx
    ON app_leads (employee_names);


-- ── 기존 데이터 마이그레이션: assigned_employee_id → employee_names ─────
-- assigned_employee_id가 있고 employee_names가 NULL인 행만 채움.
UPDATE app_leads l
SET employee_names = u.name
FROM app_users u
WHERE l.assigned_employee_id = u.id
  AND (l.employee_names IS NULL OR l.employee_names = '');


-- 결과 확인
SELECT
    COUNT(*) FILTER (WHERE employee_names IS NOT NULL AND employee_names <> '') AS with_names,
    COUNT(*) FILTER (WHERE employee_names IS NULL OR employee_names = '')       AS no_names,
    COUNT(*)                                                                     AS total
FROM app_leads;
