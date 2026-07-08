"""공용 UI 다이얼로그(팝업) 헬퍼.

`app.py` 와 다른 페이지 모듈(`lead_management.py` 등) 이 공통으로 사용.
`app.py` 는 Streamlit 메인 스크립트라 `from app import ...` 가 환경에 따라
재실행/실패 가능성이 있어, 순수 유틸만 담긴 이 모듈을 별도로 둔다.
"""

from __future__ import annotations

from typing import Callable

import streamlit as st


def open_dialog(
    title: str,
    render_fn: Callable[[], None],
    *,
    width: str = "large",
    fallback_expander: bool = True,
    on_dismiss: Callable[[], None] | str | None = None,
) -> None:
    """`st.dialog` 로 팝업을 연다. 미지원 환경에서는 `st.expander` 로 폴백.

    - `render_fn`: 팝업 안 UI 를 그리는 무인자 함수.
      저장/취소 마지막에 `st.rerun()` 호출해 팝업을 닫는다.
    - `width`: 'small' | 'medium' | 'large' (`st.dialog` width 인자).
    - `fallback_expander`: True 면 폴백 시 expander 로, False 면 인라인 렌더.
    - `on_dismiss`: X/ESC/바깥 클릭으로 닫을 때 호출할 콜백 또는 "rerun".
      Streamlit 1.48+ 에서 지원. 미지원 시 dismissible=False 로 X 를 숨겨
      폼 내 취소 버튼으로만 닫히게 한다 (세션 플래그 잔존 방지).
    """
    if hasattr(st, "dialog"):
        try:
            dec = _build_dialog_decorator(title, width=width, on_dismiss=on_dismiss)

            @dec
            def _dlg():
                render_fn()

            _dlg()
            return
        except Exception:
            pass
    if fallback_expander:
        with st.expander(f"📌 {title}", expanded=True):
            render_fn()
    else:
        render_fn()


def _build_dialog_decorator(
    title: str,
    *,
    width: str,
    on_dismiss: Callable[[], None] | str | None,
):
    """Streamlit 버전별 st.dialog 인자 호환을 맞춘 decorator 반환."""

    def _try(**kwargs):
        return st.dialog(title, **kwargs)

    # 1) width + on_dismiss
    if on_dismiss is not None:
        try:
            return _try(width=width, on_dismiss=on_dismiss)
        except TypeError:
            pass
        # 2) on_dismiss only (width 미지원)
        try:
            return _try(on_dismiss=on_dismiss)
        except TypeError:
            pass
        # 3) on_dismiss 미지원 → X 숨김 (취소 버튼으로만 닫기)
        try:
            return _try(width=width, dismissible=False)
        except TypeError:
            pass
        try:
            return _try(dismissible=False)
        except TypeError:
            pass

    # 4) width only / bare
    try:
        return _try(width=width)
    except TypeError:
        return _try()
