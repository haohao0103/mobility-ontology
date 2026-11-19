"""P4-3 proposal：本体变更分支评审流程（对标 Palantir Global Branching / ontology proposal）。

流程（全部落盘、可审计）：
    create  -> proposals/<id>/branch.json（从 ontology_extension.json 拷贝的分支副本）+ proposal.json 元数据
    review  -> 跑交叉引用校验 + 语义门禁内核，评审结论写 proposals/<id>/review.json
    merge   -> 仅 review=PASS 的分支可合回 ontology_extension.json，
               并生成 releases/<version>.json 快照（含 checksum + change list）

承重验收：未过评审（或校验 FAIL）的 merge 一律拒绝；快照 diff 自动生成。

用法：
    python tools/proposal.py create --title "加 remote_area 费用属性"
    python tools/proposal.py review <proposal_id>
    python tools/proposal.py merge <proposal_id>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ENH = os.path.join(ROOT, "enhancements", "ontology_extension.json")
PROPOSALS = os.path.join(ROOT, "enhancements", "proposals")
RELEASES = os.path.join(ROOT, "enhancements", "releases")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _checksum(data: dict) -> str:
    return hashlib.sha256(
        json.dumps(data, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:16]


def _load_main() -> dict:
    with open(ENH, encoding="utf-8") as f:
        return json.load(f)


def _validate_branch(branch: dict) -> list[dict]:
    """评审内核：分支内容 + 基础本体交叉引用校验（与门禁同一校验器）。"""
    from enhancements.registry import ExtensionRegistry
    from freight.semantic import SemanticLayer
    sem = SemanticLayer()
    # 用临时文件加载分支（ExtensionRegistry 当前从文件读）
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                     encoding="utf-8") as tf:
        json.dump(branch, tf, ensure_ascii=False)
        tmp = tf.name
    try:
        ext = ExtensionRegistry(path=tmp, base_classes=sem.classes)
        issues = ext.validate()
    finally:
        os.unlink(tmp)
    return issues


def cmd_create(args) -> int:
    os.makedirs(PROPOSALS, exist_ok=True)
    pid = f"P-{int(time.time())}"
    pdir = os.path.join(PROPOSALS, pid)
    os.makedirs(pdir, exist_ok=True)
    main_json = _load_main()
    with open(os.path.join(pdir, "branch.json"), "w", encoding="utf-8") as f:
        json.dump(main_json, f, ensure_ascii=False, indent=1)
    meta = {
        "id": pid,
        "title": args.title,
        "created_at": _now(),
        "base_checksum": _checksum(main_json),
        "status": "DRAFT",
        "review": None,
    }
    with open(os.path.join(pdir, "proposal.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print(f"分支已创建: {pdir}")
    print(f"  proposal: {pid}  title: {args.title}")
    print(f"  base checksum: {meta['base_checksum']}")
    print("  修改 enhancements/proposals/%s/branch.json 后执行 review" % pid)
    return 0


def cmd_review(args) -> int:
    pdir = os.path.join(PROPOSALS, args.proposal_id)
    branch_path = os.path.join(pdir, "branch.json")
    if not os.path.exists(branch_path):
        print(f"FAIL: proposal 不存在: {pdir}")
        return 1
    with open(branch_path, encoding="utf-8") as f:
        branch = json.load(f)
    issues = _validate_branch(branch)
    main_json = _load_main()
    changed = _checksum(branch) != _checksum(main_json)
    verdict = "PASS" if not issues else "FAIL"
    review = {
        "proposal": args.proposal_id,
        "reviewed_at": _now(),
        "verdict": verdict,
        "changed_vs_main": changed,
        "issues": issues,
        "checker": "ExtensionRegistry.validate (semantic-gate kernel)",
    }
    with open(os.path.join(pdir, "review.json"), "w", encoding="utf-8") as f:
        json.dump(review, f, ensure_ascii=False, indent=1)
    # 更新 proposal 元数据状态
    meta_path = os.path.join(pdir, "proposal.json")
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    meta["status"] = "REVIEWED"
    meta["review"] = verdict
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print(f"评审结论: {verdict}  (相对 main 有变更: {changed})")
    for i in issues:
        print(f"  - [{i['code']}] {i['message']}")
    print(f"评审记录: {os.path.join(pdir, 'review.json')}")
    return 0 if verdict == "PASS" else 2


def _diff_change_list(before: dict, after: dict) -> list[dict]:
    """粗粒度 change list：顶层键级增删改。"""
    changes = []
    for key in set(before) | set(after):
        b, a = before.get(key), after.get(key)
        if key not in before:
            changes.append({"path": key, "change": "ADDED"})
        elif key not in after:
            changes.append({"path": key, "change": "REMOVED"})
        elif b != a:
            if isinstance(b, list) and isinstance(a, list):
                bi = {json.dumps(x, sort_keys=True, ensure_ascii=False): x for x in b}
                ai = {json.dumps(x, sort_keys=True, ensure_ascii=False): x for x in a}
                for item in set(ai) - set(bi):
                    changes.append({"path": f"{key}[]", "change": "ADDED",
                                    "value": ai[item]})
                for item in set(bi) - set(ai):
                    changes.append({"path": f"{key}[]", "change": "REMOVED",
                                    "value": bi[item]})
            else:
                changes.append({"path": key, "change": "MODIFIED"})
    return changes


def cmd_merge(args) -> int:
    pdir = os.path.join(PROPOSALS, args.proposal_id)
    review_path = os.path.join(pdir, "review.json")
    branch_path = os.path.join(pdir, "branch.json")
    if not os.path.exists(branch_path):
        print(f"FAIL: proposal 不存在: {pdir}")
        return 1
    if not os.path.exists(review_path):
        print("REJECTED: 分支未评审（no review.json）。先执行 proposal review。")
        return 1
    with open(review_path, encoding="utf-8") as f:
        review = json.load(f)
    if review.get("verdict") != "PASS":
        print(f"REJECTED: 评审结论 {review.get('verdict')}，未过门禁的分支禁止合入 main。")
        return 1
    # 合并前再校验一次（评审后分支可能又被改过——双保险）
    with open(branch_path, encoding="utf-8") as f:
        branch = json.load(f)
    issues = _validate_branch(branch)
    if issues:
        print("REJECTED: 评审后分支又被修改且引入新问题：")
        for i in issues:
            print(f"  - [{i['code']}] {i['message']}")
        return 1

    main_before = _load_main()
    # 合并时把 release 版本写回本体 meta（版本演进可追溯）
    old_v = str(main_before.get("meta", {}).get("version", "0"))
    try:
        parts = old_v.split(".")
        parts[1] = str(int(parts[1]) + 1)
        version = ".".join(parts)
    except (ValueError, IndexError):
        version = old_v + "+1"
    branch.setdefault("meta", {})["version"] = version
    with open(ENH, "w", encoding="utf-8") as f:
        json.dump(branch, f, ensure_ascii=False, indent=1)

    os.makedirs(RELEASES, exist_ok=True)
    release = {
        "version": version,
        "merged_at": _now(),
        "proposal": args.proposal_id,
        "checksum": _checksum(branch),
        "base_checksum": review.get("checksum") or _checksum(main_before),
        "change_list": _diff_change_list(main_before, branch),
    }
    with open(os.path.join(RELEASES, f"{version}.json"), "w", encoding="utf-8") as f:
        json.dump(release, f, ensure_ascii=False, indent=1)
    # 更新 proposal 状态
    meta_path = os.path.join(pdir, "proposal.json")
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    meta["status"] = "MERGED"
    meta["merged_version"] = version
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=1)
    print(f"MERGED: proposal {args.proposal_id} -> ontology_extension.json")
    print(f"  release 快照: {os.path.join(RELEASES, version + '.json')}")
    print(f"  change list: {len(release['change_list'])} 项")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="本体变更 proposal 分支治理")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_create = sub.add_parser("create")
    p_create.add_argument("--title", required=True)
    p_review = sub.add_parser("review")
    p_review.add_argument("proposal_id")
    p_merge = sub.add_parser("merge")
    p_merge.add_argument("proposal_id")

    args = parser.parse_args()
    if args.cmd == "create":
        return cmd_create(args)
    if args.cmd == "review":
        return cmd_review(args)
    if args.cmd == "merge":
        return cmd_merge(args)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
