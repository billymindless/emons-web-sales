-- =====================================================================
-- 매입 원장 대사 Gemini 제안의 관리자 확인/거절 예시
-- 모델 파인튜닝이 아니라 few-shot 재사용용. 매출 입력 경로와 무관.
-- =====================================================================

CREATE TABLE IF NOT EXISTS app_import_ai_feedback (
    id            BIGSERIAL PRIMARY KEY,
    db_filename   TEXT NOT NULL,
    kind          TEXT NOT NULL,          -- 'merge' | 'fraud'
    payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
    decision      TEXT NOT NULL,          -- 'accepted' | 'rejected'
    decided_by    TEXT,
    decided_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_import_ai_feedback_db
    ON app_import_ai_feedback (db_filename, kind, decided_at DESC);

ALTER TABLE app_import_ai_feedback ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_import_ai_feedback" ON app_import_ai_feedback;
CREATE POLICY "Allow all app_import_ai_feedback" ON app_import_ai_feedback
    FOR ALL USING (true) WITH CHECK (true);
