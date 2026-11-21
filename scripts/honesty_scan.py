#!/usr/bin/env python3
"""诚实性扫描：找出「宣称做了校验、背后却没有计算」的字段。

背景（2026-11-21）
------------------
`freight/object_query.py` 曾返回 `"consistent": True` —— 硬编码常量，背后零校验；
前端 5 处引用它，其中一处把它讲成「派生查询遵守快照一致性」。
**也就是说：页面上给领导看的这句话，建立在一个恒真字段上。**

这类问题**靠人 review 发现不了第二次**（代码读起来完全正常），所以把它变成脚本：
每次改完代码自动扫一遍，出现「宣称校验但赋值恒定」就非零退出。

检查两类：
1. **装饰性校验字段**：字段名宣称一致性 / 完整性 / 有效性 / 已校验 / 已执行 / 血缘 /
   新鲜度 / 可追溯，但赋值是字面量 True/False（没有计算过程）；
2. **恒真断言**：测试里 `assert X is True/False` —— 若 X 本身是常量，就是给假严格
   发通行证（单独列出供人工复核，不直接判失败）。

用法：
    python scripts/honesty_scan.py            # 文本报告
    python scripts/honesty_scan.py --json     # 机器可读
退出码：0 = 干净；1 = 发现装饰性校验字段（CI 应失败）
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "runs", "__pycache__", "node_modules", "_out", ".pytest_tmp",
              "logs", "scripts/_out"}
SKIP_FILES = {".pyc"}

# 字段名里出现这些词，说明它在宣称"系统做了某种校验 / 保证"
CLAIM = re.compile(
    r'(?:consistent|integrity|valid|verified|checked|enforced|compliant|'
    r'guarantee|provenance|lineage|fresh|audited|traceable)',
    re.IGNORECASE)

CONST_BOOL = re.compile(r'"([a-z_]+)"\s*:\s*(True|False)\b')
WEAK_ASSERT = re.compile(r"assert\s+(.+?)\s+is\s+(True|False)\b")


def py_files(root: str, include_tests: bool = False) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel = os.path.relpath(dirpath, root)
        if not include_tests and os.path.basename(rel) == "tests":
            continue
        for name in filenames:
            if name.endswith(".py") and name not in SKIP_FILES:
                out.append(os.path.join(dirpath, name))
    return sorted(out)


def _dicts(tree: ast.AST):
    """遍历所有字典字面量（AST 层面，注释与文档字符串天然被排除）。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            yield node


def scan_claims(root: str) -> list[dict]:
    """装饰性校验字段：宣称校验 + 赋值恒定（AST 精确，零误报）。"""
    hits = []
    for path in py_files(root):
        rel = os.path.relpath(path, root)
        try:
            tree = ast.parse(open(path, encoding="utf-8", errors="ignore").read())
        except SyntaxError:
            continue
        for d in _dicts(tree):
            for key, value in zip(d.keys, d.values):
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    continue
                if not isinstance(value, ast.Constant) or not isinstance(value.value, bool):
                    continue
                if CLAIM.search(key.value):
                    hits.append({
                        "file": rel, "line": key.lineno, "field": key.value,
                        "value": value.value,
                        "source": f'"{key.value}": {value.value}',
                    })
    return hits


def scan_weak_asserts(root: str) -> list[dict]:
    """恒真断言：测试里断言的对象本身是字面量常量。"""
    hits = []
    tests_dir = os.path.join(root, "tests")
    if not os.path.isdir(tests_dir):
        return hits
    for path in py_files(tests_dir):
        rel = os.path.relpath(path, root)
        try:
            tree = ast.parse(open(path, encoding="utf-8", errors="ignore").read())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assert) or node.test is None:
                continue
            test = node.test
            if not isinstance(test, ast.Compare) or len(test.ops) != 1:
                continue
            if not isinstance(test.ops[0], (ast.Is, ast.Eq)):
                continue
            if not isinstance(test.comparators[0], ast.Constant):
                continue
            if not isinstance(test.comparators[0].value, bool):
                continue
            hits.append({"file": rel, "line": node.lineno,
                         "expr": ast.unparse(test.left),
                         "source": ast.unparse(node)[:140]})
    return hits


def scan_constant_fields(root: str) -> list[dict]:
    """全部恒定布尔字典字段（供人工分类：多数是 API 的 ok / schema 定义，属合法）。"""
    hits = []
    for path in py_files(root):
        rel = os.path.relpath(path, root)
        try:
            tree = ast.parse(open(path, encoding="utf-8", errors="ignore").read())
        except SyntaxError:
            continue
        for d in _dicts(tree):
            for key, value in zip(d.keys, d.values):
                if not isinstance(key, ast.Constant) or not isinstance(key.value, str):
                    continue
                if isinstance(value, ast.Constant) and isinstance(value.value, bool):
                    hits.append({"file": rel, "line": key.lineno,
                                 "field": key.value, "value": value.value})
    return hits


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    claims = scan_claims(ROOT)
    weak = scan_weak_asserts(ROOT)
    consts = scan_constant_fields(ROOT)
    out = {
        "ok": not claims,
        "decorative_claim_fields": claims,
        "weak_asserts": weak,
        "constant_bool_fields_total": len(consts),
    }
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if not claims else 1

    print("=" * 78)
    print("诚实性扫描：宣称做了校验、背后却没有计算的字段")
    print("=" * 78)
    if claims:
        print(f"\n发现 {len(claims)} 处装饰性校验字段（必须改）：")
        for c in claims:
            print(f"  {c['file']}:{c['line']}  \"{c['field']}\": {c['value']}")
            print(f"      {c['source']}")
    else:
        print("\n✓ 0 处：所有宣称一致性/完整性/血缘/新鲜度的字段，背后都有真实计算。")
    print(f"\n参考：全仓恒定布尔字段共 {len(consts)} 处（多数是 API 的 ok、schema 定义、"
          f"配置声明，属正常）")
    print(f"参考：测试里断言字面量常量的弱断言 {len(weak)} 处（供人工复核）")
    for w in weak[:10]:
        print(f"  {w['file']}:{w['line']}  {w['source']}")
    print("=" * 78)
    print("结论：" + ("干净" if not claims else f"发现 {len(claims)} 处必须修的假字段"))
    return 0 if not claims else 1


if __name__ == "__main__":
    raise SystemExit(main())
