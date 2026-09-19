# -*- coding: utf-8 -*-
"""외부결제 대사 AI 유사 매칭 (Gemini).

기존 `import_cost_reconcile.suggest_with_gemini` 와 동일한 UX:
- 자동 저장 없음. 관리자 확인 후에만 매칭 저장.
- accepted 8건 + rejected 4건 in-context few-shot.
- 피드백은 `app_import_ai_feedback` 재사용, `kind` 에 소스 접미사 (`extpay_pair_onnuri` 등)를 붙여
  원가 대사 피드백과 섞이지 않게 한다.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)

# 대상 미매칭 result_code
UNMATCHED_OFFICIAL_CODES = (
    "official_only",
    "ambiguous",
    "amount_mismatch",
    "official_canceled",
    "erp_canceled_official_paid",
)
MATCHED_OK_CODES = ("matched_ok", "manual_matched", "split_matched")

# 지원 소스
SUPPORTED_SOURCES = ("onnuri", "ulsanpay", "card", "mainpay")

_SRC_LABEL_KO = {
    "onnuri": "온누리",
    "ulsanpay": "울산페이",
    "card": "카드",
    "mainpay": "메인페이",
}


# ─────────────────────────────────────────────────────────────
# 마스킹 유틸 (프롬프트로 나가기 전 개인정보 축소)
# ─────────────────────────────────────────────────────────────


def _mask_name(name: str | None) -> str:
    """'장상국' → '장*국', '최형' → '최*', '홍길동재' → '홍*동재'.
    이미 마스킹된 문자(*)가 있으면 그대로 둔다."""
    s = (name or "").strip()
    if not s:
        return ""
    if "*" in s:
        return s
    if len(s) <= 1:
        return s
    if len(s) == 2:
        return s[0] + "*"
    return s[0] + "*" + s[2:]


def _mask_phone(phone: str | None) -> str:
    """전화번호는 뒤 4자리만 노출."""
    if not phone:
        return ""
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) < 4:
        return ""
    return "****-" + digits[-4:]


# ─────────────────────────────────────────────────────────────
# 컨텍스트 빌드 (df → dict)
# ─────────────────────────────────────────────────────────────


def _to_int(v: Any, default: int = 0) -> int:
    try:
        if isinstance(v, str):
            v = v.replace(",", "").strip()
            if not v:
                return default
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


def _row_pick(r: dict, key: str, default: str = "") -> str:
    v = r.get(key)
    if v is None:
        return default
    return str(v).strip()


def build_ai_context(
    df,
    source: str,
    *,
    max_official: int = 60,
    max_erp_only: int = 60,
    max_examples: int = 12,
) -> dict:
    """`_ext_pay_list_matches_df` 결과에서 AI 프롬프트용 dict 를 만든다.

    반환 스키마:
    {
      "source": str,
      "unmatched_official": [{...}],
      "erp_only": [{...}],
      "matched_examples": [{...}],
    }
    """
    empty = {
        "source": source,
        "unmatched_official": [],
        "erp_only": [],
        "matched_examples": [],
    }
    if df is None or getattr(df, "empty", True):
        return empty

    unmatched_official: list[dict] = []
    erp_only: list[dict] = []
    matched_examples: list[dict] = []

    for _, row in df.iterrows():
        code = _row_pick(row, "결과")
        try:
            row_id = int(row.get("row_id") or 0)
        except (TypeError, ValueError):
            row_id = 0
        try:
            pid = int(row.get("_payment_id")) if row.get("_payment_id") is not None else None
        except (TypeError, ValueError):
            pid = None
        try:
            oid = int(row.get("_order_id")) if row.get("_order_id") is not None else None
        except (TypeError, ValueError):
            oid = None

        if code in UNMATCHED_OFFICIAL_CODES and row_id > 0:
            if len(unmatched_official) >= max_official:
                continue
            unmatched_official.append({
                "row_id": row_id,
                "tx_date": _row_pick(row, "공식일자"),
                "last4": _row_pick(row, "뒤4"),
                "approval": _row_pick(row, "승인번호"),
                "amount": _to_int(row.get("공식금액")),
                "buyer_masked": _mask_name(_row_pick(row, "구매자")),
                "tx_status": _row_pick(row, "공식상태"),
                "result_code": code,
            })
        elif code == "erp_only":
            if len(erp_only) >= max_erp_only:
                continue
            erp_only.append({
                "payment_id": pid,
                "order_id": oid,
                "payment_date": _row_pick(row, "ERP일자"),
                "amount": _to_int(row.get("_amount_int") or row.get("ERP금액")),
                "method": _row_pick(row, "_payment_method"),
                "last4": _row_pick(row, "뒤4"),
                "approval": _row_pick(row, "승인번호"),
                "customer_masked": _mask_name(_row_pick(row, "고객명")),
                "phone_masked": _mask_phone(_row_pick(row, "고객전화")),
                "employee": _row_pick(row, "담당매니저"),
            })
        elif code in MATCHED_OK_CODES and row_id > 0 and pid is not None:
            if len(matched_examples) >= max_examples:
                continue
            matched_examples.append({
                "official": {
                    "tx_date": _row_pick(row, "공식일자"),
                    "last4": _row_pick(row, "뒤4"),
                    "approval": _row_pick(row, "승인번호"),
                    "amount": _to_int(row.get("공식금액")),
                    "buyer_masked": _mask_name(_row_pick(row, "구매자")),
                },
                "payment": {
                    "payment_id": pid,
                    "order_id": oid,
                    "payment_date": _row_pick(row, "ERP일자"),
                    "amount": _to_int(row.get("_amount_int") or row.get("ERP금액")),
                    "last4": _row_pick(row, "뒤4"),
                    "customer_masked": _mask_name(_row_pick(row, "고객명")),
                    "phone_masked": _mask_phone(_row_pick(row, "고객전화")),
                },
                "result_code": code,
            })

    return {
        "source": source,
        "unmatched_official": unmatched_official,
        "erp_only": erp_only,
        "matched_examples": matched_examples,
    }


# ─────────────────────────────────────────────────────────────
# 피드백 로더/세이버 (app_import_ai_feedback 재사용 + kind 접미사)
# ─────────────────────────────────────────────────────────────


def _kind_pattern(source: str) -> str:
    """`kind LIKE 'extpay_%_{source}'` 용 SQL 패턴."""
    return f"extpay_%_{source}"


def load_feedback(
    sc, db_filename: str, source: str,
    *, accept_n: int = 8, reject_n: int = 4,
) -> list[dict]:
    """소스별 최근 accepted/rejected 예시를 로드. sc 는 supabase client."""
    if not sc or not db_filename or source not in SUPPORTED_SOURCES:
        return []
    try:
        r = (
            sc.table("app_import_ai_feedback")
            .select("kind, payload, decision, decided_at")
            .eq("db_filename", db_filename)
            .like("kind", _kind_pattern(source))
            .order("decided_at", desc=True)
            .limit(60)
            .execute()
        )
        rows = r.data or []
    except Exception as e:
        logger.info("ext_pay ai load_feedback: %s", e)
        return []
    acc = [x for x in rows if x.get("decision") == "accepted"][:accept_n]
    rej = [x for x in rows if x.get("decision") == "rejected"][:reject_n]
    return acc + rej


def save_feedback(
    sc, db_filename: str, kind_base: str, source: str,
    payload: dict, decision: str, decided_by: str | None,
) -> bool:
    """kind = f'{kind_base}_{source}' 로 저장.
    kind_base ∈ {extpay_pair, extpay_split, extpay_flag}"""
    if not sc or not db_filename or not source:
        return False
    if not str(kind_base).startswith("extpay_"):
        return False
    try:
        sc.table("app_import_ai_feedback").insert({
            "db_filename": db_filename,
            "kind": f"{kind_base}_{source}",
            "payload": payload,
            "decision": decision,
            "decided_by": (decided_by or "").strip() or None,
        }).execute()
        return True
    except Exception as e:
        logger.info("ext_pay ai save_feedback: %s", e)
        return False


# ─────────────────────────────────────────────────────────────
# Gemini 호출
# ─────────────────────────────────────────────────────────────


_EMPTY_RESULT = {"pairs": [], "splits": [], "flags": [], "error": None}


def suggest_matches_with_gemini(
    *,
    api_key: str,
    source: str,
    context: dict,
    feedback: list[dict],
    timeout: float = 30.0,
) -> dict[str, Any]:
    """외부결제 대사 유사 매칭 제안. 자동 확정하지 않고 후보만 반환.

    반환 스키마:
    {
      "pairs":  [{"row_id": int, "payment_id": int, "reason": str, "confidence": float}],
      "splits": [{"row_id": int, "payment_ids": [int], "reason": str, "confidence": float}],
      "flags":  [{"row_id": int|None, "payment_id": int|None, "type": str,
                  "reason": str, "confidence": float}],
      "error":  str|None
    }
    """
    out = dict(_EMPTY_RESULT)
    out["pairs"] = []
    out["splits"] = []
    out["flags"] = []
    key = (api_key or os.environ.get("GEMINI_API_KEY", "")).strip()
    if not key:
        out["error"] = "GEMINI_API_KEY 없음"
        return out
    if source not in SUPPORTED_SOURCES:
        out["error"] = f"지원하지 않는 소스: {source}"
        return out
    if not context or (
        not context.get("unmatched_official") and not context.get("erp_only")
    ):
        return out

    try:
        import httpx  # type: ignore
    except ImportError:
        out["error"] = "httpx 미설치"
        return out

    src_label = _SRC_LABEL_KO.get(source, source)
    payload = {
        "source": source,
        "unmatched_official": context.get("unmatched_official") or [],
        "erp_only": context.get("erp_only") or [],
        "matched_examples": context.get("matched_examples") or [],
        "learned_examples": feedback or [],
    }
    if source == "ulsanpay":
        _priority = (
            "판단 근거 우선순위(울산페이):\n"
            "1) 승인번호(approval) 6자리 정확 일치(선행 0 포함·제거 모두 동일로 본다)\n"
            "2) 결제금액 일치\n"
            "3) 결제일과 tx_date 가 같거나 ±2일 이내\n"
            "4) 구매자 마스킹 이름과 모모 고객 마스킹 이름의 성·끝글자 조각 일치\n"
            "5) 같은 order 내 여러 결제 합이 공식 금액과 일치하면 splits 로 제안\n"
        )
    else:
        _priority = (
            "판단 근거 우선순위:\n"
            "1) 승인번호(approval) 정확 일치\n"
            "2) 전화 뒤4자리(last4) 일치\n"
            "3) 결제일과 tx_date 가 같거나 ±2일 이내\n"
            "4) 구매자 마스킹 이름과 모모 고객 마스킹 이름의 성·끝글자 조각 일치\n"
            "5) 같은 order 내 여러 결제 합이 공식 금액과 일치하면 splits 로 제안\n"
        )
    prompt = (
        f"너는 가구 매장의 외부결제 대사 보조다({src_label}). JSON만 반환한다.\n"
        "목표: 공식 파일 미매칭 행(unmatched_official)과 모모 미매칭 결제(erp_only)를 "
        f"가장 자연스럽게 잇는 후보를 제안한다.\n{_priority}"
        "규칙:\n"
        "- confidence < 0.5 는 절대 포함하지 마라.\n"
        "- 동일 payment_id 를 여러 pair 에 넣지 마라.\n"
        "- pairs 와 splits 에 같은 row_id 를 중복 넣지 마라.\n"
        "- 판단 근거를 reason 에 20자 이내로 요약.\n"
        "- 자동 확정하지 않는다. 확실하지 않으면 flags 에 검토 대상으로만 남겨라.\n"
        "- learned_examples 의 accepted 는 유사 판단 시 참고, rejected 는 유사 패턴을 피해라.\n"
        "스키마: {"
        "\"pairs\":[{\"row_id\":int,\"payment_id\":int,\"reason\":str,\"confidence\":0-1}],"
        "\"splits\":[{\"row_id\":int,\"payment_ids\":[int],\"reason\":str,\"confidence\":0-1}],"
        "\"flags\":[{\"row_id\":int|null,\"payment_id\":int|null,\"type\":str,"
        "\"reason\":str,\"confidence\":0-1}]}\n"
        f"데이터: {json.dumps(payload, ensure_ascii=False)[:12000]}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "responseMimeType": "application/json",
        },
    }
    # 일시 오류(5xx · 429) 는 백오프 후 재시도, 4xx 는 즉시 다음 모델로 폴백
    import time as _time
    # 오버라이드: st.secrets["gemini"]["extpay_models"] 리스트가 있으면 우선. 없으면 기본 폴백.
    _override: list[str] = []
    try:
        import streamlit as _st  # type: ignore
        _cfg = (_st.secrets.get("gemini") or {}).get("extpay_models") or []
        if isinstance(_cfg, (list, tuple)):
            _override = [str(x).strip() for x in _cfg if str(x).strip()]
    except Exception:
        pass
    # 이 API 키로 generateContent 가 확인된 현재 모델.
    # gemini-2.5-flash / gemini-1.5-* 는 목록에 남아도 신규 키에서 404 가 난다.
    _models = tuple(_override) or (
        "gemini-flash-latest",
        "gemini-3.5-flash",
        "gemini-flash-lite-latest",
        "gemini-3.6-flash",
        "gemini-3.8-flash",
    )
    _retry_status = {429, 500, 502, 503, 504}
    last_err: str | None = None
    last_status_codes: list[int] = []
    resp = None
    _stop_all = False
    # 1라운드: 모델만 빠르게 순회. 503 이어도 같은 모델에서 오래 기다리지 않고 다음으로.
    # 2라운드: 전부 실패하면 2초 후 한 번 더.
    for _round in (0, 1):
        if _round == 1:
            _time.sleep(2.0)
        for _model in _models:
            _url = (
                "https://generativelanguage.googleapis.com/v1beta/models/"
                f"{_model}:generateContent?key={key}"
            )
            try:
                _cur = httpx.post(_url, json=body, timeout=timeout)
            except Exception as e:
                last_err = f"{_model}: {e}"
                continue
            last_status_codes.append(_cur.status_code)
            if _cur.status_code == 200:
                resp = _cur
                break
            last_err = f"{_model}: HTTP {_cur.status_code}"
            if _cur.status_code in (401, 403):
                last_err = f"{_model}: HTTP {_cur.status_code} (API 키 권한 오류)"
                _stop_all = True
                break
        if resp is not None or _stop_all:
            break
    if resp is None:
        _access_bad = last_status_codes and all(c in (400, 404) for c in last_status_codes)
        _auth_bad = last_status_codes and any(c in (401, 403) for c in last_status_codes)
        if _auth_bad:
            out["error"] = (
                f"Gemini API 키 권한이 거부되었습니다 ({last_err}). "
                ".streamlit/secrets.toml 의 [gemini] api_key 를 확인해 주세요."
            )
        elif _access_bad:
            out["error"] = (
                "이 API 키로 사용 가능한 Gemini 모델을 찾지 못했습니다 "
                f"(마지막 오류: {last_err or 'HTTP 404'}). "
                "필요 시 `st.secrets['gemini']['extpay_models']` 에 모델 이름을 지정할 수 있습니다."
            )
        else:
            out["error"] = (
                f"Gemini 서버 일시 오류로 제안을 받지 못했습니다 ({last_err or 'unknown'}). "
                "잠시 후 다시 시도해 주세요."
            )
        return out
    try:
        data = resp.json()
        text = (
            (((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or [{}])[0]
            .get("text") or ""
        )
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            out["error"] = "JSON 객체 아님"
            return out
        parsed.setdefault("pairs", [])
        parsed.setdefault("splits", [])
        parsed.setdefault("flags", [])
        # 스키마 정제: 필수 키 없거나 confidence < 0.5 는 제거
        parsed["pairs"] = _sanitize_pairs(parsed["pairs"])
        parsed["splits"] = _sanitize_splits(parsed["splits"])
        parsed["flags"] = _sanitize_flags(parsed["flags"])
        parsed["error"] = None
        return parsed
    except Exception as e:
        out["error"] = str(e)
        return out


def _sanitize_pairs(items) -> list[dict]:
    out: list[dict] = []
    seen_pids: set[int] = set()
    seen_rows: set[int] = set()
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            row_id = int(it.get("row_id"))
            pid = int(it.get("payment_id"))
        except (TypeError, ValueError):
            continue
        try:
            conf = float(it.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        if conf < 0.5:
            continue
        if pid in seen_pids or row_id in seen_rows:
            continue
        seen_pids.add(pid)
        seen_rows.add(row_id)
        out.append({
            "row_id": row_id,
            "payment_id": pid,
            "reason": str(it.get("reason") or "").strip()[:200],
            "confidence": round(conf, 3),
        })
    return out


def _sanitize_splits(items) -> list[dict]:
    out: list[dict] = []
    seen_rows: set[int] = set()
    seen_pids: set[int] = set()
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            row_id = int(it.get("row_id"))
        except (TypeError, ValueError):
            continue
        pids_raw = it.get("payment_ids") or []
        pids: list[int] = []
        for x in pids_raw:
            try:
                pids.append(int(x))
            except (TypeError, ValueError):
                continue
        pids = sorted({p for p in pids if p not in seen_pids})
        if len(pids) < 2:
            continue
        try:
            conf = float(it.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        if conf < 0.5:
            continue
        if row_id in seen_rows:
            continue
        seen_rows.add(row_id)
        for p in pids:
            seen_pids.add(p)
        out.append({
            "row_id": row_id,
            "payment_ids": pids,
            "reason": str(it.get("reason") or "").strip()[:200],
            "confidence": round(conf, 3),
        })
    return out


def _sanitize_flags(items) -> list[dict]:
    out: list[dict] = []
    if not isinstance(items, list):
        return out
    for it in items:
        if not isinstance(it, dict):
            continue
        try:
            row_id = int(it["row_id"]) if it.get("row_id") is not None else None
        except (TypeError, ValueError):
            row_id = None
        try:
            pid = int(it["payment_id"]) if it.get("payment_id") is not None else None
        except (TypeError, ValueError):
            pid = None
        try:
            conf = float(it.get("confidence") or 0)
        except (TypeError, ValueError):
            conf = 0.0
        out.append({
            "row_id": row_id,
            "payment_id": pid,
            "type": str(it.get("type") or "").strip()[:60],
            "reason": str(it.get("reason") or "").strip()[:200],
            "confidence": round(conf, 3),
        })
    return out


def src_label(source: str) -> str:
    return _SRC_LABEL_KO.get(source, source)
