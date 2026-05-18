"""推荐系统领域服务。"""

from app.services.recommendation.ops_service import (
    AUTO_MATERIALIZATION_COMPLETED_EVENT,
    IMPORT_COMPLETED_EVENT,
    IMPORT_FAILED_EVENT,
    IMPORT_REQUESTED_EVENT,
    build_recommendation_ops_metrics,
    record_recommendation_event,
)
