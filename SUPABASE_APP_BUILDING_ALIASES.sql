-- ── 건물명 별칭(alias) 매핑 테이블 ─────────────────────────────
-- 목적: 신규 입주 아파트처럼 카카오 도로명 주소가 아직 생성되지 않아
--       app_customers.building_name 이 NULL 인 고객들을, 관리자가 수동으로
--       '주소 부분 일치 키워드 → 정식 건물명'으로 매핑하여
--       AI 세일즈 리포트 「아파트/건물 Top」 집계에 반영한다.
--
-- 실행: Supabase 대시보드 → SQL Editor 에서 이 파일 내용을 실행.
-- 사용처: sales_report_service.group_by_building() alias fallback,
--         app.py 관리자메뉴 → 「건물명 별칭 관리」 UI.

CREATE TABLE IF NOT EXISTS app_building_aliases (
    id            BIGSERIAL PRIMARY KEY,
    store_name    TEXT NOT NULL,   -- 매장 격리 (app_stores.store_name 과 동일)
    keyword       TEXT NOT NULL,   -- 주소 부분 일치 검색어 (예: '달천이파크')
    building_name TEXT NOT NULL,   -- 정식 건물명 (예: '달천이파크1차아파트')
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE (store_name, keyword)
);

CREATE INDEX IF NOT EXISTS idx_app_building_aliases_store
    ON app_building_aliases(store_name);

COMMENT ON TABLE  app_building_aliases IS '신규 입주 아파트 등 도로명 미부여 주소를 정식 건물명으로 매핑하는 alias 사전';
COMMENT ON COLUMN app_building_aliases.keyword       IS 'app_customers.address 에 대해 ILIKE 부분 일치 검색할 키워드';
COMMENT ON COLUMN app_building_aliases.building_name IS '집계 결과에 노출될 정식 건물명 (통합 표시명)';

-- RLS (다른 app_* 테이블과 동일 정책)
ALTER TABLE app_building_aliases ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_building_aliases" ON app_building_aliases;
CREATE POLICY "Allow all app_building_aliases" ON app_building_aliases
    FOR ALL USING (true) WITH CHECK (true);
