-- ────────────────────────────────────────────────────────────
-- AI 기반 채널톡 VOC 분석 결과 저장 테이블
-- Supabase SQL Editor에서 실행하세요.
-- ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS app_voc_insights (
  id                 BIGSERIAL PRIMARY KEY,
  chat_id            TEXT UNIQUE,           -- 채널톡 chat_id (중복 방지)
  customer_phone     TEXT,                  -- 고객 전화번호
  handled_by         TEXT,                  -- 담당 매니저 이메일/이름
  is_claim           BOOLEAN DEFAULT FALSE, -- 클레임 여부
  complaint_category TEXT,                  -- 배송/제품불량/가격/응대/기타/없음
  product_idea       TEXT,                  -- 신제품·개선 아이디어
  summary            TEXT,                  -- 대화 1줄 요약
  sentiment          TEXT,                  -- 긍정/중립/부정
  raw_json           JSONB,                 -- OpenAI 원본 응답 JSON
  source             TEXT DEFAULT 'webhook',-- 수집 경로: webhook / excel_import
  analyzed_at        TIMESTAMPTZ DEFAULT now()
);

-- 인덱스
CREATE INDEX IF NOT EXISTS idx_voc_insights_analyzed_at ON app_voc_insights (analyzed_at DESC);
CREATE INDEX IF NOT EXISTS idx_voc_insights_sentiment    ON app_voc_insights (sentiment);
CREATE INDEX IF NOT EXISTS idx_voc_insights_is_claim     ON app_voc_insights (is_claim);

-- RLS 비활성화 (내부 ERP, service_role_key 사용)
ALTER TABLE app_voc_insights DISABLE ROW LEVEL SECURITY;
