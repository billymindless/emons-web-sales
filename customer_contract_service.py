"""
고객 계약서 보관 백엔드 모듈.

종이 계약서를 휴대폰으로 찍어 그 고객에 붙이고, PC 에서 세로 A4 PDF 로 다시 봅니다.
app.py 의 Supabase 클라이언트를 재사용하고, 결제변경/업무 첨부와는 분리된 버킷·테이블을 씁니다.

저장 규격 (plan: 고객_계약서_촬영_보관):
  - EXIF 회전값을 픽셀에 적용 후 EXIF 제거.
  - 가로가 더 길면 시계방향 90° 회전 → 세로 A4 고정.
  - 긴 변 3508px (A4 300dpi) 로 축소, JPEG q=85. 2MB 초과면 q=75 재시도.
  - 썸네일은 긴 변 480px JPEG. 폰의 장 목록용.
  - 그 고객의 PDF (A4 세로) 를 저장 요청 안에서 다시 만들어 {db}/{cid}/contract.pdf 로 캐시.
"""

from __future__ import annotations

import io
import logging
import uuid
from typing import Iterable

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Supabase 클라이언트 (app.py 재사용)
# ─────────────────────────────────────────────────────────────────────

def _client():
    from app import get_supabase_client  # noqa: WPS433
    return get_supabase_client()


def _admin_client():
    from app import get_supabase_admin_client  # noqa: WPS433
    return get_supabase_admin_client()


BUCKET = "customer-contracts"
A4_LONG_PX = 3508      # A4 세로 긴 변 (300dpi)
A4_SHORT_PX = 2480     # A4 가로 짧은 변 (300dpi)
THUMB_LONG_PX = 480
JPEG_Q_HIGH = 85
JPEG_Q_LOW = 75
MAX_PER_PAGE_BYTES = 2 * 1024 * 1024  # 2MB. 넘을 때만 q=75

# PDF 페이지 크기 (A4 세로, PDF 포인트 = 1/72 inch)
A4_PDF_W_PT = 595
A4_PDF_H_PT = 842


# ─────────────────────────────────────────────────────────────────────
# 이미지 정규화
# ─────────────────────────────────────────────────────────────────────

def normalize_contract_image(raw: bytes) -> tuple[bytes, bytes]:
    """원본 바이트 → (저장용 JPEG, 썸네일 JPEG).

    세로 A4 긴 변 3508px, q=85. 2MB 초과 시 q=75 재시도.
    썸네일은 긴 변 480px.
    """
    from PIL import Image, ImageOps  # noqa: WPS433

    if not raw:
        raise ValueError("빈 이미지입니다.")

    img = Image.open(io.BytesIO(raw))
    # EXIF 회전값 픽셀에 적용 (대부분의 폰 사진은 EXIF 로 방향만 바뀌어 있음)
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")

    # 가로가 더 길면 세로로 회전 (계약서는 세로 A4 고정)
    w, h = img.size
    if w > h:
        img = img.rotate(-90, expand=True)
        w, h = img.size

    # A4 세로 긴 변 3508px 로 축소. 세로가 긴 변.
    if h > A4_LONG_PX:
        scale = A4_LONG_PX / float(h)
        new_w = max(1, int(round(w * scale)))
        img = img.resize((new_w, A4_LONG_PX), Image.LANCZOS)

    # 저장용 JPEG
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_Q_HIGH, optimize=True, progressive=True)
    data = buf.getvalue()
    if len(data) > MAX_PER_PAGE_BYTES:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_Q_LOW, optimize=True, progressive=True)
        data = buf.getvalue()

    # 썸네일
    thumb = img.copy()
    thumb.thumbnail((THUMB_LONG_PX, THUMB_LONG_PX), Image.LANCZOS)
    tbuf = io.BytesIO()
    thumb.save(tbuf, format="JPEG", quality=75, optimize=True, progressive=True)
    return data, tbuf.getvalue()


# ─────────────────────────────────────────────────────────────────────
# Storage 헬퍼
# ─────────────────────────────────────────────────────────────────────

def _storage_upload(path: str, data: bytes, mime: str = "image/jpeg", upsert: bool = False) -> str | None:
    """실패 시 사람이 읽을 에러 메시지, 성공 시 None."""
    admin, err = _admin_client()
    if err or not admin:
        c, cerr = _client()
        if cerr or not c:
            return err or cerr or "Supabase 연결 불가"
        admin = c
    try:
        res = admin.storage.from_(BUCKET).upload(
            path=path,
            file=data,
            file_options={"content-type": mime, "upsert": "true" if upsert else "false"},
        )
        up_err = getattr(res, "error", None)
        if up_err:
            return (
                f"Storage 업로드 실패: {up_err}. "
                f"Supabase 대시보드 → Storage → '{BUCKET}' 버킷이 있는지 확인하세요."
            )
    except Exception as e:
        msg = str(e)
        if "exists" in msg.lower() or "duplicate" in msg.lower():
            # upsert=True 로 재시도
            try:
                admin.storage.from_(BUCKET).update(
                    path=path, file=data, file_options={"content-type": mime, "upsert": "true"},
                )
                return None
            except Exception as e2:
                return f"Storage 업로드 실패: {e2}"
        return f"Storage 업로드 실패: {e}"
    return None


def signed_url(path: str, expires_in: int = 3600) -> str | None:
    if not path:
        return None
    admin, err = _admin_client()
    if err or not admin:
        c, cerr = _client()
        if cerr or not c:
            return None
        admin = c
    try:
        r = admin.storage.from_(BUCKET).create_signed_url(path, expires_in)
        if isinstance(r, dict):
            return r.get("signedURL") or r.get("signed_url")
        return getattr(r, "signedURL", None) or getattr(r, "signed_url", None)
    except Exception as e:
        logger.warning("signed_url 실패 %s: %s", path, e)
        return None


def _storage_remove(paths: list[str]) -> None:
    if not paths:
        return
    admin, err = _admin_client()
    if err or not admin:
        c, cerr = _client()
        if cerr or not c:
            return
        admin = c
    try:
        admin.storage.from_(BUCKET).remove(paths)
    except Exception as e:
        logger.warning("Storage 삭제 실패: %s", e)


# ─────────────────────────────────────────────────────────────────────
# DB 조회
# ─────────────────────────────────────────────────────────────────────

def _page_count_query(sc, db_filename: str, customer_ids: Iterable[int]) -> dict[int, int]:
    ids = sorted({int(c) for c in customer_ids if c is not None})
    if not ids:
        return {}
    try:
        r = (
            sc.table("app_customer_contracts")
            .select("customer_id")
            .eq("db_filename", db_filename)
            .in_("customer_id", ids)
            .execute()
        )
        out: dict[int, int] = {}
        for row in (r.data or []):
            try:
                cid = int(row.get("customer_id"))
            except (TypeError, ValueError):
                continue
            out[cid] = out.get(cid, 0) + 1
        return out
    except Exception as e:
        logger.warning("계약서 장수 조회 실패: %s", e)
        return {}


def count_pages_by_customers(db_filename: str, customer_ids: Iterable[int]) -> dict[int, int]:
    """고객 id 리스트 → {cid: 장수}. 결과에 없는 cid 는 0 장."""
    if not db_filename:
        return {}
    sc, err = _client()
    if err or not sc:
        return {}
    return _page_count_query(sc, db_filename, customer_ids)


def list_pages(db_filename: str, customer_id: int) -> list[dict]:
    """그 고객의 페이지들을 page_no 오름차순으로 반환."""
    if not db_filename or customer_id is None:
        return []
    sc, err = _client()
    if err or not sc:
        return []
    try:
        r = (
            sc.table("app_customer_contracts")
            .select("id, page_no, storage_path, thumb_path, byte_size, created_at, uploaded_by, order_id")
            .eq("db_filename", db_filename)
            .eq("customer_id", int(customer_id))
            .order("page_no")
            .execute()
        )
        return list(r.data or [])
    except Exception as e:
        logger.warning("계약서 페이지 조회 실패: %s", e)
        return []


def search_customers(db_filename: str, query: str, limit: int = 20) -> list[dict]:
    """이름/전화번호로 고객 검색. app.py 의 신규 매출 검색과 같은 방식."""
    import re  # noqa: WPS433
    if not db_filename:
        return []
    sc, err = _client()
    if err or not sc:
        return []
    q = (query or "").strip()
    try:
        qb = sc.table("app_customers").select("id, name, phone1, phone2, store_name")
        # store_name 필터는 호출자가 current store 로 좁히지만, 여기서는 db_filename 과 매장을 매핑 못하므로
        # 상위 호출자가 or_ 로 매장을 좁히지 않더라도 limit 로 자른다.
        if q:
            q_safe = re.sub(r"[*,]", "", q)
            qb = qb.or_(
                f"name.ilike.*{q_safe}*,phone1.ilike.*{q_safe}*,phone2.ilike.*{q_safe}*"
            )
        r = qb.order("id", desc=True).limit(limit * 3).execute()
        rows = list(r.data or [])
    except Exception as e:
        logger.warning("계약서 고객 검색 실패: %s", e)
        return []
    # 같은 이름+전화 중복 제거 (앞 쪽 id 우선)
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for row in rows:
        key = (str(row.get("name") or "").strip(), str(row.get("phone1") or "").strip())
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
        if len(out) >= limit:
            break
    return out


# ─────────────────────────────────────────────────────────────────────
# 저장 (한 장)
# ─────────────────────────────────────────────────────────────────────

def save_contract_page(
    db_filename: str,
    customer_id: int,
    raw_image: bytes,
    uploaded_by: str,
    order_id: int | None = None,
    replace_page_id: int | None = None,
) -> tuple[dict | None, str | None]:
    """한 장 저장. 성공 시 (row, None), 실패 시 (None, err).

    replace_page_id 가 있으면 그 page_no 의 사진만 교체 (다시 찍기).
    새 장이면 그 고객 최대 page_no + 1 로 저장. PDF 는 저장 안에서 재생성한다.
    """
    if not db_filename:
        return None, "매장 DB 정보가 없습니다."
    if customer_id is None:
        return None, "고객이 선택되지 않았습니다."
    if not raw_image:
        return None, "빈 파일입니다."

    sc, err = _client()
    if err or not sc:
        return None, err or "Supabase 연결 불가"

    try:
        page_bytes, thumb_bytes = normalize_contract_image(raw_image)
    except Exception as e:
        return None, f"이미지 처리 실패: {e}"

    cid = int(customer_id)

    if replace_page_id is not None:
        # 기존 행 유지, 파일만 새 UUID 로 교체 (CDN 캐시 혼선 방지)
        try:
            cur = sc.table("app_customer_contracts").select(
                "id, page_no, storage_path, thumb_path"
            ).eq("id", int(replace_page_id)).maybe_single().execute()
            old = (cur.data or {})
        except Exception as e:
            return None, f"교체 대상 조회 실패: {e}"
        if not old or int(old.get("id") or 0) != int(replace_page_id):
            return None, "교체할 페이지를 찾을 수 없습니다."
        new_path = f"{db_filename}/{cid}/{uuid.uuid4().hex}.jpg"
        new_thumb = f"{db_filename}/{cid}/thumb_{uuid.uuid4().hex}.jpg"
        up_err = _storage_upload(new_path, page_bytes, "image/jpeg")
        if up_err:
            return None, up_err
        _storage_upload(new_thumb, thumb_bytes, "image/jpeg")
        try:
            sc.table("app_customer_contracts").update({
                "storage_path": new_path,
                "thumb_path": new_thumb,
                "byte_size": len(page_bytes),
                "uploaded_by": uploaded_by,
            }).eq("id", int(replace_page_id)).execute()
        except Exception as e:
            _storage_remove([new_path, new_thumb])
            return None, f"DB 갱신 실패: {e}"
        # 옛 파일은 remove 시도 (실패해도 무시)
        _storage_remove([p for p in (old.get("storage_path"), old.get("thumb_path")) if p])
        row = {"id": int(replace_page_id), "page_no": old.get("page_no"),
               "storage_path": new_path, "thumb_path": new_thumb}
    else:
        # 새 장: 현재 최대 page_no + 1
        try:
            cur = (
                sc.table("app_customer_contracts")
                .select("page_no")
                .eq("db_filename", db_filename)
                .eq("customer_id", cid)
                .order("page_no", desc=True)
                .limit(1)
                .execute()
            )
            max_rows = cur.data or []
            next_page = int(max_rows[0]["page_no"]) + 1 if max_rows else 1
        except Exception as e:
            return None, f"page_no 조회 실패: {e}"

        new_path = f"{db_filename}/{cid}/{uuid.uuid4().hex}.jpg"
        new_thumb = f"{db_filename}/{cid}/thumb_{uuid.uuid4().hex}.jpg"
        up_err = _storage_upload(new_path, page_bytes, "image/jpeg")
        if up_err:
            return None, up_err
        _storage_upload(new_thumb, thumb_bytes, "image/jpeg")

        try:
            r = sc.table("app_customer_contracts").insert({
                "db_filename": db_filename,
                "customer_id": cid,
                "order_id": int(order_id) if order_id else None,
                "page_no": next_page,
                "storage_path": new_path,
                "thumb_path": new_thumb,
                "mime_type": "image/jpeg",
                "byte_size": len(page_bytes),
                "uploaded_by": uploaded_by,
                "ocr_status": "skipped",
            }).execute()
            row = (r.data or [{}])[0] if r.data else {}
        except Exception as e:
            _storage_remove([new_path, new_thumb])
            return None, f"DB 저장 실패: {e}"

    # PDF 재생성 (실패해도 저장은 유지)
    try:
        rebuild_pdf(db_filename, cid)
    except Exception as e:
        logger.warning("PDF 재생성 실패 (저장은 유지): %s", e)

    return row, None


def delete_page(db_filename: str, page_id: int) -> str | None:
    if not db_filename or page_id is None:
        return "잘못된 요청"
    sc, err = _client()
    if err or not sc:
        return err or "Supabase 연결 불가"
    try:
        cur = sc.table("app_customer_contracts").select(
            "id, customer_id, storage_path, thumb_path"
        ).eq("id", int(page_id)).maybe_single().execute()
        row = cur.data or {}
    except Exception as e:
        return f"조회 실패: {e}"
    if not row:
        return "없는 페이지"
    cid = int(row.get("customer_id"))
    try:
        sc.table("app_customer_contracts").delete().eq("id", int(page_id)).execute()
    except Exception as e:
        return f"삭제 실패: {e}"
    _storage_remove([p for p in (row.get("storage_path"), row.get("thumb_path")) if p])
    # PDF 재생성
    try:
        rebuild_pdf(db_filename, cid)
    except Exception as e:
        logger.warning("삭제 후 PDF 재생성 실패: %s", e)
    return None


# ─────────────────────────────────────────────────────────────────────
# PDF 재생성
# ─────────────────────────────────────────────────────────────────────

def pdf_path(db_filename: str, customer_id: int) -> str:
    return f"{db_filename}/{int(customer_id)}/contract.pdf"


def rebuild_pdf(db_filename: str, customer_id: int) -> str | None:
    """저장된 장들을 세로 A4 PDF 로 다시 만들어 Storage 에 올린다.
    장이 없으면 PDF 파일만 지운다. 성공 시 PDF 경로, 실패 시 None."""
    from PIL import Image  # noqa: WPS433
    pages = list_pages(db_filename, customer_id)
    pdf_p = pdf_path(db_filename, customer_id)
    if not pages:
        _storage_remove([pdf_p])
        return None

    admin, err = _admin_client()
    if err or not admin:
        c, cerr = _client()
        if cerr or not c:
            logger.warning("PDF 재생성 중 Supabase 연결 실패")
            return None
        admin = c

    imgs: list = []
    for p in pages:
        path = p.get("storage_path")
        if not path:
            continue
        try:
            data = admin.storage.from_(BUCKET).download(path)
        except Exception as e:
            logger.warning("PDF 재생성 중 다운로드 실패 %s: %s", path, e)
            return None
        try:
            img = Image.open(io.BytesIO(data)).convert("RGB")
        except Exception as e:
            logger.warning("PDF 재생성 중 이미지 로드 실패 %s: %s", path, e)
            return None
        # A4 세로 캔버스에 비율 유지로 중앙 배치
        canvas = Image.new("RGB", (A4_SHORT_PX, A4_LONG_PX), (255, 255, 255))
        iw, ih = img.size
        scale = min(A4_SHORT_PX / iw, A4_LONG_PX / ih)
        new_size = (max(1, int(round(iw * scale))), max(1, int(round(ih * scale))))
        if new_size != (iw, ih):
            img = img.resize(new_size, Image.LANCZOS)
        pos = ((A4_SHORT_PX - new_size[0]) // 2, (A4_LONG_PX - new_size[1]) // 2)
        canvas.paste(img, pos)
        imgs.append(canvas)

    if not imgs:
        return None

    out = io.BytesIO()
    first, rest = imgs[0], imgs[1:]
    try:
        first.save(
            out, format="PDF", save_all=True, append_images=rest,
            resolution=200.0,
        )
    except Exception as e:
        logger.warning("PDF 저장 실패: %s", e)
        return None

    up_err = _storage_upload(pdf_p, out.getvalue(), "application/pdf", upsert=True)
    if up_err:
        logger.warning("PDF 업로드 실패: %s", up_err)
        return None
    return pdf_p


def pdf_signed_url(db_filename: str, customer_id: int, expires_in: int = 3600) -> str | None:
    """PDF 가 있으면 서명 URL, 없으면 None."""
    if not db_filename or customer_id is None:
        return None
    return signed_url(pdf_path(db_filename, int(customer_id)), expires_in=expires_in)
