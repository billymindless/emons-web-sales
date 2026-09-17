# -*- coding: utf-8 -*-
"""본사 ERP "주문조회(대)_emsf" 엑셀 대사 서비스.

역할:
    - 엑셀/CSV 파싱 (제목·TOTAL 스킵, 회수/취소 제외)
    - 업로드 이력 + 출고번호 단위 스냅샷 upsert (중복 스킵, 감/증액 delta 감지)
    - 앱 주문(app_orders)과 (전화 또는 이름) + 등록일 ±2일 매칭
    - 원가 대사 (입력원가 vs 본사원가) + 계약 변경 gap 판정
    - 미리보기·다운로드용 DataFrame / 엑셀 빌더

주의:
    - 이 모듈은 `app_orders` / `app_payments` / `sales` 를 **절대 변경하지 않는다**.
    - write 는 `app_hq_order_uploads` / `app_hq_order_snapshots` / `app_hq_reconcile_matches` 뿐.
    - 동일 출고(매장+출고번호+등록일) 재업로드는 스냅샷·업로드 이력을 새로 만들지 않는다.
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

MATCH_WINDOW_DAYS: int = 2

COST_ABS_THRESHOLD: int = 10_000
COST_PCT_THRESHOLD: float = 0.05

# 스킵 대상 주문구분 (매입 원장 규칙과 동일)
EXCLUDED_ORDER_KINDS: set[str] = {"회수"}

# 전시(매장 자산) 판별
DISPLAY_ORDER_KINDS: set[str] = {"매장분"}
_DISPLAY_NAME_PATTERNS: tuple[str, ...] = ("리빙(법)",)

# 취소로 간주할 주문상태 표기 (본사 파일)
CANCELLED_STATUS_TOKENS: set[str] = {"취소", "취소요청", "회수", "반품"}

# 컬럼 별칭 (본사 주문조회(대) → 표준 필드)
COLUMN_ALIASES: dict[str, list[str]] = {
    "order_kind":     ["주문구분", "구분"],
    "total_hq":       ["합계", "총액"],
    "order_date":     ["등록일", "주문일"],
    "ship_date":      ["출고일"],
    "delivery_date":  ["배송일"],
    "customer_name":  ["고객명", "이름", "성명"],
    "employee_names": ["판매자(대)", "판매자(직)", "판매자", "담당자"],
    "phone1":         ["전화1", "전화번호1", "휴대폰"],
    "phone2":         ["전화2", "전화번호2"],
    "address1":       ["주소1", "주소", "도로명주소"],
    "address2":       ["주소2", "상세주소", "지번"],
    "ship_number":    ["출고번호"],
    "direct_ship":    ["직배구분"],
    "order_amount":   ["주문금액", "매입금액"],
    "vat":            ["부가세", "VAT"],
    "delivery_fee":   ["배송금액"],
    "paid_amount":    ["입금금액"],
    "balance_amount": ["미수금액"],
    "cash":           ["현금"],
    "bank":           ["은행"],
    "card":           ["카드"],
    "voucher":        ["상품권"],
    "etc_paid":       ["기타입금액"],
    "order_status":   ["주문상태", "상태"],
    "dispatch_status": ["배차상태"],
    "kinship":        ["연고관계"],
    "order_memo1":    ["주문메모1"],
    "order_memo2":    ["주문메모2"],
    "delivery_note":  ["배송비고"],
    "driver_name":    ["배송기사"],
    "driver_phone":   ["기사연락처"],
    "register_by":    ["등록담당"],
    "contract_date":  ["계약일"],
    "outlet":         ["아울렛"],
    "contract_no":    ["판매계약번호"],
    "coupon_no":      ["쿠폰번호"],
}

# 파일이 헤더 행이라고 판정할 마커 (3개 이상 매칭 시 헤더 행)
_HEADER_MARKER_COLS: set[str] = {"주문구분", "등록일", "고객명", "전화1", "출고번호", "주문금액", "합계"}

_TOTAL_MARKERS: set[str] = {"TOTAL", "SUM", "합계", "총계"}


# ---------------------------------------------------------------------------
# 유틸
# ---------------------------------------------------------------------------

def _clean_str(v: Any) -> str:
    if v is None:
        return ""
    try:
        if isinstance(v, float) and pd.isna(v):
            return ""
    except Exception:
        pass
    s = str(v).strip()
    if s.lower() in ("nan", "nat", "none"):
        return ""
    return s


def _to_int(v: Any) -> int:
    if v is None:
        return 0
    try:
        if isinstance(v, float) and pd.isna(v):
            return 0
    except Exception:
        pass
    if isinstance(v, (int, float)):
        try:
            return int(round(float(v)))
        except (TypeError, ValueError):
            return 0
    s = re.sub(r"[,\s원]", "", str(v))
    if not s or s in ("-", ".", "nan"):
        return 0
    try:
        return int(round(float(s)))
    except ValueError:
        return 0


def _to_bool(v: Any) -> bool:
    s = _clean_str(v).upper()
    return s in ("TRUE", "T", "Y", "YES", "1")


def _phone_digits(v: Any) -> str:
    s = _clean_str(v)
    if not s:
        return ""
    return re.sub(r"\D", "", s)


def _normalize_name_key(v: Any) -> str:
    s = _clean_str(v)
    if not s:
        return ""
    # 대괄호 태그 / 별표 접미 제거
    s = re.sub(r"\[[^\[\]]*\]", "", s).replace("*", "").strip()
    return re.sub(r"\s+", "", s).lower()


def _identity_key(phone_digits: str, customer_name: Any) -> str:
    if phone_digits:
        return phone_digits
    n = _normalize_name_key(customer_name)
    return f"NAME:{n}" if n else ""


def _parse_date(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = _clean_str(v)
    if not s:
        return None
    s = s[:10].replace(".", "-").replace("/", "-")
    try:
        return date.fromisoformat(s)
    except ValueError:
        try:
            return datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            return None


def _is_display(customer_name: Any, order_kind: Any) -> bool:
    nk = _normalize_name_key(customer_name)
    if nk:
        for pat in _DISPLAY_NAME_PATTERNS:
            if _normalize_name_key(pat) in nk:
                return True
    if _clean_str(order_kind) in DISPLAY_ORDER_KINDS:
        return True
    return False


# ---------------------------------------------------------------------------
# 파싱
# ---------------------------------------------------------------------------

def _detect_header_row(raw: pd.DataFrame) -> int:
    """앞 5행에서 헤더 마커가 3개 이상 있는 첫 행 반환. 못 찾으면 0."""
    for i in range(min(5, len(raw))):
        cells = {_clean_str(v) for v in raw.iloc[i].tolist()}
        if len(_HEADER_MARKER_COLS & cells) >= 3:
            return i
    return 0


def _read_raw(file_bytes: bytes, filename: str) -> pd.DataFrame:
    name = (filename or "").lower()
    bio = io.BytesIO(file_bytes)
    if name.endswith(".csv") or name.endswith(".txt"):
        # 자동 인코딩 시도
        for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
            try:
                bio.seek(0)
                return pd.read_csv(bio, dtype=object, header=None, encoding=enc)
            except UnicodeDecodeError:
                continue
        bio.seek(0)
        return pd.read_csv(bio, dtype=object, header=None, encoding="utf-8", errors="ignore")
    # xlsx / xls
    bio.seek(0)
    return pd.read_excel(bio, sheet_name=0, header=None, engine="openpyxl", dtype=object)


def _resolve_columns(header_row: list[str]) -> dict[str, str]:
    """헤더 라벨 → 표준 필드 매핑."""
    labels = [_clean_str(x) for x in header_row]
    out: dict[str, str] = {}
    for key, aliases in COLUMN_ALIASES.items():
        for a in aliases:
            for actual in labels:
                if actual == a:
                    out[key] = actual
                    break
            if key in out:
                break
    return out


@dataclass
class HQRow:
    """엑셀 1행 (본사 출고 1건). 파일 기준값과 파생값을 담는다."""

    ship_number: str = ""
    contract_no: str = ""
    order_kind: str = ""
    order_status: str = ""
    customer_name: str = ""
    phone1_digits: str = ""
    phone2_digits: str = ""
    address: str = ""
    employee_names: str = ""
    order_date: Optional[date] = None
    contract_date: Optional[date] = None
    ship_date: Optional[date] = None
    delivery_date: Optional[date] = None
    order_amount: int = 0
    vat: int = 0
    total_amount_hq: int = 0
    outlet: bool = False
    is_display: bool = False
    identity_key: str = ""
    raw_row: dict[str, Any] = field(default_factory=dict)


def _hq_ship_cost(row: HQRow) -> int:
    """전산 출고 원가 = 주문금액 + 부가세 (파일의 합계). 합계가 비면 두 칸을 더한다."""
    total = int(row.total_amount_hq or 0)
    if total:
        return total
    return int(row.order_amount or 0) + int(row.vat or 0)


def parse_hq_order_export(file_bytes: bytes, filename: str) -> list[HQRow]:
    """본사 주문조회(대) 엑셀/CSV → HQRow 리스트.

    - 제목 행, TOTAL 요약 행, 회수/취소로 판정되는 행은 제외.
    - `주문구분 = 주문` 만 통과. 나머지는 스킵.
    - ship_number 가 비어도 통과. 그룹핑 단계에서 contract_no / identity_key + 등록일 fallback.
    """
    raw = _read_raw(file_bytes, filename)
    if raw is None or raw.empty:
        return []

    hi = _detect_header_row(raw)
    labels = [_clean_str(x) for x in raw.iloc[hi].tolist()]
    body = raw.iloc[hi + 1:].reset_index(drop=True).copy()
    body.columns = labels + [f"__extra_{j}" for j in range(len(body.columns) - len(labels))]
    col = _resolve_columns(labels)

    def _get(row, key: str) -> Any:
        c = col.get(key)
        if c is None or c not in body.columns:
            return None
        return row[c]

    rows: list[HQRow] = []
    for _, row in body.iterrows():
        # TOTAL 행 제거
        _first_vals = [_clean_str(v).upper() for v in row.tolist()[:3]]
        if any(v in _TOTAL_MARKERS for v in _first_vals):
            continue

        kind = _clean_str(_get(row, "order_kind"))
        if not kind:
            # 완전히 빈 행 스킵
            if all(_clean_str(v) == "" for v in row.tolist()):
                continue
            # 주문구분 없는 데이터는 취급하지 않음
            continue
        if kind in EXCLUDED_ORDER_KINDS:
            continue
        if kind != "주문":
            # 회수/기타 → 스킵
            continue

        status = _clean_str(_get(row, "order_status"))
        cust = _clean_str(_get(row, "customer_name"))
        phone1 = _phone_digits(_get(row, "phone1"))
        phone2 = _phone_digits(_get(row, "phone2"))
        ident = _identity_key(phone1, cust)
        if not ident:
            # 전화/이름 둘 다 없으면 매칭 불가 → 스킵 (로그만)
            logger.info("hq skip: no identity, row=%r", row.to_dict())
            continue

        r = HQRow(
            ship_number=_clean_str(_get(row, "ship_number")),
            contract_no=_clean_str(_get(row, "contract_no")),
            order_kind=kind,
            order_status=status,
            customer_name=cust,
            phone1_digits=phone1,
            phone2_digits=phone2,
            address=" ".join(x for x in (
                _clean_str(_get(row, "address1")),
                _clean_str(_get(row, "address2")),
            ) if x),
            employee_names=_clean_str(_get(row, "employee_names")),
            order_date=_parse_date(_get(row, "order_date")) or _parse_date(_get(row, "contract_date")),
            contract_date=_parse_date(_get(row, "contract_date")),
            ship_date=_parse_date(_get(row, "ship_date")),
            delivery_date=_parse_date(_get(row, "delivery_date")),
            order_amount=_to_int(_get(row, "order_amount")),
            vat=_to_int(_get(row, "vat")),
            total_amount_hq=_to_int(_get(row, "total_hq"))
            or (_to_int(_get(row, "order_amount")) + _to_int(_get(row, "vat"))),
            outlet=_to_bool(_get(row, "outlet")),
            is_display=_is_display(cust, kind),
            identity_key=ident,
        )
        # ship_number fallback (contract_no)
        if not r.ship_number and r.contract_no:
            r.ship_number = f"CN:{r.contract_no}"
        # 최후 fallback: identity+등록일 (같은 파일 안 중복 방지에는 부족하지만 upsert 는 됨)
        if not r.ship_number:
            od = r.order_date.isoformat() if r.order_date else "NA"
            r.ship_number = f"AUTO:{r.identity_key}:{od}"

        rows.append(r)
    return rows


# ---------------------------------------------------------------------------
# 스냅샷 upsert + delta
# ---------------------------------------------------------------------------

@dataclass
class SnapshotResult:
    upload_id: Optional[int] = None
    new: list[dict] = field(default_factory=list)
    dup: list[dict] = field(default_factory=list)
    revised: list[dict] = field(default_factory=list)
    missing: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped_duplicate_upload: bool = False

    def summary(self) -> dict[str, int]:
        return {
            "new": len(self.new),
            "dup": len(self.dup),
            "revised": len(self.revised),
            "missing": len(self.missing),
        }


def _is_unique_violation(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "23505" in msg or "duplicate key" in msg or "uniq_hq_" in msg


def _snapshot_key(ship_number: str, order_date: Any) -> tuple[str, str]:
    """스냅샷 dedup 키. 출고번호는 매장별 순환이므로 등록일까지 조합."""
    od = ""
    if isinstance(order_date, date):
        od = order_date.isoformat()
    elif order_date:
        od = str(order_date)[:10]
    return (str(ship_number or ""), od)


def _fetch_snapshots_by_ship(client, db_filename: str, ship_numbers: list[str]) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    if not client or not db_filename or not ship_numbers:
        return out
    _CHUNK = 300
    for i in range(0, len(ship_numbers), _CHUNK):
        chunk = ship_numbers[i:i + _CHUNK]
        try:
            r = (
                client.table("app_hq_order_snapshots")
                .select("*")
                .eq("db_filename", db_filename)
                .in_("ship_number", chunk)
                .execute()
            )
            for row in (r.data or []):
                sn = row.get("ship_number")
                if not sn:
                    continue
                out[_snapshot_key(sn, row.get("order_date"))] = row
        except Exception as e:
            logger.warning("_fetch_snapshots_by_ship failed: %s", e)
            break
    return out


def _fetch_all_snapshot_keys(client, db_filename: str) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    if not client or not db_filename:
        return out
    _PAGE = 1000
    offset = 0
    while True:
        try:
            r = (
                client.table("app_hq_order_snapshots")
                .select("ship_number, order_date")
                .eq("db_filename", db_filename)
                .range(offset, offset + _PAGE - 1)
                .execute()
            )
            page = r.data or []
        except Exception as e:
            logger.warning("_fetch_all_snapshot_keys failed: %s", e)
            break
        for row in page:
            sn = row.get("ship_number")
            if sn:
                out.add(_snapshot_key(sn, row.get("order_date")))
        if len(page) < _PAGE:
            break
        offset += _PAGE
    return out


def process_hq_upload(
    client,
    *,
    db_filename: str,
    rows: list[HQRow],
    filename: str,
    uploaded_by: str,
    note: str = "",
) -> SnapshotResult:
    """스냅샷 upsert + upload 이력 INSERT.

    - 이미 있는 ship_number 이고 order_amount/order_status 동일 → dup (last_seen_at 만 갱신)
    - 이미 있는데 값 변경 → revised (prev_* 저장 후 UPDATE)
    - 없던 것 → new (INSERT)
    - 기존 스냅샷에 있지만 이번 파일에 없는 것은 missing 으로 표시 (스냅샷 자체는 그대로 유지)
    """
    res = SnapshotResult()
    if not client:
        res.errors.append("Supabase client 없음")
        return res
    if not db_filename:
        res.errors.append("db_filename 없음")
        return res
    if not rows:
        # 빈 파일도 업로드 이력은 남긴다
        try:
            ur = client.table("app_hq_order_uploads").insert({
                "db_filename": db_filename,
                "filename": filename or "",
                "uploaded_by": uploaded_by or "",
                "row_count": 0,
                "new_count": 0, "dup_count": 0, "revised_count": 0, "missing_count": 0,
                "hq_amount_sum": 0,
                "note": note or None,
            }).execute()
            if ur.data:
                res.upload_id = ur.data[0].get("id")
        except Exception as e:
            res.errors.append(f"업로드 이력 저장 실패: {e}")
        return res

    ship_numbers = [r.ship_number for r in rows if r.ship_number]
    existing = _fetch_snapshots_by_ship(client, db_filename, ship_numbers)

    # 파일 내·기존 스냅샷 기준으로 미리 분류. 전부 dup 이면 업로드 이력도 새로 만들지 않는다.
    planned: list[tuple[str, HQRow, dict]] = []
    seen_keys: set[tuple[str, str]] = set()
    for r in rows:
        _key = _snapshot_key(r.ship_number, r.order_date)
        prev = existing.get(_key) or {}
        if _key in seen_keys:
            planned.append(("dup", r, prev))
            continue
        seen_keys.add(_key)
        if not prev:
            planned.append(("new", r, {}))
            continue
        prev_amount = int(prev.get("order_amount") or 0)
        prev_status = _clean_str(prev.get("order_status"))
        prev_total = int(prev.get("total_amount_hq") or 0)
        if (
            prev_amount == int(r.order_amount or 0)
            and prev_status == (r.order_status or "")
            and prev_total == int(r.total_amount_hq or 0)
        ):
            planned.append(("dup", r, prev))
        else:
            planned.append(("revised", r, prev))

    n_new = sum(1 for k, _, _ in planned if k == "new")
    n_rev = sum(1 for k, _, _ in planned if k == "revised")
    skip_upload_row = n_new == 0 and n_rev == 0
    if skip_upload_row:
        res.skipped_duplicate_upload = True
        for _kind, r, prev in planned:
            res.dup.append({
                **(prev or {}),
                "ship_number": r.ship_number,
                "order_date": r.order_date.isoformat() if r.order_date else None,
                "_row_status": "dup",
            })
        return res

    order_dates = [r.order_date for r in rows if r.order_date]
    hq_sum = sum(_hq_ship_cost(r) for r in rows)
    if not skip_upload_row:
        try:
            ur = client.table("app_hq_order_uploads").insert({
                "db_filename": db_filename,
                "filename": filename or "",
                "uploaded_by": uploaded_by or "",
                "row_count": len(rows),
                "hq_amount_sum": hq_sum,
                "order_date_min": (min(order_dates).isoformat() if order_dates else None),
                "order_date_max": (max(order_dates).isoformat() if order_dates else None),
                "note": note or None,
            }).execute()
            if ur.data:
                res.upload_id = int(ur.data[0]["id"])
        except Exception as e:
            res.errors.append(f"업로드 이력 저장 실패: {e}")
            return res

    now_iso = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    for kind, r, prev in planned:
        _key = _snapshot_key(r.ship_number, r.order_date)
        payload = {
            "db_filename": db_filename,
            "ship_number": r.ship_number,
            "contract_no": r.contract_no or None,
            "phone1_digits": r.phone1_digits or None,
            "customer_name": r.customer_name or None,
            "order_date": r.order_date.isoformat() if r.order_date else None,
            "contract_date": r.contract_date.isoformat() if r.contract_date else None,
            "delivery_date": r.delivery_date.isoformat() if r.delivery_date else None,
            "ship_date": r.ship_date.isoformat() if r.ship_date else None,
            "order_amount": int(r.order_amount or 0),
            "vat": int(r.vat or 0),
            "total_amount_hq": int(r.total_amount_hq or 0),
            "order_status": r.order_status or None,
            "order_kind": r.order_kind or None,
            "employee_names": r.employee_names or None,
            "outlet": bool(r.outlet),
            "is_display": bool(r.is_display),
            "source_upload_id": res.upload_id,
            "last_seen_at": now_iso,
        }
        if kind == "dup":
            res.dup.append({**(prev or {}), "ship_number": r.ship_number, "_row_status": "dup"})
            if prev.get("id") and res.upload_id:
                try:
                    client.table("app_hq_order_snapshots").update({
                        "last_seen_at": now_iso,
                        "source_upload_id": res.upload_id,
                    }).eq("id", prev["id"]).execute()
                except Exception as e:
                    res.errors.append(f"dup 갱신 실패 ({r.ship_number}): {e}")
            continue

        if kind == "new":
            payload["first_seen_at"] = now_iso
            payload["prev_order_amount"] = None
            payload["prev_order_status"] = None
            payload["prev_uploaded_at"] = None
            try:
                _ins = client.table("app_hq_order_snapshots").insert(payload).execute()
                _new_id = None
                if getattr(_ins, "data", None):
                    try:
                        _new_id = int(_ins.data[0].get("id"))
                    except (TypeError, ValueError, KeyError):
                        _new_id = None
                existing[_key] = {**payload, "id": _new_id, "order_date": payload.get("order_date")}
                res.new.append({**payload, "_row_status": "new"})
            except Exception as e:
                if _is_unique_violation(e):
                    res.dup.append({**payload, "_row_status": "dup"})
                else:
                    res.errors.append(f"신규 스냅샷 저장 실패 ({r.ship_number}): {e}")
            continue

        prev_amount = int(prev.get("order_amount") or 0)
        new_amount = int(r.order_amount or 0)
        payload["prev_order_amount"] = prev_amount
        payload["prev_order_status"] = _clean_str(prev.get("order_status")) or None
        payload["prev_uploaded_at"] = prev.get("last_seen_at")
        try:
            client.table("app_hq_order_snapshots").update(payload).eq("id", prev["id"]).execute()
            existing[_key] = {**payload, "id": prev.get("id")}
            res.revised.append({
                **payload,
                "_row_status": "revised",
                "_delta_amount": new_amount - prev_amount,
            })
        except Exception as e:
            res.errors.append(f"revised 갱신 실패 ({r.ship_number}): {e}")

    # missing: 이 매장 스냅샷에 있는데 이번 파일에 없음 (복합 키 기준)
    try:
        all_snap = _fetch_all_snapshot_keys(client, db_filename)
        seen_keys = {_snapshot_key(r.ship_number, r.order_date) for r in rows if r.ship_number}
        for sn, od in all_snap - seen_keys:
            res.missing.append({"ship_number": sn, "order_date": od, "_row_status": "missing"})
    except Exception as e:
        logger.info("missing 계산 스킵: %s", e)

    if res.upload_id:
        try:
            client.table("app_hq_order_uploads").update({
                "new_count": len(res.new),
                "dup_count": len(res.dup),
                "revised_count": len(res.revised),
                "missing_count": len(res.missing),
            }).eq("id", res.upload_id).execute()
        except Exception as e:
            logger.info("upload 요약 갱신 실패: %s", e)

    return res


# ---------------------------------------------------------------------------
# 매칭 + 원가 대사
# ---------------------------------------------------------------------------

def _seller_cost(order: dict) -> int:
    return int(_to_int(order.get("cost_price"))) + int(_to_int(order.get("display_cost_amount")))


def classify_cost_gap(seller_cost: int, hq_cost: int) -> tuple[str, str]:
    if hq_cost > 0 and seller_cost == 0:
        return "원가 미입력", "cost_blank"
    diff = seller_cost - hq_cost
    base = max(abs(hq_cost), abs(seller_cost), 1)
    if abs(diff) >= COST_ABS_THRESHOLD or (abs(diff) / base) >= COST_PCT_THRESHOLD:
        return "원가 불일치", "cost_mismatch"
    return "원가 일치", "ok"


@dataclass
class ReconcileRow:
    hq_ships: list[str] = field(default_factory=list)
    identity_key: str = ""
    phone1_digits: str = ""
    customer_name: str = ""
    order_date: Optional[date] = None
    employee_names: str = ""
    hq_cost: int = 0
    hq_total: int = 0
    hq_status: str = ""
    prev_hq_cost: Optional[int] = None
    order_id: Optional[int] = None
    seller_cost: Optional[int] = None
    entered_sale: Optional[int] = None
    result_label: str = ""
    result_code: str = ""
    reason: str = ""


@dataclass
class ReconcileReport:
    rows: list[ReconcileRow] = field(default_factory=list)
    upload_id: Optional[int] = None
    counts: dict[str, int] = field(default_factory=dict)


def _phone_variants(digits: str) -> list[str]:
    if not digits:
        return []
    if len(digits) == 11 and digits.startswith("010"):
        return [digits, f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"]
    return [digits]


def _store_name_for_db(client, db_filename: str) -> str:
    if not client or not db_filename:
        return ""
    try:
        r = (
            client.table("app_stores")
            .select("store_name")
            .eq("db_filename", db_filename)
            .maybe_single()
            .execute()
        )
        if r.data:
            return _clean_str(r.data.get("store_name"))
    except Exception as e:
        logger.info("store_name lookup failed: %s", e)
    return ""


def _load_match_map(client, db_filename: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    if not client or not db_filename:
        return out
    try:
        r = (
            client.table("app_hq_reconcile_matches")
            .select("*")
            .eq("db_filename", db_filename)
            .execute()
        )
    except Exception as e:
        logger.info("load match map skipped: %s", e)
        return out
    for row in (r.data or []):
        ident = _clean_str(row.get("identity_key"))
        if ident:
            out[ident] = row
    return out


def _fetch_orders_by_identity(
    client, db_filename: str, hq_rows: list[HQRow],
) -> dict[str, list[dict]]:
    """전화 identity 마다 해당 매장 앱 주문을 모두 모은다. 날짜 창으로 미리 자르지 않는다."""
    out: dict[str, list[dict]] = {}
    if not client or not db_filename or not hq_rows:
        return out

    phones = sorted({
        p for r in hq_rows for p in (r.phone1_digits, r.phone2_digits) if p
    })
    if not phones:
        return out

    variants: list[str] = []
    for p in phones:
        variants.extend(_phone_variants(p))
    variants = sorted(set(variants))
    store_name = _store_name_for_db(client, db_filename)

    candidate_customer_ids: set[int] = set()
    cust_phones: dict[int, set[str]] = {}
    _CHUNK = 200
    for i in range(0, len(variants), _CHUNK):
        batch = variants[i:i + _CHUNK]
        for col in ("phone1", "phone2"):
            try:
                q = client.table("app_customers").select("id, phone1, phone2").in_(col, batch)
                if store_name:
                    q = q.eq("store_name", store_name)
                resp = q.execute()
            except Exception as e:
                logger.info("customers by %s fetch failed: %s", col, e)
                continue
            for row in (resp.data or []):
                try:
                    cid = int(row["id"])
                except (TypeError, ValueError, KeyError):
                    continue
                candidate_customer_ids.add(cid)
                cust_phones.setdefault(cid, set())
                d1 = _phone_digits(row.get("phone1"))
                d2 = _phone_digits(row.get("phone2"))
                if d1:
                    cust_phones[cid].add(d1)
                if d2:
                    cust_phones[cid].add(d2)

    if not candidate_customer_ids:
        return out

    orders_all: list[dict] = []
    ids_list = sorted(candidate_customer_ids)
    for i in range(0, len(ids_list), _CHUNK):
        batch = ids_list[i:i + _CHUNK]
        try:
            r = (
                client.table("app_orders")
                .select(
                    "id, customer_id, db_filename, order_date, delivery_date, total_amount, "
                    "cost_price, display_cost_amount, employee_names, import_source, balance_status"
                )
                .in_("customer_id", batch)
                .eq("db_filename", db_filename)
                .execute()
            )
            orders_all.extend(r.data or [])
        except Exception as e:
            logger.info("orders by customer fetch failed: %s", e)

    for hr in hq_rows:
        hq_phones = {p for p in (hr.phone1_digits, hr.phone2_digits) if p}
        if not hq_phones or not hr.identity_key:
            continue
        cands: list[dict] = []
        for o in orders_all:
            cid = o.get("customer_id")
            if cid is None:
                continue
            if cust_phones.get(int(cid), set()) & hq_phones:
                cands.append(o)
        if cands:
            out.setdefault(hr.identity_key, []).extend(cands)

    for k, arr in list(out.items()):
        seen: set[int] = set()
        uniq = []
        for o in arr:
            oid = o.get("id")
            if oid is None or int(oid) in seen:
                continue
            seen.add(int(oid))
            uniq.append(o)
        out[k] = uniq
    return out


def _window_cands(
    orders: list[dict],
    targets: list[date],
    window: int = MATCH_WINDOW_DAYS,
) -> list[tuple[int, dict]]:
    if not orders or not targets:
        return []
    out: list[tuple[int, dict]] = []
    for o in orders:
        od = _parse_date(o.get("order_date"))
        if od is None:
            continue
        gap = min(abs((od - t).days) for t in targets)
        if gap <= window:
            out.append((gap, o))
    out.sort(key=lambda x: (x[0], int(x[1].get("id") or 0)))
    return out


def _apply_order_to_row(row: ReconcileRow, o: dict, hq_cost: int, reason: str) -> None:
    row.order_id = int(o.get("id")) if o.get("id") is not None else None
    row.seller_cost = _seller_cost(o)
    row.entered_sale = _to_int(o.get("total_amount"))
    label, code = classify_cost_gap(row.seller_cost, hq_cost)
    row.result_label = label
    row.result_code = code
    row.reason = reason


def _restore_prior_match(row: ReconcileRow, prev: dict, hq_cost: int) -> bool:
    raw_oid = prev.get("order_id")
    if raw_oid is None or str(raw_oid).strip() == "":
        return False
    try:
        row.order_id = int(raw_oid)
    except (TypeError, ValueError):
        return False
    if prev.get("seller_cost") is not None:
        try:
            row.seller_cost = int(prev.get("seller_cost"))
        except (TypeError, ValueError):
            row.seller_cost = None
    if prev.get("entered_sale") is not None:
        try:
            row.entered_sale = int(prev.get("entered_sale"))
        except (TypeError, ValueError):
            row.entered_sale = None
    if row.seller_cost is not None:
        label, code = classify_cost_gap(row.seller_cost, hq_cost)
        row.result_label = label
        row.result_code = code
    else:
        row.result_label = _clean_str(prev.get("result_label")) or "이전 매칭 유지"
        row.result_code = _clean_str(prev.get("result_code")) or "ok"
    row.reason = "이전 매칭 유지"
    return True


def build_hq_reconcile(client, db_filename: str, hq_rows: list[HQRow]) -> ReconcileReport:
    """본사 rows + 앱 주문으로 대사 리포트 생성.

    Grouping: identity_key(전화 우선) 1건. 같은 전화의 출고는 본사원가를 합산한다.
    Matching: 앱 주문이 1건이면 날짜와 무관하게 자동 매칭.
    2건 이상이면 등록일 ±MATCH_WINDOW_DAYS 로 좁히고, 그래도 애매하면 unresolved.
    """
    report = ReconcileReport()
    if not hq_rows:
        return report

    orders_by_ident = _fetch_orders_by_identity(client, db_filename, hq_rows)
    prior = _load_match_map(client, db_filename)

    grouped: dict[str, list[HQRow]] = {}
    for r in hq_rows:
        if r.is_display:
            continue
        ident = r.identity_key or _identity_key(r.phone1_digits, r.customer_name)
        if not ident:
            continue
        grouped.setdefault(ident, []).append(r)

    counts: dict[str, int] = {}

    def _tick(code: str) -> None:
        counts[code] = counts.get(code, 0) + 1

    for ident, group in grouped.items():
        hq_cost = sum(_hq_ship_cost(x) for x in group)
        hq_total = sum(int(x.order_amount or 0) for x in group)
        first = group[0]
        hq_dates = [x.order_date for x in group if x.order_date]
        target_date = min(hq_dates) if hq_dates else first.order_date
        all_cands = orders_by_ident.get(ident, [])

        row = ReconcileRow(
            hq_ships=[x.ship_number for x in group],
            identity_key=ident,
            phone1_digits=first.phone1_digits,
            customer_name=first.customer_name,
            order_date=target_date,
            employee_names=first.employee_names,
            hq_cost=hq_cost,
            hq_total=hq_total,
            hq_status=first.order_status,
        )

        if len(all_cands) == 1:
            o = all_cands[0]
            od = _parse_date(o.get("order_date"))
            gap = abs((od - target_date).days) if od and target_date else None
            reason = "자동 매칭 (전화 1건)"
            if gap is not None:
                reason = f"자동 매칭 (전화 1건, Δ{gap}일)"
            _apply_order_to_row(row, o, hq_cost, reason)
            _tick(row.result_code)
        elif len(all_cands) > 1:
            windowed = _window_cands(all_cands, hq_dates, MATCH_WINDOW_DAYS)
            if len(windowed) == 1:
                gap, o = windowed[0]
                _apply_order_to_row(row, o, hq_cost, f"자동 매칭 (전화 {len(all_cands)}건 중 Δ{gap}일)")
                _tick(row.result_code)
            else:
                pick = windowed[0] if windowed else (None, all_cands[0])
                gap, o = pick
                row.order_id = int(o.get("id")) if o.get("id") is not None else None
                row.seller_cost = _seller_cost(o)
                row.entered_sale = _to_int(o.get("total_amount"))
                n = len(windowed) if windowed else len(all_cands)
                row.result_label = f"수동 선택 필요 ({n}건)"
                row.result_code = "unresolved"
                row.reason = f"후보 {n}건, 추천 #{row.order_id}"
                _tick("unresolved")
        elif prior.get(ident) and _restore_prior_match(row, prior[ident], hq_cost):
            _tick(row.result_code)
        else:
            row.result_label = "본사만 있음"
            row.result_code = "hq_only"
            row.reason = "앱 주문 없음 (동일 전화)"
            _tick("hq_only")

        report.rows.append(row)

    report.counts = counts
    return report


# ---------------------------------------------------------------------------
# DataFrame / Excel
# ---------------------------------------------------------------------------

def reconcile_to_dataframe(report: ReconcileReport) -> pd.DataFrame:
    if not report.rows:
        return pd.DataFrame(columns=[
            "주문ID", "고객명", "전화", "등록일", "담당",
            "본사원가", "주문금액", "입력원가", "원가차이", "입력판매가",
            "상태", "결과", "사유", "출고번호",
        ])
    out = []
    for r in report.rows:
        seller = r.seller_cost if r.seller_cost is not None else None
        diff = None if seller is None else (int(seller) - int(r.hq_cost))
        out.append({
            "주문ID": r.order_id if r.order_id is not None else "",
            "고객명": r.customer_name,
            "전화": r.phone1_digits,
            "등록일": r.order_date.isoformat() if r.order_date else "",
            "담당": r.employee_names,
            "본사원가": int(r.hq_cost),
            "주문금액": int(r.hq_total),
            "입력원가": seller,
            "원가차이": diff,
            "입력판매가": r.entered_sale,
            "상태": r.hq_status,
            "결과": r.result_label,
            "사유": r.reason,
            "출고번호": ", ".join(r.hq_ships),
        })
    return pd.DataFrame(out)


def build_review_excel(report: ReconcileReport) -> bytes:
    """전체 + 코드별 시트 (hq_only / cost_mismatch / cost_blank / unresolved)."""
    df_all = reconcile_to_dataframe(report)
    if df_all.empty:
        df_all = pd.DataFrame(columns=["결과"])
    codes = [r.result_code for r in report.rows]
    df_all = df_all.assign(_code=codes)
    bio = io.BytesIO()
    with pd.ExcelWriter(bio, engine="openpyxl") as w:
        df_all.drop(columns=["_code"], errors="ignore").to_excel(w, sheet_name="전체", index=False)
        for code, sheet in (
            ("hq_only", "본사만"),
            ("cost_mismatch", "원가불일치"),
            ("cost_blank", "원가미입력"),
            ("unresolved", "수동선택"),
        ):
            sub = df_all[df_all["_code"] == code].drop(columns=["_code"], errors="ignore")
            if sub.empty:
                continue
            sub.to_excel(w, sheet_name=sheet, index=False)
    bio.seek(0)
    return bio.getvalue()


# ---------------------------------------------------------------------------
# 대사 결과 persist (UNIQUE db_filename + identity_key → upsert, 행 중복 없음)
# ---------------------------------------------------------------------------

def save_reconcile_matches(
    client,
    db_filename: str,
    report: ReconcileReport,
    upload_id: Optional[int] = None,
) -> list[str]:
    """전화 단위 대사 결과를 upsert. 같은 identity 재업로드 시 행이 늘지 않는다."""
    errors: list[str] = []
    if not client or not db_filename or not report.rows:
        return errors
    now_iso = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    payloads: list[dict] = []
    for r in report.rows:
        if not r.identity_key:
            continue
        payloads.append({
            "db_filename": db_filename,
            "identity_key": r.identity_key,
            "phone1_digits": r.phone1_digits or None,
            "customer_name": r.customer_name or None,
            "order_id": r.order_id,
            "hq_ships": ", ".join(r.hq_ships) if r.hq_ships else None,
            "order_date": r.order_date.isoformat() if r.order_date else None,
            "hq_cost": int(r.hq_cost or 0),
            "seller_cost": r.seller_cost,
            "entered_sale": r.entered_sale,
            "result_code": r.result_code or None,
            "result_label": r.result_label or None,
            "reason": r.reason or None,
            "source_upload_id": upload_id,
            "updated_at": now_iso,
        })
    _CHUNK = 200
    for i in range(0, len(payloads), _CHUNK):
        batch = payloads[i:i + _CHUNK]
        try:
            client.table("app_hq_reconcile_matches").upsert(
                batch, on_conflict="db_filename,identity_key",
            ).execute()
        except Exception as e:
            if _is_unique_violation(e):
                # 레이스 시 단건 재시도
                for one in batch:
                    try:
                        client.table("app_hq_reconcile_matches").upsert(
                            one, on_conflict="db_filename,identity_key",
                        ).execute()
                    except Exception as e2:
                        errors.append(f"대사 저장 실패 ({one.get('identity_key')}): {e2}")
            else:
                errors.append(f"대사 결과 저장 실패: {e}")
    return errors


def load_reconcile_matches(client, db_filename: str) -> pd.DataFrame:
    if not client or not db_filename:
        return pd.DataFrame()
    try:
        r = (
            client.table("app_hq_reconcile_matches")
            .select("*")
            .eq("db_filename", db_filename)
            .order("updated_at", desc=True)
            .execute()
        )
        rows = r.data or []
    except Exception as e:
        logger.info("load_reconcile_matches failed: %s", e)
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    out = []
    for row in rows:
        seller = row.get("seller_cost")
        hq_cost = int(row.get("hq_cost") or 0)
        diff = None if seller is None else (int(seller) - hq_cost)
        out.append({
            "주문ID": row.get("order_id") if row.get("order_id") is not None else "",
            "고객명": row.get("customer_name") or "",
            "전화": row.get("phone1_digits") or "",
            "등록일": str(row.get("order_date") or "")[:10],
            "본사원가": hq_cost,
            "입력원가": int(seller) if seller is not None else None,
            "원가차이": diff,
            "입력판매가": row.get("entered_sale"),
            "결과": row.get("result_label") or "",
            "사유": row.get("reason") or "",
            "출고번호": row.get("hq_ships") or "",
        })
    return pd.DataFrame(out)


# ---------------------------------------------------------------------------
# 업로드 이력 조회
# ---------------------------------------------------------------------------

def load_upload_history(client, db_filename: str, limit: int = 30) -> pd.DataFrame:
    if not client or not db_filename:
        return pd.DataFrame()
    try:
        r = (
            client.table("app_hq_order_uploads")
            .select("*")
            .eq("db_filename", db_filename)
            .order("uploaded_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = r.data or []
    except Exception as e:
        logger.info("load_upload_history failed: %s", e)
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    keep = [c for c in (
        "id", "uploaded_at", "filename", "uploaded_by",
        "row_count", "new_count", "dup_count", "revised_count", "missing_count",
        "hq_amount_sum", "order_date_min", "order_date_max",
    ) if c in df.columns]
    return df[keep]


__all__ = [
    "MATCH_WINDOW_DAYS",
    "COST_ABS_THRESHOLD",
    "COST_PCT_THRESHOLD",
    "HQRow",
    "SnapshotResult",
    "ReconcileRow",
    "ReconcileReport",
    "parse_hq_order_export",
    "process_hq_upload",
    "build_hq_reconcile",
    "reconcile_to_dataframe",
    "build_review_excel",
    "save_reconcile_matches",
    "load_reconcile_matches",
    "load_upload_history",
]
