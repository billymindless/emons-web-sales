# 모모 리드 API 검증 시나리오

`docs/plans/리드_api_통합_8051f9bf.plan.md` 의 「검증」 절을 실제 명령어로 옮긴 문서.
Render 배포 직후, 그리고 이후에 채널톡·Grok Bot·에몬스 셀러 프로그램을 확장할 때마다
이 시퀀스를 재실행한다.

## 사전 준비

- Render 환경변수:
  - `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` — 이미 있음
  - `CHANNEL_TALK_ACCESS_KEY`, `CHANNEL_TALK_ACCESS_SECRET`, `CHANNEL_TALK_DEFAULT_STORE` — 이미 있음
  - `LEAD_API_TOKEN` — **이번에 신규**. 32자+ 랜덤 문자열. 채팅/깃/스크린샷에 노출 금지.
- 로컬에서는 `.env` 또는 `export` 로 동일 값을 세팅:

```bash
export LEAD_API_BASE_URL="https://<render-service>.onrender.com"
export LEAD_API_TOKEN="..."
```

## 1. 인증 가드

토큰 없으면 401, 잘못된 토큰도 401, 올바른 토큰이면 200 이어야 한다.

```bash
# 토큰 없음 → 401
curl -s -o /dev/null -w "%{http_code}\n" \
  "$LEAD_API_BASE_URL/v1/leads?limit=1"

# 잘못된 토큰 → 401
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer wrong" \
  "$LEAD_API_BASE_URL/v1/leads?limit=1"

# 올바른 토큰 → 200
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Authorization: Bearer $LEAD_API_TOKEN" \
  "$LEAD_API_BASE_URL/v1/leads?limit=1"
```

## 2. upsert 멱등성 (같은 전화번호 2회)

같은 전화로 두 번 `POST` → `app_leads` row 는 1개, 두 번째 응답은 `created=false`.

```bash
PHONE="010-9999-0001"

curl -s -X POST "$LEAD_API_BASE_URL/v1/leads" \
  -H "Authorization: Bearer $LEAD_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"phone\":\"$PHONE\",\"name\":\"검증고객\",\"memo\":\"1차 등록\",\"source_system\":\"grok_bot\"}" | jq

curl -s -X POST "$LEAD_API_BASE_URL/v1/leads" \
  -H "Authorization: Bearer $LEAD_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"phone\":\"$PHONE\",\"name\":\"검증고객\",\"memo\":\"2차 재유입\",\"source_system\":\"grok_bot\"}" | jq
```

기대 결과:

- 1회차: `{"ok":true,"created":true,"branch":"C" or "D",...}`
- 2회차: `{"ok":true,"created":false,"branch":"A" or "B",...}` (첫 upsert 로 A/B 분기)
- 모모 화면 `리드 관리` 페이지에서 검증고객이 1건만 보이고, 상담이력에 두 번째 memo 가 append 됨.

## 3. 채널톡 웹훅 → 리드 자동 등록

`chat.created` 웹훅으로 번호가 실제 존재하는 사용자를 시뮬레이션. 토큰 필요 없음(채널톡 시크릿 그대로).

```bash
curl -s -X POST "$LEAD_API_BASE_URL/channel-talk/webhook" \
  -H "Content-Type: application/json" \
  -d '{
    "event":"chat.created",
    "chat":{"id":"test-chat-1"},
    "user":{"name":"채널톡검증","mobileNumber":"01099990002"}
  }' | jq
```

기대 결과:

- 응답: `{"ok":true,"branch":"C" or "D","created":true}`
- Supabase `app_leads` 에 `phone=01099990002`, `lead_source=온라인_채널톡` row 1개
- 모모 리드 목록에 즉시 노출

두 번째 호출 (같은 유저 재유입):

```bash
curl -s -X POST "$LEAD_API_BASE_URL/channel-talk/webhook" \
  -H "Content-Type: application/json" \
  -d '{
    "event":"chat.created",
    "chat":{"id":"test-chat-2"},
    "user":{"name":"채널톡검증","mobileNumber":"01099990002"}
  }' | jq
```

기대: `{"branch":"B","created":false}`. `app_leads` row 는 여전히 1개, `last_contact_at` 만 갱신.

## 4. Grok MCP: 검색 → 메모 → 단계 변경

MCP 도구를 손으로 재현.

```bash
# 리드 검색
curl -s -H "Authorization: Bearer $LEAD_API_TOKEN" \
  "$LEAD_API_BASE_URL/v1/leads?q=검증&limit=5" | jq

# 특정 리드 상세 + 상담 이력
LEAD_ID=$(curl -s -H "Authorization: Bearer $LEAD_API_TOKEN" \
  "$LEAD_API_BASE_URL/v1/leads?phone=01099990002&limit=1" | jq -r '.leads[0].id')
curl -s -H "Authorization: Bearer $LEAD_API_TOKEN" \
  "$LEAD_API_BASE_URL/v1/leads/$LEAD_ID" | jq

# 상담 노트 추가
curl -s -X POST "$LEAD_API_BASE_URL/v1/leads/$LEAD_ID/notes" \
  -H "Authorization: Bearer $LEAD_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"summary":"Grok 도구로 남긴 상담 메모","channel":"grok_note","handled_by":"grok_bot"}' | jq

# 단계 변경 (2_상담중 → 3_견적발송)
curl -s -X PATCH "$LEAD_API_BASE_URL/v1/leads/$LEAD_ID" \
  -H "Authorization: Bearer $LEAD_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"lead_stage":"3_견적발송","source_system":"grok_bot"}' | jq
```

교차 확인:

- 모모 `리드 관리` 화면에서 단계와 노트가 동일하게 보임
- 채널톡 Custom Tab (우측 패널) 에서도 같은 이름·단계가 노출됨

## 5. 손대지 않는 것

- 넛징 메시지(T+0, T+N일) 자동 발송은 `send_now=False` 기본값으로 Grok/외부 경로에서는 차단됨
- 모모 UI 의 직접 `register_lead()` 는 기존처럼 `send_now=True` 로 발송 가능
- 이 API 는 리드 데이터 관리 전용. 문자/알림톡 발송은 별도 발송 도구(`solapi_sender`, 모모 화면) 를 사용

## 6. 실패 시 롤백

- 새 라우트가 문제를 일으키면 `render.yaml` 에서 `LEAD_API_TOKEN` 을 비우면 즉시 401 로 전 노출이 차단된다 (라우트는 살아 있지만 아무도 못 씀).
- 채널톡 웹훅은 별도 시크릿을 쓰므로 영향 없음.
