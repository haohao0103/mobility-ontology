#!/usr/bin/env python3
"""把基准打包成**可独立对外分发**的目录（本机无 GitHub API 凭据时的发布路径）。

做三件事：
1. 复制 benchmarks/ontology-execution 到 dist/ontology-execution（自包含，可直接建仓发布）；
2. 生成一份对外首屏 README（突出"30 秒跑起来"与结果表，去掉内部仓库路径）；
3. 打印建仓 + 推送的完整命令清单（用户执行即可，不需要 API token）。

用法：
    python scripts/publish_benchmark.py                 # 默认写到 dist/
    python scripts/publish_benchmark.py --out /tmp/x --keep-reference
"""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "benchmarks" / "ontology-execution"

FRONT_MATTER = """<!--
本目录是 ontology-execution 基准的**独立分发包**：
- 只依赖 Python 标准库（>=3.10），不联网；
- 不依赖任何私有仓库或私有数据；
- 可以直接 clone 后 `python runner.py` 跑起来。
发布步骤见 PUBLISH.md。
-->
"""


def build(out_dir: Path, keep_reference: bool) -> Path:
    target = out_dir / "ontology-execution"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(SRC, target)

    if not keep_reference:
        for impl in (target / "implementations").iterdir():
            shutil.rmtree(impl, ignore_errors=True)
        try:
            (target / "implementations").mkdir(exist_ok=True)
        except FileExistsError:
            pass
        (target / "implementations" / ".gitkeep").write_text("", encoding="utf-8")

    readme = target / "README.md"
    text = readme.read_text(encoding="utf-8")
    text = FRONT_MATTER + text
    # 去掉指向本仓内部路径的引用，换成独立仓库视角
    text = text.replace("benchmarks/ontology-execution/", "")
    text = re.sub(r"\[([^\]]+)\]\(benchmarks/ontology-execution/([^)]+)\)", r"[\1](\2)", text)
    readme.write_text(text, encoding="utf-8")
    return target


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "dist"))
    ap.add_argument("--keep-reference", action="store_true",
                    help="保留两个参考实现（默认不带，避免第三方抄我们的写法）")
    args = ap.parse_args()

    out_dir = Path(args.out).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = build(out_dir, args.keep_reference)

    print(f"✅ 分发包已生成：{target}")
    print(f"   文件数：{sum(1 for _ in target.rglob('*') if _.is_file())}")
    print(f"   参考实现：{'保留' if args.keep_reference else '已移除（第三方需自行实现）'}")
    print()
    print("接下来（需要你的 GitHub 账号权限，约一分钟）：")
    print("""
  1) 在 GitHub 新建一个**空**仓库，例如 ontology-execution-benchmark
     （不要勾选 README / LICENSE / .gitignore）

  2) 推送：
     cd %s
     git init && git add -A
     git commit -m "feat: ontology-execution benchmark v0.1.0"
     git remote add origin git@github.com:<你的账号>/ontology-execution-benchmark.git
     git push -u origin main

  3) 发布后建议补：CITATION.cff、issue 模板（接口契约见 PROTOCOL.md §1）、
     并在 README 首屏标注效度前提（PROTOCOL.md §6）
""" % target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
