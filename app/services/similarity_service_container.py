"""
全局服务容器 - 管理单例服务实例

用途：
- 提供应用级别的 Service Locator 模式
- 避免循环导入
- 便于依赖注入
"""

from app.services.similarity_audit_log_service import SimilarityAuditLogPersistenceService

# 全局单例实例（在应用启动时初始化）
_persistence_service: SimilarityAuditLogPersistenceService = None


def init_persistence_service(max_memory_buffer: int = 1000, max_retries: int = 3):
    """
    初始化全局持久化服务（应在应用启动时调用）

    Args:
        max_memory_buffer: 内存缓冲区大小
        max_retries: 重试次数
    """
    global _persistence_service
    _persistence_service = SimilarityAuditLogPersistenceService(
        max_memory_buffer=max_memory_buffer,
        max_retries=max_retries,
    )


def get_persistence_service() -> SimilarityAuditLogPersistenceService:
    """
    获取持久化服务实例

    Returns:
        SimilarityAuditLogPersistenceService 单例

    Raises:
        RuntimeError: 若服务未初始化
    """
    if _persistence_service is None:
        raise RuntimeError(
            "Persistence service not initialized. " "Call init_persistence_service() during app startup."
        )
    return _persistence_service
