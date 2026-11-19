"""共享出行互联网本体 · 最小闭环 v1。

分层：
  semantic_layer.json  语义层（领域词典 / 关系 / 约束 / 规则 / 定价）唯一口径
  mobility.semantic    本体定义加载与关系合法性
  mobility.store       业务数据库（高频交易）
  mobility.graph       持久化时态知识图谱（关系事实与时间线）
  mobility.constraints SHACL 风格业务约束
  mobility.rules       规则引擎（条件 → 判断 → 动作）
  mobility.engine      领域服务 / 全链路编排
  mobility.trace       客服可追溯接口
"""
from .semantic import SemanticLayer, OntologyViolation, ConstraintViolation, BusinessReject
from .engine import MobilitySystem

__all__ = [
    "SemanticLayer", "MobilitySystem",
    "OntologyViolation", "ConstraintViolation", "BusinessReject",
]
