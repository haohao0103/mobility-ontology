"""A 组：属性图路线的合格实现（不是稻草人）。

形态模拟真实属性图项目：
- 图数据 = SQLite 里的 objects/relations（同一份事实快照，只读）；
- 业务规则 = group_a/rules.yaml + 本文件的 Python 函数（服务端代码）；
- **禁止**引用 freight 包、**禁止**读本体声明文件（由 tests/test_ab_isolation.py 强制）。

变更 C1 的形态：改 rules.yaml → 重启（重新 init）→ 生效。
本文件刻意写成『规则的第二处副本』来暴露漂移风险：waive 金额上限校验
同时出现在 rules.yaml（amount_cap）和 _amount_ok() 里——变更卡若只改
其一，两侧结论即打架（这正是 A/B 计划要测的变更成本）。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FACTS_DB = ROOT / "runs" / "freight_facts.db"
WAYBILL = "waybill:WB20261105001"


class GroupA:
    name = "A: 属性图 + 服务代码规则"

    def __init__(self):
        self.conn = sqlite3.connect(f"file:{FACTS_DB}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row
        with open(HERE / "rules.yaml", encoding="utf-8") as fh:
            self.rules = yaml.safe_load(fh)
        # 服务代码里的第二处规则副本（真实项目常见形态）
        self._hardcoded_amount_cap = "actual"

    # ---- 变更接口（C1）：改 YAML 参数 ----
    def apply_change(self, card: dict) -> dict:
        """按变更卡改 rules.yaml。属性图路线改完必须重启（重新 init）才生效。"""
        if card["change"].startswith("pricing.waiting.free_minutes"):
            old = self.rules["pricing"]["waiting"]["free_minutes"]
            new = int(card["change"].split("->")[1].strip())
            self.rules["pricing"]["waiting"]["free_minutes"] = new
            path = HERE / "rules.yaml"
            raw = path.read_text(encoding="utf-8")
            path.write_text(raw.replace(f"free_minutes: {old}", f"free_minutes: {new}"),
                            encoding="utf-8")
            return {"changed_files": [str(path.relative_to(ROOT))],
                    "code_files": 0, "restart_required": True}
        raise ValueError(f"未知变更卡: {card}")

    def rollback(self):
        """把 rules.yaml 改回 30。"""
        path = HERE / "rules.yaml"
        raw = path.read_text(encoding="utf-8")
        path.write_text(re.sub(r"free_minutes: \d+", "free_minutes: 30", raw),
                        encoding="utf-8")
        self.rules["pricing"]["waiting"]["free_minutes"] = 30

    # ---- 事实读取（图查询） ----
    def _waybill(self) -> dict:
        row = self.conn.execute("SELECT props FROM objects WHERE object_id=?",
                                (WAYBILL,)).fetchone()
        return json.loads(row["props"])

    def _fees(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT r.object_id, o.props FROM relations r JOIN objects o "
            "ON o.object_id=r.object_id WHERE r.subject_id=? AND r.predicate="
            "'waybill_produces_fee' AND r.valid_to IS NULL", (WAYBILL,)).fetchall()
        return [{"id": r["object_id"], **json.loads(r["props"])} for r in rows]

    # ---- Q1 ----
    def q1_diff(self) -> dict:
        fees = self._fees()
        quoted = round(sum(f.get("quoted_amount") or 0 for f in fees), 2)
        actual = round(sum(f.get("actual_amount") or 0 for f in fees), 2)
        w = next(f for f in fees if f["fee_type"] == "waiting")
        return {"answer": {"value": round(actual - quoted, 2),
                           "quoted_total": quoted, "actual_total": actual,
                           "diff_total": round(actual - quoted, 2),
                           "waiting_expected": self._waiting_expected(w),
                           "waiting_over": round((w.get("actual_amount") or 0)
                                                 - self._waiting_expected(w), 2)},
                "explain": "图上拿 quoted/actual 相减；等候应计按 YAML 参数计算"}

    # ---- Q2 ----
    def q2_waiting(self) -> dict:
        w = next(f for f in self._fees() if f["fee_type"] == "waiting")
        expected = self._waiting_expected(w)
        return {"answer": {"value": expected, "waiting_expected": expected,
                           "waiting_over": round((w.get("actual_amount") or 0) - expected, 2)},
                "explain": "公式硬编码在服务代码 + rules.yaml 参数"}

    def _waiting_expected(self, _fee: dict) -> float:
        # 公式本体在代码里；参数在 YAML 里
        minutes = float(self._waybill().get("waiting_minutes") or 0)
        p = self.rules["pricing"]["waiting"]
        blocks = 0 if minutes <= p["free_minutes"] else -(-(minutes - p["free_minutes"])
                                                          // p["block_minutes"])
        return round(blocks * p["per_block"], 2)

    # ---- Q3：减免 40 元挡住哪一步 ----
    def q3_roadblock(self) -> dict:
        fees = self._fees()
        w = next(f for f in fees if f["fee_type"] == "waiting")
        blocked = not self._amount_ok(w, 40.0)
        return {"answer": {"value": 1 if blocked else 0,
                           "blocked_step": 1 if blocked else 0,
                           "next_action": "修正减免金额后重试" if blocked
                           else "继续 hold_settlement"},
                "explain": "金额上限校验在服务代码 _amount_ok()（第二处规则副本）"}

    def _amount_ok(self, fee: dict, amount: float) -> bool:
        # 硬编码规则副本：与 rules.yaml 的 amount_cap 语义相同但独立维护
        cap = self._hardcoded_amount_cap
        limit = fee.get("actual_amount") if cap == "actual" else None
        return limit is not None and amount <= limit
