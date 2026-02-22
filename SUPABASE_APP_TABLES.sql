-- Supabase 직원 명부·매장 테이블 (Streamlit 앱에서 사용)
-- Supabase 대시보드 → SQL Editor에서 이 파일 내용을 실행하세요.
-- CREATE TABLE IF NOT EXISTS 를 사용하므로, 테이블이 없으면 생성되고 있으면 유지됩니다. 한 번 실행해 두면 DB가 항상 유지됩니다.

-- 1) 매장 테이블 (기존 Master DB Stores와 동일 역할)
CREATE TABLE IF NOT EXISTS app_stores (
  id BIGSERIAL PRIMARY KEY,
  store_name TEXT NOT NULL UNIQUE,
  db_filename TEXT NOT NULL UNIQUE
);

-- 2) 직원 테이블 (기존 Master DB Users와 동일 역할)
-- auth.users와 구분하기 위해 app_users 사용
CREATE TABLE IF NOT EXISTS app_users (
  id BIGSERIAL PRIMARY KEY,
  username TEXT NOT NULL UNIQUE,
  password TEXT NOT NULL,
  email TEXT,
  role TEXT NOT NULL CHECK (role IN ('superadmin', 'store_admin', 'user')),
  store_id BIGINT REFERENCES app_stores(id),
  name TEXT
);

-- 3) 직원-매장 다대다 (기존 Master DB UserStores와 동일)
CREATE TABLE IF NOT EXISTS app_user_stores (
  user_id BIGINT NOT NULL REFERENCES app_users(id) ON DELETE CASCADE,
  store_id BIGINT NOT NULL REFERENCES app_stores(id) ON DELETE CASCADE,
  PRIMARY KEY (user_id, store_id)
);

-- RLS 비활성화 (앱에서 anon/service_role로 접근)
ALTER TABLE app_stores ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_user_stores ENABLE ROW LEVEL SECURITY;

-- 모든 작업 허용 정책 (실제로는 서비스에서만 접근하므로)
DROP POLICY IF EXISTS "Allow all app_stores" ON app_stores;
CREATE POLICY "Allow all app_stores" ON app_stores FOR ALL USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "Allow all app_users" ON app_users;
CREATE POLICY "Allow all app_users" ON app_users FOR ALL USING (true) WITH CHECK (true);
DROP POLICY IF EXISTS "Allow all app_user_stores" ON app_user_stores;
CREATE POLICY "Allow all app_user_stores" ON app_user_stores FOR ALL USING (true) WITH CHECK (true);

-- (선택) superadmin 초기 계정이 없으면 추가
-- 초기 비밀번호 '1234'의 SHA256 해시
INSERT INTO app_users (username, password, email, role, store_id, name)
SELECT 'superadmin', '03ac674216f3e15c761ee1a5e255f067953623c8b388b4459e13f978d7c846f4a', 'billymind@gmail.com', 'superadmin', NULL, '최고관리자'
WHERE NOT EXISTS (SELECT 1 FROM app_users WHERE username = 'superadmin');
