-- ============================================================
-- Supabase: 채널톡 웹훅 수신 로그 테이블
-- 실행 위치: Supabase 대시보드 → SQL Editor
-- ============================================================

-- 1) 테이블 생성
CREATE TABLE IF NOT EXISTS public.channel_talk_webhook_log (
    id             BIGSERIAL PRIMARY KEY,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    store_key      TEXT,                   -- 매장 키 (예: 삼산, 학성)
    phone          TEXT,                   -- 수신된 연락처 (숫자만)
    name           TEXT,                   -- 수신된 고객명
    status         TEXT NOT NULL,          -- processing | success | skipped | fail
    message        TEXT,                   -- 실패 사유 또는 안내 메시지
    store_name     TEXT,                   -- 매칭된 Supabase 매장명
    customer_id    BIGINT REFERENCES public.app_customers(id) ON DELETE SET NULL
);

-- 2) 인덱스 (조회 성능)
CREATE INDEX IF NOT EXISTS idx_ctwl_created_at  ON public.channel_talk_webhook_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ctwl_store_key   ON public.channel_talk_webhook_log (store_key);
CREATE INDEX IF NOT EXISTS idx_ctwl_phone       ON public.channel_talk_webhook_log (phone);
CREATE INDEX IF NOT EXISTS idx_ctwl_status      ON public.channel_talk_webhook_log (status);

-- 3) RLS 활성화
ALTER TABLE public.channel_talk_webhook_log ENABLE ROW LEVEL SECURITY;

-- 4) RLS 정책: service_role(웹훅 서버)은 INSERT/UPDATE 가능
DROP POLICY IF EXISTS "service_role: all" ON public.channel_talk_webhook_log;
CREATE POLICY "service_role: all" ON public.channel_talk_webhook_log
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- 5) RLS 정책: 인증된 앱 사용자는 SELECT만 허용
DROP POLICY IF EXISTS "authenticated: select" ON public.channel_talk_webhook_log;
CREATE POLICY "authenticated: select" ON public.channel_talk_webhook_log
    FOR SELECT
    TO authenticated
    USING (true);

-- ============================================================
-- 사용 방법:
--   1. 위 SQL을 Supabase SQL Editor에서 실행하세요.
--   2. channel_talk_webhook.py 서버를 실행 시
--      환경변수 SUPABASE_URL 과 SUPABASE_SERVICE_KEY 를 반드시 설정하세요.
--      예) export SUPABASE_URL="https://xxx.supabase.co"
--          export SUPABASE_SERVICE_KEY="sb_secret_..."
-- ============================================================
