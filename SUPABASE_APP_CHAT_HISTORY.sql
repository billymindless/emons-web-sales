-- ============================================================
-- app_chat_history: 전화번호 기반 통합 상담 아카이브
-- 채널톡 웹챗, 카카오톡, 오프라인 메모, 전화 통화 이력을
-- 전화번호 하나로 병합하여 재상담 시 즉시 조회 가능하게 함.
-- ============================================================

CREATE TABLE IF NOT EXISTS app_chat_history (
  id               BIGSERIAL PRIMARY KEY,
  customer_phone   TEXT NOT NULL,
  channel          TEXT NOT NULL CHECK (channel IN (
                     '채널톡_웹챗', '카카오톡', '오프라인_메모', '전화_통화'
                   )),
  chat_id          TEXT,
  summary          TEXT,
  full_text        TEXT,
  handled_by       TEXT,
  created_at       TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS app_chat_history_phone_idx
  ON app_chat_history (customer_phone);

CREATE INDEX IF NOT EXISTS app_chat_history_created_idx
  ON app_chat_history (created_at DESC);
