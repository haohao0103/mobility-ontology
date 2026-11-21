"""公开基准 ontology-execution 的回归保护。

基准本身也是代码：判定器、数据、参考实现任一处被改坏，基准就不可信了。
这里锁住三件事：
1. 判定口径正确（compare / case_params）；
2. 两个参考实现跑出来的结果稳定（本体 6/6；属性图 5/6 且失败的是漂移探针）；
3. 与 baseline.json 一致（CI 的 --check-baseline 依赖它）。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmarks" / "ontology-execution"
sys.path.insert(0, str(BENCH))


def _runner():
    spec = importlib.util.spec_from_file_location("bench_runner", BENCH / "runner.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["bench_runner"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner():
    return _runner()


@pytest.fixture(scope="module")
def manifest(runner):
    return runner.load_manifest()


class TestJudge:
    def test_numeric_tolerance(self, runner):
        assert runner.compare({"x": 12.0}, {"x": 12.005}, 0.01) == []
        assert runner.compare({"x": 12.0}, {"x": 12.02}, 0.01) != []

    def test_bool_and_code(self, runner):
        assert runner.compare({"blocked": True}, {"blocked": True}, 0.01) == []
        assert runner.compare({"blocked": True}, {"blocked": False}, 0.01) != []
        assert runner.compare({"c": "RULE_REJECTED"}, {"c": "RULE_REJECTED"}, 0.01) == []
        assert runner.compare({"c": "RULE_REJECTED"}, {"c": "INVALID_STATE"}, 0.01) != []

    def test_missing_key_is_a_miss(self, runner):
        assert runner.compare({"x": 1.0}, {}, 0.01) != []

    def test_case_params_carries_waybill(self, runner):
        case = {"args": {"amount": 40.0}, "waybill": "WB20261105002"}
        params = runner.case_params(case)
        assert params["amount"] == 40.0 and params["waybill_id"] == "WB20261105002"

    def test_audit_counting(self, runner):
        n, present = runner.audit_elements(
            {"audit": {"version": "3.0.0", "rule_id": "R-1", "effective_from": "x",
                       "evidence_ids": [], "checksum": "abc"}},
            ["version", "rule_id", "effective_from", "evidence_ids", "checksum"])
        assert n == 4 and "evidence_ids" not in present   # 空列表不算携带


class TestDataset:
    def test_dataset_is_self_contained(self):
        """数据必须能独立分发：不得引用仓库里的私有库或私有包。"""
        for name in ("ontology.json", "facts.json", "change_cards.json"):
            text = (BENCH / "data" / name).read_text(encoding="utf-8")
            assert "freight" not in text and "runs/" not in text

    def test_reference_impls_use_stdlib_only(self):
        for impl in (BENCH / "implementations").iterdir():
            src = (impl / "impl.py").read_text(encoding="utf-8")
            assert "import freight" not in src, f"{impl.name} 不得依赖私有包"
            assert "from freight" not in src, f"{impl.name} 不得依赖私有包"

    def test_manifest_expects_are_objective(self, manifest):
        """期望值必须是数值/布尔/码值，不能是主观描述。"""
        for case in manifest["cases"]:
            for key, want in case["expects"].items():
                assert isinstance(want, (int, float, bool, str)), (case["id"], key)


class TestRuns:
    def _run(self, runner, manifest, name):
        return runner.run_impl(name, runner.BUILTIN[name], manifest)

    def test_ontology_reference_passes_all(self, runner, manifest):
        run = self._run(runner, manifest, "ontology_ref")
        assert run["passed"] == run["total"] == 6, [r for r in run["results"] if r["status"] == "FAIL"]

    def test_property_graph_reference_detects_drift(self, runner, manifest):
        """属性图路线的上限口径写在两处 → 漂移探针必须检出（失败是结论，不是 bug）。"""
        run = self._run(runner, manifest, "property_graph_ref")
        failed = [r["id"] for r in run["results"] if r["status"] == "FAIL"]
        assert failed == ["C3"], f"预期只有漂移探针失败，实际 {failed}"

    def test_metrics_show_the_difference(self, runner, manifest):
        ont = runner.summarize(self._run(runner, manifest, "ontology_ref"))
        pg = runner.summarize(self._run(runner, manifest, "property_graph_ref"))
        assert ont["M2_audit_elements"] == 5 and pg["M2_audit_elements"] == 0
        assert ont["M1_restarts_required"] == 0 and pg["M1_restarts_required"] == 3
        assert ont["M3_duplicate_copies"] == 0 and pg["M3_duplicate_copies"] >= 1

    def test_matches_baseline(self, runner, manifest):
        baseline = json.loads((BENCH / "baseline.json").read_text(encoding="utf-8"))
        ref = {b["implementation"]: b for b in baseline["summary"]}
        for name in ("ontology_ref", "property_graph_ref"):
            now = runner.summarize(self._run(runner, manifest, name))
            for key in ("passed", "total", "M1_changed_files", "M1_restarts_required",
                        "M2_audit_elements", "M3_duplicate_copies"):
                assert ref[name][key] == now[key], (name, key, ref[name][key], now[key])
