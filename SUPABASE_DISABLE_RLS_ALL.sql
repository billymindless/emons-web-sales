-- =====================================================================
-- 내부 ERP 앱 — 모든 app_* 테이블 RLS 비활성화
-- (service_role 키 사용 앱은 RLS가 불필요)
-- Supabase SQL Editor에서 한 번에 실행하세요.
-- =====================================================================

-- 기존 'always true' 정책 제거 후 RLS 비활성화
DO $$
DECLARE
  tbl TEXT;
  pol TEXT;
BEGIN
  FOR tbl, pol IN
    SELECT schemaname || '.' || tablename, policyname
    FROM pg_policies
    WHERE schemaname = 'public'
      AND tablename LIKE 'app_%'
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON %s', pol, tbl);
  END LOOP;

  FOR tbl IN
    SELECT schemaname || '.' || tablename
    FROM pg_tables
    WHERE schemaname = 'public'
      AND tablename LIKE 'app_%'
  LOOP
    EXECUTE format('ALTER TABLE %s DISABLE ROW LEVEL SECURITY', tbl);
  END LOOP;
END $$;
