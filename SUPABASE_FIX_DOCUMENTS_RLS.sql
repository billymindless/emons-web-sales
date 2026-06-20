-- =====================================================================
-- app_documents RLS 정책 수정 — 403 Unauthorized 해결
-- Supabase SQL Editor에서 실행하세요.
-- =====================================================================

-- 기존 정책 전부 제거 후 재생성
DROP POLICY IF EXISTS "Allow all app_documents"   ON app_documents;
DROP POLICY IF EXISTS "Allow select app_documents" ON app_documents;
DROP POLICY IF EXISTS "Allow insert app_documents" ON app_documents;
DROP POLICY IF EXISTS "Allow update app_documents" ON app_documents;
DROP POLICY IF EXISTS "Allow delete app_documents" ON app_documents;

-- anon / authenticated 모두 허용 (서비스 키 사용 앱에 적합)
CREATE POLICY "Allow select app_documents"
  ON app_documents FOR SELECT
  TO anon, authenticated
  USING (true);

CREATE POLICY "Allow insert app_documents"
  ON app_documents FOR INSERT
  TO anon, authenticated
  WITH CHECK (true);

CREATE POLICY "Allow update app_documents"
  ON app_documents FOR UPDATE
  TO anon, authenticated
  USING (true)
  WITH CHECK (true);

CREATE POLICY "Allow delete app_documents"
  ON app_documents FOR DELETE
  TO anon, authenticated
  USING (true);
