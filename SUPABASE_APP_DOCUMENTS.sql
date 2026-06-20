-- =====================================================================
-- app_documents — 자료실 (게시판형 업무 자료 관리)
-- =====================================================================

CREATE TABLE IF NOT EXISTS app_documents (
  id          BIGSERIAL PRIMARY KEY,
  title       TEXT NOT NULL,
  content     TEXT,
  author      TEXT NOT NULL,
  db_filename TEXT,                      -- 매장 구분 (NULL = 전체 공용)
  tags        TEXT,                      -- 쉼표 구분 태그
  created_at  TIMESTAMPTZ DEFAULT now(),
  updated_at  TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE app_documents ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_documents" ON app_documents;
CREATE POLICY "Allow all app_documents"
  ON app_documents FOR ALL USING (true) WITH CHECK (true);
