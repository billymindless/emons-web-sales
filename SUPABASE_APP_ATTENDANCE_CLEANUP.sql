-- ERP 근태 모듈 리팩터 (v2.1) — 데이터 정리 마이그레이션
-- 실행 전 조건: 없음. 실행 순서: 앱 재시작 후 자동 실행되거나, 콘솔에서 수동 실행 가능. 멱등.
--
-- 목적:
--  1) 단축근무 신청 모드 폐지 이후에도 남아있던 pending sign='-' 자동생성 신청 정리
--  2) 승인 시 sign='-' 이 무조건 work_type='조퇴' 로 저장되던 버그로 잘못 분류된 log 정리
--  3) 잘못 조퇴로 저장된 log 를 원본 app_work_adjustments.kind 로 재분류

-- ------------------------------------------------------------------
-- 1) 자동 생성 조퇴 로그 삭제 (source_tag prefix note 로 식별)
--    → 다음 로그인 시 다시 재동기화되지 않도록 함께 pending 도 제거.
-- ------------------------------------------------------------------
DELETE FROM app_attendance_logs
WHERE work_type = '조퇴'
  AND note IS NOT NULL
  AND (
        note LIKE '[자동-정기근무]%'
     OR note LIKE '[자동-캘린더]%'
  );

-- ------------------------------------------------------------------
-- 2) 자동 생성 pending sign='-' adjustments 삭제 (단축근무 신청 폐지 잔재)
-- ------------------------------------------------------------------
DELETE FROM app_work_adjustments
WHERE status = 'pending'
  AND sign = '-'
  AND reason IS NOT NULL
  AND (
        reason LIKE '[자동-정기근무]%'
     OR reason LIKE '[자동-캘린더]%'
  );

-- ------------------------------------------------------------------
-- 3) 잘못 분류된 approved 조퇴 로그를 kind 에 맞게 재분류
--    현재는 sign='-' 승인이면 무조건 '조퇴' 로 저장돼서 포상/여름휴가/장기근속/etc(-) 도
--    캘린더에 '조퇴' 배지로 표시되던 문제를 원본 kind 로 되돌린다.
-- ------------------------------------------------------------------
UPDATE app_attendance_logs l
SET work_type = CASE a.kind
    WHEN 'summer_vacation' THEN '여름휴가'
    WHEN 'reward'          THEN '포상'
    WHEN 'long_service'    THEN '장기근속'
    WHEN 'etc'             THEN '특이사항'
    ELSE l.work_type
END
FROM app_work_adjustments a
WHERE l.work_type = '조퇴'
  AND l.employee_name = a.employee_name
  AND l.log_date      = a.target_date
  AND a.status        = 'approved'
  AND a.sign          = '-'
  AND a.kind IN ('summer_vacation', 'reward', 'long_service', 'etc');

-- 참고:
--  - early_leave kind 승인은 그대로 '조퇴' 로 유지 (실제로 조기퇴근이므로).
--  - 필요시 재실행해도 UPDATE 는 조건에 걸리는 행이 없으면 자동 no-op (멱등).
