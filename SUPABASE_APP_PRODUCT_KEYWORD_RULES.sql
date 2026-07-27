-- =====================================================================
-- app_product_keyword_rules : 브랜드/모델명 키워드 → 대분류 강제 규칙
-- =====================================================================
-- 목적: product_taxonomy_service.py 의 하드코딩된 _FORCE_KEYWORD_RULES 를
--       관리자가 UI(품목 분류 관리 → "브랜드/키워드 사전" 탭)에서 직접
--       추가·수정·삭제할 수 있게 하는 DB 테이블.
--   - product_name 에 keyword 가 포함되면 Gemini 호출 없이 즉시 category 로 확정.
--   - keyword 에 '+' 를 쓰면 AND 조합: 모든 조각이 포함될 때만 매칭.
--     예: '노블앙+1100' → 이름에 '노블앙' 과 '1100' 이 둘 다 있어야 매칭.
--   - priority 가 작을수록 먼저 검사한다 (동일 이름에 여러 키워드가 포함될 때
--     더 구체적인 규칙을 먼저 두기 위함). 기본값 100.
--   - priority >= 500 인 규칙은 코드 내장 종류 키워드 규칙(소파·식탁 등)보다
--     "나중에" 검사되는 포괄(fallback) 규칙이다. 여러 카테고리에 걸치는 브랜드는
--     포괄 규칙을 900 으로 등록하면 '디망스소파' 같은 이름이 내장 '소파' 규칙으로
--     먼저 분류되고, 종류 단어가 없는 이름만 브랜드 규칙으로 분류된다.
--   - 신규/미분류 품목에는 다음 분류 시도부터 즉시 적용된다.
--   - 이미 app_product_taxonomy 에 저장된 기존 건에는 관리 UI의
--     "기존 분류에도 적용" 버튼으로 소급 적용한다 (source='manual'/'override' 인
--     관리자 확정 분류는 절대 덮어쓰지 않음).
-- =====================================================================

CREATE TABLE IF NOT EXISTS app_product_keyword_rules (
  id           BIGSERIAL PRIMARY KEY,
  keyword      TEXT NOT NULL UNIQUE,
  category     TEXT NOT NULL,
  priority     INTEGER NOT NULL DEFAULT 100,
  note         TEXT,
  is_active    BOOLEAN NOT NULL DEFAULT TRUE,
  created_by   TEXT,
  updated_by   TEXT,
  created_at   TIMESTAMPTZ DEFAULT now(),
  updated_at   TIMESTAMPTZ DEFAULT now(),
  CHECK (category IN (
    '옷장','식탁','자녀방_서재','침대','SSDS침대','소파','거실장','소품','전시품','기타'
  ))
);

CREATE INDEX IF NOT EXISTS idx_app_product_keyword_rules_priority ON app_product_keyword_rules(priority);
CREATE INDEX IF NOT EXISTS idx_app_product_keyword_rules_active   ON app_product_keyword_rules(is_active);

COMMENT ON TABLE  app_product_keyword_rules          IS '품목명에 특정 키워드가 포함되면 즉시 카테고리를 확정하는 관리자 편집 규칙.';
COMMENT ON COLUMN app_product_keyword_rules.priority IS '작을수록 먼저 검사됨 (기본 100). 500 이상은 내장 종류 키워드 규칙 뒤에 검사되는 포괄(fallback) 규칙.';

ALTER TABLE app_product_keyword_rules ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow all app_product_keyword_rules" ON app_product_keyword_rules;
CREATE POLICY "Allow all app_product_keyword_rules" ON app_product_keyword_rules FOR ALL USING (true) WITH CHECK (true);

-- ---------------------------------------------------------------------
-- app_product_taxonomy.source 에 'rule' (키워드 사전 소급 적용) 값 허용
-- ---------------------------------------------------------------------
ALTER TABLE app_product_taxonomy DROP CONSTRAINT IF EXISTS app_product_taxonomy_source_check;
ALTER TABLE app_product_taxonomy ADD CONSTRAINT app_product_taxonomy_source_check
  CHECK (source IN ('gemini','manual','override','rule'));
