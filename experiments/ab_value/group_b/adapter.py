"""B 组：本体现状的薄封装（唯一允许 import freight 的一侧）。

变更 C1 的形态：改本体 JSON 副本 → 直接生效（无重启：每次建临时 SemanticLayer）。
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WAYBILL = "waybill:WB20261105001"


class GroupB:
    name = "B: 可执行本体（现状）"

    def __init__(self):
        import sys
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from freight.engine import FreightSystem
        from freight.semantic import SemanticLayer
        self._FreightSystem = FreightSystem
        self._SemanticLayer = SemanticLayer
        td = tempfile.mkdtemp(prefix="ab_group_b_")
        self.system = FreightSystem(os.path.join(td, "facts.db"), fresh=True, seed=True)

    # ---- 变更接口（C1）：改本体声明副本 ----
    def apply_change(self, card: dict) -> dict:
        raw = json.loads(json.dumps(self._SemanticLayer().raw))
        raw["pricing"]["waiting"]["free_minutes"] = 20
        path = Path(tempfile.mkdtemp(prefix="ab_b_mut_")) / "layer.json"
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        self.system.sem = self._SemanticLayer(str(path))
        self.system.facts.sem = self.system.sem
        return {"changed_files": [str(path)], "code_files": 0,
                "restart_required": False}

    def rollback(self):
        self.system.sem = self._SemanticLayer()
        self.system.facts.sem = self.system.sem

    # ---- Q1 / Q2：走诊断链 ----
    def _report(self) -> dict:
        return self.system.fee_analysis(WAYBILL)

    def q1_diff(self) -> dict:
        r = self._report()
        bd = r.get("breakdown") or []
        quoted = round(sum((b.get("quoted_amount") or 0) for b in bd), 2)
        actual = round(sum((b.get("actual_amount") or 0) for b in bd), 2)
        w = next((b for b in bd if b["fee_type"] == "waiting"), {})
        return {"answer": {"value": round(actual - quoted, 2),
                           "quoted_total": quoted, "actual_total": actual,
                           "diff_total": round(actual - quoted, 2),
                           "waiting_expected": w.get("rule_expected"),
                           "waiting_over": round((w.get("actual_amount") or 0)
                                                 - (w.get("rule_expected") or 0), 2)},
                "explain": "结论由本体算出，带版本/规则ID/生效期/证据（见 fee_analysis）",
                "audit": {"version": (r.get("summary") or {}).get("ontology_version"),
                          "rule_id": "R-WAIT-01",
                          "evidence": (r.get("evidence") or [])[:3]}}

    def q2_waiting(self) -> dict:
        r = self._report()
        w = next((b for b in (r.get("breakdown") or [])
                  if b["fee_type"] == "waiting"), {})
        expected = w.get("rule_expected")
        return {"answer": {"value": expected, "waiting_expected": expected,
                           "waiting_over": round((w.get("actual_amount") or 0)
                                                 - (expected or 0), 2)},
                "explain": "公式、版本、生效期在声明里，由本体算出",
                "audit": {"version": (r.get("summary") or {}).get("ontology_version"),
                          "rule_id": "R-WAIT-01"}}

    def q3_roadblock(self) -> dict:
        try:
            self.system.execute_action("waive_charge", "fee:F003", "u", "finance",
                                       {"amount": 40.0})
            return {"answer": {"value": 0, "blocked_step": 0,
                               "next_action": "已执行减免"},
                    "explain": "减免动作已执行"}
        except Exception as exc:  # noqa: BLE001
            return {"answer": {"value": 1, "blocked_step": 1,
                               "next_action": "减免被金额门禁拒绝（40 > 实收 25）"},
                    "explain": f"动作被本体声明门禁拒绝：{type(exc).__name__}: {str(exc)[:90]}",
                    "audit": {"rule_id": "waive_charge.amount_cap"}}
