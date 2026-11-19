"""P2-1/P2-2 函数注册表 + 沙箱承重测试。"""
from __future__ import annotations

import pytest

from enhancements.function_registry import FunctionRegistry, FunctionRegistryError
from enhancements.security import SecurityContext, AccessDenied
from enhancements.interfaces import InterfaceViolation
from enhancements import sandbox


class TestRegistry:
    def test_list_functions(self, make_ext):
        reg = FunctionRegistry(make_ext())
        fns = reg.list_functions()
        ids = {f["id"] for f in fns}
        assert {"fee_breakdown", "quote_reconcile", "chargeable_variance"} <= ids

    def test_call_via_runtime(self, enhanced):
        out = enhanced.call_function(
            "chargeable_variance", {"object_id": "waybill:WB20261105001"}
        )
        assert out["function"] == "chargeable_variance"
        assert isinstance(out["result"], int)

    def test_load_bearing_delete_registration_unknown(self, make_ext, system):
        """承重：注册表删掉函数 -> 调用报 FUNCTION_UNKNOWN。"""

        def mutate(raw):
            raw["function_registry"] = [
                f for f in raw["function_registry"] if f["id"] != "chargeable_variance"
            ]
            return raw

        from enhancements.runtime import EnhancedSystem
        enh = EnhancedSystem(system)
        enh.ext = make_ext(mutate)
        enh.functions = FunctionRegistry(enh.ext, enh.security, enh.interfaces)
        with pytest.raises(FunctionRegistryError, match="未注册"):
            enh.call_function("chargeable_variance", {"object_id": "waybill:WB20261105001"})

    def test_signature_missing_required(self, make_ext):
        reg = FunctionRegistry(make_ext())
        problems = reg.validate_signature("quote_reconcile", {})
        assert any("缺少必填参数" in p for p in problems)

    def test_signature_unknown_arg(self, make_ext):
        reg = FunctionRegistry(make_ext())
        problems = reg.validate_signature("quote_reconcile", {
            "waybill_id": "x", "bogus": 1})
        assert any("未知参数" in p for p in problems)

    def test_scope_token_blocks_call(self, enhanced):
        ctx = SecurityContext.with_roles("agent-readonly", {"freight-core"})
        ctx.scope_token = enhanced.ext.scope_tokens["default_token"]
        with pytest.raises(AccessDenied, match="scope token"):
            enhanced.call_function("evidence_chain",
                                   {"waybill_id": "waybill:WB20261105001"}, ctx)

    def test_interface_arg_violation(self, enhanced):
        shipper_id = enhanced.store.one(
            "SELECT object_id FROM objects WHERE type='Shipper' LIMIT 1"
        )["object_id"]
        with pytest.raises(InterfaceViolation):
            enhanced.call_function("chargeable_variance", {"object_id": shipper_id})


class TestSandbox:
    def test_sandbox_normal_execution(self, make_ext):
        ext = make_ext()
        out = sandbox.run_in_sandbox(
            "chargeable_variance", {"object_id": "waybill:WB20261105001"},
            ext, timeout_ms=10000,
        )
        assert out["function"] == "chargeable_variance"
        assert isinstance(out["result"], int)

    def test_sandbox_timeout_kills_runaway_function(self, make_ext):
        """承重：注册一个死循环函数 -> 沙箱超时杀掉，主进程存活。"""

        def mutate(raw):
            raw["function_registry"].append({
                "id": "spin_forever",
                "label": "死循环（沙箱演示）",
                "version": "1.0.0",
                "owner": "qa",
                "implementation": "tests._sandbox_helpers.spin_forever",
                "signature": {"params": [], "returns": "never"},
                "sandbox": {"timeout_ms": 1500, "memory_mb": 128},
            })
            return raw

        ext = make_ext(mutate)
        with pytest.raises(sandbox.SandboxTimeout):
            sandbox.run_in_sandbox("spin_forever", {}, ext,
                                   timeout_ms=1500, memory_mb=128)
        # 主进程活着走到这里即证明沙箱隔离有效

    def test_sandbox_unregistered_function(self, make_ext):
        with pytest.raises(sandbox.SandboxError, match="未注册"):
            sandbox.run_in_sandbox("no_such_fn", {}, make_ext())
