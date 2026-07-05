---
name: internal task approval doc
overview: 사용자가 첨부 이미지처럼 요청한 "사내 업무관리·결재" 기능 요구사항을 한국어 마크다운으로 정리한 사내결제시스템.md를 워크스페이스 루트에 생성한다. 코드 변경 없이 기획·설계 문서만 산출하며, 추후 구현 단계의 입력값으로 사용한다.
todos: []
isProject: false
---


# 사내결제시스템.md 작성 플랜

## 1. 산출물
- 파일: `/Users/kimseungchan/workspace/emons-web-sales/사내결제시스템.md`
- 형태: 한국어 마크다운 1개. 코드/스키마 변경 없음.

## 2. 작성할 문서 구조

### 1) 개요
- 시스템 이름: 사내 업무관리·결재 시스템 (파일명은 사용자 지정 "사내결제시스템" 유지)
- 한 줄 설명: 매장·본사 직원이 업무를 등록·할당하고 요청→진행→완료까지 추적하며, 담당자에게 자동 알림이 가는 사내 업무판.
- 기존 emons-web-sales(Streamlit + Supabase) 위에 좌측 사이드바 신규 메뉴 "사내 업무판"으로 추가.

### 2) 핵심 요구사항(사용자 명시)
- 상위 업무 / 하위 업무(트리 구조)
- 담당자 지정(다중 가능). 지정/상태 변경 시 관련 담당자 전원에게 알림.
- 일정(시작일·마감일)
- 상태: 요청 → 진행(세부: 진행 / 피드백) → 완료 / 보류
- 이미지 업로드 및 클립보드 페이스트(Ctrl+V)
- 댓글 자유 입력

### 3) 데이터 모델 (Supabase 신규)
- `app_tasks` — id, parent_task_id NULLABLE, title, description, status(`requested`/`in_progress`/`feedback`/`done`/`on_hold`), priority, start_date, due_date, created_by, store_name, db_filename, created_at, updated_at, closed_at
- `app_task_assignees` — task_id FK, employee_name, role(`owner`/`assignee`/`watcher`), assigned_at, assigned_by
- `app_task_comments` — id, task_id FK, author, body, created_at, parent_comment_id NULLABLE
- `app_task_attachments` — id, task_id FK 또는 comment_id FK, file_path(Supabase Storage), mime_type, original_name, uploaded_by, uploaded_at
- `app_task_activity` — id, task_id FK, actor, action(`created`/`assigned`/`status_changed`/`commented`/`attached`/`due_changed`), payload JSONB, created_at
- `app_notifications` — id, recipient_employee, task_id FK, type, message, link, is_read, created_at, read_at
- Supabase Storage 버킷: `task-attachments` (RLS: 본인 매장 업무만 R/W)

### 4) 화면 구성
- **목록 화면**(좌측 메뉴 클릭 시 진입)
  - 상단: 상태별 필터(요청/진행/피드백/완료/보류), 담당자 필터, 검색
  - 본문: 트리 테이블(상위 업무 펼치면 하위 업무). 컬럼 — 제목 / 상태 뱃지 / 담당자 / 마감일 / 댓글수
  - "새 업무 등록" 버튼
- **업무 상세 모달/페이지**
  - 제목, 설명, 상태(드롭다운으로 변경), 담당자(다중 선택), 일정(시작·마감 date_input)
  - 본문 영역: 댓글 타임라인. 가장 아래 입력창은 멀티라인 + 이미지 페이스트 가능(브라우저 paste 이벤트 → base64 → Supabase Storage 업로드)
  - 우측 사이드: 활동 로그(누가 언제 무엇을 바꿨는지)
  - "하위 업무 추가" 버튼
- **알림 UI**
  - 사이드바 상단에 종 아이콘 + 미확인 카운트
  - 클릭 시 최근 알림 리스트 패널, 알림 클릭 → 해당 업무로 이동 + is_read=true

### 5) 알림 트리거 규칙
- 업무 생성 → 담당자 전원에게 "신규 업무 배정"
- 담당자 추가/제거 → 해당 인원에게 "배정됨"·"제외됨"
- 상태 변경 → owner + 모든 assignee + watcher
- 댓글 작성 → 댓글 작성자 제외 전원
- 첨부 업로드 → 동일
- 마감일 변경 → 동일
- 마감 임박(D-1) 일 1회 배치 → 미완료 업무의 담당자에게 리마인드

### 6) 알림 전달 방식
- 1차: Streamlit 앱 진입/리런 시 `app_notifications`에서 본인 미확인 카운트 조회 → 종 아이콘 표시.
- 2차(선택): Solapi 알림톡으로 미확인 24h 초과 알림 일괄 푸시(기존 Solapi 인프라 재사용).
- 3차(v2): Streamlit Realtime/SSE는 채택 보류.

### 7) 권한 모델
- `user`: 본인 매장 업무 조회, 본인이 담당자/작성자인 업무 수정 가능
- `store_admin`: 매장 전체 업무 R/W, 상위 업무 잠금 가능
- `superadmin`: 전 매장 R/W, 강제 종료 가능
- 행 수준 보안(RLS)은 기존 `app_orders`처럼 store_name/db_filename 기반.

### 8) 이미지 페이스트 구현 메모
- Streamlit 기본 `st.text_area`는 paste 이벤트를 직접 못 잡음.
- 해결책 후보:
  - (a) `streamlit-paste-button` 또는 `streamlit-image-input` 커스텀 컴포넌트 도입
  - (b) `st.file_uploader` + 사용자 안내 ("Ctrl+V 후 붙여넣기 버튼")
  - (c) HTML iframe `st.components.v1.html`로 contenteditable + paste 핸들러 직접 구현 → base64 POST
- 1차 권장: (a) 외부 컴포넌트, 안 되면 (b)로 폴백.

### 9) 코드 위치(향후 구현 단계 참고)
- 신규 `SUPABASE_APP_TASKS.sql` → `_supabase_run_app_tables_sql` 등록
- 신규 `task_board.py` — 로더(`load_tasks_cached`), 알림 헬퍼(`notify_assignees`, `mark_read`), 첨부 업로더
- `app.py` 사이드바 라우팅(L17564~L17638)에 "📋 사내 업무판" 버튼 + `render_task_board()` 분기 추가
- 알림 종 아이콘은 사이드바 상단(`active_admin_page` 분기 위쪽)에 항상 표시

### 10) MVP 체크리스트(문서 말미에 포함)
- [ ] 6개 테이블 + Storage 버킷 + RLS
- [ ] 업무 목록(트리 + 필터)
- [ ] 업무 상세(설명·일정·담당자·상태 변경)
- [ ] 댓글 + 이미지 페이스트
- [ ] `app_notifications` 적재 + 종 아이콘 + 미확인 카운트
- [ ] 활동 로그 자동 기록
- [ ] 권한별 동작 확인

### 11) 열린 질문(문서에 명시해 사용자 추후 결정)
- 파일명을 "사내결제시스템"으로 유지할지, "사내업무판" 등으로 바꿀지(요구사항이 업무관리에 더 가까움)
- 알림톡 푸시 v1에 포함할지 v2로 미룰지
- 마감 임박 배치 알림의 시점(D-1 09:00 등)
- 이미지 페이스트 컴포넌트 후보 중 무엇으로 진행할지

## 3. 작업 순서
1. 위 구조대로 `사내결제시스템.md` 생성
2. 사용자 검토 받고 §11 열린 질문 확정
3. 확정 후 별도 작업으로 SQL/코드 구현 착수
