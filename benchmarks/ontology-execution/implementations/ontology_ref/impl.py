"""参考实现 A：**本体路线**（声明驱动的可执行本体）。

这个文件刻意**不依赖任何私有包**——只读标准库 + 中立数据文件。
它要展示的是一件事：**业务规则只存在于声明里，代码只负责解释声明**。

三段式：
1. 声明（data/ontology.json）：定价参数、规则公式、动作前置、状态机、来源归属；
2. 解释器（本文件）：一个 40 行的公式求值器 + 状态机判定 + 审计要素组装；
3. 事实（data/facts.json）：业务数据。

**改动规则时**：改声明 → 重新 init 即生效，不需要改本文件任何一行，也不需要重启进程。
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = Path(os.environ.get("BENCH_DATA_DIR", HERE.parent.parent / "data"))


# ---------------------------------------------------------------- 极小公式求值器
# 声明里的公式是类 Python 表达式，例如：
#   ceil(max(0, waiting_minutes - free_minutes) / block_minutes) * per_block
#
# 用 ast 解析并**按白名单**求值：只允许常量、变量、算术运算、比较，
# 以及白名单函数 ceil/floor/round/max/min/abs。这样声明里的公式
# **不能执行任意代码**（这是把公式放进声明的前提）。
FUNCS = {"ceil": math.ceil, "floor": math.floor, "round": round,
         "max": max, "min": min, "abs": abs}


def eval_formula(expr: str, variables: dict) -> float:
    """按声明求值。声明改了就变，代码不动。"""
    tree = ast.parse(expr, mode="eval")
    return float(_eval_node(tree.body, variables))


def _eval_node(node: ast.AST, variables: dict) -> float:
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)):
            raise ValueError(f"公式里只允许数值常量: {node.value!r}")
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id not in variables:
            raise ValueError(f"未知变量: {node.id}")
        return float(variables[node.id])
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, variables)
        right = _eval_node(node.right, variables)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Mod):
            return left % right
        raise ValueError(f"不支持的运算符: {type(node.op).__name__}")
    if isinstance(node, ast.UnaryOp):
        value = _eval_node(node.operand, variables)
        return -value if isinstance(node.op, ast.USub) else value
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS:
            raise ValueError("只允许调用白名单函数: ceil/floor/round/max/min/abs")
        if node.keywords:
            raise ValueError("函数参数不支持关键字形式")
        args = [_eval_node(a, variables) for a in node.args]
        return float(FUNCS[node.func.id](*args))
    raise ValueError(f"公式里不允许的语法: {type(node).__name__}")


# ---------------------------------------------------------------- 实现本体路线
class OntologyImpl:
    name = "ontology_ref: 本体路线（声明驱动）"

    def __init__(self):
        self.data_dir = Path(os.environ.get("BENCH_DATA_DIR", DATA))
        self.ontology = json.loads((self.data_dir / "ontology.json").read_text(encoding="utf-8"))
        self.facts = json.loads((self.data_dir / "facts.json").read_text(encoding="utf-8"))
        self.checksum = hashlib.md5(
            (self.data_dir / "ontology.json").read_bytes()).hexdigest()[:12]

    # ---- 声明读取 ----
    def _rule(self, rule_id: str) -> dict:
        return self.ontology["rules"][rule_id]

    def _vehicle_rate(self, fee_type: str, vehicle_type: str) -> dict:
        """按车型取定价参数——车费率覆盖在声明里，不在代码里。"""
        pricing = self.ontology["pricing"][fee_type]
        rate = (pricing.get("by_vehicle_type") or {}).get(vehicle_type) or {}
        merged = dict(pricing)
        merged.update(rate)
        return merged

    # ---- 审计要素（可审计性的来源）----
    def _audit(self, rule_id: str, evidence_ids: list[str]) -> dict:
        rule = self._rule(rule_id)
        return {
            "version": self.ontology.get("ontology_version"),
            "rule_id": rule_id,
            "effective_from": rule.get("effective_from"),
            "evidence_ids": evidence_ids,
            "checksum": self.checksum,
        }

    # ---- 用例 ----
    def q1_diff(self, waybill_id: str = "WB20261105001") -> dict:
        fees = [f for f in self.facts["fees"]
                if f.get("waybill_id", "WB20261105001") == waybill_id]
        quoted = round(sum(f["quoted_amount"] or 0 for f in fees), 2)
        actual = round(sum(f["actual_amount"] or 0 for f in fees), 2)
        waiting = next(f for f in fees if f["fee_type"] == "waiting")
        expected = self._expected("waiting", waiting, waybill_id)
        over = round((waiting["actual_amount"] or 0) - expected, 2)
        ev = [e["evidence_id"] for e in self.facts.get("evidence", [])]
        return {
            "quoted_total": quoted, "actual_total": actual,
            "diff_total": round(actual - quoted, 2),
            "waiting_expected": expected, "waiting_over": over,
            "explain": (f"实收 {actual} 比报价 {quoted} 多 {round(actual - quoted, 2)}；"
                        f"其中等候费规则应计 {expected}、实收 {waiting['actual_amount']}，"
                        f"超收 {over}"),
            "audit": self._audit("R-WAIT-01", ev),
        }

    def q2_waiting(self, waybill_id: str = "WB20261105001") -> dict:
        waiting = self._waiting_fee(waybill_id)
        expected = self._expected("waiting", waiting, waybill_id)
        over = round((waiting["actual_amount"] or 0) - expected, 2)
        return {
            "waiting_expected": expected, "waiting_over": over,
            "explain": (f"按声明公式与生效参数，等候费应计 {expected} 元，"
                        f"实收 {waiting['actual_amount']} 元，超收 {over} 元"),
            "audit": self._audit("R-WAIT-01",
                                 [e["evidence_id"] for e in self.facts.get("evidence", [])]),
        }

    def q3_roadblock(self, amount: float = 40.0,
                     waybill_id: str = "WB20261105001") -> dict:
        """减免预检：状态前置 + 角色 + 金额上限，全部取自声明。"""
        fee = self._waiting_fee(waybill_id)
        action = self.ontology["actions"]["waive_charge"]
        machine = self.ontology["state_machines"]["FeeItem"]
        rule = action.get("rule_text", "")
        if fee["status"] not in action["from_status"]:
            return {"blocked": True, "reason_code": "INVALID_STATE",
                    "explain": f"当前状态 {fee['status']} 不允许减免",
                    "audit": self._audit("R-SETTLE-01" if "R-SETTLE-01" in self.ontology.get("rules", {})
                                         else "R-WAIT-01", [])}
        cap = action.get("amount_cap")
        if cap == "actual" and amount > (fee["actual_amount"] or 0):
            return {"blocked": True, "reason_code": "AMOUNT_EXCEEDS_ACTUAL",
                    "explain": (f"减免 {amount} 元超过实收 {fee['actual_amount']} 元"
                                f"（{rule}）"),
                    "audit": self._audit("R-WAIT-01", [])}
        return {"blocked": False, "reason_code": "",
                "explain": f"减免 {amount} 元可执行：{fee['status']} -> {action['to_status']}",
                "audit": self._audit("R-WAIT-01", [])}

    # ---- 变更 ----
    def apply_change(self, card: dict) -> dict:
        """改声明即生效：不改代码、不需要重启。"""
        path = self.data_dir / "ontology.json"
        raw = path.read_text(encoding="utf-8")
        change = card["change"]
        if change.startswith('pricing.waiting.by_vehicle_type["4.2米厢货"].free_minutes'):
            data = json.loads(path.read_text(encoding="utf-8"))
            data["pricing"]["waiting"]["by_vehicle_type"]["4.2米厢货"]["free_minutes"] = 20
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            self.ontology = data
            self.checksum = hashlib.md5(path.read_bytes()).hexdigest()[:12]
            return {"changed_files": [str(path.name)], "code_files": 0,
                    "restart_required": False}
        if change.startswith("pricing.waiting.free_minutes"):
            old = self.ontology["pricing"]["waiting"]["free_minutes"]
            new = int(change.split("->")[1].strip())
            raw = raw.replace(f'"free_minutes": {old},\n    "block_minutes"',
                              f'"free_minutes": {new},\n    "block_minutes"', 1)
            raw = raw.replace(f'"free_minutes": {old}', f'"free_minutes": {new}', 1)
            path.write_text(raw, encoding="utf-8")
            self.checksum = hashlib.md5(path.read_bytes()).hexdigest()[:12]
            return {"changed_files": [str(path.name)], "code_files": 0,
                    "restart_required": False}
        if change.startswith("actions.waive_charge.amount_cap"):
            old = self.ontology["actions"]["waive_charge"].get("amount_cap", "actual")
            new = change.split("->")[1].strip()
            data = json.loads(path.read_text(encoding="utf-8"))
            data["actions"]["waive_charge"]["amount_cap"] = new
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            self.ontology = data
            self.checksum = hashlib.md5(path.read_bytes()).hexdigest()[:12]
            return {"changed_files": [str(path.name)], "code_files": 0,
                    "restart_required": False}
        if change.startswith("pricing.waiting.by_vehicle_type.add"):
            vehicle = "7.6米厢货"
            rate = {"free_minutes": 30, "block_minutes": 5, "per_block": 3.0}
            data = json.loads(raw)
            data["pricing"]["waiting"].setdefault("by_vehicle_type", {})[vehicle] = rate
            path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
            self.checksum = hashlib.md5(path.read_bytes()).hexdigest()[:12]
            return {"changed_files": [str(path.name)], "code_files": 0,
                    "restart_required": False}
        return {"changed_files": [], "code_files": 0, "restart_required": False}

    # ---- 内部：按声明算应计 ----
    def _waiting_fee(self, waybill_id: str) -> dict:
        wb = self._waybill(waybill_id)
        fee_id = f"F{8 if wb['vehicle_type'] == '7.6米厢货' else 3:03d}"
        return next(f for f in self.facts["fees"] if f["fee_id"] == fee_id)

    def _waybill(self, waybill_id: str) -> dict:
        for wb in self.facts.get("waybills") or [self.facts["waybill"]]:
            if wb["waybill_id"] == waybill_id:
                return wb
        return self.facts["waybill"]

    def _expected(self, fee_type: str, fee: dict, waybill_id: str = "WB20261105001") -> float:
        rate = self._vehicle_rate(fee_type, self._waybill(waybill_id).get("vehicle_type", ""))
        rule = self._rule("R-WAIT-01")
        variables = {
            "waiting_minutes": self._waybill(waybill_id).get("waiting_minutes", 0),
            "free_minutes": rate.get("free_minutes", rule.get("free_minutes", 0)),
            "block_minutes": rate.get("block_minutes", rule.get("block_minutes", 5)),
            "per_block": rate.get("per_block", rule.get("per_block", 0)),
        }
        return round(eval_formula(rule["formula"], variables), 2)


# runner 通过这个约定发现实现
IMPL = OntologyImpl
