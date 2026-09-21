"""주문 #10101 의 실제 결제 합계 vs balance_status 재확인. 
_load_payments_supabase 와 동일한 페이지네이션 사용."""
import tomllib
from pathlib import Path
from supabase import create_client

p = Path('.streamlit/secrets.toml')
data = tomllib.loads(p.read_text(encoding='utf-8'))
sup = data['supabase']
client = create_client(sup['url'].strip(), sup['service_role_key'].strip())

DB = 'store_1.db'
OID = 10101

# 페이지네이션으로 결제 조회
all_rows = []
offset = 0
PAGE = 1000
while True:
    r = client.table('app_payments').select('id, order_id, amount, payment_method, payment_date, created_at').eq('db_filename', DB).eq('order_id', OID).order('id').range(offset, offset + PAGE - 1).execute()
    rows = r.data or []
    all_rows.extend(rows)
    if len(rows) < PAGE:
        break
    offset += PAGE

print(f'order #{OID} payments count: {len(all_rows)}')
for row in all_rows:
    print(f"  #{row['id']} {row['payment_date']} {row['amount']:>+13,} {row['payment_method']} at {row['created_at']}")
total_paid = sum(int(r['amount']) for r in all_rows)
print(f'sum = {total_paid:+,}')

o = client.table('app_orders').select('id, total_amount, balance_status, actual_margin').eq('db_filename', DB).eq('id', OID).single().execute()
print(f"order total = {o.data['total_amount']:,}   balance_status = {o.data['balance_status']}   actual_margin = {o.data['actual_margin']}")
print(f"remaining = {o.data['total_amount'] - total_paid:+,}")
