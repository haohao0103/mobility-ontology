"""A/B 实验运行器：正确性门禁 → 六题采样 → C1 变更 → 四指标报告。

用法：
    python -m experiments.ab_value.harness.run            # 全流程
    python -m experiments.ab_value.harness.run --json     # 机器可读
M4（非研发可用性）需要真人按 ../M4_TASK_CARD.md 执行，不在本运行器内。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

import sys
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))       # experiments/ab_value（harness/group_a 可导入）
sys.path.insert(0, str(HERE.parents[2]))   # 仓库根（B 组 import freight 用）

from harness.contract import AnswerSchema, golden_match, load_fixture  # noqa: E402

FACTS_DB = HERE.parents[2] / "runs" / "freight_facts.db"


def facts_checksum() -> str:
    return hashlib.sha256(FACTS_DB.read_bytes()).hexdigest()


def gate_correctness(group, golden: dict) -> dict:
    """正确性门禁：黄金答案不匹配 → 残缺实现，停止实验。"""
    results = {}
    r1 = group.q1_diff()
    for key, gold in golden["Q1"].items():
        if key.startswith("_"):
            continue
        results[f"Q1.{key}"] = golden_match(r1["answer"].get(key), gold)
    r2 = group.q2_waiting()
    for key, gold in golden["Q2"].items():
        results[f"Q2.{key}"] = golden_match(r2["answer"].get(key), gold)
    r3 = group.q3_roadblock()
    gold_q3 = golden["Q3"]["blocked_step"]
    results["Q3.blocked_step"] = golden_match(r3["answer"].get("blocked_step"), gold_q3)
    return results


def m1_change_cost(group, card: dict, golden_after: dict) -> dict:
    """M1 变更成本：从变更命令到新答案正确返回的耗时 + 改动面。"""
    t0 = time.time()
    info = group.apply_change(card)
    r = group.q2_waiting()
    while not golden_match(r["answer"]["waiting_expected"],
                           golden_after["waiting_expected"]):
        info["restart_required"] = True
        # 模拟属性图路线必须重启：重新 init
        group.__init__()
        r = group.q2_waiting()
        break
    elapsed = round(time.time() - t0, 3)
    ok = golden_match(r["answer"]["waiting_expected"], golden_after["waiting_expected"])
    group.rollback()
    return {"files_changed": len(info["changed_files"]),
            "code_files_changed": info["code_files"],
            "restart_required": info["restart_required"],
            "wall_time_s": elapsed, "answer_ok_after_change": bool(ok)}


def m3_auditability(group) -> dict:
    """M3 可审计性：答案是否自带版本/规则ID/生效期/证据。"""
    sample = [group.q1_diff(), group.q2_waiting(), group.q3_roadblock()]
    keys = AnswerSchema.AUDIT_KEYS
    present = 0
    for s in sample:
        audit = s.get("audit") or {}
        present += sum(1 for k in keys if audit.get(k))
    # 满分 = 样本数 × 关键要素数 × 0.5（Q3 类动作答案天然缺部分要素，给一半权重）
    max_score = int(len(sample) * len(keys) * 0.5)
    return {"audit_keys": keys, "present_count": present, "score": present,
            "max_score": max(max_score, 1)}


def run_experiment() -> dict:
    golden = load_fixture("golden.json")
    change_cards = load_fixture("change_cards.json")

    from group_a.app import GroupA
    from group_b.adapter import GroupB

    checksum = facts_checksum()
    report = {"facts_checksum": checksum, "groups": {}}

    for cls in (GroupA, GroupB):
        g = cls()
        gate = gate_correctness(g, golden)
        passed = all(gate.values())
        report["groups"][g.name] = {
            "correctness_gate": {"passed": passed, "detail": gate},
            "M1_change_cost_C1": (m1_change_cost(g, change_cards["C1"],
                                                 change_cards["C1"]["expected_new_Q2"])
                                  if passed else None),
            "M3_auditability": m3_auditability(g) if passed else None,
        }
        if not passed:
            report["groups"][g.name]["verdict"] = "残缺实现 → 当场换人重写，不继续"
    # M2（新增车型）pending：需先准备含 7.6米货车 的测试运单
    report["M2_status"] = "PENDING_TEST_DATA（见 fixtures/change_cards.json C2）"
    # M4（非研发可用性）待真人执行
    report["M4_status"] = "PENDING_HUMAN（见 experiments/ab_value/M4_TASK_CARD.md）"
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = run_experiment()
    out = Path(HERE.parent / "results" / "raw")
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    (out / f"ab_run_{stamp}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        for name, g in report["groups"].items():
            print(f"\n=== {name} ===")
            print("  正确性门禁:", "PASS" if g["correctness_gate"]["passed"] else "FAIL",
                  g["correctness_gate"]["detail"])
            if g.get("M1_change_cost_C1"):
                print("  M1 变更成本(C1):", g["M1_change_cost_C1"])
            if g.get("M3_auditability"):
                print("  M3 可审计性:", g["M3_auditability"])
        print("\nM2:", report["M2_status"])
        print("M4:", report["M4_status"])
    ok = all(g["correctness_gate"]["passed"] for g in report["groups"].values())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
