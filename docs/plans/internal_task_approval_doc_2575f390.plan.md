---
name: internal task approval doc
overview: ERP 메뉴 아래 "사내 업무" 항목을 신설해 업무 등록·할당·진행·댓글·첨부·알림을 처리하는 사내 업무판을 설계한다. 알림은 (1) 인앱 종 아이콘 + (2) Solapi 카카오 친구톡 실시간 발송을 병행한다. 친구톡은 템플릿 검수 없이 즉시 도입 가능하나 직원의 회사 채널 친구추가 1회가 필요하다. 결과물은 워크스페이스 루트의 사내결제시스템.md 1개.
todos:
  - id: write_md
    content: 워크스페이스 루트에 사내결제시스템.md 생성 (메뉴 위치·데이터 모델·친구톡+인앱 푸시·구현 위치 모두 반영)
    status: completed
  - id: user_review
    content: 사용자 검토 + 열린 질문(직원 친구추가 유도 방식, 친구톡 발송 시간대 정책, paste 컴포넌트 선택, SMS fallback 여부) 확인
    status: completed
  - id: implementation_handoff
    content: 확정 시 별도 작업으로 app_users phone+kakao_friend_added 마이그레이션 → SUPABASE_APP_TASKS.sql → task_board.py → app.py ERP 라우팅 → Solapi 친구톡 발송 함수 → friend_added webhook → Supabase Storage 버킷 순으로 구현
    status: completed
isProject: false
---

# 사내결제시스템.md 작성 플랜

## 1. 산출물
- 파일: `/Users/kimseungchan/workspace/emons-web-sales/사내결제시스템.md`
- 형태: 한국어 마크다운 1개. 코드/스키마 변경 없음.

## 2. 작성할 문서 구조

### 1) 개요
- 시스템 이름: 사내 업무관리·결재 시스템 (파일명은 사용자 지정 "사내결제시스템" 유지)
- 한 줄 설명: 매장·본사 직원이 업무를 등록·할당하고 요청→진행→완료까지 추적하며, 담당자에게 자동 알림(인앱 종 아이콘 + **카카오 친구톡 실시간 푸시**)이 가는 사내 업무판.
- **메뉴 위치 (확정)**: `app.py` 사이드바 ERP 섹션(L17667~17674) 안에서 `🗓️ 근태 관리`(L17670) · `📈 인력 효율 분석`(L17672) 다음에 `📋 사내 업무` 버튼을 한 줄 추가. 라우팅은 L17717~17725 분기 블록 끝에 `if active_admin_page == "internal_work": render_internal_work(); return` 추가.
- 권한: 전 역할 노출(자기 업무는 누구나 처리 가능). `store_admin`/`superadmin`은 매장/전체 범위 관리.

### 2) 핵심 요구사항(사용자 명시)
- 상위 업무 / 하위 업무(트리 구조)
- 담당자 지정(다중 가능). 지정/상태 변경 시 관련 담당자 전원에게 알림.
- 일정(시작일·마감일)
- 상태: 요청 → 진행(세부: 진행 / 피드백) → 완료 / 보류
- 이미지 업로드 및 클립보드 페이스트(Ctrl+V)
- 댓글 자유 입력

### 3) 데이터 모델 (Supabase 신규)

#### 3-1. 기존 테이블 마이그레이션 (선결 작업)
- **`app_users` 컬럼 추가 (필수)**:
  - `phone TEXT` — 친구톡 발송 대상 식별. Solapi 친구톡은 발신프로필(채널) + 수신자 phone으로 발송.
  - `kakao_friend_added BOOLEAN DEFAULT false` — 회사 카카오채널 친구추가 완료 여부. friend_added webhook 수신 또는 관리자 수기 체크로 갱신.
  - `kakao_notify_enabled BOOLEAN DEFAULT true` — 본인이 카카오 알림 끄기 가능.
  - 사유: 현재 `SUPABASE_APP_TABLES.sql` L14~22의 `app_users`는 `id, username, password, email, role, store_id, name`만 보유.
  - 마이그레이션 파일: `SUPABASE_APP_USERS_PHONE.sql` 신설 → `_supabase_run_app_tables_sql`에 등록(L531 부근).
  - UI: `render_employee_management` (L11479~) / `render_store_admin_employees` (L12549~) 직원 등록·수정 폼에 phone 입력 + 친구추가 상태 표시(읽기 전용 배지) + 알림 토글 추가.

#### 3-2. 신규 테이블
- `app_tasks` — id, parent_task_id NULLABLE, title, description, status(`requested`/`in_progress`/`feedback`/`done`/`on_hold`), priority, start_date, due_date, created_by(username), store_name, db_filename, created_at, updated_at, closed_at
- `app_task_assignees` — task_id FK, employee_username, role(`owner`/`assignee`/`watcher`), assigned_at, assigned_by, UNIQUE(task_id, employee_username)
- `app_task_comments` — id, task_id FK, author(username), body, created_at, parent_comment_id NULLABLE
- `app_task_attachments` — id, task_id FK NULLABLE, comment_id FK NULLABLE, storage_path, mime_type, original_name, byte_size, uploaded_by, uploaded_at
- `app_task_activity` — id, task_id FK, actor, action(`created`/`assigned`/`unassigned`/`status_changed`/`commented`/`attached`/`due_changed`), payload JSONB, created_at
- `app_notifications` — id, recipient_username, task_id FK NULLABLE, type, message, link_path, channel(`in_app`/`friendtalk`/`both`), is_read, sent_at, read_at, kakao_msg_id NULLABLE, kakao_status(`pending`/`sent`/`failed`/`not_friend`/`out_of_hours`), kakao_error TEXT NULLABLE

#### 3-3. Supabase Storage
- 버킷 신설: `task-attachments` (private, RLS로 task FK의 store_name 기준 접근 제어).
- 현재 워크스페이스에서 Supabase Storage 사용 이력 없음 — 클라이언트 helper(`upload_task_attachment`, `signed_url_for`)도 함께 신규 작성 필요.

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

### 6) 알림 전달 방식 — 친구톡 + 인앱 종 아이콘 동시 채택

#### 6-1. 채택 이유 (알림톡 대신 친구톡)
- 알림톡은 템플릿 사전 검수(영업일 1~3일) + 광고성 표현 반려 리스크가 있어 진입 비용이 큼.
- **친구톡(FTA)은 템플릿 검수 없이 자유 메시지 발송 가능**. 직원이 회사 카카오채널을 1회 친구추가하면 알림톡과 동일한 카톡 도달.
- 친구톡은 광고성 메시지 사전동의가 필요하지만 **사내 업무 정보 안내는 정보성**으로 분류되어 동의 불필요.
- 단점: 친구추가하지 않은 직원은 카카오 도달 불가 → **인앱 종 아이콘으로 항상 보조**.

#### 6-2. v1 동작 흐름
1. 업무 이벤트(생성/배정/상태변경/댓글/첨부/마감변경) 발생
2. 각 recipient별로 `app_notifications` INSERT (channel=`both`, kakao_status=`pending`)
3. 즉시 Solapi 친구톡 send 호출 분기:
   - `phone` 없음 → `kakao_status=failed`, error="no_phone"
   - `kakao_notify_enabled=false` → `kakao_status=failed`, error="disabled"
   - 친구톡 발송 결과 받아 `kakao_status` 갱신 (sent/not_friend/out_of_hours/failed)
4. recipient의 다음 로그인/리런 시 인앱 종 아이콘에 미확인 카운트 노출 — 친구톡 실패 여부와 무관하게 항상 동작
5. 클릭 시 알림 패널 → 해당 업무로 이동 + is_read=true

#### 6-3. 친구톡 메시지 문구 (자유 송신, 검수 불요)
- 신규 배정  
  "[사내업무] {name}님, 새 업무가 배정되었습니다.\n• 제목: {title}\n• 마감: {due_date}\n• 요청자: {requester}\n바로가기: {link}"
- 상태 변경  
  "[사내업무] {title}\n상태 변경: {from_status} → {to_status} ({actor})"
- 새 댓글  
  "[사내업무] {title}\n{author}: {preview}\n바로가기: {link}"
- 마감 임박 (D-1 09:00 배치)  
  "[사내업무] 내일 마감: {title} ({due_date})\n바로가기: {link}"

#### 6-4. 친구톡 운영 제약
- **발송 시간대**: 08:00~21:00. 외 시간 발송은 `kakao_status=out_of_hours`로 저장하고 다음날 08:00 배치로 일괄 전송 또는 미발송 처리(정책 선택, §11 열린질문).
- **친구톡 도달 실패 fallback**: `kakao_status in (not_friend, out_of_hours, failed)`인 경우 (선택) SMS로 보낼지 → §11 열린질문.
- **친구톡 ↔ 직원 매핑**: phone으로 발송하므로 `app_users.phone`이 정확해야 함. 친구추가 webhook을 받으면 `kakao_friend_added=true` 자동 갱신.

#### 6-5. 직원 친구추가 유도 워크플로우
1. 직원 등록/수정 시 카카오채널 친구추가 링크를 표시(QR 또는 단축URL).
2. 직원 로그인 후 본인 프로필 화면에서 친구추가 상태가 false면 상단 배너 노출: "카카오 알림을 받으려면 회사 채널을 친구 추가해 주세요" + QR 코드.
3. Solapi가 친구추가 webhook을 제공하면 `api.py`에 엔드포인트 추가, sender_key를 phone과 매칭해 `kakao_friend_added=true`로 갱신. webhook 미지원 시엔 첫 친구톡 발송 응답의 success 여부로 추정 갱신.

#### 6-6. v2 확장 (보류)
- 알림톡 추가(친구추가 안 한 직원도 도달) — 카카오 비즈채널 검수 통과 시 채널 `friendtalk` → `alimtalk_fallback` 자동 분기.
- 카카오워크 봇 — 회사가 도입 시 추가.
- Streamlit Realtime/SSE 인앱 토스트는 채택 보류 — 친구톡이 사실상 실시간.

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

### 9) 코드 위치 (향후 구현 단계 참고)

#### 9-1. 신규 파일
- `SUPABASE_APP_USERS_PHONE.sql` — `app_users.phone`, `kakao_friend_added`, `kakao_notify_enabled` ALTER
- `SUPABASE_APP_TASKS.sql` — 위 §3-2 6개 테이블 + RLS
- `task_board.py` — 로더(`load_tasks_cached`, `load_task_detail`, `load_my_notifications`), 변경 헬퍼(`create_task`, `update_status`, `assign_users`, `post_comment`, `attach_file`), 알림 트리거(`notify_recipients` — in_app INSERT + 친구톡 발송 호출), 첨부 업로더(`upload_to_task_attachments`).
- `solapi_sender.py` (또는 `crm_automation.py`에 함수 추가) — `send_friendtalk(to_phone, body, link_url)` 신규. type=`CTA`(친구톡 텍스트) 또는 `CTI`(이미지) payload. 기존 L196~240 ATA 빌더를 참고해 FTA 빌더 추가. API 호출은 `requests.post(https://api.solapi.com/messages/v4/send-many)`. API 키는 `st.secrets`.
- `api.py` — Solapi 친구추가 이벤트 webhook 엔드포인트(`/webhook/solapi/friend-added`). 수신 phone/senderkey로 `app_users.kakao_friend_added=true` 갱신. 미지원 시 §6-5의 추정 갱신 로직만 사용.

#### 9-2. `app.py` 수정
| 위치 | 내용 |
|---|---|
| L531 부근 `_supabase_run_app_tables_sql` | `SUPABASE_APP_USERS_PHONE.sql`, `SUPABASE_APP_TASKS.sql` 추가 |
| L11479~ `render_employee_management` | phone 입력 + 친구추가 상태 배지 + kakao_notify_enabled 토글 |
| L12549~ `render_store_admin_employees` | 동일 (phone + 친구추가 상태 + 알림 토글). 친구추가 안 한 직원에게 QR 단축URL 노출 |
| L17674 직후 | `📋 사내 업무` 사이드바 버튼 (전 역할), 클릭 시 `active_admin_page="internal_work"` |
| L17725 직후 | 라우팅 분기: `if active_admin_page == "internal_work": render_internal_work(); return` |
| L17667 직전 (사이드바 상단) | 종 아이콘 + 미확인 카운트 (`load_my_notifications` 호출, 클릭 시 패널 토글) |
| 신규 함수 `render_internal_work()` | 목록(트리) + 상세(모달) + 댓글 + 첨부 + 활동로그 |

#### 9-3. Supabase Storage
- 버킷 `task-attachments` 신설(콘솔 또는 SQL: `insert into storage.buckets...`).
- 정책: 인증된 사용자가 자기 매장 task의 첨부만 읽기/쓰기.

### 10) MVP 체크리스트(문서 말미에 포함)
- [ ] `app_users.phone`, `kakao_friend_added`, `kakao_notify_enabled` 마이그레이션 + 직원 등록/수정 UI에 phone 입력·상태 배지·토글 추가
- [ ] 6개 신규 테이블 + Storage 버킷 + RLS
- [ ] ERP 사이드바 "📋 사내 업무" 버튼 + 라우팅
- [ ] 업무 목록(트리 + 상태/담당자 필터)
- [ ] 업무 상세(설명·일정·담당자·상태 변경·하위 업무 추가)
- [ ] 댓글 + 이미지 페이스트 + Supabase Storage 첨부
- [ ] `app_notifications` 적재 + 사이드바 종 아이콘 + 미확인 카운트
- [ ] **Solapi 친구톡 실시간 발송 함수 구현 + 4종 메시지 문구 코드 내장(검수 불요)**
- [ ] 회사 카카오채널 친구추가 유도 UX (QR/단축URL 노출 + 친구추가 webhook 엔드포인트)
- [ ] 발송 시간대(08~21시) 정책 적용
- [ ] 활동 로그 자동 기록
- [ ] 마감 임박 D-1 배치 알림
- [ ] 권한별 동작 검증 (user/store_admin/superadmin)

### 11) 열린 질문(문서에 명시해 사용자 추후 결정)
- 직원 phone 입력 — 매장 관리자가 일괄 등록할지, 직원이 본인 프로필에서 직접 등록할지
- 친구톡 4종 문구 — 위 §6-3 초안 그대로 사용할지, 회사 톤에 맞게 다듬을지
- 친구톡 발송 시간 외(21~08시) 발생 알림 — (a) 다음날 08시 일괄발송 (b) 미발송하고 인앱만
- 직원이 회사 채널 친구추가 안 한 경우 — (a) 인앱만 (b) SMS fallback(약 22원/건) (c) 알림톡 추가 도입(v2)
- 마감 임박 배치 — Supabase pg_cron / 외부 cron(GitHub Actions) / Edge Function 중 어느 것
- 이미지 페이스트 컴포넌트 후보 — `streamlit-paste-button` vs `st.file_uploader` 폴백 vs 커스텀 HTML 중 선택

## 3. 작업 순서
1. 위 §2(1~11) 구조대로 `사내결제시스템.md` 생성 — 코드 변경 없음
2. 사용자 검토 받고 §11 열린 질문 확정
3. 확정 후 별도 작업으로 다음 순서로 구현:
   a. `SUPABASE_APP_USERS_PHONE.sql` + 직원 폼에 phone·친구추가 상태·알림 토글 추가
   b. `SUPABASE_APP_TASKS.sql` + Storage 버킷
   c. `task_board.py` (CRUD + 인앱 알림 트리거)
   d. `solapi_sender.send_friendtalk` 구현 + 4종 메시지 문구 코드 내장 + 발송 시간대 정책
   e. `app.py` ERP 사이드바 버튼 + 라우팅 + 종 아이콘 + 친구추가 QR 배너
   f. `api.py` 친구추가 webhook 엔드포인트 (Solapi 지원 시)
   g. 마감 임박 배치(스케줄러 결정 후)
   h. 권한·실발송 통합 테스트
