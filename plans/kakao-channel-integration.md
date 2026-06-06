# 카카오 비즈니스 채널 고객 연동 구현 플랜

## 개요

구매 완료 고객에게 카카오 비즈니스 채널 친구추가를 유도하고, 채널 구독자와 ERP 고객 DB를 자동 동기화하여
구매 안내 / 배송 / 상담을 카카오톡으로 처리하는 전체 플로우 구현.

---

## 전체 데이터 흐름

```
고객 구매 완료 (주문 저장)
    │
    ▼
send_purchase_notification()  [customer_channel.py]
    │
    ├─ kakao_friend_added == True
    │       └─ CTA 친구톡 발송 (send_friendtalk)
    │
    └─ kakao_friend_added == False
            ├─ 알림톡 템플릿 설정 있음 → ATA 알림톡 발송 (send_alimtalk)
            └─ 알림톡 템플릿 없음     → 채널 초대 SMS 발송 (send_friendtalk + SMS 폴백)
                    │
                    ▼
            발송 결과 → app_customer_messages 기록
                    │
                    ▼
고객이 카카오채널 친구추가
    │
    ▼
Solapi Webhook → POST /webhook/solapi/friend-added  [api.py]
    │
    ├─ app_users.kakao_friend_added = true  (직원용)
    └─ app_customers.kakao_friend_added = true  (고객용, 신규 추가)
```

---

## 구현된 파일 목록

### 신규 파일

| 파일 | 설명 |
|------|------|
| `customer_channel.py` | 핵심 발송 모듈. `send_purchase_notification()`, `send_channel_invite_sms()`, `send_manual_friendtalk()` |
| `SUPABASE_KAKAO_CHANNEL.sql` | DB 마이그레이션 SQL (아래 참조) |
| `plans/kakao-channel-integration.md` | 이 파일 |

### 수정된 파일

| 파일 | 변경 내용 |
|------|-----------|
| `solapi_sender.py` | `send_alimtalk()` 함수 추가 (ATA 알림톡 발송) |
| `api.py` | `/webhook/solapi/friend-added` 에 `app_customers` 갱신 로직 추가 |
| `crm_automation.py` | "카카오 채널 현황" 탭 추가 (친구 현황 + 발송 이력 조회) |
| `app.py` | 주문 저장 후 `send_purchase_notification()` 자동 호출, 고객 상세 카카오 채널 배지/버튼 UI 추가 |

---

## DB 마이그레이션 (운영자 실행 필요)

Supabase SQL Editor에서 `SUPABASE_KAKAO_CHANNEL.sql` 실행:

```sql
-- app_customers 컬럼 추가
ALTER TABLE app_customers
  ADD COLUMN IF NOT EXISTS kakao_friend_added    BOOLEAN    DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS kakao_friend_added_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS kakao_user_key        TEXT;

-- 발송 이력 테이블
CREATE TABLE IF NOT EXISTS app_customer_messages (
  id              BIGSERIAL PRIMARY KEY,
  customer_id     BIGINT REFERENCES app_customers(id) ON DELETE SET NULL,
  store_name      TEXT,
  order_id        BIGINT,
  phone           TEXT,
  message_type    TEXT,   -- 'purchase_confirm' | 'channel_invite' | 'cs_reply' | 'manual'
  channel         TEXT,   -- 'alimtalk' | 'friendtalk' | 'sms'
  status          TEXT,   -- 'sent' | 'failed' | 'not_friend' | 'skipped' | 'out_of_hours'
  solapi_msg_id   TEXT,
  message_body    TEXT,
  error_detail    TEXT,
  sent_by         TEXT,
  created_at      TIMESTAMPTZ DEFAULT now()
);
```

---

## secrets.toml 설정 (운영자 설정 필요)

`.streamlit/secrets.toml`에 아래 항목 추가:

```toml
[solapi]
api_key = "NCSXXXXXX"
api_secret = "..."
sender = "01012345678"
pf_id = "_카카오채널pfId_"
purchase_template_code = "KA01TP..."   # 구매 완료 알림톡 템플릿 코드 (없으면 SMS 발송)
kakao_channel_url = "https://pf.kakao.com/_XXXXX"  # 채널 초대 URL
```

---

## Solapi 콘솔 설정 (운영자 처리 필요)

1. **카카오 비즈니스 채널 개설**: https://business.kakao.com 에서 채널 개설 및 심사
2. **Solapi 채널 연동**: Solapi 대시보드 > 카카오채널 > 채널 연결 → `pf_id` 획득
3. **알림톡 템플릿 등록·승인**: 구매 완료 템플릿 등록 후 카카오 심사 (2~5 영업일)
4. **친구추가 웹훅 URL 등록**: Solapi 콘솔 > 웹훅 설정
   - URL: `https://emons-sms-webhook.onrender.com/webhook/solapi/friend-added`

---

## 메시지 유형별 발송 시나리오

| 시나리오 | 트리거 | 채널 | 함수 |
|---------|--------|------|------|
| 구매 직후 자동 발송 | 주문 저장 버튼 | 알림톡 → SMS 폴백 | `send_purchase_notification()` |
| 채널 초대 수동 발송 | 관리자 버튼 클릭 | SMS (채널 URL 포함) | `send_channel_invite_sms()` |
| CS 수동 친구톡 | 관리자 팝오버 | 친구톡 | `send_manual_friendtalk()` |
| 미연결 고객 일괄 초대 | CRM 메뉴 > 카카오 채널 현황 탭 | SMS | `send_channel_invite_sms()` (반복) |

---

## 메시지 템플릿 예시

**채널 초대 SMS (친구추가 유도):**
```
[이몬스] {이름}님, 구매해 주셔서 감사합니다.
배송 안내 및 AS 문의는 카카오채널을 이용해 주세요.
채널 추가: https://pf.kakao.com/_XXXXX
```

**구매 확인 친구톡 (채널 친구 고객용):**
```
{이름}님, 주문이 확인되었습니다.
품목: {품목}
배송 예정일: {배송일}
문의사항은 이 채널로 연락해 주세요.
```

---

## UI 추가 위치 요약

### 고객 잔금 관리 > 일반 고객 탭 (고객 선택 후)
- 카카오채널 친구 상태 배지: `✅ 채널 친구` 또는 `⚠️ 미연결`
- "채널 초대 문자 발송" 버튼
- "친구톡 직접 발송" 팝오버 (메시지 입력 후 발송)

### CRM 자동화 메뉴 > 카카오 채널 현황 탭 (신규)
- 친구 현황 통계 (전체 / 친구 / 미연결 수)
- 전체 고객 / 미연결 고객 목록 테이블
- 미연결 고객 일괄 초대 발송 버튼
- 발송 이력 조회 + Excel 다운로드

---

## 주의 사항

- **알림톡(ATA)**: 카카오에서 사전 승인된 템플릿만 발송 가능. `purchase_template_code` 미설정 시 SMS로 폴백.
- **친구톡(CTA)**: 채널 친구만 수신 가능. 메시지 앞에 `(광고)` 자동 표기됨.
- **발송 실패 시**: 주문 등록 화면에는 영향 없음 (`try/except` 처리). 이력은 `app_customer_messages`에 `status=failed`로 기록.
- **야간 제한**: Solapi 정책상 21:00~08:00 발송이 거부될 수 있음. `out_of_hours` 상태로 이력 기록.
