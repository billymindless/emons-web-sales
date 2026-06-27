# AI 기반 채널톡 VOC 자동 분석 시스템 구현 계획

> 작성일: 2026-06-25

---

## 1. 시스템 개요

채널톡 상담 데이터를 **두 가지 경로**로 수집하여 AI(gpt-4o-mini)로 분석하고, 클레임 여부·불만 카테고리·신제품 아이디어·감정 등을 추출한 뒤 대시보드로 시각화합니다.

### 데이터 수집 경로

| 경로 | 대상 | 방법 |
|---|---|---|
| **실시간 (신규)** | 앞으로 종료되는 상담 | 채널톡 `chat.closed` Webhook → api.py 자동 처리 |
| **일괄 임포트 (과거)** | 기존 누적 상담 이력 | 채널톡 통계 엑셀 다운로드 → app.py 업로드 화면에서 AI 일괄 분석 |

> **ALF 제약사항**: 채널톡 AI(ALF)는 기존 상담 데이터에 직접 접근할 수 없습니다.
> 과거 데이터는 엑셀로 직접 추출 후 이 시스템에서 분석해야 합니다.

### 전체 데이터 흐름

```
[실시간 경로]
채널톡 상담 종료 (chat.closed)
         ↓
api.py /channel-talk/webhook (기존 엔드포인트)
         ↓
   ① app_chat_history 저장 (기존)
   ② OpenAI gpt-4o-mini 분석 (신규 추가)
         ↓
   app_voc_insights 테이블 저장

[일괄 임포트 경로]
채널톡 관리자 → 통계 → 상담 통계 → 상담별 → [상담 파일 다운로드] (메시지 데이터 포함)
         ↓
app.py VOC 대시보드 → 엑셀 업로드 → OpenAI 일괄 분석
         ↓
   app_voc_insights 테이블 저장
         ↓
         ↓ (두 경로 합산)
   app.py VOC 대시보드 시각화
```

---

## 2. 현황 파악

`api.py` line 1359에 `/channel-talk/webhook` 엔드포인트가 **이미 구현**되어 있습니다.

**현재 동작:**
- `chat.closed` 이벤트 수신
- ChannelTalk Open API (`GET /v5/chats/{chatId}/messages`)로 대화 전문 수집
- `app_chat_history` 테이블에 저장

**추가할 내용:**
- OpenAI 분석 호출 (실시간 경로)
- `app_voc_insights` 저장
- 과거 데이터 일괄 임포트 UI (일괄 경로)
- VOC 대시보드 페이지

---

## 3. 과거 데이터 추출 방법 (채널톡 엑셀 다운로드)

채널톡 관리자 화면에서 과거 상담 데이터를 내려받습니다.

> 소유자(Owner) 권한의 매니저 계정만 다운로드 가능합니다.

**다운로드 경로:**

```
채널톡 관리자 로그인
  → 통계 (Statistics)
  → 상담 통계
  → 상담별 탭
  → 기간 필터 설정 (원하는 날짜 범위)
  → 우측 상단 [상담 파일 다운로드] 클릭
  → 옵션에서 [메시지 데이터 포함] 체크 후 다운로드
```

**다운로드된 엑셀의 주요 컬럼:**

| 컬럼명 | 설명 |
|---|---|
| 상담 ID | chat_id에 매핑 |
| 고객 전화번호 | customer_phone에 매핑 |
| 담당 매니저 | handled_by에 매핑 |
| 메시지 내용 | full_text (대화 전문)에 매핑 |
| 상담 종료 시각 | analyzed_at 참고용 |

---

## 4. 구현 단계

### Step 1. Supabase 테이블 생성

Supabase SQL Editor에서 아래 SQL을 실행합니다.

**파일명: `SUPABASE_APP_VOC_INSIGHTS.sql`**

```sql
CREATE TABLE IF NOT EXISTS app_voc_insights (
  id                 BIGSERIAL PRIMARY KEY,
  chat_id            TEXT UNIQUE,
  customer_phone     TEXT,
  handled_by         TEXT,
  is_claim           BOOLEAN,
  complaint_category TEXT,   -- 배송 / 제품불량 / 가격 / 응대 / 기타 / 없음
  product_idea       TEXT,
  summary            TEXT,
  sentiment          TEXT,   -- 긍정 / 중립 / 부정
  raw_json           JSONB,
  analyzed_at        TIMESTAMPTZ DEFAULT now()
);

ALTER TABLE app_voc_insights DISABLE ROW LEVEL SECURITY;
```

---

### Step 2. Render 환경변수 설정

Render 대시보드 → 서비스 선택 → **Environment** 탭에서 아래 변수를 추가합니다.

| 변수명 | 값 |
|---|---|
| `OPENAI_API_KEY` | OpenAI API 키 (sk-...) |

> OpenAI API 키 발급: [platform.openai.com/api-keys](https://platform.openai.com/api-keys)

---

### Step 3. requirements.txt 수정

```
openai>=1.30.0
```

한 줄 추가.

---

### Step 4. api.py 수정 — OpenAI 분석 로직 추가

기존 `app_chat_history` 저장 직후에 아래 로직을 삽입합니다.

```python
# OpenAI VOC 분석
openai_key = os.environ.get("OPENAI_API_KEY", "")
if full_text and openai_key:
    try:
        import openai as _openai
        _ai_client = _openai.AsyncOpenAI(api_key=openai_key)
        _prompt = (
            "다음은 가구 쇼핑몰 고객 상담 대화입니다.\n"
            "아래 항목을 분석해 JSON으로만 응답하세요:\n"
            "- is_claim: bool (클레임 여부)\n"
            "- complaint_category: str (배송/제품불량/가격/응대/기타/없음 중 하나)\n"
            "- product_idea: str (신제품·개선 아이디어, 없으면 빈 문자열)\n"
            "- summary: str (대화 1줄 요약)\n"
            "- sentiment: str (긍정/중립/부정 중 하나)\n\n"
            f"대화:\n{full_text[:3000]}"
        )
        _ai_resp = await _ai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": _prompt}],
            response_format={"type": "json_object"},
            temperature=0,
        )
        _ai_data = json.loads(_ai_resp.choices[0].message.content)

        # app_voc_insights 저장
        async with httpx.AsyncClient(timeout=5.0) as _cl:
            await _cl.post(
                _supa_url("app_voc_insights"),
                headers={**_supa_headers(), "Prefer": "resolution=ignore-duplicates"},
                json={
                    "chat_id": chat_id or None,
                    "customer_phone": phone,
                    "handled_by": handled_by,
                    "is_claim": _ai_data.get("is_claim", False),
                    "complaint_category": _ai_data.get("complaint_category", ""),
                    "product_idea": _ai_data.get("product_idea", ""),
                    "summary": _ai_data.get("summary", ""),
                    "sentiment": _ai_data.get("sentiment", ""),
                    "raw_json": _ai_data,
                },
            )
        logger.info("VOC 분석 완료: chat_id=%s sentiment=%s", chat_id, _ai_data.get("sentiment"))
    except Exception as _e:
        logger.warning("VOC OpenAI 분석 실패: %s", _e)
```

---

### Step 5. app.py 수정 — VOC 대시보드 신규 함수

#### 5-1. `render_voc_dashboard()` 함수 추가

구성 요소:
- **날짜 필터**: 이번 주 / 이번 달 / 최근 3개월 / 전체
- **KPI 카드 3개**: 총 상담 건수 / 클레임 건수 / 클레임율(%)
- **파이 차트** (Plotly): 불만 카테고리 분포
- **막대 차트** (Plotly): 감정 분포 (긍정/중립/부정)
- **신제품 아이디어 리스트**: `product_idea` 가 있는 행만 추출
- **전체 데이터 테이블**: `st.dataframe`으로 검색·정렬 가능
- **과거 데이터 일괄 임포트 섹션** (아래 Step 5-4 참조)

#### 5-2. 사이드바 버튼 추가

```python
# ERP 섹션 내 (store_admin / superadmin 전용)
if role in ("store_admin", "superadmin"):
    if st.sidebar.button("📊 고객의 소리(VOC)", width='stretch'):
        st.session_state["active_admin_page"] = "voc_dashboard"
```

#### 5-3. 라우팅 추가

```python
if role in ("store_admin", "superadmin") and \
   st.session_state.get("active_admin_page") == "voc_dashboard":
    render_voc_dashboard()
    return
```

#### 5-4. 과거 데이터 일괄 임포트 UI (엑셀 업로드)

VOC 대시보드 하단에 관리자 전용 섹션을 추가합니다.

```
[과거 상담 데이터 일괄 분석] 섹션 (st.expander로 접어두기)
  ↓
st.file_uploader — 채널톡에서 다운로드한 엑셀(.xlsx) 파일 업로드
  ↓
컬럼 매핑 안내 (상담 ID / 전화번호 / 담당자 / 메시지 내용 컬럼명 입력)
  ↓
[분석 시작] 버튼 클릭 → pandas로 엑셀 읽기
  ↓
행 순서대로 OpenAI gpt-4o-mini 호출 (진행률 표시: st.progress)
  ↓
app_voc_insights에 upsert (chat_id UNIQUE → 중복 건너뜀)
  ↓
완료 후 결과 요약 (분석 완료 N건 / 중복 건너뜀 M건)
```

**엑셀 컬럼 매핑 규칙:**

| app_voc_insights 컬럼 | 채널톡 엑셀 기본 컬럼명 (예시) |
|---|---|
| `chat_id` | `상담 ID` 또는 `Chat ID` |
| `customer_phone` | `고객 전화번호` 또는 `Phone` |
| `handled_by` | `담당 매니저` 또는 `Manager` |
| `full_text` (분석용) | `메시지 내용` 또는 `Messages` |

> 실제 컬럼명이 다를 경우 UI에서 드롭다운으로 직접 선택하도록 구현합니다.

---

## 5. AI 분석 항목 상세

| 항목 | 타입 | 설명 |
|---|---|---|
| `is_claim` | Boolean | 클레임(불만/AS/환불 등) 여부 |
| `complaint_category` | String | 배송 / 제품불량 / 가격 / 응대 / 기타 / 없음 |
| `product_idea` | String | AI가 추출한 신제품·개선 아이디어 (없으면 빈 문자열) |
| `summary` | String | 대화 내용 1문장 요약 |
| `sentiment` | String | 긍정 / 중립 / 부정 |

---

## 6. 예상 비용

| 항목 | 비용 |
|---|---|
| 채널톡 Open API | 프로 요금제 포함 (추가 비용 없음) |
| OpenAI gpt-4o-mini (실시간) | 월 500건 기준 약 $1~2 (1,500~3,000원) |
| OpenAI gpt-4o-mini (과거 일괄) | 1,000건 일괄 분석 시 약 $2~4 (1회성) |
| Supabase | 기존 인프라 활용 (추가 비용 없음) |
| Render | 기존 서버 활용 (추가 비용 없음) |

---

## 7. 구현 순서 체크리스트

### 사전 준비
- [ ] Supabase SQL Editor에서 `SUPABASE_APP_VOC_INSIGHTS.sql` 실행
- [ ] Render 환경변수에 `OPENAI_API_KEY` 추가
- [ ] `requirements.txt` — `openai>=1.30.0` 추가

### 코드 구현
- [ ] `api.py` — OpenAI 분석 + `app_voc_insights` 저장 로직 추가 (실시간 경로)
- [ ] `app.py` — `render_voc_dashboard()` 함수 구현 (대시보드 + 일괄 임포트 UI)
- [ ] `app.py` — 사이드바 버튼 + 라우팅 추가
- [ ] git commit & push → Render/Streamlit Cloud 자동 배포 확인

### 데이터 적재
- [ ] 채널톡 통계 → 상담별 → 엑셀 다운로드 (메시지 데이터 포함, Owner 권한 필요)
- [ ] app.py VOC 대시보드 → 과거 데이터 일괄 분석 섹션에서 엑셀 업로드 후 분석 실행
- [ ] 채널톡 테스트 상담 종료 후 실시간 경로 `app_voc_insights` 적재 확인
