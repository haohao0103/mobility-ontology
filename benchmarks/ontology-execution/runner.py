#!/usr/bin/env python3
"""可执行本体基准 · 评测器（runner）。

用法：
    python benchmarks/ontology-execution/runner.py                 # 跑内置参考实现
    python benchmarks/ontology-execution/runner.py --impl path/to/impl.py
    python benchmarks/ontology-execution/runner.py --json           # 机器可读
    python benchmarks/ontology-execution/runner.py --check-baseline # 与基线比对（CI 用）

判定口径（**只看客观值，不看解释文字**）：
- 数值：|实现值 - 期望值| <= 容差；
- 布尔：相等；
- 字符串码值：相等；
- 用例的期望全部命中 → 该用例 PASS。

每个实现拿到**独立工作区**（data/ 的副本），变更只改自己的工作区，
因此多次运行、多个实现互不污染。

公平性铁律（见 PROTOCOL.md §2）：
1. 所有实现读**同一份数据副本**；
2. 实现不得读评测方的期望（manifest/golden）；
3. 判定只看客观值，实现的解释文字不参与判定。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent

BUILTIN = {
    "ontology_ref": HERE / "implementations" / "ontology_ref" / "impl.py",
    "property_graph_ref": HERE / "implementations" / "property_graph_ref" / "impl.py",
}
# RAG 组需要大模型端点（慢 + 要 key），默认不跑；用 --with-rag 显式加入。
BUILTIN_OPTIONAL = {
    "rag_ref": HERE / "implementations" / "rag_ref" / "impl.py",
}

# 静态检测：同一语义被**硬编码**了几遍（副本计数）。
# 只认「字面量赋值」——从声明/配置里**读取**不算副本。
DUPLICATE_PATTERNS = [
    ("rule_params", re.compile(
        r"^\s*(?:[A-Z_]*WAITING_[A-Z_]+|free_minutes|block_minutes|per_block)\s*=\s*[\d.]+",
        re.MULTILINE)),
    ("amount_cap", re.compile(
        r"^\s*(?:[A-Z_]*AMOUNT_CAP[A-Z_]*|amount_cap)\s*=\s*[\"\']",
        re.MULTILINE)),
]


def load_manifest() -> dict:
    return json.loads((HERE / "manifest.json").read_text(encoding="utf-8"))


def load_impl(path: Path):
    spec = importlib.util.spec_from_file_location(f"bench_impl_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.IMPL


def workspace_for(impl_name: str, impl_path: Path | None = None) -> Path:
    """给每个用例一个独立工作区：data/ 与实现目录各一份。

    实现目录也要复制：真实项目里配置、规则副本都躺在实现自己的目录里，
    只复制 data/ 的话，改动会污染源码目录并影响下一次运行——基准必须可重复。
    """
    ws = Path(tempfile.mkdtemp(prefix=f"bench_{impl_name}_"))
    shutil.copytree(HERE / "data", ws / "data")
    if impl_path is not None:
        shutil.copytree(impl_path.parent, ws / "impl")
    return ws


def close_workspace(ws: Path) -> None:
    shutil.rmtree(ws, ignore_errors=True)


def case_params(case: dict) -> dict:
    """用例传给方法的参数：args + 可选的 waybill。"""
    params = dict(case.get("args") or {})
    if case.get("waybill"):
        params["waybill_id"] = case["waybill"]
    return params


def compare(expects: dict, answer: dict, tolerance: float) -> list[dict]:
    """返回不匹配项列表；空列表 = PASS。"""
    misses = []
    for key, want in expects.items():
        if key not in answer:
            misses.append({"key": key, "expected": want, "actual": "<missing>"})
            continue
        got = answer[key]
        # **答不出（None）不等于答对（False）**：缺值必须判 FAIL，
        # 否则"数据不足，无法判断"会被算成"它说不会被挡" —— 这是最恶劣的伪通过。
        if got is None:
            misses.append({"key": key, "expected": want, "actual": "<无法判断>"})
            continue
        if isinstance(want, bool) or isinstance(got, bool):
            ok = bool(got) == bool(want)
        elif isinstance(want, (int, float)):
            try:
                ok = abs(float(got) - float(want)) <= tolerance
            except (TypeError, ValueError):
                ok = False
        else:
            ok = str(got) == str(want)
        if not ok:
            misses.append({"key": key, "expected": want, "actual": got})
    return misses


def audit_elements(answer: dict, required: list[str]) -> tuple[int, list[str]]:
    audit = answer.get("audit") or {}
    present = [k for k in required if audit.get(k) not in (None, "", [])]
    # checksum 是字符串，evidence_ids 可能是空列表 → 空列表不算携带
    return len(present), present


def duplicate_copies(impl_path: Path) -> int:
    """静态数副本：实现里硬编码了规则参数 / 金额口径的次数。"""
    src = impl_path.read_text(encoding="utf-8", errors="ignore")
    count = 0
    for _, pattern in DUPLICATE_PATTERNS:
        hits = pattern.findall(src)
        count += len(hits)
    return count


def run_impl(name: str, impl_path: Path, manifest: dict) -> dict:
    cards = {c["id"]: c for c in json.loads(
        (HERE / manifest["dataset"]["change_cards"]).read_text(encoding="utf-8"))["cards"]}
    tolerance = manifest.get("tolerance", 0.01)
    results = []
    metrics = {"M1": [], "M2": [], "M3": []}
    ws = None
    # 先探测实现是否可用（缺依赖的组如 RAG 无端点、selfgraph 无 Kuzu）：
    # 「不可用」与「跑失败」要分开 —— 前者是 SKIPPED，不是 FAIL，
    # 也不能把异常抛给调用方（调用方应拿到一致的结果结构）。
    try:
        Impl = load_impl(impl_path)
        Impl()
    except Exception as exc:  # noqa: BLE001
        skipped = type(exc).__name__.endswith(("Unavailable", "NotAvailable"))
        return {"implementation": name, "passed": 0, "total": 0, "results": [],
                "metrics": {"M1": [], "M2": [], "M3": []},
                "skipped": skipped,
                "error": f"{type(exc).__name__}: {exc}"}

    try:
        Impl = load_impl(impl_path)

        # ---- 查询类用例（每个用例独立工作区：变更卡之间互不污染）----
        for case in manifest["cases"]:
            if case["kind"] != "query":
                continue
            ws = workspace_for(name, impl_path)
            os.environ["BENCH_DATA_DIR"] = str(ws / "data")
            os.environ["BENCH_IMPL_DIR"] = str(ws / "impl")
            impl = Impl()
            answer = getattr(impl, case["method"])(**case_params(case))
            misses = compare(case["expects"], answer, tolerance)
            n_audit, present = audit_elements(answer, manifest["audit_elements"])
            metrics["M2"].append({"case": case["id"], "audit_present": n_audit,
                                   "elements": present})
            results.append({"id": case["id"], "name": case["name"], "kind": "query",
                            "status": "PASS" if not misses else "FAIL",
                            "misses": misses, "answer": answer})
            if ws is not None:
                close_workspace(ws)

        # ---- 变更类用例 ----
        for case in manifest["cases"]:
            if case["kind"] != "change":
                continue
            card = cards[case["card"]]
            ws = workspace_for(name, impl_path)
            os.environ["BENCH_DATA_DIR"] = str(ws / "data")
            os.environ["BENCH_IMPL_DIR"] = str(ws / "impl")
            impl = Impl()
            change = impl.apply_change(card)
            metrics["M1"].append({"case": case["id"],
                                   "changed_files": len(change.get("changed_files") or []),
                                   "code_files": change.get("code_files", 0),
                                   "restart_required": bool(change.get("restart_required"))})
            # 变更后**总是**重新取一次实现：
            #   本体路线 = 重新读声明（不需重启进程）；
            #   属性图路线 = 重启进程（重新读配置）。
            # 两者行为一致，差别只体现在 M1 的 restart_required 指标上。
            impl = Impl()
            answer = getattr(impl, case["method"])(**case_params(case))
            misses = compare(case["expects"], answer, tolerance)
            results.append({"id": case["id"], "name": case["name"], "kind": "change",
                            "status": "PASS" if not misses else "FAIL",
                            "misses": misses, "answer": answer,
                            "change": change})
            if ws is not None:
                close_workspace(ws)

        # ---- M3：副本计数（静态）----
        metrics["M3"].append({"duplicate_rule_copies": duplicate_copies(impl_path)})
    finally:
        if ws is not None:
            if ws is not None:
                close_workspace(ws)
        os.environ.pop("BENCH_DATA_DIR", None)
        os.environ.pop("BENCH_IMPL_DIR", None)

    passed = sum(1 for r in results if r["status"] == "PASS")
    return {
        "implementation": name,
        "path": str(impl_path.relative_to(ROOT)) if impl_path.is_relative_to(ROOT) else str(impl_path),
        "passed": passed, "total": len(results),
        "results": results, "metrics": metrics,
    }


def summarize(run: dict) -> dict:
    """把每个实现的指标汇总成可比的一行。"""
    m1 = run["metrics"]["M1"]
    m2 = run["metrics"]["M2"]
    m3 = run["metrics"]["M3"]
    changed = sum(x["changed_files"] for x in m1)
    restarts = sum(1 for x in m1 if x["restart_required"])
    audit = max((x["audit_present"] for x in m2), default=0)
    copies = max((x["duplicate_rule_copies"] for x in m3), default=0)
    return {
        "implementation": run["implementation"],
        "passed": run["passed"], "total": run["total"],
        "M1_changed_files": changed, "M1_restarts_required": restarts,
        "M2_audit_elements": audit, "M3_duplicate_copies": copies,
    }



# ---- 第三方实现的隔离检查（PROTOCOL.md §4）----
# 只在**读取这些文件**或**按这些键取值**时才算偷看；
# 单纯出现 expected 这类普通变量名不算（否则误报一片）。
FORBIDDEN_PATTERNS = [
    (re.compile(r"manifest\.json"), "不得读取 manifest.json（期望值在评测方手里）"),
    (re.compile(r"golden\.json"), "不得读取黄金答案"),
    (re.compile(r"baseline\.json"), "不得读取基线结果"),
    (re.compile(r"""\[["'](?:expects|expected|expect)["']\]"""), "不得按键读取期望值字段"),
    (re.compile(r"""\.get\(\s*['\"](?:expects|expected|expect)['\"]"""),
     "不得按键读取期望值字段"),
]
FORBIDDEN_IMPORTS = [
    (re.compile(r"^\s*from\s+freight\b", re.M), "不得 import 作者私有包 freight"),
    (re.compile(r"^\s*import\s+freight\b", re.M), "不得 import 作者私有包 freight"),
    (re.compile(r"semantic_layer\.json"), "不得读取作者的本体声明文件"),
]
STDLIB_OK = {
    "json", "math", "os", "re", "ast", "sys", "pathlib", "typing", "hashlib",
    "collections", "itertools", "functools", "dataclasses", "datetime", "csv",
    "sqlite3", "argparse", "copy", "random", "decimal", "statistics", "uuid",
    "urllib", "http", "socket", "subprocess", "tempfile", "shutil", "glob",
    "asyncio", "inspect", "contextlib", "enum", "abc", "typing_extensions",
    "textwrap", "time", "traceback", "string", "random",
}


def audit_impl(impl_path: Path) -> dict:
    """静态隔离检查：第三方实现不得偷看期望值、不得使用作者私有能力。"""
    src = impl_path.read_text(encoding="utf-8", errors="ignore")
    issues, notes = [], []
    for pattern, why in FORBIDDEN_PATTERNS:
        if pattern.search(src):
            issues.append(why)
    for pattern, why in FORBIDDEN_IMPORTS:
        if pattern.search(src):
            issues.append(f"源码命中禁止项：{why}")
    for m in re.finditer(r"^\s*(?:from|import)\s+([A-Za-z_][\w.]*)", src, re.M):
        root = m.group(1).split(".")[0]
        if root not in STDLIB_OK and root != "__future__":
            notes.append(f"非标准库依赖：{m.group(1)}（不失败，但请在提交里说明）")
    return {"ok": not issues, "issues": issues, "notes": notes}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", action="append", default=[],
                    help="外部实现路径（可重复）；不指定则跑内置两个参考实现")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check-baseline", action="store_true",
                    help="与 baseline.json 比对：有差异则退出码非 0（CI 用）")
    ap.add_argument("--audit-impl", action="store_true",
                    help="只做第三方实现的隔离检查（不跑用例）")
    ap.add_argument("--with-rag", action="store_true",
                    help="同时跑 RAG 路线（需要 OPENAI_CHAT_API_KEY，且会慢很多）")
    args = ap.parse_args()

    manifest = load_manifest()
    impls = {}
    for spec in args.impl:
        path = Path(spec).expanduser().resolve()
        impls[path.stem] = path
    if not impls:
        impls = {k: v for k, v in BUILTIN.items()}
        if args.with_rag:
            impls.update(BUILTIN_OPTIONAL)

    if args.audit_impl:
        reports = []
        for name, path in impls.items():
            rep = audit_impl(path)
            rep["implementation"] = name
            reports.append(rep)
            label = path.parent.name if path.parent.name != "implementations" else path.stem
            print(f"[{'OK ' if rep['ok'] else 'FAIL'}] 隔离检查 {label}"
                  + ("" if rep["ok"] else ": " + "; ".join(rep["issues"])))
            for note in rep["notes"]:
                print(f"       提示 {note}")
        return 0 if all(r["ok"] for r in reports) else 1

    runs = []
    for name, path in impls.items():
        try:
            runs.append(run_impl(name, path, manifest))
        except Exception as exc:  # noqa: BLE001
            # 「不可用」与「跑失败」要分开：需要外部依赖的组（如 RAG 需要大模型端点）
            # 缺依赖时是 SKIPPED，不是 FAIL —— 否则会把环境问题算成实现问题。
            skipped = type(exc).__name__.endswith(("Unavailable", "NotAvailable"))
            runs.append({"implementation": name, "passed": 0, "total": 0,
                         "results": [], "metrics": {"M1": [], "M2": [], "M3": []},
                         "skipped": skipped,
                         "error": f"{type(exc).__name__}: {exc}"})

    summary = [summarize(r) for r in runs]
    out = {"benchmark": manifest["benchmark"], "version": manifest["version"],
           "runs": runs, "summary": summary}

    if args.check_baseline:
        base_path = HERE / "baseline.json"
        if base_path.exists():
            baseline = json.loads(base_path.read_text(encoding="utf-8"))
            base = {b["implementation"]: b for b in baseline.get("summary", [])}
            drift = []
            for row in summary:
                ref = base.get(row["implementation"])
                if not ref:
                    drift.append({"implementation": row["implementation"],
                                  "issue": "baseline 中没有该实现"})
                    continue
                for key in ("passed", "total", "M1_changed_files", "M1_restarts_required",
                            "M2_audit_elements", "M3_duplicate_copies"):
                    if ref.get(key) != row.get(key):
                        drift.append({"implementation": row["implementation"], "metric": key,
                                      "baseline": ref.get(key), "now": row.get(key)})
            out["baseline_drift"] = drift
            out["baseline_ok"] = not drift
        else:
            out["baseline_ok"] = None
            out["baseline_drift"] = [{"issue": "缺少 baseline.json"}]

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out.get("baseline_ok", True) else 1

    print("=" * 92)
    print(f"可执行本体基准 {manifest['benchmark']} v{manifest['version']}")
    print("=" * 92)
    for row, run in zip(summary, runs):
        print(f"\n{row['implementation']}  用例 {row['passed']}/{row['total']} PASS")
        if run.get("error"):
            tag = "SKIPPED（缺依赖）" if run.get("skipped") else "ERROR"
            print(f"  ⚠ {tag}：{run['error']}")
            continue
        print(f"  M1 变更成本：改 {row['M1_changed_files']} 个文件，需重启 {row['M1_restarts_required']} 次")
        print(f"  M2 可审计性：答案携带审计要素 {row['M2_audit_elements']}/5")
        print(f"  M3 一致性风险：规则副本数 {row['M3_duplicate_copies']}")
        for r in runs:
            if r["implementation"] != row["implementation"]:
                continue
            for item in r["results"]:
                mark = "✓" if item["status"] == "PASS" else "✗"
                detail = ""
                if item["misses"]:
                    detail = "  ← " + "; ".join(
                        f"{m['key']}: 期望 {m['expected']} 实得 {m['actual']}"
                        for m in item["misses"][:3])
                print(f"    [{mark}] {item['id']} {item['name'][:44]}{detail}")
    if "baseline_drift" in out:
        print("\n" + "-" * 92)
        if out["baseline_ok"]:
            print("基线比对：一致 ✓")
        else:
            print("基线比对：存在差异 ✗")
            for d in out["baseline_drift"]:
                print("   ", json.dumps(d, ensure_ascii=False))
    print("=" * 92)
    return 0 if out.get("baseline_ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
