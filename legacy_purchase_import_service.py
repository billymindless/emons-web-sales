"""매입 원장 통합 임포트 서비스.

용도: 매입 원장 엑셀(주문 + 라인 아이템 통합) 을 app_customers · app_orders · app_order_items
에 통합 저장한다.

특징
-----
- 그룹핑 키: `(phone1_digits, 등록일, 출고번호)` 3튜플 = 1개 매출 주문 (헤더).
- 매칭 정책:
    · (customer_id, order_date) 앱 후보 0건 → to_create (신규 주문 생성)
    · 후보 1건 → to_attach (자동 매칭, 라인만 attach)
    · 후보 2건+ → unresolved (UI 에서 사용자가 수동 선택)
- 마진 역산: `판매가 = 출고가 / (1 - 0.20)` (기존 `legacy_import_service.compute_sale_price` 재사용).
- 회수 라인은 스킵, 매장분은 일반 주문과 동일 처리.
- 채널톡_자동가입 매장(store_name=CHANNEL_TALK_DEFAULT_STORE) 의 phone1 을
  대상 매장으로 사전 이관하는 유틸(`migrate_channeltalk_phones`) 을 제공.
- Dry-run(`build_preview`) 로 결과를 확인 후 `commit_import` 로 실제 INSERT.
"""
from __future__ import annotations

import io
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Optional

import pandas as pd

from legacy_import_service import (
    DEFAULT_MARGIN_RATE,
    compute_sale_price,
    normalize_phone,
    parse_date,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------

PURCHASE_IMPORT_SOURCE = "엑셀_매입원장"
"""app_orders.import_source / app_order_items.import_source / app_customers.source 태그."""

CHANNEL_TALK_DEFAULT_STORE = os.environ.get("CHANNEL_TALK_DEFAULT_STORE", "채널톡")
"""api.py 와 동일. 채널톡 자동가입 고객이 저장되는 스토어명."""

# 매입 원장 표준 필드 정의: (필드키, 한글라벨, 필수여부)
# 최종 업로드 양식(14컬럼) 기준으로 필수 필드를 최소화.
# `ship_number`, `quantity`, `product_code`, `line_cost`, `line_total` 은 있으면 사용,
# 없으면 2튜플 그룹핑 + 수량 1 기본값으로 동작한다.
PURCHASE_TARGET_FIELDS: list[tuple[str, str, bool]] = [
    # 주문 헤더 (필수)
    ("order_date",     "등록일 (계약일)",     True),
    ("customer_name",  "고객명",              True),
    ("phone1",         "전화1",               True),
    ("product_name",   "품명",                True),
    ("unit_cost",      "출고가 (단가)",       True),
    # 주문 헤더 (옵션)
    ("delivery_date",  "배송일",             False),
    ("phone2",         "전화2",              False),
    ("address",        "주소1 (도로명)",      False),
    ("address2",       "주소2 (상세/아파트)",  False),
    ("employee_names", "판매자 (담당자)",     False),
    ("order_kind",     "주문구분",           False),
    ("order_status",   "주문상태",           False),
    # 라인 아이템 (옵션)
    ("ship_number",    "출고번호",           False),
    ("product_code",   "품번",               False),
    ("quantity",       "주문수(수량)",       False),
    ("line_cost",      "주문금액 (VAT별도)",  False),
    ("vat",            "부가세",             False),
    ("line_total",     "합계 (VAT포함)",     False),
    ("item_note",      "품목비고",           False),
]

# 매핑 힌트 (엑셀 헤더 → 표준 필드)
_MAPPING_HINTS: dict[str, tuple[list[str], list[str]]] = {
    "order_date":     (["등록일", "계약일", "주문일"], ["등록", "계약"]),
    "delivery_date":  (["배송일"], ["배송"]),
    "customer_name":  (["고객명", "이름", "성명"], ["고객", "성함"]),
    "phone1":         (["전화1", "전화번호1", "휴대폰"], ["전화", "연락처"]),
    "phone2":         (["전화2", "전화번호2"], []),
    "address":        (["주소1", "주소", "도로명주소"], ["도로명"]),
    "address2":       (["주소2", "상세주소", "지번"], ["지번"]),
    "employee_names": (["판매자(직)", "판매자(대)", "담당자", "판매자"], ["판매", "담당"]),
    "ship_number":    (["출고번호"], ["출고번"]),
    "order_kind":     (["주문구분", "구분"], ["구분"]),
    "order_status":   (["주문상태", "상태"], ["상태"]),
    "product_name":   (["품명", "제품명", "상품명"], ["품명", "제품"]),
    "product_code":   (["품번", "상품코드", "제품코드"], ["품번", "코드"]),
    "quantity":       (["주문수", "수량", "판매수량"], ["수량"]),
    "unit_cost":      (["출고가", "단가", "매입단가"], ["출고", "단가"]),
    "line_cost":      (["주문금액", "매입금액"], ["금액"]),
    "vat":            (["부가세", "VAT"], ["세액"]),
    "line_total":     (["합계", "총액", "매입합계"], ["합계"]),
    "item_note":      (["품목비고", "비고"], ["비고"]),
}

# 임포트에서 제외할 주문구분 값
EXCLUDED_ORDER_KINDS: set[str] = {"회수"}

# TOTAL 요약 행 판별용 마커 (정상가 등의 셀에 들어가는 문자열)
_TOTAL_MARKERS: set[str] = {"TOTAL", "SUM", "합계", "총계"}


# ---------------------------------------------------------------------------
# 내부 유틸
# ---------------------------------------------------------------------------

def _clean_str(raw: Any) -> str:
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


def _to_int(raw: Any) -> int:
    if raw is None:
        return 0
    try:
        if isinstance(raw, float) and pd.isna(raw):
            return 0
    except Exception:
        pass
    if isinstance(raw, (int, float)):
        try:
            return int(round(float(raw)))
        except (TypeError, ValueError):
            return 0
    s = re.sub(r"[,\s]", "", str(raw))
    if not s or s in ("-", ".", "nan"):
        return 0
    try:
        return int(round(float(s)))
    except ValueError:
        return 0


_CUSTOMER_NAME_TAG_RE = re.compile(r"^(?P<name>[^\[\]]+?)\s*\[(?P<tag>[^\[\]]+)\]\s*(?P<suffix>.*)$")


def clean_customer_name(raw: Any) -> tuple[str, str, str]:
    """`홍기용[아너스빌]실측` → ('홍기용', '아너스빌', '실측').

    반환: (base_name, tag, suffix_note). 대괄호가 없으면 전체를 이름으로 사용.
    별표(*) 등의 문자는 이름 뒤에서 제거.
    """
    s = _clean_str(raw)
    if not s:
        return "", "", ""
    m = _CUSTOMER_NAME_TAG_RE.match(s)
    if m:
        base = (m.group("name") or "").strip().rstrip("*").strip()
        tag = (m.group("tag") or "").strip()
        suffix = (m.group("suffix") or "").strip()
        return base or s, tag, suffix
    # 대괄호 없음 → 전체를 이름으로 (별표 접미사만 제거)
    return s.rstrip("*").strip(), "", ""


def _normalize_name_key(name: Any) -> str:
    """이름 기반 identity 키 정규화. 공백 제거 + 소문자.

    예: '울산삼산리빙 (법)' → '울산삼산리빙(법)'
    """
    if name is None:
        return ""
    s = _clean_str(name)
    if not s:
        return ""
    return re.sub(r"\s+", "", s).lower()


def compute_identity_key(phone_digits: str, customer_name: Any) -> str:
    """그룹핑·매칭용 통합 identity key.

    - phone_digits 가 있으면 phone_digits 그대로
    - 없으면 `NAME:<정규화된 이름>` (매장 전시·전화번호 미기입 고객용)
    - 둘 다 없으면 빈 문자열
    """
    if phone_digits:
        return phone_digits
    n = _normalize_name_key(customer_name)
    if n:
        return f"NAME:{n}"
    return ""


def combine_address(addr1: Any, addr2: Any) -> str:
    """도로명(주소1) + 상세(주소2) 결합. 이미 포함이면 중복 방지."""
    a1 = _clean_str(addr1)
    a2 = _clean_str(addr2)
    if not a1 and not a2:
        return ""
    if not a2:
        return a1
    if not a1:
        return a2
    if a2 in a1:
        return a1
    return f"{a1} {a2}"


# ---------------------------------------------------------------------------
# 엑셀 파싱
# ---------------------------------------------------------------------------

_HEADER_MARKER_COLS = {"등록일", "주문구분", "고객명", "전화1", "출고가", "품명"}


def _detect_header_row(bio: io.BytesIO, sheet_name: Any) -> int:
    """엑셀의 앞 5행을 훑어 헤더로 보이는 첫 행 index(0-based) 를 반환.

    - 신규 최종 양식(14컬럼): Row 0 이 바로 헤더
    - 구 원장 양식(40컬럼): Row 0 제목, Row 1 헤더
    """
    bio.seek(0)
    _probe = pd.read_excel(bio, sheet_name=sheet_name, header=None, engine="openpyxl", dtype=object, nrows=5)
    if isinstance(_probe, dict):
        _first = next(iter(_probe.values()))
        _probe = _first if isinstance(_first, pd.DataFrame) else pd.DataFrame()
    for i in range(min(5, len(_probe))):
        cells = {_clean_str(v) for v in _probe.iloc[i].tolist()}
        if len(_HEADER_MARKER_COLS & cells) >= 3:
            return i
    return 0  # 기본: 첫 행


def parse_excel(file_bytes: bytes, sheet_name: Any = 0) -> pd.DataFrame:
    """매입 원장 엑셀 파싱.

    자동 지원:
      - 최종 양식(14컬럼): Row 0 = 헤더, Row 1+ = 데이터
      - 구 원장(40컬럼): Row 0 = 제목, Row 1 = 헤더, Row 2 = TOTAL, Row 3+ = 데이터

    헤더 위치는 앞 5행에서 `등록일/주문구분/고객명/전화1/출고가/품명` 중 3개 이상을
    포함한 첫 행으로 판단. TOTAL 요약 행은 상단에서 자동 제거.
    """
    bio = io.BytesIO(file_bytes)
    header_row = _detect_header_row(bio, sheet_name)
    bio.seek(0)
    df = pd.read_excel(bio, sheet_name=sheet_name, header=header_row, engine="openpyxl", dtype=object)
    if isinstance(df, dict):
        _first = next(iter(df.values()))
        df = _first if isinstance(_first, pd.DataFrame) else pd.DataFrame()
    df.columns = [_clean_str(c) for c in df.columns]
    if df.empty:
        return df

    # TOTAL 요약 행 제거 (상단 최대 3행 검사)
    def _looks_like_total(row) -> bool:
        for v in row.values:
            s = _clean_str(v).upper()
            if s in _TOTAL_MARKERS:
                return True
        return False
    drop_idx = []
    for _ix, _row in df.head(3).iterrows():
        if _looks_like_total(_row):
            drop_idx.append(_ix)
    if drop_idx:
        df = df.drop(index=drop_idx).reset_index(drop=True)
    return df


def auto_suggest_mapping(columns: list[str]) -> dict[str, str]:
    """엑셀 헤더 → 표준 필드 자동 매핑. exact 우선 → substring fallback."""
    normalized_cols = [(_clean_str(c), c) for c in columns]
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

    for key, _, _ in PURCHASE_TARGET_FIELDS:
        exact_list, sub_list = _MAPPING_HINTS.get(key, ([], []))
        hit = _match_exact(exact_list) or _match_substring(sub_list)
        if hit:
            result[key] = hit
            used.add(hit)
    return result


def _apply_mapping(df: pd.DataFrame, mapping: dict[str, str]) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    for key, _, _ in PURCHASE_TARGET_FIELDS:
        src = mapping.get(key)
        if src and src in df.columns:
            out[key] = df[src]
        else:
            out[key] = None
    return out


# ---------------------------------------------------------------------------
# 데이터 모델
# ---------------------------------------------------------------------------

@dataclass
class LineItem:
    row_index: int
    product_name: str
    product_code: str
    quantity: int
    unit_cost: int      # 출고가 (단가)
    line_cost: int      # 주문금액 (VAT 별도)
    vat: int            # 부가세
    line_total: int     # 합계 (VAT 포함)
    item_note: str
    order_kind: str


@dataclass
class OrderGroup:
    """1개의 매출 주문. (identity_key, order_date, ship_number) 3튜플 = 1 그룹.

    identity_key = phone1 digits (있으면) 또는 `NAME:정규화된 이름` (매장 전시·전화 미기입).
    """
    # 그룹핑·매칭 키
    identity_key: str
    phone1_digits: str
    order_date: Optional[str]
    ship_number: str
    # 대표 헤더 (그룹 내 첫번째 라인에서 채택)
    customer_name: str
    customer_tag: str
    phone1: str
    phone2: str
    address: str
    delivery_date: Optional[str]
    employee_names: str
    # 라인
    items: list[LineItem] = field(default_factory=list)
    # 파생 집계
    total_unit_cost: int = 0     # sum(unit_cost * quantity)  = 라인 원가 합
    total_line_cost: int = 0     # sum(line_cost)             = 원장 원본 매입금액 합
    total_line_total: int = 0    # sum(line_total)            = VAT 포함 매입 총액
    sale_price: int = 0          # 판매가 = 원가합 / (1 - margin)
    # 매칭 상태
    match_status: str = "unresolved"  # 'to_create' | 'to_attach' | 'unresolved' | 'invalid'
    existing_customer_id: Optional[int] = None   # 음수 = 세션 신규 슬롯
    candidate_order_ids: list[int] = field(default_factory=list)
    candidate_orders_meta: list[dict] = field(default_factory=list)  # [{"id":..., "total_amount":..., "delivery_date":..., "category":...}, ...]
    chosen_order_id: Optional[int] = None
    reason: str = ""

    @property
    def group_key(self) -> tuple:
        return (self.identity_key, self.order_date or "", self.ship_number or "")

    @property
    def is_display_customer(self) -> bool:
        """전화번호 없이 이름으로만 식별되는 고객 (예: 매장 전시)."""
        return self.identity_key.startswith("NAME:")

    def summary_label(self) -> str:
        _od = self.order_date or "?"
        _sn = self.ship_number or "?"
        return f"{self.customer_name} · {_od} · 출고#{_sn} · {len(self.items)}라인 · 원가 {self.total_line_cost:,}원"


# ---------------------------------------------------------------------------
# 그룹핑
# ---------------------------------------------------------------------------

def group_orders(
    df_raw: pd.DataFrame,
    mapping: dict[str, str],
    *,
    margin_rate: float = DEFAULT_MARGIN_RATE,
) -> tuple[list[OrderGroup], dict[str, int]]:
    """엑셀 df + 매핑 → OrderGroup 리스트 + 카운트 통계.

    통계: {total_lines, skipped_return, skipped_invalid, group_count}
    """
    stats = {"total_lines": 0, "skipped_return": 0, "skipped_invalid": 0, "group_count": 0}
    if df_raw is None or df_raw.empty:
        return [], stats
    df = _apply_mapping(df_raw, mapping)
    stats["total_lines"] = len(df)

    groups: dict[tuple, OrderGroup] = {}
    for i, row in df.iterrows():
        kind = _clean_str(row.get("order_kind"))
        if kind in EXCLUDED_ORDER_KINDS:
            stats["skipped_return"] += 1
            continue

        phone1_raw = _clean_str(row.get("phone1"))
        phone1_digits = normalize_phone(phone1_raw)
        order_date = parse_date(row.get("order_date"))
        ship_number_raw = row.get("ship_number")
        ship_number = _clean_str(ship_number_raw) if ship_number_raw is not None else ""

        _name_parsed, _tag_parsed, _suffix_parsed = clean_customer_name(row.get("customer_name"))
        # identity_key: 전화번호가 우선, 없으면 이름 기반 (매장 전시 등)
        identity_key = compute_identity_key(phone1_digits, _name_parsed)

        # 필수: identity(전화 or 이름), order_date, unit_cost > 0
        # 출고번호는 있으면 3튜플, 없으면 2튜플 그룹핑
        unit_cost = _to_int(row.get("unit_cost"))
        quantity = _to_int(row.get("quantity")) or 1

        problems: list[str] = []
        if not identity_key:
            problems.append("전화1·고객명 모두 없음")
        if not order_date:
            problems.append("등록일 파싱 실패")
        if unit_cost <= 0:
            problems.append("출고가 없음")
        if problems:
            stats["skipped_invalid"] += 1
            continue

        # 그룹핑 키: identity_key (전화 or NAME:이름) + 등록일 + 출고번호(옵션)
        key = (identity_key, order_date, ship_number)
        group = groups.get(key)
        if group is None:
            group = OrderGroup(
                identity_key=identity_key,
                phone1_digits=phone1_digits,
                order_date=order_date,
                ship_number=ship_number,
                customer_name=_name_parsed or "미입력",
                customer_tag=_tag_parsed,
                phone1=phone1_raw,
                phone2=_clean_str(row.get("phone2")),
                address=combine_address(row.get("address"), row.get("address2")),
                delivery_date=parse_date(row.get("delivery_date")),
                employee_names=_clean_str(row.get("employee_names")),
            )
            groups[key] = group

        # 첫 등장이 아니어도 배송일/담당자 등이 비어있으면 보완
        if not group.delivery_date:
            _dd = parse_date(row.get("delivery_date"))
            if _dd:
                group.delivery_date = _dd
        if not group.employee_names:
            _emp = _clean_str(row.get("employee_names"))
            if _emp:
                group.employee_names = _emp
        if not group.address:
            group.address = combine_address(row.get("address"), row.get("address2"))

        line_cost = _to_int(row.get("line_cost")) or (unit_cost * quantity)
        vat = _to_int(row.get("vat"))
        line_total = _to_int(row.get("line_total")) or (line_cost + vat)

        item = LineItem(
            row_index=int(i),
            product_name=_clean_str(row.get("product_name")),
            product_code=_clean_str(row.get("product_code")),
            quantity=quantity,
            unit_cost=unit_cost,
            line_cost=line_cost,
            vat=vat,
            line_total=line_total,
            item_note=_clean_str(row.get("item_note")),
            order_kind=kind,
        )
        group.items.append(item)
        group.total_unit_cost += unit_cost * quantity
        group.total_line_cost += line_cost
        group.total_line_total += line_total

    # 판매가 역산: 원장 매입금액 합(VAT 별도) 기준으로 역산
    for g in groups.values():
        g.sale_price = compute_sale_price(g.total_line_cost, margin_rate)

    stats["group_count"] = len(groups)
    return list(groups.values()), stats


# ---------------------------------------------------------------------------
# 미리보기
# ---------------------------------------------------------------------------

@dataclass
class PurchasePreviewResult:
    groups: list[OrderGroup] = field(default_factory=list)
    total_lines: int = 0
    skipped_return: int = 0
    skipped_invalid: int = 0
    group_count: int = 0
    new_customer_count: int = 0
    matched_customer_count: int = 0
    to_create_count: int = 0
    to_attach_count: int = 0
    unresolved_count: int = 0
    invalid_count: int = 0
    total_sale_amount: int = 0
    total_cost_amount: int = 0
    yearly_stats: list[dict] = field(default_factory=list)


def load_existing_customer_identity_map(client, store_name: str) -> dict[str, int]:
    """매장 스코프 identity_key → customer_id 매핑.

    - phone1 이 있는 고객: `phone_digits` → id
    - phone1 이 비어있는 고객 (매장 전시 등): `NAME:정규화된 이름` → id

    같은 키로 여러 row 가 있으면 가장 작은 id (가장 오래된 등록) 채택.
    """
    if not store_name:
        return {}
    result: dict[str, int] = {}
    _PAGE = 1000
    offset = 0
    while True:
        try:
            q = client.table("app_customers").select("id, name, phone1") \
                .eq("store_name", store_name).order("id").range(offset, offset + _PAGE - 1)
            r = q.execute()
        except Exception as e:
            logger.warning("load_existing_customer_identity_map failed: %s", e)
            break
        rows = (r.data or []) if hasattr(r, "data") else []
        for row in rows:
            _id = row.get("id")
            if _id is None:
                continue
            _id = int(_id)
            key = compute_identity_key(normalize_phone(row.get("phone1")), row.get("name"))
            if not key:
                continue
            if key not in result or _id < result[key]:
                result[key] = _id
        if len(rows) < _PAGE:
            break
        offset += _PAGE
    return result


def _fetch_existing_orders(client, db_filename: str) -> dict[tuple[int, str], list[dict]]:
    """대상 매장 앱 주문을 (customer_id, order_date) 로 그룹핑해 반환.

    반환: {(customer_id, order_date): [{"id":..., "total_amount":..., "cost_price":..., "delivery_date":..., "category":...}, ...]}
    """
    out: dict[tuple[int, str], list[dict]] = {}
    if not db_filename:
        return out
    _PAGE = 1000
    offset = 0
    while True:
        try:
            q = client.table("app_orders").select(
                "id, customer_id, order_date, delivery_date, total_amount, cost_price, category"
            ).eq("db_filename", db_filename).order("id").range(offset, offset + _PAGE - 1)
            r = q.execute()
        except Exception as e:
            logger.warning("_fetch_existing_orders failed: %s", e)
            break
        rows = (r.data or []) if hasattr(r, "data") else []
        for row in rows:
            cid = row.get("customer_id")
            od = row.get("order_date")
            if cid is None or not od:
                continue
            try:
                key = (int(cid), str(od)[:10])
            except (TypeError, ValueError):
                continue
            out.setdefault(key, []).append({
                "id": int(row["id"]) if row.get("id") is not None else None,
                "total_amount": float(row.get("total_amount") or 0),
                "cost_price": float(row.get("cost_price") or 0),
                "delivery_date": row.get("delivery_date"),
                "category": row.get("category") or "",
            })
        if len(rows) < _PAGE:
            break
        offset += _PAGE
    return out


def _fetch_existing_items_ship_numbers(client, db_filename: str) -> set[str]:
    """이미 임포트된 라인 아이템의 ship_number 집합 (재실행 안전용).

    같은 매장·같은 출고번호로 이미 line 이 존재하면 중복 임포트를 방지.
    """
    out: set[str] = set()
    if not db_filename:
        return out
    _PAGE = 1000
    offset = 0
    while True:
        try:
            q = client.table("app_order_items").select("ship_number") \
                .eq("db_filename", db_filename).range(offset, offset + _PAGE - 1)
            r = q.execute()
        except Exception as e:
            # 테이블 미존재 등: 조용히 종료 (마이그레이션 필요)
            logger.info("_fetch_existing_items_ship_numbers: %s", e)
            break
        rows = (r.data or []) if hasattr(r, "data") else []
        for row in rows:
            sn = row.get("ship_number")
            if sn is None:
                continue
            out.add(str(sn))
        if len(rows) < _PAGE:
            break
        offset += _PAGE
    return out


def _fetch_purchase_imported_pairs(client, db_filename: str) -> set[tuple[int, str]]:
    """`import_source='엑셀_매입원장'` 태그된 app_orders 의 (customer_id, order_date) 지문.

    출고번호가 없는 신규 양식(2튜플 그룹핑) 재실행 시 중복 방지용.
    """
    out: set[tuple[int, str]] = set()
    if not db_filename:
        return out
    _PAGE = 1000
    offset = 0
    while True:
        try:
            q = client.table("app_orders").select("customer_id, order_date") \
                .eq("db_filename", db_filename) \
                .eq("import_source", PURCHASE_IMPORT_SOURCE) \
                .order("id").range(offset, offset + _PAGE - 1)
            r = q.execute()
        except Exception as e:
            logger.info("_fetch_purchase_imported_pairs: %s", e)
            break
        rows = (r.data or []) if hasattr(r, "data") else []
        for row in rows:
            cid = row.get("customer_id")
            od = row.get("order_date")
            if cid is None or not od:
                continue
            try:
                out.add((int(cid), str(od)[:10]))
            except (TypeError, ValueError):
                pass
        if len(rows) < _PAGE:
            break
        offset += _PAGE
    return out


def build_preview(
    client,
    groups: list[OrderGroup],
    *,
    store_name: str,
    db_filename: str,
    existing_customers_by_phone: dict[str, int] | None = None,
) -> PurchasePreviewResult:
    """그룹 리스트 → 매칭 시도 후 to_create/to_attach/unresolved 분류.

    Parameters
    ----------
    client : Supabase client
    groups : `group_orders` 결과
    store_name : 대상 매장명 (app_customers.store_name)
    db_filename : 대상 매장 파일명 (app_orders.db_filename)
    existing_customers_by_phone : {identity_key: customer_id}. 미지정 시 새로 로드.
        identity_key = phone_digits (전화 있는 고객) 또는 `NAME:정규화된 이름` (전화 없는 매장 전시 등).
    """
    result = PurchasePreviewResult()
    result.group_count = len(groups)
    if not groups:
        return result

    if existing_customers_by_phone is None:
        existing_customers_by_phone = load_existing_customer_identity_map(client, store_name)

    existing_orders_by_cid_date = _fetch_existing_orders(client, db_filename)
    existing_ship_numbers = _fetch_existing_items_ship_numbers(client, db_filename)
    imported_pairs = _fetch_purchase_imported_pairs(client, db_filename)

    # 세션 신규 고객 슬롯 (음수 id)
    session_new_phone_to_slot: dict[str, int] = {}
    next_slot = -1
    yearly: dict[int, dict] = {}

    for g in groups:
        # 이미 임포트된 출고번호면 스킵 (3튜플/구 원장 재실행 안전)
        if g.ship_number and g.ship_number in existing_ship_numbers:
            g.match_status = "invalid"
            g.reason = f"이미 임포트된 출고번호 (ship_number={g.ship_number})"
            result.invalid_count += 1
            result.groups.append(g)
            continue

        # 고객 매칭 (identity_key 기반: 전화 우선, 없으면 이름 기반)
        existing_cid = existing_customers_by_phone.get(g.identity_key)
        if existing_cid is not None:
            g.existing_customer_id = int(existing_cid)
            result.matched_customer_count += 1
        elif g.identity_key in session_new_phone_to_slot:
            g.existing_customer_id = session_new_phone_to_slot[g.identity_key]
            result.matched_customer_count += 1  # 세션 내 재사용도 매칭으로 카운트
        else:
            session_new_phone_to_slot[g.identity_key] = next_slot
            g.existing_customer_id = next_slot
            next_slot -= 1
            result.new_customer_count += 1

        # 2튜플 그룹핑(출고번호 없음) 재실행 안전: 매입원장 태그된 동일 (cid, order_date) 존재 시 스킵
        if not g.ship_number and g.existing_customer_id is not None and g.existing_customer_id > 0:
            _pair = (int(g.existing_customer_id), g.order_date or "")
            if _pair in imported_pairs:
                g.match_status = "invalid"
                g.reason = f"이미 매입원장으로 임포트된 (고객#{g.existing_customer_id}, {g.order_date}) 그룹"
                result.invalid_count += 1
                result.groups.append(g)
                continue

        # 앱 후보 주문 조회 (DB 실존 고객만 대상, 세션 신규 슬롯은 무조건 to_create)
        if g.existing_customer_id is not None and g.existing_customer_id >= 0:
            key = (int(g.existing_customer_id), g.order_date or "")
            candidates = existing_orders_by_cid_date.get(key, [])
            g.candidate_order_ids = [c["id"] for c in candidates if c.get("id") is not None]
            g.candidate_orders_meta = [c for c in candidates if c.get("id") is not None]
            if len(g.candidate_order_ids) == 0:
                g.match_status = "to_create"
                result.to_create_count += 1
            elif len(g.candidate_order_ids) == 1:
                g.match_status = "to_attach"
                g.chosen_order_id = g.candidate_order_ids[0]
                result.to_attach_count += 1
            else:
                g.match_status = "unresolved"
                g.reason = f"동일 (고객, 등록일) 앱 주문 {len(g.candidate_order_ids)}건 존재"
                result.unresolved_count += 1
        else:
            g.match_status = "to_create"
            result.to_create_count += 1

        # 집계
        result.total_sale_amount += g.sale_price
        result.total_cost_amount += g.total_line_cost
        _y = int(g.order_date[:4]) if g.order_date and len(g.order_date) >= 4 else 0
        y_entry = yearly.setdefault(_y, {"year": _y, "count": 0, "sales": 0, "cost": 0})
        y_entry["count"] += 1
        y_entry["sales"] += g.sale_price
        y_entry["cost"] += g.total_line_cost

        # 라인 총계
        result.total_lines += len(g.items)
        result.groups.append(g)

    result.yearly_stats = sorted(yearly.values(), key=lambda x: x["year"])
    return result


def preview_to_dataframe(preview: PurchasePreviewResult, max_rows: int = 500) -> pd.DataFrame:
    """미리보기 결과를 화면 표시용 DataFrame 으로."""
    _STATUS_LABEL = {
        "to_create":  "신규 주문",
        "to_attach":  "자동 매칭",
        "unresolved": "수동 선택 필요",
        "invalid":    "스킵/오류",
    }
    rows = []
    for g in preview.groups[:max_rows]:
        _id_type = "매장 전시" if g.is_display_customer else "전화 매칭"
        rows.append({
            "상태": _STATUS_LABEL.get(g.match_status, g.match_status),
            "식별": _id_type,
            "고객명": g.customer_name + (f"[{g.customer_tag}]" if g.customer_tag else ""),
            "전화1": g.phone1,
            "등록일": g.order_date or "",
            "출고번호": g.ship_number,
            "라인수": len(g.items),
            "매입금액(합)": g.total_line_cost,
            "판매가(역산)": g.sale_price,
            "후보주문": ", ".join(str(x) for x in g.candidate_order_ids[:5]) + ("…" if len(g.candidate_order_ids) > 5 else ""),
            "사유": g.reason,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 채널톡_자동가입 매장 phone1 이관
# ---------------------------------------------------------------------------

@dataclass
class ChannelTalkMergeResult:
    scanned: int = 0
    migrated: int = 0
    already_in_target: int = 0
    errors: list[str] = field(default_factory=list)


def migrate_channeltalk_phones(
    client,
    target_store: str,
    phone_digits_set: set[str],
    *,
    dry_run: bool = False,
) -> ChannelTalkMergeResult:
    """채널톡 자동가입 매장에 저장된 phone1 중 임포트 대상 매장의 phone digits 와
    겹치는 레코드의 `store_name` 을 대상 매장으로 UPDATE 한다.

    - 대상 매장에 이미 같은 phone1 이 존재하면 채널톡 레코드는 그대로 두고
      `already_in_target` 카운터만 증가 (수동 병합 결정 필요).
    - dry_run=True 이면 실제 UPDATE 없이 스캔 결과만 반환.
    """
    res = ChannelTalkMergeResult()
    if not target_store or not phone_digits_set:
        return res

    # 채널톡 매장의 phone1 후보 조회
    try:
        _PAGE = 1000
        offset = 0
        ct_rows: list[dict] = []
        while True:
            q = client.table("app_customers").select("id, phone1, store_name, source") \
                .eq("store_name", CHANNEL_TALK_DEFAULT_STORE) \
                .order("id").range(offset, offset + _PAGE - 1)
            r = q.execute()
            rows = (r.data or []) if hasattr(r, "data") else []
            if not rows:
                break
            ct_rows.extend(rows)
            if len(rows) < _PAGE:
                break
            offset += _PAGE
    except Exception as e:
        res.errors.append(f"채널톡 매장 조회 실패: {e}")
        return res

    if not ct_rows:
        return res

    # 대상 매장에 이미 있는 digits 조회
    try:
        from legacy_import_service import load_existing_customer_phone_map
        target_map = load_existing_customer_phone_map(client, target_store)
    except Exception as e:
        res.errors.append(f"대상 매장 phone 조회 실패: {e}")
        target_map = {}

    to_migrate_ids: list[int] = []
    for row in ct_rows:
        res.scanned += 1
        digits = normalize_phone(row.get("phone1"))
        if not digits or digits not in phone_digits_set:
            continue
        if digits in target_map:
            res.already_in_target += 1
            continue
        _id = row.get("id")
        if _id is not None:
            to_migrate_ids.append(int(_id))

    if not to_migrate_ids or dry_run:
        return res

    # UPDATE (배치, 100건씩)
    _BATCH = 100
    for _bi in range(0, len(to_migrate_ids), _BATCH):
        chunk = to_migrate_ids[_bi:_bi + _BATCH]
        try:
            client.table("app_customers").update({"store_name": target_store}) \
                .in_("id", chunk).execute()
            res.migrated += len(chunk)
        except Exception as e:
            res.errors.append(f"이관 UPDATE 실패 ({len(chunk)}건): {e}")
    return res


# ---------------------------------------------------------------------------
# Commit (실 INSERT)
# ---------------------------------------------------------------------------

@dataclass
class PurchaseCommitResult:
    customers_inserted: int = 0
    orders_inserted: int = 0
    items_inserted: int = 0
    items_attached_existing: int = 0
    groups_unresolved_skipped: int = 0
    groups_invalid_skipped: int = 0
    failed_orders: int = 0
    failed_items: int = 0
    errors: list[str] = field(default_factory=list)


def _order_payload_from_group(g: OrderGroup, *, db_filename: str, customer_id: int) -> dict:
    """OrderGroup → app_orders INSERT payload."""
    _cats = [it.product_name for it in g.items if it.product_name]
    category = _cats[0] if _cats else None
    return {
        "db_filename": db_filename,
        "customer_id": int(customer_id),
        "employee_names": g.employee_names or None,
        "order_date": g.order_date,
        "delivery_date": g.delivery_date,
        "category": category,
        "cost_price": int(g.total_line_cost),
        "total_amount": int(g.sale_price),
        "visit_reason": "매입원장",
        "purchase_reason": "매입원장",
        "actual_margin": int(g.sale_price - g.total_line_cost),
        "display_sales_amount": 0,
        "display_cost_amount": 0,
        "balance_status": "완납",
        "import_source": PURCHASE_IMPORT_SOURCE,
    }


def _item_payloads_from_group(g: OrderGroup, *, order_id: int, db_filename: str) -> list[dict]:
    """OrderGroup 의 items → app_order_items INSERT payload 리스트."""
    out: list[dict] = []
    for it in g.items:
        out.append({
            "order_id": int(order_id),
            "db_filename": db_filename,
            "product_code": it.product_code or None,
            "product_name": it.product_name or None,
            "quantity": int(it.quantity),
            "unit_cost": int(it.unit_cost),
            "line_cost": int(it.line_cost),
            "vat": int(it.vat),
            "line_total": int(it.line_total),
            "item_note": it.item_note or None,
            "ship_number": g.ship_number or None,
            "order_kind": it.order_kind or None,
            "import_source": PURCHASE_IMPORT_SOURCE,
        })
    return out


def commit_import(
    client,
    preview: PurchasePreviewResult,
    *,
    store_name: str,
    db_filename: str,
    created_by: str = "",
    progress_cb: Optional[Callable[[str, float], None]] = None,
) -> PurchaseCommitResult:
    """PreviewResult 를 받아 실제 INSERT.

    4 phase:
      1) 신규 고객 INSERT (음수 슬롯별)
      2) to_create 그룹 → app_orders INSERT (판매가 역산 저장)
      3) 모든 to_create/to_attach 그룹 → app_order_items INSERT
      4) unresolved · invalid 는 스킵 (카운트만)
    """
    res = PurchaseCommitResult()
    if client is None or not store_name or not db_filename:
        res.errors.append("client/store_name/db_filename 필수")
        return res

    # ---- Phase 1: 신규 고객 INSERT ----
    slot_to_group: dict[int, OrderGroup] = {}
    for g in preview.groups:
        if g.match_status in ("invalid",):
            continue
        if g.existing_customer_id is not None and g.existing_customer_id < 0:
            slot_to_group.setdefault(g.existing_customer_id, g)

    slot_to_real_cid: dict[int, int] = {}
    _slots = list(slot_to_group.items())
    _BATCH = 100
    for _bi in range(0, len(_slots), _BATCH):
        chunk = _slots[_bi:_bi + _BATCH]
        payload = []
        for _slot, g in chunk:
            payload.append({
                "store_name": store_name,
                "name": g.customer_name or "미입력",
                "phone1": g.phone1 or "",
                "phone2": g.phone2 or None,
                "address": g.address or None,
                "source": PURCHASE_IMPORT_SOURCE,
            })
        try:
            resp = client.table("app_customers").insert(payload).execute()
            data = (resp.data or []) if hasattr(resp, "data") else []
            for (slot, _g), row in zip(chunk, data):
                if row and "id" in row:
                    slot_to_real_cid[slot] = int(row["id"])
                    res.customers_inserted += 1
        except Exception as e:
            res.errors.append(f"고객 INSERT 실패 ({len(chunk)}건): {e}")
        if progress_cb:
            progress_cb("고객 등록", min(1.0, (_bi + len(chunk)) / max(1, len(_slots))))

    # 신규 slot → 실제 cid 로 교체
    for g in preview.groups:
        if g.existing_customer_id is not None and g.existing_customer_id < 0:
            real = slot_to_real_cid.get(g.existing_customer_id)
            if real is not None:
                g.existing_customer_id = real

    # ---- Phase 2: to_create 그룹 → app_orders INSERT ----
    to_create = [g for g in preview.groups if g.match_status == "to_create" and g.existing_customer_id is not None and g.existing_customer_id > 0]
    order_payloads = [_order_payload_from_group(g, db_filename=db_filename, customer_id=g.existing_customer_id) for g in to_create]

    created_order_ids: list[Optional[int]] = [None] * len(to_create)
    for _bi in range(0, len(order_payloads), _BATCH):
        chunk_payloads = order_payloads[_bi:_bi + _BATCH]
        chunk_groups = to_create[_bi:_bi + _BATCH]
        try:
            resp = client.table("app_orders").insert(chunk_payloads).execute()
            data = (resp.data or []) if hasattr(resp, "data") else []
        except Exception as e:
            _msg = str(e)
            if "import_source" in _msg or "42703" in _msg:
                fallback = [{k: v for k, v in p.items() if k != "import_source"} for p in chunk_payloads]
                try:
                    resp = client.table("app_orders").insert(fallback).execute()
                    data = (resp.data or []) if hasattr(resp, "data") else []
                    res.errors.append("import_source 컬럼 미존재 → 태그 없이 저장됨. SUPABASE_APP_ORDERS_IMPORT_SOURCE.sql 실행 필요.")
                except Exception as e2:
                    res.failed_orders += len(chunk_payloads)
                    res.errors.append(f"주문 INSERT 실패 ({len(chunk_payloads)}건): {e2}")
                    continue
            else:
                res.failed_orders += len(chunk_payloads)
                res.errors.append(f"주문 INSERT 실패 ({len(chunk_payloads)}건): {e}")
                continue

        for _i, (g, row) in enumerate(zip(chunk_groups, data)):
            oid = row.get("id") if isinstance(row, dict) else None
            if oid is None:
                res.failed_orders += 1
                continue
            oid = int(oid)
            g.chosen_order_id = oid
            created_order_ids[_bi + _i] = oid
            res.orders_inserted += 1
        if progress_cb:
            progress_cb("주문 등록", min(1.0, (_bi + len(chunk_payloads)) / max(1, len(order_payloads))))

    # ---- Phase 3: 라인 아이템 INSERT ----
    item_payloads: list[dict] = []
    for g in preview.groups:
        if g.match_status not in ("to_create", "to_attach"):
            continue
        if not g.chosen_order_id:
            continue
        item_payloads.extend(_item_payloads_from_group(g, order_id=g.chosen_order_id, db_filename=db_filename))
        if g.match_status == "to_attach":
            res.items_attached_existing += len(g.items)

    for _bi in range(0, len(item_payloads), _BATCH):
        chunk = item_payloads[_bi:_bi + _BATCH]
        try:
            client.table("app_order_items").insert(chunk).execute()
            res.items_inserted += len(chunk)
        except Exception as e:
            res.failed_items += len(chunk)
            res.errors.append(f"라인 INSERT 실패 ({len(chunk)}건): {e}")
        if progress_cb:
            progress_cb("라인 저장", min(1.0, (_bi + len(chunk)) / max(1, len(item_payloads))))

    # ---- Phase 4: unresolved/invalid 카운트 ----
    for g in preview.groups:
        if g.match_status == "unresolved":
            res.groups_unresolved_skipped += 1
        elif g.match_status == "invalid":
            res.groups_invalid_skipped += 1

    return res


__all__ = [
    "PURCHASE_IMPORT_SOURCE",
    "PURCHASE_TARGET_FIELDS",
    "EXCLUDED_ORDER_KINDS",
    "LineItem",
    "OrderGroup",
    "PurchasePreviewResult",
    "PurchaseCommitResult",
    "ChannelTalkMergeResult",
    "clean_customer_name",
    "combine_address",
    "compute_identity_key",
    "parse_excel",
    "auto_suggest_mapping",
    "group_orders",
    "build_preview",
    "preview_to_dataframe",
    "load_existing_customer_identity_map",
    "migrate_channeltalk_phones",
    "commit_import",
]
