"""과거 엑셀 구매내역 일괄 임포트 서비스.

용도: 2016~2026 년 매장별 엑셀 원본(출고가 기준) 을 app_customers/app_orders/app_payments
에 통합 저장한다. 전화번호(phone1)로 재구매 고객을 통합하고, 출고가를 판매가 기준
20% 마진으로 역산하여 total_amount 로 저장한다.

설계 원칙:
  - Streamlit UI 는 app.py 에 두되, 순수 데이터 로직은 이 모듈로 분리.
  - 임포트 전 반드시 dry-run(build_preview) 으로 결과를 확인 후 commit_import 호출.
  - 재실행 안전: (customer_id, order_date, cost_price) 3튜플이 이미 존재하면 스킵.
  - import_source 컬럼(신규 SUPABASE_APP_ORDERS_IMPORT_SOURCE.sql) 태그로 롤백/식별 가능.
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

LEGACY_IMPORT_SOURCE = "엑셀_과거이력"
"""app_orders.import_source / app_customers.source 태그 값."""

DEFAULT_MARGIN_RATE = 0.20
"""판매가 기준 마진율 기본값. 판매가 = 출고가 / (1 - MARGIN_RATE)."""

# 목표 필드 정의: (필드키, 한글라벨, 필수여부)
TARGET_FIELDS: list[tuple[str, str, bool]] = [
    ("order_date",     "계약일 (주문일)",   True),
    ("delivery_date",  "배송일",           False),
    ("customer_name",  "고객명",           True),
    ("phone1",         "전화번호1",         True),
    ("phone2",         "전화번호2",         False),
    ("address",        "주소",             False),
    ("address2",       "주소2 (보조/지번)", False),
    ("employee_names", "판매자 (담당자)",   False),
    ("cost_price",     "주문금액 (출고가)", True),
    ("vat",            "부가세",           False),
    ("order_kind",     "주문구분",         False),
]

# 임포트 시 order_kind 값이 이 집합에 없으면 제외 (기본).
DEFAULT_ORDER_KIND_ALLOWED = {"주문", "판매", "정상", ""}


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def normalize_phone(raw: Any) -> str:
    """전화번호 문자열에서 숫자만 추출 (dedup 키)."""
    if raw is None:
        return ""
    try:
        if isinstance(raw, float) and pd.isna(raw):
            return ""
    except Exception:
        pass
    return re.sub(r"\D", "", str(raw))


def compute_sale_price(cost: float, margin_rate: float = DEFAULT_MARGIN_RATE) -> int:
    """판매가 기준 마진율로 출고가 → 판매가 역산.

    margin_rate = (sale - cost) / sale  →  sale = cost / (1 - margin_rate)
    반환은 원 단위 정수 (반올림).
    """
    try:
        c = float(cost or 0)
    except (TypeError, ValueError):
        return 0
    if c <= 0:
        return 0
    denom = 1.0 - float(margin_rate)
    if denom <= 0:
        return 0
    return int(round(c / denom))


def parse_date(raw: Any) -> Optional[str]:
    """다양한 포맷을 ISO 문자열(YYYY-MM-DD) 로 정규화. 실패 시 None."""
    if raw is None:
        return None
    try:
        if isinstance(raw, float) and pd.isna(raw):
            return None
    except Exception:
        pass
    if isinstance(raw, (datetime, pd.Timestamp)):
        return raw.date().isoformat()
    if isinstance(raw, date):
        return raw.isoformat()
    s = str(raw).strip()
    if not s or s.lower() in ("nan", "nat", "none"):
        return None
    # pandas 로 시도
    try:
        dt = pd.to_datetime(s, errors="coerce")
        if pd.notna(dt):
            return dt.date().isoformat()
    except Exception:
        pass
    return None


def _to_number(raw: Any) -> float:
    """엑셀 셀에서 숫자를 추출. 쉼표/공백 제거. 실패 시 0.0."""
    if raw is None:
        return 0.0
    try:
        if isinstance(raw, float) and pd.isna(raw):
            return 0.0
    except Exception:
        pass
    if isinstance(raw, (int, float)):
        return float(raw)
    s = re.sub(r"[,\s]", "", str(raw))
    if not s or s in ("-", ".", "nan"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _clean_str(raw: Any) -> str:
    """엑셀 셀 → 문자열 정규화. NaN/None → 빈 문자열."""
    if raw is None:
        return ""
    try:
        if isinstance(raw, float) and pd.isna(raw):
            return ""
    except Exception:
        pass
    s = str(raw).strip()
    if s.lower() in ("nan", "nat", "none"):
        return ""
    return s


_MAPPING_HINTS: dict[str, tuple[list[str], list[str]]] = {
    # key : (exact_candidates, substring_candidates)
    "order_date":     (["계약일", "주문일", "order_date"], ["계약", "주문일자"]),
    "delivery_date":  (["배송일", "출고일", "delivery_date"], ["배송", "출고"]),
    "customer_name":  (["고객명", "이름", "성명", "name"], ["고객", "성함"]),
    "phone1":         (["전화1", "전화번호1", "휴대폰", "phone1", "연락처1"], ["전화", "연락처", "휴대"]),
    "phone2":         (["전화2", "전화번호2", "phone2", "연락처2"], []),
    "address":        (["주소", "주소1", "address"], ["도로명"]),
    "address2":       (["주소2", "지번", "address2"], ["지번주소"]),
    "employee_names": (["판매자", "담당자", "대리점", "판매자(대리점)"], ["판매", "담당", "직원"]),
    "cost_price":     (["주문금액", "출고가", "금액", "cost", "cost_price"], ["출고", "금액"]),
    "vat":            (["부가세", "VAT", "vat"], ["세액"]),
    "order_kind":     (["주문구분", "구분", "타입", "kind"], ["구분"]),
}


def auto_suggest_mapping(columns: list[str]) -> dict[str, str]:
    """엑셀 헤더 목록을 보고 TARGET_FIELDS 로 자동 매핑 후보 생성.

    2단계: (1) exact 일치 우선 (2) substring fallback. 이미 다른 필드에 매핑된 컬럼은 재사용 안 함.
    반환: {target_field_key: excel_column_name}. 확실치 않은 필드는 포함하지 않음.
    """
    normalized_cols = [(_clean_str(c), c) for c in columns]  # (norm_lower, original)
    used: set[str] = set()
    result: dict[str, str] = {}

    def _match_exact(candidates: list[str]) -> Optional[str]:
        _cand_lower = {c.lower() for c in candidates}
        for norm, orig in normalized_cols:
            if orig in used:
                continue
            if norm.lower() in _cand_lower:
                return orig
        return None

    def _match_substring(candidates: list[str]) -> Optional[str]:
        for norm, orig in normalized_cols:
            if orig in used:
                continue
            for c in candidates:
                if c and c in norm:
                    return orig
        return None

    for key, _, _ in TARGET_FIELDS:
        exact_list, sub_list = _MAPPING_HINTS.get(key, ([], []))
        hit = _match_exact(exact_list) or _match_substring(sub_list)
        if hit:
            result[key] = hit
            used.add(hit)
    return result


# ---------------------------------------------------------------------------
# 엑셀 파싱
# ---------------------------------------------------------------------------

def parse_excel(file_bytes: bytes, sheet_name: Any = 0) -> pd.DataFrame:
    """업로드된 엑셀 바이트를 DataFrame 으로 로드. 모든 컬럼을 문자열/원본 그대로 유지."""
    bio = io.BytesIO(file_bytes)
    df = pd.read_excel(bio, sheet_name=sheet_name, engine="openpyxl", dtype=object)
    if isinstance(df, dict):
        # 여러 시트가 반환된 경우 첫번째만 사용
        _first = next(iter(df.values()))
        df = _first if isinstance(_first, pd.DataFrame) else pd.DataFrame()
    df.columns = [_clean_str(c) for c in df.columns]
    return df


# ---------------------------------------------------------------------------
# Dry-run: 미리보기 데이터셋 빌드
# ---------------------------------------------------------------------------

@dataclass
class PreparedRow:
    """임포트할 1건. commit 시 그대로 INSERT payload 로 사용."""
    # 원본 참조용 (미리보기 표시)
    row_index: int
    order_kind: str
    # 고객 필드
    customer_name: str
    phone1: str
    phone1_digits: str
    phone2: str
    address: str
    # 주문 필드
    order_date: Optional[str]
    delivery_date: Optional[str]
    cost_price: int
    sale_price: int
    employee_names: str
    # dedup / 상태
    match_status: str = "new"         # 'new' | 'existing' | 'duplicate' | 'invalid'
    existing_customer_id: Optional[int] = None
    reason: str = ""                  # invalid/duplicate 사유


@dataclass
class PreviewResult:
    rows: list[PreparedRow] = field(default_factory=list)
    total_input: int = 0
    total_valid: int = 0
    total_invalid: int = 0
    total_skipped_kind: int = 0
    total_new_customer: int = 0
    total_matched_customer: int = 0
    total_duplicate_orders: int = 0
    total_sale_amount: int = 0
    yearly_stats: list[dict] = field(default_factory=list)  # [{year, count, sales}]
    invalid_samples: list[dict] = field(default_factory=list)  # 앞 20건 표시용


def _apply_mapping(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    """엑셀 → 표준 컬럼명(TARGET_FIELDS 키) 으로 리매핑한 새 DataFrame 반환."""
    out = pd.DataFrame(index=df.index)
    for key, _, _ in TARGET_FIELDS:
        src = mapping.get(key)
        if src and src in df.columns:
            out[key] = df[src]
        else:
            out[key] = None
    return out


def build_preview(
    df_raw: pd.DataFrame,
    mapping: dict[str, str],
    *,
    margin_rate: float = DEFAULT_MARGIN_RATE,
    order_kind_allowed: Optional[set[str]] = None,
    existing_customers_by_phone: dict[str, int] | None = None,
    existing_order_fingerprints: set[tuple] | None = None,
) -> PreviewResult:
    """엑셀 df + 매핑 → 임포트 대상 목록 계산 (DB 변경 없음).

    Parameters
    ----------
    df_raw : 파싱된 엑셀 원본 DataFrame
    mapping : {target_field: 엑셀_컬럼명}
    margin_rate : 마진율 (기본 0.20)
    order_kind_allowed : 포함할 주문구분 값 집합. None 이면 기본(주문/판매/정상/빈값)
    existing_customers_by_phone : {phone1_digits: customer_id}  같은 매장의 기존 고객
    existing_order_fingerprints : {(customer_id, order_date, cost_price), ...}
        같은 매장에 이미 존재하는 주문 지문. 중복 스킵용.

    Returns
    -------
    PreviewResult
    """
    if order_kind_allowed is None:
        order_kind_allowed = DEFAULT_ORDER_KIND_ALLOWED
    if existing_customers_by_phone is None:
        existing_customers_by_phone = {}
    if existing_order_fingerprints is None:
        existing_order_fingerprints = set()

    result = PreviewResult()
    result.total_input = len(df_raw)
    if df_raw.empty:
        return result

    df = _apply_mapping(df_raw, mapping)

    # 임포트 세션 내에서 신규 고객으로 확정된 phone → 가상 id 매핑
    # (같은 파일 내 동일 phone1 은 하나의 고객으로 통합)
    session_new_phone_to_slot: dict[str, int] = {}
    next_new_slot = -1  # 음수 슬롯 id (commit 시 실제 id로 교체)
    yearly: dict[int, dict] = {}

    for i, row in df.iterrows():
        kind = _clean_str(row.get("order_kind"))
        if order_kind_allowed and kind not in order_kind_allowed:
            result.total_skipped_kind += 1
            continue

        name = _clean_str(row.get("customer_name"))
        phone1_raw = _clean_str(row.get("phone1"))
        phone1_digits = normalize_phone(phone1_raw)
        phone2_raw = _clean_str(row.get("phone2"))
        address = _clean_str(row.get("address"))
        address2 = _clean_str(row.get("address2"))
        if address2 and address2 not in address:
            address = f"{address} ({address2})" if address else address2

        order_date = parse_date(row.get("order_date"))
        delivery_date = parse_date(row.get("delivery_date"))
        cost_price_raw = _to_number(row.get("cost_price"))
        cost_price = int(round(cost_price_raw))
        sale_price = compute_sale_price(cost_price, margin_rate)
        employee_names = _clean_str(row.get("employee_names"))

        prepared = PreparedRow(
            row_index=int(i),
            order_kind=kind,
            customer_name=name,
            phone1=phone1_raw,
            phone1_digits=phone1_digits,
            phone2=phone2_raw,
            address=address,
            order_date=order_date,
            delivery_date=delivery_date,
            cost_price=cost_price,
            sale_price=sale_price,
            employee_names=employee_names,
        )

        # 유효성 검증
        problems: list[str] = []
        if not name and not phone1_digits:
            problems.append("고객명·전화 모두 없음")
        if not phone1_digits:
            problems.append("전화번호1 없음")
        if not order_date:
            problems.append("계약일 파싱 실패")
        if cost_price <= 0:
            problems.append("주문금액 없음")
        if problems:
            prepared.match_status = "invalid"
            prepared.reason = ", ".join(problems)
            result.total_invalid += 1
            if len(result.invalid_samples) < 20:
                result.invalid_samples.append({
                    "row": prepared.row_index + 2,  # 엑셀 행번호(헤더1+1index)
                    "고객명": name,
                    "전화1": phone1_raw,
                    "계약일": _clean_str(row.get("order_date")),
                    "주문금액": _clean_str(row.get("cost_price")),
                    "사유": prepared.reason,
                })
            result.rows.append(prepared)
            continue

        # 고객 매칭 (기존 DB → 세션 → 신규) + 지문 중복 검사
        existing_cid = existing_customers_by_phone.get(phone1_digits)
        if existing_cid is not None:
            prepared.existing_customer_id = existing_cid
            # 기존 DB 고객이면 지문 검사 (음수 슬롯인 세션 신규는 검사 불가 & 불필요)
            fp = (int(existing_cid), order_date, cost_price)
            if fp in existing_order_fingerprints:
                prepared.match_status = "duplicate"
                prepared.reason = f"기존 주문 지문 중복 (cid={existing_cid}, {order_date}, {cost_price:,})"
                result.total_duplicate_orders += 1
                result.rows.append(prepared)
                continue
            prepared.match_status = "existing"
            result.total_matched_customer += 1
        elif phone1_digits in session_new_phone_to_slot:
            # 같은 파일 내 앞선 행에서 이미 신규 고객 슬롯이 잡힘 → 재구매로 부착
            prepared.existing_customer_id = session_new_phone_to_slot[phone1_digits]
            prepared.match_status = "existing"
            result.total_matched_customer += 1
        else:
            session_new_phone_to_slot[phone1_digits] = next_new_slot
            prepared.existing_customer_id = next_new_slot
            prepared.match_status = "new"
            result.total_new_customer += 1
            next_new_slot -= 1

        result.total_valid += 1
        result.total_sale_amount += sale_price
        _y = int(order_date[:4]) if order_date and len(order_date) >= 4 else 0
        y_entry = yearly.setdefault(_y, {"year": _y, "count": 0, "sales": 0})
        y_entry["count"] += 1
        y_entry["sales"] += sale_price

        result.rows.append(prepared)

    result.yearly_stats = sorted(yearly.values(), key=lambda x: x["year"])
    return result


def preview_to_dataframe(preview: PreviewResult, max_rows: int = 500) -> pd.DataFrame:
    """미리보기 결과를 화면 표시용 DataFrame 으로. 최대 max_rows 개."""
    rows = []
    for r in preview.rows[:max_rows]:
        rows.append({
            "행": r.row_index + 2,
            "상태": {"new": "🆕신규", "existing": "🔗기존매칭", "duplicate": "⚠️중복스킵", "invalid": "❌오류"}.get(r.match_status, r.match_status),
            "고객명": r.customer_name,
            "전화1": r.phone1,
            "계약일": r.order_date or "",
            "배송일": r.delivery_date or "",
            "출고가": r.cost_price,
            "판매가(역산)": r.sale_price,
            "담당자": r.employee_names,
            "주소": r.address,
            "사유": r.reason,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 기존 데이터 로드 (매장별 dedup용)
# ---------------------------------------------------------------------------

def load_existing_customer_phone_map(client, store_name: str) -> dict[str, int]:
    """해당 매장 app_customers 에서 phone1_digits → id 매핑을 만든다.

    같은 phone1 로 여러 row 가 있으면 가장 작은 id (가장 오래된 등록) 를 채택.
    """
    if not store_name:
        return {}
    result: dict[str, int] = {}
    _PAGE = 1000
    offset = 0
    while True:
        try:
            q = client.table("app_customers").select("id, phone1")\
                .eq("store_name", store_name).order("id").range(offset, offset + _PAGE - 1)
            r = q.execute()
        except Exception as e:
            logger.warning("load_existing_customer_phone_map failed: %s", e)
            break
        rows = (r.data or []) if hasattr(r, "data") else []
        for row in rows:
            digits = normalize_phone(row.get("phone1"))
            if not digits:
                continue
            _id = row.get("id")
            if _id is None:
                continue
            _id = int(_id)
            if digits not in result or _id < result[digits]:
                result[digits] = _id
        if len(rows) < _PAGE:
            break
        offset += _PAGE
    return result


def load_existing_order_fingerprints(client, db_filename: str) -> set[tuple]:
    """해당 매장 app_orders 의 (customer_id, order_date, cost_price) 지문 집합.

    새 임포트가 기존 주문과 완전 중복이면 스킵. 최근 임포트 재실행 방지 목적.
    """
    if not db_filename:
        return set()
    out: set[tuple] = set()
    _PAGE = 1000
    offset = 0
    while True:
        try:
            q = client.table("app_orders").select("customer_id, order_date, cost_price")\
                .eq("db_filename", db_filename).order("id").range(offset, offset + _PAGE - 1)
            r = q.execute()
        except Exception as e:
            logger.warning("load_existing_order_fingerprints failed: %s", e)
            break
        rows = (r.data or []) if hasattr(r, "data") else []
        for row in rows:
            cid = row.get("customer_id")
            od = row.get("order_date")
            cp = row.get("cost_price")
            if cid is None or not od:
                continue
            try:
                out.add((int(cid), str(od), int(round(float(cp or 0)))))
            except (TypeError, ValueError):
                pass
        if len(rows) < _PAGE:
            break
        offset += _PAGE
    return out


# ---------------------------------------------------------------------------
# Commit: 실제 INSERT
# ---------------------------------------------------------------------------

@dataclass
class CommitResult:
    customers_inserted: int = 0
    orders_inserted: int = 0
    payments_inserted: int = 0
    duplicates_skipped: int = 0
    invalid_skipped: int = 0
    failed_customers: int = 0
    failed_orders: int = 0
    errors: list[str] = field(default_factory=list)


def commit_import(
    client,
    preview: PreviewResult,
    *,
    store_name: str,
    db_filename: str,
    created_by: str = "",
    margin_rate: float = DEFAULT_MARGIN_RATE,
    progress_cb: Optional[Callable[[str, float], None]] = None,
) -> CommitResult:
    """PreviewResult 를 받아 실제 DB에 INSERT.

    순서:
      1) 신규 phone 그룹별 app_customers INSERT → 실제 id 회수
      2) valid/existing 행마다 app_orders INSERT (import_source 태그) → 새 order_id
      3) 각 주문에 대응하는 app_payments INSERT (완납액, 배송일 또는 계약일)

    progress_cb(stage, pct) 로 진행률 콜백 (선택).
    """
    res = CommitResult()
    if client is None or not store_name or not db_filename:
        res.errors.append("client/store_name/db_filename 필수")
        return res

    # 1) 신규 고객 그룹핑: 음수 슬롯별로 첫 등장 행의 이름/주소/전화 채택
    slot_rows: dict[int, PreparedRow] = {}
    for r in preview.rows:
        if r.match_status == "new" and r.existing_customer_id is not None and r.existing_customer_id < 0:
            slot_rows.setdefault(r.existing_customer_id, r)

    slot_to_real_cid: dict[int, int] = {}
    slot_items = list(slot_rows.items())
    _BATCH = 100
    for _bi in range(0, len(slot_items), _BATCH):
        chunk = slot_items[_bi:_bi + _BATCH]
        payload = []
        for _slot, r in chunk:
            payload.append({
                "store_name": store_name,
                "name": r.customer_name or "미입력",
                "phone1": r.phone1 or "",
                "phone2": r.phone2 or None,
                "address": r.address or None,
                "source": LEGACY_IMPORT_SOURCE,
            })
        try:
            resp = client.table("app_customers").insert(payload).execute()
            data = (resp.data or []) if hasattr(resp, "data") else []
            # Supabase 는 insert 순서대로 반환 → chunk 와 zip
            for (slot, _r), row in zip(chunk, data):
                if row and "id" in row:
                    slot_to_real_cid[slot] = int(row["id"])
                    res.customers_inserted += 1
                else:
                    res.failed_customers += 1
        except Exception as e:
            res.failed_customers += len(chunk)
            res.errors.append(f"고객 INSERT 실패({len(chunk)}건): {e}")
        if progress_cb:
            progress_cb("고객 등록", min(1.0, (_bi + len(chunk)) / max(1, len(slot_items))))

    # 2 & 3) 주문 + 결제 INSERT
    valid_rows = [r for r in preview.rows if r.match_status in ("new", "existing")]
    # 주문 배치 삽입: Supabase 는 insert().execute() 로 배열도 가능하고 새 id 배열을 리턴.
    order_payloads: list[dict] = []
    order_row_refs: list[PreparedRow] = []  # 나중에 payment 매핑용
    for r in valid_rows:
        cid = r.existing_customer_id
        if cid is not None and cid < 0:
            cid = slot_to_real_cid.get(cid)
        if cid is None:
            res.failed_orders += 1
            continue
        order_payloads.append({
            "db_filename": db_filename,
            "customer_id": int(cid),
            "employee_names": r.employee_names or None,
            "order_date": r.order_date,
            "delivery_date": r.delivery_date,
            "category": None,
            "cost_price": int(r.cost_price),
            "total_amount": int(r.sale_price),
            "visit_reason": "과거이력",
            "purchase_reason": "과거이력",
            "actual_margin": int(r.sale_price - r.cost_price),
            "display_sales_amount": 0,
            "display_cost_amount": 0,
            "balance_status": "완납",
            "import_source": LEGACY_IMPORT_SOURCE,
        })
        order_row_refs.append(r)

    # 완납 결제도 함께 넣을 order_id 회수를 위해 배치 단위로 처리
    for _bi in range(0, len(order_payloads), _BATCH):
        chunk_payloads = order_payloads[_bi:_bi + _BATCH]
        chunk_refs = order_row_refs[_bi:_bi + _BATCH]
        try:
            resp = client.table("app_orders").insert(chunk_payloads).execute()
            data = (resp.data or []) if hasattr(resp, "data") else []
        except Exception as e:
            # import_source 컬럼 미존재 시 재시도 (마이그레이션 안 된 환경)
            _msg = str(e)
            if "import_source" in _msg or "42703" in _msg:
                fallback = [{k: v for k, v in p.items() if k != "import_source"} for p in chunk_payloads]
                try:
                    resp = client.table("app_orders").insert(fallback).execute()
                    data = (resp.data or []) if hasattr(resp, "data") else []
                    res.errors.append("import_source 컬럼 미존재 → 태그 없이 저장됨. SUPABASE_APP_ORDERS_IMPORT_SOURCE.sql 실행 필요.")
                except Exception as e2:
                    res.failed_orders += len(chunk_payloads)
                    res.errors.append(f"주문 INSERT 실패({len(chunk_payloads)}건): {e2}")
                    if progress_cb:
                        progress_cb("주문 등록", min(1.0, (_bi + len(chunk_payloads)) / max(1, len(order_payloads))))
                    continue
            else:
                res.failed_orders += len(chunk_payloads)
                res.errors.append(f"주문 INSERT 실패({len(chunk_payloads)}건): {e}")
                if progress_cb:
                    progress_cb("주문 등록", min(1.0, (_bi + len(chunk_payloads)) / max(1, len(order_payloads))))
                continue

        # 반환된 새 order id 를 payment payload 로 이어붙이기
        payment_payloads: list[dict] = []
        for row_ref, order_row in zip(chunk_refs, data):
            oid = order_row.get("id") if isinstance(order_row, dict) else None
            if oid is None:
                res.failed_orders += 1
                continue
            res.orders_inserted += 1
            pay_date = row_ref.delivery_date or row_ref.order_date
            payment_payloads.append({
                "db_filename": db_filename,
                "order_id": int(oid),
                "payment_date": pay_date,
                "amount": int(row_ref.sale_price),
                "payment_method": "과거이력",
                "card_company": None,
                "fee_amount": 0,
                "onnuri_approval_code": None,
                "created_by": created_by or "legacy_import",
            })
        if payment_payloads:
            try:
                client.table("app_payments").insert(payment_payloads).execute()
                res.payments_inserted += len(payment_payloads)
            except Exception as e:
                res.errors.append(f"결제 INSERT 실패({len(payment_payloads)}건): {e}")

        if progress_cb:
            progress_cb("주문 등록", min(1.0, (_bi + len(chunk_payloads)) / max(1, len(order_payloads))))

    # 스킵 카운트
    res.duplicates_skipped = preview.total_duplicate_orders
    res.invalid_skipped = preview.total_invalid

    return res


__all__ = [
    "LEGACY_IMPORT_SOURCE",
    "DEFAULT_MARGIN_RATE",
    "TARGET_FIELDS",
    "DEFAULT_ORDER_KIND_ALLOWED",
    "normalize_phone",
    "compute_sale_price",
    "parse_date",
    "auto_suggest_mapping",
    "parse_excel",
    "PreparedRow",
    "PreviewResult",
    "build_preview",
    "preview_to_dataframe",
    "load_existing_customer_phone_map",
    "load_existing_order_fingerprints",
    "CommitResult",
    "commit_import",
]
