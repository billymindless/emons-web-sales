-- =====================================================================
-- app_product_taxonomy 카테고리 개편 마이그레이션 (V2)
-- =====================================================================
-- 변경 내용:
--   1. '자녀방' + '서재_학생' → '자녀방_서재' 로 통합
--   2. '거실장' 카테고리 신규 추가
-- 적용 후 카테고리(10개): 옷장, 식탁, 자녀방_서재, 침대, SSDS침대,
--                        소파, 거실장, 소품, 전시품, 기타
--
-- Supabase SQL 편집기에서 이 파일 전체를 한 번에 실행하세요.
-- 반드시 1) 기존 CHECK 제약 제거 → 2) 데이터 이관 → 3) 새 CHECK 제약 추가 순서로 실행됩니다.
-- (제약을 먼저 풀지 않으면, 옛 제약이 '자녀방_서재' 값을 허용하지 않아 UPDATE 자체가 실패합니다.)
-- =====================================================================

-- 1) 기존 CHECK 제약 제거 (옛 카테고리 목록만 허용하던 제약)
ALTER TABLE app_product_taxonomy DROP CONSTRAINT IF EXISTS app_product_taxonomy_category_check;

-- 2) 기존 분류 데이터 이관: '자녀방' 또는 '서재_학생' → '자녀방_서재'
UPDATE app_product_taxonomy
SET category = '자녀방_서재',
    source = 'override',
    updated_at = now()
WHERE category IN ('자녀방', '서재_학생');

-- 3) 새 카테고리 목록으로 CHECK 제약 추가
ALTER TABLE app_product_taxonomy ADD CONSTRAINT app_product_taxonomy_category_check
  CHECK (category IN (
    '옷장','식탁','자녀방_서재','침대','SSDS침대','소파','거실장','소품','전시품','기타'
  ));
