-- 결제변경 예정·완료 관리 + 사업자별 결제 표기 스키마 마이그레이션
-- 안전: 반복 실행해도 오류 없음 (IF NOT EXISTS)

-- 1) 주문에 결제변경 예정 상태 (yes / no / done) 추가
alter table app_orders add column if not exists payment_change_planned text;
alter table app_orders add column if not exists payment_change_completed_at timestamptz;
alter table app_orders add column if not exists payment_change_completed_by text;
create index if not exists idx_orders_pcr_planned on app_orders(payment_change_planned);

-- 2) 매장별 사업자 목록 (예: 삼산점 = 에몬스울산전시장 / 에몬스리빙울산)
alter table app_stores add column if not exists businesses jsonb default '[]'::jsonb;

-- 3) 외부 결제 파일 업로드 배치와 결제행에 사업자
alter table app_external_pay_batches add column if not exists business_name text;
alter table app_payments add column if not exists business_name text;
create index if not exists idx_payments_business on app_payments(business_name);
