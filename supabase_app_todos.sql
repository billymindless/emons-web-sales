-- app_todos 테이블 생성 (To-Do 기능용)
-- Supabase Dashboard > SQL Editor에서 실행하세요.
-- 실행 후 PostgREST 스키마 캐시가 갱신될 때까지 몇 초 정도 걸릴 수 있습니다.

CREATE TABLE IF NOT EXISTS public.app_todos (
    id BIGSERIAL PRIMARY KEY,
    tenant_name TEXT NOT NULL,
    author TEXT DEFAULT '',
    content TEXT NOT NULL,
    is_completed BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- RLS(Row Level Security) 정책 (선택 사항 - 프로젝트 보안 정책에 따라 조정)
ALTER TABLE public.app_todos ENABLE ROW LEVEL SECURITY;

-- 모든 사용자가 app_todos를 읽고 쓰도록 허용 (인증된 사용자만 허용하려면 auth.role() 체크 추가)
DROP POLICY IF EXISTS "app_todos_select" ON public.app_todos;
CREATE POLICY "app_todos_select" ON public.app_todos FOR SELECT USING (true);

DROP POLICY IF EXISTS "app_todos_insert" ON public.app_todos;
CREATE POLICY "app_todos_insert" ON public.app_todos FOR INSERT WITH CHECK (true);

DROP POLICY IF EXISTS "app_todos_update" ON public.app_todos;
CREATE POLICY "app_todos_update" ON public.app_todos FOR UPDATE USING (true);

DROP POLICY IF EXISTS "app_todos_delete" ON public.app_todos;
CREATE POLICY "app_todos_delete" ON public.app_todos FOR DELETE USING (true);
