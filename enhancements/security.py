"""P3 安全层：对标 Palantir 强制 + 自主两层访问控制。

模型（对照 Palantir 官方文档 access-control-propagation / object-and-property-policies）：

强制控制（Mandatory）——角色再高也不豁免：
- markings：属性/行携带标记，用户必须是标记成员才能读。
  Palantir 语义："A user must be a member of all markings applied to a resource"。

自主控制（Discretionary）——读时过滤：
- object_security_policies（行级）：行匹配 row_filter 时要求 required_marking；
- property_security_policies（列级）：无标记用户读到 null（对应 Palantir 的
  "they will see a null value in place of the property value"，而非报错）。

Scope Token（对标 Scoped Tokens + model_tools 门禁）：
- agent 令牌携带可见类/函数/MCP 工具子集，服务端按令牌过滤工具面。

全部策略定义在 ontology_extension.json：加/删一条策略 -> 行为即变（承重验收）。
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SecurityContext:
    """请求方安全上下文。markings = 用户加入的标记成员组。"""
    user: str = "anonymous"
    roles: set[str] = field(default_factory=set)
    markings: set[str] = field(default_factory=set)
    scope_token: dict | None = None

    @classmethod
    def with_roles(cls, user: str, roles: set[str]) -> "SecurityContext":
        return cls(user=user, roles=roles, markings=set(roles))

    def has_marking(self, marking_id: str) -> bool:
        return marking_id in self.markings


class AccessDenied(Exception):
    """强制控制拒绝（对象整体不可见或工具越权）。"""

    def __init__(self, message: str, code: str = "MARKING_DENIED"):
        super().__init__(message)
        self.code = code


class SecurityEngine:
    def __init__(self, ext):
        self.ext = ext

    # -------------------------------------------------- 行级（对象实例过滤）
    def visible_object(self, class_id: str, props: dict, ctx: SecurityContext) -> bool:
        for pol in self.ext.object_security_policies:
            if pol.get("object_type") != class_id:
                continue
            flt = pol.get("row_filter", {})
            prop = flt.get("property")
            if prop and props.get(prop) == flt.get("equals"):
                required = pol.get("required_marking")
                if required and not ctx.has_marking(required):
                    return False  # 行命中过滤且无标记 -> 该行不可见
        return True

    def filter_objects(self, class_id: str, rows: list[dict], ctx: SecurityContext) -> list[dict]:
        return [r for r in rows if self.visible_object(class_id, r, ctx)]

    # -------------------------------------------------- 列级（属性脱敏）
    def mask_properties(self, class_id: str, props: dict, ctx: SecurityContext) -> dict:
        masked = dict(props)
        # 属性注册表 marking（强制控制，属性级）
        for name, val in list(masked.items()):
            pt = self.ext.property_types.get(name)
            if pt and pt.get("marking") and not ctx.has_marking(pt["marking"]):
                masked[name] = None  # Palantir 语义：null 而非报错
        # 列级策略（psp，读时过滤）
        for pol in self.ext.property_security_policies:
            required = pol.get("required_marking")
            if required and not ctx.has_marking(required):
                for p in pol.get("properties", []):
                    if p in masked:
                        masked[p] = None
        return masked

    # -------------------------------------------------- Scope Token（工具面过滤）
    def check_scope(self, ctx: SecurityContext, kind: str, target: str) -> None:
        """agent 令牌越权检查：kind in {class, function, mcp_tool}。"""
        token = ctx.scope_token
        if not token:
            return  # 非令牌上下文（人类用户）不走 scope 检查
        allowed = token.get({"class": "classes", "function": "functions",
                             "mcp_tool": "mcp_tools"}[kind], [])
        if target not in allowed:
            raise AccessDenied(
                f"scope token {token.get('id')} 不包含 {kind}:{target}",
                code="TOOL_NOT_IN_SCOPE",
            )

    def allowed_tools(self, ctx: SecurityContext, all_tools: list[str]) -> list[str]:
        if not ctx.scope_token:
            return list(all_tools)
        allowed = set(ctx.scope_token.get("mcp_tools", []))
        return [t for t in all_tools if t in allowed]

    # -------------------------------------------------- 策略测试器（对标 Test policies）
    def simulate(self, class_id: str, rows: list[dict], ctx: SecurityContext) -> dict:
        """模拟给定用户视角：可见对象数 + 属性可见性矩阵（Palantir Test policies 模式）。"""
        visible = self.filter_objects(class_id, rows, ctx)
        prop_names: list[str] = []
        cls = self.ext.base_classes.get(class_id, {})
        prop_names = cls.get("properties", [])
        matrix: dict[str, dict[str, bool]] = {}
        for r in visible:
            masked = self.mask_properties(class_id, r, ctx)
            matrix[r.get("object_id", "?")] = {
                p: masked.get(p) is not None for p in prop_names
            }
        return {
            "user": ctx.user,
            "class": class_id,
            "total_rows": len(rows),
            "visible_rows": len(visible),
            "property_visibility": matrix,
        }
