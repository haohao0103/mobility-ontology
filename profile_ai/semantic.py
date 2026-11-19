"""画像资产语义层：ID 规范、关系定义域/值域、状态机、时态边与约束校验。

本模块只做领域语义判断，不依赖 HugeGraph / FastAPI，可独立单测。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Iterable, List, Optional

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "semantic_layer_profile.json")
ID_RE = re.compile(
    r"^(crowd|tag|strategy|canvas|metric|evidence|version|operator|"
    r"driver|vehicle|city|order):"
    r"[a-z0-9][a-z0-9_.-]*:"
    r"[A-Za-z0-9][A-Za-z0-9_.:-]*$"
)
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


class ProfileSemanticLayer:
    """画像语义层唯一口径。"""

    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path
        with open(path, "r", encoding="utf-8") as fh:
            self.defn: Dict[str, Any] = json.load(fh)
        self.meta = self.defn["meta"]
        self.properties: Dict[str, dict] = self.defn["properties"]
        self.classes: List[dict] = self.defn["classes"]
        self.class_by_id = {c["id"]: c for c in self.classes}
        self.class_by_kind = {c["kind"]: c for c in self.classes}
        self.relations: List[dict] = self.defn["relations"]
        self.relation_by_id = {r["id"]: r for r in self.relations}
        self.state_machines: Dict[str, dict] = self.defn.get("state_machines", {})
        self.constraints: List[dict] = self.defn.get("constraints", [])
        self.context_projection: Dict[str, List[str]] = self.defn.get(
            "context_projection", {})

    # ------------------------------------------------------------------ IDs
    def validate_asset_id(self, asset_id: str,
                          expected_class: Optional[str] = None) -> List[dict]:
        violations: List[dict] = []
        if not asset_id or not isinstance(asset_id, str):
            return [self._v("C1", "ERROR", "资产 ID 不能为空", "id", asset_id)]
        if not ID_RE.match(asset_id):
            violations.append(self._v(
                "C1", "ERROR",
                "资产 ID 必须符合 {kind}:{source_system}:{source_id}", "id", asset_id))
            return violations

        kind = asset_id.split(":", 1)[0]
        cls = self.class_by_kind.get(kind)
        if cls is None:
            violations.append(self._v("C1", "ERROR", f"未知资产类型前缀: {kind}", "id", asset_id))
        elif expected_class and cls["id"] != expected_class:
            violations.append(self._v(
                "C1", "ERROR",
                f"资产类型不匹配，期望 {expected_class}，实际 {cls['id']}", "id", asset_id))
        return violations

    @staticmethod
    def kind_of(asset_id: str) -> Optional[str]:
        return asset_id.split(":", 1)[0] if asset_id and ":" in asset_id else None

    def class_of(self, asset_id: str) -> Optional[str]:
        cls = self.class_by_kind.get(self.kind_of(asset_id) or "")
        return cls["id"] if cls else None

    # ------------------------------------------------------------- vertices
    def validate_vertex(self, class_name: str, properties: Dict[str, Any],
                        asset_id: Optional[str] = None) -> List[dict]:
        violations: List[dict] = []
        cls = self.class_by_id.get(class_name)
        if cls is None:
            return [self._v("C2", "ERROR", f"未定义的顶点类型: {class_name}")]

        if asset_id:
            violations.extend(self.validate_asset_id(asset_id, class_name))

        props = properties or {}
        unknown = [k for k in props if k not in self.properties]
        if unknown:
            violations.append(self._v(
                "C2", "ERROR", f"{class_name} 包含未登记属性: {', '.join(unknown)}"))

        allowed = set(cls.get("properties", []))
        not_allowed = [k for k in props if k in self.properties and k not in allowed]
        if not_allowed:
            violations.append(self._v(
                "C2", "ERROR",
                f"{class_name} 不允许使用属性: {', '.join(not_allowed)}"))

        for key in cls.get("required", []):
            if props.get(key) in (None, ""):
                violations.append(self._v(
                    "C2", "ERROR", f"{class_name} 缺少必填属性: {key}", key, props.get(key)))

        status = props.get("status")
        machine = self.state_machines.get(class_name)
        if status and machine and status not in machine.get("states", []):
            violations.append(self._v(
                "C3", "ERROR", f"{class_name} 非法状态: {status}", "status", status))

        if class_name == "Evidence":
            if not props.get("observed_at"):
                violations.append(self._v(
                    "C10", "ERROR", "证据缺少 observed_at", "observed_at"))
            if not props.get("source_system"):
                violations.append(self._v(
                    "C10", "ERROR", "证据缺少 source_system", "source_system"))

        if class_name == "Strategy" and status == "ACTIVE" and not props.get("effective_from"):
            violations.append(self._v(
                "C11", "WARN", "ACTIVE 策略建议提供 effective_from", "effective_from"))

        if class_name == "Crowd" and not props.get("permission_scope"):
            violations.append(self._v(
                "C12", "WARN", "人群建议声明 permission_scope", "permission_scope"))

        for key in ("valid_from", "valid_to"):
            if props.get(key) and not ISO_RE.match(str(props[key])):
                violations.append(self._v(
                    "C4", "WARN", f"{key} 建议使用 ISO 8601 时间格式", key, props.get(key)))
        return violations

    # -------------------------------------------------------------- edges
    def relation(self, predicate: str) -> Optional[dict]:
        return self.relation_by_id.get(predicate)

    def edge_label(self, predicate: str, source_class: str) -> str:
        spec = self.relation_by_id.get(predicate)
        if spec is None:
            raise KeyError(f"未定义关系: {predicate}")
        domains = spec.get("domains") or [spec.get("domain")]
        if source_class not in domains:
            raise ValueError(
                f"关系 {predicate} 不支持源类型 {source_class}，允许: {domains}")
        physical = spec.get("physical")
        if not physical:
            return predicate
        return physical.replace("{domain_snake}", _snake(source_class))

    def physical_edges(self) -> List[dict]:
        """展开语义关系 -> HugeGraph 物理边标签。"""
        rows: List[dict] = []
        for spec in self.relations:
            for source in spec.get("domains") or [spec.get("domain")]:
                rows.append({
                    "name": self.edge_label(spec["id"], source),
                    "relation": spec["id"],
                    "source_label": source,
                    "target_label": spec["range"],
                    "properties": spec.get("properties", []),
                    "sort_keys": spec.get("sort_keys", []),
                })
        return rows

    def validate_relation(self, predicate: str, source_class: str,
                          target_class: str, properties: Dict[str, Any],
                          source_id: Optional[str] = None,
                          target_id: Optional[str] = None) -> List[dict]:
        violations: List[dict] = []
        spec = self.relation_by_id.get(predicate)
        if spec is None:
            return [self._v("C5", "ERROR", f"未定义关系: {predicate}")]

        domains = spec.get("domains") or [spec.get("domain")]
        if source_class not in domains:
            violations.append(self._v(
                "C5", "ERROR",
                f"{predicate} 源类型非法: {source_class}，允许: {domains}"))
        if target_class != spec.get("range"):
            violations.append(self._v(
                "C5", "ERROR",
                f"{predicate} 目标类型非法: {target_class}，期望: {spec.get('range')}"))

        props = properties or {}
        allowed = set(spec.get("properties", []))
        unknown = [k for k in props if k not in allowed]
        if unknown:
            violations.append(self._v(
                "C5", "ERROR", f"{predicate} 包含未登记边属性: {', '.join(unknown)}"))

        for key in spec.get("required", []):
            if props.get(key) in (None, ""):
                violations.append(self._v(
                    "C4", "ERROR", f"{predicate} 缺少必填边属性: {key}", key))

        if spec.get("temporal"):
            if not props.get("valid_from"):
                violations.append(self._v(
                    "C4", "ERROR", f"{predicate} 是时态边，必须提供 valid_from", "valid_from"))
            if props.get("valid_from") and props.get("valid_to"):
                if str(props["valid_to"]) < str(props["valid_from"]):
                    violations.append(self._v(
                        "C4", "ERROR", "valid_to 不能早于 valid_from", "valid_to"))

        if spec.get("forbid_self") and source_id and source_id == target_id:
            violations.append(self._v(
                "C6", "ERROR", f"{predicate} 禁止自环", "target", target_id))

        return violations

    # ---------------------------------------------------------- state machine
    def transition_allowed(self, class_name: str, from_status: str,
                           to_status: str) -> bool:
        machine = self.state_machines.get(class_name)
        if not machine:
            return False
        return to_status in machine.get("transitions", {}).get(from_status, [])

    # ---------------------------------------------------------- projection
    def projection_fields(self, level: str) -> List[str]:
        return self.context_projection.get(level.upper(), self.context_projection["OVERVIEW"])

    # ------------------------------------------------------------- dictionary
    def dictionary(self) -> dict:
        return {
            "meta": self.meta,
            "class_count": len(self.classes),
            "relation_count": len(self.relations),
            "constraint_count": len(self.constraints),
            "state_machine_count": len(self.state_machines),
            "classes": [{
                "id": c["id"], "label": c["label"], "kind": c["kind"],
                "description": c["description"], "required": c.get("required", []),
                "properties": c.get("properties", []),
            } for c in self.classes],
            "relations": [{
                "id": r["id"], "label": r["label"],
                "domains": r.get("domains") or [r.get("domain")],
                "range": r.get("range"), "temporal": r.get("temporal", False),
                "description": r.get("description", ""),
                "properties": r.get("properties", []),
                "required": r.get("required", []),
            } for r in self.relations],
            "property_defs": self.properties,
            "state_machines": self.state_machines,
            "constraints": self.constraints,
            "context_projection": self.context_projection,
            "write_gate": self.defn.get("write_gate", {}),
        }

    @staticmethod
    def _v(code: str, severity: str, message: str,
           field: Optional[str] = None, value: Any = None) -> dict:
        out = {"code": code, "severity": severity, "message": message}
        if field is not None:
            out["field"] = field
        if value is not None:
            out["value"] = value
        return out


def has_errors(violations: Iterable[dict]) -> bool:
    return any(v.get("severity") == "ERROR" for v in violations)