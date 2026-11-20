"""P1-1/P1-3/P1-4/P1-6 属性类型注册表承重测试。"""
from __future__ import annotations

import json

import pytest

from enhancements.property_registry import PropertyRegistry, RegistryViolation


def _props(registry: PropertyRegistry, class_id: str) -> dict:
    return {p.name: p for p in registry.properties_of(class_id)}


def _incremental(raw):
    """显式退回 incremental 口径。

    出厂配置已收口为 strict（legacy 通道关闭）。legacy 解析路径仍是受支持的模式，
    但属于「历史兼容」而非常态，所以测它必须显式打开，不能白嫖出厂默认值。
    """
    raw["property_registry_policy"] = {
        "enforcement": "incremental", "legacy_grace": True,
    }
    return raw


class TestRegisteredResolution:
    def test_registered_property_resolves_with_marking(self, make_ext):
        reg = PropertyRegistry(make_ext())
        p = reg.resolve("Shipper", "shipper_name")
        assert p.registered and p.marking == "PII" and p.value_type == "string"

    def test_timeseries_flag(self, make_ext):
        reg = PropertyRegistry(make_ext())
        p = reg.resolve("Waybill", "status")
        assert p.timeseries is True

    def test_primary_key_flag(self, make_ext):
        reg = PropertyRegistry(make_ext())
        p = reg.resolve("Waybill", "waybill_id")
        assert p.primary_key is True

    def test_legacy_property_unregistered(self, make_ext_without):
        # schema v3 后基础本体全量注册；要测 legacy 语义必须显式摘掉该属性
        reg = PropertyRegistry(make_ext_without({"order_id"}, _incremental))
        p = reg.resolve("Waybill", "order_id")  # 未注册的存量属性
        assert p.registered is False and p.origin == "legacy"


class TestLoadBearing:
    def test_delete_registration_removes_marking(self, make_ext_without):
        """承重：删掉 shipper_name 注册 -> 属性降级 legacy，PII 标记消失（脱敏失效）。"""

        def mutate(raw):
            raw["property_types"] = [
                p for p in raw["property_types"] if p["name"] != "shipper_name"
            ]
            return _incremental(raw)

        reg = PropertyRegistry(make_ext_without({"shipper_name"}, mutate))
        p = reg.resolve("Shipper", "shipper_name")
        assert p.registered is False
        assert p.marking is None

    def test_change_marking_takes_effect(self, make_ext):
        """承重：把 driver_name 的标记从 PII 改成 FINANCE -> 解析结果跟着变。"""

        def mutate(raw):
            for p in raw["property_types"]:
                if p["name"] == "driver_name":
                    p["marking"] = "FINANCE"
            return raw

        reg = PropertyRegistry(make_ext(mutate))
        p = reg.resolve("Driver", "driver_name")
        assert p.marking == "FINANCE"

    def test_deprecated_property_warns(self, make_ext):
        def mutate(raw):
            for p in raw["property_types"]:
                if p["name"] == "calc_basis":
                    p["status"] = "deprecated"
            return raw

        reg = PropertyRegistry(make_ext(mutate))
        p = reg.resolve("FeeItem", "calc_basis")
        assert p.status == "deprecated"
        assert any("废弃" in i for i in p.issues)

    def test_strict_mode_rejects_unregistered(self, make_ext_without):
        def mutate(raw):
            raw["property_registry_policy"]["enforcement"] = "strict"
            return raw

        reg = PropertyRegistry(make_ext_without({"order_id"}, mutate))
        with pytest.raises(RegistryViolation, match="未注册"):
            reg.resolve("Waybill", "order_id")


class TestTimeseriesQuery:
    def test_non_timeseries_rejected(self, make_ext, system):
        reg = PropertyRegistry(make_ext())
        with pytest.raises(RegistryViolation, match="不是时序属性"):
            reg.query_at_time(system.facts.store, "waybill:WB20261105001",
                               "waiting_minutes", "2026-11-19T00:00:00Z")

    def test_timeseries_query_returns_current(self, make_ext, system):
        reg = PropertyRegistry(make_ext())
        rows = reg.query_at_time(system.facts.store, "waybill:WB20261105001",
                                 "status", "2999-01-01T00:00:00Z")
        assert len(rows) >= 1
        assert rows[0]["at"] == "(current)"

    def test_flip_timeseries_flag_changes_queryability(self, make_ext, system):
        """承重：注册表 timeseries=false -> 同一属性查询被拒。"""

        def mutate(raw):
            for p in raw["property_types"]:
                if p["name"] == "status":
                    p["timeseries"] = False
            return raw

        reg = PropertyRegistry(make_ext(mutate))
        with pytest.raises(RegistryViolation, match="不是时序属性"):
            reg.query_at_time(system.facts.store, "waybill:WB20261105001",
                              "status", "2999-01-01T00:00:00Z")


class TestPrimaryKey:
    def test_primary_key_unique_passes(self, make_ext, system):
        reg = PropertyRegistry(make_ext())
        assert reg.validate_primary_key(system.facts.store, "Waybill") == []

    def test_primary_key_duplicate_detected(self, make_ext, system):
        """注入重复主键 -> 校验器必须检出。"""
        import sqlite3, os, tempfile
        db = os.path.join(tempfile.mkdtemp(), "dup.db")
        from freight.engine import FreightSystem
        sys2 = FreightSystem(db, fresh=True, seed=True)
        # 直接注入同主键的另一行
        props = dict(
            json.loads(sys2.facts.store.one(
                "SELECT props FROM objects WHERE object_id=?", ("waybill:WB20261105001",)
            )["props"])
        )
        sys2.facts.store.conn.execute(
            "INSERT INTO objects(object_id,type,name,status,props,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?)",
            ("waybill:WB20261105002", "Waybill", "重复主键测试", "CREATED",
             json.dumps(props, ensure_ascii=False), "2026-11-19T00:00:00Z",
             "2026-11-19T00:00:00Z"),
        )
        sys2.facts.store.conn.commit()
        reg = PropertyRegistry(make_ext())
        problems = reg.validate_primary_key(sys2.facts.store, "Waybill")
        assert any("主键重复" in p for p in problems)


class TestCrossReferenceValidation:
    def test_validate_clean(self, make_ext):
        assert make_ext().validate() == []

    def test_unknown_used_by_class_detected(self, make_ext):
        def mutate(raw):
            raw["property_types"][0]["used_by"] = ["NoSuchClass"]
            return raw

        issues = make_ext(mutate).validate()
        assert any(i["code"] == "PROP_USED_BY_UNKNOWN_CLASS" for i in issues)

    def test_primary_key_mismatch_detected(self, make_ext):
        def mutate(raw):
            for p in raw["property_types"]:
                if p["name"] == "shipper_name":
                    p["primary_key"] = True
            return raw

        issues = make_ext(mutate).validate()
        assert any(i["code"] == "PROP_PRIMARY_KEY_MISMATCH" for i in issues)

    def test_unknown_marking_detected(self, make_ext):
        def mutate(raw):
            for p in raw["property_types"]:
                if p["name"] == "shipper_name":
                    p["marking"] = "GHOST_MARKING"
            return raw

        issues = make_ext(mutate).validate()
        assert any(i["code"] == "PROP_MARKING_UNKNOWN" for i in issues)

    def test_invalid_status_detected(self, make_ext):
        def mutate(raw):
            for p in raw["property_types"]:
                if p["name"] == "shipper_name":
                    p["status"] = "beta"
            return raw

        issues = make_ext(mutate).validate()
        assert any(i["code"] == "PROP_STATUS_INVALID" for i in issues)


class TestGate:
    def test_gate_passes_for_registered(self, make_ext):
        reg = PropertyRegistry(make_ext())
        assert reg.gate_unregistered("Shipper", "shipper_name") == []

    def test_gate_flags_new_property_when_strict(self, make_ext):
        """strict 模式下新增未注册属性 -> 门禁拦截项。"""
        import copy
        from enhancements.registry import ExtensionRegistry

        ext = make_ext(lambda raw: {**raw, "property_registry_policy": {
            "enforcement": "strict", "legacy_grace": False}})
        # 塞一个新属性进基础类（模拟本体加了新属性）
        ext.base_classes = dict(ext.base_classes)
        shipper = copy.deepcopy(ext.base_classes["Shipper"])
        shipper["properties"] = shipper["properties"] + ["vip_level"]
        ext.base_classes["Shipper"] = shipper
        reg = PropertyRegistry(ext)
        assert reg.gate_unregistered("Shipper", "vip_level") != []
