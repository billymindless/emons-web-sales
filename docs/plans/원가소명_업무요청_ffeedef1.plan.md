---
name: 원가소명 업무요청
overview: 원가 불일치 행에서 판매담당자에게 사내 업무로 소명 요청을 보내고, 같은 화면에서 차액·메모·Ctrl+V 이미지를 붙여 등록·답신할 수 있게 한다. 기존 task_board·클립보드 첨부 API를 재사용한다.
todos:
  - id: resolve-assignees
    content: 행 employee_names → 사내 업무 username 자동 매칭 + 기존 업무 태그 조회
    status: completed
  - id: hq-explain-ui
    content: 3번 탭 cost_mismatch/cost_blank에 차액·메모·Ctrl+V 소명 요청 블록 추가
    status: completed
  - id: hq-explain-thread
    content: 생성 후 같은 화면에서 댓글+붙여넣기 답신, 중복 생성 방지
    status: completed
  - id: verify-push
    content: 문법 확인 후 커밋·푸시
    status: completed
isProject: false
---

# 원가 불일치 → 사내 업무 소명 요청

## 배경

스크린샷의 `김수홀 허유진`처럼 **본사원가 ≠ 앱(모모) 원가**일 때, 관리자가 사내 업무판으로 나가서 업무를 새로 쓰지 않고 **3번 탭에서 그 행을 고른 뒤** 바로 요청한다.

이미 있는 것:
- 업무 생성·담당자·알림: [`task_board.create_task`](task_board.py) / `notify_recipients` (인앱 + 친구톡)
- 댓글·첨부: [`post_comment`](task_board.py), [`attach_file`](task_board.py)
- Ctrl+V 이미지: [`_file_input_with_paste`](app.py) (사내 업무 등록과 동일)

새 테이블/컬럼은 만들지 않는다. 업무 `tags`로 원가 건을 식별한다.

```mermaid
flowchart LR
  pick["3번탭 행 선택"] --> form["차액 표시 + 메모 + Ctrl+V"]
  form --> create["create_task + attach_file"]
  create --> notify["담당자 인앱/친구톡"]
  notify --> reply["같은 화면 댓글+붙여넣기 또는 사내 업무 메뉴"]
```

## 넣는 위치

[`_render_hq_edit_row_action`](app.py) — `cost_mismatch` / `cost_blank` 일 때, 기존 「선택한 원가로 확정」 아래에 **「판매담당 소명 요청」** 블록을 추가한다.

확정/매장전시 버튼은 그대로 둔다. 소명 요청은 원가를 바꾸지 않는다 (`app_orders` / `sales` 미변경).

## UI (한 행당)

선택 행을 클릭(기존 selectbox)하면 아래에 고정 표시:

- 고객명, 등록일, 전화
- 본사원가 / 앱(모모) 원가 / 앱 전시원가
- **차이 = 앱원가 − 본사원가** (예: `+97,899`)
- 판매담당: 행 `employee_names` + 매칭된 주문 `employee_names` (쉼표 분리)
- 담당자 `multiselect`: [`_internal_work_employee_options`](app.py)에서 **표시명(김수홀, 허유진)으로 username 자동 선택**. 매칭 실패 시 관리자가 직접 고름
- 메모 `text_area` (필수). placeholder 예: `본사 6,689,301 / 앱 6,787,200. 차이 사유 회신 바랍니다.`
- 이미지: `_file_input_with_paste` (Ctrl+V + 파일 선택), 미리보기는 기존 `_render_upload_preview`
- 버튼: `소명 요청 보내기`

제목 자동 생성 (수정 가능):

`[원가소명] {고객명} · {등록일} · 차이 {차이:,}원`

본문 자동 붙임 + 메모:

```
매장: 울산 삼산점
고객: ...
전화: ...
등록일: ...
본사원가: ...
앱(모모) 원가: ...
차이: ...
담당: ...

요청 메모:
(관리자 입력)
```

태그: `hq-cost,{ship 또는 order_id},{등록일}` — 같은 행을 다시 열면 기존 업무를 찾아 **재생성하지 않고** 스레드를 보여 준다.

## 보낸 뒤 / 답신

같은 블록에서:

- 업무 번호 `#id` 와 상태 표시
- 기존 댓글 + 첨부 썸네일
- 답신 `text_area` + `_file_input_with_paste` → `post_comment` + `attach_file`
- 안내: `📋 사내 업무` 메뉴에서도 동일 건 확인·회신 가능

담당자는 기존 사내 업무 알림을 받고, 거기서도 댓글/Ctrl+V로 답한다. HQ 화면 쪽은 관리자가 요청·후속 메모를 끊기지 않고 쓰게 한다.

## 구현 메모

- 신규 헬퍼는 [`app.py`](app.py)에만 둔다. `_hq_resolve_assignees(employee_names, store_id, role)`, `_hq_find_explain_task(tags)`, `_render_hq_cost_explain_request(...)`.
- `task_board.py` API는 호출만 한다. 스키마 변경 없음.
- 위젯 key는 `hq_explain::{cache_key}::{row_index}` 로 탭 2/3 중복 키를 피한다.
- 담당자가 0명이면 보내기 막고, 직원 목록에서 고르라고 한다.
- 권한: 지금 3번 탭과 동일 (`store_admin` / `superadmin`).
- 해당 HQ UI 개편 plan 파일은 수정하지 않는다.

## 확인

- 3번 탭에서 불일치 행 선택 → 차액이 스크린샷과 같이 보이는지
- 담당 자동 선택(김수홀, 허유진) → 보내기 → 사내 업무에 업무·알림 생성
- Ctrl+V 이미지가 업무 첨부로 저장되는지
- 같은 행 재선택 시 기존 업무가 열리고 댓글 답신이 붙는지
- 원가 확정 버튼을 눌러도 주문 금액이 소명 요청과 섞이지 않는지
