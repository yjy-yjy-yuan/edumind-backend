"""技能注册中心（Skill Registry）。

支持版本化提示词 + 状态机 + 灰度发布：
- 每个 Skill 有多版本（v1, v2, ...），生产激活版本由 registry.json 控制
- 状态机：draft → staging → production | deprecated
- 灰度：canary（随机 N% 流量）/ full（全量）
- 热更新：无需重启即可切换激活版本（配置变更时重新加载）

使用示例::

    registry = SkillRegistry()
    skill = registry.get_active("video_understanding")
    segments = registry.get_segments("video_understanding")
    version = registry.get_version("video_understanding")
"""

from __future__ import annotations

import json
import logging
import random
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.agents.prompt_engine import PromptSegment

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------
# Skill 状态机
# ---------------------------------------------------------------


class SkillStatus(str, Enum):
    DRAFT = "draft"  # 开发中，不可用
    STAGING = "staging"  # 预发/测试阶段
    PRODUCTION = "production"  # 生产可用
    DEPRECATED = "deprecated"  # 已废弃，不可激活


# ---------------------------------------------------------------
# Rollout Strategy
# ---------------------------------------------------------------


class RolloutStrategy(str, Enum):
    FULL = "full"  # 全量
    CANARY = "canary"  # 灰度（随机百分比）
    NONE = "none"  # 不自动激活


# ---------------------------------------------------------------
# Skill Definition
# ---------------------------------------------------------------


@dataclass
class SkillSpec:
    """单个 Skill 的运行时规格。"""

    # 系统提示词（支持 Jinja2 风格变量占位 {{variable_name}}）
    system_prompt: str = ""
    # 少样本示例（每条 {role, content}）
    few_shot_examples: List[Dict[str, str]] = field(default_factory=list)
    # 约束条件
    max_tokens: int = 2048
    temperature: float = 0.7
    top_p: Optional[float] = None
    # 禁止话题（逗号分隔的关键词列表）
    banned_topics: List[str] = field(default_factory=list)
    # 调用超时（秒）
    timeout_seconds: float = 30.0
    # 工具白名单（ToolNames）
    allowed_tools: List[str] = field(default_factory=list)


@dataclass
class SkillVersion:
    """单个版本的元数据。"""

    version: str  # e.g. "1.0.0", "2.1.0"
    spec: SkillSpec
    status: SkillStatus = SkillStatus.DRAFT
    deprecated_at: Optional[str] = None  # ISO8601
    changelog: str = ""
    owner: str = ""
    created_at: str = ""  # ISO8601


@dataclass
class SkillMetadata:
    """Skill 元数据（registry 层）。"""

    id: str  # 唯一标识
    name: str  # 可读名称
    description: str = ""
    category: str = ""  # 如 "video", "search", "qa"
    tags: List[str] = field(default_factory=list)
    owner: str = ""
    # 触发事件列表
    triggers: List[str] = field(default_factory=list)
    # 版本映射：version_id -> SkillVersion
    versions: Dict[str, SkillVersion] = field(default_factory=dict)
    # 当前激活版本（registry 层控制）
    active_version: Optional[str] = None
    # 灰度发布配置
    rollout_strategy: RolloutStrategy = RolloutStrategy.FULL
    canary_percent: float = 10.0  # 0~100


@dataclass
class Skill:
    """完整的 Skill 实体。"""

    metadata: SkillMetadata
    # 运行时片段缓存（按激活版本构建）
    _segments_cache: List[PromptSegment] = field(default_factory=list, repr=False)
    _loaded_at: float = 0.0

    def get_segments(self) -> List[PromptSegment]:
        """返回激活版本的片段列表。"""
        return list(self._segments_cache)

    def get_system_prompt(self) -> str:
        """返回激活版本的系统提示词。"""
        ver = self.metadata.active_version
        if ver and ver in self.metadata.versions:
            return self.metadata.versions[ver].spec.system_prompt
        for v in self.metadata.versions.values():
            return v.spec.system_prompt
        return ""


# ---------------------------------------------------------------
# Default Skill Definitions（内置 MVP 技能）
# ---------------------------------------------------------------


def _default_learning_flow_skill() -> SkillMetadata:
    """内置学习流技能 v1。"""

    spec_v1 = SkillSpec(
        system_prompt=(
            "你是一个专业的学习助手，专为 EduMind 用户设计。\n"
            "你的职责是：\n"
            "1. 理解用户在视频中的当前学习上下文\n"
            "2. 生成精准的学习笔记摘要\n"
            "3. 自动为笔记打标签和分类\n"
            "4. 在适当场景下调用 Vinci 进行深度视频理解\n"
            "约束：\n"
            "- 始终使用中文回复\n"
            "- 摘要不超过 200 字\n"
            "- 不生成涉及政治、色情、暴力的内容\n"
            "- 如果不确定，直接说不知道，不要编造"
        ),
        few_shot_examples=[
            {
                "role": "user",
                "content": "帮我记一下这个重要概念",
            },
            {
                "role": "assistant",
                "content": "好的，已为您创建知识点笔记并绑定时间戳。",
            },
        ],
        max_tokens=2048,
        temperature=0.7,
        banned_topics=["政治", "色情", "暴力"],
        timeout_seconds=30.0,
        allowed_tools=["lf_vinci_chat", "lf_generate_summary_fallback", "lf_persist_note", "lf_create_timestamp"],
    )

    spec_v2 = SkillSpec(
        system_prompt=(
            "你是一个专业的学习助手，专为 EduMind 用户设计。\n"
            "【新增】支持多轮上下文理解，能记住用户之前的问题和回答。\n"
            "你的职责是：\n"
            "1. 理解用户在视频中的当前学习上下文（结合历史对话）\n"
            "2. 生成精准的学习笔记摘要\n"
            "3. 自动为笔记打标签和分类\n"
            "4. 在适当场景下调用 Vinci 进行深度视频理解\n"
            "约束：\n"
            "- 始终使用中文回复\n"
            "- 摘要不超过 200 字\n"
            "- 不生成涉及政治、色情、暴力的内容\n"
            "- 如果不确定，直接说不知道，不要编造\n"
            "- 遇到复杂问题时，优先调用 Vinci"
        ),
        few_shot_examples=spec_v1.few_shot_examples
        + [
            {
                "role": "user",
                "content": "之前那个公式是什么来着？",
            },
            {
                "role": "assistant",
                "content": "根据之前的对话，您提到的是洛必达法则，用于求解0/0型或∞/∞型极限问题。",
            },
        ],
        max_tokens=3072,
        temperature=0.7,
        banned_topics=["政治", "色情", "暴力"],
        timeout_seconds=45.0,
        allowed_tools=["lf_vinci_chat", "lf_generate_summary_fallback", "lf_persist_note", "lf_create_timestamp"],
    )

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return SkillMetadata(
        id="learning_flow",
        name="学习流笔记助手",
        description="视频学习场景下的自动笔记生成技能，支持上下文感知和多轮对话",
        category="video",
        tags=["笔记", "视频", "学习助手"],
        owner="learning-team",
        triggers=["video_frame_selected", "subtitle_highlighted", "note_requested"],
        versions={
            "1.0.0": SkillVersion(
                version="1.0.0",
                spec=spec_v1,
                status=SkillStatus.PRODUCTION,
                changelog="初始版本",
                owner="learning-team",
                created_at=now,
            ),
            "2.0.0": SkillVersion(
                version="2.0.0",
                spec=spec_v2,
                status=SkillStatus.STAGING,
                changelog="新增多轮上下文理解，优化 Vinci 调用策略",
                owner="learning-team",
                created_at=now,
            ),
        },
        active_version="1.0.0",
        rollout_strategy=RolloutStrategy.FULL,
        canary_percent=10.0,
    )


def _default_search_skill() -> SkillMetadata:
    """内置语义搜索技能 v1。"""

    spec = SkillSpec(
        system_prompt=(
            "你是一个语义搜索助手，帮助用户在视频字幕库中找到最相关的内容。\n"
            "你的职责是：\n"
            "1. 理解用户的自然语言查询\n"
            "2. 识别查询中的关键概念和意图\n"
            "3. 协助生成高质量的向量检索 query\n"
            "约束：\n"
            "- 查询长度不超过 200 字\n"
            "- 始终使用中文\n"
            "- 如果查询无意义，返回空列表"
        ),
        few_shot_examples=[],
        max_tokens=512,
        temperature=0.3,
        banned_topics=[],
        timeout_seconds=10.0,
        allowed_tools=[],
    )

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return SkillMetadata(
        id="semantic_search",
        name="语义搜索助手",
        description="视频字幕语义搜索的查询理解和结果排序辅助",
        category="search",
        tags=["搜索", "语义", "向量检索"],
        owner="search-team",
        triggers=["user_query_submitted"],
        versions={
            "1.0.0": SkillVersion(
                version="1.0.0",
                spec=spec,
                status=SkillStatus.PRODUCTION,
                changelog="初始版本",
                owner="search-team",
                created_at=now,
            ),
        },
        active_version="1.0.0",
        rollout_strategy=RolloutStrategy.FULL,
        canary_percent=10.0,
    )


# ---------------------------------------------------------------
# Skill Registry
# ---------------------------------------------------------------


@dataclass
class RegistryConfig:
    """注册中心配置。"""

    # skills.yaml 配置文件路径（None=使用内置默认）
    config_path: Optional[str] = None
    # 每次 get 时是否检查文件更新（文件系统变更检测）
    hot_reload: bool = True
    # 灰度随机种子（None=使用 random.random）
    canary_random: Optional[float] = None


class SkillRegistry:
    """
    全局技能注册中心。

    特性：
    - 内置默认技能（learning_flow, semantic_search）
    - 支持 YAML/JSON 配置文件扩展
    - 热更新：配置变更后自动重新加载（fs mtime 检测）
    - 灰度发布：按 canary_percent 随机路由
    - 线程安全
    """

    def __init__(self, config: Optional[RegistryConfig] = None):
        self._config = config or RegistryConfig()
        self._skills: Dict[str, SkillMetadata] = {}
        self._loaded_at: float = 0.0
        self._lock = threading.RLock()
        self._ensure_loaded()

    # ---- public API ----

    def get_active(self, skill_id: str) -> Optional[Skill]:
        """获取激活版本的 Skill 对象（含片段缓存）。"""
        self._ensure_loaded()
        with self._lock:
            meta = self._skills.get(skill_id)
            if meta is None:
                return None
            return self._build_skill(meta)

    def get_metadata(self, skill_id: str) -> Optional[SkillMetadata]:
        """仅获取元数据（不构建片段缓存）。"""
        self._ensure_loaded()
        with self._lock:
            return self._skills.get(skill_id)

    def list_skills(self) -> List[Dict[str, Any]]:
        """列出所有注册的技能（含元数据摘要）。"""
        self._ensure_loaded()
        with self._lock:
            result = []
            for meta in self._skills.values():
                result.append(self._summarize(meta))
            return result

    def get_segments(self, skill_id: str) -> List[PromptSegment]:
        """获取技能激活版本的提示词片段列表。"""
        skill = self.get_active(skill_id)
        if skill is None:
            return []
        return skill.get_segments()

    def get_version(self, skill_id: str) -> Optional[str]:
        """获取技能当前激活的版本号。"""
        self._ensure_loaded()
        with self._lock:
            meta = self._skills.get(skill_id)
            if meta is None:
                return None
            return meta.active_version

    def activate_version(self, skill_id: str, version: str) -> bool:
        """
        切换技能激活版本（无需重启，立即生效）。

        Returns:
            True=切换成功，False=版本不存在或状态不允许
        """
        self._ensure_loaded()
        with self._lock:
            meta = self._skills.get(skill_id)
            if meta is None:
                return False
            ver = meta.versions.get(version)
            if ver is None:
                logger.warning("skill %s: version %s not found", skill_id, version)
                return False
            if ver.status not in (SkillStatus.STAGING, SkillStatus.PRODUCTION):
                logger.warning(
                    "skill %s: version %s status=%s, cannot activate",
                    skill_id,
                    version,
                    ver.status.value,
                )
                return False
            meta.active_version = version
            logger.info(
                "skill %s activated version %s (was %s)",
                skill_id,
                version,
                meta.active_version,
            )
            return True

    def get_active_for_request(self, skill_id: str, user_id_hash: Optional[str | int] = None) -> Optional[Skill]:
        """
        获取技能（考虑灰度发布）。

        若 rollout_strategy == CANARY，则根据 user_id_hash 的哈希值决定是否路由到 staging 版本。
        这样同一用户多次请求会路由到同一版本（稳定性），不同用户按比例分流。
        """
        self._ensure_loaded()
        with self._lock:
            meta = self._skills.get(skill_id)
            if meta is None:
                return None

            # FULL 或无 staging 版本 → 直接返回当前激活版本
            if meta.rollout_strategy != RolloutStrategy.CANARY:
                return self._build_skill(meta)

            # 寻找 staging 版本
            staging_ver = None
            for vid, v in meta.versions.items():
                if v.status == SkillStatus.STAGING:
                    staging_ver = vid
                    break
            if staging_ver is None:
                return self._build_skill(meta)

            # 基于 user_id_hash 的确定性灰度：
            # 同一用户多次请求保持稳定路由；无 user_id_hash 时退化为随机。
            if user_id_hash is not None:
                import hashlib

                h = int(hashlib.md5(str(user_id_hash).encode()).hexdigest(), 16)
                canary_seed = (h % 10000) / 10000.0
            else:
                canary_seed = self._config.canary_random
                if canary_seed is None:
                    canary_seed = random.random()

            threshold = meta.canary_percent / 100.0
            if canary_seed < threshold:
                # 路由到 staging 版本
                meta.active_version = staging_ver
                logger.debug(
                    "skill %s canary routed to staging version %s (seed=%.3f < %.3f)",
                    skill_id,
                    staging_ver,
                    canary_seed,
                    threshold,
                )
            else:
                # 回退到 production 版本
                for vid, v in meta.versions.items():
                    if v.status == SkillStatus.PRODUCTION:
                        meta.active_version = vid
                        break

            return self._build_skill(meta)

    def reload(self) -> int:
        """
        强制重新加载配置。

        Returns:
            加载的技能数量
        """
        with self._lock:
            self._skills.clear()
            self._ensure_loaded_unlocked()
            return len(self._skills)

    # ---- internal helpers ----

    def _ensure_loaded(self) -> None:
        with self._lock:
            self._ensure_loaded_unlocked()

    def _ensure_loaded_unlocked(self) -> None:
        if self._skills:
            if not self._config.hot_reload:
                return
            # 检查配置文件 mtime
            config_path = self._resolve_config_path()
            if config_path and config_path.exists():
                mtime = config_path.stat().st_mtime
                if mtime <= self._loaded_at:
                    return
            else:
                # 无配置文件 + 已有内置技能，无需重新加载
                if self._skills:
                    return

        self._do_load()

    def _do_load(self) -> None:
        """执行实际加载逻辑（必须在持有锁时调用）。"""
        import time

        self._skills.clear()

        # 加载内置默认技能
        for default_meta_fn in (_default_learning_flow_skill, _default_search_skill):
            meta = default_meta_fn()
            self._skills[meta.id] = meta

        # 尝试加载外部配置
        config_path = self._resolve_config_path()
        if config_path and config_path.exists():
            try:
                loaded = self._load_from_file(config_path)
                for meta in loaded:
                    self._skills[meta.id] = meta
                logger.info("skill registry loaded %d skills from %s", len(loaded), config_path)
            except Exception:
                logger.exception("failed to load skills from %s, using defaults", config_path)

        self._loaded_at = time.time()

    def _resolve_config_path(self) -> Optional[Path]:
        if self._config.config_path:
            return Path(self._config.config_path)
        # 默认查找 skills.yaml / skills.json
        base = Path(__file__).resolve().parents[2]
        for name in ("skills.yaml", "skills.json"):
            p = base / name
            if p.exists():
                return p
        return None

    def _load_from_file(self, path: Path) -> List[SkillMetadata]:
        """从 YAML/JSON 文件加载技能定义。"""
        raw = path.read_text(encoding="utf-8")
        if path.suffix in (".yaml", ".yml"):
            try:
                import yaml

                data = yaml.safe_load(raw)
            except Exception:
                logger.warning("yaml parse failed for %s, treating as json", path)
                data = json.loads(raw)
        else:
            data = json.loads(raw)

        skills = []
        items = data if isinstance(data, list) else data.get("skills", [])
        for item in items:
            meta = self._parse_skill_item(item)
            if meta:
                skills.append(meta)
        return skills

    def _parse_skill_item(self, item: Dict[str, Any]) -> Optional[SkillMetadata]:
        """解析单个技能定义 dict。"""
        try:
            meta = SkillMetadata(
                id=str(item["id"]),
                name=str(item.get("name", item["id"])),
                description=str(item.get("description", "")),
                category=str(item.get("category", "")),
                tags=list(item.get("tags", [])),
                owner=str(item.get("owner", "")),
                triggers=list(item.get("triggers", [])),
                versions={},
                active_version=item.get("active_version"),
                rollout_strategy=RolloutStrategy(item.get("rollout_strategy", "full").lower()),
                canary_percent=float(item.get("canary_percent", 10.0)),
            )

            for vid, vdata in item.get("versions", {}).items():
                spec_data = vdata.get("spec", {})
                spec = SkillSpec(
                    system_prompt=str(spec_data.get("system_prompt", "")),
                    few_shot_examples=list(spec_data.get("few_shot_examples", [])),
                    max_tokens=int(spec_data.get("max_tokens", 2048)),
                    temperature=float(spec_data.get("temperature", 0.7)),
                    top_p=float(spec_data.get("top_p")) if spec_data.get("top_p") else None,
                    banned_topics=list(spec_data.get("banned_topics", [])),
                    timeout_seconds=float(spec_data.get("timeout_seconds", 30.0)),
                    allowed_tools=list(spec_data.get("allowed_tools", [])),
                )
                meta.versions[vid] = SkillVersion(
                    version=vid,
                    spec=spec,
                    status=SkillStatus(vdata.get("status", "draft").lower()),
                    deprecated_at=vdata.get("deprecated_at"),
                    changelog=str(vdata.get("changelog", "")),
                    owner=str(vdata.get("owner", "")),
                    created_at=vdata.get("created_at", ""),
                )

            if not meta.active_version and meta.versions:
                # 默认激活最高版本的 production 状态版本
                for vid, v in meta.versions.items():
                    if v.status == SkillStatus.PRODUCTION:
                        meta.active_version = vid
                        break

            return meta
        except Exception as e:
            logger.warning("failed to parse skill item %s: %s", item.get("id", "?"), e)
            return None

    def _build_skill(self, meta: SkillMetadata) -> Skill:
        """根据元数据构建 Skill（含片段缓存）。"""
        import time

        ver_id = meta.active_version
        if ver_id and ver_id in meta.versions:
            ver = meta.versions[ver_id]
        else:
            ver = next(iter(meta.versions.values()), None)

        if ver is None:
            return Skill(metadata=meta, _segments_cache=[])

        spec = ver.spec
        segments: List[PromptSegment] = []

        if spec.system_prompt:
            segments.append(
                PromptSegment(
                    name=f"{meta.id}.system",
                    content=spec.system_prompt,
                    priority=100,
                )
            )

        if spec.few_shot_examples:
            few_shot_text = "【示例对话】\n" + "\n".join(
                f"[{ex['role']}] {ex['content']}" for ex in spec.few_shot_examples
            )
            segments.append(
                PromptSegment(
                    name=f"{meta.id}.fewshot",
                    content=few_shot_text,
                    priority=90,
                )
            )

        # 约束片段
        constraints = []
        if spec.max_tokens:
            constraints.append(f"- 最大输出长度：{spec.max_tokens} tokens")
        if spec.temperature:
            constraints.append(f"- 温度参数：{spec.temperature}")
        if spec.banned_topics:
            constraints.append(f"- 禁止话题：{', '.join(spec.banned_topics)}")
        if constraints:
            segments.append(
                PromptSegment(
                    name=f"{meta.id}.constraints",
                    content="【约束】\n" + "\n".join(constraints),
                    priority=80,
                )
            )

        return Skill(
            metadata=meta,
            _segments_cache=segments,
            _loaded_at=time.time(),
        )

    @staticmethod
    def _summarize(meta: SkillMetadata) -> Dict[str, Any]:
        return {
            "id": meta.id,
            "name": meta.name,
            "category": meta.category,
            "active_version": meta.active_version,
            "versions": {
                vid: {
                    "status": v.status.value,
                    "owner": v.owner,
                    "created_at": v.created_at,
                }
                for vid, v in meta.versions.items()
            },
            "rollout_strategy": meta.rollout_strategy.value,
            "canary_percent": meta.canary_percent,
            "triggers": meta.triggers,
        }


# ---------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------

_registry: Optional[SkillRegistry] = None
_registry_lock = threading.Lock()


def get_skill_registry() -> SkillRegistry:
    """获取全局技能注册中心单例。"""
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = SkillRegistry()
    return _registry


def reset_registry_for_tests() -> None:
    """测试用：重置全局注册中心。"""
    global _registry
    with _registry_lock:
        _registry = None
