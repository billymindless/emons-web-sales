---
name: Project Management Refactor
overview: 사내업무·게시판을 프로젝트 중심 구조로 전환. 매장과 무관한 전사 단위 프로젝트가 1차이고, 모든 직원이 프로젝트 생성/수정 가능, 삭제는 superadmin + PM만, 다중 PM 지원, 프로젝트별 공개/비공개. 기존 업무·게시글은 '미분류' 프로젝트로 일괄 마이그레이션.
todos:
  - id: sql_schema
    content: "SUPABASE_APP_PROJECTS.sql 작성: app_projects, app_project_types, app_project_members + 시드 카테고리"
    status: pending
  - id: sql_migration
    content: "SUPABASE_APP_PROJECTS_MIGRATION.sql 작성: app_tasks/app_posts에 project_id 추가 + '미분류' 프로젝트로 이관"
    status: pending
  - id: backend_module
    content: "project_board.py 작성: CRUD/멤버/타입/권한 헬퍼/통계"
    status: completed
  - id: ui_list
    content: 프로젝트 목록 화면 구현 (통계 카드, 필터, 테이블)
    status: pending
  - id: ui_detail
    content: 프로젝트 상세 화면 구현 (개요/업무/게시판/팀 탭)
    status: pending
  - id: ui_form
    content: 프로젝트 추가/수정 폼 + 유형 자유 추가 기능
    status: pending
  - id: menu_routing
    content: 사이드바 라벨 변경 + 라우팅 교체 + 레거시 active_admin_page 리디렉트
    status: pending
  - id: task_integration
    content: 기존 render_internal_work + _render_board_posts_section에 project_id 인자 연결, 저장 시 project_id 자동 주입
    status: pending
  - id: permission_test
    content: 권한 통합 테스트 (생성/수정/삭제, public/private, PM 권한)
    status: pending
isProject: false
---

# 프로젝트 관리 전환 계획

## 1. 확정된 결정사항 (요약)

- **프로젝트 성격**: 내부 운영 단위 (매장과 무관, 전사 공통)
- **고객/매출 컬럼**: 제거 → 컬럼 = 프로젝트명 / 유형 / 상태 / 담당자(PM·팀원) / 기간
- **카테고리(유형)**: 관리자가 자유롭게 추가/수정/삭제 (DB로 관리)
- **권한**: 누구나 생성/수정, 삭제는 superadmin + 해당 프로젝트 PM
- **팀 구성**: 다중 PM + 팀원 N명 (2단계)
- **공개 범위**: 프로젝트별 `public/private` 설정 (생성 시 선택)
- **1차 활성화 콘텐츠**: 업무(`app_tasks`) + 게시판(`app_posts`) 두 섹션 모두 활성화
- **기존 데이터**: '미분류' 프로젝트로 일괄 이관
- **사이드바**: `📋 사내업무/게시판` 라벨 → `📊 프로젝트 관리`로 교체
- **기술 스택**: Streamlit 유지 (HTML/JS 임베드는 필요 시 추후)

## 2. 디폴트로 가는 사소한 결정 (변경 원하시면 말씀)

- **프로젝트 코드(이미지의 `[26-7]` 같은 형식)**: `YY-N` 자동 생성 (연도 뒤 2자리 + 순번)
- **마이그레이션 시 기존 `store_name`**: 프로젝트 메모로 보관 후 제거 (예: "구 매장: 울산삼산점")
- **이미지 우상단 "일정관리" 버튼**: 1차 범위에서 제외 (간트차트는 v2)
- **상단 통계 카드 4종**: 전체 / 진행중 / 담당자 미배정 / 완료 (누적 매출 자리 = 완료)
- **PM에게 카카오 친구톡 알림**: 기존 `app_notifications` + 친구톡 흐름 그대로 활용
- **빈 프로젝트(업무 0건) 삭제**: PM만 직접 삭제, 업무 있는 프로젝트는 "보류/취소" 권장

## 3. 데이터 모델

### 신규 테이블 (`SUPABASE_APP_PROJECTS.sql`)

```sql
-- 프로젝트 마스터
CREATE TABLE app_projects (
    id BIGSERIAL PRIMARY KEY,
    code VARCHAR(20) UNIQUE,                       -- '26-7'
    name VARCHAR(200) NOT NULL,
    type_id BIGINT REFERENCES app_project_types(id),
    status VARCHAR(20) DEFAULT '진행예정',         -- 진행예정|진행중|완료|보류|취소
    visibility VARCHAR(10) DEFAULT 'public',       -- public|private
    description TEXT,
    start_date DATE,
    end_date DATE,
    created_by VARCHAR(100) NOT NULL,              -- username
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    archived_at TIMESTAMPTZ
);

-- 카테고리(유형) 마스터
CREATE TABLE app_project_types (
    id BIGSERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    display_order INT DEFAULT 0,
    is_active BOOLEAN DEFAULT TRUE
);
-- Seed: 미분류, 신제품 론칭, 매장 운영, 마케팅·행사, 교육·인사, IT·시스템, 기타

-- 팀 멤버
CREATE TABLE app_project_members (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL REFERENCES app_projects(id) ON DELETE CASCADE,
    employee_username VARCHAR(100) NOT NULL,
    role VARCHAR(10) NOT NULL,                     -- pm|member
    assigned_by VARCHAR(100),
    assigned_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(project_id, employee_username)
);
CREATE INDEX idx_apm_user ON app_project_members(employee_username);
CREATE INDEX idx_apm_project ON app_project_members(project_id);
```

### 기존 테이블 확장 (`SUPABASE_APP_PROJECTS_MIGRATION.sql`)

```sql
-- app_tasks에 project_id 추가
ALTER TABLE app_tasks ADD COLUMN IF NOT EXISTS project_id BIGINT REFERENCES app_projects(id);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON app_tasks(project_id);

-- app_posts에도 project_id 추가 (1차 활성화 대상)
ALTER TABLE app_posts ADD COLUMN IF NOT EXISTS project_id BIGINT REFERENCES app_projects(id);
CREATE INDEX IF NOT EXISTS idx_posts_project ON app_posts(project_id);

-- '미분류' 프로젝트 생성 후 기존 데이터 이관
INSERT INTO app_projects (code, name, type_id, status, visibility, created_by)
VALUES ('LEGACY', '미분류 (이관)', (SELECT id FROM app_project_types WHERE name='미분류'), '진행중', 'public', 'system');

UPDATE app_tasks
SET project_id = (SELECT id FROM app_projects WHERE code='LEGACY')
WHERE project_id IS NULL;
UPDATE app_posts
SET project_id = (SELECT id FROM app_projects WHERE code='LEGACY')
WHERE project_id IS NULL;
```

## 4. 백엔드 모듈 추가

새 파일: `project_board.py` (기존 `task_board.py`/`post_board.py`와 동일 패턴)

핵심 함수:
- `load_projects_cached(user, filters)` — 권한·필터 적용한 목록
- `get_project(project_id)` — 단일 프로젝트 + 멤버 + 통계
- `create_project(payload, current_user)` — code 자동 채번 포함
- `update_project(project_id, patch)` — 권한 체크
- `delete_project(project_id, current_user)` — superadmin/PM만
- `set_members(project_id, members)` — `[{username, role}, ...]` upsert
- `list_project_types()` / `upsert_project_type()` / `deactivate_project_type()`
- `project_stats()` — 상단 카드용 (전체/진행중/담당자 미배정/완료)
- `can_view(project, user)` / `can_edit(project, user)` / `can_delete(project, user)` — 권한 헬퍼

## 5. UI 화면 (`render_project_management()` in `app.py`)

### 5-1. 목록 화면 (첨부 이미지 참고)

상단 영역:
- 헤더 `📊 프로젝트 관리` + 우측 `+ 프로젝트 추가` 버튼
- 통계 카드 4종 (`st.metric` 또는 `st.columns([1,1,1,1])` 내부 카드 div)
- 검색박스 (프로젝트명/담당자/설명)
- 유형 필터 (`st.segmented_control`, 동적 카테고리)
- 상태 필터 (`st.segmented_control`: 전체/진행예정/진행중/완료/보류/취소)
- 좌측: 담당자 필터 (사이드 컬럼 `st.columns([1, 4])` 좌측)
- 우측: 프로젝트 테이블 (`st.dataframe` + 행 클릭으로 상세 진입)

### 5-2. 프로젝트 상세 화면

- 헤더: 코드 + 프로젝트명 + 상태 배지 + 우측 액션(수정/PM 삭제)
- 탭: `개요 / 업무 / 게시판 / 팀`
  - 개요: 설명, 기간, PM 목록, 팀원 목록, 진행률(완료/전체 업무)
  - 업무: 기존 `_render_task_card()` 재활용, 새 업무 등록 시 `project_id` 자동 주입
  - 게시판: 기존 `_render_board_posts_section()` / `_render_post_card()` 재활용, 새 글 작성 시 `project_id` 자동 주입
  - 팀: 멤버 추가/제거 (username multiselect), 역할 토글 (PM ↔ 팀원)

### 5-3. 프로젝트 추가/수정 폼

- 프로젝트명, 유형(selectbox + "+새 유형 추가"), 상태, 공개 여부(public/private)
- 시작일/종료일, 설명
- PM(multiselect, 필수 1명 이상), 팀원(multiselect)

## 6. 라우팅·메뉴 변경

- `app.py` L22848–22868 사이드바 `📋 사내업무/게시판` 라벨 → `📊 프로젝트 관리`
- `active_admin_page = 'internal_board'` → `'project_mgmt'`로 변경
- 라우팅 분기에서 `render_project_management()` 호출
- 레거시 `'internal_board'`, `'internal_work'`은 → `'project_mgmt'`로 자동 리디렉트

## 7. 기존 업무/게시판 화면의 변경 범위

### 7-1. 업무 (`render_internal_work`)
- **기능 자체는 유지** (프로젝트 상세의 "업무" 탭에서 그대로 사용)
- 호출 시 `project_id` 컨텍스트를 받도록 시그니처에 인자 추가
- `_render_new_task_form()` 저장 시 현재 프로젝트의 `project_id` 자동 주입
- 기존의 `store_name`/`scope` 기반 필터링은 **유지하되 비활성화** (project_id 우선)

### 7-2. 게시판 (`_render_board_posts_section`)
- **기능 자체는 유지** (프로젝트 상세의 "게시판" 탭에서 그대로 사용)
- 호출 시 `project_id` 컨텍스트 인자 추가
- `_render_new_post_form()` 저장 시 `project_id` 자동 주입
- 기존 `scope`/`store_name` 필터는 비활성화 (project_id 우선)

## 8. 단계별 작업 순서

1. SQL: 새 테이블 + 마이그레이션 (`SUPABASE_APP_PROJECTS.sql`) — superadmin이 Supabase에서 1회 실행
2. `project_board.py` 작성
3. `render_project_management()` 목록·상세·폼 UI 구현
4. 사이드바 라벨/라우팅 교체
5. 기존 `render_internal_work()` + `_render_board_posts_section()`에 `project_id` 인자 연결
6. 권한 헬퍼 (`can_view/can_edit/can_delete`) 통합 테스트
7. 마이그레이션 동작 확인 후 운영 배포

## 9. 위험 요소 / 사용자 확인 필요

- **마이그레이션 후 롤백 불가** — 운영 DB 백업 권장 (Supabase 대시보드 백업)
- **알림 흐름** — 기존 친구톡/`app_notifications` 템플릿이 `task_id` 기반이므로 추가 변경 최소
- **사이드바 메뉴 라벨 교체**: 사용자가 익숙해질 시간 필요. 첫 진입 시 1회 토스트로 안내 권장