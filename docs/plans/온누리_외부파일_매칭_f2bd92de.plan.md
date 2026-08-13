# 온누리/울산페이 외부파일 매칭 검증

원본 플랜은 `.cursor/plans/온누리_외부파일_매칭_f2bd92de.plan.md`. 저장소 보관용 사본.

## 목적

직원이 신규고객 매출로 입력한 **온누리(전자)** / **울산페이(앱 지역화폐)** 결제를,
가맹점 포털에서 받은 공식 내역과 대조해 미입력·허위입력·결제 후 임의취소를 찾는다.

## 확정 규칙

- 대상: **신규고객 매출**로 넣은 건. 수단은 `온누리`(지류 제외) 또는 `지역화폐`(울산페이).
- 검증 시작일: 관리자 설정. 기본값 **2026-08-01**.
- 온누리 식별: **결제일 + 구매자 전화번호 뒤 4자리 + 금액**. 다중 후보는 거래시간·시간으로 보조.
- 재업로드: 공식 행 **fingerprint UNIQUE** — 중복 적재·중복 매칭 방지.
- KPI/집계/결제 저장 로직은 변경 없음. **입력 스탬프**와 **검증 UI**만 추가.

## 데이터 (SUPABASE_APP_EXTERNAL_PAY_MATCH.sql)

| 테이블 | 요점 |
|--------|------|
| `app_external_pay_settings` | `db_filename` UNIQUE · `verify_from_date` (기본 2026-08-01) |
| `app_external_pay_batches` | 업로드 1회 = 배치 1건. `source`, `file_name`, `parsed/inserted/skipped` |
| `app_external_pay_rows` | 공식 1행. `fingerprint` UNIQUE = `source|db|date|time|last4|amount|tx_status` |
| `app_external_pay_matches` | `row_id` UNIQUE → `payment_id`. `result_code` 저장 |
| `app_orders.entry_source` | 신규고객 매출은 `new_customer_sale` 로 스탬프 |

## 결과 코드

- `matched_ok`
- `official_only` (미입력)
- `official_canceled` (공식 취소 · ERP 잔존 → 임의취소 의심)
- `erp_canceled_official_paid` (ERP 취소 · 공식 결제완료)
- `ambiguous` (같은 날·뒤4·금액 다중 후보)
- `erp_only` (파일에 없는 ERP 결제 · 카운트만 표시)

## 시나리오 확인

- **중복 업로드 방지**: fingerprint UNIQUE + `_ext_pay_insert_batch_and_rows` 에서 예외
  `duplicate/unique/23505/conflict` 감지 시 skip 카운트만 증가. 매칭 테이블도
  `row_id` UNIQUE 로 두 번째 매칭 시도가 실패해도 신규 결과에 영향 없음.
- **임의취소 감지**: 공식 `거래상태`에 "취소" 포함 + ERP에 해당 결제 잔존 →
  `official_canceled` 로 표시. 반대로 ERP만 취소 흔적(같은 order_id 음수 결제)
  이면 `erp_canceled_official_paid`.
- **시작일 이전 skip**: `verify_from_date` 이전 tx_date 는 적재 자체를 건너뜀.

## UI

관리자 설정 **「8. 온누리/울산페이 외부파일 대사」** 에 통합.

1. 검증 시작일 저장 (매장별)
2. 매장/출처(온누리/울산페이) 선택
3. 파일 업로드 → 파싱 → 신규/중복/시작일이전 skip 카운트 표시 → 자동 매칭
4. 결과 표: 필터 "미결·취소 의심만"

## 울산페이 (등록됨)

- 파일: 거래내역서. 상단 가맹점 정보 행은 헤더(`거래일시`/`승인번호`/`결제금액`)를 찾아 스킵.
- 금액: **결제금액** (오타 `결재금액` 허용). `거래금액`은 사용하지 않음.
- 식별자: **승인번호 6자리** → `app_external_pay_rows.approval_code`.
- ERP 매칭: `payment_method` 에 `지역화폐` 포함 + `card_company` 숫자 뒤 6자리 + 금액.
- 지문: 온누리 형식 뒤에 `|approval_code` 를 붙여 재업로드 중복 방지.
