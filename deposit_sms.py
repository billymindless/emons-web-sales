# -*- coding: utf-8 -*-
"""기업은행 입금 SMS 파서.

기업은행 [Web발신] 문자 형식 (줄 단위):
    [Web발신]
    2026/06/01 11:30
    입금 3,716,000원
    잔액 4,801,905원
    디지털온누리
    392***16401011
    기업

입금(입금)만 처리하고 출금은 무시한다. 계좌는 마스킹되어 오므로 끝 8자리(account_suffix)로
매장을 판별한다.
"""

from __future__ import annotations

import hashlib
import re

_RE_DATETIME = re.compile(r"(\d{4})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2})")
_RE_DEPOSIT = re.compile(r"입금\s+([\d,]+)\s*원")
_RE_WITHDRAW = re.compile(r"출금\s+([\d,]+)\s*원")
_RE_BALANCE = re.compile(r"잔액\s+([\d,]+)\s*원")
_RE_ACCOUNT = re.compile(r"(\d{2,4})\*+(\d+)")


def _to_int(num_text: str) -> int:
    return int((num_text or "").replace(",", "").strip() or 0)


def parse_ibk_sms(text: str) -> dict | None:
    """기업은행 입금 SMS를 파싱해 dict 반환. 입금 문자가 아니거나 형식 불일치 시 None.

    반환 키: txn_at(ISO 문자열), amount(int), balance(int|None),
             counterparty(str|None), account_masked(str|None),
             account_suffix(str|None), bank_name(str)
    """
    if not text:
        return None

    # 출금 문자는 처리하지 않음
    if _RE_WITHDRAW.search(text) and not _RE_DEPOSIT.search(text):
        return None

    m_amount = _RE_DEPOSIT.search(text)
    if not m_amount:
        return None
    amount = _to_int(m_amount.group(1))

    m_dt = _RE_DATETIME.search(text)
    if not m_dt:
        return None
    y, mo, d, hh, mm = m_dt.groups()
    txn_at = f"{y}-{mo}-{d}T{hh}:{mm}:00+09:00"

    m_bal = _RE_BALANCE.search(text)
    balance = _to_int(m_bal.group(1)) if m_bal else None

    account_masked = None
    account_suffix = None
    m_acc = _RE_ACCOUNT.search(text)
    if m_acc:
        account_masked = m_acc.group(0)
        account_suffix = m_acc.group(2)

    counterparty = _extract_counterparty(text, account_masked)

    return {
        "txn_at": txn_at,
        "amount": amount,
        "balance": balance,
        "counterparty": counterparty,
        "account_masked": account_masked,
        "account_suffix": account_suffix,
        "bank_name": "기업은행",
    }


def _extract_counterparty(text: str, account_masked: str | None) -> str | None:
    """거래처명(입금자명) 추출: 금액/잔액/계좌/은행/머리말이 아닌 일반 텍스트 줄."""
    skip_keywords = ("[web발신]", "입금", "출금", "잔액", "기업")
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        low = line.lower()
        if any(k in low for k in skip_keywords[:1]):  # [Web발신]
            continue
        if _RE_DATETIME.search(line):
            continue
        if _RE_DEPOSIT.search(line) or _RE_WITHDRAW.search(line) or _RE_BALANCE.search(line):
            continue
        if account_masked and account_masked in line:
            continue
        if _RE_ACCOUNT.search(line):
            continue
        if line in ("기업", "기업은행"):
            continue
        if line.startswith("입금") or line.startswith("출금") or line.startswith("잔액"):
            continue
        return line
    return None


def match_store(account_suffix: str | None, accounts: list[dict]) -> str | None:
    """계좌 끝자리(account_suffix)로 매장명 판별. 매칭 실패 시 None(미분류).

    accounts: [{"account_suffix": "16401011", "store_name": "...", "is_active": True}, ...]
    """
    if not account_suffix:
        return None
    suffix = account_suffix.strip()
    for acc in accounts or []:
        if not acc.get("is_active", True):
            continue
        acc_suffix = (acc.get("account_suffix") or "").strip()
        if not acc_suffix:
            continue
        if acc_suffix == suffix or suffix.endswith(acc_suffix) or acc_suffix.endswith(suffix):
            return acc.get("store_name")
    return None


def make_dedup_hash(parsed: dict) -> str:
    """동일 입금 문자 중복 적재 방지 키. 시각+금액+계좌+거래처 조합 해시."""
    base = "|".join([
        str(parsed.get("txn_at") or ""),
        str(parsed.get("amount") or ""),
        str(parsed.get("account_suffix") or ""),
        str(parsed.get("counterparty") or ""),
    ])
    return hashlib.sha256(base.encode("utf-8")).hexdigest()
