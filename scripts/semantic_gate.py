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