"""복수 주문 초과이관 전표를 한쪽만 지우지 못하게 하는 판별.

결제변경으로 만든 마이너스 상계는 원본 결제 수단을 유지한다.
초과이관은 수단이 비어 있거나 '초과이관'이다. 이쪽만 삭제하면
상대 주문 잔금이 다시 미수로 돌아간다.
"""

from __future__ import annotations

import uuid

TRANSFER_METHOD = "초과이관"
GROUP_PREFIX = "이관그룹:"


def _blank_method(method) -> bool:
    if method is None:
        return True
    if isinstance(method, float) and method != method:
        return True
    text = str(method).strip()
    return text == "" or text.lower() in ("none", "nan", "null", "<na>")


def hide_payment_method_fields(has_overpaid: bool, actual_paid: int) -> bool:
    """실입금 0인 초과이관은 새 카드·승인번호가 없다."""
    return bool(has_overpaid and int(actual_paid) == 0)


def is_transfer_alloc(is_transfer_mode: bool, actual_paid: int, alloc_amt: int) -> bool:
    """이번 배분 행을 초과이관 전표로 저장할지.

    음수 배분은 항상 이관이다.
    실입금 0인 순수 이관에서는 반대편 양수 행도 실제 카드·현금 결제가 아니다.
    """
    if alloc_amt < 0:
        return True
    return bool(is_transfer_mode and int(actual_paid) == 0 and alloc_amt > 0)


def protects_from_single_delete(method, amount) -> bool:
    """한쪽만 지우는 버튼(상계 삭제·잘못 입력 삭제)에서 뺄 행."""
    if str(method or "").strip() == TRANSFER_METHOD:
        return True
    try:
        amt = float(amount or 0)
    except (TypeError, ValueError):
        return False
    return amt < 0 and _blank_method(method)


def new_transfer_group() -> str:
    return GROUP_PREFIX + uuid.uuid4().hex[:12]


def transfer_group_token(card_company) -> str | None:
    text = str(card_company or "").strip()
    if text.startswith(GROUP_PREFIX) and len(text) > len(GROUP_PREFIX):
        return text
    return None
