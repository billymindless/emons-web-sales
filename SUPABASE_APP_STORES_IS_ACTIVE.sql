-- app_stores.is_active 컬럼 추가 (매장 폐점 처리용)
-- 매장 데이터는 그대로 두고, is_active=FALSE 로 운영 화면에서 숨김 처리합니다.
-- 다른 매장의 운영/데이터에 영향을 주지 않도록 단일 컬럼만 추가합니다.

-- 1) 컬럼 추가 (이미 있으면 무시). NOT NULL 기본값 TRUE 이므로 기존 매장은 모두 활성 상태로 유지됨.
ALTER TABLE app_stores
  ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

-- 2) 폐점 처리 시각(감사용) — 선택 컬럼. 폐점 시점 기록/재개 시 NULL.
ALTER TABLE app_stores
  ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ NULL;

-- 3) 인덱스 (활성 매장 조회 성능)
CREATE INDEX IF NOT EXISTS idx_app_stores_is_active ON app_stores(is_active);

-- 참고 쿼리
-- 매장 폐점:
--   UPDATE app_stores SET is_active = FALSE, closed_at = NOW() WHERE store_name = '양산평산점';
-- 매장 재개:
--   UPDATE app_stores SET is_active = TRUE, closed_at = NULL WHERE store_name = '양산평산점';
-- 활성 매장만 조회:
--   SELECT * FROM app_stores WHERE is_active = TRUE ORDER BY id;
