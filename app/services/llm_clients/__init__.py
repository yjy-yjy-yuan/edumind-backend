"""LLM 客户端与运行时封装。"""

from app.services.llm_clients.qwen3vl import (
    Qwen3VLClientError,
    Qwen3VLHealthResult,
    Qwen3VLRealtimeClient,
)
from app.services.llm_clients.qwen_vl_cloud import (
    QwenVLCloudClient,
    QwenVLCloudClientError,
)
from app.services.llm_clients.vinci import (
    VinciClient,
    VinciClientError,
    VinciTimeoutError,
    VinciUnavailableError,
    VinciHTTPError,
)
from app.services.llm_clients.vinci_adapter import (
    VinciAdapterError,
    VinciAdapterService,
)
from app.services.llm_clients.ollama_runtime import get_ollama_runtime_status

from app.services.llm_clients import vinci_alerting_acceptance
from app.services.llm_clients.vinci_alerting_acceptance import (
    AlertPlatformConfig,
    build_acceptance_commands,
    build_degraded_payload,
    validate_required_fields,
)
