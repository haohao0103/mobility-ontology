"""参考实现 B：**属性图路线**（图数据 + 服务代码里的业务规则）。

形态模拟真实属性图项目：
- 图数据 = 节点（费用、运单…）+ 边（运单产生费用…），从 facts.json 构建；
- 业务规则 = <b>硬编码在服务代码里</b>（费率参数同时存在于本文件与 rules.json
  两处——这是真实项目里极常见的形态，也正是本基准要暴露的漂移风险）；
- **没有**声明、没有版本、没有生效期、没有规则 ID，因此答案里也没有审计要素。

关键差异（由评测方客观判定，不看解释文字）：
1. 改参数要同时改**两处**（代码 + 配置），漏改一处两侧结论打架；
2. 改完**必须重启**才生效；
3. 答案**不带**审计要素（version / rule_id / effective_from / evidence_ids / checksum）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("BENCH_DATA_DIR", HERE.parent.parent / "data"))
def _rules_path() -> Path:
    """配置文件位置：**每次实例化时**读取环境变量。

    不能写成模块级常量：评测器会为每个用例换工作区，模块只加载一次，
    模块级常量会把上一个工作区的路径固化下来。
    """
    return Path(os.environ.get("BENCH_IMPL_DIR", HERE)) / "rules.json"


class PropertyGraphImpl:
    name = "property_graph_ref: 属性图路线（图 + 服务代码规则）"

    def __init__(self):
        self.data_dir = Path(os.environ.get("BENCH_DATA_DIR", DATA))
        self.facts = json.loads((self.data_dir / "facts.json").read_text(encoding="utf-8"))
        self.rules_path = _rules_path()
        self.rules = json.loads(self.rules_path.read_text(encoding="utf-8"))
        # ---- 图：节点 + 边 ----
        self.nodes = {f"fee:{f['fee_id']}": {"type": "FeeItem", **f} for f in self.facts["fees"]}
        for wb in self.facts.get("waybills") or [self.facts["waybill"]]:
            self.nodes[f"waybill:{wb['waybill_id']}"] = {"type": "Waybill", **wb}
        self.edges = [(f"waybill:{f.get('waybill_id', self.facts['waybill']['waybill_id'])}",
                       f"fee:{f['fee_id']}", "produces_fee")
                      for f in self.facts["fees"]]

    # ---- 业务规则：硬编码在服务代码里（第一处副本）----
    WAITING_FREE_MINUTES = 30          # ← 副本 1
    WAITING_BLOCK_MINUTES = 5          # ← 副本 1
    WAITING_PER_BLOCK = 2.4            # ← 副本 1
    WAIT_MAX_AMOUNT = "actual"         # ← 副本 1（金额上限口径）

    def _fees_of(self, waybill_id: str) -> list[dict]:
        out = []
        for src, dst, pred in self.edges:
            if src == f"waybill:{waybill_id}" and pred == "produces_fee":
                out.append(self.nodes[dst])
        return out

    def _waiting_expected(self, waybill_id: str) -> float:
        """按服务代码 + 配置里的参数算。

        取费率的逻辑写在代码里（按车型查配置），参数值在配置里；
        但**代码里仍留着一份常量副本**（WAITING_*）——真实项目里这就是
        『同一语义被写两遍』的形态：改配置时若没同步改代码，两处会打架。
        """
        minutes = self.nodes[f"waybill:{waybill_id}"].get("waiting_minutes", 0)
        vehicle = self.nodes[f"waybill:{waybill_id}"].get("vehicle_type", "")
        pricing = self.rules["pricing"]["waiting"]
        rate = (pricing.get("by_vehicle_type") or {}).get(vehicle) or {}
        free = rate.get("free_minutes", pricing.get("free_minutes", self.WAITING_FREE_MINUTES))
        block = rate.get("block_minutes", pricing.get("block_minutes", self.WAITING_BLOCK_MINUTES))
        per_block = rate.get("per_block", pricing.get("per_block", self.WAITING_PER_BLOCK))
        blocks = 0 if minutes <= free else -(-(minutes - free) // block)
        return round(blocks * per_block, 2)

    def _waiting_fee(self, waybill_id: str) -> dict:
        wb = self._waybill(waybill_id)
        fee_id = f"F{8 if wb.get('vehicle_type') == '7.6米厢货' else 3:03d}"
        return next(f for f in self.facts["fees"] if f["fee_id"] == fee_id)

    def _waybill(self, waybill_id: str) -> dict:
        for wb in self.facts.get("waybills") or [self.facts["waybill"]]:
            if wb["waybill_id"] == waybill_id:
                return wb
        return self.facts["waybill"]

    # ---- 用例 ----
    def q1_diff(self) -> dict:
        wb = self.facts["waybill"]["waybill_id"]
        fees = self._fees_of(wb)
        quoted = round(sum(f["quoted_amount"] or 0 for f in fees), 2)
        actual = round(sum(f["actual_amount"] or 0 for f in fees), 2)
        waiting = next(f for f in fees if f["fee_type"] == "waiting")
        expected = self._waiting_expected(wb)
        over = round((waiting["actual_amount"] or 0) - expected, 2)
        return {
            "quoted_total": quoted, "actual_total": actual,
            "diff_total": round(actual - quoted, 2),
            "waiting_expected": expected, "waiting_over": over,
            "explain": (f"实收 {actual} 比报价 {quoted} 多 {round(actual - quoted, 2)}；"
                        f"等候费应计 {expected}（按服务代码参数）"),
            # 属性图路线：答案里没有版本/规则ID/生效期/证据checksum
            "audit": {},
        }

    def q2_waiting(self, waybill_id: str = "WB20261105001") -> dict:
        wb = waybill_id
        waiting = self._waiting_fee(wb)
        expected = self._waiting_expected(wb)
        over = round((waiting["actual_amount"] or 0) - expected, 2)
        return {
            "waiting_expected": expected, "waiting_over": over,
            "explain": f"按服务代码参数，等候费应计 {expected} 元，超收 {over} 元",
            "audit": {},
        }

    def q3_roadblock(self, amount: float = 40.0,
                     waybill_id: str = "WB20261105001") -> dict:
        wb = waybill_id
        waiting = self._waiting_fee(wb)
        # 金额上限：副本 2（配置里的 amount_cap）——与副本 1 的 WAIT_MAX_AMOUNT 同一语义。
        # 代码读的是**副本 1**（self.WAIT_MAX_AMOUNT），配置里改的是**副本 2**：
        # 这正是『同一语义两处写、只改一处』的漂移形态。
        cap_mode = self.WAIT_MAX_AMOUNT
        if cap_mode == "actual" and amount > (waiting["actual_amount"] or 0):
            return {"blocked": True, "reason_code": "AMOUNT_EXCEEDS_ACTUAL",
                    "explain": f"减免 {amount} 元超过实收 {waiting['actual_amount']} 元",
                    "audit": {}}
        return {"blocked": False, "reason_code": "",
                "explain": "可执行", "audit": {}}

    # ---- 变更：改配置那一份（副本 2），代码那份不改 → 漂移 ----
    def apply_change(self, card: dict) -> dict:
        change = card["change"]
        data = json.loads(self.rules_path.read_text(encoding="utf-8"))
        if change.startswith('pricing.waiting.by_vehicle_type["4.2米厢货"].free_minutes'):
            data = json.loads(self.rules_path.read_text(encoding="utf-8"))
            data["pricing"]["waiting"]["by_vehicle_type"]["4.2米厢货"]["free_minutes"] = 20
            self.rules_path.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
            return {"changed_files": [self.rules_path.name], "code_files": 0,
                    "restart_required": True}
        if change.startswith("pricing.waiting.free_minutes"):
            old = data["pricing"]["waiting"]["free_minutes"]
            new = int(change.split("->")[1].strip())
            data["pricing"]["waiting"]["free_minutes"] = new
            self.rules_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            # 真实形态：改的是配置，正在运行的进程读的还是旧值 —— 必须重启才生效
            return {"changed_files": [self.rules_path.name], "code_files": 0,
                    "restart_required": True}
        if change.startswith("actions.waive_charge.amount_cap"):
            data = json.loads(self.rules_path.read_text(encoding="utf-8"))
            data["amount_cap"] = change.split("->")[1].strip()
            self.rules_path.write_text(json.dumps(data, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
            return {"changed_files": [self.rules_path.name], "code_files": 0,
                    "restart_required": True}
        if change.startswith("pricing.waiting.by_vehicle_type.add"):
            data["pricing"]["waiting"].setdefault("by_vehicle_type", {})["7.6米厢货"] = {
                "free_minutes": 30, "block_minutes": 5, "per_block": 3.0}
            self.rules_path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            return {"changed_files": [self.rules_path.name], "code_files": 0,
                    "restart_required": True}
        return {"changed_files": [], "code_files": 0, "restart_required": True}


# runner 通过这个约定发现实现
IMPL = PropertyGraphImpl
