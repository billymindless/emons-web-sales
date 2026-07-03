"""공용 UI 다이얼로그(팝업) 헬퍼.

`app.py` 와 다른 페이지 모듈(`lead_management.py` 등) 이 공통으로 사용.
`app.py` 는 Streamlit 메인 스크립트라 `from app import ...` 가 환경에 따라
재실행/실패 가능성이 있어, 순수 유틸만 담긴 이 모듈을 별도로 둔다.
"""

from __future__ import annotations

from typing import Callable

import streamlit as st


def open_dialog(title: str, render_fn: Callable[[], None], *,
                width: str = "large",
                fallback_expander: bool = True) -> None:
    """`st.dialog` 로 팝업을 연다. 미지원 환경에서는 `st.expander` 로 폴백.

    - `render_fn`: 팝업 안 UI 를 그리는 무인자 함수.
      저장/취소 마지막에 `st.rerun()` 호출해 팝업을 닫는다.
    - `width`: 'small' | 'medium' | 'large' (`st.dialog` width 인자).
    - `fallback_expander`: True 면 폴백 시 expander 로, False 면 인라인 렌더.
    """
    if hasattr(st, "dialog"):
        try:
            try:
                dec = st.dialog(title, width=width)
            except TypeError:
                dec = st.dialog(title)

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
