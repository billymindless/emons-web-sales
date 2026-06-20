-- =====================================================================
-- app_documents 첨부파일 컬럼 추가 + Supabase Storage 버킷 안내
-- =====================================================================
-- 실행 전: Supabase 대시보드 → Storage → New Bucket
--   이름: documents  /  Public: ON (공개 버킷)
-- =====================================================================

ALTER TABLE app_documents
  ADD COLUMN IF NOT EXISTS file_url  TEXT,   -- Supabase Storage 공개 URL
  ADD COLUMN IF NOT EXISTS file_name TEXT,   -- 원본 파일명 (표시용)
  ADD COLUMN IF NOT EXISTS file_size BIGINT; -- 파일 크기 (bytes)
