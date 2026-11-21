"""来源缺口检测能力（门禁第 28 项）：四类缺口必须报得出来，数据修好必须消失。

对应 plan/source-contract-plan.md 的 G3。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from freight import scenarios  # noqa: E402
from freight.engine import FreightSystem  # noqa: E402

REQUIRED = {
    "FEE_SOURCE_UNRESOLVED", "FEE_SOURCE_TYPE_UNDECLARED",
    "FEE_SOURCE_STALE", "FEE_EVIDENCE_MISSING",
}


@pytest.fixture()
def gap_system():
    td = tempfile.mkdtemp(prefix="gap_test_")
    system = FreightSystem(os.path.join(td, "facts.db"), fresh=True, seed=True,
                           with_scenario=True)
    scenarios.build_gap_cases(system)
    system.conn.commit()
    yield system
    system.conn.close()


def _fee_id(finding: dict) -> str:
    """finding 里的 fee_id 是短 id（不带类型前缀）。"""
    return str(finding.get("fee_id") or "").split(":")[-1]


def _codes(system) -> set[str]:
    report = system.fee_analysis(scenarios.GAP_PROBE_WAYBILL)
    return {f.get("code") for f in report.get("findings") or []}


class TestGapDetection:
    def test_all_four_gap_types_detected(self, gap_system):
        codes = _codes(gap_system)
        missing = sorted(REQUIRED - codes)
        assert missing == [], f"未被检出的缺口类型: {missing}"

    def test_normal_waybill_has_no_gap_alerts(self, gap_system):
        """探针必须与正常数据隔离：正常运单不该出现这四类告警。"""
        report = gap_system.fee_analysis("waybill:WB20261105001")
        codes = {f.get("code") for f in report.get("findings") or []}
        assert not (REQUIRED & codes), f"正常数据上误报: {sorted(REQUIRED & codes)}"

    def test_fixing_evidence_removes_alert(self, gap_system):
        fee_id = "fee:GAP-NOEVIDENCE"
        before = [f for f in gap_system.fee_analysis(
            scenarios.GAP_PROBE_WAYBILL).get("findings") or []
            if f.get("code") == "FEE_EVIDENCE_MISSING"
            and _fee_id(f) == fee_id.split(":")[-1]]
        assert before, "缺证据的告警应先存在"
        scenarios.add_gap_evidence(gap_system, fee_id)
        after = [f for f in gap_system.fee_analysis(
            scenarios.GAP_PROBE_WAYBILL).get("findings") or []
            if f.get("code") == "FEE_EVIDENCE_MISSING"
            and _fee_id(f) == fee_id.split(":")[-1]]
        assert after == [], "补了证据告警仍在"

    def test_fixing_source_ownership_removes_alert(self, gap_system):
        fee_id = "fee:GAP-UNRESOLVED"
        row = gap_system.facts.get_row(fee_id)
        import json as _json
        props = _json.loads(row["props"])
        props["source_system"] = "TMS"   # TMS 认领 other
        gap_system.facts.update_object(fee_id, props, None, props.get("status"),
                                        "2026-11-21T10:00:00+08:00")
        gap_system.conn.commit()
        after = [f for f in gap_system.fee_analysis(
            scenarios.GAP_PROBE_WAYBILL).get("findings") or []
            if f.get("code") == "FEE_SOURCE_UNRESOLVED"
            and _fee_id(f) == fee_id.split(":")[-1]]
        assert after == [], "改成合法认领后告警仍在"

    def test_gate_has_gap_detection_check(self):
        from scripts.semantic_gate import check_gap_detection
        item = check_gap_detection(ROOT)
        assert item["ok"], item["issues"][:3]
