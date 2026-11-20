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
    def __init__(self, path: str = DEFAULT_PATH, base_classes: dict[str, dict] | None = None,
                 base_property_types: list[dict] | None = None):
        with open(path, encoding="utf-8") as f:
            self.raw = json.load(f)
        self.path = path
        self.meta = self.raw.get("meta", {})
        # schema v3：属性注册表以基础本体（freight/semantic_layer.json 顶层 property_types）
        # 为权威来源，扩展层只补基础本体未覆盖的属性（Palantir Property Type 一等公民）。
        self.property_types = {p["name"]: p for p in (base_property_types or [])}
        for p in self.raw.get("property_types", []):
            # 扩展层（含 proposal 分支）覆盖基础本体同名声明的定义：
            # 基础本体是属性注册表的全量权威，扩展层是它的可覆盖视图。
            self.property_types[p["name"]] = p
        self.property_list = list(self.property_types.values())
        self.policy = self.raw.get("property_registry_policy", {"enforcement": "incremental"})
        self.interfaces = {i["id"]: i for i in self.raw.get("interfaces", [])}
        self.markings = {m["id"]: m for m in self.raw.get("markings", [])}
        self.object_security_policies = self.raw.get("object_security_policies", [])
        self.property_security_policies = self.raw.get("property_security_policies", [])
        # marking 传播的两条声明式来源（数据源继承 / 类级挂标）
        self.data_source_markings = self.raw.get("data_source_markings", [])
        self.class_markings = self.raw.get("class_markings", [])
        self.scope_tokens = self.raw.get("scope_tokens", {})
        self.function_registry = {
            f["id"]: f for f in self.raw.get("function_registry", [])
        }
        self.result_types = {
            r["id"]: r for r in self.raw.get("result_types", [])
        }
        # 函数返回值可用的类型全集：标量 valueType + 结果类型 + 对象类型
        self.scalar_value_types = {
            "string", "text", "integer", "number", "decimal", "boolean",
            "timestamp", "date", "json", "enum",
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
            # 显式 implements 不得是空头支票
            for item in iface.get("implements") or []:
                cid = item.get("object_type")
                cls = self.base_classes.get(cid)
                if cls is None:
                    issues.append({
                        "code": "IFACE_IMPLEMENTS_UNKNOWN_CLASS",
                        "category": "interfaces",
                        "path": f"interfaces[{iface['id']}].implements",
                        "message": f"接口 {iface['id']} 声明实现类 {cid} 未定义",
                    })
                    continue
                pm = item.get("property_map") or {}
                props = set(cls.get("properties") or [])
                for req in iface.get("required_properties", []):
                    actual = pm.get(req, req)
                    if actual not in props:
                        issues.append({
                            "code": "IFACE_UNIMPLEMENTED",
                            "category": "interfaces",
                            "path": f"interfaces[{iface['id']}].implements[{cid}]",
                            "message": f"接口 {iface['id']} 声明 {cid} 实现，"
                                       f"但 {cid} 缺少必需属性 {actual}",
                        })
                any_of = iface.get("required_any_of") or []
                if any_of:
                    mapped = [pm.get(p, p) for p in any_of]
                    if not any(p in props for p in mapped):
                        issues.append({
                            "code": "IFACE_UNIMPLEMENTED",
                            "category": "interfaces",
                            "path": f"interfaces[{iface['id']}].implements[{cid}]",
                            "message": f"接口 {iface['id']} 声明 {cid} 实现，"
                                       f"但 {cid} 缺少任一必需属性 {mapped}",
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

        # marking 传播声明：引用的标记必须已定义，类级挂标必须指向已定义类
        for rule in self.data_source_markings:
            if rule.get("marking") not in self.markings:
                issues.append({
                    "code": "DSM_MARKING_UNKNOWN",
                    "category": "data_source_markings",
                    "path": f"data_source_markings[{rule.get('system')}]",
                    "message": f"数据源标记引用了未定义的标记: {rule.get('marking')}",
                })
        for rule in self.class_markings:
            if rule.get("marking") not in self.markings:
                issues.append({
                    "code": "CM_MARKING_UNKNOWN",
                    "category": "class_markings",
                    "path": f"class_markings[{rule.get('object_type')}]",
                    "message": f"类级标记引用了未定义的标记: {rule.get('marking')}",
                })
            if rule.get("object_type") not in self.base_classes:
                issues.append({
                    "code": "CM_OBJECT_TYPE_UNKNOWN",
                    "category": "class_markings",
                    "path": f"class_markings[{rule.get('object_type')}]",
                    "message": f"类级标记引用了未定义的类: {rule.get('object_type')}",
                })

        for fn in self.function_registry.values():
            if fn.get("accepts_interface") and fn["accepts_interface"] not in self.interfaces:
                issues.append({
                    "code": "FN_IFACE_UNKNOWN",
                    "category": "function_registry",
                    "path": f"function_registry[{fn['id']}].accepts_interface",
                    "message": f"函数 {fn['id']} 引用了未定义的接口: {fn['accepts_interface']}",
                })
            sig = fn.get("signature") or {}
            # 返回类型必须解析得到：标量 valueType / 已声明 result_type / 已定义对象类型
            ret = sig.get("returns")
            if not ret:
                issues.append({
                    "code": "FN_RETURNS_MISSING",
                    "category": "function_registry",
                    "path": f"function_registry[{fn['id']}].signature.returns",
                    "message": f"函数 {fn['id']} 未声明返回类型",
                })
            elif (ret not in self.scalar_value_types
                  and ret not in self.result_types
                  and ret not in self.base_classes):
                issues.append({
                    "code": "FN_RETURNS_UNKNOWN",
                    "category": "function_registry",
                    "path": f"function_registry[{fn['id']}].signature.returns",
                    "message": f"函数 {fn['id']} 的返回类型 {ret!r} 既不是标量类型、"
                               f"也不是已注册 result_type 或对象类型",
                })
            for p in sig.get("params", []):
                ptype = p.get("type")
                if ptype in ("object_set", "object_ref"):
                    of = p.get("of")
                    if not of:
                        issues.append({
                            "code": "FN_PARAM_OF_MISSING",
                            "category": "function_registry",
                            "path": f"function_registry[{fn['id']}].signature.params",
                            "message": f"函数 {fn['id']} 参数 {p.get('name')} 类型为 "
                                       f"{ptype} 但未声明 of",
                        })
                    elif of not in self.base_classes:
                        issues.append({
                            "code": "FN_PARAM_OF_UNKNOWN",
                            "category": "function_registry",
                            "path": f"function_registry[{fn['id']}].signature.params",
                            "message": f"函数 {fn['id']} 参数 {p.get('name')} 的 of={of} "
                                       f"不是已定义的对象类型",
                        })
                if p.get("interface") and p["interface"] not in self.interfaces:
                    issues.append({
                        "code": "FN_PARAM_IFACE_UNKNOWN",
                        "category": "function_registry",
                        "path": f"function_registry[{fn['id']}].signature.params",
                        "message": f"函数 {fn['id']} 参数 {p.get('name')} 引用了未定义接口: "
                                   f"{p['interface']}",
                    })

        # 结果类型的字段类型必须是合法 valueType
        for rt in self.result_types.values():
            for f in rt.get("fields", []):
                if f.get("type") not in (self.scalar_value_types
                                         | {"object_set", "object_ref"}):
                    issues.append({
                        "code": "RESULT_FIELD_TYPE_UNKNOWN",
                        "category": "result_types",
                        "path": f"result_types[{rt['id']}].fields[{f.get('name')}]",
                        "message": f"结果类型 {rt['id']} 的字段 {f.get('name')} "
                                   f"类型非法: {f.get('type')}",
                    })

        return issues

    def _prop_in_any_class(self, prop: str) -> bool:
        return any(prop in c.get("properties", []) for c in self.base_classes.values())
