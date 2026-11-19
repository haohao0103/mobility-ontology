"""画像 AI 语义层：HugeGraph PD 多租户 + 本体语义校验 + Agent 工具服务。

分层：
  semantic_layer_profile.json  画像资产本体（8 类顶点 / 10 类时态边 / 状态机 / 约束）
  profile_ai.semantic          语义校验（ID 规范、定义域值域、时态、状态迁移）
  profile_ai.hugegraph         HugeGraph 1.7 PD 模式原生 REST 客户端（纯标准库）
  profile_ai.audit             写操作门禁：提议 / 人工确认 / 审计留痕
  profile_ai.service           6 个 Agent 读工具 + propose / confirm 写门禁
  profile_ai.api               FastAPI 路由（挂载在 /profile）
"""
from .semantic import ProfileSemanticLayer, has_errors
from .hugegraph import HugeGraphClient, HugeGraphError
from .audit import AuditStore
from .service import ProfileService, ProfileError, AssetNotFound

__all__ = [
    "ProfileSemanticLayer", "has_errors",
    "HugeGraphClient", "HugeGraphError", "AuditStore",
    "ProfileService", "ProfileError", "AssetNotFound",
]