-- =====================================================================
-- 채널톡 콜 기반 상담일지
--   - app_call_records:        통화 원본 + 전사 + 요약 + 리드 매칭
--   - app_call_channel_teams:  매장(db_filename) ↔ 채널톡 팀/번호 매핑
--   - app_call_ingest_log:     ingest 실패·건너뜀 이벤트 로그
--
-- 계획서: docs/plans/채널톡_콜_상담일지_c5e969b5.plan.md
-- 채널톡 콜(Meet) STT 는 채널톡 내장을 사용. Gemini 요약만 우리가 추가.
-- =====================================================================

-- 1) 통화 기록 본체
CREATE TABLE IF NOT EXISTS app_call_records (
    id BIGSERIAL PRIMARY KEY,
    db_filename TEXT,                          -- 매장 매핑(없으면 NULL)
    channel_user_chat_id TEXT,                 -- 채널톡 userChatId
    channel_meet_message_id TEXT NOT NULL,     -- 채널톡 미트 messageId
    direction TEXT CHECK (direction IN ('inbound', 'outbound', 'missed')),
    from_number TEXT,
    to_number TEXT,
    caller_name TEXT,
    matched_lead_id BIGINT REFERENCES app_leads(id) ON DELETE SET NULL,
    manager_email TEXT,
    manager_name TEXT,
    started_at TIMESTAMPTZ,
    engaged_at TIMESTAMPTZ,
    ended_at TIMESTAMPTZ,
    duration_seconds INTEGER,
    recording_url TEXT,
    recording_expires_at TIMESTAMPTZ,
    transcript TEXT,                           -- 채널톡 STT 원문
    summary TEXT,                              -- Gemini 요약
    category TEXT,                             -- Gemini 카테고리
    action_items JSONB,                        -- Gemini 액션 아이템 리스트
    estimated_amount NUMERIC(12, 2),           -- Gemini 추정 견적
    raw JSONB,                                 -- 원본 API 응답 보관
    deleted_at TIMESTAMPTZ,                    -- softDelete
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (channel_meet_message_id)
);

CREATE INDEX IF NOT EXISTS idx_app_call_records_db
    ON app_call_records (db_filename, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_app_call_records_lead
    ON app_call_records (matched_lead_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_app_call_records_from
    ON app_call_records (from_number);
CREATE INDEX IF NOT EXISTS idx_app_call_records_started
    ON app_call_records (started_at DESC)
    WHERE deleted_at IS NULL;

ALTER TABLE app_call_records ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_call_records" ON app_call_records;
CREATE POLICY "Allow all app_call_records"
    ON app_call_records FOR ALL USING (true) WITH CHECK (true);


-- 2) 매장 ↔ 채널톡 팀/번호 매핑
CREATE TABLE IF NOT EXISTS app_call_channel_teams (
    id BIGSERIAL PRIMARY KEY,
    db_filename TEXT NOT NULL,
    channel_team_id TEXT NOT NULL,             -- 채널톡 팀 id
    channel_phone_number TEXT,                 -- 채널톡 발급 번호 (표시용)
    note TEXT,
    created_at TIMESTAMPTZ DEFAULT now(),
    updated_at TIMESTAMPTZ DEFAULT now(),
    UNIQUE (channel_team_id),
    UNIQUE (db_filename, channel_team_id)
);

CREATE INDEX IF NOT EXISTS idx_app_call_channel_teams_db
    ON app_call_channel_teams (db_filename);

ALTER TABLE app_call_channel_teams ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_call_channel_teams" ON app_call_channel_teams;
CREATE POLICY "Allow all app_call_channel_teams"
    ON app_call_channel_teams FOR ALL USING (true) WITH CHECK (true);


-- 3) ingest 이벤트 로그 (건너뜀 사유·오류 추적)
CREATE TABLE IF NOT EXISTS app_call_ingest_log (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL CHECK (source IN ('webhook', 'poll', 'manual')),
    action TEXT NOT NULL,
    channel_user_chat_id TEXT,
    channel_meet_message_id TEXT,
    db_filename TEXT,
    from_number TEXT,
    ok BOOLEAN NOT NULL DEFAULT TRUE,
    detail TEXT,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_app_call_ingest_log_time
    ON app_call_ingest_log (created_at DESC);

ALTER TABLE app_call_ingest_log ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_call_ingest_log" ON app_call_ingest_log;
CREATE POLICY "Allow all app_call_ingest_log"
    ON app_call_ingest_log FOR ALL USING (true) WITH CHECK (true);
