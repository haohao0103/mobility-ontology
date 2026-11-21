"""系统级血缘图：数据源 → 对象类型 → 属性 → 规则/动作/函数 → 消费。

Palantir Lineage 的本体级降级档：不接 OpenLineage/Marquez，直接从本体 JSON
的声明 + 事实库的 evidence/audit/relations 构建可追溯图。

设计纪律：
1. 血缘是「声明 + 事实」的派生视图，不新增权威源——每次查询现算，
   本体 JSON 改了血缘立刻跟着变（承重）。
2. 节点 id 用稳定前缀（源/类/属性/规则/动作/函数/对象），边带 kind 与
   来源位置（本体键路径或事实表），可逐跳解释。
3. 支持正向（属性被谁消费）与反向（属性的数据源头）两种追溯。
"""
from __future__ import annotations

import re
from typing import Any


def _rid(kind: str, name: str) -> str:
    return f"{kind}:{name}"


def _extract_props(node: Any) -> set[str]:
    """从规则 AST / judge 绑定里抽取引用的属性名。"""
    out: set[str] = set()
    if isinstance(node, dict):
        for k, v in node.items():
            if k in ("field", "prop", "property") and isinstance(v, str):
                out.add(v)
            if k == "bindings" and isinstance(v, dict):
                for spec in v.values():
                    if isinstance(spec, str):
                        out.update(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", spec))
            out.update(_extract_props(v))
    elif isinstance(node, list):
        for item in node:
            out.update(_extract_props(item))
    return out


class LineageGraph:
    def __init__(self, sem: Any, ext: Any = None, facts: Any = None):
        self.sem = sem
        self.ext = ext
        self.facts = facts

    # -------------------------------------------------- 构建
    def build(self) -> dict:
        nodes: dict[str, dict] = {}
        edges: list[dict] = []

        def node(nid: str, kind: str, label: str, **extra) -> None:
            nodes.setdefault(nid, {"id": nid, "kind": kind, "label": label, **extra})

        def edge(src: str, dst: str, kind: str, source: str) -> None:
            edges.append({"from": src, "to": dst, "kind": kind, "source": source})

        raw = self.sem.raw
        classes = raw.get("classes", [])
        known_classes = {c["id"] for c in classes}

        for cls in classes:
            cid = cls["id"]
            node(_rid("class", cid), "class", cls.get("label") or cid)
            for prop in cls.get("properties", []):
                pid = _rid("prop", f"{cid}.{prop}")
                node(pid, "property", f"{cid}.{prop}", class_id=cid, prop=prop)
                edge(_rid("class", cid), pid, "has_property", f"classes[{cid}].properties")

        for ds in raw.get("data_sources", []):
            cls = ds.get("class")
            if cls not in known_classes:
                continue
            sid = _rid("source", ds.get("system", "?") + "/" + ds.get("tool", "?"))
            node(sid, "data_source", ds.get("tool") or ds.get("system", "?"),
                 system=ds.get("system"), key=ds.get("key"))
            edge(sid, _rid("class", cls), "feeds", "data_sources[]")
            if ds.get("key"):
                edge(sid, _rid("prop", f"{cls}.{ds['key']}"), "feeds_key",
                     "data_sources[].key")

        for rel in raw.get("relations", []):
            dom, rng = rel.get("domain"), rel.get("range")
            if dom in known_classes and rng in known_classes:
                edge(_rid("class", dom), _rid("class", rng), "links",
                     f"relations[{rel['id']}]")

        for rule in raw.get("rules", []):
            rid = _rid("rule", rule.get("id", "?"))
            node(rid, "rule", rule.get("name") or rule.get("id", "?"))
            target = rule.get("domain")
            for prop in sorted(_extract_props(rule.get("when")) |
                               _extract_props(rule.get("judge"))):
                if target in known_classes:
                    edge(_rid("prop", f"{target}.{prop}"), rid, "used_by",
                         f"rules[{rule.get('id')}].when/judge")
            for binding in (rule.get("judge", {}).get("calc", {}) or {}).get("bindings", {}).values():
                if isinstance(binding, str) and "." in binding:
                    cls, prop = binding.split(".", 1)
                    if cls in known_classes:
                        edge(rid, _rid("prop", f"{cls}.{prop}"), "writes",
                             f"rules[{rule.get('id')}].judge.calc")

        for action in raw.get("actions", []):
            aid = _rid("action", action.get("id", "?"))
            node(aid, "action", action.get("label") or action.get("id", "?"))
            target = action.get("target")
            if target in known_classes:
                edge(_rid("class", target), aid, "acts_on", f"actions[{action.get('id')}].target")
                for p in action.get("params", []) or []:
                    if isinstance(p, dict) and p.get("name"):
                        edge(_rid("prop", f"{target}.{p['name']}"), aid, "param_of",
                             f"actions[{action.get('id')}].params")

        for fn in self._functions():
            fid = _rid("function", fn.get("id", "?"))
            node(fid, "function", fn.get("label") or fn.get("id", "?"))
            for target in self._fn_targets(fn):
                if target in known_classes:
                    edge(fid, _rid("class", target), "reads", f"functions[{fn.get('id')}].target")

        if self.facts is not None:
            self._facts_edges(node, edge)

        return {"nodes": list(nodes.values()), "edges": edges}

    def _functions(self) -> list[dict]:
        out = list(self.sem.raw.get("functions", []) or [])
        if self.ext is not None:
            raw = getattr(self.ext, "raw", {}) or {}
            out += raw.get("function_registry", []) or []
        return [f for f in out if isinstance(f, dict)]

    @staticmethod
    def _fn_targets(fn: dict) -> list[str]:
        target = fn.get("target")
        if isinstance(target, str):
            return [target]
        if isinstance(target, list):
            return [t for t in target if isinstance(t, str)]
        if isinstance(target, dict):
            return [t for t in target.values() if isinstance(t, str)]
        return []

    def _facts_edges(self, node, edge) -> None:
        try:
            rows = self.facts.all(
                "SELECT evidence_id, object_id, source_system, evidence_type "
                "FROM evidence")
        except Exception:  # noqa: BLE001
            return
        for r in rows:
            eid = _rid("evidence", r["evidence_id"])
            node(eid, "evidence", r["evidence_type"], source_system=r["source_system"])
            node(_rid("object", r["object_id"]), "object", r["object_id"])
            edge(eid, _rid("object", r["object_id"]), "evidences", "evidence[]")
            edge(_rid("source", r["source_system"]), eid, "produced",
                 "evidence[].source_system")

    # -------------------------------------------------- 查询
    def trace_property(self, class_id: str, prop: str) -> dict:
        graph = self.build()
        target = _rid("prop", f"{class_id}.{prop}")
        upstream, downstream = [], []
        for e in graph["edges"]:
            if e["to"] == target:
                upstream.append(e)
            if e["from"] == target:
                downstream.append(e)

        def expand(seed: list[dict], reverse: bool) -> list[dict]:
            seen, queue, out = {target}, list(seed), []
            while queue:
                e = queue.pop(0)
                other = e["from"] if reverse else e["to"]
                out.append(e)
                if other in seen:
                    continue
                seen.add(other)
                if reverse:
                    queue.extend(x for x in graph["edges"] if x["to"] == other)
                else:
                    queue.extend(x for x in graph["edges"] if x["from"] == other)
            return out

        return {
            "property": f"{class_id}.{prop}",
            "upstream": expand(upstream, True),
            "downstream": expand(downstream, False),
        }

    def trace_object(self, object_id: str) -> dict:
        graph = self.build()
        target = _rid("object", object_id)
        related = [e for e in graph["edges"]
                   if e["from"] == target or e["to"] == target]
        return {"object_id": object_id, "edges": related}

    def summary(self) -> dict:
        g = self.build()
        by_kind: dict[str, int] = {}
        for n in g["nodes"]:
            by_kind[n["kind"]] = by_kind.get(n["kind"], 0) + 1
        edge_kinds: dict[str, int] = {}
        for e in g["edges"]:
            edge_kinds[e["kind"]] = edge_kinds.get(e["kind"], 0) + 1
        return {"nodes": len(g["nodes"]), "edges": len(g["edges"]),
                "by_kind": by_kind, "by_edge_kind": edge_kinds}
