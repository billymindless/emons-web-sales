# AI 주간/월간 세일즈 리포트 자동 생성 플랜

> 작성일: 2026-07-11
> 목적: 매주 금요일(주간) · 매월 말(월간)에 매장별 + 전 매장 통합 세일즈 리포트를 AI(Gemini)로 자동 생성해 Supabase에 저장하고, Streamlit UI에서 열람 · Markdown/Excel 다운로드
> 스택: Gemini 1.5 Flash (기존) + FastAPI (`api.py`) + Render Cron / GitHub Actions + Supabase + Streamlit

---

## 1. 배경 및 목표

### 1.1 목적
- 매주 금요일 15:00 KST 에 각 매장 및 전 매장 통합의 지난 주(월~일) 세일즈 리포트를 AI 로 자동 생성
- 매월 1일 00:30 KST 에 전월 월간 리포트 자동 생성
- 사용자는 Streamlit UI에서 리포트 목록 조회 · 본문 열람 · Markdown/Excel 다운로드
- 관리자는 필요 시 특정 기간의 리포트를 수동으로도 재생성 가능

### 1.2 최종 산출물
| 산출물 | 형식 | 저장 위치 |
|---|---|---|
| 주간 리포트 | Markdown + Excel 워크북 | `app_sales_reports` 테이블 + Supabase Storage |
| 월간 리포트 | 동일 | 동일 |
| 실행 로그 | 성공/실패 · 처리 시간 | `app_sales_reports.status`, `error_message` |

---

## 2. 데이터 가용성 및 제약사항

### 2.1 사용 가능한 데이터 (기존 테이블 · 컬럼)

| 테이블 | 활용 컬럼 | 리포트 기여 |
|---|---|---|
| `app_orders` | `order_date`, `total_amount`, `cost_price`, `actual_margin`, `employee_names`, `category`, `visit_reason`, `purchase_reason`, `display_sales_amount`, `display_cost_amount` | 매출·마진·객단가·품목·유입경로·구매사유·직원 |
| `sales` | `transaction_date`, `amount`, `note`, `employee_names` | **순매출 원장** (조정·반품 반영, 리포트의 매출 기준) |
| `app_payments` | `payment_date`, `amount`, `payment_method`, `card_company` | 실수납 · 결제수단 breakdown |
| `app_customers` | `name`, `address`, `sigungu`, `bname`, `road_name`, `building_name`, `latitude`, `longitude` | 지역·아파트·건물유형 분석 |
| `app_leads` | `lead_source`, `lead_stage`, `converted_at`, `revenue_amount`, `followup_done` | 리드 전환율 · 사후관리 |
| `app_voc_insights` | `is_claim`, `complaint_category`, `sentiment`, `summary` | 클레임·감성 (선택적, 주간에는 요약만) |

### 2.2 확인된 제약사항

1. **품목별 매출 불가**
   - `app_orders.category` 는 콤마 구분 문자열 (예: `"옷장, 식탁"`)
   - **금액은 주문 단위**라 품목 차원 매출 · 객단가는 중복 계상 위험
   - → 품목은 **건수 · 비중** 만 리포트

2. **아파트명 없음**
   - `apartment_name` 컬럼 없음
   - → `building_name` (카카오 API 파생) + `address` 사용

3. **전년동월 데이터 유무는 매장별 상이**
   - 신규 매장은 전년 데이터 없음
   - → 조회 후 데이터 존재 시에만 YoY 섹션 포함

4. **모델번호 · 상품명 없음**
   - 개별 상품 SKU 없음 → 리포트에서 SKU 단위 분석 불가

5. **Streamlit 배치 스케줄 없음**
   - Streamlit 은 요청형 앱
   - → 스케줄러는 `api.py` FastAPI + 외부 크론 (Render Cron / GitHub Actions)

---

## 3. 리포트 콘텐츠 스펙

### 3.1 리포트 구조 (Markdown)

```
# {매장명} 주간 세일즈 리포트 · {YYYY-MM-DD} ~ {YYYY-MM-DD}

## 1. 이번 주 요약 (Executive Summary) — AI 생성 3~5 문장

## 2. 핵심 KPI
| 지표 | 이번 주 | 지난 주 (WoW) | 전년동주 (YoY) |
| 순매출 (sales.amount 합) | ... | +12.3% | +5.1% |
| 판매건수 (app_orders count) | ... | ... | ... |
| 객단가 | ... | ... | ... |
| 마진율 (%) | ... | ... | ... |
| 실수납액 (payments) | ... | ... | ... |

## 3. 매출 분포
### 3.1 직원별 (Top 5) — 순매출 · 마진 · 배분
### 3.2 지역별 (시군구 Top 5) — 매출 · 건수
### 3.3 아파트/건물별 (Top 10) — building_name 기준
### 3.4 카테고리별 — 건수 · 비중 (매출액 아님)
### 3.5 결제수단별 — 실수납 breakdown

## 4. 고객 유입 · 구매 동기
### 4.1 방문 경로 (visit_reason) — 건수 · 매출 · 비중
### 4.2 구매 이유 (purchase_reason) — 건수 · 매출 · 비중
### 4.3 방문 × 구매 매트릭스 (핵심 조합 Top 5)

## 5. 리드 활동 (app_leads)
- 전환율, 신규 리드, 계약 완료, 평균 클로징 기간, 사후관리 성실도

## 6. 위험 · 후속 조치 (AI 생성)
- 미수금 D-10 이내 리스트
- 급락 지표
- 다음 주 액션 제안

## 7. 부록
- 세부 데이터 링크 (다운로드된 Excel 참고)
```

### 3.2 각 섹션 데이터 소스 매핑

| 섹션 | 함수 재사용 | 신규 로직 |
|---|---|---|
| 1. Executive | Gemini 프롬프트 | 요약 프롬프트 템플릿 |
| 2. KPI | `_render_main_kpi_section` 로직 추출 | WoW/YoY 계산 헬퍼 |
| 3.1 직원별 | `_kpi_employee_totals_from_sales_slice` | 그대로 |
| 3.2 지역별 | `_render_multi_dim_analysis` groupby 로직 (`sigungu`) | 함수 추출 |
| 3.3 아파트/건물 | 동일 (`building_name`) | 동일 |
| 3.4 카테고리 | `_render_marketing_multi_period_comparison` category split | 동일 |
| 3.5 결제수단 | `render_monthly_payment_report` 로직 | 주간 단위로 축소 |
| 4.1 방문경로 | 마케팅 인사이트 그대로 | 라벨 정규화 재사용 |
| 4.2 구매이유 | 동일 | 동일 |
| 4.3 매트릭스 | `_render_multi_dim_analysis` 히트맵 | 히트맵 → Top 5 텍스트 |
| 5. 리드 | `render_sales_kpi_dashboard` | 그대로 |
| 6. 위험 | `render_dashboard` 미수금 섹션 (28747-28775) + Gemini | 요약 프롬프트 |

### 3.3 월간 리포트 추가 섹션

- **월간 트렌드:** 주차별 매출 라인 차트 (Excel 워크북에만)
- **전월 · 전년 동월 비교:** 상세 %
- **월 목표 달성률:** 매장 · 직원별
- **월간 Top 고객:** 총 구매액 Top 20 (재구매 여부 표시)

---

## 4. 아키텍처

### 4.1 컴포넌트 구성

```
┌──────────────────────────────────────────────────────────────────┐
│                        스케줄러 (외부)                             │
│  Render Cron / GitHub Actions                                     │
│  - 주간: 매 금요일 15:00 KST                                        │
│  - 월간: 매월 1일 00:30 KST                                         │
└─────────────────────────────┬─────────────────────────────────────┘
                              │ HTTPS POST
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                   api.py (FastAPI) — 신규 엔드포인트                │
│                                                                    │
│  POST /generate-sales-report                                       │
│    body: { period: "weekly" | "monthly",                          │
│            start_date, end_date,                                   │
│            store_key: "<db_filename>" | "all",                    │
│            regenerate: bool }                                     │
│                                                                    │
│  로직:                                                             │
│  1) sales_report_service.build_dataset(...)                        │
│     ├─ Supabase 조회 (orders/sales/payments/customers/leads)      │
│     └─ pandas 집계 (WoW/YoY/직원/지역/카테고리/유입/구매)          │
│  2) sales_report_service.call_gemini(dataset_summary)              │
│     └─ Gemini 1.5 Flash JSON (executive/highlights/risks/actions) │
│  3) sales_report_service.render_markdown(dataset, ai_result)       │
│  4) sales_report_service.render_excel(dataset)                     │
│  5) Supabase Storage 업로드 + app_sales_reports upsert            │
└──────────────────────────────┬────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                        데이터 저장 (Supabase)                      │
│                                                                    │
│  Table: app_sales_reports                                          │
│    id, period_type (weekly|monthly), start_date, end_date,        │
│    store_key ("all" 또는 db_filename), title,                     │
│    markdown_body (TEXT),                                          │
│    excel_url (Storage),                                           │
│    ai_summary (JSONB),                                            │
│    metrics (JSONB — 핵심 KPI 원본 값),                             │
│    status ("success"/"failed"/"running"),                         │
│    error_message, generated_at, generated_by                      │
│                                                                    │
│  Storage: sales-reports/{period}/{store}/{yyyymmdd}.xlsx          │
└──────────────────────────────┬────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Streamlit UI (app.py) — 신규 메뉴                │
│                                                                    │
│  📊 AI 세일즈 리포트                                                │
│    ├─ 리포트 목록 (기간·매장 필터 + 최신순)                          │
│    ├─ 본문 열람 (st.markdown)                                       │
│    ├─ Markdown 다운로드 (.md)                                       │
│    ├─ Excel 다운로드 (Storage URL)                                  │
│    └─ [관리자] 수동 재생성 버튼 → api 호출                          │
└──────────────────────────────────────────────────────────────────┘
```

### 4.2 신규 모듈 구조

```
sales_report_service.py    (신규 · 순수 함수 모듈, 테스트 가능)
  ├─ build_dataset(period, start, end, store_key) -> dict
  ├─ compute_kpi_with_wow_yoy(orders_df, sales_df, ...) -> dict
  ├─ group_by_region(orders, customers) -> DataFrame
  ├─ group_by_building(orders, customers) -> DataFrame
  ├─ group_by_employee(sales, orders) -> DataFrame
  ├─ group_by_category(orders) -> DataFrame
  ├─ group_by_visit_purchase(orders) -> (DataFrame, DataFrame, DataFrame)
  ├─ compute_lead_kpi(leads_df, start, end) -> dict
  ├─ collect_risks(orders, payments, today) -> dict
  ├─ call_gemini(dataset_summary) -> {executive, highlights, risks, actions}
  ├─ render_markdown(dataset, ai_result) -> str
  └─ render_excel(dataset) -> bytes

api.py (기존 확장)
  └─ POST /generate-sales-report  (신규)

app.py (기존 확장)
  ├─ 좌측 메뉴에 "📊 AI 세일즈 리포트" 추가
  ├─ render_ai_sales_reports() (신규 함수)
  └─ (선택) 대시보드에 "최근 주간 리포트" 카드 링크
```

### 4.3 왜 이 스택인가

| 결정 | 선택 | 근거 |
|---|---|---|
| LLM | **Gemini 1.5 Flash** | `api.py` 1939-1996 에서 이미 VOC 분석용으로 운영 중. `GEMINI_API_KEY` 환경변수 확립. 별도 패키지 불필요 (httpx REST) |
| 스케줄러 | **Render Cron (권장) 또는 GitHub Actions** | 코드베이스에 이미 존재하는 관례 (`api.py:2018`, `imweb_sync.py`). 별도 인프라 불필요 |
| 배치 서버 | **`api.py` FastAPI 엔드포인트** | 기존 `/run-lead-care` 와 동일 패턴 → 확장 최소 |
| 저장 | **Supabase Table + Storage** | 앱 전체 데이터 소스 통일. Streamlit 열람 시 즉시 액세스 |
| Excel | **openpyxl** | 이미 `requirements.txt` 존재. `render_monthly_payment_report` 에서 활용 중 |
| PDF | ❌ **불포함** (Phase 1) | 신규 패키지(reportlab / weasyprint) 필요. Markdown + Excel 만으로 사용자 요구 충족. Phase 3 검토 |

---

## 5. 구현 페이즈

### Phase 1 — MVP (본 계획서의 핵심)

**목표:** 매장별 · 전체 통합 주간 리포트 자동 생성 · Streamlit UI 열람 · Markdown 다운로드

1. **DB 스키마:** `SUPABASE_APP_SALES_REPORTS.sql` 생성 (테이블 + Storage bucket + RLS)
2. **`sales_report_service.py` 신규 모듈:**
   - `build_dataset` : 기간 조회 + 집계
   - `compute_kpi_with_wow_yoy`, `group_by_*`, `compute_lead_kpi`, `collect_risks`
   - `call_gemini` : Gemini JSON 응답
   - `render_markdown` : 최종 Markdown 문서
3. **`api.py` 확장:**
   - `POST /generate-sales-report`
   - `POST /generate-weekly-reports` (편의: 모든 매장 + all 통합 순차 실행)
4. **`app.py` 신규 UI:**
   - `render_ai_sales_reports()` : 목록 · 열람 · Markdown 다운로드
   - 좌측 사이드바 메뉴에 진입점 추가
5. **스케줄러 설정:**
   - `render.yaml` 에 Cron Job 추가 (매 금요일 15:00 KST → 06:00 UTC)
   - 또는 `.github/workflows/weekly-sales-report.yml` (선택)

**예상 개발 규모:** 신규 모듈 ~600 줄, DB 스키마 ~40 줄, UI ~150 줄, api.py ~80 줄

### Phase 2 — Excel 워크북 · 월간 리포트

1. `render_excel` : 5~7개 시트 (요약 · 직원 · 지역 · 카테고리 · 유입/구매 · 결제 · 리드)
2. Storage 업로드 + Streamlit 다운로드 버튼
3. 월간 리포트: 주차별 트렌드 · Top 20 고객 · 월 목표 달성률
4. `api.py` `/generate-monthly-reports`
5. 스케줄러: 매월 1일 00:30 KST

### Phase 3 — 개선 (선택)

- PDF 출력 (reportlab)
- 이메일 자동 발송 (관리자 대상, gmail_manager 재사용)
- LLM 프롬프트 A/B: 다국어 · 톤 옵션
- 인사이트 알림 (급락 지표 감지 시 Slack/카카오톡)

---

## 6. 세부 데이터 명세 (Phase 1 build_dataset)

```python
{
  "period_type": "weekly",
  "start_date": "2026-07-06",
  "end_date": "2026-07-12",
  "store_key": "emons_ulsan.db",  # or "all"
  "store_name": "울산삼산점",
  "generated_at": "2026-07-11T15:00:00+09:00",

  "kpi": {
    "sales_amount": 12345678,
    "sales_count": 42,
    "aov": 293946,
    "margin_rate": 0.185,
    "margin_amount": 2280000,
    "payments_amount": 10500000,
    "prev_week": { "sales_amount": ..., "diff_pct": +12.3 },
    "prev_year": { "sales_amount": ..., "diff_pct": +5.1 } | null
  },

  "by_employee": [
    {"name": "김태완", "sales": 5200000, "margin": 950000, "count": 12}, ...
  ],
  "by_region": [
    {"sigungu": "울산 남구", "sales": 6300000, "count": 22}, ...
  ],
  "by_building": [
    {"name": "삼산동 라이온스빌", "sales": 2100000, "count": 3}, ...
  ],
  "by_category": [
    {"category": "옷장", "count": 18, "share_pct": 42.8}, ...
  ],
  "by_visit_reason": [...],
  "by_purchase_reason": [...],
  "visit_purchase_matrix_top5": [...],
  "by_payment_method": [...],

  "leads": {
    "conversion_rate": 0.32,
    "new_leads": 15,
    "closed_deals": 5,
    "avg_closing_days": 8.2,
    "followup_rate": 0.78
  },

  "risks": {
    "unpaid_d10": [{"customer": "...", "order_id": ..., "balance": ...}, ...],
    "declining_metrics": ["margin_rate WoW -3.2%p"],
    "total_unpaid": 45000000
  },

  "ai_summary": {  # Gemini 응답
    "executive": "이번 주 매출은 전주 대비 12% 증가... (3~5문장)",
    "highlights": ["김태완 매출 1위 유지", "울산 남구 매출 집중"],
    "risks": ["구매이유 '단순교체' 비중 급증 → 객단가 하락 우려"],
    "actions": ["다음 주 신혼/혼수 타겟 마케팅 강화", "..."]
  }
}
```

---

## 7. Gemini 프롬프트 설계 (초안)

```
system: 당신은 가구 매장 판매 데이터 분석가입니다. 아래 JSON 지표를 바탕으로
        경영진용 주간 리포트 요약을 작성하세요. 사실 기반, 객관적, 실행 가능한
        3~5 문장. 다음 스키마의 JSON 만 출력하세요:
        {
          "executive": string (3~5문장),
          "highlights": string[] (최대 3개, 각 1문장),
          "risks": string[] (최대 3개),
          "actions": string[] (최대 5개, 다음 주 실행 항목)
        }

user: 매장: {store_name}
      기간: {start_date} ~ {end_date}
      KPI: {kpi}
      직원별: {by_employee_top3}
      지역별: {by_region_top3}
      카테고리: {by_category_top3}
      방문경로: {by_visit_reason_top3}
      구매이유: {by_purchase_reason_top3}
      리드: {leads}
      리스크: {risks}
```

- **모델:** `gemini-1.5-flash` (VOC와 동일)
- **응답 형식:** `response_mime_type: application/json`
- **온도:** 0.3 (분석은 결정적)
- **실패 시:** AI 섹션은 "요약 생성 실패 (사유: ...)" 로 대체, 나머지 데이터는 정상 렌더

---

## 8. Streamlit UI 상세

### 8.1 진입점

- 좌측 사이드바 > (관리자용) 메뉴에 `📊 AI 세일즈 리포트` 추가
- 슈퍼관리자는 전체 매장 볼 수 있고, 매장 관리자·일반 사용자는 소속 매장만

### 8.2 화면 구성

```
[상단 필터]
  ├─ 기간 유형: [주간 / 월간]
  ├─ 매장: [전체 통합 / 각 매장]
  └─ 연·월(월간) 또는 주 시작일(주간)

[리포트 리스트]  (최신순, 카드 or 표)
  ┌────────────────────────────────────────────────────┐
  │ 📅 2026-07-06 ~ 2026-07-12 · 울산삼산점 · 주간      │
  │ 순매출 12.3M (+12.3% WoW)  마진율 18.5%             │
  │ [본문 보기] [Markdown ⬇] [Excel ⬇]  [🔄재생성]      │
  └────────────────────────────────────────────────────┘

[본문 보기] 클릭 시 → 우측 확장 or 팝업으로 st.markdown 렌더

[관리자 전용] 상단에 [수동 생성] 버튼
  ├─ 기간·매장 선택 → api 호출 → 진행 상태 표시
```

### 8.3 캐싱

- `load_sales_reports_cached(period, store, limit=20)` — TTL 300s
- 재생성 후 `.clear()` 호출로 즉시 갱신

---

## 9. 스케줄러 세부

### 9.1 Render Cron (권장)

`render.yaml` 에 추가:
```yaml
- type: cron
  name: weekly-sales-report
  schedule: "0 6 * * 5"        # 매 금요일 06:00 UTC = 15:00 KST
  command: "curl -X POST https://<api-host>/generate-weekly-reports -H 'Authorization: Bearer $CRON_SECRET'"

- type: cron
  name: monthly-sales-report
  schedule: "30 15 1 * *"      # 매월 1일 15:30 UTC = 익일 00:30 KST
  command: "curl -X POST https://<api-host>/generate-monthly-reports -H 'Authorization: Bearer $CRON_SECRET'"
```

### 9.2 GitHub Actions (대안)

`.github/workflows/weekly-sales-report.yml`:
```yaml
on:
  schedule:
    - cron: "0 6 * * 5"
jobs:
  trigger:
    runs-on: ubuntu-latest
    steps:
      - run: |
          curl -X POST ${{ secrets.API_HOST }}/generate-weekly-reports \
            -H "Authorization: Bearer ${{ secrets.CRON_SECRET }}"
```

### 9.3 인증

`api.py` 엔드포인트는 `CRON_SECRET` 환경변수와 Bearer 헤더로 인증.

---

## 10. 개방된 결정 사항 (사용자 확인 필요)

| # | 결정 | 옵션 | 기본안 |
|---|---|---|---|
| Q1 | 스케줄러 위치 | Render Cron / GitHub Actions | **Render Cron** (기존 관례 · 이미 `render.yaml` 존재) |
| Q2 | 리포트 문서 형식 | Markdown만 / Markdown+Excel / +PDF | **Phase 1 Markdown → Phase 2 Excel 추가** |
| Q3 | 리포트 열람 권한 | 슈퍼관리자 전용 / 매장관리자+ / 전 직원 | **매장관리자 이상** (일반 사용자는 대시보드로 충분) |
| Q4 | LLM 제공자 | Gemini 1.5 Flash / GPT-4o / Claude 3.5 | **Gemini 1.5 Flash** (기존 스택 · 비용 최저) |
| Q5 | 저장 기간 | 무제한 / 12개월 자동 아카이브 | **무제한** (Supabase Storage 저렴) |
| Q6 | 실패 시 알림 | 없음 / Slack / 이메일 | **Slack** (기존 관례 없음 → 관리자 이메일도 고려) |
| Q7 | 월간 목표 대비 | 매장 목표 · 직원 목표 어디서? | ERP `_erp_get_monthly_target_row` 재사용 |

---

## 11. 리스크 및 완화

| 리스크 | 완화 |
|---|---|
| Gemini API 실패로 AI 요약 누락 | 데이터 섹션만이라도 렌더 · 재시도 + `status=failed` 로그 |
| 대용량 매장에서 리포트 생성 시간 초과 | 매장별 순차 생성 (병렬 X) · 타임아웃 30초 · Supabase 페이지네이션 |
| 스케줄러 미실행 (외부 인프라 장애) | Streamlit UI에서 관리자가 수동 재생성 가능 |
| 데이터 정합성 (매장별 커스텀 컬럼) | `build_dataset` 에서 컬럼 존재 여부 방어 코드 |
| 신규 매장은 전주/전년 없음 | 옵션 필드로 처리, UI 에서 "-" 표시 |

---

## 12. 성공 지표 (Phase 1 완료 조건)

1. 매 금요일 15:00 KST 에 모든 매장 + 전체 통합 리포트가 자동 생성 · Supabase 저장
2. Streamlit UI에서 매장관리자가 최신 리포트 열람 · Markdown 다운로드 가능
3. AI 요약이 매출/직원/유입/구매 데이터를 반영한 자연어로 3~5문장 생성
4. 실패 시 `status=failed` + `error_message` 로그 · 재생성 가능
5. 리포트 1건 생성 총 소요 시간 < 15초 (매장 1개 기준)

---

## 13. 다음 단계 (본 계획서 승인 후)

1. **Q1~Q7 결정 확정** (사용자 확인)
2. **Phase 1 스켈레톤 커밋:**
   - `SUPABASE_APP_SALES_REPORTS.sql`
   - `sales_report_service.py` (build_dataset · render_markdown · call_gemini 뼈대)
   - `api.py` `POST /generate-sales-report` (단일 매장 · 단일 기간)
3. **단위 테스트:** 특정 매장·기간에 대해 로컬에서 리포트 1건 수동 생성 · 결과 검증
4. **Streamlit UI:** 목록 · 열람 · 다운로드 (Excel 없이 Markdown만)
5. **스케줄러 활성화:** Render Cron
6. **Phase 2:** Excel 워크북 · 월간 리포트
