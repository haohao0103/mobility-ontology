"""P3 安全层：对标 Palantir 强制 + 自主两层访问控制 + marking 传播。

模型（对照 Palantir 官方文档 access-control-propagation / object-and-property-policies）：

强制控制（Mandatory）——角色再高也不豁免：
- markings：属性/行/对象携带标记，用户必须是**全部**标记的成员才能读。
  Palantir 语义："A user must be a member of all markings applied to a resource"。
- **传播继承**：对象的有效标记 = 类级标记 ∪ 行内受标记属性的标记 ∪ 数据源标记。
  数据源（data_source_markings）一旦打标，该类所有对象都继承——这正是
  「标记沿数据流传播」的实现点。少了它，marking 只是贴在列上的标签，
  换一个入口（另一张表、另一个 API）就能绕过。

自主控制（Discretionary）——读时过滤：
- object_security_policies（行级）：行匹配 row_filter 时要求 required_marking；
- property_security_policies（列级）：无标记用户读到 null（对应 Palantir 的
  "they will see a null value in place of the property value"，而非报错）。

Scope Token（对标 Scoped Tokens + model_tools 门禁）：
- agent 令牌携带可见类/函数/MCP 工具子集，服务端按令牌过滤工具面；
- 令牌自身带 markings，Agent 输出不得高于令牌权限（`guard_output`）。

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
    def __init__(self, ext, data_sources: list[dict] | None = None):
        self.ext = ext
        # 类 -> 数据源系统集合；用于数据源标记传播（继承链的源头）
        self.systems_of_class: dict[str, set[str]] = {}
        for ds in data_sources or []:
            if ds.get("class") and ds.get("system"):
                self.systems_of_class.setdefault(ds["class"], set()).add(ds["system"])

    # -------------------------------------------------- marking 传播（继承链）
    def marking_trace(self, class_id: str, props: dict) -> list[dict]:
        """对象**行级**有效标记的来源明细（Palantir 式传播可追溯）。

        两个来源，取并集：
          1. 数据源：类映射到的外部系统被打标 -> 该类所有对象继承；
          2. 类级：直接挂在对象类型上的标记。

        **属性标记不参与行级判定**。属性 marking 管的是「这一列读不读得到」
        （`mask_properties` 里置 null），不是「整行看不看得见」。把列标记提升成
        行不可见是过度管控：一个运单因为带了运费字段就整条消失，业务上等于
        「看不见运单」，而不是「看不见运费」。
        """
        trace: list[dict] = []
        for rule in self.ext.data_source_markings:
            system = rule.get("system")
            if system and system in self.systems_of_class.get(class_id, set()):
                trace.append({"marking": rule["marking"], "origin": "data_source",
                              "source": system})
        for rule in self.ext.class_markings:
            if rule.get("object_type") == class_id:
                trace.append({"marking": rule["marking"], "origin": "class",
                              "source": class_id})
        return trace

    def property_markings(self, props: dict) -> dict[str, str]:
        """列级标记清单（属性 -> 标记），供 mask_properties / 审计使用。"""
        out: dict[str, str] = {}
        for prop in props:
            pt = self.ext.property_types.get(prop)
            if pt and pt.get("marking"):
                out[prop] = pt["marking"]
        return out

    def effective_markings(self, class_id: str, props: dict) -> set[str]:
        return {t["marking"] for t in self.marking_trace(class_id, props)}

    def _all_markings_held(self, required: set[str], ctx: SecurityContext) -> bool:
        """Palantir 语义：必须是**全部**标记的成员，不是任一。"""
        return required <= ctx.markings

    # -------------------------------------------------- 行级（对象实例过滤）
    def visible_object(self, class_id: str, props: dict, ctx: SecurityContext) -> bool:
        # 传播继承的强制控制先于行级策略：任一有效标记不持有 -> 整行不可见
        if not self._all_markings_held(self.effective_markings(class_id, props), ctx):
            return False
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

    def deny_reason(self, class_id: str, props: dict, ctx: SecurityContext) -> dict | None:
        """拒绝原因（缺哪些标记、来自哪里），便于前端/Agent 解释而不是干瞪眼。"""
        missing = self.effective_markings(class_id, props) - ctx.markings
        if not missing:
            return None
        origins = [t for t in self.marking_trace(class_id, props)
                   if t["marking"] in missing]
        return {"code": "MARKING_DENIED", "missing": sorted(missing),
                "origins": origins}

    def filter_objects(self, class_id: str, rows: list[dict], ctx: SecurityContext) -> list[dict]:
        return [r for r in rows if self.visible_object(class_id, r, ctx)]

    # -------------------------------------------------- 列级（属性脱敏）
    def mask_properties(self, class_id: str, props: dict, ctx: SecurityContext) -> dict:
        masked = dict(props)
        # 属性注册表 marking（强制控制，属性级）：无标记 -> null，不报错
        for name, marking in self.property_markings(props).items():
            if not ctx.has_marking(marking):
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

    # -------------------------------------------------- Agent 输出防外泄
    def guard_output(self, class_id: str, props: dict, ctx: SecurityContext) -> dict:
        """Agent 输出前的最后一道闸：不得导出高于会话权限的内容。

        只靠读侧过滤不够——Agent 可能把对象塞进生成文本、摘要或函数返回值里，
        绕过 mask_properties。这里对**输出结构**再做一次强制判定：
        有效标记超出上下文 -> 整段拒绝（不是脱敏，因为自由文本脱不干净）。
        """
        reason = self.deny_reason(class_id, props, ctx)
        if reason is not None:
            raise AccessDenied(
                f"对象类型 {class_id} 的有效标记 {reason['missing']} 超出现有权限，"
                f"不可导出到 Agent 输出", code="OUTPUT_MARKING_DENIED")
        return self.mask_properties(class_id, props, ctx)

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
