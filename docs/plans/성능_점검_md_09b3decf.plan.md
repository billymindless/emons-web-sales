---
name: 성능 점검 MD
overview: 앱 전역 로딩/렌더 병목을 조사한 결과를 바탕으로, 우선순위별 개선 방향과 구체적 조치가 담긴 성능 점검 MD 문서를 작성합니다. 코드 변경은 하지 않고 문서만 생성합니다.
todos: []
isProject: false
---

# 프로그램 로딩 성능 점검 MD 작성

## 조사 결론 (요약)

체감 지연의 핵심은 다음 5가지입니다.

1. **`clear_data_cache()`가 `st.cache_data.clear()`로 전역 캐시를 날림** — CRUD 1건 후에도 다음 페이지가 콜드 스타트
2. **`app.py` ~29K줄 + Plotly/pandas/folium eager import** — cold start 3–7초
3. **홈/근태/슈퍼관리자 화면의 순차·무캐시 Supabase 조회** — 캐시 miss 시 2–10초+
4. **N+1 패턴** (직원×매장, 매장×4테이블 루프)
5. **`st_autorefresh(5분)` + fragment 미적용 위젯** — 불필요한 full rerun

## 생성할 문서

경로: [`docs/PERFORMANCE_AUDIT.md`](docs/PERFORMANCE_AUDIT.md)

기존 계획 문서들이 [`docs/plans/`](docs/plans/)에 있으므로, 성능 점검은 `docs/` 루트에 독립 MD로 둡니다.

## 문서 구성

### 1. 현황 요약
- `app.py` 규모(~29,115줄), 캐시 현황(`@st.cache_data` 60여 곳, `@st.cache_resource` 1곳)
- 체감 병목 Top 7 (cold start / 홈 / 근태 / 슈퍼관리자 / 캐시 무효화 / autorefresh / 사이드바)

### 2. 병목 상세 (우선순위 High → Low)
각 항목에 **위치(파일·함수·대략 라인)**, **원인**, **예상 절감**, **권장 조치**를 표로 정리.

| 우선순위 | 항목 |
|---------|------|
| P0 | `clear_data_cache()` → 타깃 `.clear()`로 교체 |
| P0 | `_load_orders_supabase` 캐시 부재 / 직접 호출 통일 |
| P0 | ERP 집계(`_erp_compute_yearly_breakdown`, `_erp_compute_monthly_remaining`, planned minutes) `@st.cache_data` |
| P1 | 홈 `render_dashboard` 순차 5–6쿼리 → 병렬 또는 배치 |
| P1 | 근태 대시보드 탭: dashboard+calendar+history 동시 로드 분리(탭/지연 로드) |
| P1 | 슈퍼관리자 전매장 N×4 루프 → 배치/병렬 |
| P1 | N+1: `_get_supabase_employee_list_with_stores` bulk join |
| P2 | Plotly/folium/crm lazy import |
| P2 | fragment 확대(일일매출 차트, 기간통계, ERP 연도 select) |
| P2 | `st_autorefresh` 완화 / 사이드바 알림 경량화 |
| P3 | 문서함·자료실 전역 `st.cache_data.clear()` 제거 |
| P3 | leave_grants 중복 fetch 제거 등 소규모 중복 쿼리 |

### 3. 이미 잘 된 패턴 (유지)
- 캘린더 `_erp_fetch_range_multi` 배치화
- 마진모니터 `mm_queried` 게이트
- To-Do / KPI `@st.fragment`
- Supabase 클라이언트 싱글톤

### 4. 권장 로드맵
- **Phase 1 (즉시, 코드 소량):** 캐시 무효화 타깃화, orders 캐시 통일, ERP 집계 캐시
- **Phase 2 (중기):** 병렬 fetch, N+1 제거, 근태 탭 지연 로드
- **Phase 3 (구조):** lazy import, fragment 확대, (장기) `app.py` 모듈 분리

### 5. 측정 방법
- Streamlit 스피너/로그로 화면별 첫 진입·캐시 hit 시간 기록
- 주요 화면 체크리스트: 로그인 → 홈 → 근태 대시보드 → 슈퍼관리자 통합

## 범위

- **이번 작업:** MD 문서 작성만 (코드 수정·커밋은 계획 승인 후 별도 요청 시)
- 근거는 `app.py`, `task_board.py`, `post_board.py`, `project_board.py`, `deposit_board.py`, `lead_management.py` 등 조사 결과 반영
