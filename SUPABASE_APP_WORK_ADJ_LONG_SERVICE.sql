-- =====================================================================
-- app_work_adjustments.kind CHECK 제약에 'long_service'(장기근속) 추가
-- =====================================================================
-- 기존: ('reward','meeting','summer_vacation','overtime','etc')
-- 변경: 위 5개 + 'long_service' (장기근속 휴가 차감 신청용)
-- =====================================================================

ALTER TABLE app_work_adjustments
    DROP CONSTRAINT IF EXISTS app_work_adjustments_kind_check;

ALTER TABLE app_work_adjustments
    ADD CONSTRAINT app_work_adjustments_kind_check
    CHECK (kind IN ('reward','meeting','summer_vacation','long_service','overtime','etc'));
