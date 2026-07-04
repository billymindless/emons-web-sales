-- 개인별 UI 설정 (즐겨찾기 메뉴 등) 저장 테이블
-- 로그인한 사용자가 자주 쓰는 메뉴 라벨을 저장해 드롭다운 위 바로가기 버튼으로 노출한다.
-- username 은 app.py 세션(current_user.username, 로그인 이메일 or 아이디) 기준.

-- 1) 테이블 생성
CREATE TABLE IF NOT EXISTS app_user_preferences (
  username   TEXT PRIMARY KEY,
  fav_menus  JSONB NOT NULL DEFAULT '[]',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 2) RLS 활성화 + 전체 허용 정책 (기존 app_* 테이블과 동일한 패턴)
ALTER TABLE app_user_preferences ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "allow_all_app_user_preferences" ON app_user_preferences;
CREATE POLICY "allow_all_app_user_preferences"
  ON app_user_preferences
  FOR ALL USING (true) WITH CHECK (true);

-- 참고 쿼리
-- 즐겨찾기 조회:
--   SELECT fav_menus FROM app_user_preferences WHERE username = 'user@example.com';
-- 즐겨찾기 저장(upsert):
--   INSERT INTO app_user_preferences (username, fav_menus)
--   VALUES ('user@example.com', '["1. 대시보드","5. 새로운 매출 등록"]'::jsonb)
--   ON CONFLICT (username) DO UPDATE SET fav_menus = EXCLUDED.fav_menus, updated_at = NOW();
