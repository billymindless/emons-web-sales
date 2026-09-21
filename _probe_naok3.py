"""주문 #10101 관련 태스크/PCR/외부매칭/task_activity 확인"""
import tomllib, json
from pathlib import Path
from supabase import create_client

p = Path('.streamlit/secrets.toml')
data = tomllib.loads(p.read_text(encoding='utf-8'))
sup = data['supabase']
client = create_client(sup['url'].strip(), sup['service_role_key'].strip())

OID = 10101
DB = 'store_1.db'

out = {}

# 1) app_payment_change_requests
try:
    r = client.table('app_payment_change_requests').select('*').eq('db_filename', DB).eq('sale_id', OID).execute()
    out['payment_change_requests'] = r.data or []
except Exception as e:
    out['payment_change_requests_err'] = str(e)

# 2) app_tasks referencing this order
try:
    r = client.table('app_tasks').select('id, task_type, title, status, related_id, related_data, created_at').eq('related_id', OID).execute()
    out['tasks_related_to_order'] = r.data or []
except Exception as e:
    out['tasks_err'] = str(e)

# 3) app_task_activity for these tasks
task_ids = [t['id'] for t in (out.get('tasks_related_to_order') or [])]
if task_ids:
    try:
        r = client.table('app_task_activity').select('*').in_('task_id', task_ids).order('created_at').execute()
        out['task_activity'] = r.data or []
    except Exception as e:
        out['task_activity_err'] = str(e)

# 4) app_external_pay_matches for this order's payments
try:
    r = client.table('app_external_pay_matches').select('*').in_('payment_id', [13555, 14541, 14545]).execute()
    out['external_matches'] = r.data or []
except Exception as e:
    out['external_matches_err'] = str(e)

Path('_naok_dump3.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
print('written to _naok_dump3.json, size=', Path('_naok_dump3.json').stat().st_size)
