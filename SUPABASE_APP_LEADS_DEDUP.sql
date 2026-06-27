-- ============================================================
-- app_leads 중복 리드 정리
-- 기준: 같은 store_name + phone 조합에서 id가 가장 작은 행(최초 등록)만 보존
-- 실행 전: 아래 STEP 1 조회로 삭제 대상 확인 후 STEP 2 삭제 실행
-- ============================================================


-- ── STEP 1: 중복 현황 조회 (삭제 전 반드시 확인) ──────────────────

SELECT
    phone,
    store_name,
    COUNT(*)          AS 중복수,
    MIN(id)           AS 보존_id,
    ARRAY_AGG(id ORDER BY id) AS 전체_id_목록,
    MIN(created_at)::date AS 최초등록일
FROM app_leads
GROUP BY phone, store_name
HAVING COUNT(*) > 1
ORDER BY 중복수 DESC, phone;


-- ── STEP 2: 중복 삭제 (id가 가장 작은 1건만 남기고 나머지 삭제) ────

DELETE FROM app_leads
WHERE id IN (
    SELECT id
    FROM (
        SELECT
            id,
            ROW_NUMBER() OVER (
                PARTITION BY phone, store_name
                ORDER BY id ASC   -- id 오름차순 = 가장 먼저 등록된 행 보존
            ) AS rn
        FROM app_leads
    ) ranked
    WHERE rn > 1
);


-- ── STEP 3: 삭제 후 잔여 중복 없는지 확인 ───────────────────────────

SELECT
    phone,
    store_name,
    COUNT(*) AS 건수
FROM app_leads
GROUP BY phone, store_name
HAVING COUNT(*) > 1;

-- 결과가 0행이면 정리 완료.


-- ── STEP 4: 향후 중복 방지 UNIQUE 제약 추가 (권장) ──────────────────
-- 중복 정리 완료 후 아래 제약을 추가하면 코드 차원 외에 DB 차원에서도 방어됩니다.

ALTER TABLE app_leads
    ADD CONSTRAINT app_leads_phone_store_unique
    UNIQUE (phone, store_name);
