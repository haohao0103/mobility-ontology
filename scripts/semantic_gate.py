"""本体语义门禁：CI 与本地演示共用。

校验工作副本、全部发布快照与快照 checksum；并自检错误规则确实会被拦截。
任何语义问题都会以非 0 退出码结束，阻止合并或发布。
"""
from __future__ import annotations

import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from freight.release import file_checksum, list_manifests, semantic_path  # noqa: E402
from freight.semantic import (SemanticLayer, validate_ontology_substance,  # noqa: E402
                             validate_semantic)

CURRENT = os.path.join(ROOT, "freight", "semantic_layer.json")
ROOT_LAYER = os.path.join(ROOT, "semantic_layer.json")
SCHEMA = os.path.join(ROOT, "schema", "semantic_layer.schema.json")
RULE_AST_SCHEMA = os.path.join(ROOT, "schema", "rule_ast.schema.json")
RULE_CASES_DIR = os.path.join(ROOT, "tests", "rules")
SHACL_CASES = os.path.join(ROOT, "tests", "shacl", "cases.json")
FREIGHT_BASE = "https://w3id.org/mobility-ontology/freight#"
MOBILITY_BASE = "https://w3id.org/mobility-ontology/core#"


def check_schema(path: str, label: str) -> dict:
    """按元模型 schema 校验结构与字段类型；跨引用完整性交给 validate_semantic。"""
    target = f"{label}#schema"
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        return {
            "target": target,
            "ok": False,
            "issues": [{
                "code": "SCHEMA_DEPENDENCY_MISSING",
                "path": target,
                "message": f"缺少 jsonschema，无法执行元模型校验: {exc}",
            }],
        }

    with open(SCHEMA, encoding="utf-8") as f:
        schema = json.load(f)
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(raw),
        key=lambda error: list(error.path),
    )
    issues = [{
        "code": "SCHEMA_INVALID",
        "path": "/".join(str(part) for part in error.path) or "$",
        "message": error.message,
    } for error in errors]
    return {
        "target": target,
        "version": raw.get("meta", {}).get("version"),
        "ok": not issues,
        "issues": issues,
    }


def check_rule_ast(path: str, label: str) -> dict:
    """G1-03：校验所有结构化规则 AST；不再允许自由字符串公式进入发布。"""
    target = f"{label}#rule_ast"
    try:
        from jsonschema import Draft202012Validator
    except ImportError as exc:
        return {
            "target": target,
            "ok": False,
            "issues": [{
                "code": "SCHEMA_DEPENDENCY_MISSING",
                "path": target,
                "message": f"缺少 jsonschema，无法执行规则 AST 校验: {exc}",
            }],
        }

    with open(RULE_AST_SCHEMA, encoding="utf-8") as f:
        schema = json.load(f)
    validator = Draft202012Validator(schema)
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)

    issues = []
    for rule_index, rule in enumerate(raw.get("rules") or []):
        judge = rule.get("judge") or {}
        nodes = []
        if judge.get("ast") is not None:
            nodes.append((
                f"rules/{rule_index}/judge/ast",
                judge["ast"],
            ))
        bindings = (judge.get("calc") or {}).get("bindings") or {}
        if isinstance(bindings, dict):
            for key, node in bindings.items():
                nodes.append((
                    f"rules/{rule_index}/judge/calc/bindings/{key}",
                    node,
                ))
        for node_path, node in nodes:
            for error in sorted(validator.iter_errors(node),
                                key=lambda item: list(item.path)):
                suffix = "/".join(str(part) for part in error.path)
                issues.append({
                    "code": "RULE_AST_SCHEMA_INVALID",
                    "path": f"{node_path}/{suffix}" if suffix else node_path,
                    "message": f"{rule.get('id', rule_index)}: {error.message}",
                })
    return {
        "target": target,
        "version": raw.get("meta", {}).get("version"),
        "ok": not issues,
        "issues": issues,
    }


def _same_rule_value(actual, expected) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return actual is expected
    if expected is None:
        return actual is None
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isclose(float(actual), float(expected), rel_tol=1e-9, abs_tol=1e-9)
    return actual == expected


def check_rule_cases(path: str, label: str) -> dict:
    """G1-04：执行 tests/rules/*.json，保证每条规则至少 2 正 2 反。"""
    target = f"{label}#rule_cases"
    issues: list[dict] = []
    layer = SemanticLayer(path)
    rule_ids = set(layer.rules)
    coverage = {rule_id: {"positive": 0, "negative": 0} for rule_id in rule_ids}

    files = sorted(
        os.path.join(RULE_CASES_DIR, name)
        for name in os.listdir(RULE_CASES_DIR)
        if name.endswith(".json")
    ) if os.path.isdir(RULE_CASES_DIR) else []
    if not files:
        return {
            "target": target,
            "ok": False,
            "issues": [{
                "code": "RULE_CASE_MISSING",
                "path": "tests/rules",
                "message": "未找到规则回归用例文件",
            }],
        }

    for case_file in files:
        try:
            with open(case_file, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            issues.append({
                "code": "RULE_CASE_FILE_INVALID",
                "path": os.path.relpath(case_file, ROOT),
                "message": f"用例文件无法解析: {exc}",
            })
            continue

        for index, case in enumerate(doc.get("cases") or []):
            case_id = str(case.get("id") or f"{os.path.basename(case_file)}#{index}")
            rule_id = case.get("rule_id")
            kind = case.get("kind")
            case_path = f"{os.path.relpath(case_file, ROOT)}#{case_id}"
            if rule_id not in rule_ids:
                issues.append({
                    "code": "RULE_CASE_UNKNOWN_RULE",
                    "path": case_path,
                    "message": f"用例引用了不存在的规则: {rule_id}",
                })
                continue
            if kind not in ("positive", "negative"):
                issues.append({
                    "code": "RULE_CASE_INVALID",
                    "path": case_path,
                    "message": f"kind 必须是 positive 或 negative，实际为 {kind!r}",
                })
                continue
            coverage[rule_id][kind] += 1

            try:
                result = layer.evaluate_rule(rule_id, case.get("ctx") or {})
            except Exception as exc:  # noqa: BLE001 - 门禁把异常转成可读问题
                issues.append({
                    "code": "RULE_CASE_EXECUTION_FAILED",
                    "path": case_path,
                    "message": f"{rule_id} 执行失败: {exc}",
                })
                continue

            expect = case.get("expect") or {}
            for field, expected in expect.items():
                actual = result.get(field)
                if isinstance(expected, list) and isinstance(actual, list):
                    matched = sorted(str(item) for item in actual) == sorted(
                        str(item) for item in expected
                    )
                else:
                    matched = _same_rule_value(actual, expected)
                if not matched:
                    issues.append({
                        "code": "RULE_CASE_FAILED",
                        "path": case_path,
                        "message": (
                            f"{rule_id}.{field} 期望 {expected!r}，实际 {actual!r}"
                        ),
                    })

    for rule_id, counts in sorted(coverage.items()):
        for kind in ("positive", "negative"):
            if counts[kind] < 2:
                issues.append({
                    "code": "RULE_CASE_COVERAGE",
                    "path": f"rules/{rule_id}",
                    "message": f"{rule_id} 仅覆盖 {counts[kind]} 个 {kind} 用例，至少需要 2 个",
                })

    return {"target": target, "ok": not issues, "issues": issues}


def check_validate(path: str, label: str) -> dict:
    """G1-05：验证 SemanticLayer.validate() 与 API 的六类结构化错误。"""
    target = f"{label}#validate"
    issues: list[dict] = []
    layer = SemanticLayer(path)

    clean = layer.validate()
    if not clean.get("ok"):
        issues.append({
            "code": "VALIDATE_CLEAN_FAILED",
            "path": target,
            "message": f"当前语义层校验未通过: {clean.get('summary')}",
        })
    categories = clean.get("categories") or {}
    for name in ("types", "relations", "statuses", "rules", "ast", "references"):
        if name not in categories:
            issues.append({
                "code": "VALIDATE_CATEGORY_MISSING",
                "path": target,
                "message": f"validate() 缺少错误分类: {name}",
            })

    with open(path, encoding="utf-8") as f:
        bad = json.load(f)
    bad.setdefault("classes", []).append({
        "id": "Waybill",
        "label": "",
        "key": "",
        "properties": [],
        "statuses": [],
    })
    bad.setdefault("relations", []).append({
        "id": "bad_relation",
        "domain": "NoSuchClass",
        "range": "Waybill",
        "label": "坏定义域",
    })
    bad.setdefault("relations", []).append({
        "id": "bad_relation_range",
        "domain": "Waybill",
        "range": "NoSuchClass",
        "label": "坏值域",
    })
    bad.setdefault("actions", []).append({
        "id": "bad_action",
        "target": "Waybill",
        "from_status": ["NO_SUCH_STATUS"],
        "roles": ["driver"],
        "params": [],
    })
    bad.setdefault("rules", []).append({
        "id": "R-BAD-COND",
        "name": "坏条件规则",
        "domain": "FeeItem",
        "when": {"field": "fee_type", "op": "regex", "value": "base"},
        "judge": {"ast": {"op": "const", "value": 1}, "explain": "坏条件"},
    })
    bad.setdefault("rules", []).append({
        "id": "R-BAD-AST",
        "name": "坏 AST 规则",
        "domain": "FeeItem",
        "when": {"field": "fee_type", "op": "eq", "value": "base"},
        "judge": {"ast": {"op": "exec"}, "explain": "坏 AST"},
    })
    bad.setdefault("pricing", {})["bad_fee"] = {"rule_id": "R-NO-SUCH-RULE"}

    negative = layer.validate(bad)
    counts = (negative.get("summary") or {}).get("by_category") or {}
    for name in ("types", "relations", "statuses", "rules", "ast", "references"):
        if counts.get(name, 0) < 1:
            issues.append({
                "code": "VALIDATE_NEGATIVE_CONTROL_FAILED",
                "path": target,
                "message": f"负例未触发 {name} 分类错误，实际计数 {counts.get(name, 0)}",
            })
    codes = {issue.get("code") for issue in negative.get("issues") or []}
    for code in ("RELATION_DOMAIN_UNKNOWN", "RELATION_RANGE_UNKNOWN"):
        if code not in codes:
            issues.append({
                "code": "VALIDATE_NEGATIVE_CONTROL_FAILED",
                "path": target,
                "message": f"负例未触发 {code}",
            })

    return {"target": target, "ok": not issues, "issues": issues}


def check_substance(path: str, label: str) -> dict:
    """反虚胖：属性注册表完备、描述非模板、状态机不雷同、无脚本批量生成类。

    只对工作副本执行；历史发布快照允许保留旧形态。
    """
    target = f"{label}#substance"
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    # 接口声明在扩展层（enhancements/ontology_extension.json），合并后一起校验
    ext_path = os.path.join(os.path.dirname(path), "..", "enhancements",
                            "ontology_extension.json")
    if os.path.exists(ext_path):
        with open(ext_path, encoding="utf-8") as f:
            ext = json.load(f)
        merged = dict(raw)
        merged["interfaces"] = ext.get("interfaces", [])
        issues = validate_ontology_substance(merged)
    else:
        issues = validate_ontology_substance(raw)
    return {"target": target, "ok": not issues, "issues": issues}


def check_model_tools(path: str, label: str) -> dict:
    """G1-08：模型可见工具面不得暴露旧闭包入口和 closure 参数。"""
    target = f"{label}#tools"
    from freight.fact_mcp import build_tool_surface

    surface = build_tool_surface(SemanticLayer(path))
    names = {tool["name"] for tool in surface}
    issues: list[dict] = []
    if "ontology.search" in names:
        issues.append({
            "code": "MODEL_TOOL_LEGACY_CLOSURE",
            "path": target,
            "message": "模型可见工具面仍暴露旧闭包入口 ontology.search",
        })
    schema_tool = next(
        (tool for tool in surface if tool["name"] == "ontology.schema"), None
    )
    if schema_tool is None:
        issues.append({
            "code": "MODEL_TOOL_MISSING",
            "path": target,
            "message": "模型可见工具面缺少 ontology.schema",
        })
    elif "closure" in (schema_tool.get("arguments") or {}):
        issues.append({
            "code": "MODEL_TOOL_CLOSURE_EXPOSED",
            "path": f"{target}/ontology.schema",
            "message": "ontology.schema 仍向模型暴露 closure 参数",
        })
    for required in ("ontology.catalog", "ontology.object_type_search"):
        if required not in names:
            issues.append({
                "code": "MODEL_TOOL_MISSING",
                "path": target,
                "message": f"模型可见工具面缺少 {required}",
            })
    return {"target": target, "ok": not issues, "issues": issues}


def check_breaking_changes(root: str) -> dict:
    """P2-3：破坏性变更必须逐条签字。

    比对「最新发布快照 -> 当前工作副本」。任何 CLASS_REMOVED / PROPERTY_REMOVED /
    类型变更 / 基数收紧，都必须在 freight/breaking_approvals.json 里有**路径完全一致**
    的签字，否则失败。没这一关，改一个属性类型就能悄悄打爆所有 OSDK 客户端。
    """
    from freight.change_gate import review
    from freight.release import semantic_path

    target = "freight/semantic_layer.json#breaking"
    item: dict = {"target": target, "ok": True, "issues": []}
    with open(CURRENT, encoding="utf-8") as f:
        current = json.load(f)
    manifests = [m for m in list_manifests() if m.get("version")]
    if not manifests:
        return item  # 还没有任何发布快照，无从比对
    latest = sorted(manifests, key=lambda m: m["version"])[-1]["version"]
    with open(semantic_path(latest), encoding="utf-8") as f:
        previous = json.load(f)
    ext_path = os.path.join(root, "enhancements", "ontology_extension.json")
    ext = {}
    if os.path.exists(ext_path):
        with open(ext_path, encoding="utf-8") as f:
            ext = json.load(f)
    result = review(previous, current, ext, root)
    item["version"] = latest
    item["breaking_count"] = result["breaking_count"]
    if result["unapproved_count"]:
        item["ok"] = False
        for change in result["unapproved"]:
            item["issues"].append({
                "code": "BREAKING_CHANGE_UNAPPROVED",
                "path": change["path"],
                "message": f"{change['code']}: {change['detail']}（未签字）",
            })
    return item


def check_extension_schema(root: str) -> dict:
    """P0-1：扩展层必须有 schema 契约，且负向用例必须被拦。

    扩展层（enhancements/ontology_extension.json）是追齐层的第二份本体：
    属性注册表 / 接口 / 标记 / 安全策略 / 函数注册表全在这里。它没有 schema
    就等于没有契约——垃圾键会被静默接受，本函数用四条负向用例证明拦截生效。
    """
    import jsonschema

    target = "enhancements/ontology_extension.json#schema"
    item: dict = {"target": target, "ok": True, "issues": []}
    schema_path = os.path.join(root, "schema", "ontology_extension.schema.json")
    ext_path = os.path.join(root, "enhancements", "ontology_extension.json")
    if not os.path.exists(schema_path):
        item["ok"] = False
        item["issues"].append({"code": "EXT_SCHEMA_MISSING", "path": schema_path,
                               "message": "扩展层 Schema 不存在"})
        return item
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    with open(ext_path, encoding="utf-8") as f:
        ext = json.load(f)
    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        item["ok"] = False
        item["issues"].append({"code": "EXT_SCHEMA_INVALID", "path": schema_path,
                               "message": f"Schema 自身不合法: {exc.message}"})
        return item

    validator = jsonschema.Draft202012Validator(schema)
    for error in sorted(validator.iter_errors(ext), key=lambda e: list(e.path)):
        item["ok"] = False
        item["issues"].append({
            "code": "EXT_SCHEMA_INVALID",
            "path": ".".join(str(p) for p in error.path) or "$",
            "message": error.message,
        })

    # 基础 schema 与扩展 schema 的 propertyType 定义不得漂移（两处枚举必须一致）
    base_schema_path = os.path.join(root, "schema", "semantic_layer.schema.json")
    with open(base_schema_path, encoding="utf-8") as f:
        base_schema = json.load(f)
    b_pt = base_schema["$defs"]["propertyType"]["properties"]
    e_pt = schema["$defs"]["propertyType"]["properties"]

    def enum_of(node: dict, defs: dict):
        """取枚举值；$ref 指向本地 $defs 时解析过去。

        不解析 $ref 会得到 None != [...]，看着像漂移其实是没读到——
        这种「假失败」比漏检更烦人，也更容易被当成噪音忽略。
        """
        if "enum" in node:
            return node["enum"]
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/"):
            target = defs.get(ref.split("/")[-1], {})
            return target.get("enum")
        return None

    for field in ("value_type", "base_type"):
        b_enum = enum_of(b_pt[field], base_schema["$defs"])
        e_enum = enum_of(e_pt[field], schema["$defs"])
        if b_enum != e_enum:
            item["ok"] = False
            item["issues"].append({
                "code": "EXT_SCHEMA_DRIFT",
                "path": f"$defs.propertyType.{field}",
                "message": f"扩展层 {field} 枚举与基础 schema 不一致: "
                           f"{e_enum} != {b_enum}",
            })
    b_keys = set(b_pt) - {"status"}
    e_keys = set(e_pt)
    if not b_keys <= e_keys:
        item["ok"] = False
        item["issues"].append({
            "code": "EXT_SCHEMA_DRIFT",
            "path": "$defs.propertyType.properties",
            "message": f"扩展层 propertyType 缺少基础 schema 的字段: "
                       f"{sorted(b_keys - e_keys)}",
        })

    # 二源真相：同一个属性在两份注册表里的定义不得分叉。
    # 扩展层是「可覆盖视图」——覆盖本身合法（比如提高标记级别），但**静默降级**
    # 不合法：基础层标了 PII、扩展层写 null，运行时取扩展层那份，脱敏就没了，
    # 而两边都"合法"、schema 也都过。承重实验 L12b 就是从这里漏的。
    layer_path = os.path.join(root, "freight", "semantic_layer.json")
    with open(layer_path, encoding="utf-8") as f:
        layer = json.load(f)
    base_pts = {p["name"]: p for p in layer.get("property_types") or []}
    for p in ext.get("property_types") or []:
        b = base_pts.get(p.get("name"))
        if b is None:
            continue
        for field in ("value_type", "base_type"):
            if b.get(field) != p.get(field):
                item["ok"] = False
                item["issues"].append({
                    "code": "PROP_TYPE_DIVERGED",
                    "path": f"property_types[{p['name']}].{field}",
                    "message": f"{p['name']}.{field} 两份注册表不一致: "
                               f"基础 {b.get(field)!r} / 扩展 {p.get(field)!r}",
                })
        if (b.get("marking") or None) != (p.get("marking") or None):
            item["ok"] = False
            item["issues"].append({
                "code": "PROP_MARKING_DIVERGED",
                "path": f"property_types[{p['name']}].marking",
                "message": f"{p['name']}.marking 两份注册表不一致: "
                           f"基础 {b.get('marking')!r} / 扩展 {p.get('marking')!r}"
                           f"（扩展层优先，等于运行时按后者执行）",
            })

    # 负向用例：以下四类污染必须被拦，漏一个就说明 schema 是装饰
    negatives = [
        ("垃圾顶层键", lambda d: d.__setitem__("totally_bogus_top_key", {"x": 1})),
        ("垃圾属性字段",
         lambda d: d["property_types"][0].__setitem__("bogus_field_xyz", 123)),
        ("非法 value_type",
         lambda d: d["property_types"][0].__setitem__("value_type", "not_a_real_type")),
        ("object_set 缺 of",
         lambda d: d["function_registry"][0]["signature"]["params"].append(
             {"name": "bad_set", "type": "object_set"})),
    ]
    for label, mutate in negatives:
        bad = json.loads(json.dumps(ext))
        mutate(bad)
        if not list(validator.iter_errors(bad)):
            item["ok"] = False
            item["issues"].append({
                "code": "EXT_SCHEMA_NEGATIVE_LEAKED",
                "path": target,
                "message": f"负向用例未被拦截: {label}",
            })
    return item


def check_policy_strict(root: str) -> dict:
    """P0-3：属性注册表策略必须是 strict 且 legacy 通道关闭。

    incremental + legacy_grace=true 等于「登记册有了、海关不开」：未注册属性
    静默通过。宽松口径必须先能证明零未注册属性，才允许存在的理由。
    """
    target = "enhancements/ontology_extension.json#policy"
    item: dict = {"target": target, "ok": True, "issues": []}
    ext_path = os.path.join(root, "enhancements", "ontology_extension.json")
    with open(ext_path, encoding="utf-8") as f:
        ext = json.load(f)
    pol = ext.get("property_registry_policy") or {}
    if pol.get("enforcement") != "strict":
        item["ok"] = False
        item["issues"].append({
            "code": "POLICY_NOT_STRICT",
            "path": "property_registry_policy.enforcement",
            "message": f"enforcement 必须是 strict，实际 {pol.get('enforcement')!r}",
        })
    if pol.get("legacy_grace"):
        item["ok"] = False
        item["issues"].append({
            "code": "POLICY_LEGACY_GRACE_OPEN",
            "path": "property_registry_policy.legacy_grace",
            "message": "strict 模式下 legacy_grace 必须为 false",
        })
    # strict 的前提：当前本体里零个未注册属性
    layer_path = os.path.join(root, "freight", "semantic_layer.json")
    with open(layer_path, encoding="utf-8") as f:
        layer = json.load(f)
    registered = {p["name"] for p in layer.get("property_types") or []}
    uncovered = sorted({
        f"{c['id']}.{p}" for c in layer.get("classes") or []
        for p in c.get("properties") or [] if p not in registered
    })
    if uncovered:
        item["ok"] = False
        item["issues"].append({
            "code": "POLICY_STRICT_PRECONDITION_FAILED",
            "path": "classes[*].properties",
            "message": f"strict 前提不成立，仍有 {len(uncovered)} 个未注册属性: "
                       f"{uncovered[:10]}",
        })
    return item


def check_display_metadata(root: str) -> dict:
    """P1-1：展示层元数据与不可变 rid（对标 Palantir displayMetadata / rid）。

    只对工作副本执行：历史发布快照允许保留旧形态，不要求补展示层字段。
    rid 必须与 freight/rid_ledger.json 一致——手改 rid 会断链，门禁必须拦。
    """
    target = "freight/semantic_layer.json#display"
    item: dict = {"target": target, "ok": True, "issues": []}
    layer_path = os.path.join(root, "freight", "semantic_layer.json")
    with open(layer_path, encoding="utf-8") as f:
        layer = json.load(f)
    ledger_path = os.path.join(root, "freight", "rid_ledger.json")
    ledger: dict = {}
    if os.path.exists(ledger_path):
        with open(ledger_path, encoding="utf-8") as f:
            ledger = json.load(f)

    # 复数目录名 -> 台账单数前缀。**不要用 kind[:-1] 去凑**：classes[:-1] 得到的是
    # "classe"，台账里写的是 "class:Waybill"——查不到就 `if expected` 短路，于是
    # 类 rid 被手改也一声不响。承重实验 L8 就是从这里漏过去的。
    ledger_prefix = {"classes": "class", "relations": "relation"}
    seen_rid: dict[str, str] = {}
    for kind in ("classes", "relations"):
        for obj in layer.get(kind) or []:
            oid = obj.get("id")
            where = f"{kind}[{oid}]"
            dm = obj.get("display_metadata")
            if not obj.get("rid"):
                item["ok"] = False
                item["issues"].append({"code": "RID_MISSING", "path": where,
                                       "message": f"{oid} 缺少 rid"})
            elif not dm:
                item["ok"] = False
                item["issues"].append({
                    "code": "DISPLAY_METADATA_MISSING", "path": where,
                    "message": f"{oid} 缺少 display_metadata"})
            if obj.get("rid"):
                if obj["rid"] in seen_rid:
                    item["ok"] = False
                    item["issues"].append({
                        "code": "RID_DUP", "path": where,
                        "message": f"rid 重复: {obj['rid']}（{seen_rid[obj['rid']]}）"})
                seen_rid[obj["rid"]] = oid
                ledger_key = f"{ledger_prefix[kind]}:{oid}"
                expected = ledger.get(ledger_key)
                if expected is None:
                    # 台账缺项不能当"没问题"：没有基线就无从判断 rid 是否被改过。
                    item["ok"] = False
                    item["issues"].append({
                        "code": "RID_LEDGER_MISSING", "path": where,
                        "message": f"{ledger_key} 不在 rid 台账里，rid 漂移无从校验"})
                elif expected != obj["rid"]:
                    item["ok"] = False
                    item["issues"].append({
                        "code": "RID_LEDGER_DRIFT", "path": where,
                        "message": f"{ledger_key} 的 rid 与台账不一致: "
                                   f"台账 {expected} / 本体 {obj['rid']}"})
            if dm:
                missing = [f for f in ("icon", "color", "visibility", "category")
                           if not dm.get(f)]
                if missing:
                    item["ok"] = False
                    item["issues"].append({
                        "code": "DISPLAY_METADATA_INCOMPLETE", "path": where,
                        "message": f"{oid} 的 display_metadata 缺: {missing}"})
            if kind == "classes" and not obj.get("plural_display_name"):
                item["ok"] = False
                item["issues"].append({
                    "code": "PLURAL_DISPLAY_NAME_MISSING", "path": where,
                    "message": f"{oid} 缺少 plural_display_name"})
    return item


def check_metadata(path: str, label: str) -> dict:
    """G1-02：当前工作副本的类/关系/规则/动作必须有来源与版本元数据。"""
    target = f"{label}#metadata"
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    fields = ("owner", "source", "version", "iri", "effective_from")
    issues = []
    for collection in ("classes", "relations", "rules", "actions"):
        for index, item in enumerate(raw.get(collection) or []):
            missing = [field for field in fields if not item.get(field)]
            if missing:
                issues.append({
                    "code": "METADATA_MISSING",
                    "path": f"{collection}/{index}",
                    "message": f"{item.get('id', index)} 缺少元数据字段: {', '.join(missing)}",
                })
    return {
        "target": target,
        "version": raw.get("meta", {}).get("version"),
        "ok": not issues,
        "issues": issues,
    }


def check_file(path: str, label: str) -> dict:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    issues = validate_semantic(raw)
    return {
        "target": label,
        "version": raw.get("meta", {}).get("version"),
        "ok": not issues,
        "issues": issues,
    }


def check_shacl(path: str, label: str, base_iri: str) -> dict:
    """导出标准 SHACL，并用 pySHACL 校验形状本身与负例拦截能力。"""
    target = f"{label}#shacl"
    issues: list[dict] = []
    try:
        from rdflib import Graph
        from pyshacl import validate as shacl_validate
        from tools.export_shacl import build_negative_control, build_shapes
    except ImportError as exc:
        return {
            "target": target,
            "ok": False,
            "issues": [{
                "code": "SHACL_DEPENDENCY_MISSING",
                "path": target,
                "message": f"缺少 rdflib/pyshacl，无法运行标准 SHACL 校验: {exc}",
            }],
        }

    with open(path, encoding="utf-8") as f:
        layer = json.load(f)

    try:
        shapes = build_shapes(layer, base_iri)
        conforms, _, _ = shacl_validate(Graph(), shacl_graph=shapes, inference=None)
        if not conforms:
            issues.append({
                "code": "SHACL_SHAPE_INVALID",
                "path": target,
                "message": "SHACL 形状在空数据图上即不通过，形状定义自身有误",
            })

        constraint_ids = {c.get("id") for c in layer.get("constraints") or []}
        expected = [cid for cid in ("C3", "C5") if cid in constraint_ids]
        if expected:
            bad_conforms, _, report = shacl_validate(
                build_negative_control(base_iri), shacl_graph=shapes, inference=None
            )
            for cid in expected:
                if bad_conforms or cid not in report:
                    issues.append({
                        "code": "SHACL_NEGATIVE_CONTROL_FAILED",
                        "path": target,
                        "message": f"负例未按预期触发 {cid}",
                    })
    except Exception as exc:  # noqa: BLE001 - 门禁需要把异常转成结构化问题
        issues.append({
            "code": "SHACL_EXPORT_FAILED",
            "path": target,
            "message": f"SHACL 导出/校验异常: {exc}",
        })

    return {"target": target, "ok": not issues, "issues": issues}


def _shacl_case_graph(case: dict, base_iri: str):
    from rdflib import Graph, Literal, Namespace, URIRef
    from rdflib.namespace import RDF, XSD

    mo = Namespace(base_iri)
    graph = Graph()
    for node in case.get("nodes") or []:
        subject = URIRef(base_iri + node["id"])
        graph.add((subject, RDF.type, mo[node["type"]]))
        for key, value in (node.get("props") or {}).items():
            values = value if isinstance(value, list) else [value]
            for item in values:
                if isinstance(item, bool):
                    literal = Literal(item)
                elif isinstance(item, int):
                    literal = Literal(item, datatype=XSD.integer)
                elif isinstance(item, float):
                    literal = Literal(str(item), datatype=XSD.decimal)
                else:
                    literal = Literal(item)
                graph.add((subject, mo[key], literal))
    for rel in case.get("relations") or []:
        graph.add((
            URIRef(base_iri + rel["subject"]),
            mo[rel["predicate"]],
            URIRef(base_iri + rel["object"]),
        ))
    return graph


def check_shacl_cases(path: str, label: str, base_iri: str) -> dict:
    """G1-07：C1~C8 每条约束至少 1 条正例通过、1 条反例被拦截。"""
    target = f"{label}#shacl_cases"
    issues: list[dict] = []
    try:
        from pyshacl import validate as shacl_validate
        from tools.export_shacl import build_shapes
    except ImportError as exc:
        return {
            "target": target,
            "ok": False,
            "issues": [{
                "code": "SHACL_DEPENDENCY_MISSING",
                "path": target,
                "message": f"缺少 rdflib/pyshacl，无法运行 SHACL 回归: {exc}",
            }],
        }

    with open(path, encoding="utf-8") as f:
        layer = json.load(f)
    with open(SHACL_CASES, encoding="utf-8") as f:
        document = json.load(f)
    shapes = build_shapes(layer, base_iri)
    coverage: dict[str, dict[str, int]] = {}

    for index, case in enumerate(document.get("cases") or []):
        case_id = case.get("id") or f"case-{index}"
        constraint_id = case.get("constraint_id")
        kind = case.get("kind")
        if kind not in ("positive", "negative"):
            issues.append({
                "code": "SHACL_CASE_KIND_INVALID",
                "path": f"{target}/{case_id}",
                "message": f"用例类型非法: {kind}",
            })
            continue
        coverage.setdefault(constraint_id, {"positive": 0, "negative": 0})
        coverage[constraint_id][kind] += 1
        graph = _shacl_case_graph(case, base_iri)
        conforms, _, report = shacl_validate(graph, shacl_graph=shapes, inference=None)
        if kind == "positive" and not conforms:
            issues.append({
                "code": "SHACL_CASE_POSITIVE_FAILED",
                "path": f"{target}/{case_id}",
                "message": f"{constraint_id} 正例未通过: {report[:500]}",
            })
        if kind == "negative":
            if conforms:
                issues.append({
                    "code": "SHACL_CASE_NEGATIVE_PASSED",
                    "path": f"{target}/{case_id}",
                    "message": f"{constraint_id} 反例未被拦截",
                })
            elif constraint_id not in report:
                issues.append({
                    "code": "SHACL_CASE_WRONG_CONSTRAINT",
                    "path": f"{target}/{case_id}",
                    "message": f"{case_id} 被拦截但不是目标约束 {constraint_id}",
                })

    for constraint in layer.get("constraints") or []:
        constraint_id = constraint.get("id")
        counts = coverage.get(constraint_id) or {"positive": 0, "negative": 0}
        for kind in ("positive", "negative"):
            if counts[kind] < 1:
                issues.append({
                    "code": "SHACL_CASE_MISSING",
                    "path": target,
                    "message": f"{constraint_id} 缺少 {kind} 用例",
                })

    return {
        "target": target,
        "ok": not issues,
        "cases": len(document.get("cases") or []),
        "issues": issues,
    }


def check_query_schema(root: str) -> dict:
    """E5 门禁：查询树 Schema 自洽 + 负样本三例（非法算子/未知属性/超深嵌套）必须被拦。"""
    import jsonschema

    from freight.object_query import ObjectQueryEngine, QueryError
    from freight.semantic import SemanticLayer

    target = "schema/object_query.schema.json#query_dsl"
    item: dict = {"target": target, "ok": True, "issues": []}
    schema_path = os.path.join(root, "schema", "object_query.schema.json")
    if not os.path.exists(schema_path):
        item["ok"] = False
        item["issues"].append({"code": "QUERY_SCHEMA_MISSING", "path": schema_path,
                               "message": "查询树 Schema 不存在"})
        return item
    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    try:
        jsonschema.Draft7Validator.check_schema(schema)
    except jsonschema.SchemaError as exc:
        item["ok"] = False
        item["issues"].append({"code": "QUERY_SCHEMA_INVALID", "path": schema_path,
                               "message": f"Schema 自身不合法: {exc.message}"})
        return item

    # 正例必须同时通过 Schema 与本体期校验
    good = {
        "set": {"from": "Waybill", "where": {"status": {"$in": ["DELIVERED"]}}},
        "orderBy": [{"prop": "actual_fee", "dir": "desc"}],
        "page": {"limit": 10},
    }
    try:
        jsonschema.validate(good, schema)
    except jsonschema.ValidationError as exc:
        item["ok"] = False
        item["issues"].append({"code": "QUERY_SCHEMA_GOOD_REJECTED",
                               "message": f"正例被 Schema 拒绝: {exc.message}"})
    # 负样本三例：必须被本体期校验拦（schema 只管结构，属性/算子白名单在引擎）
    negatives = [
        ("PREDICATE_OP_UNKNOWN", {
            "set": {"from": "Waybill", "where": {"actual_fee": {"$weird": 1}}}}),
        ("PREDICATE_PROP_UNKNOWN", {
            "set": {"from": "Waybill", "where": {"no_such_prop": {"$eq": 1}}}}),
        ("QUERY_DEPTH_EXCEEDED", {
            "set": {"from": "Waybill"},
            "where": _deep_predicate(40)}),
    ]
    sem = SemanticLayer()
    engine = ObjectQueryEngine(sem)
    for expected_code, bad_query in negatives:
        try:
            engine.validate(bad_query)
            item["ok"] = False
            item["issues"].append({
                "code": "QUERY_NEGATIVE_LEAKED",
                "message": f"负样本未被拦截（期望 {expected_code}）",
            })
        except QueryError as exc:
            if exc.code != expected_code:
                item["ok"] = False
                item["issues"].append({
                    "code": "QUERY_NEGATIVE_WRONG_CODE",
                    "message": f"拦截码 {exc.code} != 期望 {expected_code}",
                })
    return item


def _deep_predicate(depth: int) -> dict:
    node: dict = {"status": {"$eq": "LOADED"}}
    for _ in range(depth):
        node = {"$or": [node]}
    return node


def check_state_machines(root: str) -> dict:
    """P2-4：状态机完备性——状态的来源与归宿说得清，终态不得再有迁出边。

    迁移边真源在动作的 from_status/to_status；状态机声明（顶层 state_machines）
    补的是「起点 / 终态 / 外部可写状态」。判据内核在
    `SemanticLayer.validate_state_machines()`，本函数负责跑一遍 + 用四条负向
    用例证明判据真的会拦（不拦就是装饰）。
    """
    target = "freight/semantic_layer.json#state_machines"
    item: dict = {"target": target, "ok": True, "issues": []}
    layer_path = os.path.join(root, "freight", "semantic_layer.json")
    sem = SemanticLayer(layer_path)
    for iss in sem.validate_state_machines():
        if iss.get("severity") == "info":
            continue
        item["ok"] = False
        item["issues"].append({"code": iss["code"], "path": iss["path"],
                               "message": iss["message"]})

    def _mutate_drop_decl(raw):
        raw.get("state_machines", {}).pop("Waybill", None)
        return raw

    def _mutate_bad_terminal(raw):
        raw["state_machines"]["Waybill"]["terminal"] = ["ACCEPTED"]
        return raw

    def _mutate_unknown_state(raw):
        raw["state_machines"]["Waybill"]["initial"] = ["NOT_A_STATUS"]
        return raw

    def _mutate_orphan_decl(raw):
        raw["state_machines"]["Ghost"] = {"initial": [], "terminal": [],
                                          "external_states": []}
        return raw

    negatives = [
        ("有动作却不声明状态机", _mutate_drop_decl, "STATE_MACHINE_UNDECLARED"),
        ("终态仍有迁出边", _mutate_bad_terminal, "STATE_MACHINE_TERMINAL_HAS_OUTGOING"),
        ("声明含不存在的状态", _mutate_unknown_state, "STATE_MACHINE_DECL_INVALID"),
        ("无动作却声明状态机", _mutate_orphan_decl, "STATE_MACHINE_ORPHAN_DECL"),
    ]
    with open(layer_path, encoding="utf-8") as f:
        base_raw = json.load(f)
    for label, mutate, expect_code in negatives:
        bad_raw = mutate(json.loads(json.dumps(base_raw)))
        tmp = os.path.join(root, "logs", ".state_machine_negative.json")
        os.makedirs(os.path.dirname(tmp), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(bad_raw, f, ensure_ascii=False)
        try:
            bad_sem = SemanticLayer(tmp)
            codes = {i["code"] for i in bad_sem.validate_state_machines()}
        except Exception as exc:  # noqa: BLE001 - 负向用例崩了也算没拦住
            codes = set()
            item["issues"].append({
                "code": "STATE_MACHINE_NEGATIVE_CRASH", "path": target,
                "message": f"负向用例执行异常: {exc}"})
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        if expect_code not in codes:
            item["ok"] = False
            item["issues"].append({
                "code": "STATE_MACHINE_NEGATIVE_LEAKED", "path": target,
                "message": f"负向用例未被拦截（期望 {expect_code}）: {label}"})
    return item


def check_abox_coverage(root: str) -> dict:
    """P2-6：ABox 深度校验（深水区覆盖）——本体「能表达」之外，还要「被业务证明过」。

    判据内核在 `freight/abox.py`：
    - V1 深水区每个类型至少有 1 个对象实例；
    - V2 深水区每个类型不是孤岛（至少出现在一条关系实例的一端）；
    - V3 每个迁移动作至少有 1 条 APPROVED 审计（动作真被执行过，不是只声明）。

    度量跑在「种子数据 + 核心业务剧本」上（`freight/scenarios.py`），
    所以测到的是真跑过的覆盖，不是声明出来的覆盖。
    """
    target = "freight/semantic_layer.json#abox_targets"
    item: dict = {"target": target, "ok": True, "issues": []}
    from freight import abox

    layer_path = os.path.join(root, "freight", "semantic_layer.json")
    sem = SemanticLayer(layer_path)
    db_path = abox.build_db(prefix="abox_gate_")
    try:
        metrics = abox.measure(sem, db_path)
        for iss in abox.check(sem, db_path):
            item["ok"] = False
            item["issues"].append({"code": iss["code"], "path": iss["path"],
                                   "message": iss["message"]})
        item["coverage"] = {
            "class_coverage": metrics["class_coverage"],
            "edge_coverage": metrics["edge_coverage"],
            "action_coverage": metrics["action_coverage"],
            "core_types": metrics["core_types"],
        }

        # 负向用例：删掉实例 / 删掉边 / 删掉审计，必须分别被抓出来
        negatives = [
            ("删掉深水区类型的全部实例",
             "DELETE FROM objects WHERE type='Carrier'",
             "ABOX_CLASS_NO_INSTANCE"),
            # 必须删两端：只删出边时该类型仍有入边，不算孤岛（负向用例本身要成立）
            ("删掉某类型的全部关系实例",
             "DELETE FROM relations WHERE subject_type='Stop' OR object_type='Stop'",
             "ABOX_CLASS_ISOLATED"),
            ("删掉动作的 APPROVED 审计",
             "DELETE FROM audit WHERE action_id='issue_quote' AND decision='APPROVED'",
             "ABOX_ACTION_UNEXERCISED"),
            ("把审计里的迁移结果改成与声明不符",
             "UPDATE audit SET result='{\"after_status\":\"WRONG\"}' "
             "WHERE action_id='accept_order' AND decision='APPROVED'",
             "ABOX_TRANSITION_MISMATCH"),
        ]
        import sqlite3
        for label, sql, expect_code in negatives:
            probe_db = abox.build_db(prefix="abox_neg_")
            try:
                conn = sqlite3.connect(probe_db)
                conn.execute(sql)
                conn.commit()
                conn.close()
                codes = {i["code"] for i in abox.check(sem, probe_db)}
            except Exception as exc:  # noqa: BLE001 - 负向用例崩了也算没拦住
                codes = set()
                item["issues"].append({
                    "code": "ABOX_NEGATIVE_CRASH", "path": target,
                    "message": f"负向用例执行异常: {exc}"})
            finally:
                if os.path.exists(probe_db):
                    os.remove(probe_db)
            if expect_code not in codes:
                item["ok"] = False
                item["issues"].append({
                    "code": "ABOX_NEGATIVE_LEAKED", "path": target,
                    "message": f"负向用例未被拦截（期望 {expect_code}）: {label}"})
    finally:
        if os.path.exists(db_path):
            os.remove(db_path)
    return item


def check_source_contracts(root: str) -> dict:
    """P2-7：来源契约——费用类型归属矩阵 + 覆盖度 + 缺口与口径冲突的检测能力。

    机制在 `d046ae1` 已经做完（连接器按 `fee_types` 认领、缺口显式化、报价三口径
    互校），但**没有检查会问**："这个费用类型归谁管""三方是否齐全""检测能力还在不在"。
    本门禁项把这些变成硬判据：
    - G1 归属矩阵：定价里的每个 fee_type 恰好被一个连接器认领；
    - G2 覆盖度（V5）：每个 fee_type 在 FeeItem / PricingRule / QuoteItem 三方齐全；
    - 负向用例四条：删认领 / 双认领 / 删报价明细 / 改报价明细金额（口径冲突）。
    """
    target = "freight/semantic_layer.json#data_sources"
    item: dict = {"target": target, "ok": True, "issues": []}
    from freight import abox, source_coverage

    layer_path = os.path.join(root, "freight", "semantic_layer.json")
    sem = SemanticLayer(layer_path)

    def add_issue(iss: dict) -> None:
        item["ok"] = False
        item["issues"].append({"code": iss["code"], "path": iss.get("path", target),
                               "message": iss["message"]})

    for iss in source_coverage.check_fee_type_ownership(sem):
        add_issue(iss)

    db = abox.build_db(prefix="src_gate_")
    try:
        for iss in source_coverage.check_fee_type_coverage(sem, db):
            add_issue(iss)
        item["coverage"] = source_coverage.measure_fee_type_coverage(sem, db)
        item["ownership"] = {
            "universe": source_coverage.fee_type_universe(sem),
            "owners": {k: [c.get("system") for c in v]
                       for k, v in source_coverage.fee_type_owners(sem).items()},
        }

        # ---- 负向用例 ----
        def _temp_layer(mutate):
            import tempfile
            raw = json.loads(json.dumps(sem.raw))
            mutate(raw)
            fd, path = tempfile.mkstemp(suffix=".json", prefix="src_neg_")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False)
            return path

        def _drop_claim(raw):
            for ds in raw.get("data_sources") or []:
                if ds.get("system") == "TMS":
                    ds["fee_types"] = [t for t in ds.get("fee_types") or []
                                       if t != "waiting"]
            return raw

        def _double_claim(raw):
            for ds in raw.get("data_sources") or []:
                if ds.get("system") == "TMS":
                    ds["fee_types"] = list(ds.get("fee_types") or []) + ["highway"]
            return raw

        for label, mutate, expect in (
            ("删掉一个费用类型的认领", _drop_claim, "FEE_TYPE_NO_OWNER"),
            ("两个来源认领同一费用类型", _double_claim, "FEE_TYPE_MULTI_OWNER"),
        ):
            tmp = _temp_layer(mutate)
            try:
                codes = {i["code"] for i in
                         source_coverage.check_fee_type_ownership(SemanticLayer(tmp))}
            except Exception as exc:  # noqa: BLE001
                codes = set()
                item["issues"].append({
                    "code": "SOURCE_NEGATIVE_CRASH", "path": target,
                    "message": f"负向用例执行异常: {exc}"})
            finally:
                os.remove(tmp)
            if expect not in codes:
                item["ok"] = False
                item["issues"].append({
                    "code": "SOURCE_NEGATIVE_LEAKED", "path": target,
                    "message": f"负向用例未被拦截（期望 {expect}）: {label}"})

        # 负向用例：删掉一类 QuoteItem -> 覆盖度缺项
        import sqlite3
        probe_db = abox.build_db(prefix="src_neg_db_")
        try:
            conn = sqlite3.connect(probe_db)
            row = conn.execute(
                "SELECT object_id, props FROM objects WHERE type='QuoteItem' "
                "AND props LIKE '%waiting%' LIMIT 1").fetchone()
            if row:
                conn.execute("DELETE FROM objects WHERE object_id=?", (row[0],))
                conn.commit()
            conn.close()
            codes = {i["code"] for i in
                     source_coverage.check_fee_type_coverage(sem, probe_db)}
            if "FEE_TYPE_NO_QUOTE_ITEM" not in codes:
                item["ok"] = False
                item["issues"].append({
                    "code": "SOURCE_NEGATIVE_LEAKED", "path": target,
                    "message": "负向用例未被拦截（期望 FEE_TYPE_NO_QUOTE_ITEM）: 删掉一类报价明细"})
        finally:
            if os.path.exists(probe_db):
                os.remove(probe_db)

        # 负向用例：改报价明细金额 -> 三口径必须报冲突
        probe_db = abox.build_db(prefix="src_neg_quote_")
        try:
            conn = sqlite3.connect(probe_db)
            row = conn.execute(
                "SELECT object_id, props FROM objects WHERE type='QuoteItem' LIMIT 1"
            ).fetchone()
            if row:
                props = json.loads(row[1])
                props["amount"] = round(float(props.get("amount") or 0) + 100.0, 2)
                conn.execute("UPDATE objects SET props=? WHERE object_id=?",
                             (json.dumps(props, ensure_ascii=False), row[0]))
                conn.commit()
            conn.close()
            from freight.engine import FreightSystem

            system = FreightSystem(probe_db, fresh=False, seed=False)
            try:
                report = system.fee_analysis("waybill:WB20261105001")
                codes = {f.get("code") for f in report.get("findings") or []}
            finally:
                system.conn.close()
            if "QUOTE_TOTAL_MISMATCH" not in codes:
                item["ok"] = False
                item["issues"].append({
                    "code": "SOURCE_NEGATIVE_LEAKED", "path": target,
                    "message": "负向用例未被拦截（期望 QUOTE_TOTAL_MISMATCH）: 报价明细金额被改"})
        finally:
            if os.path.exists(probe_db):
                os.remove(probe_db)
    finally:
        if os.path.exists(db):
            os.remove(db)
    return item


def main() -> int:
    results = [check_schema(CURRENT, "freight/semantic_layer.json")]
    results.append(check_metadata(CURRENT, "freight/semantic_layer.json"))
    results.append(check_rule_ast(CURRENT, "freight/semantic_layer.json"))
    results.append(check_rule_cases(CURRENT, "freight/semantic_layer.json"))
    results.append(check_validate(CURRENT, "freight/semantic_layer.json"))
    results.append(check_substance(CURRENT, "freight/semantic_layer.json"))
    results.append(check_file(CURRENT, "freight/semantic_layer.json"))
    results.append(check_shacl(CURRENT, "freight/semantic_layer.json", FREIGHT_BASE))
    results.append(check_shacl(ROOT_LAYER, "semantic_layer.json", MOBILITY_BASE))
    results.append(check_shacl_cases(ROOT_LAYER, "semantic_layer.json", MOBILITY_BASE))
    results.append(check_model_tools(CURRENT, "freight/semantic_layer.json"))
    results.append(check_extension_schema(ROOT))
    results.append(check_policy_strict(ROOT))
    results.append(check_display_metadata(ROOT))
    results.append(check_breaking_changes(ROOT))
    results.append(check_state_machines(ROOT))
    results.append(check_abox_coverage(ROOT))
    results.append(check_source_contracts(ROOT))
    results.append(check_query_schema(ROOT))
    for manifest in list_manifests():
        version = manifest["version"]
        path = semantic_path(version)
        item = check_schema(path, manifest.get("source", path))
        results.append(item)
        results.append(check_rule_ast(path, manifest.get("source", path)))
        item = check_file(path, manifest.get("source", path))
        expected = manifest.get("checksum")
        actual = file_checksum(path)
        if expected and expected != actual:
            item["ok"] = False
            item["issues"].append({
                "code": "RELEASE_CHECKSUM_MISMATCH",
                "path": f"freight/releases/{version}",
                "message": f"快照 checksum 不一致: 期望 {expected}, 实际 {actual}",
            })
        results.append(item)
        results.append(check_shacl(path, manifest.get("source", path), FREIGHT_BASE))

    with open(CURRENT, encoding="utf-8") as f:
        bad = json.load(f)
    bad.setdefault("rules", []).append({
        "id": "R-ERR-01",
        "name": "错误规则演示：非法费用类型",
        "domain": "FeeItem",
        "when": {"field": "fee_type", "op": "in", "value": ["toll_fee"]},
        "judge": {
            "formula": "actual_amount > expected_amount",
            "explain": "引用不存在的费用类型",
        },
    })
    negative_issues = validate_semantic(bad)
    negative = {
        "target": "negative-control",
        "ok": bool(negative_issues),
        "blocked": bool(negative_issues),
        "issues": negative_issues,
    }

    by_target = {item["target"]: item["ok"] for item in results}
    coverage_targets = {
        "schema": "freight/semantic_layer.json#schema",
        "domain_range": "freight/semantic_layer.json#validate",
        "ast": "freight/semantic_layer.json#rule_ast",
        "rule_cases": "freight/semantic_layer.json#rule_cases",
        "references": "freight/semantic_layer.json#validate",
        "shacl": "freight/semantic_layer.json#shacl",
        "shacl_cases": "semantic_layer.json#shacl_cases",
        "model_tools": "freight/semantic_layer.json#tools",
        "query_dsl": "schema/object_query.schema.json#query_dsl",
        "extension_schema": "enhancements/ontology_extension.json#schema",
        "property_policy": "enhancements/ontology_extension.json#policy",
        "display_metadata": "freight/semantic_layer.json#display",
        "breaking_changes": "freight/semantic_layer.json#breaking",
        "state_machines": "freight/semantic_layer.json#state_machines",
        "abox_coverage": "freight/semantic_layer.json#abox_targets",
        "source_contracts": "freight/semantic_layer.json#data_sources",
    }
    coverage = {
        name: {"target": target, "ok": bool(by_target.get(target))}
        for name, target in coverage_targets.items()
    }
    passed = (
        all(item["ok"] for item in results)
        and negative["blocked"]
        and all(item["ok"] for item in coverage.values())
    )
    output = {
        "ok": passed,
        "gate": "semantic",
        "checked": len(results),
        "coverage": coverage,
        "results": results,
        "negative_control": negative,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())