-- 전사 공지사항 테이블 (기존 SQLite Notices 마이그레이션용 — Streamlit Cloud 영구 저장 목적)
-- Supabase 대시보드 → SQL Editor에서 실행하세요.
-- ※ 이미 테이블이 존재하는 경우에도 안전하게 실행됩니다 (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS).

-- 1. 테이블 신규 생성 (없는 경우에만)
CREATE TABLE IF NOT EXISTS app_notices (
  id BIGSERIAL PRIMARY KEY,
  title TEXT,
  content TEXT,
  external_link TEXT,
  message TEXT,                 -- 레거시 호환용(옛 버전은 content 대신 message 사용)
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- 2. 기존 테이블에 누락 컬럼이 있는 경우 추가 (마이그레이션)
ALTER TABLE app_notices ADD COLUMN IF NOT EXISTS title TEXT;
ALTER TABLE app_notices ADD COLUMN IF NOT EXISTS content TEXT;
ALTER TABLE app_notices ADD COLUMN IF NOT EXISTS external_link TEXT;
ALTER TABLE app_notices ADD COLUMN IF NOT EXISTS message TEXT;
ALTER TABLE app_notices ADD COLUMN IF NOT EXISTS is_active BOOLEAN DEFAULT true;
ALTER TABLE app_notices ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ DEFAULT now();

-- 3. 인덱스 생성 (활성 공지 최신순 조회 최적화)
CREATE INDEX IF NOT EXISTS idx_app_notices_active_created
  ON app_notices(is_active, created_at DESC);

-- 4. RLS 정책 (모든 매장 공통 공지이므로 전체 읽기·쓰기 허용)
ALTER TABLE app_notices ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_notices" ON app_notices;
CREATE POLICY "Allow all app_notices" ON app_notices FOR ALL USING (true) WITH CHECK (true);
