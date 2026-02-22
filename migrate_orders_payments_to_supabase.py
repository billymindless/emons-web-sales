#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
일회성 마이그레이션: 기존 store_*.db의 Orders / Payments 데이터를 Supabase app_orders, app_payments로 이전합니다.
실행 전에 Supabase에 SUPABASE_ORDERS_PAYMENTS.sql을 적용해 두세요.
사용법: python migrate_orders_payments_to_supabase.py
       또는 streamlit run app.py 후 별도 터미널에서 python migrate_orders_payments_to_supabase.py
환경: .streamlit/secrets.toml 또는 환경변수로 Supabase URL/Key 설정. databases/ 폴더에 store_*.db 존재.
"""
import os
import sqlite3
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_DIR = os.path.join(BASE_DIR, "databases")
ORDERS_PAYMENTS_TENANT_COL = "db_filename"


def get_master_conn():
    path = os.path.join(BASE_DIR, "databases", "master_system.db")
    if not os.path.exists(path):
        return None
    return sqlite3.connect(path)


def get_store_list():
    """(db_filename, store_name) 목록. Master DB Stores 우선, 없으면 databases/ 내 store_*.db 스캔."""
    conn = get_master_conn()
    if conn:
        try:
            cur = conn.execute("SELECT db_filename, store_name FROM Stores ORDER BY id")
            rows = cur.fetchall()
            conn.close()
            if rows:
                return [(r[0], r[1]) for r in rows]
        except Exception:
            pass
        conn.close()
    stores = []
    if os.path.isdir(DB_DIR):
        for f in sorted(os.listdir(DB_DIR)):
            if f.startswith("store_") and f.endswith(".db"):
                stores.append((f, f.replace(".db", "")))
    return stores


def load_orders_from_sqlite(conn):
    try:
        cur = conn.execute("PRAGMA table_info(Orders)")
        cols = [r[1] for r in cur.fetchall()]
    except Exception:
        return []
    base = ["id", "customer_id", "employee_names", "order_date", "delivery_date", "category", "cost_price",
            "total_amount", "visit_reason", "purchase_reason", "actual_margin", "display_sales_amount",
            "display_cost_amount", "balance_status"]
    sel = [c for c in base if c in cols]
    if not sel:
        return []
    try:
        cur = conn.execute("SELECT " + ", ".join(sel) + " FROM Orders ORDER BY id")
        rows = cur.fetchall()
        return [dict(zip(sel, r)) for r in rows]
    except Exception:
        return []


def load_payments_from_sqlite(conn):
    try:
        cur = conn.execute("SELECT id, order_id, payment_date, amount, payment_method, card_company, fee_amount, onnuri_approval_code FROM Payments ORDER BY id")
        rows = cur.fetchall()
        return [{"id": r[0], "order_id": r[1], "payment_date": r[2], "amount": r[3], "payment_method": r[4], "card_company": r[5], "fee_amount": r[6], "onnuri_approval_code": r[7]} for r in rows]
    except Exception:
        return []


def get_supabase_client():
    try:
        from supabase import create_client
    except ImportError:
        print("supabase 패키지가 필요합니다: pip install supabase")
        return None, "supabase not installed"
    url = os.environ.get("SUPABASE_URL") or ""
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("SUPABASE_KEY") or ""
    if not url or not key:
        try:
            secrets_path = os.path.join(BASE_DIR, ".streamlit", "secrets.toml")
            if os.path.isfile(secrets_path):
                try:
                    import tomllib
                    with open(secrets_path, "rb") as f:
                        secrets = tomllib.load(f)
                except ImportError:
                    import toml
                    with open(secrets_path, "r", encoding="utf-8") as f:
                        secrets = toml.load(f)
                supabase = secrets.get("supabase") or {}
                url = url or supabase.get("url") or supabase.get("SUPABASE_URL") or ""
                key = key or supabase.get("service_role_key") or supabase.get("SUPABASE_SERVICE_ROLE_KEY") or ""
        except Exception:
            pass
    if not url or not key:
        return None, "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY (or .streamlit/secrets.toml [supabase]) required"
    try:
        client = create_client(url.strip(), key.strip())
        return client, None
    except Exception as e:
        return None, str(e)


def migrate_store(client, db_filename: str, store_name: str):
    path = os.path.join(DB_DIR, db_filename)
    if not os.path.exists(path):
        return 0, 0, f"파일 없음: {path}"
    conn = sqlite3.connect(path)
    try:
        orders = load_orders_from_sqlite(conn)
        payments = load_payments_from_sqlite(conn)
    finally:
        conn.close()
    if not orders:
        return 0, 0, None
    old_to_new_order_id = {}
    inserted_orders = 0
    for o in orders:
        payload = {
            ORDERS_PAYMENTS_TENANT_COL: db_filename,
            "customer_id": o.get("customer_id"),
            "employee_names": o.get("employee_names"),
            "order_date": o.get("order_date") or "",
            "delivery_date": o.get("delivery_date"),
            "category": o.get("category"),
            "cost_price": o.get("cost_price"),
            "total_amount": o.get("total_amount"),
            "visit_reason": o.get("visit_reason"),
            "purchase_reason": o.get("purchase_reason"),
            "actual_margin": o.get("actual_margin"),
            "display_sales_amount": o.get("display_sales_amount") or 0,
            "display_cost_amount": o.get("display_cost_amount") or 0,
            "balance_status": o.get("balance_status"),
        }
        try:
            r = client.table("app_orders").insert(payload).execute()
            if r.data and len(r.data) > 0 and "id" in r.data[0]:
                new_id = int(r.data[0]["id"])
                old_to_new_order_id[int(o.get("id"))] = new_id
                inserted_orders += 1
        except Exception as e:
            return inserted_orders, 0, f"Order id={o.get('id')} insert failed: {e}"
    inserted_payments = 0
    for p in payments:
        old_oid = int(p.get("order_id"))
        new_oid = old_to_new_order_id.get(old_oid)
        if new_oid is None:
            continue
        payload = {
            ORDERS_PAYMENTS_TENANT_COL: db_filename,
            "order_id": new_oid,
            "payment_date": p.get("payment_date") or "",
            "amount": float(p.get("amount") or 0),
            "payment_method": p.get("payment_method"),
            "card_company": p.get("card_company"),
            "fee_amount": p.get("fee_amount"),
            "onnuri_approval_code": p.get("onnuri_approval_code"),
        }
        try:
            client.table("app_payments").insert(payload).execute()
            inserted_payments += 1
        except Exception as e:
            return inserted_orders, inserted_payments, f"Payment order_id={old_oid} insert failed: {e}"
    return inserted_orders, inserted_payments, None


def main():
    print("Supabase 주문/결제 마이그레이션 (store_*.db → app_orders, app_payments)")
    client, err = get_supabase_client()
    if err:
        print("Supabase 연결 실패:", err)
        sys.exit(1)
    stores = get_store_list()
    if not stores:
        print("매장(store_*.db) 목록이 비어 있습니다.")
        sys.exit(0)
    total_orders, total_payments = 0, 0
    for db_filename, store_name in stores:
        o_count, p_count, err_msg = migrate_store(client, db_filename, store_name)
        if err_msg:
            print(f"  [{db_filename}] 오류: {err_msg} (orders={o_count}, payments={p_count} 까지 반영)")
        else:
            print(f"  [{db_filename}] {store_name}: orders {o_count}, payments {p_count}")
        total_orders += o_count
        total_payments += p_count
    print(f"총 주문 {total_orders}건, 결제 {total_payments}건 마이그레이션 완료.")


if __name__ == "__main__":
    main()
