# -*- coding: utf-8 -*-
"""본사 ERP "주문조회(대)_emsf" 엑셀 대사 서비스.

역할:
    - 엑셀/CSV 파싱 (제목·TOTAL 스킵, 회수/취소 제외)
    - 업로드 이력 + 출고번호 단위 스냅샷 upsert (중복 스킵, 감/증액 delta 감지)
    - 앱 주문(app_orders)과 같은 전화면 출고·주문을 한 건으로 합산 매칭
      (전화 없을 때만 이름 + 등록일 ±2일)
    - 원가 대사 (입력원가 vs 본사원가) + 계약 변경 gap 판정
    - 미리보기·다운로드용 DataFrame / 엑셀 빌더

주의:
    - 이 모듈은 `app_orders` / `app_payments` / `sales` 를 **절대 변경하지 않는다**.
    - write 는 `app_hq_order_uploads` INSERT 와 `app_hq_order_snapshots` upsert 뿐.
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
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
    if v is None:
        return ""
    try:
        if isinstance(v, float) and pd.isna(v):
            return ""
    except Exception:
        pass
    # 엑셀 숫자/과학적 표기 (1.094667915E10, 1094667915.0)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        try:
            n = int(round(float(v)))
            return str(n) if n > 0 else ""
        except (TypeError, ValueError, OverflowError):
            pass
    s = _clean_str(v)
    if not s:
        return ""
    if re.fullmatch(r"[-+]?\d+(?:\.\d+)?[eE][-+]?\d+", s):
        try:
            n = int(round(float(s)))
            return str(n) if n > 0 else ""
        except (TypeError, ValueError, OverflowError):
            pass
    return re.sub(r"\D", "", s)


def _normalize_name_key(v: Any) -> str:
    s = _clean_str(v)
    if not s:
        return ""
    # 대괄호 태그 / 별표 접미 제거
    s = re.sub(r"\[[^\[\]]*\]", "", s).replace("*", "").strip()
    return re.sub(r"\s+", "", s).lower()


def _phone_match_key(digits: str) -> str:
    """전화 비교 키. 010-xxxx-xxxx / 10자리 변형을 같은 값으로 본다."""
    d = re.sub(r"\D", "", digits or "")
    if len(d) >= 10:
        return d[-10:]
    return d


def _identity_key(phone_digits: str, customer_name: Any) -> str:
    pk = _phone_match_key(phone_digits)
    if pk:
        return pk
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
        ident = _identity_key(phone1 or phone2, cust)
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

    def summary(self) -> dict[str, int]:
        return {
            "new": len(self.new),
            "dup": len(self.dup),
            "revised": len(self.revised),
            "missing": len(self.missing),
        }


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

    # 업로드 이력 먼저 INSERT (요약값은 나중에 update)
    order_dates = [r.order_date for r in rows if r.order_date]
    hq_sum = sum(_hq_ship_cost(r) for r in rows)
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

    ship_numbers = [r.ship_number for r in rows if r.ship_number]
    existing = _fetch_snapshots_by_ship(client, db_filename, ship_numbers)
    now_iso = datetime.utcnow().isoformat(timespec="seconds") + "Z"

    for r in rows:
        _key = _snapshot_key(r.ship_number, r.order_date)
        prev = existing.get(_key) or {}
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
        if not prev:
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
                # 같은 파일 내에서 동일 키가 다시 등장할 때 재INSERT 하지 않도록 즉시 등록
                existing[_key] = {**payload, "id": _new_id, "order_date": payload.get("order_date")}
                res.new.append({**payload, "_row_status": "new"})
            except Exception as e:
                res.errors.append(f"신규 스냅샷 저장 실패 ({r.ship_number}): {e}")
            continue

        prev_amount = int(prev.get("order_amount") or 0)
        prev_status = _clean_str(prev.get("order_status"))
        new_amount = int(r.order_amount or 0)
        new_status = r.order_status or ""
        if prev_amount == new_amount and prev_status == new_status:
            # 값 동일 → dup, last_seen_at 만 갱신
            try:
                client.table("app_hq_order_snapshots").update({
                    "last_seen_at": now_iso,
                    "source_upload_id": res.upload_id,
                }).eq("id", prev["id"]).execute()
                existing[_key] = {**prev, "last_seen_at": now_iso, "source_upload_id": res.upload_id}
            except Exception as e:
                res.errors.append(f"dup 갱신 실패 ({r.ship_number}): {e}")
            res.dup.append({**prev, "_row_status": "dup"})
            continue

        # revised
        payload["prev_order_amount"] = prev_amount
        payload["prev_order_status"] = prev_status or None
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

    # 업로드 요약 update
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
    """본사 대사용 입력원가. cost_price 만 사용. display_cost_amount(전시원가)는 제외.

    본사 ERP 출고에는 전시품 원가가 없어, 앱 전시원가를 더하면 대사가 어긋난다.
    전시원가는 `_display_cost` 로 별도 열에 표시한다.
    """
    return int(_to_int(order.get("cost_price")))


def _display_cost(order: dict) -> int:
    """앱 전시품 원가 (`display_cost_amount`). 본사 대사에서는 참고 열."""
    return int(_to_int(order.get("display_cost_amount")))


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
    order_date_end: Optional[date] = None
    employee_names: str = ""
    hq_cost: int = 0
    hq_total: int = 0
    hq_status: str = ""
    prev_hq_cost: Optional[int] = None
    order_id: Optional[int] = None
    order_ids: list[int] = field(default_factory=list)
    seller_cost: Optional[int] = None  # 일반원가 (cost_price) 합
    display_cost: Optional[int] = None  # 전시원가 (display_cost_amount) 합
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
    """DB exact-match 용 전화 표기 변형."""
    d = re.sub(r"\D", "", digits or "")
    if not d:
        return []
    out: set[str] = {d}
    last10 = d[-10:] if len(d) >= 10 else d
    if last10:
        out.add(last10)
    if len(last10) == 10 and last10.startswith("10"):
        full11 = "0" + last10
        mid, tail = last10[2:6], last10[6:]
        out.update({
            full11,
            last10,
            f"010-{mid}-{tail}",
            f"010 {mid} {tail}",
            f"010.{mid}.{tail}",
            f"+82{last10}",
            f"82{last10}",
        })
    if len(d) == 11 and d.startswith("010"):
        out.add(f"{d[:3]}-{d[3:7]}-{d[7:]}")
        out.add(d[1:])
    return [x for x in out if x]


def _store_name_for_db(client, db_filename: str) -> str:
    if not client or not db_filename:
        return ""
    try:
        r = (
            client.table("app_stores")
            .select("store_name")
            .eq("db_filename", db_filename)
            .limit(1)
            .execute()
        )
        rows = r.data or []
        if rows:
            return _clean_str(rows[0].get("store_name"))
    except Exception as e:
        logger.info("store_name lookup failed: %s", e)
    return ""


def _iter_store_customers(client, store_name: str) -> Iterable[dict]:
    """매장 고객을 페이지 단위로 조회한다."""
    if not client or not store_name:
        return
    page = 1000
    offset = 0
    while True:
        try:
            r = (
                client.table("app_customers")
                .select("id, phone1, phone2")
                .eq("store_name", store_name)
                .range(offset, offset + page - 1)
                .execute()
            )
        except Exception as e:
            logger.info("store customers fetch failed: %s", e)
            return
        rows = r.data or []
        for row in rows:
            yield row
        if len(rows) < page:
            return
        offset += page


def _customer_phone_keys(row: dict) -> set[str]:
    keys: set[str] = set()
    for col in ("phone1", "phone2"):
        k = _phone_match_key(_phone_digits(row.get(col)))
        if k:
            keys.add(k)
    return keys


def _fetch_orders_by_identity(
    client, db_filename: str, hq_rows: list[HQRow], window_days: int = MATCH_WINDOW_DAYS,
) -> dict[str, list[dict]]:
    """전화 identity 마다 앱 주문 후보를 모은다. 파일 기간 ±30일."""
    out: dict[str, list[dict]] = {}
    if not client or not db_filename or not hq_rows:
        return out

    dates = [r.order_date for r in hq_rows if r.order_date]
    if not dates:
        return out
    lookback = max(int(window_days or 0), 30)
    lo = min(dates) - timedelta(days=lookback)
    hi = max(dates) + timedelta(days=lookback)

    wanted_keys: set[str] = set()
    raw_phones: list[str] = []
    for r in hq_rows:
        for p in (r.phone1_digits, r.phone2_digits):
            k = _phone_match_key(p)
            if k:
                wanted_keys.add(k)
                raw_phones.append(p)

    candidate_customer_ids: set[int] = set()
    cust_phone_keys: dict[int, set[str]] = {}

    def _absorb_customer(row: dict) -> None:
        try:
            cid = int(row["id"])
        except (TypeError, ValueError, KeyError):
            return
        keys = _customer_phone_keys(row)
        if not keys:
            return
        if keys & wanted_keys:
            candidate_customer_ids.add(cid)
            cust_phone_keys.setdefault(cid, set()).update(keys)

    if wanted_keys:
        variants: list[str] = []
        seen_v: set[str] = set()
        for p in raw_phones:
            for v in _phone_variants(p):
                if v not in seen_v:
                    seen_v.add(v)
                    variants.append(v)
        _CHUNK = 200
        for i in range(0, len(variants), _CHUNK):
            batch = variants[i:i + _CHUNK]
            for col in ("phone1", "phone2"):
                try:
                    r = (
                        client.table("app_customers")
                        .select("id, phone1, phone2")
                        .in_(col, batch)
                        .execute()
                    )
                except Exception as e:
                    logger.info("customers by %s fetch failed: %s", col, e)
                    continue
                for row in (r.data or []):
                    _absorb_customer(row)

        store_name = _store_name_for_db(client, db_filename)
        if store_name:
            for row in _iter_store_customers(client, store_name):
                _absorb_customer(row)

    if not candidate_customer_ids:
        return out

    orders_all: list[dict] = []
    ids_list = sorted(candidate_customer_ids)
    _CHUNK2 = 200
    for i in range(0, len(ids_list), _CHUNK2):
        batch = ids_list[i:i + _CHUNK2]
        try:
            r = (
                client.table("app_orders")
                .select(
                    "id, customer_id, db_filename, order_date, delivery_date, total_amount, "
                    "cost_price, display_cost_amount, employee_names, import_source, balance_status"
                )
                .in_("customer_id", batch)
                .eq("db_filename", db_filename)
                .gte("order_date", lo.isoformat())
                .lte("order_date", hi.isoformat())
                .execute()
            )
            orders_all.extend(r.data or [])
        except Exception as e:
            logger.info("orders by customer fetch failed: %s", e)

    missing_phone = [cid for cid in ids_list if cid not in cust_phone_keys]
    for i in range(0, len(missing_phone), 200):
        batch = missing_phone[i:i + 200]
        try:
            r = client.table("app_customers").select("id, phone1, phone2").in_("id", batch).execute()
            for row in r.data or []:
                _absorb_customer(row)
        except Exception as e:
            logger.info("customer phones fetch failed: %s", e)

    for o in orders_all:
        cid = o.get("customer_id")
        if cid is None:
            continue
        try:
            icid = int(cid)
        except (TypeError, ValueError):
            continue
        for k in cust_phone_keys.get(icid, set()):
            if k in wanted_keys:
                out.setdefault(k, []).append(o)

    for k, arr in list(out.items()):
        seen: set[int] = set()
        uniq = []
        for o in arr:
            oid = o.get("id")
            if oid is None:
                continue
            try:
                ioid = int(oid)
            except (TypeError, ValueError):
                continue
            if ioid in seen:
                continue
            seen.add(ioid)
            uniq.append(o)
        out[k] = uniq
    return out


def _window_cands(orders: list[dict], target: Optional[date], window: int = MATCH_WINDOW_DAYS) -> list[tuple[int, dict]]:
    if not orders or target is None:
        return []
    out: list[tuple[int, dict]] = []
    for o in orders:
        od = _parse_date(o.get("order_date"))
        if od is None:
            continue
        gap = abs((od - target).days)
        if gap <= window:
            out.append((gap, o))
    out.sort(key=lambda x: (x[0], int(x[1].get("id") or 0)))
    return out


def build_hq_reconcile(client, db_filename: str, hq_rows: list[HQRow]) -> ReconcileReport:
    """본사 rows + 앱 주문으로 대사 리포트 생성.

    - 전화가 있으면 전화번호 기준으로 본사 출고를 한 건으로 합산하고,
      같은 전화의 앱 주문 원가·판매가도 합산해 비교한다. (등록일 ±2일 제한 없음)
    - 전화가 없으면 (이름, 등록일) + ±2일 창으로 기존과 같이 매칭.
    """
    report = ReconcileReport()
    if not hq_rows:
        return report

    orders_by_ident = _fetch_orders_by_identity(client, db_filename, hq_rows)

    grouped: dict[tuple, list[HQRow]] = {}
    for r in hq_rows:
        if r.is_display:
            continue
        pk = _phone_match_key(r.phone1_digits or r.phone2_digits)
        if pk:
            key: tuple = ("P", pk)
        else:
            key = ("N", r.identity_key, r.order_date.isoformat() if r.order_date else "")
        grouped.setdefault(key, []).append(r)

    counts: dict[str, int] = {}

    def _tick(code: str) -> None:
        counts[code] = counts.get(code, 0) + 1

    for key, group in grouped.items():
        dates = sorted({x.order_date for x in group if x.order_date})
        target_date = dates[0] if dates else None
        hq_cost = sum(_hq_ship_cost(x) for x in group)
        hq_total = sum(int(x.order_amount or 0) for x in group)
        first = group[0]
        is_phone = key[0] == "P"
        lookup = key[1] if is_phone else first.identity_key
        raw_cands = orders_by_ident.get(lookup, [])

        if is_phone:
            matched = list(raw_cands)
        else:
            matched = [o for _, o in _window_cands(raw_cands, target_date, MATCH_WINDOW_DAYS)]
        _seen_m: set[int] = set()
        _uniq_m: list[dict] = []
        for o in matched:
            try:
                _mid = int(o.get("id"))
            except (TypeError, ValueError):
                continue
            if _mid in _seen_m:
                continue
            _seen_m.add(_mid)
            _uniq_m.append(o)
        matched = _uniq_m

        row = ReconcileRow(
            hq_ships=[x.ship_number for x in group],
            identity_key=first.identity_key,
            phone1_digits=first.phone1_digits or first.phone2_digits,
            customer_name=first.customer_name,
            order_date=target_date,
            order_date_end=dates[-1] if dates else None,
            employee_names=first.employee_names,
            hq_cost=hq_cost,
            hq_total=hq_total,
            hq_status=first.order_status,
        )

        ship_note = ""
        if is_phone and len(group) > 1:
            d1 = dates[0].isoformat() if dates else ""
            d2 = dates[-1].isoformat() if dates else ""
            span = d1 if d1 == d2 else f"{d1}~{d2}"
            ship_note = f"전화 동일 출고 {len(group)}건 합산 ({span})"

        if not matched:
            row.result_label = "본사만 있음"
            row.result_code = "hq_only"
            row.reason = ship_note or (
                "앱 주문 없음 (동일 전화)" if is_phone
                else f"앱 주문 없음 (등록일 ±{MATCH_WINDOW_DAYS}일)"
            )
            _tick("hq_only")
        else:
            oids: list[int] = []
            for o in matched:
                if o.get("id") is None:
                    continue
                oids.append(int(o["id"]))
            oids = sorted(set(oids))
            row.order_ids = oids
            row.order_id = oids[0] if oids else None
            row.seller_cost = sum(_seller_cost(o) for o in matched)
            row.display_cost = sum(_display_cost(o) for o in matched)
            row.entered_sale = sum(_to_int(o.get("total_amount")) for o in matched)
            label, code = classify_cost_gap(int(row.seller_cost or 0), hq_cost)
            row.result_label = label
            row.result_code = code
            bits = []
            if ship_note:
                bits.append(ship_note)
            if len(oids) == 1:
                bits.append(f"자동 매칭 #{oids[0]}")
            else:
                bits.append("앱주문 " + ",".join(f"#{i}" for i in oids) + " 합산")
            row.reason = " · ".join(bits)
            _tick(code)

        report.rows.append(row)

    report.counts = counts
    return report


# ---------------------------------------------------------------------------
# DataFrame / Excel
# ---------------------------------------------------------------------------

def reconcile_to_dataframe(report: ReconcileReport) -> pd.DataFrame:
    _cols = [
        "주문ID", "고객명", "전화", "등록일", "담당",
        "본사원가", "주문금액",
        "일반원가", "일반원가차이",
        "전시원가", "전시원가차이",
        "입력판매가",
        "상태", "결과", "사유", "출고번호",
    ]
    if not report.rows:
        return pd.DataFrame(columns=_cols)
    out = []
    for r in report.rows:
        seller = r.seller_cost if r.seller_cost is not None else None
        disp = r.display_cost if r.display_cost is not None else None
        # 일반원가차이: 일반원가가 0이면 (원가 미입력) 차이를 계산하지 않음
        gen_diff = None if (seller is None or int(seller) == 0) else (int(seller) - int(r.hq_cost))
        # 전시원가차이: 전시원가가 0이면 계산하지 않음 (전시품이 없는 주문)
        disp_diff = None if (disp is None or int(disp) == 0) else (int(disp) - int(r.hq_cost))
        out.append({
            "주문ID": (
                ",".join(str(i) for i in r.order_ids) if r.order_ids
                else (r.order_id if r.order_id is not None else "")
            ),
            "고객명": r.customer_name,
            "전화": r.phone1_digits,
            "등록일": (
                f"{r.order_date.isoformat()}~{r.order_date_end.isoformat()}"
                if r.order_date and r.order_date_end and r.order_date_end != r.order_date
                else (r.order_date.isoformat() if r.order_date else "")
            ),
            "담당": r.employee_names,
            "본사원가": int(r.hq_cost),
            "주문금액": int(r.hq_total),
            "일반원가": seller,
            "일반원가차이": gen_diff,
            "전시원가": disp,
            "전시원가차이": disp_diff,
            "입력판매가": r.entered_sale,
            "상태": r.hq_status,
            "결과": r.result_label,
            "사유": r.reason,
            "출고번호": ", ".join(r.hq_ships),
        })
    return pd.DataFrame(out, columns=_cols)


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
    "load_upload_history",
]
