-- 마이그레이션: app_payments.amount, fee_amount 컬럼을 REAL → BIGINT 변경
-- 적용: Supabase 대시보드 → SQL Editor에서 실행
-- 이유: REAL(float4, 32비트 부동소수점)은 100만원대 금액에서 최대 ±5원 반올림 오류 발생
--       (예: 1,179,165원 → 1,179,160원으로 저장/표시되는 문제)
--       금액은 항상 정수(원 단위)이므로 BIGINT가 정확함

-- 1단계: amount 컬럼을 BIGINT로 변환 (기존 데이터는 반올림해서 정수로 변환)
ALTER TABLE app_payments
  ALTER COLUMN amount TYPE BIGINT USING ROUND(amount)::BIGINT;

-- 2단계: fee_amount 컬럼을 BIGINT로 변환
ALTER TABLE app_payments
  ALTER COLUMN fee_amount TYPE BIGINT USING ROUND(fee_amount)::BIGINT;

-- 확인 쿼리 (선택사항)
-- SELECT id, amount, fee_amount FROM app_payments ORDER BY id DESC LIMIT 20;
