---
name: Solapi 고객채팅 연동
overview: 가이드의 3가지 핵심 개념(kakao_user_key 매핑 테이블, SOLAPI Signature 보안 검증, 고객 수신 메시지 저장)을 기존 Streamlit/FastAPI 스택에 추가한다. Next.js/Edge Function 전환은 하지 않는다.
todos:
  - id: sql-kakao-mapping
    content: kakao_mapping 테이블 + app_customer_messages.direction 컨럼 SQL 작성
    status: completed
  - id: api-signature
    content: api.py에 SOLAPI Signature 검증 헬퍼 추가
    status: completed
  - id: api-userkey
    content: api.py friend-added 웹훅에 kakao_user_key 저장 로직 추가
    status: completed
  - id: api-inbound
    content: api.py /webhook/solapi/message-received 신규 엔드포인트 구현
    status: completed
  - id: crm-inbound-ui
    content: crm_automation.py 카카오 채널 탭에 인바운드 메시지 조회 + 답장 UI 추가
    status: completed
isProject: false
---

# Solapi 고객 채팅 연동 플랜

## 현재 구현 vs 가이드 차이

| 항목 | 현재 구현 | 가이드 요구 | 추가 필요 |
|------|-----------|-------------|-----------|
| 친구추가 감지 | phone 매칭으로 kakao_friend_added=true | kakao_user_key 별도 저장 | kakao_user_key 컬럼/매핑 |
| 웹훅 보안 | 없음 | SOLAPI Signature 검증 | X-Solapi-Secret 검증 |
| 수신 메시지 저장 | 없음 | 고객 인바운드 메시지 저장 | 신규 엔드포인트 + 테이블 |
| 상담 UI | CRM 발송 이력 조회 | 실시간 채팅창 | Streamlit 채팅 패널 |

---

## 추가 구현 3단계

### Step 1: DB 확장 (SQL)

`app_customers`에 `kakao_user_key` 컬럼 추가 (이미 `kakao_user_key TEXT` 컬럼은 `SUPABASE_KAKAO_CHANNEL.sql`에 선언되어 있으나 아직 실제로 저장되지 않음):

```sql
-- app_customer_messages에 수신 메시지 저장용 컬럼 추가
ALTER TABLE app_customer_messages
  ADD COLUMN IF NOT EXISTS direction TEXT DEFAULT 'outbound';
  -- 'outbound': 우리가 발송, 'inbound': 고객이 카카오로 보낸 메시지
```

`kakao_mapping` 테이블 신규 생성 (가이드 Step 1):

```sql
CREATE TABLE IF NOT EXISTS kakao_mapping (
  id             BIGSERIAL PRIMARY KEY,
  kakao_user_key TEXT UNIQUE NOT NULL,
  customer_id    BIGINT REFERENCES app_customers(id) ON DELETE CASCADE,
  created_at     TIMESTAMPTZ DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_kakao_mapping_user_key ON kakao_mapping(kakao_user_key);
```

### Step 2: api.py 확장

**2-1. SOLAPI Signature 검증 헬퍼 추가** (보안):

Solapi는 웹훅 요청 헤더에 `X-Solapi-Secret` 값을 포함하여 보냄. 환경변수 `SOLAPI_WEBHOOK_SECRET`과 비교하여 위조 요청 차단:

```python
def _verify_solapi_signature(request_headers: dict) -> bool:
    expected = os.environ.get("SOLAPI_WEBHOOK_SECRET", "")
    if not expected:
        return True  # 미설정 시 검증 통과 (개발환경)
    received = request_headers.get("x-solapi-secret", "")
    return hmac.compare_digest(expected, received)
```

**2-2. 기존 friend-added 웹훅에 kakao_user_key 저장 추가**:

현재 phone만 저장 → `user_key`도 함께 저장:

```python
# api.py solapi_friend_added_webhook 내부
user_key = payload.get("userKey") or payload.get("user_key") or ""
if user_key and digits:
    # kakao_mapping INSERT (중복 시 무시)
    await client.post(
        _supa_url("kakao_mapping"),
        headers={**headers, "Prefer": "resolution=ignore-duplicates"},
        json={"kakao_user_key": user_key, "customer_id": matched_customer_id},
    )
    # app_customers.kakao_user_key 갱신
    await client.patch(
        _supa_url("app_customers") + f"?phone1=eq.{digits}",
        headers=headers,
        json={"kakao_user_key": user_key},
    )
```

**2-3. 신규 엔드포인트: 고객 수신 메시지 저장**

고객이 카카오채널로 보낸 메시지를 수신하는 웹훅:

```python
@app.post("/webhook/solapi/message-received")
async def solapi_message_received(request: Request) -> JSONResponse:
    # payload: {userKey, text, messageType, ...}
    # 1. kakao_mapping에서 user_key → customer_id 조회
    # 2. app_customer_messages에 direction='inbound'로 저장
    # 3. 200 OK 즉시 반환 (타임아웃 방지)
```

### Step 3: Streamlit 상담 패널 UI (crm_automation.py)

"카카오 채널 현황" 탭에 **"고객 인바운드 메시지"** 섹션 추가:

```python
# crm_automation.py _render_kakao_channel_tab() 내부에 추가
with st.expander("고객 수신 메시지 (인바운드)", expanded=True):
    # app_customer_messages WHERE direction='inbound' 조회
    # 고객 선택 시 해당 고객과의 대화 이력 표시
    # 답장 입력창 + 발송 버튼 (send_manual_friendtalk 호출)
```

---

## 파일별 변경 요약

- [api.py](api.py): Signature 검증 추가, kakao_user_key 저장, `/webhook/solapi/message-received` 엔드포인트 신규
- [crm_automation.py](crm_automation.py): 인바운드 메시지 조회 + 답장 UI 추가
- SQL 신규: `kakao_mapping` 테이블, `app_customer_messages.direction` 컬럼

## render.yaml 환경변수 추가

```yaml
- key: SOLAPI_WEBHOOK_SECRET
  sync: false   # Render 대시보드에서 직접 입력
```

## 실시간 채팅 (Realtime) 에 대하여

가이드가 제안하는 Supabase Realtime 구독은 Next.js에 특화된 기능입니다.
Streamlit에서 동일한 경험을 내려면 `st.rerun()` 폴링(5초 간격)으로 구현하며,
완전한 실시간이 필요하다면 별도 Next.js 채팅창을 나중에 추가하는 것이 현실적입니다.
