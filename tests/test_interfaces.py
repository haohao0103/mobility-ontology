"""P1-2 接口多态承重测试。"""
from __future__ import annotations

import pytest

from enhancements.interfaces import (
    InterfaceRegistry, InterfaceViolation, compute_variance,
)
from enhancements.function_registry import FunctionRegistry, FunctionRegistryError


def _structural(raw, *interface_ids):
    """把指定接口退回 structural 形状匹配（删掉显式 implements）。

    出厂配置已改为显式 implements（Palantir ImplementsInterfaces2 语义）：
    显式声明存在时它就是权威，改属性 shape 不再影响实现类集合。
    要测 structural 匹配路径，必须显式退回——不能靠出厂默认值偶然成立。
    """
    for i in raw["interfaces"]:
        if not interface_ids or i["id"] in interface_ids:
            i.pop("implements", None)
            i["match"] = "structural"
    return raw


class TestStructuralMatch:
    def test_chargeable_matches(self, make_ext):
        reg = InterfaceRegistry(make_ext())
        impls = reg.implementations("Chargeable")
        assert "Waybill" in impls and "FeeItem" in impls

    def test_party_matches(self, make_ext):
        reg = InterfaceRegistry(make_ext())
        impls = reg.implementations("Party")
        assert "Shipper" in impls and "Driver" in impls

    def test_conforms_negative(self, make_ext):
        reg = InterfaceRegistry(make_ext())
        # Shipper 没有金额属性 -> 不是 Chargeable
        assert reg.conforms("Shipper", "Chargeable") is False

    def test_unknown_interface_rejected(self, make_ext):
        reg = InterfaceRegistry(make_ext())
        with pytest.raises(InterfaceViolation):
            reg.implementations("NoSuchInterface")


class TestLoadBearing:
    def test_delete_required_anyof_shrinks_implementations(self, make_ext):
        """承重（structural 路径）：删掉 required_any_of 金额要求 -> Chargeable 实现类暴增。"""

        def mutate(raw):
            for i in raw["interfaces"]:
                if i["id"] == "Chargeable":
                    del i["required_any_of"]
            return _structural(raw, "Chargeable")

        before = InterfaceRegistry(make_ext(_structural)).implementations("Chargeable")
        after = InterfaceRegistry(make_ext(mutate)).implementations("Chargeable")
        assert len(after) > len(before)
        assert set(before) < set(after)

    def test_remove_timeseries_flag_kills_temporal_iface(self, make_ext):
        """承重（structural 路径）：去掉 status 的 timeseries -> TemporalTracked 无实现类。"""

        def mutate(raw):
            for p in raw["property_types"]:
                if p["name"] == "status":
                    p["timeseries"] = False
            return _structural(raw, "TemporalTracked")

        reg = InterfaceRegistry(make_ext(mutate))
        assert reg.implementations("TemporalTracked") == []

    def test_explicit_implements_is_authoritative(self, make_ext):
        """承重（显式路径）：显式 implements 存在时，改 shape 不影响实现类集合。"""

        def mutate(raw):
            for p in raw["property_types"]:
                if p["name"] == "status":
                    p["timeseries"] = False
            return raw  # 保留出厂显式 implements

        reg = InterfaceRegistry(make_ext(mutate))
        assert "Waybill" in reg.implementations("TemporalTracked")

    def test_lying_implements_is_caught(self, make_ext):
        """承重：声明「Shipper 实现 Chargeable」但 Shipper 没有金额属性 -> IFACE_UNIMPLEMENTED。"""

        def mutate(raw):
            for i in raw["interfaces"]:
                if i["id"] == "Chargeable":
                    i["implements"].append({"object_type": "Shipper"})
            return raw

        issues = make_ext(mutate).validate()
        assert any(i["code"] == "IFACE_UNIMPLEMENTED" for i in issues)

    def test_property_map_allows_renamed_property(self, make_ext):
        """承重：接口属性名与实现类不同名时，靠 property_map 接线。"""

        def mutate(raw):
            for i in raw["interfaces"]:
                if i["id"] == "Party":
                    i["implements"] = [
                        {"object_type": "Shipper",
                         "property_map": {"status": "status"}},
                    ]
            return raw

        assert make_ext(mutate).validate() == []


class TestPolymorphicFunction:
    def test_variance_on_waybill(self, enhanced):
        result = compute_variance("waybill:WB20261105001", system=enhanced)
        assert isinstance(result, int)

    def test_variance_on_feeitem(self, enhanced):
        fee_id = enhanced.store.one(
            "SELECT object_id FROM objects WHERE type='FeeItem' LIMIT 1"
        )["object_id"]
        result = compute_variance(fee_id, system=enhanced)
        assert isinstance(result, int)

    def test_variance_rejects_non_chargeable(self, enhanced):
        shipper_id = enhanced.store.one(
            "SELECT object_id FROM objects WHERE type='Shipper' LIMIT 1"
        )["object_id"]
        with pytest.raises(InterfaceViolation):
            compute_variance(shipper_id, system=enhanced)

    def test_delete_interface_registration_breaks_function(self, make_ext, system):
        """承重：注册表删掉 Chargeable 接口 -> 多态函数调不动（接口 gate 拒绝）。"""

        def mutate(raw):
            raw["interfaces"] = [i for i in raw["interfaces"] if i["id"] != "Chargeable"]
            return raw

        ext = make_ext(mutate)
        # 注册表校验应报 FN_IFACE_UNKNOWN（chargeable_variance 声明了不存在的接口）
        issues = ext.validate()
        assert any(i["code"] == "FN_IFACE_UNKNOWN" for i in issues)
        # 运行时接口 gate 也拒绝
        from enhancements.security import SecurityContext
        from enhancements.runtime import EnhancedSystem
        enh = EnhancedSystem(system)
        # 用被裁剪的 ext 替换运行时注册表（模拟只改 JSON 后的运行时行为）
        enh.ext = ext
        from enhancements.interfaces import InterfaceRegistry
        enh.interfaces = InterfaceRegistry(ext)
        with pytest.raises((InterfaceViolation, FunctionRegistryError)):
            enh.call_function(
                "chargeable_variance", {"object_id": "waybill:WB20261105001"},
                ctx=SecurityContext.with_roles("t", {"freight-core"}),
            )


class TestFunctionArgGate:
    def test_function_arg_gate_rejects(self, make_ext):
        ext = make_ext()
        reg = InterfaceRegistry(ext)
        with pytest.raises(InterfaceViolation, match="不符合"):
            reg.check_function_arg("chargeable_variance", "Shipper")

    def test_function_arg_gate_passes(self, make_ext):
        reg = InterfaceRegistry(make_ext())
        reg.check_function_arg("chargeable_variance", "Waybill")
