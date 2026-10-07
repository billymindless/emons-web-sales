-- 방수커버 단가. 전 매장 공통. 금액만 관리자 설정에서 바꾼다.
-- 다시 실행해도 이미 저장된 금액은 덮어쓰지 않는다.

CREATE TABLE IF NOT EXISTS app_waterproof_cover_prices (
    code        TEXT PRIMARY KEY,
    label       TEXT NOT NULL,
    amount      INTEGER NOT NULL,
    sort_order  INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    updated_by  TEXT,
    updated_at  TIMESTAMPTZ DEFAULT now()
);

INSERT INTO app_waterproof_cover_prices (code, label, amount, sort_order, kind)
VALUES
    ('s',     'S',            7700,  1, 'size'),
    ('ss',    'SS',           8030,  2, 'size'),
    ('w1200', '1200/1300',    8800,  3, 'size'),
    ('q',     'Q',            9460,  4, 'size'),
    ('k',     'K',            9900,  5, 'size'),
    ('lk',    'LK 1800*2000', 10450, 6, 'size'),
    ('kk',    'KK 1800*2100', 12100, 7, 'size'),
    ('liner', '리너',         7700,  8, 'option'),
    ('pad',   '밀림방지패드', 7700,  9, 'option')
ON CONFLICT (code) DO NOTHING;

ALTER TABLE app_waterproof_cover_prices ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "Allow all app_waterproof_cover_prices" ON app_waterproof_cover_prices;
CREATE POLICY "Allow all app_waterproof_cover_prices" ON app_waterproof_cover_prices
    FOR ALL USING (true) WITH CHECK (true);
