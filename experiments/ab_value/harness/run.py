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
    """M1 变更成本：从变更命令到新答案正确返回的耗时 + 改动面。

    真实两阶段：变更后立即求值——若答案未变（正在运行的进程还持旧配置），
    属性图路线需要重启（重新 init）才生效；本体路线改声明即生效。
    """
    t0 = time.time()
    info = group.apply_change(card)
    r = group.q2_waiting()
    restart = False
    if not golden_match(r["answer"]["waiting_expected"], golden_after["waiting_expected"]):
        restart = True
        group.__init__()  # 重启：重新加载配置/声明
        r = group.q2_waiting()
    elapsed = round(time.time() - t0, 3)
    ok = golden_match(r["answer"]["waiting_expected"], golden_after["waiting_expected"])
    group.rollback()
    return {"files_changed": len(info["changed_files"]),
            "code_files_changed": info["code_files"],
            "restart_required": bool(restart or info.get("restart_required")),
            "wall_time_s": elapsed, "answer_ok_after_change": bool(ok)}


def m3_auditability(group) -> dict:
    """M3 可审计性：答案是否自带版本/规则ID/生效期/证据。"""
    sample = [group.q1_diff(), group.q2_waiting(), group.q3_roadblock()]
    keys = AnswerSchema.AUDIT_KEYS
    present = 0
    for s in sample:
        audit = s.get("audit") or {}
        present += sum(1 for k in keys if audit.get(k))
    # 满分 = 样本数 × 关键要素数（缺失项如实留空，不加权美化）
    return {"audit_keys": keys, "present_count": present, "score": present,
            "max_score": len(sample) * len(keys)}


def m2_new_vehicle(group, card: dict) -> dict:
    """M2 新增车型：C2 变更 + 测试运单求应计，采集改动面与正确性（含真实重启检测）。"""
    props = card["test_waybill_props"]
    expected = card["expected_new_waiting"]
    t0 = time.time()
    info = group.apply_change(card)
    value = group.waiting_expected_for(props["vehicle_type"],
                                       float(props["waiting_minutes"]))
    restart = False
    if not golden_match(value, expected):
        restart = True
        group.__init__()  # 属性图路线需要重启才加载新配置
        value = group.waiting_expected_for(props["vehicle_type"],
                                           float(props["waiting_minutes"]))
    elapsed = round(time.time() - t0, 3)
    ok = golden_match(value, expected)
    group.rollback()
    return {"files_changed": len(info["changed_files"]),
            "code_files_changed": info["code_files"],
            "restart_required": bool(restart or info.get("restart_required")),
            "wall_time_s": elapsed, "answer_ok_after_change": bool(ok),
            "new_waiting_expected": value}


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
            "M2_new_vehicle_C2": (m2_new_vehicle(g, change_cards["C2"])
                                  if passed else None),
            "M3_auditability": m3_auditability(g) if passed else None,
        }
        if not passed:
            report["groups"][g.name]["verdict"] = "残缺实现 → 当场换人重写，不继续"
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
            if g.get("M2_new_vehicle_C2"):
                print("  M2 新增车型(C2):", g["M2_new_vehicle_C2"])
            if g.get("M3_auditability"):
                print("  M3 可审计性:", g["M3_auditability"])
        print("\nM4:", report["M4_status"])
    ok = all(g["correctness_gate"]["passed"] for g in report["groups"].values())
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
