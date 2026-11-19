"""P4-4 使用度量：类引用图与删除前依赖检查（对标 Palantir 使用度量 / 影响分析）。

从本体 JSON（基础层 + 扩展层）构建反向引用图：
- 类 <- 关系 domain/range
- 类 <- 函数 target / 接口实现
- 类 <- 动作
- 类 <- 属性注册表 used_by
- 类 <- 行级安全策略 object_type

承重验收：删类前查 dependents -> 非空则门禁拦截（引用完整性）。
全部从 JSON 现算，本体变更 -> 引用图即变，代码零改动。
"""
from __future__ import annotations


class UsageGraph:
    def __init__(self, ext, base_semantic_raw: dict | None = None):
        self.ext = ext
        self.raw = base_semantic_raw or {}

    def dependents(self, class_id: str) -> list[dict]:
        """谁引用了这个类（删除/废弃前的依赖检查）。"""
        deps: list[dict] = []
        for rel in self.raw.get("relations", []):
            if rel.get("domain") == class_id or rel.get("range") == class_id:
                deps.append({"kind": "relation", "id": rel["id"]})
        for fn in self.raw.get("functions", []):
            if fn.get("target") == class_id:
                deps.append({"kind": "function", "id": fn["id"]})
        for act in self.raw.get("actions", []):
            targets = act.get("targets") or ([act["target"]] if act.get("target") else [])
            if class_id in targets:
                deps.append({"kind": "action", "id": act["id"]})
        for p in self.ext.property_list:
            if class_id in p.get("used_by", []):
                deps.append({"kind": "property_type", "id": p["name"]})
        for pol in self.ext.object_security_policies:
            if pol.get("object_type") == class_id:
                deps.append({"kind": "object_security_policy", "id": pol["id"]})
        for fn in self.ext.function_registry.values():
            iface = fn.get("accepts_interface")
            if iface:
                impls = self.ext.interfaces.get(iface, {})
                if impls.get("match") == "structural":
                    # 结构匹配是动态的，用反向判定
                    cls = self.ext.base_classes.get(class_id)
                    if cls is not None:
                        from enhancements.interfaces import InterfaceRegistry
                        if InterfaceRegistry(self.ext).conforms(class_id, iface):
                            deps.append({
                                "kind": "function_interface",
                                "id": fn["id"], "interface": iface,
                            })
        return deps

    def usage_summary(self) -> dict:
        """全类使用度量：每类被引用次数（找出零引用孤儿类与热点类）。"""
        counts: dict[str, int] = {}
        for cid in self.ext.base_classes:
            counts[cid] = len(self.dependents(cid))
        ranked = sorted(counts.items(), key=lambda kv: -kv[1])
        return {
            "total_classes": len(counts),
            "orphans": sorted([c for c, n in counts.items() if n == 0]),
            "top_referenced": [{"class": c, "refs": n} for c, n in ranked[:10]],
        }
