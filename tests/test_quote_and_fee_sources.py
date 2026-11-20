"""报价明细与多源费用取数承重测试。

两条落地约束：
  1. 报价口径必须来自计价中心报价单明细，不能复用 Waybill.estimated_fee 快照；
  2. 费用明细必须按 data_sources 声明的连接器逐源取数，取不到/无证据要显式缺口。
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from freight.engine import FreightSystem, now_iso  # noqa: E402
from freight.fact_store import FactStore  # noqa: E402

WB = "waybill:WB20261105001"


@pytest.fixture()
def fs(tmp_path):
    system = FreightSystem(db_path=str(tmp_path / "facts.db"))
    yield system
    system.conn.close()


def _codes(result):
    return [finding["code"] for finding in result["findings"]]


class TestQuoteDetailProvenance:
    def test_quoted_total_comes_from_quote_items(self, fs):
        r = fs.fee_analysis(WB)
        assert r["quoted_total_source"] == "pricing.list_quote_items"
        assert r["quote"]["quote_id"] == "quote:Q20261105001"
        assert len(r["quote"]["items"]) == 7
        assert r["quote"]["quoted_total"] == 480.0
        assert r["quoted_total"] == 480.0
        assert r["variance"] == 38.0

    def test_quote_item_change_changes_variance_without_touching_snapshot(self, fs):
        """改报价明细金额，报价口径必须跟着变；运单快照不参与计算。"""
        row = FactStore.decode(fs.facts.get_row("quote_item:QI001"))
        row["props"]["amount"] = 300.0
        fs.facts.update_object(
            "quote_item:QI001", row["props"], row["name"], row["status"], now_iso())
        r = fs.fee_analysis(WB)
        assert r["quote"]["quoted_total"] == 400.0
        assert r["quoted_total"] == 400.0
        assert r["variance"] == 118.0
        assert "QUOTE_TOTAL_MISMATCH" in _codes(r)

    def test_snapshot_drift_is_reported(self, fs):
        row = FactStore.decode(fs.facts.get_row(WB))
        row["props"]["estimated_fee"] = 500.0
        fs.facts.update_object(WB, row["props"], row["name"], row["status"], now_iso())
        r = fs.fee_analysis(WB)
        assert r["quoted_total"] == 480.0
        assert "QUOTE_SNAPSHOT_DRIFT" in _codes(r)

    def test_missing_quote_items_blocks_quote_conclusion(self, fs):
        fs.conn.execute(
            "DELETE FROM relations WHERE predicate='quote_contains_item'")
        fs.conn.commit()
        r = fs.fee_analysis(WB)
        assert r["quoted_total"] == 0.0
        assert "QUOTE_ITEMS_MISSING" in _codes(r)


class TestFeeSourceCoverage:
    def test_fees_are_resolved_across_systems(self, fs):
        r = fs.fee_analysis(WB)
        coverage = r["source_coverage"]
        assert coverage["resolved"] == 7
        assert coverage["unresolved"] == 0
        assert {row["system"] for row in coverage["sources"]} == {"TMS", "ETC"}
        assert {row["source_system"] for row in coverage["fee_sources"]} == {"TMS", "ETC"}
        assert "FEE_SOURCE_UNRESOLVED" not in _codes(r)

    def test_removing_connector_creates_explicit_gap(self, fs):
        fs.sem.data_sources = [
            item for item in fs.sem.data_sources
            if not (item["class"] == "FeeItem" and item["system"] == "TMS")
        ]
        r = fs.fee_analysis(WB)
        assert r["source_coverage"]["unresolved"] == 6
        assert _codes(r).count("FEE_SOURCE_UNRESOLVED") == 6

    def test_required_evidence_missing_is_finding(self, fs):
        fs.conn.execute("DELETE FROM evidence WHERE evidence_id='evidence:E003'")
        fs.conn.commit()
        r = fs.fee_analysis(WB)
        assert "FEE_EVIDENCE_MISSING" in _codes(r)

    def test_stale_source_is_finding(self, fs):
        row = FactStore.decode(fs.facts.get_row("fee:F004"))
        row["props"]["occurred_at"] = "2026-10-01T10:00:00+08:00"
        fs.facts.update_object(
            "fee:F004", row["props"], row["name"], row["status"], now_iso())
        fs.conn.execute(
            "UPDATE evidence SET occurred_at='2026-10-01T10:00:00+08:00' "
            "WHERE evidence_id='evidence:E004'")
        fs.conn.commit()
        r = fs.fee_analysis(WB)
        assert "FEE_SOURCE_STALE" in _codes(r)

    def test_traceability_includes_quote_and_sources(self, fs):
        r = fs.fee_analysis(WB)
        steps = {step["id"]: step for step in r["traceability"]["steps"]}
        assert steps["quote"]["source"] == "pricing.list_quote_items"
        assert "7" in steps["quote"]["detail"]
        assert steps["source_coverage"]["value"] == "7/7 项已解析"
        assert steps["source:TMS"]["source"] == "tms.list_charge_lines"
        assert steps["source:ETC"]["source"] == "etc.list_toll_transactions"