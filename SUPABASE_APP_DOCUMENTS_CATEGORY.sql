-- =====================================================================
-- app_documents — 카테고리 / 하위카테고리 컬럼 추가
-- Supabase SQL Editor에서 실행하세요.
-- =====================================================================

ALTER TABLE app_documents
  ADD COLUMN IF NOT EXISTS category    TEXT DEFAULT '미분류',
  ADD COLUMN IF NOT EXISTS subcategory TEXT;

-- 기존 자료를 '미분류'로 초기화
UPDATE app_documents
SET category = '미분류'
WHERE category IS NULL;
