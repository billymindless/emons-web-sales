-- 프로젝트 관리 모듈 마이그레이션
-- 기존 app_tasks / app_posts 에 project_id 컬럼 추가 후
-- '미분류 (이관)' 프로젝트로 일괄 이관.
-- 모든 DDL은 멱등 (IF NOT EXISTS / UPDATE WHERE NULL)
--
-- 실행 전제: SUPABASE_APP_PROJECTS.sql 이 먼저 실행되어 있어야 함.

-- ─────────────────────────────────────────────────────────────────────
-- 1) app_tasks / app_posts 에 project_id 컬럼 추가
-- ─────────────────────────────────────────────────────────────────────
ALTER TABLE app_tasks ADD COLUMN IF NOT EXISTS project_id BIGINT NULL REFERENCES app_projects(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_app_tasks_project ON app_tasks(project_id);

ALTER TABLE app_posts ADD COLUMN IF NOT EXISTS project_id BIGINT NULL REFERENCES app_projects(id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_app_posts_project ON app_posts(project_id);

-- ─────────────────────────────────────────────────────────────────────
-- 2) '미분류 (이관)' 프로젝트 1건 생성 (code='LEGACY')
--    이미 존재하면 건너뜀.
-- ─────────────────────────────────────────────────────────────────────
INSERT INTO app_projects (code, name, type_id, status, visibility, description, created_by)
SELECT
    'LEGACY',
    '미분류 (이관)',
    (SELECT id FROM app_project_types WHERE name = '미분류' LIMIT 1),
    '진행중',
    'public',
    '프로젝트 관리 도입 전에 작성된 기존 사내업무/게시판 데이터의 이관 보관 프로젝트입니다.',
    'system'
WHERE NOT EXISTS (SELECT 1 FROM app_projects WHERE code = 'LEGACY');

-- ─────────────────────────────────────────────────────────────────────
-- 3) 기존 app_tasks 중 project_id 가 비어있는 모든 행을 LEGACY 프로젝트로 이관
--    기존 store_name 정보는 description 메모에 보존(있는 경우)
-- ─────────────────────────────────────────────────────────────────────
UPDATE app_tasks
SET project_id = (SELECT id FROM app_projects WHERE code = 'LEGACY' LIMIT 1),
    description = COALESCE(description, '') ||
                  CASE
                      WHEN store_name IS NOT NULL AND store_name <> ''
                          THEN E'\n\n[이관 메모] 구 매장: ' || store_name
                      ELSE ''
                  END
WHERE project_id IS NULL;

-- ─────────────────────────────────────────────────────────────────────
-- 4) 기존 app_posts 중 project_id 가 비어있는 모든 행을 LEGACY 프로젝트로 이관
--    기존 store_name 정보는 content 메모에 보존(있는 경우)
-- ─────────────────────────────────────────────────────────────────────
UPDATE app_posts
SET project_id = (SELECT id FROM app_projects WHERE code = 'LEGACY' LIMIT 1),
    content = COALESCE(content, '') ||
              CASE
                  WHEN store_name IS NOT NULL AND store_name <> ''
                      THEN E'\n\n[이관 메모] 구 매장: ' || store_name
                  ELSE ''
              END
WHERE project_id IS NULL;

-- ─────────────────────────────────────────────────────────────────────
-- 5) LEGACY 프로젝트 팀: 기존 task 작성자 + 담당자를 멤버(PM 1명 = 'system')로 자동 등록
--    PM 권한이 비는 상황을 방지하기 위해 'system' 가상 사용자를 PM 으로 추가.
--    실제 운영자가 superadmin 으로 LEGACY 프로젝트에 진입해 PM 재지정 가능.
-- ─────────────────────────────────────────────────────────────────────
INSERT INTO app_project_members (project_id, employee_username, role, assigned_by)
SELECT
    (SELECT id FROM app_projects WHERE code = 'LEGACY' LIMIT 1),
    'system',
    'pm',
    'system'
ON CONFLICT (project_id, employee_username) DO NOTHING;

-- 기존 task 작성자 자동 등록(팀원)
INSERT INTO app_project_members (project_id, employee_username, role, assigned_by)
SELECT DISTINCT
    (SELECT id FROM app_projects WHERE code = 'LEGACY' LIMIT 1),
    created_by,
    'member',
    'system'
FROM app_tasks
WHERE created_by IS NOT NULL AND created_by <> ''
ON CONFLICT (project_id, employee_username) DO NOTHING;

-- 기존 task 담당자 자동 등록(팀원)
INSERT INTO app_project_members (project_id, employee_username, role, assigned_by)
SELECT DISTINCT
    (SELECT id FROM app_projects WHERE code = 'LEGACY' LIMIT 1),
    employee_username,
    'member',
    'system'
FROM app_task_assignees
WHERE employee_username IS NOT NULL AND employee_username <> ''
ON CONFLICT (project_id, employee_username) DO NOTHING;

-- 기존 post 작성자 자동 등록(팀원)
INSERT INTO app_project_members (project_id, employee_username, role, assigned_by)
SELECT DISTINCT
    (SELECT id FROM app_projects WHERE code = 'LEGACY' LIMIT 1),
    author,
    'member',
    'system'
FROM app_posts
WHERE author IS NOT NULL AND author <> ''
ON CONFLICT (project_id, employee_username) DO NOTHING;

-- ─────────────────────────────────────────────────────────────────────
-- 6) 검증용 쿼리 (실행 후 수동 확인)
-- ─────────────────────────────────────────────────────────────────────
-- SELECT 'app_tasks LEGACY 이관 건수' AS label, COUNT(*) AS cnt
-- FROM app_tasks WHERE project_id = (SELECT id FROM app_projects WHERE code = 'LEGACY');
--
-- SELECT 'app_posts LEGACY 이관 건수' AS label, COUNT(*) AS cnt
-- FROM app_posts WHERE project_id = (SELECT id FROM app_projects WHERE code = 'LEGACY');
--
-- SELECT 'app_tasks 미이관 잔여' AS label, COUNT(*) AS cnt
-- FROM app_tasks WHERE project_id IS NULL;
--
-- SELECT 'LEGACY 멤버 수' AS label, COUNT(*) AS cnt
-- FROM app_project_members WHERE project_id = (SELECT id FROM app_projects WHERE code = 'LEGACY');
