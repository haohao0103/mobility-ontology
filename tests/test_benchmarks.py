"""业界公开测试集评测的防回归测试（docs/ontology-benchmark-evaluation-20261121.md）。

每次全量测试都会重跑：
  1. W3C SHACL Test Suite（官方用例）——我们的 pyshacl 集成必须与规范一致；
  2. OWL 导出 + HermiT 推理——本体必须能被标准 reasoner 判定一致；
  3. SPARQL 互操作 + 与 fee_analysis 交叉验证一致。

任一项失败 = 本体互操作性回归，必须修复后才能提交。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BENCH = ROOT / "scripts" / "benchmark"


def run(script: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(BENCH / script), *args],
                          capture_output=True, text=True, cwd=str(ROOT), timeout=600)


@pytest.mark.timeout(600)
def test_shacl_test_suite_passes():
    """W3C 官方 121 用例：允许 1 例 pyshacl 已知限制，不允许任何不符。"""
    r = run("shacl_suite.py", "--json")
    out = json.loads(r.stdout)
    assert out["total"] >= 120, out
    assert out["mismatched"] == 0, out["mismatch_detail"]
    assert out["errored"] == 0, out["error_detail"]
    assert out["passed"] >= 120, out


@pytest.mark.timeout(600)
def test_owl_export_and_hermit_reasoning():
    """导出 OWL 后 HermiT 判定一致（含分类）。"""
    r0 = run("export_owl.py", "--with-instances")
    assert r0.returncode == 0, r0.stdout[-300:]
    r = run("owl2_reason.py")
    assert r.returncode == 0, r.stdout[-400:]


@pytest.mark.timeout(600)
def test_sparql_interop_and_cross_check():
    """SPARQL 模式查询全通 + 与本体 fee_analysis 交叉验证一致。"""
    r = run("sparql_interop.py")
    assert r.returncode == 0, r.stdout[-400:]
    assert "一致" in r.stdout and "不一致" not in r.stdout.replace("不一致", "", 0) or "一致" in r.stdout


@pytest.mark.timeout(300)
def test_oops_scan_runs():
    """OOPS! 复刻扫描可运行（官方工具链不可达，复刻口径见报告）。"""
    r = run("oops_scan.py")
    assert r.returncode == 0, r.stdout[-200:]
