---
name: bank-deposit-sms
overview: 기업은행 입출금 SMS([Web발신] 형식)를 사업장 휴대폰에서 webhook으로 포워딩 받아, 마스킹 계좌 끝8자리로 매장을 자동 분류하고 입출금 원장에 적재한 뒤, '메뉴 선택' 드롭다운의 '입금 관리' 화면에서 매장 드롭다운으로 매장별 조회·매출 연결·직원 알림까지 제공한다.
todos:
  - id: sql
    content: SUPABASE_APP_BANK_ACCOUNTS.sql, SUPABASE_APP_DEPOSITS.sql 신설 및 app.py 자동실행 목록 등록
    status: completed
  - id: parser
    content: "deposit_sms.py: 기업은행 [Web발신] 입출금 파서, 계좌 끝8자리→매장 매칭, 중복방지 해시"
    status: completed
  - id: webhook
    content: "api.py: POST /webhook/sms/deposit 엔드포인트(토큰 인증, app_deposits upsert)"
    status: completed
  - id: board_module
    content: "deposit_board.py: load/create/update/link/delete (캐시+CRUD 패턴)"
    status: completed
  - id: ui
    content: "app.py render_deposit_management(): 매장드롭다운·입출금토글·KPI카드·검색·표·수기등록·매출연결"
    status: completed
  - id: routing
    content: "app.py: 메뉴 선택 드롭다운(tab_labels)에 입금 관리 추가 + idx 분기 + 관리자설정 계좌-매장 매핑 UI"
    status: completed
  - id: noti
    content: (선택) 입금 수신 시 해당 매장 직원 친구톡/토스트 알림
    status: pending
isProject: false
---

## 입금 SMS 연동 · 입금 관리 화면 구현 계획

### 1. 데이터베이스 (신규 SQL 2개)

`SUPABASE_APP_BANK_ACCOUNTS.sql` — 계좌-매장 매핑
- `id`, `bank_name`(예: 기업은행), `account_suffix`(매칭 키, 문자에 보이는 끝 8자리 예 `16401011`), `account_masked`(표시용 `392***16401011`), `account_alias`, `store_name`(app_stores.store_name과 일치), `is_active`, `created_at`
- 문자의 계좌가 마스킹(`392***16401011`)되어 오므로 **끝 8자리 suffix**로 매장 판별. 샘플상 `16401011` ↔ `16501015`가 서로 다른 매장.

`SUPABASE_APP_DEPOSITS.sql` — 입출금 원장
- `id`, `txn_type`(`deposit`|`withdrawal`), `txn_at`(timestamp, 문자의 `YYYY/MM/DD HH:MM`), `counterparty`(거래처/입금자명 예 디지털온누리), `amount`(numeric), `balance`(numeric, 문자의 잔액), `bank_name`, `account_suffix`, `account_masked`, `store_name`(미분류 시 NULL), `source`(`auto_sms`|`manual`), `linked_sale_id`(매출 연결), `raw_message`(원문 보관), `dedup_hash`(중복 방지 UNIQUE), `created_by`, `created_at`
- `[app.py](app.py)` 540~543, 599~603행의 자동 실행 SQL 목록에 두 파일 등록.

### 2. SMS 파서 모듈 (신규 `deposit_sms.py`)
실제 기업은행 `[Web발신]` 형식 (줄 단위):
```
[Web발신]
2026/06/01 11:30
입금 3,716,000원        (또는 출금)
잔액 4,801,905원
디지털온누리
392***16401011
기업
```
- `parse_ibk_sms(text)`: 줄 분해 후 추출 — `txn_at`(2번째 줄 `\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}`), `txn_type`+`amount`(`(입금|출금)\s+([\d,]+)원`), `balance`(`잔액\s+([\d,]+)원`), `counterparty`(거래처명 줄), `account_masked`/`account_suffix`(`\d{3}\*+(\d+)` → 끝 8자리), 은행=`기업`. 형식 불일치 시 None.
- `match_store(account_suffix, accounts)`: 끝 8자리 일치로 매장 판별, 없으면 None(미분류).
- `make_dedup_hash(...)`: `txn_at`+`txn_type`+`amount`+`account_suffix`+`counterparty` 해시로 중복 방지(동일시각·동일거래처 다건도 금액/계좌로 구분).

### 3. Webhook 엔드포인트 ([api.py](api.py))
- 기존 FastAPI 앱에 `POST /webhook/sms/deposit` 추가 (friend-added 패턴·`_supa_headers`/`_supa_url` 재사용).
- 인증: `X-Webhook-Token` 헤더를 환경변수 `SMS_WEBHOOK_TOKEN`과 비교(불일치 시 401).
- 본문 → `parse_ibk_sms` → `match_store` → `app_deposits` upsert(`dedup_hash` 충돌 시 무시). 입금/출금 모두 적재(`txn_type` 구분).
- 미분류(계좌 suffix 매칭 실패)도 `store_name=null`로 적재.
- (선택) 입금 적재 시 해당 매장 담당자에게 Solapi 친구톡 발송(출금은 알림 제외).

### 4. 입금 관리 화면 ([app.py](app.py) 신규 `render_deposit_management()`)
스크린샷 레이아웃 재현:
- 상단 **매장 선택 드롭다운(`st.selectbox` "매장 선택")**: 선택한 매장의 입금만 필터링해 표시.
  - `superadmin`: `전체` + 각 매장 + `미분류`(계좌 매칭 실패 건) 선택.
  - `store_admin`/`user`: 본인 소속 매장으로 고정(복수 소속이면 해당 매장만 드롭다운에 노출).
- **입금/출금 토글**: 기본 `입금`만 표시, `출금`/`전체` 전환 가능(`txn_type` 필터).
- 상단 KPI 카드 4개(선택 매장·입금 기준): 이번 달 입금액 / 당해년도 누적 입금액 / 입금 건수 / 매출 연결 건수.
- 검색창(거래처명·은행·메모) + 정렬 가능한 표: 일시 / 거래처명 / 금액 / 잔액 / 은행 / 출처(수기·자동) / 매출 연결.
- "입금 등록" 버튼: 수기 입금 추가(`source='manual'`).
- 각 행 "매출 연결": 선택 매장 미수금 주문/매출(`sales`)을 드롭다운 선택해 `linked_sale_id` 연결.
- 데이터 접근: `task_board.py`/`post_board.py`의 `@st.cache_data` + 모듈 CRUD 패턴을 따른 `deposit_board.py` 신설.

### 5. 메뉴 드롭다운 라우팅 + 관리자 설정 ([app.py](app.py))
- "메뉴 선택" 드롭다운(`tab_labels`, 22590·22602행)에 `입금 관리` 항목 추가:
  - `store_admin` 메뉴와 `user` 메뉴 양쪽 `tab_labels`에 삽입(번호 재정렬).
  - `superadmin`은 `_SUPERADMIN_MENUS`(17236행)에도 추가.
- `idx` 분기(22653행 이하)에 `render_deposit_management()` 호출 추가 — 선택 시 페이지 내 매장 드롭다운으로 매장별 확인.
- `render_admin_settings()`(15866행)에 "계좌-매장 매핑 관리" 섹션 추가: `app_bank_accounts` CRUD UI + webhook URL/토큰 안내.

### 6. 운영 가이드 (사업장 휴대폰 설정)
- 안드로이드 SMS 포워딩 앱에서: 발신번호=기업은행, 본문 키워드="입금" 필터 → webhook URL로 POST, `X-Webhook-Token` 설정.
- 계좌번호로 자동 분류되므로 매장별 별도 URL 불필요.

### 검증 기준
- 샘플 입금 SMS를 webhook에 POST → `app_deposits`에 올바른 매장·금액·입금자로 1건 적재, 동일 문자 재전송 시 중복 미적재.
- 미등록 계좌 문자 → 미분류로 적재, 계좌 등록 후 화면에서 매장 지정 가능.
- 입금 관리 화면에서 매장별 필터·KPI·매출 연결 동작.