"""Widget-composition helpers extracted from ``UnifiedApp``.

Each builder takes the ``UnifiedApp`` host plus the parent frame and row,
constructs the widgets, and assigns them back onto ``host`` so the rest of
the app keeps its existing attribute interface. Builders contain only
widget layout — no business logic — and are exercised indirectly by the
``UnifiedApp`` GUI tests.
"""

from src.gui_builders.engine_bar import build_engine_bar
from src.gui_builders.header_bar import build_header_bar

__all__ = [
    "build_engine_bar",
    "build_header_bar",
]
