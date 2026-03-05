-- ============================================================
-- Supabase: CRM 자동화 룰 저장 테이블
-- 실행 위치: Supabase 대시보드 → SQL Editor
-- ============================================================

-- 1) 테이블 생성
CREATE TABLE IF NOT EXISTS public.crm_automations (
    id                      BIGSERIAL PRIMARY KEY,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    store_name              TEXT NOT NULL,              -- 매장명
    campaign_name           TEXT NOT NULL,              -- 캠페인 이름
    send_channel            TEXT NOT NULL,              -- '일반 문자' | '카카오 브랜드톡'
    kakao_channel_id        TEXT,                       -- 카카오톡 채널 ID
    message_template        TEXT NOT NULL,              -- 메시지 템플릿 ({이름} 등 변수 포함)
    trigger_type            TEXT NOT NULL,              -- '즉시 발송' | '특정일 예약 발송' | '배송 후 N일...'
    scheduled_date          DATE,                       -- 예약 발송 날짜 (특정일 예약 시)
    delivery_offset_days    INTEGER,                    -- 배송일 기준 오프셋 일수 (7/30/100/365)
    filter_items            TEXT,                       -- JSON 배열: 품목 필터 ["소파","침대"]
    filter_region           TEXT,                       -- 지역 키워드
    filter_price_min        INTEGER DEFAULT 0,          -- 판매가 최소
    filter_price_max        INTEGER DEFAULT 0,          -- 판매가 최대 (0 = 제한 없음)
    fallback_sms            BOOLEAN DEFAULT TRUE,       -- 카카오 실패 시 SMS 대체 발송
    solapi_payload_preview  JSONB,                      -- 생성된 Solapi payload (검토·재사용용)
    target_count            INTEGER DEFAULT 0,          -- 저장 시점 타겟 인원 수
    status                  TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'paused' | 'done' | 'error'
    last_run_at             TIMESTAMPTZ,                -- 마지막 실행 시각 (스케줄러가 업데이트)
    last_run_result         TEXT                        -- 마지막 실행 결과 메시지
);

-- 2) 인덱스
CREATE INDEX IF NOT EXISTS idx_crm_store_name   ON public.crm_automations (store_name);
CREATE INDEX IF NOT EXISTS idx_crm_trigger_type ON public.crm_automations (trigger_type);
CREATE INDEX IF NOT EXISTS idx_crm_status       ON public.crm_automations (status);
CREATE INDEX IF NOT EXISTS idx_crm_created_at   ON public.crm_automations (created_at DESC);

-- 3) RLS 활성화
ALTER TABLE public.crm_automations ENABLE ROW LEVEL SECURITY;

-- 4) RLS 정책: service_role (API 서버 / 웹훅) 은 전체 접근 허용
DROP POLICY IF EXISTS "service_role: all" ON public.crm_automations;
CREATE POLICY "service_role: all" ON public.crm_automations
    FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- 5) RLS 정책: 인증된 앱 사용자는 SELECT / INSERT / UPDATE 허용
DROP POLICY IF EXISTS "authenticated: read_write" ON public.crm_automations;
CREATE POLICY "authenticated: read_write" ON public.crm_automations
    FOR ALL
    TO authenticated
    USING (true)
    WITH CHECK (true);

-- ============================================================
-- 스케줄러 연동 안내 (자동/예약 발송 실행 방법)
-- ============================================================
-- '특정일 예약 발송' / '배송 후 N일 자동 발송' 룰은 이 테이블에 저장만 됩니다.
-- 실제 발송 트리거는 아래 방법 중 하나를 선택해 구현하세요:
--
--   A) Supabase Edge Function + pg_cron (추천)
--      - pg_cron 으로 매일 오전 9시에 Edge Function 실행
--      - Edge Function 에서 crm_automations 조회 → Solapi API 호출
--
--   B) 외부 cron 서버 (e.g. Railway, Render 의 cron job)
--      - api.py 에 GET /crm/run-scheduled 엔드포인트 추가
--      - cron job 에서 해당 URL 을 매일 호출
--
-- Solapi API 발신번호/인증키는 .streamlit/secrets.toml 에 추가:
--   [solapi]
--   API_KEY    = "NCSXXXXXX"
--   API_SECRET = "XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXX"
--   SENDER     = "0312345678"   # 등록된 발신번호
-- ============================================================
