"""货拉拉订单本体：加载唯一语义层定义，校验对象与关系合法性。"""
from __future__ import annotations

import ast
import json
import math
import os
import re
from collections import deque

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "semantic_layer.json")


class OntologyViolation(Exception):
    """对象类型或关系不符合本体定义时抛出。"""


class BusinessReject(Exception):
    """规则判定拒绝时抛出，code 用于前端区分原因。"""

    def __init__(self, message: str, code: str = "RULE_REJECTED", details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}


class SemanticLayer:
    def __init__(self, path: str = DEFAULT_PATH):
        with open(path, encoding="utf-8") as f:
            self.raw = json.load(f)
        self.path = path
        self.classes = {c["id"]: c for c in self.raw["classes"]}
        self.relations = {r["id"]: r for r in self.raw["relations"]}
        self.actions = {a["id"]: a for a in self.raw["actions"]}
        self.rules = {r["id"]: r for r in self.raw["rules"]}
        self.pricing = self.raw["pricing"]
        self.functions = {
            f["id"]: f for f in self.raw.get("functions", [])
        }
        self.primitives = {
            p["name"]: p for p in self.raw.get("primitives", [])
        }
        self.data_sources = self.raw.get("data_sources", [])
        self.relation_source = self.raw.get("relation_source", {})
        self.closure_config = self.raw.get("closure") or {}
        self.catalog_config = self.raw.get("catalog") or {}
        self.object_type_groups = [
            group for group in (self.raw.get("object_type_groups") or [])
            if group.get("id")
        ]
        self.object_type_group_map = {
            group["id"]: group for group in self.object_type_groups
        }

    def class_of(self, type_id: str) -> dict:
        if type_id not in self.classes:
            raise OntologyViolation(f"未定义的对象类型: {type_id}")
        return self.classes[type_id]

    def validate_object(self, type_id: str, props: dict) -> None:
        cls = self.class_of(type_id)
        allowed = set(cls["properties"])
        unknown = sorted(set(props) - allowed)
        if unknown:
            raise OntologyViolation(f"{type_id} 不允许的属性: {', '.join(unknown)}")
        status = props.get("status")
        if status and status not in cls["statuses"]:
            raise OntologyViolation(f"{type_id} 非法状态: {status}")

    CARDINALITIES = ("one_to_one", "one_to_many", "many_to_one", "many_to_many")

    def validate_relation(self, predicate: str, subject_type: str, object_type: str) -> None:
        rel = self.relations.get(predicate)
        if rel is None:
            raise OntologyViolation(f"未定义的关系: {predicate}")
        if subject_type != rel["domain"]:
            raise OntologyViolation(
                f"关系 {predicate} 定义域应为 {rel['domain']}，实际 {subject_type}"
            )
        if object_type != rel["range"]:
            raise OntologyViolation(
                f"关系 {predicate} 值域应为 {rel['range']}，实际 {object_type}"
            )
        card = rel.get("cardinality", "many_to_many")
        if card not in self.CARDINALITIES:
            raise OntologyViolation(
                f"关系 {predicate} 基数声明非法: {card}（合法值 {self.CARDINALITIES}）"
            )

    def relation_label(self, predicate: str) -> str:
        rel = self.relations.get(predicate)
        return rel["label"] if rel else predicate

    def actions_for(self, type_id: str, status: str | None) -> list[dict]:
        return [
            a for a in self.raw["actions"]
            if a["target"] == type_id and (not a.get("from_status") or status in a["from_status"])
        ]

    def rules_for(self, type_id: str, props: dict) -> list[dict]:
        matched = []
        for rule in self.raw["rules"]:
            if rule["domain"] == type_id and match_condition(rule.get("when", {}), props):
                matched.append(rule)
        return matched

    # ------------------------------------------------------------------ 规则执行
    def diagnosis_required_types(self, seed_type: str) -> list[str]:
        """诊断域的结构依赖：由本体声明的诊断关系推导，不在代码里写答案键。

        diagnosis.required_relations 换了，这里推导出的依赖集就随之变化；
        没有声明时退化为整张关系图的可达类型。
        """
        spec = self.raw.get("diagnosis") or {}
        rel_ids = [r for r in (spec.get("required_relations") or []) if r in self.relations]
        if not rel_ids:
            return sorted(self._expand_types([seed_type], 4))
        selected = {seed_type} if seed_type in self.classes else set()
        frontier = set(selected)
        while frontier:
            nxt: set[str] = set()
            for rid in rel_ids:
                rel = self.relations[rid]
                domain, range_ = rel.get("domain"), rel.get("range")
                if domain in frontier and range_ in self.classes:
                    nxt.add(range_)
                if range_ in frontier and domain in self.classes:
                    nxt.add(domain)
            nxt -= selected
            if not nxt:
                break
            selected |= nxt
            frontier = nxt
        return sorted(selected)

    def closure_relation_specs(self) -> list[dict]:
        """强依赖闭包的关系白名单，来源只有语义层，不在代码里写死。

        closure.relations 显式声明“哪些关系参与对象上下文闭包、沿哪个方向走”。
        没有配置时退回 diagnosis.required_relations；两者都没有才退化为全关系图。
        """
        configured = self.closure_config.get("relations") or {}
        specs = [
            {**self.relations[rid], "closure_direction": direction}
            for rid, direction in configured.items()
            if rid in self.relations
        ]
        if specs:
            return specs
        diagnosis = self.raw.get("diagnosis") or {}
        return [
            {**self.relations[rid], "closure_direction": "both"}
            for rid in (diagnosis.get("required_relations") or [])
            if rid in self.relations
        ]

    def closure_max_depth(self, default: int = 6) -> int:
        try:
            depth = int(self.closure_config.get("max_depth") or default)
        except (TypeError, ValueError):
            depth = default
        return max(1, min(depth, 8))

    def evidence_relation_for(self, type_id: str) -> str | None:
        """返回该类型挂证据的关系，避免运行时维护第二份证据关系清单。"""
        for rel in self.raw["relations"]:
            if rel.get("domain") == type_id and rel.get("range") == "Evidence":
                return rel["id"]
        return None

    def _fee_context(self, fee_type: str, params: dict,
                     waybill: dict, vehicle_type: str = "") -> dict:
        config = dict(self.pricing.get(fee_type) or {})
        effective = dict(config)
        override = (config.get("by_vehicle_type") or {}).get(vehicle_type)
        if isinstance(override, dict):
            effective.update(override)
        # 定价规则实例上的参数优先于静态价格表，保证历史版本回放口径一致。
        for key in ("free_minutes", "block_minutes", "per_block"):
            if params.get(key) is not None:
                effective[key] = params[key]
        ctx = dict(waybill)
        ctx.update(params or {})
        ctx["vehicle_type"] = (
            str(params.get("vehicle_type"))
            if params.get("vehicle_type")
            else str(vehicle_type or waybill.get("vehicle_type") or "")
        )
        ctx["params"] = params or {}
        ctx["pricing"] = config
        ctx["tariff"] = effective
        ctx["rule"] = params or {}
        return ctx

    def compute_fee(self, fee_type: str, params: dict, waybill: dict,
                    vehicle_type: str = "") -> tuple[float | None, dict]:
        """执行本体中的 judge.compute 表达式，返回应计金额与中间量。

        公式只存在于 semantic_layer.json；删掉或改掉 compute，这里就得不到
        应计金额，业务结论随之改变，本体因此是承重的。
        """
        config = self.pricing.get(fee_type) or {}
        rule = self.rules.get(str(config.get("rule_id") or ""))
        if not rule:
            return None, {}
        judge = rule.get("judge") or {}
        ast_node = judge.get("ast")
        calc = judge.get("calc") or {}
        compute = judge.get("compute")
        # 只有声明了金额目标的 AST 才是费用计算公式；证据/一致性规则不参与算费。
        fee_ast = ast_node if calc.get("target") in FEE_AMOUNT_TARGETS else None
        if fee_ast is None and not compute:
            return None, {}
        ctx = self._fee_context(fee_type, params or {}, waybill or {}, vehicle_type)
        try:
            if fee_ast is not None:
                value = evaluate_rule_ast(fee_ast, ctx)
                meta = {
                    key: evaluate_rule_ast(expr, ctx)
                    for key, expr in (calc.get("bindings") or {}).items()
                }
            elif isinstance(compute, dict):
                result_expr = compute.get("result")
                if not result_expr:
                    return None, {}
                value = evaluate_expression(result_expr, ctx)
                meta = {
                    key: evaluate_expression(expr, ctx)
                    for key, expr in (compute.get("meta") or {}).items()
                }
            else:
                value = evaluate_expression(str(compute), ctx)
                meta = {}
        except ZeroDivisionError:
            return None, {}
        if value is None:
            return None, {}
        return round(float(value), 2), meta

    def evaluate_rule(self, rule_id: str, ctx: dict) -> dict:
        """执行单条规则：when 条件 + AST 计算/策略，返回可审计结果。

        这是 G1-03 的统一入口：公式规则走 AST；动作策略规则仍用
        forbidden_actions / allowed_actions 作为结构化策略，不执行字符串。
        """
        rule = self.rules.get(rule_id)
        if not rule:
            raise FormulaError(f"规则不存在: {rule_id}")
        judge = rule.get("judge") or {}
        matched = match_condition(rule.get("when") or {}, ctx)
        calc = judge.get("calc") or {}
        result = {
            "rule_id": rule_id,
            "name": rule.get("name"),
            "matched": matched,
            "explain": judge.get("explain"),
            "severity": judge.get("severity"),
            "allowed_actions": list(judge.get("allowed_actions") or []),
            "forbidden_actions": list(judge.get("forbidden_actions") or []),
        }
        ast_node = judge.get("ast")
        if ast_node is None:
            result["value"] = matched
            result["meta"] = {}
            return result
        if not matched:
            result.update({
                "value": None, "meta": {},
                "target": calc.get("target"),
                "unit": calc.get("unit"),
                "rounding": calc.get("rounding"),
            })
            return result
        result.update({
            "value": evaluate_rule_ast(ast_node, ctx),
            "target": calc.get("target"),
            "unit": calc.get("unit"),
            "rounding": calc.get("rounding"),
            "meta": {
                key: evaluate_rule_ast(expr, ctx)
                for key, expr in (calc.get("bindings") or {}).items()
            },
        })
        return result

    def validate(self, raw: dict | None = None) -> dict:
        """校验本体契约，按类型/关系/状态/规则/AST/引用完整性分组返回。

        `raw` 仅用于测试或门禁传入待校验副本；默认校验当前加载的语义层。
        """
        data = self.raw if raw is None else raw
        issues = validate_semantic(data)
        categories = {name: [] for name in VALIDATION_CATEGORIES}
        for issue in issues:
            categories.setdefault(issue.get("category", "references"), []).append(issue)
        dimensions = [
            {
                "id": name,
                "label": VALIDATION_LABELS[name],
                "ok": not categories[name],
                "count": len(categories[name]),
            }
            for name in VALIDATION_CATEGORIES
        ]
        version = None
        if isinstance(data, dict):
            version = (data.get("meta") or {}).get("version")
        return {
            "ok": not issues,
            "version": version,
            "dimensions": dimensions,
            "categories": categories,
            "summary": {
                "total": len(issues),
                "by_category": {
                    name: len(categories[name]) for name in VALIDATION_CATEGORIES
                },
            },
            "issues": issues,
        }

    def describe(self, types: list[str] | None = None,
                 fee_types: list[str] | None = None) -> dict:
        """按类型查询本体定义：类、邻接关系、适用规则与定价参数。

        入参只使用业务数据的类型和费用类型，不预先指定规则或关系 ID；
        规则、关系由本体图按作用域推导出来。
        """
        type_ids = [t for t in (types or []) if t in self.classes]
        fee_ids = [f for f in (fee_types or []) if f]
        described = {
            "classes": [self.classes[t] for t in type_ids],
            "relations": [
                r for r in self.raw["relations"]
                if r["domain"] in type_ids or r["range"] in type_ids
            ],
            "rules": [],
            "actions": [
                a for a in self.raw["actions"] if a["target"] in type_ids
            ],
            "pricing": {f: self.pricing[f] for f in fee_ids if f in self.pricing},
        }
        for rule in self.raw["rules"]:
            if rule["domain"] not in type_ids:
                continue
            conditions = (rule.get("when") or {}).get("all") or []
            rule_fee_types = {
                cond.get("value") for cond in conditions
                if cond.get("field") == "fee_type"
            }
            if rule_fee_types and fee_ids and not (rule_fee_types & set(fee_ids)):
                continue
            described["rules"].append(rule)
        return described

    def group_ids_for_types(self, type_ids: list[str] | set[str]) -> list[str]:
        wanted = set(type_ids or [])
        return [
            group["id"] for group in self.object_type_groups
            if wanted & set(group.get("types") or [])
        ]

    def catalog_budget(self, default: int = 6000) -> int:
        try:
            budget = int(self.catalog_config.get("context_budget_chars") or default)
        except (TypeError, ValueError):
            budget = default
        return max(500, budget)

    def _catalog_payload(self, type_ids: list[str], group_ids: list[str] | None,
                         mode: str, budget: int) -> dict:
        selected = [cid for cid in self.classes if cid in set(type_ids)]
        selected_set = set(selected)
        max_desc = self.catalog_config.get("description_max_chars") or 80
        compact = mode in ("full", "group")
        types = []
        for cid in selected:
            cls = self.classes[cid]
            entry = {
                "id": cid,
                "label": cls.get("label", cid),
                "groups": self.group_ids_for_types([cid]),
            }
            if not compact:
                description = str(cls.get("description", ""))
                if len(description) > max_desc:
                    description = description[:max_desc] + "…"
                entry.update({
                    "description": description,
                    "aliases": list(cls.get("aliases") or []),
                    "key": cls.get("key"),
                })
            types.append(entry)
        relations = []
        for rel in self.raw["relations"]:
            domain, range_ = rel.get("domain"), rel.get("range")
            if compact:
                # 紧凑目录只保留组内关系；跨组边界用 schema / search-around 展开。
                if domain not in selected_set or range_ not in selected_set:
                    continue
            elif domain not in selected_set and range_ not in selected_set:
                continue
            entry = {
                "id": rel["id"],
                "label": rel.get("label", rel["id"]),
                "domain": domain,
                "range": range_,
            }
            if not compact:
                entry["boundary"] = (
                    domain not in selected_set or range_ not in selected_set
                )
            relations.append(entry)
        groups = []
        for group in self.object_type_groups:
            members = [t for t in (group.get("types") or []) if t in selected_set]
            if not members:
                continue
            entry = {
                "id": group["id"],
                "label": group.get("label", group["id"]),
                "types": members,
            }
            if not compact:
                entry.update({
                    "description": group.get("description", ""),
                    "aliases": list(group.get("aliases") or []),
                })
            groups.append(entry)
        fee_types = list(self.pricing)
        payload = {
            "mode": mode,
            "group_ids": group_ids or [],
            "budget": budget,
            "types": types,
            "relations": relations,
            "groups": groups,
            "fee_types": fee_types,
            "stats": {
                "types": len(types),
                "relations": len(relations),
                "groups": len(groups),
            },
        }
        payload["characters"] = len(json.dumps(
            {"types": types, "relations": relations, "groups": groups,
             "fee_types": fee_types},
            ensure_ascii=False,
        ))
        payload["fits"] = payload["characters"] <= budget
        return payload

    def catalog(self, group_ids: list[str] | None = None,
                budget: int | None = None) -> dict:
        """返回紧凑 TBox 目录；装得下时全量返回，装不下时只返回业务域索引。"""
        limit = max(500, int(budget)) if budget else self.catalog_budget()
        if group_ids:
            requested = list(dict.fromkeys(str(g) for g in group_ids if g))
            unknown = [g for g in requested if g not in self.object_type_group_map]
            if unknown:
                raise BusinessReject(
                    f"未定义的对象类型组: {', '.join(unknown)}",
                    "SCHEMA_ERROR",
                    {"unknown": unknown, "allowed": sorted(self.object_type_group_map)},
                )
            type_ids: list[str] = []
            for gid in requested:
                for cid in self.object_type_group_map[gid].get("types") or []:
                    if cid in self.classes and cid not in type_ids:
                        type_ids.append(cid)
            payload = self._catalog_payload(type_ids, requested, "group", limit)
            payload["message"] = (
                f"已按 {len(requested)} 个对象类型组缩域，"
                f"返回 {len(payload['types'])} 个类型。"
            )
            return payload

        payload = self._catalog_payload(list(self.classes), None, "full", limit)
        if payload["fits"]:
            payload["message"] = (
                f"全量紧凑目录 {payload['characters']} 字符，"
                f"在 {limit} 字符预算内，直接注入。"
            )
            return payload

        payload["mode"] = "overflow"
        payload["types"] = []
        payload["relations"] = []
        payload["fee_types"] = []
        payload["groups"] = self.catalog_groups()
        payload["message"] = (
            f"全量目录 {payload['characters']} 字符，超出 {limit} 字符预算；"
            "请先选 object type group 缩域。"
        )
        return payload

    def catalog_groups(self) -> list[dict]:
        groups = []
        for group in self.object_type_groups:
            type_ids = [t for t in (group.get("types") or []) if t in self.classes]
            payload = self._catalog_payload(
                type_ids, [group["id"]], "group", self.catalog_budget(),
            )
            description = str(group.get("description", ""))
            if len(description) > 60:
                description = description[:60] + "…"
            groups.append({
                "id": group["id"],
                "label": group.get("label", group["id"]),
                "description": description,
                "aliases": list(group.get("aliases") or []),
                "type_count": len(type_ids),
                "characters": payload["characters"],
            })
        return groups

    def object_type_search(self, question: str, top_k: int = 8,
                           group_ids: list[str] | None = None) -> dict:
        """按对象类型元数据检索锚点类型，对应 AIP Analyst 的 object type search。"""
        query = (question or "").strip().lower()
        terms: dict[str, int] = {}
        for i in range(max(0, len(query) - 1)):
            gram = query[i:i + 2]
            if gram.strip():
                terms[gram] = terms.get(gram, 0) + 2
        for word in re.findall(r"[a-z0-9_]{2,}", query):
            terms[word] = terms.get(word, 0) + 3

        allowed_groups = set(group_ids or [])
        if allowed_groups:
            unknown = [g for g in allowed_groups if g not in self.object_type_group_map]
            if unknown:
                raise BusinessReject(
                    f"未定义的对象类型组: {', '.join(sorted(unknown))}",
                    "SCHEMA_ERROR",
                    {"unknown": sorted(unknown), "allowed": sorted(self.object_type_group_map)},
                )

        hits = []
        for cid, cls in self.classes.items():
            member_groups = self.group_ids_for_types([cid])
            if allowed_groups and not (set(member_groups) & allowed_groups):
                continue
            label = str(cls.get("label", cid))
            aliases = [str(x) for x in (cls.get("aliases") or [])]
            group_text = " ".join(
                f"{self.object_type_group_map[g].get('label', g)} "
                f"{self.object_type_group_map[g].get('description', '')} "
                f"{' '.join(map(str, self.object_type_group_map[g].get('aliases') or []))}"
                for g in member_groups
            )
            fields = {
                "id": cid.lower(),
                "label": label.lower(),
                "description": str(cls.get("description", "")).lower(),
                "aliases": " ".join(aliases).lower(),
                "group": group_text.lower(),
            }
            score = 0
            matched_fields = set()
            if query:
                if query == fields["id"] or query == fields["label"]:
                    score += 20
                    matched_fields.add("id" if query == fields["id"] else "label")
                elif query in fields["id"]:
                    score += 8
                    matched_fields.add("id")
                if query in fields["label"] and "label" not in matched_fields:
                    score += 6
                    matched_fields.add("label")
                if query in fields["description"]:
                    score += 3
                    matched_fields.add("description")
                if query in fields["aliases"]:
                    score += 6
                    matched_fields.add("aliases")
                if query in fields["group"]:
                    score += 2
                    matched_fields.add("group")
            for term, weight in terms.items():
                for field_name, text in fields.items():
                    if term in text:
                        # 类型名称与检索词完全一致时优先锚定，避免被同域
                        # 生成类型的描述/别名淹没。
                        score += weight + (4 if field_name == "label" and term == text else 0)
                        matched_fields.add(field_name)
            if score <= 0:
                continue
            hits.append({
                "kind": "class",
                "id": cid,
                "label": label,
                "score": score,
                "matched_fields": sorted(matched_fields),
                "groups": member_groups,
            })

        # 派生对象类型也参与类型发现（kind="derived"）：
        # 用户问"承运资源/司机车辆"能锚定到派生视图，而非只知道实体类。
        for did, dt in {d["id"]: d
                        for d in self.raw.get("derived_types", [])}.items():
            label = str(dt.get("label", did))
            aliases = [str(x) for x in (dt.get("aliases") or [])]
            fields = {
                "id": did.lower(),
                "label": label.lower(),
                "description": str(dt.get("description", "")).lower(),
                "aliases": " ".join(aliases).lower(),
            }
            score = 0
            matched_fields = set()
            if query:
                if query in fields["id"]:
                    score += 8
                    matched_fields.add("id")
                if query in fields["label"]:
                    score += 6
                    matched_fields.add("label")
                if query in fields["aliases"]:
                    score += 6
                    matched_fields.add("aliases")
                if query in fields["description"]:
                    score += 3
                    matched_fields.add("description")
            for term, weight in terms.items():
                for field_name, text in fields.items():
                    if term in text:
                        score += weight
                        matched_fields.add(field_name)
            if score <= 0:
                continue
            hits.append({
                "kind": "derived",
                "id": did,
                "label": label,
                "score": score,
                "matched_fields": sorted(matched_fields),
                "groups": [],
            })

        # 费用类型命中时，把费用关联回它的本体作用域（通常是 FeeItem）。
        fee_anchor_scores: dict[str, int] = {}
        for fee_type in self.pricing:
            fee_text = self._fee_text(fee_type).lower()
            fee_score = sum(weight for term, weight in terms.items() if term in fee_text)
            if fee_type.lower() in query:
                fee_score += 5
            if fee_score <= 0:
                continue
            pricing = self.pricing.get(fee_type) or {}
            rule = self.rules.get(pricing.get("rule_id") or "") or {}
            domain = rule.get("domain")
            if domain in self.classes:
                fee_anchor_scores[domain] = max(
                    fee_anchor_scores.get(domain, 0), fee_score,
                )
        by_id = {hit["id"]: hit for hit in hits}
        for cid, fee_score in fee_anchor_scores.items():
            member_groups = self.group_ids_for_types([cid])
            if allowed_groups and not (set(member_groups) & allowed_groups):
                continue
            hit = by_id.get(cid)
            if hit is None:
                hit = {
                    "kind": "class",
                    "id": cid,
                    "label": self.classes[cid].get("label", cid),
                    "score": 0,
                    "matched_fields": [],
                    "groups": member_groups,
                }
                hits.append(hit)
                by_id[cid] = hit
            hit["score"] += fee_score
            if "fee_type" not in hit["matched_fields"]:
                hit["matched_fields"].append("fee_type")
        hits.sort(key=lambda item: (-item["score"], item["id"]))
        hits = hits[:max(1, min(int(top_k), 20))]

        group_hits = []
        for gid in self.group_ids_for_types([h["id"] for h in hits]):
            group = self.object_type_group_map[gid]
            group_hits.append({
                "id": gid,
                "label": group.get("label", gid),
                "aliases": list(group.get("aliases") or []),
                "types": [t for t in (group.get("types") or []) if t in self.classes],
            })
        return {
            "question": question,
            "group_ids": sorted(allowed_groups),
            "types": hits,
            "groups": group_hits,
            "message": "命中对象类型锚点。" if hits else "没有命中对象类型锚点。",
        }

    def schema_search_around(self, type_ids: list[str],
                             link_type_ids: list[str] | None = None,
                             direction: str = "both") -> dict:
        """按 link type 从锚点类型展开一跳邻居，不计算全局闭包。"""
        roots = [t for t in dict.fromkeys(type_ids or []) if t]
        unknown = [t for t in roots if t not in self.classes]
        if unknown:
            raise BusinessReject(
                f"未定义的本体类型: {', '.join(unknown)}",
                "SCHEMA_ERROR",
                {"unknown": unknown, "allowed": sorted(self.classes)},
            )
        requested = [r for r in (link_type_ids or []) if r]
        unknown_links = [r for r in requested if r not in self.relations]
        if unknown_links:
            raise BusinessReject(
                f"未定义的关系: {', '.join(unknown_links)}",
                "SCHEMA_ERROR",
                {"unknown": unknown_links, "allowed": sorted(self.relations)},
            )
        direction = direction if direction in ("out", "in", "both") else "both"
        root_set = set(roots)
        neighbors: list[str] = []
        relations = []
        for rel in self.raw["relations"]:
            if requested and rel["id"] not in requested:
                continue
            domain, range_ = rel.get("domain"), rel.get("range")
            step_direction = None
            if direction in ("out", "both") and domain in root_set:
                step_direction = "out"
            elif direction in ("in", "both") and range_ in root_set:
                step_direction = "in"
            if not step_direction:
                continue
            other = range_ if step_direction == "out" else domain
            if other not in self.classes:
                continue
            if other not in neighbors:
                neighbors.append(other)
            relations.append({
                "id": rel["id"],
                "label": rel.get("label", rel["id"]),
                "domain": domain,
                "range": range_,
                "direction": step_direction,
            })
        return {
            "anchor_types": roots,
            "direction": direction,
            "types": neighbors,
            "relations": relations,
            "stats": {"anchors": len(roots), "neighbors": len(neighbors),
                      "relations": len(relations)},
        }

    def search(self, question: str, top_k: int = 8,
               fee_top_k: int = 4,
               relation_specs: list[dict] | None = None) -> dict:
        """按业务问题关键词召回 top-k 类型与费用类型，不读取完整定义。

        只返回 id、label、相关度和召回原因，供大模型圈定范围；
        需要字段、关系、规则时再调用 describe。类型总数严格不超过 top_k，
        与本体规模无关；后续可把打分替换成向量检索。

        relation_specs 控制命中类型的一跳邻接扩展：强依赖模式下只沿白名单
        展开，避免弱关联先混进种子再被闭包继承；direct keyword matches 不受限。
        """
        query = (question or "").strip().lower()
        terms: dict[str, int] = {}
        for i in range(max(0, len(query) - 1)):
            gram = query[i:i + 2]
            if gram.strip():
                terms[gram] = terms.get(gram, 0) + 2
        for word in re.findall(r"[a-z0-9_]{2,}", query):
            terms[word] = terms.get(word, 0) + 3

        def score(text: str, label: str = "") -> int:
            low = text.lower()
            total = sum(weight for term, weight in terms.items() if term in low)
            if label and label.lower() in query:
                total += 5
            return total

        scores = {
            cid: score(self._class_text(cid), cls.get("label", ""))
            for cid, cls in self.classes.items()
        }
        fee_scores = {
            fee_type: score(self._fee_text(fee_type), fee_type)
            for fee_type in self.pricing
        }
        # 费用关键词命中时，把分数回传给其规则作用域（通常是 FeeItem）。
        for fee_type, fee_score in fee_scores.items():
            if not fee_score:
                continue
            pricing = self.pricing.get(fee_type) or {}
            rule = self.rules.get(pricing.get("rule_id") or "") or {}
            domain = rule.get("domain")
            if domain in scores:
                scores[domain] = max(scores[domain], fee_score)

        positive = {cid: value for cid, value in scores.items() if value > 0}
        hit_ids = sorted(positive, key=lambda cid: (-positive[cid], cid))[:max(1, top_k)]
        matched = {cid: positive[cid] for cid in hit_ids}

        # 一跳邻接扩展：命中类型的直接关联类型也计入候选，仍受 top_k 总数限制。
        # 强依赖模式只沿白名单和声明方向展开，弱关联不会先污染种子集合。
        neighbors = relation_specs if relation_specs is not None else [
            {**rel, "closure_direction": "both"} for rel in self.raw["relations"]
        ]
        expanded = dict(matched)
        for rel in neighbors:
            direction = rel.get("closure_direction", "both")
            domain, range_ = rel.get("domain"), rel.get("range")
            if direction in ("out", "both") and domain in matched and range_ in self.classes:
                expanded[range_] = max(expanded.get(range_, 0), matched[domain] // 2)
            if direction in ("in", "both") and range_ in matched and domain in self.classes:
                expanded[domain] = max(expanded.get(domain, 0), matched[range_] // 2)

        # 候选不足 top_k 时按 id 补齐，保证规划器始终拿到固定规模的候选集。
        if len(expanded) < top_k:
            for cid in sorted(scores, key=lambda c: (-scores[c], c)):
                if len(expanded) >= top_k:
                    break
                expanded.setdefault(cid, scores[cid])

        types = []
        for cid, value in expanded.items():
            if cid in matched:
                reason = "关键词命中"
            elif value > scores[cid]:
                reason = "邻接扩展"
            else:
                reason = "兜底候选"
            types.append({
                "kind": "class",
                "id": cid,
                "label": self.classes[cid].get("label", cid),
                "score": value,
                "reason": reason,
            })
        types.sort(key=lambda item: (-item["score"], item["id"]))
        types = types[:max(1, top_k)]

        fee_ids = sorted(fee_scores, key=lambda f: (-fee_scores[f], f))[:max(1, fee_top_k)]
        fee_types = [
            {
                "kind": "pricing",
                "id": f"pricing.{fee_type}",
                "fee_type": fee_type,
                "label": fee_type,
                "score": fee_scores[fee_type],
                "reason": "关键词命中" if fee_scores[fee_type] else "费用类型候选",
            }
            for fee_type in fee_ids
        ]
        return {
            "question": query,
            "types": types,
            "fee_types": fee_types,
        }

    def schema(self, question: str | None = None,
               types: list[str] | None = None,
               top_k: int = 12, depth: int = 2,
               fee_top_k: int = 4, closure: str = "strong") -> dict:
        """读取 schema：类型、属性、关系；可按问题聚焦并做邻接闭包。

        closure="strong" 只沿语义层 closure.relations 白名单展开，方向可控，
        不会因为 TBox 里存在一条弱关联就把整张图拖进来；closure="all"
        保留旧的全关系双向遍历，供显式全量检索使用。
        """
        closure_mode = "strong" if closure == "strong" else "all"
        specs = self.closure_relation_specs() if closure_mode == "strong" else None
        if closure_mode == "strong" and not specs:
            closure_mode = "all"

        seeds: list[str]
        search_result: dict | None = None
        if types:
            unknown = [t for t in types if t not in self.classes]
            if unknown:
                raise BusinessReject(
                    f"未定义的本体类型: {', '.join(unknown)}",
                    "SCHEMA_ERROR",
                    {"unknown": unknown, "allowed": sorted(self.classes)},
                )
            seeds = list(dict.fromkeys(types))
            mode = "explicit"
        elif question:
            search_result = self.search(
                question, top_k=top_k, fee_top_k=fee_top_k,
                relation_specs=specs if closure_mode == "strong" else None,
            )
            seeds = [item["id"] for item in search_result["types"]]
            mode = "focused"
        else:
            seeds = list(self.classes)
            mode = "full"

        if mode == "full":
            selected = set(self.classes)
            closure_mode = "all"
            specs = None
        else:
            selected = self._expand_types(seeds, depth, specs)
        relation_ids = (
            {rel["id"] for rel in specs}
            if specs is not None
            else set(self.relations)
        )
        reasons = {
            cid: ("种子类型" if cid in seeds else "邻接闭包")
            for cid in selected
        }
        if search_result:
            for item in search_result["types"]:
                if item["id"] in reasons:
                    reasons[item["id"]] = item["reason"]

        classes = []
        for cid in sorted(selected):
            cls = self.classes[cid]
            props = cls.get("properties", [])
            property_names = list(props) if not isinstance(props, dict) else list(props)
            classes.append({
                "type": cid,
                "id": cid,
                "label": cls.get("label", cid),
                "description": cls.get("description", ""),
                "key": cls.get("key"),
                "properties": property_names,
                "statuses": cls.get("statuses", []),
                "reason": reasons.get(cid, "邻接闭包"),
            })

        relations = []
        for rel in self.raw["relations"]:
            if rel["id"] not in relation_ids:
                continue
            domain, range_ = rel.get("domain"), rel.get("range")
            if domain not in selected and range_ not in selected:
                continue
            relations.append({
                **rel,
                "boundary": domain not in selected or range_ not in selected,
            })

        if search_result:
            fee_types = [
                item["fee_type"] for item in search_result["fee_types"]
            ]
        else:
            fee_types = list(self.pricing)

        return {
            "question": question,
            "mode": mode,
            "depth": depth,
            "seed_types": seeds,
            "types": [cls["type"] for cls in classes],
            "classes": classes,
            "relations": relations,
            "closure": {
                "mode": closure_mode,
                "max_depth": self.closure_max_depth() if closure_mode == "strong" else depth,
                "relation_ids": sorted(relation_ids),
                "excluded_relation_ids": sorted(set(self.relations) - relation_ids),
            },
            "fee_types": fee_types,
            "stats": {
                "classes": len(classes),
                "relations": len(relations),
                "seed_types": len(seeds),
                "expanded_types": len(classes) - len(seeds),
            },
        }

    def schema_search(self, question: str, top_k: int = 8, depth: int = 2,
                      fee_top_k: int = 4) -> dict:
        """按自然语言问题检索 schema，并返回 depth 跳闭包。"""
        return self.schema(
            question=question, top_k=top_k, depth=depth, fee_top_k=fee_top_k
        )

    def _expand_types(self, seed_types: list[str], depth: int,
                      relation_specs: list[dict] | None = None) -> set[str]:
        """按关系白名单和方向做逐跳闭包。

        relation_specs 为 None 时才退化为全关系双向遍历；强依赖模式传入的
        spec 带 closure_direction（out/in/both），不会把每条弱关联都当成依赖。
        """
        if relation_specs is None:
            relation_specs = [
                {**rel, "closure_direction": "both"}
                for rel in self.raw["relations"]
            ]
        selected = {t for t in seed_types if t in self.classes}
        frontier = set(selected)
        for _ in range(max(0, depth)):
            next_frontier: set[str] = set()
            for rel in relation_specs:
                direction = rel.get("closure_direction", "both")
                domain, range_ = rel.get("domain"), rel.get("range")
                if direction in ("out", "both") and domain in frontier and range_ in self.classes:
                    next_frontier.add(range_)
                if direction in ("in", "both") and range_ in frontier and domain in self.classes:
                    next_frontier.add(domain)
            next_frontier -= selected
            if not next_frontier:
                break
            selected |= next_frontier
            frontier = next_frontier
        return selected

    def paths(self, from_type: str, to_type: str,
              max_depth: int = 4, limit: int = 10) -> list[dict]:
        """查询两个类型之间的合法关系路径，方向按关系定义双向展开。"""
        unknown = [
            t for t in (from_type, to_type) if t not in self.classes
        ]
        if unknown:
            raise BusinessReject(
                f"未定义的本体类型: {', '.join(unknown)}",
                "SCHEMA_ERROR",
                {"unknown": unknown, "allowed": sorted(self.classes)},
            )
        limit = max(1, min(int(limit), 50))
        max_depth = max(1, min(int(max_depth), 8))
        if from_type == to_type:
            return [{
                "from_type": from_type, "to_type": to_type,
                "depth": 0, "steps": [],
            }]

        adjacency: dict[str, list[tuple[str, dict, str]]] = {}
        for rel in self.raw["relations"]:
            domain, range_ = rel.get("domain"), rel.get("range")
            if domain in self.classes and range_ in self.classes:
                adjacency.setdefault(domain, []).append((range_, rel, "out"))
                adjacency.setdefault(range_, []).append((domain, rel, "in"))

        found: list[dict] = []
        queue = deque([(from_type, [], {from_type})])
        while queue and len(found) < limit:
            node, steps, visited = queue.popleft()
            if len(steps) >= max_depth:
                continue
            for next_type, rel, direction in adjacency.get(node, []):
                if next_type in visited:
                    continue
                step = {
                    "predicate": rel["id"],
                    "label": rel.get("label", rel["id"]),
                    "from": node,
                    "to": next_type,
                    "direction": direction,
                    "domain": rel.get("domain"),
                    "range": rel.get("range"),
                }
                next_steps = steps + [step]
                if next_type == to_type:
                    found.append({
                        "from_type": from_type,
                        "to_type": to_type,
                        "depth": len(next_steps),
                        "steps": next_steps,
                    })
                else:
                    queue.append((
                        next_type, next_steps, visited | {next_type}
                    ))
        return found

    def _class_text(self, type_id: str) -> str:
        cls = self.classes[type_id]
        properties = cls.get("properties", [])
        if isinstance(properties, dict):
            properties = list(properties)
        groups = self.group_ids_for_types([type_id])
        group_text = " ".join(
            f"{self.object_type_group_map[g].get('label', g)} "
            f"{' '.join(map(str, self.object_type_group_map[g].get('aliases') or []))}"
            for g in groups
        )
        return " ".join([
            type_id,
            str(cls.get("label", "")),
            str(cls.get("description", "")),
            " ".join(map(str, cls.get("aliases") or [])),
            str(cls.get("key", "")),
            " ".join(map(str, properties)),
            " ".join(map(str, cls.get("statuses", []))),
            group_text,
        ])

    def _fee_text(self, fee_type: str) -> str:
        parts = [fee_type]
        pricing = self.pricing.get(fee_type) or {}
        rule = self.rules.get(pricing.get("rule_id") or "")
        if rule:
            judge = rule.get("judge") or {}
            parts.extend([
                str(rule.get("name", "")),
                str(judge.get("formula", "")),
                str(judge.get("explain", "")),
            ])
        return " ".join(parts)

    def dictionary(self) -> dict:
        return {
            "meta": self.raw["meta"],
            "classes": list(self.raw["classes"]),
            "class_count": len(self.classes),
            "relations": list(self.raw["relations"]),
            "relation_count": len(self.relations),
            "rules": list(self.raw["rules"]),
            "rule_count": len(self.rules),
            "actions": list(self.raw["actions"]),
            "action_count": len(self.actions),
            "functions": list(self.raw.get("functions", [])),
            "function_count": len(self.functions),
            "derived_types": list(self.raw.get("derived_types", [])),
            "derived_type_count": len(self.raw.get("derived_types", [])),
            "primitives": list(self.raw.get("primitives", [])),
            "primitive_count": len(self.primitives),
            "pricing": self.pricing,
            "data_sources": list(self.data_sources),
            "relation_source": self.relation_source,
        }


def _atom(atom: dict, ctx: dict) -> bool:
    left = ctx.get(atom["field"])
    right = atom.get("value")
    op = atom["op"]
    if op == "eq":
        return left == right
    if op == "ne":
        return left != right
    if op == "in":
        return left in (right or [])
    if op == "not_in":
        return left not in (right or [])
    if op == "is_null":
        return left is None
    if op == "not_null":
        return left is not None
    if op == "gt":
        return left is not None and left > right
    if op == "gte":
        return left is not None and left >= right
    if op == "lt":
        return left is not None and left < right
    if op == "lte":
        return left is not None and left <= right
    raise ValueError(f"不支持的条件操作符: {op}")


def match_condition(cond: dict, ctx: dict) -> bool:
    if not cond:
        return True
    if "all" in cond:
        return all(match_condition(x, ctx) for x in cond["all"])
    if "any" in cond:
        return any(match_condition(x, ctx) for x in cond["any"])
    return _atom(cond, ctx)


class FormulaError(ValueError):
    """本体公式无法执行时抛出，用于区分‘规则没算’和‘规则算错’。"""


_SAFE_FUNCS = {
    "abs": abs, "min": min, "max": max, "round": round,
    "ceil": math.ceil, "floor": math.floor, "sum": sum, "len": len,
}


def is_expression(expr: str) -> bool:
    """判断一段文字是否是可执行的金额公式，而不是自然语言说明。"""
    if not isinstance(expr, str) or not expr.strip():
        return False
    try:
        ast.parse(expr, mode="eval")
    except SyntaxError:
        return False
    return True


def evaluate_expression(expr: str, ctx: dict):
    """执行语义层里的 judge.compute 公式。

    只支持取值、算术、比较、布尔、白名单函数和下标/字段读取，
    不接受赋值、属性调用或任何可产生副作用的话法。
    """
    if not isinstance(expr, str) or not expr.strip():
        raise FormulaError("空表达式")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise FormulaError(f"表达式语法错误: {expr!r}") from exc
    return _eval_node(tree.body, ctx)


def _eval_node(node, ctx: dict):
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float, str, bool)) or node.value is None:
            return node.value
        raise FormulaError("不支持的常量类型")
    if isinstance(node, ast.Name):
        if node.id in ctx:
            return ctx[node.id]
        raise FormulaError(f"未定义变量: {node.id}")
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, ctx)
        right = _eval_node(node.right, ctx)
        op = node.op
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, ast.Div):
            return left / right
        if isinstance(op, ast.FloorDiv):
            return left // right
        if isinstance(op, ast.Mod):
            return left % right
        if isinstance(op, ast.Pow):
            return left ** right
        raise FormulaError("不支持的算术运算")
    if isinstance(node, ast.UnaryOp):
        value = _eval_node(node.operand, ctx)
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return +value
        if isinstance(node.op, ast.Not):
            return not value
        raise FormulaError("不支持的一元运算")
    if isinstance(node, ast.BoolOp):
        values = [_eval_node(v, ctx) for v in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, ctx)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, ctx)
            if not _compare(op, left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_FUNCS:
            raise FormulaError("只允许白名单函数")
        args = [_eval_node(arg, ctx) for arg in node.args]
        return _SAFE_FUNCS[node.func.id](*args)
    if isinstance(node, ast.Subscript):
        value = _eval_node(node.value, ctx)
        key = _eval_node(node.slice, ctx)
        try:
            return value[key]
        except (KeyError, IndexError, TypeError) as exc:
            raise FormulaError(f"无法按下标读取: {key!r}") from exc
    if isinstance(node, ast.Attribute):
        value = _eval_node(node.value, ctx)
        if isinstance(value, dict) and node.attr in value:
            return value[node.attr]
        raise FormulaError(f"字段不存在: {node.attr}")
    if isinstance(node, ast.IfExp):
        return (
            _eval_node(node.body, ctx)
            if _eval_node(node.test, ctx)
            else _eval_node(node.orelse, ctx)
        )
    raise FormulaError(f"不支持的表达式节点: {type(node).__name__}")


def _compare(op, left, right) -> bool:
    if isinstance(op, ast.Eq):
        return left == right
    if isinstance(op, ast.NotEq):
        return left != right
    if isinstance(op, ast.Lt):
        return left < right
    if isinstance(op, ast.LtE):
        return left <= right
    if isinstance(op, ast.Gt):
        return left > right
    if isinstance(op, ast.GtE):
        return left >= right
    raise FormulaError("不支持的比较运算")


# ---------------------------------------------------------------- 规则 AST 执行
AST_ARITY: dict[str, tuple[int, int | None]] = {
    "add": (2, 2), "sub": (2, 2), "mul": (2, 2), "div": (2, 2),
    "floordiv": (2, 2), "mod": (2, 2), "pow": (2, 2),
    "neg": (1, 1), "not": (1, 1), "and": (1, None), "or": (1, None),
    "eq": (2, 2), "ne": (2, 2), "lt": (2, 2), "lte": (2, 2),
    "gt": (2, 2), "gte": (2, 2), "in": (2, 2), "not_in": (2, 2),
    "if": (3, 3), "min": (1, None), "max": (1, None), "sum": (1, 1),
    "abs": (1, 1), "ceil": (1, 1), "floor": (1, 1), "round": (1, 2),
    "len": (1, 1), "index": (2, 2), "evidence_present": (1, 1),
}
AST_OPS = set(AST_ARITY) | {"const", "field"}
FEE_AMOUNT_TARGETS = {"actual_amount", "expected_amount", "amount"}


def _ast_field(path: str, ctx: dict):
    if path in ctx:
        return ctx[path]
    value = ctx
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            raise FormulaError(f"规则 AST 字段不存在: {path}")
    return value


def evaluate_rule_ast(node: dict, ctx: dict):
    """白名单求值规则 AST，禁止任意代码执行。"""
    if not isinstance(node, dict):
        raise FormulaError("规则 AST 节点必须是对象")
    op = node.get("op")
    if op not in AST_OPS:
        raise FormulaError(f"未知规则 AST 操作: {op!r}")

    if op == "const":
        if "value" not in node:
            raise FormulaError("const 节点缺少 value")
        return node["value"]
    if op == "field":
        path = node.get("path")
        if not isinstance(path, str) or not path:
            raise FormulaError("field 节点缺少 path")
        return _ast_field(path, ctx)

    args = node.get("args")
    if not isinstance(args, list):
        raise FormulaError(f"规则 AST 操作 {op} 缺少 args 数组")
    minimum, maximum = AST_ARITY[op]
    if len(args) < minimum or (maximum is not None and len(args) > maximum):
        raise FormulaError(f"规则 AST 操作 {op} 参数个数非法: {len(args)}")

    if op == "and":
        return all(evaluate_rule_ast(arg, ctx) for arg in args)
    if op == "or":
        return any(evaluate_rule_ast(arg, ctx) for arg in args)
    if op == "if":
        branch = args[1] if evaluate_rule_ast(args[0], ctx) else args[2]
        return evaluate_rule_ast(branch, ctx)

    values = [evaluate_rule_ast(arg, ctx) for arg in args]
    if op == "add":
        return values[0] + values[1]
    if op == "sub":
        return values[0] - values[1]
    if op == "mul":
        return values[0] * values[1]
    if op == "div":
        return values[0] / values[1]
    if op == "floordiv":
        return values[0] // values[1]
    if op == "mod":
        return values[0] % values[1]
    if op == "pow":
        return values[0] ** values[1]
    if op == "neg":
        return -values[0]
    if op == "not":
        return not values[0]
    if op == "eq":
        return values[0] == values[1]
    if op == "ne":
        return values[0] != values[1]
    if op == "lt":
        return values[0] < values[1]
    if op == "lte":
        return values[0] <= values[1]
    if op == "gt":
        return values[0] > values[1]
    if op == "gte":
        return values[0] >= values[1]
    if op == "in":
        return values[0] in values[1]
    if op == "not_in":
        return values[0] not in values[1]
    if op == "min":
        return min(values[0] if len(values) == 1 and isinstance(values[0], list) else values)
    if op == "max":
        return max(values[0] if len(values) == 1 and isinstance(values[0], list) else values)
    if op == "sum":
        return sum(values[0])
    if op == "abs":
        return abs(values[0])
    if op == "ceil":
        return math.ceil(values[0])
    if op == "floor":
        return math.floor(values[0])
    if op == "round":
        return round(values[0]) if len(values) == 1 else round(values[0], int(values[1]))
    if op == "len":
        return len(values[0])
    if op == "index":
        try:
            return values[0][values[1]]
        except (KeyError, IndexError, TypeError) as exc:
            raise FormulaError(f"规则 AST 无法按下标读取: {values[1]!r}") from exc
    if op == "evidence_present":
        name = values[0]
        evidence = ctx.get("evidence") or ctx.get("evidence_types") or []
        for item in evidence:
            if item == name:
                return True
            if isinstance(item, dict) and name in {
                item.get("type"), item.get("kind"),
                item.get("code"), item.get("source_system"),
            }:
                return True
        return False
    raise FormulaError(f"未实现的规则 AST 操作: {op}")


def validate_rule_ast(node: dict, path: str, add) -> None:
    """结构校验规则 AST；运行时字段解析仍在 evaluate_rule_ast。"""
    if not isinstance(node, dict):
        add("RULE_AST_NODE_INVALID", path, "规则 AST 节点必须是对象")
        return
    op = node.get("op")
    if op not in AST_OPS:
        add("RULE_AST_OP_INVALID", f"{path}.op", f"未知规则 AST 操作: {op!r}")
        return
    if op == "field":
        if not isinstance(node.get("path"), str) or not node.get("path"):
            add("RULE_AST_PATH_MISSING", f"{path}.path", "field 节点缺少 path")
        return
    if op == "const":
        if "value" not in node:
            add("RULE_AST_VALUE_MISSING", f"{path}.value", "const 节点缺少 value")
        return
    args = node.get("args")
    if not isinstance(args, list):
        add("RULE_AST_ARGS_MISSING", f"{path}.args", f"{op} 节点缺少 args 数组")
        return
    minimum, maximum = AST_ARITY[op]
    if len(args) < minimum or (maximum is not None and len(args) > maximum):
        add("RULE_AST_ARITY_INVALID", f"{path}.args",
            f"{op} 参数个数非法: {len(args)}，期望 {minimum}..{maximum or 'N'}")
    for index, arg in enumerate(args):
        validate_rule_ast(arg, f"{path}.args[{index}]", add)


VALID_OPS = {"eq", "ne", "in", "not_in", "is_null", "not_null",
             "gt", "gte", "lt", "lte"}
ACTION_PARAM_TYPES = {"string", "number", "integer", "boolean", "object", "array"}

VALIDATION_CATEGORIES = ("types", "relations", "statuses", "rules", "ast", "references")
VALIDATION_LABELS = {
    "types": "类型与属性",
    "relations": "关系定义域与值域",
    "statuses": "状态与动作",
    "rules": "规则与定价",
    "ast": "规则 AST",
    "references": "引用完整性",
}

ISSUE_CATEGORY = {
    "ROOT_NOT_OBJECT": "types",
    "META_MISSING": "references",
    "VERSION_MISSING": "references",
    "NAME_MISSING": "references",
    "CLASSES_MISSING": "types",
    "CLASS_NOT_OBJECT": "types",
    "CLASS_ID_MISSING": "types",
    "CLASS_DUPLICATE": "types",
    "CLASS_PROPERTIES_INVALID": "types",
    "CLASS_STATUSES_INVALID": "statuses",
    "CLASS_KEY_MISSING": "types",
    "CLASS_KEY_NOT_PROPERTY": "types",
    "CLASS_LABEL_MISSING": "types",
    "FEE_TYPE_PROPERTY_MISSING": "types",
    "RELATIONS_INVALID": "relations",
    "RELATION_NOT_OBJECT": "relations",
    "RELATION_ID_MISSING": "relations",
    "RELATION_DUPLICATE": "relations",
    "RELATION_DOMAIN_UNKNOWN": "relations",
    "RELATION_RANGE_UNKNOWN": "relations",
    "RELATION_LABEL_MISSING": "relations",
    "PRICING_MISSING": "rules",
    "PRICING_ITEM_INVALID": "rules",
    "PRICING_RULE_MISSING": "rules",
    "PRICING_RULE_DOMAIN_INVALID": "rules",
    "PRICING_NUMBER_INVALID": "rules",
    "PRICING_VEHICLE_TYPE_INVALID": "rules",
    "ACTIONS_INVALID": "statuses",
    "ACTION_NOT_OBJECT": "statuses",
    "ACTION_ID_MISSING": "statuses",
    "ACTION_DUPLICATE": "statuses",
    "ACTION_TARGET_UNKNOWN": "references",
    "ACTION_FROM_STATUS_INVALID": "statuses",
    "ACTION_FROM_STATUS_UNKNOWN": "statuses",
    "ACTION_TO_STATUS_UNKNOWN": "statuses",
    "ACTION_ROLES_INVALID": "statuses",
    "ACTION_PARAMS_INVALID": "statuses",
    "ACTION_PARAM_NOT_OBJECT": "statuses",
    "ACTION_PARAM_NAME_MISSING": "statuses",
    "ACTION_PARAM_TYPE_INVALID": "statuses",
    "RULES_INVALID": "rules",
    "RULE_NOT_OBJECT": "rules",
    "RULE_ID_MISSING": "rules",
    "RULE_DUPLICATE": "rules",
    "RULE_DOMAIN_UNKNOWN": "references",
    "RULE_CONDITION_INVALID": "rules",
    "RULE_FIELD_UNKNOWN": "rules",
    "RULE_OP_UNSUPPORTED": "rules",
    "UNKNOWN_FEE_TYPE": "rules",
    "RULE_JUDGE_MISSING": "rules",
    "RULE_JUDGE_FIELD_MISSING": "rules",
    "RULE_AST_NODE_INVALID": "ast",
    "RULE_AST_OP_INVALID": "ast",
    "RULE_AST_PATH_MISSING": "ast",
    "RULE_AST_VALUE_MISSING": "ast",
    "RULE_AST_ARGS_MISSING": "ast",
    "RULE_AST_ARITY_INVALID": "ast",
    "RULE_CALC_INVALID": "ast",
    "RULE_COMPUTE_INVALID": "rules",
    "RULE_COMPUTE_SYNTAX": "rules",
    "RULE_ACTION_UNKNOWN": "references",
    "PRIMITIVES_INVALID": "references",
    "PRIMITIVE_NOT_OBJECT": "references",
    "PRIMITIVE_NAME_MISSING": "references",
    "PRIMITIVE_NAME_DUPLICATE": "references",
    "PRIMITIVE_ARGUMENTS_INVALID": "references",
    "FUNCTIONS_INVALID": "references",
    "FUNCTION_NOT_OBJECT": "references",
    "FUNCTION_ID_MISSING": "references",
    "FUNCTION_TARGET_UNKNOWN": "references",
    "FUNCTION_IMPL_MISSING": "references",
    "FUNCTION_PARAM_NAME_MISSING": "references",
    "FUNCTION_PARAM_TYPE_INVALID": "references",
    "DATA_SOURCES_INVALID": "references",
    "DATA_SOURCE_NOT_OBJECT": "references",
    "DATA_SOURCE_CLASS_UNKNOWN": "references",
    "DATA_SOURCE_TOOL_MISSING": "references",
    "PRICING_RULE_UNKNOWN": "references",
}


def issue_category(code: str) -> str:
    return ISSUE_CATEGORY.get(code, "references")


def _walk_atoms(cond: dict, path: str):
    if not isinstance(cond, dict):
        yield cond, path
        return
    if "all" in cond or "any" in cond:
        key = "all" if "all" in cond else "any"
        for index, item in enumerate(cond.get(key) or []):
            yield from _walk_atoms(item, f"{path}.{key}[{index}]")
        return
    yield cond, path


def validate_semantic(raw: dict) -> list[dict]:
    """校验语义层的引用完整性与结构约束，供 CI 和演示门禁复用。"""
    issues: list[dict] = []

    def add(code: str, path: str, message: str) -> None:
        issues.append({
            "code": code,
            "category": issue_category(code),
            "path": path,
            "message": message,
        })

    if not isinstance(raw, dict):
        return [{
            "code": "ROOT_NOT_OBJECT",
            "category": "types",
            "path": "$",
            "message": "语义层根节点必须是对象",
        }]

    meta = raw.get("meta")
    if not isinstance(meta, dict):
        add("META_MISSING", "meta", "缺少 meta 定义")
        meta = {}
    if not str(meta.get("version") or "").strip():
        add("VERSION_MISSING", "meta.version", "缺少本体版本号")
    if not str(meta.get("name") or "").strip():
        add("NAME_MISSING", "meta.name", "缺少本体名称")

    classes: dict[str, dict] = {}
    raw_classes = raw.get("classes")
    if not isinstance(raw_classes, list) or not raw_classes:
        add("CLASSES_MISSING", "classes", "classes 必须是非空数组")
        raw_classes = []
    for index, cls in enumerate(raw_classes):
        path = f"classes[{index}]"
        if not isinstance(cls, dict):
            add("CLASS_NOT_OBJECT", path, "类定义必须是对象")
            continue
        cid = str(cls.get("id") or "").strip()
        if not cid:
            add("CLASS_ID_MISSING", path, "类缺少 id")
            continue
        if cid in classes:
            add("CLASS_DUPLICATE", path, f"类 id 重复: {cid}")
        classes[cid] = cls
        properties = cls.get("properties")
        statuses = cls.get("statuses")
        if not isinstance(properties, list) or not properties:
            add("CLASS_PROPERTIES_INVALID", f"{path}.properties", f"{cid} 的 properties 必须是非空数组")
            properties = []
        if not isinstance(statuses, list) or not statuses:
            add("CLASS_STATUSES_INVALID", f"{path}.statuses", f"{cid} 的 statuses 必须是非空数组")
            statuses = []
        key = cls.get("key")
        if not key:
            add("CLASS_KEY_MISSING", f"{path}.key", f"{cid} 缺少业务主键 key")
        elif key not in properties:
            add("CLASS_KEY_NOT_PROPERTY", f"{path}.key", f"{cid} 的主键 {key} 不在 properties 中")
        if not str(cls.get("label") or "").strip():
            add("CLASS_LABEL_MISSING", f"{path}.label", f"{cid} 缺少中文标签")

    if "FeeItem" in classes:
        fee_props = classes["FeeItem"].get("properties") or []
        if "fee_type" not in fee_props:
            add("FEE_TYPE_PROPERTY_MISSING", "classes[FeeItem].properties",
                "FeeItem 必须声明 fee_type 属性，规则条件依赖该字段")

    relations: dict[str, dict] = {}
    raw_relations = raw.get("relations")
    if not isinstance(raw_relations, list):
        add("RELATIONS_INVALID", "relations", "relations 必须是数组")
        raw_relations = []
    for index, rel in enumerate(raw_relations):
        path = f"relations[{index}]"
        if not isinstance(rel, dict):
            add("RELATION_NOT_OBJECT", path, "关系定义必须是对象")
            continue
        rid = str(rel.get("id") or "").strip()
        if not rid:
            add("RELATION_ID_MISSING", path, "关系缺少 id")
            continue
        if rid in relations:
            add("RELATION_DUPLICATE", path, f"关系 id 重复: {rid}")
        relations[rid] = rel
        domain, range_ = rel.get("domain"), rel.get("range")
        if domain not in classes:
            add("RELATION_DOMAIN_UNKNOWN", f"{path}.domain",
                f"关系 {rid} 的定义域 {domain!r} 未在 classes 中定义")
        if range_ not in classes:
            add("RELATION_RANGE_UNKNOWN", f"{path}.range",
                f"关系 {rid} 的值域 {range_!r} 未在 classes 中定义")
        if not str(rel.get("label") or "").strip():
            add("RELATION_LABEL_MISSING", f"{path}.label", f"关系 {rid} 缺少中文标签")

    pricing = raw.get("pricing")
    if not isinstance(pricing, dict) or not pricing:
        add("PRICING_MISSING", "pricing", "pricing 必须是非空对象")
        pricing = {}

    actions: dict[str, dict] = {}
    raw_actions = raw.get("actions")
    if not isinstance(raw_actions, list):
        add("ACTIONS_INVALID", "actions", "actions 必须是数组")
        raw_actions = []
    for index, action in enumerate(raw_actions):
        path = f"actions[{index}]"
        if not isinstance(action, dict):
            add("ACTION_NOT_OBJECT", path, "动作定义必须是对象")
            continue
        aid = str(action.get("id") or "").strip()
        if not aid:
            add("ACTION_ID_MISSING", path, "动作缺少 id")
            continue
        if aid in actions:
            add("ACTION_DUPLICATE", path, f"动作 id 重复: {aid}")
        actions[aid] = action
        target = action.get("target")
        if target not in classes:
            add("ACTION_TARGET_UNKNOWN", f"{path}.target",
                f"动作 {aid} 的目标类型 {target!r} 未定义")
            continue
        statuses = set(classes[target].get("statuses") or [])
        from_status = action.get("from_status") or []
        if not isinstance(from_status, list):
            add("ACTION_FROM_STATUS_INVALID", f"{path}.from_status",
                f"动作 {aid} 的 from_status 必须是数组")
            from_status = []
        for status in from_status:
            if status not in statuses:
                add("ACTION_FROM_STATUS_UNKNOWN", f"{path}.from_status",
                    f"动作 {aid} 的前置状态 {status!r} 不属于 {target}")
        to_status = action.get("to_status")
        if to_status is not None and to_status not in statuses:
            add("ACTION_TO_STATUS_UNKNOWN", f"{path}.to_status",
                f"动作 {aid} 的目标状态 {to_status!r} 不属于 {target}")
        roles = action.get("roles")
        if not isinstance(roles, list) or not roles:
            add("ACTION_ROLES_INVALID", f"{path}.roles", f"动作 {aid} 必须声明可执行角色")
        params = action.get("params")
        if not isinstance(params, list):
            add("ACTION_PARAMS_INVALID", f"{path}.params", f"动作 {aid} 的 params 必须是数组")
            params = []
        for pindex, param in enumerate(params):
            ppath = f"{path}.params[{pindex}]"
            if not isinstance(param, dict):
                add("ACTION_PARAM_NOT_OBJECT", ppath, "动作参数必须是对象")
                continue
            if not str(param.get("name") or "").strip():
                add("ACTION_PARAM_NAME_MISSING", ppath, "动作参数缺少 name")
            if param.get("type") not in ACTION_PARAM_TYPES:
                add("ACTION_PARAM_TYPE_INVALID", f"{ppath}.type",
                    f"动作参数类型 {param.get('type')!r} 不受支持")

    rules: dict[str, dict] = {}
    raw_rules = raw.get("rules")
    if not isinstance(raw_rules, list):
        add("RULES_INVALID", "rules", "rules 必须是数组")
        raw_rules = []
    for index, rule in enumerate(raw_rules):
        path = f"rules[{index}]"
        if not isinstance(rule, dict):
            add("RULE_NOT_OBJECT", path, "规则定义必须是对象")
            continue
        rid = str(rule.get("id") or "").strip()
        if not rid:
            add("RULE_ID_MISSING", path, "规则缺少 id")
            continue
        if rid in rules:
            add("RULE_DUPLICATE", path, f"规则 id 重复: {rid}")
        rules[rid] = rule
        domain = rule.get("domain")
        if domain not in classes:
            add("RULE_DOMAIN_UNKNOWN", f"{path}.domain",
                f"规则 {rid} 的作用域 {domain!r} 未定义")
            domain_class = {}
        else:
            domain_class = classes[domain]
        props = set(domain_class.get("properties") or [])
        for atom, atom_path in _walk_atoms(rule.get("when") or {}, f"{path}.when"):
            if not isinstance(atom, dict):
                add("RULE_CONDITION_INVALID", atom_path, "规则条件必须是对象")
                continue
            field_name = atom.get("field")
            op = atom.get("op")
            if field_name not in props:
                add("RULE_FIELD_UNKNOWN", f"{atom_path}.field",
                    f"规则 {rid} 引用的字段 {field_name!r} 不在 {domain} 的属性中")
            if op not in VALID_OPS:
                add("RULE_OP_UNSUPPORTED", f"{atom_path}.op",
                    f"规则 {rid} 使用了不支持的操作符 {op!r}")
            if field_name == "fee_type":
                values = atom.get("value")
                values = values if isinstance(values, list) else [values]
                for value in values:
                    if value not in pricing:
                        add("UNKNOWN_FEE_TYPE", f"{atom_path}.value",
                            f"规则 {rid} 引用了未在 pricing 中定义的 fee_type: {value!r}")
        judge = rule.get("judge")
        if not isinstance(judge, dict) or not judge:
            add("RULE_JUDGE_MISSING", f"{path}.judge", f"规则 {rid} 缺少 judge 定义")
            judge = {}
        if not str(judge.get("explain") or "").strip():
            add("RULE_JUDGE_FIELD_MISSING", f"{path}.judge.explain",
                f"规则 {rid} 的 judge.explain 不能为空")
        has_guard = bool(judge.get("forbidden_actions") or judge.get("allowed_actions"))
        has_ast = judge.get("ast") is not None
        if not str(judge.get("formula") or "").strip() and not has_ast and not has_guard:
            add("RULE_JUDGE_FIELD_MISSING", f"{path}.judge.formula",
                f"规则 {rid} 必须提供 judge.formula、judge.ast 或动作约束")
        if has_ast:
            validate_rule_ast(judge["ast"], f"{path}.judge.ast", add)
        calc = judge.get("calc")
        if calc is not None:
            if not isinstance(calc, dict):
                add("RULE_CALC_INVALID", f"{path}.judge.calc", "judge.calc 必须是对象")
            else:
                for key in ("target", "unit", "rounding"):
                    if key in calc and not isinstance(calc[key], str):
                        add("RULE_CALC_INVALID", f"{path}.judge.calc.{key}",
                            f"judge.calc.{key} 必须是字符串")
                bindings = calc.get("bindings") or {}
                if not isinstance(bindings, dict):
                    add("RULE_CALC_INVALID", f"{path}.judge.calc.bindings",
                        "judge.calc.bindings 必须是对象")
                else:
                    for key, expression in bindings.items():
                        validate_rule_ast(
                            expression,
                            f"{path}.judge.calc.bindings.{key}", add,
                        )
        compute = judge.get("compute")
        if compute is not None:
            if isinstance(compute, dict):
                expressions = [compute.get("result")]
                expressions.extend((compute.get("meta") or {}).values())
            else:
                expressions = [compute]
            for expression in expressions:
                if not isinstance(expression, str) or not expression.strip():
                    add("RULE_COMPUTE_INVALID", f"{path}.judge.compute",
                        f"规则 {rid} 的 compute 表达式必须是非空字符串")
                    continue
                try:
                    ast.parse(expression, mode="eval")
                except SyntaxError:
                    add("RULE_COMPUTE_SYNTAX", f"{path}.judge.compute",
                        f"规则 {rid} 的 compute 表达式无法解析: {expression!r}")
        for field_name in ("forbidden_actions", "allowed_actions"):
            for action_id in judge.get(field_name) or []:
                if action_id not in actions:
                    add("RULE_ACTION_UNKNOWN", f"{path}.judge.{field_name}",
                        f"规则 {rid} 引用了未定义的动作: {action_id!r}")

    for fee_type, config in pricing.items():
        path = f"pricing.{fee_type}"
        if not isinstance(config, dict):
            add("PRICING_ITEM_INVALID", path, "定价配置必须是对象")
            continue
        rule_id = config.get("rule_id")
        if not rule_id:
            add("PRICING_RULE_MISSING", f"{path}.rule_id", f"费用类型 {fee_type} 缺少 rule_id")
        elif rule_id not in rules:
            add("PRICING_RULE_UNKNOWN", f"{path}.rule_id",
                f"费用类型 {fee_type} 引用了未定义的规则: {rule_id!r}")
        elif rules[rule_id].get("domain") != "FeeItem":
            add("PRICING_RULE_DOMAIN_INVALID", f"{path}.rule_id",
                f"费用类型 {fee_type} 的规则 {rule_id} 必须作用于 FeeItem")
        if fee_type == "waiting":
            for key in ("free_minutes", "block_minutes", "per_block"):
                value = config.get(key)
                if not isinstance(value, (int, float)) or value < 0:
                    add("PRICING_NUMBER_INVALID", f"{path}.{key}",
                        f"等候费参数 {key} 必须是非负数字")
            by_vehicle_type = config.get("by_vehicle_type")
            if by_vehicle_type is not None:
                if not isinstance(by_vehicle_type, dict) or not by_vehicle_type:
                    add("PRICING_VEHICLE_TYPE_INVALID", f"{path}.by_vehicle_type",
                        "等候费车型差异化配置必须是非空对象")
                else:
                    for vehicle_type, override in by_vehicle_type.items():
                        item_path = f"{path}.by_vehicle_type.{vehicle_type}"
                        if not isinstance(override, dict):
                            add("PRICING_VEHICLE_TYPE_INVALID", item_path,
                                "车型等候费配置必须是对象")
                            continue
                        for key in ("free_minutes", "block_minutes", "per_block"):
                            value = override.get(key)
                            if not isinstance(value, (int, float)) or value < 0:
                                add("PRICING_NUMBER_INVALID", f"{item_path}.{key}",
                                    f"车型 {vehicle_type} 的等候费参数 {key} 必须是非负数字")

    primitives = raw.get("primitives", [])
    if not isinstance(primitives, list):
        add("PRIMITIVES_INVALID", "primitives", "primitives 必须是数组")
        primitives = []
    seen_primitives = set()
    for index, primitive in enumerate(primitives):
        path = f"primitives[{index}]"
        if not isinstance(primitive, dict):
            add("PRIMITIVE_NOT_OBJECT", path, "工具原语定义必须是对象")
            continue
        name = str(primitive.get("name") or "").strip()
        if not name:
            add("PRIMITIVE_NAME_MISSING", f"{path}.name", "工具原语缺少 name")
        elif name in seen_primitives:
            add("PRIMITIVE_NAME_DUPLICATE", f"{path}.name", f"工具原语重复: {name}")
        seen_primitives.add(name)
        if not isinstance(primitive.get("arguments"), dict):
            add("PRIMITIVE_ARGUMENTS_INVALID", f"{path}.arguments",
                f"工具原语 {name or index} 的 arguments 必须是对象")

    functions = raw.get("functions", [])
    if not isinstance(functions, list):
        add("FUNCTIONS_INVALID", "functions", "functions 必须是数组")
        functions = []
    for index, function in enumerate(functions):
        path = f"functions[{index}]"
        if not isinstance(function, dict):
            add("FUNCTION_NOT_OBJECT", path, "函数定义必须是对象")
            continue
        fid = str(function.get("id") or "").strip()
        if not fid:
            add("FUNCTION_ID_MISSING", f"{path}.id", "函数缺少 id")
        if function.get("target") not in classes:
            add("FUNCTION_TARGET_UNKNOWN", f"{path}.target",
                f"函数 {fid or index} 引用了未定义的类: {function.get('target')!r}")
        if not str(function.get("implementation") or "").strip():
            add("FUNCTION_IMPL_MISSING", f"{path}.implementation",
                f"函数 {fid or index} 缺少 implementation")
        for pindex, param in enumerate(function.get("params") or []):
            ppath = f"{path}.params[{pindex}]"
            if not str(param.get("name") or "").strip():
                add("FUNCTION_PARAM_NAME_MISSING", f"{ppath}.name", "函数参数缺少 name")
            if param.get("type", "string") not in ACTION_PARAM_TYPES:
                add("FUNCTION_PARAM_TYPE_INVALID", f"{ppath}.type",
                    f"函数参数类型不支持: {param.get('type')!r}")

    data_sources = raw.get("data_sources")
    if not isinstance(data_sources, list):
        add("DATA_SOURCES_INVALID", "data_sources", "data_sources 必须是数组")
        data_sources = []
    for index, source in enumerate(data_sources):
        path = f"data_sources[{index}]"
        if not isinstance(source, dict):
            add("DATA_SOURCE_NOT_OBJECT", path, "数据源映射必须是对象")
            continue
        if source.get("class") not in classes:
            add("DATA_SOURCE_CLASS_UNKNOWN", f"{path}.class",
                f"数据源映射引用了未定义的类: {source.get('class')!r}")
        if not str(source.get("tool") or "").strip():
            add("DATA_SOURCE_TOOL_MISSING", f"{path}.tool", "数据源映射缺少 tool")

    return issues


# ─────────────────────────── 本体实质校验（反虚胖） ───────────────────────────
# 背景：曾经用 tools/expand_ontology.py 把同一模板复制 540 次，把规模顶到
# 「500 类 / 500 关系」。规模数字达标，但 540 个类属性全部是
# xxx_id + name + status + created_at + updated_at、描述套模板、状态机一律
# ACTIVE/INACTIVE，且 ABox 从未承载过一个实例。
# 下面几条断言专门堵住这条路：规模可以大，但每个类都必须是有实质定义的。

TEMPLATE_DESC_MARKERS = (
    "域的，按", "按 {}_id 检索", "待补充", "TODO", "占位",
)
GENERIC_STATUS_SETS = (
    ("ACTIVE", "INACTIVE"),
    ("ACTIVE", "DISABLED"),
    ("NORMAL", "ABNORMAL"),
    ("ENABLED", "DISABLED"),
)
MAX_UNIFORM_STATUS_SHARE = 0.05


def validate_ontology_substance(raw: dict) -> list[dict]:
    """校验本体是否有实质内容（反批量模板凑数）。仅对工作副本执行。"""
    issues: list[dict] = []

    def add(code: str, path: str, message: str) -> None:
        issues.append({
            "code": code,
            "category": "substance",
            "path": path,
            "message": message,
        })

    classes = [c for c in (raw.get("classes") or []) if isinstance(c, dict)]
    relations = [r for r in (raw.get("relations") or []) if isinstance(r, dict)]
    registry = {p.get("name"): p for p in (raw.get("property_types") or [])
                if isinstance(p, dict)}

    if not registry:
        add("PROPERTY_REGISTRY_MISSING", "property_types",
            "本体未声明属性注册表：每个属性必须有独立定义"
            "（value_type/base_type/unit/marking/used_by）")

    for cls in classes:
        cid = cls.get("id")
        for prop in cls.get("properties") or []:
            if registry and prop not in registry:
                add("PROP_UNREGISTERED", f"classes[{cid}].properties",
                    f"属性 {cid}.{prop} 未在 property_types 注册")

    # 描述不得是模板句
    for cls in classes:
        desc = str(cls.get("description") or "").strip()
        cid = cls.get("id")
        if not desc:
            add("CLASS_DESCRIPTION_MISSING", f"classes[{cid}].description",
                f"类 {cid} 缺少业务描述（必须说清它在链路里的角色）")
            continue
        for marker in TEMPLATE_DESC_MARKERS:
            if marker in desc:
                add("CLASS_DESCRIPTION_TEMPLATED", f"classes[{cid}].description",
                    f"类 {cid} 的描述看起来是模板句: {desc[:40]!r}")
                break

    # 禁止脚本批量生成的类
    generated = [c.get("id") for c in classes
                 if str(c.get("source") or "").startswith("generated:")]
    if generated:
        add("CLASS_AUTO_GENERATED", "classes",
            f"{len(generated)} 个类由脚本批量生成（source=generated:*），"
            f"例如 {generated[:5]}；规模必须来自人工建模，不能来自模板复制")

    # 状态机不得千篇一律
    if len(classes) >= 20:
        counter: dict[tuple, int] = {}
        for cls in classes:
            key = tuple(cls.get("statuses") or [])
            counter[key] = counter.get(key, 0) + 1
        for key, count in counter.items():
            share = count / len(classes)
            if key and key in GENERIC_STATUS_SETS and share > MAX_UNIFORM_STATUS_SHARE:
                add("CLASS_STATUS_UNIFORM", "classes[*].statuses",
                    f"{count}/{len(classes)} 个类共用同一组通用状态 {list(key)}"
                    f"（{share:.0%}）；每个实体应有自己的状态机")

    # 关系不得是模板化的 xxx_belongs_to_yyy
    if len(relations) >= 50:
        templated = [r.get("id") for r in relations
                     if str(r.get("id") or "").endswith("_belongs_to")]
        if len(templated) / len(relations) > 0.2:
            add("RELATION_TEMPLATED", "relations",
                f"{len(templated)}/{len(relations)} 条关系是 xxx_belongs_to_yyy 形式，"
                f"疑似模板批量生成")

    # 接口（Palantir Interface，structural match）必须真的被类实现
    by_id = {c.get("id"): c for c in classes}
    for iface in (raw.get("interfaces") or []):
        if not isinstance(iface, dict):
            continue
        iid = iface.get("id")
        req = iface.get("required_properties") or []
        any_of = iface.get("required_any_of") or []
        impls = []
        for cls in classes:
            props = set(cls.get("properties") or [])
            if not set(req) <= props:
                continue
            if any_of and not (set(any_of) & props):
                continue
            impls.append(cls.get("id"))
        if not impls:
            add("IFACE_UNIMPLEMENTED", f"interfaces[{iid}]",
                f"接口 {iid} 的 required_properties={req} / required_any_of={any_of} "
                f"没有任何类满足——空壳声明，structural match 落不了地")

    return issues