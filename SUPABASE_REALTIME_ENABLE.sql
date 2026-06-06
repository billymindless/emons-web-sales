-- Supabase Realtime 활성화
-- Supabase 대시보드 → Database → Replication → Tables 에서 직접 추가하거나
-- 아래 SQL을 SQL Editor에서 실행하세요.

-- app_customer_messages 테이블을 Realtime 구독 대상에 추가
ALTER PUBLICATION supabase_realtime ADD TABLE app_customer_messages;

-- kakao_mapping 테이블도 Realtime 구독 대상에 추가 (고객 목록 자동 갱신용)
ALTER PUBLICATION supabase_realtime ADD TABLE kakao_mapping;
