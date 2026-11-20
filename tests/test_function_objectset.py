"""函数 object_set 参数承重测试——对齐 Palantir Function 的 ObjectSet 入参。

Palantir Foundry 的 Function 签名可以直接吃 Object Set（一次调用对整个
对象集计算），而非让调用方逐对象循环。本测试验证：
  1. 查询树(dict)入参 -> 查询引擎解析（含行级安全）-> 批量执行
  2. 显式 ID 列表(list)入参 -> 查库校验 -> 批量执行
  3. 类型不符 / 不存在对象 / 非法形态 -> 结构化拒绝
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from enhancements.function_registry import FunctionRegistryError  # noqa: E402
from enhancements.runtime import EnhancedSystem  # noqa: E402
from enhancements.security import SecurityContext  # noqa: E402
from freight.engine import FreightSystem  # noqa: E402

CTX = SecurityContext(user="analyst", roles=set(), markings=set())


@pytest.fixture()
def enh(tmp_path):
    fs = FreightSystem(db_path=str(tmp_path / "fn.db"))
    return EnhancedSystem(fs)


class TestObjectSetParam:
    def test_query_tree_input(self, enh):
        """查询树入参：from Waybill -> 批量拆解全部运单。"""
        out = enh.functions.call(
            "fee_breakdown_batch",
            {"waybills": {"set": {"from": "Waybill"}}},
            runtime=enh, ctx=CTX)
        r = out["result"]
        assert r["count"] >= 3  # seed 至少 3 张运单
        assert all("variance" in x for x in r["results"])

    def test_query_tree_with_predicate(self, enh):
        """查询树带谓词：只拆 DELIVERED 的运单。"""
        out = enh.functions.call(
            "fee_breakdown_batch",
            {"waybills": {"set": {"from": "Waybill"},
                          "where": {"status": {"$eq": "DELIVERED"}}}},
            runtime=enh, ctx=CTX)
        r = out["result"]
        assert r["count"] >= 1

    def test_explicit_id_list_input(self, enh):
        """显式 ID 列表入参。"""
        out = enh.functions.call(
            "fee_breakdown_batch",
            {"waybills": ["waybill:WB20261105001"]},
            runtime=enh, ctx=CTX)
        r = out["result"]
        assert r["count"] == 1
        assert abs(r["total_variance"] - 38.0) < 1e-6

    def test_type_mismatch_rejected(self, enh):
        """of=Waybill 但传入 FeeItem ID -> 结构化拒绝。"""
        with pytest.raises(FunctionRegistryError, match="类型不符|类型"):
            enh.functions.call(
                "fee_breakdown_batch",
                {"waybills": ["fee:F001"]},
                runtime=enh, ctx=CTX)

    def test_nonexistent_object_rejected(self, enh):
        """ID 不存在 -> 拒绝。"""
        with pytest.raises(FunctionRegistryError, match="不存在"):
            enh.functions.call(
                "fee_breakdown_batch",
                {"waybills": ["waybill:NOPE"]},
                runtime=enh, ctx=CTX)

    def test_invalid_shape_rejected(self, enh):
        """非法形态（字符串）-> 拒绝。"""
        with pytest.raises(FunctionRegistryError, match="查询树|dict"):
            enh.functions.call(
                "fee_breakdown_batch",
                {"waybills": "waybill:WB20261105001"},
                runtime=enh, ctx=CTX)

    def test_of_type_filter_rejects_foreign_set(self, enh):
        """of=Waybill 但查询树产出 FeeItem 集 -> 拒绝（先过滤再校验总量）。"""
        with pytest.raises(FunctionRegistryError, match="类型"):
            enh.functions.call(
                "fee_breakdown_batch",
                {"waybills": {"set": {"followLink": {
                    "set": {"ids": ["waybill:WB20261105001"]},
                    "link": "waybill_produces_fee", "direction": "out"}}}},
                runtime=enh, ctx=CTX)
