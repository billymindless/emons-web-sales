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
from typing import Any, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# 관리자 UI · 서비스에서 공유하는 카테고리 도메인.
# SUPABASE_APP_PRODUCT_TAXONOMY.sql CHECK 제약과 반드시 동일해야 한다.
CATEGORIES: list[str] = [
    "옷장", "식탁", "자녀방", "침대", "SSDS침대",
    "서재_학생", "소파", "소품", "전시품", "기타",
]

TAXONOMY_TABLE = "app_product_taxonomy"
ITEMS_TABLE = "app_order_items"


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
    "출력은 오직 JSON 오브젝트로만, 각 키가 입력 품목명, 값이 카테고리다. "
    "허용 카테고리는 다음 10개 중 하나로만 답한다.\n"
    "['옷장','식탁','자녀방','침대','SSDS침대','서재_학생','소파','소품','전시품','기타']\n\n"
    "분류 힌트:\n"
    "- '옷장': 옷장·행거·드레스룸·붙박이장 등 옷 수납 가구.\n"
    "- '식탁': 식탁·다이닝체어·벤치 등 식사용 가구.\n"
    "- '자녀방': 유아·아동·주니어 침대·책상·수납 등 자녀방 전용 가구.\n"
    "- '침대': 성인 침대(퀸/킹) 본체·매트리스·프레임·머리판·협탁·서랍장·깔판·수납함·확장형 옵션·사이드쿠션 등 침대 세트 구성품 일체 (자녀방·SSDS 제외).\n"
    "- 'SSDS침대': 이름에 'SSDS' 가 포함되거나 SSDS 브랜드의 침대류 및 부속(협탁·머리판·확장옵션 포함).\n"
    "- '서재_학생': 서재 책상·책장·학생용 가구.\n"
    "- '소파': 소파·리클라이너·소파테이블.\n"
    "- '소품': 거울·조명·화병·러그 등 세트에 속하지 않는 단품 소품. 침대 세트에 명확히 속하는 협탁·수납함은 '침대' 로 분류.\n"
    "- '전시품': 이름에 '전시' 표기가 있는 항목.\n"
    "- '기타': 위 어느 것에도 명확히 해당하지 않는 부자재·배송비·설치비 등.\n\n"
    "예시: {\"디망스침대협탁\":\"침대\",\"디망스침대확장형사이드쿠션\":\"침대\",\"거울\":\"소품\",\"SSDS침대머리판\":\"SSDS침대\"}"
)


def classify_with_gemini(
    names: list[str],
    api_key: Optional[str] = None,
    model: str = "gemini-flash-latest",
    timeout: float = 30.0,
    batch: int = 30,
) -> dict[str, tuple[str, float]]:
    """품목명 리스트를 Gemini 로 배치 분류.

    반환: {product_name: (category, confidence)}.
    - 결과가 없거나 허용 카테고리 밖이면 ('기타', 0.0).
    - API 키 없거나 오류 시 전체 이름을 ('기타', 0.0) 로 채움.
    """
    if not names:
        return {}
    key = (api_key or os.environ.get("GEMINI_API_KEY", "")).strip()

    allowed = set(CATEGORIES)
    result: dict[str, tuple[str, float]] = {}

    if not key:
        logger.warning("classify_with_gemini: GEMINI_API_KEY 미설정 → 전체 '기타' 반환")
        return {n: ("기타", 0.0) for n in names}

    try:
        import httpx
    except ImportError:
        logger.warning("httpx 미설치 → 전체 '기타' 반환")
        return {n: ("기타", 0.0) for n in names}

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={key}"
    )

    def _one_batch(chunk: list[str]) -> dict[str, str]:
        user_prompt = (
            "다음 품목명들을 카테고리로 분류하라. JSON 오브젝트로만 답하라.\n"
            + json.dumps(chunk, ensure_ascii=False)
        )
        body = {
            "systemInstruction": {"parts": [{"text": _SYSTEM_PROMPT}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": 0.0,
                "responseMimeType": "application/json",
            },
        }
        try:
            with httpx.Client(timeout=timeout) as c:
                r = c.post(url, json=body)
                r.raise_for_status()
            raw = r.json()
            text = raw["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(text)
            if not isinstance(data, dict):
                return {}
            return {str(k): str(v) for k, v in data.items()}
        except Exception as e:
            logger.warning("Gemini 분류 배치 실패 (%d개): %s", len(chunk), e)
            return {}

    seen: set[str] = set()
    dedup = []
    for n in names:
        if n in seen:
            continue
        seen.add(n)
        dedup.append(n)

    for i in range(0, len(dedup), max(1, batch)):
        chunk = dedup[i:i + batch]
        raw_map = _one_batch(chunk)
        for name in chunk:
            cat = raw_map.get(name, "").strip()
            if cat not in allowed:
                cat = "기타"
                conf = 0.0
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
    if source not in ("gemini", "manual", "override"):
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
    "load_taxonomy_map",
    "load_taxonomy_full",
    "find_unclassified_product_names",
    "count_all_product_names",
    "classify_with_gemini",
    "upsert_classifications",
    "delete_classification",
    "load_order_items_batched",
]
