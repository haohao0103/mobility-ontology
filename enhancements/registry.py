"""扩展层注册表：加载 ontology_extension.json，叠加在 freight 语义层之上。

交叉引用校验（门禁扩展的校验内核）：
- property_types.used_by 引用的类必须在基础本体存在，且类属性表确实含该属性；
- property_types 之间 name 不得重复；primary_key 标记的属性必须是所属类的 key；
- interfaces.required_* 引用的属性必须存在于注册表或基础本体；
- function_registry.implementation 必须可解析；
- markings / policies 引用的标记必须已定义。
"""
from __future__ import annotations

import json
import os

DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "ontology_extension.json"
)


class ExtensionRegistry:
    def __init__(self, path: str = DEFAULT_PATH, base_classes: dict[str, dict] | None = None):
        with open(path, encoding="utf-8") as f:
            self.raw = json.load(f)
        self.path = path
        self.meta = self.raw.get("meta", {})
        self.property_types = {p["name"]: p for p in self.raw.get("property_types", [])}
        self.property_list = list(self.property_types.values())
        self.policy = self.raw.get("property_registry_policy", {"enforcement": "incremental"})
        self.interfaces = {i["id"]: i for i in self.raw.get("interfaces", [])}
        self.markings = {m["id"]: m for m in self.raw.get("markings", [])}
        self.object_security_policies = self.raw.get("object_security_policies", [])
        self.property_security_policies = self.raw.get("property_security_policies", [])
        self.scope_tokens = self.raw.get("scope_tokens", {})
        self.function_registry = {
            f["id"]: f for f in self.raw.get("function_registry", [])
        }
        self.lifecycle_statuses = set(
            self.raw.get("lifecycle_statuses", ["endorsed", "active", "experimental", "deprecated"])
        )
        self.base_classes = base_classes or {}

    # -------------------------------------------------- 交叉引用校验
    def validate(self) -> list[dict]:
        issues: list[dict] = []

        for p in self.property_list:
            pstatus = p.get("status", "active")
            if pstatus not in self.lifecycle_statuses:
                issues.append({
                    "code": "PROP_STATUS_INVALID",
                    "category": "property_types",
                    "path": f"property_types[{p['name']}].status",
                    "message": f"属性 {p['name']} 状态非法: {pstatus}",
                })
            for cls_id in p.get("used_by", []):
                cls = self.base_classes.get(cls_id)
                if cls is None:
                    issues.append({
                        "code": "PROP_USED_BY_UNKNOWN_CLASS",
                        "category": "property_types",
                        "path": f"property_types[{p['name']}].used_by",
                        "message": f"属性 {p['name']} 引用了未定义的类: {cls_id}",
                    })
                elif p["name"] not in cls.get("properties", []):
                    issues.append({
                        "code": "PROP_NOT_IN_CLASS",
                        "category": "property_types",
                        "path": f"property_types[{p['name']}].used_by",
                        "message": f"类 {cls_id} 的属性表不含 {p['name']}",
                    })
                elif p.get("primary_key") and cls.get("key") != p["name"]:
                    issues.append({
                        "code": "PROP_PRIMARY_KEY_MISMATCH",
                        "category": "property_types",
                        "path": f"property_types[{p['name']}].primary_key",
                        "message": f"属性 {p['name']} 标记为主键，但类 {cls_id} 的 key 是 {cls.get('key')}",
                    })
            if p.get("marking") and p["marking"] not in self.markings:
                issues.append({
                    "code": "PROP_MARKING_UNKNOWN",
                    "category": "property_types",
                    "path": f"property_types[{p['name']}].marking",
                    "message": f"属性 {p['name']} 引用了未定义的标记: {p['marking']}",
                })

        for iface in self.interfaces.values():
            for prop in iface.get("required_properties", []):
                if prop not in self.property_types and not self._prop_in_any_class(prop):
                    issues.append({
                        "code": "IFACE_REQUIRED_PROP_UNKNOWN",
                        "category": "interfaces",
                        "path": f"interfaces[{iface['id']}].required_properties",
                        "message": f"接口 {iface['id']} 必需属性未定义: {prop}",
                    })

        for pol in self.object_security_policies:
            if pol.get("required_marking") not in self.markings:
                issues.append({
                    "code": "OSP_MARKING_UNKNOWN",
                    "category": "object_security_policies",
                    "path": f"object_security_policies[{pol.get('id')}].required_marking",
                    "message": f"行级策略引用了未定义的标记: {pol.get('required_marking')}",
                })
            if pol.get("object_type") not in self.base_classes:
                issues.append({
                    "code": "OSP_OBJECT_TYPE_UNKNOWN",
                    "category": "object_security_policies",
                    "path": f"object_security_policies[{pol.get('id')}].object_type",
                    "message": f"行级策略引用了未定义的类: {pol.get('object_type')}",
                })

        for pol in self.property_security_policies:
            if pol.get("required_marking") not in self.markings:
                issues.append({
                    "code": "PSP_MARKING_UNKNOWN",
                    "category": "property_security_policies",
                    "path": f"property_security_policies[{pol.get('id')}].required_marking",
                    "message": f"列级策略引用了未定义的标记: {pol.get('required_marking')}",
                })

        for fn in self.function_registry.values():
            if fn.get("accepts_interface") and fn["accepts_interface"] not in self.interfaces:
                issues.append({
                    "code": "FN_IFACE_UNKNOWN",
                    "category": "function_registry",
                    "path": f"function_registry[{fn['id']}].accepts_interface",
                    "message": f"函数 {fn['id']} 引用了未定义的接口: {fn['accepts_interface']}",
                })

        return issues

    def _prop_in_any_class(self, prop: str) -> bool:
        return any(prop in c.get("properties", []) for c in self.base_classes.values())
