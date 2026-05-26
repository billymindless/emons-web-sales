-- 사내 업무판(사내결제시스템) 스키마
-- 8개 신규 테이블 + Storage 버킷 + 4종 알림 문구 seed
-- 모든 DDL은 멱등 (IF NOT EXISTS / ON CONFLICT DO NOTHING)

-- ─────────────────────────────────────────────────────────────────────
-- 1) app_tasks : 업무
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_tasks (
    id BIGSERIAL PRIMARY KEY,
    parent_task_id BIGINT NULL REFERENCES app_tasks(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'requested'
        CHECK (status IN ('requested','in_progress','feedback','done','on_hold')),
    priority TEXT NOT NULL DEFAULT 'normal'
        CHECK (priority IN ('low','normal','high','urgent')),
    start_date DATE,
    due_date DATE,
    created_by TEXT NOT NULL,
    store_name TEXT,
    db_filename TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS idx_app_tasks_parent ON app_tasks(parent_task_id);
CREATE INDEX IF NOT EXISTS idx_app_tasks_status ON app_tasks(status);
CREATE INDEX IF NOT EXISTS idx_app_tasks_store ON app_tasks(store_name);
CREATE INDEX IF NOT EXISTS idx_app_tasks_due ON app_tasks(due_date);

-- ─────────────────────────────────────────────────────────────────────
-- 2) app_task_assignees : 담당자
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_task_assignees (
    id BIGSERIAL PRIMARY KEY,
    task_id BIGINT NOT NULL REFERENCES app_tasks(id) ON DELETE CASCADE,
    employee_username TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'assignee'
        CHECK (role IN ('owner','assignee','watcher')),
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    assigned_by TEXT,
    UNIQUE (task_id, employee_username)
);
CREATE INDEX IF NOT EXISTS idx_app_task_assignees_user ON app_task_assignees(employee_username);

-- ─────────────────────────────────────────────────────────────────────
-- 3) app_task_comments : 댓글
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_task_comments (
    id BIGSERIAL PRIMARY KEY,
    task_id BIGINT NOT NULL REFERENCES app_tasks(id) ON DELETE CASCADE,
    author TEXT NOT NULL,
    body TEXT NOT NULL,
    parent_comment_id BIGINT NULL REFERENCES app_task_comments(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_app_task_comments_task ON app_task_comments(task_id);

-- ─────────────────────────────────────────────────────────────────────
-- 4) app_task_attachments : 첨부
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_task_attachments (
    id BIGSERIAL PRIMARY KEY,
    task_id BIGINT NULL REFERENCES app_tasks(id) ON DELETE CASCADE,
    comment_id BIGINT NULL REFERENCES app_task_comments(id) ON DELETE CASCADE,
    storage_path TEXT NOT NULL,
    mime_type TEXT,
    original_name TEXT,
    byte_size BIGINT,
    uploaded_by TEXT,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_app_task_attachments_task ON app_task_attachments(task_id);
CREATE INDEX IF NOT EXISTS idx_app_task_attachments_comment ON app_task_attachments(comment_id);

-- ─────────────────────────────────────────────────────────────────────
-- 5) app_task_activity : 활동 로그
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_task_activity (
    id BIGSERIAL PRIMARY KEY,
    task_id BIGINT NOT NULL REFERENCES app_tasks(id) ON DELETE CASCADE,
    actor TEXT,
    action TEXT NOT NULL,
    payload JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_app_task_activity_task ON app_task_activity(task_id);

-- ─────────────────────────────────────────────────────────────────────
-- 6) app_notifications : 인앱 + 친구톡 알림 큐
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_notifications (
    id BIGSERIAL PRIMARY KEY,
    recipient_username TEXT NOT NULL,
    task_id BIGINT NULL REFERENCES app_tasks(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    message TEXT NOT NULL,
    link_path TEXT,
    channel TEXT NOT NULL DEFAULT 'both'
        CHECK (channel IN ('in_app','friendtalk','both')),
    is_read BOOLEAN NOT NULL DEFAULT false,
    sent_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    read_at TIMESTAMPTZ NULL,
    kakao_msg_id TEXT,
    kakao_status TEXT
        CHECK (kakao_status IN ('pending','sent','failed','not_friend','out_of_hours','disabled','skipped','no_phone')),
    kakao_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_app_notifications_recipient ON app_notifications(recipient_username, is_read);
CREATE INDEX IF NOT EXISTS idx_app_notifications_kakao_status ON app_notifications(kakao_status);

-- ─────────────────────────────────────────────────────────────────────
-- 7) app_notification_templates : 친구톡 문구 (매장관리자 편집 가능)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_notification_templates (
    id BIGSERIAL PRIMARY KEY,
    template_key TEXT NOT NULL UNIQUE,
    body_template TEXT NOT NULL,
    description TEXT,
    updated_by TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 4종 기본 문구 seed (이미 존재하면 그대로 둠)
INSERT INTO app_notification_templates (template_key, body_template, description) VALUES
    ('task_assigned',
     E'[사내업무] {name}님, 새 업무가 배정되었습니다.\n• 제목: {title}\n• 마감: {due_date}\n• 요청자: {requester}\n바로가기: {link}',
     '신규 업무 배정 안내')
ON CONFLICT (template_key) DO NOTHING;

INSERT INTO app_notification_templates (template_key, body_template, description) VALUES
    ('status_changed',
     E'[사내업무] {title}\n상태 변경: {from_status} → {to_status} ({actor})',
     '업무 상태 변경 안내')
ON CONFLICT (template_key) DO NOTHING;

INSERT INTO app_notification_templates (template_key, body_template, description) VALUES
    ('comment_added',
     E'[사내업무] {title}\n{author}: {preview}\n바로가기: {link}',
     '새 댓글 안내')
ON CONFLICT (template_key) DO NOTHING;

INSERT INTO app_notification_templates (template_key, body_template, description) VALUES
    ('due_soon',
     E'[사내업무] 내일 마감: {title} ({due_date})\n바로가기: {link}',
     '마감 임박 D-1 안내')
ON CONFLICT (template_key) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────
-- 8) app_batch_runs : 배치 멱등성 보장 (오늘 D-1 한 번만 실행)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_batch_runs (
    id BIGSERIAL PRIMARY KEY,
    batch_key TEXT NOT NULL,
    run_date DATE NOT NULL,
    status TEXT NOT NULL DEFAULT 'running'
        CHECK (status IN ('running','success','failed')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ NULL,
    processed_count INT,
    error TEXT,
    UNIQUE (batch_key, run_date)
);

-- ─────────────────────────────────────────────────────────────────────
-- Storage 버킷 (Supabase Storage)
-- ─────────────────────────────────────────────────────────────────────
INSERT INTO storage.buckets (id, name, public)
VALUES ('task-attachments', 'task-attachments', false)
ON CONFLICT (id) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────
-- RLS — 이 앱은 service_role/anon 단일 키로 접근하므로 정책은 단순화.
-- 실 운영에서 사용자별 jwt 도입 시 store_name·db_filename 기반 정책 추가.
-- ─────────────────────────────────────────────────────────────────────
ALTER TABLE app_tasks ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_task_assignees ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_task_comments ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_task_attachments ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_task_activity ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_notification_templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_batch_runs ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'app_tasks' AND policyname = 'app_tasks_all') THEN
        CREATE POLICY app_tasks_all ON app_tasks FOR ALL USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'app_task_assignees' AND policyname = 'app_task_assignees_all') THEN
        CREATE POLICY app_task_assignees_all ON app_task_assignees FOR ALL USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'app_task_comments' AND policyname = 'app_task_comments_all') THEN
        CREATE POLICY app_task_comments_all ON app_task_comments FOR ALL USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'app_task_attachments' AND policyname = 'app_task_attachments_all') THEN
        CREATE POLICY app_task_attachments_all ON app_task_attachments FOR ALL USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'app_task_activity' AND policyname = 'app_task_activity_all') THEN
        CREATE POLICY app_task_activity_all ON app_task_activity FOR ALL USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'app_notifications' AND policyname = 'app_notifications_all') THEN
        CREATE POLICY app_notifications_all ON app_notifications FOR ALL USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'app_notification_templates' AND policyname = 'app_notification_templates_all') THEN
        CREATE POLICY app_notification_templates_all ON app_notification_templates FOR ALL USING (true) WITH CHECK (true);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE tablename = 'app_batch_runs' AND policyname = 'app_batch_runs_all') THEN
        CREATE POLICY app_batch_runs_all ON app_batch_runs FOR ALL USING (true) WITH CHECK (true);
    END IF;
END $$;
