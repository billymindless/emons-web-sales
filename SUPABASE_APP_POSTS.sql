-- 사내 게시판(게시물) 스키마
-- 글 작성·댓글·첨부 (담당자/상태/알림 없음). 첨부는 Storage 버킷 'task-attachments' 재사용.
-- 모든 DDL은 멱등 (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS)

-- ─────────────────────────────────────────────────────────────────────
-- 1) app_posts : 게시물
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_posts (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT,
    author TEXT NOT NULL,
    store_name TEXT,
    scope TEXT NOT NULL DEFAULT 'store'
        CHECK (scope IN ('store','company')),
    tags TEXT,
    is_pinned BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_app_posts_scope ON app_posts(scope);
CREATE INDEX IF NOT EXISTS idx_app_posts_store ON app_posts(store_name);
CREATE INDEX IF NOT EXISTS idx_app_posts_pinned ON app_posts(is_pinned, created_at DESC);

-- ─────────────────────────────────────────────────────────────────────
-- 2) app_post_comments : 댓글
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_post_comments (
    id BIGSERIAL PRIMARY KEY,
    post_id BIGINT NOT NULL REFERENCES app_posts(id) ON DELETE CASCADE,
    author TEXT NOT NULL,
    body TEXT NOT NULL,
    parent_comment_id BIGINT NULL REFERENCES app_post_comments(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_app_post_comments_post ON app_post_comments(post_id);

-- ─────────────────────────────────────────────────────────────────────
-- 3) app_post_attachments : 첨부
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_post_attachments (
    id BIGSERIAL PRIMARY KEY,
    post_id BIGINT NULL REFERENCES app_posts(id) ON DELETE CASCADE,
    comment_id BIGINT NULL REFERENCES app_post_comments(id) ON DELETE CASCADE,
    storage_path TEXT NOT NULL,
    mime_type TEXT,
    original_name TEXT,
    byte_size BIGINT,
    uploaded_by TEXT,
    uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_app_post_attachments_post ON app_post_attachments(post_id);
CREATE INDEX IF NOT EXISTS idx_app_post_attachments_comment ON app_post_attachments(comment_id);

-- ─────────────────────────────────────────────────────────────────────
-- RLS (앱 레벨에서 가시성 필터링 — 기존 app_tasks와 동일 정책)
-- ─────────────────────────────────────────────────────────────────────
ALTER TABLE app_posts ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_posts" ON app_posts;
CREATE POLICY "Allow all app_posts" ON app_posts FOR ALL USING (true) WITH CHECK (true);

ALTER TABLE app_post_comments ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_post_comments" ON app_post_comments;
CREATE POLICY "Allow all app_post_comments" ON app_post_comments FOR ALL USING (true) WITH CHECK (true);

ALTER TABLE app_post_attachments ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_post_attachments" ON app_post_attachments;
CREATE POLICY "Allow all app_post_attachments" ON app_post_attachments FOR ALL USING (true) WITH CHECK (true);
