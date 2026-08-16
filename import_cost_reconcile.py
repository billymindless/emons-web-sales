# -*- coding: utf-8 -*-
"""매입 원장 vs 판매자 입력 원가 대사 (헤더 미변경).

매출 등록/결제/주문수정 저장 경로는 호출하지 않는다.
"""
from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Optional

logger = logging.getLogger(__name__)

COST_ABS_THRESHOLD = 10_000
COST_PCT_THRESHOLD = 0.05
COST_CRITICAL_ABS = 50_000
COST_CRITICAL_PCT = 0.20


def _as_date(v) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    if len(s) < 10:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def _i(v) -> int:
    try:
        return int(round(float(v or 0)))
    except (TypeError, ValueError):
        return 0


def _seller_cost(meta: dict) -> int:
    return _i(meta.get("cost_price")) + _i(meta.get("display_cost_amount"))


def classify_cost_gap(seller_cost: int, hq_cost: int) -> tuple[str, str]:
    """반환: (결과라벨, 코드)."""
    if hq_cost > 0 and seller_cost == 0:
        return "원가 미입력", "cost_blank"
    diff = seller_cost - hq_cost
    base = max(abs(hq_cost), abs(seller_cost), 1)
    if abs(diff) >= COST_ABS_THRESHOLD or (abs(diff) / base) >= COST_PCT_THRESHOLD:
        return "원가 불일치", "cost_mismatch"
    return "원가 일치", "ok"


def alert_level_for_gap(seller_cost: int, hq_cost: int, code: str) -> str:
    if code == "cost_blank":
        return "warning"
    if code != "cost_mismatch":
        return "info"
    diff = abs(seller_cost - hq_cost)
    base = max(abs(hq_cost), abs(seller_cost), 1)
    if diff >= COST_CRITICAL_ABS or (diff / base) >= COST_CRITICAL_PCT:
        return "critical"
    return "warning"


@dataclass
class ReconcileResult:
    rows: list[dict] = field(default_factory=list)
    mismatch: int = 0
    blank: int = 0
    hq_only: int = 0
    app_only: int = 0
    ok: int = 0


def build_reconcile_from_preview(preview, orders_by_cid: dict[int, list[dict]]) -> ReconcileResult:
    """미리보기 그룹을 앱 주문 단위로 합산 비교."""
    out = ReconcileResult()
    attached: dict[int, list] = defaultdict(list)
    attached_oids: set[int] = set()
    hq_only_groups = []

    for g in getattr(preview, "groups", []) or []:
        status = getattr(g, "match_status", "")
        oid = getattr(g, "chosen_order_id", None)
        if status == "to_attach" and oid:
            attached[int(oid)].append(g)
            attached_oids.add(int(oid))
        elif status == "to_create":
            cid = getattr(g, "existing_customer_id", None)
            if cid is not None and int(cid) > 0:
                hq_only_groups.append(g)

    meta_by_id: dict[int, dict] = {}
    for recs in (orders_by_cid or {}).values():
        for rec in recs:
            try:
                meta_by_id[int(rec["id"])] = rec
            except (TypeError, ValueError, KeyError):
                continue

    for oid, groups in attached.items():
        meta = meta_by_id.get(oid) or {}
        if not meta:
            for g in groups:
                for m in getattr(g, "candidate_orders_meta", []) or []:
                    if m.get("id") == oid:
                        meta = m
                        break
        seller = _seller_cost(meta)
        hq = sum(_i(getattr(g, "total_line_cost", 0)) for g in groups)
        label, code = classify_cost_gap(seller, hq)
        deliveries = sorted({
            str(getattr(g, "delivery_date", "") or meta.get("delivery_date") or "")[:10]
            for g in groups
        } | {str(meta.get("delivery_date") or "")[:10]})
        deliveries = [d for d in deliveries if d]
        row = {
            "주문ID": oid,
            "고객명": getattr(groups[0], "customer_name", "") or "",
            "전화": getattr(groups[0], "phone1", "") or "",
            "배송일": " / ".join(deliveries),
            "담당": str(meta.get("employee_names") or getattr(groups[0], "employee_names", "") or ""),
            "입력원가": seller,
            "본사원가": hq,
            "원가차이": seller - hq,
            "입력판매가": _i(meta.get("total_amount")),
            "결과": label,
            "_code": code,
            "_level": alert_level_for_gap(seller, hq, code),
        }
        out.rows.append(row)
        if code == "cost_mismatch":
            out.mismatch += 1
        elif code == "cost_blank":
            out.blank += 1
        else:
            out.ok += 1

    for g in hq_only_groups:
        out.hq_only += 1
        out.rows.append({
            "주문ID": "",
            "고객명": getattr(g, "customer_name", "") or "",
            "전화": getattr(g, "phone1", "") or "",
            "배송일": str(getattr(g, "delivery_date", "") or "")[:10],
            "담당": getattr(g, "employee_names", "") or "",
            "입력원가": None,
            "본사원가": _i(getattr(g, "total_line_cost", 0)),
            "원가차이": None,
            "입력판매가": None,
            "결과": "본사만 있음",
            "_code": "hq_only",
            "_level": "warning",
        })

    file_dates: list[date] = []
    for g in getattr(preview, "groups", []) or []:
        for raw in (getattr(g, "delivery_date", None), getattr(g, "order_date", None)):
            d = _as_date(raw)
            if d:
                file_dates.append(d)
    if file_dates:
        d0, d1 = min(file_dates), max(file_dates)
        phones = {getattr(g, "phone1_digits", "") for g in getattr(preview, "groups", []) or []}
        phones.discard("")
        for cid, recs in (orders_by_cid or {}).items():
            for rec in recs:
                try:
                    oid = int(rec["id"])
                except (TypeError, ValueError, KeyError):
                    continue
                if oid in attached_oids:
                    continue
                if rec.get("import_source"):
                    continue
                dd = _as_date(rec.get("delivery_date"))
                if dd is None or dd < d0 or dd > d1:
                    continue
                out.app_only += 1
                out.rows.append({
                    "주문ID": oid,
                    "고객명": "",
                    "전화": "",
                    "배송일": str(rec.get("delivery_date") or "")[:10],
                    "담당": str(rec.get("employee_names") or ""),
                    "입력원가": _seller_cost(rec),
                    "본사원가": None,
                    "원가차이": None,
                    "입력판매가": _i(rec.get("total_amount")),
                    "결과": "앱만 있음",
                    "_code": "app_only",
                    "_level": "warning",
                    "_customer_id": cid,
                })

    return out


def reconcile_dataframe(result: ReconcileResult):
    import pandas as pd
    if not result.rows:
        return pd.DataFrame(columns=[
            "주문ID", "고객명", "전화", "배송일", "담당",
            "입력원가", "본사원가", "원가차이", "입력판매가", "결과",
        ])
    df = pd.DataFrame(result.rows)
    show = [c for c in (
        "주문ID", "고객명", "전화", "배송일", "담당",
        "입력원가", "본사원가", "원가차이", "입력판매가", "결과",
    ) if c in df.columns]
    return df[show]


def build_store_reconcile_report(client, db_filename: str, start: date, end: date) -> ReconcileResult:
    """기간(배송일) 내 헤더 vs 라인 원가. 엑셀 없이 재조회."""
    out = ReconcileResult()
    if not client or not db_filename:
        return out
    try:
        page, offset, orders = 1000, 0, []
        while True:
            r = (
                client.table("app_orders")
                .select(
                    "id, customer_id, delivery_date, total_amount, cost_price, "
                    "display_cost_amount, employee_names, import_source"
                )
                .eq("db_filename", db_filename)
                .gte("delivery_date", start.isoformat())
                .lte("delivery_date", end.isoformat())
                .order("id")
                .range(offset, offset + page - 1)
                .execute()
            )
            rows = r.data or []
            orders.extend(rows)
            if len(rows) < page:
                break
            offset += page
    except Exception as e:
        logger.warning("build_store_reconcile_report orders: %s", e)
        return out
    oids = []
    for o in orders:
        try:
            oids.append(int(o["id"]))
        except (TypeError, ValueError, KeyError):
            continue
    line_sum: dict[int, int] = {}
    for i in range(0, len(oids), 200):
        chunk = oids[i : i + 200]
        try:
            ir = (
                client.table("app_order_items")
                .select("order_id, line_cost")
                .in_("order_id", chunk)
                .execute()
            )
            for it in ir.data or []:
                try:
                    oid = int(it["order_id"])
                except (TypeError, ValueError, KeyError):
                    continue
                line_sum[oid] = line_sum.get(oid, 0) + _i(it.get("line_cost"))
        except Exception as e:
            logger.info("build_store_reconcile_report items: %s", e)
            break
    for o in orders:
        try:
            oid = int(o["id"])
        except (TypeError, ValueError, KeyError):
            continue
        seller = _seller_cost(o)
        hq = line_sum.get(oid)
        if hq is None:
            if o.get("import_source"):
                continue
            out.app_only += 1
            code, label = "app_only", "앱만 있음"
            hq_disp = None
            diff = None
        else:
            label, code = classify_cost_gap(seller, hq)
            hq_disp = hq
            diff = seller - hq
            if code == "cost_mismatch":
                out.mismatch += 1
            elif code == "cost_blank":
                out.blank += 1
            else:
                out.ok += 1
        out.rows.append({
            "주문ID": oid,
            "고객명": "",
            "전화": "",
            "배송일": str(o.get("delivery_date") or "")[:10],
            "담당": str(o.get("employee_names") or ""),
            "입력원가": seller,
            "본사원가": hq_disp,
            "원가차이": diff,
            "입력판매가": _i(o.get("total_amount")),
            "결과": label,
            "_code": code,
            "_level": alert_level_for_gap(seller, hq or 0, code) if hq is not None else "warning",
        })
    return out


def load_ai_feedback(client, db_filename: str, *, accept_n: int = 8, reject_n: int = 4) -> list[dict]:
    if not client or not db_filename:
        return []
    try:
        r = (
            client.table("app_import_ai_feedback")
            .select("kind, payload, decision, decided_at")
            .eq("db_filename", db_filename)
            .order("decided_at", desc=True)
            .limit(40)
            .execute()
        )
        rows = r.data or []
    except Exception as e:
        logger.info("load_ai_feedback: %s", e)
        return []
    acc = [x for x in rows if x.get("decision") == "accepted"][:accept_n]
    rej = [x for x in rows if x.get("decision") == "rejected"][:reject_n]
    return acc + rej


def save_ai_feedback(client, db_filename: str, kind: str, payload: dict, decision: str, decided_by: str) -> bool:
    if not client or not db_filename:
        return False
    try:
        client.table("app_import_ai_feedback").insert({
            "db_filename": db_filename,
            "kind": kind,
            "payload": payload,
            "decision": decision,
            "decided_by": (decided_by or "").strip() or None,
        }).execute()
        return True
    except Exception as e:
        logger.info("save_ai_feedback: %s", e)
        return False


def suggest_with_gemini(
    *,
    api_key: str,
    reconcile: ReconcileResult,
    preview_groups: list,
    feedback: list[dict],
    timeout: float = 30.0,
) -> dict[str, Any]:
    """분할 합산·부정 의심 제안. 확인 전까지 적용하지 않음."""
    empty = {"merges": [], "fraud_flags": [], "error": None}
    key = (api_key or os.environ.get("GEMINI_API_KEY", "")).strip()
    if not key:
        empty["error"] = "GEMINI_API_KEY 없음"
        return empty
    try:
        import httpx
    except ImportError:
        empty["error"] = "httpx 미설치"
        return empty

    suspects = [r for r in reconcile.rows if r.get("_code") in (
        "cost_mismatch", "cost_blank", "hq_only", "app_only",
    )]
    group_brief = []
    for g in (preview_groups or [])[:80]:
        group_brief.append({
            "name": getattr(g, "customer_name", ""),
            "phone": getattr(g, "phone1", ""),
            "order_date": getattr(g, "order_date", ""),
            "delivery_date": getattr(g, "delivery_date", ""),
            "status": getattr(g, "match_status", ""),
            "hq_cost": _i(getattr(g, "total_line_cost", 0)),
            "chosen_order_id": getattr(g, "chosen_order_id", None),
            "items": [getattr(it, "product_name", "") for it in (getattr(g, "items", []) or [])[:6]],
        })
    payload = {
        "suspects": [
            {k: r.get(k) for k in (
                "주문ID", "고객명", "전화", "배송일", "입력원가", "본사원가", "원가차이", "결과",
            )}
            for r in suspects[:60]
        ],
        "groups": group_brief,
        "examples": feedback[:12],
    }
    prompt = (
        "너는 가구 매장 매출-본사원장 대사 보조다. JSON만 반환한다.\n"
        "목표: (1) 실측+시공처럼 한 판매가 여러 주문으로 나뉜 묶음 제안 "
        "(2) 원가 누락·허위 입력 의심 설명.\n"
        "자동 확정하지 말고 제안만 한다. 확실하지 않으면 넣지 마라.\n"
        "스키마: {\"merges\":[{\"order_ids\":[int],\"reason\":str,\"confidence\":0-1}],"
        "\"fraud_flags\":[{\"order_id\":int|null,\"type\":str,\"reason\":str,\"confidence\":0-1}]}\n"
        f"데이터: {json.dumps(payload, ensure_ascii=False)[:12000]}"
    )
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-flash-latest:generateContent?key=" + key
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "responseMimeType": "application/json"},
    }
    try:
        resp = httpx.post(url, json=body, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
        text = (
            (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}])[0]
            .get("text") or ""
        )
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            empty["error"] = "JSON 객체 아님"
            return empty
        parsed.setdefault("merges", [])
        parsed.setdefault("fraud_flags", [])
        parsed["error"] = None
        return parsed
    except Exception as e:
        empty["error"] = str(e)
        return empty


def apply_confirmed_merges(rows: list[dict], merges: list[dict]) -> list[dict]:
    """확인된 묶음을 가상 한 줄로 합산. 원본 주문 헤더는 변경하지 않음."""
    if not merges:
        return rows
    remove: set[int] = set()
    extra: list[dict] = []
    by_id = {}
    for r in rows:
        try:
            by_id[int(r["주문ID"])] = r
        except (TypeError, ValueError):
            continue
    for m in merges:
        ids = []
        for x in m.get("order_ids") or []:
            try:
                ids.append(int(x))
            except (TypeError, ValueError):
                continue
        ids = [i for i in ids if i in by_id]
        if len(ids) < 2:
            continue
        parts = [by_id[i] for i in ids]
        seller = sum(_i(p.get("입력원가")) for p in parts)
        hq_vals = [p.get("본사원가") for p in parts if p.get("본사원가") is not None]
        hq = sum(_i(v) for v in hq_vals) if hq_vals else None
        sale = sum(_i(p.get("입력판매가")) for p in parts)
        if hq is None:
            label, code = "앱만 있음", "app_only"
            diff = None
        else:
            label, code = classify_cost_gap(seller, hq)
            diff = seller - hq
        extra.append({
            "주문ID": " / ".join(str(i) for i in ids),
            "고객명": parts[0].get("고객명") or "",
            "전화": parts[0].get("전화") or "",
            "배송일": " / ".join(str(p.get("배송일") or "") for p in parts),
            "담당": parts[0].get("담당") or "",
            "입력원가": seller,
            "본사원가": hq,
            "원가차이": diff,
            "입력판매가": sale,
            "결과": f"{label} (AI묶음)",
            "_code": code,
            "_level": alert_level_for_gap(seller, hq or 0, code) if hq is not None else "info",
        })
        remove.update(ids)
    kept = []
    for r in rows:
        try:
            if int(r["주문ID"]) in remove:
                continue
        except (TypeError, ValueError):
            pass
        kept.append(r)
    return extra + kept
