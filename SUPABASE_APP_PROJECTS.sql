-- 프로젝트 관리 모듈 스키마
-- 3개 신규 테이블 + 시드 카테고리 + RLS
-- 모든 DDL은 멱등 (IF NOT EXISTS / ON CONFLICT DO NOTHING)

-- ─────────────────────────────────────────────────────────────────────
-- 1) app_project_types : 프로젝트 유형(카테고리) 마스터
--    관리자가 자유롭게 추가/수정/비활성화
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_project_types (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    display_order INT NOT NULL DEFAULT 0,
    is_active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_app_project_types_active ON app_project_types(is_active, display_order);

-- 시드 카테고리 (이미 존재하면 그대로 둠)
INSERT INTO app_project_types (name, display_order) VALUES
    ('미분류',        0),
    ('신제품 론칭',   10),
    ('매장 운영',     20),
    ('마케팅·행사',   30),
    ('교육·인사',     40),
    ('IT·시스템',     50),
    ('기타',          60)
ON CONFLICT (name) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────
-- 2) app_projects : 프로젝트 마스터
--    매장과 무관한 전사 단위. 모든 직원이 생성 가능, 삭제는 superadmin + PM만.
--    visibility: public(전 직원 조회) | private(팀원만 조회)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_projects (
    id BIGSERIAL PRIMARY KEY,
    code TEXT UNIQUE,                                  -- 자동 채번: 'YY-N' 형식 (예: 26-7)
    name TEXT NOT NULL,
    type_id BIGINT NULL REFERENCES app_project_types(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT '진행예정'
        CHECK (status IN ('진행예정','진행중','완료','보류','취소')),
    visibility TEXT NOT NULL DEFAULT 'public'
        CHECK (visibility IN ('public','private')),
    description TEXT,
    start_date DATE,
    end_date DATE,
    created_by TEXT NOT NULL,                          -- username
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS idx_app_projects_status ON app_projects(status);
CREATE INDEX IF NOT EXISTS idx_app_projects_type ON app_projects(type_id);
CREATE INDEX IF NOT EXISTS idx_app_projects_visibility ON app_projects(visibility);
CREATE INDEX IF NOT EXISTS idx_app_projects_archived ON app_projects(archived_at);

-- ─────────────────────────────────────────────────────────────────────
-- 3) app_project_members : 팀 (다중 PM + 팀원 N명)
--    role='pm' 인 멤버가 한 명이라도 있어야 함 (앱 레벨에서 강제)
-- ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_project_members (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES app_projects(id) ON DELETE CASCADE,
    employee_username TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member'
        CHECK (role IN ('pm','member')),
    assigned_by TEXT,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, employee_username)
);
CREATE INDEX IF NOT EXISTS idx_app_project_members_user ON app_project_members(employee_username);
CREATE INDEX IF NOT EXISTS idx_app_project_members_project ON app_project_members(project_id);
CREATE INDEX IF NOT EXISTS idx_app_project_members_role ON app_project_members(project_id, role);

-- ─────────────────────────────────────────────────────────────────────
-- RLS (DROP/CREATE — psycopg2 세미콜론 분할 호환)
-- 앱 레벨 권한 필터를 사용하므로 Allow-all 정책 (기존 app_tasks 패턴과 동일)
-- ─────────────────────────────────────────────────────────────────────
ALTER TABLE app_project_types ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_project_types" ON app_project_types;
CREATE POLICY "Allow all app_project_types" ON app_project_types FOR ALL USING (true) WITH CHECK (true);

ALTER TABLE app_projects ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_projects" ON app_projects;
CREATE POLICY "Allow all app_projects" ON app_projects FOR ALL USING (true) WITH CHECK (true);

ALTER TABLE app_project_members ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_project_members" ON app_project_members;
CREATE POLICY "Allow all app_project_members" ON app_project_members FOR ALL USING (true) WITH CHECK (true);
