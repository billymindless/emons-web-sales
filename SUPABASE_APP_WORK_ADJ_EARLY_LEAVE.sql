-- =====================================================================
-- app_work_adjustments.kind CHECK 제약에 'early_leave'(조기퇴근) 추가
-- =====================================================================
-- 기존: ('reward','meeting','summer_vacation','long_service','overtime','etc')
-- 변경: 위 6개 + 'early_leave' (조기퇴근 신청용)
-- 근태 리팩터에서 조기퇴근을 별도 kind 로 분리했으나 DB 제약이 누락되어 있었음.
-- =====================================================================

ALTER TABLE app_work_adjustments
    DROP CONSTRAINT IF EXISTS app_work_adjustments_kind_check;

ALTER TABLE app_work_adjustments
    ADD CONSTRAINT app_work_adjustments_kind_check
    CHECK (kind IN ('reward','meeting','summer_vacation','long_service','overtime','early_leave','etc'));
