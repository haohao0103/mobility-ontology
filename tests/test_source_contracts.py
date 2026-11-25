"""来源契约（门禁第 27 项）：归属矩阵 · 覆盖度 · 报价三口径。

对应 plan/source-contract-plan.md 的 G1~G4。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from freight import abox, source_coverage  # noqa: E402
from freight.engine import FreightSystem  # noqa: E402
from freight.semantic import SemanticLayer  # noqa: E402


@pytest.fixture(scope="module")
def layer():
    return SemanticLayer()


@pytest.fixture(scope="module")
def db():
    path = abox.build_db(prefix="src_test_")
    yield path
    if os.path.exists(path):
        os.remove(path)


def _mutated_layer(mutate):
    raw = json.loads(json.dumps(SemanticLayer().raw))
    mutate(raw)
    # 内存实验通道：发布 pin 只为磁盘发布物把门，不挡改声明看行为的承重测试
    return SemanticLayer.from_raw(raw), None


class TestOwnershipMatrix:
    def test_universe_comes_from_pricing(self, layer):
        universe = source_coverage.fee_type_universe(layer)
        assert universe == ["base", "highway", "insurance", "loading",
                            "mileage", "other", "waiting"]
        assert set(universe) == set(layer.pricing.keys())

    def test_every_fee_type_has_exactly_one_owner(self, layer):
        issues = source_coverage.check_fee_type_ownership(layer)
        assert issues == [], issues
        owners = source_coverage.fee_type_owners(layer)
        assert set(owners) == set(source_coverage.fee_type_universe(layer))
        assert all(len(v) == 1 for v in owners.values()), owners

    def test_highway_is_owned_by_etc(self, layer):
        owners = source_coverage.fee_type_owners(layer)
        assert owners["highway"][0]["system"] == "ETC"

    def test_dropped_claim_is_caught(self):
        def mutate(raw):
            for ds in raw["data_sources"]:
                # 注意：TMS 在 data_sources 里有多条记录，只有 FeeItem 那条声明了 fee_types
                if ds.get("system") == "TMS" and ds.get("fee_types"):
                    ds["fee_types"] = [t for t in ds["fee_types"] if t != "waiting"]
        sem, _path = _mutated_layer(mutate)
        try:
            codes = {i["code"] for i in source_coverage.check_fee_type_ownership(sem)}
            assert "FEE_TYPE_NO_OWNER" in codes
        finally:
            if _path and os.path.exists(_path):
                os.remove(_path)

    def test_double_claim_is_caught(self):
        def mutate(raw):
            for ds in raw["data_sources"]:
                if ds.get("system") == "TMS" and ds.get("fee_types"):
                    ds["fee_types"] = list(ds["fee_types"]) + ["highway"]
        sem, _path = _mutated_layer(mutate)
        try:
            codes = {i["code"] for i in source_coverage.check_fee_type_ownership(sem)}
            assert "FEE_TYPE_MULTI_OWNER" in codes
        finally:
            if _path and os.path.exists(_path):
                os.remove(_path)

    def test_claim_outside_pricing_is_caught(self):
        def mutate(raw):
            for ds in raw["data_sources"]:
                if ds.get("system") == "TMS" and ds.get("fee_types"):
                    ds["fee_types"] = list(ds["fee_types"]) + ["drone_fee"]
        sem, _path = _mutated_layer(mutate)
        try:
            codes = {i["code"] for i in source_coverage.check_fee_type_ownership(sem)}
            assert "FEE_TYPE_NOT_IN_PRICING" in codes
        finally:
            if _path and os.path.exists(_path):
                os.remove(_path)


class TestFeeTypeCoverage:
    def test_three_sides_complete(self, layer, db):
        m = source_coverage.measure_fee_type_coverage(layer, db)
        assert m["ratio"] == 1.0, m["per_type"]
        for t in m["per_type"]:
            assert t["fee_items"] and t["pricing_rules"] and t["quote_items"], t

    def test_missing_quote_item_is_caught(self, layer, db):
        import shutil
        probe = abox.build_db(prefix="src_cov_")
        try:
            conn = sqlite3.connect(probe)
            row = conn.execute(
                "SELECT object_id FROM objects WHERE type='QuoteItem' "
                "AND props LIKE '%waiting%' LIMIT 1").fetchone()
            assert row, "剧本数据里应有 waiting 类报价明细"
            conn.execute("DELETE FROM objects WHERE object_id=?", (row[0],))
            conn.commit()
            conn.close()
            codes = {i["code"] for i in
                     source_coverage.check_fee_type_coverage(layer, probe)}
            assert "FEE_TYPE_NO_QUOTE_ITEM" in codes
        finally:
            if os.path.exists(probe):
                os.remove(probe)


class TestQuoteThreeWay:
    def test_consistent_quote_has_no_mismatch(self, db):
        system = FreightSystem(db, fresh=False, seed=False)
        try:
            report = system.fee_analysis("waybill:WB20261105001")
            codes = {f.get("code") for f in report.get("findings") or []}
            assert "QUOTE_TOTAL_MISMATCH" not in codes
            assert "QUOTE_NOT_FOUND" not in codes
        finally:
            system.conn.close()

    def test_tampered_quote_amount_is_reported(self, db):
        import shutil
        probe = abox.build_db(prefix="src_quote_")
        try:
            conn = sqlite3.connect(probe)
            row = conn.execute(
                "SELECT object_id, props FROM objects WHERE type='QuoteItem' LIMIT 1"
            ).fetchone()
            assert row
            props = json.loads(row[1])
            props["amount"] = round(float(props.get("amount") or 0) + 100.0, 2)
            conn.execute("UPDATE objects SET props=? WHERE object_id=?",
                         (json.dumps(props, ensure_ascii=False), row[0]))
            conn.commit()
            conn.close()
            system = FreightSystem(probe, fresh=False, seed=False)
            try:
                report = system.fee_analysis("waybill:WB20261105001")
                codes = {f.get("code") for f in report.get("findings") or []}
                assert "QUOTE_TOTAL_MISMATCH" in codes
            finally:
                system.conn.close()
        finally:
            if os.path.exists(probe):
                os.remove(probe)


class TestGateItem:
    def test_gate_has_source_contract_check(self):
        from scripts.semantic_gate import check_source_contracts
        item = check_source_contracts(ROOT)
        assert item["ok"], item["issues"][:3]
        assert item["ownership"]["universe"]
        assert item["coverage"]["ratio"] == 1.0
