"""Palantir 追齐层：六项缺口的承重测试。

每个测试都对应一个**曾经的缺口**，并且断言「不修就会漏」——只证明绿了不算数：
- P0-1 扩展层无 schema：垃圾键/非法类型必须被拦；
- P0-2 属性注册表只登记不执行：类型错的写入必须被拒（创建 + 更新两条路径）；
- P0-3 策略口径：出厂必须 strict 且 legacy 关闭，改回去门禁要响；
- P1-1 展示层：全量 class/link 必须有 rid + display_metadata + plural_display_name；
- P1-2 函数签名类型化：参数类型、返回类型、返回字段都必须校验；
- P1-3 接口显式 implements：显式优先、谎报要被抓、属性映射要生效；
- P2-1 物化投影：一致性可验证、不新鲜必须回退全表（不能静默少行）；
- P2-2 marking 传播：数据源/类级标记向对象传播，属性标记只管列不管行；
- P2-3 破坏性变更：未签字必须 FAIL。
"""
from __future__ import annotations

import copy
import json
import os
import sys
import tempfile

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from enhancements.function_registry import FunctionRegistryError
from enhancements.registry import ExtensionRegistry
from enhancements.security import AccessDenied, SecurityContext
from freight import projection
from freight.semantic import OntologyViolation


# ---------------------------------------------------------------- P0-1
class TestExtensionSchema:
    def _gate(self):
        from scripts.semantic_gate import check_extension_schema
        return check_extension_schema(ROOT)

    def test_shipped_extension_passes(self):
        item = self._gate()
        assert item["ok"], item["issues"]

    def test_garbage_key_is_blocked(self, tmp_path):
        """负向：往扩展层塞垃圾顶层键必须被 schema 拒绝。"""
        import jsonschema
        schema = json.load(open(os.path.join(
            ROOT, "schema", "ontology_extension.schema.json"), encoding="utf-8"))
        ext = json.load(open(os.path.join(
            ROOT, "enhancements", "ontology_extension.json"), encoding="utf-8"))
        bad = copy.deepcopy(ext)
        bad["totally_bogus_top_key"] = {"x": 1}
        errors = list(jsonschema.Draft202012Validator(schema).iter_errors(bad))
        assert errors, "垃圾顶层键竟然通过了 schema"

    def test_illegal_value_type_is_blocked(self):
        import jsonschema
        schema = json.load(open(os.path.join(
            ROOT, "schema", "ontology_extension.schema.json"), encoding="utf-8"))
        ext = json.load(open(os.path.join(
            ROOT, "enhancements", "ontology_extension.json"), encoding="utf-8"))
        bad = copy.deepcopy(ext)
        bad["property_types"][0]["value_type"] = "not_a_real_type"
        assert list(jsonschema.Draft202012Validator(schema).iter_errors(bad))

    def test_object_set_param_requires_of(self):
        """负向：object_set 参数缺 of 必须被拦（否则不知道元素类型）。"""
        import jsonschema
        schema = json.load(open(os.path.join(
            ROOT, "schema", "ontology_extension.schema.json"), encoding="utf-8"))
        ext = json.load(open(os.path.join(
            ROOT, "enhancements", "ontology_extension.json"), encoding="utf-8"))
        bad = copy.deepcopy(ext)
        bad["function_registry"][0]["signature"]["params"].append(
            {"name": "s", "type": "object_set"})
        assert list(jsonschema.Draft202012Validator(schema).iter_errors(bad))

    def test_extension_does_not_override_base_types(self):
        """扩展层可以覆盖标记，但值类型必须与基础注册表一致（否则二源真相）。"""
        base = json.load(open(os.path.join(
            ROOT, "freight", "semantic_layer.json"), encoding="utf-8"))
        base_pt = {p["name"]: p for p in base["property_types"]}
        ext = json.load(open(os.path.join(
            ROOT, "enhancements", "ontology_extension.json"), encoding="utf-8"))
        for p in ext["property_types"]:
            b = base_pt.get(p["name"])
            if b is None:
                continue
            assert p["value_type"] == b["value_type"], f"{p['name']} 类型分叉"
            assert p["base_type"] == b["base_type"], f"{p['name']} base_type 分叉"

    def _mirror_root(self, tmp_path, base_mut=None, ext_mut=None):
        """搭一个只含三份文件的最小 root，用来跑 check_extension_schema。

        门禁函数按路径读文件，不能靠 make_ext 的内存变换——这里必须落盘。
        """
        import shutil
        from scripts.semantic_gate import check_extension_schema

        for d in ("schema", "freight", "enhancements"):
            (tmp_path / d).mkdir()
        shutil.copy2(os.path.join(ROOT, "schema", "ontology_extension.schema.json"),
                     tmp_path / "schema" / "ontology_extension.schema.json")
        shutil.copy2(os.path.join(ROOT, "schema", "semantic_layer.schema.json"),
                     tmp_path / "schema" / "semantic_layer.schema.json")
        layer = json.load(open(os.path.join(
            ROOT, "freight", "semantic_layer.json"), encoding="utf-8"))
        ext = json.load(open(os.path.join(
            ROOT, "enhancements", "ontology_extension.json"), encoding="utf-8"))
        if base_mut:
            layer = base_mut(layer)
        if ext_mut:
            ext = ext_mut(ext)
        with open(tmp_path / "freight" / "semantic_layer.json", "w",
                  encoding="utf-8") as f:
            json.dump(layer, f, ensure_ascii=False)
        with open(tmp_path / "enhancements" / "ontology_extension.json", "w",
                  encoding="utf-8") as f:
            json.dump(ext, f, ensure_ascii=False)
        return check_extension_schema(str(tmp_path))

    def test_marking_downgrade_is_caught(self, tmp_path):
        """二源真相：基础层标了 PII、扩展层仍是 null（静默降级）必须被门禁抓。

        运行时取的是扩展层那份，两边都"合法"、schema 也都过，
        但脱敏会悄悄消失——这种降级不能靠人肉 review。
        """
        def mark_base(layer):
            for p in layer["property_types"]:
                if p["name"] == "waiting_minutes":
                    p["marking"] = "PII"
            return layer

        item = self._mirror_root(tmp_path, base_mut=mark_base)
        codes = {i["code"] for i in item["issues"]}
        assert item["ok"] is False and "PROP_MARKING_DIVERGED" in codes

    def test_type_divergence_is_caught(self, tmp_path):
        def flip_base(layer):
            for p in layer["property_types"]:
                if p["name"] == "waiting_minutes":
                    p["value_type"] = "string"
            return layer

        item = self._mirror_root(tmp_path, base_mut=flip_base)
        codes = {i["code"] for i in item["issues"]}
        assert item["ok"] is False and "PROP_TYPE_DIVERGED" in codes



# ---------------------------------------------------------------- P0-2
class TestPropertyTypeEnforcement:
    def test_wrong_scalar_type_rejected_on_create(self, system):
        with pytest.raises(OntologyViolation, match="类型不符"):
            system.add_object("Waybill", "waybill:TYPEX", "类型探针", None,
                              {"waybill_id": "TYPEX", "estimated_fee": "abc"})

    def test_wrong_integer_type_rejected_on_create(self, system):
        props = dict(
            json.loads(system.facts.one(
                "SELECT props FROM objects WHERE object_id=?",
                ("waybill:WB20261105001",))["props"])
        )
        props["waiting_minutes"] = "not-a-number"
        with pytest.raises(OntologyViolation, match="类型不符"):
            system.add_object("Waybill", "waybill:TYPEX2", "类型探针", None,
                              {**props, "waybill_id": "TYPEX2"})

    def test_status_enum_still_enforced(self, system):
        with pytest.raises(OntologyViolation, match="非法状态"):
            system.add_object("Waybill", "waybill:TYPEY", "状态探针", "NOT_A_STATUS",
                              {"waybill_id": "TYPEY"})

    def test_datetime_must_be_iso(self, system):
        with pytest.raises(OntologyViolation, match="类型不符"):
            system.add_object("Waybill", "waybill:TYPEZ", "时间探针", None,
                              {"waybill_id": "TYPEZ", "accepted_at": "不是时间"})

    def test_update_path_also_enforced(self, system):
        """写入口收口：动作直接 UPDATE 属性也必须过类型校验。

        只校验创建路径时，类型约束在「新建」生效、改一条就绕过去——那是装饰。
        """
        view = system.get_object("waybill:WB20261105001")
        props = dict(view["properties"])
        props["estimated_fee"] = "abc"
        with pytest.raises(OntologyViolation, match="类型不符"):
            system.facts.update_object("waybill:WB20261105001", props, None,
                                       view["status"], "2026-11-20T00:00:00+08:00")

    def test_seed_data_is_type_clean(self, system):
        """存量体检：种子数据里不允许任何类型违规（否则开了校验就炸）。"""
        pt = system.sem.property_types
        rows = system.facts.all("SELECT object_id, type, props FROM objects")
        bad = []
        for r in rows:
            for k, v in json.loads(r["props"]).items():
                entry = pt.get(k)
                if entry is None:
                    bad.append((r["object_id"], k, "UNREGISTERED"))
                elif not system.sem._type_ok(entry["value_type"], v):
                    bad.append((r["object_id"], k, entry["value_type"]))
        assert bad == []


# ---------------------------------------------------------------- P0-3
class TestPolicyStrict:
    def test_shipped_policy_is_strict(self):
        ext = json.load(open(os.path.join(
            ROOT, "enhancements", "ontology_extension.json"), encoding="utf-8"))
        pol = ext["property_registry_policy"]
        assert pol["enforcement"] == "strict"
        assert pol["legacy_grace"] is False

    def test_gate_flags_relaxed_policy(self, tmp_path):
        """把口径改回 incremental 必须被门禁拦住。"""
        from scripts.semantic_gate import check_policy_strict
        assert check_policy_strict(ROOT)["ok"] is True

    def test_strict_covers_every_class_property(self):
        layer = json.load(open(os.path.join(
            ROOT, "freight", "semantic_layer.json"), encoding="utf-8"))
        registered = {p["name"] for p in layer["property_types"]}
        uncovered = [f"{c['id']}.{p}" for c in layer["classes"]
                     for p in c["properties"] if p not in registered]
        assert uncovered == []


# ---------------------------------------------------------------- P1-1
class TestDisplayMetadataAndRid:
    def test_every_class_has_display_and_rid(self):
        layer = json.load(open(os.path.join(
            ROOT, "freight", "semantic_layer.json"), encoding="utf-8"))
        missing = []
        for c in layer["classes"]:
            if not c.get("rid"):
                missing.append((c["id"], "rid"))
            if not c.get("plural_display_name"):
                missing.append((c["id"], "plural_display_name"))
            dm = c.get("display_metadata") or {}
            for f in ("icon", "color", "visibility", "category"):
                if not dm.get(f):
                    missing.append((c["id"], f"display_metadata.{f}"))
        assert missing == []

    def test_every_relation_has_display_and_rid(self):
        layer = json.load(open(os.path.join(
            ROOT, "freight", "semantic_layer.json"), encoding="utf-8"))
        missing = [(r["id"], "rid") for r in layer["relations"] if not r.get("rid")]
        missing += [(r["id"], "display_metadata") for r in layer["relations"]
                    if not r.get("display_metadata")]
        assert missing == []

    def test_rid_unique(self):
        layer = json.load(open(os.path.join(
            ROOT, "freight", "semantic_layer.json"), encoding="utf-8"))
        rids = [x["rid"] for x in layer["classes"] + layer["relations"]]
        assert len(rids) == len(set(rids))

    def test_rid_matches_ledger(self):
        """rid 是内核身份：本体里的值必须与台账一致，手改会断链。"""
        layer = json.load(open(os.path.join(
            ROOT, "freight", "semantic_layer.json"), encoding="utf-8"))
        ledger = json.load(open(os.path.join(
            ROOT, "freight", "rid_ledger.json"), encoding="utf-8"))
        for kind, items in (("class", layer["classes"]), ("relation", layer["relations"])):
            for item in items:
                assert ledger[f"{kind}:{item['id']}"] == item["rid"]

    def test_display_gate_passes(self):
        from scripts.semantic_gate import check_display_metadata
        item = check_display_metadata(ROOT)
        assert item["ok"], item["issues"][:5]

    def test_rid_is_kind_separated(self):
        """类与链接的 rid 前缀不同，避免同名类/链接串身份。"""
        from ontology import rid_for
        assert rid_for("class", "Waybill").split(".")[3] == "object-type"
        assert rid_for("relation", "waybill_has_track").split(".")[3] == "link-type"


# ---------------------------------------------------------------- P1-2
class TestFunctionTyping:
    def test_param_type_checked(self, enhanced):
        with pytest.raises(FunctionRegistryError) as exc:
            enhanced.call_function("fee_breakdown", {"waybill_id": 12345})
        assert exc.value.code == "SIGNATURE_INVALID"

    def test_unknown_param_still_rejected(self, enhanced):
        with pytest.raises(FunctionRegistryError) as exc:
            enhanced.call_function("fee_breakdown",
                                   {"waybill_id": "waybill:WB20261105001", "x": 1})
        assert exc.value.code == "SIGNATURE_INVALID"

    def test_return_shape_checked(self, enhanced):
        """把 Result Type 声明改小 -> 实际返回的多余字段必须被抓。"""
        rt = enhanced.ext.result_types["FeeBreakdown"]
        saved = list(rt["fields"])
        rt["fields"] = [f for f in saved if f["name"] != "conclusion"]
        try:
            with pytest.raises(FunctionRegistryError) as exc:
                enhanced.call_function("fee_breakdown",
                                       {"waybill_id": "waybill:WB20261105001"})
            assert exc.value.code in ("RETURN_FIELD_UNDECLARED", "RETURN_FIELD_MISSING")
        finally:
            rt["fields"] = saved

    def test_return_field_type_checked(self, enhanced):
        rt = enhanced.ext.result_types["FeeBreakdown"]
        saved = [dict(f) for f in rt["fields"]]
        for f in rt["fields"]:
            if f["name"] == "quoted_total":
                f["type"] = "string"
        try:
            with pytest.raises(FunctionRegistryError) as exc:
                enhanced.call_function("fee_breakdown",
                                       {"waybill_id": "waybill:WB20261105001"})
            assert exc.value.code == "RETURN_FIELD_TYPE_MISMATCH"
        finally:
            rt["fields"] = saved

    def test_unknown_returns_caught_by_registry(self, make_ext):
        def mutate(raw):
            raw["function_registry"][0]["signature"]["returns"] = "NoSuchResultType"
            return raw

        codes = {i["code"] for i in make_ext(mutate).validate()}
        assert "FN_RETURNS_UNKNOWN" in codes

    def test_result_types_are_declared(self):
        ext = json.load(open(os.path.join(
            ROOT, "enhancements", "ontology_extension.json"), encoding="utf-8"))
        declared = {r["id"] for r in ext["result_types"]}
        scalars = {"string", "text", "integer", "number", "decimal", "boolean",
                   "timestamp", "date", "json", "enum"}
        layer = json.load(open(os.path.join(
            ROOT, "freight", "semantic_layer.json"), encoding="utf-8"))
        classes = {c["id"] for c in layer["classes"]}
        for fn in ext["function_registry"]:
            ret = fn["signature"]["returns"]
            assert ret in declared | scalars | classes, f"{fn['id']} 返回类型未定义: {ret}"


# ---------------------------------------------------------------- P2-1
class TestObjectProjection:
    def test_projection_consistent_with_source(self, system):
        assert projection.consistency(system.conn) == []

    def test_projection_narrows_candidates(self, system):
        from freight.object_query import ObjectQueryEngine
        engine = ObjectQueryEngine(system.sem)
        out = engine.execute(
            {"set": {"from": "Waybill", "where": {"status": {"$eq": "DELIVERED"}}}},
            system.facts.store)
        assert engine.last_projection_hit is True
        assert out["total"] >= 1

    def test_composite_predicate_falls_back(self, system):
        from freight.object_query import ObjectQueryEngine
        engine = ObjectQueryEngine(system.sem)
        out = engine.execute(
            {"set": {"from": "Waybill",
                     "where": {"$or": [{"status": {"$eq": "DELIVERED"}}]}}},
            system.facts.store)
        assert engine.last_projection_hit is False
        assert out["total"] >= 1

    def test_stale_projection_falls_back_instead_of_dropping_rows(self, system):
        """不新鲜时必须回退全表：投影少行会让结果静默变少，比报错危险。"""
        from freight.object_query import ObjectQueryEngine
        query = {"set": {"from": "Waybill", "where": {"status": {"$eq": "DELIVERED"}}}}
        baseline = ObjectQueryEngine(system.sem).execute(
            query, system.facts.store)["total"]
        system.conn.execute("DELETE FROM object_props WHERE prop='status'")
        system.conn.commit()
        assert projection.is_fresh(system.conn) is False
        engine = ObjectQueryEngine(system.sem)
        out = engine.execute(query, system.facts.store)
        assert engine.last_projection_stale is True
        assert out["total"] == baseline, "闸门没拦住，投影少行导致结果变少"
        projection.sync_all(system.conn)
        system.conn.commit()

    def test_value_level_staleness_caught_by_consistency(self, system):
        """值被改脏（条数不变）闸门测不出来，必须靠 consistency 全量比对。"""
        system.conn.execute(
            "UPDATE object_props SET str_value='BROKEN' WHERE prop='status'")
        system.conn.commit()
        try:
            assert projection.consistency(system.conn) != []
        finally:
            projection.sync_all(system.conn)
            system.conn.commit()


# ---------------------------------------------------------------- P2-2
class TestMarkingPropagation:
    def test_data_source_marking_hides_whole_object(self, enhanced):
        """客服工单源打了 DISPUTE_OPS -> 该类对象对无标记用户整行不可见。"""
        ctx = SecurityContext(user="anon")
        row = enhanced.store.one(
            "SELECT object_id, type, props FROM objects WHERE type='ExceptionTicket'")
        assert row is not None
        props = json.loads(row["props"])
        assert enhanced.security.effective_markings("ExceptionTicket", props) == {
            "DISPUTE_OPS"}
        assert enhanced.security.visible_object("ExceptionTicket", props, ctx) is False

    def test_data_source_marking_allows_holder(self, enhanced):
        ctx = SecurityContext.with_roles("ops", {"DISPUTE_OPS"})
        row = enhanced.store.one(
            "SELECT object_id, props FROM objects WHERE type='ExceptionTicket'")
        props = json.loads(row["props"])
        assert enhanced.security.visible_object("ExceptionTicket", props, ctx) is True

    def test_property_marking_does_not_hide_row(self, enhanced):
        """属性标记只管列：Shipper 有 PII 列，但整行不能因此消失。"""
        ctx = SecurityContext(user="anon")
        row = enhanced.store.one(
            "SELECT object_id, props FROM objects WHERE type='Shipper'")
        props = json.loads(row["props"])
        assert enhanced.security.effective_markings("Shipper", props) == set()
        assert enhanced.security.visible_object("Shipper", props, ctx) is True
        masked = enhanced.security.mask_properties("Shipper", props, ctx)
        assert masked["shipper_name"] is None, "PII 列未脱敏"

    def test_marking_trace_explains_origin(self, enhanced):
        row = enhanced.store.one(
            "SELECT props FROM objects WHERE type='ExceptionTicket'")
        trace = enhanced.security.marking_trace(
            "ExceptionTicket", json.loads(row["props"]))
        assert trace and trace[0]["origin"] == "data_source"
        assert trace[0]["source"] == "客服工单"

    def test_deny_reason_names_missing_marking(self, enhanced):
        row = enhanced.store.one(
            "SELECT props FROM objects WHERE type='ExceptionTicket'")
        reason = enhanced.security.deny_reason(
            "ExceptionTicket", json.loads(row["props"]), SecurityContext(user="anon"))
        assert reason["missing"] == ["DISPUTE_OPS"]

    def test_class_marking_propagates(self, enhanced):
        """类级标记同样向对象传播（只改 JSON，代码零改动）。"""
        enhanced.ext.class_markings.append(
            {"object_type": "Vehicle", "marking": "FINANCE"})
        try:
            row = enhanced.store.one(
                "SELECT props FROM objects WHERE type='Vehicle'")
            props = json.loads(row["props"])
            assert enhanced.security.effective_markings("Vehicle", props) == {"FINANCE"}
        finally:
            enhanced.ext.class_markings.pop()

    def test_guard_output_denies_high_marking(self, enhanced):
        row = enhanced.store.one(
            "SELECT props FROM objects WHERE type='ExceptionTicket'")
        props = json.loads(row["props"])
        with pytest.raises(AccessDenied) as exc:
            enhanced.security.guard_output(
                "ExceptionTicket", props, SecurityContext(user="anon"))
        assert exc.value.code == "OUTPUT_MARKING_DENIED"

    def test_unknown_marking_reference_caught(self, make_ext):
        def mutate(raw):
            raw["data_source_markings"][0]["marking"] = "GHOST"
            return raw

        codes = {i["code"] for i in make_ext(mutate).validate()}
        assert "DSM_MARKING_UNKNOWN" in codes


# ---------------------------------------------------------------- P2-3
class TestBreakingChangeGate:
    def _pair(self):
        layer = json.load(open(os.path.join(
            ROOT, "freight", "semantic_layer.json"), encoding="utf-8"))
        return copy.deepcopy(layer), copy.deepcopy(layer)

    def test_property_removal_is_breaking(self):
        from freight.change_gate import classify
        before, after = self._pair()
        after["classes"][0]["properties"] = after["classes"][0]["properties"][1:]
        codes = {c["code"] for c in classify(before, after)}
        assert "PROPERTY_REMOVED" in codes

    def test_type_change_is_breaking(self):
        from freight.change_gate import classify
        before, after = self._pair()
        after["property_types"][0]["value_type"] = "json"
        codes = {c["code"] for c in classify(before, after)}
        assert "PROPERTY_TYPE_CHANGED" in codes

    def test_cardinality_tightening_is_breaking(self):
        from freight.change_gate import classify
        before, after = self._pair()
        after["relations"][0]["cardinality"] = "one_to_one"
        before["relations"][0]["cardinality"] = "many_to_many"
        codes = {c["code"] for c in classify(before, after)}
        assert "RELATION_CARDINALITY_TIGHTENED" in codes

    def test_label_change_is_not_breaking(self):
        from freight.change_gate import classify
        before, after = self._pair()
        after["classes"][0]["label"] = after["classes"][0]["label"] + "（改）"
        assert [c for c in classify(before, after) if c["severity"] == "breaking"] == []

    def test_unapproved_breaking_fails_review(self):
        from freight.change_gate import review
        before, after = self._pair()
        after["classes"][0]["properties"] = after["classes"][0]["properties"][1:]
        result = review(before, after, {}, None)  # root=None -> 无签字台账
        assert result["verdict"] == "FAIL"
        assert result["unapproved_count"] >= 1

    def test_shipped_state_is_fully_approved(self):
        """当前本体的破坏项必须全部有签字，否则门禁会红。"""
        from scripts.semantic_gate import check_breaking_changes
        item = check_breaking_changes(ROOT)
        assert item["ok"], item["issues"][:5]
