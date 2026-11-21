"""演示负向实验室测试（计划 B-21）：覆盖 B-14~B-20 正向/负向 + 主库零污染。"""
from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from freight.engine import FreightSystem
from freight import demo_labs as dl

WB = "waybill:WB20261105001"


@pytest.fixture(scope="module")
def system():
    s = FreightSystem(str(Path(tempfile.mkdtemp()) / "f.db"),
                      fresh=True, seed=True, with_scenario=True)
    return s


def snapshot(system):
    c = system.conn
    return {t: (c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
            for t in ("objects", "relations", "evidence", "audit", "events")}


class TestZeroPollution:
    def test_labs_do_not_write_main_db(self, system):
        before = snapshot(system)
        dl.action_lab(system, WB)
        dl.contract_simulate(system, "drop_claim")
        dl.guard_demo(system, "drop_property_unsigned")
        dl.type_lab(system)
        dl.declaration_check(system, omit_tool="ontology.catalog")
        after = snapshot(system)
        assert before == after
        assert dl.action_lab(system, WB)["audit_delta"] == 0


class TestB14Contracts:
    def test_coverage_and_drop_claim_flip(self, system):
        cov = dl.contract_coverage(system, WB)
        assert cov["source_coverage"]["unresolved"] == 0
        assert not any(f["code"] == "FEE_SOURCE_UNRESOLVED" for f in cov["findings"])
        r = dl.contract_simulate(system, "drop_claim", WB)
        codes = [f["code"] for f in r["after"]["findings"]]
        assert "FEE_SOURCE_UNRESOLVED" in codes
        assert r["persisted"] is False
        assert any(f["code"] == "FEE_SOURCE_UNRESOLVED" for f in r["flips"])

    def test_drop_source_also_flips(self, system):
        r = dl.contract_simulate(system, "drop_source", WB)
        assert any(f["code"] == "FEE_SOURCE_UNRESOLVED" for f in r["flips"])

    def test_abox_coverage_readable(self, system):
        r = dl.abox_coverage(system)
        assert r["class_coverage"]["total"] > 0
        assert isinstance(r["issues"], list)


class TestB15ActionLab:
    def test_cases_codes_and_rollback(self, system):
        r = dl.action_lab(system, WB)
        by_case = {c["case"]: c for c in r["cases"]}
        assert by_case["wrong_role"]["code"] == "FORBIDDEN_ROLE"
        assert by_case["wrong_state"]["ok"] is False
        assert by_case["allowed"]["ok"] is True
        assert by_case["replay_1"]["result"].get("replayed") is True
        assert by_case["replay_2"]["result"].get("replayed") is True
        assert r["audit_delta"] == 0
        assert r["state_restored"] is True
        assert r["persisted"] is False


class TestB16GuardDemo:
    def test_unsigned_fail_signed_pass(self, system):
        u = dl.guard_demo(system, "drop_property_unsigned")
        s = dl.guard_demo(system, "drop_property_signed")
        assert u["verdict"] == "FAIL" and u["unapproved_count"] >= 1
        assert s["verdict"] == "PASS" and s["unapproved_count"] == 0
        assert u["impacts"]
        assert u["persisted"] is False and s["persisted"] is False


class TestB17TypeLab:
    def test_type_change_rejected(self, system):
        r = dl.type_lab(system, "waiting_minutes_boolean")
        assert r["after"]["create"]["ok"] is False
        assert r["after"]["create"]["code"] == "TYPE_MISMATCH"
        assert any(f["code"] == "TYPE_MISMATCH" for f in r["flips"])
        assert r["persisted"] is False


class TestB18DeclarationCheck:
    def test_omit_tool_and_type(self, system):
        t = dl.declaration_check(system, omit_tool="ontology.catalog")
        assert any(f["code"] == "TOOL_UNAVAILABLE" for f in t["flips"])
        y = dl.declaration_check(system, omit_type="FeeItem")
        assert any(f["code"] == "TYPE_UNBOUND" for f in y["flips"])
        assert t["after"]["evidence_checksum"]


class TestEnhancedLabs:
    @pytest.fixture(scope="class")
    def enh(self, system):
        import freight.api as fa
        from enhancements.runtime import EnhancedSystem

        old = fa._SYSTEM
        fa._SYSTEM = system
        try:
            return EnhancedSystem(system)
        finally:
            fa._SYSTEM = old

    def test_interface_polymorphism_flip(self, enh):
        from enhancements.demo_labs import interface_simulate
        r = interface_simulate(enh)
        assert r["before"]["impl_count"] > r["after"]["impl_count"]
        assert r["before"]["function_accepted"] is True
        assert r["after"]["function_accepted"] is False
        assert any(f["code"] == "INTERFACE_VIOLATION" for f in r["flips"])

    def test_guard_output_denies_anonymous(self, enh):
        from enhancements.demo_labs import guard_output_lab
        r = guard_output_lab(enh)
        assert r["status"] == "DENIED"
        assert any(f["code"] == "OUTPUT_MARKING_DENIED" for f in r["findings"])

    def test_marking_flip(self, enh):
        from enhancements.demo_labs import simulate_marking
        r = simulate_marking(enh, "drop_source_marking", "ExceptionTicket")
        assert r["after"]["visible_rows"] > r["before"]["visible_rows"]
        assert r["flips"]
        assert r["persisted"] is False
