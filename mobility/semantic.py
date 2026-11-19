"""本体层：加载语义层定义，校验关系合法性，导出领域词典。"""
from __future__ import annotations
import json
import os
from datetime import datetime, timedelta

DEFAULT_SEMANTIC_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "semantic_layer.json"
)


class OntologyViolation(Exception):
    """关系不符合本体定义域/值域时抛出。"""


class ConstraintViolation(Exception):
    """业务约束被违反时抛出。"""


class BusinessReject(Exception):
    """规则判定为拒绝（如超出服务区、少付、超额退款）时抛出。"""


class Clock:
    """确定性逻辑时钟：所有业务事实时间可复现、可溯源。"""

    def __init__(self, start: str = "2026-11-12 09:00:00"):
        self.t = datetime.strptime(start, "%Y-%m-%d %H:%M:%S")

    def now(self) -> str:
        return self.t.strftime("%Y-%m-%d %H:%M:%S")

    def advance(self, minutes: float = 1) -> str:
        self.t += timedelta(minutes=minutes)
        return self.now()

    @staticmethod
    def minutes_between(a: str, b: str) -> float:
        fmt = "%Y-%m-%d %H:%M:%S"
        return (datetime.strptime(b, fmt) - datetime.strptime(a, fmt)).total_seconds() / 60.0


class SemanticLayer:
    def __init__(self, path: str = DEFAULT_SEMANTIC_PATH):
        with open(path, encoding="utf-8") as f:
            self.raw = json.load(f)
        self.path = path
        self.classes = {c["id"]: c for c in self.raw["classes"]}
        self.relations = {r["id"]: r for r in self.raw["relations"]}
        self.lifecycle = self.raw["lifecycle"]
        self.constraints = self.raw["constraints"]
        self.pricing_cfg = self.raw["pricing"]
        self.address_region = self.raw["address_region"]
        self.service_area = set(self.raw["service_area"])
        self.fee_strategies = self.raw["fee_strategies"]
        self._rule_index: dict[str, list[dict]] = {}
        for r in self.raw["rules"]:
            self._rule_index.setdefault(r["domain"], []).append(r)
        for domain in self._rule_index:
            self._rule_index[domain].sort(key=lambda x: -x["priority"])

    # ---------- 本体：关系合法性 ----------
    def validate_relation(self, predicate: str, subject_type: str, object_type: str) -> None:
        rel = self.relations.get(predicate)
        if rel is None:
            raise OntologyViolation(f"未定义的关系: {predicate}")
        if subject_type != rel["domain"]:
            raise OntologyViolation(
                f"关系 {predicate} 的定义域应为 {rel['domain']}，实际 {subject_type}"
            )
        if object_type != rel["range"]:
            raise OntologyViolation(
                f"关系 {predicate} 的值域应为 {rel['range']}，实际 {object_type}"
            )

    def is_temporal(self, predicate: str) -> bool:
        rel = self.relations.get(predicate)
        return bool(rel and rel.get("temporal"))

    # ---------- 状态机 ----------
    def can_transition(self, kind: str, frm: str, to: str) -> bool:
        lc = self.lifecycle.get(kind)
        if lc is None:
            return True
        allowed = lc["transitions"].get(frm, [])
        return to in allowed

    def assert_transition(self, kind: str, frm: str, to: str) -> None:
        if not self.can_transition(kind, frm, to):
            raise BusinessReject(f"非法状态转移 {kind}: {frm} → {to}")

    # ---------- 规则 ----------
    def rules(self, domain: str) -> list[dict]:
        return self._rule_index.get(domain, [])

    def rule(self, rule_id: str) -> dict | None:
        for rs in self._rule_index.values():
            for r in rs:
                if r["id"] == rule_id:
                    return r
        return None

    # ---------- 空间 / 定价 ----------
    def region_of(self, address: str) -> str | None:
        return self.address_region.get(address)

    def in_service_area(self, address: str) -> bool:
        return self.address_region.get(address) in self.service_area

    def pricing(self, vehicle_type: str) -> dict:
        return self.pricing_cfg["by_vehicle_type"][vehicle_type]

    def surcharge(self, region: str) -> float:
        return float(self.pricing_cfg["region_surcharge"].get(region, 0.0))

    def coupon_amount(self, coupon_id: str) -> float:
        return float(self.pricing_cfg["coupon"].get(coupon_id, 0.0))

    # ---------- 领域词典（交付物之一） ----------
    def dictionary(self) -> dict:
        return {
            "meta": self.raw["meta"],
            "class_count": len(self.classes),
            "classes": list(self.raw["classes"]),
            "relation_count": len(self.relations),
            "relations": list(self.raw["relations"]),
            "properties": self.raw["properties"],
            "lifecycle": self.lifecycle,
            "constraint_count": len(self.constraints),
            "constraints": self.constraints,
            "rule_count": len(self.raw["rules"]),
            "rules": [
                {"id": r["id"], "domain": r["domain"], "name": r["name"],
                 "when": r["when"], "judge": r["judge"], "actions": r["actions"]}
                for r in self.raw["rules"]
            ],
            "fee_strategies": self.fee_strategies,
            "pricing": self.pricing_cfg,
        }
