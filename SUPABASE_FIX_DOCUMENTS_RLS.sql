-- =====================================================================
-- app_documents 403 오류 해결 — 둘 중 하나만 실행하세요
-- =====================================================================

-- ▶ 방법 1 (권장, 가장 간단): RLS 완전 비활성화
--   내부 ERP 앱이므로 RLS 불필요
ALTER TABLE app_documents DISABLE ROW LEVEL SECURITY;


-- ▶ 방법 2: RLS 유지 + 모든 역할에 명시적 권한 부여 (방법 1 대신 사용)
-- DROP POLICY IF EXISTS "Allow all app_documents"    ON app_documents;
-- DROP POLICY IF EXISTS "Allow select app_documents" ON app_documents;
-- DROP POLICY IF EXISTS "Allow insert app_documents" ON app_documents;
-- DROP POLICY IF EXISTS "Allow update app_documents" ON app_documents;
-- DROP POLICY IF EXISTS "Allow delete app_documents" ON app_documents;
--
-- CREATE POLICY "Allow select app_documents"
--   ON app_documents FOR SELECT TO anon, authenticated USING (true);
-- CREATE POLICY "Allow insert app_documents"
--   ON app_documents FOR INSERT TO anon, authenticated WITH CHECK (true);
-- CREATE POLICY "Allow update app_documents"
--   ON app_documents FOR UPDATE TO anon, authenticated USING (true) WITH CHECK (true);
-- CREATE POLICY "Allow delete app_documents"
--   ON app_documents FOR DELETE TO anon, authenticated USING (true);
