# 프로그램 로딩 성능 점검 (Performance Audit)

> 작성일: 2026-07-10
> 마지막 갱신: 2026-07-11 (Phase 1 적용 완료)
> 대상: `emons-web-sales` Streamlit 앱 (`app.py` 및 부속 모듈)
> 목적: 앱 전역 로딩·렌더 병목을 식별하고, 우선순위별 개선 방향을 정리한 실행 지향 문서

## 진행 현황

| Phase | 상태 | 커밋 |
|-------|------|------|
| Phase 1 (P0 + Phase1-5 + P3-1) | ✅ 완료 (2026-07-11) | 아래 상세 참조 |
| Phase 2 | ⏳ 대기 | - |
| Phase 3 | ⏳ 대기 | - |

### Phase 1 적용 내역

1. **P0-1** `clear_data_cache()` 리팩터 완료
   - `st.cache_data.clear()` 전역 clear 제거
   - 세일즈·고객·매장·직원·To-do 도메인 캐시만 개별 `.clear()` 호출 (24개 함수)
   - 도메인별 헬퍼 신설: `_invalidate_orders()`, `_invalidate_payments()`, `_invalidate_customers()`
2. **P0-2** `_load_orders_supabase` 에 `@st.cache_data(ttl=1800)` 추가 (payments 와 대칭)
3. **P0-3** ERP 집계 3함수 캐싱
   - `_erp_compute_monthly_planned_minutes` (ttl=120)
   - `_erp_compute_monthly_remaining` (ttl=120)
   - `_erp_compute_yearly_breakdown` (ttl=180)
   - `_erp_invalidate_fetch_caches` 에 무효화 훅 추가
4. **Phase1-5** `st_autorefresh` interval 5분 → 15분
5. **P3-1** 문서함 3개 `st.cache_data.clear()` → `_fetch_docs.clear()` 국소화

### Phase 1 예상 체감 개선

- 저장 후 재렌더: **1–3초 단축** (ERP/문서/즐겨찾기 캐시 유지)
- 근태 재진입: **1–2초 단축** (집계 함수 캐시 히트)
- 홈 마진 모니터 · 백업 · 잔금 UI: **0.5–3초 단축** (`_load_orders_supabase` 캐시 히트)
- 5분 → 15분 주기 완화로 유휴 시간 rerun 부담 감소

---

## 1. 현황 요약

### 1.1 규모

| 항목 | 값 |
|------|-----|
| `app.py` 라인 수 | 약 29,115줄 (단일 monolith) |
| 부속 모듈 | `task_board.py`, `post_board.py`, `project_board.py`, `deposit_board.py`, `lead_management.py`, `elevator_inspection.py`, `crm_automation.py`, `gmail_manager.py`, `ui_dialogs.py`, `solapi_sender.py` |
| `@st.cache_data` 사용 | 약 60개 함수 (TTL 30초 ~ 24시간) |
| `@st.cache_resource` | 1개 (`_init_system_once`) |
| `functools.lru_cache` | 없음 |

### 1.2 체감 병목 Top 7

| 순위 | 병목 | 영향 |
|------|------|------|
| 1 | `app.py` 29K줄 + Plotly / pandas / folium **eager import** | cold start **3–7초** |
| 2 | `render_erp_attendance` 기본 탭에서 대시보드 + 캘린더 + 신청내역 **동시 로드** | 근태 진입 **3–8초** |
| 3 | `render_dashboard` 순차 Supabase 5–6쿼리 (전량 로드) | 홈 **2–5초** (캐시 miss) |
| 4 | Superadmin 전매장 통합 대시보드 N × 4 테이블 순차 루프 | **수 초 ~ 10초+** |
| 5 | `clear_data_cache()` = `st.cache_data.clear()` **전역 무효화** | CRUD 1건 후 다음 페이지 콜드 스타트 |
| 6 | `st_autorefresh(300_000)` 5분 주기 full rerun | 세션 유지용이나 모든 캐시 재검증 |
| 7 | 사이드바 `task_board` 알림 매 rerun 조회 | **200–500ms** × 모든 페이지 |

---

## 2. 병목 상세 (우선순위별)

### P0 — 즉시 개선 필요 (낮은 코드 리스크, 큰 효과)

#### P0-1. 전역 캐시 무효화 제거

- **위치:** [`app.py:2598-2612`](app.py) `clear_data_cache()`
- **호출 지점:** `app.py` 내 약 50회 이상 (주문/결제/직원/매장 CRUD 직후)
- **원인:** `st.cache_data.clear()` 호출로 **앱 전체** `@st.cache_data` 함수 캐시가 한꺼번에 무효화됨. ERP(60s), 즐겨찾기(300s), 인력분석(300s), 대시보드(1800–3600s)까지 전부 콜드 스타트로 전환.
- **예상 절감:** 저장 직후 다음 페이지 렌더 **1–5초 단축**
- **권장 조치:**
  - `st.cache_data.clear()` 제거
  - 변경된 도메인 캐시만 개별 `.clear()` 호출 (예: 주문 저장 시 `load_orders_cached.clear()` + `load_sales_cached.clear()`)
  - 함수형 헬퍼: `_invalidate_orders()`, `_invalidate_customers()` 등 도메인별 함수로 분리

#### P0-2. `_load_orders_supabase` 캐시 부재 / 직접 호출 통일

- **위치:** [`app.py:2777`](app.py) `_load_orders_supabase` (캐시 없음)
- **비교:** [`app.py:2798`](app.py) `_load_payments_supabase`는 `@st.cache_data` 있음 — **비대칭 구조**
- **직접 호출 지점:** 마진 모니터 (5397–5411), 백업 (9151–9187), 신규 매출 빠른 잔금 (24187–24189) 등
- **원인:** `load_orders_cached`를 우회하여 매 렌더마다 Supabase 페이지네이션 조회
- **예상 절감:** 해당 화면 진입 시 **0.5–3초 단축**
- **권장 조치:**
  - `_load_orders_supabase`에 `@st.cache_data(ttl=1800)` 추가
  - 또는 모든 직접 호출을 `load_orders_cached`로 통일

#### P0-3. ERP 집계 함수 캐싱

- **위치:**
  - [`app.py:11076-11117`](app.py) `_erp_compute_monthly_planned_minutes`
  - [`app.py:11120-11222`](app.py) `_erp_compute_monthly_remaining`
  - [`app.py:11941+`](app.py) `_erp_compute_yearly_breakdown`
- **원인:** 캘린더 · 대시보드에서 매번 호출되며, 각 함수가 `app_attendance_logs` / `app_shift_schedules` / `app_work_adjustments`를 **전매장 무캐시** 조회
- **예상 절감:** 근태 진입 **1–3초 단축** (캐시 miss 후 두 번째 방문부터)
- **권장 조치:**
  - `@st.cache_data(ttl=60~120, show_spinner=False)` 데코레이터 추가
  - 캐시 키는 `(db_filename, employee_name, year, month)` 형태로 고정
  - CRUD 시 `.clear()` 타깃 호출

---

### P1 — 중요 (구조 리팩터 필요)

#### P1-1. 홈 대시보드 순차 Supabase 조회

- **위치:** [`app.py:28115-28140`](app.py) `render_dashboard`
- **현재:** `load_orders_cached` → `load_payments_cached` → `load_customers_cached` → `load_sales_cached` → `load_payment_history_dashboard_cached` **순차** 5–6회 round-trip
- **예상 절감:** 캐시 miss 시 **1–3초 단축**
- **권장 조치:**
  - `concurrent.futures.ThreadPoolExecutor`로 병렬 fetch (Supabase 클라이언트는 스레드 안전)
  - 또는 Supabase RPC로 필요한 KPI를 서버측 집계 후 1회 반환
  - 병렬 진행 상황을 `st.status`로 표시

#### P1-2. 근태 대시보드 탭 지연 로드

- **위치:** [`app.py:12616-12665`](app.py) `render_erp_attendance` 대시보드 브랜치
- **현재:** 기본 진입 시 `_erp_tab_dashboard` + `_erp_tab_calendar` + `_erp_render_my_adj_history` **모두 즉시 실행**
- **원인:** 캘린더는 `@st.fragment`지만 **첫 진입 시** 전부 로드
- **예상 절감:** ERP 진입 **1–4초 단축**
- **권장 조치:**
  - 방법 A: 대시보드 카드만 즉시 렌더, 캘린더·신청내역은 `st.expander(expanded=False)` 또는 `st.tabs`로 분리하여 사용자가 열 때만 로드
  - 방법 B: 캘린더를 별도 `st.tabs` 항목으로 이동 ("현황" / "캘린더" / "신청내역")

#### P1-3. Superadmin 전매장 통합 대시보드

- **위치:** [`app.py:8418-8550`](app.py) `_superadmin_tab1_integrated_dashboard`
- **현재:** "전체 매장" 선택 시 매장마다 orders / payments / sales / customers **4회** 순차 로드
- **예상 절감:** 매장 8개 기준 **3–10초 단축**
- **권장 조치:**
  - 매장 for-loop을 `ThreadPoolExecutor(max_workers=4)`로 병렬화 (매장×4 = 32 요청을 4–8 스레드로)
  - 또는 통합 캐시 함수 `_load_all_stores_summary_cached(period)` 신설하여 단일 캐시 히트 경로 제공

#### P1-4. N+1 패턴 제거

- **위치:**
  - [`app.py:1343-1345`](app.py) `_get_supabase_employee_list_with_stores` — 직원 N명 × `_get_supabase_user_store_ids`
  - [`app.py:23537-23540`](app.py) `render_employee_management` — 동일 패턴
  - [`app.py:1207-1210`](app.py) `_get_supabase_user_allowed_stores` — store_id별 개별 `maybe_single`
- **권장 조치:**
  - `app_user_stores` 전체를 1회 조회 후 Python dict로 조인
  - 매장·직원 목록은 이미 캐시되어 있으므로 in-memory join 비용 무시 가능
- **예상 절감:** 직원 관리 페이지 **0.3–1초 단축**

---

### P2 — 개선 여지 (중장기)

#### P2-1. Import 지연 로드

- **위치:** [`app.py:6-54`](app.py) 상단 import
- **현재 eager import:** `plotly.express`, `plotly.graph_objects`, `pandas`, `streamlit`, `requests`, `crm_automation`, `folium`, `streamlit_folium`, `supabase`
- **예상 cold start 부담:** **3–7초**
- **권장 조치:**
  - Plotly: 차트 렌더 함수 내부에서 lazy import (`from plotly.express import ...`)
  - Folium: 지도 표시 함수에서만 import
  - `crm_automation`: 이미 lazy 가능 구조 (현재도 try/except)이나 진입 시점 확인
  - pandas · streamlit은 필수 유지

#### P2-2. Fragment 확대

**추가 후보 (현재 fragment 없음):**

| 위치 | 트리거 | 현재 문제 |
|------|--------|-----------|
| [`app.py:12688`](app.py) `_erp_tab_dashboard` | 연도 selectbox | 연도 바꿀 때마다 근태 전체 rerun |
| [`app.py:7966`](app.py) `_render_daily_sales_multi_compare` | 월/비교월/단위 select | 대시보드 전체 rerun |
| [`app.py:28785-28794`](app.py) 홈 기간 통계 date_input | 시작/종료일 | 대시보드 전체 rerun |
| [`app.py:8435`](app.py) `_superadmin_tab1` 매장 select | 매장 변경 | superadmin 전체 rerun |

- **예상 절감:** 위젯 조작당 **0.5–2초**

#### P2-3. Autorefresh · 사이드바 완화

- **위치:**
  - [`app.py:28937`](app.py) `st_autorefresh(interval=300_000)`
  - [`app.py:549-559`](app.py) `_render_primary_nav` 사이드바 알림
- **권장 조치:**
  - autorefresh 5분 → 15분 (세션 유지 목적이라면 충분)
  - 또는 서버측 세션 refresh만 하는 heartbeat 컴포넌트로 대체
  - 사이드바 `count_unread_notifications` TTL 확대(현재 30–60s → 300s) 또는 fragment 격리

---

### P3 — 소규모 정리

#### P3-1. 문서함·자료실 전역 clear 제거

- **위치:** [`app.py:16758, 16970, 17047`](app.py) `st.cache_data.clear()`
- **조치:** 중첩 함수 `_fetch_docs.clear()` 또는 `load_documents_cached.clear()`만 호출

#### P3-2. 중복 fetch 제거

- **위치:**
  - [`app.py:12735, 12739-12743`](app.py) `_erp_compute_leave_status`와 대시보드가 `app_leave_grants` **2회** 중복 조회
  - `_erp_compute_yearly_breakdown` vs `_erp_compute_leave_status` — `app_attendance_logs` 다른 filter로 중복
- **조치:** 상위에서 1회 fetch 후 dict 전달 or 통합 캐시 함수

#### P3-3. `load_payment_history_dashboard_cached` fallback 최소화

- **위치:** [`app.py:2390-2425`](app.py)
- **원인:** 5000행 실패 시 4000행 재시도 — worst case 2회 쿼리
- **조치:** 초기 limit을 4000 등 안정값으로 고정하거나 페이지네이션 도입

---

## 3. 이미 잘 되어 있는 패턴 (유지)

| 패턴 | 위치 | 설명 |
|------|------|------|
| 캘린더 배치 조회 | [`app.py:14136-14149`](app.py) | 매장별 순차 4×N → 테이블당 1회 `.in_()` |
| 조회 게이트 | [`app.py:5388-5393`](app.py) `render_margin_monitor` | `mm_queried` 세션 플래그로 버튼 전 DB 미호출 |
| Fragment 격리 | [`app.py:28829`](app.py) `_render_dashboard_todos_only` 등 | To-Do / KPI 섹션 부분 rerun |
| Supabase 클라이언트 싱글톤 | [`app.py:689`](app.py) `get_supabase_client` | 모듈 전역 캐시, 매 rerun 재생성 방지 |
| `@st.cache_resource` 시스템 초기화 | [`app.py:28835`](app.py) `_init_system_once` | 세션당 1회 SQLite schema 체크 |

---

## 4. 권장 로드맵

### Phase 1 — 즉시 적용 (1–2일, 리스크 낮음)

1. `clear_data_cache()` 리팩터: 도메인별 `.clear()` 헬퍼로 교체
2. `_load_orders_supabase`에 `@st.cache_data(ttl=1800)` 추가
3. ERP 집계 3함수 캐시 데코레이터 추가
4. 문서함/자료실 전역 clear 제거
5. `st_autorefresh` interval 5분 → 15분

**예상 체감 개선:** 저장 후 재렌더 **1–3초 단축**, 근태 재진입 **1–2초 단축**

### Phase 2 — 구조 개선 (1–2주)

1. 홈 대시보드 병렬 fetch (`ThreadPoolExecutor`)
2. 근태 대시보드 탭 분리 (지연 로드)
3. Superadmin 매장 루프 병렬화
4. N+1 bulk join 리팩터 (`app_user_stores` 등)
5. Fragment 확대 (연도/기간/매장 select 위젯)

**예상 체감 개선:** 초회 진입 **2–5초 단축**, 위젯 조작 **0.5–2초 단축**

### Phase 3 — 장기 구조 (선택적)

1. Plotly / Folium lazy import
2. `app.py` 모듈 분할 (`erp/`, `sales/`, `admin/` 등 서브 파일로)
3. Supabase RPC로 서버측 집계 이관 (홈 KPI, superadmin 통합)
4. 백그라운드 warm-up (앱 부팅 후 주요 캐시 미리 채우기)

**예상 체감 개선:** cold start **2–4초 단축**

---

## 5. 측정 방법

### 5.1 화면별 첫 진입 시간 측정

각 병목 화면 진입 직전에 `time.perf_counter()` 로그를 삽입해 baseline 측정:

```python
import time
_t0 = time.perf_counter()
# ... 화면 렌더 ...
st.caption(f"⏱ 렌더 {time.perf_counter() - _t0:.2f}s")
```

또는 Streamlit 자체 스피너 안에 계측:

```python
with st.spinner("데이터 불러오는 중..."):
    _t0 = time.perf_counter()
    orders = load_orders_cached(...)
    print(f"[perf] orders {time.perf_counter() - _t0:.2f}s")
```

### 5.2 체크리스트 (Phase 1 전후 비교)

| 화면 | Cold (첫 로그인) | Warm (재진입) | 목표 (Phase 1 후) |
|------|------------------|---------------|-------------------|
| 로그인 → 홈 | ___초 | ___초 | -1~3초 |
| 근태 대시보드 (첫 진입) | ___초 | ___초 | -1~2초 |
| 근태 대시보드 (연도 변경) | ___초 | ___초 | -0.5~2초 |
| Superadmin 통합 대시보드 (전체 매장) | ___초 | ___초 | -3~10초 |
| 주문 저장 → 홈 복귀 | ___초 | ___초 | -1~5초 (P0-1 효과) |

### 5.3 캐시 상태 확인

Streamlit 콘솔 로그에서 `Cache miss` 로그 카운트 또는 `st.cache_data`의 `hit_rate` (커스텀 데코레이터 필요) 관찰.

---

## 6. 참고

- 조사 근거: `app.py` 및 부속 모듈의 `@st.cache_data`, `.clear()`, `get_supabase`, `_erp_fetch`, `execute()`, `st.rerun`, `pd.read`, `load_` 등 패턴 grep
- 이 문서는 실행 계획이 아닌 **점검 보고서**입니다. 실제 적용 시 각 항목별로 별도 커밋으로 분리 권장
