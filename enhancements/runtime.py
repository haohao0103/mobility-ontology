"""EnhancedSystem：FreightSystem 的增强包装（组合而非修改，破坏面为零）。

统一挂载：
- ext: ExtensionRegistry（本体扩展层）
- properties: PropertyRegistry
- interfaces: InterfaceRegistry
- security: SecurityEngine
- functions: FunctionRegistry
- usage: UsageGraph
- store: FactStore（读连接池化后）
"""
from __future__ import annotations

from freight.engine import FreightSystem
from enhancements.registry import ExtensionRegistry
from enhancements.property_registry import PropertyRegistry
from enhancements.interfaces import InterfaceRegistry
from enhancements.security import SecurityEngine, SecurityContext
from enhancements.function_registry import FunctionRegistry
from enhancements.usage_graph import UsageGraph
from enhancements.search import search_objects


class EnhancedSystem:
    def __init__(self, system: FreightSystem):
        self.system = system
        self.ext = ExtensionRegistry(base_classes=system.sem.classes,
                                  base_property_types=system.sem.raw.get("property_types"))
        self.properties = PropertyRegistry(self.ext)
        self.interfaces = InterfaceRegistry(self.ext)
        self.security = SecurityEngine(self.ext)
        self.functions = FunctionRegistry(self.ext, self.security, self.interfaces)
        self.usage = UsageGraph(self.ext, system.sem.raw)
        self.store = system.facts.store

    # 委托既有能力
    def get_object(self, object_id: str):
        return self.system.get_object(object_id)

    # -------------------------------------------------- 增强能力门面
    def secure_get_object(self, object_id: str, ctx: SecurityContext) -> dict:
        """安全读对象：行级过滤 + 列级脱敏（三层按本体 JSON 策略执行）。"""
        row = self.store.one("SELECT * FROM objects WHERE object_id=?", (object_id,))
        if row is None:
            raise LookupError(f"对象不存在: {object_id}")
        import json as _json
        props = _json.loads(row["props"])
        if not self.security.visible_object(row["type"], props, ctx):
            from enhancements.security import AccessDenied
            raise AccessDenied(
                f"对象 {object_id} 被行级安全策略过滤（用户 {ctx.user} 无所需标记）",
                code="ROW_LEVEL_DENIED",
            )
        if ctx.scope_token:
            self.security.check_scope(ctx, "class", row["type"])
        masked = self.security.mask_properties(row["type"], props, ctx)
        return {
            "object_id": row["object_id"],
            "type": row["type"],
            "name": row["name"],
            "status": row["status"],
            "props": masked,
        }

    def search(self, q=None, types=None, status=None, property_filter=None,
               limit=20, offset=0) -> dict:
        return search_objects(
            self.store, self.ext, q=q, types=types, status=status,
            property_filter=property_filter, limit=limit, offset=offset,
        )

    def call_function(self, function_id: str, args: dict, ctx: SecurityContext | None = None):
        return self.functions.call(function_id, args, runtime=self, ctx=ctx)
