import tomllib, json
from datetime import date
from pathlib import Path
from supabase import create_client
import pandas as pd

p = Path('.streamlit/secrets.toml')
data = tomllib.loads(p.read_text(encoding='utf-8'))
sup = data['supabase']
client = create_client(sup['url'].strip(), sup['service_role_key'].strip())

# 실제 UI 로직 재현
import legacy_purchase_import_service as lps

# 나옥운 두 고객ID
all_cids = [7805, 8072]

orders_r = client.table('app_orders').select(
    'id, customer_id, order_date, delivery_date, category, cost_price, total_amount, actual_margin, display_sales_amount, display_cost_amount, visit_reason, purchase_reason, employee_names, import_source, balance_status'
).eq('db_filename', 'store_1.db').in_('customer_id', all_cids).execute()
orders = pd.DataFrame(orders_r.data or [])
print('orders columns:', list(orders.columns))
print(orders[['id', 'order_date', 'total_amount', 'balance_status', 'import_source']].to_string())

# 결제 조회
pay_r = client.table('app_payments').select('id, order_id, payment_date, amount, payment_method').eq('db_filename', 'store_1.db').execute()
pays = pd.DataFrame(pay_r.data or [])
print()
print('payments for our orders:')
print(pays[pays['order_id'].isin(orders['id'].tolist())][['id', 'order_id', 'payment_date', 'amount', 'payment_method']].to_string())

pay_sum = pays.groupby('order_id')['amount'].sum() if not pays.empty else pd.Series(dtype=float)
orders['paid'] = orders['id'].map(pay_sum).fillna(0)
orders['balance'] = orders['total_amount'] - orders['paid']
print()
print('BEFORE zero_legacy filter:')
print(orders[['id', 'total_amount', 'paid', 'balance', 'balance_status', 'import_source', 'order_date']].to_string())

orders2 = lps.apply_legacy_import_paid_balance(orders.copy(), balance_col='balance')
print()
print('AFTER zero_legacy filter:')
print(orders2[['id', 'total_amount', 'paid', 'balance', 'balance_status', 'import_source', 'order_date']].to_string())

# mask check
mask = lps.mask_legacy_import_force_paid(orders)
print()
print('force_paid mask:')
print(mask)
