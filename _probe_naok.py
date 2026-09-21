import tomllib, json
from pathlib import Path
from supabase import create_client

p = Path('.streamlit/secrets.toml')
data = tomllib.loads(p.read_text(encoding='utf-8'))
sup = data['supabase']
client = create_client(sup['url'].strip(), sup['service_role_key'].strip())

out = []
c = client.table('app_customers').select('id, name, phone1, store_name').ilike('phone1', '%9194-3729%').execute()
out.append({'customers': c.data or []})

if c.data:
    for cust in c.data:
        cid = cust['id']
        o = client.table('app_orders').select(
            'id, order_date, total_amount, balance_status, import_source, db_filename, payment_change_planned, cost_price'
        ).eq('customer_id', cid).execute()
        for od in (o.data or []):
            oid = od['id']
            dfn = od['db_filename']
            block = {'cust_id': cid, 'cust_name': cust['name'], 'order': od}
            pays = client.table('app_payments').select(
                'id, payment_date, amount, payment_method, card_company, onnuri_approval_code, created_at, created_by, business_name'
            ).eq('order_id', oid).eq('db_filename', dfn).order('id').execute()
            block['payments'] = pays.data or []
            total_paid = sum(int(pr['amount']) for pr in (pays.data or []))
            block['sum_payments'] = total_paid
            block['computed_balance'] = int(od['total_amount']) - total_paid
            try:
                ph = client.table('app_payment_history').select(
                    'id, action_type, changed_at, reason, old_payment_data, new_payment_data, changed_by'
                ).eq('db_filename', dfn).eq('sale_id', int(oid)).order('changed_at', desc=True).limit(20).execute()
                block['history'] = ph.data or []
            except Exception as e:
                block['history_err'] = str(e)
            out.append(block)

Path('_naok_dump.json').write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
print('written to _naok_dump.json, size=', Path('_naok_dump.json').stat().st_size)
