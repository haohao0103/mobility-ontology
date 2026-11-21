"""A/B 价值实验：候选接口契约与答案 schema（两组必须实现同一契约）。

A 组（属性图路线）与 B 组（本体现状）对上层只暴露同一组函数：
    init() / q1_diff() / q2_waiting() / q3_roadblock() / apply_change() / rollback()

公平性铁律（与 plan/ab-value-experiment-plan.md §3 一致）：
1. 两组读同一份 FactStore 快照（checksum 一致才开跑）；
2. A 组禁止 import freight.*、禁止读 semantic_layer.json；
3. 正确性门禁先行：黄金答案不过 → 残缺实现 → 当场换人重写，不允许继续；
4. 计时口径：从「变更命令」到「新答案正确返回」。
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


class AnswerSchema:
    """统一答案结构：数值答案 + 审计要素（M3 可审计性评分的输入）。"""

    REQUIRED_NUMERIC_KEYS = ("value", "tolerance")
    AUDIT_KEYS = ("version", "rule_id", "effective_from", "evidence_ids", "checksum")

    @staticmethod
    def check(answer: dict) -> tuple[bool, str]:
        if not isinstance(answer, dict) or "value" not in answer:
            return False, "answer 缺少 value"
        if not isinstance(answer.get("explain"), str) or not answer["explain"]:
            return False, "answer 缺少 explain"
        return True, "ok"


def load_fixture(name: str):
    with open(FIXTURES / name, encoding="utf-8") as fh:
        return json.load(fh)


def golden_match(answer_value: float, golden: dict) -> bool:
    return abs(float(answer_value) - float(golden["value"])) <= golden["tolerance"]
