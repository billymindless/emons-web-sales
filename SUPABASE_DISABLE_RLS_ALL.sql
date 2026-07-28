-- =====================================================================
-- ⚠️⚠️ 사용 금지 (DEPRECATED — 보안 취약) ⚠️⚠️
-- =====================================================================
-- 이 스크립트는 모든 app_* 테이블의 RLS를 꺼서 프로젝트 URL + anon 키만으로
-- 누구나 REST API 로 전체 데이터를 읽기/수정/삭제할 수 있게 만든다.
-- Supabase Security Advisor 'rls_disabled_in_public' 경고의 원인.
-- → 대신 SUPABASE_SECURITY_ENABLE_RLS_ALL.sql 을 실행할 것.
--    (service_role 키는 RLS 를 우회하므로 앱 동작에 RLS 비활성화가 필요 없음)
-- =====================================================================
-- (이하 원본 — 실행하지 마세요)
--
-- 내부 ERP 앱 — 모든 app_* 테이블 RLS 비활성화
-- (service_role 키 사용 앱은 RLS가 불필요)
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
