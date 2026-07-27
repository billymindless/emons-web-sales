"""품목 분류(taxonomy) 서비스.

용도
-----
`app_order_items.product_name` 을 대분류 카테고리(1필드) 로 매핑하는
`app_product_taxonomy` 테이블을 조회·갱신하고, Gemini 배치 분류를 수행한다.

핵심 흐름
---------
1) `find_unclassified_product_names` : 임포트된 라인아이템 중 taxonomy 미분류 목록
2) `classify_with_gemini`             : 미분류 목록을 Gemini 로 배치 분류 (JSON)
3) `upsert_classifications`           : 결과를 taxonomy 에 UPSERT
4) `load_taxonomy_map`                : (product_name → category) dict 를 반환
5) `load_order_items_batched`         : order_id 리스트 → items DataFrame (배치)
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# 관리자 UI · 서비스에서 공유하는 카테고리 도메인.
# SUPABASE_APP_PRODUCT_TAXONOMY.sql CHECK 제약과 반드시 동일해야 한다.
CATEGORIES: list[str] = [
    "옷장", "식탁", "자녀방_서재", "침대", "SSDS침대",
    "소파", "거실장", "소품", "전시품", "기타",
]

TAXONOMY_TABLE = "app_product_taxonomy"
ITEMS_TABLE = "app_order_items"
KEYWORD_RULES_TABLE = "app_product_keyword_rules"


# ---------------------------------------------------------------------------
# 조회
# ---------------------------------------------------------------------------

def _page_select(client, table: str, columns: str, *, filters: list[tuple[str, str, Any]] | None = None) -> list[dict]:
    """공용 페이지네이션 SELECT (1000행 page).

    in_ 필터는 200개 청크로 자동 분할.
    """
    filters = filters or []
    in_filters = [(op, c, v) for op, c, v in filters if op == "in_"]
    base = [(op, c, v) for op, c, v in filters if op != "in_"]
    _IN_CHUNK = 200

    def _run(extra_in: list[tuple[str, str, Any]]) -> list[dict]:
        out: list[dict] = []
        _PAGE = 1000
        offset = 0
        while True:
            q = client.table(table).select(columns)
            for op, c, v in base + extra_in:
                if op == "eq":
                    q = q.eq(c, v)
                elif op == "in_":
                    q = q.in_(c, v)
            r = q.range(offset, offset + _PAGE - 1).execute()
            rows = (r.data or []) if hasattr(r, "data") else []
            out.extend(rows)
            if len(rows) < _PAGE:
                break
            offset += _PAGE
        return out

    if not in_filters:
        return _run([])

    op0, c0, vals0 = in_filters[0]
    vals0 = list(vals0 or [])
    if not vals0:
        return []
    if len(vals0) <= _IN_CHUNK:
        return _run([(op0, c0, vals0)] + in_filters[1:])
    merged: list[dict] = []
    for i in range(0, len(vals0), _IN_CHUNK):
        merged.extend(_run([(op0, c0, vals0[i:i + _IN_CHUNK])] + in_filters[1:]))
    return merged


def load_taxonomy_map(client) -> dict[str, str]:
    """(product_name → category) 매핑 딕셔너리 반환."""
    if client is None:
        return {}
    try:
        rows = _page_select(client, TAXONOMY_TABLE, "product_name, category")
    except Exception as e:
        logger.warning("taxonomy 로드 실패: %s", e)
        return {}
    return {
        str(r["product_name"]): str(r["category"])
        for r in rows
        if r.get("product_name") and r.get("category")
    }


def load_taxonomy_full(client) -> pd.DataFrame:
    """taxonomy 전체를 DataFrame 으로 반환 (관리 UI 용)."""
    if client is None:
        return pd.DataFrame(columns=["product_name", "category", "source", "confidence", "updated_by", "updated_at"])
    try:
        rows = _page_select(
            client, TAXONOMY_TABLE,
            "product_name, category, source, confidence, updated_by, updated_at",
        )
    except Exception as e:
        logger.warning("taxonomy 전체 로드 실패: %s", e)
        return pd.DataFrame(columns=["product_name", "category", "source", "confidence", "updated_by", "updated_at"])
    return pd.DataFrame(rows) if rows else pd.DataFrame(
        columns=["product_name", "category", "source", "confidence", "updated_by", "updated_at"]
    )


def find_unclassified_product_names(
    client,
    *,
    db_filename: Optional[str] = None,
    limit: int = 2000,
) -> pd.DataFrame:
    """taxonomy 에 없는 product_name 을 빈도 desc 로 반환.

    반환 컬럼: product_name, 빈도(라인 등장 횟수), 최근등장(created_at 최대)
    """
    if client is None:
        return pd.DataFrame(columns=["product_name", "빈도", "최근등장"])

    filters: list[tuple[str, str, Any]] = []
    if db_filename:
        filters.append(("eq", "db_filename", db_filename))
    try:
        rows = _page_select(client, ITEMS_TABLE, "product_name, created_at", filters=filters)
    except Exception as e:
        logger.warning("items distinct 조회 실패: %s", e)
        return pd.DataFrame(columns=["product_name", "빈도", "최근등장"])

    if not rows:
        return pd.DataFrame(columns=["product_name", "빈도", "최근등장"])

    df = pd.DataFrame(rows)
    df = df[df["product_name"].astype(str).str.strip() != ""].copy()
    if df.empty:
        return pd.DataFrame(columns=["product_name", "빈도", "최근등장"])

    known = set(load_taxonomy_map(client).keys())
    df = df[~df["product_name"].isin(known)]
    if df.empty:
        return pd.DataFrame(columns=["product_name", "빈도", "최근등장"])

    agg = (
        df.groupby("product_name")
        .agg(빈도=("product_name", "count"), 최근등장=("created_at", "max"))
        .reset_index()
        .sort_values("빈도", ascending=False)
    )
    if limit and limit > 0:
        agg = agg.head(int(limit))
    return agg.reset_index(drop=True)


def count_all_product_names(client, *, db_filename: Optional[str] = None) -> int:
    """전체 등장한 distinct product_name 개수 (통계용)."""
    if client is None:
        return 0
    filters: list[tuple[str, str, Any]] = []
    if db_filename:
        filters.append(("eq", "db_filename", db_filename))
    try:
        rows = _page_select(client, ITEMS_TABLE, "product_name", filters=filters)
    except Exception:
        return 0
    if not rows:
        return 0
    s = pd.Series([r.get("product_name") for r in rows], dtype="object")
    s = s[s.astype(str).str.strip() != ""]
    return int(s.nunique())


# ---------------------------------------------------------------------------
# Gemini 배치 분류
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "너는 가구 매장의 매입 원장 품목명을 대분류 카테고리 1개로 분류하는 분류기다. "
    "출력은 오직 JSON 문자열 배열로만 답한다. 배열의 각 원소는 입력 품목명 리스트와 "
    "동일한 개수·동일한 순서로, i번째 원소가 i번째 품목명의 카테고리다 (품목명 자체는 출력하지 않는다).\n"
    "허용 카테고리는 다음 10개 중 하나로만 답한다.\n"
    "['옷장','식탁','자녀방_서재','침대','SSDS침대','소파','거실장','소품','전시품','기타']\n\n"
    "분류 힌트:\n"
    "- '옷장': 옷장·행거·드레스룸·붙박이장 등 옷 수납 가구.\n"
    "- '식탁': 식탁·다이닝체어·벤치 등 식사용 가구.\n"
    "- '자녀방_서재': 유아·아동·주니어 침대·책상·수납 등 자녀방 전용 가구 및 서재 책상·책장·학생용 가구.\n"
    "- '침대': 성인 침대(퀸/킹) 본체·매트리스·프레임·머리판·협탁·서랍장·깔판·수납함·확장형 옵션·사이드쿠션 등 침대 세트 구성품 일체 (자녀방_서재·SSDS 제외).\n"
    "- 'SSDS침대': 이름에 'SSDS' 가 포함되거나 SSDS 브랜드의 침대류 및 부속(협탁·머리판·확장옵션 포함).\n"
    "- '소파': 소파·리클라이너·소파테이블.\n"
    "- '거실장': TV장·거실 수납장·사이드보드 등 거실용 수납 가구.\n"
    "- '소품': 거울·조명·화병·러그·티테이블 등 세트에 속하지 않는 단품 소품. 침대 세트에 명확히 속하는 협탁·수납함은 '침대' 로 분류.\n"
    "- '전시품': 이름에 '전시' 표기가 있는 항목.\n"
    "- '기타': 위 어느 것에도 명확히 해당하지 않는 부자재·배송비·설치비 등.\n\n"
    "'기타' 는 최후의 수단이다. 이름에 명시적인 힌트 단어가 없어도 재질·형태·구성품·브랜드 표기 등 "
    "이름에서 유추 가능한 모든 정보를 활용해 위 카테고리 중 가장 가능성이 높은 것으로 분류하라. "
    "정말로 어느 카테고리에도 해당하지 않는 부자재·배송비·설치비·서비스성 항목에만 '기타' 를 사용하고, "
    "애매하다는 이유만으로 '기타' 를 고르지 마라.\n\n"
    "예시 — 입력 [\"디망스침대협탁\",\"거울\",\"SSDS침대머리판\",\"TV장1800\"] → 출력 [\"침대\",\"소품\",\"SSDS침대\",\"거실장\"]"
)

# 규칙 기반 강제 분류: Gemini 호출에 앞서 결정적으로 확정한다 (문자열 패턴이 명확한 경우).
# 1) 'SSDS침대' 규칙: 소괄호() 안에 '1100'/'SS'/'DS'/'WS' 표기가 있고, 그 앞부분에 '침대'/'매트리스'/'머리판' 이
#    포함되며, '식탁'/'옷장' 등 다른 가구 키워드가 이름에 없어야 한다.
# 2) 키워드 강제 규칙: 아래 순서대로 첫 매칭 키워드의 카테고리로 무조건 분류한다.
#    (더 구체적인 패턴을 먼저 두어야 한다 — 예: '일체형화장대' 는 '화장대' 보다 앞서 검사)
_SSDS_BED_KEYWORDS = ("침대", "매트리스", "머리판")
_SSDS_EXCLUDE_KEYWORDS = ("식탁", "옷장")
_SSDS_SIZE_TOKENS = ("1100", "SS", "DS", "WS")
_PAREN_CONTENT_RE = re.compile(r"\(([^)]*)\)")

_FORCE_KEYWORD_RULES: list[tuple[str, str]] = [
    ("거실협탁", "거실장"),
    ("침대협탁", "침대"),          # '침대협탁판넬' 도 이 패턴에 포함되어 함께 처리됨
    ("일체형화장대", "옷장"),       # '화장대' 규칙보다 먼저 검사해야 한다
    ("마감용머리목", "옷장"),
    ("마감용 머리목", "옷장"),
    ("이불장", "옷장"),
    ("기둥목", "옷장"),
    ("옷장", "옷장"),
    ("화장대", "소품"),
    ("서랍장", "소품"),
    ("침대깔판", "침대"),          # SS/DS 규칙에서 걸리지 않은 나머지 침대깔판
    ("머리판", "침대"),            # SS/DS 규칙에서 걸리지 않은 나머지 머리판
    ("아이누리", "자녀방_서재"),
    ("책상의자", "자녀방_서재"),
    ("티테이블", "소품"),
    ("거실장", "거실장"),
    ("소파", "소파"),
    ("식탁", "식탁"),
    ("매트리스", "침대"),          # SS/DS 규칙에서 걸리지 않은 나머지 매트리스
]


def _apply_ssds_size_rule(name: str) -> Optional[str]:
    """소괄호 안 SS/DS/WS/1100 표기 + 앞선 '침대'/'매트리스' → 'SSDS침대' 강제 분류. 아니면 None."""
    if not name:
        return None
    if any(kw in name for kw in _SSDS_EXCLUDE_KEYWORDS):
        return None
    for m in _PAREN_CONTENT_RE.finditer(name):
        content_upper = m.group(1).upper()
        if not any(tok in content_upper for tok in _SSDS_SIZE_TOKENS):
            continue
        prefix = name[:m.start()]
        if any(kw in prefix for kw in _SSDS_BED_KEYWORDS):
            return "SSDS침대"
    return None


def _apply_keyword_rules(name: str, extra_rules: Optional[list[tuple[str, str]]] = None) -> Optional[str]:
    """단순 키워드 포함 시 무조건 해당 카테고리로 강제 분류. 순서대로 첫 매칭 적용.

    extra_rules(관리자가 DB에 등록한 브랜드/키워드 사전)를 하드코딩 규칙보다 먼저 검사한다.
    """
    if not name:
        return None
    for keyword, category in (extra_rules or []):
        if keyword and keyword in name:
            return category
    for keyword, category in _FORCE_KEYWORD_RULES:
        if keyword in name:
            return category
    return None


def _apply_rule_based_category(name: str, extra_rules: Optional[list[tuple[str, str]]] = None) -> Optional[str]:
    """규칙 기반 카테고리 확정: SSDS 규격 규칙 → 키워드 강제 규칙 순으로 확인."""
    return _apply_ssds_size_rule(name) or _apply_keyword_rules(name, extra_rules)


def classify_with_gemini(
    names: list[str],
    api_key: Optional[str] = None,
    model: str = "gemini-flash-latest",
    timeout: float = 30.0,
    batch: int = 30,
    extra_rules: Optional[list[tuple[str, str]]] = None,
) -> dict[str, tuple[str, float]]:
    """품목명 리스트를 Gemini 로 배치 분류.

    반환: {product_name: (category, confidence)}.
    - 규칙 기반 강제 분류(`_apply_rule_based_category`: SSDS 규격 표기 + extra_rules(DB 키워드 사전)
      + `_FORCE_KEYWORD_RULES` 키워드 목록)에 걸리면 Gemini 호출 없이 확정 분류한다.
    - 나머지는 Gemini 응답을 그대로 신뢰한다. 응답이 허용 카테고리 밖이거나 파싱 실패 시에만 ('기타', 0.0).
      Gemini 는 '기타' 를 최후의 수단으로만 쓰도록 프롬프트에서 지시받는다 (`_SYSTEM_PROMPT`).
    - API 키 없거나 오류 시 나머지 이름을 ('기타', 0.0) 로 채움.
    """
    if not names:
        return {}

    allowed = set(CATEGORIES)
    result: dict[str, tuple[str, float]] = {}

    remaining: list[str] = []
    for n in names:
        forced = _apply_rule_based_category(n, extra_rules)
        if forced:
            result[n] = (forced, 1.0)
        else:
            remaining.append(n)

    if not remaining:
        return result

    key = (api_key or os.environ.get("GEMINI_API_KEY", "")).strip()

    if not key:
        logger.warning("classify_with_gemini: GEMINI_API_KEY 미설정 → 나머지 전체 '기타' 반환")
        result.update({n: ("기타", 0.0) for n in remaining})
        return result

    try:
        import httpx
    except ImportError:
        logger.warning("httpx 미설치 → 나머지 전체 '기타' 반환")
        result.update({n: ("기타", 0.0) for n in remaining})
        return result

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={key}"
    )

    def _one_batch(chunk: list[str]) -> Optional[list[str]]:
        """chunk 와 동일 개수·순서의 카테고리 배열을 반환. 실패/개수불일치 시 None.

        (품목명을 JSON 키로 되돌려받는 방식은 LLM이 원문을 살짝 바꿔 반환하면
        문자열이 어긋나 전부 매칭 실패 → 전부 '기타' 로 빠지는 문제가 있어,
        입력 순서에 대응하는 배열 방식 + enum 스키마로 대체한다.)
        """
        user_prompt = (
            "다음 품목명 리스트를 각각 카테고리로 분류하라. "
            "입력과 동일한 개수·동일한 순서의 JSON 문자열 배열로만 답하라 (품목명은 출력하지 않는다).\n"
            + json.dumps(chunk, ensure_ascii=False)
        )
        body = {
            "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
                "responseSchema": {
                    "type": "ARRAY",
                    "items": {"type": "STRING", "enum": CATEGORIES},
                },
            },
        }
        try:
            with httpx.Client(timeout=timeout) as c:
                r = c.post(url, json=body)
                r.raise_for_status()
            raw = r.json()
            text = raw["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(text)
            if not isinstance(data, list) or len(data) != len(chunk):
                logger.warning(
                    "Gemini 응답 개수 불일치: 요청 %d개 → 응답 %r",
                    len(chunk), data if not isinstance(data, list) else f"{len(data)}개",
                )
                return None
            return [str(x) for x in data]
        except Exception as e:
            logger.warning("Gemini 분류 배치 실패 (%d개): %s", len(chunk), e)
            return None

    seen: set[str] = set()
    dedup = []
    for n in remaining:
        if n in seen:
            continue
        seen.add(n)
        dedup.append(n)

    for i in range(0, len(dedup), max(1, batch)):
        chunk = dedup[i:i + batch]
        cats = _one_batch(chunk)
        if cats is None:
            # 배치 실패/개수 불일치 시에만 '기타' 로 채운다 (Gemini 응답을 받은 경우는 그대로 신뢰).
            result.update({n: ("기타", 0.0) for n in chunk})
            continue
        for name, cat in zip(chunk, cats):
            cat = (cat or "").strip()
            if cat not in allowed:
                cat, conf = "기타", 0.0
            else:
                conf = 0.9  # Gemini 는 확신도를 안 주므로 정상 응답이면 고정 0.9
            result[name] = (cat, conf)

    return result


# ---------------------------------------------------------------------------
# 저장 (UPSERT)
# ---------------------------------------------------------------------------

def upsert_classifications(
    client,
    mapping: dict[str, tuple[str, float]] | dict[str, str],
    *,
    source: str = "gemini",
    updated_by: str = "",
    batch: int = 100,
) -> tuple[int, list[str]]:
    """taxonomy 에 (product_name, category) UPSERT.

    mapping 값은 (category, confidence) 또는 category(str) 둘 다 허용.
    반환: (upserted_count, errors)
    """
    errors: list[str] = []
    if client is None or not mapping:
        return 0, errors
    if source not in ("gemini", "manual", "override", "rule"):
        errors.append(f"허용되지 않은 source: {source}")
        return 0, errors

    allowed = set(CATEGORIES)
    payloads: list[dict] = []
    for name, val in mapping.items():
        if not name:
            continue
        if isinstance(val, tuple):
            cat, conf = val[0], val[1] if len(val) > 1 else None
        else:
            cat, conf = val, None
        if cat not in allowed:
            errors.append(f"허용되지 않은 category={cat!r} (품목명={name!r}) → 스킵")
            continue
        payloads.append({
            "product_name": name,
            "category": cat,
            "source": source,
            "confidence": (float(conf) if conf is not None else None),
            "updated_by": updated_by or None,
        })

    upserted = 0
    for i in range(0, len(payloads), max(1, batch)):
        chunk = payloads[i:i + batch]
        try:
            client.table(TAXONOMY_TABLE).upsert(chunk, on_conflict="product_name").execute()
            upserted += len(chunk)
        except Exception as e:
            errors.append(f"UPSERT 실패 ({len(chunk)}건): {e}")
    return upserted, errors


def delete_classification(client, product_name: str) -> bool:
    """단일 product_name 분류 삭제 (오분류 정정 목적)."""
    if client is None or not product_name:
        return False
    try:
        client.table(TAXONOMY_TABLE).delete().eq("product_name", product_name).execute()
        return True
    except Exception as e:
        logger.warning("분류 삭제 실패 (%s): %s", product_name, e)
        return False


# ---------------------------------------------------------------------------
# 브랜드/키워드 사전 (app_product_keyword_rules)
# ---------------------------------------------------------------------------

def load_keyword_rules(client) -> list[tuple[str, str]]:
    """활성 키워드 규칙을 (keyword, category) 리스트로 반환 (priority 오름차순).

    `classify_with_gemini(extra_rules=...)` 에 그대로 전달하는 용도.
    테이블 미생성 등 오류 시 빈 리스트 (하드코딩 규칙만 동작).
    """
    if client is None:
        return []
    try:
        rows = _page_select(
            client, KEYWORD_RULES_TABLE, "keyword, category, priority",
            filters=[("eq", "is_active", True)],
        )
    except Exception as e:
        logger.warning("keyword rules 로드 실패: %s", e)
        return []
    allowed = set(CATEGORIES)
    valid = [
        r for r in rows
        if str(r.get("keyword") or "").strip() and r.get("category") in allowed
    ]
    valid.sort(key=lambda r: (int(r.get("priority") or 100), str(r.get("keyword"))))
    return [(str(r["keyword"]).strip(), str(r["category"])) for r in valid]


def load_keyword_rules_full(client) -> pd.DataFrame:
    """키워드 규칙 전체를 DataFrame 으로 반환 (관리 UI 용, 비활성 포함)."""
    _cols = ["id", "keyword", "category", "priority", "note", "is_active", "updated_by", "updated_at"]
    if client is None:
        return pd.DataFrame(columns=_cols)
    try:
        rows = _page_select(client, KEYWORD_RULES_TABLE, ", ".join(_cols))
    except Exception as e:
        logger.warning("keyword rules 전체 로드 실패: %s", e)
        return pd.DataFrame(columns=_cols)
    if not rows:
        return pd.DataFrame(columns=_cols)
    df = pd.DataFrame(rows)
    return df.sort_values(["priority", "keyword"]).reset_index(drop=True)


def save_keyword_rule(
    client,
    keyword: str,
    category: str,
    *,
    priority: int = 100,
    note: str = "",
    is_active: bool = True,
    updated_by: str = "",
) -> tuple[bool, str]:
    """키워드 규칙 UPSERT (keyword UNIQUE 기준). 반환: (성공여부, 오류메시지)."""
    keyword = (keyword or "").strip()
    if client is None or not keyword:
        return False, "client/keyword 필수"
    if category not in set(CATEGORIES):
        return False, f"허용되지 않은 category: {category!r}"
    payload = {
        "keyword": keyword,
        "category": category,
        "priority": int(priority),
        "note": (note or "").strip() or None,
        "is_active": bool(is_active),
        "updated_by": updated_by or None,
    }
    try:
        client.table(KEYWORD_RULES_TABLE).upsert(payload, on_conflict="keyword").execute()
        return True, ""
    except Exception as e:
        return False, f"키워드 규칙 저장 실패 ({keyword!r}): {e}"


def delete_keyword_rule(client, keyword: str) -> bool:
    """키워드 규칙 삭제."""
    keyword = (keyword or "").strip()
    if client is None or not keyword:
        return False
    try:
        client.table(KEYWORD_RULES_TABLE).delete().eq("keyword", keyword).execute()
        return True
    except Exception as e:
        logger.warning("키워드 규칙 삭제 실패 (%s): %s", keyword, e)
        return False


def reapply_keyword_rule_to_existing(
    client,
    keyword: str,
    category: str,
    *,
    updated_by: str = "",
) -> tuple[int, list[str]]:
    """키워드 규칙을 이미 분류된 기존 taxonomy 건에 소급 적용.

    - 대상: product_name 에 keyword 가 포함되고, 현재 category 가 다르며,
      source 가 'gemini' 또는 'rule' 인 건 (자동 분류 결과만 교정).
    - `source='manual'`/`'override'` 로 관리자가 직접 확정한 분류는 절대 덮어쓰지 않는다.
    반환: (수정 건수, 오류 리스트)
    """
    keyword = (keyword or "").strip()
    if client is None or not keyword:
        return 0, ["client/keyword 필수"]
    if category not in set(CATEGORIES):
        return 0, [f"허용되지 않은 category: {category!r}"]
    try:
        rows = _page_select(client, TAXONOMY_TABLE, "product_name, category, source")
    except Exception as e:
        return 0, [f"기존 분류 조회 실패: {e}"]
    targets = [
        str(r["product_name"]) for r in rows
        if r.get("product_name") and keyword in str(r["product_name"])
        and r.get("source") in ("gemini", "rule")
        and r.get("category") != category
    ]
    if not targets:
        return 0, []
    mapping = {name: (category, 1.0) for name in targets}
    return upsert_classifications(client, mapping, source="rule", updated_by=updated_by)


# ---------------------------------------------------------------------------
# 배치 로드 (다면분석·마케팅 인사이트 공용)
# ---------------------------------------------------------------------------

def load_order_items_batched(
    client,
    order_ids: list[int],
    columns: str = "order_id, product_name, quantity, line_cost, line_total",
) -> pd.DataFrame:
    """order_id 리스트 → items DataFrame (200개씩 in_ chunk)."""
    if client is None or not order_ids:
        return pd.DataFrame(columns=[c.strip() for c in columns.split(",")])
    ids = [int(x) for x in order_ids if x is not None]
    try:
        rows = _page_select(client, ITEMS_TABLE, columns, filters=[("in_", "order_id", ids)])
    except Exception as e:
        logger.warning("items 배치 로드 실패: %s", e)
        return pd.DataFrame(columns=[c.strip() for c in columns.split(",")])
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=[c.strip() for c in columns.split(",")])


__all__ = [
    "CATEGORIES",
    "TAXONOMY_TABLE",
    "ITEMS_TABLE",
    "KEYWORD_RULES_TABLE",
    "load_taxonomy_map",
    "load_taxonomy_full",
    "find_unclassified_product_names",
    "count_all_product_names",
    "classify_with_gemini",
    "upsert_classifications",
    "delete_classification",
    "load_keyword_rules",
    "load_keyword_rules_full",
    "save_keyword_rule",
    "delete_keyword_rule",
    "reapply_keyword_rule_to_existing",
    "load_order_items_batched",
]
