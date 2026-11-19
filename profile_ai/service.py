"""画像语义层 Agent 工具服务。

读接口：search_assets / get_context / validate_crowd / impact_analysis /
        trace_evidence / query_metric
写接口：propose_change -> confirm_change -> 审计留痕
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections import Counter, deque
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from .audit import AuditStore
from .hugegraph import HugeGraphClient, HugeGraphError
from .semantic import ProfileSemanticLayer, has_errors


class ProfileError(Exception):
    status_code = 400


class AssetNotFound(ProfileError):
    status_code = 404


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# 影响面只沿“本体声明的下游因果边”扩散；同城/共享标签等邻域关系不算影响面。
# 每条边同时声明因果方向和原因，BFS 只按这里的方向走，物理边的字面方向不参与判断。
# 层级：Tag/Crowd（定义层） -> Strategy（决策层） -> CanvasTask（执行层） -> Order（事实层）
CAUSAL_IMPACT_RELATIONS = {
    "crowd_has_tag": {
        "directions": {"IN"}, "from_kind": "tag", "to_kind": "crowd",
        "rationale": "标签口径变化会改变引用它的人群定义，再向下游扩散",
    },
    "crowd_derived_from": {
        "directions": {"OUT"}, "from_kind": "crowd", "to_kind": "crowd",
        "rationale": "父人群变化会继承到派生人群",
    },
    "strategy_uses_tag": {
        "directions": {"IN"}, "from_kind": "tag", "to_kind": "strategy",
        "rationale": "策略规则引用标签，标签变化会直接改变策略口径",
    },
    "strategy_targets_crowd": {
        "directions": {"IN"}, "from_kind": "crowd", "to_kind": "strategy",
        "rationale": "人群变化会改变策略作用对象",
    },
    "canvas_references_crowd": {
        "directions": {"IN"}, "from_kind": "crowd", "to_kind": "canvas",
        "rationale": "画布引用人群，人群变化会影响投放执行",
    },
    "canvas_references_strategy": {
        "directions": {"IN"}, "from_kind": "strategy", "to_kind": "canvas",
        "rationale": "画布引用策略，策略变化会影响投放执行",
    },
    "canvas_generates_order": {
        "directions": {"OUT"}, "from_kind": "canvas", "to_kind": "order",
        "rationale": "画布执行会真实产生订单，是资损影响面的终点",
    },
}
DOWNSTREAM_IMPACT_DIRECTIONS = {
    name: spec["directions"] for name, spec in CAUSAL_IMPACT_RELATIONS.items()
}

# 非因果邻域关系的排除原因：这些边只描述属性/观测/审计，不参与变更传播。
NON_CAUSAL_RELATION_REASONS = {
    "crowd_scope_city": "城市范围是圈选条件与归属，同城不构成变更传播",
    "order_in_city": "订单城市是事实属性，不是下游资产",
    "order_served_by_driver": "订单-司机是事实归属，不是下游资产",
    "driver_owns_vehicle": "司机-车辆是事实归属，不是下游资产",
    "driver_in_city": "司机城市是事实属性，不是下游资产",
    "asset_has_metric": "指标是效果观测，不是执行路径",
    "asset_has_evidence": "证据是结论支撑材料，不是下游资产",
    "asset_has_version": "版本是变更快照，不是下游资产",
    "version_generated_by": "操作人是审计信息，不是下游资产",
}


def _clean(props: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in props.items() if v is not None and v != ""}


class ProfileService:
    def __init__(self, client: Optional[HugeGraphClient] = None,
                 audit: Optional[AuditStore] = None,
                 semantic: Optional[ProfileSemanticLayer] = None):
        self.semantic = semantic or ProfileSemanticLayer()
        self.client = client or HugeGraphClient()
        self.client.semantic = self.semantic
        self.audit = audit or AuditStore()
        self._physical = {e["name"]: e for e in self.semantic.physical_edges()}
        self._impact_trace_cache: Dict[tuple, tuple] = {}
        self._impact_trace_lock = threading.Lock()

    # ------------------------------------------------------------------ meta
    def health(self) -> dict:
        out = self.client.health()
        out["semantic"] = {
            "version": self.semantic.meta.get("version"),
            "classes": len(self.semantic.classes),
            "relations": len(self.semantic.relations),
            "constraints": len(self.semantic.constraints),
        }
        return out

    def provision(self) -> dict:
        space = self.client.ensure_graphspace()
        graph = self.client.ensure_graph()
        schema = self.client.ensure_schema()
        return {"graphspace": space, "graph": graph, "schema": schema,
                "health": self.client.health()}

    def dictionary(self) -> dict:
        return self.semantic.dictionary()

    # --------------------------------------------------------------- vertices
    def _vertex(self, asset_id: str) -> dict:
        raw = self.client.get_vertex(asset_id)
        if not raw:
            raise AssetNotFound(f"资产不存在: {asset_id}")
        label = raw.get("label")
        cls = self.semantic.class_by_id.get(label)
        props = raw.get("properties") or {}
        return {
            "id": str(raw.get("id")),
            "label": label,
            "kind": cls.get("kind") if cls else self.semantic.kind_of(asset_id),
            "name": props.get("name"),
            "display_name": props.get("display_name"),
            "status": props.get("status"),
            "owner": props.get("owner"),
            "description": props.get("description"),
            "updated_at": props.get("updated_at") or props.get("created_at"),
            "properties": props,
            "raw": raw,
        }

    @staticmethod
    def _summary(v: dict) -> dict:
        return {k: v.get(k) for k in (
            "id", "label", "kind", "name", "display_name", "status", "owner",
            "description", "updated_at")}

    def _edge_view(self, edge: dict, anchor: str,
                   at: Optional[str] = None) -> dict:
        source_id, target_id = self.client.edge_endpoints(edge)
        source_id = str(source_id) if source_id is not None else None
        target_id = str(target_id) if target_id is not None else None
        physical = edge.get("label")
        spec = self._physical.get(physical)
        props = edge.get("properties") or {}
        direction = "OUT" if source_id == anchor else "IN"
        neighbor_id = target_id if direction == "OUT" else source_id
        return {
            "edge_id": edge.get("id"),
            "physical_label": physical,
            "relation": spec["relation"] if spec else physical,
            "source_id": source_id,
            "target_id": target_id,
            "source_class": spec["source_label"] if spec else None,
            "target_class": spec["target_label"] if spec else None,
            "direction": direction,
            "neighbor_id": neighbor_id,
            "neighbor_class": (spec["target_label"] if direction == "OUT"
                               else spec["source_label"]) if spec else None,
            "properties": props,
            "active": self._edge_active(props, at),
        }

    @staticmethod
    def _normalize_at(at: Optional[str]) -> Optional[str]:
        """把 as-of 时间统一成与 valid_from/valid_to 可比较的 UTC 字符串。"""
        if at in (None, ""):
            return None
        text = str(at).strip()
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            raise ProfileError("at 必须是 ISO 8601 时间，例如 2026-03-15T00:00:00Z")
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @staticmethod
    def _edge_active(props: dict, at: Optional[str] = None) -> bool:
        at = at or now_iso()
        vf = props.get("valid_from")
        vt = props.get("valid_to")
        if vf and str(vf) > at:
            return False
        if vt and str(vt) < at:
            return False
        return True

    def _neighbors(self, asset_id: str, limit: int = 300,
                   at: Optional[str] = None,
                   active_only: bool = False) -> List[dict]:
        edges = self.client.list_edges(vertex_id=asset_id, direction="BOTH",
                                       limit=limit)
        cache: Dict[str, Optional[dict]] = {}
        rows: List[dict] = []
        for edge in edges:
            view = self._edge_view(edge, asset_id, at=at)
            if active_only and not view["active"]:
                continue
            nid = view.get("neighbor_id")
            if not nid:
                continue
            if nid not in cache:
                raw = self.client.get_vertex(nid)
                if raw:
                    label = raw.get("label")
                    cls = self.semantic.class_by_id.get(label)
                    props = raw.get("properties") or {}
                    cache[nid] = {
                        "id": str(raw.get("id")), "label": label,
                        "kind": cls.get("kind") if cls else None,
                        "name": props.get("name"),
                        "display_name": props.get("display_name"),
                        "status": props.get("status"),
                        "owner": props.get("owner"),
                        "description": props.get("description"),
                        "updated_at": props.get("updated_at") or props.get("created_at"),
                    }
                else:
                    cache[nid] = None
            if cache[nid] is None:
                continue
            view["vertex"] = cache[nid]
            rows.append(view)
        return rows

    # ----------------------------------------------------------------- search
    def search_assets(self, query: str = "", kind: Optional[str] = None,
                      status: Optional[str] = None, limit: int = 20) -> dict:
        limit = max(1, min(int(limit), 100))
        q = (query or "").strip().lower()
        tokens = [t for t in q.replace("，", " ").replace(",", " ").split() if t]
        class_id = None
        if kind:
            if kind in self.semantic.class_by_id:
                class_id = kind
            elif kind in self.semantic.class_by_kind:
                class_id = self.semantic.class_by_kind[kind]["id"]

        items: List[dict] = []
        seen = set()
        for cls in self.semantic.classes:
            if class_id and cls["id"] != class_id:
                continue
            for raw in self.client.list_vertices(label=cls["id"], limit=500):
                aid = str(raw.get("id"))
                if aid in seen:
                    continue
                props = raw.get("properties") or {}
                if status and props.get("status") != status:
                    continue
                score, fields = self._score(aid, props, q, tokens)
                if q and score <= 0:
                    continue
                seen.add(aid)
                items.append({
                    "id": aid, "label": cls["id"], "kind": cls["kind"],
                    "name": props.get("name"),
                    "display_name": props.get("display_name"),
                    "status": props.get("status"),
                    "owner": props.get("owner"),
                    "description": props.get("description"),
                    "source_system": props.get("source_system"),
                    "updated_at": props.get("updated_at") or props.get("created_at"),
                    "score": round(score, 4),
                    "matched_fields": fields,
                })
        items.sort(key=lambda x: (-x["score"], str(x.get("updated_at") or "")),
                   reverse=False)
        items.sort(key=lambda x: (-x["score"], str(x.get("updated_at") or "")))
        return {"query": query, "kind": class_id or kind, "status": status,
                "total": len(items), "items": items[:limit],
                "projection": "OVERVIEW"}

    @staticmethod
    def _score(aid: str, props: dict, q: str, tokens: List[str]) -> tuple:
        if not q:
            return 0.1, []
        fields: List[str] = []
        score = 0.0
        if aid.lower() == q:
            score, fields = 1.0, ["id"]
        else:
            name = str(props.get("name") or "").lower()
            display = str(props.get("display_name") or "").lower()
            desc = str(props.get("description") or "").lower()
            keywords = " ".join(str(x) for x in (props.get("keywords") or [])).lower()
            if name == q or display == q:
                score, fields = 0.95, ["name"]
            elif q in name or q in display:
                score, fields = 0.8, ["name"]
            elif q in desc:
                score, fields = 0.55, ["description"]
            elif q in keywords:
                score, fields = 0.45, ["keywords"]
            else:
                hits = sum(1 for t in tokens if t in name or t in display
                           or t in desc or t in keywords)
                if hits:
                    score = min(0.4, 0.15 * hits)
                    fields = ["tokens"]
        return score, fields

    # --------------------------------------------------------------- context
    def get_context(self, asset_id: str, level: str = "OVERVIEW",
                    at: Optional[str] = None) -> dict:
        level = (level or "OVERVIEW").upper()
        if level not in ("OVERVIEW", "NETWORK", "FULL"):
            raise ProfileError("level 只能是 OVERVIEW / NETWORK / FULL")
        at_norm = self._normalize_at(at)
        asset = self._vertex(asset_id)
        fields = self.semantic.projection_fields(level)
        out: Dict[str, Any] = {f: asset.get(f) for f in fields if f != "properties"}
        out["asset_id"] = asset["id"]
        out["label"] = asset["label"]
        out["kind"] = asset["kind"]
        out["name"] = asset["name"]
        out["display_name"] = asset.get("display_name")
        out["status"] = asset.get("status")
        out["owner"] = asset.get("owner")
        out["description"] = asset.get("description")
        out["updated_at"] = asset.get("updated_at")

        neighbors = self._neighbors(asset_id, at=at_norm,
                                    active_only=bool(at_norm))
        counts = Counter(n["relation"] for n in neighbors)
        out["summary"] = {
            "neighbors": len(neighbors),
            "by_relation": dict(counts),
            "tags": counts.get("crowd_has_tag", 0),
            "evidence": counts.get("asset_has_evidence", 0),
            "versions": counts.get("asset_has_version", 0),
            "metrics": counts.get("asset_has_metric", 0),
            "strategies": counts.get("strategy_targets_crowd", 0),
            "canvases": (counts.get("canvas_references_crowd", 0)
                         + counts.get("canvas_references_strategy", 0)),
        }

        if level != "OVERVIEW":
            out["neighbors"] = [{
                "relation": n["relation"], "physical_label": n["physical_label"],
                "direction": n["direction"], "active": n["active"],
                "properties": n["properties"],
                "vertex": n["vertex"],
            } for n in neighbors]

        if level == "FULL":
            out["properties"] = asset["properties"]
            out["evidence"] = self._related_vertices(neighbors, "asset_has_evidence")
            out["versions"] = self._related_vertices(neighbors, "asset_has_version")
            out["metrics"] = self._related_vertices(neighbors, "asset_has_metric")
            out["lineage"] = self.lineage(asset_id, depth=2, at=at_norm)

        out["as_of"] = at_norm or now_iso()
        out["temporal_snapshot"] = bool(at_norm)
        out["trust"] = {
            "source": "HugeGraph",
            "graphspace": self.client.graphspace,
            "graph": self.client.graph,
            "projection": level,
            "edge_count": len(neighbors),
            "generated_at": now_iso(),
        }
        return out

    @staticmethod
    def _related_vertices(neighbors: List[dict], relation: str) -> List[dict]:
        return [{
            "relation": n["relation"], "properties": n["properties"],
            "active": n["active"], "vertex": n["vertex"],
        } for n in neighbors if n["relation"] == relation]

    def _lineage_node(self, node_id: str) -> Optional[dict]:
        raw = self.client.get_vertex(node_id)
        if not raw:
            return None
        label = raw.get("label")
        cls = self.semantic.class_by_id.get(label)
        props = raw.get("properties") or {}
        return {
            "id": str(raw.get("id")), "label": label,
            "kind": cls.get("kind") if cls else self.semantic.kind_of(node_id),
            "name": props.get("name"),
            "display_name": props.get("display_name"),
            "status": props.get("status"),
        }

    def lineage(self, asset_id: str, depth: int = 3,
                at: Optional[str] = None) -> dict:
        """只沿 crowd_derived_from 追溯派生血缘，区分上游来源与下游派生。"""
        depth = max(1, min(int(depth), 6))
        at_norm = self._normalize_at(at)
        root = self._vertex(asset_id)
        try:
            label = self.semantic.edge_label("crowd_derived_from", "Crowd")
        except Exception:
            label = None

        def walk(direction: str) -> dict:
            nodes: Dict[str, dict] = {}
            edges: List[dict] = []
            paths: List[dict] = []
            seen_edges = set()
            q = deque([(root["id"], 0, [root["id"]])])
            while q and len(nodes) < 500:
                current, d, path = q.popleft()
                if d >= depth:
                    continue
                for edge in self.client.list_edges(vertex_id=current, direction="BOTH",
                                                   label=label, limit=300):
                    view = self._edge_view(edge, current, at=at_norm)
                    if view["relation"] != "crowd_derived_from":
                        continue
                    if view["direction"] != direction or not view["active"]:
                        continue
                    key = (view["source_id"], view["target_id"],
                           view["properties"].get("valid_from"))
                    if key not in seen_edges:
                        seen_edges.add(key)
                        edges.append({
                            "source_id": view["source_id"],
                            "target_id": view["target_id"],
                            "properties": view["properties"],
                        })
                    nid = view["neighbor_id"]
                    if not nid or nid in path:
                        continue
                    if nid not in nodes:
                        node = self._lineage_node(nid)
                        if not node:
                            continue
                        node["depth"] = d + 1
                        nodes[nid] = node
                        paths.append({"target_id": nid, "depth": d + 1,
                                      "path": path + [nid]})
                        q.append((nid, d + 1, path + [nid]))
            return {"nodes": list(nodes.values()), "edges": edges, "paths": paths}

        downstream = walk("OUT")
        upstream = walk("IN")
        return {
            "root": self._summary(root),
            "relation": "crowd_derived_from",
            "depth": depth,
            "at": at_norm or now_iso(),
            "temporal_snapshot": bool(at_norm),
            "downstream": downstream,
            "upstream": upstream,
            "summary": {
                "descendants": len(downstream["nodes"]),
                "ancestors": len(upstream["nodes"]),
                "max_downstream_depth": max(
                    [p["depth"] for p in downstream["paths"]] or [0]),
                "max_upstream_depth": max(
                    [p["depth"] for p in upstream["paths"]] or [0]),
            },
            "generated_at": now_iso(),
        }

    # -------------------------------------------------------------- validate
    def validate_crowd(self, crowd_id: str) -> dict:
        asset = self._vertex(crowd_id)
        if asset["label"] != "Crowd":
            raise ProfileError(f"{crowd_id} 不是人群资产，实际类型: {asset['label']}")
        violations = self.semantic.validate_vertex(
            "Crowd", asset["properties"], crowd_id)
        edges = self.client.list_edges(vertex_id=crowd_id, direction="BOTH",
                                       limit=500)
        counts = Counter()
        for edge in edges:
            view = self._edge_view(edge, crowd_id)
            counts[view["relation"]] += 1
            spec = self.semantic.relation(view["relation"])
            if spec is None:
                violations.append(self.semantic._v(
                    "C5", "ERROR", f"未登记关系: {view['physical_label']}"))
                continue
            violations.extend(self.semantic.validate_relation(
                view["relation"], view["source_class"], view["target_class"],
                view["properties"], view["source_id"], view["target_id"]))
            if not view["active"]:
                violations.append(self.semantic._v(
                    "C4", "WARN", f"关系 {view['relation']} 当前不在有效期内"))
            if not self.client.get_vertex(view["neighbor_id"]):
                violations.append(self.semantic._v(
                    "C5", "ERROR", f"关系目标不存在: {view['neighbor_id']}"))

        if counts.get("crowd_has_tag", 0) < 1:
            violations.append(self.semantic._v(
                "C7", "ERROR", "人群至少需要一个标签定义"))
        if counts.get("asset_has_evidence", 0) < 1:
            violations.append(self.semantic._v(
                "C8", "ERROR", "人群至少需要一条证据"))

        checks = [
            {"name": "asset_exists", "ok": True},
            {"name": "id_format", "ok": not has_errors(
                self.semantic.validate_asset_id(crowd_id, "Crowd"))},
            {"name": "required_properties", "ok": not any(
                v["code"] == "C2" and v["severity"] == "ERROR" for v in violations)},
            {"name": "status_machine", "ok": not any(
                v["code"] == "C3" and v["severity"] == "ERROR" for v in violations)},
            {"name": "has_tags", "ok": counts.get("crowd_has_tag", 0) > 0},
            {"name": "has_evidence", "ok": counts.get("asset_has_evidence", 0) > 0},
            {"name": "temporal_edges", "ok": not any(
                v["code"] == "C4" and v["severity"] == "ERROR" for v in violations)},
        ]
        errors = [v for v in violations if v["severity"] == "ERROR"]
        warnings = [v for v in violations if v["severity"] == "WARN"]
        return {
            "asset_id": crowd_id, "ok": not errors,
            "counts": dict(counts), "checks": checks,
            "violations": errors, "warnings": warnings,
            "generated_at": now_iso(),
        }

    # ------------------------------------------------------------- correction
    def diagnose_crowd(self, crowd_id: str, include_impact: bool = True,
                       at: Optional[str] = None) -> dict:
        """P0 人群智能纠错：结构化意图 vs 圈选规则的确定性冲突诊断。"""
        from .correction import CrowdCorrectionEngine

        asset = self._vertex(crowd_id)
        if asset["label"] != "Crowd":
            raise ProfileError(f"{crowd_id} 不是人群资产，实际类型: {asset['label']}")
        return CrowdCorrectionEngine(self).diagnose(
            crowd_id, include_impact=include_impact, at=at)

    def scan_crowds(self, limit: int = 200) -> dict:
        """批量扫描全部人群，输出纠错清单与 KPI。"""
        from .correction import CrowdCorrectionEngine

        return CrowdCorrectionEngine(self).scan(limit=limit)

    # ---------------------------------------------------------------- impact
    @staticmethod
    def _impact_effect(kind: Optional[str], status: Optional[str],
                       props: dict, at: str) -> dict:
        """按状态机与时态属性判断该对象在 as-of 时间是否仍在生效。"""
        reasons: List[str] = []
        active = True
        if kind == "strategy":
            if status and status != "ACTIVE":
                active = False
                reasons.append(f"策略状态为 {status}")
            vf, vt = props.get("effective_from"), props.get("effective_to")
            if vf and str(vf) > at:
                active = False
                reasons.append(f"策略 {vf} 才生效")
            if vt and str(vt) < at:
                active = False
                reasons.append(f"策略 {vt} 已失效")
        elif kind == "canvas":
            if status and status != "RUNNING":
                active = False
                reasons.append(f"画布状态为 {status}")
        elif kind in ("crowd", "tag"):
            if status and status != "ACTIVE":
                active = False
                reasons.append(f"{kind} 状态为 {status}")
        elif kind == "order":
            # 订单是已经发生的事实，状态只用于区分完成/退款等，不参与传播判定。
            pass
        return {"active": active, "reasons": reasons}

    def _impact_change(self, root: dict, proposed_rule: Optional[str]) -> dict:
        """对“修改前规则 -> 修改后规则”做确定性差异，并回填可观测的人群规模。"""
        from .correction import parse_rule

        props = root.get("properties") or {}
        before_rule = str(props.get("rule_expression") or "").strip()
        if not proposed_rule:
            return {
                "applied": False,
                "before_rule": before_rule or None,
                "note": "未提供 proposed_rule；当前结果只描述现有规则的影响面",
            }
        proposed_rule = str(proposed_rule).strip()
        before_conditions, before_errors = parse_rule(before_rule)
        after_conditions, after_errors = parse_rule(proposed_rule)
        before_map = {c["field"]: c for c in before_conditions}
        after_map = {c["field"]: c for c in after_conditions}
        changes: List[dict] = []
        for field in sorted(set(before_map) | set(after_map)):
            b = before_map.get(field) or {}
            a = after_map.get(field) or {}
            bv, av = b.get("value"), a.get("value")
            if bv == av:
                continue
            if field == "city_code":
                bs = set(bv if isinstance(bv, list) else ([bv] if bv else []))
                as_ = set(av if isinstance(av, list) else ([av] if av else []))
                changes.append({
                    "field": field,
                    "before": len(bs), "after": len(as_),
                    "city_scope": {
                        "before_count": len(bs), "after_count": len(as_),
                        "added": sorted(as_ - bs), "removed": sorted(bs - as_),
                    },
                })
            else:
                changes.append({"field": field, "before": bv, "after": av})

        audience_before = props.get("estimated_size")
        audience_after = None
        evidence = None
        if root.get("kind") == "crowd":
            # 只认规则完全等价的已观测人群，避免凭空推算新规则规模。
            for raw in self.client.list_vertices(label="Crowd", limit=300):
                other = raw.get("properties") or {}
                if str(raw.get("id")) == root.get("id"):
                    continue
                if str(other.get("rule_expression") or "").strip() != proposed_rule:
                    continue
                audience_after = other.get("estimated_size")
                evidence = {
                    "crowd_id": str(raw.get("id")),
                    "name": other.get("name"),
                    "basis": "rule_expression 完全等价的已观测人群",
                }
                break

        audience = {
            "before": audience_before,
            "after": audience_after,
            "delta": (audience_after - audience_before
                      if isinstance(audience_after, (int, float))
                      and isinstance(audience_before, (int, float)) else None),
            "evidence": evidence,
        }
        if audience_after is None:
            audience["note"] = "缺少规则完全等价的观测人群，需源系统 explain 后回填"

        return {
            "applied": True,
            "before_rule": before_rule or None,
            "proposed_rule": proposed_rule,
            "condition_changes": changes,
            "audience": audience,
            "parse_errors": before_errors + after_errors,
        }

    def impact_analysis(self, asset_id: str, depth: int = 2,
                        at: Optional[str] = None,
                        proposed_rule: Optional[str] = None) -> dict:
        """本体约束影响面：按因果边方向扩散，并按状态/时态判定是否真实生效。"""
        depth = max(1, min(int(depth), 4))
        at_norm = self._normalize_at(at) or now_iso()
        root = self._vertex(asset_id)
        root_id = root["id"]
        root_effect = self._impact_effect(
            root["kind"], root["status"], root["properties"], at_norm)
        nodes: Dict[str, dict] = {root_id: {
            "id": root_id, "label": root["label"], "kind": root["kind"],
            "name": root["name"], "status": root["status"], "distance": 0,
            "via_relation": None, "impact_type": "ROOT",
            "path": [root_id], "path_edges": [], "effect": root_effect,
            "facts": self._summary(root),
        }}
        edges: List[dict] = []
        paths: Dict[str, List[str]] = {root_id: [root_id]}
        q = deque([(root_id, 0)])
        seen_edges = set()
        skipped_relations: Counter = Counter()
        while q and len(nodes) < 500:
            current, d = q.popleft()
            if d >= depth:
                continue
            for edge in self.client.list_edges(vertex_id=current, direction="BOTH",
                                               limit=300):
                view = self._edge_view(edge, current, at=at_norm)
                allowed = DOWNSTREAM_IMPACT_DIRECTIONS.get(view["relation"])
                if not allowed or view["direction"] not in allowed:
                    # 只统计“不在下游因果白名单里”的邻域关系；同一条因果边的
                    # 反向投影不计入 skipped，避免重复噪声。
                    if view["relation"] not in DOWNSTREAM_IMPACT_DIRECTIONS:
                        skipped_relations[view["relation"]] += 1
                    continue
                if not view["active"]:
                    continue
                nid = view["neighbor_id"]
                if not nid:
                    continue
                key = (view["source_id"], view["target_id"],
                       view["physical_label"], view["properties"].get("valid_from"))
                if key not in seen_edges:
                    seen_edges.add(key)
                    spec = CAUSAL_IMPACT_RELATIONS.get(view["relation"]) or {}
                    edges.append({
                        "source_id": view["source_id"],
                        "target_id": view["target_id"],
                        "direction": view["direction"],
                        "relation": view["relation"],
                        "physical_label": view["physical_label"],
                        "from_kind": spec.get("from_kind"),
                        "to_kind": spec.get("to_kind"),
                        "rationale": spec.get("rationale"),
                        "active": view["active"],
                        "properties": view["properties"],
                    })
                if nid not in nodes:
                    raw = self.client.get_vertex(nid)
                    if not raw:
                        continue
                    label = raw.get("label")
                    cls = self.semantic.class_by_id.get(label)
                    props = raw.get("properties") or {}
                    kind = cls.get("kind") if cls else None
                    path_edges = list(nodes[current].get("path_edges") or []) + [{
                        "from": current,
                        "relation": view["relation"],
                        "to": nid,
                    }]
                    nodes[nid] = {
                        "id": nid, "label": label, "kind": kind,
                        "name": props.get("name"), "status": props.get("status"),
                        "distance": d + 1, "via_relation": view["relation"],
                        "impact_type": ("REALIZED" if kind == "order"
                                        else ("DIRECT" if d + 1 == 1 else "INDIRECT")),
                        "path": paths.get(current, [current]) + [nid],
                        "path_edges": path_edges,
                        "effect": self._impact_effect(
                            kind, props.get("status"), props, at_norm),
                        "facts": {
                            k: props.get(k) for k in (
                                "estimated_size", "target_size", "rule_expression",
                                "effective_from", "effective_to", "next_run_at",
                                "last_run_at", "schedule", "amount", "city_code",
                                "driver_id", "owner", "updated_at")
                            if props.get(k) not in (None, "")
                        },
                    }
                    paths[nid] = paths.get(current, [current]) + [nid]
                    q.append((nid, d + 1))

        affected = [n for nid, n in nodes.items() if nid != root_id]
        kinds = Counter(n.get("kind") for n in affected)
        impact_types = Counter(n.get("impact_type") for n in affected)
        active_nodes = [n for n in affected if n["effect"]["active"]]
        active_strategies = [n for n in active_nodes if n["kind"] == "strategy"]
        active_canvases = [n for n in active_nodes if n["kind"] == "canvas"]
        realized_orders = [n for n in affected if n["kind"] == "order"]
        realized_amount = sum(
            float(n.get("facts", {}).get("amount") or 0) for n in realized_orders)
        root_size = root["properties"].get("estimated_size")
        crowd_sizes = [
            n.get("facts", {}).get("estimated_size") for n in affected
            if n["kind"] == "crowd"
            and isinstance(n.get("facts", {}).get("estimated_size"), (int, float))
        ]
        if isinstance(root_size, (int, float)) and root_size > 0:
            audience_size, audience_basis = root_size, "root_estimated_size"
        elif crowd_sizes:
            audience_size, audience_basis = max(crowd_sizes), "max_affected_crowd"
        else:
            audience_size, audience_basis = 0, "unavailable"

        if realized_orders or active_canvases or active_strategies:
            risk = "HIGH"
        elif active_nodes:
            risk = "MEDIUM"
        else:
            risk = "LOW"

        skipped_details = {
            relation: {
                "count": count,
                "reason": NON_CAUSAL_RELATION_REASONS.get(
                    relation, "未在本体因果白名单中声明，不参与影响面扩散"),
            } for relation, count in skipped_relations.items()
        }
        relation_counts = Counter(e["relation"] for e in edges)
        root_summary = self._summary(root)
        root_summary["estimated_size"] = root["properties"].get("estimated_size")
        root_summary["rule_expression"] = root["properties"].get("rule_expression")

        return {
            "root": root_summary,
            "depth": depth,
            "at": at_norm,
            "temporal_snapshot": bool(self._normalize_at(at)),
            "definition": "ontology_causal_downstream",
            "definition_text": (
                "影响面只沿本体声明的因果边扩散：Tag/Crowd 定义层变化沿 "
                "crowd_has_tag / strategy_uses_tag 进入 Strategy，再沿 "
                "canvas_references_* 进入 CanvasTask，最终沿 "
                "canvas_generates_order 到达订单；同城、共享标签反向投影、"
                "指标/证据/版本/司机归属均被排除。"
            ),
            "allowed_relations": sorted(DOWNSTREAM_IMPACT_DIRECTIONS),
            "allowed_relation_details": [
                {"relation": name, **spec}
                for name, spec in CAUSAL_IMPACT_RELATIONS.items()
            ],
            "ontology_basis": {
                "layers": {
                    "definition": ["Tag", "Crowd"],
                    "decision": ["Strategy"],
                    "execution": ["CanvasTask"],
                    "fact": ["Order"],
                },
                "temporal_rule": "只有 valid_from <= at <= valid_to 的边参与传播",
                "state_rule": "Strategy 需 ACTIVE 且在 effective_from/to 内；"
                              "CanvasTask 需 RUNNING；Crowd/Tag 需 ACTIVE",
                "exclusion_rule": "同城、共享标签反向投影、指标、证据、版本、"
                                  "司机/车辆归属都不构成资损影响面",
            },
            "risk": risk,
            "verdict": {
                "decision": ("BLOCK_CHANGE" if risk == "HIGH"
                             else ("REVIEW" if risk == "MEDIUM" else "PASS")),
                "reason": (
                    f"命中 {len(active_strategies)} 个生效策略、"
                    f"{len(active_canvases)} 个运行中画布、"
                    f"{len(realized_orders)} 个已发生订单"
                    if risk == "HIGH" else
                    ("只命中人群/标签等定义层对象" if risk == "MEDIUM"
                     else "没有命中任何下游因果对象")
                ),
                "required_gate": ("propose_change -> confirm_change"
                                  if risk != "LOW" else "read_only"),
            },
            "summary": {
                "affected_total": len(affected),
                "by_kind": dict(kinds),
                "by_impact_type": dict(impact_types),
                "by_relation": dict(relation_counts),
                "edge_count": len(edges),
                "skipped_neighbor_relations": dict(skipped_relations),
                "skipped_relation_details": skipped_details,
            },
            "quantification": {
                "audience_size": audience_size,
                "audience_basis": audience_basis,
                "realized_orders": len(realized_orders),
                "realized_order_amount": round(realized_amount, 2),
                "currency": "CNY",
                "active_strategies": len(active_strategies),
                "active_canvases": len(active_canvases),
                "derived_crowds": len([n for n in affected if n["kind"] == "crowd"]),
                "reach_estimate": audience_size if active_canvases else 0,
                "note": "audience_size 取根资产 estimated_size；根资产无规模时取"
                        "受影响人群的最大 estimated_size（去重口径需源系统确认）；"
                        "realized_order_amount 为已发生订单金额之和；"
                        "reach_estimate 为未来执行画布的受众上限估算",
            },
            "change": self._impact_change(root, proposed_rule),
            "affected": affected,
            "edges": edges,
            "paths": paths,
            "generated_at": now_iso(),
        }

    def _cached_impact_for_trace(self, asset_id: str, depth: int,
                                 at: Optional[str],
                                 proposed_rule: Optional[str]) -> dict:
        key = (asset_id, int(depth), at or "", proposed_rule or "")
        now = time.monotonic()
        with self._impact_trace_lock:
            cached = self._impact_trace_cache.get(key)
            if cached and now - cached[0] < 120:
                return cached[1]
        impact = self.impact_analysis(
            asset_id, depth=depth, at=at, proposed_rule=proposed_rule)
        with self._impact_trace_lock:
            self._impact_trace_cache[key] = (now, impact)
            if len(self._impact_trace_cache) > 32:
                oldest = min(self._impact_trace_cache,
                             key=lambda k: self._impact_trace_cache[k][0])
                self._impact_trace_cache.pop(oldest, None)
        return impact

    def impact_trace(self, asset_id: str, depth: int = 3,
                     at: Optional[str] = None,
                     proposed_rule: Optional[str] = None,
                     step: int = 1) -> dict:
        """确定性逐步推导：每次只返回当前步骤的输入、输出、规则和证据。

        与诊断推理链不同，这里不经过大模型；每一步都是影响面算法的真实阶段，
        同样输入必得同样结果，便于向业务方逐步解释本体如何得出裁决。
        """
        impact = self._cached_impact_for_trace(
            asset_id, depth, at, proposed_rule)
        stages = self._impact_trace_stages(impact, depth=depth, at=at)
        total = len(stages)
        current = max(1, min(int(step), total))
        return {
            "asset_id": impact["root"]["id"],
            "at": impact["at"],
            "depth": impact["depth"],
            "step": current,
            "total_steps": total,
            "done": current >= total,
            "stage": stages[current - 1],
            "stages": [
                {"step": i + 1, "key": s["key"], "title": s["title"],
                 "state": ("current" if i + 1 == current
                           else ("done" if i + 1 < current else "pending"))}
                for i, s in enumerate(stages)
            ],
            "meta": {
                "proposed_rule": proposed_rule,
                "generated_at": impact["generated_at"],
                "mode": "deterministic_impact_trace",
            },
        }

    def _impact_trace_stages(self, impact: dict, depth: int,
                             at: Optional[str]) -> List[dict]:
        root = impact["root"]
        summary = impact["summary"]
        edges = impact.get("edges") or []
        affected = impact.get("affected") or []
        skipped = summary.get("skipped_relation_details") or {}
        base = f"/profile/assets/{quote(root['id'], safe='')}/impact-trace"

        def edge_line(e: dict) -> dict:
            props = e.get("properties") or {}
            return {
                "relation": e.get("relation"),
                "from": e.get("source_id"), "to": e.get("target_id"),
                "from_kind": e.get("from_kind"), "to_kind": e.get("to_kind"),
                "direction": e.get("direction"),
                "valid_from": props.get("valid_from"),
                "valid_to": props.get("valid_to"),
            }

        def node_line(n: dict) -> dict:
            return {
                "id": n.get("id"), "kind": n.get("kind"),
                "name": n.get("name"), "status": n.get("status"),
                "distance": n.get("distance"),
                "active": (n.get("effect") or {}).get("active"),
                "reasons": (n.get("effect") or {}).get("reasons") or [],
                "via_relation": n.get("via_relation"),
            }

        definition_edges = [
            e for e in edges if e.get("to_kind") in ("tag", "crowd", "strategy")]
        execution_edges = [
            e for e in edges if e.get("to_kind") in ("canvas", "order")]
        definition_nodes = [
            n for n in affected if n.get("kind") in ("tag", "crowd", "strategy")]
        execution_nodes = [
            n for n in affected if n.get("kind") in ("canvas", "order")]
        excluded = [
            {"relation": rel, "reason": reason,
             "observed_count": (skipped.get(rel) or {}).get("count", 0)}
            for rel, reason in NON_CAUSAL_RELATION_REASONS.items()
        ]
        inactive = [n for n in affected if not (n.get("effect") or {}).get("active")]

        return [
            {
                "step": 1, "key": "resolve_root", "title": "解析根资产",
                "api": f"{base}?depth={depth}&step=1",
                "rule": "按全局 ID 读取顶点，确定对象类型、当前状态、当前规则和规模口径。",
                "input": {"asset_id": root.get("id"), "at": impact["at"]},
                "output": {"root": root,
                           "effect": self._impact_effect(
                               root.get("kind"), root.get("status"),
                               self._vertex(root["id"]).get("properties") or {},
                               impact["at"])},
                "evidence": {"vertices_read": 1},
            },
            {
                "step": 2, "key": "load_contract", "title": "加载本体因果契约",
                "api": f"{base}?depth={depth}&step=2",
                "rule": "只允许 7 条显式因果边沿下游方向传播；白名单外的邻域关系逐条记录排除原因。",
                "input": {"asset_kind": root.get("kind"),
                          "allowed_relations": impact.get("allowed_relations")},
                "output": {
                    "ontology_basis": impact.get("ontology_basis"),
                    "allowed_relation_details": impact.get(
                        "allowed_relation_details"),
                    "excluded_relations": excluded,
                },
                "evidence": {"whitelist_edges": len(
                    impact.get("allowed_relation_details") or []),
                    "excluded_relation_types": len(excluded)},
            },
            {
                "step": 3, "key": "propagate_definition",
                "title": "定义层 → 决策层传播",
                "api": f"{base}?depth={depth}&step=3",
                "rule": "标签沿 crowd_has_tag / strategy_uses_tag 进入人群与策略；父人群沿 crowd_derived_from 进入派生人群；人群沿 strategy_targets_crowd 进入策略。",
                "input": {"depth": impact["depth"],
                          "from": root.get("id"),
                          "relations": ["crowd_has_tag", "crowd_derived_from",
                                        "strategy_uses_tag", "strategy_targets_crowd"]},
                "output": {"edges": [edge_line(e) for e in definition_edges],
                           "objects": [node_line(n) for n in definition_nodes]},
                "evidence": {"edges": len(definition_edges),
                             "objects": len(definition_nodes)},
            },
            {
                "step": 4, "key": "propagate_fact",
                "title": "执行层 → 事实层传播",
                "api": f"{base}?depth={depth}&step=4",
                "rule": "人群/策略沿 canvas_references_* 进入画布，画布沿 canvas_generates_order 到达已发生订单。",
                "input": {"depth": impact["depth"],
                          "relations": ["canvas_references_crowd",
                                        "canvas_references_strategy",
                                        "canvas_generates_order"]},
                "output": {"edges": [edge_line(e) for e in execution_edges],
                           "objects": [node_line(n) for n in execution_nodes]},
                "evidence": {"edges": len(execution_edges),
                             "objects": len(execution_nodes)},
            },
            {
                "step": 5, "key": "effect_check",
                "title": "时态与状态双重生效",
                "api": f"{base}?depth={depth}&step=5",
                "rule": impact["ontology_basis"]["temporal_rule"] + "；" + impact["ontology_basis"]["state_rule"],
                "input": {"at": impact["at"],
                          "objects": [n.get("id") for n in affected]},
                "output": {"objects": [node_line(n) for n in affected],
                           "inactive_objects": [n.get("id") for n in inactive]},
                "evidence": {"checked": len(affected),
                             "active": len(affected) - len(inactive),
                             "inactive": len(inactive)},
            },
            {
                "step": 6, "key": "quantify", "title": "量化影响",
                "api": f"{base}?depth={depth}&step=6",
                "rule": "人数取根资产 estimated_size，根资产无规模时取受影响人群最大值；订单金额只累加已发生订单。",
                "input": {"objects": [n.get("id") for n in affected],
                          "by_kind": summary.get("by_kind")},
                "output": {"quantification": impact.get("quantification"),
                           "summary": {"affected_total": summary.get("affected_total"),
                                       "by_kind": summary.get("by_kind"),
                                       "by_impact_type": summary.get("by_impact_type")}},
                "evidence": {"realized_orders": impact["quantification"].get("realized_orders"),
                             "realized_order_amount": impact["quantification"].get("realized_order_amount"),
                             "audience_basis": impact["quantification"].get("audience_basis")},
            },
            {
                "step": 7, "key": "diff_change", "title": "对比修改前后规则",
                "api": f"{base}?depth={depth}&step=7",
                "rule": "解析修改前后规则，列出条件与城市范围差异；只有规则完全等价的已观测人群才回填人数。",
                "input": {"before_rule": impact["change"].get("before_rule"),
                          "proposed_rule": impact["change"].get("proposed_rule")},
                "output": impact.get("change"),
                "evidence": {"applied": impact["change"].get("applied"),
                             "condition_changes": len(
                                 impact["change"].get("condition_changes") or []),
                             "audience_basis": (
                                 (impact["change"].get("audience") or {})
                                 .get("evidence") or {}).get("basis")},
            },
            {
                "step": 8, "key": "verdict", "title": "生成可解释裁决",
                "api": f"{base}?depth={depth}&step=8",
                "rule": "命中生效订单/画布/策略判 HIGH 并阻断；只命中定义层判 MEDIUM 并复核；否则放行。",
                "input": {"risk": impact.get("risk"),
                          "active_strategies": impact["quantification"].get("active_strategies"),
                          "active_canvases": impact["quantification"].get("active_canvases"),
                          "realized_orders": impact["quantification"].get("realized_orders")},
                "output": {"verdict": impact.get("verdict"),
                           "quantification": impact.get("quantification"),
                           "skipped_relation_details": skipped},
                "evidence": {"decision": (impact.get("verdict") or {}).get("decision"),
                             "required_gate": (impact.get("verdict") or {}).get("required_gate")},
            },
        ]

    # ----------------------------------------------------------------- trace
    def trace_evidence(self, asset_id: str, at: Optional[str] = None) -> dict:
        at_norm = self._normalize_at(at)
        asset = self._vertex(asset_id)
        edges = self.client.list_edges(vertex_id=asset_id, direction="BOTH",
                                       limit=500)
        evidence_edges = []
        version_edges = []
        for edge in edges:
            view = self._edge_view(edge, asset_id, at=at_norm)
            if at_norm and not view["active"]:
                continue
            if view["relation"] == "asset_has_evidence" and view["direction"] == "OUT":
                evidence_edges.append(view)
            elif view["relation"] == "asset_has_version" and view["direction"] == "OUT":
                version_edges.append(view)

        evidence_items: List[dict] = []
        timeline: List[dict] = []
        for view in evidence_edges:
            raw = self.client.get_vertex(view["neighbor_id"])
            if not raw:
                continue
            props = raw.get("properties") or {}
            observed = props.get("observed_at") or props.get("created_at")
            if at_norm and observed and str(observed) > at_norm:
                continue
            item = {
                "edge": {"valid_from": view["properties"].get("valid_from"),
                         "valid_to": view["properties"].get("valid_to"),
                         "confidence": view["properties"].get("confidence"),
                         "active": view["active"]},
                "vertex": {"id": str(raw.get("id")), "label": raw.get("label"),
                           "properties": props},
            }
            evidence_items.append(item)
            timeline.append({
                "at": props.get("observed_at") or props.get("created_at"),
                "type": "evidence",
                "id": str(raw.get("id")),
                "title": props.get("name"),
                "source_system": props.get("source_system"),
                "content": props.get("content"),
            })

        version_items: List[dict] = []
        for view in version_edges:
            raw = self.client.get_vertex(view["neighbor_id"])
            if not raw:
                continue
            props = raw.get("properties") or {}
            created = props.get("created_at") or props.get("updated_at")
            if at_norm and created and str(created) > at_norm:
                continue
            vid = str(raw.get("id"))
            operators = []
            for op_edge in self.client.list_edges(vertex_id=vid, direction="OUT",
                                                  label="version_generated_by",
                                                  limit=20):
                op_view = self._edge_view(op_edge, vid)
                op_raw = self.client.get_vertex(op_view["neighbor_id"]) if op_view["neighbor_id"] else None
                if op_raw:
                    operators.append({
                        "id": str(op_raw.get("id")),
                        "name": (op_raw.get("properties") or {}).get("name"),
                        "action": op_view["properties"].get("action"),
                    })
            item = {"vertex": {"id": vid, "label": raw.get("label"),
                               "properties": props},
                    "operators": operators}
            version_items.append(item)
            timeline.append({
                "at": props.get("created_at") or props.get("updated_at"),
                "type": "version",
                "id": vid,
                "title": props.get("name"),
                "version": props.get("version"),
                "change_summary": props.get("change_summary"),
                "operators": operators,
            })

        timeline.sort(key=lambda x: str(x.get("at") or ""))
        sources = sorted({e["vertex"]["properties"].get("source_system")
                          for e in evidence_items
                          if e["vertex"]["properties"].get("source_system")})
        return {
            "asset": self._summary(asset),
            "at": at_norm or now_iso(),
            "temporal_snapshot": bool(at_norm),
            "evidence_count": len(evidence_items),
            "version_count": len(version_items),
            "evidence": evidence_items,
            "versions": version_items,
            "timeline": timeline,
            "coverage": {
                "source_systems": sources,
                "has_evidence": bool(evidence_items),
                "has_versions": bool(version_items),
            },
            "generated_at": now_iso(),
        }

    # ---------------------------------------------------------------- metric
    def query_metric(self, asset_id: Optional[str] = None,
                     metric_id: Optional[str] = None,
                     name: Optional[str] = None,
                     limit: int = 20) -> dict:
        limit = max(1, min(int(limit), 100))
        items: List[dict] = []
        if metric_id:
            raw = self.client.get_vertex(metric_id)
            if not raw:
                raise AssetNotFound(f"指标不存在: {metric_id}")
            items.append({"vertex": self._metric_view(raw), "edge": None})
        elif asset_id:
            self._vertex(asset_id)
            for view in self._neighbors(asset_id):
                if view["relation"] != "asset_has_metric":
                    continue
                raw = self.client.get_vertex(view["neighbor_id"])
                if raw:
                    items.append({
                        "vertex": self._metric_view(raw),
                        "edge": {
                            "direction": view["direction"],
                            "valid_from": view["properties"].get("valid_from"),
                            "valid_to": view["properties"].get("valid_to"),
                            "weight": view["properties"].get("weight"),
                        },
                    })
        else:
            q = (name or "").lower()
            for raw in self.client.list_vertices(label="Metric", limit=500):
                props = raw.get("properties") or {}
                text = " ".join(str(props.get(k) or "") for k in (
                    "name", "display_name", "metric_name", "description")).lower()
                if q and q not in text:
                    continue
                items.append({"vertex": self._metric_view(raw), "edge": None})

        values = [i["vertex"]["value"] for i in items
                  if isinstance(i["vertex"].get("value"), (int, float))]
        return {
            "asset_id": asset_id, "metric_id": metric_id, "name": name,
            "total": len(items), "items": items[:limit],
            "summary": {
                "count": len(items),
                "valued_count": len(values),
                "average": round(sum(values) / len(values), 6) if values else None,
                "units": sorted({i["vertex"].get("unit") for i in items
                                 if i["vertex"].get("unit")}),
            },
            "generated_at": now_iso(),
        }

    @staticmethod
    def _metric_view(raw: dict) -> dict:
        props = raw.get("properties") or {}
        return {
            "id": str(raw.get("id")), "label": raw.get("label"),
            "name": props.get("name"),
            "display_name": props.get("display_name"),
            "metric_name": props.get("metric_name"),
            "value": props.get("value"),
            "unit": props.get("unit"),
            "period": props.get("period"),
            "definition": props.get("definition"),
            "expression": props.get("expression"),
            "status": props.get("status"),
            "source_system": props.get("source_system"),
            "properties": props,
        }

    # ---------------------------------------------------------------- writes
    def propose_change(self, action: str, asset_id: str, payload: dict,
                       reason: str, actor: str) -> dict:
        gate = self.semantic.defn.get("write_gate", {})
        if action not in gate.get("allowed_actions", []):
            raise ProfileError(
                f"不允许的写动作: {action}，允许: {gate.get('allowed_actions')}")
        if not reason or not actor:
            raise ProfileError("写操作必须提供 reason 与 actor")
        asset = self._vertex(asset_id)
        normalized = self._validate_action(action, asset, payload or {})
        return self.audit.create_proposal(action, asset_id, normalized, reason, actor)

    def _validate_action(self, action: str, asset: dict, payload: dict) -> dict:
        class_name = asset["label"]
        props = asset["properties"]
        out = dict(payload)
        if action == "update_status":
            status = str(out.get("status") or "").upper()
            if not status:
                raise ProfileError("update_status 需要 status")
            if class_name not in self.semantic.state_machines:
                raise ProfileError(f"{class_name} 没有定义状态机")
            current = props.get("status") or "DRAFT"
            if not self.semantic.transition_allowed(class_name, current, status):
                raise ProfileError(f"非法状态迁移: {current} -> {status}")
            out["status"] = status
            return out

        if action == "attach_tag":
            if class_name != "Crowd":
                raise ProfileError("attach_tag 只允许对 Crowd 操作")
            tag_id = out.get("tag_id")
            if not tag_id:
                raise ProfileError("attach_tag 需要 tag_id")
            tag = self._vertex(tag_id)
            if tag["label"] != "Tag":
                raise ProfileError(f"{tag_id} 不是 Tag")
            edge_props = _clean({
                "weight": out.get("weight"),
                "valid_from": out.get("valid_from") or now_iso(),
                "valid_to": out.get("valid_to"),
                "source_system": out.get("source_system") or "profile_ai",
                "created_at": now_iso(),
            })
            violations = self.semantic.validate_relation(
                "crowd_has_tag", "Crowd", "Tag", edge_props,
                asset["id"], tag_id)
            if has_errors(violations):
                raise ProfileError(f"标签关系校验失败: {violations}")
            out.update({"tag_id": tag_id, "edge_properties": edge_props})
            return out

        if action == "attach_evidence":
            evidence = out.get("evidence")
            evidence_id = out.get("evidence_id")
            if not evidence and not evidence_id:
                raise ProfileError("attach_evidence 需要 evidence_id 或 evidence")
            if evidence:
                evidence = dict(evidence)
                if not evidence.get("id"):
                    source_system = evidence.get("source_system") or "profile_ai"
                    evidence["id"] = (
                        f"evidence:{source_system}:{uuid.uuid4().hex[:12]}")
                evidence.setdefault("observed_at", now_iso())
                evidence.setdefault("created_at", now_iso())
                evidence.setdefault("updated_at", now_iso())
                violations = self.semantic.validate_vertex(
                    "Evidence", _clean(evidence), evidence["id"])
                if has_errors(violations):
                    raise ProfileError(f"证据校验失败: {violations}")
                evidence_id = evidence["id"]
            else:
                ev = self._vertex(evidence_id)
                if ev["label"] != "Evidence":
                    raise ProfileError(f"{evidence_id} 不是 Evidence")
            domains = self.semantic.relation("asset_has_evidence")["domains"]
            if class_name not in domains:
                raise ProfileError(f"{class_name} 不支持关联证据")
            edge_props = _clean({
                "confidence": out.get("confidence"),
                "valid_from": out.get("valid_from") or now_iso(),
                "valid_to": out.get("valid_to"),
                "source_system": out.get("source_system") or "profile_ai",
                "created_at": now_iso(),
            })
            violations = self.semantic.validate_relation(
                "asset_has_evidence", class_name, "Evidence", edge_props,
                asset["id"], evidence_id)
            if has_errors(violations):
                raise ProfileError(f"证据关系校验失败: {violations}")
            out.update({"evidence_id": evidence_id, "evidence": evidence,
                        "edge_properties": edge_props})
            return out

        if action == "publish_strategy":
            if class_name != "Strategy":
                raise ProfileError("publish_strategy 只允许对 Strategy 操作")
            current = props.get("status") or "DRAFT"
            if not self.semantic.transition_allowed("Strategy", current, "ACTIVE"):
                raise ProfileError(f"策略不能从 {current} 发布为 ACTIVE")
            out["status"] = "ACTIVE"
            out.setdefault("effective_from", now_iso())
            return out

        if action == "update_properties":
            cls = self.semantic.class_by_id.get(class_name)
            allowed = set((cls or {}).get("properties", []))
            changes = {k: v for k, v in out.items()
                       if k in allowed and k not in ("status",)}
            if not changes:
                raise ProfileError("update_properties 没有可更新的白名单字段")
            return {"changes": changes}

        raise ProfileError(f"未实现的写动作: {action}")

    def confirm_change(self, proposal_id: str, approver: str,
                       approve: bool = True) -> dict:
        proposal = self.audit.get_proposal(proposal_id)
        if not proposal:
            raise AssetNotFound(f"提议不存在: {proposal_id}")
        if proposal["status"] != "PENDING":
            raise ProfileError(f"提议 {proposal_id} 当前状态为 {proposal['status']}，不能重复确认")
        if not approver:
            raise ProfileError("人工确认必须提供 approver")

        if not approve:
            return self.audit.finish_proposal(
                proposal_id, "REJECTED", approver,
                {"asset_id": proposal["asset_id"], "action": proposal["action"],
                 "approved": False})

        try:
            result = self._execute(proposal)
            with self._impact_trace_lock:
                self._impact_trace_cache.clear()
        except Exception as e:
            self.audit.finish_proposal(
                proposal_id, "FAILED", approver,
                {"asset_id": proposal["asset_id"], "action": proposal["action"],
                 "approved": True, "error": f"{type(e).__name__}: {e}"})
            if isinstance(e, ProfileError):
                raise
            raise ProfileError(f"执行失败: {type(e).__name__}: {e}") from e

        return self.audit.finish_proposal(
            proposal_id, "EXECUTED", approver,
            {"asset_id": proposal["asset_id"], "action": proposal["action"],
             "approved": True, "written": result})

    def _execute(self, proposal: dict) -> dict:
        action = proposal["action"]
        asset_id = proposal["asset_id"]
        payload = proposal["payload"] or {}
        asset = self._vertex(asset_id)
        class_name = asset["label"]
        ts = now_iso()

        if action == "update_status":
            status = payload["status"]
            self.semantic.validate_vertex(
                class_name, {**asset["properties"], "status": status}, asset_id)
            self.client.update_vertex(asset_id, class_name, _clean({
                "status": status,
                "status_reason": payload.get("status_reason"),
                "updated_at": ts,
            }))
            return {"asset_id": asset_id, "status": status}

        if action == "attach_tag":
            label = self.semantic.edge_label("crowd_has_tag", class_name)
            self.client.add_edge(label, asset_id, payload["tag_id"],
                                 payload["edge_properties"])
            return {"asset_id": asset_id, "edge": label,
                    "target": payload["tag_id"],
                    "properties": payload["edge_properties"]}

        if action == "attach_evidence":
            evidence = payload.get("evidence")
            evidence_id = payload["evidence_id"]
            if evidence:
                evidence_id = self.client.upsert_vertex(
                    "Evidence", evidence_id, _clean(evidence)).get("id", evidence_id)
            label = self.semantic.edge_label("asset_has_evidence", class_name)
            self.client.add_edge(label, asset_id, evidence_id,
                                 payload["edge_properties"])
            return {"asset_id": asset_id, "edge": label,
                    "evidence_id": evidence_id,
                    "properties": payload["edge_properties"]}

        if action == "publish_strategy":
            self.client.update_vertex(asset_id, class_name, _clean({
                "status": "ACTIVE",
                "effective_from": payload.get("effective_from") or ts,
                "updated_at": ts,
            }))
            return {"asset_id": asset_id, "status": "ACTIVE",
                    "effective_from": payload.get("effective_from") or ts}

        if action == "update_properties":
            changes = dict(payload.get("changes") or {})
            changes["updated_at"] = ts
            self.client.update_vertex(asset_id, class_name, _clean(changes))
            return {"asset_id": asset_id, "changes": changes}

        raise ProfileError(f"未实现的写动作: {action}")

    def list_proposals(self, status: Optional[str] = None,
                       limit: int = 50) -> dict:
        return {"items": self.audit.list_proposals(status, limit)}

    def list_audit(self, limit: int = 100) -> dict:
        return {"items": self.audit.list_audit(limit)}