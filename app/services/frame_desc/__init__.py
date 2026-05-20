"""画面描述领域服务。"""

from app.services.frame_desc.debug import get_frame_description_debug_logger
from app.services.frame_desc.service import (
    FrameDescConfigError,
    FrameDescriptionService,
    FrameDescServiceError,
)
from app.services.frame_desc.source_extractor import (
    FrameSourceExtractionError,
    extract_frame_from_video_url,
)
