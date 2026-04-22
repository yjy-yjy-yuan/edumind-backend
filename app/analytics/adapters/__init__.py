"""模块化适配器（search / similarity 等）。"""

from app.analytics.adapters.search import emit_search_legacy_event
from app.analytics.adapters.search import legacy_search_dict_to_event
from app.analytics.adapters.similarity import emit_similarity_audit_event
from app.analytics.adapters.similarity import similarity_audit_to_event

__all__ = [
    "emit_search_legacy_event",
    "legacy_search_dict_to_event",
    "emit_similarity_audit_event",
    "similarity_audit_to_event",
]
