-- =====================================================================
-- 보안 잠금: public 스키마 전체 테이블 RLS 활성화 + anon 권한 회수
-- =====================================================================
-- 배경:
--   과거 SUPABASE_DISABLE_RLS_ALL.sql 실행으로 모든 app_* 테이블의 RLS가
--   비활성화되어, Supabase Security Advisor 가 전 테이블에
--   'rls_disabled_in_public' (Table publicly accessible) 경고를 낸다.
--   RLS가 꺼져 있으면 프로젝트 URL + anon 키(공개 키 취급)만으로
--   REST API(PostgREST)를 통해 모든 테이블을 읽기/수정/삭제할 수 있다.
--
-- 해결 원리:
--   이 앱(Streamlit 서버)은 st.secrets 의 service_role 키로 접근하며,
--   service_role 은 RLS 를 우회한다. 따라서
--     1) RLS 를 켜고
--     2) 정책을 하나도 만들지 않으면 (기본 거부)
--   anon/authenticated 의 외부 접근만 차단되고 앱은 그대로 동작한다.
--
-- ⚠️ 실행 전 필수 확인:
--   배포 환경(Streamlit Cloud → App settings → Secrets)의 [supabase] 섹션에
--   service_role_key 가 설정되어 있어야 한다. anon 키(key/anon_key)로만
--   운영 중이라면 이 스크립트 실행 즉시 앱의 모든 DB 조회가 차단된다.
--
-- 실행: Supabase Dashboard → SQL Editor 에서 전체 실행.
-- =====================================================================

-- 1) public 스키마 모든 테이블 RLS 활성화 (정책 없음 = anon 기본 거부)
DO $$
DECLARE tbl TEXT;
BEGIN
  FOR tbl IN
    SELECT schemaname || '.' || tablename
    FROM pg_tables
    WHERE schemaname = 'public'
  LOOP
    EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', tbl);
  END LOOP;
END $$;

-- 1-b) 기존 'Allow all' 등 허용 정책 전부 제거
--      (과거 각 테이블 생성 SQL 이 만든 USING (true) 정책이 남아 있으면
--       RLS 를 켜도 익명 접근이 계속 허용되고 Advisor 경고도 유지된다.
--       service_role 은 RLS 를 우회하므로 정책이 없어도 앱은 정상 동작.)
DO $$
DECLARE tbl TEXT; pol TEXT;
BEGIN
  FOR tbl, pol IN
    SELECT schemaname || '.' || tablename, policyname
    FROM pg_policies
    WHERE schemaname = 'public'
  LOOP
    EXECUTE format('DROP POLICY IF EXISTS %I ON %s', pol, tbl);
  END LOOP;
END $$;

-- 2) 이중 안전장치: anon/authenticated 의 테이블·시퀀스 권한 자체를 회수
--    (RLS 와 별개로 GRANT 레벨에서도 차단 — 향후 새 테이블에도 기본 적용)
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon, authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON TABLES FROM anon, authenticated;
ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE ALL ON SEQUENCES FROM anon, authenticated;

-- =====================================================================
-- 검증 쿼리 (실행 후 확인)
-- =====================================================================

-- (a) RLS 꺼진 테이블이 0건이어야 한다
SELECT tablename
FROM pg_tables
WHERE schemaname = 'public' AND rowsecurity = false;

-- (b) 남아 있는 정책 목록 (없거나, 의도적으로 만든 것만 있어야 한다)
SELECT tablename, policyname, roles
FROM pg_policies
WHERE schemaname = 'public';

-- =====================================================================
-- 외부 차단 확인 (터미널에서, anon 키 사용 시 권한 오류/빈 응답이어야 정상):
--   curl "https://<프로젝트>.supabase.co/rest/v1/app_customers?limit=1" \
--     -H "apikey: <anon키>" -H "Authorization: Bearer <anon키>"
-- =====================================================================

-- =====================================================================
-- (별도 수동 조치) Storage 'documents' 버킷 비공개 전환
-- =====================================================================
-- Dashboard → Storage → documents 버킷 → Edit bucket → Public 해제 (Private).
-- 앱 코드는 서명 URL(create_signed_url) 방식으로 전환 완료되어
-- Private 전환 후에도 자료실 다운로드·미리보기가 정상 동작한다.
-- (task-attachments 버킷은 원래 Private + 서명 URL 방식이므로 조치 불필요)
-- =====================================================================
