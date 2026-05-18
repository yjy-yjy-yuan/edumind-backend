"""视频领域服务。"""

from app.services.video.content import (
    SUPPORTED_SUMMARY_STYLES,
    clean_multiline_text,
    clean_whitespace,
    ensure_sentence_tail,
    extract_transcript_text,
    fallback_primary_topic_name,
    fallback_tags,
    generate_primary_topic_name,
    generate_video_summary,
    generate_video_tags,
    infer_subject_from_text,
    normalize_primary_topic_name,
    normalize_summary_style,
    normalize_tags,
    read_subtitle_text,
    remove_srt_timing_markers,
    split_sentences,
    tokenize_sentence,
)
from app.services.video.api import (
    build_processing_metadata,
    build_processing_options,
    serialize_video,
)
from app.services.video.processing_registry import (
    forget_video_processing_request,
    get_video_processing_request,
    remember_video_processing_request,
)
from app.services.video.recommendation import (
    SCENE_MAP,
    list_recommendation_scenes,
    load_candidate_videos_for_recommendation,
    normalize_scene,
    recommend_videos,
    sanitize_recommendation_payload_for_client,
    summarize_recommendation_sources,
)
from app.services.video.url_import import (
    DISABLED_REMOTE_VIDEO_SOURCE_MESSAGE,
    import_remote_video_from_url,
)
from app.services.video.external_candidate import (
    ExternalCandidate,
    ExternalProviderFetchSummary,
    fetch_external_candidates,
    fetch_external_candidates_report,
    serialize_provider_summary,
)
